from __future__ import annotations

import hmac
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from . import __version__
from .config import Settings, load_settings
from .context_resolver import resolve_context
from .database import Database
from .decision_graph import DecisionGraph, looks_like_decision_query
from .history import (
    HistoryArchive,
    build_chunks,
    build_summary,
    derive_title,
    normalize_visible_messages,
    session_identity,
    visible_content_sha256,
)
from .hybrid_retrieval import HybridHistoryRetriever, HttpSemanticHistoryProvider
from .memory_extractor import extract_memory_candidates, memory_fingerprint
from .correction_memory import (
    correction_source_hash,
    extract_operational_corrections,
    operational_correction_definitions,
)
from .models import (
    BootstrapRequest,
    ContextResolveRequest,
    EventCreate,
    HistoryMessageAppend,
    HistorySessionImport,
    MemoryDecision,
    MemoryProposal,
    RecallRequest,
    ResponsePreferences,
    TaskCreate,
    TaskReport,
    WorkReceiptCreate,
    WorkCatchUpRequest,
    WorkCheckpointCreate,
    WorkTurnStart,
)
from .vault import Vault
from .work_state import classify_prompt, work_title


settings: Settings = load_settings()
db = Database(settings.db_path)
semantic_history_provider = (
    HttpSemanticHistoryProvider(
        settings.semantic_history_url,
        settings.semantic_history_token,
        settings.semantic_history_timeout_seconds,
        include_content=settings.history_retrieval_mode != "hybrid-shadow",
    )
    if settings.history_retrieval_mode != "keyword"
    else None
)
history_retriever = HybridHistoryRetriever(
    db,
    mode=settings.history_retrieval_mode,
    provider=semantic_history_provider,
    candidate_limit=50,
    keyword_alpha=0.8,
    rrf_constant=60,
    keyword_guard=2,
    stats_path=(
        settings.data_dir / "evals" / settings.history_retrieval_mode / "runtime-metrics.json"
        if settings.history_retrieval_mode != "keyword"
        else None
    ),
)
decision_graph = DecisionGraph(
    settings.data_dir / "evals" / "memory-semantic-v1" / "decision-graph" / "decision-graph.sqlite3"
)
vault = Vault(settings.vault_dir, settings.assistant_name)
history_archive = HistoryArchive(settings.data_dir / "history")
history_write_lock = threading.RLock()


def seed() -> None:
    previous_name = db.get_meta("assistant_name", "")
    previous_marker = db.get_meta("response_marker", "")
    inherited_default_markers = {"🐳 十元在线"}
    if previous_name:
        inherited_default_markers.add(f"🐳 {previous_name}在线")
    if previous_marker in inherited_default_markers:
        db.set_meta("response_marker", f"🐳 {settings.assistant_name}在线")
    db.seed_meta("response_style_mode", "canary")
    db.seed_meta("response_marker", f"🐳 {settings.assistant_name}在线")
    db.set_meta("assistant_name", settings.assistant_name)
    # Public installations start without anyone else's memories.
    db.enrich_operational_corrections(operational_correction_definitions())
    vault.sync_confirmed(db.list_memories("confirmed", 5000))


def understanding_brief(corrections: list[dict]) -> dict:
    recent = sorted(
        corrections,
        key=lambda item: (item.get("last_seen_at") or "", item.get("priority") or 0),
        reverse=True,
    )[:4]
    focuses = []
    for item in recent:
        if not any(item.get(key) for key in ("rationale", "success_signal", "anti_pattern")):
            continue
        focuses.append(
            {
                "category": item.get("category"),
                "directive": item.get("content"),
                "why_it_matters": item.get("rationale") or "",
                "success_signal": item.get("success_signal") or "",
                "avoid": item.get("anti_pattern") or "",
                "last_seen_at": item.get("last_seen_at"),
            }
        )
    return {
        "principle": (
            "先识别当前请求的真实目标，再让相关的已确认记忆和用户纠正实际改变本轮行动。"
        ),
        "focuses": focuses,
        "self_check": [
            "是否主动连上了与本轮最相关的一条旧反馈，而不是倾倒整份画像？",
            "是否说清当前真正的落差，并接走了一个具体负担？",
            "是否用真实行动或证据体现理解，而不是只说‘我懂’？",
        ],
    }


