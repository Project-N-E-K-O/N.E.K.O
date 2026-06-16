import asyncio
from datetime import datetime

import pytest

from main_logic.topic.pipeline import TopicHookPool, _clean_material


@pytest.fixture(autouse=True)
def _neutralize_deep_search(monkeypatch):
    # Keep the pipeline tests hermetic: the delivery-time deep search calls a
    # capable-tier LLM, which most tests here don't exercise. Dedicated
    # deep-search tests below override this stub with their own.
    async def _no_deep(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "main_logic.activity.llm_enrichment.derive_deep_search_query",
        _no_deep,
        raising=False,
    )


async def _async_identity_enrich(materials, **kwargs):
    return [dict(m) for m in materials]


def test_clean_material_normalizes_media_intent_string_and_bad_created_at():
    material = _clean_material(
        {
            "interest": "转职",
            "media_intent": "news",
            "created_at": "not-a-number",
            "relevance": 90,
        }
    )

    assert material is not None
    assert material["media_intent"] == ["news"]
    assert isinstance(material["created_at"], float)


@pytest.mark.asyncio
async def test_topic_pool_waits_for_slow_global_collection_before_analysis():
    calls = []

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, global_signals):
        calls.append(global_signals)
        return [
            {
                "interest": "慢慢形成的长期兴趣",
                "hook": "从多次提到的稳定兴趣切入",
                "readiness": 91,
                "confidence": 86,
                "risk": 12,
                "relevance": 91,
            }
        ]

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        min_user_turns_for_topic=3,
    )

    pool.note_user_message("妮可", "第一句只是随口聊聊")
    pool.note_user_message("妮可", "第二句还不够稳定，只是零散地提了一下工作")

    await pool.process_now("妮可")

    assert calls == []
    assert pool.get_ready_materials("妮可") == []

    pool.note_user_message("妮可", "第三句认真聊到最近一直在纠结换工作和现实压力")
    await pool.process_now("妮可")

    assert len(calls) == 1
    assert "全局证据:" in calls[0]
    assert "第一句只是随口聊聊" in calls[0]
    assert pool.get_ready_materials("妮可")[0]["interest"] == "慢慢形成的长期兴趣"


@pytest.mark.asyncio
async def test_topic_pool_global_signals_keep_more_than_recent_window():
    captured = {}

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, global_signals):
        captured["recent"] = list(user_msgs)
        captured["global"] = global_signals
        return [
            {
                "interest": "长期反复出现的换车兴趣",
                "hook": "从长期反复出现的点轻轻接住",
                "readiness": 95,
                "confidence": 90,
                "risk": 5,
                "relevance": 95,
            }
        ]

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        min_user_turns_for_topic=4,
    )
    for idx in range(10):
        pool.note_user_message("妮可", f"第{idx}次聊换车和预算")

    await pool.process_now("妮可")

    assert "第0次聊换车和预算" not in captured["recent"]
    assert "第0次聊换车和预算" in captured["global"]


@pytest.mark.asyncio
async def test_topic_pool_collects_silently_until_background_processing():
    calls = []

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        calls.append((list(user_msgs), list(ai_msgs), lang))
        return [
            {
                "interest": "想买凯迪拉克但预算压力很大",
                "hook": "从预算压力轻轻接住，别做理财课",
                "opening_intent": "像朋友随口接一句",
                "deepening_hint": "用户接话后，再聊目标和现实怎么折中",
                "relevance": 91,
            }
        ]

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        min_user_turns_for_topic=1,
    )

    pool.note_user_message(
        "妮可",
        "我想买凯迪拉克，但我根本买不起，毕业一年才攒了4600",
        lang="zh-CN",
    )

    assert calls == []
    assert pool.get_ready_materials("妮可") == []

    await pool.process_now("妮可")

    assert calls
    materials = pool.get_ready_materials("妮可")
    assert len(materials) == 1
    assert materials[0]["interest"] == "想买凯迪拉克但预算压力很大"
    assert "毕业一年才攒了4600" not in str(materials[0])


