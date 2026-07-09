import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.proactive_delivery import DELIVERY_ACK_FUTURE_KEY
from main_logic.proactive_delivery import ProactiveDeliveryManager
from main_logic.topic.delivery import (
    build_topic_hook_callback,
    clear_topic_session_manager_getter,
    register_topic_session_manager_getter,
    topic_hook_delivery_available,
    trigger_topic_hook_once,
)


@pytest.fixture(autouse=True)
def _default_proactive_chat_enabled(monkeypatch):
    monkeypatch.setattr(
        "utils.preferences.load_global_conversation_settings",
        lambda: {"proactiveChatEnabled": True},
    )


def test_build_topic_hook_callback_contains_natural_opening_instruction():
    callback = build_topic_hook_callback(
        {
            "hook_id": "topic_car",
            "interest": "用户把买车当成新阶段",
            "hook": "从买车背后的生活阶段感切入",
            "opening_intent": "轻轻调侃，不要像问卷",
            "deepening_hint": "用户接话后再聊现实需求",
        },
        lang="zh-CN",
    )

    assert callback["channel"] == "topic_hook"
    assert callback["source_kind"] == "topic"
    assert callback["delivery_mode"] == "proactive"
    assert callback["priority"] == -20
    assert callback["metadata"]["hook_id"] == "topic_car"
    assert "只生成一句自然开场" in callback["detail"]
    assert "根据你的近期兴趣" in callback["detail"]
    assert "最近关注：用户把买车当成新阶段" in callback["detail"]
    # slimmed: small-model-authored angle/opening/deepening are no longer shipped
    assert "从买车背后的生活阶段感切入" not in callback["detail"]
    assert "轻轻调侃" not in callback["detail"]


def test_build_topic_hook_callback_requires_visible_online_angle_when_available():
    callback = build_topic_hook_callback(
        {
            "hook_id": "topic_car_cost",
            "interest": "用户把买车和生活自由感联系在一起",
            "hook": "先接住不想被人生流程推着走",
            "opening_intent": "像朋友随口一提",
            "online_used": True,
            "online_query": "年轻人 买车 通勤 养车 成本",
            "online_angle": "有搜索结果提到年轻人买车会先看通勤半径和养车成本",
        },
        lang="zh-CN",
    )

    assert "联网补充" in callback["detail"]
    assert "通勤半径和养车成本" in callback["detail"]
    assert "必须自然用上其中一个具体信息" in callback["detail"]


def test_build_topic_hook_callback_localizes_detail_for_japanese():
    callback = build_topic_hook_callback(
        {
            "hook_id": "topic_music",
            "interest": "夜に聴くインディーポップ",
            "hook": "眠る前の静かな気分から入る",
            "opening_intent": "友達みたいに短く触れる",
            "deepening_hint": "相手が乗ったら最近の曲の好みに広げる",
            "why_now": "最近よく音楽の話をしている",
            "material_hint": {"summary": "週末に聴いた曲の話題"},
            "online_query": "日本 インディーポップ 夜 おすすめ",
            "online_angle": "検索結果では夜向けの落ち着いたプレイリストが紹介されている",
        },
        lang="ja",
    )

    detail = callback["detail"]
    # intro line removed; detail is interest + online + final only
    assert "これは、すでに選別済みの低頻度な深掘り話題 hook です。" not in detail
    assert "最近気にしていること：夜に聴くインディーポップ" in detail
    assert "オンライン補足：" in detail
    assert "自然な一言の切り出しだけを生成してください" in detail
    assert "这是一个已经筛好的低频深话题 hook" not in detail
    assert "最近关注：" not in detail
    assert "请只生成一句自然开场" not in detail


