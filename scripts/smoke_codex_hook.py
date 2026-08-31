from __future__ import annotations

import json
import subprocess
from pathlib import Path


def main() -> None:
    home = Path.home()
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
        "Codex 身体",
        "本轮十元 Core 已连接",
        "关于十元所服务的人",
        "用户画像（包含已确认事实与明确标注的待验证判断）",
        "十元开发状态",
        "十元知情范围与新鲜度",
        "未接入来源和未授权公司原文仍未知",
        "用户反复纠正（高优先级操作规则，不是人格事实）",
        "当前理解重点（要体现在回应里，不要原样复述给用户）",
        "不要向用户证明十元保存了多少资料",
        "跨会话先恢复用户过去的明确建议与纠正",
        "模糊指代候选",
        "最近工作与跨身体活动",
        "自上次读取后的其他身体活动",
        "最近任务报告",
        "面前有一个活着的对话者",
        "先真实反应，再说事情",
        "轻轻打趣用户、自己或当前的麻烦",
        "可爱和元气主要来自反应、节奏和机灵感",
        "🐳 十元在线",
    ]
    missing = [item for item in expected if item not in context]
    if specific.get("hookEventName") != "UserPromptSubmit" or missing:
        raise RuntimeError(f"Codex Hook context check failed; missing={missing}")
    print(f"Codex Hook OK: current-turn Core context loaded ({len(context)} chars)")


if __name__ == "__main__":
    main()
