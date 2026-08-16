from __future__ import annotations

from _galgame_bridge_support import *


def _capsule_shared(
    *,
    events: list[dict[str, object]],
    lines: list[dict[str, object]],
    scene_id: str = "scene-a",
    session_id: str = "sess-a",
) -> dict[str, object]:
    latest = lines[-1] if lines else {}
    return _shared_state(
        mode="companion",
        session_id=session_id,
        last_seq=max((int(item.get("seq") or 0) for item in events), default=0),
        snapshot=_session_state(
            speaker=str(latest.get("speaker") or ""),
            text=str(latest.get("text") or ""),
            scene_id=scene_id,
            line_id=str(latest.get("line_id") or ""),
            ts=str(latest.get("ts") or "2026-04-21T08:35:00Z"),
        ),
        history_events=events,
        history_lines=lines,
    )


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_first_stable_event_submits_capsule_without_game_llm(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    gateway = _FakeLLMGateway()
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=gateway,
        host_adapter=_FakeHostAdapter(),
    )
    line = _summary_test_line("scene-a", 1)
    shared = _capsule_shared(
        events=[_summary_test_line_event("scene-a", 1, seq=1)],
        lines=[line],
    )

    await agent.tick(shared)
    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert gateway.summarize_calls == []
    assert len(ctx.pushed_messages) == 1
    pushed = ctx.pushed_messages[0]
    assert pushed["metadata"]["kind"] == "scene_delta"
    assert pushed["metadata"]["new_stable_line_count"] == 1
    assert str(line["text"]) in str(pushed["content"])


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_choices_shown_event_submits_complete_menu_atomically(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    choices = [
        {"choice_id": "choice-a", "text": "追上去", "index": 0},
        {"choice_id": "choice-b", "text": "留在原地", "index": 1},
        {"choice_id": "choice-c", "text": "先观察四周", "index": 2},
    ]
    event = _event(
        seq=1,
        event_type="choices_shown",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:01Z",
        payload={
            "scene_id": "scene-a",
            "route_id": "",
            "choices": choices,
        },
    )
    shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        last_seq=1,
        snapshot=_session_state(
            scene_id="scene-a",
            choices=choices,
            is_menu_open=True,
        ),
        history_events=[event],
    )

    await agent.tick(shared)
    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.pushed_messages) == 1
    pushed = ctx.pushed_messages[0]
    assert pushed["metadata"]["new_choice_count"] == 3
    response_target = str(pushed["content"]).split("本次回应对象：", 1)[-1]
    assert all(choice["text"] in response_target for choice in choices)

    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == 1


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_eight_lines_update_memory_without_summary_notification(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    gateway = _FakeLLMGateway()
    plugin = GalgameBridgePlugin(ctx)
    agent = GameLLMAgent(
        plugin=plugin,
        logger=_Logger(),
        llm_gateway=gateway,
        host_adapter=_FakeHostAdapter(),
    )
    lines = [_summary_test_line("scene-a", index) for index in range(1, 9)]
    events = [
        _summary_test_line_event("scene-a", index, seq=index)
        for index in range(1, 9)
    ]
    shared = _capsule_shared(events=events, lines=lines)

    await agent.tick(shared)
    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(gateway.summarize_calls) == 1
    assert [item["metadata"]["kind"] for item in ctx.pushed_messages] == [
        "scene_delta"
    ]
    assert "scene-a" in plugin._story_so_far
    assert any(
        item.get("scene_id") == "scene-a" and item.get("summary")
        for item in agent._scene_memory
    )


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_same_seq_is_suppressed_but_same_text_with_new_seq_is_allowed(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    first_line = _summary_test_line("scene-a", 1)
    first_event = _summary_test_line_event("scene-a", 1, seq=1)
    first = _capsule_shared(events=[first_event], lines=[first_line])

    await agent.tick(first)
    await agent.tick(first)
    await agent.drain_summary_tasks(timeout=1.0)
    await agent.tick(first)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == 1

    repeated_line = {**first_line, "ts": "2026-04-21T08:35:02Z"}
    repeated_event = {
        **first_event,
        "seq": 2,
        "ts": "2026-04-21T08:35:02Z",
    }
    second = _capsule_shared(
        events=[first_event, repeated_event],
        lines=[first_line, repeated_line],
    )
    await agent.tick(second)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.pushed_messages) == 2
    assert ctx.pushed_messages[-1]["metadata"]["kind"] == "scene_delta"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_tentative_ocr_never_enters_capsule(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    tentative_text = "TENTATIVE_PRIVATE_OCR"
    event = _event(
        seq=1,
        event_type="line_observed",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:01Z",
        payload={
            "speaker": "Yukino",
            "text": tentative_text,
            "line_id": "tentative-1",
            "scene_id": "scene-a",
            "route_id": "",
            "stability": "tentative",
        },
    )
    shared = _shared_state(
        mode="companion",
        snapshot=_session_state(scene_id="scene-a"),
        history_events=[event],
        history_lines=[],
        history_observed_lines=[event["payload"]],
    )

    await agent.tick(shared)
    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert ctx.pushed_messages == []


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_game_llm_old_summary_text_never_reaches_cat_capsule(tmp_path: Path) -> None:
    old_summary = "丛雨的眼神好犀利"
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    gateway = _FakeLLMGateway(
        summarize_payload={
            "degraded": False,
            "summary": old_summary,
            "key_points": [],
            "diagnostic": "",
        }
    )
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=gateway,
        host_adapter=_FakeHostAdapter(),
    )
    lines = [_summary_test_line("scene-a", index) for index in range(1, 9)]
    events = [
        _summary_test_line_event("scene-a", index, seq=index)
        for index in range(1, 9)
    ]
    shared = _capsule_shared(events=events, lines=lines)

    await agent.tick(shared)
    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(gateway.summarize_calls) == 1
    assert all(old_summary not in str(item) for item in ctx.pushed_messages)


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_trusted_memory_to_ocr_handoff_skips_overlap_and_pushes_new_suffix(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    memory_line = {
        **_summary_test_line("memory-scene", 1),
        "text": "same handoff line",
    }
    memory_event = _event(
        seq=1,
        event_type="line_changed",
        session_id="memory-session",
        game_id="demo.alpha",
        ts=str(memory_line["ts"]),
        payload={**memory_line, "stability": "stable"},
    )
    memory_shared = _shared_state(
        mode="companion",
        game_id="demo.alpha",
        session_id="memory-session",
        active_data_source=DATA_SOURCE_MEMORY_READER,
        snapshot=_session_state(
            text="same handoff line",
            scene_id="memory-scene",
            line_id="memory-line-1",
        ),
        history_events=[memory_event],
        history_lines=[memory_line],
    )
    await agent.tick(memory_shared)
    await agent.tick(memory_shared)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == 1
    first_key = ctx.pushed_messages[0]["coalesce_key"]

    ocr_overlap = {
        **_summary_test_line("ocr-scene", 1),
        "line_id": "ocr-line-a",
        "text": "same handoff line",
    }
    ocr_event = _event(
        seq=1,
        event_type="line_changed",
        session_id="ocr-session",
        game_id="demo.alpha",
        ts=str(ocr_overlap["ts"]),
        payload={**ocr_overlap, "stability": "stable"},
    )
    ocr_shared = _shared_state(
        mode="companion",
        game_id="demo.alpha",
        session_id="ocr-session",
        active_data_source=DATA_SOURCE_OCR_READER,
        ocr_reader_runtime={
            "effective_process_name": "demo.exe",
            "effective_window_title": "Demo",
            "target_hwnd": 100,
            "target_window_visible": True,
        },
        snapshot=_session_state(
            text="same handoff line",
            scene_id="ocr-scene",
            line_id="ocr-line-a",
        ),
        history_events=[ocr_event],
        history_lines=[ocr_overlap],
    )
    await agent.tick(ocr_shared)
    await agent.tick(ocr_shared)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == 1

    new_line = {
        **_summary_test_line("ocr-scene", 2),
        "line_id": "ocr-line-b",
        "text": "genuinely new suffix",
    }
    new_event = _event(
        seq=2,
        event_type="line_changed",
        session_id="ocr-session",
        game_id="demo.alpha",
        ts=str(new_line["ts"]),
        payload={**new_line, "stability": "stable"},
    )
    ocr_shared["history_events"] = [ocr_event, new_event]
    ocr_shared["history_lines"] = [ocr_overlap, new_line]
    ocr_shared["latest_snapshot"] = _session_state(
        text="genuinely new suffix",
        scene_id="ocr-scene",
        line_id="ocr-line-b",
        ts=str(new_line["ts"]),
    )
    await agent.tick(ocr_shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.pushed_messages) == 2
    assert ctx.pushed_messages[-1]["coalesce_key"] == first_key
    assert "genuinely new suffix" in str(ctx.pushed_messages[-1]["content"])
    assert "same handoff line" not in str(ctx.pushed_messages[-1]["content"]).split(
        "本次回应对象：", 1
    )[-1]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_real_game_reset_allows_same_text_again(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )

    async def _run_game(game_id: str, session_id: str) -> None:
        line = {**_summary_test_line("scene-a", 1), "text": "same reset line"}
        event = _event(
            seq=1,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            ts=str(line["ts"]),
            payload={**line, "stability": "stable"},
        )
        shared = _shared_state(
            mode="companion",
            game_id=game_id,
            session_id=session_id,
            snapshot=_session_state(
                text="same reset line",
                scene_id="scene-a",
                line_id="scene-a-line-1",
            ),
            history_events=[event],
            history_lines=[line],
        )
        await agent.tick(shared)
        await agent.tick(shared)
        await agent.drain_summary_tasks(timeout=1.0)

    await _run_game("demo.alpha", "session-a")
    await _run_game("demo.beta", "session-b")

    assert len(ctx.pushed_messages) == 2


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_submitted_false_keeps_capsule_retryable(tmp_path: Path) -> None:
    class _ReceiptCtx(_Ctx):
        def __init__(self, plugin_dir: Path, effective_config: dict[str, object]) -> None:
            super().__init__(plugin_dir, effective_config)
            self.receipts = iter(
                (
                    {"submitted": False, "reason": "backpressure"},
                    {"submitted": False, "reason": "backpressure"},
                    {"submitted": True},
                )
            )
            self.attempt_count = 0

        def push_message(self, **kwargs):
            self.attempt_count += 1
            receipt = next(self.receipts)
            if receipt["submitted"]:
                self.pushed_messages.append(dict(kwargs))
            return receipt

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _ReceiptCtx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    line = _summary_test_line("scene-a", 1)
    shared = _capsule_shared(
        events=[_summary_test_line_event("scene-a", 1, seq=1)],
        lines=[line],
    )

    await agent.tick(shared)
    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=2.0)
    assert ctx.attempt_count == 2
    assert ctx.pushed_messages == []
    assert all(
        not list(item.get("committed_event_keys") or [])
        for item in agent._scene_capsule_delivery_ledger.values()
    )

    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)
    assert ctx.attempt_count == 3
    assert len(ctx.pushed_messages) == 1
    assert any(
        list(item.get("committed_event_keys") or [])
        for item in agent._scene_capsule_delivery_ledger.values()
    )


