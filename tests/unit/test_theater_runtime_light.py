"""验证轻量 Runtime 的启动、推进、自由对话和事务能力。"""  # noqa: DOCSTRING_CJK

import asyncio
import json

import pytest

from services.theater import rules, runtime, session_store, story_graph, story_loader, turn_service


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
async def test_start_personalizes_opening_with_active_catgirl(monkeypatch, tmp_path):
    """正式开场必须经过当前猫娘人格演绎，不能直接把作者 opening_dialogue 当成朗读稿。"""  # noqa: DOCSTRING_CJK

    class _CurrentCatgirlConfig:
        """只提供 Runtime 角色归属校验所需的当前猫娘。"""  # noqa: DOCSTRING_CJK

        def load_characters(self):
            return {"当前猫娘": "霜瞳", "猫娘": {"霜瞳": {}}}

    captured = {}

    async def _fake_opening_performance(**kwargs):
        """模拟模型按霜瞳人格转述开场，同时记录收到的作者语义和下一组选项。"""  # noqa: DOCSTRING_CJK
        captured.update(kwargs)
        return {
            "narration": kwargs["callback"],
            "dialogue": "哼，先帮本小姐接住那颗快掉下去的星星，别让它摔了喵。",
            "choice_rewrites": [],
            "matched_choice_id": "",
        }

    monkeypatch.setattr(runtime.llm, "generate_turn_async", _fake_opening_performance)
    started = await runtime.start_session(
        tmp_path / "theater",
        lanlan_name="霜瞳",
        story_id="date_list_last_item_story",
        config_manager=_CurrentCatgirlConfig(),
    )

    assert started["dialogue"]["text"].startswith("哼，先帮本小姐")
    assert captured["progress_kind"] == "opening"
    author_opening = captured["node"]["scripted_dialogue"]
    assert author_opening.startswith("今天旧街有纪念祭")
    assert "两张票" in author_opening
    assert "帮我接一下" in author_opening
    assert {item["choice_id"] for item in captured["choice_options"]} == {
        "choice_catch_star_charm",
        "choice_praise_star_charm",
    }


@pytest.mark.asyncio
async def test_graph_progress_gives_model_the_next_visible_choices(monkeypatch, tmp_path):
    """人格化当前对白时必须提供下一轮按钮，避免模型省略按钮所依赖的剧情邀请。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="霜瞳", story_id="date_list_last_item_story")
    captured = {}

    async def _fake_performance(**kwargs):
        captured.update(kwargs)
        return {
            "narration": kwargs["callback"],
            "dialogue": "这颗歪星星就先留着；本小姐带了两张票，就是来约你一起出发的喵。",
            "choice_rewrites": [],
            "matched_choice_id": "",
        }

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id="choice_catch_star_charm",
        client_turn_id="turn_personalized_handoff",
        base_revision=0,
    )

    assert captured["progress_kind"] == "graph_progress"
    assert [item["choice_id"] for item in captured["choice_options"]] == [
        "choice_promise_one_surprise",
        "choice_take_ticket_and_list",
    ]
    assert [item["choice_id"] for item in result["suggestion_options"]] == [
        "choice_promise_one_surprise",
        "choice_take_ticket_and_list",
    ]


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
async def test_free_input_rejects_oversized_message_before_persisting(tmp_path):
    """超长自由输入必须在调用模型和写入 Session 前被明确拒绝。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_input_cap")

    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="很" * (turn_service.MAX_FREE_INPUT_CHARS + 1),
        client_turn_id="turn_oversized_input",
        base_revision=0,
    )

    assert result == {"ok": False, "reason": "free_input_too_long"}
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["state_revision"] == 0
    assert len(saved["turns"]) == 1


@pytest.mark.asyncio
async def test_cached_nonterminal_turn_cannot_revive_stale_session(tmp_path):
    """旧 Session 被替换后，同一幂等 ID 也不能回放可恢复快照。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_cache_old")
    request = dict(
        session_id=old_session["session_id"],
        input_kind="free_input",
        message="这轮结果会进入幂等缓存",
        client_turn_id="turn_cached_before_replace",
        base_revision=0,
    )
    committed = await runtime.submit_input(root, **request)
    assert committed["ok"] is True
    await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_cache_new")

    replay = await runtime.submit_input(root, **request)

    assert replay == {"ok": False, "reason": "stale_session", "skipped": True}


@pytest.mark.asyncio
async def test_replaced_session_stays_ended_after_replacement_closes(tmp_path):
    """新演出结束并清空 active 后，被替换的旧 Session 也不能重新恢复。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_replaced_old")
    replacement = await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_replaced_new")
    await runtime.end_session(root, session_id=replacement["session_id"])

    restored = await runtime.get_state(root, old_session["session_id"])
    submitted = await runtime.submit_input(
        root,
        session_id=old_session["session_id"],
        input_kind="free_input",
        message="旧演出不应复活",
        client_turn_id="turn_replaced_old",
        base_revision=0,
    )

    assert restored["can_resume"] is False
    assert restored["phase"] == "ended"
    assert submitted == {"ok": False, "reason": "session_ended"}


