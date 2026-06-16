import pytest


def test_select_lang_template_falls_back_zh_family_to_zh():
    from main_logic.activity.llm_enrichment import _select_lang_template

    # zh-TW with no zh-TW entry must fall back to the Simplified zh prompt,
    # NOT English (regression guard for activity/open-thread enrichment).
    zh_only = {"zh": "简体", "en": "english"}
    assert _select_lang_template(zh_only, "zh-TW") == "简体"
    assert _select_lang_template(zh_only, "zh") == "简体"
    assert _select_lang_template(zh_only, "ja") == "english"

    # An explicit zh-TW entry still wins over the zh fallback.
    with_trad = {"zh": "简体", "zh-TW": "繁體", "en": "english"}
    assert _select_lang_template(with_trad, "zh-TW") == "繁體"


@pytest.mark.asyncio
async def test_derive_deep_search_query_parses_json_query(monkeypatch):
    from main_logic.activity import llm_enrichment

    async def fake_capable(prompt, *, timeout, label):
        assert "文本世界模型" in prompt
        return '{"query": "文本世界模型 无撤回 幻觉 最新"}'

    monkeypatch.setattr(llm_enrichment, "_invoke_capable_tier", fake_capable)

    q = await llm_enrichment.derive_deep_search_query(
        interest="文本世界模型的无撤回机制",
        keywords=["文本世界模型", "幻觉"],
        lang="zh-CN",
    )
    assert q == "文本世界模型 无撤回 幻觉 最新"


@pytest.mark.asyncio
async def test_derive_deep_search_query_none_when_model_silent(monkeypatch):
    from main_logic.activity import llm_enrichment

    async def fake_capable(prompt, *, timeout, label):
        return None

    monkeypatch.setattr(llm_enrichment, "_invoke_capable_tier", fake_capable)

    q = await llm_enrichment.derive_deep_search_query(
        interest="只有兴趣没有关键词",
        keywords=[],
        lang="en",
    )
    assert q is None


@pytest.mark.asyncio
async def test_call_topic_candidates_parses_model_output(monkeypatch):
    from main_logic.activity import llm_enrichment

    async def fake_invoke(prompt, *, timeout, label):
        assert label == "topic_candidates"
        assert "凯迪拉克" in prompt
        return """```json
        {
          "topics": [
            {
              "interest": "想买凯迪拉克但预算压力很大",
              "hook": "接住想买车和现实预算的冲突",
              "opening_intent": "像朋友随口一提，不像问卷",
              "deepening_hint": "用户接话后聊目标和现实怎么折中",
              "relevance": 93
            },
            {"interest": "你好", "relevance": 10}
          ]
        }
        ```"""

    monkeypatch.setattr(llm_enrichment, "_invoke_emotion_tier", fake_invoke)

    topics = await llm_enrichment.call_topic_candidates(
        user_msgs=[(1.0, "我想买凯迪拉克，但我根本买不起，毕业一年才攒了4600")],
        ai_msgs=[],
        lang="zh-CN",
    )

    assert topics == [
        {
            "interest": "想买凯迪拉克但预算压力很大",
            "keywords": [],
            "relevance": 93,
            "risk": 20,
        }
    ]


@pytest.mark.asyncio
async def test_call_topic_candidates_passes_global_signals_and_keeps_keywords(monkeypatch):
    from main_logic.activity import llm_enrichment

    captured = {}

    async def fake_invoke(prompt, *, timeout, label):
        captured["prompt"] = prompt
        return """
        {
          "topics": [
            {
              "interest": "用户把买车和生活自由感联系在一起",
              "hook": "先接住不想被人生流程推着走",
              "opening_intent": "短一点，像随口想起来",
              "deepening_hint": "用户接话后再聊自由感和现实成本",
              "why_now": "多次提到买车、预算和不想被固定流程推着走",
              "search_query": "年轻人 买车 通勤 养车 成本",
              "keywords": ["买车", "自由感"],
              "relevance": 91,
              "risk": 18
            }
          ]
        }
        """

    monkeypatch.setattr(llm_enrichment, "_invoke_emotion_tier", fake_invoke)

    topics = await llm_enrichment.call_topic_candidates(
        user_msgs=[(1.0, "刚才又聊到买车")],
        ai_msgs=[],
        lang="zh-CN",
        global_signals="全局信号：用户三次提到买车和自由感",
    )

    assert "全局信号：用户三次提到买车和自由感" in captured["prompt"]
    assert topics == [
        {
            "interest": "用户把买车和生活自由感联系在一起",
            "keywords": ["买车", "自由感"],
            "relevance": 91,
            "risk": 18,
        }
    ]


