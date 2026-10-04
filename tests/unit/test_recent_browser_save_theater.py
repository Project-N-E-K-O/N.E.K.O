# -*- coding: utf-8 -*-
"""Memory Browser saves must never turn theater capsules into ordinary memory."""
from __future__ import annotations

import json

import pytest

from utils import recent_file
from memory.message_sources import is_theater_episode_summary
from utils.llm_client import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    messages_from_dict,
    messages_to_dict,
)


@pytest.fixture(autouse=True)
def _reset_recent_file_locks():
    """Keep the module-level recent lock registry isolated per test."""
    for registry in (
        recent_file._LOCKS,
        recent_file._PENDING,
        recent_file._REDIRECTS,
        recent_file._DELETED,
        recent_file._GENERATIONS,
        recent_file._CONTENT_VERSIONS,
    ):
        registry.clear()
    yield
    for registry in (
        recent_file._LOCKS,
        recent_file._PENDING,
        recent_file._REDIRECTS,
        recent_file._DELETED,
        recent_file._GENERATIONS,
        recent_file._CONTENT_VERSIONS,
    ):
        registry.clear()


_CAPSULE_METADATA = {
    "source": "theater_numeric_v2",
    "memory_tier": "episode_summary",
    "message_kind": "episode_summary",
    "story_id": "story_rain",
    "session_id": "session_rain_1",
    "story_title": "雨夜合租",
    "run_index": 1,
    "story_run_count": 1,
}


def _original_history() -> list:
    return [
        SystemMessage(content="先前对话的备忘录: 旧摘要"),
        HumanMessage(content="h1"),
        SystemMessage(content="共同守住了雨夜里的住处。", metadata=dict(_CAPSULE_METADATA)),
        AIMessage(content="a1"),
        HumanMessage(content="h2"),
    ]


async def _save_through_route(tmp_path, monkeypatch, build_chat):
    from main_routers import memory_router
    import httpx
    import utils.config_manager as config_manager_module

    recent_path = tmp_path / "Role" / "recent.json"
    recent_path.parent.mkdir(parents=True)
    recent_file.write_recent_payload(
        recent_path, messages_to_dict(_original_history()),
    )

    class _Config:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

    class _Request:
        async def json(self):
            return {
                "filename": "recent_Role.json",
                "chat": build_chat(loaded_items),
                "fingerprint": loaded["fingerprint"],
                "identity_token": loaded["identity_token"],
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, _url, **_kwargs):
            return None

    monkeypatch.setattr(config_manager_module, "get_config_manager", lambda: _Config())
    monkeypatch.setattr(memory_router, "assert_cloudsave_writable", lambda *a, **k: None)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: _Client())
    loaded = await memory_router.get_recent_file("recent_Role.json")
    loaded_items = json.loads(loaded["content"])
    response = await memory_router.save_recent_file(_Request())
    saved = messages_from_dict(json.loads(recent_path.read_text(encoding="utf-8")))
    return response, saved


def _browser_chat(loaded_items: list) -> list[dict]:
    """Mirror memory_browser.js: role/text plus source_index and theater flag."""
    chat = []
    for index, item in enumerate(loaded_items):
        metadata = item["data"].get("metadata") or {}
        chat.append({
            "role": item["type"],
            "text": item["data"]["content"],
            "source_index": index,
            "theater": metadata.get("source") == "theater_numeric_v2",
        })
    return chat


def _assert_capsule_intact(saved: list) -> None:
    capsules = [message for message in saved if is_theater_episode_summary(message)]
    assert len(capsules) == 1
    assert capsules[0].metadata == _CAPSULE_METADATA
    assert capsules[0].content == "共同守住了雨夜里的住处。"
    assert [
        message for message in saved
        if message.content == "共同守住了雨夜里的住处。"
    ] == capsules


