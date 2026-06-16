from main_logic.topic.hooks import build_topic_hook_prompt


def test_build_topic_hook_prompt_combines_memory_and_open_threads():
    prompt = build_topic_hook_prompt(
        lang="zh-CN",
        followup_topics=[
            {"id": "r1", "text": "用户最近在纠结直播里的角色风格和吐槽尺度"},
        ],
        open_threads=[
            "用户刚才还没回答：更想要会接梗，还是更想要敢吐槽",
        ],
    )

    assert "低频深话题候选" in prompt
    assert "直播里的角色风格" in prompt
    assert "更想要会接梗" in prompt
    assert "只选一个" in prompt
    assert "先判断" in prompt
    assert "关系深度" in prompt
    assert "宁可不用" in prompt
    assert "多轮" in prompt
    assert "根据你的近期兴趣" not in prompt
    assert "我注意到你最近" not in prompt


def test_build_topic_hook_prompt_uses_traditional_chinese_header():
    prompt = build_topic_hook_prompt(
        lang="zh-TW",
        open_threads=["最近想用繁體中文聊城市流行"],
    )

    assert "低頻深話題候選" in prompt
    assert "關係深度" in prompt
    assert "寧可不用" in prompt
    assert "低频深话题候选" not in prompt
    assert "宁可不用" not in prompt


def test_build_topic_hook_prompt_returns_empty_without_candidates():
    assert build_topic_hook_prompt(
        lang="zh-CN",
        followup_topics=[],
        open_threads=[],
    ) == ""


def test_build_topic_hook_prompt_preserves_supported_non_english_locale():
    prompt = build_topic_hook_prompt(
        lang="ja",
        open_threads=["さっきの転職の話が少し残っている"],
    )

    assert "低頻度の深め話題候補" in prompt
    assert "未完了の話題" in prompt
    assert "Low-frequency deeper topic candidates" not in prompt
