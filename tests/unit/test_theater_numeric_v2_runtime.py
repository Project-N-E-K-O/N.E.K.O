"""验证 Numeric v2 最少回合、确定性路线与原子持久化。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import gc
import json
import os

import pytest

from services.theater import numeric_v2_archive, numeric_v2_maintenance, numeric_v2_store
from services.theater.numeric_v2_maintenance import (
    QUARANTINE_FILE_LIMIT,
    audit_numeric_v2_storage,
)
from services.theater.numeric_v2_registry import NumericV2PackageRegistry
from services.theater.numeric_v2_store import update_numeric_v2_character_bindings
from services.theater.numeric_v2_runtime import (
    MetricChangeV2,
    NumericV2Engine,
    NumericV2Runtime,
    NumericV2RuntimeError,
    TurnRequestV2,
)
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from utils.config_manager import ensure_catgirl_character_id, get_reserved


def test_numeric_v2_turn_request_validates_ephemeral_input_source():
    """输入来源只接受自由输入和当前推荐两种 UI 事实。"""  # noqa: DOCSTRING_CJK

    legacy = TurnRequestV2.from_mapping({
        "client_turn_id": "legacy_input_source",
        "base_revision": 0,
        "message": "继续。",
    })
    suggested = TurnRequestV2.from_mapping({
        "client_turn_id": "suggested_input_source",
        "base_revision": 0,
        "message": "（点头）就这么做。",
        "input_source": "suggestion",
    })

    assert legacy.input_source == "freeform"
    assert suggested.input_source == "suggestion"
    with pytest.raises(NumericV2RuntimeError, match="numeric_turn_request_invalid"):
        TurnRequestV2.from_mapping({
            "client_turn_id": "invalid_input_source",
            "base_revision": 0,
            "message": "继续。",
            "input_source": "unknown",
        })


def test_numeric_v2_idle_store_and_receipt_locks_are_reclaimed(tmp_path):
    """锁在并发窗口内必须复用，调用方释放后不得按历史 ID 永久积累。"""  # noqa: DOCSTRING_CJK

    session_path = tmp_path / "numeric_v2" / "sessions" / "lock-test.json"
    session_key = str(session_path.resolve())
    session_lock = numeric_v2_store._lock(session_path)
    assert numeric_v2_store._lock(session_path) is session_lock

    receipt_path = tmp_path / "numeric_v2" / "end_receipts" / "lock-test.json"
    receipt_key = str(receipt_path.resolve())
    receipt_lock = numeric_v2_archive._receipt_lock(receipt_path)
    assert numeric_v2_archive._receipt_lock(receipt_path) is receipt_lock

    # 删除测试持有的最后强引用后，弱引用表应自动清除两个空闲条目。
    del session_lock
    del receipt_lock
    gc.collect()
    assert session_key not in numeric_v2_store._LOCKS
    assert receipt_key not in numeric_v2_archive._RECEIPT_LOCKS


def test_numeric_v2_session_budget_profile_persists_and_legacy_defaults_balanced():
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(
        session_id="budget_profile",
        catgirl_binding={"catgirl_id": "catgirl:test", "catgirl_name": "测试猫娘"},
        opening_performance=_opening(),
        actor_budget_profile="economy",
    )

    assert session.actor_budget_profile == "economy"
    assert type(session).from_mapping(session.to_dict()).actor_budget_profile == "economy"
    legacy = session.to_dict()
    legacy.pop("actor_budget_profile")
    assert type(session).from_mapping(legacy).actor_budget_profile == "balanced"


def test_numeric_v2_session_rejects_unknown_budget_profile():
    engine = NumericV2Engine.from_mapping(numeric_v2_story())

    with pytest.raises(
        NumericV2RuntimeError,
        match="numeric_actor_budget_profile_invalid",
    ):
        engine.create_session(
            session_id="invalid_budget_profile",
            catgirl_binding={"catgirl_id": "catgirl:test", "catgirl_name": "测试猫娘"},
            opening_performance=_opening(),
            actor_budget_profile="unlimited",
        )


def test_numeric_v2_runtime_has_no_hard_goal_delivery_directive():
    """Runtime 只记录自然发生的证据，不再生成强制交付指令。"""  # noqa: DOCSTRING_CJK

    engine = NumericV2Engine.from_mapping(numeric_v2_story())

    assert not hasattr(engine, "build_delivery_directive")


def test_existing_character_id_is_persisted_in_canonical_form():
    """已存在的合法 UUID 也要回写统一格式，避免角色绑定出现多种表示。"""  # noqa: DOCSTRING_CJK
    card = {
        "_reserved": {
            "character_id": "character_12345678-1234-5678-9ABC-DEF012345678",
        },
    }

    character_id, changed = ensure_catgirl_character_id(card)

    assert changed is True
    assert character_id == "character_12345678123456789abcdef012345678"
    assert get_reserved(card, "character_id") == character_id


def test_numeric_v2_receipt_path_rejects_parent_directory_escape(tmp_path):
    """结束回执只接受服务端固定格式，不能借路径片段逃逸归档目录。"""  # noqa: DOCSTRING_CJK
    store = numeric_v2_archive.NumericV2ArchiveStore(tmp_path)

    with pytest.raises(
        numeric_v2_archive.NumericV2ArchiveError,
        match="numeric_end_receipt_invalid",
    ):
        store._receipt_path("theater_end_../../outside")


@pytest.mark.parametrize(
    ("read_failure", "expected_error"),
    [
        ("permission", "numeric_end_receipt_read_failed"),
        ("invalid_json", "numeric_end_receipt_read_failed"),
        ("invalid_payload", "numeric_public_archive_invalid"),
        ("invalid_schema", "numeric_public_archive_invalid"),
        ("missing_story_id", "numeric_public_archive_invalid"),
        ("missing_session_id", "numeric_public_archive_invalid"),
        ("missing_character_id", "numeric_public_archive_invalid"),
        ("invalid_story_id_type", "numeric_public_archive_invalid"),
        ("invalid_session_id_type", "numeric_public_archive_invalid"),
        ("invalid_character_id_type", "numeric_public_archive_invalid"),
    ],
)
def test_numeric_v2_public_archive_delete_aborts_on_read_failure(
    tmp_path,
    monkeypatch,
    read_failure,
    expected_error,
):
    """破坏性删除遇到不可读或损坏档案时必须中止，不能静默遗漏。"""  # noqa: DOCSTRING_CJK

    store = numeric_v2_archive.NumericV2ArchiveStore(tmp_path)
    archive_path = store.public_archive_root / "transient.json"
    store._write(archive_path, {
        "schema": "neko.theater.numeric.v2.public-archive",
        "story_id": "story_transient_delete",
        "session_id": "session_transient_delete",
        "character_id": "character_transient_delete",
        "catgirl_name": "小葵",
    })
    path_type = type(archive_path)
    original_read_text = path_type.read_text

    def transient_read(path, *args, **kwargs):
        if path == archive_path:
            if read_failure == "invalid_json":
                return "{"
            if read_failure == "invalid_payload":
                return "[]"
            if read_failure == "invalid_schema":
                return json.dumps({"schema": "unknown"})
            if read_failure.startswith("missing_"):
                invalid_archive = {
                    "schema": "neko.theater.numeric.v2.public-archive",
                    "story_id": "story_transient_delete",
                    "session_id": "session_transient_delete",
                    "character_id": "character_transient_delete",
                }
                invalid_archive.pop(read_failure.removeprefix("missing_"))
                return json.dumps(invalid_archive)
            if read_failure.startswith("invalid_") and read_failure.endswith("_type"):
                invalid_archive = {
                    "schema": "neko.theater.numeric.v2.public-archive",
                    "story_id": "story_transient_delete",
                    "session_id": "session_transient_delete",
                    "character_id": "character_transient_delete",
                }
                invalid_field = read_failure.removeprefix("invalid_").removesuffix("_type")
                invalid_archive[invalid_field] = []
                return json.dumps(invalid_archive)
            raise PermissionError("temporary archive failure")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(path_type, "read_text", transient_read)

    with pytest.raises(
        numeric_v2_archive.NumericV2ArchiveError,
        match=expected_error,
    ):
        store.delete_public_archives(
            story_id="story_transient_delete",
            character_id="character_transient_delete",
        )

    assert archive_path.is_file()


def test_numeric_v2_legacy_archives_and_receipts_follow_character_rename(tmp_path):
    """旧版空 character_id 的冷档案、回执和待提交档案必须随角色改名迁移。"""  # noqa: DOCSTRING_CJK

    store = numeric_v2_archive.NumericV2ArchiveStore(tmp_path)
    receipt_id = "theater_end_" + "a" * 40
    legacy_identity = {"character_id": "", "catgirl_name": "旧角色"}
    public_archive = {
        "schema": "neko.theater.numeric.v2.public-archive",
        "story_id": "rename_story",
        "session_id": "rename_session",
        **legacy_identity,
    }
    receipt = {
        "schema": "neko.theater.numeric.v2.end-receipt",
        "receipt_id": receipt_id,
        "story_id": "rename_story",
        "session_id": "rename_session",
        **legacy_identity,
    }
    staged_archive = dict(public_archive)
    store._write(store._public_archive_path("rename_session"), public_archive)
    store._write(store._receipt_path(receipt_id), receipt)
    store._write(store._staged_archive_path(receipt_id), staged_archive)

    result = store.update_character_binding(
        character_id="character_" + "1" * 32,
        legacy_catgirl_name="旧角色",
        catgirl_name="新角色",
    )

    expected_identity = {
        "character_id": "character_" + "1" * 32,
        "catgirl_name": "新角色",
    }
    assert result == {"archives": 1, "receipts": 1, "staged_archives": 1}
    assert {
        key: store._read(store._public_archive_path("rename_session"))[key]
        for key in expected_identity
    } == expected_identity
    assert {
        key: store._read(store._receipt_path(receipt_id))[key]
        for key in expected_identity
    } == expected_identity
    assert {
        key: store._read(store._staged_archive_path(receipt_id))[key]
        for key in expected_identity
    } == expected_identity


@pytest.mark.asyncio
async def test_numeric_v2_empty_character_id_delete_keeps_other_legacy_names(tmp_path):
    """旧角色卡按名称删除时不能把空 character_id 扩散成全角色删除。"""  # noqa: DOCSTRING_CJK
    session_root = tmp_path / "numeric_v2" / "sessions"
    archive_root = tmp_path / "numeric_v2" / "public_archives"
    session_root.mkdir(parents=True)
    archive_root.mkdir(parents=True)
    for session_id, catgirl_name in (("legacy-a", "小葵"), ("legacy-b", "雪奈")):
        payload = {
            "schema": numeric_v2_store.STORE_SCHEMA,
            "session": {
                "session_id": session_id,
                "story_package_id": "legacy-story",
                "status": "ended",
                "catgirl_binding": {
                    "catgirl_name": catgirl_name,
                    "character_id": "",
                },
            },
        }
        (session_root / f"{session_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        archive_payload = {
            "schema": "neko.theater.numeric.v2.public-archive",
            "session_id": session_id,
            "story_id": "legacy-story",
            "catgirl_name": catgirl_name,
            "character_id": "",
        }
        (archive_root / f"{session_id}.json").write_text(
            json.dumps(archive_payload, ensure_ascii=False),
            encoding="utf-8",
        )

    deleted = await numeric_v2_store.delete_numeric_v2_sessions(
        tmp_path,
        character_id="",
        legacy_catgirl_name="小葵",
    )

    assert [item["session_id"] for item in deleted] == ["legacy-a"]
    assert not (session_root / "legacy-a.json").exists()
    assert (session_root / "legacy-b.json").is_file()
    assert not (archive_root / "legacy-a.json").exists()
    assert (archive_root / "legacy-b.json").is_file()


@pytest.mark.asyncio
async def test_numeric_v2_scoped_delete_preserves_other_story_slots(tmp_path):
    """同时按剧本和角色删除时，只能移除交集槽位。"""  # noqa: DOCSTRING_CJK
    index_path = tmp_path / "numeric_v2" / "story_sessions.json"
    numeric_v2_store._write_story_session_slots(index_path, {
        "story-a": {"character-a": "session-aa", "character-b": "session-ab"},
        "story-b": {"character-a": "session-ba"},
    })

    await numeric_v2_store.delete_numeric_v2_sessions(
        tmp_path,
        story_id="story-a",
        character_id="character-a",
    )

    assert numeric_v2_store._read_story_session_slots(index_path) == {
        "story-a": {"character-b": "session-ab"},
        "story-b": {"character-a": "session-ba"},
    }


def _binding() -> dict[str, str]:
    return {
        "character_id": "character_11111111111111111111111111111111",
        "catgirl_id": "catgirl:character_11111111111111111111111111111111",
        "catgirl_name": "Lan",
        "player_address": "哥哥",
        "profile_revision": "characters:test",
        "profile_hash": "sha256:test",
    }


def _opening() -> dict:
    return {
        "narration": "花店风铃轻响。",
        "dialogue": [{"speaker_id": "active_catgirl", "text": "你回来了。"}],
        "suggested_inputs": ["问她近况"],
    }


def _performance(text: str) -> dict:
    return {
        "narration": "她认真听完。",
        "dialogue": [{"speaker_id": "active_catgirl", "text": text}],
        "suggested_inputs": [],
    }


def _transition_performance(target_node_id: str) -> dict:
    return {
        "suggested_inputs": [],
        "segments": [
            {
                "phase": "source_response",
                "content": [
                    {"type": "narration", "text": "她回应后收住话题。"},
                    {"type": "dialogue", "speaker_id": "active_catgirl", "text": "明天再说。"},
                ],
            },
            {
                "phase": "transition_bridge",
                "content": [{"type": "narration", "text": "夜色过去。"}],
            },
            {
                "phase": "target_opening",
                "content": [{"type": "narration", "text": "第二天清晨，花店重新开门。"}],
            },
        ],
        "transition_delivered": True,
        "visible_node_id": target_node_id,
    }


def _branch_story() -> dict:
    story = numeric_v2_story()
    story["nodes"][0]["route_gates"][0]["conditions"]["all"][0]["value"] = 25
    story["nodes"][0]["route_gates"][1]["conditions"]["all"][0]["value"] = 25
    return story


@pytest.mark.parametrize(
    ("existing_offer", "new_offer", "expected_offer"),
    [
        (False, True, True),
        (True, False, True),
        (False, False, False),
    ],
)
def test_numeric_v2_runtime_is_the_only_transition_offer_state_writer(
    existing_offer,
    new_offer,
    expected_offer,
):
    """Actor 只提交已验证信号，三份公开状态由 Runtime 一次性同步。"""  # noqa: DOCSTRING_CJK

    engine = NumericV2Engine.from_mapping(_branch_story())
    session = engine.create_session(
        session_id=f"offer_state_{existing_offer}_{new_offer}",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    session = replace(session, transition_offered=existing_offer)
    outcome = engine.resolve_turn(
        session,
        TurnRequestV2("offer_state_turn", 0, "我们再确认一下。"),
        (),
        transition_intent="unclear",
    )

    finalized, performance = engine.finalize_transition_offer_state(
        outcome,
        {"performance": "（点头）好。", "transition_offered": False},
        new_offer=new_offer,
    )

    assert finalized.session.transition_offered is expected_offer
    assert finalized.ledger_event["transition_offered"] is expected_offer
    assert performance["transition_offered"] is expected_offer


@pytest.mark.asyncio
async def test_numeric_v2_player_address_is_committed_only_with_successful_turn(tmp_path):
    runtime = NumericV2Runtime(
        NumericV2Engine.from_mapping(numeric_v2_story(player_address_known=False)),
        tmp_path,
    )
    stored = await runtime.start_session(
        session_id="runtime_player_address_state",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    assert stored.session.player_address_known is False
    assert stored.session.to_dict()["player_address_known"] is False

    mentioned = runtime.prepare_turn(
        stored,
        TurnRequestV2("address_mentioned", 0, "你认识哥哥吗？"),
        (),
        scene_complete=False,
    )
    assert mentioned.session.player_address_known is False
    assert mentioned.ledger_event["player_address_known"] is False

    disclosed = runtime.prepare_turn(
        stored,
        TurnRequestV2("address_disclosure", 0, "我叫哥哥。"),
        (),
        scene_complete=False,
    )
    assert disclosed.session.player_address_known is True
    assert disclosed.ledger_event["player_address_known"] is True

    with pytest.raises(ValueError, match="numeric_performance_invalid"):
        await runtime.commit_turn(disclosed, {"performance": "（只有动作没有对白）"})

    unchanged = await runtime.restore_session(stored.session.session_id)
    assert unchanged is not None
    assert unchanged.session.player_address_known is False
    assert unchanged.session.revision == 0

    committed = await runtime.commit_turn(
        disclosed,
        {"performance": "我听见了。", "suggested_inputs": []},
    )
    assert committed.session.player_address_known is True
    assert committed.ledger_events[-1]["player_address_known"] is True

    restored = await runtime.restore_session(stored.session.session_id)
    assert restored is not None
    assert restored.session.player_address_known is True


@pytest.mark.asyncio
async def test_numeric_v2_surface_you_fallback_does_not_count_as_disclosed_name(tmp_path):
    runtime = NumericV2Runtime(
        NumericV2Engine.from_mapping(numeric_v2_story(player_address_known=False)),
        tmp_path,
    )
    binding = _binding()
    binding["player_address"] = "你"
    stored = await runtime.start_session(
        session_id="runtime_surface_you_fallback",
        catgirl_binding=binding,
        opening_performance=_opening(),
    )

    prepared = runtime.prepare_turn(
        stored,
        TurnRequestV2("surface_you_fallback", 0, "你好，你先说。"),
        (),
        scene_complete=False,
    )

    assert prepared.session.player_address_known is False


@pytest.mark.asyncio
async def test_numeric_v2_route_change_requires_visible_transition_before_commit(tmp_path):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_transition_guard",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    first = runtime.prepare_turn(
        stored,
        TurnRequestV2("transition_turn_1", 0, "我先听你说。"),
        (),
        scene_complete=False,
    )
    # 只有已提交正文中出现具体提议，下一轮接受时才允许换幕。
    offered = replace(
        first,
        session=replace(first.session, transition_offered=True),
        ledger_event={**first.ledger_event, "transition_offered": True},
    )
    stored = await runtime.commit_turn(
        offered,
        {**_performance("那就先坐一会儿。"), "transition_offered": True},
    )
    second = runtime.prepare_turn(
        stored,
        TurnRequestV2("transition_turn_2", 1, "我答应把话说完。"),
        (),
        scene_complete=True,
        transition_intent="accept",
    )

    with pytest.raises(ValueError, match="numeric_transition_performance_invalid"):
        await runtime.commit_turn(second, _performance("旧场景继续。"))

    committed = await runtime.commit_turn(
        second,
        _transition_performance(second.session.current_node_id),
    )
    restored = await runtime.restore_session(committed.session.session_id)

    assert restored is not None
    assert restored.session.current_node_id == second.session.current_node_id
    assert restored.session.performance_history[-1]["visible_node_id"] == second.session.current_node_id


@pytest.mark.asyncio
async def test_numeric_v2_route_change_accepts_empty_deduplicated_bridge(tmp_path):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_empty_transition_bridge",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    first = runtime.prepare_turn(
        stored,
        TurnRequestV2("empty_bridge_1", 0, "我先听你说。"),
        (),
        scene_complete=False,
    )
    # 先把转场提议写入上一轮的可见结果，模拟 Actor 已经公开提出下一步。
    offered = replace(
        first,
        session=replace(first.session, transition_offered=True),
        ledger_event={**first.ledger_event, "transition_offered": True},
    )
    stored = await runtime.commit_turn(
        offered,
        {**_performance("那就先坐一会儿。"), "transition_offered": True},
    )
    second = runtime.prepare_turn(
        stored,
        TurnRequestV2("empty_bridge_2", 1, "我答应把话说完。"),
        (),
        scene_complete=True,
        transition_intent="accept",
    )
    finalized = runtime.engine.finalize_transition_performance(
        second,
        {
            "suggested_inputs": [],
            "segments": [
                {
                    "phase": "source_response",
                    "performance": "（收好旧信）那就明天再说。",
                },
                {
                    "phase": "transition_bridge",
                    "scene_narration": "",
                },
                {
                    "phase": "target_opening",
                    "performance": "（推开店门）早上好。",
                },
            ],
        },
        target_opening="第二天清晨，花店重新开门。",
    )

    committed = await runtime.commit_turn(second, finalized)
    restored = await runtime.restore_session(committed.session.session_id)

    assert restored is not None
    transition = restored.session.performance_history[-1]
    assert transition["segments"][1]["scene_narration"] == ""
    assert transition["segments"][2]["scene_narration"] == "第二天清晨，花店重新开门。"


@pytest.mark.asyncio
async def test_numeric_v2_session_creation_falls_back_without_hardlinks(tmp_path, monkeypatch):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)

    def reject_hardlink(*_args, **_kwargs):
        raise OSError("hard links unsupported")

    monkeypatch.setattr(numeric_v2_store.os, "link", reject_hardlink)

    stored = await runtime.start_session(
        session_id="runtime_no_hardlink",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )

    assert stored.session.session_id == "runtime_no_hardlink"
    assert (tmp_path / "numeric_v2" / "sessions" / "runtime_no_hardlink.json").is_file()


@pytest.mark.asyncio
async def test_numeric_v2_session_creation_rolls_back_when_index_write_fails(
    tmp_path,
    monkeypatch,
):
    """恢复索引发布失败时不能遗留不可达的 Session 文件。"""  # noqa: DOCSTRING_CJK
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)

    def _reject_index(_stories):
        raise OSError("index write failed")

    monkeypatch.setattr(runtime.store, "_write_story_session_index", _reject_index)

    with pytest.raises(OSError, match="index write failed"):
        await runtime.start_session(
            session_id="runtime_index_failure",
            catgirl_binding=_binding(),
            opening_performance=_opening(),
        )

    assert not runtime.store._path("runtime_index_failure").exists()


@pytest.mark.asyncio
async def test_numeric_v2_story_session_index_survives_runtime_recreation(tmp_path):
    story = _branch_story()
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_story_resume",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )

    restarted_runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    restored = await restarted_runtime.restore_story_session(_binding())

    assert restored is not None
    assert restored.session.session_id == stored.session.session_id
    assert restored.session.revision == 0
    index = (tmp_path / "numeric_v2" / "story_sessions.json").read_text(encoding="utf-8")
    assert "runtime_story_resume" in index
    assert '"character_11111111111111111111111111111111":"runtime_story_resume"' in index


@pytest.mark.asyncio
async def test_numeric_v2_session_writer_rejects_unreadable_story_index(tmp_path):
    """损坏索引不能被新 Session 当作空索引覆盖。"""  # noqa: DOCSTRING_CJK

    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    index_path = tmp_path / "numeric_v2" / "story_sessions.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text("{broken-json", encoding="utf-8")

    with pytest.raises(
        numeric_v2_store.NumericV2StoreError,
        match="numeric_story_session_index_read_failed",
    ):
        await runtime.start_session(
            session_id="runtime_unreadable_index",
            catgirl_binding=_binding(),
            opening_performance=_opening(),
        )

    assert index_path.read_text(encoding="utf-8") == "{broken-json"
    assert not runtime.store._path("runtime_unreadable_index").exists()


@pytest.mark.asyncio
async def test_numeric_v2_session_delete_rejects_unreadable_story_index(tmp_path):
    """损坏索引时删除请求不能先移除 Session 文件。"""  # noqa: DOCSTRING_CJK

    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_delete_unreadable_index",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    index_path = tmp_path / "numeric_v2" / "story_sessions.json"
    index_path.write_text("{broken-json", encoding="utf-8")

    with pytest.raises(
        numeric_v2_store.NumericV2StoreError,
        match="numeric_story_session_index_read_failed",
    ):
        await numeric_v2_store.delete_numeric_v2_sessions(
            tmp_path,
            story_id=stored.session.story_package_id,
        )

    assert runtime.store._path(stored.session.session_id).is_file()
    assert index_path.read_text(encoding="utf-8") == "{broken-json"


@pytest.mark.asyncio
async def test_numeric_v2_session_delete_rejects_transient_session_read_failure(
    tmp_path,
    monkeypatch,
):
    """破坏性枚举遇到暂时性 I/O 失败时必须保留 Session 和索引。"""  # noqa: DOCSTRING_CJK

    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_delete_unreadable_session",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    session_path = runtime.store._path(stored.session.session_id)
    index_path = tmp_path / "numeric_v2" / "story_sessions.json"
    path_type = type(session_path)
    original_read_text = path_type.read_text

    def transient_read(path, *args, **kwargs):
        if path == session_path:
            raise PermissionError("temporary session failure")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(path_type, "read_text", transient_read)

    with pytest.raises(
        numeric_v2_store.NumericV2StoreError,
        match="numeric_session_read_failed",
    ):
        await numeric_v2_store.delete_numeric_v2_sessions(
            tmp_path,
            story_id=stored.session.story_package_id,
        )

    assert session_path.is_file()
    assert index_path.is_file()


@pytest.mark.asyncio
async def test_numeric_v2_session_delete_rejects_transient_archive_read_failure(
    tmp_path,
    monkeypatch,
):
    """冷档案暂时不可读时必须在删除任何 Session 前中止级联操作。"""  # noqa: DOCSTRING_CJK

    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_delete_unreadable_archive",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    session_path = runtime.store._path(stored.session.session_id)
    index_path = tmp_path / "numeric_v2" / "story_sessions.json"
    archive_path = (
        tmp_path
        / "numeric_v2"
        / "public_archives"
        / f"{stored.session.session_id}.json"
    )
    archive_path.parent.mkdir(parents=True)
    archive_path.write_text(
        json.dumps(
            {
                "schema": "neko.theater.numeric.v2.public-archive",
                "story_id": stored.session.story_package_id,
                "session_id": stored.session.session_id,
                "character_id": _binding()["character_id"],
                "catgirl_name": _binding()["catgirl_name"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    path_type = type(archive_path)
    original_read_text = path_type.read_text

    def transient_read(path, *args, **kwargs):
        if path == archive_path:
            raise PermissionError("temporary archive failure")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(path_type, "read_text", transient_read)

    with pytest.raises(
        numeric_v2_store.NumericV2StoreError,
        match="numeric_public_archive_read_failed",
    ):
        await numeric_v2_store.delete_numeric_v2_sessions(
            tmp_path,
            story_id=stored.session.story_package_id,
        )

    assert session_path.is_file()
    assert archive_path.is_file()
    assert index_path.is_file()


@pytest.mark.asyncio
async def test_numeric_v2_story_restore_ignores_sessions_from_other_stories(tmp_path):
    story = _branch_story()
    other_story = deepcopy(story)
    other_story["meta"]["story_id"] = "numeric_other_story"
    registry = NumericV2PackageRegistry(tmp_path / "numeric_v2" / "packages")
    registry.import_package(story)
    registry.import_package(other_story)
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    other_runtime = NumericV2Runtime(NumericV2Engine.from_mapping(other_story), tmp_path)

    current = await runtime.start_session(
        session_id="runtime_current_story",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    await other_runtime.start_session(
        session_id="runtime_other_story",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )

    index_path = tmp_path / "numeric_v2" / "story_sessions.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["stories"].pop(story["meta"]["story_id"])
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    audit_numeric_v2_storage(
        tmp_path,
        registry,
        character_ids_by_name={"Lan": _binding()["character_id"]},
    )

    restored = await runtime.restore_story_session(_binding())

    assert restored is not None
    assert restored.session.session_id == current.session.session_id


@pytest.mark.asyncio
async def test_numeric_v2_commit_rejects_session_ended_during_model_wait(tmp_path):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_ended_during_turn",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    outcome = runtime.prepare_turn(
        stored,
        TurnRequestV2("turn_after_end", 0, "这轮不应覆盖结束状态。"),
        (),
        scene_complete=False,
    )
    await runtime.end_session(
        stored.session.session_id,
        base_revision=0,
        base_lifecycle_revision=0,
        reason="user_exit",
    )

    with pytest.raises(numeric_v2_store.NumericV2StoreRevisionConflictError, match="session_already_ended"):
        await runtime.commit_turn(outcome, _performance("不应提交。"))

    restored = await runtime.restore_session(stored.session.session_id)
    assert restored is not None
    assert restored.session.status == "ended"
    assert restored.session.revision == 0


@pytest.mark.asyncio
async def test_turn_prepared_before_exit_cannot_commit_after_resume(tmp_path):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    current = await runtime.start_session(session_id="lifecycle_turn", catgirl_binding=_binding(), opening_performance=_opening())
    outcome = runtime.prepare_turn(current, TurnRequestV2("stale", 0, "old input"), ())
    await runtime.end_session(current.session.session_id, base_revision=0, base_lifecycle_revision=0, reason="user_exit")
    resumed = await runtime.resume_session(current.session.session_id, base_revision=0, base_lifecycle_revision=1)
    with pytest.raises(numeric_v2_store.NumericV2StoreRevisionConflictError, match="numeric_base_lifecycle_revision_mismatch"):
        await runtime.commit_turn(outcome, _performance("stale response"))
    assert await runtime.restore_session(current.session.session_id) == resumed
    fresh = runtime.prepare_turn(resumed, TurnRequestV2("fresh", 0, "new input"), ())
    committed = await runtime.commit_turn(fresh, _performance("fresh response"))
    assert committed.session.lifecycle_revision == 2
    assert await runtime.restore_session(current.session.session_id) == committed


@pytest.mark.asyncio
async def test_numeric_v2_lifecycle_revision_rejects_delayed_end_and_resume(tmp_path):
    """同一演绎回合内，旧的结束或继续请求不能覆盖更新的生命周期状态。"""  # noqa: DOCSTRING_CJK

    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    started = await runtime.start_session(
        session_id="runtime_lifecycle_fence",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    ended = await runtime.end_session(
        started.session.session_id,
        base_revision=0,
        base_lifecycle_revision=0,
        reason="user_exit",
    )
    resumed = await runtime.resume_session(
        started.session.session_id,
        base_revision=0,
        base_lifecycle_revision=1,
    )
    ended_again = await runtime.end_session(
        started.session.session_id,
        base_revision=0,
        base_lifecycle_revision=2,
        reason="user_exit",
    )

    with pytest.raises(numeric_v2_store.NumericV2StoreRevisionConflictError):
        await runtime.resume_session(
            started.session.session_id,
            base_revision=0,
            base_lifecycle_revision=1,
        )

    resumed_again = await runtime.resume_session(
        started.session.session_id,
        base_revision=0,
        base_lifecycle_revision=3,
    )
    with pytest.raises(numeric_v2_store.NumericV2StoreRevisionConflictError):
        await runtime.end_session(
            started.session.session_id,
            base_revision=0,
            base_lifecycle_revision=2,
            reason="user_exit",
        )

    assert ended.session.lifecycle_revision == 1
    assert resumed.session.lifecycle_revision == 2
    assert ended_again.session.lifecycle_revision == 3
    assert resumed_again.session.lifecycle_revision == 4
    assert resumed_again.session.status == "active"
    assert resumed_again.session.revision == 0


@pytest.mark.asyncio
async def test_numeric_v2_story_restore_prunes_legacy_duplicate_sessions(tmp_path):
    story = _branch_story()
    registry = NumericV2PackageRegistry(tmp_path / "numeric_v2" / "packages")
    registry.import_package(story)
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    await runtime.start_session(
        session_id="runtime_story_old",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    newer = await runtime.start_session(
        session_id="runtime_story_new",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    (tmp_path / "numeric_v2" / "story_sessions.json").unlink()
    old_path = tmp_path / "numeric_v2" / "sessions" / "runtime_story_old.json"
    new_path = tmp_path / "numeric_v2" / "sessions" / "runtime_story_new.json"
    os.utime(old_path, ns=(1_000_000_000, 1_000_000_000))
    os.utime(new_path, ns=(2_000_000_000, 2_000_000_000))

    audit_numeric_v2_storage(
        tmp_path,
        registry,
        character_ids_by_name={"Lan": _binding()["character_id"]},
    )

    restored = await runtime.restore_story_session(_binding())

    assert restored is not None
    assert restored.session.session_id == newer.session.session_id
    session_files = list((tmp_path / "numeric_v2" / "sessions").glob("*.json"))
    assert [path.stem for path in session_files] == ["runtime_story_new"]


@pytest.mark.asyncio
async def test_numeric_v2_startup_audit_bounds_corrupt_quarantine(tmp_path):
    story = _branch_story()
    registry = NumericV2PackageRegistry(tmp_path / "numeric_v2" / "packages")
    registry.import_package(story)
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    valid = await runtime.start_session(
        session_id="runtime_valid_after_audit",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    session_root = tmp_path / "numeric_v2" / "sessions"
    for index in range(QUARANTINE_FILE_LIMIT + 3):
        (session_root / f"corrupt_{index}.json").write_text("{", encoding="utf-8")

    result = audit_numeric_v2_storage(
        tmp_path,
        registry,
        character_ids_by_name={"Lan": _binding()["character_id"]},
    )

    assert result["quarantined"] == QUARANTINE_FILE_LIMIT + 3
    assert len(list((tmp_path / "numeric_v2" / "quarantine").glob("*"))) == QUARANTINE_FILE_LIMIT
    assert [path.stem for path in session_root.glob("*.json")] == [
        valid.session.session_id
    ]
    restored = await runtime.restore_story_session(_binding())
    assert restored is not None
    assert restored.session.session_id == valid.session.session_id


@pytest.mark.asyncio
async def test_numeric_v2_startup_audit_does_not_quarantine_transient_io_failure(
    tmp_path,
    monkeypatch,
):
    """暂时性读取失败必须中止审计，不能把有效 Session 当作损坏数据移动。"""  # noqa: DOCSTRING_CJK

    story = _branch_story()
    registry = NumericV2PackageRegistry(tmp_path / "numeric_v2" / "packages")
    registry.import_package(story)
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_transient_audit_failure",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    session_path = runtime.store._path(stored.session.session_id)
    original_read_summary = numeric_v2_maintenance._read_numeric_v2_session_summary

    def transient_read(path, *args, **kwargs):
        if path == session_path:
            raise PermissionError("temporary storage failure")
        return original_read_summary(path, *args, **kwargs)

    monkeypatch.setattr(
        numeric_v2_maintenance,
        "_read_numeric_v2_session_summary",
        transient_read,
    )

    with pytest.raises(
        numeric_v2_store.NumericV2StoreError,
        match="numeric_session_audit_read_failed",
    ):
        audit_numeric_v2_storage(
            tmp_path,
            registry,
            character_ids_by_name={"Lan": _binding()["character_id"]},
        )

    assert session_path.is_file()
    assert not list((tmp_path / "numeric_v2" / "quarantine").glob("*"))


@pytest.mark.asyncio
async def test_numeric_v2_startup_audit_quarantines_missing_story_session(tmp_path):
    """剧本已确定删除的孤儿 Session 应被隔离，不能误判为暂时性 I/O 故障。"""  # noqa: DOCSTRING_CJK

    story = _branch_story()
    registry = NumericV2PackageRegistry(tmp_path / "numeric_v2" / "packages")
    registry.import_package(story)
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_missing_story_audit",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    session_path = runtime.store._path(stored.session.session_id)
    registry.delete_package(story["meta"]["story_id"])

    result = audit_numeric_v2_storage(
        tmp_path,
        registry,
        character_ids_by_name={"Lan": _binding()["character_id"]},
    )

    assert result == {"valid": 0, "quarantined": 1}
    assert not session_path.exists()
    assert len(list((tmp_path / "numeric_v2" / "quarantine").glob("*"))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["upgrade", "compile", "io", "unknown"])
async def test_audit_keeps_all_sessions_when_package_cannot_be_loaded(tmp_path, monkeypatch, failure):
    from services.theater.numeric_v2_registry import NumericV2PackageError
    story = _branch_story()
    registry = NumericV2PackageRegistry(tmp_path / "numeric_v2" / "packages")
    registry.import_package(story)
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    for i in range(8):
        await runtime.start_session(session_id=f"preserve_{i}", catgirl_binding=_binding(), opening_performance=_opening())
    index = tmp_path / "numeric_v2/story_sessions.json"
    before = {path: path.read_bytes() for path in runtime.store.root.glob("*.json")}
    original_index = index.read_bytes()
    calls = []
    def load(story_id):
        calls.append(story_id)
        if failure == "unknown":
            raise RuntimeError("unexpected loader failure")
        if failure == "io":
            raise NumericV2PackageError("read failed") from OSError("unreadable")
        raise NumericV2PackageError("numeric_v2_upgrade_required" if failure == "upgrade" else "compile failed")
    monkeypatch.setattr(registry, "load_engine", load)
    if failure in {"io", "unknown"}:
        with pytest.raises((numeric_v2_store.NumericV2StoreError, RuntimeError)):
            audit_numeric_v2_storage(tmp_path, registry)
    else:
        assert audit_numeric_v2_storage(tmp_path, registry) == {"valid": 0, "quarantined": 0}
    assert len(calls) == 1
    assert json.loads(index.read_bytes()) == json.loads(original_index)
    assert all(path.read_bytes() == contents for path, contents in before.items())
    assert not list((tmp_path / "numeric_v2/quarantine").glob("*"))


@pytest.mark.asyncio
async def test_numeric_v2_recovers_prepared_story_delete_after_interruption(tmp_path):
    story = _branch_story()
    registry = NumericV2PackageRegistry(tmp_path / "numeric_v2" / "packages")
    registry.import_package(story)
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_delete_interrupted",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    public_archive = tmp_path / "numeric_v2" / "public_archives" / "archive.json"
    public_archive.parent.mkdir(parents=True)
    public_archive.write_text(json.dumps({
        "schema": "neko.theater.numeric.v2.public-archive",
        "story_id": story["meta"]["story_id"],
        "session_id": stored.session.session_id,
        "character_id": _binding()["character_id"],
        "catgirl_name": _binding()["catgirl_name"],
    }), encoding="utf-8")
    archive_store = numeric_v2_archive.NumericV2ArchiveStore(tmp_path)
    receipt = archive_store.create_or_get(stored.session)
    numeric_v2_maintenance._prepare_delete_transaction(
        tmp_path,
        registry,
        story["meta"]["story_id"],
    )
    await numeric_v2_store.delete_numeric_v2_sessions(
        tmp_path,
        story_id=story["meta"]["story_id"],
    )
    archive_store.delete_receipts(story_id=story["meta"]["story_id"])
    registry.delete_package(story["meta"]["story_id"])

    numeric_v2_maintenance.recover_numeric_v2_delete_transactions(tmp_path)

    assert registry.package_path(story["meta"]["story_id"]).is_file()
    assert runtime.store._path(stored.session.session_id).is_file()
    assert public_archive.is_file()
    assert archive_store.load(receipt["receipt_id"]) is not None
    restored = await runtime.restore_story_session(_binding())
    assert restored is not None
    assert restored.session.session_id == stored.session.session_id


@pytest.mark.asyncio
async def test_numeric_v2_restart_replaces_ended_session_in_same_catgirl_slot(tmp_path):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    old = await runtime.start_session(
        session_id="runtime_story_ended",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    ended = await runtime.end_session(
        old.session.session_id,
        base_revision=0,
        base_lifecycle_revision=0,
        reason="user_exit",
    )
    # 重开必须显式指出被替换的旧 Session，测试与生产接口保持同一条原子替换链。
    restarted = await runtime.replace_active_session(
        previous_session_id=old.session.session_id,
        session_id="runtime_story_reopened",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )

    assert ended.session.status == "ended"
    assert restarted.session.session_id == "runtime_story_reopened"
    assert restarted.session.status == "active"
    assert not (tmp_path / "numeric_v2" / "sessions" / "runtime_story_ended.json").exists()
    restored = await runtime.restore_story_session(_binding())
    assert restored is not None
    assert restored.session.session_id == "runtime_story_reopened"


@pytest.mark.asyncio
async def test_numeric_v2_preserves_one_session_per_story_and_catgirl(tmp_path):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    lan = await runtime.start_session(
        session_id="runtime_lan",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    other_binding = {
        **_binding(),
        "character_id": "character_22222222222222222222222222222222",
        "catgirl_id": "catgirl:character_22222222222222222222222222222222",
        "catgirl_name": "Mio",
        "profile_revision": "characters:mio",
        "profile_hash": "sha256:mio",
    }
    mio = await runtime.start_session(
        session_id="runtime_mio",
        catgirl_binding=other_binding,
        opening_performance=_opening(),
    )

    assert (await runtime.restore_story_session(_binding())).session.session_id == lan.session.session_id
    assert (await runtime.restore_story_session(other_binding)).session.session_id == mio.session.session_id

    # 只替换当前猫娘的恢复槽位，另一只猫娘的 Session 必须保持不变。
    restarted_lan = await runtime.replace_active_session(
        previous_session_id=lan.session.session_id,
        session_id="runtime_lan_restarted",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )

    session_files = sorted(
        path.stem
        for path in (tmp_path / "numeric_v2" / "sessions").glob("*.json")
    )
    assert session_files == ["runtime_lan_restarted", "runtime_mio"]
    assert (await runtime.restore_story_session(_binding())).session == restarted_lan.session
    assert (await runtime.restore_story_session(other_binding)).session == mio.session


@pytest.mark.asyncio
async def test_numeric_v2_indexed_restore_does_not_scan_unrelated_session_files(tmp_path):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_indexed_only",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    corrupt_path = tmp_path / "numeric_v2" / "sessions" / "unrelated_corrupt.json"
    corrupt_path.write_text("{", encoding="utf-8")

    restored = await runtime.restore_story_session(_binding())

    assert restored is not None
    assert restored.session.session_id == stored.session.session_id
    assert corrupt_path.read_text(encoding="utf-8") == "{"


@pytest.mark.asyncio
async def test_numeric_v2_indexed_restore_propagates_transient_read_failure(
    tmp_path,
    monkeypatch,
):
    """恢复槽位暂时不可读时必须中止，不能把有效进度当作不存在。"""  # noqa: DOCSTRING_CJK

    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_indexed_transient_failure",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    session_path = runtime.store._path(stored.session.session_id)
    path_type = type(session_path)
    original_read_text = path_type.read_text

    def transient_read(path, *args, **kwargs):
        if path == session_path:
            raise PermissionError("temporary storage failure")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(path_type, "read_text", transient_read)

    with pytest.raises(
        numeric_v2_store.NumericV2StoreError,
        match="numeric_session_read_failed",
    ):
        await runtime.restore_story_session(_binding())

    assert session_path.is_file()


@pytest.mark.asyncio
async def test_numeric_v2_character_id_survives_rename_but_blocks_same_name_reuse(
    tmp_path,
):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    original_binding = {**_binding(), "player_address": "旧称呼"}
    original = await runtime.start_session(
        session_id="runtime_character_identity",
        catgirl_binding=original_binding,
        opening_performance=_opening(),
    )
    renamed_binding = {
        **_binding(),
        "catgirl_name": "Lan Renamed",
        "player_address": "新称呼",
    }
    await update_numeric_v2_character_bindings(
        tmp_path,
        character_id=_binding()["character_id"],
        legacy_catgirl_name="Lan",
        catgirl_binding=renamed_binding,
    )

    restored_after_rename = await runtime.restore_story_session(renamed_binding)
    reused_name_binding = {
        **_binding(),
        "character_id": "character_33333333333333333333333333333333",
        "catgirl_id": "catgirl:character_33333333333333333333333333333333",
    }

    assert restored_after_rename is not None
    assert restored_after_rename.session.session_id == original.session.session_id
    assert restored_after_rename.session.catgirl_binding == {
        **renamed_binding,
        "player_address": "旧称呼",
    }
    assert await runtime.restore_story_session(reused_name_binding) is None


@pytest.mark.asyncio
async def test_numeric_v2_keeps_playing_when_scene_is_incomplete(tmp_path):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_scene_incomplete",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    stored = await runtime.commit_turn(
        runtime.prepare_turn(
            stored,
            TurnRequestV2("turn_1", 0, "先把眼前的误会说清楚。"),
            (),
            scene_complete=False,
        ),
        _performance("我们先说清楚。"),
    )

    second = runtime.prepare_turn(
        stored,
        TurnRequestV2("turn_2", 1, "这件事还没有解决。"),
        (),
        scene_complete=False,
    )

    assert second.route is None
    assert second.route_status == "playing"
    assert second.session.current_node_id == "start"
    assert second.ledger_event["scene_complete"] is False


def test_numeric_v2_rejects_model_invented_metric_criterion():
    engine = NumericV2Engine.from_mapping(_branch_story())

    with pytest.raises(ValueError, match="metric_change_criterion_invalid"):
        MetricChangeV2.from_mapping(
            {
                "metric_id": "trust",
                "delta": 1,
                "criterion": "模型自行补充的依据",
                "evidence": "玩家说会留下",
            },
            engine.metric_schema,
        )


@pytest.mark.asyncio
async def test_numeric_v2_uncommitted_candidate_does_not_change_session(tmp_path):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_atomic",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    runtime.prepare_turn(
        stored,
        TurnRequestV2("turn_not_committed", 0, "这轮模拟 Actor 失败。"),
        (),
    )

    restored = await runtime.restore_session("runtime_atomic")
    assert restored is not None
    assert restored.session.revision == 0
    assert restored.session.performance_history == ()
    assert restored.ledger_events == ()


@pytest.mark.asyncio
async def test_numeric_v2_restore_rejects_tampered_ledger(tmp_path):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_tamper",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    outcome = runtime.prepare_turn(
        stored,
        TurnRequestV2("turn_1", 0, "先聊聊。"),
        (),
    )
    committed = await runtime.commit_turn(outcome, _performance("好。"))
    path = runtime.store._path(committed.session.session_id)
    payload = deepcopy(json.loads(path.read_text(encoding="utf-8")))
    payload["ledger_events"][0]["after_metrics"]["trust"] = 99
    payload["session"]["metrics"]["trust"] = 99
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="numeric_ledger_replay_mismatch"):
        await runtime.restore_session("runtime_tamper")


@pytest.mark.asyncio
async def test_numeric_v2_restore_rejects_truncated_performance_history(tmp_path):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    stored = await runtime.start_session(
        session_id="runtime_truncated_performance",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    outcome = runtime.prepare_turn(
        stored,
        TurnRequestV2("turn_1", 0, "先聊聊。"),
        (),
    )
    committed = await runtime.commit_turn(outcome, _performance("好。"))
    path = runtime.store._path(committed.session.session_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["session"]["performance_history"] = []
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(
        numeric_v2_store.NumericV2StoreError,
        match="numeric_performance_history_mismatch",
    ):
        await runtime.restore_session("runtime_truncated_performance")


@pytest.mark.asyncio
async def test_session_commit_rechecks_fence_after_waiting_for_file_lock(tmp_path):
    import asyncio
    from contextlib import contextmanager
    writable = True

    @contextmanager
    def transaction():
        if not writable:
            raise PermissionError("maintenance")
        yield

    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path, write_transaction=transaction)
    current = await runtime.start_session(session_id="fenced", catgirl_binding=_binding(), opening_performance=_opening())
    outcome = runtime.prepare_turn(current, TurnRequestV2("late", 0, "input"), ())
    async with numeric_v2_store._lock(runtime.store._path("fenced")):
        task = asyncio.create_task(runtime.commit_turn(outcome, _performance("response")))
        await asyncio.sleep(0)
        assert not task.done()
        writable = False
    with pytest.raises(PermissionError, match="maintenance"):
        await task
    assert await runtime.restore_session("fenced") == current