@pytest.mark.asyncio
async def test_topic_pool_uses_ai_context_without_blocking_collection():
    async def fake_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        assert user_msgs == ["我想换工作，但是怕踩坑，最近每天都在想是不是该换个方向"]
        assert ai_msgs == ["换工作这事可以慢慢拆，别上来就破釜沉舟。"]
        return [
            {
                "interest": "想换工作但担心选错",
                "hook": "接住换工作的犹豫，不催决定",
            }
        ]

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        min_user_turns_for_topic=1,
    )

    pool.note_user_message("妮可", "我想换工作，但是怕踩坑，最近每天都在想是不是该换个方向")
    pool.note_ai_message("妮可", "换工作这事可以慢慢拆，别上来就破釜沉舟。")

    assert pool.get_ready_materials("妮可") == []
    await pool.process_now("妮可")

    materials = pool.get_ready_materials("妮可")
    assert materials[0]["interest"] == "想换工作但担心选错"
    # hook is no longer carried on the cleaned material (slimmed contract)
    assert "hook" not in materials[0]
    assert materials[0]["relevance"] == 70


@pytest.mark.asyncio
async def test_topic_pool_passes_chat_language_to_online_enrichment(monkeypatch):
    langs = []

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        return [
            {
                "interest": "看平民代步车的小改件",
                "hook": "聊入门小改怎么少花冤枉钱",
            }
        ]

    async def fake_enrich(materials, *, lang=None, max_materials=2, **kwargs):
        langs.append(lang)
        return list(materials)

    monkeypatch.setattr(
        "main_logic.topic.pipeline.enrich_topic_materials_online",
        fake_enrich,
    )

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "你候选几个汽车品牌，我最近在想便宜代步车和预算怎么平衡", lang="zh-CN")

    await pool.process_now("妮可")

    assert langs == ["zh-CN"]


@pytest.mark.asyncio
async def test_topic_pool_discards_stale_analysis_when_new_turn_arrives():
    release = asyncio.Event()
    calls = []

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        calls.append(list(user_msgs))
        await release.wait()
        return [
            {
                "interest": "旧话题",
                "hook": "这不该覆盖新输入",
                "relevance": 90,
            }
        ]

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "第一句认真说一下我最近一直在纠结要不要换工作")

    task = asyncio.create_task(pool.process_now("妮可"))
    await asyncio.sleep(0)
    pool.note_user_message("妮可", "第二句又补充说我主要怕选错以后回不了头")
    release.set()
    assert await task is None

    assert calls == [["第一句认真说一下我最近一直在纠结要不要换工作"]]
    assert pool.get_ready_materials("妮可") == []


@pytest.mark.asyncio
async def test_topic_pool_discards_stale_analysis_when_new_turn_arrives_during_enrichment(monkeypatch):
    entered_enrich = asyncio.Event()
    release_enrich = asyncio.Event()

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        return [
            {
                "interest": "旧话题",
                "hook": "这不该覆盖新输入",
                "relevance": 90,
            }
        ]

    async def fake_enrich(materials, *, lang=None, max_materials=2, **kwargs):
        entered_enrich.set()
        await release_enrich.wait()
        return list(materials)

    monkeypatch.setattr(
        "main_logic.topic.pipeline.enrich_topic_materials_online",
        fake_enrich,
    )

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "第一句认真说一下我最近一直在纠结要不要换工作")

    task = asyncio.create_task(pool.process_now("妮可"))
    await entered_enrich.wait()
    pool.note_user_message("妮可", "第二句又补充说我主要怕选错以后回不了头")
    release_enrich.set()
    assert await task is None

    assert pool.get_ready_materials("妮可") == []


