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