@pytest.mark.asyncio
async def test_cached_terminal_turn_remains_idempotent(tmp_path):
    """主动离场已经提交后，同一幂等 ID 重试仍返回原终局响应。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_exit_cache")
    request = dict(
        session_id=started["session_id"],
        input_kind="user_exit",
        client_turn_id="turn_exit_cached",
        base_revision=0,
    )

    first = await runtime.submit_input(root, **request)
    replay = await runtime.submit_input(root, **request)

    assert replay == first
    assert replay["ending"]["reason"] == "user_exit"


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
async def test_start_rechecks_current_catgirl_after_waiting_for_character_lock(tmp_path):
    """开场等待旧角色锁期间切换猫娘后，不得创建旧角色 Session。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"

    class _MutableConfigManager:
        """模拟角色切换在开场请求排队期间发布新当前猫娘。"""  # noqa: DOCSTRING_CJK

        current_name = "旧猫娘"

        async def aload_characters(self):
            """返回调用时已经发布的当前猫娘。"""  # noqa: DOCSTRING_CJK
            return {"当前猫娘": self.current_name}

    config_manager = _MutableConfigManager()
    async with session_store.character_guard(root, "旧猫娘"):
        start_task = asyncio.create_task(
            runtime.start_session(
                root,
                lanlan_name="旧猫娘",
                client_start_id="start_waiting_character_switch",
                config_manager=config_manager,
            )
        )
        # 让开场任务进入角色锁等待，再模拟切换事务在同一边界内发布新角色。
        await asyncio.sleep(0)
        config_manager.current_name = "新猫娘"

    result = await start_task

    assert result == {"ok": False, "reason": "session_character_mismatch"}
    assert await session_store.list_session_ids(root) == []
    assert await session_store.load_active_sessions(root) == {}


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
async def test_failed_active_publication_ends_unpublished_replacement(monkeypatch, tmp_path):
    """新 Session 发布失败后必须终结，索引重建不能让未公开剧情复活。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    original = await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_published")

    async def _fail_active_publication(_root, _lanlan_name, _session_id):
        """模拟新 Session 已保存但活动索引无法持久化。"""  # noqa: DOCSTRING_CJK
        raise OSError("active index unavailable")

    monkeypatch.setattr(session_store, "set_active_session", _fail_active_publication)
    with pytest.raises(OSError, match="active index unavailable"):
        await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_unpublished")

    session_ids = await session_store.list_session_ids(root)
    unpublished_id = next(session_id for session_id in session_ids if session_id != original["session_id"])
    unpublished = await session_store.load_session(root, unpublished_id)
    restored_original = await session_store.load_session(root, original["session_id"])

    assert unpublished["phase"] == "ended"
    assert unpublished["ended_at"]
    assert unpublished["public_snapshot"]["can_resume"] is False
    assert restored_original["ended_at"] is None


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
async def test_dialogue_claim_and_new_start_share_character_boundary(monkeypatch, tmp_path):
    """旧对白认领写盘完成前，同猫娘新开场不能先替换 active Session。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_claim_old")
    real_save_session = session_store.save_session
    claim_save_entered = asyncio.Event()
    release_claim_save = asyncio.Event()

    async def _pause_claim_save(target_root, session):
        """只暂停旧 Session 的已朗读 revision 写盘，制造 active 切换竞争窗口。"""  # noqa: DOCSTRING_CJK
        if session.get("session_id") == old_session["session_id"] and session.get("spoken_dialogue_revisions") == [0]:
            claim_save_entered.set()
            await release_claim_save.wait()
        await real_save_session(target_root, session)

    monkeypatch.setattr(session_store, "save_session", _pause_claim_save)
    claim_task = asyncio.create_task(
        runtime.claim_dialogue_speech(root, session_id=old_session["session_id"], state_revision=0)
    )
    await claim_save_entered.wait()
    start_task = asyncio.create_task(
        runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_claim_new")
    )
    done, _pending = await asyncio.wait({start_task}, timeout=0.05)

    assert not done
    release_claim_save.set()
    claim, replacement = await asyncio.gather(claim_task, start_task)
    assert claim["line"] == old_session["dialogue"]["text"]
    assert replacement["session_id"] != old_session["session_id"]


