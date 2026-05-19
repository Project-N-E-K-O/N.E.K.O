from __future__ import annotations

from _galgame_test_support import *

@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_background_bridge_poll_continues_for_subsecond_ocr_interval(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        ocr_reader={
            "enabled": True,
            "trigger_mode": "interval",
            "poll_interval_seconds": 0.1,
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(cfg)
    poll_calls = 0

    async def _poll_bridge(*, force: bool) -> None:
        nonlocal poll_calls
        assert force is False
        poll_calls += 1
        with plugin._state_lock:
            plugin._state.active_data_source = DATA_SOURCE_OCR_READER
            plugin._state.ocr_reader_runtime = {"status": "active"}
            plugin._state.next_poll_at_monotonic = time.monotonic() + 0.01
        if poll_calls >= 2:
            plugin._bridge_poll_thread_stop.set()

    plugin._poll_bridge = _poll_bridge  # type: ignore[method-assign]

    try:
        await asyncio.wait_for(plugin._run_background_bridge_poll(), timeout=0.5)
    finally:
        plugin._bridge_poll_thread_stop.clear()

    assert poll_calls == 2


@pytest.mark.plugin_unit
def test_request_ocr_after_advance_capture_respects_trigger_mode_and_reader_state(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)

    def _make_plugin(*, cfg: dict[str, object] | None) -> tuple[GalgameBridgePlugin, list[bool]]:
        ctx = _Ctx(plugin_dir, cfg or _make_effective_config(bridge_root))
        plugin = GalgameBridgePlugin(ctx)
        plugin._cfg = build_config(cfg) if cfg is not None else None
        starts: list[bool] = []
        plugin._start_background_bridge_poll = lambda: starts.append(True) or True  # type: ignore[method-assign]
        return plugin, starts

    cases = [
        None,
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True, "trigger_mode": "interval"},
        ),
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": False, "trigger_mode": "after_advance"},
        ),
        _make_effective_config(
            bridge_root,
            galgame={"reader_mode": DATA_SOURCE_MEMORY_READER},
            ocr_reader={"enabled": True, "trigger_mode": "after_advance"},
        ),
    ]
    for cfg in cases:
        plugin, starts = _make_plugin(cfg=cfg)
        plugin.request_ocr_after_advance_capture(reason="agent_advance")
        assert plugin._has_pending_ocr_advance_capture() is False
        assert starts == []

    plugin, starts = _make_plugin(
        cfg=_make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True, "trigger_mode": "after_advance"},
        )
    )
    plugin.request_ocr_after_advance_capture(reason="agent_advance")
    assert plugin._has_pending_ocr_advance_capture() is True
    assert starts == [True]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_set_mode_from_choice_advisor_to_companion_queues_after_advance_ocr_capture(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        galgame={"reader_mode": DATA_SOURCE_OCR_READER},
        ocr_reader={"enabled": True, "trigger_mode": "after_advance"},
    )
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, cfg))
    plugin._cfg = build_config(cfg)
    plugin._config_service = SimpleNamespace(
        persist_preferences=lambda **kwargs: None,
        persist_reader_mode=lambda **kwargs: None,
    )
    starts: list[bool] = []
    plugin._start_background_bridge_poll = lambda: starts.append(True) or True  # type: ignore[method-assign]

    async def _ensure_monitor() -> bool:
        return False

    plugin._ensure_ocr_foreground_advance_monitor = _ensure_monitor  # type: ignore[method-assign]
    with plugin._state_lock:
        plugin._state.mode = "choice_advisor"
        plugin._state.active_data_source = DATA_SOURCE_OCR_READER
        plugin._state.active_session_id = "ocr-session"

    result = await plugin.galgame_set_mode(mode="companion")

    assert isinstance(result, Ok)
    assert plugin._has_pending_ocr_advance_capture() is True
    assert plugin._last_ocr_advance_capture_reason == "mode_change_to_read_only"
    assert starts


@pytest.mark.asyncio
@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("initial_mode", "ocr_reader", "reader_mode"),
    [
        ("choice_advisor", {"enabled": True, "trigger_mode": "interval"}, DATA_SOURCE_OCR_READER),
        ("choice_advisor", {"enabled": False, "trigger_mode": "after_advance"}, DATA_SOURCE_OCR_READER),
        ("choice_advisor", {"enabled": True, "trigger_mode": "after_advance"}, DATA_SOURCE_MEMORY_READER),
        ("companion", {"enabled": True, "trigger_mode": "after_advance"}, DATA_SOURCE_OCR_READER),
    ],
)
async def test_set_mode_to_read_only_does_not_queue_ocr_capture_when_ineligible(
    tmp_path: Path,
    initial_mode: str,
    ocr_reader: dict[str, object],
    reader_mode: str,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        galgame={"reader_mode": reader_mode},
        ocr_reader=ocr_reader,
    )
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, cfg))
    plugin._cfg = build_config(cfg)
    plugin._config_service = SimpleNamespace(
        persist_preferences=lambda **kwargs: None,
        persist_reader_mode=lambda **kwargs: None,
    )
    plugin._start_background_bridge_poll = lambda: True  # type: ignore[method-assign]

    async def _ensure_monitor() -> bool:
        return False

    plugin._ensure_ocr_foreground_advance_monitor = _ensure_monitor  # type: ignore[method-assign]
    with plugin._state_lock:
        plugin._state.mode = initial_mode
        plugin._state.active_data_source = DATA_SOURCE_OCR_READER
        plugin._state.active_session_id = "ocr-session"

    result = await plugin.galgame_set_mode(mode="companion")

    assert isinstance(result, Ok)
    assert plugin._has_pending_ocr_advance_capture() is False
    assert plugin._last_ocr_advance_capture_reason == ""


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_set_ocr_timing_to_interval_clears_after_advance_pending(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        ocr_reader={"enabled": True, "trigger_mode": "after_advance"},
    )
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, cfg))
    plugin._cfg = build_config(cfg)
    plugin._config_service = SimpleNamespace(persist_ocr_timing=lambda **kwargs: None)
    plugin._ocr_reader_manager = SimpleNamespace(update_config=lambda config: None)
    plugin._start_background_bridge_poll = lambda: False  # type: ignore[method-assign]
    with plugin._state_lock:
        plugin._pending_ocr_advance_captures = 3
        plugin._last_ocr_advance_capture_requested_at = time.monotonic() - 1.0
        plugin._last_ocr_advance_capture_reason = "manual_foreground_advance"

    result = await plugin.galgame_set_ocr_timing(
        poll_interval_seconds=1.0,
        trigger_mode="interval",
    )

    assert isinstance(result, Ok)
    assert plugin._has_pending_ocr_advance_capture() is False
    assert plugin._last_ocr_advance_capture_requested_at == 0.0
    assert plugin._last_ocr_advance_capture_reason == ""
    assert plugin._cfg.ocr_reader_trigger_mode == "interval"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_set_ocr_timing_persists_and_toggles_fast_loop(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        ocr_reader={"enabled": True, "fast_loop_enabled": True},
    )
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, cfg))
    plugin._cfg = build_config(cfg)
    persist_calls: list[dict[str, object]] = []
    manager_updates: list[bool] = []
    cancel_calls: list[bool] = []
    plugin._config_service = SimpleNamespace(
        persist_ocr_timing=lambda **kwargs: persist_calls.append(dict(kwargs)),
    )
    plugin._ocr_reader_manager = SimpleNamespace(
        update_config=lambda config: manager_updates.append(
            bool(config.ocr_reader_fast_loop_enabled)
        )
    )
    plugin._start_background_bridge_poll = lambda: False  # type: ignore[method-assign]

    async def _cancel_fast_loop() -> None:
        cancel_calls.append(True)

    async def _ensure_monitor() -> None:
        return None

    plugin._cancel_ocr_fast_loop = _cancel_fast_loop  # type: ignore[method-assign]
    plugin._ensure_ocr_foreground_advance_monitor = _ensure_monitor  # type: ignore[method-assign]

    result = await plugin.galgame_set_ocr_timing(
        poll_interval_seconds=1.5,
        trigger_mode="interval",
        fast_loop_enabled=False,
    )

    assert isinstance(result, Ok)
    assert plugin._cfg.ocr_reader_fast_loop_enabled is False
    assert plugin._fast_loop_auto_enabled is False
    assert manager_updates == [False]
    assert cancel_calls == [True]
    assert persist_calls == [
        {
            "poll_interval_seconds": 1.5,
            "trigger_mode": "interval",
            "fast_loop_enabled": False,
        }
    ]
    assert result.value["fast_loop_enabled"] is False


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_set_ocr_timing_starts_fast_loop_when_enabled(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        ocr_reader={"enabled": True, "fast_loop_enabled": False},
    )
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, cfg))
    plugin._cfg = build_config(cfg)
    start_calls: list[bool] = []
    plugin._config_service = SimpleNamespace(persist_ocr_timing=lambda **kwargs: None)
    plugin._ocr_reader_manager = SimpleNamespace(update_config=lambda config: None)
    plugin._start_background_bridge_poll = lambda: False  # type: ignore[method-assign]
    plugin._start_ocr_fast_loop = lambda: start_calls.append(True) or True  # type: ignore[method-assign]

    async def _ensure_monitor() -> None:
        return None

    plugin._ensure_ocr_foreground_advance_monitor = _ensure_monitor  # type: ignore[method-assign]

    result = await plugin.galgame_set_ocr_timing(
        poll_interval_seconds=1.5,
        trigger_mode="interval",
        fast_loop_enabled=True,
    )

    assert isinstance(result, Ok)
    assert plugin._cfg.ocr_reader_fast_loop_enabled is True
    assert start_calls == [True]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_set_ocr_backend_resets_capture_runtime_diagnostics_on_change(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        ocr_reader={"enabled": True, "backend_selection": "rapidocr", "capture_backend": "dxcam"},
    )
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, cfg))
    plugin._cfg = build_config(cfg)
    plugin._config_service = SimpleNamespace(
        persist_ocr_backend_selection=lambda **kwargs: None,
    )
    resets: list[bool] = []
    plugin._ocr_reader_manager = SimpleNamespace(
        update_config=lambda config: None,
        reset_capture_runtime_diagnostics=lambda: resets.append(True),
    )
    plugin._start_background_bridge_poll = lambda: False  # type: ignore[method-assign]

    # Legacy "imagegrab" is accepted at the API boundary but normalized to "mss"
    # so old configs auto-rewrite on the next save.
    result = await plugin.galgame_set_ocr_backend(capture_backend="imagegrab")

    assert isinstance(result, Ok)
    assert plugin._cfg.ocr_reader_capture_backend == "mss"
    assert resets == [True]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_bridge_tick_skips_manual_foreground_advance_when_monitor_active(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        ocr_reader={"enabled": True, "trigger_mode": "after_advance"},
    )
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, cfg))
    plugin._cfg = build_config(cfg)
    plugin._ocr_reader_manager = SimpleNamespace()
    plugin._start_background_bridge_poll = lambda: False  # type: ignore[method-assign]
    trigger_calls = 0

    def _trigger() -> None:
        nonlocal trigger_calls
        trigger_calls += 1

    plugin._trigger_ocr_for_manual_foreground_advance = _trigger  # type: ignore[method-assign]
    task = asyncio.create_task(asyncio.sleep(60.0))
    plugin._ocr_foreground_advance_monitor_task = task
    try:
        await plugin.bridge_tick()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert trigger_calls == 0


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_bridge_tick_keeps_manual_foreground_advance_fallback_without_monitor(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        ocr_reader={"enabled": True, "trigger_mode": "after_advance"},
    )
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, cfg))
    plugin._cfg = build_config(cfg)
    plugin._ocr_reader_manager = SimpleNamespace()
    plugin._start_background_bridge_poll = lambda: False  # type: ignore[method-assign]
    trigger_calls = 0

    def _trigger() -> None:
        nonlocal trigger_calls
        trigger_calls += 1

    plugin._trigger_ocr_for_manual_foreground_advance = _trigger  # type: ignore[method-assign]

    await plugin.bridge_tick()

    assert trigger_calls == 1


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_poll_bridge_clears_pending_after_ocr_capture_failure(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={"enabled": False},
        ocr_reader={
            "enabled": True,
            "trigger_mode": "after_advance",
            "poll_interval_seconds": 1.0,
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(cfg)

    class _CaptureFailedOcrManager:
        def update_config(self, config):
            del config

        def update_advance_speed(self, advance_speed):
            del advance_speed

        def refresh_foreground_state(self):
            return {"status": "active", "target_is_foreground": True}

        async def tick(self, **kwargs):
            del kwargs
            return SimpleNamespace(
                warnings=["ocr_reader capture failed: timed out"],
                should_rescan=False,
                stable_event_emitted=False,
                runtime={
                    "enabled": True,
                    "status": "active",
                    "detail": "capture_failed",
                    "last_capture_error": "ocr_reader capture/OCR timed out after 12.0s",
                },
            )

        def current_window_target(self):
            return {}

    plugin._ocr_reader_manager = _CaptureFailedOcrManager()
    with plugin._state_lock:
        plugin._pending_ocr_advance_captures = 8
        plugin._last_ocr_advance_capture_requested_at = time.monotonic() - 3.0
        plugin._last_ocr_advance_capture_reason = "manual_foreground_advance"
        plugin._state.next_poll_at_monotonic = 0.0

    await plugin._poll_bridge(force=False)

    assert plugin._has_pending_ocr_advance_capture() is False


@pytest.mark.plugin_unit
def test_ocr_foreground_refresh_uses_ttl_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(bridge_root, ocr_reader={"enabled": True})
    ctx = _Ctx(
        plugin_dir,
        cfg,
    )
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(cfg)
    now = {"value": 1000.0}
    monkeypatch.setattr(galgame_plugin_module.time, "monotonic", lambda: now["value"])
    calls = {"count": 0}

    class _OcrManager:
        def refresh_foreground_state(self):
            calls["count"] += 1
            return {"status": "active", "foreground_refresh_seq": calls["count"]}

    plugin._ocr_reader_manager = _OcrManager()

    plugin._refresh_ocr_foreground_state()
    plugin._refresh_ocr_foreground_state()
    now["value"] += 2.1
    plugin._refresh_ocr_foreground_state()
    plugin._refresh_ocr_foreground_state(force=True)

    assert calls["count"] == 3
    assert plugin._state.ocr_reader_runtime["foreground_refresh_seq"] == 3


@pytest.mark.plugin_unit
def test_ocr_foreground_refresh_preserves_bridge_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(bridge_root, ocr_reader={"enabled": True})
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, cfg))
    plugin._cfg = build_config(cfg)
    monkeypatch.setattr(galgame_plugin_module.time, "monotonic", lambda: 1000.0)

    class _OcrManager:
        def refresh_foreground_state(self):
            return {
                "status": "active",
                "detail": "attached_no_text_yet",
                "target_is_foreground": True,
            }

    plugin._ocr_reader_manager = _OcrManager()
    plugin._state.ocr_reader_runtime = {
        "status": "active",
        "ocr_tick_allowed": True,
        "ocr_reader_allowed": True,
        "ocr_tick_gate_allowed": True,
        "ocr_last_tick_decision_at": "2026-04-29T00:00:00Z",
        "pending_ocr_advance_capture": True,
        "pending_ocr_advance_reason": "manual_foreground_advance",
    }

    plugin._refresh_ocr_foreground_state(force=True)

    runtime = plugin._state.ocr_reader_runtime
    assert runtime["detail"] == "attached_no_text_yet"
    assert runtime["target_is_foreground"] is True
    assert runtime["ocr_tick_allowed"] is True
    assert runtime["ocr_reader_allowed"] is True
    assert runtime["ocr_tick_gate_allowed"] is True
    assert runtime["ocr_last_tick_decision_at"] == "2026-04-29T00:00:00Z"
    assert runtime["pending_ocr_advance_capture"] is True
    assert runtime["pending_ocr_advance_reason"] == "manual_foreground_advance"


