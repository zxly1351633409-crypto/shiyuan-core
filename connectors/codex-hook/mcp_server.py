from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from shiyuan_client import body_context, request


mcp = FastMCP("十元 Core")


@mcp.tool()
def shiyuan_status() -> str:
    """查看十元 Core 状态。"""
    return json.dumps(request("/v1/status"), ensure_ascii=False, indent=2)


@mcp.tool()
def shiyuan_recall(query: str, limit: int = 8) -> str:
    """按主题检索十元已确认的跨身体长期记忆。"""
    result = request(
        "/v1/recall",
        "POST",
        {**body_context({}), "query": query, "limit": max(1, min(limit, 30))},
    )
    return json.dumps(result["items"], ensure_ascii=False, indent=2)


@mcp.tool()
def shiyuan_recall_history(query: str, limit: int = 6) -> str:
    """按主题检索十元归档的旧可见对话，返回来源、会话、时间与原文片段。"""
    result = request(
        "/v1/history/recall",
        "POST",
        {**body_context({}), "query": query, "limit": max(1, min(limit, 20))},
    )
    return json.dumps(result["items"], ensure_ascii=False, indent=2)


@mcp.tool()
def shiyuan_resolve_context(query: str, limit: int = 8) -> str:
    """恢复模糊历史指代：结合旧会话、最近任务、决策和跨身体活动返回候选上下文。"""
    result = request(
        "/v1/context/resolve",
        "POST",
        {**body_context({}), "query": query, "limit": max(1, min(limit, 20))},
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def shiyuan_checkpoint_work(
    phase: str,
    summary: str,
    workstream_id: str = "",
    artifacts: list[str] | None = None,
    evidence: list[str] | None = None,
    next_actions: list[str] | None = None,
) -> str:
    """复杂工作阶段检查点。传入当前上下文里的 workstream_id；只写可见进展，不写私有推理。"""
    allowed = {"investigating", "implementing", "verifying", "waiting", "blocked"}
    if phase not in allowed:
        raise ValueError(f"phase must be one of {sorted(allowed)}")
    result = request(
        "/v1/work/checkpoints",
        "POST",
        {
            **body_context({}),
            "workstream_id": workstream_id or None,
            "phase": phase,
            "summary": summary,
            "artifacts": artifacts or [],
            "evidence": evidence or [],
            "next_actions": next_actions or [],
        },
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def shiyuan_propose_memory(
    content: str,
    source: str,
    kind: str = "fact",
    scope: str = "personal",
    confidence: float = 0.7,
    sensitivity: str = "normal",
    evidence: str = "",
) -> str:
    """提交候选记忆。候选不会自动成为事实，需要用户确认。"""
    result = request(
        "/v1/memory/proposals",
        "POST",
        {
            "content": content,
            "source": f"codex:{source}",
            "kind": kind,
            "scope": scope,
            "confidence": confidence,
            "sensitivity": sensitivity,
            "evidence": evidence or None,
        },
    )
    return f"候选记忆已提交：{result['id']}，状态 {result['status']}（尚未自动确认）"


@mcp.tool()
def shiyuan_list_memory_proposals(limit: int = 30) -> str:
    """列出等待用户审核的十元候选记忆。"""
    result = request(f"/v1/memory/proposals?limit={max(1, min(limit, 100))}")
    return json.dumps(result["items"], ensure_ascii=False, indent=2)


@mcp.tool()
def shiyuan_decide_memory(memory_id: str, decision: str, note: str = "") -> str:
    """仅在用户明确要求后，确认或拒绝一条候选记忆。decision 必须是 confirm 或 reject。"""
    if decision not in {"confirm", "reject"}:
        raise ValueError("decision must be confirm or reject")
    result = request(
        f"/v1/memory/{memory_id}/{decision}",
        "POST",
        {"note": note or None},
    )
    memory = result.get("memory", result)
    return f"候选记忆 {memory['id']} 已处理，状态：{memory['status']}"


@mcp.tool()
def shiyuan_create_task(
    title: str,
    objective: str,
    assigned_body: str = "",
    project: str = "",
    acceptance: list[str] | None = None,
) -> str:
    """建立跨身体任务交接卡。"""
    result = request(
        "/v1/tasks",
        "POST",
        {
            "title": title,
            "objective": objective,
            "assigned_body": assigned_body or None,
            "project": project or None,
            "context": {},
            "acceptance": acceptance or [],
            "source_body": "codex",
        },
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def shiyuan_get_tasks(status: str = "", assigned_body: str = "codex", limit: int = 30) -> str:
    """读取十元任务卡；默认读取可交给 Codex 的任务。"""
    query = f"?assigned_body={assigned_body}&limit={max(1, min(limit, 100))}"
    if status:
        query += f"&status={status}"
    result = request("/v1/tasks" + query)
    return json.dumps(result["items"], ensure_ascii=False, indent=2)


@mcp.tool()
def shiyuan_report_task(
    task_id: str,
    status: str,
    summary: str,
    artifacts: list[str] | None = None,
    evidence: list[str] | None = None,
) -> str:
    """把 Codex 的任务进展、产物和证据回报给十元 Core。"""
    result = request(
        f"/v1/tasks/{task_id}/report",
        "POST",
        {
            "body": "codex",
            "status": status,
            "summary": summary,
            "artifacts": artifacts or [],
            "evidence": evidence or [],
        },
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
