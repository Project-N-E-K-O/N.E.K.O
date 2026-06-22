import asyncio
import inspect
import json
import threading
import time
from datetime import datetime

import pytest

from main_logic.topic.pipeline import TopicHookPool, _clean_material
from main_logic.topic.signals import TopicSignalStore


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


def test_clean_material_ignores_media_intent_and_normalizes_bad_created_at():
    material = _clean_material(
        {
            "interest": "转职",
            "media_intent": "news",
            "created_at": "not-a-number",
            "relevance": 90,
        }
    )

    assert material is not None
    assert "media_intent" not in material
    assert isinstance(material["created_at"], float)


def test_topic_signal_store_persists_recent_turns_across_instances(tmp_path):
    path = tmp_path / "topic_signals.json"
    now = datetime.now().timestamp()
    store = TopicSignalStore(
        min_user_turns_for_topic=1,
        persistence_path=path,
    )

    store.note_turn("妮可", actor="user", text="我最近一直在纠结换工作", now=now)
    store.flush()

    reloaded = TopicSignalStore(
        min_user_turns_for_topic=1,
        persistence_path=path,
    )
    assert reloaded.is_ready("妮可")
    assert "换工作" in reloaded.format_global_signals("妮可", lang="zh-CN")


def test_topic_signal_store_flushes_pruned_entries_after_load(tmp_path):
    path = tmp_path / "topic_signals.json"
    now = time.time()
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "characters": {
                    "妮可": [
                        {
                            "actor": "user",
                            "text": "超出保留窗口的旧证据",
                            "timestamp": now - 20,
                        },
                        {
                            "actor": "user",
                            "text": "仍然有效的新证据",
                            "timestamp": now,
                        },
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    store = TopicSignalStore(
        min_user_turns_for_topic=1,
        persistence_path=path,
        retention_seconds=10,
    )
    store.flush()

    persisted = json.loads(path.read_text(encoding="utf-8"))
    entries = persisted["characters"]["妮可"]
    assert len(entries) == 1
    assert entries[0]["text"] == "仍然有效的新证据"


def test_topic_pool_explicit_purge_flushes_only_when_signals_changed(monkeypatch):
    pool = TopicHookPool(
        auto_schedule=False,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "这是需要清掉的隐私前证据")
    flushes = []
    monkeypatch.setattr(pool._signal_store, "flush", lambda: flushes.append("flush"))

    pool._purge_accumulated_signals("妮可")
    pool._purge_accumulated_signals("妮可")

    assert flushes == ["flush"]


def test_topic_pool_global_explicit_purge_clears_all_characters():
    pool = TopicHookPool(
        auto_schedule=False,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "第一个角色的隐私前证据")
    pool.note_user_message("兰兰", "第二个角色的隐私前证据")

    pool.purge_all_accumulated_signals()

    assert pool._signal_store.names() == []
    assert pool._dirty == set()


def test_topic_signal_store_batches_persistence_off_chat_path(monkeypatch, tmp_path):
    from main_logic.topic import signals as topic_signals

    calls = []

    def fake_atomic_write_json(path, payload, **kwargs):
        calls.append((path, payload, kwargs))

    monkeypatch.setattr(topic_signals, "atomic_write_json", fake_atomic_write_json)
    store = TopicSignalStore(
        min_user_turns_for_topic=1,
        persistence_path=tmp_path / "topic_signals.json",
        persistence_flush_delay_seconds=60,
    )

    store.note_turn("妮可", actor="user", text="我最近一直在纠结换工作")
    store.note_turn("妮可", actor="user", text="这个问题又聊了第二轮")

    assert calls == []

    store.flush()

    assert len(calls) == 1
    assert len(calls[0][1]["characters"]["妮可"]) == 2


def test_topic_signal_store_keeps_dirty_when_flush_write_fails(monkeypatch, tmp_path):
    from main_logic.topic import signals as topic_signals

    attempts = 0

    def flaky_atomic_write_json(path, payload, **kwargs):
        nonlocal attempts
        attempts += 1
        raise OSError("simulated write failure")

    monkeypatch.setattr(topic_signals, "atomic_write_json", flaky_atomic_write_json)
    store = TopicSignalStore(
        min_user_turns_for_topic=1,
        persistence_path=tmp_path / "topic_signals.json",
        persistence_flush_delay_seconds=60,
    )

    store.note_turn("妮可", actor="user", text="待清理的候选证据")
    store.flush()

    assert attempts == 1
    assert store._persist_dirty is True
    assert store._persist_timer is not None
    store._persist_timer.cancel()


def test_topic_signal_store_explicit_flush_wins_over_inflight_write(tmp_path):
    path = tmp_path / "topic_signals.json"
    store = TopicSignalStore(
        min_user_turns_for_topic=1,
        persistence_path=path,
        persistence_flush_delay_seconds=60,
    )
    original_write = store._write_payload
    first_write_entered = threading.Event()
    release_first_write = threading.Event()
    write_count = 0

    def slow_first_write(payload):
        nonlocal write_count
        write_count += 1
        if write_count == 1:
            first_write_entered.set()
            assert release_first_write.wait(timeout=1.0)
        original_write(payload)

    store._write_payload = slow_first_write
    store.note_turn("妮可", actor="user", text="隐私前的候选证据")

    first_flush = threading.Thread(target=store.flush)
    first_flush.start()
    assert first_write_entered.wait(timeout=1.0)

    explicit_flush = threading.Thread(
        target=lambda: (store.clear("妮可"), store.flush())
    )
    explicit_flush.start()
    release_first_write.set()
    first_flush.join(timeout=1.0)
    explicit_flush.join(timeout=1.0)
    assert not first_flush.is_alive()
    assert not explicit_flush.is_alive()

    reloaded = TopicSignalStore(
        min_user_turns_for_topic=1,
        persistence_path=path,
    )
    assert not reloaded.is_ready("妮可")


def test_topic_signal_store_drops_entries_older_than_retention(tmp_path):
    path = tmp_path / "topic_signals.json"
    now = datetime.now().timestamp()
    store = TopicSignalStore(
        min_user_turns_for_topic=1,
        retention_seconds=12 * 60 * 60,
        persistence_path=path,
    )

    store.note_turn("妮可", actor="user", text="前一天的旧话题", now=now - 13 * 60 * 60)
    store.note_turn("妮可", actor="user", text="今天的新话题", now=now)
    store.flush()

    signals = store.format_global_signals("妮可", lang="zh-CN")
    assert "前一天的旧话题" not in signals
    assert "今天的新话题" in signals


@pytest.mark.asyncio
async def test_topic_pool_waits_for_slow_global_collection_before_analysis():
    calls = []

    async def fake_analyzer(*, lang, global_signals):
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
    assert "- [" in calls[0]  # rendered evidence lines, no inner header
    assert "第一句只是随口聊聊" in calls[0]
    assert pool.get_ready_materials("妮可")[0]["interest"] == "慢慢形成的长期兴趣"


@pytest.mark.asyncio
async def test_topic_pool_passes_full_global_signals_to_analyzer():
    captured = {}

    async def fake_analyzer(*, lang, global_signals):
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

    assert "第0次聊换车和预算" in captured["global"]
    assert "第9次聊换车和预算" in captured["global"]


@pytest.mark.asyncio
async def test_topic_pool_collects_silently_until_background_processing():
    calls = []

    async def fake_analyzer(*, lang, **kwargs):
        calls.append(lang)
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
    async def fake_analyzer(*, lang, global_signals=None, **kwargs):
        # both the user turn and the AI turn reach the analyzer via the
        # rendered slow evidence (no separate recent-conversation input)
        assert "我想换工作" in (global_signals or "")
        assert "换工作这事可以慢慢拆" in (global_signals or "")
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
async def test_topic_pool_passes_chat_language_to_delivery_prepare(monkeypatch):
    langs = []
    delivered = asyncio.Event()

    async def fake_analyzer(*, lang, **kwargs):
        return [
            {
                "interest": "看平民代步车的小改件",
                "hook": "聊入门小改怎么少花冤枉钱",
            }
        ]

    async def fake_enrich(materials, *, lang=None, max_materials=2, **kwargs):
        langs.append(lang)
        return list(materials)

    async def fake_derive(**kwargs):
        return "代步车 小改件"

    async def fake_trigger(*, lanlan_name, material, lang):
        delivered.set()
        return True

    monkeypatch.setattr(
        "main_logic.activity.llm_enrichment.derive_deep_search_query", fake_derive
    )
    monkeypatch.setattr(
        "main_logic.topic.pipeline.enrich_topic_materials_online",
        fake_enrich,
    )

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        topic_trigger=fake_trigger,
        trigger_delay_seconds=0.05,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "你候选几个汽车品牌，我最近在想便宜代步车和预算怎么平衡", lang="zh-CN")

    await pool.process_now("妮可")
    await asyncio.wait_for(delivered.wait(), timeout=1.0)

    assert langs == ["zh-CN"]


@pytest.mark.asyncio
async def test_topic_pool_heartbeat_language_does_not_override_character_language(monkeypatch):
    analyzer_langs = []
    enrich_langs = []
    delivered = asyncio.Event()

    async def fake_analyzer(*, lang, **kwargs):
        analyzer_langs.append(lang)
        return [
            {
                "interest": "看平民代步车的小改件",
                "hook": "聊入门小改怎么少花冤枉钱",
            }
        ]

    async def fake_enrich(materials, *, lang=None, max_materials=2, **kwargs):
        enrich_langs.append(lang)
        return list(materials)

    async def fake_derive(**kwargs):
        return "代步车 小改件"

    async def fake_trigger(*, lanlan_name, material, lang):
        delivered.set()
        return True

    monkeypatch.setattr(
        "main_logic.activity.llm_enrichment.derive_deep_search_query", fake_derive
    )
    monkeypatch.setattr(
        "main_logic.topic.pipeline.enrich_topic_materials_online",
        fake_enrich,
    )

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        topic_trigger=fake_trigger,
        candidate_quiet_seconds=0,
        trigger_delay_seconds=0.01,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "我最近在看便宜代步车和小改件怎么平衡", lang="zh-CN")

    await pool.process_ready_topics(lang="en", now=time.time() + 60)
    await asyncio.wait_for(delivered.wait(), timeout=1.0)

    assert analyzer_langs == ["zh-CN"]
    assert enrich_langs == ["zh-CN"]