@pytest.mark.plugin_unit
def test_status_debug_payload_overlays_live_pending_ocr_advance_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(bridge_root, ocr_reader={"enabled": True})
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, cfg))
    plugin._cfg = build_config(cfg)
    monkeypatch.setattr(galgame_plugin_module.time, "monotonic", lambda: 1000.0)
    with plugin._state_lock:
        plugin._pending_ocr_advance_captures = 2
        plugin._last_ocr_advance_capture_requested_at = 999.0
        plugin._last_ocr_advance_capture_reason = "manual_foreground_advance"

    payload = plugin._add_bridge_poll_debug_payload(
        {
            "ocr_reader_runtime": {
                "status": "active",
                "pending_ocr_advance_capture": False,
                "pending_ocr_advance_reason": "",
            }
        }
    )

    assert payload["pending_ocr_advance_capture"] is True
    assert payload["pending_manual_foreground_ocr_capture"] is True
    assert payload["pending_ocr_advance_reason"] == "manual_foreground_advance"
    assert payload["pending_ocr_advance_capture_age_seconds"] == pytest.approx(1.0)
    assert payload["pending_ocr_delay_remaining"] == pytest.approx(0.0)
    assert payload["ocr_reader_runtime"]["pending_ocr_advance_capture"] is True
    assert (
        payload["ocr_reader_runtime"]["pending_ocr_advance_reason"]
        == "manual_foreground_advance"
    )


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_auto_reader_keeps_configured_ocr_available_when_memory_default_unavailable(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        galgame={"reader_mode": "auto"},
        memory_reader={
            "enabled": True,
            "auto_detect": True,
            "poll_interval_seconds": 1,
        },
        ocr_reader={
            "enabled": True,
            "backend_selection": "rapidocr",
            "capture_backend": "dxcam",
            "trigger_mode": "interval",
            "poll_interval_seconds": 1.0,
        },
        rapidocr={"enabled": True},
    )
    ocr_game_id = "ocr-configured"
    ocr_session_id = "ocr-session"
    _create_game_dir(
        bridge_root,
        game_id=ocr_game_id,
        session_payload=_ocr_reader_session(
            game_id=ocr_game_id,
            session_id=ocr_session_id,
            last_seq=1,
            state=_session_state(
                speaker="ocr",
                text="configured OCR line",
                scene_id="ocr-scene",
                line_id="ocr-line",
            ),
        ),
        events=[],
    )

    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(cfg)
    plugin._start_background_bridge_poll = lambda: False  # type: ignore[method-assign]

    ocr_ticks: list[dict[str, object]] = []

    async def _memory_tick(**_kwargs):
        return SimpleNamespace(
            warnings=[],
            should_rescan=False,
            runtime={
                "enabled": True,
                "status": "idle",
                "detail": "invalid_textractor_path",
            },
        )

    async def _ocr_tick(**kwargs):
        ocr_ticks.append(dict(kwargs))
        return SimpleNamespace(
            warnings=[],
            should_rescan=False,
            stable_event_emitted=True,
            runtime={
                "enabled": True,
                "status": "active",
                "detail": "stable",
                "game_id": ocr_game_id,
                "session_id": ocr_session_id,
            },
        )

    plugin._memory_reader_manager = SimpleNamespace(
        update_config=lambda config: None,
        tick=_memory_tick,
    )
    plugin._ocr_reader_manager = SimpleNamespace(
        update_config=lambda config: None,
        update_advance_speed=lambda speed: None,
        tick=_ocr_tick,
        current_window_target=lambda: {},
    )

    await plugin._poll_bridge(force=True)
    status = await plugin.galgame_get_status()
    snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(status, Ok)
    assert isinstance(snapshot, Ok)
    assert ocr_ticks
    assert status.value["active_data_source"] == DATA_SOURCE_OCR_READER
    assert snapshot.value["snapshot"]["text"] == "configured OCR line"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_auto_reader_keeps_rapidocr_enabled_ocr_available_when_backend_auto(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        galgame={"reader_mode": "auto"},
        memory_reader={
            "enabled": True,
            "auto_detect": True,
            "poll_interval_seconds": 1,
        },
        ocr_reader={
            "enabled": True,
            "backend_selection": "auto",
            "capture_backend": "auto",
            "trigger_mode": "interval",
            "poll_interval_seconds": 1.0,
        },
        rapidocr={"enabled": True},
    )
    ocr_game_id = "ocr-rapidocr-enabled"
    ocr_session_id = "ocr-session"
    _create_game_dir(
        bridge_root,
        game_id=ocr_game_id,
        session_payload=_ocr_reader_session(
            game_id=ocr_game_id,
            session_id=ocr_session_id,
            last_seq=1,
            state=_session_state(
                speaker="ocr",
                text="rapidocr enabled OCR line",
                scene_id="ocr-scene",
                line_id="ocr-line",
            ),
        ),
        events=[],
    )

    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, cfg))
    plugin._cfg = build_config(cfg)
    plugin._start_background_bridge_poll = lambda: False  # type: ignore[method-assign]
    ocr_ticks: list[dict[str, object]] = []

    async def _memory_tick(**_kwargs):
        return SimpleNamespace(
            warnings=[],
            should_rescan=False,
            runtime={
                "enabled": True,
                "status": "idle",
                "detail": "invalid_textractor_path",
            },
        )

    async def _ocr_tick(**kwargs):
        ocr_ticks.append(dict(kwargs))
        return SimpleNamespace(
            warnings=[],
            should_rescan=False,
            stable_event_emitted=True,
            runtime={
                "enabled": True,
                "status": "active",
                "detail": "stable",
                "game_id": ocr_game_id,
                "session_id": ocr_session_id,
            },
        )

    plugin._memory_reader_manager = SimpleNamespace(
        update_config=lambda config: None,
        tick=_memory_tick,
    )
    plugin._ocr_reader_manager = SimpleNamespace(
        update_config=lambda config: None,
        update_advance_speed=lambda speed: None,
        tick=_ocr_tick,
        current_window_target=lambda: {},
    )

    await plugin._poll_bridge(force=True)
    status = await plugin.galgame_get_status()
    snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(status, Ok)
    assert isinstance(snapshot, Ok)
    assert ocr_ticks
    assert status.value["active_data_source"] == DATA_SOURCE_OCR_READER
    assert snapshot.value["snapshot"]["text"] == "rapidocr enabled OCR line"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_set_ocr_capture_profile_updates_state_and_store(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True},
        ),
    )
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()

    saved = await plugin.galgame_set_ocr_capture_profile(
        process_name="DemoGame.exe",
        left_inset_ratio=0.08,
        right_inset_ratio=0.06,
        top_ratio=0.34,
        bottom_inset_ratio=0.22,
    )

    assert isinstance(saved, Ok)
    assert saved.value["process_name"] == "DemoGame.exe"
    assert saved.value["stage"] == "default"
    assert saved.value["capture_profile"]["top_ratio"] == pytest.approx(0.34)
    with plugin._state_lock:
        assert plugin._state.ocr_capture_profiles["DemoGame.exe"]["left_inset_ratio"] == pytest.approx(0.08)
    restored, _warnings = plugin._persist.load()
    assert restored[STORE_OCR_CAPTURE_PROFILES]["DemoGame.exe"]["bottom_inset_ratio"] == pytest.approx(0.22)

    cleared = await plugin.galgame_set_ocr_capture_profile(
        process_name="DemoGame.exe",
        clear=True,
    )

    assert isinstance(cleared, Ok)
    assert cleared.value["cleared"] is True
    with plugin._state_lock:
        assert "DemoGame.exe" not in plugin._state.ocr_capture_profiles


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_screen_template_draft_and_validation_use_current_runtime(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True},
        ),
    )
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    with plugin._state_lock:
        plugin._state.ocr_reader_runtime = {
            "process_name": "DemoGame.exe",
            "window_title": "Demo Window",
            "width": 1280,
            "height": 720,
            "last_raw_ocr_text": "Archive\nSpecial\nBack",
            "capture_stage": OCR_CAPTURE_PROFILE_STAGE_GALLERY,
        }
        plugin._state.screen_type = OCR_CAPTURE_PROFILE_STAGE_GALLERY
        plugin._state.screen_ui_elements = [{"text": "Archive"}, {"text": "Special"}]

    draft_result = await plugin.galgame_build_ocr_screen_template_draft(
        stage=OCR_CAPTURE_PROFILE_STAGE_GALLERY,
        region={"left": 0.1, "top": 0.2, "right": 0.5, "bottom": 0.6},
    )

    assert isinstance(draft_result, Ok)
    draft = draft_result.value["template"]
    assert draft["stage"] == OCR_CAPTURE_PROFILE_STAGE_GALLERY
    assert draft["process_names"] == ["DemoGame.exe"]
    assert "Archive" in draft["keywords"]
    assert draft["regions"][0]["left"] == pytest.approx(0.1)

    validation = await plugin.galgame_validate_ocr_screen_templates([draft])

    assert isinstance(validation, Ok)
    assert validation.value["classification"]["screen_type"] == OCR_CAPTURE_PROFILE_STAGE_GALLERY
    assert validation.value["classification"]["screen_debug"]["reason"] == "screen_template"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_train_and_evaluate_ocr_screen_awareness_model_entries(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True},
        ),
    )
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    samples_path = tmp_path / "screen-samples.jsonl"
    model_path = tmp_path / "screen-model.json"
    report_path = tmp_path / "screen-report.json"
    records = [
        {
            "label": OCR_CAPTURE_PROFILE_STAGE_TITLE,
            "visual_features": {"mean_luminance": 190 + index, "luminance_std": 30, "texture_score": 20},
            "ocr_lines": ["Start"],
        }
        for index in range(3)
    ] + [
        {
            "label": OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
            "visual_features": {"mean_luminance": 3 + index, "luminance_std": 1, "texture_score": 1},
            "ocr_lines": [],
        }
        for index in range(3)
    ]
    samples_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )

    trained = await plugin.galgame_train_ocr_screen_awareness_model(
        sample_path=str(samples_path),
        output_path=str(model_path),
        validation_ratio=0.0,
        min_samples_per_stage=2,
    )
    evaluated = await plugin.galgame_evaluate_ocr_screen_awareness_model(
        sample_path=str(samples_path),
        model_path=str(model_path),
        report_path=str(report_path),
    )

    assert isinstance(trained, Ok)
    assert isinstance(evaluated, Ok)
    assert model_path.is_file()
    assert report_path.is_file()
    assert trained.value["evaluation"]["sample_count"] == 6
    assert evaluated.value["evaluation"]["accuracy"] >= 0.8


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_apply_recommended_ocr_capture_profile_records_rollback_and_restores_previous(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True},
        ),
    )
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    bucket_key = build_ocr_capture_profile_bucket_key(1280, 720).lower()
    with plugin._state_lock:
        plugin._state.ocr_reader_runtime = {
            "process_name": "DemoGame.exe",
            "width": 1280,
            "height": 720,
            "recommended_capture_profile_process_name": "DemoGame.exe",
            "recommended_capture_profile_stage": OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
            "recommended_capture_profile_save_scope": OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET,
            "recommended_capture_profile": {
                "left_inset_ratio": 0.05,
                "right_inset_ratio": 0.06,
                "top_ratio": 0.52,
                "bottom_inset_ratio": 0.12,
            },
            "recommended_capture_profile_confidence": 0.82,
            "recommended_capture_profile_manual_present": False,
        }

    applied = await plugin.galgame_apply_recommended_ocr_capture_profile(confirm=True)

    assert isinstance(applied, Ok)
    assert applied.value["rollback_pending"] is True
    with plugin._state_lock:
        stored = plugin._state.ocr_capture_profiles["DemoGame.exe"]
        assert (
            stored[OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY][bucket_key]["stages"][
                OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
            ]["top_ratio"]
            == pytest.approx(0.52)
        )

    rolled_back = await plugin.galgame_rollback_ocr_capture_profile(confirm=True)

    assert isinstance(rolled_back, Ok)
    with plugin._state_lock:
        assert "DemoGame.exe" not in plugin._state.ocr_capture_profiles
    assert plugin._ocr_capture_profile_last_rollback_reason == "manual_rollback_recommended_capture_profile"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_recommended_ocr_capture_profile_auto_rolls_back_after_repeated_failure(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True},
        ),
    )
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    with plugin._state_lock:
        plugin._state.ocr_reader_runtime = {
            "process_name": "DemoGame.exe",
            "width": 1280,
            "height": 720,
            "recommended_capture_profile_process_name": "DemoGame.exe",
            "recommended_capture_profile_stage": OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
            "recommended_capture_profile_save_scope": OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET,
            "recommended_capture_profile": {
                "left_inset_ratio": 0.05,
                "right_inset_ratio": 0.05,
                "top_ratio": 0.54,
                "bottom_inset_ratio": 0.10,
            },
            "recommended_capture_profile_confidence": 0.8,
        }
    applied = await plugin.galgame_apply_recommended_ocr_capture_profile(confirm=True)
    assert isinstance(applied, Ok)

    failure_runtime = {
        "detail": "ocr_capture_diagnostic_required",
        "ocr_capture_diagnostic_required": True,
        "consecutive_no_text_polls": 3,
    }
    await plugin._update_ocr_capture_profile_rollback_state(failure_runtime)
    await plugin._update_ocr_capture_profile_rollback_state(failure_runtime)

    with plugin._state_lock:
        assert "DemoGame.exe" not in plugin._state.ocr_capture_profiles
    assert plugin._ocr_capture_profile_pending_rollback == {}
    assert plugin._ocr_capture_profile_last_rollback_reason.startswith("recommended_profile_failed:")


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_aihong_stage_specific_capture_profiles_preserve_two_stage_resolution(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True},
        ),
    )
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()

    saved = await plugin.galgame_set_ocr_capture_profile(
        process_name="TheLamentingGeese.exe",
        stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
        left_inset_ratio=0.11,
        right_inset_ratio=0.12,
        top_ratio=0.61,
        bottom_inset_ratio=0.14,
    )

    assert isinstance(saved, Ok)
    assert saved.value["stage"] == OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
    with plugin._state_lock:
        stored = plugin._state.ocr_capture_profiles["TheLamentingGeese.exe"]
        assert stored[OCR_CAPTURE_PROFILE_STAGE_DIALOGUE]["top_ratio"] == pytest.approx(0.61)

    assert plugin._ocr_reader_manager is not None
    target = DetectedGameWindow(
        hwnd=301,
        title="哀鸿",
        process_name="TheLamentingGeese.exe",
        pid=6001,
    )

    dialogue_profile = plugin._ocr_reader_manager._capture_profile_for_target(
        target,
        stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    )
    menu_profile = plugin._ocr_reader_manager._capture_profile_for_target(
        target,
        stage=OCR_CAPTURE_PROFILE_STAGE_MENU,
    )

    assert plugin._ocr_reader_manager._should_use_aihong_two_stage(target) is True
    assert dialogue_profile.top_ratio == pytest.approx(0.61)
    assert menu_profile.top_ratio == pytest.approx(0.0)
    assert menu_profile.bottom_inset_ratio == pytest.approx(0.0)


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_aihong_stage_specific_capture_profiles_can_save_and_clear_per_stage(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True},
        ),
    )
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()

    dialogue_saved = await plugin.galgame_set_ocr_capture_profile(
        process_name="TheLamentingGeese.exe",
        stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
        left_inset_ratio=0.09,
        right_inset_ratio=0.10,
        top_ratio=0.62,
        bottom_inset_ratio=0.15,
    )
    menu_saved = await plugin.galgame_set_ocr_capture_profile(
        process_name="TheLamentingGeese.exe",
        stage=OCR_CAPTURE_PROFILE_STAGE_MENU,
        left_inset_ratio=0.18,
        right_inset_ratio=0.19,
        top_ratio=0.38,
        bottom_inset_ratio=0.31,
    )

    assert isinstance(dialogue_saved, Ok)
    assert isinstance(menu_saved, Ok)
    with plugin._state_lock:
        stored = plugin._state.ocr_capture_profiles["TheLamentingGeese.exe"]
        assert stored[OCR_CAPTURE_PROFILE_STAGE_DIALOGUE]["left_inset_ratio"] == pytest.approx(0.09)
        assert stored[OCR_CAPTURE_PROFILE_STAGE_MENU]["top_ratio"] == pytest.approx(0.38)
    restored, _warnings = plugin._persist.load()
    restored_entry = restored[STORE_OCR_CAPTURE_PROFILES]["TheLamentingGeese.exe"]
    assert restored_entry[OCR_CAPTURE_PROFILE_STAGE_DIALOGUE]["bottom_inset_ratio"] == pytest.approx(0.15)
    assert restored_entry[OCR_CAPTURE_PROFILE_STAGE_MENU]["right_inset_ratio"] == pytest.approx(0.19)

    cleared = await plugin.galgame_set_ocr_capture_profile(
        process_name="TheLamentingGeese.exe",
        stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
        clear=True,
    )

    assert isinstance(cleared, Ok)
    with plugin._state_lock:
        stored = plugin._state.ocr_capture_profiles["TheLamentingGeese.exe"]
        assert OCR_CAPTURE_PROFILE_STAGE_DIALOGUE not in stored
        assert OCR_CAPTURE_PROFILE_STAGE_MENU in stored