def response_style() -> dict:
    mode = db.get_meta("response_style_mode", "canary")
    default_marker = f"🐳 {settings.assistant_name}在线"
    marker = db.get_meta("response_marker", default_marker) or default_marker
    if mode == "off":
        return {"mode": "off", "marker": "", "instruction": "回复样式连接标记当前关闭。"}
    return {
        "mode": "canary",
        "marker": marker,
        "instruction": (
            f"这是{settings.assistant_name} Core 的默认跨身体回复节奏。先给真实结果，再给必要证据和下一步。"
            "表达自然、具体、简洁；区分事实、推断和待确认项。不要模仿第三方角色，"
            "不要编造情绪、证据或完成状态，不输出模型私有推理。安全、隐私、费用和故障场景先说明风险。"
            "只有收到本 canary 指令时，"
            f"才在最终用户可见回复末尾单独附加“{marker}”；Core 不可达时不得冒用该标记。"
        ),
    }


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db.initialize()
    vault.initialize()
    history_archive.initialize()
    seed()
    yield


app = FastAPI(title="Shiyuan Personal Assistant Core", version=__version__, lifespan=lifespan)
MEMORY_CONSOLE_PATH = Path(__file__).with_name("static") / "memory_console.html"


def require_token(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    provided = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(provided, settings.token):
        raise HTTPException(status_code=403, detail="Invalid token")


auth = [Depends(require_token)]


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "name": f"{settings.assistant_name} Core",
        "assistant_name": settings.assistant_name,
        "version": __version__,
        "index": "sqlite-fts5" if db.fts_enabled else "sqlite-like",
        "auto_memory": "conservative-candidate" if settings.auto_memory_enabled else "off",
        "work_continuity": "structured-receipts",
        "history": "layered-visible-transcripts",
        "history_retrieval": settings.history_retrieval_mode,
    }


@app.get("/memory-console", response_class=HTMLResponse)
def memory_console() -> HTMLResponse:
    page = MEMORY_CONSOLE_PATH.read_text(encoding="utf-8").replace("十元", settings.assistant_name)
    return HTMLResponse(page)


@app.post("/v1/bootstrap", dependencies=auth)
def bootstrap(request: BootstrapRequest) -> dict:
    active_tasks = db.list_tasks(status=None, assigned_body=request.body, limit=8)
    active_tasks = [task for task in active_tasks if task["status"] not in {"completed", "failed"}]
    recent_work = db.list_recent_work(6, request.project)
    unread_work = db.catch_up_work(request.body, request.device, limit=30, advance=True)
    corrections = db.list_operational_corrections(
        "active",
        16,
        body=request.body,
        device=request.device,
        project=request.project,
    )
    return {
        "core": settings.assistant_name,
        "body": request.body,
        "identity": vault.read_identity(),
        "user_profile": vault.read_user_profile(),
        "development_status": vault.read_development_status(),
        "operational_corrections": corrections,
        "understanding_brief": understanding_brief(corrections),
        "active_tasks": active_tasks,
        "recent_task_reports": db.list_recent_task_reports(6),
        "recent_work": recent_work,
        "unread_work": unread_work,
        "knowledge_coverage": db.knowledge_coverage(),
        "response_style": response_style(),
        "rules": [
            "已确认记忆才可当作事实；候选记忆必须明确标注。",
            "高优先级用户纠正只约束 Agent 的工作方式，不作为用户人格事实；新会话应先执行这些纠正。",
            "用户明确表达的偏好、习惯、事实或长期规则可由保守提取器自动形成候选；候选不自动永久化。",
            "跨身体工作由 Hook 自动登记结构化回执；任务卡用于正式任务治理，不要求用户手工维护。",
            "另一个身体接续前先读最近工作；存在活跃租约冲突时先只读检查，除非用户明确转交。",
            "复杂工作在调查、实现和验证阶段更新安全检查点；只记录可见进展、产物和证据，不记录私有推理。",
            "公司资料不得因个人记忆系统而绕过公司政策外传。",
        ],
    }