@pytest.mark.asyncio
async def test_topic_pool_discards_stale_analysis_when_new_turn_arrives():
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def fake_analyzer(*, lang, **kwargs):
        calls.append(lang)
        entered.set()
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
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    pool.note_user_message("妮可", "第二句又补充说我主要怕选错以后回不了头")
    release.set()
    assert await task is None

    assert len(calls) == 1  # analyzer ran once; its stale result was discarded
    assert pool.get_ready_materials("妮可") == []


@pytest.mark.asyncio
async def test_topic_pool_discards_analysis_when_explicit_purge_happens_midflight():
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fake_analyzer(*, lang, **kwargs):
        entered.set()
        await release.wait()
        return [
            {
                "interest": "隐私清理前的旧快照",
                "hook": "这不该在隐私清理后恢复",
                "relevance": 90,
            }
        ]

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "隐私前聊到的候选证据")

    task = asyncio.create_task(pool.process_now("妮可"))
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    pool._purge_accumulated_signals("妮可")
    release.set()
    assert await task is None

    assert pool.get_ready_materials("妮可") == []


@pytest.mark.asyncio
async def test_topic_pool_candidate_phase_does_not_enrich_online(monkeypatch):
    async def fake_analyzer(*, lang, **kwargs):
        return [
            {
                "interest": "旧话题",
                "hook": "这不该覆盖新输入",
                "relevance": 90,
            }
        ]

    async def fake_enrich(materials, *, lang=None, max_materials=2, **kwargs):
        raise AssertionError("candidate phase must not enrich online")

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

    await pool.process_now("妮可")

    assert pool.get_ready_materials("妮可")[0]["interest"] == "旧话题"