@pytest.mark.plugin_unit
def test_store_load_preserves_legacy_and_window_bucket_capture_profiles(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    bucket_key = build_ocr_capture_profile_bucket_key(1280, 720).lower()

    plugin._persist._write(
        STORE_OCR_CAPTURE_PROFILES,
        {
            "Legacy.exe": {
                "left_inset_ratio": 0.08,
                "right_inset_ratio": 0.06,
                "top_ratio": 0.34,
                "bottom_inset_ratio": 0.22,
            },
            "DemoGame.exe": {
                "default": {
                    "left_inset_ratio": 0.05,
                    "right_inset_ratio": 0.05,
                    "top_ratio": 0.62,
                    "bottom_inset_ratio": 0.08,
                },
                OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY: {
                    bucket_key: {
                        "width": 1280,
                        "height": 720,
                        "aspect_ratio": 1.7778,
                        "stages": {
                            OCR_CAPTURE_PROFILE_STAGE_DIALOGUE: {
                                "left_inset_ratio": 0.09,
                                "right_inset_ratio": 0.11,
                                "top_ratio": 0.48,
                                "bottom_inset_ratio": 0.13,
                            }
                        },
                    }
                },
            },
        },
    )

    restored, warnings = plugin._persist.load()

    assert warnings == []
    restored_profiles = restored[STORE_OCR_CAPTURE_PROFILES]
    assert restored_profiles["Legacy.exe"]["top_ratio"] == pytest.approx(0.34)
    assert restored_profiles["DemoGame.exe"]["default"]["top_ratio"] == pytest.approx(0.62)
    assert (
        restored_profiles["DemoGame.exe"][OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY][bucket_key]["stages"][
            OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
        ]["top_ratio"]
        == pytest.approx(0.48)
    )

    plugin._persist.persist_ocr_capture_profiles(restored_profiles)
    persisted, persist_warnings = plugin._persist.load()

    assert persist_warnings == []
    assert (
        persisted[STORE_OCR_CAPTURE_PROFILES]["DemoGame.exe"][OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY][bucket_key][
            "width"
        ]
        == 1280
    )


@pytest.mark.plugin_unit
def test_ocr_capture_profile_exact_bucket_wins_over_process_fallback(tmp_path: Path) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(_make_effective_config(bridge_root, ocr_reader={"enabled": True})),
        platform_fn=lambda: True,
        window_scanner=lambda: [],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    bucket_key = build_ocr_capture_profile_bucket_key(1280, 720).lower()
    manager.update_capture_profiles(
        {
            "DemoGame.exe": {
                "default": {
                    "left_inset_ratio": 0.05,
                    "right_inset_ratio": 0.05,
                    "top_ratio": 0.62,
                    "bottom_inset_ratio": 0.08,
                },
                OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY: {
                    bucket_key: {
                        "width": 1280,
                        "height": 720,
                        "aspect_ratio": 1.7778,
                        "stages": {
                            OCR_CAPTURE_PROFILE_STAGE_DIALOGUE: {
                                "left_inset_ratio": 0.07,
                                "right_inset_ratio": 0.08,
                                "top_ratio": 0.44,
                                "bottom_inset_ratio": 0.12,
                            }
                        },
                    }
                },
            }
        }
    )

    selection = manager._capture_profile_selection_for_target(
        DetectedGameWindow(
            hwnd=11,
            title="Demo",
            process_name="DemoGame.exe",
            pid=9001,
            width=1280,
            height=720,
        ),
        stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    )

    assert selection.match_source == OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_EXACT
    assert selection.bucket_key == bucket_key
    assert selection.profile.top_ratio == pytest.approx(0.44)


@pytest.mark.plugin_unit
def test_ocr_capture_profile_uses_nearest_aspect_bucket_when_exact_size_missing(
    tmp_path: Path,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(_make_effective_config(bridge_root, ocr_reader={"enabled": True})),
        platform_fn=lambda: True,
        window_scanner=lambda: [],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    bucket_key = build_ocr_capture_profile_bucket_key(1600, 900).lower()
    manager.update_capture_profiles(
        {
            "DemoGame.exe": {
                "default": {
                    "left_inset_ratio": 0.05,
                    "right_inset_ratio": 0.05,
                    "top_ratio": 0.62,
                    "bottom_inset_ratio": 0.08,
                },
                OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY: {
                    bucket_key: {
                        "width": 1600,
                        "height": 900,
                        "aspect_ratio": 1.7778,
                        "stages": {
                            OCR_CAPTURE_PROFILE_STAGE_DIALOGUE: {
                                "left_inset_ratio": 0.06,
                                "right_inset_ratio": 0.07,
                                "top_ratio": 0.46,
                                "bottom_inset_ratio": 0.10,
                            }
                        },
                    }
                },
            }
        }
    )

    selection = manager._capture_profile_selection_for_target(
        DetectedGameWindow(
            hwnd=12,
            title="Demo",
            process_name="DemoGame.exe",
            pid=9002,
            width=1920,
            height=1080,
        ),
        stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    )

    assert selection.match_source == OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_ASPECT_NEAREST
    assert selection.bucket_key == bucket_key
    assert selection.profile.top_ratio == pytest.approx(0.46)


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_set_ocr_capture_profile_window_bucket_only_updates_current_bucket(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True},
        ),
    )
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    bucket_key = build_ocr_capture_profile_bucket_key(1280, 720).lower()

    with plugin._state_lock:
        plugin._state.ocr_reader_runtime = {
            "process_name": "DemoGame.exe",
            "width": 1280,
            "height": 720,
        }

    await plugin.galgame_set_ocr_capture_profile(
        process_name="DemoGame.exe",
        stage="default",
        save_scope=OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK,
        left_inset_ratio=0.05,
        right_inset_ratio=0.05,
        top_ratio=0.62,
        bottom_inset_ratio=0.08,
    )
    with plugin._state_lock:
        plugin._state.ocr_reader_runtime = {
            "process_name": "DemoGame.exe",
            "width": 1280,
            "height": 720,
        }
    saved = await plugin.galgame_set_ocr_capture_profile(
        process_name="DemoGame.exe",
        stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
        save_scope=OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET,
        left_inset_ratio=0.09,
        right_inset_ratio=0.11,
        top_ratio=0.48,
        bottom_inset_ratio=0.12,
    )

    assert isinstance(saved, Ok)
    with plugin._state_lock:
        stored = plugin._state.ocr_capture_profiles["DemoGame.exe"]
        assert stored["default"]["top_ratio"] == pytest.approx(0.62)
        assert (
            stored[OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY][bucket_key]["stages"][
                OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
            ]["top_ratio"]
            == pytest.approx(0.48)
        )
    restored, _warnings = plugin._persist.load()
    assert (
        restored[STORE_OCR_CAPTURE_PROFILES]["DemoGame.exe"][OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY][bucket_key][
            "stages"
        ][OCR_CAPTURE_PROFILE_STAGE_DIALOGUE]["bottom_inset_ratio"]
        == pytest.approx(0.12)
    )


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_set_ocr_capture_profile_window_bucket_refreshes_runtime_without_bridge_poll(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True},
        ),
    )
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    bucket_key = build_ocr_capture_profile_bucket_key(1280, 720).lower()
    target = DetectedGameWindow(
        hwnd=901,
        title="Demo Window",
        process_name="DemoGame.exe",
        pid=8801,
        width=1280,
        height=720,
    )
    manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        platform_fn=lambda: True,
        window_scanner=lambda: [],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    manager._attached_window = target
    manager._runtime.enabled = True
    manager._runtime.status = "active"
    manager._runtime.capture_stage = OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
    plugin._ocr_reader_manager = manager
    with plugin._state_lock:
        plugin._state.ocr_reader_runtime = {
            "enabled": True,
            "status": "active",
            "process_name": "DemoGame.exe",
            "pid": 8801,
            "window_title": "Demo Window",
            "width": 1280,
            "height": 720,
            "capture_stage": OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
            "capture_profile_match_source": "builtin_preset",
            "capture_profile_bucket_key": "",
        }

    async def _unexpected_poll(*, force: bool = False):
        raise AssertionError(f"unexpected bridge poll during OCR profile save: force={force}")

    plugin._poll_bridge = _unexpected_poll

    saved = await plugin.galgame_set_ocr_capture_profile(
        process_name="DemoGame.exe",
        stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
        save_scope=OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET,
        left_inset_ratio=0.09,
        right_inset_ratio=0.11,
        top_ratio=0.48,
        bottom_inset_ratio=0.12,
    )

    assert isinstance(saved, Ok)
    assert (
        saved.value["status"]["ocr_reader_runtime"]["capture_profile_match_source"]
        == OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_EXACT
    )
    assert saved.value["status"]["ocr_reader_runtime"]["capture_profile_bucket_key"] == bucket_key
    with plugin._state_lock:
        assert (
            plugin._state.ocr_reader_runtime["capture_profile_match_source"]
            == OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_EXACT
        )
        assert plugin._state.ocr_reader_runtime["capture_profile_bucket_key"] == bucket_key


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_set_ocr_capture_profile_process_fallback_only_updates_fallback(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True},
        ),
    )
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    bucket_key = build_ocr_capture_profile_bucket_key(1280, 720).lower()

    with plugin._state_lock:
        plugin._state.ocr_reader_runtime = {
            "process_name": "DemoGame.exe",
            "width": 1280,
            "height": 720,
        }
        plugin._state.ocr_capture_profiles = {
            "DemoGame.exe": {
                OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY: {
                    bucket_key: {
                        "width": 1280,
                        "height": 720,
                        "aspect_ratio": 1.7778,
                        "stages": {
                            OCR_CAPTURE_PROFILE_STAGE_DIALOGUE: {
                                "left_inset_ratio": 0.09,
                                "right_inset_ratio": 0.11,
                                "top_ratio": 0.48,
                                "bottom_inset_ratio": 0.12,
                            }
                        },
                    }
                }
            }
        }
    plugin._persist.persist_ocr_capture_profiles(plugin._state.ocr_capture_profiles)

    saved = await plugin.galgame_set_ocr_capture_profile(
        process_name="DemoGame.exe",
        stage="default",
        save_scope=OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK,
        left_inset_ratio=0.05,
        right_inset_ratio=0.06,
        top_ratio=0.60,
        bottom_inset_ratio=0.09,
    )

    assert isinstance(saved, Ok)
    with plugin._state_lock:
        stored = plugin._state.ocr_capture_profiles["DemoGame.exe"]
        assert stored["default"]["top_ratio"] == pytest.approx(0.60)
        assert (
            stored[OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY][bucket_key]["stages"][
                OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
            ]["top_ratio"]
            == pytest.approx(0.48)
        )


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_runtime_exposes_window_bucket_match_metadata(tmp_path: Path) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)
    cfg = _make_effective_config(
        bridge_root,
        ocr_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
            "poll_interval_seconds": 999.0,
        },
    )
    bucket_key = build_ocr_capture_profile_bucket_key(1280, 720).lower()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(cfg),
        time_fn=lambda: 1713000000.0,
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=401,
                title="Demo Window",
                process_name="DemoGame.exe",
                pid=7001,
                width=1280,
                height=720,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(["测试文本", "测试文本"]),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: 1713000000.0,
        ),
    )
    manager.update_capture_profiles(
        {
            "DemoGame.exe": {
                OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY: {
                    bucket_key: {
                        "width": 1280,
                        "height": 720,
                        "aspect_ratio": 1.7778,
                        "stages": {
                            OCR_CAPTURE_PROFILE_STAGE_DIALOGUE: {
                                "left_inset_ratio": 0.08,
                                "right_inset_ratio": 0.06,
                                "top_ratio": 0.47,
                                "bottom_inset_ratio": 0.11,
                            }
                        },
                    }
                }
            }
        }
    )

    result = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    assert result.runtime["width"] == 1280
    assert result.runtime["height"] == 720
    assert result.runtime["aspect_ratio"] == pytest.approx(1280 / 720, rel=1e-4)
    assert result.runtime["capture_profile_match_source"] == OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_EXACT
    assert result.runtime["capture_profile_bucket_key"] == bucket_key


@pytest.mark.plugin_unit
def test_auto_recalibrate_ocr_dialogue_profile_selects_best_candidate_and_returns_bucket(
    tmp_path: Path,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(_make_effective_config(bridge_root, ocr_reader={"enabled": True})),
        platform_fn=lambda: True,
        window_scanner=lambda: [],
        capture_backend=_FakeImageCaptureBackend(size=(1000, 500)),
        ocr_backend=_CropAwareOcrBackend(
            lambda image: "这是自动校准命中的对白文本。"
            if getattr(image, "crop_box", None) == (50, 250, 950, 440)
            else "菜单"
        ),
    )
    manager._attached_window = DetectedGameWindow(
        hwnd=501,
        title="Demo Window",
        process_name="DemoGame.exe",
        pid=7101,
        width=1000,
        height=500,
    )

    payload = manager.auto_recalibrate_dialogue_profile()

    assert payload["save_scope"] == OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET
    assert payload["bucket_key"] == "1000x500"
    assert payload["capture_profile"]["top_ratio"] == pytest.approx(0.50)
    assert payload["capture_profile"]["bottom_inset_ratio"] == pytest.approx(0.12)
    assert payload["sample_text"] == "这是自动校准命中的对白文本。"


@pytest.mark.plugin_unit
def test_auto_recalibrate_ocr_dialogue_profile_excludes_title_bar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    monkeypatch.setattr(
        galgame_ocr_reader,
        "_target_client_rect",
        lambda target: (0, 50, target.width, target.height),
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(
            _make_effective_config(
                bridge_root,
                ocr_reader={
                    "enabled": True,
                    "top_ratio": 0.02,
                    "bottom_inset_ratio": 0.58,
                },
            )
        ),
        platform_fn=lambda: True,
        window_scanner=lambda: [],
        capture_backend=_FakeImageCaptureBackend(size=(1000, 500)),
        ocr_backend=_CropAwareOcrBackend(
            lambda image: "这是排除标题栏后的对白文本。"
            if getattr(image, "crop_box", (0, 0, 0, 0))[1] >= 60
            else "the lamenting geese"
        ),
    )
    manager._attached_window = DetectedGameWindow(
        hwnd=503,
        title="Demo Window",
        process_name="DemoGame.exe",
        pid=7103,
        width=1000,
        height=500,
    )

    payload = manager.auto_recalibrate_dialogue_profile()

    assert payload["capture_profile"]["top_ratio"] >= 0.12
    assert payload["sample_text"] == "这是排除标题栏后的对白文本。"


@pytest.mark.plugin_unit
def test_auto_recalibrate_aihong_dialogue_profile_can_escape_stale_narrow_bucket(
    tmp_path: Path,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    target = DetectedGameWindow(
        hwnd=502,
        title="TheLamentingGeese",
        process_name="TheLamentingGeese.exe",
        pid=7102,
        width=1040,
        height=807,
    )
    expected_box = (0, 484, 1040, 766)
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(_make_effective_config(bridge_root, ocr_reader={"enabled": True})),
        platform_fn=lambda: True,
        window_scanner=lambda: [],
        capture_backend=_FakeImageCaptureBackend(size=(1040, 807)),
        ocr_backend=_CropAwareOcrBackend(
            lambda image: "王生：算了，没事。"
            if getattr(image, "crop_box", None) == expected_box
            else ""
        ),
    )
    manager.update_capture_profiles(
        {
            "TheLamentingGeese.exe": {
                "__window_buckets__": {
                    "1040x807": {
                        "width": 1040,
                        "height": 807,
                        "aspect_ratio": 1.2887,
                        "stages": {
                            "dialogue_stage": {
                                "left_inset_ratio": 0.05,
                                "right_inset_ratio": 0.24,
                                "top_ratio": 0.69,
                                "bottom_inset_ratio": 0.12,
                            }
                        },
                    }
                }
            }
        }
    )
    manager._attached_window = target
    payload = manager.auto_recalibrate_dialogue_profile()

    assert payload["bucket_key"] == "1040x807"
    assert payload["capture_profile"]["left_inset_ratio"] == pytest.approx(0.0)
    assert payload["capture_profile"]["right_inset_ratio"] == pytest.approx(0.0)
    assert payload["capture_profile"]["top_ratio"] == pytest.approx(0.60)
    assert payload["capture_profile"]["bottom_inset_ratio"] == pytest.approx(0.05)
    assert payload["sample_text"] == "王生：算了，没事。"


@pytest.mark.plugin_unit
def test_ocr_reader_foreground_refresh_updates_target_without_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    target = DetectedGameWindow(
        hwnd=610,
        title="TheLamentingGeese",
        process_name="TheLamentingGeese.exe",
        pid=8101,
        width=1040,
        height=807,
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(_make_effective_config(bridge_root, ocr_reader={"enabled": True})),
        time_fn=lambda: 1713000000.0,
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(["不会被调用"]),
    )
    manager.list_windows_snapshot()
    manager.update_window_target(
        {
            "mode": "manual",
            "window_key": target.window_key,
            "process_name": target.process_name,
            "normalized_title": target.normalized_title,
            "pid": target.pid,
            "last_known_hwnd": target.hwnd,
        }
    )

    monkeypatch.setattr(
        galgame_ocr_reader,
        "_target_window_capture_state",
        lambda _target: (True, False, True, ""),
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: target.hwnd)
    runtime = manager.refresh_foreground_state()

    assert runtime["target_is_foreground"] is True
    assert runtime["foreground_hwnd"] == target.hwnd
    assert runtime["target_hwnd"] == target.hwnd
    assert runtime["foreground_refresh_detail"] == "manual_target_exact:foreground_hwnd"

    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 999999)
    runtime = manager.refresh_foreground_state()

    assert runtime["target_is_foreground"] is False
    assert runtime["foreground_hwnd"] == 999999
    assert runtime["target_hwnd"] == target.hwnd
    assert runtime["foreground_refresh_detail"] == "manual_target_exact:background"
    assert runtime["target_window_visible"] is True
    assert runtime["target_window_minimized"] is False
    assert runtime["ocr_window_capture_eligible"] is True
    assert runtime["ocr_window_capture_available"] is False
    assert runtime["input_target_foreground"] is False
    assert runtime["input_target_block_reason"] == "target_not_foreground"

    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 888888)
    monkeypatch.setattr(galgame_ocr_reader, "_window_process_id", lambda hwnd: target.pid)
    runtime = manager.refresh_foreground_state()

    assert runtime["target_is_foreground"] is True
    assert runtime["input_target_foreground"] is True
    assert runtime["input_target_block_reason"] == ""
    assert runtime["foreground_hwnd"] == 888888
    assert runtime["target_hwnd"] == target.hwnd
    assert runtime["foreground_refresh_detail"] == "manual_target_exact:foreground_pid"


@pytest.mark.plugin_unit
def test_ocr_reader_foreground_refresh_reports_minimized_target_capture_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    target = DetectedGameWindow(
        hwnd=611,
        title="TheLamentingGeese",
        process_name="TheLamentingGeese.exe",
        pid=8101,
        width=1040,
        height=807,
        is_minimized=True,
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(_make_effective_config(bridge_root, ocr_reader={"enabled": True})),
        time_fn=lambda: 1713000000.0,
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(["不会被调用"]),
    )
    manager.list_windows_snapshot()
    manager.update_window_target(
        {
            "mode": "manual",
            "window_key": target.window_key,
            "process_name": target.process_name,
            "normalized_title": target.normalized_title,
            "pid": target.pid,
            "last_known_hwnd": target.hwnd,
        }
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 999999)
    monkeypatch.setattr(
        galgame_ocr_reader,
        "_target_window_capture_state",
        lambda _target: (True, True, False, "target_minimized"),
    )

    runtime = manager.refresh_foreground_state()

    assert runtime["target_window_visible"] is True
    assert runtime["target_window_minimized"] is True
    assert runtime["ocr_window_capture_eligible"] is False
    assert runtime["ocr_window_capture_available"] is False
    assert runtime["ocr_window_capture_block_reason"] == "target_minimized"
    assert runtime["input_target_foreground"] is False
    assert runtime["input_target_block_reason"] == "target_not_foreground"


@pytest.mark.plugin_unit
def test_ocr_reader_foreground_refresh_rebounds_manual_target_by_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    rebound = DetectedGameWindow(
        hwnd=711,
        title="TheLamentingGeese",
        process_name="TheLamentingGeese.exe",
        pid=8102,
        width=1040,
        height=807,
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(_make_effective_config(bridge_root, ocr_reader={"enabled": True})),
        time_fn=lambda: 1713000000.0,
        platform_fn=lambda: True,
        window_scanner=lambda: [rebound],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(["不会被调用"]),
    )
    manager.list_windows_snapshot()
    manager.update_window_target(
        {
            "mode": "manual",
            "window_key": "stale-window-key",
            "process_name": rebound.process_name,
            "normalized_title": rebound.normalized_title,
            "pid": 9999,
            "last_known_hwnd": 9999,
        }
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: rebound.hwnd)

    runtime = manager.refresh_foreground_state()

    assert runtime["target_is_foreground"] is True
    assert runtime["target_hwnd"] == rebound.hwnd
    assert runtime["foreground_refresh_detail"] == "manual_target_rebound:foreground_hwnd"


