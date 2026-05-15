from __future__ import annotations

from _galgame_test_support import (
    Any,
    DATA_SOURCE_BRIDGE_SDK,
    DATA_SOURCE_MEMORY_READER,
    DATA_SOURCE_OCR_READER,
    Err,
    Future,
    GalgameBridgePlugin,
    OCR_CAPTURE_PROFILE_STAGE_TITLE,
    Ok,
    Path,
    SimpleNamespace,
    _copy_bridge_fixture_scenario,
    _create_game_dir,
    _Ctx,
    _default_bridge_root_raw,
    _event,
    _isolate_galgame_runtime_root,  # noqa: F401
    _Logger,
    _make_effective_config,
    _make_plugin_dirs,
    _noop_install_entry_poll,
    _session,
    _session_state,
    _shared_state,
    _write_events,
    _write_session,
    asyncio,
    build_config,
    build_explain_context,
    build_summarize_context,
    expand_bridge_root,
    galgame_plugin_module,
    galgame_service,
    json,
    pytest,
    read_session_json,
    resolve_effective_current_line,
    tail_events_jsonl,
    threading,
    time,
)

@pytest.mark.asyncio
async def test_install_progress_callback_uses_supported_run_update_fields() -> None:
    class _ProgressPlugin:
        logger = _Logger()

        def __init__(self) -> None:
            self.run_updates: list[dict[str, object]] = []

        async def run_update(self, **kwargs):
            if "status" in kwargs:
                raise TypeError("unexpected status")
            self.run_updates.append(dict(kwargs))
            return {"ok": True}

    plugin = _ProgressPlugin()
    callback = GalgameBridgePlugin._resolve_install_progress_callback(plugin, "run-1")

    await callback(
        {
            "phase": "downloading",
            "message": "Downloading Textractor",
            "progress": 0.25,
            "downloaded_bytes": 10,
            "total_bytes": 20,
            "resume_from": 0,
            "asset_name": "Textractor.zip",
            "release_name": "v1",
        }
    )

    assert plugin.run_updates == [
        {
            "run_id": "run-1",
            "progress": 0.25,
            "stage": "downloading",
            "message": "Downloading Textractor",
            "metrics": {
                "phase": "downloading",
                "downloaded_bytes": 10,
                "total_bytes": 20,
                "resume_from": 0,
                "asset_name": "Textractor.zip",
                "release_name": "v1",
            },
        }
    ]


@pytest.mark.plugin_unit
def test_screen_classified_event_updates_snapshot_state() -> None:
    snapshot = _session_state(scene_id="scene-a", line_id="line-1")
    updated = galgame_service.apply_event_to_snapshot(
        snapshot,
        {
            "seq": 3,
            "ts": "2026-04-29T03:00:00Z",
            "type": "screen_classified",
            "payload": {
                "screen_type": OCR_CAPTURE_PROFILE_STAGE_TITLE,
                "screen_confidence": 0.88,
                "screen_ui_elements": [
                    {
                        "element_id": "start",
                        "text": "Start Game",
                        "bounds": {"left": 10, "top": 20, "right": 110, "bottom": 48},
                    }
                ],
                "screen_debug": {"reason": "title_keywords", "sources": ["full_frame"]},
            },
        },
    )

    assert updated["screen_type"] == OCR_CAPTURE_PROFILE_STAGE_TITLE
    assert updated["screen_confidence"] == pytest.approx(0.88)
    assert updated["screen_ui_elements"][0]["text"] == "Start Game"
    assert updated["screen_debug"]["reason"] == "title_keywords"
    assert updated["ts"] == "2026-04-29T03:00:00Z"


@pytest.mark.plugin_unit
def test_expand_bridge_root_and_read_bom_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    expanded = expand_bridge_root("%LOCALAPPDATA%/N.E.K.O/galgame-bridge")
    assert expanded == tmp_path / "Local" / "N.E.K.O" / "galgame-bridge"

    session_path = tmp_path / "session.json"
    _write_session(
        session_path,
        _session(
            game_id="demo.game",
            session_id="sess-1",
            last_seq=1,
            state=_session_state(speaker="雪乃", text="你好"),
        ),
        bom=True,
    )
    result = read_session_json(session_path)
    assert result.error == ""
    assert result.session is not None
    assert result.session["state"]["speaker"] == "雪乃"


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("platform_value", "use_xdg_data_home", "expected_raw"),
    [
        ("win32", False, "%LOCALAPPDATA%/N.E.K.O/galgame-bridge"),
        ("darwin", False, "~/Library/Application Support/N.E.K.O/galgame-bridge"),
        ("linux", True, "xdg"),
        ("linux", False, "~/.local/share/N.E.K.O/galgame-bridge"),
    ],
)
def test_default_bridge_root_raw_uses_platform_conventions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    platform_value: str,
    use_xdg_data_home: bool,
    expected_raw: str,
) -> None:
    monkeypatch.setattr(galgame_service.sys, "platform", platform_value)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    if use_xdg_data_home:
        xdg_data_home = tmp_path / "xdg-data"
        monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data_home))
        assert _default_bridge_root_raw() == f"{xdg_data_home}/N.E.K.O/galgame-bridge"
        return
    assert _default_bridge_root_raw() == expected_raw


