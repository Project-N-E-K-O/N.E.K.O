"""回归测试：reply_buffer_service 的 buffer / store_reply / _flush_impl 修改后行为验证。"""
from __future__ import annotations

import asyncio
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_package_name = "plugin.plugins.qq_auto_reply"
_mock_pipeline_models = MagicMock()
for name in ("pipeline_models", f"{_package_name}.pipeline_models"):
    sys.modules[name] = _mock_pipeline_models

import reply_buffer_service
reply_buffer_service.__package__ = _package_name
from reply_buffer_service import QQReplyBufferService, PendingReply, _MAX_BUFFER_COUNT


def _make_plugin():
    p = MagicMock()
    p._qq_settings = {}
    p._emit_log = MagicMock()
    p.permission_mgr = None
    p.reply_delivery_node = MagicMock()
    p.reply_delivery_node.deliver = AsyncMock(return_value=MagicMock(delivered=True))
    p.reply_pipeline = MagicMock()
    p.reply_pipeline.run = AsyncMock()
    return p


class TestStoreReplyDetached(unittest.TestCase):
    """Fix #1 & #6: store_reply 正确回填到 _detached 桶（复合键）。"""

    def setUp(self):
        self.svc = QQReplyBufferService(_make_plugin())

    def test_store_into_pending_normal(self):
        p = PendingReply("u1", False, "")
        p.bucket_id = 5
        self.svc._pending["k"] = p
        ok = self.svc.store_reply("k", "hello", ["block"], expected_bucket_id=5)
        self.assertTrue(ok)
        self.assertEqual(p.bot_blocks, ["block"])

    def test_store_into_detached(self):
        p = PendingReply("u1", True, "g1")
        p.bucket_id = 7
        self.svc._detached[self.svc._detached_key("k", 7)] = p
        ok = self.svc.store_reply("k", "hi", ["b"], expected_bucket_id=7)
        self.assertTrue(ok)
        self.assertEqual(p.bot_blocks, ["b"])

    def test_store_pending_newer_but_detached_matches(self):
        """_pending 有新桶(id=9), _detached 有匹配桶(id=8) → 写入 _detached。"""
        pending_new = PendingReply("u1", True, "g1")
        pending_new.bucket_id = 9
        self.svc._pending["k"] = pending_new
        detached_old = PendingReply("u1", True, "g1")
        detached_old.bucket_id = 8
        self.svc._detached[self.svc._detached_key("k", 8)] = detached_old

        ok = self.svc.store_reply("k", "old_reply", ["old_block"], expected_bucket_id=8)
        self.assertTrue(ok)
        self.assertIsNone(pending_new.bot_blocks)
        self.assertEqual(detached_old.bot_blocks, ["old_block"])

    def test_store_pending_matches_no_detached_fallback(self):
        pending = PendingReply("u1", True, "g1")
        pending.bucket_id = 10
        detached_other = PendingReply("u1", True, "g1")
        detached_other.bucket_id = 10
        self.svc._pending["k"] = pending
        self.svc._detached[self.svc._detached_key("k", 10)] = detached_other
        ok = self.svc.store_reply("k", "r", ["b"], expected_bucket_id=10)
        self.assertTrue(ok)
        self.assertEqual(pending.bot_blocks, ["b"])
        self.assertIsNone(detached_other.bot_blocks)

    def test_store_id_mismatch_both_dicts(self):
        pending = PendingReply("u1", True, "g1")
        pending.bucket_id = 100
        detached = PendingReply("u1", True, "g1")
        detached.bucket_id = 101
        self.svc._pending["k"] = pending
        self.svc._detached[self.svc._detached_key("k", 101)] = detached
        ok = self.svc.store_reply("k", "r", ["b"], expected_bucket_id=42)
        self.assertFalse(ok)

    def test_store_no_bucket(self):
        ok = self.svc.store_reply("k", "r", ["b"], expected_bucket_id=1)
        self.assertFalse(ok)

    def test_two_detached_buckets_same_session(self):
        """Fix #7: 两个 detached 桶共存（复合键），各自独立回填。"""
        a = PendingReply("u1", True, "g1")
        a.bucket_id = 5
        b = PendingReply("u1", True, "g1")
        b.bucket_id = 6
        self.svc._detached[self.svc._detached_key("k", 5)] = a
        self.svc._detached[self.svc._detached_key("k", 6)] = b

        ok_a = self.svc.store_reply("k", "reply_a", ["ba"], expected_bucket_id=5)
        ok_b = self.svc.store_reply("k", "reply_b", ["bb"], expected_bucket_id=6)
        self.assertTrue(ok_a)
        self.assertTrue(ok_b)
        self.assertEqual(a.bot_blocks, ["ba"])
        self.assertEqual(b.bot_blocks, ["bb"])


