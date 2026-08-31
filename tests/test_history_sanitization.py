from app.history import normalize_visible_messages, sanitize_visible_content


def test_transport_core_context_is_removed_from_visible_history():
    value = (
        "<shiyuan_core_context>内部画像、任务和检索结果</shiyuan_core_context>\n\n"
        "这是用户真正看见的回复。"
    )
    assert sanitize_visible_content(value) == "这是用户真正看见的回复。"
    messages = normalize_visible_messages(
        [
            {"role": "assistant", "content": value},
            {"role": "assistant", "content": "<shiyuan_core_context>只有内部内容</shiyuan_core_context>"},
            {"role": "user", "content": "继续刚才的"},
        ]
    )
    assert messages == [
        {"role": "assistant", "content": "这是用户真正看见的回复。"},
        {"role": "user", "content": "继续刚才的"},
    ]