@pytest.mark.asyncio
async def test_topic_pool_debounce_retries_after_background_analyzer_failure():
    calls = 0
    retried = asyncio.Event()

    async def flaky_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary topic analyzer failure")
        retried.set()
        return [
            {
                "interest": "稳定转职话题",
                "hook": "接住用户对转职的犹豫",
                "collection_score": 90,
                "readiness": 88,
                "confidence": 84,
                "risk": 10,
                "relevance": 90,
            }
        ]

    pool = TopicHookPool(
        analyzer=flaky_analyzer,
        auto_schedule=True,
        enable_online_enrichment=False,
        debounce_seconds=0.001,
        min_user_turns_for_topic=1,
    )

    pool.note_user_message("妮可", "我最近一直在想转职，不知道下一步怎么选")
    pool.note_user_message("妮可", "转职这件事我已经反复想了几周，主要怕走错方向")
    pool.note_user_message("妮可", "现在的工作也不是不能做，但我总觉得继续拖会更难")
    pool.note_user_message("妮可", "所以我想聊聊转职的现实风险和机会")

    await asyncio.wait_for(retried.wait(), timeout=1.0)

    async def wait_for_materials():
        for _ in range(50):
            materials = pool.get_ready_materials("妮可")
            if materials:
                return materials
            await asyncio.sleep(0.01)
        return []

    materials = await wait_for_materials()

    assert calls == 2
    assert materials[0]["interest"] == "稳定转职话题"


@pytest.mark.asyncio
async def test_topic_pool_keeps_dirty_when_analyzer_returns_none():
    calls = 0
    retried = asyncio.Event()

    async def flaky_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        retried.set()
        return [
            {
                "interest": "稳定换城市话题",
                "hook": "接住用户想换城市生活的念头",
                "collection_score": 90,
                "readiness": 88,
                "confidence": 84,
                "risk": 10,
                "relevance": 90,
            }
        ]

    pool = TopicHookPool(
        analyzer=flaky_analyzer,
        auto_schedule=True,
        enable_online_enrichment=False,
        debounce_seconds=0.001,
        min_user_turns_for_topic=1,
    )

    pool.note_user_message("妮可", "我最近一直想换个城市生活，但又怕重新开始太难")
    pool.note_user_message("妮可", "换城市这件事反复想了很久，主要是想改变现在的节奏")

    await asyncio.wait_for(retried.wait(), timeout=1.0)

    async def wait_for_materials():
        for _ in range(50):
            materials = pool.get_ready_materials("妮可")
            if materials:
                return materials
            await asyncio.sleep(0.01)
        return []

    materials = await wait_for_materials()

    assert calls == 2
    assert materials[0]["interest"] == "稳定换城市话题"


@pytest.mark.asyncio
async def test_topic_pool_triggers_ready_hook_after_quiet_window():
    delivered = []

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        return [
            {
                "interest": "买车像进入新生活阶段",
                "hook": "从买车背后的生活阶段感切入",
                "relevance": 95,
            }
        ]

    async def fake_trigger(*, lanlan_name, material, lang):
        delivered.append((lanlan_name, material["interest"], lang))
        return True

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_delay_seconds=0.01,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "我感觉买车算人生大事，最近一直在想它是不是代表生活进入新阶段", lang="zh-CN")

    await pool.process_now("妮可")
    assert delivered == []

    await asyncio.sleep(0.03)

    assert delivered == [("妮可", "买车像进入新生活阶段", "zh-CN")]
    assert pool.get_ready_materials("妮可") == []
    assert pool._materials["妮可"][0]["status"] == "used"


@pytest.mark.asyncio
async def test_topic_pool_clears_pending_trigger_when_privacy_turns_on(monkeypatch):
    delivered = []
    privacy_enabled = False

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        return [
            {
                "interest": "买车像进入新生活阶段",
                "hook": "从买车背后的生活阶段感切入",
                "relevance": 95,
            }
        ]

    async def fake_trigger(*, lanlan_name, material, lang):
        delivered.append((lanlan_name, material["interest"], lang))
        return True

    monkeypatch.setattr(
        "main_logic.topic.pipeline._privacy_mode_active",
        lambda: privacy_enabled,
    )
    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_delay_seconds=0.01,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "我感觉买车算人生大事，最近一直在想它是不是代表生活进入新阶段", lang="zh-CN")

    await pool.process_now("妮可")
    assert pool.get_ready_materials("妮可")

    privacy_enabled = True
    await asyncio.sleep(0.03)

    assert delivered == []
    assert pool.get_ready_materials("妮可") == []