class TestHasPendingAndGetState(unittest.TestCase):

    def setUp(self):
        self.svc = QQReplyBufferService(_make_plugin())

    def test_has_pending_detached(self):
        p = PendingReply("u1", True, "g1")
        p.task = None
        self.svc._detached[self.svc._detached_key("k", 1)] = p
        self.assertTrue(self.svc.has_pending("k"))

    def test_get_state_includes_detached(self):
        p1 = PendingReply("u1", False, "")
        p1.entries.append(("u1", "hello"))
        p2 = PendingReply("u2", True, "g1")
        p2.entries.append(("u2", "hi"))
        self.svc._pending["k1"] = p1
        self.svc._detached[self.svc._detached_key("k2", 5)] = p2
        state = self.svc.get_state()
        self.assertEqual(state["count"], 2)


class TestFlushDetachedCleanup(unittest.IsolatedAsyncioTestCase):

    async def test_cleanup_after_successful_delivery(self):
        svc = QQReplyBufferService(_make_plugin())
        p = PendingReply("u1", True, "g1")
        p.entries.append(("u1", "hi"))
        p.bot_blocks = [MagicMock(text="reply")]
        p.wait_until = 0
        p.bucket_id = 3
        svc._detached[svc._detached_key("k", 3)] = p
        await svc._flush_detached("k", p)
        # _flush_impl 成功后清理 _detached
        self.assertNotIn(svc._detached_key("k", 3), svc._detached)

    async def test_cleanup_after_timeout(self):
        svc = QQReplyBufferService(_make_plugin())
        p = PendingReply("u1", True, "g1")
        p.entries.append(("u1", "hi"))
        p.bot_blocks = None
        p.wait_until = 0
        p.bucket_id = 4
        p._no_reply_retries = 30
        svc._detached[svc._detached_key("k", 4)] = p
        await svc._flush_detached("k", p)
        # _flush_impl 超时后清理 _detached
        self.assertNotIn(svc._detached_key("k", 4), svc._detached)

    async def test_detached_persists_during_retry(self):
        """重试期间 _detached 条目保持，pipeline 可回填。"""
        svc = QQReplyBufferService(_make_plugin())
        p = PendingReply("u1", True, "g1")
        p.entries.append(("u1", "hi"))
        p.bot_blocks = None
        p.wait_until = 0
        p.bucket_id = 5
        dk = svc._detached_key("k", 5)
        svc._detached[dk] = p

        # 起 detached flush（会进入重试循环）
        task = asyncio.create_task(svc._flush_detached("k", p))
        await asyncio.sleep(0.05)
        # 重试期间 _detached 仍在
        self.assertIn(dk, svc._detached)
        # pipeline 可回填
        ok = svc.store_reply("k", "late_reply", ["late_block"], expected_bucket_id=5)
        self.assertTrue(ok)
        self.assertEqual(p.bot_blocks, ["late_block"])
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_two_detached_dont_interfere(self):
        """两个 detached 桶各自 flush，互不干扰。"""
        svc = QQReplyBufferService(_make_plugin())
        a = PendingReply("u1", True, "g1")
        a.entries.append(("u1", "a"))
        a.bot_blocks = [MagicMock(text="reply_a")]
        a.wait_until = 0
        a.bucket_id = 10
        b = PendingReply("u1", True, "g1")
        b.entries.append(("u1", "b"))
        b.bot_blocks = [MagicMock(text="reply_b")]
        b.wait_until = 0
        b.bucket_id = 11
        svc._detached[svc._detached_key("k", 10)] = a
        svc._detached[svc._detached_key("k", 11)] = b

        await svc._flush_detached("k", a)
        # a 被清理，b 还在
        self.assertNotIn(svc._detached_key("k", 10), svc._detached)
        self.assertIn(svc._detached_key("k", 11), svc._detached)

        await svc._flush_detached("k", b)
        self.assertNotIn(svc._detached_key("k", 11), svc._detached)