@pytest.mark.asyncio
async def test_topic_pool_keeps_prepared_material_when_new_turn_arrives_during_prepare(monkeypatch):
    entered_enrich = asyncio.Event()
    release_enrich = asyncio.Event()
    delivered = []

    async def fake_analyzer(*, lang, **kwargs):
        return [
            {
                "interest": "旧话题",
                "keywords": ["旧话题"],
                "relevance": 90,
            }
        ]

    async def fake_derive(**kwargs):
        return "旧话题 深搜"

    async def fake_enrich(materials, *, lang=None, max_materials=2, **kwargs):
        entered_enrich.set()
        await release_enrich.wait()
        out = []
        for material in materials:
            item = dict(material)
            item["material_hint"] = {"summary": "prepared"}
            item["online_query"] = "旧话题 深搜"
            item["online_angle"] = "prepared angle"
            out.append(item)
        return out

    async def fake_trigger(*, lanlan_name, material, lang):
        delivered.append(material["material_hint"]["summary"])
        return True

    monkeypatch.setattr(
        "main_logic.activity.llm_enrichment.derive_deep_search_query", fake_derive
    )
    monkeypatch.setattr(
        "main_logic.topic.pipeline.enrich_topic_materials_online",
        fake_enrich,
    )

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        topic_trigger=fake_trigger,
        trigger_delay_seconds=0.01,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "第一句认真说一下我最近一直在纠结要不要换工作")

    await pool.process_now("妮可")
    await entered_enrich.wait()
    pool.note_user_message("妮可", "第二句又补充说我主要怕选错以后回不了头")
    release_enrich.set()
    await asyncio.sleep(0.01)
    assert delivered == ["prepared"]


@pytest.mark.asyncio
async def test_topic_pool_does_not_wait_for_new_turn_before_delivery_prepare(monkeypatch):
    deep_calls = []
    delivered = []

    async def fake_analyzer(*, lang, **kwargs):
        return [
            {
                "interest": "旧话题",
                "keywords": ["旧话题"],
                "relevance": 90,
            }
        ]

    async def fake_deepen(self, name, material, lang):
        deep_calls.append(material["interest"])

    async def fake_trigger(*, lanlan_name, material, lang):
        delivered.append(material["interest"])
        return True

    monkeypatch.setattr(TopicHookPool, "_deepen_material", fake_deepen)
    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        topic_trigger=fake_trigger,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "旧话题：我最近一直在纠结买车是不是代表生活进入新阶段")

    await pool.process_now("妮可")
    await asyncio.sleep(0.01)
    pool.note_user_message("妮可", "新话题：我后来又开始纠结换工作和现实压力怎么平衡")
    await asyncio.sleep(0.01)
    assert deep_calls == ["旧话题"]
    assert delivered == ["旧话题"]


@pytest.mark.asyncio
async def test_topic_pool_preserves_post_candidate_signals_after_delivery():
    delivered = []

    async def fake_analyzer(*, lang, **kwargs):
        return [
            {
                "interest": "旧话题",
                "keywords": ["旧话题"],
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
        trigger_delay_seconds=0.1,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "旧话题：我最近一直在纠结买车是不是代表生活进入新阶段")

    await pool.process_now("妮可")
    await asyncio.sleep(0.03)
    pool.note_user_message("妮可", "新话题：我后来又开始纠结换工作和现实压力怎么平衡")
    await asyncio.sleep(0.18)

    signals = pool._signal_store.format_global_signals("妮可", lang="zh-CN")
    assert delivered == ["旧话题"]
    assert "新话题" in signals
    assert "旧话题" not in signals


@pytest.mark.asyncio
async def test_topic_pool_waits_for_delivery_gate_before_deep_prepare(monkeypatch):
    deep_calls = []
    delivered = []
    gate = {"open": False}

    async def fake_analyzer(*, lang, **kwargs):
        return [
            {
                "interest": "需要等投递窗口的话题",
                "keywords": ["窗口"],
                "relevance": 90,
            }
        ]

    async def fake_deepen(self, name, material, lang):
        deep_calls.append(material["interest"])
        material["material_hint"] = {"summary": "prepared"}

    async def fake_trigger(*, lanlan_name, material, lang):
        delivered.append(material["interest"])
        return True

    monkeypatch.setattr(TopicHookPool, "_deepen_material", fake_deepen)
    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        topic_trigger=fake_trigger,
        delivery_available=lambda name: gate["open"],
        trigger_delay_seconds=0,
        trigger_retry_delay_seconds=0.03,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "投递窗口关闭时也不能提前烧掉深搜准备")

    await pool.process_now("妮可")
    await asyncio.sleep(0.01)

    assert deep_calls == []
    assert delivered == []
    assert "deep_search_done" not in pool._materials["妮可"][0]

    gate["open"] = True
    await asyncio.sleep(0.05)

    assert deep_calls == ["需要等投递窗口的话题"]
    assert delivered == ["需要等投递窗口的话题"]