@pytest.mark.plugin_unit
def test_expand_bridge_root_handles_user_home_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir()

    def _fake_expanduser(value: str) -> str:
        if value.startswith("~/"):
            return str(home_dir / value[2:])
        if value == "~":
            return str(home_dir)
        return value

    monkeypatch.setattr("plugin.plugins.galgame_plugin.reader.os.path.expanduser", _fake_expanduser)

    mac_path = expand_bridge_root("~/Library/Application Support/N.E.K.O/galgame-bridge")
    linux_path = expand_bridge_root("~/.local/share/N.E.K.O/galgame-bridge")

    assert mac_path == home_dir / "Library" / "Application Support" / "N.E.K.O" / "galgame-bridge"
    assert linux_path == home_dir / ".local" / "share" / "N.E.K.O" / "galgame-bridge"


@pytest.mark.plugin_unit
@pytest.mark.parametrize("raw_path", ["relative/root", "http://example.invalid/bridge", r"\\server\share"])
def test_expand_bridge_root_rejects_untrusted_paths(raw_path: str) -> None:
    with pytest.raises(ValueError, match="bridge_root must be"):
        expand_bridge_root(raw_path)


@pytest.mark.plugin_unit
@pytest.mark.parametrize("bridge_root_value", [None, "", "   "])
def test_build_config_uses_default_bridge_root_when_missing_or_blank(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bridge_root_value: str | None,
) -> None:
    expected = tmp_path / "auto" / "bridge"
    monkeypatch.setattr(galgame_service, "_default_bridge_root_raw", lambda: str(expected))

    galgame_config = {} if bridge_root_value is None else {"bridge_root": bridge_root_value}
    cfg = build_config({"galgame": galgame_config})

    assert cfg.bridge_root == expected


@pytest.mark.plugin_unit
def test_build_config_prefers_explicit_bridge_root(tmp_path: Path) -> None:
    explicit = tmp_path / "custom" / "bridge"
    cfg = build_config({"galgame": {"bridge_root": str(explicit)}})
    assert cfg.bridge_root == explicit


