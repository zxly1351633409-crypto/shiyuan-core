import importlib
import os
import sqlite3

from fastapi.testclient import TestClient


def make_client(tmp_path):
    os.environ["SHIYUAN_DATA_DIR"] = str(tmp_path)
    os.environ["SHIYUAN_CORE_TOKEN"] = "test-token-that-is-definitely-long-enough"
    import app.main

    importlib.reload(app.main)
    return TestClient(app.main.app), app.main.settings.token


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_memory_task_and_event_flow(tmp_path):
    client, token = make_client(tmp_path)
    with client:
        assert client.get("/health").json()["ok"] is True
        assert client.get("/health").json()["auto_memory"] == "conservative-candidate"
        assert client.get("/v1/status").status_code == 401

        bootstrap = client.post(
            "/v1/bootstrap",
            headers=auth(token),
            json={"body": "codex", "device": "test"},
        )
        assert bootstrap.status_code == 200
        assert bootstrap.json()["core"] == "十元"
        initial_coverage = bootstrap.json()["knowledge_coverage"]
        assert initial_coverage["memory"] == {}
        assert initial_coverage["operational_corrections"] == {}
        assert initial_coverage["history"] == {"sessions": 0, "messages": 0, "sources": []}
        assert "private-reasoning-excluded" in initial_coverage["limits"]
        assert "候选记忆不是事实" in bootstrap.json()["identity"]
        assert "用户画像" in bootstrap.json()["user_profile"]
        assert "当前版本：Core v0.3.7" in bootstrap.json()["development_status"]
        assert client.get("/health").json()["work_continuity"] == "structured-receipts"
        assert client.get("/health").json()["history"] == "layered-visible-transcripts"
        assert client.get("/health").json()["history_retrieval"] == "keyword"
        core_status = client.get("/v1/status", headers=auth(token)).json()
        assert core_status["history_retrieval"]["mode"] == "keyword"
        assert core_status["decision_graph"]["available"] is False
        decisions = client.post(
            "/v1/decisions/search",
            headers=auth(token),
            json={"body": "codex", "device": "test", "query": "为什么放弃旧方案", "limit": 8},
        )
        assert decisions.status_code == 200
        assert decisions.json()["items"] == []
        assert decisions.json()["confirmed"] is False
        progress_path = tmp_path / "vault" / "90 System" / "开发状态.md"
        assert progress_path.exists()
        progress_path.write_text("# 动态开发状态\n\n- 当前重点：测试热读取。\n", encoding="utf-8")
        progress_refreshed = client.post(
            "/v1/bootstrap",
            headers=auth(token),
            json={"body": "codex", "device": "test"},
        ).json()
        assert "测试热读取" in progress_refreshed["development_status"]
        profile_path = tmp_path / "vault" / "00 Identity" / "用户画像.md"
        profile_path.write_text("# 动态用户画像\n\n- 这是 Vault 中的实时版本。\n", encoding="utf-8")
        refreshed = client.post(
            "/v1/bootstrap",
            headers=auth(token),
            json={"body": "hana", "device": "test"},
        ).json()
        assert "这是 Vault 中的实时版本" in refreshed["user_profile"]
        assert bootstrap.json()["response_style"]["mode"] == "canary"
        assert bootstrap.json()["response_style"]["marker"] == "🐳 十元在线"
        style_instruction = bootstrap.json()["response_style"]["instruction"]
        assert "先给真实结果" in style_instruction
        assert "不要模仿第三方角色" in style_instruction
        assert "安全、隐私、费用和故障场景先说明风险" in style_instruction
        assert len(style_instruction) < 1500
        confirmed_snapshot = tmp_path / "vault" / "10 Memory" / "Confirmed" / "已确认记忆.md"
        assert confirmed_snapshot.exists()
        assert "十元已确认记忆" in confirmed_snapshot.read_text(encoding="utf-8")

        preferences = client.put(
            "/v1/preferences",
            headers=auth(token),
            json={"response_style_mode": "off", "response_marker": "🐳 十元在线"},
        )
        assert preferences.status_code == 200
        assert preferences.json()["mode"] == "off"
        assert client.get("/v1/preferences", headers=auth(token)).json()["mode"] == "off"

        client.put(
            "/v1/preferences",
            headers=auth(token),
            json={"response_style_mode": "canary", "response_marker": "🐳 十元在线"},
        )

        proposal = client.post(
            "/v1/memory/proposals",
            headers=auth(token),
            json={
                "kind": "preference",
                "content": "测试时喜欢先查看验证证据。",
                "source": "pytest",
            },
        ).json()
        assert proposal["status"] == "candidate"

        confirmed = client.post(
            f"/v1/memory/{proposal['id']}/confirm",
            headers=auth(token),
            json={"note": "测试确认"},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["memory"]["status"] == "confirmed"
        assert "测试时喜欢先查看验证证据" in confirmed_snapshot.read_text(encoding="utf-8")

        recalled = client.post(
            "/v1/recall",
            headers=auth(token),
            json={"body": "hana", "device": "test", "query": "验证证据"},
        ).json()
        assert any("验证证据" in item["content"] for item in recalled["items"])

        task = client.post(
            "/v1/tasks",
            headers=auth(token),
            json={
                "title": "验证任务卡",
                "objective": "证明跨身体任务接口可用",
                "assigned_body": "codex",
                "source_body": "hana",
                "acceptance": ["状态可回报"],
            },
        ).json()
        report = client.post(
            f"/v1/tasks/{task['id']}/report",
            headers=auth(token),
            json={"body": "codex", "status": "completed", "summary": "已验证"},
        )
        assert report.status_code == 200
        reports_bootstrap = client.post(
            "/v1/bootstrap",
            headers=auth(token),
            json={"body": "hana", "device": "test"},
        ).json()
        assert reports_bootstrap["recent_task_reports"][0]["task_title"] == "验证任务卡"

        event = client.post(
            "/v1/events",
            headers=auth(token),
            json={"event_type": "test", "body": "codex", "idempotency_key": "same"},
        )
        duplicate = client.post(
            "/v1/events",
            headers=auth(token),
            json={"event_type": "test", "body": "codex", "idempotency_key": "same"},
        )
        assert event.json()["id"] == duplicate.json()["id"]

        automatic = client.post(
            "/v1/events",
            headers=auth(token),
            json={
                "event_type": "user_prompt",
                "body": "codex",
                "summary": "我喜欢先看验证证据。普通临时任务不应该被记住。",
                "idempotency_key": "auto-memory-same",
            },
        ).json()
        assert [item["content"] for item in automatic["memory_candidates"]] == ["我喜欢先看验证证据"]

        correction_event = client.post(
            "/v1/events",
            headers=auth(token),
            json={
                "event_type": "user_prompt",
                "body": "codex",
                "device": "test",
                "session_id": "correction-session",
                "summary": "以后回复不要冷冰冰的，要更有人味、可爱和元气，多用短句分段。",
                "idempotency_key": "operational-correction-style",
            },
        ).json()
        assert correction_event["operational_corrections_updated"][0]["status"] == "active"
        assert correction_event["operational_corrections_updated"][0]["evidence_count"] == 1
        active_corrections = client.get(
            "/v1/corrections", headers=auth(token)
        ).json()["items"]
        assert active_corrections[0]["category"] == "response_human_warm"
        assert "冷冰冰" not in active_corrections[0]["content"]
        correction_bootstrap = client.post(
            "/v1/bootstrap",
            headers=auth(token),
            json={"body": "hana", "device": "test", "session_id": "fresh-session"},
        ).json()
        assert correction_bootstrap["operational_corrections"][0]["category"] == "response_human_warm"
        assert correction_bootstrap["knowledge_coverage"]["operational_corrections"]["active"]["count"] == 1

        duplicate_correction = client.post(
            "/v1/events",
            headers=auth(token),
            json={
                "event_type": "user_prompt",
                "body": "hana",
                "device": "test",
                "session_id": "another-session",
                "summary": "以后回复不要冷冰冰的，要更有人味、可爱和元气，多用短句分段。",
                "idempotency_key": "operational-correction-style",
            },
        ).json()
        assert duplicate_correction["operational_corrections_updated"] == []
        automatic_duplicate = client.post(
            "/v1/events",
            headers=auth(token),
            json={
                "event_type": "user_prompt",
                "body": "hana",
                "summary": "我喜欢先看验证证据。",
                "idempotency_key": "auto-memory-other-event",
            },
        ).json()
        assert automatic_duplicate["memory_candidates"] == []

        question = client.post(
            "/v1/events",
            headers=auth(token),
            json={
                "event_type": "user_prompt",
                "body": "hana",
                "summary": "我喜欢什么？我希望 password=example 被记住。",
                "idempotency_key": "auto-memory-blocked",
            },
        ).json()
        assert question["memory_candidates"] == []

        proposals = client.get("/v1/memory/proposals", headers=auth(token)).json()["items"]
        auto_proposal = next(item for item in proposals if item["content"] == "我喜欢先看验证证据")
        assert auto_proposal["status"] == "candidate"
        assert auto_proposal["fingerprint"]
        assert "完整提示词" not in (auto_proposal.get("evidence") or "")
        not_yet_recalled = client.post(
            "/v1/recall",
            headers=auth(token),
            json={"body": "codex", "device": "test", "query": "先看验证证据"},
        ).json()
        assert not any(item["id"] == auto_proposal["id"] for item in not_yet_recalled["items"])

        client.post(
            f"/v1/memory/{auto_proposal['id']}/confirm",
            headers=auth(token),
            json={"note": "测试中明确确认"},
        )
        recalled_auto = client.post(
            "/v1/recall",
            headers=auth(token),
            json={"body": "hana", "device": "test", "query": "先看验证证据"},
        ).json()
        assert any(item["id"] == auto_proposal["id"] for item in recalled_auto["items"])


def test_cross_body_work_continuity(tmp_path):
    client, token = make_client(tmp_path)
    with client:
        codex_context = {
            "body": "codex",
            "device": "home",
            "session_id": "codex-session",
            "project": "F:/project",
        }
        started = client.post(
            "/v1/work/turn-start",
            headers=auth(token),
            json={**codex_context, "prompt": "请实现跨身体工作连续性", "turn_id": "turn-1"},
        ).json()
        assert started["mode"] == "work"
        assert started["workstream"]["owner_body"] == "codex"
        workstream_id = started["workstream"]["id"]

        hana_view = client.post(
            "/v1/bootstrap",
            headers=auth(token),
            json={"body": "hana", "device": "home"},
        ).json()
        assert hana_view["recent_work"][0]["id"] == workstream_id
        assert hana_view["recent_work"][0]["status"] == "running"
        assert hana_view["recent_work"][0]["is_active"] is True
        coverage = hana_view["knowledge_coverage"]
        assert coverage["work"]["active"] == 1
        assert coverage["work"]["stale"] == 0
        assert any(
            item["body"] == "codex" and item["device"] == "home" and item["active_work"] == 1
            for item in coverage["work"]["bodies"]
        )

        checkpoint = client.post(
            "/v1/work/checkpoints",
            headers=auth(token),
            json={
                **codex_context,
                "workstream_id": workstream_id,
                "turn_id": "turn-1",
                "phase": "implementing",
                "summary": "接口表结构已经完成，正在编写跨身体测试。",
                "artifacts": ["F:/project/app/database.py"],
                "evidence": ["schema 初始化通过"],
                "next_actions": ["运行 pytest"],
                "idempotency_key": "checkpoint-turn-1",
            },
        ).json()
        assert checkpoint["stored"] is True
        assert checkpoint["checkpoint"]["payload"]["phase"] == "implementing"
        hana_updates = client.post(
            "/v1/work/catch-up",
            headers=auth(token),
            json={"body": "hana", "device": "home", "limit": 30, "advance": True},
        ).json()
        assert any(
            item["kind"] == "checkpoint" and "接口表结构" in item["summary"]
            for item in hana_updates["items"]
        )
        assert client.post(
            "/v1/work/catch-up",
            headers=auth(token),
            json={"body": "hana", "device": "home", "limit": 30, "advance": True},
        ).json()["items"] == []

        inquiry = client.post(
            "/v1/work/turn-start",
            headers=auth(token),
            json={"body": "hana", "device": "home", "session_id": "hana-view", "prompt": "Codex 做了什么？"},
        ).json()
        assert inquiry["mode"] == "inquiry"
        assert inquiry["workstream"] is None
        assert inquiry["recent_work"][0]["owner_body"] == "codex"

        conflict = client.post(
            "/v1/work/turn-start",
            headers=auth(token),
            json={"body": "hana", "device": "home", "session_id": "hana-session", "prompt": "继续刚才的"},
        ).json()
        assert conflict["lease_conflict"] is True
        assert conflict["workstream"]["owner_body"] == "codex"

        receipt_body = {
            **codex_context,
            "turn_id": "turn-1",
            "status": "waiting",
            "result_summary": "接口已经写好，等待另一个身体继续验证。",
            "decisions": ["采用结构化回执"],
            "artifacts": ["F:/project/app/work_state.py"],
            "evidence": ["pytest 接口测试通过"],
            "next_actions": ["Hana 继续做反向接续测试"],
            "idempotency_key": "receipt-turn-1",
        }
        receipt = client.post("/v1/work/receipts", headers=auth(token), json=receipt_body).json()
        duplicate = client.post("/v1/work/receipts", headers=auth(token), json=receipt_body).json()
        assert receipt["stored"] is True
        assert receipt["receipt"]["id"] == duplicate["receipt"]["id"]

        continued = client.post(
            "/v1/work/turn-start",
            headers=auth(token),
            json={"body": "hana", "device": "home", "session_id": "hana-session", "prompt": "继续刚才的"},
        ).json()
        assert continued["lease_conflict"] is False
        assert continued["workstream"]["id"] == workstream_id
        assert continued["workstream"]["owner_body"] == "hana"

        hana_receipt = client.post(
            "/v1/work/receipts",
            headers=auth(token),
            json={
                "body": "hana", "device": "home", "session_id": "hana-session",
                "status": "completed", "result_summary": "反向接续测试已完成。",
                "artifacts": [], "evidence": ["Hana 接续成功"], "next_actions": [],
                "idempotency_key": "hana-receipt",
            },
        ).json()
        assert hana_receipt["receipt"]["workstream_id"] == workstream_id
        codex_view = client.post(
            "/v1/bootstrap",
            headers=auth(token),
            json={"body": "codex", "device": "home"},
        ).json()
        assert codex_view["recent_work"][0]["latest_receipt"]["body"] == "hana"
        assert codex_view["recent_work"][0]["latest_receipt"]["result_summary"] == "反向接续测试已完成。"


def test_work_receipt_compactor_is_bounded():
    from app.work_state import classify_prompt, compact_assistant_message

    assert classify_prompt("Codex 刚才做了什么？") == "inquiry"
    assert classify_prompt("继续刚才的") == "continuation"
    assert classify_prompt("你来接手并继续") == "transfer"
    assert classify_prompt("请修复这个问题") == "work"
    assert classify_prompt("你好呀") == "chat"
    raw = "已完成接口实现。\n\n验证：pytest 全部通过。\n\n下一步：需要重启 Hana。\n\n" + "原文" * 1000 + "\n\n🐳 十元在线"
    compact = compact_assistant_message(raw)
    assert len(compact["result_summary"]) <= 900
    assert "🐳 十元在线" not in compact["result_summary"]
    assert compact["status"] == "waiting"
    assert any("pytest" in item for item in compact["evidence"])
    assert any("重启 Hana" in item for item in compact["next_actions"])


def test_expired_running_work_is_reported_as_stale(tmp_path):
    client, token = make_client(tmp_path)
    with client:
        started = client.post(
            "/v1/work/turn-start",
            headers=auth(token),
            json={
                "body": "hana",
                "device": "home",
                "session_id": "stale-session",
                "prompt": "请扫描当前项目",
            },
        ).json()
        workstream_id = started["workstream"]["id"]
        import app.main

        with app.main.db.connect() as connection:
            connection.execute(
                "UPDATE workstreams SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",
                (workstream_id,),
            )
        item = client.get("/v1/work/recent?limit=1", headers=auth(token)).json()["items"][0]
        assert item["status"] == "running"
        assert item["effective_status"] == "stale"
        assert item["is_active"] is False


def test_layered_history_import_recall_and_idempotence(tmp_path):
    client, token = make_client(tmp_path)
    payload = {
        "source": "hana",
        "source_session_id": "hana-history-1",
        "title": "几何节点教学版",
        "source_locator": ".hanako/agents/hanako/sessions/example.jsonl",
        "source_fingerprint": "abc123",
        "started_at": "2026-08-20T10:00:00Z",
        "ended_at": "2026-08-20T10:05:00Z",
        "messages": [
            {"role": "user", "content": "请把这个几何节点项目整理成中文教学版。"},
            {"role": "assistant", "content": "已保留原节点结构，并补上可追溯的中文讲解。"},
        ],
    }
    with client:
        inserted = client.post("/v1/history/sessions", headers=auth(token), json=payload)
        assert inserted.status_code == 200
        assert inserted.json()["action"] == "inserted"
        session_id = inserted.json()["session"]["id"]

        duplicate = client.post("/v1/history/sessions", headers=auth(token), json=payload)
        assert duplicate.status_code == 200
        assert duplicate.json()["action"] == "skipped"

        status = client.get("/v1/history/status", headers=auth(token)).json()
        assert status["sessions"] == 1
        assert status["messages"] == 2
        assert status["sources"] == {"hana": 1}
        assert status["index"] in {"trigram", "unicode61"}
        coverage = client.get("/v1/coverage", headers=auth(token)).json()
        assert coverage["history"]["sessions"] == 1
        assert coverage["history"]["messages"] == 2
        assert coverage["history"]["sources"][0]["source"] == "hana"
        serialized_coverage = str(coverage)
        assert "几何节点教学版" not in serialized_coverage
        assert ".hanako/agents" not in serialized_coverage

        recalled = client.post(
            "/v1/history/recall",
            headers=auth(token),
            json={"body": "codex", "device": "test", "query": "几何节点教学", "limit": 4},
        ).json()
        assert recalled["items"]
        assert recalled["items"][0]["source"] == "hana"
        assert "中文教学版" in recalled["items"][0]["content"]

        resolved = client.post(
            "/v1/context/resolve",
            headers=auth(token),
            json={"body": "codex", "device": "test", "query": "以前那个后来呢", "limit": 8},
        ).json()
        assert resolved["vague_reference"] is True
        assert resolved["resolution_mode"] == "timeline-and-activity"
        assert resolved["candidate_interpretations"]
        assert any(item["session_id"] == session_id for item in resolved["items"])

        restored = client.get(
            f"/v1/history/sessions/{session_id}?include_messages=true", headers=auth(token)
        ).json()
        assert [item["role"] for item in restored["messages"]] == ["user", "assistant"]
        assert restored["messages"][0]["content"] == payload["messages"][0]["content"]

        append_payload = {
            "source": "hana",
            "source_session_id": "hana-history-1",
            "source_locator": "live-hook/hana",
            "idempotency_key": "history-live-message-key-0001",
            "message": {
                "role": "user",
                "content": "以后还要继续补充这段会话。",
                "timestamp": "2026-08-20T10:06:00Z",
            },
        }
        appended = client.post(
            "/v1/history/messages", headers=auth(token), json=append_payload
        ).json()
        repeated = client.post(
            "/v1/history/messages", headers=auth(token), json=append_payload
        ).json()
        assert appended["action"] == "updated"
        assert repeated["action"] == "skipped"
        restored_live = client.get(
            f"/v1/history/sessions/{session_id}?include_messages=true", headers=auth(token)
        ).json()
        assert [item["content"] for item in restored_live["messages"]].count(
            "以后还要继续补充这段会话。"
        ) == 1


def test_database_migrates_v1_memory_table(tmp_path):
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """CREATE TABLE memories (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL,
                scope TEXT NOT NULL, source TEXT NOT NULL, confidence REAL NOT NULL,
                sensitivity TEXT NOT NULL, evidence TEXT, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )

    from app.database import Database

    database = Database(database_path)
    database.initialize()
    with database.connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(memories)")}
        schema_version = connection.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()["value"]
    assert "fingerprint" in columns
    assert schema_version == "8"
    with database.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {
        "workstreams",
        "work_session_links",
        "work_receipts",
        "work_activity",
        "work_cursors",
        "history_sessions",
        "operational_corrections",
        "operational_correction_evidence",
        "history_chunks",
        "history_chunks_fts",
    } <= tables


def test_open_correction_scope_and_readonly_memory_dashboard(tmp_path):
    client, token = make_client(tmp_path)
    with client:
        page = client.get("/memory-console")
        assert page.status_code == 200
        assert "十元记忆管理台" in page.text
        assert "只读模式" in page.text
        assert "我喜欢先看验证证据" not in page.text
        assert client.get("/v1/memory/dashboard").status_code == 401

        event = client.post(
            "/v1/events",
            headers=auth(token),
            json={
                "event_type": "user_prompt",
                "body": "codex",
                "device": "home",
                "session_id": "identity-boundary",
                "summary": "你是十元，不是小鲸鱼，不要混淆了。",
                "idempotency_key": "open-identity-boundary",
            },
        ).json()
        stored = event["operational_corrections_updated"][0]
        assert stored["origin"] == "open-v2"
        assert stored["status"] == "active"

        client.post(
            "/v1/events",
            headers=auth(token),
            json={
                "event_type": "user_prompt",
                "body": "codex",
                "device": "home",
                "session_id": "hana-only",
                "summary": "以后在 Hana 里称呼十元为十元，不要使用其他代号。",
                "idempotency_key": "open-hana-scope",
            },
        )
        hana = client.post(
            "/v1/bootstrap",
            headers=auth(token),
            json={"body": "hana", "device": "home"},
        ).json()["operational_corrections"]
        codex = client.post(
            "/v1/bootstrap",
            headers=auth(token),
            json={"body": "codex", "device": "home"},
        ).json()["operational_corrections"]
        assert any(item["scope"] == "body:hana" for item in hana)
        assert not any(item["scope"] == "body:hana" for item in codex)

        dashboard = client.get(
            "/v1/memory/dashboard", headers=auth(token)
        ).json()
        assert dashboard["readonly"] is True
        assert any(item["id"] == stored["id"] for item in dashboard["corrections"]["active"])
        assert dashboard["memories"]["confirmed"] == []


def test_schema6_operational_corrections_migrate_without_losing_rules(tmp_path):
    database_path = tmp_path / "schema6.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO meta(key,value) VALUES('schema_version','6');
            CREATE TABLE operational_corrections (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL UNIQUE,
                content TEXT NOT NULL,
                priority INTEGER NOT NULL,
                status TEXT NOT NULL,
                activation_reason TEXT NOT NULL,
                evidence_count INTEGER NOT NULL,
                session_count INTEGER NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                latest_body TEXT NOT NULL,
                latest_device TEXT NOT NULL,
                latest_session_id TEXT,
                latest_event_id TEXT
            );
            CREATE TABLE operational_correction_evidence (
                id TEXT PRIMARY KEY,
                correction_id TEXT NOT NULL REFERENCES operational_corrections(id) ON DELETE CASCADE,
                source_key TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                body TEXT NOT NULL,
                device TEXT NOT NULL,
                session_id TEXT,
                event_id TEXT,
                explicit INTEGER NOT NULL,
                observed_at TEXT NOT NULL,
                UNIQUE(correction_id, source_key)
            );
            INSERT INTO operational_corrections VALUES(
                'old-rule','memory_continuity','跨会话先恢复用户纠正。',100,
                'active','explicit-user-correction',1,1,
                '2026-08-30T00:00:00+00:00','2026-08-30T00:00:00+00:00',
                'codex','home','session-1','event-1'
            );
            """
        )

    from app.database import Database

    database = Database(database_path)
    database.initialize()
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM operational_corrections WHERE id='old-rule'"
        ).fetchone()
        version = connection.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()["value"]
    assert version == "8"
    assert row["content"] == "跨会话先恢复用户纠正。"
    assert row["scope"] == "global"
    assert row["origin"] == "bounded"
    assert row["version"] == 1
    assert {"rationale", "success_signal", "anti_pattern"} <= set(row.keys())