def test_build_topic_hook_callback_preserves_traditional_chinese():
    callback = build_topic_hook_callback(
        {
            "hook_id": "topic_music",
            "interest": "最近想聽城市流行",
            "hook": "從晚上散步時想聽的歌切入",
            "opening_intent": "像朋友隨口一提",
            "online_query": "台灣 城市流行 夜晚 歌單",
            "online_angle": "搜尋結果提到夜晚通勤歌單",
        },
        lang="zh-TW",
    )

    detail = callback["detail"]
    # intro line removed; detail is interest + online + final only
    assert "這是一個已經篩好的低頻深話題 hook" not in detail
    assert "最近關注：最近想聽城市流行" in detail
    assert "請只生成一句自然開場" in detail
    assert "聯網補充" in detail
    assert "这是一个已经筛好的低频深话题 hook" not in detail
    assert "最近关注：" not in detail
    assert "请只生成一句自然开场" not in detail


def test_detail_templates_online_clause_has_no_english_hook_term():
    """Regression: the online sub-clause must not leak the English internal
    term 'hook' into any locale's prompt. When a topic is online-enriched
    (has online_angle), every locale's detail carries the online clause and
    weak models echo the trailing 'hook' as spoken text (non-EN especially)."""
    from main_logic.topic.delivery import _DETAIL_TEMPLATES
    for locale, tmpl in _DETAIL_TEMPLATES.items():
        assert 'hook' not in tmpl['online'].lower(), (
            f"{locale} online template still leaks the English term 'hook'"
        )


@pytest.mark.asyncio
async def test_trigger_topic_hook_once_enqueues_existing_manager_callback(monkeypatch):
    mgr = MagicMock()
    mgr.submit_proactive_callback = None
    mgr.enqueue_agent_callback = MagicMock()
    mgr.trigger_agent_callbacks = AsyncMock(return_value=True)

    clear_topic_session_manager_getter()
    register_topic_session_manager_getter(lambda name: mgr)

    delivered = await trigger_topic_hook_once(
        lanlan_name="妮可",
        material={
            "hook_id": "topic_car",
            "interest": "用户把买车当成新阶段",
            "hook": "从买车背后的生活阶段感切入",
        },
        lang="zh-CN",
    )

    assert delivered is True
    mgr.enqueue_agent_callback.assert_called_once()
    callback = mgr.enqueue_agent_callback.call_args.args[0]
    assert callback["task_id"] == "topic_car"
    assert callback["channel"] == "topic_hook"
    mgr.trigger_agent_callbacks.assert_awaited_once()
    clear_topic_session_manager_getter()


@pytest.mark.asyncio
async def test_trigger_topic_hook_once_waits_for_confirmed_delivery(monkeypatch):
    mgr = MagicMock()
    mgr.enqueue_agent_callback = MagicMock()
    mgr.trigger_agent_callbacks = AsyncMock(return_value=True)

    def submit(callback, *, priority=0, coalesce_key=None):
        mgr.submitted_callback = callback
        mgr.submitted_priority = priority
        mgr.submitted_coalesce_key = coalesce_key
        callback[DELIVERY_ACK_FUTURE_KEY].set_result(True)

    mgr.submit_proactive_callback = MagicMock(side_effect=submit)

    clear_topic_session_manager_getter()
    register_topic_session_manager_getter(lambda name: mgr)

    delivered = await trigger_topic_hook_once(
        lanlan_name="妮可",
        material={
            "hook_id": "topic_car",
            "interest": "用户把买车当成新阶段",
            "hook": "从买车背后的生活阶段感切入",
        },
        lang="zh-CN",
    )

    assert delivered is True
    mgr.submit_proactive_callback.assert_called_once()
    mgr.enqueue_agent_callback.assert_not_called()
    mgr.trigger_agent_callbacks.assert_not_called()
    callback = mgr.submitted_callback
    assert callback["task_id"] == "topic_car"
    assert mgr.submitted_priority == -20
    assert mgr.submitted_coalesce_key == "topic_car"
    clear_topic_session_manager_getter()