@pytest.mark.asyncio
async def test_topic_pool_triggers_highest_relevance_material_first():
    delivered = []

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        return [
            {
                "interest": "低优先级话题",
                "hook": "这个话题先不要浪费触发机会",
                "relevance": 80,
            },
            {
                "interest": "高优先级话题",
                "hook": "这个才应该先触发",
                "relevance": 96,
            },
        ]

    async def fake_trigger(*, lanlan_name, material, lang):
        delivered.append(material["interest"])
        return True

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_delay_seconds=0.01,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "我最近认真聊了两个方向，但其中一个明显更适合展开", lang="zh-CN")

    await pool.process_now("妮可")
    await asyncio.sleep(0.03)

    assert delivered == ["高优先级话题"]
    assert pool._materials["妮可"][0]["interest"] == "高优先级话题"
    assert pool._materials["妮可"][0]["status"] == "used"
    assert pool.get_ready_materials("妮可")[0]["interest"] == "低优先级话题"


@pytest.mark.asyncio
async def test_topic_pool_keeps_material_pending_when_delivery_defers():
    first_attempt = asyncio.Event()
    attempts = []

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        return [
            {
                "interest": "买车像进入新生活阶段",
                "hook": "从买车背后的生活阶段感切入",
                "relevance": 95,
            }
        ]

    async def fake_trigger(*, lanlan_name, material, lang):
        attempts.append((lanlan_name, material["interest"], lang))
        first_attempt.set()
        return False

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_delay_seconds=0.01,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "我感觉买车算人生大事，最近一直在想它是不是代表生活进入新阶段", lang="zh-CN")

    await pool.process_now("妮可")
    await first_attempt.wait()

    assert attempts == [("妮可", "买车像进入新生活阶段", "zh-CN")]
    assert pool.get_ready_materials("妮可")[0]["status"] == "pending"
    pool._cancel_trigger("妮可")


@pytest.mark.asyncio
async def test_topic_pool_retries_pending_material_after_delivery_defers():
    attempts = []
    retried = asyncio.Event()

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        return [
            {
                "interest": "买车像进入新生活阶段",
                "hook": "从买车背后的生活阶段感切入",
                "relevance": 95,
            }
        ]

    async def fake_trigger(*, lanlan_name, material, lang):
        attempts.append((lanlan_name, material["interest"], lang))
        if len(attempts) >= 2:
            retried.set()
            return True
        return False

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_delay_seconds=0.01,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "我感觉买车算人生大事，最近一直在想它是不是代表生活进入新阶段", lang="zh-CN")

    await pool.process_now("妮可")
    await asyncio.wait_for(retried.wait(), timeout=1.0)

    assert attempts == [
        ("妮可", "买车像进入新生活阶段", "zh-CN"),
        ("妮可", "买车像进入新生活阶段", "zh-CN"),
    ]
    assert pool.get_ready_materials("妮可") == []
    assert pool._materials["妮可"][0]["status"] == "used"


@pytest.mark.asyncio
async def test_topic_pool_retries_pending_material_after_trigger_exception():
    attempts = []
    retried = asyncio.Event()

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        return [
            {
                "interest": "买车像进入新生活阶段",
                "hook": "从买车背后的生活阶段感切入",
                "relevance": 95,
            }
        ]

    async def fake_trigger(*, lanlan_name, material, lang):
        attempts.append((lanlan_name, material["interest"], lang))
        if len(attempts) == 1:
            raise RuntimeError("delivery temporarily unavailable")
        retried.set()
        return True

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_delay_seconds=0.01,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "我感觉买车算人生大事，最近一直在想它是不是代表生活进入新阶段", lang="zh-CN")

    await pool.process_now("妮可")
    await asyncio.wait_for(retried.wait(), timeout=1.0)

    assert attempts == [
        ("妮可", "买车像进入新生活阶段", "zh-CN"),
        ("妮可", "买车像进入新生活阶段", "zh-CN"),
    ]
    assert pool.get_ready_materials("妮可") == []
    assert pool._materials["妮可"][0]["status"] == "used"