def test_felt_understanding_brief_keeps_reason_success_and_antipattern(tmp_path):
    client, token = make_client(tmp_path)
    with client:
        event = client.post(
            "/v1/events",
            headers=auth(token),
            json={
                "event_type": "user_prompt",
                "body": "codex",
                "device": "home",
                "session_id": "felt-session",
                "summary": "我总感觉你在别的会话里并没有真正懂我的感受和反馈。",
                "idempotency_key": "felt-understanding-real-feedback",
            },
        ).json()
        learned = next(
            item for item in event["operational_corrections_updated"]
            if item["category"] == "felt_understanding"
        )
        assert learned["status"] == "active"
        assert "档案数量" in learned["rationale"]
        assert "少解释一次" in learned["success_signal"]
        assert "空泛说" in learned["anti_pattern"]

        bootstrap = client.post(
            "/v1/bootstrap",
            headers=auth(token),
            json={"body": "hana", "device": "home"},
        ).json()
        assert bootstrap["understanding_brief"]["principle"]
        focus = next(
            item for item in bootstrap["understanding_brief"]["focuses"]
            if item["category"] == "felt_understanding"
        )
        assert focus["why_it_matters"] == learned["rationale"]
        assert focus["success_signal"] == learned["success_signal"]
        assert focus["avoid"] == learned["anti_pattern"]