@pytest.mark.asyncio
async def test_trigger_topic_hook_once_reresolves_live_language(monkeypatch):
    mgr = MagicMock()
    mgr.topic_hook_delivery_allowed = MagicMock(return_value=True)
    mgr.current_topic_language = MagicMock(return_value="zh-TW")
    mgr.enqueue_agent_callback = MagicMock()

    def submit(callback, *, priority=0, coalesce_key=None):
        mgr.submitted_callback = callback
        callback[DELIVERY_ACK_FUTURE_KEY].set_result(True)

    mgr.submit_proactive_callback = MagicMock(side_effect=submit)

    clear_topic_session_manager_getter()
    register_topic_session_manager_getter(lambda name: mgr)

    delivered = await trigger_topic_hook_once(
        lanlan_name="妮可",
        # captured locale is stale zh-CN; the live tracker says zh-TW
        material={"hook_id": "topic_music", "interest": "最近想聽城市流行"},
        lang="zh-CN",
    )

    assert delivered is True
    detail = mgr.submitted_callback["detail"]
    assert "最近關注：最近想聽城市流行" in detail  # rendered with the live zh-TW template
    assert "最近关注：" not in detail  # not collapsed back to the captured zh
    mgr.current_topic_language.assert_called_once()
    clear_topic_session_manager_getter()


@pytest.mark.asyncio
async def test_trigger_topic_hook_once_skips_when_activity_gate_closed(monkeypatch):
    mgr = MagicMock()
    mgr.topic_hook_delivery_allowed = MagicMock(return_value=False)
    mgr.submit_proactive_callback = MagicMock()
    mgr.enqueue_agent_callback = MagicMock()
    mgr.trigger_agent_callbacks = AsyncMock(return_value=True)

    clear_topic_session_manager_getter()
    register_topic_session_manager_getter(lambda name: mgr)

    delivered = await trigger_topic_hook_once(
        lanlan_name="妮可",
        material={"hook_id": "topic_car", "interest": "用户把买车当成新阶段"},
        lang="zh-CN",
    )

    assert delivered is False
    mgr.topic_hook_delivery_allowed.assert_called_once()
    mgr.submit_proactive_callback.assert_not_called()
    mgr.enqueue_agent_callback.assert_not_called()
    mgr.trigger_agent_callbacks.assert_not_called()
    clear_topic_session_manager_getter()


@pytest.mark.asyncio
async def test_trigger_topic_hook_once_skips_when_proactive_chat_disabled(monkeypatch):
    mgr = MagicMock()
    mgr.topic_hook_delivery_allowed = MagicMock(return_value=True)
    mgr.submit_proactive_callback = MagicMock()
    mgr.enqueue_agent_callback = MagicMock()
    mgr.trigger_agent_callbacks = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "utils.preferences.load_global_conversation_settings",
        lambda: {"proactiveChatEnabled": False},
    )

    clear_topic_session_manager_getter()
    register_topic_session_manager_getter(lambda name: mgr)

    delivered = await trigger_topic_hook_once(
        lanlan_name="妮可",
        material={"hook_id": "topic_car", "interest": "用户把买车当成新阶段"},
        lang="zh-CN",
    )

    assert delivered is False
    mgr.topic_hook_delivery_allowed.assert_not_called()
    mgr.submit_proactive_callback.assert_not_called()
    mgr.enqueue_agent_callback.assert_not_called()
    mgr.trigger_agent_callbacks.assert_not_called()
    clear_topic_session_manager_getter()


def test_topic_hook_delivery_available_false_during_goodbye_silent():
    class FakeManager:
        def is_goodbye_silent(self):
            return True

        def topic_hook_delivery_allowed(self):
            return True

        def submit_proactive_callback(self, callback, *, priority=0, coalesce_key=None):
            raise AssertionError("preflight should not submit")

    clear_topic_session_manager_getter()
    register_topic_session_manager_getter(lambda name: FakeManager())
    try:
        assert topic_hook_delivery_available("妮可") is False
    finally:
        clear_topic_session_manager_getter()


