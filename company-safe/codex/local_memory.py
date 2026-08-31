from __future__ import annotations

import json
import msvcrt
import os
import re
import uuid
from contextlib import contextmanager
from functools import wraps
from hashlib import sha256
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from memory_extractor import extract_memory_candidates, memory_fingerprint, normalize_memory_content


def work_locked(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._work_lock():
            return method(self, *args, **kwargs)
    return wrapper


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def classify_prompt(prompt: str) -> str:
    text = " ".join(str(prompt or "").strip().split()).lower()
    if any(cue in text for cue in ("做了什么", "做到哪", "什么进展", "当前进度", "在跑什么", "任务状态")):
        return "inquiry"
    if any(cue in text for cue in ("接手", "转交", "交给", "换到", "由你继续", "让你继续", "你来继续")):
        return "transfer"
    if any(cue in text for cue in ("继续刚才", "继续上次", "接着刚才", "接着做", "继续做", "续上", "恢复任务")):
        return "continuation"
    if any(cue in text for cue in (
        "实现", "开发", "修复", "解决", "修改", "调整", "搭建", "部署", "安装", "升级",
        "测试", "验证", "检查", "扫描", "分析", "整理", "生成", "创建", "制作", "构建",
        "打包", "同步", "迁移", "执行", "查找", "查看", "读取", "编写", "优化", "完善",
    )):
        return "work"
    return "chat"


def work_title(prompt: str, limit: int = 120) -> str:
    text = " ".join(str(prompt or "").strip().split())
    return (re.split(r"[。！？!?\n]", text, maxsplit=1)[0].strip() or "未命名工作")[:limit]


def compact_assistant_message(message: str) -> dict[str, Any]:
    text = str(message or "").replace("🐳 公司十元本地模式", "").replace("🐳 十元在线", "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    summary = ""
    for part in paragraphs:
        if part.startswith(("#", "```")) and not summary:
            continue
        summary = f"{summary}\n\n{part}".strip()
        if len(summary) >= 900:
            break
    summary = summary[:900] or "本轮没有可保存的用户可见结果摘要。"
    def matches(cues: tuple[str, ...]) -> list[str]:
        return [line[:500] for line in lines if any(cue in line.lower() for cue in cues)][:5]
    next_actions = matches(("下一步", "未完成", "尚未", "还需", "仍需", "待处理", "遗留"))
    blocked = any(cue in text.lower() for cue in ("阻塞", "无法继续", "需要用户", "等待授权", "失败"))
    completed = any(cue in text.lower() for cue in ("已完成", "完成了", "全部通过", "已经解决", "已部署"))
    return {
        "status": "blocked" if blocked else "completed" if completed and not next_actions else "waiting",
        "result_summary": summary,
        "decisions": matches(("决定", "采用", "选择", "保持", "改为")),
        "artifacts": [],
        "evidence": matches(("通过", "passed", "healthy", "测试", "验证", "校验")),
        "next_actions": next_actions,
    }


def default_state_root() -> Path:
    return Path(
        os.environ.get("SHIYUAN_COMPANY_STATE", Path.home() / ".shiyuan-company" / "state")
    )


def _search_units(value: str) -> set[str]:
    normalized = normalize_memory_content(value)
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


VAGUE_REFERENCE_CUES = (
    "以前那个", "之前那个", "上次那个", "刚才那个", "原来那个", "那个东西",
    "那件事", "后来呢", "然后呢", "继续刚才", "接着刚才", "续上",
)


def _is_vague_reference(query: str) -> bool:
    compact = "".join(str(query or "").split())
    return any(cue in compact for cue in VAGUE_REFERENCE_CUES)


def _safe_session_name(body: str, session_id: str | None) -> str:
    identity = f"{body}::{session_id or 'unknown'}"
    return f"{body}-{sha256(identity.encode('utf-8')).hexdigest()[:24]}.jsonl"


def _redact_credentials(value: str) -> str:
    text = str(value or "")
    text = re.sub(
        r"(?i)\b(password|passwd|secret|token|api[_ -]?key|access[_ -]?key)\s*[:=]\s*[^\s,;]+",
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\bsk-[a-z0-9_-]{8,}\b", "[REDACTED_API_KEY]", text)
    text = re.sub(
        r"-----BEGIN\s+(?:RSA|OPENSSH|EC|DSA)?\s*PRIVATE\s+KEY-----.*?-----END\s+(?:RSA|OPENSSH|EC|DSA)?\s*PRIVATE\s+KEY-----",
        "[REDACTED_PRIVATE_KEY]", text, flags=re.DOTALL | re.IGNORECASE,
    )
    return text


class LocalMemoryStore:
    def __init__(self, root: Path | None = None):
        self.root = (root or default_state_root()).resolve()
        for status in ("candidate", "confirmed", "rejected"):
            (self.root / "memory" / status).mkdir(parents=True, exist_ok=True)
        (self.root / "work").mkdir(parents=True, exist_ok=True)
        (self.root / "history").mkdir(parents=True, exist_ok=True)

    def _status_dir(self, status: str) -> Path:
        if status not in {"candidate", "confirmed", "rejected"}:
            raise ValueError(f"unsupported memory status: {status}")
        return self.root / "memory" / status

    @staticmethod
    def _write_atomic(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)

    def _find(self, memory_id: str) -> tuple[Path, dict[str, Any]]:
        for status in ("candidate", "confirmed", "rejected"):
            for path in self._status_dir(status).glob("*.json"):
                value = json.loads(path.read_text(encoding="utf-8"))
                if value.get("id") == memory_id:
                    return path, value
        raise KeyError(memory_id)

    def propose_from_text(self, text: str, source_body: str) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        for candidate in extract_memory_candidates(text):
            fingerprint = memory_fingerprint(candidate.content)
            if any((self._status_dir(status) / f"{fingerprint}.json").exists() for status in ("candidate", "confirmed", "rejected")):
                continue
            timestamp = now_iso()
            record = {
                "id": f"local-{fingerprint[:24]}",
                **candidate.model_dump(),
                "source": f"auto-company-local:{source_body}",
                "evidence": "从用户明确陈述中保守提取；完整提示词未保存。",
                "fingerprint": fingerprint,
                "status": "candidate",
                "local_only": True,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            self._write_atomic(self._status_dir("candidate") / f"{fingerprint}.json", record)
            created.append(record)
        return created

    def list(self, status: str = "candidate", limit: int = 100) -> list[dict[str, Any]]:
        records = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in self._status_dir(status).glob("*.json")
        ]
        records.sort(key=lambda value: value.get("updated_at", ""), reverse=True)
        return records[:limit]

    def decide(self, memory_id: str, decision: str, note: str | None = None) -> dict[str, Any]:
        if decision not in {"confirm", "reject"}:
            raise ValueError("decision must be confirm or reject")
        source_path, record = self._find(memory_id)
        if record["status"] != "candidate":
            raise ValueError("only candidate memories can be reviewed")
        target_status = "confirmed" if decision == "confirm" else "rejected"
        record["status"] = target_status
        record["updated_at"] = now_iso()
        if note:
            record["review_note"] = note[:2000]
        target_path = self._status_dir(target_status) / source_path.name
        self._write_atomic(target_path, record)
        source_path.unlink()
        return record

    def recall(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        records = self.list("confirmed", 500)
        query_units = _search_units(query)
        if not query_units:
            return records[:limit]
        ranked = []
        for record in records:
            memory_units = _search_units(str(record.get("content", "")))
            overlap = len(query_units & memory_units)
            if overlap:
                ranked.append((overlap / max(len(query_units), 1), record.get("updated_at", ""), record))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in ranked[:limit]]

    def append_visible_message(
        self,
        body: str,
        session_id: str | None,
        role: str,
        content: str,
        device: str = "company",
    ) -> dict[str, Any] | None:
        """Archive only user-visible user/assistant text on the company machine."""
        text = _redact_credentials(str(content or "").strip())
        if not text or role not in {"user", "assistant"}:
            return None
        record_hash = sha256(
            f"{body}\0{session_id or ''}\0{role}\0{text}".encode("utf-8")
        ).hexdigest()
        target = self.root / "history" / _safe_session_name(body, session_id)
        if target.exists():
            try:
                tail = target.read_text(encoding="utf-8")[-20000:]
                if f'"fingerprint": "{record_hash}"' in tail:
                    return None
            except OSError:
                pass
        record = {
            "id": str(uuid.uuid4()),
            "fingerprint": record_hash,
            "body": body,
            "device": device,
            "session_id": session_id,
            "role": role,
            "content": text,
            "visible_only": True,
            "local_only": True,
            "created_at": now_iso(),
        }
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def _history_records(self, max_files: int = 500, max_records: int = 20000) -> list[dict[str, Any]]:
        paths = sorted(
            self.root.joinpath("history").glob("*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:max_files]
        records: list[dict[str, Any]] = []
        for path in paths:
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        value = json.loads(line)
                        if value.get("role") in {"user", "assistant"} and value.get("content"):
                            records.append(value)
            except (OSError, ValueError, TypeError):
                continue
        records.sort(key=lambda value: value.get("created_at", ""), reverse=True)
        return records[:max_records]

    def search_history(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        records = self._history_records()
        query_units = _search_units(query)
        vague = _is_vague_reference(query)
        ranked: list[tuple[float, str, dict[str, Any]]] = []
        for index, record in enumerate(records):
            content = str(record.get("content", ""))
            content_units = _search_units(content)
            overlap = len(query_units & content_units)
            score = overlap / max(len(query_units), 1) if query_units else 0.0
            if normalize_memory_content(query) and normalize_memory_content(query) in normalize_memory_content(content):
                score += 1.0
            if vague:
                score += max(0.0, 0.35 - index * 0.002)
            if score > 0:
                ranked.append((score, record.get("created_at", ""), record))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        result = []
        for score, _, record in ranked[:limit]:
            value = dict(record)
            value["score"] = round(score, 4)
            value["content"] = str(value["content"])[:1600]
            result.append(value)
        return result

    def recent_history(self, limit: int = 8) -> list[dict[str, Any]]:
        result = []
        for record in self._history_records(max_records=max(limit, 1))[:limit]:
            value = dict(record)
            value["content"] = str(value["content"])[:1600]
            result.append(value)
        return result

    def _work_state(self) -> dict[str, Any]:
        path = self.root / "work" / "state.json"
        if not path.exists():
            return {"version": 2, "workstreams": [], "links": {}, "activities": [], "cursors": {}, "next_sequence": 1}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value.get("workstreams"), list) and isinstance(value.get("links"), dict):
                value.setdefault("version", 2)
                value.setdefault("activities", [])
                value.setdefault("cursors", {})
                value.setdefault("next_sequence", 1)
                return value
        except (OSError, ValueError, TypeError):
            pass
        return {"version": 2, "workstreams": [], "links": {}, "activities": [], "cursors": {}, "next_sequence": 1}

    @contextmanager
    def _work_lock(self):
        path = self.root / "work" / "state.lock"
        handle = path.open("a+b")
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            handle.close()

    def _save_work_state(self, state: dict[str, Any]) -> None:
        state["version"] = 2
        state["workstreams"] = sorted(
            state.get("workstreams", []), key=lambda item: item.get("updated_at", ""), reverse=True
        )[:100]
        state["activities"] = sorted(
            state.get("activities", []), key=lambda item: int(item.get("sequence", 0))
        )[-1000:]
        self._write_atomic(self.root / "work" / "state.json", state)

    @staticmethod
    def _record_activity(
        state: dict[str, Any], body: str, kind: str, work: dict[str, Any], summary: str
    ) -> dict[str, Any]:
        sequence = int(state.get("next_sequence", 1))
        activity = {
            "id": str(uuid.uuid4()), "sequence": sequence, "body": body, "kind": kind,
            "workstream_id": work["id"], "title": work.get("title", ""),
            "summary": str(summary or "")[:900], "created_at": now_iso(), "local_only": True,
        }
        state["next_sequence"] = sequence + 1
        state.setdefault("activities", []).append(activity)
        return activity

    @staticmethod
    def _effective_work(value: dict[str, Any]) -> dict[str, Any]:
        item = dict(value)
        lease_until = str(item.get("lease_until") or "")
        item["effective_status"] = (
            "stale" if item.get("status") == "running" and lease_until and lease_until <= now_iso()
            else item.get("status")
        )
        checkpoints = list(item.get("checkpoints") or [])
        item["latest_checkpoint"] = checkpoints[-1] if checkpoints else None
        return item

    @work_locked
    def start_work(self, body: str, session_id: str | None, prompt: str, device: str = "company") -> dict[str, Any]:
        mode = classify_prompt(prompt)
        state = self._work_state()
        if mode in {"chat", "inquiry"}:
            return {"mode": mode, "workstream": None, "lease_conflict": False, "recent_work": [self._effective_work(item) for item in state["workstreams"][:6]]}
        timestamp = now_iso()
        lease_until = (datetime.now(UTC) + timedelta(minutes=20)).isoformat(timespec="seconds")
        link_key = f"{body}::{session_id}" if session_id else None
        work = next((item for item in state["workstreams"] if link_key and item["id"] == state["links"].get(link_key)), None)
        if work and work.get("status") == "completed" and mode == "work":
            work = None
        if not work and mode in {"continuation", "transfer"} and state["workstreams"]:
            work = state["workstreams"][0]
        if not work:
            work = {
                "id": str(uuid.uuid4()), "title": work_title(prompt), "objective": work_title(prompt, 300),
                "status": "running", "owner_body": body, "owner_device": device,
                "owner_session_id": session_id, "lease_until": lease_until,
                "latest_receipt": None, "checkpoints": [], "local_only": True,
                "created_at": timestamp, "updated_at": timestamp,
            }
            state["workstreams"].insert(0, work)
        conflict = bool(
            work.get("status") == "running" and work.get("lease_until", "") > timestamp
            and (work.get("owner_body") != body or work.get("owner_session_id") != session_id)
        )
        if not conflict or mode == "transfer":
            work.update({
                "status": "running", "owner_body": body, "owner_device": device,
                "owner_session_id": session_id, "lease_until": lease_until, "updated_at": timestamp,
            })
            if link_key:
                state["links"][link_key] = work["id"]
            self._record_activity(state, body, "started" if not work.get("latest_receipt") else "resumed", work, prompt)
        self._save_work_state(state)
        return {"mode": mode, "workstream": self._effective_work(work), "lease_conflict": conflict, "recent_work": [self._effective_work(item) for item in state["workstreams"][:6]]}

    @work_locked
    def record_work_checkpoint(
        self,
        body: str,
        session_id: str | None,
        phase: str,
        summary: str,
        artifacts: list[str] | None = None,
        evidence: list[str] | None = None,
        next_actions: list[str] | None = None,
        device: str = "company",
        workstream_id: str | None = None,
    ) -> dict[str, Any] | None:
        state = self._work_state()
        link_key = f"{body}::{session_id}" if session_id else None
        work_id = workstream_id or (state["links"].get(link_key) if link_key else None)
        work = next((item for item in state["workstreams"] if item["id"] == work_id), None)
        if not work:
            return None
        timestamp = now_iso()
        checkpoint = {
            "id": str(uuid.uuid4()), "phase": str(phase or "progress")[:80],
            "summary": str(summary or "")[:900], "body": body, "device": device,
            "session_id": session_id, "artifacts": (artifacts or [])[:10],
            "evidence": (evidence or [])[:10], "next_actions": (next_actions or [])[:10],
            "visible_only": True, "local_only": True, "created_at": timestamp,
        }
        work.setdefault("checkpoints", []).append(checkpoint)
        work["checkpoints"] = work["checkpoints"][-30:]
        work.update({"updated_at": timestamp, "lease_until": (datetime.now(UTC) + timedelta(minutes=20)).isoformat(timespec="seconds")})
        self._record_activity(state, body, "checkpoint", work, checkpoint["summary"])
        self._save_work_state(state)
        return checkpoint

    @work_locked
    def record_work_receipt(self, body: str, session_id: str | None, receipt: dict[str, Any], device: str = "company") -> dict[str, Any] | None:
        state = self._work_state()
        link_key = f"{body}::{session_id}" if session_id else None
        work_id = state["links"].get(link_key) if link_key else None
        work = next((item for item in state["workstreams"] if item["id"] == work_id), None)
        if not work:
            return None
        value = {
            "id": str(uuid.uuid4()), "body": body, "device": device, "session_id": session_id,
            "status": receipt["status"], "result_summary": receipt["result_summary"][:900],
            "decisions": receipt.get("decisions", [])[:10], "artifacts": receipt.get("artifacts", [])[:10],
            "evidence": receipt.get("evidence", [])[:10], "next_actions": receipt.get("next_actions", [])[:10],
            "local_only": True, "created_at": now_iso(),
        }
        work.update({
            "status": value["status"], "owner_body": body, "owner_device": device,
            "owner_session_id": session_id, "lease_until": None,
            "latest_receipt": value, "updated_at": value["created_at"],
        })
        self._record_activity(state, body, "receipt", work, value["result_summary"])
        self._save_work_state(state)
        return value

    @work_locked
    def recent_work(self, limit: int = 6) -> list[dict[str, Any]]:
        return [self._effective_work(item) for item in self._work_state()["workstreams"][:limit]]

    @work_locked
    def catch_up_work(self, body: str, limit: int = 20) -> list[dict[str, Any]]:
        state = self._work_state()
        last_seen = int(state.setdefault("cursors", {}).get(body, 0))
        activities = [
            item for item in state.get("activities", [])
            if int(item.get("sequence", 0)) > last_seen and item.get("body") != body
        ][:limit]
        max_sequence = max([int(item.get("sequence", 0)) for item in state.get("activities", [])] or [last_seen])
        state["cursors"][body] = max_sequence
        self._save_work_state(state)
        return activities

    def resolve_context(self, query: str, body: str, limit: int = 8) -> dict[str, Any]:
        vague = _is_vague_reference(query)
        memories = self.recall(query, limit)
        history = self.search_history(query, limit)
        if vague and not history:
            history = self.recent_history(limit)
        work = self.recent_work(limit)
        candidates: list[dict[str, Any]] = []
        for item in work[: min(4, limit)]:
            candidates.append({
                "type": "work", "id": item["id"], "title": item.get("title", ""),
                "summary": (item.get("latest_checkpoint") or item.get("latest_receipt") or {}).get("summary")
                or (item.get("latest_receipt") or {}).get("result_summary") or item.get("objective", ""),
                "created_at": item.get("updated_at", ""),
            })
        for item in history[: max(0, limit - len(candidates))]:
            candidates.append({
                "type": "history", "id": item["id"], "title": f"{item.get('body')} {item.get('role')}",
                "summary": item.get("content", ""), "created_at": item.get("created_at", ""),
            })
        return {
            "query": query, "body": body, "vague_reference": vague,
            "memories": memories, "history": history, "recent_work": work,
            "candidate_interpretations": candidates[:limit],
            "needs_clarification": vague and len(candidates) > 1,
            "guidance": "先按时间线和最近工作推断最可能对象；只有候选确实并列才询问用户。",
        }

    def status(self) -> dict[str, Any]:
        return {
            "state_root": str(self.root),
            "candidate": len(self.list("candidate", 10000)),
            "confirmed": len(self.list("confirmed", 10000)),
            "rejected": len(self.list("rejected", 10000)),
            "workstreams": len(self.recent_work(10000)),
            "history_messages": len(self._history_records()),
            "work_activity": len(self._work_state().get("activities", [])),
            "work_cursors": len(self._work_state().get("cursors", {})),
        }

    def knowledge_coverage(self) -> dict[str, Any]:
        memories = {
            status: {"count": len(self.list(status, 10000))}
            for status in ("confirmed", "candidate", "rejected")
        }
        history = self._history_records()
        sources: dict[str, dict[str, Any]] = {}
        for item in history:
            source = str(item.get("body") or "unknown")
            current = sources.setdefault(source, {"source": source, "messages": 0, "latest_at": ""})
            current["messages"] += 1
            current["latest_at"] = max(current["latest_at"], str(item.get("created_at") or ""))
        work = {"active": 0, "stale": 0, "waiting": 0, "blocked": 0, "completed": 0}
        bodies: dict[tuple[str, str], dict[str, Any]] = {}
        for item in self.recent_work(10000):
            effective = str(item.get("effective_status") or item.get("status") or "unknown")
            key_name = "active" if effective == "running" else effective
            work[key_name] = work.get(key_name, 0) + 1
            body = str(item.get("owner_body") or "unknown")
            device = str(item.get("owner_device") or "unknown")
            key = (body, device)
            current = bodies.setdefault(
                key,
                {"body": body, "device": device, "last_activity_at": "", "active_work": 0},
            )
            current["last_activity_at"] = max(
                current["last_activity_at"], str(item.get("updated_at") or "")
            )
            if key_name == "active":
                current["active_work"] += 1
        return {
            "generated_at": now_iso(),
            "memory": memories,
            "history": {
                "messages": len(history),
                "sources": sorted(sources.values(), key=lambda item: item["latest_at"], reverse=True),
            },
            "work": {
                **work,
                "bodies": sorted(
                    bodies.values(), key=lambda item: item["last_activity_at"], reverse=True
                ),
            },
            "limits": [
                "company-local-only",
                "visible-history-only",
                "private-reasoning-excluded",
                "unconnected-sources-unknown",
            ],
        }