@pytest.mark.plugin_unit
def test_ocr_reader_foreground_advance_ignores_background_click_then_accepts_game_click(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    target = DetectedGameWindow(
        hwnd=721,
        title="TheLamentingGeese",
        process_name="TheLamentingGeese.exe",
        pid=8201,
        width=1040,
        height=807,
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(_make_effective_config(bridge_root, ocr_reader={"enabled": True})),
        time_fn=lambda: 1713000100.0,
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    manager._attached_window = target
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: target.hwnd)
    monkeypatch.setattr(galgame_ocr_reader, "_root_window_handle", lambda hwnd: int(hwnd or 0))
    monkeypatch.setattr(galgame_ocr_reader, "_window_process_id", lambda hwnd: 0)

    monitor = _FakeAdvanceInputMonitor(
        [
            galgame_ocr_reader._MouseWheelEvent(
                seq=1,
                ts=1713000100.0,
                delta=0,
                foreground_hwnd=999,
                point_hwnd=999,
                kind="left_click",
            )
        ]
    )
    manager._wheel_monitor = monitor

    assert manager.consume_foreground_advance_input() is False
    assert manager._runtime.foreground_advance_last_matched is False

    monitor.events.append(
        galgame_ocr_reader._MouseWheelEvent(
            seq=2,
            ts=1713000100.2,
            delta=0,
            foreground_hwnd=target.hwnd,
            point_hwnd=target.hwnd,
            kind="left_click",
        )
    )

    assert manager.consume_foreground_advance_input() is True
    assert manager._runtime.foreground_advance_last_kind == "left_click"
    assert manager._runtime.foreground_advance_last_matched is True
    assert manager.consume_foreground_advance_input() is False


@pytest.mark.plugin_unit
def test_ocr_reader_foreground_advance_accepts_mouse_wheel_down(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    target = DetectedGameWindow(
        hwnd=722,
        title="TheLamentingGeese",
        process_name="TheLamentingGeese.exe",
        pid=8202,
        width=1040,
        height=807,
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(_make_effective_config(bridge_root, ocr_reader={"enabled": True})),
        time_fn=lambda: 1713000200.0,
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    manager.list_windows_snapshot()
    manager.update_window_target(
        {
            "mode": "manual",
            "window_key": target.window_key,
            "process_name": target.process_name,
            "normalized_title": target.normalized_title,
            "pid": target.pid,
            "last_known_hwnd": target.hwnd,
        }
    )
    manager._wheel_monitor = _FakeAdvanceInputMonitor(
        [
            galgame_ocr_reader._MouseWheelEvent(
                seq=1,
                ts=1713000200.0,
                delta=-120,
                foreground_hwnd=target.hwnd,
                point_hwnd=target.hwnd,
                kind="wheel",
            )
        ]
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: target.hwnd)
    monkeypatch.setattr(galgame_ocr_reader, "_root_window_handle", lambda hwnd: int(hwnd or 0))
    monkeypatch.setattr(galgame_ocr_reader, "_window_process_id", lambda hwnd: 0)

    assert manager.consume_foreground_advance_input() is True
    assert manager._runtime.foreground_advance_last_kind == "wheel"
    assert manager._runtime.foreground_advance_last_delta == -120
    assert manager._runtime.foreground_advance_last_matched is True


@pytest.mark.plugin_unit
def test_ocr_reader_foreground_advance_reports_coalesced_click_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    target = DetectedGameWindow(
        hwnd=723,
        title="TheLamentingGeese",
        process_name="TheLamentingGeese.exe",
        pid=8203,
        width=1040,
        height=807,
    )
    clock = {"now": 1713000300.5}
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(_make_effective_config(bridge_root, ocr_reader={"enabled": True})),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    manager._attached_window = target
    manager._wheel_monitor = _FakeAdvanceInputMonitor(
        [
            galgame_ocr_reader._MouseWheelEvent(
                seq=1,
                ts=1713000300.0,
                delta=0,
                foreground_hwnd=target.hwnd,
                point_hwnd=target.hwnd,
                kind="left_click",
            ),
            galgame_ocr_reader._MouseWheelEvent(
                seq=2,
                ts=1713000300.2,
                delta=0,
                foreground_hwnd=target.hwnd,
                point_hwnd=target.hwnd,
                kind="left_click",
            ),
        ]
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: target.hwnd)
    monkeypatch.setattr(galgame_ocr_reader, "_root_window_handle", lambda hwnd: int(hwnd or 0))
    monkeypatch.setattr(galgame_ocr_reader, "_window_process_id", lambda hwnd: 0)

    result = manager.consume_foreground_advance_inputs()

    assert result.triggered is True
    assert result.consumed_count == 2
    assert result.matched_count == 2
    assert result.coalesced is True
    assert result.coalesced_count == 1
    assert abs(result.last_event_age_seconds - 0.3) < 1e-6
    assert manager._runtime.foreground_advance_consumed_count == 2
    assert manager._runtime.foreground_advance_matched_count == 2
    assert manager._runtime.foreground_advance_coalesced_count == 1
    assert abs(manager._runtime.foreground_advance_last_event_age_seconds - 0.3) < 1e-6


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_auto_detects_single_confident_window_without_foreground(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    target = DetectedGameWindow(
        hwnd=812,
        title="TheLamentingGeese",
        process_name="TheLamentingGeese.exe",
        pid=9102,
        width=1040,
        height=807,
    )
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(_make_effective_config(bridge_root, ocr_reader={"enabled": True})),
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(["王生：单窗口兜底。"]),
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 0)

    result = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    assert result.runtime["status"] == "active"
    assert result.runtime["detail"] == "receiving_text"
    assert result.runtime["target_selection_mode"] == "auto"
    assert result.runtime["target_selection_detail"] == "single_confident_candidate"
    assert result.runtime["candidate_count"] == 1
    assert capture_backend.capture_calls == 1
    assert manager._writer.last_seq >= 1


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_auto_detects_foreground_window_before_manual_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    target = DetectedGameWindow(
        hwnd=813,
        title="TheLamentingGeese",
        process_name="TheLamentingGeese.exe",
        pid=9103,
        width=1040,
        height=807,
    )
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(
            _make_effective_config(
                bridge_root,
                ocr_reader={
                    "enabled": True,
                    "screen_awareness_full_frame_ocr": True,
                    "screen_awareness_multi_region_ocr": True,
                    "screen_awareness_visual_rules": True,
                },
            )
        ),
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(["王生\n算了，没事。"]),
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: target.hwnd)

    result = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    assert result.runtime["target_selection_mode"] == "auto"
    assert result.runtime["target_selection_detail"] == "foreground_window"
    assert result.runtime["effective_process_name"] == "TheLamentingGeese.exe"
    assert capture_backend.capture_calls == 1
    assert manager._writer.last_seq >= 1


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_does_not_auto_capture_common_non_game_foreground_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    browser = DetectedGameWindow(
        hwnd=814,
        title="Some Web Page",
        process_name="chrome.exe",
        pid=9104,
        class_name="Chrome_WidgetWin_1",
        width=1280,
        height=800,
    )
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(_make_effective_config(bridge_root, ocr_reader={"enabled": True})),
        platform_fn=lambda: True,
        window_scanner=lambda: [browser],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(["不应读取网页文本"]),
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: browser.hwnd)

    result = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    assert result.runtime["status"] == "idle"
    assert result.runtime["detail"] == "waiting_for_valid_window"
    assert result.runtime["target_selection_detail"] == "foreground_window_needs_manual_confirmation"
    assert capture_backend.capture_calls == 0
    assert manager._writer.last_seq == 0


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_ignores_chinese_plugin_ui_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    target = DetectedGameWindow(
        hwnd=815,
        title="TheLamentingGeese",
        process_name="TheLamentingGeese.exe",
        pid=9105,
        width=1040,
        height=807,
    )
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(_make_effective_config(bridge_root, ocr_reader={"enabled": True})),
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(["运行控制 模式静默 静默进入待机恢复活跃"]),
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: target.hwnd)

    result = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    assert capture_backend.capture_calls == 1
    assert manager._writer.last_seq == 0
    assert result.runtime["last_raw_ocr_text"] == ""
    assert result.runtime["last_rejected_ocr_text"] == "运行控制 模式静默 静默进入待机恢复活跃"
    assert result.runtime["last_rejected_ocr_reason"] == "self_ui_guard"
    assert result.runtime["screen_awareness_last_skip_reason"] == "rejected_primary_text"
    assert any("N.E.K.O plugin UI" in warning for warning in result.warnings)


@pytest.mark.plugin_unit
def test_ocr_reader_capture_backend_config_is_sanitized(tmp_path: Path) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)

    config = build_config(
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True, "capture_backend": "dxcam"},
        )
    )
    assert config.ocr_reader_capture_backend == "dxcam"

    smart_config = build_config(
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True, "capture_backend": "smart"},
        )
    )
    assert smart_config.ocr_reader_capture_backend == "smart"

    fallback_config = build_config(
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True, "capture_backend": "unknown"},
        )
    )
    assert fallback_config.ocr_reader_capture_backend == "smart"


@pytest.mark.plugin_unit
def test_win32_capture_backend_selection_orders_dxcam_first_for_auto() -> None:
    # Default chain: dxcam → mss → pyautogui (PrintWindow dropped from default
    # fallback because it's a "render to DC" mechanism that often produces
    # stale frames on DirectX/Unity games and is slower than BitBlt-based
    # backends; still reachable as explicit selection + Smart background).
    backend = galgame_ocr_reader.Win32CaptureBackend(selection="auto")
    assert [item.kind for item in backend._backends] == ["dxcam", "mss", "pyautogui"]

    dxcam_backend = galgame_ocr_reader.Win32CaptureBackend(selection="dxcam")
    assert [item.kind for item in dxcam_backend._backends] == ["dxcam", "mss", "pyautogui"]

    mss_backend = galgame_ocr_reader.Win32CaptureBackend(selection="mss")
    assert [item.kind for item in mss_backend._backends] == ["mss", "dxcam", "pyautogui"]

    pyautogui_backend = galgame_ocr_reader.Win32CaptureBackend(selection="pyautogui")
    assert [item.kind for item in pyautogui_backend._backends] == ["pyautogui", "dxcam", "mss"]

    # Legacy "imagegrab" selection migrates to MSS for backward compatibility.
    legacy_imagegrab_backend = galgame_ocr_reader.Win32CaptureBackend(selection="imagegrab")
    assert legacy_imagegrab_backend.selection == "mss"
    assert [item.kind for item in legacy_imagegrab_backend._backends] == ["mss", "dxcam", "pyautogui"]

    # PrintWindow as explicit selection still falls through to all GDI backends.
    printwindow_backend = galgame_ocr_reader.Win32CaptureBackend(selection="printwindow")
    assert [item.kind for item in printwindow_backend._backends] == [
        "printwindow", "dxcam", "mss", "pyautogui"
    ]


@pytest.mark.plugin_unit
def test_win32_capture_backend_smart_uses_target_aware_order() -> None:
    backend = galgame_ocr_reader.Win32CaptureBackend(selection="smart")
    foreground = DetectedGameWindow(
        hwnd=1,
        title="Demo",
        process_name="DemoGame.exe",
        pid=100,
        is_foreground=True,
    )
    background = DetectedGameWindow(
        hwnd=2,
        title="Demo",
        process_name="DemoGame.exe",
        pid=101,
        is_foreground=False,
    )

    assert [item.kind for item in backend._ordered_backends_for_target(foreground)] == [
        "dxcam",
        "mss",
        "pyautogui",
    ]
    # Background target: only PrintWindow can plausibly capture occluded windows.
    assert [item.kind for item in backend._ordered_backends_for_target(background)] == [
        "printwindow"
    ]


@pytest.mark.plugin_unit
def test_win32_capture_backend_printwindow_strict_for_background_target() -> None:
    # Explicit `selection="printwindow"` should ONLY use PrintWindow on a
    # background/occluded target. Falling through to dxcam/mss/pyautogui
    # would silently OCR the occluding window (screen pixels, not target
    # window) — defeats the whole reason a user picks PrintWindow explicitly.
    # Foreground target keeps the fallback chain since the other backends
    # would also see the correct window.
    backend = galgame_ocr_reader.Win32CaptureBackend(selection="printwindow")
    foreground = DetectedGameWindow(
        hwnd=1,
        title="Demo",
        process_name="DemoGame.exe",
        pid=200,
        is_foreground=True,
    )
    background = DetectedGameWindow(
        hwnd=2,
        title="Demo",
        process_name="DemoGame.exe",
        pid=201,
        is_foreground=False,
    )
    assert [item.kind for item in backend._ordered_backends_for_target(foreground)] == [
        "printwindow",
        "dxcam",
        "mss",
        "pyautogui",
    ]
    assert [item.kind for item in backend._ordered_backends_for_target(background)] == [
        "printwindow"
    ]


@pytest.mark.plugin_unit
def test_ocr_stability_ignores_whitelisted_trailing_orphan_only() -> None:
    clean = galgame_ocr_reader._clean_ocr_dialogue_text("三年前初患此病，我便将人视作走兽。")
    orphan = galgame_ocr_reader._clean_ocr_dialogue_text("三年前初患此病，我便将人视作走兽。义")
    assert orphan == clean
    assert galgame_ocr_reader._ocr_stability_key(orphan) == galgame_ocr_reader._ocr_stability_key(clean)
    assert not galgame_ocr_reader._ocr_stability_keys_match(
        galgame_ocr_reader._ocr_stability_key("我喜欢你"),
        galgame_ocr_reader._ocr_stability_key("我喜欢他"),
    )


@pytest.mark.plugin_unit
def test_ocr_poll_latency_samples_auto_degrade_full_screen_awareness(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        ocr_reader={
            "enabled": True,
            "screen_awareness_latency_mode": "full",
        },
    )
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, cfg))
    plugin._cfg = build_config(cfg)
    applied_modes: list[str] = []
    plugin._ocr_reader_manager = SimpleNamespace(
        update_config=lambda config: applied_modes.append(
            config.ocr_reader_screen_awareness_latency_mode
        )
    )

    for duration in (3.2, 3.4, 3.6, 4.0, 5.0):
        plugin._record_ocr_poll_duration({"last_poll_duration_seconds": duration})

    status = plugin._bridge_poll_debug_payload()
    assert plugin._cfg.ocr_reader_screen_awareness_latency_mode == "balanced"
    assert applied_modes == ["balanced"]
    assert status["ocr_poll_latency_sample_count"] == 5
    assert status["ocr_poll_duration_p95_seconds"] > 3.0
    assert status["ocr_auto_degrade_count"] == 1
    assert "full->balanced" in status["ocr_auto_degrade_reason"]


@pytest.mark.plugin_unit
def test_primary_diagnosis_warns_when_ocr_candidate_waits_too_long() -> None:
    diagnosis = galgame_service.build_primary_diagnosis(
        {
            "ocr_reader_runtime": {
                "stable_ocr_block_reason": "waiting_for_repeat",
            },
            "candidate_age_seconds": 9.5,
        }
    )

    assert diagnosis["severity"] == "warning"
    assert diagnosis["title"] == "OCR 候选台词确认过慢"


@pytest.mark.plugin_unit
def test_ocr_background_status_visible_background_readable_when_target_visible_but_not_foreground() -> None:
    status = galgame_service.build_ocr_background_status(
        {
            "ocr_reader_trigger_mode": "after_advance",
            "ocr_reader_runtime": {
                "status": "active",
                "detail": "receiving_text",
                "ocr_context_state": "stable",
                "target_is_foreground": False,
                "input_target_foreground": False,
                "input_target_block_reason": "target_not_foreground",
                "target_window_visible": True,
                "target_window_minimized": False,
                "ocr_window_capture_eligible": True,
                "ocr_window_capture_available": True,
                "ocr_window_capture_block_reason": "",
                "capture_backend_kind": "dxcam",
            },
        }
    )
    diagnosis = galgame_service.build_primary_diagnosis(
        {
            "ocr_reader_trigger_mode": "after_advance",
            "ocr_reader_runtime": {
                "status": "active",
                "detail": "receiving_text",
                "ocr_context_state": "stable",
                "target_is_foreground": False,
                "input_target_foreground": False,
                "input_target_block_reason": "target_not_foreground",
                "target_window_visible": True,
                "target_window_minimized": False,
                "ocr_window_capture_eligible": True,
                "ocr_window_capture_available": True,
                "ocr_window_capture_block_reason": "",
                "capture_backend_kind": "dxcam",
            },
        }
    )

    assert status["state"] == "visible_background_readable"
    assert status["foreground_resume_pending"] is True
    assert status["ocr_window_capture_eligible"] is True
    assert status["ocr_window_capture_available"] is True
    assert status["input_target_foreground"] is False
    assert status["input_target_block_reason"] == "target_not_foreground"
    assert "OCR 可读取可见游戏窗口" in status["message"]
    assert diagnosis["title"] == "OCR 可读，自动输入等待前台"


@pytest.mark.plugin_unit
def test_ocr_background_status_target_minimized_blocks_capture() -> None:
    status = galgame_service.build_ocr_background_status(
        {
            "ocr_reader_trigger_mode": "after_advance",
            "ocr_reader_runtime": {
                "status": "active",
                "detail": "receiving_text",
                "ocr_context_state": "stable",
                "target_is_foreground": False,
                "input_target_foreground": False,
                "input_target_block_reason": "target_not_foreground",
                "target_window_visible": True,
                "target_window_minimized": True,
                "ocr_window_capture_eligible": False,
                "ocr_window_capture_available": False,
                "ocr_window_capture_block_reason": "target_minimized",
                "capture_backend_kind": "dxcam",
            },
        }
    )

    assert status["state"] == "target_unavailable"
    assert status["capture_backend_blocked"] is True
    assert status["ocr_window_capture_eligible"] is False
    assert status["ocr_window_capture_block_reason"] == "target_minimized"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_pauses_background_printwindow_after_blank_frame(
    tmp_path: Path,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    target = DetectedGameWindow(
        hwnd=333,
        title="Background Demo",
        process_name="DemoGame.exe",
        pid=3333,
        width=1280,
        height=720,
        is_foreground=False,
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(_make_effective_config(bridge_root, ocr_reader={"enabled": True})),
        time_fn=lambda: 1710000000.0,
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=_FakePrintWindowBlankCaptureBackend(),
        ocr_backend=_FakeOcrBackend([""]),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: 1710000000.0,
        ),
    )

    result = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    assert result.runtime["detail"] == "capture_failed"
    assert result.runtime["ocr_context_state"] == "capture_failed"
    assert "backend_not_suitable_for_background" in result.runtime["last_capture_error"]
    assert result.runtime["capture_backend_detail"] == "backend_not_suitable_for_background"


@pytest.mark.plugin_unit
def test_ocr_window_inventory_uses_root_hwnd_foreground_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    target = DetectedGameWindow(
        hwnd=200,
        title="Demo",
        process_name="DemoGame.exe",
        pid=9001,
        width=1280,
        height=720,
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(_make_effective_config(bridge_root, ocr_reader={"enabled": True})),
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 201)
    monkeypatch.setattr(
        galgame_ocr_reader,
        "_root_window_handle",
        lambda hwnd: 100 if hwnd in {200, 201} else hwnd,
    )

    eligible, _excluded = manager._scan_window_inventory()

    assert eligible
    assert eligible[0].is_foreground is True


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_marks_stale_capture_backend_after_repeated_same_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    now = [1713000000.0]
    target = DetectedGameWindow(
        hwnd=816,
        title="TheLamentingGeese",
        process_name="TheLamentingGeese.exe",
        pid=9106,
        width=1040,
        height=807,
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(
            _make_effective_config(
                bridge_root,
                ocr_reader={"enabled": True, "poll_interval_seconds": 0.1},
            )
        ),
        time_fn=lambda: now[0],
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(["", "", ""]),
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: target.hwnd)

    result = None
    for _ in range(3):
        result = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
        now[0] += 1.0

    assert result is not None
    assert result.runtime["last_capture_image_hash"]
    assert result.runtime["consecutive_same_capture_frames"] >= 3
    assert result.runtime["stale_capture_backend"] is True
    assert result.runtime["ocr_context_state"] == "stale_capture_backend"