@pytest.mark.asyncio
async def test_topic_pool_release_predicate_ignores_new_turns_during_delivery():
    release_checks = []

    async def fake_analyzer(*, lang, **kwargs):
        return [{"interest": "排队投递时不再受 quiet gate 影响", "relevance": 90}]

    async def fake_trigger(*, lanlan_name, material, lang):
        release_available = material["_topic_release_available"]
        release_checks.append(release_available())
        pool.note_user_message("妮可", "trigger 已交给投递队列后又来了新 turn", lang="zh-CN")
        release_checks.append(release_available())
        return False

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_retry_delay_seconds=60,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "一个已经成熟、准备排队投递的话题", lang="zh-CN")
    await pool.process_now("妮可")
    await asyncio.sleep(0.03)

    assert release_checks == [True, True]


@pytest.mark.asyncio
async def test_topic_pool_release_predicate_skips_manager_release_after_claim():
    calls = []
    release_checks = []

    async def fake_analyzer(*, lang, **kwargs):
        return [{"interest": "提交后不重复检查 manager release", "relevance": 90}]

    def delivery_available(name, *, include_manager_release=True):
        calls.append(include_manager_release)
        return True

    async def fake_trigger(*, lanlan_name, material, lang):
        release_checks.append(material["_topic_release_available"]())
        return False

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        delivery_available=delivery_available,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_retry_delay_seconds=60,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "这个话题先通过 submit 前检查，claim 后 manager release 会变成关闭", lang="zh-CN")
    await pool.process_now("妮可")
    await asyncio.sleep(0.03)

    assert calls == [True, True, False]
    assert release_checks == [True]


@pytest.mark.asyncio
async def test_topic_pool_release_predicate_reruns_legacy_delivery_gate():
    calls = []
    release_checks = []
    gate = {"open": True}

    async def fake_analyzer(*, lang, **kwargs):
        return [{"interest": "legacy delivery gate 关闭后不能放行", "relevance": 90}]

    def delivery_available(name):
        calls.append(name)
        return gate["open"]

    async def fake_trigger(*, lanlan_name, material, lang):
        gate["open"] = False
        release_checks.append(material["_topic_release_available"]())
        return False

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        delivery_available=delivery_available,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_retry_delay_seconds=60,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "这个话题先通过 submit 前检查，但 release 前 legacy gate 会关闭", lang="zh-CN")
    await pool.process_now("妮可")
    await asyncio.sleep(0.03)

    assert calls == ["妮可", "妮可", "妮可"]
    assert release_checks == [False]


@pytest.mark.asyncio
async def test_topic_pool_process_ready_ignores_legacy_candidate_quiet_window():
    calls = []

    async def fake_analyzer(*, lang, **kwargs):
        calls.append(lang)
        return [{"interest": "稳定话题", "relevance": 90}]

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        candidate_quiet_seconds=60,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "我最近一直在纠结要不要换工作")
    last_turn_at = pool._signal_store.last_turn_at("妮可")
    assert last_turn_at is not None

    await pool.process_ready_topics(now=last_turn_at + 1, lang="zh-CN")
    assert calls == ["zh-CN"]
    assert pool.get_ready_materials("妮可")[0]["interest"] == "稳定话题"


@pytest.mark.asyncio
async def test_topic_pool_process_ready_rearms_restored_signals_until_delivery(tmp_path):
    calls = []
    path = tmp_path / "topic_signals.json"
    base = time.time() - 120
    store = TopicSignalStore(
        min_user_turns_for_topic=2,
        persistence_path=path,
    )
    store.note_turn("妮可", actor="user", text="我最近一直在认真考虑换城市生活", now=base)
    store.note_turn("妮可", actor="user", text="换城市这件事反复纠结很久", now=base + 1)
    store.flush()

    async def fake_analyzer(*, lang, global_signals):
        calls.append(global_signals)
        return [{"interest": "恢复后的换城市话题", "relevance": 90}]

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        candidate_quiet_seconds=60,
        min_user_turns_for_topic=2,
        signal_store_path=path,
    )

    await pool.process_ready_topics(now=base + 121, lang="zh-CN")
    await pool.process_ready_topics(now=base + 141, lang="zh-CN")

    assert len(calls) == 1
    assert "换城市" in calls[0]
    assert pool.get_ready_materials("妮可")[0]["interest"] == "恢复后的换城市话题"

    restarted = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        candidate_quiet_seconds=60,
        min_user_turns_for_topic=2,
        signal_store_path=path,
    )

    await restarted.process_ready_topics(now=base + 161, lang="zh-CN")

    assert len(calls) == 2
    assert "换城市" in calls[1]
    assert restarted.get_ready_materials("妮可")[0]["interest"] == "恢复后的换城市话题"