@pytest.mark.asyncio
async def test_topic_pool_does_not_cancel_current_trigger_when_ai_turn_is_recorded():
    triggered = asyncio.Event()

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        return [
            {
                "interest": "买车像进入新生活阶段",
                "hook": "从买车背后的生活阶段感切入",
                "relevance": 95,
            }
        ]

    pool = None

    async def fake_trigger(*, lanlan_name, material, lang):
        pool.note_ai_message(lanlan_name, "我刚刚把这个话题自然说出来了", lang=lang)
        await asyncio.sleep(0)
        triggered.set()
        return True

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_delay_seconds=0.01,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "我感觉买车算人生大事，最近一直在想它是不是代表生活进入新阶段", lang="zh-CN")

    await pool.process_now("妮可")
    await asyncio.wait_for(triggered.wait(), timeout=1.0)

    assert pool.get_ready_materials("妮可") == []
    assert pool._materials["妮可"][0]["status"] == "used"


@pytest.mark.asyncio
async def test_topic_pool_resets_trigger_wait_when_chat_continues():
    delivered = []

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        return [
            {
                "interest": user_msgs[-1],
                "hook": "接住最新话题",
                "relevance": 90,
            }
        ]

    async def fake_trigger(*, lanlan_name, material, lang):
        delivered.append(material["interest"])
        return True

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_delay_seconds=0.04,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "旧话题：我最近一直在纠结买车是不是代表生活进入新阶段", lang="zh-CN")
    await pool.process_now("妮可")

    await asyncio.sleep(0.02)
    pool.note_user_message("妮可", "新话题：我后来又开始纠结换工作和现实压力怎么平衡", lang="zh-CN")
    await pool.process_now("妮可")
    await asyncio.sleep(0.03)
    assert delivered == []

    await asyncio.sleep(0.03)
    assert delivered == ["新话题：我后来又开始纠结换工作和现实压力怎么平衡"]


@pytest.mark.asyncio
async def test_topic_pool_limits_daily_topic_triggers_to_two():
    delivered = []
    analyzer_calls = 0

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        nonlocal analyzer_calls
        interests = [
            "凯迪拉克预算压力",
            "周末海边旅行计划",
            "新房装修色差问题",
        ]
        analyzer_calls += 1
        return [
            {
                "interest": interests[analyzer_calls - 1],
                "hook": f"自然接住{interests[analyzer_calls - 1]}",
                "relevance": 95,
            }
        ]

    async def fake_trigger(*, lanlan_name, material, lang):
        delivered.append(material["interest"])
        return True

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_delay_seconds=0.01,
        min_trigger_gap_seconds=0,
        min_user_turns_for_topic=1,
    )

    for idx in range(3):
        pool.note_user_message("妮可", f"第{idx}轮认真聊一个新方向，信息量足够做深话题", lang="zh-CN")
        await pool.process_now("妮可")
        await asyncio.sleep(0.03)

    assert delivered == ["凯迪拉克预算压力", "周末海边旅行计划"]
    assert pool.get_ready_materials("妮可") == []


def test_topic_pool_daily_topic_limit_resets_on_calendar_day():
    pool = TopicHookPool(
        auto_schedule=False,
        enable_online_enrichment=False,
        min_user_turns_for_topic=1,
    )
    day_one_late = datetime(2026, 6, 14, 23, 50).timestamp()
    day_one_later = datetime(2026, 6, 14, 23, 55).timestamp()
    day_one_end = datetime(2026, 6, 14, 23, 59).timestamp()
    day_two_start = datetime(2026, 6, 15, 0, 1).timestamp()
    pool._used_topics["妮可"] = [
        {"used_at": day_one_late, "hook_id": "a", "interest": "前一天话题 A", "units": []},
        {"used_at": day_one_later, "hook_id": "b", "interest": "前一天话题 B", "units": []},
    ]

    assert pool._daily_quota_reached("妮可", now=day_one_end)
    assert not pool._daily_quota_reached("妮可", now=day_two_start)