@pytest.mark.plugin_unit
def test_ocr_reader_capture_backend_switch_clears_stale_capture_diagnostics(
    tmp_path: Path,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(
            _make_effective_config(
                bridge_root,
                ocr_reader={
                    "enabled": True,
                    "capture_backend": "dxcam",
                    "trigger_mode": "interval",
                },
                rapidocr={"enabled": False},
            )
        ),
        platform_fn=lambda: False,
        window_scanner=lambda: [],
    )
    manager._last_capture_error = "old capture failure"
    manager._last_capture_image_hash = "same-frame"
    manager._consecutive_same_capture_frames = 5
    manager._stale_capture_backend = True
    manager._runtime.last_capture_error = "old capture failure"
    manager._runtime.last_capture_image_hash = "same-frame"
    manager._runtime.consecutive_same_capture_frames = 5
    manager._runtime.stale_capture_backend = True
    manager._runtime.consecutive_no_text_polls = 3
    manager._runtime.ocr_capture_diagnostic_required = True
    manager._runtime.ocr_context_state = "stale_capture_backend"

    manager.update_config(
        build_config(
            _make_effective_config(
                bridge_root,
                ocr_reader={
                    "enabled": True,
                    "capture_backend": "imagegrab",
                    "trigger_mode": "interval",
                },
                rapidocr={"enabled": False},
            )
        )
    )

    assert manager._last_capture_error == ""
    assert manager._last_capture_image_hash == ""
    assert manager._consecutive_same_capture_frames == 0
    assert manager._stale_capture_backend is False
    assert manager._runtime.last_capture_error == ""
    assert manager._runtime.last_capture_image_hash == ""
    assert manager._runtime.consecutive_same_capture_frames == 0
    assert manager._runtime.stale_capture_backend is False
    assert manager._runtime.consecutive_no_text_polls == 0
    assert manager._runtime.ocr_capture_diagnostic_required is False
    assert manager._runtime.ocr_context_state == ""


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_auto_recalibrate_ocr_dialogue_profile_persists_bucket_and_survives_restart(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True},
        ),
    )
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    target = DetectedGameWindow(
        hwnd=602,
        title="Demo Window",
        process_name="DemoGame.exe",
        pid=7202,
        width=1000,
        height=500,
    )
    manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        platform_fn=lambda: True,
        window_scanner=lambda: [],
        capture_backend=_FakeImageCaptureBackend(size=(1000, 500)),
        ocr_backend=_CropAwareOcrBackend(
            lambda image: "这是自动校准命中的对白文本。"
            if getattr(image, "crop_box", None) == (50, 250, 950, 440)
            else "菜单"
        ),
    )
    manager._attached_window = target
    manager._runtime.enabled = True
    manager._runtime.status = "active"
    manager._runtime.capture_stage = OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
    plugin._ocr_reader_manager = manager
    with plugin._state_lock:
        plugin._state.ocr_reader_runtime = {
            "enabled": True,
            "status": "active",
            "process_name": "DemoGame.exe",
            "pid": 7202,
            "window_title": "Demo Window",
            "width": 1000,
            "height": 500,
            "capture_stage": OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
        }

    async def _unexpected_poll(*, force: bool = False):
        raise AssertionError(f"unexpected bridge poll during auto recalibrate: force={force}")

    plugin._poll_bridge = _unexpected_poll

    result = await plugin.galgame_auto_recalibrate_ocr_dialogue_profile()

    assert isinstance(result, Ok)
    assert result.value["bucket_key"] == "1000x500"
    assert result.value["save_scope"] == OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET
    assert (
        result.value["status"]["ocr_reader_runtime"]["capture_profile_match_source"]
        == OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_EXACT
    )

    await plugin.shutdown()

    restarted = GalgameBridgePlugin(ctx)
    await restarted.startup()

    with restarted._state_lock:
        stored = restarted._state.ocr_capture_profiles["DemoGame.exe"]
        assert (
            stored[OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY]["1000x500"]["stages"][
                OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
            ]["top_ratio"]
            == pytest.approx(0.50)
        )

    restored_manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(_make_effective_config(bridge_root, ocr_reader={"enabled": True})),
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=602,
                title="Demo Window",
                process_name="DemoGame.exe",
                pid=7202,
                width=1000,
                height=500,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(["测试文本", "测试文本"]),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: 1713000000.0,
        ),
    )
    with restarted._state_lock:
        restored_manager.update_capture_profiles(restarted._state.ocr_capture_profiles)

    tick = await restored_manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    assert tick.runtime["capture_profile_match_source"] == OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_EXACT
    assert tick.runtime["capture_profile_bucket_key"] == "1000x500"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_auto_recalibrate_ocr_dialogue_profile_failure_does_not_write_store(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": True},
        ),
    )
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    plugin._ocr_reader_manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        platform_fn=lambda: True,
        window_scanner=lambda: [],
        capture_backend=_FakeImageCaptureBackend(size=(1000, 500)),
        ocr_backend=_CropAwareOcrBackend(lambda image: "菜单"),
    )
    plugin._ocr_reader_manager._attached_window = DetectedGameWindow(
        hwnd=601,
        title="Demo Window",
        process_name="DemoGame.exe",
        pid=7201,
        width=1000,
        height=500,
    )

    result = await plugin.galgame_auto_recalibrate_ocr_dialogue_profile()

    assert isinstance(result, Err)
    assert "稳定对白界面" in str(result.error)
    with plugin._state_lock:
        assert plugin._state.ocr_capture_profiles == {}


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_list_and_set_ocr_window_target_updates_state_and_store(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            ocr_reader={"enabled": False},
        ),
    )
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()

    eligible_window = DetectedGameWindow(
        hwnd=101,
        title="Aiyoku no Eustia",
        process_name="Aiyoku.exe",
        pid=4242,
    )
    excluded_window = DetectedGameWindow(
        hwnd=202,
        title="Galgame Plugin - N.E.K.O Plugin Manager",
        process_name="chrome.exe",
        pid=1500,
    )
    plugin._ocr_reader_manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        platform_fn=lambda: True,
        window_scanner=lambda: [eligible_window, excluded_window],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )

    listed = await plugin.galgame_list_ocr_windows(include_excluded=True)

    assert isinstance(listed, Ok)
    assert listed.value["candidate_count"] == 1
    assert listed.value["excluded_candidate_count"] == 1
    assert listed.value["windows"][0]["window_key"] == eligible_window.window_key
    assert listed.value["excluded_windows"][0]["exclude_reason"] == "excluded_self_window"

    saved = await plugin.galgame_set_ocr_window_target(window_key=eligible_window.window_key)

    assert isinstance(saved, Ok)
    assert saved.value["window_target"]["mode"] == "manual"
    assert saved.value["window_target"]["window_key"] == eligible_window.window_key
    assert "background_poll_started" in saved.value
    assert "status" not in saved.value
    with plugin._state_lock:
        assert plugin._state.ocr_window_target["window_key"] == eligible_window.window_key
    restored, _warnings = plugin._persist.load()
    assert restored[STORE_OCR_WINDOW_TARGET]["window_key"] == eligible_window.window_key

    rejected = await plugin.galgame_set_ocr_window_target(window_key=excluded_window.window_key)

    assert isinstance(rejected, Err)
    assert "excluded OCR window" in str(rejected.error)

    cleared = await plugin.galgame_set_ocr_window_target(clear=True)

    assert isinstance(cleared, Ok)
    assert cleared.value["window_target"]["mode"] == "auto"
    assert "background_poll_started" in cleared.value
    assert "status" not in cleared.value
    with plugin._state_lock:
        assert plugin._state.ocr_window_target["mode"] == "auto"
    await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_poll_bridge_persists_rebound_ocr_window_target(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            ocr_reader={
                "enabled": True,
                "install_target_dir": str(install_root),
            },
        ),
    )
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()

    rebound_window = DetectedGameWindow(
        hwnd=778,
        title="Aiyoku no Eustia",
        process_name="Aiyoku.exe",
        pid=5566,
    )
    original_target = {
        "mode": "manual",
        "window_key": "ocrwin:legacy-window",
        "process_name": rebound_window.process_name,
        "normalized_title": rebound_window.normalized_title,
        "pid": 4455,
        "last_known_hwnd": 777,
        "selected_at": "2026-04-24T10:00:00Z",
    }
    plugin._ocr_reader_manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        platform_fn=lambda: True,
        window_scanner=lambda: [rebound_window],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend([""]),
    )
    plugin._ocr_reader_manager.update_window_target(original_target)
    with plugin._state_lock:
        plugin._state.ocr_window_target = dict(original_target)
    plugin._persist.persist_ocr_window_target(original_target)

    await plugin._poll_bridge(force=True)

    with plugin._state_lock:
        assert plugin._state.ocr_window_target["window_key"] == rebound_window.window_key
        assert plugin._state.ocr_window_target["pid"] == rebound_window.pid
        assert plugin._state.ocr_window_target["last_known_hwnd"] == rebound_window.hwnd
    restored, _warnings = plugin._persist.load()
    assert restored[STORE_OCR_WINDOW_TARGET]["window_key"] == rebound_window.window_key
    assert restored[STORE_OCR_WINDOW_TARGET]["pid"] == rebound_window.pid


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_auto_reader_interval_ocr_takes_over_from_stale_memory_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={
            "enabled": True,
            "textractor_path": str(tmp_path / "TextractorCLI.exe"),
        },
        ocr_reader={
            "enabled": False,
            "install_target_dir": str(install_root),
            "poll_interval_seconds": 1.0,
            "trigger_mode": "interval",
            "no_text_takeover_after_seconds": 0.0,
        },
    )
    memory_game_id = "mem-stale"
    memory_session_id = "mem-session"

    ctx = _Ctx(plugin_dir, cfg)
    monkeypatch.setattr(galgame_plugin_module, "MemoryReaderManager", _NoopMemoryReaderManager)
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    plugin._start_background_bridge_poll = lambda: False
    _create_game_dir(
        bridge_root,
        game_id=memory_game_id,
        session_payload=_memory_reader_session(
            game_id=memory_game_id,
            session_id=memory_session_id,
            last_seq=4,
            state=_session_state(
                speaker="memory",
                text="old memory line",
                scene_id="mem-scene",
                line_id="mem-line",
            ),
        ),
        events=[],
    )
    plugin._memory_reader_manager = SimpleNamespace(
        update_config=lambda config: None,
        tick=lambda **kwargs: asyncio.sleep(
            0,
            result=SimpleNamespace(
                warnings=[],
                should_rescan=False,
                runtime={
                    "enabled": True,
                    "status": "active",
                    "detail": "attached_idle_after_text",
                    "process_name": "DemoGame.exe",
                    "pid": 5252,
                    "engine": "renpy",
                    "game_id": memory_game_id,
                    "session_id": memory_session_id,
                    "last_seq": 5,
                    "last_event_ts": "2026-04-29T01:00:05Z",
                    "last_text_seq": 2,
                    "last_text_ts": "2026-04-29T01:00:00Z",
                },
            ),
        ),
        shutdown=lambda: asyncio.sleep(0, result=None),
    )
    assert plugin._cfg is not None
    plugin._cfg.ocr_reader_enabled = True
    plugin._cfg.ocr_reader_trigger_mode = "interval"
    target = DetectedGameWindow(
        hwnd=205,
        title="OCR Interval Window",
        process_name="DemoGame.exe",
        pid=5252,
        width=1040,
        height=807,
    )
    capture_backend = _FakeCaptureBackend()
    plugin._ocr_reader_manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        time_fn=lambda: 1710000400.0,
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(["新しい台詞です。"]),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: 1710000400.0,
        ),
    )

    await plugin._poll_bridge(force=True)
    await plugin._poll_bridge(force=True)
    await plugin._poll_bridge(force=True)
    snapshot = await plugin.galgame_get_snapshot()
    status = await plugin.galgame_get_status()

    assert isinstance(snapshot, Ok)
    assert isinstance(status, Ok)
    assert status.value["active_data_source"] == DATA_SOURCE_OCR_READER
    assert snapshot.value["snapshot"]["text"] == "新しい台詞です。"
    assert capture_backend.capture_calls >= 1


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_after_advance_bootstrap_continues_until_stable_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)

    cfg = _make_effective_config(
        bridge_root,
        memory_reader={
            "enabled": False,
        },
        ocr_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
            "poll_interval_seconds": 999.0,
            "trigger_mode": "after_advance",
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    clock = {"now": 1710000100.0}
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 201)
    plugin._ocr_reader_manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=201,
                title="OCR Bootstrap Window",
                process_name="DemoGame.exe",
                pid=5252,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(
            [
                "雪乃：首次可见台词。",
                "雪乃：首次可见台词。",
            ]
        ),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )
    _clear_bridge_root(bridge_root)

    await plugin._poll_bridge(force=True)
    first_snapshot = await plugin.galgame_get_snapshot()
    assert isinstance(first_snapshot, Ok)
    assert first_snapshot.value["snapshot"]["text"] == "首次可见台词。"
    assert first_snapshot.value["snapshot"]["stability"] == "stable"

    clock["now"] += 1.0
    plugin._state.next_poll_at_monotonic = 0.0
    await plugin._poll_bridge(force=False)
    second_snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(second_snapshot, Ok)
    assert second_snapshot.value["snapshot"]["text"] == "首次可见台词。"
    assert second_snapshot.value["snapshot"]["stability"] == "stable"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_after_advance_updates_scene_from_embedded_background_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)

    cfg = _make_effective_config(
        bridge_root,
        memory_reader={
            "enabled": False,
        },
        ocr_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
            "poll_interval_seconds": 999.0,
            "trigger_mode": "after_advance",
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    clock = {"now": 1710000200.0}
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 202)
    plugin._ocr_reader_manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=202,
                title="OCR Scene Window",
                process_name="DemoGame.exe",
                pid=5353,
            )
        ],
        capture_backend=_FakeBackgroundHashCaptureBackend(
            [
                "0000000000000000",
                "ffffffffffffffff",
            ]
        ),
        ocr_backend=_FakeOcrBackend(
            [
                "雪乃：第一句台词。",
                "雪乃：第二句台词。",
            ]
        ),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )
    _clear_bridge_root(bridge_root)

    await plugin._poll_bridge(force=True)
    first_snapshot = await plugin.galgame_get_snapshot()
    assert isinstance(first_snapshot, Ok)
    first_scene_id = first_snapshot.value["snapshot"]["scene_id"]
    assert first_snapshot.value["snapshot"]["text"] == "第一句台词。"

    clock["now"] += 0.2
    await plugin._poll_bridge(force=True)
    second_snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(second_snapshot, Ok)
    second_scene_id = second_snapshot.value["snapshot"]["scene_id"]
    assert second_snapshot.value["snapshot"]["text"] == "第二句台词。"
    assert second_scene_id != first_scene_id
    assert str(second_scene_id).endswith("scene-0002")


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_after_advance_active_waits_for_explicit_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={"enabled": False},
        ocr_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
            "poll_interval_seconds": 0.5,
            "trigger_mode": "after_advance",
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    plugin._start_background_bridge_poll = lambda: False
    with plugin._state_lock:
        plugin._state.mode = "choice_advisor"
    clock = {"now": 1710000250.0}
    capture_backend = _FakeCaptureBackend()
    target = DetectedGameWindow(
        hwnd=203,
        title="OCR After Advance Window",
        process_name="DemoGame.exe",
        pid=5354,
        width=1280,
        height=720,
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 0)
    plugin._ocr_reader_manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(["雪乃：第一句。", "雪乃：第二句。"]),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )
    _clear_bridge_root(bridge_root)

    await plugin._poll_bridge(force=True)
    first_snapshot = await plugin.galgame_get_snapshot()
    assert isinstance(first_snapshot, Ok)
    assert first_snapshot.value["snapshot"]["text"] == "第一句。"
    assert capture_backend.capture_calls == 1

    clock["now"] += 1.0
    with plugin._state_lock:
        plugin._state.next_poll_at_monotonic = 0.0
    await plugin._poll_bridge(force=False)
    second_snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(second_snapshot, Ok)
    assert second_snapshot.value["snapshot"]["text"] == "第一句。"
    assert capture_backend.capture_calls == 1

    with plugin._state_lock:
        waiting_runtime = dict(plugin._state.ocr_reader_runtime)
    assert waiting_runtime["ocr_tick_allowed"] is False
    assert waiting_runtime["ocr_tick_block_reason"] == "trigger_mode_after_advance_waiting_for_input"
    assert waiting_runtime["ocr_emit_block_reason"] == ""
    assert waiting_runtime["ocr_reader_allowed"] is True
    assert waiting_runtime["ocr_trigger_mode_effective"] == "after_advance"
    assert waiting_runtime["ocr_waiting_for_advance"] is True
    assert waiting_runtime["ocr_waiting_for_advance_reason"] == "trigger_mode_after_advance_waiting_for_input"
    assert waiting_runtime["ocr_last_tick_decision_at"]
    assert waiting_runtime["ocr_tick_gate_allowed"] is False
    assert waiting_runtime["ocr_tick_skipped_reason"] == "tick_gate_closed"
    assert waiting_runtime["pending_manual_foreground_ocr_capture"] is False
    assert waiting_runtime["foreground_refresh_attempted"] is True
    assert waiting_runtime["foreground_refresh_skipped_reason"] == ""

    plugin.request_ocr_after_advance_capture(reason="manual_foreground_advance")
    with plugin._state_lock:
        plugin._last_ocr_advance_capture_requested_at = time.monotonic()
        plugin._state.next_poll_at_monotonic = 0.0
    await plugin._poll_bridge(force=False)

    with plugin._state_lock:
        delayed_runtime = dict(plugin._state.ocr_reader_runtime)
    assert capture_backend.capture_calls == 1
    assert delayed_runtime["ocr_tick_allowed"] is False
    assert delayed_runtime["ocr_tick_block_reason"] == "waiting_pending_advance_delay"
    assert delayed_runtime["ocr_tick_gate_allowed"] is False
    assert delayed_runtime["ocr_tick_skipped_reason"] == "tick_gate_closed"
    assert delayed_runtime["pending_manual_foreground_ocr_capture"] is True
    assert delayed_runtime["pending_ocr_advance_reason"] == "manual_foreground_advance"
    assert delayed_runtime["pending_ocr_delay_remaining"] > 0.0

    clock["now"] += 1.0
    with plugin._state_lock:
        plugin._last_ocr_advance_capture_requested_at = time.monotonic() - 1.0
        plugin._state.next_poll_at_monotonic = 0.0
    await plugin._poll_bridge(force=False)
    clicked_snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(clicked_snapshot, Ok)
    assert clicked_snapshot.value["snapshot"]["text"] == "第二句。"
    assert capture_backend.capture_calls == 2
    assert plugin._has_pending_ocr_advance_capture() is False
    with plugin._state_lock:
        clicked_runtime = dict(plugin._state.ocr_reader_runtime)
    assert clicked_runtime["ocr_tick_gate_allowed"] is True
    assert clicked_runtime["ocr_tick_entered"] is True
    assert clicked_runtime["ocr_tick_lock_acquired"] is True
    assert clicked_runtime["ocr_tick_skipped_reason"] == ""


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_after_advance_companion_keeps_snapshot_refreshing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={"enabled": False},
        ocr_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
            "poll_interval_seconds": 0.5,
            "trigger_mode": "after_advance",
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    plugin._start_background_bridge_poll = lambda: False
    clock = {"now": 1710000252.0}
    capture_backend = _FakeCaptureBackend()
    target = DetectedGameWindow(
        hwnd=213,
        title="OCR Companion Window",
        process_name="DemoGame.exe",
        pid=5364,
        width=1280,
        height=720,
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 0)
    plugin._ocr_reader_manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(
            [
                "Alice: first line.",
                "Alice: second line.",
            ]
        ),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )
    _clear_bridge_root(bridge_root)

    await plugin._poll_bridge(force=True)
    first_snapshot = await plugin.galgame_get_snapshot()
    assert isinstance(first_snapshot, Ok)
    assert first_snapshot.value["snapshot"]["text"] == "first line."
    assert plugin._has_pending_ocr_advance_capture() is False

    clock["now"] += 1.0
    with plugin._state_lock:
        plugin._state.next_poll_at_monotonic = 0.0
    await plugin._poll_bridge(force=False)
    second_snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(second_snapshot, Ok)
    assert second_snapshot.value["snapshot"]["text"] == "second line."
    assert capture_backend.capture_calls == 2
    assert plugin._has_pending_ocr_advance_capture() is False
    with plugin._state_lock:
        companion_runtime = dict(plugin._state.ocr_reader_runtime)
    assert companion_runtime["ocr_tick_allowed"] is True
    assert companion_runtime["ocr_tick_block_reason"] == ""
    assert companion_runtime["ocr_reader_allowed"] is True
    assert companion_runtime["ocr_waiting_for_advance"] is False


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_after_advance_title_screen_refreshes_without_pending_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={"enabled": False},
        ocr_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
            "poll_interval_seconds": 0.5,
            "trigger_mode": "after_advance",
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    plugin._start_background_bridge_poll = lambda: False
    clock = {"now": 1710000255.0}
    capture_backend = _FakeCaptureBackend()
    target = DetectedGameWindow(
        hwnd=204,
        title="OCR Title Screen Window",
        process_name="DemoGame.exe",
        pid=5355,
        width=1280,
        height=720,
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 0)
    plugin._ocr_reader_manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(
            [
                "Start Game\nContinue\nConfig\nExit",
                "Start Game\nContinue\nConfig\nExit",
            ]
        ),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )
    _clear_bridge_root(bridge_root)

    await plugin._poll_bridge(force=True)
    first_snapshot = await plugin.galgame_get_snapshot()
    assert isinstance(first_snapshot, Ok)
    assert first_snapshot.value["snapshot"]["screen_type"] == OCR_CAPTURE_PROFILE_STAGE_TITLE
    assert first_snapshot.value["snapshot"]["text"] == ""
    with plugin._state_lock:
        first_runtime = dict(plugin._state.ocr_reader_runtime)
        first_history_lines = list(plugin._state.history_lines)
    assert first_runtime["detail"] == "screen_classified"
    assert first_runtime["ocr_context_state"] == "screen_classified"
    assert first_history_lines == []
    assert capture_backend.capture_calls == 1

    clock["now"] += 1.0
    with plugin._state_lock:
        plugin._state.next_poll_at_monotonic = 0.0
    await plugin._poll_bridge(force=False)
    second_snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(second_snapshot, Ok)
    assert second_snapshot.value["snapshot"]["screen_type"] == OCR_CAPTURE_PROFILE_STAGE_TITLE
    with plugin._state_lock:
        second_history_lines = list(plugin._state.history_lines)
    assert second_history_lines == []
    assert capture_backend.capture_calls == 2
    assert plugin._has_pending_ocr_advance_capture() is False


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_after_advance_title_refresh_stops_after_dialogue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={"enabled": False},
        ocr_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
            "poll_interval_seconds": 0.5,
            "trigger_mode": "after_advance",
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    plugin._start_background_bridge_poll = lambda: False
    with plugin._state_lock:
        plugin._state.mode = "choice_advisor"
    clock = {"now": 1710000258.0}
    capture_backend = _FakeCaptureBackend()
    target = DetectedGameWindow(
        hwnd=205,
        title="OCR Title To Dialogue Window",
        process_name="DemoGame.exe",
        pid=5356,
        width=1280,
        height=720,
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 0)
    plugin._ocr_reader_manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(
            [
                "Start Game\nContinue\nConfig\nExit",
                "雪乃：重新进入游戏。",
                "雪乃：重新进入游戏。",
                "雪乃：不应被连续读取。",
            ]
        ),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )
    _clear_bridge_root(bridge_root)

    await plugin._poll_bridge(force=True)
    title_snapshot = await plugin.galgame_get_snapshot()
    assert isinstance(title_snapshot, Ok)
    assert title_snapshot.value["snapshot"]["screen_type"] == OCR_CAPTURE_PROFILE_STAGE_TITLE
    assert capture_backend.capture_calls == 1

    clock["now"] += 1.0
    with plugin._state_lock:
        plugin._state.next_poll_at_monotonic = 0.0
    await plugin._poll_bridge(force=False)
    dialogue_snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(dialogue_snapshot, Ok)
    assert dialogue_snapshot.value["snapshot"]["text"] == "重新进入游戏。"
    assert capture_backend.capture_calls == 2

    clock["now"] += 1.0
    with plugin._state_lock:
        plugin._state.next_poll_at_monotonic = 0.0
    await plugin._poll_bridge(force=False)
    final_snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(final_snapshot, Ok)
    assert final_snapshot.value["snapshot"]["text"] == "重新进入游戏。"
    assert capture_backend.capture_calls == 2

    clock["now"] += 1.0
    with plugin._state_lock:
        plugin._state.next_poll_at_monotonic = 0.0
    await plugin._poll_bridge(force=False)
    stable_snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(stable_snapshot, Ok)
    assert stable_snapshot.value["snapshot"]["text"] == "重新进入游戏。"
    assert capture_backend.capture_calls == 2
    assert plugin._has_pending_ocr_advance_capture() is False


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_after_advance_backlog_screen_does_not_block_new_dialogue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={"enabled": False},
        ocr_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
            "poll_interval_seconds": 0.5,
            "trigger_mode": "after_advance",
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    plugin._start_background_bridge_poll = lambda: False
    with plugin._state_lock:
        plugin._state.mode = "choice_advisor"
    clock = {"now": 1710000259.0}
    capture_backend = _FakeCaptureBackend()
    target = DetectedGameWindow(
        hwnd=206,
        title="OCR Backlog Window",
        process_name="DemoGame.exe",
        pid=5357,
        width=1280,
        height=720,
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 0)
    plugin._ocr_reader_manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(
            [
                "雪乃：今の台詞。",
                "Backlog\n雪乃：前の台詞。\n王生：今の台詞。",
                "雪乃：新しい台詞。",
            ]
        ),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )
    _clear_bridge_root(bridge_root)

    await plugin._poll_bridge(force=True)
    first_snapshot = await plugin.galgame_get_snapshot()
    assert isinstance(first_snapshot, Ok)
    assert first_snapshot.value["snapshot"]["text"] == "今の台詞。"
    assert capture_backend.capture_calls == 1

    plugin.request_ocr_after_advance_capture(reason="manual_foreground_advance")
    clock["now"] += 1.0
    with plugin._state_lock:
        plugin._last_ocr_advance_capture_requested_at = time.monotonic() - 1.0
        plugin._state.next_poll_at_monotonic = 0.0
    await plugin._poll_bridge(force=False)
    backlog_snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(backlog_snapshot, Ok)
    assert backlog_snapshot.value["snapshot"]["screen_type"] == OCR_CAPTURE_PROFILE_STAGE_GALLERY
    assert backlog_snapshot.value["snapshot"]["text"] == "今の台詞。"
    with plugin._state_lock:
        backlog_runtime = dict(plugin._state.ocr_reader_runtime)
        backlog_history_lines = list(plugin._state.history_lines)
    assert backlog_runtime["detail"] in {"screen_classified", "receiving_text"}
    assert [item["text"] for item in backlog_history_lines] == ["今の台詞。"]
    assert capture_backend.capture_calls == 2

    clock["now"] += 1.0
    with plugin._state_lock:
        plugin._state.next_poll_at_monotonic = 0.0
    await plugin._poll_bridge(force=False)
    final_snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(final_snapshot, Ok)
    assert final_snapshot.value["snapshot"]["text"] == "新しい台詞。"
    assert capture_backend.capture_calls == 3
    assert plugin._has_pending_ocr_advance_capture() is False


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_after_advance_title_screen_with_previous_dialogue_keeps_refreshing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={"enabled": False},
        ocr_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
            "poll_interval_seconds": 0.5,
            "trigger_mode": "after_advance",
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    plugin._start_background_bridge_poll = lambda: False
    with plugin._state_lock:
        plugin._state.mode = "choice_advisor"
    clock = {"now": 1710000261.0}
    capture_backend = _FakeCaptureBackend()
    target = DetectedGameWindow(
        hwnd=207,
        title="OCR Dialogue Title Window",
        process_name="DemoGame.exe",
        pid=5358,
        width=1280,
        height=720,
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 0)
    plugin._ocr_reader_manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(
            [
                "Alice: old line.",
                "Start Game\nContinue\nConfig\nExit",
                "Alice: new line.",
            ]
        ),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )
    _clear_bridge_root(bridge_root)

    await plugin._poll_bridge(force=True)
    first_snapshot = await plugin.galgame_get_snapshot()
    assert isinstance(first_snapshot, Ok)
    assert first_snapshot.value["snapshot"]["text"] == "old line."

    plugin.request_ocr_after_advance_capture(reason="manual_foreground_advance")
    clock["now"] += 1.0
    with plugin._state_lock:
        plugin._last_ocr_advance_capture_requested_at = time.monotonic() - 3.0
        plugin._state.next_poll_at_monotonic = 0.0
    await plugin._poll_bridge(force=False)
    title_snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(title_snapshot, Ok)
    assert title_snapshot.value["snapshot"]["screen_type"] == OCR_CAPTURE_PROFILE_STAGE_TITLE
    assert title_snapshot.value["snapshot"]["text"] == "old line."
    with plugin._state_lock:
        title_runtime = dict(plugin._state.ocr_reader_runtime)
    assert title_runtime["detail"] == "screen_classified"
    assert capture_backend.capture_calls == 2
    assert plugin._has_pending_ocr_advance_capture() is False

    clock["now"] += 1.0
    with plugin._state_lock:
        plugin._state.next_poll_at_monotonic = 0.0
    await plugin._poll_bridge(force=False)
    final_snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(final_snapshot, Ok)
    assert final_snapshot.value["snapshot"]["text"] == "new line."
    assert capture_backend.capture_calls == 3
    assert plugin._has_pending_ocr_advance_capture() is False


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_after_advance_capture_failure_keeps_pending_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={"enabled": False},
        ocr_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
            "poll_interval_seconds": 0.5,
            "trigger_mode": "after_advance",
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    plugin._start_background_bridge_poll = lambda: False
    clock = {"now": 1710000262.0}
    capture_backend = _FakeCaptureBackend()
    target = DetectedGameWindow(
        hwnd=208,
        title="OCR Retry Window",
        process_name="DemoGame.exe",
        pid=5359,
        width=1280,
        height=720,
    )

    class _FailOnCallOcrBackend(_FakeOcrBackend):
        def __init__(self, texts: list[str], *, fail_on_calls: set[int]) -> None:
            super().__init__(texts)
            self._calls = 0
            self._fail_on_calls = set(fail_on_calls)

        def extract_text(self, image: str) -> str:
            self._calls += 1
            if self._calls in self._fail_on_calls:
                raise RuntimeError("temporary OCR failure")
            return super().extract_text(image)

    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 0)
    plugin._ocr_reader_manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=capture_backend,
        ocr_backend=_FailOnCallOcrBackend(
            ["Alice: first line.", "Alice: second line."],
            fail_on_calls={2},
        ),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )
    _clear_bridge_root(bridge_root)

    await plugin._poll_bridge(force=True)
    first_snapshot = await plugin.galgame_get_snapshot()
    assert isinstance(first_snapshot, Ok)
    assert first_snapshot.value["snapshot"]["text"] == "first line."

    plugin.request_ocr_after_advance_capture(reason="manual_foreground_advance")
    clock["now"] += 1.0
    with plugin._state_lock:
        plugin._last_ocr_advance_capture_requested_at = time.monotonic() - 1.0
        plugin._state.next_poll_at_monotonic = 0.0
    await plugin._poll_bridge(force=False)
    failed_snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(failed_snapshot, Ok)
    assert failed_snapshot.value["snapshot"]["text"] == "first line."
    with plugin._state_lock:
        failed_runtime = dict(plugin._state.ocr_reader_runtime)
    assert failed_runtime["detail"] == "capture_failed"
    assert failed_runtime["ocr_tick_gate_allowed"] is True
    assert failed_runtime["ocr_tick_entered"] is True
    assert failed_runtime["pending_manual_foreground_ocr_capture"] is True
    assert failed_runtime["pending_ocr_advance_reason"] == "manual_foreground_advance"
    assert failed_runtime["pending_ocr_advance_clear_reason"] == ""
    assert capture_backend.capture_calls == 2
    assert plugin._has_pending_ocr_advance_capture() is True

    clock["now"] += 1.0
    with plugin._state_lock:
        plugin._state.next_poll_at_monotonic = 0.0
    await plugin._poll_bridge(force=False)
    final_snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(final_snapshot, Ok)
    assert final_snapshot.value["snapshot"]["text"] == "second line."
    assert capture_backend.capture_calls == 3
    assert plugin._has_pending_ocr_advance_capture() is False


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_after_advance_clears_stale_pending_when_tick_gate_closed(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        galgame={"reader_mode": DATA_SOURCE_MEMORY_READER},
        memory_reader={"enabled": False},
        ocr_reader={
            "enabled": True,
            "trigger_mode": "after_advance",
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    plugin._start_background_bridge_poll = lambda: False
    with plugin._state_lock:
        plugin._pending_ocr_advance_captures = 1
        plugin._last_ocr_advance_capture_requested_at = time.monotonic() - 5.0
        plugin._last_ocr_advance_capture_reason = "manual_foreground_advance"
        plugin._state.active_data_source = DATA_SOURCE_OCR_READER
        plugin._state.ocr_reader_runtime = {
            "status": "active",
            "detail": "receiving_observed_text",
            "ocr_context_state": "observed",
        }
        plugin._state.next_poll_at_monotonic = 0.0

    await plugin._poll_bridge(force=False)

    with plugin._state_lock:
        runtime = dict(plugin._state.ocr_reader_runtime)
    assert plugin._has_pending_ocr_advance_capture() is False
    assert runtime["ocr_reader_allowed"] is False
    assert runtime["ocr_tick_allowed"] is False
    assert runtime["ocr_tick_block_reason"] == "reader_mode_memory_only"
    assert runtime["ocr_tick_skipped_reason"] == "tick_gate_closed"
    assert runtime["pending_manual_foreground_ocr_capture"] is False
    assert runtime["pending_ocr_advance_reason"] == ""
    assert runtime["pending_ocr_advance_clear_reason"] == "tick_gate_timeout"
    assert runtime["foreground_refresh_attempted"] is False
    assert runtime["foreground_refresh_skipped_reason"] == "ocr_reader_not_allowed"


@pytest.mark.plugin_unit
def test_after_advance_screen_refresh_needed_is_limited_to_non_dialogue_screens() -> None:
    def needed(
        screen_type: str,
        *,
        active_data_source: str = DATA_SOURCE_OCR_READER,
        choices: list[dict[str, object]] | None = None,
        is_menu_open: bool = False,
        ocr_reader_allowed: bool = True,
        context_state: str = "screen_classified",
        detail: str = "",
        confidence: float = 0.64,
        text: str = "",
    ) -> bool:
        return galgame_plugin_module._after_advance_screen_refresh_needed(
            local={
                "active_data_source": active_data_source,
                "latest_snapshot": {
                    "screen_type": screen_type,
                    "screen_confidence": confidence,
                    "text": text,
                    "line_id": "line-1" if text else "",
                    "choices": list(choices or []),
                    "is_menu_open": is_menu_open,
                },
            },
            ocr_reader_runtime={
                "status": "active",
                "ocr_context_state": context_state,
                "detail": detail,
            },
            ocr_reader_allowed=ocr_reader_allowed,
            ocr_trigger_mode="after_advance",
        )

    for screen_type in {
        OCR_CAPTURE_PROFILE_STAGE_TITLE,
        OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
        OCR_CAPTURE_PROFILE_STAGE_CONFIG,
        OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
        OCR_CAPTURE_PROFILE_STAGE_GALLERY,
        OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
    }:
        assert needed(screen_type) is True

    assert needed(OCR_CAPTURE_PROFILE_STAGE_MENU) is True
    assert needed(
        OCR_CAPTURE_PROFILE_STAGE_MENU,
        choices=[{"choice_id": "c1", "text": "左边"}],
    ) is False
    assert needed(OCR_CAPTURE_PROFILE_STAGE_MENU, is_menu_open=True) is False
    assert needed(OCR_CAPTURE_PROFILE_STAGE_DIALOGUE) is False
    assert needed(OCR_CAPTURE_PROFILE_STAGE_DEFAULT) is False
    assert needed(OCR_CAPTURE_PROFILE_STAGE_MINIGAME) is False
    assert needed(OCR_CAPTURE_PROFILE_STAGE_TITLE, active_data_source=DATA_SOURCE_MEMORY_READER) is False
    assert needed(OCR_CAPTURE_PROFILE_STAGE_TITLE, ocr_reader_allowed=False) is False
    assert needed(OCR_CAPTURE_PROFILE_STAGE_TITLE, context_state="stable") is False
    assert needed(OCR_CAPTURE_PROFILE_STAGE_TITLE, context_state="stable", text="dialogue") is False
    assert needed(OCR_CAPTURE_PROFILE_STAGE_TITLE, text="old dialogue") is True
    assert needed(OCR_CAPTURE_PROFILE_STAGE_CONFIG, text="old dialogue") is True
    assert needed(
        OCR_CAPTURE_PROFILE_STAGE_GALLERY,
        context_state="stable",
        detail="screen_classified",
        text="old dialogue",
    ) is True
    assert (
        needed(
            OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
            detail="screen_classified",
            text="dialogue",
        )
        is False
    )
    assert needed(OCR_CAPTURE_PROFILE_STAGE_TITLE, confidence=0.44) is False


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_after_advance_keyboard_writes_next_stable_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={"enabled": False},
        ocr_reader={
            "enabled": False,
            "poll_interval_seconds": 1.0,
            "trigger_mode": "after_advance",
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(ctx._config)
    _enable_injected_ocr_reader(plugin, trigger_mode="after_advance")
    plugin._start_background_bridge_poll = lambda: False
    clock = {"now": 1710000260.0}
    target = DetectedGameWindow(
        hwnd=204,
        title="OCR Keyboard Advance Window",
        process_name="DemoGame.exe",
        pid=5355,
        width=1280,
        height=720,
    )
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(["雪乃：第一句。", "雪乃：第二句。"]),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )
    plugin._ocr_reader_manager = manager
    _clear_bridge_root(bridge_root)
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: target.hwnd)
    monkeypatch.setattr(galgame_ocr_reader, "_root_window_handle", lambda hwnd: int(hwnd or 0))
    monkeypatch.setattr(galgame_ocr_reader, "_window_process_id", lambda hwnd: 0)

    await plugin._poll_bridge(force=True)
    first_snapshot = await plugin.galgame_get_snapshot()
    assert isinstance(first_snapshot, Ok)
    assert first_snapshot.value["snapshot"]["text"] == "第一句。"

    manager._wheel_monitor = _FakeAdvanceInputMonitor(
        [
            galgame_ocr_reader._MouseWheelEvent(
                seq=1,
                ts=clock["now"],
                delta=0,
                foreground_hwnd=target.hwnd,
                kind="key",
                key_code=0x20,
            )
        ]
    )
    plugin._trigger_ocr_for_manual_foreground_advance()
    assert plugin._has_pending_ocr_advance_capture() is True
    clock["now"] += 1.0
    with plugin._state_lock:
        plugin._last_ocr_advance_capture_requested_at = time.monotonic() - 1.0
        plugin._state.next_poll_at_monotonic = 0.0

    await plugin._poll_bridge(force=False)
    second_snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(second_snapshot, Ok)
    assert second_snapshot.value["snapshot"]["text"] == "第二句。"
    assert capture_backend.capture_calls == 2
    assert plugin._has_pending_ocr_advance_capture() is False


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_foreground_refresh_queues_pending_capture_retry(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={"enabled": False},
        ocr_reader={
            "enabled": False,
            "trigger_mode": "after_advance",
            "poll_interval_seconds": 1.0,
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(ctx._config)
    _enable_injected_ocr_reader(plugin, trigger_mode="after_advance")

    class _ForegroundRefreshOcrManager:
        def update_config(self, config):
            del config

        def refresh_foreground_state(self):
            return {
                "status": "active",
                "target_is_foreground": True,
                "game_id": "ocr-demo",
                "session_id": "sess-ocr",
            }

        async def tick(self, **kwargs):
            del kwargs
            return SimpleNamespace(
                warnings=[],
                runtime={
                    "status": "active",
                    "target_is_foreground": True,
                    "game_id": "ocr-demo",
                    "session_id": "sess-ocr",
                },
                should_rescan=False,
                stable_event_emitted=False,
            )

        def current_window_target(self):
            return {}

    plugin._ocr_reader_manager = _ForegroundRefreshOcrManager()
    with plugin._state_lock:
        plugin._state.active_data_source = DATA_SOURCE_OCR_READER
        plugin._state.ocr_reader_runtime = {
            "status": "active",
            "target_is_foreground": False,
            "game_id": "ocr-demo",
            "session_id": "sess-ocr",
        }
        plugin._state.next_poll_at_monotonic = 0.0

    await plugin._poll_bridge(force=False)

    assert plugin._has_pending_ocr_advance_capture() is True
    assert plugin._last_ocr_advance_capture_reason == "foreground_target_activated"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_after_advance_coalesces_transient_background_scenes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)

    cfg = _make_effective_config(
        bridge_root,
        memory_reader={
            "enabled": False,
        },
        ocr_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
            "poll_interval_seconds": 999.0,
            "trigger_mode": "after_advance",
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    clock = {"now": 1710000250.0}
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 204)
    capture_backend = _FakeBackgroundHashCaptureBackend(
        [
            "0000000000000000",
            "ffffffffffffffff",
            "3f00001c1c0d0f3f",
            "00007efe7c3f3fff",
        ]
    )
    plugin._ocr_reader_manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=204,
                title="OCR Transient Scene Window",
                process_name="DemoGame.exe",
                pid=5354,
            )
        ],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(
            [
                "雪乃：第一句台词。",
                "",
                "",
                "王生：第二句台词。",
            ]
        ),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )
    _clear_bridge_root(bridge_root)

    for _ in range(4):
        await plugin._poll_bridge(force=True)
        clock["now"] += 1.0

    snapshot = await plugin.galgame_get_snapshot()
    assert isinstance(snapshot, Ok)
    assert snapshot.value["snapshot"]["text"] == "第二句台词。"
    assert snapshot.value["snapshot"]["scene_id"].endswith("scene-0002")

    events_path = bridge_root / plugin._ocr_reader_manager._writer.game_id / "events.jsonl"
    scene_events = [
        event for event in _read_bridge_events(events_path) if event["type"] == "scene_changed"
    ]
    assert [event["payload"]["scene_id"] for event in scene_events] == [
        f"ocr:{plugin._ocr_reader_manager._writer.game_id}:scene-0002"
    ]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_after_advance_manual_click_writes_stable_line_without_memory_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={
            "enabled": True,
            "textractor_path": str(tmp_path / "TextractorCLI.exe"),
        },
        ocr_reader={
            "enabled": False,
            "poll_interval_seconds": 1.0,
            "trigger_mode": "after_advance",
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(ctx._config)
    _enable_injected_ocr_reader(plugin, trigger_mode="after_advance")
    plugin._start_background_bridge_poll = lambda: False
    target = DetectedGameWindow(
        hwnd=203,
        title="OCR Click Window",
        process_name="TheLamentingGeese.exe",
        pid=5454,
        width=1040,
        height=807,
    )
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        time_fn=lambda: 1710000300.0,
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(["王生\n算了，没事。"]),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: 1710000300.0,
        ),
    )
    manager.list_windows_snapshot()
    manager.update_window_target(
        {
            "mode": "manual",
            "window_key": target.window_key,
            "process_name": target.process_name,
            "normalized_title": target.normalized_title,
            "pid": target.pid,
            "last_known_hwnd": target.hwnd,
        }
    )
    manager._wheel_monitor = _FakeAdvanceInputMonitor(
        [
            galgame_ocr_reader._MouseWheelEvent(
                seq=1,
                ts=1710000300.0,
                delta=0,
                foreground_hwnd=target.hwnd,
                point_hwnd=target.hwnd,
                kind="left_click",
            )
        ]
    )
    plugin._ocr_reader_manager = manager

    async def _unexpected_memory_tick(**kwargs):
        del kwargs
        raise AssertionError("memory_reader must not block after-advance OCR capture")

    plugin._memory_reader_manager = SimpleNamespace(
        update_config=lambda config: None,
        tick=_unexpected_memory_tick,
        shutdown=lambda: asyncio.sleep(0, result=None),
    )
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: target.hwnd)
    monkeypatch.setattr(galgame_ocr_reader, "_root_window_handle", lambda hwnd: int(hwnd or 0))
    monkeypatch.setattr(galgame_ocr_reader, "_window_process_id", lambda hwnd: 0)

    plugin._trigger_ocr_for_manual_foreground_advance()
    assert plugin._has_pending_ocr_advance_capture() is True
    with plugin._state_lock:
        plugin._last_ocr_advance_capture_requested_at = time.monotonic() - 1.0
        plugin._state.next_poll_at_monotonic = 0.0

    await plugin._poll_bridge(force=False)
    snapshot = await plugin.galgame_get_snapshot()
    status = await plugin.galgame_get_status()

    assert isinstance(snapshot, Ok)
    assert isinstance(status, Ok)
    assert snapshot.value["snapshot"]["text"] == "王生 算了，没事。"
    assert snapshot.value["snapshot"]["stability"] == "stable"
    assert status.value["active_data_source"] == DATA_SOURCE_OCR_READER
    assert capture_backend.capture_calls == 1
    assert plugin._has_pending_ocr_advance_capture() is False


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_after_advance_manual_click_ocr_is_not_blocked_by_memory_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={"enabled": True, "textractor_path": str(tmp_path / "TextractorCLI.exe")},
        ocr_reader={
            "enabled": False,
            "poll_interval_seconds": 1.0,
            "trigger_mode": "after_advance",
        },
    )
    memory_game_id = "mem-stale"
    _create_game_dir(
        bridge_root,
        game_id=memory_game_id,
        session_payload=_memory_reader_session(
            game_id=memory_game_id,
            session_id="mem-session",
            last_seq=1,
            state=_session_state(
                speaker="内存",
                text="旧的内存读取台词。",
                scene_id="mem-scene",
                line_id="mem-line",
            ),
        ),
        events=[],
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(ctx._config)
    _enable_injected_ocr_reader(plugin, trigger_mode="after_advance")
    plugin._start_background_bridge_poll = lambda: False
    plugin._memory_reader_manager = SimpleNamespace(
        update_config=lambda config: None,
        tick=lambda **kwargs: asyncio.sleep(
            0,
            result=SimpleNamespace(
                warnings=[],
                should_rescan=False,
                runtime={
                    "enabled": True,
                    "status": "active",
                    "detail": "receiving_text",
                    "game_id": memory_game_id,
                    "session_id": "mem-session",
                    "last_seq": 1,
                    "last_text_seq": 1,
                    "last_text_ts": "2026-04-29T01:00:00Z",
                },
            ),
        ),
        shutdown=lambda: asyncio.sleep(0, result=None),
    )
    await plugin._poll_bridge(force=True)
    status_before = await plugin.galgame_get_status()
    assert isinstance(status_before, Ok)
    assert status_before.value["active_data_source"] == DATA_SOURCE_MEMORY_READER

    target = DetectedGameWindow(
        hwnd=203,
        title="OCR Click Window",
        process_name="TheLamentingGeese.exe",
        pid=5454,
        width=1040,
        height=807,
    )
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        time_fn=lambda: 1710000300.0,
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(["王生\n新的 OCR 台词。"]),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: 1710000300.0,
        ),
    )
    manager.list_windows_snapshot()
    manager.update_window_target(
        {
            "mode": "manual",
            "window_key": target.window_key,
            "process_name": target.process_name,
            "normalized_title": target.normalized_title,
            "pid": target.pid,
            "last_known_hwnd": target.hwnd,
        }
    )
    manager._wheel_monitor = _FakeAdvanceInputMonitor(
        [
            galgame_ocr_reader._MouseWheelEvent(
                seq=1,
                ts=1710000300.0,
                delta=0,
                foreground_hwnd=target.hwnd,
                point_hwnd=target.hwnd,
                kind="left_click",
            )
        ]
    )
    plugin._ocr_reader_manager = manager
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: target.hwnd)
    monkeypatch.setattr(galgame_ocr_reader, "_root_window_handle", lambda hwnd: int(hwnd or 0))
    monkeypatch.setattr(galgame_ocr_reader, "_window_process_id", lambda hwnd: 0)

    plugin._trigger_ocr_for_manual_foreground_advance()
    assert plugin._has_pending_ocr_advance_capture() is True
    with plugin._state_lock:
        plugin._last_ocr_advance_capture_requested_at = time.monotonic() - 1.0
        plugin._state.next_poll_at_monotonic = 0.0

    await plugin._poll_bridge(force=False)
    snapshot = await plugin.galgame_get_snapshot()
    status = await plugin.galgame_get_status()

    assert isinstance(snapshot, Ok)
    assert isinstance(status, Ok)
    assert snapshot.value["snapshot"]["text"] == "王生 新的 OCR 台词。"
    assert snapshot.value["snapshot"]["stability"] == "stable"
    assert status.value["active_data_source"] == DATA_SOURCE_OCR_READER
    assert capture_backend.capture_calls == 1
    assert plugin._has_pending_ocr_advance_capture() is False