@pytest.mark.asyncio
async def test_topic_pool_restored_signals_respect_persisted_used_history(tmp_path):
    path = tmp_path / "topic_signals.json"
    delivered = []
    analyzer_calls = 0

    async def fake_analyzer(*, lang, global_signals):
        nonlocal analyzer_calls
        analyzer_calls += 1
        return [
            {
                "interest": "买车",
                "keywords": [],
                "relevance": 90,
            }
        ]

    async def fake_trigger(*, lanlan_name, material, lang):
        delivered.append(material["interest"])
        return True

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        topic_trigger=fake_trigger,
        auto_schedule=False,
        enable_online_enrichment=False,
        candidate_quiet_seconds=0,
        trigger_delay_seconds=0,
        min_user_turns_for_topic=1,
        daily_topic_limit=2,
        signal_store_path=path,
    )
    pool.note_user_message("妮可", "先投递一次，建立今天已经用过 deep topic 的节流历史", lang="zh-CN")
    await pool.process_now("妮可", lang="zh-CN")
    await asyncio.sleep(0.02)

    assert delivered == ["买车"]
    used_path = path.with_name("topic_signals.used_topics.json")
    used_payload = json.loads(used_path.read_text(encoding="utf-8"))
    assert used_payload["characters"]["妮可"][0]["used_at"] > 0
    used_text = used_path.read_text(encoding="utf-8")
    assert used_payload["characters"]["妮可"][0]["interest_hash"]
    assert used_payload["characters"]["妮可"][0]["keyword_hashes"] == []
    assert "买车" not in used_text

    store = TopicSignalStore(min_user_turns_for_topic=1, persistence_path=path)
    store.note_turn("妮可", actor="user", text="重启后仍然残留的同题候选证据", now=time.time())
    store.flush()

    restarted = TopicHookPool(
        analyzer=fake_analyzer,
        topic_trigger=fake_trigger,
        auto_schedule=False,
        enable_online_enrichment=False,
        candidate_quiet_seconds=0,
        trigger_delay_seconds=0,
        min_user_turns_for_topic=1,
        daily_topic_limit=2,
        signal_store_path=path,
    )
    await restarted.process_ready_topics(now=time.time() + 120, lang="zh-CN")
    await asyncio.sleep(0.02)

    assert analyzer_calls == 2
    assert delivered == ["买车"]
    assert restarted.get_ready_materials("妮可") == []


@pytest.mark.asyncio
async def test_topic_pool_clears_durable_signals_when_analysis_has_no_material(tmp_path):
    path = tmp_path / "topic_signals.json"

    async def fake_analyzer(*, lang, **kwargs):
        return []

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        signal_store_path=path,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "这批证据最后没有形成可投递话题", lang="zh-CN")
    await pool.process_now("妮可")

    assert pool.get_ready_materials("妮可") == []
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["characters"] == {}


@pytest.mark.asyncio
async def test_topic_pool_clears_durable_signals_after_successful_delivery(tmp_path):
    path = tmp_path / "topic_signals.json"
    delivered = asyncio.Event()

    async def fake_analyzer(*, lang, global_signals):
        return [{"interest": "投递后应清理的话题", "relevance": 90}]

    async def fake_trigger(*, lanlan_name, material, lang):
        delivered.set()
        return True

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_retry_delay_seconds=0.01,
        min_user_turns_for_topic=1,
        signal_store_path=path,
    )
    pool.note_user_message("妮可", "这段候选证据投递完成后不该继续留在磁盘", lang="zh-CN")
    await pool.process_now("妮可")
    await asyncio.wait_for(delivered.wait(), timeout=1.0)
    for _ in range(20):
        if not pool._trigger_tasks.get("妮可"):
            break
        await asyncio.sleep(0.01)

    reloaded = TopicSignalStore(
        min_user_turns_for_topic=1,
        persistence_path=path,
    )
    assert not reloaded.is_ready("妮可")


@pytest.mark.asyncio
async def test_topic_pool_process_ready_scopes_to_requested_character():
    calls = []

    async def fake_analyzer(*, lang, global_signals):
        calls.append(global_signals)
        return [{"interest": global_signals, "relevance": 90}]

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        candidate_quiet_seconds=60,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "妮可自己的可分析话题")
    pool.note_user_message("兰兰", "兰兰自己的可分析话题")
    now = time.time() + 120

    await pool.process_ready_topics(lanlan_name="妮可", now=now, lang="zh-CN")
    await pool.process_ready_topics(lanlan_name="妮可", now=now + 20, lang="zh-CN")

    assert len(calls) == 1
    assert "妮可自己的可分析话题" in calls[0]
    assert "兰兰自己的可分析话题" not in calls[0]
    assert pool.get_ready_materials("妮可")
    assert pool.get_ready_materials("兰兰") == []


@pytest.mark.asyncio
async def test_topic_pool_under_ready_signals_do_not_drop_pending_material():
    async def fake_analyzer(*, lang, global_signals):
        return [{"interest": "已经准备好的旧话题", "relevance": 90}]

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        candidate_quiet_seconds=0,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "我最近一直在纠结换工作")
    await pool.process_now("妮可", lang="zh-CN")
    assert pool.get_ready_materials("妮可")[0]["interest"] == "已经准备好的旧话题"

    pool.note_ai_message("妮可", "我先帮你把这个问题放在一边")
    await pool.process_ready_topics(lanlan_name="妮可", now=time.time() + 60, lang="zh-CN")

    assert pool.get_ready_materials("妮可")[0]["interest"] == "已经准备好的旧话题"


@pytest.mark.asyncio
async def test_topic_pool_processes_requested_character_while_privacy_is_enabled():
    calls = []

    async def fake_analyzer(*, lang, global_signals):
        calls.append(global_signals)
        return [{"interest": "隐私模式也不影响抽取", "relevance": 90}]

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        candidate_quiet_seconds=60,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "隐私开启前的候选证据", lang="zh-CN")
    last_turn_at = pool._signal_store.last_turn_at("妮可")
    assert last_turn_at is not None

    await pool.process_ready_topics(
        lanlan_name="妮可",
        now=last_turn_at + 30,
        lang="zh-CN",
    )

    assert len(calls) == 1
    assert pool.get_ready_materials("妮可")[0]["interest"] == "隐私模式也不影响抽取"


