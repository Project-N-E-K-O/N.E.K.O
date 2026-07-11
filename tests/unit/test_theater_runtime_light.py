"""验证轻量 Runtime 的启动、推进、自由对话和事务能力。"""  # noqa: DOCSTRING_CJK

import asyncio
import json

import pytest

from services.theater import runtime, session_store


@pytest.mark.asyncio
async def test_choice_roleplay_restore_and_exit(tmp_path):
    """一场演出可以推进、自由回应、恢复并主动离场。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘")
    assert started["ok"] is True
    assert started["state_revision"] == 0
    assert started["suggestion_options"]
    saved_start = await session_store.load_session(root, started["session_id"])
    assert saved_start["schema_version"] == session_store.SESSION_SCHEMA_VERSION

    roleplay = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="我想先听听你的心情",
        client_turn_id="turn_roleplay",
        base_revision=0,
    )
    assert roleplay["scenario_trace"]["progress_kind"] == "roleplay_response"
    assert roleplay["suggestion_options"]

    choice = roleplay["suggestion_options"][0]
    progressed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=choice["choice_id"],
        client_turn_id="turn_choice",
        base_revision=1,
    )
    assert progressed["scenario_trace"]["progress_kind"] == "graph_progress"
    restored = await runtime.get_state(root, started["session_id"])
    assert restored["state_revision"] == 2
    assert restored["dialogue"] == progressed["dialogue"]

    exited = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="user_exit",
        client_turn_id="turn_exit",
        base_revision=2,
    )
    assert exited["ending"]["reason"] == "user_exit"
    assert exited["can_resume"] is False


@pytest.mark.asyncio
async def test_start_uses_author_initial_scene_id(monkeypatch, tmp_path):
    """同一 phase 有多个场景时，开场必须使用作者指定的 initial_scene_id。"""  # noqa: DOCSTRING_CJK
    story = {
        "id": "scene-choice",
        "title": "场景选择测试",
        "initial_scene_id": "scene_selected",
        "opening_dialogue": "从指定场景开始喵。",
        "scenes": [
            {"id": "scene_wrong", "phase": "setup", "title": "错误场景", "text": "不应显示"},
            {"id": "scene_selected", "phase": "setup", "title": "指定场景", "text": "正确开场"},
        ],
        "narrative_nodes": [
            {"node_id": "node_start", "belong_phase": "setup", "node_type": "seed", "state_diff": {"add": []}}
        ],
        "edges": [],
    }

    async def _load_story(_story_id):
        """返回包含同 phase 多场景的可控故事。"""  # noqa: DOCSTRING_CJK
        return story

    monkeypatch.setattr(runtime.story_loader, "load_story", _load_story)
    started = await runtime.start_session(tmp_path / "theater", lanlan_name="测试猫娘")
    assert started["scene"] == {
        "scene_id": "scene_selected",
        "title": "指定场景",
        "text": "正确开场",
    }


@pytest.mark.asyncio
async def test_management_end_and_expiry_keep_public_phase_consistent(tmp_path):
    """管理结束和过期清理都必须同步内部与公开的 ended 状态。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    manually_started = await runtime.start_session(root, lanlan_name="手动结束猫娘")
    assert (await runtime.end_session(root, session_id=manually_started["session_id"]))["ok"] is True
    manual_state = await runtime.get_state(root, manually_started["session_id"])
    assert manual_state["phase"] == "ended"
    assert manual_state["can_resume"] is False

    expired_started = await runtime.start_session(root, lanlan_name="过期猫娘")
    expired_session = await session_store.load_session(root, expired_started["session_id"])
    expired_session["updated_at"] = 1
    await session_store.save_session(root, expired_session)
    cleanup_now = runtime.THEATER_SESSION_TTL_MS + 2
    assert await runtime.cleanup_expired_sessions(root, now_ms=cleanup_now) == {"expired": 1}
    expired_state = await runtime.get_state(root, expired_started["session_id"])
    assert expired_state["phase"] == "ended"
    assert expired_state["can_resume"] is False
    saved_expired = await session_store.load_session(root, expired_started["session_id"])
    assert saved_expired["phase"] == "ended"
    assert saved_expired["updated_at"] == cleanup_now