@app.get("/v1/preferences", dependencies=auth)
def get_preferences() -> dict:
    return response_style()


@app.put("/v1/preferences", dependencies=auth)
def update_preferences(preferences: ResponsePreferences) -> dict:
    db.set_meta("response_style_mode", preferences.response_style_mode)
    db.set_meta(
        "response_marker",
        preferences.response_marker.strip() or f"🐳 {settings.assistant_name}在线",
    )
    return response_style()


@app.post("/v1/recall", dependencies=auth)
def recall(request: RecallRequest) -> dict:
    return {"items": db.recall(request.query, request.limit), "query": request.query}


@app.get("/v1/corrections", dependencies=auth)
def list_corrections(
    status: str = Query(
        default="active", pattern=r"^(active|pending|inactive|superseded)$"
    ),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    return {"items": db.list_operational_corrections(status, limit), "status": status}


@app.get("/v1/memory/dashboard", dependencies=auth)
def memory_dashboard(limit: int = Query(default=200, ge=1, le=500)) -> dict:
    correction_states = ("active", "pending", "inactive", "superseded")
    memory_states = ("confirmed", "candidate", "rejected", "superseded")
    return {
        "core": settings.assistant_name,
        "version": __version__,
        "readonly": True,
        "coverage": db.knowledge_coverage(),
        "corrections": {
            state: db.list_operational_corrections(state, limit)
            for state in correction_states
        },
        "memories": {
            state: db.list_memories(state, limit)
            for state in memory_states
        },
        "limits": [
            "只读管理台不提供确认、修改、停用或删除操作。",
            "纠正规则显示规范化内容与来源元数据，不显示原始用户提示。",
            "候选记忆不是事实，不能作为 confirmed 使用。",
        ],
    }


@app.post("/v1/history/recall", dependencies=auth)
def recall_history(request: RecallRequest) -> dict:
    return {"items": history_retriever.retrieve(request.query, request.limit), "query": request.query}


@app.post("/v1/context/resolve", dependencies=auth)
def resolve_memory_context(request: ContextResolveRequest) -> dict:
    active_tasks = db.list_tasks(status=None, assigned_body=request.body, limit=8)
    active_tasks = [task for task in active_tasks if task["status"] not in {"completed", "failed"}]
    recent_history = db.list_recent_history_context(
        8,
        exclude_source=request.body,
        exclude_source_session_id=request.session_id,
    ) if request.include_recent else []
    resolved = resolve_context(
        request.query,
        request.limit,
        history_retriever.retrieve,
        db.list_recent_work(8, request.project),
        active_tasks,
        db.list_recent_task_reports(8),
        recent_history,
        request.session_id,
    )
    resolved["decision_candidates"] = (
        decision_graph.search(request.query, min(request.limit, 6), include_related=False)
        if looks_like_decision_query(request.query)
        else []
    )
    return resolved


@app.post("/v1/decisions/search", dependencies=auth)
def search_decisions(request: RecallRequest) -> dict:
    return {
        "items": decision_graph.search(request.query, request.limit),
        "query": request.query,
        "authority": "candidate-derived",
        "confirmed": False,
    }


@app.post("/v1/history/sessions", dependencies=auth)
def import_history_session(request: HistorySessionImport) -> dict:
    values = request.model_dump()
    messages = values.pop("messages")
    with history_write_lock:
        return store_history_session(values, messages)


def store_history_session(values: dict, raw_messages: list[dict]) -> dict:
    messages = normalize_visible_messages(raw_messages)
    if not messages:
        raise HTTPException(status_code=422, detail="No visible user/assistant messages")
    history_id = session_identity(values["source"], values["source_session_id"])
    title = derive_title(messages, values.get("title", ""))
    content_sha256 = visible_content_sha256(messages)
    timestamps = [message.get("timestamp") for message in messages if message.get("timestamp")]
    session = {
        **values,
        "id": history_id,
        "title": title,
        "started_at": values.get("started_at") or (timestamps[0] if timestamps else None),
        "ended_at": values.get("ended_at") or (timestamps[-1] if timestamps else None),
        "messages": messages,
        "content_sha256": content_sha256,
        "message_count": len(messages),
        "character_count": sum(len(message["content"]) for message in messages),
        "summary": build_summary(messages, title),
    }
    chunks = build_chunks(messages)
    try:
        existing = db.get_history_session(history_id)
    except KeyError:
        existing = None
    if existing and existing["content_sha256"] == content_sha256:
        return db.upsert_history_session(session, chunks, existing["raw_relpath"])
    raw_relpath = history_archive.write(session)
    return db.upsert_history_session(session, chunks, raw_relpath)


@app.post("/v1/history/messages", dependencies=auth)
def append_history_message(request: HistoryMessageAppend) -> dict:
    values = request.model_dump()
    history_id = session_identity(values["source"], values["source_session_id"])
    with history_write_lock:
        try:
            existing = db.get_history_session(history_id)
            archived = history_archive.read(existing["raw_relpath"])
            messages = list(archived.get("messages", []))
        except KeyError:
            existing = None
            messages = []
        if any(message.get("message_id") == values["idempotency_key"] for message in messages):
            return {"action": "skipped", "session": existing}
        incoming = values["message"]
        incoming["message_id"] = values["idempotency_key"]
        messages.append(incoming)
        metadata = {
            "source": values["source"],
            "source_session_id": values["source_session_id"],
            "title": (existing or {}).get("title") or values.get("title", ""),
            "source_locator": (existing or {}).get("source_locator") or values["source_locator"],
            "source_fingerprint": (existing or {}).get("source_fingerprint") or "live-hook",
            "started_at": (existing or {}).get("started_at"),
            "ended_at": incoming.get("timestamp") or (existing or {}).get("ended_at"),
            "import_version": max(2, int((existing or {}).get("import_version") or 1)),
        }
        return store_history_session(metadata, messages)


@app.get("/v1/history/status", dependencies=auth)
def history_status() -> dict:
    return db.history_status()


@app.get("/v1/history/sessions/{session_id}", dependencies=auth)
def get_history_session(session_id: str, include_messages: bool = False) -> dict:
    try:
        stored = db.get_history_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="History session not found") from None
    if not include_messages:
        return stored
    archived = history_archive.read(stored["raw_relpath"])
    return {**stored, "messages": archived.get("messages", [])}