def test_topic_hook_delivery_available_false_when_manager_cannot_release():
    class FakeManager:
        def topic_hook_delivery_allowed(self):
            return True

        def _can_release_proactive(self):
            return False

        def submit_proactive_callback(self, callback, *, priority=0, coalesce_key=None):
            raise AssertionError("preflight should not submit")

    clear_topic_session_manager_getter()
    register_topic_session_manager_getter(lambda name: FakeManager())
    try:
        assert topic_hook_delivery_available("妮可") is False
    finally:
        clear_topic_session_manager_getter()


def test_topic_hook_delivery_available_false_when_proactive_chat_disabled(monkeypatch):
    class FakeManager:
        def topic_hook_delivery_allowed(self):
            return True

        def submit_proactive_callback(self, callback, *, priority=0, coalesce_key=None):
            raise AssertionError("preflight should not submit")

    monkeypatch.setattr(
        "utils.preferences.load_global_conversation_settings",
        lambda: {"proactiveChatEnabled": False},
    )
    clear_topic_session_manager_getter()
    register_topic_session_manager_getter(lambda name: FakeManager())
    try:
        assert topic_hook_delivery_available("妮可") is False
    finally:
        clear_topic_session_manager_getter()


def test_topic_hook_delivery_available_true_when_proactive_chat_setting_missing(monkeypatch):
    class FakeManager:
        def topic_hook_delivery_allowed(self):
            return True

        def submit_proactive_callback(self, callback, *, priority=0, coalesce_key=None):
            raise AssertionError("preflight should not submit")

    monkeypatch.setattr(
        "utils.preferences.load_global_conversation_settings",
        lambda: {},
    )
    clear_topic_session_manager_getter()
    register_topic_session_manager_getter(lambda name: FakeManager())
    try:
        assert topic_hook_delivery_available("妮可") is True
    finally:
        clear_topic_session_manager_getter()


def test_topic_hook_delivery_available_false_when_proactive_chat_settings_fail(monkeypatch):
    class FakeManager:
        def topic_hook_delivery_allowed(self):
            return True

        def submit_proactive_callback(self, callback, *, priority=0, coalesce_key=None):
            raise AssertionError("preflight should not submit")

    def fail_settings():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(
        "utils.preferences.load_global_conversation_settings",
        fail_settings,
    )
    clear_topic_session_manager_getter()
    register_topic_session_manager_getter(lambda name: FakeManager())
    try:
        assert topic_hook_delivery_available("妮可") is False
    finally:
        clear_topic_session_manager_getter()


@pytest.mark.asyncio
async def test_trigger_topic_hook_once_retracts_submitted_callback_when_cancelled(monkeypatch):
    delivered_batches = []
    submitted = asyncio.Event()

    async def deliver(batch):
        delivered_batches.append(batch)

    class FakeManager:
        def __init__(self):
            self.pending_agent_callbacks = []
            self.pending_extra_replies = []
            self.proactive_manager = ProactiveDeliveryManager(
                deliver=deliver,
                can_release=lambda: False,
            )

        def submit_proactive_callback(self, callback, *, priority=0, coalesce_key=None):
            self.submitted_callback = callback
            self.proactive_manager.submit(callback, priority=priority, coalesce_key=coalesce_key)
            submitted.set()

        def enqueue_agent_callback(self, callback):
            self.pending_agent_callbacks.append(callback)

        async def trigger_agent_callbacks(self):
            return True

    mgr = FakeManager()
    clear_topic_session_manager_getter()
    register_topic_session_manager_getter(lambda name: mgr)

    task = asyncio.create_task(
        trigger_topic_hook_once(
            lanlan_name="妮可",
            material={"hook_id": "topic_car", "interest": "买车"},
            lang="zh-CN",
        )
    )
    await asyncio.wait_for(submitted.wait(), timeout=1)

    task.cancel()
    cancel_results = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(cancel_results[0], asyncio.CancelledError)

    assert mgr.proactive_manager.drain_pending() == []
    assert delivered_batches == []
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == []
    clear_topic_session_manager_getter()