@pytest.mark.asyncio
async def test_idempotency_and_revision_conflict(tmp_path):
    """重复请求回放首次结果，旧 revision 不得覆盖新状态。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘")
    option = started["suggestion_options"][0]
    request = dict(
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=option["choice_id"],
        client_turn_id="turn_same",
        base_revision=0,
    )
    first = await runtime.submit_input(root, **request)
    replay = await runtime.submit_input(root, **request)
    assert replay == first

    conflict = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="继续聊聊",
        client_turn_id="turn_conflict",
        base_revision=0,
    )
    assert conflict == {"ok": False, "reason": "state_revision_conflict", "retryable": True, "state_revision": 1}


@pytest.mark.asyncio
async def test_concurrent_start_retry_reuses_one_session(tmp_path):
    """同一开场幂等 ID 的并发请求只能创建并返回一个 Session。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    results = await asyncio.gather(
        runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_same"),
        runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_same"),
    )
    assert results[0] == results[1]
    assert len(await session_store.list_session_ids(root)) == 1
    saved = await session_store.load_session(root, results[0]["session_id"])
    assert saved["start_client_id"] == "start_same"


@pytest.mark.asyncio
async def test_active_index_memory_changes_only_after_persistence(monkeypatch, tmp_path):
    """活动索引写盘失败时不能提前发布只存在于内存的新映射。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"

    async def _load_active_sessions(_root):
        """模拟空的磁盘活动索引。"""  # noqa: DOCSTRING_CJK
        return {}

    async def _save_active_sessions(_root, _active):
        """模拟活动索引持久化失败。"""  # noqa: DOCSTRING_CJK
        raise OSError("disk unavailable")

    monkeypatch.setattr(session_store, "load_active_sessions", _load_active_sessions)
    monkeypatch.setattr(session_store, "save_active_sessions", _save_active_sessions)
    with pytest.raises(OSError, match="disk unavailable"):
        await session_store.set_active_session(
            root,
            "持久化失败猫娘",
            "theater_00000000-0000-0000-0000-000000000001",
        )
    cache_key = session_store._active_cache_key(root, "持久化失败猫娘")
    assert cache_key not in session_store._ACTIVE_BY_ROOT_AND_LANLAN


@pytest.mark.asyncio
async def test_dialogue_speech_claims_each_revision_once(tmp_path):
    """TTS 只能取得已提交的公开猫娘对白，同一 revision 的重试不得重复播报。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘")
    first = await runtime.claim_dialogue_speech(
        root,
        session_id=started["session_id"],
        state_revision=0,
    )
    assert first["line"] == started["dialogue"]["text"]
    assert first["lanlan_name"] == "测试猫娘"

    replay = await runtime.claim_dialogue_speech(
        root,
        session_id=started["session_id"],
        state_revision=0,
    )
    assert replay["skipped"] == "already_spoken"
    stale = await runtime.claim_dialogue_speech(
        root,
        session_id=started["session_id"],
        state_revision=1,
    )
    assert stale == {"ok": True, "skipped": "stale_revision", "state_revision": 0}


@pytest.mark.asyncio
async def test_stale_session_dialogue_cannot_claim_tts(tmp_path):
    """被新开场替代的旧 Session 不得抢播对白或中断当前演出。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_old")
    await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_new")

    claim = await runtime.claim_dialogue_speech(
        root,
        session_id=old_session["session_id"],
        state_revision=0,
    )
    assert claim == {"ok": True, "skipped": "stale_session", "state_revision": 0}


@pytest.mark.asyncio
async def test_concurrent_turns_only_commit_one_revision(tmp_path):
    """同一 revision 的并发回合只有一个可以提交。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘")

    async def submit(suffix: str):
        return await runtime.submit_input(
            root,
            session_id=started["session_id"],
            input_kind="free_input",
            message=f"这是并发输入{suffix}",
            client_turn_id=f"turn_{suffix}",
            base_revision=0,
        )

    results = await asyncio.gather(submit("a"), submit("b"))
    assert sum(result.get("ok") is True for result in results) == 1
    assert sum(result.get("reason") == "state_revision_conflict" for result in results) == 1


