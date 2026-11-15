# -*- coding: utf-8 -*-
"""Regression tests for review_history's memo (head SystemMessage) preservation.

Bug (scenario D): when ``snapshot`` started with a SystemMessage memo and
nothing intervened to break the fingerprint chain at index 0, the
``_compute_review_capacity`` walk matched all the way through the head
SystemMessage (snapshot[0] == current[0] for the same memo), so the
replacement window covered the memo slot. The review LLM is told (via
``<key point 3>``) to preserve the memo, returning it in ``corrected_dialogue`` with
role ``SYSTEM_MESSAGE``, but the patching loop dropped any system role
unconditionally — leaving the memo slot empty in ``new_history`` and the
on-disk recent file ended up with no memo.

Fix: accept LLM's SystemMessage output into ``corrected_messages``, plus
defensive restore from snapshot[0] if LLM hallucinates a delete.
"""
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


# ─────────────── stub LLM ───────────────


class _FakeLLM:
    """Minimal stand-in for the review LLM. ``ainvoke`` returns a canned
    JSON string with the prescribed ``corrected_dialogue`` shape."""

    def __init__(self, corrected_dialogue: list[dict], explanation: str = "test"):
        self._payload = json.dumps(
            {"explanation": explanation, "corrected_dialogue": corrected_dialogue},
            ensure_ascii=False,
        )

    async def ainvoke(self, prompt: str, **kwargs: Any) -> Any:
        class _R:
            content: str

        r = _R()
        r.content = self._payload
        return r

    async def aclose(self) -> None:
        return None


# ─────────────── manager builder bypassing __init__ ───────────────