@pytest.mark.asyncio
async def test_activity_tracker_privacy_purge_noops_for_topic_pool(monkeypatch):
    from main_logic.activity.tracker import UserActivityTracker

    calls = []

    class FakePool:
        def purge_all_accumulated_signals(self):
            calls.append("all")

        def purge_accumulated_signals(self, lanlan_name):
            calls.append(lanlan_name)

    monkeypatch.setattr(
        "main_logic.topic.pipeline.get_topic_hook_pool",
        lambda: FakePool(),
    )

    tracker = UserActivityTracker("妮可")
    await tracker._purge_topic_candidates_for_privacy(all_characters=True)

    assert calls == []


@pytest.mark.asyncio
async def test_activity_tracker_private_tick_purge_noops_for_topic_pool(monkeypatch):
    from main_logic.activity.tracker import UserActivityTracker

    calls = []

    class FakePool:
        def purge_all_accumulated_signals(self):
            calls.append("all")

        def purge_accumulated_signals(self, lanlan_name):
            calls.append(lanlan_name)

    monkeypatch.setattr(
        "main_logic.topic.pipeline.get_topic_hook_pool",
        lambda: FakePool(),
    )

    tracker = UserActivityTracker("妮可")
    await tracker._purge_topic_candidates_for_privacy()

    assert calls == []


@pytest.mark.asyncio
async def test_activity_tracker_topic_candidate_kickoff_does_not_block_heartbeat(monkeypatch):
    from main_logic.activity.tracker import UserActivityTracker

    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    class FakePool:
        async def process_ready_topics(self, **kwargs):
            calls.append(kwargs)
            entered.set()
            await release.wait()

    monkeypatch.setattr(
        "main_logic.topic.pipeline.get_topic_hook_pool",
        lambda: FakePool(),
    )

    tracker = UserActivityTracker("妮可")
    tracker._process_topic_candidates_if_ready(lang="zh-CN", now=123.0)
    await asyncio.wait_for(entered.wait(), timeout=1.0)

    tracker._process_topic_candidates_if_ready(lang="zh-CN", now=124.0)
    assert len(calls) == 1

    release.set()
    await asyncio.wait_for(tracker._topic_candidate_task, timeout=1.0)
    assert calls == [{"lanlan_name": "妮可", "lang": "zh-CN", "now": 123.0}]


@pytest.mark.asyncio
async def test_activity_tracker_can_start_topic_heartbeat_without_collector():
    from main_logic.activity.tracker import UserActivityTracker

    tracker = UserActivityTracker("妮可")
    tracker.ensure_activity_guess_loop_started()

    assert tracker._activity_guess_loop_task is not None
    assert tracker._collector_started is False

    tracker._activity_guess_loop_task.cancel()
    result = await asyncio.gather(
        tracker._activity_guess_loop_task,
        return_exceptions=True,
    )
    assert isinstance(result[0], asyncio.CancelledError)


def test_activity_tracker_topic_candidate_heartbeat_uses_full_global_locale():
    from main_logic.activity.tracker import UserActivityTracker

    source = inspect.getsource(UserActivityTracker._activity_guess_loop)

    assert "from utils.language_utils import get_global_language, get_global_language_full" in source
    assert "activity_lang = get_global_language() or 'en'" in source
    assert "topic_lang = get_global_language_full() or activity_lang" in source
    assert "self._process_topic_candidates_if_ready(lang=topic_lang, now=ts)" in source
    assert "lang=activity_lang" in source


@pytest.mark.asyncio
async def test_topic_pool_debounce_retries_after_background_analyzer_failure():
    calls = 0
    retried = asyncio.Event()

    async def flaky_analyzer(*, lang, **kwargs):
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
        auto_schedule=False,
        enable_online_enrichment=False,
        candidate_quiet_seconds=0,
        min_user_turns_for_topic=1,
    )

    pool.note_user_message("妮可", "我最近一直在想转职，不知道下一步怎么选")
    pool.note_user_message("妮可", "转职这件事我已经反复想了几周，主要怕走错方向")
    pool.note_user_message("妮可", "现在的工作也不是不能做，但我总觉得继续拖会更难")
    pool.note_user_message("妮可", "所以我想聊聊转职的现实风险和机会")

    await pool.process_ready_topics(lang="zh-CN")
    await pool.process_ready_topics(lang="zh-CN")
    await asyncio.wait_for(retried.wait(), timeout=1.0)
    materials = pool.get_ready_materials("妮可")

    assert calls == 2
    assert materials[0]["interest"] == "稳定转职话题"


@pytest.mark.asyncio
async def test_topic_pool_keeps_dirty_when_analyzer_returns_none():
    calls = 0
    retried = asyncio.Event()

    async def flaky_analyzer(*, lang, **kwargs):
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
        auto_schedule=False,
        enable_online_enrichment=False,
        candidate_quiet_seconds=0,
        min_user_turns_for_topic=1,
    )

    pool.note_user_message("妮可", "我最近一直想换个城市生活，但又怕重新开始太难")
    pool.note_user_message("妮可", "换城市这件事反复想了很久，主要是想改变现在的节奏")

    await pool.process_ready_topics(lang="zh-CN")
    await pool.process_ready_topics(lang="zh-CN")
    await asyncio.wait_for(retried.wait(), timeout=1.0)
    materials = pool.get_ready_materials("妮可")

    assert calls == 2
    assert materials[0]["interest"] == "稳定换城市话题"


@pytest.mark.asyncio
async def test_topic_pool_triggers_ready_hook_without_quiet_window():
    delivered = []

    async def fake_analyzer(*, lang, **kwargs):
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
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "我感觉买车算人生大事，最近一直在想它是不是代表生活进入新阶段", lang="zh-CN")

    await pool.process_now("妮可")
    await asyncio.sleep(0.03)

    assert delivered == [("妮可", "买车像进入新生活阶段", "zh-CN")]
    assert pool.get_ready_materials("妮可") == []
    assert pool._materials["妮可"] == []