@pytest.mark.asyncio
async def test_dialogue_playback_submission_holds_character_boundary(tmp_path):
    """对白进入 TTS 管线前，新开场不能越过同一角色原子边界。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_play_old")
    playback_entered = asyncio.Event()
    release_playback = asyncio.Event()

    async def _pause_playback(claim):
        """暂停 TTS 提交，验证角色锁覆盖认领返回后的原竞态窗口。"""  # noqa: DOCSTRING_CJK
        playback_entered.set()
        await release_playback.wait()
        return {"ok": True, "line": claim["line"], "audio_queued": True}

    playback_task = asyncio.create_task(
        runtime.claim_dialogue_speech(
            root,
            session_id=old_session["session_id"],
            state_revision=0,
            play=_pause_playback,
        )
    )
    await playback_entered.wait()
    start_task = asyncio.create_task(
        runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_play_new")
    )
    done, _pending = await asyncio.wait({start_task}, timeout=0.05)

    assert not done
    release_playback.set()
    played, replacement = await asyncio.gather(playback_task, start_task)
    assert played["audio_queued"] is True
    assert replacement["session_id"] != old_session["session_id"]


@pytest.mark.asyncio
async def test_character_publication_waits_for_dialogue_playback(tmp_path):
    """当前猫娘配置不能在旧对白仍向 TTS 提交时提前发布。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="旧猫娘", client_start_id="start_before_publish")
    playback_entered = asyncio.Event()
    release_playback = asyncio.Event()
    published_names = []

    async def _pause_playback(claim):
        """暂停旧对白提交，暴露角色发布与 TTS 的竞争窗口。"""  # noqa: DOCSTRING_CJK
        playback_entered.set()
        await release_playback.wait()
        return {"ok": True, "line": claim["line"], "audio_queued": True}

    async def _publish_new_character():
        """记录配置发布时点，不读写真实角色文件。"""  # noqa: DOCSTRING_CJK
        published_names.append("新猫娘")

    playback_task = asyncio.create_task(
        runtime.claim_dialogue_speech(
            root,
            session_id=started["session_id"],
            state_revision=0,
            play=_pause_playback,
        )
    )
    await playback_entered.wait()
    switch_task = asyncio.create_task(
        runtime.publish_character_switch(
            root,
            old_lanlan_name="旧猫娘",
            publish=_publish_new_character,
        )
    )
    done, _pending = await asyncio.wait({switch_task}, timeout=0.05)

    assert not done
    assert published_names == []
    release_playback.set()
    played, switched = await asyncio.gather(playback_task, switch_task)
    assert played["audio_queued"] is True
    assert switched["cleared"] is True
    assert published_names == ["新猫娘"]
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["ended_at"]


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
async def test_ended_session_dialogue_cannot_claim_tts(tmp_path):
    """角色切换结束并清空 active 索引后，旧 Session 仍不得认领对白。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="旧猫娘", client_start_id="start_before_switch")
    assert (await runtime.end_session(root, session_id=started["session_id"]))["ok"] is True

    claim = await runtime.claim_dialogue_speech(
        root,
        session_id=started["session_id"],
        state_revision=0,
    )

    assert claim == {"ok": True, "skipped": "stale_session", "state_revision": 0}
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["spoken_dialogue_revisions"] == []


@pytest.mark.asyncio
async def test_committed_terminal_dialogue_can_claim_tts(tmp_path):
    """主动离场的已提交终局对白应朗读一次，不能被 ended_at 提前吞掉。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_terminal_tts")
    exited = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="user_exit",
        client_turn_id="turn_terminal_tts",
        base_revision=0,
    )

    claim = await runtime.claim_dialogue_speech(
        root,
        session_id=started["session_id"],
        state_revision=exited["state_revision"],
    )

    assert claim["line"] == exited["dialogue"]["text"]
    assert claim["state_revision"] == exited["state_revision"]


@pytest.mark.asyncio
async def test_switched_character_dialogue_cannot_claim_tts(tmp_path):
    """当前猫娘变化后，旧角色对白不得写入已朗读 revision。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="旧猫娘", client_start_id="start_old_tts_character")

    claim = await runtime.claim_dialogue_speech(
        root,
        session_id=started["session_id"],
        state_revision=0,
        expected_lanlan_name="新猫娘",
    )

    assert claim == {"ok": True, "skipped": "character_changed", "state_revision": 0}
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["spoken_dialogue_revisions"] == []


@pytest.mark.asyncio
async def test_turn_rechecks_stale_session_after_llm_returns(monkeypatch, tmp_path):
    """模型等待期间被新开场替换的旧 Session 不得再提交候选状态。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_old")
    llm_entered = asyncio.Event()
    release_llm = asyncio.Event()

    async def _wait_for_replacement(**_kwargs):
        """暂停模型结果，给另一个窗口留下替换活动 Session 的确定窗口。"""  # noqa: DOCSTRING_CJK
        llm_entered.set()
        await release_llm.wait()
        return {"narration": "旧演绎不应提交。", "dialogue": "这句也不应保存喵。", "choice_rewrites": []}

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _wait_for_replacement)
    pending_turn = asyncio.create_task(
        runtime.submit_input(
            root,
            session_id=old_session["session_id"],
            input_kind="free_input",
            message="等你回应时我打开了新窗口",
            client_turn_id="turn_waiting_llm",
            base_revision=0,
        )
    )
    await llm_entered.wait()
    replacement = await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_new")
    release_llm.set()

    assert (await pending_turn) == {"ok": False, "reason": "stale_session", "skipped": True}
    assert replacement["session_id"] != old_session["session_id"]
    saved_old = await session_store.load_session(root, old_session["session_id"])
    assert saved_old["state_revision"] == 0
    assert len(saved_old["turns"]) == 1
    assert saved_old["turns"][0]["text"] == old_session["dialogue"]["text"]


