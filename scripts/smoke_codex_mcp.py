from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(
        command=str(Path.home() / ".shiyuan" / "venv" / "Scripts" / "python.exe"),
        args=[str(Path.home() / ".shiyuan" / "codex-hook" / "mcp_server.py")],
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            expected = {
                "shiyuan_status",
                "shiyuan_recall",
                "shiyuan_recall_history",
                "shiyuan_resolve_context",
                "shiyuan_checkpoint_work",
                "shiyuan_propose_memory",
                "shiyuan_list_memory_proposals",
                "shiyuan_decide_memory",
                "shiyuan_create_task",
                "shiyuan_get_tasks",
                "shiyuan_report_task",
            }
            missing = sorted(expected - set(names))
            if missing:
                raise RuntimeError(f"Missing MCP tools: {missing}")
            result = await session.call_tool("shiyuan_status", {})
            text = result.content[0].text
            status = json.loads(text)
            if status.get("core") != "十元":
                raise RuntimeError(f"Unexpected Core response: {status}")
            print(f"Codex MCP OK: {len(names)} tools, Core {status['version']}")


if __name__ == "__main__":
    asyncio.run(main())
