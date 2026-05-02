import asyncio

import pytest

from main_routers import game_router


@pytest.fixture(autouse=True)
def _reset_game_state():
    sessions_snapshot = dict(game_router._game_sessions)
    routes_snapshot = dict(game_router._game_route_states)
    game_router._game_sessions.clear()
    game_router._game_route_states.clear()
    try:
        yield
    finally:
        game_router._game_sessions.clear()
        game_router._game_sessions.update(sessions_snapshot)
        game_router._game_route_states.clear()
        game_router._game_route_states.update(routes_snapshot)


def _new_state(monkeypatch):
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {})
    return game_router._activate_game_route("soccer", "match_1", "Lan")


def _mark_game_started(state, elapsed_ms=12_000):
    state["game_started"] = True
    state["game_started_elapsed_ms"] = elapsed_ms
    state["game_started_at"] = game_router.time.time() - (elapsed_ms / 1000.0)
    return state


def _append_user_line(state, index):
    game_router._append_game_dialog(state, {
        "type": "user",
        "source": "external_text_route",
        "text": f"第 {index} 句",
    })


def _fake_success_result():
    return {
        "rollingSummary": "主人一直在追分，猫娘用轻松语气回应。",
        "signals": {
            "主人信号": [{
                "signalLabel": "主人在意能否追上比分",
                "summary": "主人多次提到追分。",
                "evidence": [{"id": "glog_0003", "quote": "第 3 句"}],
                "lastRound": 3,
                "count": 1,
            }],
            "关系互动信号": [],
            "猫娘信号": [],
            "比赛事实": [{
                "signalLabel": "官方比分按 finalScore 记录",
                "summary": "比分解释以状态为准。",
                "evidence": [{"id": "glog_0005", "quote": "第 5 句"}],
                "lastRound": 5,
                "count": 1,
            }],
            "口头声明": [],
        },
        "source": {"provider": "fake"},
    }


