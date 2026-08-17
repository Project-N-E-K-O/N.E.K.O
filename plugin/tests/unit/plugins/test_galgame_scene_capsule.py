from __future__ import annotations

from _galgame_bridge_support import *
from plugin.plugins.galgame_plugin.agent_scene_tracker import AgentSceneTracker


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


@pytest.mark.plugin_unit
def test_sequence_less_delivery_key_uses_occurrence_high_water() -> None:
    first = GameLLMAgent._summary_delivery_key(
        scene_id="scene-a",
        route_id="",
        stable_line_count=16,
        last_line_occurrence_key="private-occurrence-a",
    )
    second = GameLLMAgent._summary_delivery_key(
        scene_id="scene-a",
        route_id="",
        stable_line_count=16,
        last_line_occurrence_key="private-occurrence-b",
    )

    assert first != second
    assert "private-occurrence" not in first
    assert "private-occurrence" not in second


@pytest.mark.plugin_unit
def test_scene_tracker_retains_all_pending_scopes_above_soft_limit() -> None:
    tracker = AgentSceneTracker(seen_line_limit=64)
    tracker.sync_current_scene_summary_mirror("scene-a", route_id="current")

    for index in range(33):
        assert tracker.remember_scene_line(
            "scene-a",
            f"event-{index}",
            route_id=f"route-{index}",
            seq=0,
            ts=f"2026-04-21T08:35:{index:02d}Z",
        )

    pending_states = [
        state
        for state in tracker.summary_scene_states.values()
        if int(state.get("lines_since_push") or 0) > 0
    ]
    assert len(pending_states) == 33
    assert {str(state.get("route_id") or "") for state in pending_states} == {
        f"route-{index}" for index in range(33)
    }


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_same_session_id_across_games_uses_distinct_boundary_and_resets_memory(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    alpha = _shared_state(
        mode="companion",
        push_notifications=False,
        game_id="demo.alpha",
        session_id="shared-session",
        snapshot=_session_state(scene_id="scene-alpha"),
    )
    await agent.tick(alpha)
    await agent.tick(alpha)
    alpha_boundary = agent._scene_capsule_boundary_key(
        alpha,
        session_id="shared-session",
    )
    agent._scene_memory[:] = [
        {
            "scene_id": "scene-alpha",
            "route_id": "",
            "summary": "alpha-only memory",
        }
    ]
    beta = _shared_state(
        mode="companion",
        push_notifications=False,
        game_id="demo.beta",
        session_id="shared-session",
        snapshot=_session_state(scene_id="scene-beta"),
    )

    await agent.tick(beta)
    beta_boundary = agent._scene_capsule_boundary_key(
        beta,
        session_id="shared-session",
    )

    assert beta_boundary != alpha_boundary
    assert agent._last_session_transition_type == "real_session_reset"
    assert agent._scene_memory == []


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
async def test_capsule_ignores_reader_private_binary_shared_state(
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
    line = _summary_test_line("scene-a", 1)
    shared = _capsule_shared(
        events=[_summary_test_line_event("scene-a", 1, seq=1)],
        lines=[line],
    )
    shared["reader_private_transport"] = b"\x00\xff"

    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.pushed_messages) == 1
    assert str(line["text"]) in str(ctx.pushed_messages[0]["content"])


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_sequence_less_line_event_keeps_identity_when_prefix_is_trimmed(
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
    line = _summary_test_line("scene-a", 1)
    line_event = _summary_test_line_event("scene-a", 1, seq=0)
    prefix_event = _event(
        seq=0,
        event_type="screen_classified",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:34:59Z",
        payload={"screen_type": "dialogue"},
    )
    shared = _capsule_shared(
        events=[prefix_event, line_event],
        lines=[line],
    )

    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == 1

    shared["history_events"] = [line_event]
    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.pushed_messages) == 1


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_sequence_less_choice_event_keeps_identity_when_prefix_is_trimmed(
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
    choices = [
        {"choice_id": "choice-a", "text": "追上去", "index": 0},
        {"choice_id": "choice-b", "text": "留在原地", "index": 1},
    ]
    choice_event = _event(
        seq=0,
        event_type="choices_shown",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:01Z",
        payload={"scene_id": "scene-a", "choices": choices},
    )
    prefix_event = _event(
        seq=0,
        event_type="screen_classified",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:00Z",
        payload={"screen_type": "dialogue"},
    )
    shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        last_seq=0,
        snapshot=_session_state(
            scene_id="scene-a",
            choices=choices,
            is_menu_open=True,
        ),
        history_events=[prefix_event, choice_event],
    )

    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == 1

    shared["history_events"] = [choice_event]
    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.pushed_messages) == 1


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_later_sequence_less_history_event_wins_over_older_sequenced_event(
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
    old_line = _summary_test_line("scene-a", 1)
    old_line_event = _summary_test_line_event("scene-a", 1, seq=9)
    latest_choice_text = "追上最新的脚步"
    latest_choice = {
        "choice_id": "choice-latest",
        "text": latest_choice_text,
        "index": 0,
    }
    latest_choice_event = _event(
        seq=0,
        event_type="choices_shown",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="",
        payload={"scene_id": "scene-a", "choices": [latest_choice]},
    )
    shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        last_seq=9,
        snapshot=_session_state(
            scene_id="scene-a",
            choices=[latest_choice],
            is_menu_open=True,
        ),
        history_events=[old_line_event, latest_choice_event],
        history_lines=[old_line],
    )

    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.pushed_messages) == 1
    response_target = str(ctx.pushed_messages[0]["content"]).split(
        "本次回应对象：", 1
    )[-1]
    assert latest_choice_text in response_target
    assert str(old_line["text"]) not in response_target


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_scene_delta_includes_fixed_character_anchor(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    agent = GameLLMAgent(
        plugin=plugin,
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    with plugin._state_lock:
        plugin._state.character_mode = "fixed"
        plugin._state.character_fixed_name = "Murasame"
        plugin._state.character_profile_game_id = "senren_banka"
        plugin._state.character_profiles = {
            "Murasame": {
                "identity": "A guarded blade spirit",
                "background": ["sealed for centuries"],
            }
        }
    line = _summary_test_line("scene-a", 1)
    shared = _capsule_shared(
        events=[_summary_test_line_event("scene-a", 1, seq=1)],
        lines=[line],
    )

    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    pushed_content = str(ctx.pushed_messages[0]["content"])
    assert "======[角色身份]" in pushed_content
    assert "Murasame" in pushed_content
    assert str(line["text"]) in pushed_content


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


@pytest.mark.plugin_unit
def test_choice_selected_event_normalizes_canonical_payload_fields(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    shown = _event(
        seq=1,
        event_type="choices_shown",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:01Z",
        payload={
            "scene_id": "scene-a",
            "choices": [
                {"choice_id": "choice-a", "text": "追上去", "index": 0},
                {"choice_id": "choice-b", "text": "留在原地", "index": 1},
            ],
        },
    )
    selected = _event(
        seq=2,
        event_type="choice_selected",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:02Z",
        payload={
            "scene_id": "scene-a",
            "choice_id": "choice-b",
            "choice_text": "留在原地",
            "choice_index": 1,
        },
    )
    shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        last_seq=2,
        snapshot=_session_state(scene_id="scene-a", route_id="route-a"),
        history_events=[shown, selected],
    )

    occurrences = agent._scene_capsule_choice_occurrences(
        shared,
        snapshot=dict(shared["latest_snapshot"]),
    )

    selected_choices = [
        dict(item.get("choice") or {})
        for item in occurrences
        if str((item.get("choice") or {}).get("choice_state") or "") == "selected"
    ]
    assert selected_choices == [
        {
            "choice_id": "choice-b",
            "text": "留在原地",
            "index": 1,
            "choice_state": "selected",
            "scene_id": "scene-a",
            "route_id": "route-a",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_route_less_line_event_inherits_current_route_for_capsule(
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
    old_line = _summary_test_line("scene-a", 1)
    line = _summary_test_line("scene-a", 2)
    route_boundary = _event(
        seq=1,
        event_type="session_started",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:00Z",
        payload={"scene_id": "scene-a", "route_id": "route-a"},
    )
    events = [
        _summary_test_line_event("scene-a", 1, seq=2),
        _summary_test_line_event("scene-a", 2, seq=3),
    ]
    for item in [old_line, line]:
        assert "route_id" in item
        item["route_id"] = ""
    for event in events:
        event_payload = event.get("payload")
        assert isinstance(event_payload, dict)
        event_payload.pop("route_id", None)
    shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        last_seq=3,
        snapshot=_session_state(
            speaker=str(line["speaker"]),
            text=str(line["text"]),
            scene_id="scene-a",
            route_id="route-a",
            line_id=str(line["line_id"]),
            ts=str(line["ts"]),
        ),
        history_events=[route_boundary, *events],
        history_lines=[old_line, line],
    )

    occurrences = agent._scene_capsule_line_occurrences(
        shared,
        snapshot=dict(shared["latest_snapshot"]),
    )
    assert len(occurrences) == 2
    assert all(
        str((item.get("line") or {}).get("route_id") or "") == "route-a"
        for item in occurrences
    )

    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.pushed_messages) == 1
    assert ctx.pushed_messages[0]["metadata"]["route_id"] == "route-a"
    response_target = str(ctx.pushed_messages[0]["content"]).split(
        "本次回应对象：", 1
    )[-1]
    assert str(line["text"]) in response_target
    assert str(old_line["text"]) not in response_target


@pytest.mark.plugin_unit
def test_latest_scene_summary_text_matches_exact_route(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(
            _Ctx(plugin_dir, _make_effective_config(bridge_root))
        ),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    agent._scene_memory.extend(
        [
            {
                "scene_id": "scene-a",
                "route_id": "route-a",
                "summary": "route A memory",
            },
            {
                "scene_id": "scene-a",
                "route_id": "route-b",
                "summary": "route B memory",
            },
        ]
    )

    assert (
        agent._latest_scene_summary_text(
            {"scene_id": "scene-a", "route_id": "route-a"}
        )
        == "route A memory"
    )
    assert (
        agent._latest_scene_summary_text(
            {"scene_id": "scene-a", "route_id": "route-c"}
        )
        == ""
    )


@pytest.mark.plugin_unit
def test_latest_scene_summary_text_uses_trusted_alias_on_exact_route(
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
    shared = _shared_state(
        mode="companion",
        game_id="demo.alpha",
        session_id="ocr-session",
        active_data_source=DATA_SOURCE_OCR_READER,
        snapshot=_session_state(scene_id="ocr-scene", route_id="route-a"),
    )
    boundary_key = agent._scene_capsule_boundary_key(
        shared,
        session_id="ocr-session",
    )
    agent._scene_capsule_delivery_ledger[boundary_key] = {
        "memory_handoff_scene_aliases": {
            agent._scene_tracker.summary_scope_key(
                "ocr-scene",
                "route-a",
            ): "memory-scene"
        }
    }
    agent._scene_memory.extend(
        [
            {
                "scene_id": "memory-scene",
                "route_id": "route-a",
                "summary": "trusted aliased route A memory",
            },
            {
                "scene_id": "memory-scene",
                "route_id": "route-b",
                "summary": "route B memory must not leak",
            },
        ]
    )

    assert (
        agent._latest_scene_summary_text(
            {"scene_id": "ocr-scene", "route_id": "route-a"},
            shared=shared,
        )
        == "trusted aliased route A memory"
    )
    assert (
        agent._latest_scene_summary_text(
            {"scene_id": "ocr-scene", "route_id": "route-b"},
            shared=shared,
        )
        == ""
    )


@pytest.mark.plugin_unit
def test_choice_history_fallback_does_not_treat_shown_as_selected(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        snapshot=_session_state(scene_id="scene-a"),
        history_choices=[
            {
                "choice_id": "choice-a",
                "text": "追上去",
                "scene_id": "scene-a",
                "action": "shown",
            },
            {
                "choice_id": "choice-b",
                "text": "留在原地",
                "scene_id": "scene-a",
                "action": "selected",
            },
        ],
    )

    occurrences = agent._scene_capsule_choice_occurrences(
        shared,
        snapshot=dict(shared["latest_snapshot"]),
    )

    choices = [dict(item.get("choice") or {}) for item in occurrences]
    assert [(item["text"], item["choice_state"]) for item in choices] == [
        ("留在原地", "selected")
    ]


@pytest.mark.plugin_unit
def test_selected_choice_fallback_keeps_event_identity_after_event_eviction(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    selected_choice = {
        "choice_id": "choice-a",
        "text": "追上去",
        "scene_id": "scene-a",
        "route_id": "route-a",
        "action": "selected",
        "ts": "2026-04-21T08:35:02Z",
    }
    choice_event = _event(
        seq=2,
        event_type="choice_selected",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:02Z",
        payload={
            "scene_id": "scene-a",
            "route_id": "route-a",
            "choice": {**selected_choice, "index": 0},
        },
    )
    snapshot = _session_state(scene_id="scene-a", route_id="route-a", choices=[])
    with_event = _shared_state(
        mode="companion",
        session_id="sess-a",
        snapshot=snapshot,
        history_events=[choice_event],
        history_choices=[selected_choice],
    )

    original = agent._scene_capsule_choice_occurrences(
        with_event,
        snapshot=snapshot,
    )
    original_key = next(
        item["event_key"]
        for item in original
        if int(item.get("seq") or 0) == 2
    )
    after_eviction = agent._scene_capsule_choice_occurrences(
        {**with_event, "history_events": []},
        snapshot=snapshot,
    )

    assert [item["event_key"] for item in after_eviction] == [original_key]


@pytest.mark.plugin_unit
def test_selected_choice_without_scene_keeps_event_time_scope_after_scene_change(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    selected_choice = {
        "choice_id": "choice-a",
        "text": "follow her",
        "route_id": "route-a",
        "action": "selected",
        "ts": "2026-04-21T08:35:02Z",
    }
    choice_event = _event(
        seq=2,
        event_type="choice_selected",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:02Z",
        payload={
            "route_id": "route-a",
            "choice": {"choice_id": "choice-a", "text": "follow her"},
        },
    )
    scene_event = _event(
        seq=1,
        event_type="line_changed",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:01Z",
        payload={"scene_id": "scene-a", "route_id": "route-a", "text": "choose"},
    )
    scene_a_snapshot = _session_state(scene_id="scene-a", route_id="route-a")
    with_event = _shared_state(
        session_id="sess-a",
        snapshot=scene_a_snapshot,
        history_events=[scene_event, choice_event],
        history_choices=[selected_choice],
    )

    original = agent._scene_capsule_choice_occurrences(
        with_event,
        snapshot=scene_a_snapshot,
    )
    original_key = next(item["event_key"] for item in original if item["seq"] == 2)
    scene_b_snapshot = _session_state(scene_id="scene-b", route_id="route-a")
    after_scene_change = agent._scene_capsule_choice_occurrences(
        {
            **with_event,
            "latest_snapshot": scene_b_snapshot,
            "history_events": [],
        },
        snapshot=scene_b_snapshot,
    )

    assert [item["event_key"] for item in after_scene_change] == [original_key]
    assert [
        str((item.get("choice") or {}).get("scene_id") or "")
        for item in after_scene_change
    ] == ["scene-a"]


@pytest.mark.plugin_unit
def test_snapshot_choice_menu_is_merged_with_older_event_occurrences(
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
    old_choices = [
        {"choice_id": "old-a", "text": "old option A", "index": 0},
        {"choice_id": "old-b", "text": "old option B", "index": 1},
    ]
    current_choices = [
        {"choice_id": "new-a", "text": "current option A", "index": 0},
        {"choice_id": "new-b", "text": "current option B", "index": 1},
    ]
    old_event = _event(
        seq=1,
        event_type="choices_shown",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:01Z",
        payload={
            "scene_id": "scene-a",
            "route_id": "route-a",
            "choices": old_choices,
        },
    )
    snapshot = _session_state(
        scene_id="scene-a",
        route_id="route-a",
        choices=current_choices,
    )
    shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        snapshot=snapshot,
        history_events=[old_event],
    )

    occurrences = agent._scene_capsule_choice_occurrences(
        shared,
        snapshot=snapshot,
    )

    assert {
        str((occurrence.get("choice") or {}).get("text") or "")
        for occurrence in occurrences
    } == {
        "old option A",
        "old option B",
        "current option A",
        "current option B",
    }
    assert sum(bool(item.get("snapshot_fallback")) for item in occurrences) == 2

    same_snapshot = _session_state(
        scene_id="scene-a",
        route_id="route-a",
        choices=old_choices,
    )
    same_occurrences = agent._scene_capsule_choice_occurrences(
        {
            **shared,
            "latest_snapshot": same_snapshot,
        },
        snapshot=same_snapshot,
    )
    assert len(same_occurrences) == 2
    assert not any(item.get("snapshot_fallback") for item in same_occurrences)


@pytest.mark.plugin_unit
def test_fallback_occurrence_state_evicts_jittered_session_keys(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )

    for index in range(40):
        source_key = f"ocr_reader|session-{index}|history_line"
        agent._scene_capsule_line_fallback_aliases[source_key] = {1: "event-key"}
        agent._scene_capsule_fallback_occurrence_ids(
            source_key=source_key,
            signatures=[f"line-{index}"],
        )

    assert len(agent._scene_capsule_fallback_occurrences) == 32
    assert len(agent._scene_capsule_line_fallback_aliases) == 32
    assert "ocr_reader|session-0|history_line" not in (
        agent._scene_capsule_fallback_occurrences
    )
    assert "ocr_reader|session-0|history_line" not in (
        agent._scene_capsule_line_fallback_aliases
    )
    assert "ocr_reader|session-39|history_line" in (
        agent._scene_capsule_fallback_occurrences
    )


@pytest.mark.plugin_unit
def test_fallback_line_aliases_only_keep_current_history_window(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    source_key = "bridge_sdk|sess-a|history_line"

    for index in range(1, 80):
        line_indexes = range(max(1, index - 1), index + 1)
        lines = [_summary_test_line("scene-a", item) for item in line_indexes]
        events = [
            _summary_test_line_event("scene-a", item, seq=item)
            for item in line_indexes
        ]
        shared = _capsule_shared(events=events, lines=lines)
        agent._scene_capsule_line_occurrences(
            shared,
            snapshot=dict(shared["latest_snapshot"]),
        )

    assert len(agent._scene_capsule_line_fallback_aliases[source_key]) == 2
    assert len(
        agent._scene_capsule_fallback_occurrences[source_key]["signatures"]
    ) == 2


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
        # The service's text-dedupe window keeps only the first copy in the
        # cumulative line list.  The newer seq remains a distinct story event.
        lines=[first_line],
    )
    await agent.tick(second)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.pushed_messages) == 2
    assert ctx.pushed_messages[-1]["metadata"]["kind"] == "scene_delta"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_same_session_stream_reset_retires_high_seq_pending_capsule(
    tmp_path: Path,
) -> None:
    class _PendingCtx(_Ctx):
        def __init__(self, plugin_dir: Path, effective_config: dict[str, object]) -> None:
            super().__init__(plugin_dir, effective_config)
            self.first_attempted = asyncio.Event()
            self.attempts = 0

        def push_message(self, **kwargs):
            self.attempts += 1
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
    stable_line = _summary_test_line("scene-a", 1)
    high_observed = _event(
        seq=99,
        event_type="line_observed",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:00Z",
        payload={
            "speaker": "Yukino",
            "text": "old tentative",
            "line_id": "line-observed-99",
            "scene_id": "scene-a",
            "route_id": "",
            "stability": "tentative",
        },
    )
    high_stable = _summary_test_line_event("scene-a", 1, seq=100)
    await agent.tick(
        _capsule_shared(
            events=[high_observed, high_stable],
            lines=[stable_line],
        )
    )
    await asyncio.wait_for(ctx.first_attempted.wait(), timeout=0.5)
    previous_epoch = agent._scene_capsule_observation_epoch

    reset_observed = _event(
        seq=1,
        event_type="line_observed",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:36:00Z",
        payload={
            "speaker": "Yukino",
            "text": "new tentative",
            "line_id": "line-observed-1",
            "scene_id": "scene-a",
            "route_id": "",
            "stability": "tentative",
        },
    )
    reset_shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        last_seq=1,
        snapshot=_session_state(scene_id="scene-a"),
        history_events=[reset_observed],
        history_lines=[],
        history_observed_lines=[reset_observed["payload"]],
    )
    await agent.tick(reset_shared)
    await asyncio.sleep(0)

    assert agent._scene_capsule_observation_epoch > previous_epoch
    assert agent._scene_capsule_tasks == set()
    assert ctx.attempts == 1


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
async def test_trusted_handoff_uses_previous_native_scene_summary_as_seed(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    gateway = _FakeLLMGateway()
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=gateway,
        host_adapter=_FakeHostAdapter(),
    )
    memory_line = _summary_test_line("memory-scene", 1)
    memory_line["text"] = str(_summary_test_line("ocr-scene", 1)["text"])
    memory_shared = _shared_state(
        mode="companion",
        game_id="demo.alpha",
        session_id="memory-session",
        active_data_source=DATA_SOURCE_MEMORY_READER,
        snapshot=_session_state(
            text=str(memory_line["text"]),
            scene_id="memory-scene",
            line_id=str(memory_line["line_id"]),
        ),
        history_lines=[memory_line],
    )
    await agent.tick(memory_shared)
    agent._scene_memory.append(
        {
            "scene_id": "memory-scene",
            "route_id": "",
            "summary": "cumulative memory before handoff",
        }
    )

    ocr_lines = [_summary_test_line("ocr-scene", index) for index in range(1, 10)]
    ocr_events = [
        _summary_test_line_event("ocr-scene", index, seq=index)
        for index in range(1, 10)
    ]
    latest_line = ocr_lines[-1]
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
            text=str(latest_line["text"]),
            scene_id="ocr-scene",
            line_id=str(latest_line["line_id"]),
            ts=str(latest_line["ts"]),
        ),
        history_events=ocr_events,
        history_lines=ocr_lines,
    )
    await agent.tick(ocr_shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(gateway.summarize_calls) == 1
    assert (
        gateway.summarize_calls[0]["previous_scene_summary"]
        == "cumulative memory before handoff"
    )


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_disabled_notifications_still_reconcile_handoff_for_memory(
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
        "text": "same silent handoff line",
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
        push_notifications=False,
        game_id="demo.alpha",
        session_id="memory-session",
        active_data_source=DATA_SOURCE_MEMORY_READER,
        snapshot=_session_state(
            text="same silent handoff line",
            scene_id="memory-scene",
            line_id="memory-line-1",
        ),
        history_events=[memory_event],
        history_lines=[memory_line],
    )
    await agent.tick(memory_shared)
    before_count = sum(
        int(state.get("lines_since_push") or 0)
        for state in agent._scene_tracker.summary_scene_states.values()
    )

    ocr_line = {
        **_summary_test_line("ocr-scene", 1),
        "line_id": "ocr-line-1",
        "text": "same silent handoff line",
    }
    ocr_event = _event(
        seq=1,
        event_type="line_changed",
        session_id="ocr-session",
        game_id="demo.alpha",
        ts=str(ocr_line["ts"]),
        payload={**ocr_line, "stability": "stable"},
    )
    ocr_shared = _shared_state(
        mode="companion",
        push_notifications=False,
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
            text="same silent handoff line",
            scene_id="ocr-scene",
            line_id="ocr-line-1",
        ),
        history_events=[ocr_event],
        history_lines=[ocr_line],
    )
    await agent.tick(ocr_shared)
    after_count = sum(
        int(state.get("lines_since_push") or 0)
        for state in agent._scene_tracker.summary_scene_states.values()
    )

    assert before_count == 1
    assert after_count == before_count
    assert ctx.pushed_messages == []


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_trusted_handoff_resets_marker_sequence_space(
    tmp_path: Path,
) -> None:
    class _HandoffCtx(_Ctx):
        def __init__(self, plugin_dir: Path, effective_config: dict[str, object]) -> None:
            super().__init__(plugin_dir, effective_config)
            self.receipts = iter(
                (
                    {"submitted": True},
                    {"submitted": False, "reason": "backpressure"},
                    {"submitted": True},
                )
            )
            self.attempted = asyncio.Event()
            self.attempt_count = 0

        def push_message(self, **kwargs):
            self.attempt_count += 1
            receipt = next(self.receipts)
            if self.attempt_count == 2:
                self.attempted.set()
            if receipt["submitted"]:
                self.pushed_messages.append(dict(kwargs))
            return receipt

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _HandoffCtx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
        config=SimpleNamespace(scene_summary_repeat_guard_enabled=False),
    )
    memory_line = {
        **_summary_test_line("memory-scene", 1),
        "text": "handoff overlap",
    }
    memory_observed = _event(
        seq=100,
        event_type="line_observed",
        session_id="memory-session",
        game_id="demo.alpha",
        ts=str(memory_line["ts"]),
        payload={**memory_line, "stability": "tentative"},
    )
    memory_changed = _event(
        seq=101,
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
            text="handoff overlap",
            scene_id="memory-scene",
            line_id=str(memory_line["line_id"]),
        ),
        history_events=[memory_observed, memory_changed],
        history_lines=[memory_line],
    )
    await agent.tick(memory_shared)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == 1

    ocr_line = {
        **_summary_test_line("ocr-scene", 1),
        "line_id": "ocr-line-1",
        "text": "handoff overlap",
    }
    ocr_changed = _event(
        seq=1,
        event_type="line_changed",
        session_id="ocr-session",
        game_id="demo.alpha",
        ts=str(ocr_line["ts"]),
        payload={**ocr_line, "stability": "stable"},
    )
    ocr_shared = _shared_state(
        mode="companion",
        game_id="demo.alpha",
        session_id="ocr-session",
        active_data_source=DATA_SOURCE_OCR_READER,
        snapshot=_session_state(
            text="handoff overlap",
            scene_id="ocr-scene",
            line_id="ocr-line-1",
        ),
        history_events=[ocr_changed],
        history_lines=[ocr_line],
    )
    await agent.tick(ocr_shared)
    await asyncio.wait_for(ctx.attempted.wait(), timeout=0.5)

    tentative = _event(
        seq=2,
        event_type="line_observed",
        session_id="ocr-session",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:02Z",
        payload={
            **ocr_line,
            "text": "new tentative text",
            "stability": "tentative",
        },
    )
    ocr_shared["history_events"] = [ocr_changed, tentative]
    ocr_shared["history_observed_lines"] = [tentative["payload"]]
    await agent.tick(ocr_shared)
    await agent.drain_summary_tasks(timeout=1.5)

    assert ctx.attempt_count == 2
    assert len(ctx.pushed_messages) == 1
    assert all(
        str(item.get("status") or "") != "queued"
        for item in agent._outbound_messages
    )


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
    assert [item["status"] for item in agent._outbound_messages] == [
        "superseded",
        "delivered",
    ]
    assert all(item["status"] != "queued" for item in agent._recent_pushes)

    await agent.tick(second)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == (1 if repeat_guard_enabled else 2)


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_read_only_scene_change_invalidates_pending_capsule(
    tmp_path: Path,
) -> None:
    class _ReadOnlyCtx(_Ctx):
        def __init__(self, plugin_dir: Path, effective_config: dict[str, object]) -> None:
            super().__init__(plugin_dir, effective_config)
            self.first_attempted = asyncio.Event()
            self.attempt_count = 0

        def push_message(self, **kwargs):
            self.attempt_count += 1
            if self.attempt_count == 1:
                self.first_attempted.set()
                return {"submitted": False, "reason": "backpressure"}
            self.pushed_messages.append(dict(kwargs))
            return {"submitted": True}

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _ReadOnlyCtx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    scene_a_line = _summary_test_line("scene-a", 1)
    scene_a_event = _summary_test_line_event("scene-a", 1, seq=1)
    scene_a = _capsule_shared(events=[scene_a_event], lines=[scene_a_line])
    await agent.tick(scene_a)
    await asyncio.wait_for(ctx.first_attempted.wait(), timeout=0.5)

    scene_b_line = _summary_test_line("scene-b", 2)
    scene_b_event = _summary_test_line_event("scene-b", 2, seq=2)
    scene_b = _capsule_shared(
        events=[scene_a_event, scene_b_event],
        lines=[scene_a_line, scene_b_line],
        scene_id="scene-b",
    )
    messages = await agent.list_messages(scene_b, direction="outbound")
    await agent.drain_summary_tasks(timeout=1.5)

    assert messages["messages"][0]["status"] == "superseded"
    assert ctx.attempt_count == 1
    assert ctx.pushed_messages == []
    assert agent._observed_scene_id == "scene-a"

    await agent.tick(scene_b)
    await agent.drain_summary_tasks(timeout=1.0)
    assert ctx.attempt_count == 2
    assert len(ctx.pushed_messages) == 1
    assert str(scene_b_line["text"]) in str(ctx.pushed_messages[0]["content"])


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_disabling_notifications_retires_pending_capsule_retry(
    tmp_path: Path,
) -> None:
    class _NotificationCtx(_Ctx):
        def __init__(self, plugin_dir: Path, effective_config: dict[str, object]) -> None:
            super().__init__(plugin_dir, effective_config)
            self.first_attempted = asyncio.Event()
            self.attempt_count = 0

        def push_message(self, **kwargs):
            self.attempt_count += 1
            self.first_attempted.set()
            return {"submitted": False, "reason": "backpressure"}

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _NotificationCtx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    shared = _capsule_shared(
        events=[_summary_test_line_event("scene-a", 1, seq=1)],
        lines=[_summary_test_line("scene-a", 1)],
    )

    await agent.tick(shared)
    await asyncio.wait_for(ctx.first_attempted.wait(), timeout=1.0)
    disabled = {**shared, "push_notifications": False}
    await agent.tick(disabled)
    await agent.drain_summary_tasks(timeout=1.5)

    assert ctx.attempt_count == 1
    assert ctx.pushed_messages == []
    assert all(
        str(item.get("status") or "") != "queued"
        for item in agent._outbound_messages
    )


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_disabled_interval_events_are_not_replayed_when_notifications_resume(
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
    line = _summary_test_line("scene-a", 1)
    choices = [
        {"choice_id": "choice-a", "text": "追上去", "index": 0},
        {"choice_id": "choice-b", "text": "留在原地", "index": 1},
    ]
    choice_event = _event(
        seq=2,
        event_type="choices_shown",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:02Z",
        payload={"scene_id": "scene-a", "route_id": "", "choices": choices},
    )
    disabled = _shared_state(
        mode="companion",
        push_notifications=False,
        session_id="sess-a",
        last_seq=2,
        snapshot=_session_state(
            speaker=str(line["speaker"]),
            text=str(line["text"]),
            scene_id="scene-a",
            line_id=str(line["line_id"]),
            ts=str(line["ts"]),
            choices=choices,
            is_menu_open=True,
        ),
        history_events=[
            _summary_test_line_event("scene-a", 1, seq=1),
            choice_event,
        ],
        history_lines=[line],
    )

    await agent.tick(disabled)
    await agent.drain_summary_tasks(timeout=1.0)

    assert agent._scene_tracker.current_scene_lines_since_push("scene-a") == 1
    assert ctx.pushed_messages == []

    resumed = {**disabled, "push_notifications": True}
    await agent.tick(resumed)
    await agent.drain_summary_tasks(timeout=1.0)

    assert agent._scene_tracker.current_scene_lines_since_push("scene-a") == 1
    assert ctx.pushed_messages == []


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_newer_memory_summary_wins_when_old_llm_finishes_last(
    tmp_path: Path,
) -> None:
    class _OutOfOrderGateway(_FakeLLMGateway):
        def __init__(self) -> None:
            super().__init__()
            self.first_started = asyncio.Event()
            self.second_started = asyncio.Event()
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
                self.second_started.set()
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
    )
    await asyncio.sleep(0)
    assert not gateway.second_started.is_set()
    gateway.release_first.set()
    await agent.drain_summary_tasks(timeout=1.0)

    scene_memory = [
        item for item in agent._scene_memory if item.get("scene_id") == "scene-a"
    ]
    assert scene_memory[-1]["summary"] == "newer memory summary"
    assert "newer memory summary" in plugin._story_so_far
    assert "older memory summary" not in plugin._story_so_far
    assert gateway.second_started.is_set()
    assert (
        gateway.summarize_calls[-1]["previous_scene_summary"]
        == "older memory summary"
    )
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
    )
    await asyncio.wait_for(gateway.started.wait(), timeout=0.5)
    agent._start_generation += 1
    gateway.release.set()
    await agent.drain_summary_tasks(timeout=1.0)

    assert "memory survives runtime reset" in plugin._story_so_far


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_submitted_capsule_is_committed_when_input_turns_stale_during_push(
    tmp_path: Path,
) -> None:
    class _StaleAfterSubmitCtx(_Ctx):
        agent: GameLLMAgent | None = None

        def push_message(self, **kwargs):
            self.pushed_messages.append(dict(kwargs))
            assert self.agent is not None
            self.agent._scene_capsule_observation_epoch += 1
            return {"submitted": True}

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _StaleAfterSubmitCtx(plugin_dir, _make_effective_config(bridge_root))
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    ctx.agent = agent
    line = _summary_test_line("scene-a", 1)
    shared = _capsule_shared(
        events=[_summary_test_line_event("scene-a", 1, seq=1)],
        lines=[line],
    )

    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    ledger = next(iter(agent._scene_capsule_delivery_ledger.values()))
    assert len(ctx.pushed_messages) == 1
    assert ledger["committed_event_keys"]
    assert agent._summary_debug["last_capsule_submitted"][
        "stale_after_submission"
    ] is True

    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == 1


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_committed_ledger_covers_full_multi_choice_history(
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
    events: list[dict[str, object]] = []
    latest_choices: list[dict[str, object]] = []
    for seq in range(1, 501):
        latest_choices = [
            {"choice_id": f"{seq}-a", "text": f"menu {seq} option a"},
            {"choice_id": f"{seq}-b", "text": f"menu {seq} option b"},
        ]
        events.append(
            _event(
                seq=seq,
                event_type="choices_shown",
                session_id="sess-a",
                game_id="demo.alpha",
                ts=f"2026-04-21T08:{seq // 60:02d}:{seq % 60:02d}Z",
                payload={
                    "scene_id": "scene-a",
                    "route_id": "",
                    "choices": latest_choices,
                },
            )
        )
    shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        last_seq=500,
        snapshot=_session_state(
            scene_id="scene-a",
            choices=latest_choices,
            is_menu_open=True,
        ),
        history_events=events,
    )

    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=2.0)

    ledger = next(iter(agent._scene_capsule_delivery_ledger.values()))
    assert len(ledger["committed_event_keys"]) == 1000
    assert len(ctx.pushed_messages) == 1

    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=2.0)
    assert len(ctx.pushed_messages) == 1


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_scene_round_trip_preserves_boundary_delivery_ledger(
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
    scene_a_line = _summary_test_line("scene-a", 1)
    scene_a_event = _summary_test_line_event("scene-a", 1, seq=1)
    scene_a = _capsule_shared(events=[scene_a_event], lines=[scene_a_line])
    await agent.tick(scene_a)
    await agent.drain_summary_tasks(timeout=1.0)

    scene_b_line = _summary_test_line("scene-b", 2)
    scene_b_event = _summary_test_line_event("scene-b", 2, seq=2)
    scene_b = _capsule_shared(
        events=[scene_a_event, scene_b_event],
        lines=[scene_a_line, scene_b_line],
        scene_id="scene-b",
    )
    await agent.tick(scene_b)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == 2

    returned_to_scene_a = _capsule_shared(
        events=[scene_a_event, scene_b_event],
        lines=[scene_a_line, scene_b_line],
        scene_id="scene-a",
    )
    returned_to_scene_a["latest_snapshot"] = _session_state(
        text=str(scene_a_line["text"]),
        scene_id="scene-a",
        line_id=str(scene_a_line["line_id"]),
        ts=str(scene_a_line["ts"]),
    )
    await agent.tick(returned_to_scene_a)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.pushed_messages) == 2
    assert len(agent._scene_capsule_delivery_ledger) == 1
    ledger = next(iter(agent._scene_capsule_delivery_ledger.values()))
    assert len(ledger["committed_event_keys"]) == 2


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_trusted_source_handoff_reconciles_same_choice_menu(
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
    memory_choices = [
        {"choice_id": "mem:line-1#choice0", "text": "follow her"},
        {"choice_id": "mem:line-1#choice1", "text": "wait here"},
    ]
    ocr_choices = [
        {"choice_id": "ocr:line-1#choice0", "text": "follow her"},
        {"choice_id": "ocr:line-1#choice1", "text": "wait here"},
    ]

    def _choice_shared(
        *,
        source: str,
        session_id: str,
        scene_id: str,
        events: list[dict[str, object]],
        visible: list[dict[str, object]],
    ) -> dict[str, object]:
        return _shared_state(
            mode="companion",
            game_id="demo.alpha",
            session_id=session_id,
            active_data_source=source,
            ocr_reader_runtime=(
                {
                    "effective_process_name": "demo.exe",
                    "effective_window_title": "Demo",
                    "target_hwnd": 100,
                    "target_window_visible": True,
                }
                if source == DATA_SOURCE_OCR_READER
                else None
            ),
            snapshot=_session_state(
                scene_id=scene_id,
                choices=visible,
                is_menu_open=True,
            ),
            last_seq=max((int(item.get("seq") or 0) for item in events), default=0),
            history_events=events,
        )

    memory_event = _event(
        seq=1,
        event_type="choices_shown",
        session_id="memory-session",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:01Z",
        payload={
            "scene_id": "memory-scene",
            "route_id": "",
            "choices": memory_choices,
        },
    )
    memory_shared = _choice_shared(
        source=DATA_SOURCE_MEMORY_READER,
        session_id="memory-session",
        scene_id="memory-scene",
        events=[memory_event],
        visible=memory_choices,
    )
    await agent.tick(memory_shared)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == 1

    ocr_event = _event(
        seq=1,
        event_type="choices_shown",
        session_id="ocr-session",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:01Z",
        payload={
            "scene_id": "ocr-scene",
            "route_id": "",
            "choices": ocr_choices,
        },
    )
    ocr_shared = _choice_shared(
        source=DATA_SOURCE_OCR_READER,
        session_id="ocr-session",
        scene_id="ocr-scene",
        events=[ocr_event],
        visible=ocr_choices,
    )
    await agent.tick(ocr_shared)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == 1

    new_choices = [{"choice_id": "c", "text": "open the door"}]
    new_event = _event(
        seq=2,
        event_type="choices_shown",
        session_id="ocr-session",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:02Z",
        payload={
            "scene_id": "ocr-scene",
            "route_id": "",
            "choices": new_choices,
        },
    )
    updated = _choice_shared(
        source=DATA_SOURCE_OCR_READER,
        session_id="ocr-session",
        scene_id="ocr-scene",
        events=[ocr_event, new_event],
        visible=new_choices,
    )
    await agent.tick(updated)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.pushed_messages) == 2
    assert "open the door" in str(ctx.pushed_messages[-1]["content"])


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_same_scene_different_routes_keep_capsules_and_memory_separate(
    tmp_path: Path,
) -> None:
    class _RouteGateway(_FakeLLMGateway):
        def __init__(self) -> None:
            super().__init__()
            self.route_a_started = asyncio.Event()
            self.release_route_a = asyncio.Event()

        async def summarize_scene(self, context):
            line = list(context.get("stable_lines") or [])[0]
            route_id = str(line.get("route_id") or "")
            if route_id == "route-a":
                self.route_a_started.set()
                await self.release_route_a.wait()
            return {
                "degraded": False,
                "summary": f"memory for {route_id}",
                "key_points": [],
                "diagnostic": "",
            }

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    gateway = _RouteGateway()
    plugin = GalgameBridgePlugin(ctx)
    agent = GameLLMAgent(
        plugin=plugin,
        logger=_Logger(),
        llm_gateway=gateway,
        host_adapter=_FakeHostAdapter(),
    )
    plugin._game_agent = agent

    def _route_line(route_id: str, seq: int) -> dict[str, object]:
        return {
            **_summary_test_line("scene-a", seq),
            "line_id": "shared-line-id",
            "text": "same route text",
            "route_id": route_id,
        }

    route_a_line = _route_line("route-a", 1)
    route_a_event = _event(
        seq=1,
        event_type="line_changed",
        session_id="sess-a",
        game_id="demo.alpha",
        ts=str(route_a_line["ts"]),
        payload={**route_a_line, "stability": "stable"},
    )
    route_a_shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        snapshot=_session_state(
            text="same route text",
            scene_id="scene-a",
            route_id="route-a",
            line_id="shared-line-id",
        ),
        history_events=[route_a_event],
        history_lines=[route_a_line],
    )
    await agent.tick(route_a_shared)
    await agent.drain_summary_tasks(timeout=1.0)

    route_b_line = _route_line("route-b", 2)
    route_b_event = _event(
        seq=2,
        event_type="line_changed",
        session_id="sess-a",
        game_id="demo.alpha",
        ts=str(route_b_line["ts"]),
        payload={**route_b_line, "stability": "stable"},
    )
    route_b_shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        snapshot=_session_state(
            text="same route text",
            scene_id="scene-a",
            route_id="route-b",
            line_id="shared-line-id",
        ),
        history_events=[route_a_event, route_b_event],
        history_lines=[route_a_line, route_b_line],
    )
    await agent.tick(route_b_shared)
    await agent.drain_summary_tasks(timeout=1.0)
    assert len(ctx.pushed_messages) == 2
    assert ctx.pushed_messages[-1]["metadata"]["route_id"] == "route-b"

    route_a_context = {
        "stable_lines": [route_a_line],
        "current_snapshot": route_a_shared["latest_snapshot"],
    }
    route_b_context = {
        "stable_lines": [route_b_line],
        "current_snapshot": route_b_shared["latest_snapshot"],
    }
    agent._schedule_scene_summary_task(
        shared=route_b_shared,
        session_id="sess-a",
        scene_id="scene-a",
        route_id="route-a",
        snapshot=route_a_shared["latest_snapshot"],
        context=route_a_context,
        trigger="line_count",
        metadata={"scheduled_from_event_seq": 10},
    )
    await asyncio.wait_for(gateway.route_a_started.wait(), timeout=0.5)
    agent._schedule_scene_summary_task(
        shared=route_b_shared,
        session_id="sess-a",
        scene_id="scene-a",
        route_id="route-b",
        snapshot=route_b_shared["latest_snapshot"],
        context=route_b_context,
        trigger="line_count",
        metadata={"scheduled_from_event_seq": 10},
    )
    await asyncio.sleep(0)
    gateway.release_route_a.set()
    await agent.drain_summary_tasks(timeout=1.0)

    memories = {
        str(item.get("route_id") or ""): str(item.get("summary") or "")
        for item in agent._scene_memory
        if item.get("scene_id") == "scene-a"
    }
    assert memories["route-a"] == "memory for route-a"
    assert memories["route-b"] == "memory for route-b"
    assert len(agent._scene_summary_latest_memory_order_by_scene) == 2
    assert plugin._story_so_far.count("memory for route-a") == 1
    assert plugin._story_so_far.count("memory for route-b") == 1


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_trusted_source_handoff_allows_same_boundary_memory_backfill(
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
                "summary": "trusted handoff memory backfill",
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
    line = _summary_test_line("ocr-scene", 1)
    memory_shared = _shared_state(
        mode="companion",
        game_id="mem-0123456789abcdef",
        session_id="memory-session",
        active_data_source=DATA_SOURCE_MEMORY_READER,
        snapshot=_session_state(scene_id="memory-scene"),
        history_lines=[line],
    )
    memory_shared["active_session_meta"] = {
        "metadata": {
            "game_process_name": "demo.exe",
            "window_title": "Demo",
        }
    }
    await agent.tick(memory_shared)
    context = build_summarize_context(
        memory_shared,
        scene_id="memory-scene",
        config=agent._context_config,
    )
    agent._schedule_scene_summary_task(
        shared=memory_shared,
        session_id="memory-session",
        scene_id="memory-scene",
        route_id="",
        snapshot=memory_shared["latest_snapshot"],
        context=context,
        trigger="line_count",
        metadata={"scheduled_from_event_seq": 1},
    )
    await asyncio.wait_for(gateway.started.wait(), timeout=0.5)

    ocr_shared = _shared_state(
        mode="companion",
        game_id="ocr-0123456789ab",
        session_id="ocr-session",
        active_data_source=DATA_SOURCE_OCR_READER,
        ocr_reader_runtime={
            "effective_process_name": "demo.exe",
            "effective_window_title": "Demo",
            "target_hwnd": 100,
            "target_window_visible": True,
        },
        snapshot=_session_state(scene_id="ocr-scene"),
        history_lines=[],
    )
    await agent.tick(ocr_shared)
    gateway.release.set()
    await agent.drain_summary_tasks(timeout=1.0)

    assert "trusted handoff memory backfill" in plugin._story_so_far


@pytest.mark.plugin_unit
def test_choice_selected_missing_index_uses_positional_signature(
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

    def occurrence_for(payload: dict[str, object]) -> dict[str, object]:
        event = _event(
            seq=1,
            event_type="choice_selected",
            session_id="sess-a",
            game_id="demo.alpha",
            ts="2026-04-21T08:35:01Z",
            payload={"scene_id": "scene-a", **payload},
        )
        shared = _shared_state(
            mode="companion",
            session_id="sess-a",
            last_seq=1,
            snapshot=_session_state(scene_id="scene-a", route_id="route-a"),
            history_events=[event],
        )
        return agent._scene_capsule_choice_occurrences(
            shared,
            snapshot=dict(shared["latest_snapshot"]),
        )[0]

    missing_index = occurrence_for({"choice_id": "choice-a", "choice_text": "追上去"})
    explicit_zero = occurrence_for(
        {
            "choice_id": "choice-a",
            "choice_text": "追上去",
            "choice_index": 0,
        }
    )

    assert missing_index["event_group_key"] == explicit_zero["event_group_key"]
    assert missing_index["event_key"] == explicit_zero["event_key"]
    assert "index" not in dict(missing_index["choice"])


@pytest.mark.plugin_unit
async def test_invalid_tail_line_does_not_block_current_route_inheritance(
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
    stable = _summary_test_line_event("scene-a", 1, seq=1)
    tentative = _summary_test_line_event("scene-a", 2, seq=2)
    for event in (stable, tentative):
        payload = event.get("payload")
        assert isinstance(payload, dict)
        payload.pop("route_id", None)
    tentative_payload = tentative["payload"]
    assert isinstance(tentative_payload, dict)
    tentative_payload["stability"] = "tentative"
    shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        last_seq=2,
        snapshot=_session_state(scene_id="scene-a", route_id="route-a"),
        history_events=[stable, tentative],
    )

    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.pushed_messages) == 1
    assert str((stable.get("payload") or {}).get("text") or "") in str(
        ctx.pushed_messages[0]["content"]
    )
    assert str((tentative.get("payload") or {}).get("text") or "") not in str(
        ctx.pushed_messages[0]["content"]
    )


@pytest.mark.plugin_unit
async def test_route_less_line_does_not_inherit_route_from_later_boundary(
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
    session_started = _event(
        seq=1,
        event_type="session_started",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:00Z",
        payload={"scene_id": "scene-a", "route_id": "route-a"},
    )
    line = _summary_test_line_event("scene-a", 1, seq=2)
    line_payload = line.get("payload")
    assert isinstance(line_payload, dict)
    line_payload.pop("route_id", None)
    route_boundary = _event(
        seq=3,
        event_type="scene_changed",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:03Z",
        payload={"scene_id": "scene-a", "route_id": "route-b"},
    )
    shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        last_seq=3,
        snapshot=_session_state(scene_id="scene-a", route_id="route-b"),
        history_events=[session_started, line, route_boundary],
    )

    occurrences = agent._scene_capsule_line_occurrences(
        shared,
        snapshot=dict(shared["latest_snapshot"]),
    )
    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert str((occurrences[0].get("line") or {}).get("route_id") or "") == "route-a"
    assert ctx.pushed_messages == []


@pytest.mark.plugin_unit
async def test_scene_less_line_does_not_inherit_scene_from_later_boundary(
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
    session_started = _event(
        seq=1,
        event_type="session_started",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:00Z",
        payload={"scene_id": "scene-a", "route_id": "route-a"},
    )
    line = _summary_test_line_event("scene-a", 1, seq=2)
    line_payload = line.get("payload")
    assert isinstance(line_payload, dict)
    line_payload.pop("scene_id", None)
    scene_boundary = _event(
        seq=3,
        event_type="scene_changed",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:03Z",
        payload={"scene_id": "scene-b", "route_id": "route-a"},
    )
    shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        last_seq=3,
        snapshot=_session_state(scene_id="scene-b", route_id="route-a"),
        history_events=[session_started, line, scene_boundary],
    )

    occurrences = agent._scene_capsule_line_occurrences(
        shared,
        snapshot=dict(shared["latest_snapshot"]),
    )
    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert str((occurrences[0].get("line") or {}).get("scene_id") or "") == "scene-a"
    assert ctx.pushed_messages == []


@pytest.mark.plugin_unit
def test_invalid_tail_choice_does_not_block_current_route_inheritance(
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
    shown = _event(
        seq=1,
        event_type="choices_shown",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:01Z",
        payload={
            "scene_id": "scene-a",
            "choices": [{"choice_id": "choice-a", "text": "追上去"}],
        },
    )
    invalid = _event(
        seq=2,
        event_type="choice_selected",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:02Z",
        payload={"scene_id": "scene-a", "choice_text": ""},
    )
    shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        last_seq=2,
        snapshot=_session_state(scene_id="scene-a", route_id="route-a"),
        history_events=[shown, invalid],
    )

    occurrences = agent._scene_capsule_choice_occurrences(
        shared,
        snapshot=dict(shared["latest_snapshot"]),
    )

    assert len(occurrences) == 1
    assert str((occurrences[0].get("choice") or {}).get("route_id") or "") == (
        "route-a"
    )


@pytest.mark.plugin_unit
async def test_scene_less_choice_does_not_inherit_scene_from_later_boundary(
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
    session_started = _event(
        seq=1,
        event_type="session_started",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:00Z",
        payload={"scene_id": "scene-a", "route_id": "route-a"},
    )
    choices = _event(
        seq=2,
        event_type="choices_shown",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:02Z",
        payload={
            "route_id": "route-a",
            "choices": [{"choice_id": "choice-a", "text": "追上去"}],
        },
    )
    scene_boundary = _event(
        seq=3,
        event_type="scene_changed",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:03Z",
        payload={"scene_id": "scene-b", "route_id": "route-a"},
    )
    shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        last_seq=3,
        snapshot=_session_state(scene_id="scene-b", route_id="route-a"),
        history_events=[session_started, choices, scene_boundary],
    )

    occurrences = agent._scene_capsule_choice_occurrences(
        shared,
        snapshot=dict(shared["latest_snapshot"]),
    )
    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert str((occurrences[0].get("choice") or {}).get("scene_id") or "") == (
        "scene-a"
    )
    assert ctx.pushed_messages == []


@pytest.mark.plugin_unit
async def test_capsule_cancellation_retires_every_live_occurrence(
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
    never = asyncio.Event()

    async def pending_capsule() -> bool:
        await never.wait()
        return True

    event_versions = {f"event-{index}": 1 for index in range(1200)}
    task = asyncio.create_task(pending_capsule())
    agent._scene_capsule_tasks.add(task)
    agent._scene_capsule_task_meta[task] = {
        "event_keys": list(event_versions),
        "event_versions": event_versions,
        "observation_epoch": 1,
    }

    agent._cancel_scene_capsule_tasks(reason="new_tentative_input", retire=True)
    await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled()
    assert len(agent._scene_capsule_retired_event_versions) == 1200
    assert set(agent._scene_capsule_retired_event_versions) == set(event_versions)


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_live_event_versions_are_not_evicted_before_tentative_cancellation(
    tmp_path: Path,
) -> None:
    class _PendingCtx(_Ctx):
        def __init__(self, plugin_dir: Path, effective_config: dict[str, object]) -> None:
            super().__init__(plugin_dir, effective_config)
            self.first_attempted = asyncio.Event()
            self.attempts = 0

        def push_message(self, **kwargs):
            self.attempts += 1
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
    choices = [
        {
            "choice_id": f"choice-{index}",
            "text": f"option-{index}",
            "index": index,
        }
        for index in range(2050)
    ]
    choices_event = _event(
        seq=1,
        event_type="choices_shown",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:01Z",
        payload={"scene_id": "scene-a", "route_id": "", "choices": choices},
    )
    snapshot = _session_state(
        scene_id="scene-a",
        choices=choices,
        is_menu_open=True,
    )
    initial = _shared_state(
        mode="companion",
        session_id="sess-a",
        last_seq=1,
        snapshot=snapshot,
        history_events=[choices_event],
    )
    await agent.tick(initial)
    await asyncio.wait_for(ctx.first_attempted.wait(), timeout=0.5)

    assert len(agent._scene_capsule_event_versions) == len(choices)

    tentative = _event(
        seq=2,
        event_type="line_observed",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:02Z",
        payload={
            "speaker": "Yukino",
            "text": "new tentative",
            "line_id": "line-observed-2",
            "scene_id": "scene-a",
            "route_id": "",
            "stability": "tentative",
        },
    )
    updated = _shared_state(
        mode="companion",
        session_id="sess-a",
        last_seq=2,
        snapshot=snapshot,
        history_events=[choices_event, tentative],
        history_observed_lines=[tentative["payload"]],
    )
    await agent.tick(updated)
    await asyncio.sleep(0)

    assert len(agent._scene_capsule_retired_event_versions) == len(choices)
    assert agent._scene_capsule_tasks == set()
    assert ctx.attempts == 1


@pytest.mark.plugin_unit
def test_trailing_implicit_events_inherit_snapshot_scope_for_full_retained_run(
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
    line_events = [
        _event(
            seq=index,
            event_type="line_changed",
            session_id="sess-a",
            game_id="demo.alpha",
            ts=f"2026-04-21T08:35:{index:02d}Z",
            payload={
                "speaker": "Yukino",
                "text": f"implicit line {index}",
                "line_id": f"line-{index}",
            },
        )
        for index in range(1, 4)
    ]
    choice_events = [
        _event(
            seq=index + 3,
            event_type="choices_shown",
            session_id="sess-a",
            game_id="demo.alpha",
            ts=f"2026-04-21T08:35:{index + 3:02d}Z",
            payload={
                "choices": [
                    {"choice_id": f"choice-{index}", "text": f"option {index}"}
                ]
            },
        )
        for index in range(1, 3)
    ]
    snapshot = _session_state(scene_id="scene-a", route_id="route-a")
    shared = _shared_state(
        session_id="sess-a",
        last_seq=5,
        snapshot=snapshot,
        history_events=[*line_events, *choice_events],
    )

    line_occurrences = agent._scene_capsule_line_occurrences(
        shared,
        snapshot=snapshot,
    )
    choice_occurrences = agent._scene_capsule_choice_occurrences(
        shared,
        snapshot=snapshot,
    )

    assert len(line_occurrences) == 3
    assert len(choice_occurrences) == 2
    assert {
        (
            str((item.get("line") or {}).get("scene_id") or ""),
            str((item.get("line") or {}).get("route_id") or ""),
        )
        for item in line_occurrences
    } == {("scene-a", "route-a")}
    assert {
        (
            str((item.get("choice") or {}).get("scene_id") or ""),
            str((item.get("choice") or {}).get("route_id") or ""),
        )
        for item in choice_occurrences
    } == {("scene-a", "route-a")}


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_selected_choice_with_implicit_scope_reaches_memory_archive(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    gateway = _FakeLLMGateway()
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(
            _Ctx(plugin_dir, _make_effective_config(bridge_root))
        ),
        logger=_Logger(),
        llm_gateway=gateway,
        host_adapter=_FakeHostAdapter(),
    )
    lines = [_summary_test_line("scene-a", index) for index in range(1, 9)]
    for line in lines:
        line["route_id"] = "route-a"
    selected_event = _event(
        seq=9,
        event_type="choice_selected",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:36:09Z",
        payload={
            "choice_id": "choice-a",
            "choice_text": "follow her",
            "choice_index": 0,
        },
    )
    shared = _shared_state(
        mode="companion",
        push_notifications=False,
        session_id="sess-a",
        last_seq=9,
        snapshot=_session_state(scene_id="scene-a", route_id="route-a"),
        history_events=[selected_event],
        history_lines=lines,
        history_choices=[
            {
                "action": "selected",
                "choice_id": "choice-old",
                "text": "wait here",
                "scene_id": "scene-a",
                "route_id": "route-a",
                "ts": "2026-04-21T08:34:00Z",
            },
            {
                "action": "selected",
                "choice_id": "choice-a",
                "text": "follow her",
                "scene_id": "",
                "route_id": "",
                "ts": "2026-04-21T08:36:09Z",
            }
        ],
    )

    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(gateway.summarize_calls) == 1
    assert gateway.summarize_calls[0]["recent_choices"] == [
        {
            "action": "selected",
            "choice_id": "choice-old",
            "text": "wait here",
            "scene_id": "scene-a",
            "route_id": "route-a",
            "ts": "2026-04-21T08:34:00Z",
        },
        {
            "choice_id": "choice-a",
            "text": "follow her",
            "index": 0,
            "choice_state": "selected",
            "scene_id": "scene-a",
            "route_id": "route-a",
            "ts": "2026-04-21T08:36:09Z",
            "action": "selected",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_trusted_handoff_serializes_pending_aliased_archives(
    tmp_path: Path,
) -> None:
    class _AliasedArchiveGateway(_FakeLLMGateway):
        def __init__(self) -> None:
            super().__init__()
            self.first_started = asyncio.Event()
            self.second_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def summarize_scene(self, context):
            self.summarize_calls.append(dict(context))
            if len(self.summarize_calls) == 1:
                self.first_started.set()
                await self.release_first.wait()
                summary = "memory-reader cumulative archive"
            else:
                self.second_started.set()
                summary = "ocr cumulative archive"
            return {
                "degraded": False,
                "summary": summary,
                "key_points": [],
                "diagnostic": "",
            }

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    gateway = _AliasedArchiveGateway()
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(ctx),
        logger=_Logger(),
        llm_gateway=gateway,
        host_adapter=_FakeHostAdapter(),
    )
    memory_line = _summary_test_line("memory-scene", 1)
    memory_line["text"] = str(_summary_test_line("ocr-scene", 1)["text"])
    memory_shared = _shared_state(
        mode="companion",
        push_notifications=False,
        game_id="mem-0123456789abcdef",
        session_id="memory-session",
        active_data_source=DATA_SOURCE_MEMORY_READER,
        snapshot=_session_state(scene_id="memory-scene"),
        history_lines=[memory_line],
    )
    memory_shared["active_session_meta"] = {
        "metadata": {
            "game_process_name": "demo.exe",
            "window_title": "Demo",
        }
    }
    await agent.tick(memory_shared)
    memory_context = build_summarize_context(
        memory_shared,
        scene_id="memory-scene",
        config=agent._context_config,
    )
    agent._schedule_scene_summary_task(
        shared=memory_shared,
        session_id="memory-session",
        scene_id="memory-scene",
        route_id="",
        snapshot=memory_shared["latest_snapshot"],
        context=memory_context,
        trigger="line_count",
        metadata={"scheduled_from_event_seq": 1},
    )
    await asyncio.wait_for(gateway.first_started.wait(), timeout=0.5)

    ocr_lines = [_summary_test_line("ocr-scene", index) for index in range(1, 10)]
    ocr_shared = _shared_state(
        mode="companion",
        push_notifications=False,
        game_id="ocr-0123456789ab",
        session_id="ocr-session",
        active_data_source=DATA_SOURCE_OCR_READER,
        ocr_reader_runtime={
            "effective_process_name": "demo.exe",
            "effective_window_title": "Demo",
            "target_hwnd": 100,
            "target_window_visible": True,
        },
        snapshot=_session_state(scene_id="ocr-scene"),
        history_lines=ocr_lines,
    )
    await agent.tick(ocr_shared)
    await asyncio.sleep(0)

    assert not gateway.second_started.is_set()
    gateway.release_first.set()
    await agent.drain_summary_tasks(timeout=1.0)

    assert gateway.second_started.is_set()
    assert len(gateway.summarize_calls) == 2
    assert (
        gateway.summarize_calls[1]["previous_scene_summary"]
        == "memory-reader cumulative archive"
    )


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_cancelled_scene_memory_predecessor_does_not_cancel_successor(
    tmp_path: Path,
) -> None:
    class _CancelledPredecessorGateway(_FakeLLMGateway):
        def __init__(self) -> None:
            super().__init__()
            self.first_started = asyncio.Event()
            self.second_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def summarize_scene(self, context):
            self.summarize_calls.append(dict(context))
            if len(self.summarize_calls) == 1:
                self.first_started.set()
                await self.release_first.wait()
                summary = "cancelled predecessor archive"
            else:
                self.second_started.set()
                summary = "successor archive"
            return {
                "degraded": False,
                "summary": summary,
                "key_points": [],
                "diagnostic": "",
            }

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    gateway = _CancelledPredecessorGateway()
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(
            _Ctx(plugin_dir, _make_effective_config(bridge_root))
        ),
        logger=_Logger(),
        llm_gateway=gateway,
        host_adapter=_FakeHostAdapter(),
    )
    line = _summary_test_line("scene-a", 1)
    shared = _shared_state(
        mode="companion",
        push_notifications=False,
        session_id="sess-a",
        snapshot=_session_state(
            text=str(line["text"]),
            scene_id="scene-a",
            line_id=str(line["line_id"]),
        ),
        history_lines=[line],
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
    )
    await asyncio.wait_for(gateway.first_started.wait(), timeout=0.5)
    predecessor = next(iter(agent._summary_tasks))
    agent._schedule_scene_summary_task(
        shared=shared,
        session_id="sess-a",
        scene_id="scene-a",
        route_id="",
        snapshot=shared["latest_snapshot"],
        context=context,
        trigger="line_count",
        metadata={"scheduled_from_event_seq": 2},
    )
    await asyncio.sleep(0)

    try:
        predecessor.cancel()
        await asyncio.gather(predecessor, return_exceptions=True)
        await asyncio.wait_for(gateway.second_started.wait(), timeout=0.5)
        await agent.drain_summary_tasks(timeout=1.0)

        assert len(gateway.summarize_calls) == 2
        assert agent._scene_memory[-1]["summary"] == "successor archive"
    finally:
        pending = list(agent._summary_tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.plugin_unit
def test_fallback_occurrences_advance_for_same_content_at_new_timestamp(
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
    snapshot = _session_state(scene_id="scene-a", route_id="route-a")

    def line(ts: str) -> dict[str, object]:
        return {
            "speaker": "Yukino",
            "text": "same repeated line",
            "scene_id": "scene-a",
            "route_id": "route-a",
            "ts": ts,
            "stability": "stable",
        }

    def choice(ts: str) -> dict[str, object]:
        return {
            "action": "selected",
            "text": "same repeated choice",
            "scene_id": "scene-a",
            "route_id": "route-a",
            "ts": ts,
        }

    first_shared = _shared_state(
        session_id="sess-a",
        snapshot=snapshot,
        history_lines=[line("2026-04-21T08:35:01Z"), line("2026-04-21T08:35:02Z")],
        history_choices=[
            choice("2026-04-21T08:35:01Z"),
            choice("2026-04-21T08:35:02Z"),
        ],
    )
    first_lines = agent._scene_capsule_line_occurrences(
        first_shared,
        snapshot=snapshot,
    )
    first_choices = agent._scene_capsule_choice_occurrences(
        first_shared,
        snapshot=snapshot,
    )
    shifted_shared = {
        **first_shared,
        "history_lines": [
            line("2026-04-21T08:35:02Z"),
            line("2026-04-21T08:35:03Z"),
        ],
        "history_choices": [
            choice("2026-04-21T08:35:02Z"),
            choice("2026-04-21T08:35:03Z"),
        ],
    }
    shifted_lines = agent._scene_capsule_line_occurrences(
        shifted_shared,
        snapshot=snapshot,
    )
    shifted_choices = agent._scene_capsule_choice_occurrences(
        shifted_shared,
        snapshot=snapshot,
    )

    assert shifted_lines[0]["event_key"] == first_lines[1]["event_key"]
    assert shifted_lines[1]["event_key"] not in {
        item["event_key"] for item in first_lines
    }
    assert shifted_choices[0]["event_key"] == first_choices[1]["event_key"]
    assert shifted_choices[1]["event_key"] not in {
        item["event_key"] for item in first_choices
    }


@pytest.mark.plugin_unit
def test_scene_summary_tracker_isolates_same_scene_across_routes(
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

    for index in range(7):
        assert agent._scene_tracker.remember_scene_line(
            "scene-a",
            f"route-a-line-{index}",
            route_id="route-a",
            seq=index + 1,
            ts=f"2026-04-21T08:35:{index:02d}Z",
        )
    assert agent._scene_tracker.remember_scene_line(
        "scene-a",
        "route-b-line-1",
        route_id="route-b",
        seq=8,
        ts="2026-04-21T08:35:08Z",
    )

    agent._scene_tracker.mark_scene_summary_scheduled(
        "scene-a",
        route_id="route-b",
        seq=8,
    )

    assert (
        agent._scene_tracker.current_scene_lines_since_push(
            "scene-a",
            route_id="route-a",
        )
        == 7
    )
    assert (
        agent._scene_tracker.current_scene_lines_since_push(
            "scene-a",
            route_id="route-b",
        )
        == 0
    )
    assert len(agent._scene_tracker.summary_scene_states) == 2


@pytest.mark.plugin_unit
def test_summary_scene_setter_preserves_current_route_scope(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(
            _Ctx(plugin_dir, _make_effective_config(bridge_root))
        ),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    agent._scene_tracker.sync_current_scene_summary_mirror(
        "scene-a",
        route_id="route-a",
    )

    agent._summary_scene_id = "scene-b"

    assert agent._scene_tracker.summary_scene_id == "scene-b"
    assert agent._scene_tracker.summary_route_id == "route-a"
    assert agent._scene_tracker.summary_scene_scope_key == (
        agent._scene_tracker.summary_scope_key("scene-b", "route-a")
    )


@pytest.mark.plugin_unit
@pytest.mark.parametrize("has_matching_event", [False, True])
def test_empty_fallback_scene_inherits_event_or_snapshot_scope(
    tmp_path: Path,
    has_matching_event: bool,
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
    fallback_line = {
        "line_id": "legacy-line",
        "speaker": "Yukino",
        "text": "legacy reconstructed line",
        "scene_id": "",
        "route_id": "",
        "stability": "stable",
        "ts": "2026-04-21T08:35:01Z",
    }
    history_events = []
    expected_scope = ("snapshot-scene", "")
    if has_matching_event:
        history_events = [
            _event(
                seq=1,
                event_type="line_changed",
                session_id="sess-a",
                game_id="demo.alpha",
                ts="2026-04-21T08:35:01Z",
                payload={
                    **fallback_line,
                    "scene_id": "event-scene",
                    "route_id": "event-route",
                },
            )
        ]
        expected_scope = ("event-scene", "event-route")
    snapshot = _session_state(scene_id="snapshot-scene", route_id="snapshot-route")
    shared = _shared_state(
        session_id="sess-a",
        last_seq=1 if has_matching_event else 0,
        snapshot=snapshot,
        history_events=history_events,
        history_lines=[fallback_line],
    )

    occurrences = agent._scene_capsule_line_occurrences(
        shared,
        snapshot=snapshot,
    )

    assert {
        (
            str((item.get("line") or {}).get("scene_id") or ""),
            str((item.get("line") or {}).get("route_id") or ""),
        )
        for item in occurrences
    } == {expected_scope}


@pytest.mark.plugin_unit
def test_empty_fallback_line_keeps_first_reconciled_scope_after_event_eviction(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    fallback_line = {
        "line_id": "legacy-line", "speaker": "Yukino",
        "text": "legacy reconstructed line", "scene_id": "", "route_id": "",
        "stability": "stable", "ts": "2026-04-21T08:35:01Z",
    }
    event = _event(
        seq=1, event_type="line_changed", session_id="sess-a", game_id="demo.alpha",
        ts=str(fallback_line["ts"]),
        payload={**fallback_line, "scene_id": "event-scene", "route_id": "event-route"},
    )
    first_snapshot = _session_state(scene_id="event-scene", route_id="event-route")
    first_shared = _shared_state(
        session_id="sess-a", last_seq=1, snapshot=first_snapshot,
        history_events=[event], history_lines=[fallback_line],
    )
    agent._scene_capsule_line_occurrences(first_shared, snapshot=first_snapshot)
    moved_snapshot = _session_state(scene_id="new-scene", route_id="new-route")
    moved_shared = _shared_state(
        session_id="sess-a", last_seq=2, snapshot=moved_snapshot,
        history_events=[], history_lines=[fallback_line],
    )

    occurrences = agent._scene_capsule_line_occurrences(moved_shared, snapshot=moved_snapshot)
    fallback = next(
        item for item in occurrences
        if str((item.get("line") or {}).get("line_id") or "") == "legacy-line"
    )

    assert (fallback["line"]["scene_id"], fallback["line"]["route_id"]) == (
        "event-scene", "event-route",
    )


@pytest.mark.plugin_unit
def test_route_only_fallback_line_keeps_event_time_route_after_event_eviction(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    fallback_line = {
        "line_id": "legacy-line",
        "speaker": "Yukino",
        "text": "route reconstructed line",
        "scene_id": "scene-a",
        "route_id": "",
        "stability": "stable",
        "ts": "2026-04-21T08:35:01Z",
    }
    event = _event(
        seq=1,
        event_type="line_changed",
        session_id="sess-a",
        game_id="demo.alpha",
        ts=str(fallback_line["ts"]),
        payload={**fallback_line, "route_id": "event-route"},
    )
    first_snapshot = _session_state(scene_id="scene-a", route_id="event-route")
    first_shared = _shared_state(
        session_id="sess-a",
        last_seq=1,
        snapshot=first_snapshot,
        history_events=[event],
        history_lines=[fallback_line],
    )
    agent._scene_capsule_line_occurrences(first_shared, snapshot=first_snapshot)
    moved_snapshot = _session_state(scene_id="scene-a", route_id="new-route")
    moved_shared = _shared_state(
        session_id="sess-a",
        last_seq=2,
        snapshot=moved_snapshot,
        history_events=[],
        history_lines=[fallback_line],
    )

    occurrences = agent._scene_capsule_line_occurrences(
        moved_shared,
        snapshot=moved_snapshot,
    )
    fallback = next(
        item
        for item in occurrences
        if str((item.get("line") or {}).get("line_id") or "") == "legacy-line"
    )

    assert (fallback["line"]["scene_id"], fallback["line"]["route_id"]) == (
        "scene-a",
        "event-route",
    )


@pytest.mark.plugin_unit
def test_scene_tracker_retains_configured_live_line_occurrence_window(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    config = build_config(
        _make_effective_config(
            bridge_root,
            galgame={"history_events_limit": 600, "history_lines_limit": 20},
        )
    )
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
        config=config,
    )
    tracker = agent._scene_tracker
    occurrence_count = config.history_events_limit + config.history_lines_limit

    for index in range(occurrence_count):
        assert tracker.remember_scene_line(
            "scene-a",
            f"line-{index}",
            seq=index + 1,
            ts=f"2026-04-21T08:{index // 60:02d}:{index % 60:02d}Z",
        )

    assert all(
        not tracker.remember_scene_line(
            "scene-a",
            f"line-{index}",
            seq=index + 1,
            ts=f"2026-04-21T08:{index // 60:02d}:{index % 60:02d}Z",
        )
        for index in range(occurrence_count)
    )


@pytest.mark.plugin_unit
def test_delivered_archive_preserves_lines_arriving_after_schedule(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(), llm_gateway=_FakeLLMGateway(), host_adapter=_FakeHostAdapter(),
    )
    tracker = agent._scene_tracker
    tracker.remember_scene_line(
        "scene-a", "line-1", seq=1, ts="2026-04-21T08:35:01Z", now_monotonic=10.0,
    )
    owner_token = tracker.mark_scene_summary_scheduled("scene-a", seq=1)
    tracker.remember_scene_line(
        "scene-a", "line-2", seq=2, ts="2026-04-21T08:35:02Z", now_monotonic=20.0,
    )

    tracker.mark_scene_summary_delivered(
        "scene-a",
        seq=1,
        owner_token=owner_token,
    )
    state = tracker.state_for_scene("scene-a")

    assert state["lines_since_push"] == 1
    assert state["pending_since_monotonic"] == 20.0


@pytest.mark.plugin_unit
def test_inflight_archive_scope_survives_pruning_until_failure_restore(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(), llm_gateway=_FakeLLMGateway(), host_adapter=_FakeHostAdapter(),
    )
    tracker = agent._scene_tracker
    tracker.remember_scene_line(
        "scene-inflight",
        "line-1",
        seq=1,
        ts="2026-04-21T08:35:01Z",
    )
    owner_token = tracker.mark_scene_summary_scheduled("scene-inflight", seq=1)

    for index in range(tracker._SUMMARY_SCENE_STATE_LIMIT + 2):
        tracker.state_for_scene(f"empty-scene-{index}")

    scope_key = tracker.summary_scope_key("scene-inflight")
    assert scope_key in tracker.summary_scene_states
    assert tracker.summary_scene_states[scope_key]["scheduled_in_flight_count"] == 1

    tracker.restore_scene_summary_schedule(
        "scene-inflight",
        seq=1,
        lines_since_push=1,
        owner_token=owner_token,
    )

    state = tracker.summary_scene_states[scope_key]
    assert state["scheduled_in_flight_count"] == 0
    assert state["lines_since_push"] == 1


@pytest.mark.plugin_unit
def test_overlapping_archive_owners_release_inflight_scope_independently(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(), llm_gateway=_FakeLLMGateway(), host_adapter=_FakeHostAdapter(),
    )
    tracker = agent._scene_tracker
    for scene_id, scheduled_seq in (("scene-zero", 0), ("scene-positive", 7)):
        older_owner_token = tracker.mark_scene_summary_scheduled(
            scene_id,
            seq=scheduled_seq,
        )
        newer_owner_token = tracker.mark_scene_summary_scheduled(
            scene_id,
            seq=scheduled_seq,
        )

        tracker.restore_scene_summary_schedule(
            scene_id,
            seq=scheduled_seq,
            lines_since_push=8,
            owner_token=older_owner_token,
        )
        state = tracker.state_for_scene(scene_id)
        assert state["scheduled_in_flight_count"] == 1
        assert state["lines_since_push"] == 0

        tracker.discard_scene_summary_schedule(
            scene_id,
            seq=scheduled_seq,
            owner_token=newer_owner_token,
        )
        assert state["scheduled_in_flight_count"] == 0


@pytest.mark.plugin_unit
def test_reloading_same_checkpoint_occurrence_resets_cumulative_memory(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(), llm_gateway=_FakeLLMGateway(), host_adapter=_FakeHostAdapter(),
    )
    snapshot = _session_state(scene_id="scene-a", route_id="route-a")
    snapshot["save_context"] = {
        "kind": "load", "slot_id": "slot-1", "checkpoint_id": "checkpoint-a",
    }

    def load_event(seq: int, ts: str) -> dict[str, Any]:
        return _event(
            seq=seq, event_type="save_loaded", session_id="sess-a", game_id="demo.alpha",
            ts=ts,
            payload={
                "reason": "load", "scene_id": "scene-a", "route_id": "route-a",
                "save_context": dict(snapshot["save_context"]),
            },
        )

    first_shared = _shared_state(
        session_id="sess-a", last_seq=1, snapshot=snapshot,
        history_events=[load_event(1, "2026-04-21T08:35:01Z")],
    )
    agent._maybe_schedule_scene_capsule(
        first_shared, snapshot=snapshot, line_occurrences=[],
        all_choice_occurrences=[], allow_delivery=False,
    )
    agent._scene_memory.append(
        {"scene_id": "scene-a", "route_id": "route-a", "summary": "future timeline"}
    )
    second_shared = _shared_state(
        session_id="sess-a", last_seq=2, snapshot=snapshot,
        history_events=[
            load_event(1, "2026-04-21T08:35:01Z"),
            load_event(2, "2026-04-21T08:36:01Z"),
        ],
    )

    agent._maybe_schedule_scene_capsule(
        second_shared, snapshot=snapshot, line_occurrences=[],
        all_choice_occurrences=[], allow_delivery=False,
    )

    assert agent._scene_memory == []
    agent._scene_memory.append(
        {"scene_id": "scene-a", "route_id": "route-a", "summary": "new timeline"}
    )
    event_evicted_shared = _shared_state(
        session_id="sess-a", last_seq=3, snapshot=snapshot, history_events=[],
    )

    agent._maybe_schedule_scene_capsule(
        event_evicted_shared, snapshot=snapshot, line_occurrences=[],
        all_choice_occurrences=[], allow_delivery=False,
    )

    assert agent._scene_memory[-1]["summary"] == "new timeline"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_current_snapshot_menu_outranks_older_sequenced_line(
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
    old_line = _summary_test_line("scene-a", 1)
    choices = [
        {"choice_id": "choice-a", "text": "open the door", "index": 0},
        {"choice_id": "choice-b", "text": "wait outside", "index": 1},
    ]
    shared = _shared_state(
        mode="companion",
        session_id="sess-a",
        last_seq=1,
        snapshot=_session_state(
            scene_id="scene-a",
            line_id=str(old_line["line_id"]),
            choices=choices,
            is_menu_open=True,
            ts="2026-04-21T08:35:02Z",
        ),
        history_events=[_summary_test_line_event("scene-a", 1, seq=1)],
        history_lines=[old_line],
    )

    await agent.tick(shared)
    await agent.drain_summary_tasks(timeout=1.0)

    assert len(ctx.pushed_messages) == 1
    assert ctx.pushed_messages[0]["metadata"]["new_stable_line_count"] == 0
    assert ctx.pushed_messages[0]["metadata"]["new_choice_count"] == 2


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_handoff_alias_requires_overlapping_scene_evidence(
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
    memory_line = {
        **_summary_test_line("memory-scene", 1),
        "text": "old logical scene",
    }
    memory_shared = _shared_state(
        mode="companion",
        push_notifications=False,
        game_id="demo.alpha",
        session_id="memory-session",
        active_data_source=DATA_SOURCE_MEMORY_READER,
        snapshot=_session_state(scene_id="memory-scene", text="old logical scene"),
        history_events=[
            _event(
                seq=1,
                event_type="line_changed",
                session_id="memory-session",
                game_id="demo.alpha",
                ts=str(memory_line["ts"]),
                payload={**memory_line, "stability": "stable"},
            )
        ],
        history_lines=[memory_line],
    )
    memory_shared["active_session_meta"] = {
        "metadata": {"game_process_name": "demo.exe", "window_title": "Demo"}
    }
    await agent.tick(memory_shared)
    for ledger in agent._scene_capsule_delivery_ledger.values():
        ledger["memory_handoff_scene_aliases"] = {
            agent._scene_tracker.summary_scope_key("ocr-scene", ""): "memory-scene"
        }

    ocr_line = {
        **_summary_test_line("ocr-scene", 1),
        "line_id": "ocr-line-1",
        "text": "genuinely different scene",
    }
    ocr_shared = _shared_state(
        mode="companion",
        push_notifications=False,
        game_id="demo.alpha",
        session_id="ocr-session",
        active_data_source=DATA_SOURCE_OCR_READER,
        ocr_reader_runtime={
            "effective_process_name": "demo.exe",
            "effective_window_title": "Demo",
            "target_hwnd": 100,
            "target_window_visible": True,
        },
        snapshot=_session_state(scene_id="ocr-scene", text="genuinely different scene"),
        history_events=[
            _event(
                seq=1,
                event_type="line_changed",
                session_id="ocr-session",
                game_id="demo.alpha",
                ts=str(ocr_line["ts"]),
                payload={**ocr_line, "stability": "stable"},
            )
        ],
        history_lines=[ocr_line],
    )
    await agent.tick(ocr_shared)

    assert all(
        not dict(ledger.get("memory_handoff_scene_aliases") or {})
        for ledger in agent._scene_capsule_delivery_ledger.values()
    )


@pytest.mark.plugin_unit
def test_handoff_waits_for_first_stable_line_before_deciding_scene_alias(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    memory_snapshot = _session_state(scene_id="memory-scene", route_id="route-a")
    memory_shared = _shared_state(
        game_id="demo.alpha",
        session_id="memory-session",
        active_data_source=DATA_SOURCE_MEMORY_READER,
        snapshot=memory_snapshot,
    )
    memory_occurrence = {
        "event_key": "memory-line",
        "line": {
            "scene_id": "memory-scene",
            "route_id": "route-a",
            "text": "overlapping line",
        },
    }
    agent._maybe_schedule_scene_capsule(
        memory_shared,
        snapshot=memory_snapshot,
        line_occurrences=[memory_occurrence],
        all_choice_occurrences=[],
        allow_delivery=False,
    )
    ocr_snapshot = _session_state(scene_id="ocr-scene", route_id="route-a")
    ocr_shared = _shared_state(
        game_id="demo.alpha",
        session_id="ocr-session",
        active_data_source=DATA_SOURCE_OCR_READER,
        snapshot=ocr_snapshot,
    )
    agent._remember_trusted_scene_source_handoff(
        agent._session_fingerprint(memory_shared),
        agent._session_fingerprint(ocr_shared),
    )

    agent._maybe_schedule_scene_capsule(
        ocr_shared,
        snapshot=ocr_snapshot,
        line_occurrences=[],
        all_choice_occurrences=[],
        allow_delivery=False,
    )
    agent._maybe_schedule_scene_capsule(
        ocr_shared,
        snapshot=ocr_snapshot,
        line_occurrences=[
            {
                "event_key": "ocr-line",
                "line": {
                    "scene_id": "ocr-scene",
                    "route_id": "route-a",
                    "text": "overlapping line",
                },
            }
        ],
        all_choice_occurrences=[],
        allow_delivery=False,
    )

    boundary = agent._scene_capsule_boundary_key(
        ocr_shared,
        session_id="ocr-session",
    )
    ledger = agent._scene_capsule_delivery_ledger[boundary]
    assert ledger["memory_handoff_scene_aliases"] == {
        agent._scene_tracker.summary_scope_key(
            "ocr-scene",
            "route-a",
        ): "memory-scene"
    }


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_aliased_archive_seed_uses_newest_committed_summary(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    gateway = _FakeLLMGateway()
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(
            _Ctx(plugin_dir, _make_effective_config(bridge_root))
        ),
        logger=_Logger(),
        llm_gateway=gateway,
        host_adapter=_FakeHostAdapter(),
    )
    line = _summary_test_line("memory-scene", 1)
    shared = _shared_state(
        mode="companion",
        push_notifications=False,
        game_id="demo.alpha",
        session_id="memory-session",
        active_data_source=DATA_SOURCE_MEMORY_READER,
        snapshot=_session_state(scene_id="memory-scene"),
        history_lines=[line],
    )
    await agent.tick(shared)
    agent._scene_memory.clear()
    agent._scene_memory.extend(
        [
        {
            "scene_id": "memory-scene",
            "route_id": "",
            "summary": "stale memory-reader archive",
            "ts": "2026-04-21T08:35:01Z",
        },
        {
            "scene_id": "ocr-scene",
            "route_id": "",
            "summary": "newer OCR cumulative archive",
            "ts": "2026-04-21T08:36:01Z",
        },
        ]
    )
    context = build_summarize_context(
        shared,
        scene_id="memory-scene",
        config=agent._context_config,
    )
    agent._schedule_scene_summary_task(
        shared=shared,
        session_id="memory-session",
        scene_id="memory-scene",
        route_id="",
        snapshot=shared["latest_snapshot"],
        context=context,
        trigger="line_count",
        metadata={
            "scheduled_from_event_seq": 1,
            "memory_scene_alias_ids": ["ocr-scene"],
        },
    )
    await agent.drain_summary_tasks(timeout=1.0)

    assert gateway.summarize_calls[-1]["previous_scene_summary"] == (
        "newer OCR cumulative archive"
    )


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_aliased_archive_seed_uses_latest_same_second_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    gateway = _FakeLLMGateway()
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(
            _Ctx(plugin_dir, _make_effective_config(bridge_root))
        ),
        logger=_Logger(),
        llm_gateway=gateway,
        host_adapter=_FakeHostAdapter(),
    )
    line = _summary_test_line("memory-scene", 1)
    shared = _shared_state(
        mode="companion",
        push_notifications=False,
        game_id="demo.alpha",
        session_id="memory-session",
        active_data_source=DATA_SOURCE_MEMORY_READER,
        snapshot=_session_state(scene_id="memory-scene"),
        history_lines=[line],
    )
    await agent.tick(shared)
    agent._scene_memory.clear()
    monkeypatch.setattr(
        agent,
        "_utc_now_iso",
        lambda: "2026-04-21T08:36:01Z",
    )
    agent._replace_scene_memory_summary(
        scene_id="memory-scene",
        route_id="",
        summary="stale memory-reader archive",
    )
    agent._replace_scene_memory_summary(
        scene_id="ocr-scene",
        route_id="",
        summary="OCR archive",
    )
    agent._replace_scene_memory_summary(
        scene_id="memory-scene",
        route_id="",
        summary="latest memory-reader archive",
    )
    context = build_summarize_context(
        shared,
        scene_id="memory-scene",
        config=agent._context_config,
    )
    agent._schedule_scene_summary_task(
        shared=shared,
        session_id="memory-session",
        scene_id="memory-scene",
        route_id="",
        snapshot=shared["latest_snapshot"],
        context=context,
        trigger="line_count",
        metadata={
            "scheduled_from_event_seq": 1,
            "memory_scene_alias_ids": ["ocr-scene"],
        },
    )
    await agent.drain_summary_tasks(timeout=1.0)

    assert gateway.summarize_calls[-1]["previous_scene_summary"] == (
        "latest memory-reader archive"
    )


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_completed_archive_keeps_lines_arriving_while_task_runs(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    gateway = _BlockingSummaryGateway()
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(
            _Ctx(plugin_dir, _make_effective_config(bridge_root))
        ),
        logger=_Logger(),
        llm_gateway=gateway,
        host_adapter=_FakeHostAdapter(),
    )
    lines = [_summary_test_line("scene-a", index) for index in range(1, 9)]
    shared = _capsule_shared(
        events=[
            _summary_test_line_event("scene-a", index, seq=index)
            for index in range(1, 9)
        ],
        lines=lines,
    )

    await agent.tick(shared)
    await agent.tick(shared)
    await asyncio.wait_for(gateway.summary_started.wait(), timeout=0.5)
    assert agent._scene_tracker.remember_scene_line(
        "scene-a",
        "line-arrived-during-archive",
        seq=9,
        ts="2026-04-21T08:35:09Z",
        now_monotonic=20.0,
    )
    gateway.release_summary.set()
    await agent.drain_summary_tasks(timeout=1.0)

    assert agent._scene_tracker.current_scene_lines_since_push("scene-a") == 1


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_permanent_memory_task_cancel_does_not_restore_abandoned_batch(
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
    for index in range(8):
        agent._scene_tracker.remember_scene_line(
            "scene-a",
            f"line-{index}",
            seq=index + 1,
            ts=f"2026-04-21T08:35:{index + 1:02d}Z",
        )
    owner_token = agent._scene_tracker.mark_scene_summary_scheduled("scene-a", seq=8)
    blocker = asyncio.Event()

    async def _pending() -> bool:
        await blocker.wait()
        return True

    task = asyncio.create_task(_pending())
    agent._track_summary_task(
        task,
        scene_id="scene-a",
        scheduled_seq=8,
        scheduled_owner_token=owner_token,
        scheduled_line_count=8,
        meta={"summary_delivery_key": "scene-a:8"},
    )
    await asyncio.sleep(0)

    agent._cancel_scene_memory_tasks(reason="scene_memory_timeline_boundary")
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert agent._scene_tracker.current_scene_lines_since_push("scene-a") == 0
    assert "last_task_restored_schedule" not in agent._summary_debug


@pytest.mark.plugin_unit
def test_fallback_line_scope_metadata_follows_bounded_history_window(
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
    first_line = {
        "line_id": "legacy-1",
        "speaker": "Yukino",
        "text": "first bounded line",
        "scene_id": "",
        "route_id": "",
        "stability": "stable",
        "ts": "2026-04-21T08:35:01Z",
    }
    first_snapshot = _session_state(scene_id="scene-a", route_id="route-a")
    first_shared = _shared_state(
        session_id="sess-a",
        snapshot=first_snapshot,
        history_lines=[first_line],
    )
    agent._scene_capsule_line_occurrences(first_shared, snapshot=first_snapshot)
    source_key = f"{agent._current_input_source(first_shared)}|sess-a|history_line"
    assert len(agent._scene_capsule_fallback_occurrences[source_key]["scopes"]) == 1

    second_line = {
        **first_line,
        "line_id": "legacy-2",
        "text": "replacement bounded line",
        "ts": "2026-04-21T08:35:02Z",
    }
    second_snapshot = _session_state(scene_id="scene-b", route_id="route-b")
    second_shared = _shared_state(
        session_id="sess-a",
        snapshot=second_snapshot,
        history_lines=[second_line],
    )

    agent._scene_capsule_line_occurrences(second_shared, snapshot=second_snapshot)
    scopes = agent._scene_capsule_fallback_occurrences[source_key]["scopes"]

    assert len(scopes) == 1
    assert next(iter(scopes.values())) == {
        "scene_id": "scene-b",
        "route_id": "",
    }


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_same_game_and_session_source_switch_enters_transition_branch(
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
    memory_shared = _shared_state(
        game_id="demo.alpha",
        session_id="shared-session",
        active_data_source=DATA_SOURCE_MEMORY_READER,
        snapshot=_session_state(scene_id="scene-a"),
    )
    memory_shared["active_session_meta"] = {
        "metadata": {
            "game_process_name": "game.exe",
            "game_pid": 4242,
            "window_title": "Demo Game",
            "target_hwnd": 100,
        }
    }
    await agent.tick(memory_shared)
    agent._scene_capsule_marker_event_state["line_changed"] = {
        "type": "line_changed",
        "seq": 1,
        "semantic": "memory-digest",
    }
    ocr_shared = _shared_state(
        game_id="demo.alpha",
        session_id="shared-session",
        active_data_source=DATA_SOURCE_OCR_READER,
        ocr_reader_runtime={
            "effective_process_name": "GAME.EXE",
            "effective_window_title": "Demo Game",
            "pid": 4242,
            "target_hwnd": 100,
            "target_window_visible": True,
        },
        snapshot=_session_state(scene_id="scene-a"),
    )

    await agent.tick(ocr_shared)

    assert agent._last_session_transition_fields["previous_data_source"] == (
        DATA_SOURCE_MEMORY_READER
    )
    assert agent._last_session_transition_fields["current_data_source"] == (
        DATA_SOURCE_OCR_READER
    )
    assert agent._scene_capsule_marker_event_state == {}


@pytest.mark.asyncio
@pytest.mark.plugin_unit
@pytest.mark.parametrize("sequenceful", [False, True])
async def test_line_count_archive_context_marks_only_the_new_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sequenceful: bool,
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
    old_choice_event = _event(
        seq=1 if sequenceful else 0,
        event_type="choice_selected",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:00Z",
        payload={
            "scene_id": "scene-a",
            "choice": {"choice_id": "choice-old", "text": "take the old route"},
        },
    )
    first_events = [
        _summary_test_line_event(
            "scene-a",
            index,
            seq=index + 1 if sequenceful else 0,
        )
        for index in range(1, 9)
    ]
    choice_event = _event(
        seq=10 if sequenceful else 0,
        event_type="choice_selected",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:09Z",
        payload={
            "scene_id": "scene-a",
            "choice": {"choice_id": "choice-new", "text": "take the new route"},
        },
    )
    second_events = [
        _summary_test_line_event(
            "scene-a",
            index,
            seq=index + 2 if sequenceful else 0,
        )
        for index in range(9, 17)
    ]
    lines = [_summary_test_line("scene-a", index) for index in range(1, 17)]
    shared = _capsule_shared(
        events=[old_choice_event, *first_events, choice_event, *second_events],
        lines=lines,
    )
    snapshot = shared["latest_snapshot"]
    assert isinstance(snapshot, dict)
    occurrences = agent._scene_capsule_line_occurrences(shared, snapshot=snapshot)
    for occurrence in occurrences[:8]:
        line = occurrence["line"]
        agent._scene_tracker.remember_scene_line(
            "scene-a",
            str(occurrence["event_key"]),
            seq=int(occurrence["seq"]),
            ts=str(line.get("ts") or ""),
        )
    first_scheduled_seq = int(occurrences[7]["seq"])
    owner_token = agent._scene_tracker.mark_scene_summary_scheduled(
        "scene-a",
        seq=first_scheduled_seq,
    )
    agent._scene_tracker.mark_scene_summary_delivered(
        "scene-a",
        seq=first_scheduled_seq,
        owner_token=owner_token,
    )
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(agent, "_maybe_schedule_scene_capsule", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        agent,
        "_schedule_scene_summary_task",
        lambda **kwargs: captured.append(kwargs),
    )

    await agent._maybe_push_periodic_scene_summary(shared, snapshot=snapshot)

    assert len(captured) == 1
    context = captured[0]["context"]
    assert [line["text"] for line in context["new_stable_lines"]] == [
        line["text"] for line in lines[8:]
    ]
    assert [choice["text"] for choice in context["new_choices"]] == [
        "take the new route"
    ]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_load_boundary_archive_excludes_retained_preload_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    old_lines = [_summary_test_line("scene-a", index) for index in range(1, 3)]
    old_events = [
        _summary_test_line_event("scene-a", index, seq=index)
        for index in range(1, 3)
    ]
    old_choice = {"choice_id": "old-choice", "text": "abandoned future"}
    old_choice_event = _event(
        seq=3,
        event_type="choice_selected",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:03Z",
        payload={"scene_id": "scene-a", "choice": old_choice},
    )
    first_shared = _capsule_shared(
        events=[*old_events, old_choice_event],
        lines=old_lines,
    )
    first_snapshot = first_shared["latest_snapshot"]
    assert isinstance(first_snapshot, dict)
    await agent._maybe_push_periodic_scene_summary(
        first_shared,
        snapshot=first_snapshot,
    )

    save_context = {
        "kind": "load",
        "slot_id": "slot-1",
        "checkpoint_id": "checkpoint-a",
    }
    load_event = _event(
        seq=4,
        event_type="save_loaded",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:04Z",
        payload={
            "reason": "load",
            "scene_id": "scene-a",
            "save_context": save_context,
        },
    )
    new_choice = {"choice_id": "new-choice", "text": "restored path"}
    new_choice_event = _event(
        seq=5,
        event_type="choice_selected",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:05Z",
        payload={"scene_id": "scene-a", "choice": new_choice},
    )
    new_lines = [_summary_test_line("scene-a", index) for index in range(3, 11)]
    new_events = [
        _summary_test_line_event("scene-a", index, seq=index + 3)
        for index in range(3, 11)
    ]
    load_snapshot = _session_state(
        scene_id="scene-a",
        text=str(new_lines[-1]["text"]),
        line_id=str(new_lines[-1]["line_id"]),
    )
    load_snapshot["save_context"] = save_context
    load_shared = _shared_state(
        mode="companion",
        push_notifications=False,
        session_id="sess-a",
        last_seq=13,
        snapshot=load_snapshot,
        history_events=[
            *old_events,
            old_choice_event,
            load_event,
            new_choice_event,
            *new_events,
        ],
        history_lines=[*old_lines, *new_lines],
        history_choices=[
            {**old_choice, "action": "selected", "scene_id": "scene-a"},
            {**new_choice, "action": "selected", "scene_id": "scene-a"},
        ],
    )
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        agent,
        "_schedule_scene_summary_task",
        lambda **kwargs: captured.append(kwargs),
    )

    await agent._maybe_push_periodic_scene_summary(
        load_shared,
        snapshot=load_snapshot,
    )

    assert len(captured) == 1
    context = captured[0]["context"]
    assert [line["text"] for line in context["stable_lines"]] == [
        line["text"] for line in new_lines
    ]
    assert [choice["text"] for choice in context["recent_choices"]] == [
        "restored path"
    ]


@pytest.mark.plugin_unit
def test_load_boundary_retains_fallback_only_occurrences_added_after_boundary(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(), llm_gateway=_FakeLLMGateway(), host_adapter=_FakeHostAdapter(),
    )
    save_context = {
        "kind": "load",
        "slot_id": "slot-1",
        "checkpoint_id": "checkpoint-a",
    }
    snapshot = _session_state(scene_id="scene-a", route_id="route-a")
    snapshot["save_context"] = save_context
    load_event = _event(
        seq=10,
        event_type="save_loaded",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:10Z",
        payload={
            "reason": "load",
            "scene_id": "scene-a",
            "route_id": "route-a",
            "save_context": save_context,
        },
    )
    preload_event = _event(
        seq=9,
        event_type="scene_changed",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:09Z",
        payload={"scene_id": "scene-a", "route_id": "route-a"},
    )
    old_line = {
        "line_id": "old-line",
        "text": "abandoned future",
        "scene_id": "scene-a",
        "route_id": "route-a",
        "stability": "stable",
    }
    old_choice = {
        "choice_id": "old-choice",
        "text": "old decision",
        "action": "selected",
        "scene_id": "scene-a",
        "route_id": "route-a",
    }
    first_shared = _shared_state(
        session_id="sess-a",
        last_seq=10,
        snapshot=snapshot,
        history_events=[preload_event, load_event],
        history_lines=[old_line],
        history_choices=[old_choice],
    )
    first_lines = agent._scene_capsule_line_occurrences(
        first_shared,
        snapshot=snapshot,
    )
    first_choices = agent._scene_capsule_choice_occurrences(
        first_shared,
        snapshot=snapshot,
    )

    filtered_lines, filtered_choices, boundary_active = (
        agent._scene_timeline_occurrences_after_save_boundary(
            first_shared,
            snapshot=snapshot,
            line_occurrences=first_lines,
            choice_occurrences=first_choices,
        )
    )

    assert boundary_active is True
    assert filtered_lines == []
    assert filtered_choices == []

    new_line = {
        "line_id": "new-line",
        "text": "restored timeline",
        "scene_id": "scene-a",
        "route_id": "route-a",
        "stability": "stable",
    }
    new_choice = {
        "choice_id": "new-choice",
        "text": "new decision",
        "action": "selected",
        "scene_id": "scene-a",
        "route_id": "route-a",
    }
    second_shared = _shared_state(
        session_id="sess-a",
        last_seq=10,
        snapshot=snapshot,
        history_events=[load_event],
        history_lines=[old_line, new_line],
        history_choices=[old_choice, new_choice],
    )
    second_lines = agent._scene_capsule_line_occurrences(
        second_shared,
        snapshot=snapshot,
    )
    second_choices = agent._scene_capsule_choice_occurrences(
        second_shared,
        snapshot=snapshot,
    )

    filtered_lines, filtered_choices, boundary_active = (
        agent._scene_timeline_occurrences_after_save_boundary(
            second_shared,
            snapshot=snapshot,
            line_occurrences=second_lines,
            choice_occurrences=second_choices,
        )
    )

    assert boundary_active is True
    assert [item["line"]["text"] for item in filtered_lines] == [
        "restored timeline"
    ]
    assert [item["choice"]["text"] for item in filtered_choices] == [
        "new decision"
    ]


@pytest.mark.plugin_unit
def test_load_boundary_accepts_provably_new_fallbacks_from_the_boundary_tick(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    old_line = {
        "line_id": "old-line",
        "text": "abandoned future",
        "scene_id": "scene-a",
        "route_id": "route-a",
        "stability": "stable",
    }
    old_choice = {
        "choice_id": "old-choice",
        "text": "old decision",
        "action": "selected",
        "scene_id": "scene-a",
        "route_id": "route-a",
    }
    baseline_snapshot = _session_state(scene_id="scene-a", route_id="route-a")
    baseline_shared = _shared_state(
        session_id="sess-a",
        snapshot=baseline_snapshot,
        history_lines=[old_line],
        history_choices=[old_choice],
    )
    agent._scene_capsule_line_occurrences(
        baseline_shared,
        snapshot=baseline_snapshot,
    )
    agent._scene_capsule_choice_occurrences(
        baseline_shared,
        snapshot=baseline_snapshot,
    )

    save_context = {
        "kind": "load",
        "slot_id": "slot-1",
        "checkpoint_id": "checkpoint-a",
    }
    load_snapshot = _session_state(scene_id="scene-a", route_id="route-a")
    load_snapshot["save_context"] = save_context
    load_event = _event(
        seq=10,
        event_type="save_loaded",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:10Z",
        payload={"reason": "load", "save_context": save_context},
    )
    new_line = {
        "line_id": "new-line",
        "text": "restored timeline",
        "scene_id": "scene-a",
        "route_id": "route-a",
        "stability": "stable",
    }
    new_choice = {
        "choice_id": "new-choice",
        "text": "new decision",
        "action": "selected",
        "scene_id": "scene-a",
        "route_id": "route-a",
    }
    load_shared = _shared_state(
        session_id="sess-a",
        last_seq=10,
        snapshot=load_snapshot,
        history_events=[load_event],
        history_lines=[old_line, new_line],
        history_choices=[old_choice, new_choice],
    )
    line_occurrences = agent._scene_capsule_line_occurrences(
        load_shared,
        snapshot=load_snapshot,
    )
    choice_occurrences = agent._scene_capsule_choice_occurrences(
        load_shared,
        snapshot=load_snapshot,
    )

    filtered_lines, filtered_choices, boundary_active = (
        agent._scene_timeline_occurrences_after_save_boundary(
            load_shared,
            snapshot=load_snapshot,
            line_occurrences=line_occurrences,
            choice_occurrences=choice_occurrences,
        )
    )

    assert boundary_active is True
    assert [item["line"]["text"] for item in filtered_lines] == [
        "restored timeline"
    ]
    assert [item["choice"]["text"] for item in filtered_choices] == [
        "new decision"
    ]


@pytest.mark.plugin_unit
def test_load_boundary_fence_survives_save_event_window_eviction(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    agent = GameLLMAgent(
        plugin=GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root))),
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    save_context = {
        "kind": "load",
        "slot_id": "slot-1",
        "checkpoint_id": "checkpoint-a",
    }
    snapshot = _session_state(scene_id="scene-a", route_id="route-a")
    snapshot["save_context"] = save_context
    old_line = {
        "line_id": "old-line",
        "text": "abandoned future",
        "scene_id": "scene-a",
        "route_id": "route-a",
        "stability": "stable",
    }
    old_choice = {
        "choice_id": "old-choice",
        "text": "old decision",
        "action": "selected",
        "scene_id": "scene-a",
        "route_id": "route-a",
    }
    baseline_shared = _shared_state(
        session_id="sess-a",
        snapshot=_session_state(scene_id="scene-a", route_id="route-a"),
        history_lines=[old_line],
        history_choices=[old_choice],
    )
    agent._scene_capsule_line_occurrences(
        baseline_shared,
        snapshot=baseline_shared["latest_snapshot"],
    )
    agent._scene_capsule_choice_occurrences(
        baseline_shared,
        snapshot=baseline_shared["latest_snapshot"],
    )
    load_event = _event(
        seq=10,
        event_type="save_loaded",
        session_id="sess-a",
        game_id="demo.alpha",
        ts="2026-04-21T08:35:10Z",
        payload={"reason": "load", "save_context": save_context},
    )
    load_shared = _shared_state(
        session_id="sess-a",
        last_seq=10,
        snapshot=snapshot,
        history_events=[load_event],
        history_lines=[old_line],
        history_choices=[old_choice],
    )
    agent._scene_timeline_occurrences_after_save_boundary(
        load_shared,
        snapshot=snapshot,
        line_occurrences=agent._scene_capsule_line_occurrences(
            load_shared,
            snapshot=snapshot,
        ),
        choice_occurrences=agent._scene_capsule_choice_occurrences(
            load_shared,
            snapshot=snapshot,
        ),
    )

    new_line = {
        "line_id": "new-line",
        "text": "restored timeline",
        "scene_id": "scene-a",
        "route_id": "route-a",
        "stability": "stable",
    }
    new_choice = {
        "choice_id": "new-choice",
        "text": "new decision",
        "action": "selected",
        "scene_id": "scene-a",
        "route_id": "route-a",
    }
    evicted_shared = _shared_state(
        session_id="sess-a",
        last_seq=12,
        snapshot=snapshot,
        history_events=[
            _event(
                seq=11,
                event_type="screen_classified",
                session_id="sess-a",
                game_id="demo.alpha",
                ts="2026-04-21T08:35:11Z",
                payload={"scene_id": "scene-a"},
            )
        ],
        history_lines=[old_line, new_line],
        history_choices=[old_choice, new_choice],
    )
    line_occurrences = agent._scene_capsule_line_occurrences(
        evicted_shared,
        snapshot=snapshot,
    )
    choice_occurrences = agent._scene_capsule_choice_occurrences(
        evicted_shared,
        snapshot=snapshot,
    )
    filtered_lines, filtered_choices, boundary_active = (
        agent._scene_timeline_occurrences_after_save_boundary(
            evicted_shared,
            snapshot=snapshot,
            line_occurrences=line_occurrences,
            choice_occurrences=choice_occurrences,
        )
    )

    assert boundary_active is True
    assert [item["line"]["text"] for item in filtered_lines] == [
        "restored timeline"
    ]
    assert [item["choice"]["text"] for item in filtered_choices] == [
        "new decision"
    ]
