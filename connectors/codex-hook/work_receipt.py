from __future__ import annotations

import re


MARKERS = ("🐳 十元在线", "🐳 公司十元本地模式")
EVIDENCE_CUES = ("通过", "passed", "healthy", "测试", "验证", "sha-256", "commit", "提交", "校验")
NEXT_CUES = ("下一步", "未完成", "尚未", "还需", "仍需", "待处理", "遗留", "需要重启")
DECISION_CUES = ("决定", "采用", "选择", "保持", "不再", "改为", "范围")


def _matches(lines: list[str], cues: tuple[str, ...], limit: int = 5) -> list[str]:
    result = []
    for line in lines:
        if any(cue in line.lower() for cue in cues):
            value = re.sub(r"^[\s#>*+\-\d.）)✅⚠️🔍]+", "", line).strip()
            if value and value not in result:
                result.append(value[:500])
        if len(result) >= limit:
            break
    return result


def compact_assistant_message(message: str) -> dict:
    text = str(message or "").replace("\r\n", "\n")
    for marker in MARKERS:
        text = text.replace(marker, "")
    text = text.strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chosen = []
    for part in paragraphs:
        if part.startswith(("#", "```")) and not chosen:
            continue
        chosen.append(part)
        if len("\n\n".join(chosen)) >= 900:
            break
    summary = "\n\n".join(chosen).strip()[:900] or "本轮没有可保存的用户可见结果摘要。"
    artifacts = []
    found = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
    found += re.findall(r"`([^`\n]*(?:[A-Za-z]:\\|/)[^`\n]+)`", text)
    found += re.findall(r"(?<!\w)([A-Za-z]:\\[^\s<>\"|?*]+)", text)
    for value in found:
        value = value.strip()
        if value.startswith(("http://", "https://")) or not value:
            continue
        if value not in artifacts:
            artifacts.append(value[:1000])
        if len(artifacts) >= 12:
            break
    next_actions = _matches(lines, NEXT_CUES)
    lowered = text.lower()
    blocked = any(cue in lowered for cue in ("阻塞", "无法继续", "需要用户", "等待授权", "失败"))
    completed = any(cue in lowered for cue in ("已完成", "完成了", "全部通过", "已经解决", "已部署"))
    return {
        "status": "blocked" if blocked else "completed" if completed and not next_actions else "waiting",
        "result_summary": summary,
        "decisions": _matches(lines, DECISION_CUES),
        "artifacts": artifacts,
        "evidence": _matches(lines, EVIDENCE_CUES),
        "next_actions": next_actions,
    }
