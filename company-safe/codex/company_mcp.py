from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from handoff import create_handoff
from local_memory import LocalMemoryStore


OUTBOX = Path(os.environ.get("SHIYUAN_COMPANY_OUTBOX", Path.home() / "Documents" / "十元交接箱"))
MEMORY = LocalMemoryStore()


TOOLS = [
    {
        "name": "shiyuan_company_status",
        "description": "查看十元公司安全模式状态；该模式不连接家庭 Core",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "export_shiyuan_company_handoff",
        "description": "仅在用户明确要求时，把已脱敏的公司工作摘要保存为等待人工审核的本地交接卡；不会自动发送",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "不含未公开项目名的概括标题"},
                "summary": {"type": "string", "description": "允许带离公司的抽象摘要，不得粘贴原文"},
                "decisions": {"type": "array", "items": {"type": "string"}},
                "next_actions": {"type": "array", "items": {"type": "string"}},
                "sensitivity": {"type": "string", "enum": ["safe_summary", "review_required", "do_not_export"]},
                "contains_company_confidential": {"type": "boolean"},
                "source_body": {"type": "string"},
            },
            "required": ["title", "summary", "sensitivity", "contains_company_confidential"],
        },
    },
    {
        "name": "list_shiyuan_company_memory_proposals",
        "description": "列出只保存在公司电脑本地的十元候选记忆；候选尚未成为事实",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}},
        },
    },
    {
        "name": "review_shiyuan_company_memory",
        "description": "仅在用户明确要求后，确认或拒绝一条公司本地候选记忆",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["confirm", "reject"]},
                "note": {"type": "string"},
            },
            "required": ["memory_id", "decision"],
        },
    },
    {
        "name": "recall_shiyuan_company_memory",
        "description": "从公司电脑本地读取已由用户确认的十元增量记忆",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 30},
            },
        },
    },
    {
        "name": "recent_shiyuan_company_work",
        "description": "读取公司本机最近的结构化工作状态和跨身体结果回执",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 30}},
        },
    },
    {
        "name": "resolve_shiyuan_company_context",
        "description": "在公司本机从已确认记忆、可见对话和工作检查点中恢复模糊上下文；不会联网",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 30},
            },
            "required": ["query"],
        },
    },
    {
        "name": "checkpoint_shiyuan_company_work",
        "description": "为公司本机进行中的工作保存用户可见检查点；不得写入私有推理、隐藏提示或工具原始输出",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workstream_id": {"type": "string"},
                "phase": {"type": "string"},
                "summary": {"type": "string"},
                "artifacts": {"type": "array", "items": {"type": "string"}},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "next_actions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["workstream_id", "phase", "summary"],
        },
    },
]


def send(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def tool_result(text: str, structured: dict[str, Any] | None = None, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if structured is not None:
        result["structuredContent"] = structured
    return result


def handle(method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", "2025-03-26"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "shiyuan-company-safe", "version": "0.1.8"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "shiyuan_company_status":
            data = {
                "mode": "company-safe-offline",
                "core_connected": False,
                "automatic_upload": False,
                "memory_snapshot": "confirmed-full-2026-08-29",
                "outbox": str(OUTBOX),
                "local_memory": MEMORY.status(),
                "knowledge_coverage": MEMORY.knowledge_coverage(),
                "visible_conversation_saved_locally": True,
                "private_reasoning_saved": False,
            }
            return tool_result(json.dumps(data, ensure_ascii=False, indent=2), data)
        if name == "export_shiyuan_company_handoff":
            try:
                card, paths = create_handoff(arguments, OUTBOX)
            except Exception as error:
                return tool_result(f"交接卡未生成：{error}", is_error=True)
            message = (
                f"交接卡已保存到公司本地，状态：{card['export_status']}。\n"
                + "\n".join(str(path) for path in paths)
                + "\n本地生成不等于获准外传；请先人工审核。"
            )
            return tool_result(message, {"card": card, "paths": [str(path) for path in paths]})
        if name == "list_shiyuan_company_memory_proposals":
            items = MEMORY.list("candidate", int(arguments.get("limit", 100)))
            return tool_result(json.dumps({"items": items}, ensure_ascii=False, indent=2), {"items": items})
        if name == "review_shiyuan_company_memory":
            try:
                memory = MEMORY.decide(
                    str(arguments.get("memory_id", "")),
                    str(arguments.get("decision", "")),
                    arguments.get("note"),
                )
            except (KeyError, ValueError) as error:
                return tool_result(f"候选审核未完成：{error}", is_error=True)
            return tool_result(json.dumps(memory, ensure_ascii=False, indent=2), memory)
        if name == "recall_shiyuan_company_memory":
            items = MEMORY.recall(
                str(arguments.get("query", "")), int(arguments.get("limit", 8))
            )
            return tool_result(json.dumps({"items": items}, ensure_ascii=False, indent=2), {"items": items})
        if name == "recent_shiyuan_company_work":
            items = MEMORY.recent_work(int(arguments.get("limit", 6)))
            return tool_result(json.dumps({"items": items}, ensure_ascii=False, indent=2), {"items": items})
        if name == "resolve_shiyuan_company_context":
            value = MEMORY.resolve_context(
                str(arguments.get("query", "")), "codex", int(arguments.get("limit", 8))
            )
            return tool_result(json.dumps(value, ensure_ascii=False, indent=2), value)
        if name == "checkpoint_shiyuan_company_work":
            value = MEMORY.record_work_checkpoint(
                "codex", None, str(arguments.get("phase", "progress")),
                str(arguments.get("summary", "")), arguments.get("artifacts"),
                arguments.get("evidence"), arguments.get("next_actions"),
                workstream_id=str(arguments.get("workstream_id", "")),
            )
            if value is None:
                return tool_result("未找到对应公司本地工作流。", is_error=True)
            return tool_result(json.dumps(value, ensure_ascii=False, indent=2), value)
        return tool_result(f"未知工具：{name}", is_error=True)
    raise KeyError(method)


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8-sig")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        if "id" not in request:
            continue
        try:
            result = handle(request.get("method", ""), request.get("params") or {})
            send({"jsonrpc": "2.0", "id": request["id"], "result": result})
        except Exception as error:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {"code": -32601, "message": str(error)},
                }
            )


if __name__ == "__main__":
    main()