@pytest.mark.unit
@pytest.mark.asyncio
async def test_browser_save_keeps_theater_capsule_while_applying_ordinary_edits(
    tmp_path, monkeypatch,
):
    def _build(loaded_items):
        chat = _browser_chat(loaded_items)
        chat[1]["text"] = "h1 edited"
        del chat[4]  # 用户删除最后一条普通消息
        return chat

    response, saved = await _save_through_route(tmp_path, monkeypatch, _build)

    assert response["success"] is True
    _assert_capsule_intact(saved)
    assert [message.content for message in saved] == [
        "先前对话的备忘录: 旧摘要", "h1 edited", "共同守住了雨夜里的住处。", "a1",
    ]
    assert not saved[1].metadata and not saved[3].metadata

    # /theater/forget must still find the capsule after a browser save.
    from memory.recent import CompressedRecentHistoryManager

    mgr = object.__new__(CompressedRecentHistoryManager)
    mgr.user_histories = {}
    mgr.compress_threshold = 20
    recent_path = tmp_path / "Role" / "recent.json"
    removed = mgr._forget_theater_story_locked(recent_path, "Role", "story_rain")
    assert removed == 1
    remaining = messages_from_dict(json.loads(recent_path.read_text(encoding="utf-8")))
    assert [message.content for message in remaining] == [
        "先前对话的备忘录: 旧摘要", "h1 edited", "a1",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_browser_save_from_role_text_client_keeps_capsule_metadata(
    tmp_path, monkeypatch,
):
    def _build(loaded_items):
        chat = [
            {"role": item["type"], "text": item["data"]["content"]}
            for item in loaded_items
        ]
        chat[3]["text"] = "a1 edited"
        return chat

    response, saved = await _save_through_route(tmp_path, monkeypatch, _build)

    assert response["success"] is True
    _assert_capsule_intact(saved)
    assert [message.content for message in saved] == [
        "先前对话的备忘录: 旧摘要", "h1", "共同守住了雨夜里的住处。", "a1 edited", "h2",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_browser_save_restores_capsule_omitted_by_client(tmp_path, monkeypatch):
    def _build(loaded_items):
        return [
            item for item in _browser_chat(loaded_items) if not item["theater"]
        ]

    response, saved = await _save_through_route(tmp_path, monkeypatch, _build)

    assert response["success"] is True
    _assert_capsule_intact(saved)
    assert [message.content for message in saved] == [
        "先前对话的备忘录: 旧摘要", "h1", "共同守住了雨夜里的住处。", "a1", "h2",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_browser_save_rejects_capsule_edited_by_role_text_client(
    tmp_path, monkeypatch,
):
    def _build(loaded_items):
        chat = [
            {"role": item["type"], "text": item["data"]["content"]}
            for item in loaded_items
        ]
        chat[2]["text"] = "先前对话的备忘录: 被改写的剧场摘要"
        return chat

    response, saved = await _save_through_route(tmp_path, monkeypatch, _build)

    assert response.status_code == 409
    assert json.loads(response.body)["code"] == "RECENT_FILE_THEATER_READONLY"
    assert messages_to_dict(saved) == messages_to_dict(_original_history())


@pytest.mark.unit
def test_browser_item_flagged_theater_is_never_written_as_ordinary():
    from main_routers import memory_router

    current = json.dumps(messages_to_dict(_original_history()))
    chat = [
        {"role": "system", "text": "先前对话的备忘录: 旧摘要", "source_index": 0},
        {"role": "human", "text": "h1", "source_index": 1},
        # 下标失效（例如指向越界位置）时也不能把剧场条目当普通文本写回。
        {"role": "system", "text": "被改写的剧场摘要", "source_index": 99, "theater": True},
        {"role": "ai", "text": "a1", "source_index": 3},
    ]
    payload = [
        {"type": item["role"], "data": {"content": item["text"]}} for item in chat
    ]

    merged = messages_from_dict(
        memory_router._merge_browser_payload_with_theater(current, payload, chat)
    )

    _assert_capsule_intact(merged)
    assert [message.content for message in merged] == [
        "先前对话的备忘录: 旧摘要", "h1", "共同守住了雨夜里的住处。", "a1",
    ]


@pytest.mark.unit
def test_browser_save_without_theater_keeps_payload_unchanged():
    from main_routers import memory_router

    current = json.dumps(messages_to_dict([
        HumanMessage(content="h1"), AIMessage(content="a1"),
    ]))
    payload = [{"type": "human", "data": {"content": "edited"}}]

    assert memory_router._merge_browser_payload_with_theater(
        current, payload, [{"role": "human", "text": "edited"}],
    ) is payload


@pytest.mark.unit
def test_retract_theater_episode_drops_only_the_declined_archive_capsule(tmp_path):
    """Retract removes the capsule of one archive range and keeps everything else."""
    from memory.recent import CompressedRecentHistoryManager

    declined = dict(_CAPSULE_METADATA, archive_through_revision=7)
    other_session = dict(_CAPSULE_METADATA, session_id="session_rain_2", archive_through_revision=7)
    recent_path = tmp_path / "Role" / "recent.json"
    recent_path.parent.mkdir(parents=True)
    recent_file.write_recent_payload(recent_path, messages_to_dict([
        HumanMessage(content="h1"),
        SystemMessage(content="被拒绝的摘要", metadata=declined),
        SystemMessage(content="另一周目摘要", metadata=other_session),
    ]))
    mgr = object.__new__(CompressedRecentHistoryManager)
    mgr.user_histories = {}
    mgr.compress_threshold = 20

    # A different through-revision is a different archive: nothing to take back.
    assert mgr._retract_theater_episode_locked(recent_path, "Role", "story_rain", "session_rain_1", 3) == 0
    assert mgr._retract_theater_episode_locked(recent_path, "Role", "story_rain", "session_rain_1", 7) == 1
    # Idempotent retry.
    assert mgr._retract_theater_episode_locked(recent_path, "Role", "story_rain", "session_rain_1", 7) == 0

    remaining = messages_from_dict(json.loads(recent_path.read_text(encoding="utf-8")))
    assert [message.content for message in remaining] == ["h1", "另一周目摘要"]