@pytest.mark.asyncio
async def test_turn_commit_blocks_replacement_start_until_save_finishes(monkeypatch, tmp_path):
    """旧回合从 stale 校验到写盘结束前，新开场不能替换 active Session。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_before_commit")
    real_save_session = session_store.save_session
    candidate_save_entered = asyncio.Event()
    release_candidate_save = asyncio.Event()

    async def _pause_candidate_save(target_root, session):
        """只暂停 revision 1 的候选写盘，确定性暴露 stale 校验后的替换窗口。"""  # noqa: DOCSTRING_CJK
        if session.get("session_id") == started["session_id"] and session.get("state_revision") == 1:
            candidate_save_entered.set()
            await release_candidate_save.wait()
        await real_save_session(target_root, session)

    monkeypatch.setattr(session_store, "save_session", _pause_candidate_save)
    turn_task = asyncio.create_task(
        runtime.submit_input(
            root,
            session_id=started["session_id"],
            input_kind="free_input",
            message="这轮正在提交",
            client_turn_id="turn_atomic_commit",
            base_revision=0,
        )
    )
    await candidate_save_entered.wait()
    start_task = asyncio.create_task(
        runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_after_commit")
    )
    done, _pending = await asyncio.wait({start_task}, timeout=0.05)

    assert not done
    release_candidate_save.set()
    committed, replacement = await asyncio.gather(turn_task, start_task)
    assert committed["ok"] is True
    assert committed["state_revision"] == 1
    assert replacement["session_id"] != started["session_id"]


@pytest.mark.asyncio
async def test_turn_rechecks_current_catgirl_after_llm_returns(monkeypatch, tmp_path):
    """模型等待期间切换猫娘时，旧角色候选回合不得提交。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(root, lanlan_name="旧猫娘", client_start_id="start_old_character")
    llm_entered = asyncio.Event()
    release_llm = asyncio.Event()

    class _MutableConfigManager:
        """模拟角色切换先更新当前猫娘、后异步清理旧 Session 的时序。"""  # noqa: DOCSTRING_CJK

        current_name = "旧猫娘"

        async def aload_characters(self):
            """返回调用时的当前猫娘配置。"""  # noqa: DOCSTRING_CJK
            return {"当前猫娘": self.current_name}

    async def _wait_for_character_switch(**_kwargs):
        """暂停模型结果，暴露角色配置已经切换但 Session 尚未结束的窗口。"""  # noqa: DOCSTRING_CJK
        llm_entered.set()
        await release_llm.wait()
        return {"narration": "旧角色结果不应提交。", "dialogue": "这句也不应播放喵。", "choice_rewrites": []}

    config_manager = _MutableConfigManager()
    monkeypatch.setattr("services.theater.llm.generate_turn_async", _wait_for_character_switch)
    pending_turn = asyncio.create_task(
        runtime.submit_input(
            root,
            session_id=old_session["session_id"],
            input_kind="free_input",
            message="你回应时我切换了猫娘",
            client_turn_id="turn_waiting_character_switch",
            base_revision=0,
            config_manager=config_manager,
            expected_lanlan_name="旧猫娘",
        )
    )
    await llm_entered.wait()
    config_manager.current_name = "新猫娘"
    release_llm.set()

    assert (await pending_turn) == {"ok": False, "reason": "session_character_mismatch"}
    saved_old = await session_store.load_session(root, old_session["session_id"])
    assert saved_old["state_revision"] == 0
    assert len(saved_old["turns"]) == 1


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
async def test_session_state_and_input_reject_another_catgirl(tmp_path):
    """本地旧 Session ID 不能恢复或推进其他猫娘的私有演绎。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(root, lanlan_name="旧猫娘", client_start_id="start_old_catgirl")

    restored = await runtime.get_state(
        root,
        old_session["session_id"],
        expected_lanlan_name="当前猫娘",
    )
    submitted = await runtime.submit_input(
        root,
        session_id=old_session["session_id"],
        input_kind="free_input",
        message="继续上一位猫娘的剧情",
        client_turn_id="turn_wrong_catgirl",
        base_revision=0,
        expected_lanlan_name="当前猫娘",
    )

    assert restored == {"ok": False, "reason": "session_character_mismatch"}
    assert submitted == {"ok": False, "reason": "session_character_mismatch"}
    saved = await session_store.load_session(root, old_session["session_id"])
    assert saved["state_revision"] == 0
    assert len(saved["turns"]) == 1


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
async def test_corrupt_active_session_index_recovers_as_empty(tmp_path):
    """损坏的活动索引不得阻断读取，并应能被下一次正常写入重建。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    path = session_store.active_sessions_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"测试猫娘":', encoding="utf-8")
    session_store.reset_active_sessions_for_tests()

    assert await session_store.load_active_sessions(root) == {}

    session_id = "theater_00000000-0000-0000-0000-000000000009"
    await session_store.set_active_session(root, "测试猫娘", session_id)
    assert await session_store.load_active_sessions(root) == {"测试猫娘": session_id}


