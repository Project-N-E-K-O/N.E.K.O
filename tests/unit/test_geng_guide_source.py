from knowledge.importers.geng_guide import load_geng_guide_markdown


def test_geng_guide_import_keeps_summary_highlights_and_tags_but_not_questions():
    entries = load_geng_guide_markdown(
        """1:23
夺笋是什么梗【梗指南】
Summary
用于调侃说话损。
Highlights
常在玩笑语境中使用。#阴阳怪气 #梗
Questions
这是不是人身攻击？
""".encode("utf-8")
    )

    assert len(entries) == 1
    entry = entries[0]
    assert entry.title == "夺笋"
    assert entry.summary == "用于调侃说话损。"
    assert "常在玩笑语境中使用。" in entry.content
    assert "这是不是人身攻击" not in entry.content
    assert entry.terms["alias"] == ()
    assert "topic:阴阳怪气" in entry.tags
    assert "source:geng-guide" in entry.tags


def test_geng_guide_import_is_stable_for_a_repeated_export():
    raw = """1:23
遮遮乐是什么意思【梗指南】
摘要
调侃遮挡内容的表达。
""".encode("utf-8")

    first, second = load_geng_guide_markdown(raw), load_geng_guide_markdown(raw)

    assert first[0].title == second[0].title
    assert first[0].content_hash == second[0].content_hash