@pytest.mark.unit
def test_append_game_dialog_generates_stable_ids(monkeypatch):
    state = _new_state(monkeypatch)

    game_router._append_game_dialog(state, {"type": "user", "text": "先来一球"})
    game_router._append_game_dialog(state, {"id": "glog_0010", "type": "assistant", "line": "看我的"})
    game_router._append_game_dialog(state, {"type": "user", "text": "继续"})

    assert [item["id"] for item in state["game_dialog_log"]] == [
        "glog_0001",
        "glog_0010",
        "glog_0011",
    ]
    assert state["game_dialog_log"][1]["line"] == "看我的"
    assert state["game_dialog_seq"] == 11


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_organizer_triggers_at_15_and_keeps_recent_window(monkeypatch):
    state = _new_state(monkeypatch)
    snapshots = []

    async def fake_ai(_state, snapshot):
        snapshots.append(list(snapshot))
        return _fake_success_result()

    monkeypatch.setattr(game_router, "_run_game_context_organizer_ai", fake_ai)

    for index in range(1, 15):
        _append_user_line(state, index)
        assert "_game_context_organizer_task" not in state

    _append_user_line(state, 15)
    task = state["_game_context_organizer_task"]
    await task

    assert len(snapshots) == 1
    assert [item["id"] for item in snapshots[0]] == [f"glog_{index:04d}" for index in range(1, 16)]
    assert state["game_context_summary"] == "主人一直在追分，猫娘用轻松语气回应。"
    assert state["game_context_signals"]["主人信号"][0]["signalLabel"] == "主人在意能否追上比分"
    assert state["game_context_organizer"]["last_organized_id"] == "glog_0009"
    assert state["game_context_organizer"]["failure_count"] == 0
    assert state["game_context_recent_ids"] == [f"glog_{index:04d}" for index in range(10, 16)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_organizer_does_not_truncate_logs_added_while_running(monkeypatch):
    state = _new_state(monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_ai(_state, snapshot):
        started.set()
        await release.wait()
        return _fake_success_result()

    monkeypatch.setattr(game_router, "_run_game_context_organizer_ai", fake_ai)

    for index in range(1, 16):
        _append_user_line(state, index)
    task = state["_game_context_organizer_task"]
    await asyncio.wait_for(started.wait(), timeout=1.0)

    _append_user_line(state, 16)
    _append_user_line(state, 17)
    release.set()
    await task

    assert len(state["game_dialog_log"]) == 17
    assert state["game_context_organizer"]["last_organized_id"] == "glog_0009"
    assert state["game_context_recent_ids"] == [f"glog_{index:04d}" for index in range(12, 18)]
    assert [item["id"] for item in state["game_dialog_log"][-2:]] == ["glog_0016", "glog_0017"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_organizer_failure_keeps_window_until_new_logs(monkeypatch):
    state = _new_state(monkeypatch)

    async def fake_ai(_state, _snapshot):
        raise RuntimeError("organizer unavailable")

    monkeypatch.setattr(game_router, "_run_game_context_organizer_ai", fake_ai)

    for index in range(1, 16):
        _append_user_line(state, index)
    task = state["_game_context_organizer_task"]
    await task

    assert len(state["game_dialog_log"]) == 15
    assert state["game_context_summary"] == ""
    assert state["game_context_organizer"]["failure_count"] == 1
    assert state["game_context_organizer"]["degraded"] is False
    assert state["_game_context_organizer_task"] is task


@pytest.mark.unit
@pytest.mark.asyncio
async def test_context_organizer_degrades_at_40_pending_items(monkeypatch):
    state = _new_state(monkeypatch)
    calls = 0

    async def fake_ai(_state, _snapshot):
        nonlocal calls
        calls += 1
        raise RuntimeError("organizer unavailable")

    monkeypatch.setattr(game_router, "_run_game_context_organizer_ai", fake_ai)

    for index in range(1, 41):
        _append_user_line(state, index)
    await state["_game_context_organizer_task"]

    assert calls == 1
    assert state["game_context_organizer"]["degraded"] is True
    assert state["game_context_organizer"]["error"] == "degraded_after_40_pending_items"
    assert state["game_context_organizer"]["last_organized_id"] == ""

    _append_user_line(state, 41)
    assert calls == 1


@pytest.mark.unit
def test_degraded_context_does_not_schedule_organizer(monkeypatch):
    state = _new_state(monkeypatch)
    state["game_context_organizer"]["degraded"] = True

    for index in range(1, 16):
        _append_user_line(state, index)

    assert "_game_context_organizer_task" not in state
    assert state["game_context_organizer"]["running"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_finalize_waits_for_running_context_organizer_before_archive(monkeypatch):
    state = _mark_game_started(_new_state(monkeypatch))
    state["finalScore"] = {"player": 3, "ai": 6}
    started = asyncio.Event()
    release = asyncio.Event()
    submitted = []

    async def fake_ai(_state, _snapshot):
        started.set()
        await release.wait()
        return _fake_success_result()

    async def fake_submit(archive):
        submitted.append(archive)
        return {"ok": True, "status": "cached", "count": 1}

    monkeypatch.setattr(game_router, "_run_game_context_organizer_ai", fake_ai)
    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)

    for index in range(1, 16):
        _append_user_line(state, index)
    await asyncio.wait_for(started.wait(), timeout=1.0)
    for index in range(16, 31):
        _append_user_line(state, index)

    finalize_task = asyncio.create_task(
        game_router._finalize_game_route_state(state, reason="manual")
    )
    await asyncio.sleep(0)
    release.set()
    result = await finalize_task

    assert result["archive"]["game_context_summary"] == "主人一直在追分，猫娘用轻松语气回应。"
    assert result["archive"]["game_context_signals"]["主人信号"][0]["signalLabel"] == "主人在意能否追上比分"
    assert submitted[0]["game_context_summary"] == "主人一直在追分，猫娘用轻松语气回应。"
    assert state["game_route_active"] is False
    assert state["game_context_organizer"]["running"] is False
    assert state["game_context_organizer"]["last_organized_id"] == "glog_0009"
    assert state["_game_context_organizer_task"] is not None
    assert state["_game_context_organizer_task"].done()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_finalize_times_out_running_context_organizer_without_late_archive_mutation(monkeypatch):
    state = _mark_game_started(_new_state(monkeypatch))
    state["finalScore"] = {"player": 1, "ai": 4}
    started = asyncio.Event()
    submitted = []

    async def fake_ai(_state, _snapshot):
        started.set()
        await asyncio.Event().wait()
        return _fake_success_result()

    async def fake_submit(archive):
        submitted.append(archive)
        return {"ok": True, "status": "cached", "count": 1}

    monkeypatch.setattr(game_router, "_GAME_CONTEXT_FINALIZE_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(game_router, "_run_game_context_organizer_ai", fake_ai)
    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)

    for index in range(1, 16):
        _append_user_line(state, index)
    await asyncio.wait_for(started.wait(), timeout=1.0)

    result = await game_router._finalize_game_route_state(state, reason="manual")

    assert result["archive"]["game_context_summary"] == ""
    assert submitted[0]["game_context_summary"] == ""
    assert state["game_context_organizer"]["running"] is False
    assert state["game_context_organizer"]["error"] == "finalize_timeout"
    assert state["_game_context_organizer_task"].done()


@pytest.mark.unit
def test_game_prompt_orders_pregame_then_rolling_context(monkeypatch):
    recent_dialogues = [
        {"id": "glog_0010", "type": "user", "text": "我快追上了"},
        {"id": "glog_0011", "type": "assistant", "line": "那我也认真一点。"},
    ]
    prompt = game_router._build_game_prompt(
        "soccer",
        "Lan",
        "喜欢陪主人玩。",
        {"gameStance": "soft_teasing", "tonePolicy": "轻松逗主人。"},
        {
            "summary": "前半局猫娘领先，主人开始追分。",
            "signals": _fake_success_result()["signals"],
            "recent_dialogues": recent_dialogues,
            "degraded": False,
        },
    )

    assert prompt.index("开局上下文") < prompt.index("局内上下文整理")
    assert prompt.index("局内滚动摘要") < prompt.index("局内信号列表")
    assert prompt.index("局内信号列表") < prompt.index("最近原文窗口")
    assert prompt.index("最近原文窗口") < prompt.index("当前状态和当前事件")
    assert "主人在意能否追上比分" in prompt


@pytest.mark.unit
def test_degraded_game_prompt_excludes_summary_and_signals():
    prompt = game_router._build_game_prompt(
        "soccer",
        "Lan",
        "喜欢陪主人玩。",
        {"gameStance": "neutral_play"},
        {
            "summary": "不应进入 prompt 的摘要",
            "signals": {"主人信号": [{"signalLabel": "不应进入 prompt 的信号"}]},
            "recent_dialogues": [{"id": "glog_0040", "type": "user", "text": "继续踢"}],
            "degraded": True,
        },
    )

    assert "纯游戏模式" in prompt
    assert "局内滚动摘要" not in prompt
    assert "不应进入 prompt 的摘要" not in prompt
    assert "不应进入 prompt 的信号" not in prompt
    assert "glog_0040" in prompt


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refresh_game_session_instructions_rebuilds_prompt_from_context(monkeypatch):
    state = _new_state(monkeypatch)
    state["preGameContext"] = {"gameStance": "soft_teasing"}
    state["game_context_summary"] = "主人追分后猫娘放慢了节奏。"
    state["game_context_signals"] = _fake_success_result()["signals"]
    _append_user_line(state, 1)

    class FakeSession:
        def __init__(self):
            self.updates = []

        async def update_session(self, payload):
            self.updates.append(payload)

    fake_session = FakeSession()
    monkeypatch.setattr(game_router, "_get_current_character_info", lambda: {
        "lanlan_name": "Lan",
        "master_name": "主人",
        "lanlan_prompt": "喜欢陪主人玩。",
    })

    entry = {"session": fake_session, "instructions": "stale instructions"}
    await game_router._refresh_game_session_instructions(entry, "soccer", "match_1")

    assert len(fake_session.updates) == 1
    assert "开局上下文" in fake_session.updates[0]["instructions"]
    assert "局内滚动摘要：主人追分后猫娘放慢了节奏。" in fake_session.updates[0]["instructions"]
    assert "主人在意能否追上比分" in fake_session.updates[0]["instructions"]
    assert entry["instructions"] == fake_session.updates[0]["instructions"]


@pytest.mark.unit
def test_archive_includes_context_summary_and_grouped_signals(monkeypatch):
    state = _new_state(monkeypatch)
    state["finalScore"] = {"player": 3, "ai": 6}
    state["game_context_summary"] = "猫娘领先后放慢节奏，主人继续追分。"
    state["game_context_signals"] = _fake_success_result()["signals"]
    state["game_context_organizer"]["source"] = {"provider": "fake"}
    _append_user_line(state, 1)

    archive = game_router._build_game_archive(state)
    memory_text = game_router._build_game_archive_memory_summary_text(archive)
    highlight_source = game_router._build_game_archive_memory_highlight_source(archive)

    assert archive["game_context_summary"] == "猫娘领先后放慢节奏，主人继续追分。"
    assert archive["game_context_signals"]["主人信号"][0]["signalLabel"] == "主人在意能否追上比分"
    assert archive["game_context_degraded"] is False
    assert "局内滚动摘要：猫娘领先后放慢节奏，主人继续追分。" in memory_text
    assert "局内中文分组信号" in memory_text
    assert "主人在意能否追上比分" in highlight_source
    assert "筛选优先级" in highlight_source


@pytest.mark.unit
@pytest.mark.asyncio
async def test_degraded_archive_uses_minimal_memory_facts(monkeypatch):
    state = _new_state(monkeypatch)
    state["finalScore"] = {"player": 1, "ai": 9}
    state["game_context_summary"] = "不可靠关系摘要"
    state["game_context_signals"] = {"关系互动信号": [{"signalLabel": "不可靠关系信号"}]}
    state["game_context_organizer"]["degraded"] = True
    _append_user_line(state, 1)
    game_router._append_game_dialog(state, {
        "type": "game_event",
        "kind": "goal-scored",
        "result_line": "算你赢啦。",
    })

    archive = game_router._build_game_archive(state)

    async def fake_select(_archive):
        raise AssertionError("degraded archive should not call highlight LLM")

    monkeypatch.setattr(game_router, "_select_game_archive_memory_highlights", fake_select)
    highlights = await game_router._ensure_game_archive_memory_highlights(archive)
    memory_text = game_router._build_game_archive_memory_summary_text(archive)
    messages = game_router._build_game_archive_memory_messages(archive)

    assert highlights["source"]["method"] == "degraded_minimal_facts"
    assert "局内上下文整理已降级为纯游戏模式" in memory_text
    assert "官方比分：主人 1 : 9 Lan。" in memory_text
    assert "不可靠关系摘要" not in memory_text
    assert "不可靠关系信号" not in memory_text
    assert "口头让步规则" in memory_text
    assert [message["role"] for message in messages] == ["system"]
    assert "算你赢啦" not in messages[0]["content"][0]["text"]