class TestMultiMessageSkipWait(unittest.IsolatedAsyncioTestCase):

    async def test_multi_message_skips_wait(self):
        svc = QQReplyBufferService(_make_plugin())
        p = PendingReply("u1", True, "g1")
        p.entries = [("u1", "m1"), ("u2", "m2")]
        p.bot_blocks = None
        p.wait_until = 0
        svc._pending["k"] = p
        await svc._flush("k", p)
        svc.plugin.reply_pipeline.run.assert_awaited_once()
        self.assertNotIn("k", svc._pending)

    async def test_single_message_still_waits(self):
        svc = QQReplyBufferService(_make_plugin())
        p = PendingReply("u1", True, "g1")
        p.entries = [("u1", "m1")]
        p.bot_blocks = None
        p.wait_until = 0
        svc._pending["k"] = p
        task = asyncio.create_task(svc._flush("k", p))
        await asyncio.sleep(0.05)
        svc.plugin.reply_pipeline.run.assert_not_awaited()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestBufferMaxCountDetached(unittest.IsolatedAsyncioTestCase):

    async def test_max_count_moves_to_detached(self):
        svc = QQReplyBufferService(_make_plugin())
        key = "group:123"
        result = await svc.buffer(key, "m1", "u1", True, "123")
        self.assertFalse(result)
        bucket_id = svc._pending[key].bucket_id
        for i in range(2, 18):
            result = await svc.buffer(key, f"m{i}", f"u{i}", True, "123")
            self.assertTrue(result)
        self.assertNotIn(key, svc._pending)
        self.assertIn(svc._detached_key(key, bucket_id), svc._detached)
        self.assertEqual(len(svc._detached), 1)

    async def test_max_count_old_pipeline_can_store_reply(self):
        svc = QQReplyBufferService(_make_plugin())
        key = "group:456"
        await svc.buffer(key, "m1", "u1", True, "456")
        old_bucket_id = svc._pending[key].bucket_id
        for i in range(2, 18):
            await svc.buffer(key, f"m{i}", f"u{i}", True, "456")
        ok = svc.store_reply(key, "reply", ["block"], expected_bucket_id=old_bucket_id)
        self.assertTrue(ok)

    async def test_two_max_count_buckets_coexist(self):
        """两个满桶共存 _detached（复合键不覆盖）。"""
        svc = QQReplyBufferService(_make_plugin())
        key = "group:789"
        await svc.buffer(key, "m1", "u1", True, "789")
        bid1 = svc._pending[key].bucket_id
        for i in range(2, 18):
            await svc.buffer(key, f"m{i}", f"u{i}", True, "789")
        # 新消息建新桶
        await svc.buffer(key, "m18", "u18", True, "789")
        bid2 = svc._pending[key].bucket_id
        for i in range(19, 35):
            await svc.buffer(key, f"m{i}", f"u{i}", True, "789")
        # 两个 detached 桶共存
        self.assertIn(svc._detached_key(key, bid1), svc._detached)
        self.assertIn(svc._detached_key(key, bid2), svc._detached)
        # 各自能独立回填
        ok1 = svc.store_reply(key, "r1", ["b1"], expected_bucket_id=bid1)
        ok2 = svc.store_reply(key, "r2", ["b2"], expected_bucket_id=bid2)
        self.assertTrue(ok1)
        self.assertTrue(ok2)


class TestTopicShiftSavesToDetached(unittest.IsolatedAsyncioTestCase):
    """Fix #3: 话题偏移时旧桶保存到 _detached。"""

    async def test_topic_shift_old_bucket_in_detached(self):
        """模拟话题偏移：abandon_on_no_reply 时旧桶移入 _detached 并起 flush。"""
        svc = QQReplyBufferService(_make_plugin())
        p = PendingReply("u1", True, "g1")
        p.entries.append(("u1", "old_topic"))
        p.bot_blocks = None
        p.wait_until = 0
        p.bucket_id = 42
        svc._pending["k"] = p

        await svc._flush("k", p, abandon_on_no_reply=True)
        self.assertNotIn("k", svc._pending)
        dk = svc._detached_key("k", 42)
        self.assertIn(dk, svc._detached)
        # flush_detached task 已启动
        self.assertIsNotNone(p.task)
        self.assertFalse(p.task.done())
        # 旧 pipeline 可回填 → 会被 flush task 交付
        ok = svc.store_reply("k", "late", ["late_block"], expected_bucket_id=42)
        self.assertTrue(ok)
        self.assertEqual(svc._detached[dk].bot_blocks, ["late_block"])
        # flush task 会在后台交付（cancel 避免残留）
        p.task.cancel()
        try:
            await p.task
        except asyncio.CancelledError:
            pass


class TestFrequencySuppression(unittest.IsolatedAsyncioTestCase):

    async def test_buffer_returns_true_on_subsequent(self):
        key = "group:freq1"
        svc = QQReplyBufferService(_make_plugin())
        self.assertFalse(await svc.buffer(key, "m1", "u1", True, "freq1"))
        for i in range(2, 8):
            self.assertTrue(await svc.buffer(key, f"m{i}", f"u{i}", True, "freq1"))

    async def test_buffer_applies_delay(self):
        import time as _time
        svc = QQReplyBufferService(_make_plugin())
        now = _time.time()
        await svc.buffer("group:delay1", "test", "u1", True, "delay1")
        p = svc._pending["group:delay1"]
        self.assertGreater(p.wait_until, now)
        self.assertLess(p.wait_until, now + 30)

    def test_max_count_controlled(self):
        self.assertEqual(_MAX_BUFFER_COUNT, 17)

    async def test_many_messages_merge(self):
        svc = QQReplyBufferService(_make_plugin())
        key = "group:merge1"
        await svc.buffer(key, "m1", "u1", True, "merge1")
        for i in range(2, 6):
            await svc.buffer(key, f"m{i}", f"u{i}", True, "merge1")
        p = svc._pending[key]
        self.assertEqual(len(p.entries), 5)
        p.bot_blocks = None
        p.wait_until = 0
        await svc._flush(key, p)
        svc.plugin.reply_pipeline.run.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