@pytest.mark.asyncio
@pytest.mark.plugin_unit
@pytest.mark.parametrize("repeat_guard_enabled", [True, False])
async def test_new_capsule_cancels_old_retry_and_commits_superseded_events(
    tmp_path: Path,
    repeat_guard_enabled: bool,
) -> None:
    class _RaceCtx(_Ctx):
        def __init__(self, plugin_dir: Path, effective_config: dict[str, object]) -> None:
            super().__init__(plugin_dir, effective_config)
            self.first_attempted = asyncio.Event()
            self.attempted_contents: list[str] = []

        def push_message(self, **kwargs):
            content = str(kwargs.get("content") or "")
            self.attempted_contents.append(content)
            if len(self.attempted_contents) == 1:
                self.first_attempted.set()
                return {"submitted": False, "reason": "backpressure"}
            self.pushed_messages.append(dict(kwargs))
            return {"submitted": True}

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _RaceCtx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
        config=SimpleNamespace(
            scene_summary_repeat_guard_enabled=repeat_guard_enabled,
        ),
    )
    first_line = _summary_test_line("scene-a", 1)
    first_event = _summary_test_line_event("scene-a", 1, seq=1)
    first = _capsule_shared(events=[first_event], lines=[first_line])
    await agent.tick(first)
    await agent.tick(first)
    await asyncio.wait_for(ctx.first_attempted.wait(), timeout=0.5)

    second_line = _summary_test_line("scene-a", 2)
    second_event = _summary_test_line_event("scene-a", 2, seq=2)
    second = _capsule_shared(
        events=[first_event, second_event],
        lines=[first_line, second_line],
    )
    await agent.tick(second)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.attempted_contents) == 2
    assert len(ctx.pushed_messages) == 1
    response_target = str(ctx.pushed_messages[0]["content"]).split(
        "本次回应对象：", 1
    )[-1]
    assert str(second_line["text"]) in response_target
    assert str(first_line["text"]) not in response_target

    await agent.tick(second)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == (1 if repeat_guard_enabled else 2)


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_newer_memory_summary_wins_when_old_llm_finishes_last(
    tmp_path: Path,
) -> None:
    class _OutOfOrderGateway(_FakeLLMGateway):
        def __init__(self) -> None:
            super().__init__()
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()
            self.call_count = 0

        async def summarize_scene(self, context):
            self.call_count += 1
            call_number = self.call_count
            self.summarize_calls.append(dict(context))
            if call_number == 1:
                self.first_started.set()
                await self.release_first.wait()
                summary = "older memory summary"
            else:
                summary = "newer memory summary"
            return {
                "degraded": False,
                "summary": summary,
                "key_points": [],
                "diagnostic": "",
            }

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    gateway = _OutOfOrderGateway()
    plugin = GalgameBridgePlugin(ctx)
    agent = GameLLMAgent(
        plugin=plugin,
        logger=_Logger(),
        llm_gateway=gateway,
        host_adapter=_FakeHostAdapter(),
    )
    lines = [_summary_test_line("scene-a", 1)]
    shared = _shared_state(
        mode="companion",
        snapshot=_session_state(
            text=str(lines[-1]["text"]),
            scene_id="scene-a",
            line_id=str(lines[-1]["line_id"]),
        ),
        history_lines=lines,
    )
    await agent.tick(shared)
    context = build_summarize_context(
        shared,
        scene_id="scene-a",
        config=agent._context_config,
    )
    agent._schedule_scene_summary_task(
        shared=shared,
        session_id="sess-a",
        scene_id="scene-a",
        route_id="",
        snapshot=shared["latest_snapshot"],
        context=context,
        trigger="line_count",
        metadata={"scheduled_from_event_seq": 1},
        update_scene_memory=True,
    )
    await asyncio.wait_for(gateway.first_started.wait(), timeout=0.5)
    agent._schedule_scene_summary_task(
        shared=shared,
        session_id="sess-a",
        scene_id="scene-a",
        route_id="",
        snapshot=shared["latest_snapshot"],
        context=context,
        trigger="line_count",
        metadata={"scheduled_from_event_seq": 2},
        update_scene_memory=True,
    )
    await asyncio.sleep(0)
    gateway.release_first.set()
    await agent.drain_summary_tasks(timeout=1.0)

    scene_memory = [
        item for item in agent._scene_memory if item.get("scene_id") == "scene-a"
    ]
    assert scene_memory[-1]["summary"] == "newer memory summary"
    assert "newer memory summary" in plugin._story_so_far
    assert "older memory summary" not in plugin._story_so_far
    assert all(
        item["metadata"]["kind"] == "scene_delta"
        for item in ctx.pushed_messages
    )


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_superseded_reservation_stays_owned_during_new_retry(
    tmp_path: Path,
) -> None:
    class _TwoFailureRaceCtx(_Ctx):
        def __init__(self, plugin_dir: Path, effective_config: dict[str, object]) -> None:
            super().__init__(plugin_dir, effective_config)
            self.first_attempted = asyncio.Event()
            self.second_attempted = asyncio.Event()
            self.attempted_contents: list[str] = []

        def push_message(self, **kwargs):
            self.attempted_contents.append(str(kwargs.get("content") or ""))
            attempt = len(self.attempted_contents)
            if attempt == 1:
                self.first_attempted.set()
            if attempt == 2:
                self.second_attempted.set()
            if attempt <= 2:
                return {"submitted": False, "reason": "backpressure"}
            self.pushed_messages.append(dict(kwargs))
            return {"submitted": True}

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _TwoFailureRaceCtx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    first_line = _summary_test_line("scene-a", 1)
    first_event = _summary_test_line_event("scene-a", 1, seq=1)
    first = _capsule_shared(events=[first_event], lines=[first_line])
    await agent.tick(first)
    await agent.tick(first)
    await asyncio.wait_for(ctx.first_attempted.wait(), timeout=0.5)

    second_line = _summary_test_line("scene-a", 2)
    second_event = _summary_test_line_event("scene-a", 2, seq=2)
    second = _capsule_shared(
        events=[first_event, second_event],
        lines=[first_line, second_line],
    )
    await agent.tick(second)
    await asyncio.wait_for(ctx.second_attempted.wait(), timeout=0.5)
    await asyncio.sleep(0)

    observed_order = agent._scene_summary_latest_observed_order
    await agent.tick(second)
    assert agent._scene_summary_latest_observed_order == observed_order
    assert len(agent._scene_capsule_tasks) == 1

    await agent.drain_summary_tasks(timeout=2.0)
    assert len(ctx.attempted_contents) == 3
    assert len(ctx.pushed_messages) == 1
    response_target = str(ctx.pushed_messages[0]["content"]).split(
        "本次回应对象：", 1
    )[-1]
    assert str(second_line["text"]) in response_target
    assert str(first_line["text"]) not in response_target


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_no_seq_history_window_slide_does_not_replay_old_tail(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    first_line = _summary_test_line("scene-a", 1)
    second_line = _summary_test_line("scene-a", 2)
    initial = _capsule_shared(events=[], lines=[first_line, second_line])
    await agent.tick(initial)
    await agent.tick(initial)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == 1

    slid_without_delta = _capsule_shared(events=[], lines=[second_line])
    await agent.tick(slid_without_delta)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == 1

    third_line = _summary_test_line("scene-a", 3)
    slid_with_delta = _capsule_shared(
        events=[],
        lines=[second_line, third_line],
    )
    await agent.tick(slid_with_delta)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == 2
    response_target = str(ctx.pushed_messages[-1]["content"]).split(
        "本次回应对象：", 1
    )[-1]
    assert str(third_line["text"]) in response_target
    assert str(second_line["text"]) not in response_target


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_source_handoff_without_game_id_uses_matching_window_identity(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    memory_line = {
        **_summary_test_line("memory-scene", 1),
        "text": "same no-game-id line",
    }
    memory_shared = _shared_state(
        mode="companion",
        game_id="",
        session_id="memory-session",
        active_data_source=DATA_SOURCE_MEMORY_READER,
        snapshot=_session_state(
            text=str(memory_line["text"]),
            scene_id="memory-scene",
            line_id=str(memory_line["line_id"]),
        ),
        history_lines=[memory_line],
    )
    memory_shared["active_session_meta"] = {
        "metadata": {
            "game_process_name": "demo.exe",
            "game_pid": 4242,
            "window_title": "Demo Window",
        }
    }
    await agent.tick(memory_shared)
    await agent.tick(memory_shared)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == 1

    ocr_line = {
        **_summary_test_line("ocr-scene", 1),
        "line_id": "ocr-line-1",
        "text": "same no-game-id line",
    }
    ocr_shared = _shared_state(
        mode="companion",
        game_id="",
        session_id="ocr-session",
        active_data_source=DATA_SOURCE_OCR_READER,
        ocr_reader_runtime={
            "effective_process_name": "DEMO.EXE",
            "effective_window_title": "Demo Window",
            "pid": 4242,
            "target_hwnd": 100,
            "target_window_visible": True,
        },
        snapshot=_session_state(
            text=str(ocr_line["text"]),
            scene_id="ocr-scene",
            line_id=str(ocr_line["line_id"]),
        ),
        history_lines=[ocr_line],
    )
    await agent.tick(ocr_shared)
    await agent.tick(ocr_shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.pushed_messages) == 1
    memory_return = dict(memory_shared)
    memory_return["active_session_id"] = "memory-session-2"
    await agent.tick(memory_return)
    await agent.tick(memory_return)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.pushed_messages) == 1
    assert agent._last_session_transition_type == "real_session_reset"
    assert agent._scene_capsule_delivery_ledger


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_same_seq_stable_correction_retires_pending_event_version(
    tmp_path: Path,
) -> None:
    class _CorrectionCtx(_Ctx):
        def __init__(self, plugin_dir: Path, effective_config: dict[str, object]) -> None:
            super().__init__(plugin_dir, effective_config)
            self.first_attempted = asyncio.Event()
            self.attempted_contents: list[str] = []

        def push_message(self, **kwargs):
            content = str(kwargs.get("content") or "")
            self.attempted_contents.append(content)
            if len(self.attempted_contents) == 1:
                self.first_attempted.set()
                return {"submitted": False, "reason": "backpressure"}
            self.pushed_messages.append(dict(kwargs))
            return {"submitted": True}

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _CorrectionCtx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    original_line = {**_summary_test_line("scene-a", 1), "text": "old OCR text"}
    original_event = _event(
        seq=1,
        event_type="line_changed",
        session_id="sess-a",
        game_id="demo.alpha",
        ts=str(original_line["ts"]),
        payload={**original_line, "stability": "stable"},
    )
    await agent.tick(_capsule_shared(events=[original_event], lines=[original_line]))
    await asyncio.wait_for(ctx.first_attempted.wait(), timeout=0.5)

    corrected_line = {**original_line, "text": "corrected stable text"}
    corrected_event = {
        **original_event,
        "payload": {**corrected_line, "stability": "stable"},
    }
    await agent.tick(
        _capsule_shared(events=[corrected_event], lines=[corrected_line])
    )
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.pushed_messages) == 1
    assert "corrected stable text" in str(ctx.pushed_messages[0]["content"])
    assert "old OCR text" not in str(ctx.pushed_messages[0]["content"])


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_tentative_observation_retires_pending_capsule_with_guard_disabled(
    tmp_path: Path,
) -> None:
    class _TentativeCtx(_Ctx):
        def __init__(self, plugin_dir: Path, effective_config: dict[str, object]) -> None:
            super().__init__(plugin_dir, effective_config)
            self.first_attempted = asyncio.Event()
            self.attempt_count = 0

        def push_message(self, **kwargs):
            self.attempt_count += 1
            self.first_attempted.set()
            return {"submitted": False, "reason": "backpressure"}

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _TentativeCtx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
        config=SimpleNamespace(
            scene_summary_repeat_guard_enabled=False,
        ),
    )
    stable_line = _summary_test_line("scene-a", 1)
    stable_event = _summary_test_line_event("scene-a", 1, seq=1)
    await agent.tick(_capsule_shared(events=[stable_event], lines=[stable_line]))
    await asyncio.wait_for(ctx.first_attempted.wait(), timeout=0.5)

    tentative_event = _event(
        seq=2,
        event_type="line_observed",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:02Z",
        payload={
            "speaker": "Yukino",
            "text": "tentative replacement",
            "line_id": "tentative-2",
            "scene_id": "scene-a",
            "route_id": "",
            "stability": "tentative",
            "confidence": 0.51,
        },
    )
    tentative_shared = _capsule_shared(
        events=[stable_event, tentative_event],
        lines=[stable_line],
    )
    tentative_shared["history_observed_lines"] = [tentative_event["payload"]]
    await agent.tick(tentative_shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert ctx.attempt_count == 1
    assert ctx.pushed_messages == []


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_history_event_window_slide_keeps_pending_capsule_identity(
    tmp_path: Path,
) -> None:
    class _PendingCtx(_Ctx):
        def __init__(self, plugin_dir: Path, effective_config: dict[str, object]) -> None:
            super().__init__(plugin_dir, effective_config)
            self.first_attempted = asyncio.Event()

        def push_message(self, **kwargs):
            self.first_attempted.set()
            return {"submitted": False, "reason": "backpressure"}

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _PendingCtx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    line = _summary_test_line("scene-a", 1)
    event = _summary_test_line_event("scene-a", 1, seq=1)
    await agent.tick(_capsule_shared(events=[event], lines=[line]))
    await asyncio.wait_for(ctx.first_attempted.wait(), timeout=0.5)
    scheduled_order = agent._scene_summary_latest_observed_order

    await agent.tick(_capsule_shared(events=[], lines=[line]))

    assert agent._scene_summary_latest_observed_order == scheduled_order
    assert len(agent._scene_capsule_tasks) == 1
    assert ctx.pushed_messages == []


@pytest.mark.plugin_unit
def test_capsule_marker_ignores_poll_noise_and_history_window_slide(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(
            _Ctx(plugin_dir, _make_effective_config(bridge_root))
        ),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    first = _summary_test_line_event("scene-a", 1, seq=1)
    latest = _summary_test_line_event("scene-a", 2, seq=2)
    shared = _capsule_shared(
        events=[first, latest],
        lines=[_summary_test_line("scene-a", 1), _summary_test_line("scene-a", 2)],
    )
    snapshot = shared["latest_snapshot"]
    boundary_key = agent._scene_capsule_boundary_key(shared, session_id="sess-a")
    marker = agent._build_scene_capsule_input_marker(
        shared,
        snapshot=snapshot,
        boundary_key=boundary_key,
        scene_id="scene-a",
        route_id="",
    )

    noisy_latest = {
        **latest,
        "ts": "2099-01-01T00:00:00Z",
        "payload": {**latest["payload"], "confidence": 0.01},
    }
    screen_event = _event(
        seq=3,
        event_type="screen_classified",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2099-01-01T00:00:01Z",
        payload={"screen_type": "dialogue", "confidence": 0.02},
    )
    slid = _capsule_shared(
        events=[noisy_latest, screen_event],
        lines=[_summary_test_line("scene-a", 2)],
    )
    assert agent._build_scene_capsule_input_marker(
        slid,
        snapshot=slid["latest_snapshot"],
        boundary_key=boundary_key,
        scene_id="scene-a",
        route_id="",
    ) == marker


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_memory_completion_ignores_unrelated_start_generation_change(
    tmp_path: Path,
) -> None:
    class _BlockingGateway(_FakeLLMGateway):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def summarize_scene(self, context):
            self.started.set()
            await self.release.wait()
            return {
                "degraded": False,
                "summary": "memory survives runtime reset",
                "key_points": [],
                "diagnostic": "",
            }

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    gateway = _BlockingGateway()
    plugin = GalgameBridgePlugin(ctx)
    agent = GameLLMAgent(
        plugin=plugin,
        logger=_Logger(),
        llm_gateway=gateway,
        host_adapter=_FakeHostAdapter(),
    )
    line = _summary_test_line("scene-a", 1)
    shared = _capsule_shared(events=[], lines=[line])
    await agent.tick(shared)
    context = build_summarize_context(
        shared,
        scene_id="scene-a",
        config=agent._context_config,
    )
    agent._schedule_scene_summary_task(
        shared=shared,
        session_id="sess-a",
        scene_id="scene-a",
        route_id="",
        snapshot=shared["latest_snapshot"],
        context=context,
        trigger="line_count",
        metadata={"scheduled_from_event_seq": 1},
        update_scene_memory=True,
    )
    await asyncio.wait_for(gateway.started.wait(), timeout=0.5)
    agent._start_generation += 1
    gateway.release.set()
    await agent.drain_summary_tasks(timeout=1.0)

    assert "memory survives runtime reset" in plugin._story_so_far
