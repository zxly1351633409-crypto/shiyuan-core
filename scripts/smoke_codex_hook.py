from __future__ import annotations

import json
import subprocess
from pathlib import Path


def main() -> None:
    home = Path.home()
    config = json.loads((home / ".shiyuan" / "client.json").read_text(encoding="utf-8"))
    assistant_name = str(config.get("assistant_name") or "我的助手")
    python = home / ".shiyuan" / "venv" / "Scripts" / "python.exe"
    hook = home / ".shiyuan" / "codex-hook" / "codex_hook.py"
    payload = {
        "session_id": "shiyuan-codex-smoke",
        "cwd": str(Path.cwd()),
        "hook_event_name": "UserPromptSubmit",
        # Empty prompt avoids writing a synthetic user message event to Core.
        "prompt": "",
    }
    completed = subprocess.run(
        [str(python), str(hook), "UserPromptSubmit"],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=10,
    )
    result = json.loads(completed.stdout)
    specific = result.get("hookSpecificOutput") or {}
    context = specific.get("additionalContext") or ""
    expected = [
        "<shiyuan_core_context>",
        f"你当前是{assistant_name}使用的 Codex 身体",
        f"本轮{assistant_name} Core已连接",
        "用户画像（包含已确认事实与明确标注的待验证判断）",
        f"{assistant_name}开发状态",
        f"{assistant_name}知情范围与新鲜度",
        "未接入来源和未授权公司原文仍未知",
        "用户反复纠正（高优先级操作规则，不是人格事实）",
        "当前理解重点（要体现在回应里，不要原样复述给用户）",
        "模糊指代候选",
        "最近工作与跨身体活动",
        "自上次读取后的其他身体活动",
        "最近任务报告",
        f"🐳 {assistant_name}在线",
    ]
    missing = [item for item in expected if item not in context]
    if specific.get("hookEventName") != "UserPromptSubmit" or missing:
        raise RuntimeError(f"Codex Hook context check failed; missing={missing}")
    print(f"Codex Hook OK: {assistant_name} context loaded ({len(context)} chars)")


if __name__ == "__main__":
    main()
