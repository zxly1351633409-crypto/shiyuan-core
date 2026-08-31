from __future__ import annotations

import json
import sys
from pathlib import Path

from local_memory import LocalMemoryStore, compact_assistant_message
from live_activity_bridge import ensure_live_activity_bridge


if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8-sig")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    event_name = sys.argv[1] if len(sys.argv) > 1 else "SessionStart"
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    state_root = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    store = LocalMemoryStore(state_root)
    if event_name in {"SessionStart", "UserPromptSubmit"}:
        ensure_live_activity_bridge(store.root)
    session_id = payload.get("session_id") or payload.get("sessionId") or payload.get("thread_id")
    if event_name == "Stop":
        message = str(payload.get("last_assistant_message") or "")
        if message:
            store.append_visible_message("codex", session_id, "assistant", message)
            store.record_work_receipt("codex", session_id, compact_assistant_message(message))
        print("{}")
        return
    if event_name not in {"SessionStart", "UserPromptSubmit"}:
        return
    here = Path(__file__).parent
    policy_path = here / "company-policy.md"
    memory_path = here / "confirmed-memory.md"
    if not policy_path.is_file():
        policy_path = here.parent / "company-policy.md"
    if not memory_path.is_file():
        memory_path = here.parent / "confirmed-memory.md"
    policy = policy_path.read_text(encoding="utf-8")
    memory = memory_path.read_text(encoding="utf-8")
    prompt = str(
        payload.get("prompt")
        or payload.get("user_prompt")
        or payload.get("message")
        or ""
    )
    if event_name == "UserPromptSubmit" and prompt:
        store.append_visible_message("codex", session_id, "user", prompt)
        store.propose_from_text(prompt, source_body="codex")
        store.start_work("codex", session_id, prompt)
    resolved = store.resolve_context(prompt, "codex", limit=8)
    relevant = resolved["memories"]
    dynamic_memories = "\n".join(
        f"- [{item['kind']}] {item['content']}（公司本地已确认）" for item in relevant
    ) or "- 暂无公司本地已确认记忆"
    pending = store.status()["candidate"]
    coverage = store.knowledge_coverage()
    coverage_history = coverage["history"]
    coverage_work = coverage["work"]
    coverage_sources = "；".join(
        f"{item['source']} {item['messages']} 条，更新至 {item['latest_at'] or '未知'}"
        for item in coverage_history["sources"][:8]
    ) or "尚无公司本地可见历史"
    coverage_bodies = "；".join(
        f"{item['body']}@{item['device']} 最后活动 {item['last_activity_at'] or '未知'}，活跃工作 {item['active_work']}"
        for item in coverage_work["bodies"][:8]
    ) or "尚无公司本地身体活动"
    recent_work = resolved["recent_work"]
    work_context = "\n".join(
        f"- {item['id']} | {item.get('effective_status') or item['status']} | 当前身体：{item.get('owner_body') or '无'} | {item['title']}"
        + (f"\n  最近检查点：{item['latest_checkpoint']['summary']}" if item.get("latest_checkpoint") else "")
        + (f"\n  最近结果：{item['latest_receipt']['result_summary']}" if item.get("latest_receipt") else "")
        for item in recent_work
    ) or "- 暂无公司本地结构化工作记录"
    history_context = "\n".join(
        f"- [{item.get('body')}:{item.get('role')} {item.get('created_at')}] {str(item.get('content', ''))[:600]}"
        for item in resolved["history"][:4]
    ) or "- 暂无匹配的公司本地可见对话"
    candidates = "\n".join(
        f"- [{item['type']}] {item.get('title')}: {str(item.get('summary', ''))[:500]}"
        for item in resolved["candidate_interpretations"][:6]
    ) or "- 暂无候选"
    unread = store.catch_up_work("codex", 20)
    unread_context = "\n".join(
        f"- #{item['sequence']} {item['body']} {item['kind']}：{item['summary']}"
        for item in unread
    ) or "- 暂无其他身体的新活动"
    local_context = (
        "## 公司本地增量记忆\n"
        f"{dynamic_memories}\n"
        f"- 待审核候选：{pending} 条。候选不能当作事实；只在用户明确要求后调用审核工具。\n"
        "- 本地提取器只保存短候选结论，不保存本轮完整提示词。"
        "\n## 公司本地知情范围与新鲜度\n"
        f"- 可见消息：{coverage_history['messages']}；来源：{coverage_sources}\n"
        f"- 记忆：confirmed {coverage['memory']['confirmed']['count']}；candidate {coverage['memory']['candidate']['count']}；rejected {coverage['memory']['rejected']['count']}。\n"
        f"- 工作：active {coverage_work.get('active', 0)}；stale {coverage_work.get('stale', 0)}；waiting {coverage_work.get('waiting', 0)}；blocked {coverage_work.get('blocked', 0)}。\n"
        f"- 身体：{coverage_bodies}\n"
        "- 边界：只覆盖公司本机已接入的可见对话和结构化工作；不联网，不采集私有推理，未接入内容仍未知。"
        "\n## 公司本地可见对话召回\n"
        f"{history_context}\n"
        "- 只归档用户与助手可见文本；不归档私有推理、隐藏系统提示或工具原始输出。"
        "\n## 公司本地最近工作\n"
        f"{work_context}\n"
        "## 模糊指代候选\n"
        f"{candidates}\n"
        f"- 是否模糊指代：{resolved['vague_reference']}；先自行推断最可能对象，只有候选确实并列才追问。\n"
        "## 自上次读取后的其他身体活动\n"
        f"{unread_context}\n"
        "- 用户说“继续刚才的”时，优先恢复最近工作；活跃租约属于另一身体时先只读检查，明确接手后再修改。"
    )
    context = f"{policy.rstrip()}\n\n{memory.rstrip()}\n\n{local_context}\n"
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": event_name,
                    "additionalContext": context,
                }
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