@pytest.mark.asyncio
async def test_active_session_restores_after_memory_index_reset(tmp_path):
    """进程内索引清空后仍可从文件恢复当前演出。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘")
    session_store.reset_active_sessions_for_tests()
    restored = await runtime.get_active_state(root, lanlan_name="测试猫娘")
    assert restored["ok"] is True
    assert restored["session_id"] == started["session_id"]


@pytest.mark.asyncio
async def test_active_session_cache_is_scoped_by_theater_root(tmp_path):
    """同名猫娘在不同数据根中必须分别读取各自的活动 Session。"""  # noqa: DOCSTRING_CJK
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    session_a = "theater_00000000-0000-0000-0000-000000000001"
    session_b = "theater_00000000-0000-0000-0000-000000000002"
    await session_store.save_active_sessions(root_a, {"同名猫娘": session_a})
    await session_store.save_active_sessions(root_b, {"同名猫娘": session_b})
    session_store.reset_active_sessions_for_tests()

    assert await session_store.get_active_session_id(root_a, "同名猫娘") == session_a
    assert await session_store.get_active_session_id(root_b, "同名猫娘") == session_b


@pytest.mark.asyncio
async def test_active_session_index_serializes_updates_across_characters(monkeypatch, tmp_path):
    """不同猫娘并发更新共享索引时必须保留双方映射。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    stored: dict[str, str] = {}
    load_count = 0
    first_save_entered = asyncio.Event()
    release_first_save = asyncio.Event()

    async def _load_active_sessions(_root):
        """返回当前测试索引副本，并记录并发读取次数。"""  # noqa: DOCSTRING_CJK
        nonlocal load_count
        load_count += 1
        return dict(stored)

    async def _save_active_sessions(_root, active):
        """暂停第一位猫娘的写入，制造可复现的读改写竞争窗口。"""  # noqa: DOCSTRING_CJK
        if "猫娘甲" in active and "猫娘乙" not in active:
            first_save_entered.set()
            await release_first_save.wait()
        stored.clear()
        stored.update(active)

    monkeypatch.setattr(session_store, "load_active_sessions", _load_active_sessions)
    monkeypatch.setattr(session_store, "save_active_sessions", _save_active_sessions)

    first = asyncio.create_task(session_store.set_active_session(root, "猫娘甲", "theater_00000000-0000-0000-0000-000000000001"))
    await first_save_entered.wait()
    second = asyncio.create_task(session_store.set_active_session(root, "猫娘乙", "theater_00000000-0000-0000-0000-000000000002"))
    await asyncio.sleep(0)
    # 第二次读必须等待第一轮完整写入，不能在旧索引上独立计算。
    assert load_count == 1
    release_first_save.set()
    await asyncio.gather(first, second)
    assert stored == {
        "猫娘甲": "theater_00000000-0000-0000-0000-000000000001",
        "猫娘乙": "theater_00000000-0000-0000-0000-000000000002",
    }


@pytest.mark.asyncio
async def test_free_input_never_submits_even_when_it_repeats_choice_label(tmp_path):
    """自由输入即使逐字复述按钮也只能演绎，权威推进必须提交 choice_id。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘", story_id="tape_for_tomorrow_story")
    choice_ids = [item["choice_id"] for item in started["suggestion_options"]]
    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message=started["suggestion_options"][0]["label"],
        client_turn_id="turn_repeat_choice_label",
        base_revision=0,
    )
    assert result["scenario_trace"]["progress_kind"] == "roleplay_response"
    assert [item["choice_id"] for item in result["suggestion_options"]] == choice_ids


@pytest.mark.asyncio
async def test_free_dialogue_rewrites_choice_label_without_changing_target(monkeypatch, tmp_path):
    """自由对话可以更新按钮表达，但点击后仍进入作者原定节点并清除旧覆盖。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘", story_id="always_like_you_story")

    async def _fake_performance(**kwargs):
        """用可控演绎复现“承认保留照片后按钮应承接”的真实问题。"""  # noqa: DOCSTRING_CJK
        if kwargs["progress_kind"] == "roleplay_response":
            current = kwargs["choice_options"][0]
            return {
                "narration": "她轻轻抚过照片折角。",
                "dialogue": "因为我一直舍不得丢掉它喵。",
                "choice_rewrites": [
                    {
                        "choice_id": current["choice_id"],
                        "label": "把照片轻轻收好，回应她迟到的坦白",
                    }
                ],
            }
        return {"narration": "照片被重新收好。", "dialogue": "谢谢你愿意听我说完喵。", "choice_rewrites": []}

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    first_choice = next(item for item in started["suggestion_options"] if item["choice_mode"] == "action")
    recognized = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=first_choice["choice_id"],
        client_turn_id="turn_pick_photo",
        base_revision=0,
    )
    static_choice_id = recognized["suggestion_options"][0]["choice_id"]

    roleplay = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="你怎么还留着这张相片？",
        client_turn_id="turn_ask_photo",
        base_revision=1,
    )
    assert roleplay["suggestion_options"] == [
        {
            "choice_id": static_choice_id,
            "label": "把照片轻轻收好，回应她迟到的坦白",
            "choice_mode": "action",
        }
    ]

    progressed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=static_choice_id,
        client_turn_id="turn_store_photo",
        base_revision=2,
    )
    assert progressed["dialogue"]["text"] == "谢谢你愿意听我说完喵。"
    assert progressed["suggestion_options"][0]["choice_id"] != static_choice_id
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["story_state"]["choice_label_overrides"] == {}