@pytest.mark.asyncio
async def test_corrupt_active_index_rebuilds_latest_unended_session(tmp_path):
    """索引损坏后必须恢复最新未结束演出，并继续把被替换 Session 判为 stale。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_rebuild_old")
    replacement = await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_rebuild_new")
    path = session_store.active_sessions_path(root)
    path.write_text('{"测试猫娘":', encoding="utf-8")
    session_store.reset_active_sessions_for_tests()

    rebuilt = await session_store.load_active_sessions(root)
    old_saved = await session_store.load_session(root, old_session["session_id"])

    assert rebuilt == {"测试猫娘": replacement["session_id"]}
    assert old_saved["ended_at"]
    assert await session_store.is_stale_session(root, old_saved) is True


@pytest.mark.asyncio
async def test_invalid_active_index_payload_rebuilds_current_session(tmp_path):
    """合法 JSON 的错误顶层结构也必须重建，不能按空索引放行历史 Session。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘", client_start_id="start_invalid_index")
    path = session_store.active_sessions_path(root)
    path.write_text("[]", encoding="utf-8")
    session_store.reset_active_sessions_for_tests()

    rebuilt = await session_store.load_active_sessions(root)

    assert rebuilt == {"测试猫娘": started["session_id"]}
    assert json.loads(path.read_text(encoding="utf-8")) == rebuilt


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
async def test_free_input_matching_current_choice_advances_author_node(monkeypatch, tmp_path):
    """自由输入明确完成当前 Choice 时复用稳定 ID 推进，不要求玩家重复点击。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘", story_id="date_list_last_item_story")
    selected = started["suggestion_options"][0]
    before = await session_store.load_session(root, started["session_id"])

    async def _fake_performance(**kwargs):
        """模拟模型把玩家自然语言安全映射到本轮提供的稳定 Choice。"""  # noqa: DOCSTRING_CJK
        if kwargs["progress_kind"] == "graph_progress":
            return {
                "narration": kwargs["callback"],
                "dialogue": "我会从这一刻的选择继续回应你喵。",
                "choice_rewrites": [],
                "matched_choice_id": "",
            }
        return {
            "narration": "",
            "dialogue": "我知道你已经作出决定了喵。",
            "choice_rewrites": [],
            "matched_choice_id": kwargs["choice_options"][0]["choice_id"],
        }

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message=selected["label"],
        client_turn_id="turn_repeat_choice_label",
        base_revision=0,
    )
    assert result["scenario_trace"] == {
        "progress_kind": "graph_progress",
        "action_label": selected["label"],
    }
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["story_state"]["current_node_id"] != before["story_state"]["current_node_id"]
    assert selected["choice_id"] not in [item["choice_id"] for item in result["suggestion_options"]]


@pytest.mark.asyncio
async def test_repeated_latent_intent_branches_only_after_two_goal_pullbacks(monkeypatch, tmp_path):
    """普通岔题会清零计数；同一作者意图连续第三次出现时才进入隐藏支线。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="糖糖", story_id="date_list_last_item_story")
    story = await story_loader.load_story("date_list_last_item_story")
    session = await session_store.load_session(root, started["session_id"])
    state = rules.initial_state(story, initial_node_id=story_loader.initial_node_id(story))
    # 直接构造已经抵达试点节点的权威状态，避免本测试重复验证前十轮主线选择。
    for node_id in (
        "node_date_list_seed",
        "node_protect_charm",
        "node_theme_question",
        "node_festival_invitation",
        "node_choose_real_start",
        "node_enter_festival",
        "node_handhold_lane",
        "node_exchange_gifts",
        "node_share_dessert",
        "node_photo_booth",
        "node_honest_observation",
    ):
        rules.apply_node(story, state, story_graph.node_by_id(story, node_id))
    session["story_state"] = state
    session["phase"] = "closeness"
    session["turns"] = []
    await session_store.save_session(root, session)

    async def _fake_performance(**kwargs):
        """用稳定 intent_id 模拟语义分类；目标节点演出不再返回任何路由字段。"""  # noqa: DOCSTRING_CJK
        if kwargs["progress_kind"] == "graph_progress":
            return {
                "narration": kwargs["callback"],
                "dialogue": "糖糖会把刚才的问题认真说完喵。",
                "choice_rewrites": [],
                "matched_choice_id": "",
                "observed_intent_id": "",
            }
        observed = (
            "intent_continue_mutual_impression_talk"
            if kwargs["user_message"].startswith("印象")
            else ""
        )
        return {
            "narration": "",
            "dialogue": "糖糖先回应你现在说的这句话喵。",
            "choice_rewrites": [],
            "matched_choice_id": "",
            "observed_intent_id": observed,
        }

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    messages = [
        "印象一：你怎么看我？",
        "雨是不是快停了？",
        "印象二：我还是想知道你怎么看我。",
        "印象三：再说具体一点吧。",
        "印象四：先把这个问题说完。",
    ]
    results = []
    for revision, message in enumerate(messages):
        results.append(
            await runtime.submit_input(
                root,
                session_id=started["session_id"],
                input_kind="free_input",
                message=message,
                client_turn_id=f"turn_latent_{revision}",
                base_revision=revision,
            )
        )
        saved = await session_store.load_session(root, started["session_id"])
        if revision == 0:
            assert saved["story_state"]["intent_streak"] == 1
        elif revision == 1:
            assert saved["story_state"]["intent_streak"] == 0
        elif revision == 2:
            assert saved["story_state"]["intent_streak"] == 1
        elif revision == 3:
            assert saved["story_state"]["intent_streak"] == 2

    assert [item["scenario_trace"]["progress_kind"] for item in results] == [
        "roleplay_response",
        "roleplay_response",
        "roleplay_response",
        "roleplay_response",
        "graph_progress",
    ]
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["story_state"]["current_node_id"] == "node_deep_impression_conversation"
    assert saved["story_state"]["branch_commitment"] == "transition_deep_impression"
    assert saved["story_state"]["intent_streak"] == 0
    assert {item["choice_id"] for item in results[-1]["suggestion_options"]} == {
        "choice_continue_impression_talk",
        "choice_meet_tomorrow_without_list",
    }
    # 内部图谱身份和拉回计数不经过 Projector，前端只能看到作者推荐按钮。
    serialized = json.dumps(results[-1], ensure_ascii=False)
    assert "intent_continue_mutual_impression_talk" not in serialized
    assert "transition_deep_impression" not in serialized


