from app.correction_memory import extract_operational_corrections
from app.database import Database


def categories(text: str) -> set[str]:
    return {item.category for item in extract_operational_corrections(text)}


def test_extracts_only_bounded_cross_task_corrections():
    assert "response_human_warm" in categories(
        "以后回复不要冷冰冰的，要更有人味、可爱和元气，多用短句分段。"
    )
    assert "memory_continuity" in categories(
        "不同会话总让我重复同样的建议，说明你没有记住以前的纠正。"
    )
    assert "storage_hygiene" in categories(
        "这是一个警告：不要把产物散落在共享盘根目录，要放统一文件夹。"
    )
    assert extract_operational_corrections("帮我看看今天北京天气") == []


def test_extracts_felt_understanding_as_experience_rule():
    items = extract_operational_corrections(
        "我总感觉你在别的会话里面并没有很懂我的感受和反馈。"
    )
    item = next(value for value in items if value.category == "felt_understanding")
    assert item.explicit is True
    assert "档案数量" in item.rationale
    assert "少解释一次" in item.success_signal
    assert "emoji" in item.anti_pattern


def test_canonical_correction_does_not_copy_paths_or_raw_text():
    source = r"以后不要把脚本散落在 D:\Private 根目录，应归入统一文件夹。"
    item = extract_operational_corrections(source)[0]
    assert "D:\\Private" not in item.content
    assert item.explicit is True


def test_internal_acceptance_prompts_do_not_reinforce_user_corrections():
    assert extract_operational_corrections(
        "十元内部纠正记忆接收验收。请列出 memory_continuity 和 evidence_before_completion。"
    ) == []


def test_open_v2_extracts_new_identity_boundary_without_predeclared_category():
    items = extract_operational_corrections("你是十元，不是小鲸鱼，不要混淆了。")
    assert len(items) == 1
    item = items[0]
    assert item.category.startswith("open_")
    assert item.origin == "open-v2"
    assert item.scope == "global"
    assert item.explicit is True
    assert "十元的身份是十元" in item.content
    assert "小鲸鱼" in item.content


def test_open_v2_rejects_temporary_work_and_redacts_sensitive_fragments():
    assert extract_operational_corrections("普通临时任务不应该被记住。") == []
    assert extract_operational_corrections("这次不要修改这个文件。") == []
    item = extract_operational_corrections(
        r"以后十元不要在回复里展示 C:\secret\note.txt 或 token=abcdefghijklmnop。"
    )[0]
    assert r"C:\secret" not in item.content
    assert "abcdefghijklmnop" not in item.content
    assert "[本地路径已省略]" in item.content
    assert "[凭据已省略]" in item.content


def test_open_v2_infers_body_scope():
    item = extract_operational_corrections(
        "以后在 Hana 里称呼十元为十元，不要使用其他代号。",
        body="codex",
    )[0]
    assert item.scope == "body:hana"

    project_item = extract_operational_corrections(
        "以后在本项目里十元必须使用海棠作为状态代号。",
        project=r"C:\company-secret\Project-X",
    )[0]
    assert project_item.scope.startswith("project:")
    assert "company-secret" not in project_item.scope
    assert "Project-X" not in project_item.scope


def test_open_v2_deduplicates_rephrases_and_supersedes_explicit_conflicts(tmp_path):
    database = Database(tmp_path / "core.sqlite3")
    database.initialize()

    def observe(text: str, source_key: str, session_id: str):
        item = extract_operational_corrections(text)[0]
        return database.observe_operational_correction(
            category=item.category,
            content=item.content,
            priority=item.priority,
            explicit=item.explicit,
            source_key=source_key,
            source_hash=source_key * 8,
            body="codex",
            device="test",
            session_id=session_id,
            event_id=None,
            scope=item.scope,
            origin=item.origin,
            content_fingerprint=item.content_fingerprint,
            conflict_key=item.conflict_key,
            polarity=item.polarity,
        )

    first, _ = observe("以后十元的回复必须带简短标题。", "first", "s1")
    repeat, _ = observe("后续十元的回复需要带简短标题。", "repeat", "s2")
    assert repeat["id"] == first["id"]
    replacement, _ = observe("以后十元的回复不要带简短标题。", "replacement", "s3")
    assert replacement["id"] != first["id"]
    assert replacement["version"] == 2
    assert replacement["supersedes_id"] == first["id"]
    assert database.list_operational_corrections("active", 10)[0]["id"] == replacement["id"]
    assert database.list_operational_corrections("superseded", 10)[0]["id"] == first["id"]
    assert extract_operational_corrections(
        "[hana_reminder]\n- 工具不可用\n[/hana_reminder]\n\n"
        "十元内部测试：新会话连通性，请简短回复连接状态。"
    ) == []


def test_exact_evidence_cleanup_recomputes_rule_counts(tmp_path):
    database = Database(tmp_path / "core.sqlite3")
    database.initialize()
    for key, session in (("real", "user-session"), ("synthetic", "eval-session")):
        database.observe_operational_correction(
            category="memory_continuity",
            content="跨会话先恢复用户纠正。",
            priority=100,
            explicit=key == "real",
            source_key=key,
            source_hash=key * 16,
            body="hana",
            device="test",
            session_id=session,
            event_id=None,
        )
    assert database.list_operational_corrections("active", 5)[0]["evidence_count"] == 2
    result = database.remove_operational_correction_evidence(["synthetic"])
    assert result["deleted_evidence"] == 1
    stored = database.list_operational_corrections("active", 5)[0]
    assert stored["evidence_count"] == 1
    assert stored["session_count"] == 1
    assert stored["latest_session_id"] == "user-session"