@pytest.mark.asyncio
async def test_topic_pool_delivers_existing_pending_material_when_gate_opens():
    delivered = []
    gate = {"open": False}

    async def fake_analyzer(*, lang, **kwargs):
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
        delivery_available=lambda name: gate["open"],
        trigger_retry_delay_seconds=0.01,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "我感觉买车算人生大事，最近一直在想它是不是代表生活进入新阶段", lang="zh-CN")

    await pool.process_now("妮可")
    assert pool.get_ready_materials("妮可")

    await pool.process_ready_topics(lang="zh-CN")
    gate["open"] = True
    await asyncio.sleep(0.03)

    assert delivered == [("妮可", "买车像进入新生活阶段", "zh-CN")]
    assert pool.get_ready_materials("妮可") == []


@pytest.mark.asyncio
async def test_topic_pool_redacted_turn_timestamp_does_not_quiet_pending_delivery():
    delivered = []

    async def fake_analyzer(*, lang, **kwargs):
        return [
            {
                "interest": "买车像进入新生活阶段",
                "hook": "从买车背后的生活阶段感切入",
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
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "我感觉买车算人生大事，最近一直在想它是不是代表生活进入新阶段", lang="zh-CN")

    await pool.process_now("妮可")
    await asyncio.sleep(0.02)
    pool.note_turn_timestamp("妮可", lang="zh-CN")
    await asyncio.sleep(0.03)

    assert delivered == ["买车像进入新生活阶段"]


@pytest.mark.asyncio
async def test_topic_pool_triggers_highest_relevance_material_first():
    delivered = []

    async def fake_analyzer(*, lang, **kwargs):
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
        trigger_retry_delay_seconds=0.01,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "我最近认真聊了两个方向，但其中一个明显更适合展开", lang="zh-CN")

    await pool.process_now("妮可")
    await asyncio.sleep(0.03)

    assert delivered == ["高优先级话题"]
    assert pool._materials["妮可"] == []
    assert pool.get_ready_materials("妮可") == []