@pytest.mark.asyncio
async def test_natural_language_match_regenerates_performance_from_target_node(monkeypatch, tmp_path):
    """自然语言命中后必须立刻演目标节点，不能把旧节点台词延迟显示一轮。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="糖糖", story_id="date_list_last_item_story")
    first_choice = next(item for item in started["suggestion_options"] if item["choice_mode"] == "dialogue")
    routed_calls = []

    async def _fake_performance(**kwargs):
        """先返回旧节点坏台词与命中 ID，再验证正式演出改用目标节点上下文。"""  # noqa: DOCSTRING_CJK
        if kwargs["progress_kind"] == "roleplay_response":
            routed_calls.append((kwargs["node"]["node_id"], kwargs["progress_kind"], kwargs["choice_options"]))
            return {
                "narration": "",
                "dialogue": "既然你喜欢这颗歪星星，那糖糖就放心啦喵。",
                "choice_rewrites": [],
                "matched_choice_id": "choice_promise_one_surprise",
            }
        if kwargs["node"]["node_id"] == "node_theme_question":
            routed_calls.append((kwargs["node"]["node_id"], kwargs["progress_kind"], kwargs["choice_options"]))
            return {
                "narration": kwargs["callback"],
                "dialogue": "那就出发喵，到了入口我们先去星灯长廊看看。",
                "choice_rewrites": [],
                "matched_choice_id": "",
            }
        return {
            "narration": kwargs["callback"],
            "dialogue": "糖糖很珍惜你的话，也想邀请你一起出发喵。",
            "choice_rewrites": [],
            "matched_choice_id": "",
        }

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    progressed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=first_choice["choice_id"],
        client_turn_id="turn_keep_handmade_star",
        base_revision=0,
    )
    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="出发吧，目标星灯祭",
        client_turn_id="turn_depart_by_natural_language",
        base_revision=progressed["state_revision"],
    )

    assert [(node_id, kind) for node_id, kind, _options in routed_calls] == [
        ("node_protect_charm", "roleplay_response"),
        ("node_theme_question", "graph_progress"),
    ]
    assert [item["choice_id"] for item in routed_calls[1][2]] == [
        "choice_wear_pair_wristband",
        "choice_explain_reflective_wristband",
    ]
    assert "星星" not in result["dialogue"]["text"]
    assert "出发" in result["dialogue"]["text"]
    assert result["scenario_trace"]["progress_kind"] == "graph_progress"
    assert result["suggestion_options"][0]["choice_id"] == "choice_wear_pair_wristband"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["出发", "出发吧", "出发，目标星灯祭", "走吧"])
async def test_authored_completion_advances_before_old_node_generation(monkeypatch, tmp_path, message):
    """作者声明的完成表达必须直接演目标节点，不能先生成一次旧邀请。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="糖糖", story_id="date_list_last_item_story")
    first_choice = next(item for item in started["suggestion_options"] if item["choice_mode"] == "dialogue")
    calls = []

    async def _fake_performance(**kwargs):
        """记录实际演绎节点，确保确定性路由没有先请求旧节点。"""  # noqa: DOCSTRING_CJK
        calls.append((kwargs["node"]["node_id"], kwargs["progress_kind"]))
        return {
            "narration": kwargs["callback"],
            "dialogue": "那就出发喵，先去旧街入口。",
            "choice_rewrites": [],
            "matched_choice_id": "",
        }

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    progressed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=first_choice["choice_id"],
        client_turn_id="turn_keep_handmade_star",
        base_revision=0,
    )
    calls.clear()

    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message=message,
        client_turn_id=f"turn_authored_depart_{message}",
        base_revision=progressed["state_revision"],
    )

    assert calls == [("node_theme_question", "graph_progress")]
    assert result["scenario_trace"] == {"progress_kind": "graph_progress", "action_label": message}
    assert result["suggestion_options"][0]["choice_id"] == "choice_wear_pair_wristband"
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["story_state"]["current_node_id"] == "node_theme_question"
    assert message not in saved["story_state"]["scene_notes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["先别出发", "为什么出发"])
async def test_authored_completion_does_not_consume_negation_or_question(monkeypatch, tmp_path, message):
    """含否定或疑问的长句不是作者完成表达，必须保留为当前节点互动。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="糖糖", story_id="date_list_last_item_story")
    first_choice = next(item for item in started["suggestion_options"] if item["choice_mode"] == "dialogue")
    calls = []

    async def _fake_performance(**kwargs):
        """明确返回未命中，隔离确定性匹配与模型自由路由。"""  # noqa: DOCSTRING_CJK
        calls.append((kwargs["node"]["node_id"], kwargs["progress_kind"]))
        return {
            "narration": kwargs["callback"] if kwargs["progress_kind"] == "graph_progress" else "",
            "dialogue": "糖糖先回答你眼前的问题喵。",
            "choice_rewrites": [],
            "matched_choice_id": "",
        }

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    progressed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=first_choice["choice_id"],
        client_turn_id="turn_keep_handmade_star",
        base_revision=0,
    )
    calls.clear()

    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message=message,
        client_turn_id=f"turn_hold_depart_{message}",
        base_revision=progressed["state_revision"],
    )

    assert calls == [("node_protect_charm", "roleplay_response")]
    assert result["scenario_trace"]["progress_kind"] == "roleplay_response"
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["story_state"]["current_node_id"] == "node_protect_charm"
    assert saved["story_state"]["scene_notes"][-1] == message


@pytest.mark.asyncio
async def test_free_input_without_valid_model_match_stays_on_current_node(tmp_path):
    """模型不可用时即使玩家复述按钮也不得由服务端猜测推进。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘", story_id="date_list_last_item_story")
    before = await session_store.load_session(root, started["session_id"])

    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message=started["suggestion_options"][0]["label"],
        client_turn_id="turn_model_unavailable_hold",
        base_revision=0,
    )

    after = await session_store.load_session(root, started["session_id"])
    assert result["scenario_trace"]["progress_kind"] == "roleplay_response"
    assert after["story_state"]["current_node_id"] == before["story_state"]["current_node_id"]


