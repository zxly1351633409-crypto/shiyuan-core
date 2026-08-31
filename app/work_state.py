from __future__ import annotations

import re
from typing import Any


MARKERS = ("🐳 十元在线", "🐳 公司十元本地模式")
INQUIRY_CUES = (
    "做了什么", "做到哪", "什么进展", "进展如何", "当前进度", "现在状态",
    "正在做什么", "在跑什么", "完成了吗", "任务状态",
)
TRANSFER_CUES = ("接手", "转交", "交给", "换到", "换成", "由你继续", "让你继续", "你来继续")
CONTINUATION_CUES = ("继续刚才", "继续上次", "接着刚才", "接着做", "继续做", "续上", "恢复任务", "从这里继续")
WORK_CUES = (
    "实现", "开发", "修复", "解决", "修改", "调整", "搭建", "部署", "安装", "升级",
    "测试", "验证", "检查", "扫描", "分析", "整理", "生成", "创建", "制作", "构建",
    "打包", "同步", "迁移", "执行", "查找", "查看", "读取", "编写", "优化", "完善",
)
EVIDENCE_CUES = ("通过", "passed", "healthy", "测试", "验证", "sha-256", "commit", "提交", "校验")
NEXT_CUES = ("下一步", "未完成", "尚未", "还需", "仍需", "待处理", "遗留", "需要重启")
DECISION_CUES = ("决定", "采用", "选择", "保持", "不再", "改为", "范围")


def classify_prompt(prompt: str) -> str:
    text = " ".join(str(prompt or "").strip().split()).lower()
    if not text:
        return "chat"
    if any(cue in text for cue in INQUIRY_CUES):
        return "inquiry"
    if any(cue in text for cue in TRANSFER_CUES):
        return "transfer"
    if any(cue in text for cue in CONTINUATION_CUES):
        return "continuation"
    if any(cue in text for cue in WORK_CUES):
        return "work"
    return "chat"


def work_title(prompt: str, limit: int = 80) -> str:
    text = " ".join(str(prompt or "").strip().split())
    first = re.split(r"[。！？!?\n]", text, maxsplit=1)[0].strip()
    return (first or "未命名工作")[:limit]


def _clean_message(message: str) -> str:
    text = str(message or "").replace("\r\n", "\n")
    for marker in MARKERS:
        text = text.replace(marker, "")
    return text.strip()


def _lines_with_cues(lines: list[str], cues: tuple[str, ...], limit: int = 5) -> list[str]:
    result: list[str] = []
    for line in lines:
        lowered = line.lower()
        if any(cue in lowered for cue in cues):
            cleaned = re.sub(r"^[\s#>*+\-\d.）)✅⚠️🔍]+", "", line).strip()
            if cleaned and cleaned not in result:
                result.append(cleaned[:500])
        if len(result) >= limit:
            break
    return result


def _artifacts(text: str) -> list[str]:
    candidates: list[str] = []
    candidates.extend(re.findall(r"\[[^\]]+\]\(([^)]+)\)", text))
    candidates.extend(re.findall(r"`([^`\n]*(?:[A-Za-z]:\\|/)[^`\n]+)`", text))
    candidates.extend(re.findall(r"(?<!\w)([A-Za-z]:\\[^\s<>\"|?*]+)", text))
    result: list[str] = []
    for item in candidates:
        value = item.strip().split(":", 1)[0] if item.startswith("http") else item.strip()
        if not value or value.startswith(("http://", "https://")):
            continue
        if value not in result:
            result.append(value[:1000])
        if len(result) >= 12:
            break
    return result


def compact_assistant_message(message: str, max_summary: int = 900) -> dict[str, Any]:
    text = _clean_message(message)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    summary_parts: list[str] = []
    for paragraph in paragraphs:
        if paragraph.startswith(("#", "```")) and not summary_parts:
            continue
        summary_parts.append(paragraph)
        if len("\n\n".join(summary_parts)) >= max_summary:
            break
    summary = "\n\n".join(summary_parts).strip()[:max_summary]
    if not summary:
        summary = "本轮没有可保存的用户可见结果摘要。"

    unfinished = _lines_with_cues(lines, NEXT_CUES)
    blocked = any(cue in text.lower() for cue in ("阻塞", "无法继续", "需要用户", "等待授权", "失败"))
    completed = any(cue in text.lower() for cue in ("已完成", "完成了", "全部通过", "已经解决", "已部署"))
    status = "blocked" if blocked else "completed" if completed and not unfinished else "waiting"
    return {
        "status": status,
        "result_summary": summary,
        "decisions": _lines_with_cues(lines, DECISION_CUES),
        "artifacts": _artifacts(text),
        "evidence": _lines_with_cues(lines, EVIDENCE_CUES),
        "next_actions": unfinished,
    }
