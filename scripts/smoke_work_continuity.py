from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "work-continuity-smoke-token-long-enough"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(url: str, route: str, method: str = "GET", body: dict | None = None) -> dict:
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{url}{route}", data=payload, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def run_hook(config: Path, event: str, payload: dict) -> str:
    env = os.environ.copy()
    env["SHIYUAN_CLIENT_CONFIG"] = str(config)
    completed = subprocess.run(
        [sys.executable, str(ROOT / "connectors" / "codex-hook" / "codex_hook.py"), event],
        input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True,
        encoding="utf-8", env=env, check=True, timeout=10,
    )
    return completed.stdout


def main() -> None:
    port = free_port()
    core_url = f"http://127.0.0.1:{port}"
    # Windows may hold SQLite WAL handles for a moment after the temporary
    # uvicorn process exits. A cleanup race must not hide the E2E result.
    with tempfile.TemporaryDirectory(prefix="shiyuan-work-e2e-", ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment.update({
            "SHIYUAN_DATA_DIR": str(root / "data"), "SHIYUAN_CORE_TOKEN": TOKEN,
            "SHIYUAN_HOST": "127.0.0.1", "SHIYUAN_PORT": str(port), "PYTHONPATH": str(ROOT),
        })
        server = subprocess.Popen(
            [sys.executable, "-m", "app.main"], cwd=ROOT, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        )
        try:
            for _ in range(50):
                try:
                    with urllib.request.urlopen(f"{core_url}/health", timeout=1) as response:
                        if json.loads(response.read().decode("utf-8"))["ok"]:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("temporary Core did not become ready")

            client_config = root / "client.json"
            client_config.write_text(json.dumps({
                "core_url": core_url, "token": TOKEN, "body": "codex", "device": "e2e",
                "timeout_seconds": 2, "capture_messages": True,
            }), encoding="utf-8")
            started = json.loads(run_hook(client_config, "UserPromptSubmit", {
                "session_id": "codex-e2e", "turn_id": "codex-turn", "cwd": str(ROOT),
                "prompt": "请实现跨身体接续接口",
            }))
            assert "最近工作与跨身体活动" in started["hookSpecificOutput"]["additionalContext"]
            stopped = json.loads(run_hook(client_config, "Stop", {
                "session_id": "codex-e2e", "turn_id": "codex-turn", "cwd": str(ROOT),
                "last_assistant_message": "Codex 接口已经完成。\n\n验证：pytest 全部通过。",
            }))
            assert stopped == {}

            hana_home = root / "hana"
            config_dir = hana_home / "plugin-data" / "shiyuan-hook"
            config_dir.mkdir(parents=True)
            (config_dir / "config.json").write_text(json.dumps({
                "coreUrl": core_url, "token": TOKEN, "body": "hana", "device": "e2e",
                "timeoutMs": 2000, "captureMessages": True,
            }), encoding="utf-8")
            node_env = os.environ.copy()
            node_env.update({
                "HANA_HOME": str(hana_home),
                "SHIYUAN_HANA_SOURCE": str(ROOT / "connectors" / "hana-hook" / "extensions" / "shiyuan-context.js"),
            })
            hana = subprocess.run(
                ["node", str(ROOT / "scripts" / "smoke_work_hana.mjs")],
                capture_output=True, text=True, encoding="utf-8", env=node_env, check=True, timeout=15,
            )
            assert json.loads(hana.stdout)["ok"] is True
            recent = request(core_url, "/v1/work/recent?limit=2")
            latest = recent["items"][0]
            assert latest["latest_receipt"]["body"] == "hana"
            assert latest["latest_receipt"]["result_summary"] == "已完成 Hana 反向接续。\n\n验证：Hana Hook 测试通过。"
            assert len(latest["latest_receipt"]["result_summary"]) <= 900
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
    print("Work continuity E2E OK: Codex -> Core -> Hana -> Core, structured receipts only")


if __name__ == "__main__":
    main()