@pytest.mark.asyncio
async def test_call_topic_candidates_skips_low_relevance(monkeypatch):
    from main_logic.activity import llm_enrichment

    async def fake_invoke(prompt, *, timeout, label):
        return """
        {
          "topics": [
            {
              "interest": "一个相关度还不够的薄话题",
              "hook": "先不要开口",
              "relevance": 62,
              "risk": 10
            }
          ]
        }
        """

    monkeypatch.setattr(llm_enrichment, "_invoke_emotion_tier", fake_invoke)

    topics = await llm_enrichment.call_topic_candidates(
        user_msgs=[(1.0, "还没聊开")],
        ai_msgs=[],
        lang="zh-CN",
        global_signals="收集进度: 60%",
    )

    assert topics == []


@pytest.mark.asyncio
async def test_call_topic_candidates_skips_high_risk(monkeypatch):
    from main_logic.activity import llm_enrichment

    async def fake_invoke(prompt, *, timeout, label):
        return """
        {
          "topics": [
            {
              "interest": "一个相关但触碰风险偏高的话题",
              "relevance": 90,
              "risk": 80
            }
          ]
        }
        """

    monkeypatch.setattr(llm_enrichment, "_invoke_emotion_tier", fake_invoke)

    topics = await llm_enrichment.call_topic_candidates(
        user_msgs=[(1.0, "顺口提了一句不太想被追问的事")],
        ai_msgs=[],
        lang="zh-CN",
    )

    # relevance clears the bar but risk > 65 must still reject — guards the
    # risk gate against regression now that thresholds live only in code.
    assert topics == []


@pytest.mark.asyncio
async def test_call_topic_candidates_keeps_short_cjk_interests(monkeypatch):
    from main_logic.activity import llm_enrichment

    async def fake_invoke(prompt, *, timeout, label):
        return """
        {
          "topics": [
            {
              "interest": "转职",
              "hook": "接住用户对转职的犹豫",
              "relevance": 88,
              "risk": 10
            }
          ]
        }
        """

    monkeypatch.setattr(llm_enrichment, "_invoke_emotion_tier", fake_invoke)

    topics = await llm_enrichment.call_topic_candidates(
        user_msgs=[(1.0, "我最近一直在想转职")],
        ai_msgs=[],
        lang="zh-CN",
        global_signals="收集进度: 100%",
    )

    assert topics and topics[0]["interest"] == "转职"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lang", "marker"),
    [
        ("ja", "ユーザーの言語で"),
        ("ko", "사용자 언어로"),
        ("es", "en el idioma del usuario"),
        ("pt", "no idioma do usuario"),
        ("ru", "на языке пользователя"),
        ("zh-TW", "使用繁體中文"),
    ],
)
async def test_call_topic_candidates_uses_localized_prompt_for_supported_languages(
    monkeypatch,
    lang,
    marker,
):
    from main_logic.activity import llm_enrichment

    captured = {}

    async def fake_invoke(prompt, *, timeout, label):
        captured["prompt"] = prompt
        return '{"topics":[]}'

    monkeypatch.setattr(llm_enrichment, "_invoke_emotion_tier", fake_invoke)

    topics = await llm_enrichment.call_topic_candidates(
        user_msgs=[(1.0, "I mentioned wanting a new phone.")],
        ai_msgs=[],
        lang=lang,
        global_signals="collection: enough evidence",
    )

    assert topics == []
    assert marker in captured["prompt"]
    assert "Output strict JSON" not in captured["prompt"]


@pytest.mark.asyncio
async def test_invoke_emotion_tier_uses_project_message_classes(monkeypatch):
    from main_logic.activity import llm_enrichment
    from utils.llm_client import HumanMessage

    captured = {}

    class FakeConfigManager:
        def get_model_api_config(self, name):
            assert name == "emotion"
            return {
                "model": "fake-emotion-model",
                "api_key": "fake-key",
                "base_url": "https://example.invalid/v1",
            }

    class FakeResponse:
        content = '{"topics":[]}'

    class FakeLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def ainvoke(self, messages):
            captured["messages"] = messages
            return FakeResponse()

    def fake_create_chat_llm(*args, **kwargs):
        return FakeLLM()

    monkeypatch.setattr(
        "utils.config_manager.get_config_manager",
        lambda: FakeConfigManager(),
    )
    monkeypatch.setattr("utils.llm_client.create_chat_llm", fake_create_chat_llm)
    monkeypatch.setattr("utils.token_tracker.set_call_type", lambda value: None)

    raw = await llm_enrichment._invoke_emotion_tier(
        "提炼一个深话题",
        timeout=1.0,
        label="topic_candidates",
    )

    assert raw == '{"topics":[]}'
    assert isinstance(captured["messages"][0], HumanMessage)
    assert captured["messages"][0].content == "提炼一个深话题"
