# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from memory.recent import CompressedRecentHistoryManager
from utils.llm_client import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    messages_from_dict,
    messages_to_dict,
)


class _InvalidSummaryLLM:
    """返回无法解析的内容，用来模拟摘要模型连续失败。"""

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, prompt: str, **kwargs: Any) -> Any:
        self.calls += 1

        class _R:
            content = "not-json"

        return _R()

    async def aclose(self) -> None:
        return None


class _FakeConfig:
    """只提供 update_history 需要的角色 recent 路径。"""

    def __init__(self, lanlan_name: str, recent_path: str):
        self._lanlan_name = lanlan_name
        self._recent_path = recent_path

    async def aget_character_data(self):
        return (
            None,
            None,
            None,
            None,
            {},
            None,
            None,
            None,
            {self._lanlan_name: self._recent_path},
        )


@pytest.fixture(autouse=True)
def _patch_cloudsave(monkeypatch):
    monkeypatch.setattr(
        "memory.recent.assert_cloudsave_writable",
        lambda *a, **kw: None,
    )


def _run(coro):
    return asyncio.run(coro)


def _make_manager(
    tmp_path: Path,
    lanlan_name: str = "Xiaoba",
) -> tuple[CompressedRecentHistoryManager, str]:
    recent_path = str(tmp_path / "recent.json")
    mgr = object.__new__(CompressedRecentHistoryManager)
    mgr._config_manager = _FakeConfig(lanlan_name, recent_path)
    mgr.max_history_length = 4
    mgr.compress_threshold = 5
    mgr.log_file_path = {lanlan_name: recent_path}
    mgr.name_mapping = {
        "human": "Master",
        "ai": lanlan_name,
        "system": "SYSTEM_MESSAGE",
    }
    mgr.user_histories = {lanlan_name: []}
    return mgr, lanlan_name