@pytest.mark.asyncio
async def test_topic_pool_keeps_material_pending_when_delivery_defers():
    first_attempt = asyncio.Event()
    attempts = []

    async def fake_analyzer(*, lang, **kwargs):
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
        trigger_retry_delay_seconds=0.01,
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

    async def fake_analyzer(*, lang, **kwargs):
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
        trigger_retry_delay_seconds=0.01,
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
async def test_topic_pool_backoff_when_delivery_defers_with_open_window():
    attempts = []
    first_attempt = asyncio.Event()
    retried = asyncio.Event()

    async def fake_analyzer(*, lang, **kwargs):
        return [
            {
                "interest": "买车像进入新生活阶段",
                "hook": "从买车背后的生活阶段感切入",
                "relevance": 95,
            }
        ]

    async def fake_trigger(*, lanlan_name, material, lang):
        attempts.append(time.time())
        if len(attempts) == 1:
            first_attempt.set()
            return False
        retried.set()
        return True

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_delay_seconds=0,
        trigger_retry_delay_seconds=0.05,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "我感觉买车算人生大事，最近一直在想它是不是代表生活进入新阶段", lang="zh-CN")

    await pool.process_now("妮可")
    await asyncio.wait_for(first_attempt.wait(), timeout=1.0)
    await asyncio.sleep(0.02)
    assert len(attempts) == 1

    await asyncio.wait_for(retried.wait(), timeout=1.0)

    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_topic_pool_retries_pending_material_after_trigger_exception():
    attempts = []
    retried = asyncio.Event()

    async def fake_analyzer(*, lang, **kwargs):
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
        trigger_retry_delay_seconds=0.01,
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

    async def fake_analyzer(*, lang, **kwargs):
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
async def test_topic_pool_keeps_pending_delivery_when_chat_continues():
    delivered = []

    async def fake_analyzer(*, lang, global_signals=None, **kwargs):
        last_line = (global_signals or "").strip().splitlines()[-1]
        interest = last_line.split(": ", 1)[-1] if ": " in last_line else last_line
        return [
            {
                "interest": interest,
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
        delivery_available=lambda name: False,
        trigger_retry_delay_seconds=60,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "旧话题：我最近一直在纠结买车是不是代表生活进入新阶段", lang="zh-CN")
    await pool.process_now("妮可")
    assert pool.get_ready_materials("妮可")[0]["interest"] == "旧话题：我最近一直在纠结买车是不是代表生活进入新阶段"

    pool.note_user_message("妮可", "新话题：我后来又开始纠结换工作和现实压力怎么平衡", lang="zh-CN")
    await pool.process_now("妮可")

    assert delivered == []
    assert pool.get_ready_materials("妮可")[0]["interest"] == "旧话题：我最近一直在纠结买车是不是代表生活进入新阶段"
    assert "妮可" in pool._dirty


@pytest.mark.asyncio
async def test_topic_pool_limits_daily_topic_triggers_to_two():
    delivered = []
    analyzer_calls = 0

    async def fake_analyzer(*, lang, **kwargs):
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
        for _ in range(20):
            if len(delivered) >= min(idx + 1, 2):
                break
            await asyncio.sleep(0.01)

    assert delivered == ["凯迪拉克预算压力", "周末海边旅行计划"]
    ready = pool.get_ready_materials("妮可")
    assert [item["interest"] for item in ready] == ["新房装修色差问题"]


@pytest.mark.asyncio
async def test_topic_pool_candidate_ignores_daily_quota():
    async def fake_analyzer(*, lang, **kwargs):
        return [
            {
                "interest": "新抽取的话题仍可入池",
                "keywords": ["新抽取"],
                "relevance": 95,
            }
        ]

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        daily_topic_limit=1,
        min_user_turns_for_topic=1,
    )
    pool._mark_topic_used(
        "妮可",
        {
            "used_at": time.time(),
            "interest": "今天已经投递过的话题",
            "keywords": ["已投递"],
        },
    )
    assert pool._daily_quota_reached("妮可")

    pool.note_user_message("妮可", "今天 quota 满了，但 candidate 仍然应该能抽取新话题", lang="zh-CN")
    await pool.process_now("妮可")

    assert pool.get_ready_materials("妮可")[0]["interest"] == "新抽取的话题仍可入池"


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

    async def fake_analyzer(*, lang, **kwargs):
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
async def test_topic_pool_suppresses_same_topic_after_it_was_used_recently():
    delivered = []

    async def fake_analyzer(*, lang, **kwargs):
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
async def test_topic_pool_suppresses_same_topic_across_calendar_day_within_48h(monkeypatch):
    delivered = []
    day_one_late = datetime(2026, 6, 14, 23, 50).timestamp()
    day_two_start = datetime(2026, 6, 15, 0, 1).timestamp()
    monkeypatch.setattr("main_logic.topic.pipeline.time.time", lambda: day_two_start)

    async def fake_analyzer(*, lang, **kwargs):
        return [
            {
                "interest": "跨天但仍在48小时窗口内的话题",
                "keywords": ["跨天去重"],
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
        min_trigger_gap_seconds=0,
        min_user_turns_for_topic=1,
    )
    pool._used_topics["妮可"] = [
        {
            "used_at": day_one_late,
            "interest": "跨天但仍在48小时窗口内的话题",
            "keywords": ["跨天去重"],
            "keyword_hashes": [],
            "bigram_hashes": [],
        }
    ]

    pool.note_user_message(
        "妮可",
        "我又继续说跨天去重这件事，虽然过了零点但其实还是同一个话题",
        lang="zh-CN",
    )
    await pool.process_ready_topics(now=day_two_start, lang="zh-CN")
    await asyncio.sleep(0.03)

    assert delivered == []
    assert pool.get_ready_materials("妮可") == []


@pytest.mark.asyncio
async def test_enrich_pool_keeps_material_when_privacy_toggles_on_mid_analysis():
    privacy = {"on": False}

    async def fake_analyzer(*, lang, global_signals):
        privacy["on"] = True
        return [
            {
                "interest": "隐私切换不影响话题留存",
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

    assert pool.get_ready_materials("妮可")[0]["interest"] == "隐私切换不影响话题留存"


@pytest.mark.asyncio
async def test_trigger_keeps_prepared_material_when_privacy_toggles_on_during_deepen(monkeypatch):
    privacy = {"on": False}
    delivered = []

    async def fake_analyzer(*, lang, global_signals):
        return [
            {
                "interest": "深搜期间隐私切换的话题",
                "keywords": ["privacy"],
                "relevance": 95,
                "risk": 10,
            }
        ]

    async def fake_deepen(self, name, material, lang):
        privacy["on"] = True
        material["material_hint"] = {"summary": "prepared during privacy"}

    async def fake_trigger(*, lanlan_name, material, lang):
        delivered.append(material["interest"])
        return True

    monkeypatch.setattr(TopicHookPool, "_deepen_material", fake_deepen)

    pool = TopicHookPool(
        analyzer=fake_analyzer,
        auto_schedule=False,
        enable_online_enrichment=False,
        topic_trigger=fake_trigger,
        trigger_delay_seconds=0.01,
        min_user_turns_for_topic=1,
    )
    pool.note_user_message("妮可", "一个足够具体、可以深挖的话题", lang="zh-CN")
    await pool.process_now("妮可")
    for _ in range(20):
        if delivered:
            break
        await asyncio.sleep(0.01)

    assert delivered == ["深搜期间隐私切换的话题"]
    assert pool.get_ready_materials("妮可") == []


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
    derive_calls = []
    enrich_calls = []

    async def fake_derive(**kwargs):
        derive_calls.append(1)
        return "q"

    async def fake_enrich(materials, **kwargs):
        enrich_calls.append([dict(m) for m in materials])
        return [dict(m, material_hint={"summary": "floor-online"}) for m in materials]

    monkeypatch.setattr(
        "main_logic.activity.llm_enrichment.derive_deep_search_query", fake_derive
    )
    monkeypatch.setattr(topic_pipeline, "enrich_topic_materials_online", fake_enrich)

    # deep-search disabled → never derives, but still runs floor online enrichment
    pool_off = TopicHookPool(auto_schedule=False, enable_deep_search=False)
    m1 = {"interest": "x", "keywords": ["x"]}
    await pool_off._deepen_material("n", m1, "zh")
    assert derive_calls == []
    assert len(enrich_calls) == 1
    assert m1["deep_search_done"] is True
    assert m1["material_hint"] == {"summary": "floor-online"}

    # online enrichment disabled → stays fully offline, including query derivation
    pool_online_off = TopicHookPool(
        auto_schedule=False,
        enable_online_enrichment=False,
    )
    m_offline = {"interest": "x", "keywords": ["x"]}
    await pool_online_off._deepen_material("n", m_offline, "zh")
    assert derive_calls == []
    assert len(enrich_calls) == 1
    assert "deep_search_done" not in m_offline

    # enabled → derives once; second call is a cached no-op
    pool_on = TopicHookPool(auto_schedule=False)
    m2 = {"interest": "x", "keywords": ["x"]}
    await pool_on._deepen_material("n", m2, "zh")
    await pool_on._deepen_material("n", m2, "zh")
    assert derive_calls == [1]
    assert len(enrich_calls) == 2
    assert m2["deep_search_done"] is True