@app.post("/v1/memory/proposals", dependencies=auth)
def propose_memory(proposal: MemoryProposal) -> dict:
    return db.insert_memory(**proposal.model_dump(), status="candidate")


@app.get("/v1/memory/proposals", dependencies=auth)
def list_proposals(limit: int = Query(default=100, ge=1, le=500)) -> dict:
    return {"items": db.list_memories("candidate", limit)}


@app.post("/v1/memory/{memory_id}/confirm", dependencies=auth)
def confirm_memory(memory_id: str, decision: MemoryDecision) -> dict:
    try:
        memory = db.decide_memory(memory_id, "confirmed", decision.note)
    except KeyError:
        raise HTTPException(status_code=404, detail="Memory not found") from None
    path = vault.sync_confirmed(db.list_memories("confirmed", 5000))
    return {"memory": memory, "vault_path": str(path.relative_to(settings.vault_dir))}


@app.post("/v1/memory/{memory_id}/reject", dependencies=auth)
def reject_memory(memory_id: str, decision: MemoryDecision) -> dict:
    try:
        return db.decide_memory(memory_id, "rejected", decision.note)
    except KeyError:
        raise HTTPException(status_code=404, detail="Memory not found") from None


@app.post("/v1/events", dependencies=auth)
def add_event(event: EventCreate) -> dict:
    stored = db.add_event(event.model_dump())
    created_candidates = []
    updated_corrections = []
    if settings.auto_memory_enabled and event.event_type == "user_prompt" and event.summary:
        prompt_hash = correction_source_hash(event.summary)
        source_key = event.idempotency_key or stored["id"]
        for correction in extract_operational_corrections(
            event.summary,
            body=event.body,
            device=event.device,
            project=event.project,
        ):
            observed, created = db.observe_operational_correction(
                category=correction.category,
                content=correction.content,
                priority=correction.priority,
                explicit=correction.explicit,
                source_key=source_key,
                source_hash=prompt_hash,
                body=event.body,
                device=event.device,
                session_id=event.session_id,
                event_id=stored["id"],
                scope=correction.scope,
                origin=correction.origin,
                content_fingerprint=correction.content_fingerprint,
                conflict_key=correction.conflict_key,
                polarity=correction.polarity,
                rationale=correction.rationale,
                success_signal=correction.success_signal,
                anti_pattern=correction.anti_pattern,
            )
            if created:
                updated_corrections.append(observed)
        for candidate in extract_memory_candidates(event.summary):
            memory, created = db.insert_memory_if_new(
                **candidate.model_dump(),
                source=f"auto:{event.body}:{stored['id']}",
                evidence=(
                    "用户明确陈述，经保守规则自动提取；尚未由用户确认。"
                    f" 来源事件：{stored['id']}"
                ),
                fingerprint=memory_fingerprint(candidate.content),
                status="candidate",
            )
            if created:
                created_candidates.append(memory)
    return {
        **stored,
        "memory_candidates": created_candidates,
        "operational_corrections_updated": updated_corrections,
    }


