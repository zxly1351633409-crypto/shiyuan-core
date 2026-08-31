from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT_PATH = ROOT / "connectors" / "codex-hook" / "shiyuan_client.py"


def load_client():
    spec = importlib.util.spec_from_file_location("shiyuan_client_offline_test", CLIENT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_codex_offline_queue_replays_in_order(tmp_path, monkeypatch):
    client = load_client()
    config_path = tmp_path / "client.json"
    outbox = tmp_path / "ordered-outbox"
    config_path.write_text(
        json.dumps(
            {
                "core_url": "http://127.0.0.1:9",
                "token": "test-token",
                "body": "codex",
                "offline_outbox_dir": str(outbox),
                "timeout_seconds": 0.05,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHIYUAN_CLIENT_CONFIG", str(config_path))
    client._FLUSH_ATTEMPTED = True

    def offline(*_args, **_kwargs):
        raise OSError("isolated offline test")

    monkeypatch.setattr(client, "request", offline)
    assert client.safe_request(
        "/v1/events", "POST", {"summary": "first", "idempotency_key": "one"},
        queue_on_failure=True,
    ) is None
    assert client.safe_request(
        "/v1/history/messages", "POST", {"summary": "second", "idempotency_key": "two"},
        queue_on_failure=True,
    ) is None
    assert client.offline_outbox_status()["pending"] == 2

    replayed = []

    def online(route, method="GET", body=None, **_kwargs):
        replayed.append((route, method, body))
        return {"ok": True}

    monkeypatch.setattr(client, "request", online)
    result = client.flush_offline_outbox()
    assert result == {"replayed": 2, "remaining": 0}
    assert [item[2]["summary"] for item in replayed] == ["first", "second"]
    assert client.offline_outbox_status()["pending"] == 0


def test_context_cache_preserves_memory_but_disables_online_marker(tmp_path, monkeypatch):
    client = load_client()
    config_path = tmp_path / "client.json"
    config_path.write_text(
        json.dumps(
            {
                "core_url": "http://127.0.0.1:9",
                "token": "test-token",
                "body": "codex",
                "context_cache_path": str(tmp_path / "context-cache.json"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHIYUAN_CLIENT_CONFIG", str(config_path))
    bootstrap = {
        "core": "海棠",
        "identity": "十元身份",
        "user_profile": "用户希望少重复解释。",
        "understanding_brief": {
            "principle": "让反馈改变行动。",
            "focuses": [
                {
                    "category": "felt_understanding",
                    "directive": "不要只复述画像。",
                    "why_it_matters": "用户要感到被认出。",
                    "success_signal": "主动接上旧反馈。",
                    "avoid": "空泛说我懂。",
                }
            ],
            "self_check": ["是否让用户少解释一次？"],
        },
        "response_style": {
            "mode": "canary",
            "marker": "🐳 十元在线",
            "instruction": "保持温暖；在线时附加标记。",
        },
    }
    client.save_context_cache(bootstrap, {"items": []}, {})
    cached = client.load_context_cache()
    assert cached and cached["bootstrap"]["user_profile"] == "用户希望少重复解释。"
    rendered = client.format_context(
        cached["bootstrap"],
        cached["recall"],
        cached["history_recall"],
        {"pending": 2},
        core_online=False,
        cached_at=cached["saved_at"],
    )
    assert "用户要感到被认出" in rendered
    assert "本机只读缓存" in rendered
    assert "绝对不要附加在线标记" in rendered