def _make_manager(tmp_path: Path, snapshot: list, fake_llm: _FakeLLM) -> tuple[
    CompressedRecentHistoryManager, str, str,
]:
    """Build a CompressedRecentHistoryManager without touching ConfigManager.

    ``snapshot`` is written to disk as ``recent.json`` so that
    ``aget_recent_history`` reads it back as ``current``. The fake LLM is
    bound via ``_get_review_llm``. ``name_mapping`` mirrors the real
    config_manager output.
    """
    lanlan_name = "Xiaoba"
    master = "Master"
    file_path = str(tmp_path / "recent.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(messages_to_dict(snapshot), f, ensure_ascii=False)

    mgr = object.__new__(CompressedRecentHistoryManager)
    mgr._config_manager = object()  # only used by assert_cloudsave_writable (patched)
    mgr.max_history_length = 10
    mgr.compress_threshold = 15
    mgr.log_file_path = {lanlan_name: file_path}
    mgr.name_mapping = {
        "human": master,
        "ai": lanlan_name,
        "system": "SYSTEM_MESSAGE",
    }
    mgr.user_histories = {lanlan_name: list(snapshot)}
    mgr._get_review_llm = lambda: fake_llm  # type: ignore[method-assign]
    return mgr, lanlan_name, master


def _read_disk(file_path: str) -> list:
    with open(file_path, encoding="utf-8") as f:
        return messages_from_dict(json.load(f))


# ─────────────── tests ───────────────


@pytest.fixture(autouse=True)
def _patch_cloudsave(monkeypatch):
    """Stub out the cloudsave gate so we don't need a real ConfigManager.

    The old ``aget_recent_history`` stub here is gone on purpose: the review
    commit now reads the file directly inside its critical section, so the stub
    was no longer on the code path and would have read as "this fixture controls
    ``current``" while controlling nothing.
    """
    monkeypatch.setattr(
        "memory.recent.assert_cloudsave_writable",
        lambda *a, **kw: None,
    )


def _run(coro):
    return asyncio.run(coro)


def test_memo_preserved_when_snapshot_has_head_system_and_llm_returns_it(tmp_path):
    """Scenario D: snapshot=[memo, dialogue...]; current identical (no
    parallel compress); LLM returns memo at head of corrected_dialogue.
    Expect: head SystemMessage survives, edited content from LLM applied."""
    memo = "先前对话的备忘录: 用户喜欢深夜聊天。"
    snapshot = [
        SystemMessage(content=memo),
        HumanMessage(content="hi 1"),
        HumanMessage(content="hi 2"),
        AIMessage(content="ai 1"),
        AIMessage(content="ai 2"),
        HumanMessage(content="hi 3"),
        AIMessage(content="ai 3"),
    ]
    edited_memo = "先前对话的备忘录: 该用户偏好夜聊。"
    corrected = [
        {"role": "SYSTEM_MESSAGE", "content": edited_memo},
        {"role": "Master", "content": "hi 1"},
        {"role": "Master", "content": "hi 2"},
        {"role": "Xiaoba", "content": "ai 1"},
        {"role": "Xiaoba", "content": "ai 2"},
        {"role": "Master", "content": "hi 3"},
        {"role": "Xiaoba", "content": "ai 3"},
    ]
    fake_llm = _FakeLLM(corrected)
    mgr, name, _master = _make_manager(tmp_path, snapshot, fake_llm)

    status, _fp = _run(mgr.review_history(name, snapshot=list(snapshot)))

    assert status == "patched"
    final = _read_disk(mgr.log_file_path[name])
    assert isinstance(final[0], SystemMessage), f"head must remain system, got {type(final[0]).__name__}"
    assert final[0].content == edited_memo, "LLM-edited memo content should be applied"
    assert len(final) == len(snapshot), "no concurrent change → length unchanged"


def test_memo_restored_when_llm_hallucinates_delete(tmp_path):
    """Scenario D defensive case: LLM ignores <key point 3> and omits the system
    entry from corrected_dialogue. Expect: snapshot[0] memo is restored
    (don't allow LLM to silently drop the memo)."""
    memo = "先前对话的备忘录: keep me."
    snapshot = [
        SystemMessage(content=memo),
        HumanMessage(content="hi 1"),
        HumanMessage(content="hi 2"),
        AIMessage(content="ai 1"),
        AIMessage(content="ai 2"),
        HumanMessage(content="hi 3"),
        AIMessage(content="ai 3"),
    ]
    # LLM forgot the system entry entirely.
    corrected = [
        {"role": "Master", "content": "hi 1"},
        {"role": "Master", "content": "hi 2"},
        {"role": "Xiaoba", "content": "ai 1"},
        {"role": "Xiaoba", "content": "ai 2"},
        {"role": "Master", "content": "hi 3"},
        {"role": "Xiaoba", "content": "ai 3"},
    ]
    fake_llm = _FakeLLM(corrected)
    mgr, name, _master = _make_manager(tmp_path, snapshot, fake_llm)

    status, _fp = _run(mgr.review_history(name, snapshot=list(snapshot)))

    assert status == "patched"
    final = _read_disk(mgr.log_file_path[name])
    assert isinstance(final[0], SystemMessage), "memo must be restored from snapshot"
    assert final[0].content == memo, "restored memo should be original (not LLM's omission)"


def test_empty_llm_output_falls_through_to_white_review(tmp_path):
    """An empty corrected_dialogue from the LLM signals \"delete the entire section\"; the original design
    handled this as a white review when take_count == 0 (no disk write and no fingerprint update).

    Regression: the normalization fallback must intervene only when corrected_messages is non-empty,
    otherwise an empty list would be forcibly replaced with [snapshot[0]], bypassing the white-review gate and erasing the conversation pane
    so only the memo remains (CodeRabbit Critical).
    """
    memo = "先前对话的备忘录: original."
    snapshot = [
        SystemMessage(content=memo),
        HumanMessage(content="hi 1"),
        AIMessage(content="ai 1"),
        HumanMessage(content="hi 2"),
        AIMessage(content="ai 2"),
    ]
    on_disk_before = list(snapshot)
    fake_llm = _FakeLLM([])  # LLM returns empty
    mgr, name, _master = _make_manager(tmp_path, on_disk_before, fake_llm)

    status, fp = _run(mgr.review_history(name, snapshot=list(snapshot)))

    assert status == "white", f"空 corrected → 应走 white review，实际 {status}"
    assert fp is None
    # Disk contents remain untouched
    final = _read_disk(mgr.log_file_path[name])
    assert len(final) == len(on_disk_before)
    assert isinstance(final[0], SystemMessage) and final[0].content == memo


def test_pre_compression_drops_hallucinated_system(tmp_path):
    """History has not been compacted yet (snapshot[0] is not a SystemMessage), but the LLM hallucinated
    a system line: it must be dropped and the old filter behavior restored. Otherwise normalization does not run,
    and the fake memo is injected into the uncompacted conversation and contaminates downstream context (Codex P2)."""
    snapshot = [
        HumanMessage(content="hi 1"),
        AIMessage(content="ai 1"),
        HumanMessage(content="hi 2"),
        AIMessage(content="ai 2"),
        HumanMessage(content="hi 3"),
        AIMessage(content="ai 3"),
    ]
    corrected = [
        {"role": "Master", "content": "hi 1"},
        {"role": "Xiaoba", "content": "ai 1"},
        {"role": "SYSTEM_MESSAGE", "content": "fake hallucinated memo"},  # Hallucination
        {"role": "Master", "content": "hi 2"},
        {"role": "Xiaoba", "content": "ai 2"},
        {"role": "Master", "content": "hi 3"},
        {"role": "Xiaoba", "content": "ai 3"},
    ]
    fake_llm = _FakeLLM(corrected)
    mgr, name, _master = _make_manager(tmp_path, snapshot, fake_llm)

    status, _fp = _run(mgr.review_history(name, snapshot=list(snapshot)))

    assert status == "patched"
    final = _read_disk(mgr.log_file_path[name])
    # No SystemMessage may appear anywhere (snapshot has none, so result must not either)
    assert not any(isinstance(m, SystemMessage) for m in final), \
        f"幻觉 system 必须被 drop，实际 final={[type(m).__name__ for m in final]}"


def test_only_system_no_dialogue_falls_through_to_white_review(tmp_path):
    """If the LLM returns only a system message and no conversation ≡ an empty list (semantically, \"delete the whole conversation\"),
    use white review.

    Regression: normalization must not \"repair\" this bad output into [SystemMessage], or
    length=1 would bypass the take_count==0 white-review gate, wiping the conversation pane and leaving only the memo
    （Codex P1）。
    """
    memo = "先前对话的备忘录: original."
    snapshot = [
        SystemMessage(content=memo),
        HumanMessage(content="hi 1"),
        AIMessage(content="ai 1"),
        HumanMessage(content="hi 2"),
        AIMessage(content="ai 2"),
    ]
    on_disk_before = list(snapshot)
    fake_llm = _FakeLLM([
        {"role": "SYSTEM_MESSAGE", "content": "memo only, no dialogue"},
    ])
    mgr, name, _master = _make_manager(tmp_path, on_disk_before, fake_llm)

    status, fp = _run(mgr.review_history(name, snapshot=list(snapshot)))

    assert status == "white", f"只返 system → 应走 white review，实际 {status}"
    assert fp is None
    # Disk remains unchanged
    final = _read_disk(mgr.log_file_path[name])
    assert len(final) == len(on_disk_before)
    assert isinstance(final[0], SystemMessage) and final[0].content == memo
    # Conversation pane remains unchanged
    assert [type(m).__name__ for m in final[1:]] == ["HumanMessage", "AIMessage", "HumanMessage", "AIMessage"]


def test_memo_promoted_when_llm_misplaces_it_in_middle(tmp_path):
    """LLM hallucinates by putting SystemMessage at index 1 instead of 0.
    Expect: it gets promoted to head of corrected_messages."""
    memo = "先前对话的备忘录: 用户喜欢深夜聊天。"
    misplaced_memo = "先前对话的备忘录: 用户偏好夜聊。"
    snapshot = [
        SystemMessage(content=memo),
        HumanMessage(content="hi 1"),
        HumanMessage(content="hi 2"),
        AIMessage(content="ai 1"),
        AIMessage(content="ai 2"),
        HumanMessage(content="hi 3"),
        AIMessage(content="ai 3"),
    ]
    corrected = [
        {"role": "Master", "content": "hi 1"},
        {"role": "SYSTEM_MESSAGE", "content": misplaced_memo},  # Misaligned
        {"role": "Master", "content": "hi 2"},
        {"role": "Xiaoba", "content": "ai 1"},
        {"role": "Xiaoba", "content": "ai 2"},
        {"role": "Master", "content": "hi 3"},
        {"role": "Xiaoba", "content": "ai 3"},
    ]
    fake_llm = _FakeLLM(corrected)
    mgr, name, _master = _make_manager(tmp_path, snapshot, fake_llm)

    status, _fp = _run(mgr.review_history(name, snapshot=list(snapshot)))

    assert status == "patched"
    final = _read_disk(mgr.log_file_path[name])
    assert isinstance(final[0], SystemMessage), "memo 必须在头部"
    assert final[0].content == misplaced_memo, "用 LLM 给的（首条 system）"
    # No SystemMessage may appear at any other position
    assert not any(isinstance(m, SystemMessage) for m in final[1:])


def test_memo_dedup_when_llm_returns_multiple_systems(tmp_path):
    """LLM returns several SystemMessages. Expect: keep only the first as the leading memo."""
    memo = "先前对话的备忘录: 原文。"
    snapshot = [
        SystemMessage(content=memo),
        HumanMessage(content="hi 1"),
        HumanMessage(content="hi 2"),
        AIMessage(content="ai 1"),
        AIMessage(content="ai 2"),
        HumanMessage(content="hi 3"),
        AIMessage(content="ai 3"),
    ]
    corrected = [
        {"role": "SYSTEM_MESSAGE", "content": "memo v1"},
        {"role": "Master", "content": "hi 1"},
        {"role": "SYSTEM_MESSAGE", "content": "memo v2 (bogus)"},  # Extra output
        {"role": "Master", "content": "hi 2"},
        {"role": "Xiaoba", "content": "ai 1"},
        {"role": "Xiaoba", "content": "ai 2"},
        {"role": "Master", "content": "hi 3"},
        {"role": "Xiaoba", "content": "ai 3"},
    ]
    fake_llm = _FakeLLM(corrected)
    mgr, name, _master = _make_manager(tmp_path, snapshot, fake_llm)

    status, _fp = _run(mgr.review_history(name, snapshot=list(snapshot)))

    assert status == "patched"
    final = _read_disk(mgr.log_file_path[name])
    sys_msgs = [m for m in final if isinstance(m, SystemMessage)]
    assert len(sys_msgs) == 1, f"应只剩 1 条 system，实际 {len(sys_msgs)}"
    assert isinstance(final[0], SystemMessage)
    assert final[0].content == "memo v1", "只留首条作为头部 memo"


def test_memo_preserved_with_concurrent_append(tmp_path):
    """Scenario D + parallel append: snapshot=[memo, dialogue]; current=
    snapshot+1 new message added during review. Expect: memo + edited
    dialogue + new appended message all coexist."""
    memo = "先前对话的备忘录: hello world."
    snapshot = [
        SystemMessage(content=memo),
        HumanMessage(content="hi 1"),
        AIMessage(content="ai 1"),
        HumanMessage(content="hi 2"),
        AIMessage(content="ai 2"),
    ]
    # Simulate a new user message being written to disk while the review LLM is running
    appended = HumanMessage(content="brand new")
    current_on_disk = list(snapshot) + [appended]
    fake_llm = _FakeLLM([
        {"role": "SYSTEM_MESSAGE", "content": memo},
        {"role": "Master", "content": "hi 1"},
        {"role": "Xiaoba", "content": "ai 1"},
        {"role": "Master", "content": "hi 2"},
        {"role": "Xiaoba", "content": "ai 2"},
    ])
    mgr, name, _master = _make_manager(tmp_path, current_on_disk, fake_llm)

    status, _fp = _run(mgr.review_history(name, snapshot=list(snapshot)))

    assert status == "patched"
    final = _read_disk(mgr.log_file_path[name])
    assert isinstance(final[0], SystemMessage)
    assert final[0].content == memo
    assert final[-1].content == "brand new", "appended message must survive review patch"
    assert len(final) == len(snapshot) + 1