@app.post("/v1/work/turn-start", dependencies=auth)
def start_work(request: WorkTurnStart) -> dict:
    mode = classify_prompt(request.prompt)
    state = db.start_work(request.model_dump(), mode, work_title(request.prompt))
    return {
        **state,
        "recent_work": db.list_recent_work(6, request.project),
        "recent_task_reports": db.list_recent_task_reports(6),
    }


@app.post("/v1/work/receipts", dependencies=auth)
def create_work_receipt(request: WorkReceiptCreate) -> dict:
    receipt = db.record_work_receipt(request.model_dump())
    return {"stored": receipt is not None, "receipt": receipt}


@app.post("/v1/work/checkpoints", dependencies=auth)
def create_work_checkpoint(request: WorkCheckpointCreate) -> dict:
    checkpoint = db.record_work_checkpoint(request.model_dump())
    return {"stored": checkpoint is not None, "checkpoint": checkpoint}


@app.post("/v1/work/catch-up", dependencies=auth)
def catch_up_work(request: WorkCatchUpRequest) -> dict:
    return db.catch_up_work(request.body, request.device, request.limit, request.advance)


@app.get("/v1/work/recent", dependencies=auth)
def recent_work(
    limit: int = Query(default=6, ge=1, le=30),
    project: str | None = None,
) -> dict:
    return {
        "items": db.list_recent_work(limit, project),
        "recent_task_reports": db.list_recent_task_reports(limit),
    }


@app.post("/v1/tasks", dependencies=auth)
def create_task(task: TaskCreate) -> dict:
    return db.create_task(task.model_dump())


@app.get("/v1/tasks", dependencies=auth)
def list_tasks(
    status: str | None = None,
    assigned_body: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    return {"items": db.list_tasks(status, assigned_body, limit)}


@app.post("/v1/tasks/{task_id}/report", dependencies=auth)
def report_task(task_id: str, report: TaskReport) -> dict:
    try:
        return db.report_task(task_id, report.model_dump())
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None


@app.get("/v1/status", dependencies=auth)
def status() -> dict:
    return {
        "core": settings.assistant_name,
        "version": __version__,
        "data_dir": str(settings.data_dir),
        "vault_dir": str(settings.vault_dir),
        "index": "sqlite-fts5" if db.fts_enabled else "sqlite-like",
        "auto_memory": "conservative-candidate" if settings.auto_memory_enabled else "off",
        "work_continuity": "structured-receipts",
        "history_retrieval": history_retriever.status(),
        "decision_graph": decision_graph.status(),
        **db.status(),
    }


@app.get("/v1/coverage", dependencies=auth)
def knowledge_coverage() -> dict:
    return db.knowledge_coverage()


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
