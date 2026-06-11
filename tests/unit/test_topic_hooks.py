from main_logic.topic_hooks import build_topic_hook_prompt


def test_build_topic_hook_prompt_combines_memory_and_open_threads():
    prompt = build_topic_hook_prompt(
        lang="zh-CN",
        recent_topics=[
            "我的车是白色的，不会给我用差漆吧，有色差就不好看了",
        ],
        followup_topics=[
            {"id": "r1", "text": "用户最近在纠结直播里的角色风格和吐槽尺度"},
        ],
        open_threads=[
            "用户刚才还没回答：更想要会接梗，还是更想要敢吐槽",
        ],
    )

    assert "低频深话题候选" in prompt
    assert "刚聊到的点" in prompt
    assert "白色的，不会给我用差漆" in prompt
    assert "直播里的角色风格" in prompt
    assert "更想要会接梗" in prompt
    assert "只选一个" in prompt
    assert "先判断" in prompt
    assert "寒暄" in prompt
    assert "强相关" in prompt
    assert "关系深度" in prompt
    assert "宁可不用" in prompt
    assert "多轮" in prompt
    assert "根据你的近期兴趣" not in prompt
    assert "我注意到你最近" not in prompt
    assert "我们来聊聊" not in prompt


def test_build_topic_hook_prompt_returns_empty_without_candidates():
    assert build_topic_hook_prompt(
        lang="zh-CN",
        recent_topics=[],
        followup_topics=[],
        open_threads=[],
    ) == ""


def test_build_topic_hook_prompt_renders_topic_materials_with_online_hints():
    prompt = build_topic_hook_prompt(
        lang="zh-CN",
        topic_materials=[
            {
                "hook_id": "topic_car_paint",
                "interest": "用户担心白车补漆有色差",
                "hook": "从白车补漆切入，轻轻吐槽一下补漆笔翻车",
                "opening_intent": "一句话抛钩子，不做教程",
                "deepening_hint": "如果用户接话，再聊预算和如何避免被坑",
                "material_hint": {
                    "summary": "找到了白车补漆避坑指南，可以借一个具体点开口。",
                    "links": [
                        {
                            "type": "video",
                            "title": "白车补漆避坑指南",
                            "url": "https://example.test/video",
                        }
                    ],
                },
            }
        ],
    )

    assert "深话题 hook" in prompt
    assert "用户担心白车补漆有色差" in prompt
    assert "白车补漆避坑指南" in prompt
    assert "一句话抛钩子" in prompt
    assert "如果用户接话" in prompt


def test_build_topic_hook_prompt_localizes_topic_material_fields_for_english():
    prompt = build_topic_hook_prompt(
        lang="en",
        topic_materials=[
            {
                "interest": "switching careers",
                "hook": "nudge the career-change hesitation",
                "opening_intent": "one casual sentence",
                "deepening_hint": "follow the user's reaction",
                "online_angle": "mention one concrete salary trend naturally",
                "material_hint": {
                    "summary": "Found a practical career-change checklist.",
                    "links": [
                        {"type": "news", "title": "Career change guide"},
                    ],
                },
            }
        ],
    )

    assert "Relationship point=switching careers" in prompt
    assert "Entry hook=nudge the career-change hesitation" in prompt
    assert "Online material=Found a practical career-change checklist." in prompt
    assert "Source titles=news:Career change guide" in prompt
    assert "关系点=" not in prompt
    assert "联网素材=" not in prompt


def test_build_topic_hook_prompt_preserves_supported_non_english_locale():
    prompt = build_topic_hook_prompt(
        lang="ja",
        topic_materials=[
            {
                "interest": "転職の迷い",
                "hook": "次の働き方の不安から入る",
                "opening_intent": "短く自然に触れる",
                "deepening_hint": "相手の反応に合わせて広げる",
            }
        ],
        open_threads=["さっきの転職の話が少し残っている"],
    )

    assert "低頻度の深め話題候補" in prompt
    assert "深め話題 hook" in prompt
    assert "関係点=転職の迷い" in prompt
    assert "未完了の話題" in prompt
    assert "Low-frequency deeper topic candidates" not in prompt
    assert "Deep topic hook" not in prompt