def test_topic_pool_min_trigger_gap_survives_calendar_day_reset():
    pool = TopicHookPool(
        auto_schedule=False,
        enable_online_enrichment=False,
        min_trigger_gap_seconds=4 * 60 * 60,
        min_user_turns_for_topic=1,
    )
    day_one_late = datetime(2026, 6, 14, 23, 50).timestamp()
    day_two_start = datetime(2026, 6, 15, 0, 1).timestamp()
    pool._used_topics["妮可"] = [
        {"used_at": day_one_late, "hook_id": "a", "interest": "前一天话题", "units": []},
    ]

    assert not pool._daily_quota_reached("妮可", now=day_two_start)
    assert pool._seconds_until_next_topic_trigger("妮可", now=day_two_start) > 0


@pytest.mark.asyncio
async def test_topic_pool_does_not_trigger_second_topic_immediately_after_first():
    delivered = []
    analyzer_calls = 0

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        nonlocal analyzer_calls
        analyzer_calls += 1
        interest = "凯迪拉克预算压力" if analyzer_calls == 1 else "海边旅行计划"
        return [
            {
                "interest": interest,
                "hook": f"自然接住{interest}",
                "relevance": 95,
            }
        ]

    async def fake_trigger(*, lanlan_name, material, lang):
        delivered.append(material["interest"])
        return True

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_delay_seconds=0.01,
        min_trigger_gap_seconds=0.2,
        min_user_turns_for_topic=1,
    )

    pool.note_user_message("妮可", "第一轮认真聊一个深话题，里面有足够多的具体背景、现实纠结、近期计划和反复提到的细节", lang="zh-CN")
    await pool.process_now("妮可")
    await asyncio.sleep(0.03)
    assert delivered == ["凯迪拉克预算压力"]

    pool.note_user_message("妮可", "第二轮又聊出另一个深话题，依然有明确场景、具体选择、近期困扰和可以继续展开的细节", lang="zh-CN")
    await pool.process_now("妮可")
    await asyncio.sleep(0.05)
    assert delivered == ["凯迪拉克预算压力"]

    await asyncio.sleep(0.2)
    assert delivered == ["凯迪拉克预算压力", "海边旅行计划"]


@pytest.mark.asyncio
async def test_topic_pool_suppresses_same_topic_after_it_was_used_today():
    delivered = []

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, **kwargs):
        return [
            {
                "interest": "文本世界模型的无撤回机制与幻觉问题",
                "hook": "从模型没有撤回功能切入",
                "search_query": "文本世界模型 无撤回 幻觉",
                "relevance": 95,
            }
        ]

    async def fake_trigger(*, lanlan_name, material, lang):
        delivered.append(material["interest"])
        return True

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_delay_seconds=0.01,
        min_user_turns_for_topic=1,
    )

    pool.note_user_message("妮可", "我在研究文本世界模型，因为没有撤回功能所以会产生幻觉", lang="zh-CN")
    await pool.process_now("妮可")
    await asyncio.sleep(0.03)

    pool.note_user_message("妮可", "继续说文本世界模型，逐字预测和幻觉这个方向真的很关键", lang="zh-CN")
    await pool.process_now("妮可")
    await asyncio.sleep(0.03)

    assert delivered == ["文本世界模型的无撤回机制与幻觉问题"]
    assert pool.get_ready_materials("妮可") == []


@pytest.mark.asyncio
async def test_enrich_pool_discards_material_when_privacy_toggles_on_mid_analysis(monkeypatch):
    # TOCTOU guard: privacy passes the start-of-call wipe, then flips ON during
    # the analyzer await. Material collected across the privacy interval must be
    # discarded, not stored for a later trigger.
    from main_logic.topic import pipeline as topic_pipeline

    privacy = {"on": False}
    monkeypatch.setattr(topic_pipeline, "_privacy_mode_active", lambda: privacy["on"])

    async def fake_analyzer(*, user_msgs, ai_msgs, lang, global_signals):
        privacy["on"] = True  # user enables privacy while we're "analyzing"
        return [
            {
                "interest": "隐私期间产生的话题不该留存",
                "keywords": ["x"],
                "relevance": 95,
                "risk": 10,
            }
        ]

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "在聊一个深话题，有场景有困扰可以展开", lang="zh-CN")
    await pool.process_now("妮可")
    await asyncio.sleep(0.02)

    assert pool.get_ready_materials("妮可") == []
    assert pool._materials.get("妮可") in (None, [])


