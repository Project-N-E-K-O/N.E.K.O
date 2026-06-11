from __future__ import annotations

# Compatibility shim: bridge tests were split by behavior area.
from _galgame_test_support import *  # noqa: F401,F403


def test_load_context_snapshot_for_state_falls_back_to_active_game() -> None:
    calls: list[str] = []

    class _Persist:
        def load_context_snapshot(self, *, current_game_id: str, **_: object) -> dict[str, object]:
            calls.append(current_game_id)
            if current_game_id == "game-active":
                return {
                    "game_id": "game-active",
                    "summary_seed": "restored",
                    "saved_at": time.time(),
                }
            return {}

    plugin = GalgameBridgePlugin.__new__(GalgameBridgePlugin)
    plugin._cfg = SimpleNamespace(
        context_persist_enabled=True,
        context_persist_max_age_seconds=3600.0,
        context_persist_require_game_id=True,
    )
    plugin._state = SimpleNamespace(bound_game_id="", active_game_id="game-active")
    plugin._persist = _Persist()

    assert plugin._load_context_snapshot_for_state()["summary_seed"] == "restored"
    assert calls == ["game-active"]


def test_commit_state_preserves_private_context_snapshot_on_public_poll_snapshot(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    private_snapshot = {
        "scene_id": "scene-a",
        "game_id": "game-a",
        "route_id": "route-a",
        "summary_seed": "saved seed",
        "stable_line_ids": ["line-1", "line-2"],
        "saved_at": 123.0,
    }

    with plugin._state_lock:
        plugin._state.context_snapshot = dict(private_snapshot)

    payload = plugin._snapshot_state(fresh=True)
    assert "summary_seed" not in payload["context_snapshot"]
    assert "stable_line_ids" not in payload["context_snapshot"]

    plugin._commit_state(payload)

    with plugin._state_lock:
        assert plugin._state.context_snapshot["summary_seed"] == "saved seed"
        assert plugin._state.context_snapshot["stable_line_ids"] == ["line-1", "line-2"]


def test_load_context_snapshot_for_state_allows_missing_game_id_when_not_required() -> None:
    calls: list[str] = []

    class _Persist:
        def load_context_snapshot(
            self,
            *,
            current_game_id: str,
            **_: object,
        ) -> dict[str, object]:
            calls.append(current_game_id)
            return {
                "game_id": "",
                "summary_seed": "restored without game id",
                "saved_at": time.time(),
            }

    plugin = GalgameBridgePlugin.__new__(GalgameBridgePlugin)
    plugin._cfg = SimpleNamespace(
        context_persist_enabled=True,
        context_persist_max_age_seconds=3600.0,
        context_persist_require_game_id=False,
    )
    plugin._state = SimpleNamespace(bound_game_id="", active_game_id="")
    plugin._persist = _Persist()

    assert (
        plugin._load_context_snapshot_for_state()["summary_seed"]
        == "restored without game id"
    )
    assert calls == [""]


def test_persist_context_snapshot_allows_missing_game_id_when_not_required() -> None:
    saved: list[dict[str, object]] = []

    class _Persist:
        def persist_context_snapshot(self, snapshot: dict[str, object]) -> None:
            saved.append(dict(snapshot))

    plugin = GalgameBridgePlugin.__new__(GalgameBridgePlugin)
    plugin._cfg = SimpleNamespace(
        context_persist_enabled=True,
        context_persist_require_game_id=False,
    )
    plugin._state = SimpleNamespace(
        active_game_id="",
        active_session_id="",
        latest_snapshot={"scene_id": "scene-a", "route_id": ""},
        context_snapshot={},
    )
    plugin._state_lock = threading.Lock()
    plugin._state_dirty = False
    plugin._cached_snapshot = {"stale": True}
    plugin._persist = _Persist()

    plugin._persist_context_snapshot_from_summary(
        {
            "game_id": "",
            "scene_id": "scene-a",
            "route_id": "",
            "stable_lines": [{"line_id": "line-1"}],
        },
        {"summary": "summary without game id"},
    )

    assert saved
    assert saved[0]["game_id"] == ""
    assert saved[0]["summary_seed"] == "summary without game id"
    assert plugin._state.context_snapshot["summary_seed"] == "summary without game id"
    assert plugin._state_dirty is True
    assert plugin._cached_snapshot is None


def test_persist_context_snapshot_skips_write_when_session_turns_stale() -> None:
    saved: list[dict[str, object]] = []

    class _Persist:
        def persist_context_snapshot(self, snapshot: dict[str, object]) -> None:
            saved.append(dict(snapshot))

    class _Logger:
        def warning(self, *_: object, **__: object) -> None:
            return None

    plugin = GalgameBridgePlugin.__new__(GalgameBridgePlugin)
    plugin._cfg = SimpleNamespace(
        context_persist_enabled=True,
        context_persist_require_game_id=True,
    )
    plugin._state = SimpleNamespace(
        active_game_id="demo.alpha",
        active_session_id="sess-a",
        latest_snapshot={"scene_id": "scene-a", "route_id": "route-a"},
        context_snapshot={},
    )
    plugin._state_lock = threading.Lock()
    plugin._state_dirty = False
    plugin._cached_snapshot = {"stale": True}
    plugin._persist = _Persist()
    plugin.logger = _Logger()

    checks = 0
    original_liveness = plugin._context_snapshot_liveness_matches

    def _flip_session_after_first_check(**kwargs: object) -> bool:
        nonlocal checks
        checks += 1
        if checks == 2:
            plugin._state.active_session_id = "sess-b"
        return original_liveness(**kwargs)  # type: ignore[arg-type]

    plugin._context_snapshot_liveness_matches = _flip_session_after_first_check  # type: ignore[method-assign]

    plugin._persist_context_snapshot_from_summary(
        {
            "game_id": "demo.alpha",
            "session_id": "sess-a",
            "scene_id": "scene-a",
            "route_id": "route-a",
            "stable_lines": [{"line_id": "line-1"}],
        },
        {"summary": "stale during write"},
    )

    assert checks == 2
    assert saved == []
    assert plugin._state.context_snapshot == {}
    assert plugin._state_dirty is False


@pytest.mark.asyncio
async def test_summarize_scene_treats_context_snapshot_persist_as_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Gateway:
        async def summarize_scene(self, context: dict[str, object]) -> dict[str, object]:
            return {"summary": "summary ok"}

    def _raise_persist(*_: object) -> None:
        raise RuntimeError("store unavailable")

    context = {
        "scene_id": "scene-a",
        "recent_lines": [{"speaker": "A", "text": "line."}],
        "current_snapshot": {"text": "line."},
    }
    monkeypatch.setattr(
        galgame_plugin_module,
        "build_summarize_context",
        lambda *_args, **_kwargs: context,
    )

    plugin = GalgameBridgePlugin.__new__(GalgameBridgePlugin)
    plugin._llm_gateway = _Gateway()
    plugin._snapshot_state = lambda **_kwargs: {}
    plugin._cfg = SimpleNamespace()
    plugin._persist_context_snapshot_from_summary = _raise_persist
    plugin.logger = _Logger()

    result = await plugin.galgame_summarize_scene()

    assert isinstance(result, Ok)
    assert result.value["summary"] == "summary ok"
    assert result.value["scene_id"] == "scene-a"


def test_commit_state_skips_json_copy_when_payload_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    plugin._commit_state(plugin._snapshot_state(fresh=True))
    cached_snapshot = plugin._snapshot_state()
    assert plugin._state_dirty is False
    assert plugin._cached_snapshot is cached_snapshot
    payload = plugin._snapshot_state(fresh=True)

    def _unexpected_json_copy(value: object) -> object:
        raise AssertionError(f"json_copy should be skipped for unchanged commit field: {value!r}")

    monkeypatch.setattr(galgame_plugin_module, "json_copy", _unexpected_json_copy)

    plugin._commit_state(payload)

    assert plugin._state_dirty is False
    assert plugin._cached_snapshot is cached_snapshot


@pytest.mark.plugin_unit
def test_commit_state_only_copies_changed_mutable_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    plugin._commit_state(plugin._snapshot_state(fresh=True))
    plugin._snapshot_state()
    payload = plugin._snapshot_state(fresh=True)
    payload["last_error"] = {"kind": "warning", "message": "changed"}
    copied_values: list[object] = []

    def _tracking_json_copy(value: object) -> object:
        copied_values.append(value)
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, list):
            return list(value)
        return value

    monkeypatch.setattr(galgame_plugin_module, "json_copy", _tracking_json_copy)

    plugin._commit_state(payload)

    assert copied_values == [{"kind": "warning", "message": "changed"}]
    assert plugin._state.last_error == {"kind": "warning", "message": "changed"}
    assert plugin._state_dirty is True
    assert plugin._cached_snapshot is None