@pytest.mark.asyncio
@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("event_kind", "event_delta", "event_key_code"),
    [
        ("left_click", 0, 0),
        ("wheel", -120, 0),
        ("key", 0, 0x20),
    ],
)
async def test_after_advance_manual_input_discovers_ocr_target_while_memory_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_kind: str,
    event_delta: int,
    event_key_code: int,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={"enabled": True, "textractor_path": str(tmp_path / "TextractorCLI.exe")},
        ocr_reader={
            "enabled": False,
            "poll_interval_seconds": 1.0,
            "trigger_mode": "after_advance",
        },
    )
    memory_game_id = "mem-stale"
    _create_game_dir(
        bridge_root,
        game_id=memory_game_id,
        session_payload=_memory_reader_session(
            game_id=memory_game_id,
            session_id="mem-session",
            last_seq=1,
            state=_session_state(
                speaker="内存",
                text="旧的内存读取台词。",
                scene_id="mem-scene",
                line_id="mem-line",
            ),
        ),
        events=[],
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(ctx._config)
    _enable_injected_ocr_reader(plugin, trigger_mode="after_advance")
    plugin._start_background_bridge_poll = lambda: False
    plugin._memory_reader_manager = SimpleNamespace(
        update_config=lambda config: None,
        tick=lambda **kwargs: asyncio.sleep(
            0,
            result=SimpleNamespace(
                warnings=[],
                should_rescan=False,
                runtime={
                    "enabled": True,
                    "status": "active",
                    "detail": "receiving_text",
                    "game_id": memory_game_id,
                    "session_id": "mem-session",
                    "last_seq": 1,
                    "last_text_seq": 1,
                    "last_text_ts": "2026-04-29T01:00:00Z",
                },
            ),
        ),
        shutdown=lambda: asyncio.sleep(0, result=None),
    )
    await plugin._poll_bridge(force=True)
    status_before = await plugin.galgame_get_status()
    assert isinstance(status_before, Ok)
    assert status_before.value["active_data_source"] == DATA_SOURCE_MEMORY_READER

    target = DetectedGameWindow(
        hwnd=204,
        title="OCR Auto Target Window",
        process_name="TheLamentingGeese.exe",
        pid=5455,
        width=1040,
        height=807,
    )
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        time_fn=lambda: 1710000310.0,
        platform_fn=lambda: True,
        window_scanner=lambda: [target],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(["王生\n点击后自动发现新台词。"]),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: 1710000310.0,
        ),
    )
    manager._wheel_monitor = _FakeAdvanceInputMonitor(
        [
            galgame_ocr_reader._MouseWheelEvent(
                seq=1,
                ts=1710000310.0,
                delta=event_delta,
                foreground_hwnd=0,
                point_hwnd=target.hwnd,
                kind=event_kind,
                key_code=event_key_code,
            )
        ]
    )
    plugin._ocr_reader_manager = manager
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 0)
    monkeypatch.setattr(galgame_ocr_reader, "_root_window_handle", lambda hwnd: int(hwnd or 0))
    monkeypatch.setattr(galgame_ocr_reader, "_window_process_id", lambda hwnd: 0)

    plugin._trigger_ocr_for_manual_foreground_advance()
    assert plugin._has_pending_ocr_advance_capture() is True
    with plugin._state_lock:
        plugin._last_ocr_advance_capture_requested_at = time.monotonic() - 1.0
        plugin._state.next_poll_at_monotonic = 0.0

    await plugin._poll_bridge(force=False)
    snapshot = await plugin.galgame_get_snapshot()
    status = await plugin.galgame_get_status()

    assert isinstance(snapshot, Ok)
    assert isinstance(status, Ok)
    assert snapshot.value["snapshot"]["text"] == "王生 点击后自动发现新台词。"
    assert snapshot.value["snapshot"]["stability"] == "stable"
    assert status.value["active_data_source"] == DATA_SOURCE_OCR_READER
    assert capture_backend.capture_calls == 1
    assert plugin._has_pending_ocr_advance_capture() is False


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_bridge_sdk_session_preempts_ocr_reader_candidate(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)

    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            ocr_reader={
                "enabled": True,
                "install_target_dir": str(install_root),
                "poll_interval_seconds": 999.0,
            },
        ),
    )
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    clock = {"now": 1711000000.0}
    plugin._ocr_reader_manager = OcrReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=202,
                title="OCR Demo Window",
                process_name="DemoGame.exe",
                pid=4343,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(
            [
                "雪乃：OCR 台词。",
                "雪乃：OCR 台词。",
            ]
        ),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )

    await plugin._poll_bridge(force=True)
    clock["now"] += 1.0
    await plugin._poll_bridge(force=True)
    clock["now"] += 1.0
    await plugin._poll_bridge(force=True)

    _create_game_dir(
        bridge_root,
        game_id="demo.sdk",
        session_payload=_session(
            game_id="demo.sdk",
            session_id="sdk-session-1",
            last_seq=3,
            state=_session_state(
                speaker="桥接",
                text="来自 Bridge SDK 的台词。",
                scene_id="scene-sdk",
                line_id="line-sdk",
                ts="2026-04-21T08:31:00Z",
            ),
        ),
        events=[],
    )

    await plugin._poll_bridge(force=True)
    status = await plugin.galgame_get_status()
    snapshot = await plugin.galgame_get_snapshot()

    assert isinstance(status, Ok)
    assert isinstance(snapshot, Ok)
    assert status.value["active_data_source"] == DATA_SOURCE_BRIDGE_SDK
    assert snapshot.value["snapshot"]["text"] == "来自 Bridge SDK 的台词。"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_aihong_menu_stage_rejects_short_dialogue_false_positive(tmp_path: Path) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)
    cfg = _make_effective_config(
        bridge_root,
        ocr_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
            "poll_interval_seconds": 999.0,
        },
    )
    clock = {"now": 1712000000.0}
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(cfg),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=301,
                title="哀鸿",
                process_name="TheLamentingGeese.exe",
                pid=6001,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(
            [
                "王生：前文台词。",
                "王生：前文台词。",
                "",
                "",
                "王生\n别喝了。",
                "",
                "王生\n别喝了。",
            ]
        ),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )

    for _ in range(6):
        await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
        clock["now"] += 1.0

    game_dir = bridge_root / str(manager._writer.game_id)
    session = read_session_json(game_dir / "session.json")
    events = _read_bridge_events(game_dir / "events.jsonl")

    assert session.error == ""
    assert session.session is not None
    assert all(event["type"] != "choices_shown" for event in events)
    assert session.session["state"]["is_menu_open"] is False
    assert session.session["state"]["choices"] == []


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_quick_followup_confirm_emits_line_without_waiting_next_tick(
    tmp_path: Path,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)
    cfg = _make_effective_config(
        bridge_root,
        ocr_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
            "poll_interval_seconds": 999.0,
        },
    )
    clock = {"now": 1712050000.0}
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(cfg),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=399,
                title="测试游戏",
                process_name="Demo.exe",
                pid=6099,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(
            [
                "王生：前文台词。",
                "王生：前文台词。",
                "王生：别喝了。",
                "王生：别喝了。",
            ]
        ),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )

    for _ in range(2):
        await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
        clock["now"] += 1.0
    for _ in range(2):
        await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
        clock["now"] += 1.0
    await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
    clock["now"] += 1.0
    await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    game_dir = bridge_root / str(manager._writer.game_id)
    session = read_session_json(game_dir / "session.json")
    events = _read_bridge_events(game_dir / "events.jsonl")

    assert session.session is not None
    assert session.session["state"]["text"] == "别喝了。"
    assert [event["type"] for event in events].count("line_changed") >= 2


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_aihong_menu_stage_requires_two_stable_short_menu_reads_before_choices_event(
    tmp_path: Path,
) -> None:
    _plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    install_root = tmp_path / "Tesseract"
    _prepare_fake_tesseract_install(install_root)
    cfg = _make_effective_config(
        bridge_root,
        ocr_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
            "poll_interval_seconds": 999.0,
        },
    )
    clock = {"now": 1712100000.0}
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config(cfg),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=302,
                title="哀鸿",
                process_name="TheLamentingGeese.exe",
                pid=6002,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(
            [
                "王生：前文台词。",
                "王生：前文台词。",
                "",
                "",
                "去东院\n去西院",
                "",
                "去东院\n去西院",
            ]
        ),
        writer=OcrReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )

    for _ in range(5):
        await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
        clock["now"] += 1.0

    game_dir = bridge_root / str(manager._writer.game_id)
    events_before_confirm = _read_bridge_events(game_dir / "events.jsonl")
    assert all(event["type"] != "choices_shown" for event in events_before_confirm)

    for _ in range(2):
        await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
        clock["now"] += 1.0

    session = read_session_json(game_dir / "session.json")
    events = _read_bridge_events(game_dir / "events.jsonl")

    assert session.error == ""
    assert session.session is not None
    assert [event["type"] for event in events][-1] == "choices_shown"
    assert session.session["state"]["is_menu_open"] is True
    assert [item["text"] for item in session.session["state"]["choices"]] == ["去东院", "去西院"]


