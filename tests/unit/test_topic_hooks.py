from main_logic.topic.hooks import build_topic_hook_prompt


def test_build_topic_hook_prompt_renders_old_memory_cues_only():
    prompt = build_topic_hook_prompt(
        lang="zh-CN",
        followup_topics=[
            {"id": "r1", "text": "用户最近在纠结直播里的角色风格和吐槽尺度"},
        ],
    )

    assert prompt.startswith("回忆线索：以下旧话题距今较久，可顺手接、但没必要主动提出。")
    assert "======" not in prompt
    assert "较久前的回忆线索" in prompt
    assert "直播里的角色风格" in prompt
    assert "旧话题距今较久" in prompt
    assert "顺手接" in prompt
    assert "主动提出" in prompt
    # No leaked scheduling jargon or self-contradicting count, and an
    # encouraging framing rather than the old "better none than forced".
    assert "低频" not in prompt
    assert "触发频率" not in prompt
    assert "宁可不用" not in prompt
    assert "刚才没聊完" not in prompt
    assert "根据你最近的兴趣" not in prompt
    assert "我注意到你最近" not in prompt


def test_build_topic_hook_prompt_uses_traditional_chinese_header():
    prompt = build_topic_hook_prompt(
        lang="zh-TW",
        followup_topics=[{"text": "最近想用繁體中文聊城市流行"}],
    )

    assert prompt.startswith("回憶線索：以下舊話題距今較久，可順手接、但沒必要主動提出。")
    assert "較久前的回憶線索" in prompt
    assert "回忆线索：" not in prompt
    assert "低頻" not in prompt


def test_build_topic_hook_prompt_returns_empty_without_candidates():
    assert build_topic_hook_prompt(
        lang="zh-CN",
        followup_topics=[],
    ) == ""


def test_build_topic_hook_prompt_preserves_supported_non_english_locale():
    prompt = build_topic_hook_prompt(
        lang="ja",
        followup_topics=[{"text": "さっきの転職の話が少し残っている"}],
    )

    assert prompt.startswith("記憶の手がかり：")
    assert "古い記憶の手がかり" in prompt
    assert "Memory cues:" not in prompt