@pytest.mark.asyncio
async def test_natural_language_and_click_commit_same_author_state(monkeypatch, tmp_path):
    """自然语言只是 Choice 的第二入口，提交后的作者权威状态必须与点击完全一致。"""  # noqa: DOCSTRING_CJK
    click_root = tmp_path / "click" / "theater"
    natural_root = tmp_path / "natural" / "theater"
    clicked_start = await runtime.start_session(
        click_root,
        lanlan_name="测试猫娘",
        story_id="date_list_last_item_story",
    )
    natural_start = await runtime.start_session(
        natural_root,
        lanlan_name="测试猫娘",
        story_id="date_list_last_item_story",
    )
    selected = clicked_start["suggestion_options"][0]

    async def _fake_performance(**kwargs):
        """只在自由输入时返回当前稳定 ID，点击链保持原有显式路由。"""  # noqa: DOCSTRING_CJK
        choice_options = kwargs.get("choice_options") or []
        return {
            "narration": kwargs.get("callback") or "作者回调会在自然语言命中后补入。",
            "dialogue": "这一步由你决定喵。",
            "choice_rewrites": [],
            "matched_choice_id": (
                choice_options[0]["choice_id"]
                if kwargs["progress_kind"] == "roleplay_response" and choice_options
                else ""
            ),
        }

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    await runtime.submit_input(
        click_root,
        session_id=clicked_start["session_id"],
        input_kind="choice",
        choice_id=selected["choice_id"],
        client_turn_id="turn_click_choice",
        base_revision=0,
    )
    await runtime.submit_input(
        natural_root,
        session_id=natural_start["session_id"],
        input_kind="free_input",
        message=selected["label"],
        client_turn_id="turn_natural_choice",
        base_revision=0,
    )

    clicked = await session_store.load_session(click_root, clicked_start["session_id"])
    natural = await session_store.load_session(natural_root, natural_start["session_id"])
    assert natural["story_state"] == clicked["story_state"]


@pytest.mark.asyncio
async def test_free_dialogue_rewrites_choice_label_without_changing_target(monkeypatch, tmp_path):
    """自由对话可以更新按钮表达，但点击后仍进入作者原定节点并清除旧覆盖。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘", story_id="date_list_last_item_story")

    async def _fake_performance(**kwargs):
        """用当前约会剧情复现“自由追问后按钮应承接”的更新链。"""  # noqa: DOCSTRING_CJK
        if kwargs["progress_kind"] == "roleplay_response":
            current = kwargs["choice_options"][0]
            return {
                "narration": "她把约会清单折到只剩路线的一面。",
                "dialogue": "真正想说的话，我希望你留到看着我的时候再决定喵。",
                "matched_choice_id": "",
                "choice_rewrites": [
                    {
                        "choice_id": current["choice_id"],
                        "label": "“真正想说的话，我留到你面前再说。”",
                    }
                ],
            }
        return {"narration": "清单只留下路线，背面的答案被重新折起。", "dialogue": "好，那我等你亲口告诉我喵。", "choice_rewrites": []}

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    first_choice = next(item for item in started["suggestion_options"] if item["choice_mode"] == "action")
    recognized = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=first_choice["choice_id"],
        client_turn_id="turn_keep_handmade_charm",
        base_revision=0,
    )
    static_choice_id = recognized["suggestion_options"][0]["choice_id"]
    unchanged_choice = recognized["suggestion_options"][1]

    roleplay = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="那你希望我今天哪件事不要提前计划？",
        client_turn_id="turn_ask_what_to_leave_open",
        base_revision=1,
    )
    assert roleplay["suggestion_options"] == [
        {
            "choice_id": static_choice_id,
            "label": "“真正想说的话，我留到你面前再说。”",
            "choice_mode": "dialogue",
        },
        unchanged_choice,
    ]

    progressed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=static_choice_id,
        client_turn_id="turn_leave_words_unplanned",
        base_revision=2,
    )
    assert progressed["dialogue"]["text"] == "好，那我等你亲口告诉我喵。"
    assert progressed["suggestion_options"][0]["choice_id"] != static_choice_id
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["story_state"]["choice_label_overrides"] == {}


@pytest.mark.asyncio
async def test_free_dialogue_does_not_keep_previous_choice_rewrite(monkeypatch, tmp_path):
    """下一轮模型漏写改写时必须清掉旧覆盖，不能让按钮永远停在旧上文。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘", story_id="date_list_last_item_story")
    first_choice = next(item for item in started["suggestion_options"] if item["choice_mode"] == "action")
    calls = 0

    async def _fake_performance(**kwargs):
        """第一轮返回上下文化按钮，第二轮故意漏写以验证旧值不会继续遗留。"""  # noqa: DOCSTRING_CJK
        nonlocal calls
        if kwargs["progress_kind"] == "roleplay_response":
            calls += 1
            current = kwargs["choice_options"][0]
            return {
                "narration": "",
                "dialogue": "我听见了喵。",
                "matched_choice_id": "",
                "choice_rewrites": (
                    [{"choice_id": current["choice_id"], "label": "“先收好票，我们到入口再决定。”"}]
                    if calls == 1
                    else []
                ),
            }
        return {"narration": kwargs.get("callback") or "挂坠被收好。", "dialogue": "一起出发吗？", "choice_rewrites": []}

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    progressed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=first_choice["choice_id"],
        client_turn_id="turn_keep_charm_before_rewrites",
        base_revision=0,
    )
    author_label = progressed["suggestion_options"][0]["label"]

    first = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="先去哪里？",
        client_turn_id="turn_contextual_rewrite",
        base_revision=1,
    )
    assert first["suggestion_options"][0]["label"] == "“先收好票，我们到入口再决定。”"

    second = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="那就走吧。",
        client_turn_id="turn_missing_rewrite",
        base_revision=2,
    )
    assert second["suggestion_options"][0]["label"] == author_label
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["story_state"]["choice_label_overrides"] == {}