def _write_recent(path: str, messages: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(messages_to_dict(messages), f, ensure_ascii=False)


def _read_recent(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return messages_from_dict(json.load(f))


def test_compress_history_returns_none_when_summary_llm_keeps_failing(tmp_path):
    mgr, name = _make_manager(tmp_path)
    fake_llm = _InvalidSummaryLLM()
    setattr(mgr, "_get_llm", lambda: fake_llm)
    setattr(
        mgr,
        "_aread_last_past_block_update_at",
        lambda _name: asyncio.sleep(0, result=None),
    )

    result = _run(mgr.compress_history([HumanMessage(content="hello")], name))

    assert result is None
    assert fake_llm.calls == 3


def test_update_history_preserves_existing_memo_when_compression_fails(tmp_path):
    mgr, name = _make_manager(tmp_path)
    old_messages = [
        SystemMessage(content="先前对话的备忘录: 柚希喜欢咖啡，讨厌重复提醒。"),
        HumanMessage(content="old user 1"),
        AIMessage(content="old ai 1"),
        HumanMessage(content="old user 2"),
        AIMessage(content="old ai 2"),
        HumanMessage(content="old user 3"),
    ]
    _write_recent(mgr.log_file_path[name], old_messages)

    async def _failed_compress(*args, **kwargs):
        return None

    setattr(mgr, "compress_history", _failed_compress)

    _run(mgr.update_history([AIMessage(content="new ai")], name, compress=True))

    final = _read_recent(mgr.log_file_path[name])
    assert len(final) == len(old_messages) + 1
    assert isinstance(final[0], SystemMessage)
    assert final[0].content == old_messages[0].content
    assert final[-1].content == "new ai"


# ── 持续失败兜底 / 分段压缩 / 后台合并 回归测试 ──────────────────────────

class _ValidSummaryLLM:
    """返回合法 JSON 摘要，模拟压缩成功。"""

    def __init__(self, summary: str = "压缩后的摘要"):
        self.calls = 0
        self._summary = summary

    async def ainvoke(self, prompt: str, **kwargs: Any) -> Any:
        self.calls += 1
        payload = json.dumps({"summary": self._summary}, ensure_ascii=False)

        class _R:
            content = payload

        return _R()

    async def aclose(self) -> None:
        return None


def _mock_summary_anchors(mgr):
    """mock past-block 锚点读写，让 compress_history 成功路径不碰磁盘 meta。"""
    setattr(mgr, "_aread_last_past_block_update_at", lambda _n: asyncio.sleep(0, result=None))
    setattr(mgr, "_awrite_last_past_block_update_at", lambda _n: asyncio.sleep(0, result=None))


def test_compress_history_returns_memo_on_success(tmp_path):
    mgr, name = _make_manager(tmp_path)
    fake_llm = _ValidSummaryLLM("柚希今天聊了咖啡。")
    setattr(mgr, "_get_llm", lambda: fake_llm)
    _mock_summary_anchors(mgr)

    result = _run(mgr.compress_history([HumanMessage(content="hello")], name))

    assert result is not None
    memo, summary = result
    assert isinstance(memo, SystemMessage)
    assert "柚希今天聊了咖啡。" in summary
    assert fake_llm.calls == 1  # 小输入走单次路径，不分段


def test_split_messages_by_budget(tmp_path, monkeypatch):
    mgr, name = _make_manager(tmp_path)
    monkeypatch.setattr("memory.recent.RECENT_COMPRESS_INPUT_BUDGET_TOKENS", 5)
    msgs = [HumanMessage(content=f"message number {i}") for i in range(6)]
    chunks = mgr._split_messages_by_budget(msgs, name)
    assert len(chunks) >= 2
    assert sum(len(c) for c in chunks) == len(msgs)  # 不丢消息
    flat = [m for c in chunks for m in c]
    assert [m.content for m in flat] == [m.content for m in msgs]  # 顺序保持


def test_compress_history_uses_segmented_path_for_large_input(tmp_path, monkeypatch):
    mgr, name = _make_manager(tmp_path)
    monkeypatch.setattr("memory.recent.RECENT_COMPRESS_INPUT_BUDGET_TOKENS", 5)
    fake_llm = _ValidSummaryLLM("s")
    setattr(mgr, "_get_llm", lambda: fake_llm)
    _mock_summary_anchors(mgr)

    msgs = [HumanMessage(content=f"message number {i}") for i in range(6)]
    result = _run(mgr.compress_history(msgs, name))

    assert result is not None
    assert fake_llm.calls > 1  # 走了分段（map 多次 + 主体最终总结）


def test_enforce_hard_cap_drops_oldest_keeps_memo_and_recent(tmp_path, monkeypatch):
    mgr, name = _make_manager(tmp_path)
    monkeypatch.setattr("memory.recent.RECENT_HARD_CAP_TOKENS", 20)
    memo = SystemMessage(content="先前对话的备忘录: 长期记忆。")
    body = [HumanMessage(content=f"original message {i} with some length") for i in range(12)]
    mgr.user_histories[name] = [memo] + body

    _run(mgr.enforce_hard_cap(name))

    kept = mgr.user_histories[name]
    assert kept[0] is memo  # 备忘录（已压缩长期记忆）保留
    assert len(kept) < 1 + len(body)  # 丢了最旧的原文
    assert len(kept) >= 1 + mgr.max_history_length  # 至少保留近期 max_history_length 条
    assert kept[-1].content == body[-1].content  # 保留的是最新的


def test_enforce_hard_cap_noop_when_under_budget(tmp_path):
    mgr, name = _make_manager(tmp_path)
    history = [SystemMessage(content="memo")] + [HumanMessage(content=f"m{i}") for i in range(8)]
    mgr.user_histories[name] = list(history)
    _run(mgr.enforce_hard_cap(name))
    assert mgr.user_histories[name] == history  # 未超大上限，不动


def test_merge_backup_memo_merges_when_batch_present(tmp_path):
    mgr, name = _make_manager(tmp_path)
    batch = [
        HumanMessage(content="u1"), AIMessage(content="a1"),
        HumanMessage(content="u2"), AIMessage(content="a2"),
    ]
    new_during = [HumanMessage(content="during1"), AIMessage(content="during2")]
    mgr.user_histories[name] = list(batch) + list(new_during)
    memo = SystemMessage(content="先前对话的备忘录: 压缩结果。")

    status = _run(mgr.merge_backup_memo(name, list(batch), memo))

    assert status == "merged"
    merged = mgr.user_histories[name]
    assert merged[0] is memo
    assert [m.content for m in merged[1:]] == [m.content for m in new_during]  # 期间新增保留


def test_merge_backup_memo_moot_when_batch_gone(tmp_path):
    mgr, name = _make_manager(tmp_path)
    batch = [HumanMessage(content="u1"), AIMessage(content="a1"), HumanMessage(content="u2")]
    # current 已被主路径压成 memo + 近期，batch 不在头部
    mgr.user_histories[name] = [SystemMessage(content="已压缩"), HumanMessage(content="recent")]

    status = _run(mgr.merge_backup_memo(name, list(batch), SystemMessage(content="memo")))

    assert status == "moot"
    assert mgr.user_histories[name][0].content == "已压缩"  # current 不动


def test_update_history_callback_ok_false_on_failure(tmp_path):
    mgr, name = _make_manager(tmp_path)
    msgs = [HumanMessage(content=f"m{i}") for i in range(7)]
    _write_recent(mgr.log_file_path[name], msgs)

    async def _fail(*a, **k):
        return None
    setattr(mgr, "compress_history", _fail)

    calls = []

    async def _cb(ln, snap, ok, detailed):
        calls.append((ln, ok))

    _run(mgr.update_history([HumanMessage(content="new")], name, on_compress_done=_cb))
    assert calls == [(name, False)]


def test_update_history_callback_ok_true_on_success(tmp_path):
    mgr, name = _make_manager(tmp_path)
    msgs = [HumanMessage(content=f"m{i}") for i in range(7)]
    _write_recent(mgr.log_file_path[name], msgs)

    async def _ok(*a, **k):
        return (SystemMessage(content="memo"), "memo")
    setattr(mgr, "compress_history", _ok)

    calls = []

    async def _cb(ln, snap, ok, detailed):
        calls.append((ln, ok))

    _run(mgr.update_history([HumanMessage(content="new")], name, on_compress_done=_cb))
    assert calls == [(name, True)]


def test_merge_backup_memo_reports_failed_on_write_error(tmp_path, monkeypatch):
    mgr, name = _make_manager(tmp_path)
    batch = [HumanMessage(content="u1"), AIMessage(content="a1"), HumanMessage(content="u2")]
    mgr.user_histories[name] = list(batch)

    async def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("memory.recent.atomic_write_json_async", _boom)

    status = _run(mgr.merge_backup_memo(name, list(batch), SystemMessage(content="memo")))
    assert status == "failed"  # 落盘失败必须报 failed，不能谎报 merged