@pytest.mark.asyncio
async def test_deepen_material_uses_derived_query_and_overrides_floor(monkeypatch):
    from main_logic.topic import pipeline as topic_pipeline

    async def fake_derive(*, interest, keywords, floor_angle, lang, **kwargs):
        assert interest == "文本世界模型"
        return "文本世界模型 幻觉 最新研究"

    async def fake_enrich(materials, **kwargs):
        out = []
        for m in materials:
            m = dict(m)
            m["material_hint"] = {"summary": f"deep:{m.get('deep_query')}"}
            m["online_used"] = True
            m["online_query"] = m.get("deep_query")
            m["online_angle"] = "最新研究综述"
            out.append(m)
        return out

    monkeypatch.setattr(
        "main_logic.activity.llm_enrichment.derive_deep_search_query", fake_derive
    )
    monkeypatch.setattr(topic_pipeline, "enrich_topic_materials_online", fake_enrich)

    pool = TopicHookPool(auto_schedule=False)
    material = {
        "interest": "文本世界模型",
        "keywords": ["文本世界模型", "幻觉"],
        "material_hint": {"summary": "floor"},
        "online_angle": "floor angle",
    }
    await pool._deepen_material("妮可", material, "zh-CN")

    assert material["deep_search_done"] is True
    assert material["deep_query"] == "文本世界模型 幻觉 最新研究"
    assert material["material_hint"] == {"summary": "deep:文本世界模型 幻觉 最新研究"}
    assert material["online_query"] == "文本世界模型 幻觉 最新研究"


@pytest.mark.asyncio
async def test_deepen_material_keeps_floor_when_deep_search_finds_nothing(monkeypatch):
    from main_logic.topic import pipeline as topic_pipeline

    async def fake_derive(**kwargs):
        return "some deep query"

    monkeypatch.setattr(
        "main_logic.activity.llm_enrichment.derive_deep_search_query", fake_derive
    )
    monkeypatch.setattr(
        topic_pipeline, "enrich_topic_materials_online", _async_identity_enrich
    )

    pool = TopicHookPool(auto_schedule=False)
    material = {"interest": "x", "keywords": ["x"], "material_hint": {"summary": "floor"}}
    await pool._deepen_material("妮可", material, "zh-CN")

    assert material["material_hint"] == {"summary": "floor"}  # floor preserved
    assert material["deep_query"] == "some deep query"


@pytest.mark.asyncio
async def test_deepen_material_idempotent_and_respects_disable(monkeypatch):
    from main_logic.topic import pipeline as topic_pipeline
    calls = []

    async def fake_derive(**kwargs):
        calls.append(1)
        return "q"

    monkeypatch.setattr(
        "main_logic.activity.llm_enrichment.derive_deep_search_query", fake_derive
    )
    monkeypatch.setattr(
        topic_pipeline, "enrich_topic_materials_online", _async_identity_enrich
    )

    # disabled → never derives, never marks done
    pool_off = TopicHookPool(auto_schedule=False, enable_deep_search=False)
    m1 = {"interest": "x", "keywords": ["x"]}
    await pool_off._deepen_material("n", m1, "zh")
    assert calls == []
    assert "deep_search_done" not in m1

    # enabled → derives once; second call is a cached no-op
    pool_on = TopicHookPool(auto_schedule=False)
    m2 = {"interest": "x", "keywords": ["x"]}
    await pool_on._deepen_material("n", m2, "zh")
    await pool_on._deepen_material("n", m2, "zh")
    assert calls == [1]
    assert m2["deep_search_done"] is True