def test_choice_rewrite_rejects_action_text_inside_dialogue_option():
    """对白按钮改写混入“轻声说”等动作说明时必须保留作者原文。"""  # noqa: DOCSTRING_CJK
    options = [
        {
            "choice_id": "choice_name",
            "label": "“霜瞳。”",
            "author_label": "“霜瞳。”",
            "choice_mode": "dialogue",
        }
    ]
    rewrites = [{"choice_id": "choice_name", "label": "轻声唤她的名字：“霜瞳。”"}]
    assert turn_service._validated_choice_rewrites(rewrites, options) == {}


def test_choice_rewrite_rejects_unchanged_author_label():
    """只改引号或标点不能冒充承接新上文的推荐文案。"""  # noqa: DOCSTRING_CJK
    options = [
        {
            "choice_id": "choice_depart",
            "label": "“好，那就一起出发吧。”",
            "author_label": "“好，那就一起出发吧。”",
            "choice_mode": "dialogue",
        }
    ]
    rewrites = [{"choice_id": "choice_depart", "label": '"好，那就一起出发吧!"'}]
    assert turn_service._validated_choice_rewrites(rewrites, options) == {}


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
async def test_date_story_completes_through_structured_runtime(tmp_path):
    """甜蜜新剧本通过真实 Runtime 连续推进后必须确认约会并正式落幕。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    result = await runtime.start_session(root, lanlan_name="测试猫娘", story_id="date_list_last_item_story")
    player_facing_turns = [result["dialogue"]["text"], result["narration"]["text"]]
    path = [
        "choice_catch_star_charm",
        "choice_promise_one_surprise",
        "choice_wear_pair_wristband",
        "choice_hold_hands",
        "choice_propose_blind_gift",
        "choice_choose_star_bell",
        "choice_place_selected_gift",
        "choice_offer_favorite_bite",
        "choice_take_four_photos",
        "choice_write_small_observation",
        "choice_stop_checking_cards",
        "choice_pick_up_script_cards",
        "choice_go_off_map_together",
        "choice_confess_without_script",
        "choice_write_tomorrow_together",
    ]
    for revision, choice_id in enumerate(path):
        assert choice_id in {option["choice_id"] for option in result["suggestion_options"]}
        result = await runtime.submit_input(
            root,
            session_id=result["session_id"],
            input_kind="choice",
            choice_id=choice_id,
            client_turn_id=f"turn_date_{revision}",
            base_revision=revision,
        )
        assert result["ok"] is True
        dialogue_text = result["dialogue"]["text"]
        player_facing_turns.extend([dialogue_text, result["narration"]["text"]])
        if choice_id == "choice_promise_one_surprise":
            assert "两条腕带" in dialogue_text
            assert "各自戴" in dialogue_text
    assert result["ending"]["ending_id"] == "ending_last_item_is_tomorrow"
    assert result["can_resume"] is False
    assert result["phase"] == "ending"
    complete_visible_path = "\n".join(player_facing_turns)
    for obsolete_positive_phrase in (
        "双人券上的七项",
        "入场券上的任务",
        "七项挑战规则",
        "领取同步印章",
        "挑战设备",
        "计分牌",
        "盖章提示",
    ):
        assert obsolete_positive_phrase not in complete_visible_path


@pytest.mark.asyncio
async def test_date_story_completes_after_fifteen_runtime_turns(tmp_path):
    """十五轮规整主线必须通过 Runtime 连续提交并正常落幕。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    result = await runtime.start_session(root, lanlan_name="测试猫娘", story_id="date_list_last_item_story")

    # 每轮使用首个作者选项；测试重点是完整链路、revision 和结局，不锁死文案细节。
    for revision in range(15):
        assert result["suggestion_options"], revision
        choice_id = result["suggestion_options"][0]["choice_id"]
        result = await runtime.submit_input(
            root,
            session_id=result["session_id"],
            input_kind="choice",
            choice_id=choice_id,
            client_turn_id=f"turn_date_first_choice_{revision}",
            base_revision=revision,
        )
        assert result["ok"] is True

    assert result["state_revision"] == 15
    assert result["ending"]["ending_id"] == "ending_last_item_is_tomorrow"
    assert result["can_resume"] is False
    assert result["phase"] == "ending"