@pytest.mark.plugin_unit
def test_tail_events_handles_utf8_crlf_and_partial_line(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    game_id = "demo.game"
    session_id = "sess-1"
    first = _event(
        seq=1,
        event_type="line_changed",
        session_id=session_id,
        game_id=game_id,
        payload={"speaker": "雪乃", "text": "今天也一起回家吧。", "line_id": "line-1", "scene_id": "scene-a", "route_id": ""},
        ts="2026-04-21T08:31:00Z",
    )
    second = _event(
        seq=2,
        event_type="choices_shown",
        session_id=session_id,
        game_id=game_id,
        payload={"line_id": "line-1", "scene_id": "scene-a", "route_id": "", "choices": []},
        ts="2026-04-21T08:31:01Z",
    )
    partial = json.dumps(
        _event(
            seq=3,
            event_type="heartbeat",
            session_id=session_id,
            game_id=game_id,
            payload={"state_ts": "2026-04-21T08:31:01Z", "idle_seconds": 5},
            ts="2026-04-21T08:31:06Z",
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    cutoff = len(partial) // 2
    total_size = _write_events(events_path, [first, second], trailing=partial[:cutoff], crlf=True)

    result = tail_events_jsonl(events_path, offset=0, line_buffer=b"")
    assert len(result.events) == 2
    assert result.next_offset == total_size
    assert result.line_buffer == partial[:cutoff]

    with events_path.open("ab") as handle:
        handle.write(partial[cutoff:] + b"\n")

    resumed = tail_events_jsonl(
        events_path,
        offset=result.next_offset,
        line_buffer=result.line_buffer,
    )
    assert [event["seq"] for event in resumed.events] == [3]
    assert resumed.line_buffer == b""


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_startup_binds_latest_session_and_exposes_ui(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    _create_game_dir(
        bridge_root,
        game_id="demo.alpha",
        session_payload=_session(
            game_id="demo.alpha",
            session_id="sess-a",
            last_seq=1,
            state=_session_state(text="alpha"),
        ),
    )
    _create_game_dir(
        bridge_root,
        game_id="demo.beta",
        session_payload=_session(
            game_id="demo.beta",
            session_id="sess-b",
            last_seq=3,
            state=_session_state(text="beta"),
        ),
    )

    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    startup = await plugin.startup()
    assert isinstance(startup, Ok)

    status = await plugin.galgame_get_status()
    snapshot = await plugin.galgame_get_snapshot()
    open_ui = await plugin.galgame_open_ui()

    assert isinstance(status, Ok)
    assert status.value["bound_game_id"] == ""
    assert status.value["active_session_id"] == "sess-b"
    assert status.value["available_game_ids"] == ["demo.alpha", "demo.beta"]
    assert "bound=demo.beta" in status.value["summary"]
    assert "textractor" in status.value
    assert isinstance(snapshot, Ok)
    assert snapshot.value["game_id"] == "demo.beta"
    assert snapshot.value["session_id"] == "sess-b"
    assert isinstance(open_ui, Ok)
    assert open_ui.value["available"] is True
    assert open_ui.value["path"] == "/plugin/galgame_plugin/ui/"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_startup_auto_opens_ui_only_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened_urls: list[str] = []
    monkeypatch.setattr(galgame_plugin_module, "_open_url_in_browser", opened_urls.append)
    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "49001")

    disabled_root = tmp_path / "disabled"
    disabled_root.mkdir()
    disabled_plugin_dir, disabled_bridge_root = _make_plugin_dirs(disabled_root)
    disabled_ctx = _Ctx(disabled_plugin_dir, _make_effective_config(disabled_bridge_root))
    disabled_plugin = GalgameBridgePlugin(disabled_ctx)
    disabled_plugin._poll_bridge = _noop_install_entry_poll  # type: ignore[method-assign]
    disabled_plugin._build_status_payload_async = lambda: asyncio.sleep(0, result={})  # type: ignore[method-assign]
    disabled_plugin._start_ocr_fast_loop = lambda: False  # type: ignore[method-assign]
    disabled_plugin._ensure_ocr_foreground_advance_monitor = lambda: asyncio.sleep(0, result=False)  # type: ignore[method-assign]
    disabled_startup = await disabled_plugin.startup()

    assert isinstance(disabled_startup, Ok)
    assert opened_urls == []

    enabled_root = tmp_path / "enabled"
    enabled_root.mkdir()
    enabled_plugin_dir, enabled_bridge_root = _make_plugin_dirs(enabled_root)
    enabled_ctx = _Ctx(
        enabled_plugin_dir,
        _make_effective_config(enabled_bridge_root, galgame={"auto_open_ui": True}),
    )
    enabled_plugin = GalgameBridgePlugin(enabled_ctx)
    enabled_plugin._poll_bridge = _noop_install_entry_poll  # type: ignore[method-assign]
    enabled_plugin._build_status_payload_async = lambda: asyncio.sleep(0, result={})  # type: ignore[method-assign]
    enabled_plugin._start_ocr_fast_loop = lambda: False  # type: ignore[method-assign]
    enabled_plugin._ensure_ocr_foreground_advance_monitor = lambda: asyncio.sleep(0, result=False)  # type: ignore[method-assign]
    enabled_startup = await enabled_plugin.startup()

    assert isinstance(enabled_startup, Ok)
    assert opened_urls == ["http://127.0.0.1:49001/plugin/galgame_plugin/ui/"]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_bridge_tick_runs_agent_before_slow_background_poll(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(_make_effective_config(bridge_root))
    events: list[str] = []
    poll_started = asyncio.Event()
    poll_continue = asyncio.Event()

    class _TickAgent:
        def __init__(self) -> None:
            self.calls = 0

        async def tick(self, shared: dict[str, Any]) -> None:
            del shared
            self.calls += 1
            events.append("agent_tick")

        async def shutdown(self) -> None:
            return None

    async def _slow_poll(*, force: bool) -> None:
        assert force is False
        events.append("poll_start")
        poll_started.set()
        await poll_continue.wait()
        events.append("poll_done")

    agent = _TickAgent()
    plugin._game_agent = agent  # type: ignore[assignment]
    plugin._poll_bridge = _slow_poll  # type: ignore[method-assign]

    started_at = time.monotonic()
    await plugin.bridge_tick()
    elapsed = time.monotonic() - started_at
    await asyncio.wait_for(poll_started.wait(), timeout=0.5)
    task = plugin._bridge_poll_task

    assert elapsed < 0.5
    assert agent.calls == 1
    assert events[:2] == ["agent_tick", "poll_start"]
    assert task is not None
    assert not task.done()

    status = await plugin._build_status_payload_async()
    assert status["bridge_poll_running"] is True
    assert status["bridge_poll_inflight_seconds"] >= 0.0
    assert status["last_agent_tick_at"] > 0.0

    poll_continue.set()
    await asyncio.wait_for(task, timeout=0.5)

    assert plugin._bridge_poll_task is None
    assert plugin._last_bridge_poll_duration_seconds >= 0.0
    assert events[-1] == "poll_done"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_bridge_tick_does_not_start_concurrent_background_polls(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(_make_effective_config(bridge_root))
    poll_started = asyncio.Event()
    poll_continue = asyncio.Event()
    poll_starts = 0

    class _TickAgent:
        def __init__(self) -> None:
            self.calls = 0

        async def tick(self, shared: dict[str, Any]) -> None:
            del shared
            self.calls += 1

        async def shutdown(self) -> None:
            return None

    async def _slow_poll(*, force: bool) -> None:
        nonlocal poll_starts
        assert force is False
        poll_starts += 1
        poll_started.set()
        await poll_continue.wait()

    agent = _TickAgent()
    plugin._game_agent = agent  # type: ignore[assignment]
    plugin._poll_bridge = _slow_poll  # type: ignore[method-assign]

    await plugin.bridge_tick()
    await asyncio.wait_for(poll_started.wait(), timeout=0.5)
    task = plugin._bridge_poll_task
    await plugin.bridge_tick()

    assert agent.calls == 2
    assert poll_starts == 1
    assert plugin._bridge_poll_task is task

    poll_continue.set()
    assert task is not None
    await asyncio.wait_for(task, timeout=0.5)


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_set_mode_rolls_back_runtime_state_when_reader_mode_persist_fails(
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

    def _fail_reader_mode(**_kwargs):
        raise RuntimeError("store unavailable")

    plugin._config_service = SimpleNamespace(
        persist_preferences=lambda **kwargs: None,
        persist_reader_mode=_fail_reader_mode,
    )
    manager_updates: list[str] = []
    fake_manager = SimpleNamespace(
        update_config=lambda config: manager_updates.append(str(config.reader_mode))
    )
    plugin._memory_reader_manager = fake_manager
    plugin._ocr_reader_manager = fake_manager
    with plugin._state_lock:
        plugin._state.mode = "choice_advisor"
        plugin._state.push_notifications = True
        plugin._state.advance_speed = "medium"
        plugin._state.active_data_source = DATA_SOURCE_OCR_READER
        plugin._state.next_poll_at_monotonic = 123.0
        plugin._pending_ocr_advance_captures = 2
        plugin._last_ocr_advance_capture_requested_at = time.monotonic() - 1.0
        plugin._last_ocr_advance_capture_reason = "manual_foreground_advance"

    result = await plugin.galgame_set_mode(
        mode="companion",
        push_notifications=False,
        advance_speed="fast",
        reader_mode=DATA_SOURCE_MEMORY_READER,
    )

    assert isinstance(result, Err)
    assert plugin._cfg.reader_mode == DATA_SOURCE_OCR_READER
    with plugin._state_lock:
        assert plugin._state.mode == "choice_advisor"
        assert plugin._state.push_notifications is True
        assert plugin._state.advance_speed == "medium"
        assert plugin._state.active_data_source == DATA_SOURCE_OCR_READER
        assert plugin._state.next_poll_at_monotonic == 123.0
    assert plugin._has_pending_ocr_advance_capture() is True
    assert plugin._last_ocr_advance_capture_reason == "manual_foreground_advance"
    assert DATA_SOURCE_MEMORY_READER in manager_updates
    assert DATA_SOURCE_OCR_READER in manager_updates


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_set_mode_returns_compatible_payload_when_already_applied(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(bridge_root)
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, cfg))
    plugin._cfg = build_config(cfg)

    persist_calls: list[str] = []
    plugin._config_service = SimpleNamespace(
        persist_preferences=lambda **kwargs: persist_calls.append("preferences"),
        persist_reader_mode=lambda **kwargs: persist_calls.append("reader_mode"),
    )
    manager_updates: list[str] = []
    fake_manager = SimpleNamespace(
        update_config=lambda config: manager_updates.append(str(config.reader_mode))
    )
    plugin._memory_reader_manager = fake_manager
    plugin._ocr_reader_manager = fake_manager

    async def _fail_monitor() -> bool:
        raise AssertionError("idempotent set_mode must not start foreground monitor")

    plugin._ensure_ocr_foreground_advance_monitor = _fail_monitor  # type: ignore[method-assign]
    with plugin._state_lock:
        plugin._state.mode = "choice_advisor"
        plugin._state.push_notifications = True
        plugin._state.advance_speed = "medium"

    result = await plugin.galgame_set_mode(
        mode="choice_advisor",
        push_notifications=True,
    )

    assert isinstance(result, Ok)
    assert result.value["mode"] == "choice_advisor"
    assert result.value["push_notifications"] is True
    assert result.value["advance_speed"] == "medium"
    assert result.value["reader_mode"] == plugin._cfg.reader_mode
    assert result.value["summary"] == (
        "mode=choice_advisor "
        "push_notifications=True "
        "advance_speed=medium "
        f"reader_mode={plugin._cfg.reader_mode}"
    )
    assert result.value["skipped"] is True
    assert result.value["skip_reason"] == "already_applied"
    assert persist_calls == []
    assert manager_updates == []


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_background_bridge_poll_exception_records_error(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(_make_effective_config(bridge_root))

    async def _failing_poll(*, force: bool) -> None:
        assert force is False
        raise RuntimeError("ocr exploded")

    plugin._poll_bridge = _failing_poll  # type: ignore[method-assign]

    await plugin.bridge_tick()
    task = plugin._bridge_poll_task
    assert task is not None
    await asyncio.wait_for(task, timeout=0.5)

    with plugin._state_lock:
        last_error = dict(plugin._state.last_error)

    assert plugin._bridge_poll_task is None
    assert last_error["source"] == "bridge_reader"
    assert "bridge background poll failed: ocr exploded" in last_error["message"]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_shutdown_cancels_background_bridge_poll(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(_make_effective_config(bridge_root))
    poll_started = asyncio.Event()
    cancelled = False

    async def _slow_poll(*, force: bool) -> None:
        nonlocal cancelled
        assert force is False
        poll_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled = True
            raise

    plugin._poll_bridge = _slow_poll  # type: ignore[method-assign]

    await plugin.bridge_tick()
    await asyncio.wait_for(poll_started.wait(), timeout=0.5)
    task = plugin._bridge_poll_task
    assert task is not None

    result = await plugin.shutdown()

    assert isinstance(result, Ok)
    assert cancelled is True
    assert task.done()
    assert plugin._bridge_poll_task is None


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_shutdown_logs_noncritical_cleanup_failures(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    warning_messages: list[str] = []

    class _CaptureLogger(_Logger):
        def warning(self, message, *args, **kwargs):
            warning_messages.append(str(message).format(*args))

    class _FailingManager:
        def __init__(self, label: str) -> None:
            self.label = label

        async def shutdown(self) -> None:
            raise RuntimeError(f"{self.label} exploded")

    plugin = GalgameBridgePlugin(ctx)
    plugin.logger = _CaptureLogger()
    plugin._memory_reader_manager = _FailingManager("memory")
    plugin._ocr_reader_manager = _FailingManager("ocr")

    result = await plugin.shutdown()

    assert isinstance(result, Ok)
    assert any(
        "galgame memory reader shutdown failed: memory exploded" in item
        for item in warning_messages
    )
    assert any(
        "galgame OCR reader shutdown failed: ocr exploded" in item
        for item in warning_messages
    )


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_bridge_tick_cancels_stale_background_poll(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(_make_effective_config(bridge_root))
    poll_started = asyncio.Event()
    cancelled = False

    async def _stuck_poll(*, force: bool) -> None:
        nonlocal cancelled
        assert force is False
        poll_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled = True
            raise

    plugin._poll_bridge = _stuck_poll  # type: ignore[method-assign]

    await plugin.bridge_tick()
    await asyncio.wait_for(poll_started.wait(), timeout=0.5)
    task = plugin._bridge_poll_task
    assert task is not None

    with plugin._state_lock:
        plugin._pending_ocr_advance_captures = 8
    plugin._bridge_poll_started_at = (
        time.monotonic() - plugin._background_bridge_poll_stale_timeout_seconds() - 1.0
    )
    await plugin.bridge_tick()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.5)

    with plugin._state_lock:
        last_error = dict(plugin._state.last_error)

    assert cancelled is True
    assert plugin._bridge_poll_task is None
    assert plugin._has_pending_ocr_advance_capture() is False
    assert last_error["source"] == "bridge_reader"
    assert "timed out" in last_error["message"]


@pytest.mark.plugin_unit
def test_background_bridge_poll_done_callback_does_not_clear_newer_task(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    old_task: Future[None] = Future()
    newer_task: Future[None] = Future()

    with plugin._bridge_poll_task_lock:
        plugin._bridge_poll_task = newer_task
    old_task.set_result(None)
    plugin._clear_completed_background_bridge_poll(old_task)

    assert plugin._bridge_poll_task is newer_task

    newer_task.set_result(None)
    plugin._clear_completed_background_bridge_poll(newer_task)

    assert plugin._bridge_poll_task is None


@pytest.mark.plugin_unit
def test_stop_bridge_poll_loop_cancels_pending_loop_tasks(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    started = threading.Event()
    cancelled = threading.Event()

    async def _pending_task() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    loop = plugin._ensure_bridge_poll_loop()
    assert loop is not None

    async def _touch_poll_lock() -> int:
        plugin._poll_bridge_async_lock()
        return id(asyncio.get_running_loop())

    loop_key = asyncio.run_coroutine_threadsafe(_touch_poll_lock(), loop).result(timeout=1.0)
    assert loop_key in plugin._poll_bridge_locks

    future = asyncio.run_coroutine_threadsafe(_pending_task(), loop)
    assert started.wait(timeout=1.0)

    plugin._stop_bridge_poll_loop()

    assert cancelled.wait(timeout=1.0)
    assert future.done()
    assert plugin._bridge_poll_loop is None
    assert plugin._bridge_poll_thread is None
    assert loop_key not in plugin._poll_bridge_locks


@pytest.mark.plugin_unit
def test_config_service_persist_runtime_state_uses_defaults_for_missing_keys() -> None:
    class _Persist:
        def __init__(self) -> None:
            self.payload: dict[str, object] = {}

        def persist_runtime(self, **kwargs) -> None:
            self.payload = dict(kwargs)

    persist = _Persist()
    service = galgame_plugin_module.GalgamePluginConfigService(
        SimpleNamespace(_persist=persist)
    )

    service.persist_runtime_state({})

    assert persist.payload == {
        "session_id": "",
        "events_byte_offset": 0,
        "events_file_size": 0,
        "last_seq": 0,
        "dedupe_window": [],
        "last_error": {},
    }


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_set_mode_and_bind_game_persist_across_restart(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    _create_game_dir(
        bridge_root,
        game_id="demo.alpha",
        session_payload=_session(
            game_id="demo.alpha",
            session_id="sess-a",
            last_seq=2,
            state=_session_state(text="alpha"),
        ),
    )
    _create_game_dir(
        bridge_root,
        game_id="demo.beta",
        session_payload=_session(
            game_id="demo.beta",
            session_id="sess-b",
            last_seq=1,
            state=_session_state(text="beta"),
        ),
    )

    ctx1 = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin1 = GalgameBridgePlugin(ctx1)
    await plugin1.startup()

    mode_result = await plugin1.galgame_set_mode(
        mode="choice_advisor",
        push_notifications=False,
    )
    bind_result = await plugin1.galgame_bind_game(game_id="demo.beta")
    assert isinstance(mode_result, Ok)
    assert isinstance(bind_result, Ok)

    ctx2 = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin2 = GalgameBridgePlugin(ctx2)
    await plugin2.startup()
    status = await plugin2.galgame_get_status()
    assert isinstance(status, Ok)
    assert status.value["mode"] == "choice_advisor"
    assert status.value["push_notifications"] is False
    assert status.value["bound_game_id"] == "demo.beta"
    assert status.value["active_session_id"] == "sess-b"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_save_loaded_and_repeated_line_do_not_duplicate_stable_history(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    game_id = "demo.alpha"
    session_id = "sess-a"
    events = [
        _event(
            seq=1,
            event_type="session_started",
            session_id=session_id,
            game_id=game_id,
            payload={
                "game_title": "demo.alpha",
                "engine": "renpy",
                "locale": "ja-JP",
                "started_at": "2026-04-21T08:30:00Z",
                "scene_id": "boot",
                "line_id": "",
                "route_id": "",
                "is_menu_open": False,
                "speaker": "",
                "text": "",
                "choices": [],
                "save_context": {"kind": "unknown", "slot_id": "", "display_name": ""},
            },
            ts="2026-04-21T08:30:00Z",
        ),
        _event(
            seq=2,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            payload={
                "speaker": "雪乃",
                "text": "今天也一起回家吧。",
                "line_id": "script/ch1.rpy:120",
                "scene_id": "ch1_after_school",
                "route_id": "",
            },
            ts="2026-04-21T08:31:00Z",
        ),
        _event(
            seq=3,
            event_type="save_loaded",
            session_id=session_id,
            game_id=game_id,
            payload={
                "reason": "rollback",
                "scene_id": "ch1_after_school",
                "line_id": "script/ch1.rpy:120",
                "route_id": "",
                "save_context": {"kind": "rollback", "slot_id": "", "display_name": "rollback"},
            },
            ts="2026-04-21T08:31:10Z",
        ),
        _event(
            seq=4,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            payload={
                "speaker": "雪乃",
                "text": "今天也一起回家吧。",
                "line_id": "script/ch1.rpy:120",
                "scene_id": "ch1_after_school",
                "route_id": "",
            },
            ts="2026-04-21T08:31:11Z",
        ),
    ]
    _create_game_dir(
        bridge_root,
        game_id=game_id,
        session_payload=_session(
            game_id=game_id,
            session_id=session_id,
            last_seq=4,
            state=_session_state(
                speaker="雪乃",
                text="今天也一起回家吧。",
                scene_id="ch1_after_school",
                line_id="script/ch1.rpy:120",
                ts="2026-04-21T08:31:11Z",
            ),
        ),
        events=events,
    )

    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    history = await plugin.galgame_get_history(limit=20, include_events=True)
    assert isinstance(history, Ok)
    assert len(history.value["events"]) == 4
    assert len(history.value["stable_lines"]) == 1
    assert history.value["stable_lines"][0]["line_id"] == "script/ch1.rpy:120"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_bridge_fixture_manual_load_round_exposes_bridge_sdk_status_snapshot_and_history(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    _copy_bridge_fixture_scenario(bridge_root, "manual_load")

    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    await plugin._poll_bridge(force=True)

    status = await plugin.galgame_get_status()
    snapshot = await plugin.galgame_get_snapshot()
    history = await plugin.galgame_get_history(limit=20, include_events=True)

    assert isinstance(status, Ok)
    assert status.value["active_data_source"] == DATA_SOURCE_BRIDGE_SDK
    assert status.value["summary"].startswith("已通过 Bridge SDK 连接")
    assert status.value["memory_reader_runtime"]["detail"] == "disabled_by_config"

    assert isinstance(snapshot, Ok)
    assert snapshot.value["snapshot"]["scene_id"] == "after_school"
    assert snapshot.value["snapshot"]["line_id"] == "script.rpy:28"
    assert snapshot.value["snapshot"]["is_menu_open"] is True
    assert snapshot.value["snapshot"]["save_context"]["kind"] == "manual"
    assert len(snapshot.value["snapshot"]["choices"]) == 2

    assert isinstance(history, Ok)
    assert history.value["events"][-2]["type"] == "save_loaded"
    assert history.value["events"][-2]["payload"]["reason"] == "load"
    assert history.value["events"][-1]["type"] == "choices_shown"
    assert history.value["events"][-1]["payload"]["line_id"] == "script.rpy:28"
    assert history.value["stable_lines"][-1]["line_id"] == "script.rpy:45"
    assert len(history.value["stable_lines"]) == 6


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_bridge_fixture_rollback_round_preserves_history_and_supports_phase2_llm_entries(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    _copy_bridge_fixture_scenario(bridge_root, "rollback")

    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            llm={"target_entry_ref": "fake_llm:run"},
            ocr_reader={"enabled": False},
            rapidocr={"enabled": False},
        ),
    )

    async def _handler(**kwargs):
        params = kwargs.get("params") or {}
        operation = params.get("operation")
        if operation == "explain_line":
            return {"explanation": "这是回滚后的菜单锚点。", "evidence": []}
        if operation == "summarize_scene":
            return {
                "summary": "场景重新回到了 after_school 的选项前。",
                "key_points": [{"type": "decision", "text": "rollback 已完成。"}],
            }
        if operation == "suggest_choice":
            context = params.get("context") or {}
            visible_choices = context.get("visible_choices") or []
            return {
                "choices": [
                    {
                        "choice_id": visible_choices[0]["choice_id"],
                        "text": visible_choices[0]["text"],
                        "rank": 1,
                        "reason": "继续验证 rollback 后的菜单消费。",
                    }
                ]
            }
        raise AssertionError(f"unexpected operation: {operation}")

    ctx.entry_handler = _handler
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    await plugin._poll_bridge(force=True)

    snapshot = await plugin.galgame_get_snapshot()
    history = await plugin.galgame_get_history(limit=20, include_events=True)
    explain = await plugin.galgame_explain_line()
    summarize = await plugin.galgame_summarize_scene()
    suggest = await plugin.galgame_suggest_choice()

    assert isinstance(snapshot, Ok)
    assert snapshot.value["snapshot"]["scene_id"] == "after_school"
    assert snapshot.value["snapshot"]["save_context"]["kind"] == "rollback"
    assert snapshot.value["snapshot"]["is_menu_open"] is True

    assert isinstance(history, Ok)
    assert history.value["events"][-3]["type"] == "save_loaded"
    assert history.value["events"][-3]["payload"]["reason"] == "rollback"
    repeated_lines = [
        item for item in history.value["stable_lines"] if item["line_id"] == "script.rpy:28"
    ]
    assert len(repeated_lines) == 1

    assert isinstance(explain, Ok)
    assert explain.value["degraded"] is False
    assert explain.value["line_id"] == "script.rpy:28"
    assert explain.value["explanation"] == "这是回滚后的菜单锚点。"

    assert isinstance(summarize, Ok)
    assert summarize.value["degraded"] is False
    assert summarize.value["scene_id"] == "after_school"
    assert summarize.value["summary"] == "场景重新回到了 after_school 的选项前。"

    assert isinstance(suggest, Ok)
    assert suggest.value["degraded"] is False
    assert suggest.value["choices"][0]["choice_id"] == "script.rpy:28#choice0"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_restart_restores_cursor_and_processes_new_tail(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    game_id = "demo.alpha"
    session_id = "sess-a"
    game_dir = _create_game_dir(
        bridge_root,
        game_id=game_id,
        session_payload=_session(
            game_id=game_id,
            session_id=session_id,
            last_seq=2,
            state=_session_state(
                speaker="雪乃",
                text="旧台词",
                line_id="line-1",
                scene_id="scene-a",
                ts="2026-04-21T08:30:02Z",
            ),
        ),
        events=[
            _event(
                seq=1,
                event_type="session_started",
                session_id=session_id,
                game_id=game_id,
                payload={
                    "game_title": game_id,
                    "engine": "renpy",
                    "locale": "ja-JP",
                    "started_at": "2026-04-21T08:30:00Z",
                    "scene_id": "boot",
                    "line_id": "",
                    "route_id": "",
                    "is_menu_open": False,
                    "speaker": "",
                    "text": "",
                    "choices": [],
                    "save_context": {"kind": "unknown", "slot_id": "", "display_name": ""},
                },
                ts="2026-04-21T08:30:00Z",
            ),
            _event(
                seq=2,
                event_type="line_changed",
                session_id=session_id,
                game_id=game_id,
                payload={
                    "speaker": "雪乃",
                    "text": "旧台词",
                    "line_id": "line-1",
                    "scene_id": "scene-a",
                    "route_id": "",
                },
                ts="2026-04-21T08:30:02Z",
            ),
        ],
    )

    ctx1 = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin1 = GalgameBridgePlugin(ctx1)
    await plugin1.startup()

    new_event = _event(
        seq=3,
        event_type="line_changed",
        session_id=session_id,
        game_id=game_id,
        payload={
            "speaker": "雪乃",
            "text": "重启后新增台词",
            "line_id": "line-2",
            "scene_id": "scene-a",
            "route_id": "",
        },
        ts="2026-04-21T08:30:05Z",
    )
    with (game_dir / "events.jsonl").open("ab") as handle:
        handle.write(
            json.dumps(new_event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
    _write_session(
        game_dir / "session.json",
        _session(
            game_id=game_id,
            session_id=session_id,
            last_seq=3,
            state=_session_state(
                speaker="雪乃",
                text="重启后新增台词",
                line_id="line-2",
                scene_id="scene-a",
                ts="2026-04-21T08:30:05Z",
            ),
        ),
    )

    ctx2 = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin2 = GalgameBridgePlugin(ctx2)
    await plugin2.startup()
    history = await plugin2.galgame_get_history(limit=20, include_events=True)
    assert isinstance(history, Ok)
    assert history.value["events"][-1]["seq"] == 3
    assert history.value["stable_lines"][-1]["line_id"] == "line-2"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_truncation_sets_stream_reset_pending(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    game_id = "demo.alpha"
    session_id = "sess-a"
    game_dir = _create_game_dir(
        bridge_root,
        game_id=game_id,
        session_payload=_session(
            game_id=game_id,
            session_id=session_id,
            last_seq=2,
            state=_session_state(text="alpha"),
        ),
        events=[
            _event(
                seq=1,
                event_type="session_started",
                session_id=session_id,
                game_id=game_id,
                payload={
                    "game_title": game_id,
                    "engine": "renpy",
                    "locale": "ja-JP",
                    "started_at": "2026-04-21T08:30:00Z",
                    "scene_id": "boot",
                    "line_id": "",
                    "route_id": "",
                    "is_menu_open": False,
                    "speaker": "",
                    "text": "",
                    "choices": [],
                    "save_context": {"kind": "unknown", "slot_id": "", "display_name": ""},
                },
                ts="2026-04-21T08:30:00Z",
            ),
            _event(
                seq=2,
                event_type="line_changed",
                session_id=session_id,
                game_id=game_id,
                payload={
                    "speaker": "雪乃",
                    "text": "旧台词",
                    "line_id": "line-1",
                    "scene_id": "scene-a",
                    "route_id": "",
                },
                ts="2026-04-21T08:30:02Z",
            ),
        ],
    )

    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    (game_dir / "events.jsonl").write_bytes(b"")
    await plugin._poll_bridge(force=True)
    status = await plugin.galgame_get_status()
    assert isinstance(status, Ok)
    assert status.value["stream_reset_pending"] is True


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_stale_then_new_event_recovers_to_active(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    game_id = "demo.alpha"
    session_id = "sess-a"
    game_dir = _create_game_dir(
        bridge_root,
        game_id=game_id,
        session_payload=_session(
            game_id=game_id,
            session_id=session_id,
            last_seq=1,
            state=_session_state(text="alpha"),
        ),
        events=[
            _event(
                seq=1,
                event_type="line_changed",
                session_id=session_id,
                game_id=game_id,
                payload={
                    "speaker": "雪乃",
                    "text": "旧台词",
                    "line_id": "line-1",
                    "scene_id": "scene-a",
                    "route_id": "",
                },
                ts="2026-04-21T08:30:02Z",
            )
        ],
    )

    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()

    with plugin._state_lock:
        plugin._state.last_seen_data_monotonic = time.monotonic() - 5.0

    await plugin._poll_bridge(force=True)
    stale_status = await plugin.galgame_get_status()
    assert isinstance(stale_status, Ok)
    assert stale_status.value["connection_state"] == "stale"

    with (game_dir / "events.jsonl").open("ab") as handle:
        handle.write(
            json.dumps(
                _event(
                    seq=2,
                    event_type="line_changed",
                    session_id=session_id,
                    game_id=game_id,
                    payload={
                        "speaker": "雪乃",
                        "text": "新台词",
                        "line_id": "line-2",
                        "scene_id": "scene-a",
                        "route_id": "",
                    },
                    ts="2026-04-21T08:30:06Z",
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
    _write_session(
        game_dir / "session.json",
        _session(
            game_id=game_id,
            session_id=session_id,
            last_seq=2,
            state=_session_state(
                speaker="雪乃",
                text="新台词",
                line_id="line-2",
                scene_id="scene-a",
                ts="2026-04-21T08:30:06Z",
            ),
        ),
    )

    await plugin._poll_bridge(force=True)
    active_status = await plugin.galgame_get_status()
    assert isinstance(active_status, Ok)
    assert active_status.value["connection_state"] == "active"


@pytest.mark.plugin_unit
def test_summarize_context_uses_observed_lines_when_stable_history_is_empty() -> None:
    context = build_summarize_context(
        _shared_state(
            snapshot=_session_state(
                speaker="王生",
                text="算了，没事。",
                scene_id="ocr:scene-a",
                line_id="ocr:line-1",
                ts="2024-04-02T12:00:00Z",
            ),
            history_lines=[],
            history_observed_lines=[
                {
                    "line_id": "ocr:line-1",
                    "speaker": "王生",
                    "text": "算了，没事。",
                    "scene_id": "ocr:scene-a",
                    "route_id": "",
                    "stability": "tentative",
                    "ts": "2024-04-02T12:00:00Z",
                }
            ],
        ),
        scene_id="ocr:scene-a",
    )

    assert context["stable_lines"] == []
    assert len(context["observed_lines"]) == 1
    assert context["recent_lines"][0]["stability"] == "tentative"
    assert "算了，没事。" in context["scene_summary_seed"]


@pytest.mark.plugin_unit
def test_effective_current_line_and_explain_context_fall_back_to_observed() -> None:
    shared = _shared_state(
        snapshot=_session_state(
            speaker="",
            text="",
            scene_id="",
            line_id="",
            ts="2024-04-02T12:00:00Z",
        ),
        history_lines=[],
        history_observed_lines=[
            {
                "line_id": "ocr:line-1",
                "speaker": "王生",
                "text": "算了，没事。",
                "scene_id": "ocr:unknown_scene",
                "route_id": "ocr:route",
                "stability": "tentative",
                "ts": "2024-04-02T12:00:01Z",
            }
        ],
        active_data_source=DATA_SOURCE_OCR_READER,
    )

    effective = resolve_effective_current_line(shared)
    context = build_explain_context(shared, line_id="")

    assert effective is not None
    assert effective["source"] == "observed"
    assert context["line_id"] == "ocr:line-1"
    assert context["text"] == "算了，没事。"
    assert context["observed_lines"][0]["text"] == "算了，没事。"