@pytest.mark.plugin_unit
def test_game_llm_agent_reply_context_keeps_condensed_count_for_internal_line_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    agent = GameLLMAgent(
        plugin=plugin,
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    monkeypatch.setattr(
        game_llm_agent_module,
        "_compute_dynamic_line_limit",
        lambda *args, **kwargs: 2,
    )
    shared = _shared_state(
        snapshot=_session_state(scene_id="scene-a", line_id="line-current"),
        history_lines=[
            {
                "speaker": "雪乃",
                "text": "第一句\n第二句\n第三句",
                "line_id": "s1",
                "scene_id": "scene-a",
                "_condensed_line_ids": ["s1", "s2", "s3"],
                "_condensed_count": 3,
            }
        ],
        history_observed_lines=[
            {
                "speaker": "雪乃",
                "text": "候选一句\n候选二句",
                "line_id": "o1",
                "scene_id": "scene-a",
                "_condensed_line_ids": ["o1", "o2"],
                "_condensed_count": 2,
            }
        ],
    )

    public_context = agent._build_agent_reply_context(shared, prompt="status")["public_context"]

    assert public_context["stable_lines"][0]["_condensed_count"] == 3
    assert public_context["observed_lines"][0]["_condensed_count"] == 2
    assert game_llm_agent_module._context_line_count(public_context["stable_lines"]) == 3
    assert all("_condensed_count" not in line for line in public_context["recent_lines"])
    assert all("_condensed_line_ids" not in line for line in public_context["recent_lines"])


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_game_llm_agent_scene_summary_counts_condensed_stable_lines(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    agent = GameLLMAgent(
        plugin=plugin,
        logger=_Logger(),
        llm_gateway=_FakeLLMGateway(),
        host_adapter=_FakeHostAdapter(),
    )
    shared = _shared_state(
        mode="companion",
        snapshot=_session_state(
            speaker="雪乃",
            text="第 8 句台词。",
            scene_id="scene-a",
            line_id="line-8",
            ts="2026-04-21T08:33:08Z",
        ),
    )
    agent._runtime_loop = asyncio.get_running_loop()
    agent._op_lock = asyncio.Lock()
    agent._observed_session_id = str(shared["active_session_id"])
    agent._observed_scene_id = "scene-a"
    agent._schedule_scene_summary_task(
        shared=shared,
        session_id=str(shared["active_session_id"]),
        scene_id="scene-a",
        route_id="",
        snapshot=dict(shared["latest_snapshot"]),
        context={
            "scene_id": "scene-a",
            "route_id": "",
            "stable_lines": [
                {
                    "line_id": "line-1",
                    "speaker": "雪乃",
                    "text": "\n".join(f"第 {index} 句台词。" for index in range(1, 9)),
                    "scene_id": "scene-a",
                    "route_id": "",
                    "ts": "2026-04-21T08:33:08Z",
                    "_condensed_line_ids": [f"line-{index}" for index in range(1, 9)],
                    "_condensed_count": 8,
                }
            ],
            "observed_lines": [],
            "recent_choices": [],
        },
        trigger="line_count",
        metadata={
            "context_type": "galgame_scene_context",
            "trigger": "line_count",
            "scheduled_from_event_seq": 0,
            "last_line_seq": 0,
        },
        update_scene_memory=False,
        scheduled_line_count=8,
    )
    await _drain_agent_summary_tasks(agent)

    assert ctx.pushed_messages[-1]["metadata"]["kind"] == "scene_summary"
    assert ctx.pushed_messages[-1]["metadata"]["trigger"] == "line_count"
    assert ctx.pushed_messages[-1]["metadata"]["stable_line_count"] == 8
    assert ctx.pushed_messages[-1]["metadata"]["summary_delivery_key"] == "scene-a:0:8"