@pytest.mark.plugin_unit
def test_aihong_menu_choice_parser_ignores_money_status_lines() -> None:
    choices = _coerce_aihong_menu_choices(
        [
            "爽快给他钱手",
            "不给钱手",
            "银两剩余",
            "5两P入",
        ]
    )

    assert choices == ["爽快给他钱", "不给钱"]


@pytest.mark.plugin_unit
def test_aihong_menu_status_only_text_is_not_dialogue() -> None:
    assert _looks_like_aihong_menu_status_only_text("银两剩余\n5两P入") is True


@pytest.mark.plugin_unit
def test_short_non_cjk_ocr_noise_is_not_dialogue() -> None:
    assert _looks_like_noise_ocr_text("?") is True
    assert _looks_like_noise_ocr_text("K") is True
    assert _looks_like_noise_ocr_text("呼一一呼！之") is False


@pytest.mark.plugin_unit
def test_virtual_mouse_dialogue_target_maps_client_relative_point() -> None:
    target = local_input._resolve_virtual_mouse_dialogue_target(
        {"instruction_variant": 0},
        (883, 133, 1907, 901),
    )

    assert target["success"] is True
    assert target["target_id"] == "dialogue_continue_primary"
    assert target["screen_x"] == 1118
    assert target["screen_y"] == 709
    assert target["client_rect"] == {"left": 883, "top": 133, "right": 1907, "bottom": 901}