@pytest.mark.asyncio
async def test_legacy_session_returns_upgrade_result_without_discarding_active_index(tmp_path):
    """旧 Session 不得误读，但恢复接口必须明确提示升级且保留原索引。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    session_id = "theater_00000000-0000-0000-0000-000000000001"
    path = session_store.session_path(root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 模拟瘦身前存档：保留合法 ID，但故意没有 schema_version。
    path.write_text(json.dumps({"session_id": session_id, "lanlan_name": "旧猫娘"}), encoding="utf-8")
    await session_store.save_active_sessions(root, {"旧猫娘": session_id})
    session_store.reset_active_sessions_for_tests()

    restored = await runtime.get_active_state(root, lanlan_name="旧猫娘")
    assert restored == {
        "ok": False,
        "reason": "session_upgrade_required",
        "session_id": session_id,
    }
    assert await session_store.load_active_sessions(root) == {"旧猫娘": session_id}
    assert await session_store.load_session(root, session_id) is None

    # 普通开场请求不能把旧恢复入口静默覆盖；只有玩家看到提示后的显式替换才允许新开场。
    blocked_start = await runtime.start_session(
        root,
        lanlan_name="旧猫娘",
        client_start_id="start_without_consent",
    )
    assert blocked_start == {
        "ok": False,
        "reason": "session_upgrade_required",
        "session_id": session_id,
    }
    assert await session_store.load_active_sessions(root) == {"旧猫娘": session_id}

    replacement = await runtime.start_session(
        root,
        lanlan_name="旧猫娘",
        client_start_id="start_after_consent",
        replace_incompatible_session=True,
    )
    assert replacement["ok"] is True
    assert replacement["session_id"] != session_id
    assert session_store.session_path(root, session_id).is_file()
    assert await session_store.load_active_sessions(root) == {"旧猫娘": replacement["session_id"]}

    # 角色切换再次遇到旧索引时也只清理索引，不尝试迁移未知私有状态。
    await session_store.save_active_sessions(root, {"旧猫娘": session_id})
    session_store.reset_active_sessions_for_tests()
    cleared = await runtime.clear_character_session(root, lanlan_name="旧猫娘")
    assert cleared == {"ok": True, "cleared": True, "session_id": session_id}
    assert await session_store.load_active_sessions(root) == {}


@pytest.mark.asyncio
async def test_tape_story_completes_through_structured_runtime(tmp_path):
    """新剧本通过真实 Runtime 连续推进后必须回到现实并正式落幕。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    result = await runtime.start_session(root, lanlan_name="测试猫娘", story_id="tape_for_tomorrow_story")
    path = [
        "choice_ask_permission",
        "choice_play_tape",
        "choice_enter_memory_dialogue",
        "choice_ask_why_push_away",
        "choice_go_broadcast_room",
        "choice_offer_present_words",
        "choice_finish_broadcast",
        "choice_return_present",
    ]
    for revision, choice_id in enumerate(path):
        assert choice_id in {option["choice_id"] for option in result["suggestion_options"]}
        result = await runtime.submit_input(
            root,
            session_id=result["session_id"],
            input_kind="choice",
            choice_id=choice_id,
            client_turn_id=f"turn_tape_{revision}",
            base_revision=revision,
        )
        assert result["ok"] is True
    assert result["ending"]["ending_id"] == "ending_tape_for_tomorrow"
    assert result["can_resume"] is False
    assert result["phase"] == "ending"


@pytest.mark.asyncio
async def test_long_romance_story_completes_after_twenty_eight_runtime_turns(tmp_path):
    """二十八回合都市爱情主线必须通过 Runtime 连续提交并正常落幕。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    result = await runtime.start_session(root, lanlan_name="测试猫娘", story_id="always_like_you_story")

    # 每轮使用首个作者选项；测试重点是完整链路、revision 和结局，不锁死文案细节。
    for revision in range(28):
        assert result["suggestion_options"], revision
        choice_id = result["suggestion_options"][0]["choice_id"]
        result = await runtime.submit_input(
            root,
            session_id=result["session_id"],
            input_kind="choice",
            choice_id=choice_id,
            client_turn_id=f"turn_long_romance_{revision}",
            base_revision=revision,
        )
        assert result["ok"] is True

    assert result["state_revision"] == 28
    assert result["ending"]["ending_id"] == "ending_meet_again_before_evening_wind"
    assert result["can_resume"] is False
    assert result["phase"] == "ending"