@pytest.mark.asyncio
async def test_trigger_topic_hook_once_treats_cancelled_after_ack_as_delivered(monkeypatch):
    current_task = None

    class FakeManager:
        def submit_proactive_callback(self, callback, *, priority=0, coalesce_key=None):
            loop = asyncio.get_running_loop()
            future = callback[DELIVERY_ACK_FUTURE_KEY]
            assert current_task is not None
            loop.call_soon(future.set_result, True)
            loop.call_soon(current_task.cancel)

        def enqueue_agent_callback(self, callback):
            raise AssertionError("submit path should be used")

        async def trigger_agent_callbacks(self):
            raise AssertionError("submit path should wait for ack")

    mgr = FakeManager()
    clear_topic_session_manager_getter()
    register_topic_session_manager_getter(lambda name: mgr)

    current_task = asyncio.current_task()
    delivered = await trigger_topic_hook_once(
        lanlan_name="妮可",
        material={"hook_id": "topic_car", "interest": "买车"},
        lang="zh-CN",
    )

    assert delivered is True
    clear_topic_session_manager_getter()


@pytest.mark.asyncio
async def test_trigger_topic_hook_once_returns_false_when_manager_defers(monkeypatch):
    mgr = MagicMock()
    mgr.submit_proactive_callback = None
    mgr.enqueue_agent_callback = MagicMock()
    mgr.trigger_agent_callbacks = AsyncMock(return_value=False)

    clear_topic_session_manager_getter()
    register_topic_session_manager_getter(lambda name: mgr)

    delivered = await trigger_topic_hook_once(
        lanlan_name="妮可",
        material={"hook_id": "topic_car", "interest": "买车"},
        lang="zh-CN",
    )

    assert delivered is False
    mgr.enqueue_agent_callback.assert_called_once()
    mgr.trigger_agent_callbacks.assert_awaited_once()
    clear_topic_session_manager_getter()


@pytest.mark.asyncio
async def test_trigger_topic_hook_once_removes_callback_when_delivery_defers(monkeypatch):
    class FakeManager:
        def __init__(self):
            self.pending_agent_callbacks = []
            self.pending_extra_replies = []

        def enqueue_agent_callback(self, callback):
            callback["_callback_delivery_id"] = "topic_delivery_id"
            self.pending_agent_callbacks.append(callback)
            self.pending_extra_replies.append({"_callback_delivery_id": "topic_delivery_id"})

        async def trigger_agent_callbacks(self):
            return False

    mgr = FakeManager()
    clear_topic_session_manager_getter()
    register_topic_session_manager_getter(lambda name: mgr)

    delivered = await trigger_topic_hook_once(
        lanlan_name="妮可",
        material={"hook_id": "topic_car", "interest": "买车"},
        lang="zh-CN",
    )

    assert delivered is False
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == []
    clear_topic_session_manager_getter()


@pytest.mark.asyncio
async def test_trigger_topic_hook_once_returns_false_without_manager(monkeypatch):
    clear_topic_session_manager_getter()
    register_topic_session_manager_getter(lambda name: None)

    delivered = await trigger_topic_hook_once(
        lanlan_name="妮可",
        material={"hook_id": "topic_car", "interest": "买车"},
        lang="zh-CN",
    )

    assert delivered is False
    clear_topic_session_manager_getter()


@pytest.mark.asyncio
async def test_trigger_topic_hook_once_does_not_import_main_server(monkeypatch):
    clear_topic_session_manager_getter()

    delivered = await trigger_topic_hook_once(
        lanlan_name="妮可",
        material={"hook_id": "topic_car", "interest": "买车"},
        lang="zh-CN",
    )

    assert delivered is False