@pytest.mark.plugin_unit
def test_virtual_mouse_dialogue_target_honors_explicit_target_id() -> None:
    target = local_input._resolve_virtual_mouse_dialogue_target(
        {
            "instruction_variant": 0,
            "virtual_mouse_target_id": "dialogue_text_mid",
        },
        (0, 0, 1000, 800),
    )

    assert target["success"] is True
    assert target["target_id"] == "dialogue_text_mid"
    assert target["candidate_index"] == 2
    assert target["screen_x"] == 300
    assert target["screen_y"] == 608


@pytest.mark.plugin_unit
def test_virtual_mouse_dialogue_target_skips_forbidden_zone() -> None:
    target = local_input._resolve_virtual_mouse_dialogue_target(
        {"instruction_variant": 0},
        (0, 0, 1000, 800),
        candidates=(
            {"target_id": "bad_toolbar", "relative_x": 0.60, "relative_y": 0.80},
            {"target_id": "safe_text", "relative_x": 0.20, "relative_y": 0.75},
        ),
    )

    assert target["success"] is True
    assert target["target_id"] == "safe_text"
    assert target["screen_x"] == 200
    assert target["screen_y"] == 600
    assert target["skipped_candidates"][0]["forbidden_zone"] == "bottom_toolbar"


@pytest.mark.plugin_unit
def test_input_safety_policy_blocks_deny_markers() -> None:
    reason = local_input._input_safety_policy_block_reason(
        target={"pid": 1234, "process_name": "EasyAntiCheat.exe", "window_title": ""},
        hwnd=99,
        window_title="",
    )

    assert reason.startswith("blocked_by_input_safety_policy")
    assert "deny marker" in reason


@pytest.mark.plugin_unit
def test_local_input_safety_policy_does_not_emit_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clicks: list[tuple[object, ...]] = []
    taps: list[tuple[object, ...]] = []
    monkeypatch.setattr(local_input.sys, "platform", "win32")
    monkeypatch.setattr(
        local_input,
        "_find_window_for_pid",
        lambda pid: (99, (0, 0, 1000, 800)),
    )
    monkeypatch.setattr(local_input, "_window_text", lambda hwnd: "")
    monkeypatch.setattr(local_input, "_click", lambda *args: clicks.append(args))
    monkeypatch.setattr(local_input, "_tap_key", lambda *args, **kwargs: taps.append(args))

    result = local_input.perform_local_input_actuation(
        {"ocr_reader_runtime": {"pid": 1234, "process_name": "EasyAntiCheat.exe"}},
        {"kind": "advance", "strategy_id": "advance_click", "instruction_variant": 0},
    )

    assert result["success"] is False
    assert result["reason"] == "blocked_by_input_safety_policy"
    assert result["safety_policy"]["blocked"] is True
    assert clicks == []
    assert taps == []


@pytest.mark.plugin_unit
def test_local_input_focus_failure_reports_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clicks: list[tuple[object, ...]] = []
    taps: list[tuple[object, ...]] = []
    monkeypatch.setattr(local_input.sys, "platform", "win32")
    monkeypatch.setattr(
        local_input,
        "_find_window_for_pid",
        lambda pid: (99, (0, 0, 1000, 800)),
    )
    monkeypatch.setattr(local_input, "_window_text", lambda hwnd: "Game")
    monkeypatch.setattr(local_input, "_is_current_process_elevated", lambda: False)
    monkeypatch.setattr(local_input, "_is_process_elevated", lambda pid: False)
    monkeypatch.setattr(local_input, "_click", lambda *args: clicks.append(args))
    monkeypatch.setattr(local_input, "_tap_key", lambda *args, **kwargs: taps.append(args))

    def _fail_focus(hwnd: int) -> bool:
        local_input._LAST_FOCUS_WINDOW_DIAGNOSTIC = "SetForegroundWindow failed: denied"
        return False

    monkeypatch.setattr(local_input, "_focus_window", _fail_focus)

    result = local_input.perform_local_input_actuation(
        {"ocr_reader_runtime": {"pid": 1234, "process_name": "game.exe"}},
        {"kind": "advance", "strategy_id": "advance_click", "instruction_variant": 0},
    )

    assert result["success"] is False
    assert result["reason"] == "blocked_by_input_safety_policy"
    assert (
        result["safety_policy"]["focus_diagnostic"]
        == "SetForegroundWindow failed: denied"
    )
    assert clicks == []
    assert taps == []


@pytest.mark.plugin_unit
def test_local_input_recover_escape_for_screen_awareness(monkeypatch: pytest.MonkeyPatch) -> None:
    taps: list[tuple[object, ...]] = []
    monkeypatch.setattr(local_input.sys, "platform", "win32")
    monkeypatch.setattr(
        local_input,
        "_find_window_for_pid",
        lambda pid: (99, (0, 0, 1000, 800)),
    )
    monkeypatch.setattr(local_input, "_window_text", lambda hwnd: "Game")
    monkeypatch.setattr(local_input, "_is_current_process_elevated", lambda: False)
    monkeypatch.setattr(local_input, "_is_process_elevated", lambda pid: False)
    monkeypatch.setattr(local_input, "_focus_window", lambda hwnd: True)
    monkeypatch.setattr(local_input, "_tap_key", lambda *args, **kwargs: taps.append(args))

    result = local_input.perform_local_input_actuation(
        {
            "ocr_reader_runtime": {"pid": 1234, "process_name": "game.exe"},
            "latest_snapshot": {"screen_type": OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD},
        },
        {"kind": "recover", "strategy_id": "save_load_escape"},
    )

    assert result["success"] is True
    assert taps[-1][1] == local_input.VK_ESCAPE


@pytest.mark.plugin_unit
def test_local_input_advance_click_blocks_visible_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clicks: list[tuple[object, ...]] = []
    monkeypatch.setattr(local_input.sys, "platform", "win32")
    monkeypatch.setattr(local_input, "_find_window_for_pid", lambda pid: (99, (0, 0, 1000, 800)))
    monkeypatch.setattr(local_input, "_window_text", lambda hwnd: "TheLamentingGeese")
    monkeypatch.setattr(local_input, "_is_current_process_elevated", lambda: False)
    monkeypatch.setattr(local_input, "_is_process_elevated", lambda pid: False)
    monkeypatch.setattr(local_input, "_focus_window", lambda hwnd: True)
    monkeypatch.setattr(local_input, "_client_screen_rect", lambda hwnd: (0, 0, 1000, 800))
    monkeypatch.setattr(local_input, "_click", lambda *args: clicks.append(args))

    result = local_input.perform_local_input_actuation(
        {
            "ocr_reader_runtime": {"pid": 1234, "process_name": "TheLamentingGeese.exe"},
            "latest_snapshot": {
                "is_menu_open": True,
                "choices": [{"choice_id": "c1", "text": "左边", "index": 0}],
            },
        },
        {"kind": "advance", "strategy_id": "advance_click", "instruction_variant": 0},
    )

    assert result["success"] is False
    assert result["reason"] == "advance_click_blocked_by_visible_choices"
    assert result["virtual_mouse"]["blocked"] is True
    assert clicks == []


@pytest.mark.plugin_unit
def test_local_input_choice_bounds_uses_capture_rect_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clicks: list[tuple[object, ...]] = []
    taps: list[tuple[object, ...]] = []
    monkeypatch.setattr(local_input.sys, "platform", "win32")
    monkeypatch.setattr(
        local_input,
        "_find_window_for_pid",
        lambda pid: (99, (145, 108, 1185, 915)),
    )
    monkeypatch.setattr(local_input, "_window_text", lambda hwnd: "TheLamentingGeese")
    monkeypatch.setattr(local_input, "_is_current_process_elevated", lambda: False)
    monkeypatch.setattr(local_input, "_is_process_elevated", lambda pid: False)
    monkeypatch.setattr(local_input, "_focus_window", lambda hwnd: True)
    monkeypatch.setattr(local_input, "_client_screen_rect", lambda hwnd: (153, 139, 1177, 907))
    monkeypatch.setattr(local_input, "_click", lambda *args: clicks.append(args))
    monkeypatch.setattr(local_input, "_tap_key", lambda *args, **kwargs: taps.append(args))

    result = local_input.perform_local_input_actuation(
        {
            "ocr_reader_runtime": {
                "pid": 42248,
                "process_name": "TheLamentingGeese.exe",
            },
        },
        {
            "kind": "choose",
            "strategy_id": "choose_rank_1_variant_1",
            "candidate_index": 0,
            "candidate_choices": [
                {
                    "text": "爽快给他钱",
                    "index": 0,
                    "bounds": {
                        "left": 494.0,
                        "top": 261.0,
                        "right": 734.0,
                        "bottom": 295.0,
                    },
                    "bounds_coordinate_space": "capture",
                    "source_size": {"width": 1040.0, "height": 807.0},
                    "capture_rect": {"left": 145, "top": 108, "right": 1185, "bottom": 915},
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["method"] == "choice_bounds_click"
    assert result["coordinate_space"] == "capture"
    assert result["screen_points"][0] == {"x": 759, "y": 386}
    assert clicks[0] == (99, 759, 386)
    assert clicks[0] != (99, 767, 403)
    assert taps[-1][1] == local_input.VK_RETURN


@pytest.mark.plugin_unit
def test_local_input_choice_bounds_defaults_to_window_rect_without_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clicks: list[tuple[object, ...]] = []
    monkeypatch.setattr(local_input.sys, "platform", "win32")
    monkeypatch.setattr(
        local_input,
        "_find_window_for_pid",
        lambda pid: (99, (145, 108, 1185, 915)),
    )
    monkeypatch.setattr(local_input, "_window_text", lambda hwnd: "TheLamentingGeese")
    monkeypatch.setattr(local_input, "_is_current_process_elevated", lambda: False)
    monkeypatch.setattr(local_input, "_is_process_elevated", lambda pid: False)
    monkeypatch.setattr(local_input, "_focus_window", lambda hwnd: True)
    monkeypatch.setattr(local_input, "_client_screen_rect", lambda hwnd: (153, 139, 1177, 907))
    monkeypatch.setattr(local_input, "_click", lambda *args: clicks.append(args))
    monkeypatch.setattr(local_input, "_tap_key", lambda *args, **kwargs: None)

    result = local_input.perform_local_input_actuation(
        {
            "ocr_reader_runtime": {
                "pid": 42248,
                "process_name": "TheLamentingGeese.exe",
            },
        },
        {
            "kind": "choose",
            "strategy_id": "choose_rank_1_variant_1",
            "candidate_index": 0,
            "candidate_choices": [
                {
                    "text": "爽快给他钱",
                    "index": 0,
                    "bounds": {"left": 494, "top": 261, "right": 734, "bottom": 295},
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["coordinate_space"] == "window"
    assert result["screen_points"][0] == {"x": 759, "y": 386}
    assert clicks[0] == (99, 759, 386)


@pytest.mark.plugin_unit
def test_ocr_writer_start_session_resets_initial_scene_to_game_id(tmp_path: Path) -> None:
    writer = OcrReaderBridgeWriter(bridge_root=tmp_path, time_fn=lambda: 1712100100.0)
    writer.start_session(
        DetectedGameWindow(
            hwnd=404,
            title="哀鸿",
            process_name="TheLamentingGeese.exe",
            pid=6104,
        )
    )

    game_dir = tmp_path / writer.game_id
    session = read_session_json(game_dir / "session.json")
    events = _read_bridge_events(game_dir / "events.jsonl")
    expected_scene_id = f"ocr:{writer.game_id}:scene-0001"

    assert session.session is not None
    assert session.session["state"]["scene_id"] == expected_scene_id
    assert events[0]["payload"]["scene_id"] == expected_scene_id
    assert "unknown" not in expected_scene_id


@pytest.mark.plugin_unit
def test_ocr_writer_can_emit_choices_without_prior_line(tmp_path: Path) -> None:
    writer = OcrReaderBridgeWriter(bridge_root=tmp_path, time_fn=lambda: 1712100100.0)
    writer.start_session(
        DetectedGameWindow(
            hwnd=404,
            title="哀鸿",
            process_name="TheLamentingGeese.exe",
            pid=6104,
        )
    )

    assert (
        writer.emit_choices(
            ["爽快给他钱", "不给钱"],
            ts="2024-04-02T12:00:00Z",
            choice_bounds=[
                {"left": 494, "top": 261, "right": 734, "bottom": 295},
                {"left": 485, "top": 321, "right": 742, "bottom": 363},
            ],
            choice_bounds_metadata={
                "bounds_coordinate_space": "capture",
                "source_size": {"width": 1040.0, "height": 807.0},
                "capture_rect": {"left": 145, "top": 108, "right": 1185, "bottom": 915},
            },
        )
        is True
    )

    game_dir = tmp_path / writer.game_id
    session = read_session_json(game_dir / "session.json")
    events = _read_bridge_events(game_dir / "events.jsonl")

    assert session.session is not None
    assert session.session["state"]["line_id"]
    assert session.session["state"]["is_menu_open"] is True
    assert [item["text"] for item in session.session["state"]["choices"]] == [
        "爽快给他钱",
        "不给钱",
    ]
    first_choice = session.session["state"]["choices"][0]
    assert first_choice["bounds_coordinate_space"] == "capture"
    assert first_choice["source_size"] == {"width": 1040.0, "height": 807.0}
    assert first_choice["capture_rect"] == {"left": 145, "top": 108, "right": 1185, "bottom": 915}
    assert events[-1]["type"] == "choices_shown"


@pytest.mark.plugin_unit
def test_ocr_line_observed_updates_snapshot_without_stable_history(tmp_path: Path) -> None:
    writer = OcrReaderBridgeWriter(bridge_root=tmp_path, time_fn=lambda: 1712100100.0)
    writer.start_session(
        DetectedGameWindow(
            hwnd=404,
            title="哀鸿",
            process_name="TheLamentingGeese.exe",
            pid=6104,
        )
    )

    assert writer.emit_line_observed("王生：算了，没事。", ts="2024-04-02T12:00:00Z") is True

    game_dir = tmp_path / writer.game_id
    session = read_session_json(game_dir / "session.json")
    events = _read_bridge_events(game_dir / "events.jsonl")
    history_events: list[dict[str, Any]] = []
    history_lines: list[dict[str, Any]] = []
    history_observed_lines: list[dict[str, Any]] = []
    history_choices: list[dict[str, Any]] = []
    dedupe_window: list[dict[str, str]] = []
    cfg = build_config({"galgame": {"bridge_root": str(tmp_path)}})
    for event in events:
        galgame_service.apply_event_to_histories(
            history_events=history_events,
            history_lines=history_lines,
            history_observed_lines=history_observed_lines,
            history_choices=history_choices,
            dedupe_window=dedupe_window,
            event=event,
            config=cfg,
            game_id=writer.game_id,
        )

    assert session.session is not None
    assert session.session["state"]["speaker"] == "王生"
    assert session.session["state"]["text"] == "算了，没事。"
    assert session.session["state"]["stability"] == "tentative"
    assert events[-1]["type"] == "line_observed"
    assert history_lines == []
    assert len(history_observed_lines) == 1
    assert history_observed_lines[0]["stability"] == "tentative"
    assert history_observed_lines[0]["text"] == "算了，没事。"


@pytest.mark.plugin_unit
def test_ocr_line_second_stable_read_enters_history(tmp_path: Path) -> None:
    writer = OcrReaderBridgeWriter(bridge_root=tmp_path, time_fn=lambda: 1712100100.0)
    writer.start_session(
        DetectedGameWindow(
            hwnd=404,
            title="哀鸿",
            process_name="TheLamentingGeese.exe",
            pid=6104,
        )
    )

    writer.emit_line_observed("王生：算了，没事。", ts="2024-04-02T12:00:00Z")
    assert writer.emit_line("王生：算了，没事。", ts="2024-04-02T12:00:01Z") is True

    game_dir = tmp_path / writer.game_id
    session = read_session_json(game_dir / "session.json")
    events = _read_bridge_events(game_dir / "events.jsonl")
    history_events: list[dict[str, Any]] = []
    history_lines: list[dict[str, Any]] = []
    history_observed_lines: list[dict[str, Any]] = []
    history_choices: list[dict[str, Any]] = []
    dedupe_window: list[dict[str, str]] = []
    cfg = build_config({"galgame": {"bridge_root": str(tmp_path)}})
    for event in events:
        galgame_service.apply_event_to_histories(
            history_events=history_events,
            history_lines=history_lines,
            history_observed_lines=history_observed_lines,
            history_choices=history_choices,
            dedupe_window=dedupe_window,
            event=event,
            config=cfg,
            game_id=writer.game_id,
        )

    assert session.session is not None
    assert session.session["state"]["stability"] == "stable"
    assert events[-1]["type"] == "line_changed"
    assert len(history_lines) == 1
    assert history_lines[0]["speaker"] == "王生"
    assert history_lines[0]["text"] == "算了，没事。"
    assert len(history_observed_lines) == 1
    assert history_observed_lines[0]["stability"] == "stable"


@pytest.mark.plugin_unit
def test_ocr_advance_speed_controls_line_changed_threshold(tmp_path: Path) -> None:
    writer = OcrReaderBridgeWriter(bridge_root=tmp_path, time_fn=lambda: 1712100100.0)
    writer.start_session(
        DetectedGameWindow(
            hwnd=404,
            title="哀鸿",
            process_name="TheLamentingGeese.exe",
            pid=6104,
        )
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config({"galgame": {"bridge_root": str(tmp_path)}}),
        writer=writer,
    )

    manager.update_advance_speed("slow")
    assert manager._emit_line_from_ocr_text("王生：算了，没事。", now=1712100100.0) is False
    assert manager._emit_line_from_ocr_text("王生：算了，没事。", now=1712100101.0) is False
    assert manager._emit_line_from_ocr_text("王生：算了，没事。", now=1712100102.0) is True

    slow_events = _read_bridge_events(tmp_path / writer.game_id / "events.jsonl")
    assert [event["type"] for event in slow_events].count("line_changed") == 1

    fast_root = tmp_path / "fast"
    fast_writer = OcrReaderBridgeWriter(bridge_root=fast_root, time_fn=lambda: 1712100200.0)
    fast_writer.start_session(
        DetectedGameWindow(
            hwnd=405,
            title="哀鸿",
            process_name="TheLamentingGeese.exe",
            pid=6105,
        )
    )
    fast_manager = OcrReaderManager(
        logger=_Logger(),
        config=build_config({"galgame": {"bridge_root": str(fast_root)}}),
        writer=fast_writer,
    )
    fast_manager.update_advance_speed("fast")

    assert fast_manager._emit_line_from_ocr_text("王生：算了，没事。", now=1712100200.0) is True
    fast_events = _read_bridge_events(fast_root / fast_writer.game_id / "events.jsonl")
    assert [event["type"] for event in fast_events][-2:] == ["line_observed", "line_changed"]
