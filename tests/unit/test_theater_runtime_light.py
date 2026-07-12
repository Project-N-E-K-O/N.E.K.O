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
