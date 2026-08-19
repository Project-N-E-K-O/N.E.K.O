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
    _ocr_reader_session,
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
from plugin.plugins.galgame_plugin.reader import (
    EventStreamBoundary,
    read_stream_checkpoint as read_events_checkpoint,
    snapshot_events_boundary as read_events_boundary,
)


def _append_event(events_path: Path, event: dict[str, object]) -> None:
    with events_path.open("ab") as handle:
        handle.write(
            json.dumps(
                event,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
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
def test_preexisting_session_reattachment_state_is_count_bounded(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    plugin._startup_existing_session_ids = set()

    for index in range(20):
        session_id = f"preexisting-{index}"
        identity = (DATA_SOURCE_BRIDGE_SDK, "demo.alpha", session_id)
        plugin._startup_existing_session_ids.add(identity)
        plugin._remember_active_preexisting_session_state(
            {
                "active_data_source": DATA_SOURCE_BRIDGE_SDK,
                "active_game_id": "demo.alpha",
                "active_session_id": session_id,
                "history_events": [{"seq": index + 1}],
                "last_seq": index + 1,
            }
        )

    expected = {
        (DATA_SOURCE_BRIDGE_SDK, "demo.alpha", f"preexisting-{index}")
        for index in range(4, 20)
    }
    assert set(plugin._startup_preexisting_session_states) == expected
    assert plugin._startup_existing_session_ids == expected


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


@pytest.mark.plugin_unit
def test_tail_events_detects_nonempty_stream_truncated_before_cursor(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_bytes(b'{"session_id":"sess-a","seq":2}\n')

    result = tail_events_jsonl(
        events_path,
        offset=events_path.stat().st_size + 128,
        line_buffer=b"partial",
    )

    assert result.reset_detected is True
    assert result.file_size == events_path.stat().st_size
    assert result.line_buffer == b""


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_first_bridge_poll_binds_latest_session_and_exposes_ui(tmp_path: Path) -> None:
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
    assert startup.value["result"]["available_game_ids"] == []
    await plugin._poll_bridge(force=True)

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
async def test_bridge_tick_initializes_vision_once_outside_event_loop(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    plugin._cfg = build_config(_make_effective_config(bridge_root))
    event_loop_thread_id = threading.get_ident()

    class _LazyVisionManager:
        def __init__(self) -> None:
            self.calls = 0
            self.initialized = False
            self.thread_id = 0

        def vision_classifier_initialization_pending(self) -> bool:
            return not self.initialized

        def initialize_vision_classifier_if_needed(self) -> None:
            self.calls += 1
            self.thread_id = threading.get_ident()
            self.initialized = True

    manager = _LazyVisionManager()
    plugin._ocr_reader_manager = manager  # type: ignore[assignment]
    plugin._refresh_ocr_foreground_state = lambda: None  # type: ignore[method-assign]
    plugin._ocr_foreground_advance_monitor_active = lambda: True  # type: ignore[method-assign]
    plugin._start_background_bridge_poll = lambda: False  # type: ignore[method-assign]
    plugin._start_ocr_fast_loop = lambda: False  # type: ignore[method-assign]

    await plugin.bridge_tick()
    await plugin.bridge_tick()

    assert manager.calls == 1
    assert manager.thread_id != event_loop_thread_id


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
async def test_set_mode_rejects_empty_reader_mode(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    plugin._cfg = build_config(_make_effective_config(bridge_root))

    result = await plugin.galgame_set_mode(
        mode="companion",
        reader_mode="",
    )

    assert isinstance(result, Err)
    assert "invalid reader_mode" in str(result.error)


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
async def test_shutdown_defers_ocr_cancellation_until_all_resources_close(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    cleanup_order: list[str] = []

    class _CancellingOcrReader:
        async def shutdown(self) -> None:
            cleanup_order.append("ocr")
            raise asyncio.CancelledError()

    class _GameAgent:
        async def drain_summary_tasks(self, *, timeout: float) -> None:
            assert timeout == 5.0
            cleanup_order.append("agent-drain")

        async def shutdown(self) -> None:
            cleanup_order.append("agent")

    class _ShutdownResource:
        def __init__(self, label: str) -> None:
            self.label = label

        async def shutdown(self) -> None:
            cleanup_order.append(self.label)

    class _Store:
        async def close(self) -> None:
            cleanup_order.append("store")

    plugin._ocr_reader_manager = _CancellingOcrReader()
    plugin._game_agent = _GameAgent()
    plugin._llm_gateway = _ShutdownResource("llm")
    plugin._host_agent_adapter = _ShutdownResource("host")
    plugin.store = _Store()

    with pytest.raises(asyncio.CancelledError):
        await plugin.shutdown()

    assert cleanup_order == ["ocr", "agent-drain", "agent", "llm", "host", "store"]


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


@pytest.mark.plugin_unit
def test_runtime_restore_keeps_cursor_but_discards_persisted_dedupe_window(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))

    plugin._set_runtime_from_store(
        {
            "bound_game_id": "demo.alpha",
            "session_id": "sess-a",
            "events_byte_offset": 321,
            "events_file_size": 654,
            "last_seq": 7,
            "dedupe_window": [
                {
                    "game_id": "demo.alpha",
                    "scene_id": "scene-a",
                    "line_id": "line-old",
                    "normalized_text": "旧台词",
                }
            ],
        },
        [],
    )

    restored = plugin._snapshot_state()
    assert restored["bound_game_id"] == "demo.alpha"
    assert restored["active_session_id"] == "sess-a"
    assert restored["events_byte_offset"] == 321
    assert restored["events_file_size"] == 654
    assert restored["last_seq"] == 7
    assert restored["dedupe_window"] == []


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
    await plugin1._poll_bridge(force=True)

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
    await plugin2._poll_bridge(force=True)
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
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    session_started_at = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(plugin._plugin_run_started_at + 1.0),
    )
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
                "started_at": session_started_at,
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
            started_at=session_started_at,
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

    await plugin._poll_bridge(force=True)
    history = await plugin.galgame_get_history(limit=20, include_events=True)
    assert isinstance(history, Ok)
    assert len(history.value["events"]) == 4
    assert len(history.value["stable_lines"]) == 1
    assert history.value["stable_lines"][0]["line_id"] == "script/ch1.rpy:120"


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_current_session_poll_preserves_internal_save_boundary(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    game_id = "demo.load-boundary"
    session_id = "sess-load-boundary"
    plugin = GalgameBridgePlugin(
        _Ctx(plugin_dir, _make_effective_config(bridge_root))
    )
    await plugin.startup()
    try:
        session_started_at = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(plugin._plugin_run_started_at + 1.0),
        )
        source_state = _session_state(
            scene_id="scene-a",
            route_id="route-a",
            ts="2026-04-21T08:31:02Z",
        )
        source_state["save_context"] = {
            "kind": "load",
            "slot_id": "slot-a",
            "display_name": "Slot A",
        }
        game_dir = _create_game_dir(
            bridge_root,
            game_id=game_id,
            session_payload=_session(
                game_id=game_id,
                session_id=session_id,
                last_seq=1,
                started_at=session_started_at,
                state=source_state,
            ),
            events=[
                _event(
                    seq=1,
                    event_type="session_started",
                    session_id=session_id,
                    game_id=game_id,
                    payload={
                        "scene_id": "scene-a",
                        "route_id": "route-a",
                        "save_context": {"kind": "unknown"},
                    },
                    ts="2026-04-21T08:31:01Z",
                ),
            ],
        )

        await plugin._poll_bridge(force=True)
        _append_event(
            game_dir / "events.jsonl",
            _event(
                seq=2,
                event_type="save_loaded",
                session_id=session_id,
                game_id=game_id,
                payload={
                    "reason": "load",
                    "scene_id": "scene-a",
                    "route_id": "route-a",
                    "save_context": source_state["save_context"],
                },
                ts="2026-04-21T08:31:02Z",
            ),
        )
        _write_session(
            game_dir / "session.json",
            _session(
                game_id=game_id,
                session_id=session_id,
                last_seq=2,
                started_at=session_started_at,
                state=source_state,
            ),
        )
        await plugin._poll_bridge(force=True)
        first_boundary = plugin._snapshot_state()["latest_snapshot"][
            "save_boundary"
        ]
        assert first_boundary == {
            "kind": "load",
            "seq": 2,
            "ts": "2026-04-21T08:31:02Z",
        }

        await plugin._poll_bridge(force=True)
        assert plugin._snapshot_state()["latest_snapshot"][
            "save_boundary"
        ] == first_boundary
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_bridge_fixture_manual_load_round_exposes_bridge_sdk_status_snapshot_and_history(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    game_dir = _copy_bridge_fixture_scenario(bridge_root, "manual_load")
    session_read = read_session_json(game_dir / "session.json")
    assert session_read.session is not None
    current_session = dict(session_read.session)
    current_session["started_at"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(plugin._plugin_run_started_at + 1.0),
    )
    _write_session(game_dir / "session.json", current_session)
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
    game_dir = _copy_bridge_fixture_scenario(bridge_root, "rollback")
    session_read = read_session_json(game_dir / "session.json")
    assert session_read.session is not None
    current_session = dict(session_read.session)
    current_session["started_at"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(plugin._plugin_run_started_at + 1.0),
    )
    _write_session(game_dir / "session.json", current_session)
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
async def test_restart_baselines_existing_session_and_processes_only_post_mount_tail_once(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    game_id = "demo.alpha"
    session_id = "sess-a"
    old_choices = [
        {
            "choice_id": "line-old#choice0",
            "text": "旧选项",
            "index": 0,
            "enabled": True,
        }
    ]
    game_dir = _create_game_dir(
        bridge_root,
        game_id=game_id,
        session_payload=_session(
            game_id=game_id,
            session_id=session_id,
            last_seq=3,
            started_at="2000-01-01T00:00:00Z",
            state=_session_state(
                speaker="雪乃",
                text="旧台词",
                choices=old_choices,
                line_id="line-old",
                scene_id="scene-a",
                is_menu_open=True,
                ts="2000-01-01T00:00:02Z",
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
                    "started_at": "2000-01-01T00:00:00Z",
                    "scene_id": "boot",
                    "line_id": "",
                    "route_id": "",
                    "is_menu_open": False,
                    "speaker": "",
                    "text": "",
                    "choices": [],
                    "save_context": {"kind": "unknown", "slot_id": "", "display_name": ""},
                },
                ts="2000-01-01T00:00:00Z",
            ),
            _event(
                seq=2,
                event_type="line_changed",
                session_id=session_id,
                game_id=game_id,
                payload={
                    "speaker": "雪乃",
                    "text": "旧台词",
                    "line_id": "line-old",
                    "scene_id": "scene-a",
                    "route_id": "",
                },
                ts="2000-01-01T00:00:02Z",
            ),
            _event(
                seq=3,
                event_type="choices_shown",
                session_id=session_id,
                game_id=game_id,
                payload={
                    "line_id": "line-old",
                    "scene_id": "scene-a",
                    "route_id": "",
                    "choices": old_choices,
                },
                ts="2000-01-01T00:00:03Z",
            ),
        ],
    )
    events_path = game_dir / "events.jsonl"
    events_before_mount = events_path.read_bytes()

    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    try:
        await plugin._poll_bridge(force=True)

        mounted = plugin._snapshot_state()
        assert mounted["history_events"] == []
        assert mounted["history_lines"] == []
        assert mounted["history_observed_lines"] == []
        assert mounted["history_choices"] == []
        assert mounted["dedupe_window"] == []
        assert mounted["line_buffer"] == b""
        assert mounted["events_byte_offset"] == len(events_before_mount)
        assert mounted["events_file_size"] == len(events_before_mount)
        assert mounted["last_seq"] == 3
        assert mounted["latest_snapshot"].get("speaker", "") == ""
        assert mounted["latest_snapshot"].get("text", "") == ""
        assert mounted["latest_snapshot"].get("line_id", "") == ""
        assert mounted["latest_snapshot"].get("stability", "") == ""
        assert mounted["latest_snapshot"].get("choices", []) == []

        status = await plugin.galgame_get_status()
        snapshot = await plugin.galgame_get_snapshot()
        history = await plugin.galgame_get_history(limit=20, include_events=True)
        assert isinstance(status, Ok)
        assert isinstance(snapshot, Ok)
        assert isinstance(history, Ok)
        assert status.value["effective_current_line"] == {}
        assert snapshot.value["effective_current_line"] == {}
        assert history.value["events"] == []
        assert history.value["stable_lines"] == []
        assert history.value["observed_lines"] == []
        assert history.value["choices"] == []
        assert events_path.read_bytes() == events_before_mount

        assert plugin._game_agent is not None
        agent_context = plugin._game_agent._build_agent_reply_context(
            mounted,
            prompt="当前台词是什么？",
        )
        public_context = agent_context["public_context"]
        assert public_context["current_line"]["text"] == ""
        assert public_context["latest_line"] == ""
        assert public_context["recent_lines"] == []
        assert public_context["recent_choices"] == []
        assert ctx.pushed_messages == []

        heartbeat = _event(
            seq=4,
            event_type="heartbeat",
            session_id=session_id,
            game_id=game_id,
            payload={
                "state_ts": "2026-04-21T08:30:04Z",
                "idle_seconds": 2,
            },
            ts="2026-04-21T08:30:04Z",
        )
        with events_path.open("ab") as handle:
            handle.write(
                json.dumps(heartbeat, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
        _write_session(
            game_dir / "session.json",
            _session(
                game_id=game_id,
                session_id=session_id,
                last_seq=4,
                started_at="2000-01-01T00:00:00Z",
                state=_session_state(
                    speaker="雪乃",
                    text="旧台词",
                    choices=old_choices,
                    line_id="line-old",
                    scene_id="scene-a",
                    is_menu_open=True,
                    ts="2000-01-01T00:00:03Z",
                ),
            ),
        )

        await plugin._poll_bridge(force=True)
        after_heartbeat = plugin._snapshot_state()
        heartbeat_status = await plugin.galgame_get_status()
        heartbeat_snapshot = await plugin.galgame_get_snapshot()
        assert after_heartbeat["latest_snapshot"] == {}
        assert after_heartbeat["history_lines"] == []
        assert after_heartbeat["history_observed_lines"] == []
        assert after_heartbeat["history_choices"] == []
        assert [event["seq"] for event in after_heartbeat["history_events"]] == [4]
        assert isinstance(heartbeat_status, Ok)
        assert isinstance(heartbeat_snapshot, Ok)
        assert heartbeat_status.value["effective_current_line"] == {}
        assert heartbeat_snapshot.value["effective_current_line"] == {}

        new_event = _event(
            seq=5,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            payload={
                "speaker": "雪乃",
                "text": "挂载后新增台词",
                "line_id": "line-new",
                "scene_id": "scene-a",
                "route_id": "",
            },
            ts="2026-04-21T08:30:05Z",
        )
        with events_path.open("ab") as handle:
            handle.write(
                json.dumps(new_event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
        _write_session(
            game_dir / "session.json",
            _session(
                game_id=game_id,
                session_id=session_id,
                last_seq=5,
                started_at="2000-01-01T00:00:00Z",
                state=_session_state(
                    speaker="雪乃",
                    text="挂载后新增台词",
                    line_id="line-new",
                    scene_id="scene-a",
                    ts="2026-04-21T08:30:05Z",
                ),
            ),
        )

        await plugin._poll_bridge(force=True)
        await plugin._poll_bridge(force=True)
        history_after_new_line = await plugin.galgame_get_history(limit=20, include_events=True)
        assert isinstance(history_after_new_line, Ok)
        assert [event["seq"] for event in history_after_new_line.value["events"]] == [4, 5]
        assert [line["line_id"] for line in history_after_new_line.value["stable_lines"]] == [
            "line-new"
        ]
        assert history_after_new_line.value["stable_lines"][0]["text"] == "挂载后新增台词"
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_preexisting_session_resumes_cursor_after_candidate_gap(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    session_id = "sess-a"
    game_id = "demo.alpha"
    old_line = _event(
        seq=1,
        event_type="line_changed",
        session_id=session_id,
        game_id=game_id,
        ts="2000-01-01T00:00:01Z",
        payload={
            "speaker": "Yukino",
            "text": "pre-start line",
            "line_id": "line-old",
            "scene_id": "scene-a",
            "route_id": "",
        },
    )
    game_dir = _create_game_dir(
        bridge_root,
        game_id=game_id,
        session_payload=_session(
            game_id=game_id,
            session_id=session_id,
            last_seq=1,
            started_at="2000-01-01T00:00:00Z",
            state=_session_state(
                text="pre-start line",
                line_id="line-old",
                scene_id="scene-a",
            ),
        ),
        events=[old_line],
    )
    session_path = game_dir / "session.json"
    events_path = game_dir / "events.jsonl"
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    await plugin.startup()
    try:
        await plugin._poll_bridge(force=True)
        first_new_line = _event(
            seq=2,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            ts="2026-04-21T08:35:02Z",
            payload={
                "speaker": "Yukino",
                "text": "line before candidate gap",
                "line_id": "line-before-gap",
                "scene_id": "scene-a",
                "route_id": "",
            },
        )
        _append_event(events_path, first_new_line)
        _write_session(
            session_path,
            _session(
                game_id=game_id,
                session_id=session_id,
                last_seq=2,
                started_at="2000-01-01T00:00:00Z",
                state=_session_state(
                    text="line before candidate gap",
                    line_id="line-before-gap",
                    scene_id="scene-a",
                ),
            ),
        )
        await plugin._poll_bridge(force=True)
        before_gap = plugin._snapshot_state()
        assert before_gap["last_seq"] == 2

        session_path.unlink()
        await plugin._poll_bridge(force=True)
        during_gap = plugin._snapshot_state()
        assert during_gap["active_session_id"] == ""
        assert during_gap["last_seq"] == 2

        gap_line = _event(
            seq=3,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            ts="2026-04-21T08:35:03Z",
            payload={
                "speaker": "Yukino",
                "text": "line during candidate gap",
                "line_id": "line-during-gap",
                "scene_id": "scene-a",
                "route_id": "",
            },
        )
        _append_event(events_path, gap_line)
        _write_session(
            session_path,
            _session(
                game_id=game_id,
                session_id=session_id,
                last_seq=3,
                started_at="2000-01-01T00:00:00Z",
                state=_session_state(
                    text="line during candidate gap",
                    line_id="line-during-gap",
                    scene_id="scene-a",
                ),
            ),
        )
        await plugin._poll_bridge(force=True)

        resumed = plugin._snapshot_state()
        history = await plugin.galgame_get_history(limit=20, include_events=True)
        assert resumed["active_session_id"] == session_id
        assert resumed["last_seq"] == 3
        assert isinstance(history, Ok)
        assert [event["seq"] for event in history.value["events"]] == [2, 3]
        assert [line["line_id"] for line in history.value["stable_lines"]] == [
            "line-before-gap",
            "line-during-gap",
        ]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_current_process_new_session_warmup_keeps_first_line(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    try:
        game_id = "demo.new"
        session_id = "sess-new"
        first_line = _event(
            seq=1,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            payload={
                "speaker": "雪乃",
                "text": "本次运行首句",
                "line_id": "line-first",
                "scene_id": "scene-new",
                "route_id": "",
            },
            ts="2099-01-01T00:00:01Z",
        )
        game_dir = _create_game_dir(
            bridge_root,
            game_id=game_id,
            session_payload=_session(
                game_id=game_id,
                session_id=session_id,
                last_seq=1,
                started_at="2099-01-01T00:00:00Z",
                state=_session_state(
                    speaker="雪乃",
                    text="本次运行首句",
                    line_id="line-first",
                    scene_id="scene-new",
                    ts="2099-01-01T00:00:01Z",
                ),
            ),
            events=[first_line],
        )
        events_before_poll = (game_dir / "events.jsonl").read_bytes()

        await plugin._poll_bridge(force=True)

        history = await plugin.galgame_get_history(limit=20, include_events=True)
        snapshot = await plugin.galgame_get_snapshot()
        assert isinstance(history, Ok)
        assert isinstance(snapshot, Ok)
        assert [event["seq"] for event in history.value["events"]] == [1]
        assert [line["line_id"] for line in history.value["stable_lines"]] == ["line-first"]
        assert snapshot.value["effective_current_line"]["text"] == "本次运行首句"
        assert (game_dir / "events.jsonl").read_bytes() == events_before_poll
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_current_process_ocr_warmup_ignores_other_session_events(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(plugin_dir, _make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    try:
        game_id = "ocr-demo.shared"
        session_id = "ocr-current"
        foreign_line = _event(
            seq=99,
            event_type="line_changed",
            session_id="ocr-previous",
            game_id=game_id,
            payload={
                "speaker": "旧角色",
                "text": "另一会话的旧台词",
                "line_id": "foreign-line",
                "scene_id": "foreign-scene",
                "route_id": "ocr",
            },
            ts="2000-01-01T00:00:01Z",
        )
        current_line = _event(
            seq=1,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            payload={
                "speaker": "雪乃",
                "text": "本次 OCR 首句",
                "line_id": "ocr-current-line",
                "scene_id": "ocr-current-scene",
                "route_id": "ocr",
            },
            ts="2099-01-01T00:00:01Z",
        )
        session_payload = _ocr_reader_session(
            game_id=game_id,
            session_id=session_id,
            last_seq=1,
            state=_session_state(
                speaker="雪乃",
                text="本次 OCR 首句",
                line_id="ocr-current-line",
                scene_id="ocr-current-scene",
                route_id="ocr",
                ts="2099-01-01T00:00:01Z",
            ),
        )
        session_payload["started_at"] = "2099-01-01T00:00:00Z"
        game_dir = _create_game_dir(
            bridge_root,
            game_id=game_id,
            session_payload=session_payload,
            events=[foreign_line, current_line],
        )
        events_before_poll = (game_dir / "events.jsonl").read_bytes()

        await plugin._poll_bridge(force=True)

        status = await plugin.galgame_get_status()
        history = await plugin.galgame_get_history(limit=20, include_events=True)
        snapshot = await plugin.galgame_get_snapshot()
        assert isinstance(status, Ok)
        assert isinstance(history, Ok)
        assert isinstance(snapshot, Ok)
        assert status.value["active_data_source"] == DATA_SOURCE_OCR_READER
        assert [event["seq"] for event in history.value["events"]] == [1]
        assert [line["line_id"] for line in history.value["stable_lines"]] == [
            "ocr-current-line"
        ]
        assert history.value["stable_lines"][0]["text"] == "本次 OCR 首句"
        assert snapshot.value["effective_current_line"]["text"] == "本次 OCR 首句"
        assert (game_dir / "events.jsonl").read_bytes() == events_before_poll
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_preexisting_empty_event_stream_processes_first_appended_line(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    game_id = "demo.empty"
    session_id = "sess-empty"
    game_dir = _create_game_dir(
        bridge_root,
        game_id=game_id,
        session_payload=_session(
            game_id=game_id,
            session_id=session_id,
            last_seq=0,
            started_at="2000-01-01T00:00:00Z",
            state=_session_state(
                speaker="雪乃",
                text="不应恢复的旧快照",
                line_id="old-snapshot-line",
                scene_id="scene-a",
                ts="2000-01-01T00:00:00Z",
            ),
        ),
        events=[],
    )
    events_path = game_dir / "events.jsonl"
    assert events_path.read_bytes() == b""

    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    await plugin.startup()
    try:
        await plugin._poll_bridge(force=True)
        mounted = plugin._snapshot_state()
        assert mounted["latest_snapshot"] == {}
        assert mounted["history_events"] == []
        assert mounted["events_byte_offset"] == 0
        assert mounted["events_file_size"] == 0
        assert mounted["last_seq"] == 0

        first_line = _event(
            seq=1,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            payload={
                "speaker": "雪乃",
                "text": "空文件挂载后的第一句",
                "line_id": "line-first",
                "scene_id": "scene-a",
                "route_id": "",
            },
            ts="2026-04-21T08:30:01Z",
        )
        with events_path.open("ab") as handle:
            handle.write(
                json.dumps(first_line, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
        _write_session(
            game_dir / "session.json",
            _session(
                game_id=game_id,
                session_id=session_id,
                last_seq=1,
                started_at="2000-01-01T00:00:00Z",
                state=_session_state(
                    speaker="雪乃",
                    text="空文件挂载后的第一句",
                    line_id="line-first",
                    scene_id="scene-a",
                    ts="2026-04-21T08:30:01Z",
                ),
            ),
        )

        await plugin._poll_bridge(force=True)

        after_first_line = plugin._snapshot_state()
        history = await plugin.galgame_get_history(limit=20, include_events=True)
        assert after_first_line["stream_reset_pending"] is False
        assert isinstance(history, Ok)
        assert [event["seq"] for event in history.value["events"]] == [1]
        assert [line["line_id"] for line in history.value["stable_lines"]] == ["line-first"]
        assert history.value["stable_lines"][0]["text"] == "空文件挂载后的第一句"
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
@pytest.mark.parametrize("event_type", ["line_changed", "choices_shown"])
async def test_preexisting_first_event_inherits_only_hidden_snapshot_identity(
    tmp_path: Path,
    event_type: str,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    game_id = "demo.hidden-baseline"
    session_id = "sess-hidden-baseline"
    old_state = _session_state(
        speaker="旧角色",
        text="不得暴露的启动前台词",
        choices=[{"choice_id": "old-choice", "text": "旧选项", "index": 0}],
        line_id="line-old",
        scene_id="scene-a",
        route_id="route-a",
        is_menu_open=True,
        ts="2000-01-01T00:00:00Z",
    )
    game_dir = _create_game_dir(
        bridge_root,
        game_id=game_id,
        session_payload=_session(
            game_id=game_id,
            session_id=session_id,
            last_seq=0,
            started_at="2000-01-01T00:00:00Z",
            state=old_state,
        ),
        events=[],
    )
    events_path = game_dir / "events.jsonl"
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    await plugin.startup()
    try:
        await plugin._poll_bridge(force=True)
        assert plugin._snapshot_state()["latest_snapshot"] == {}

        new_choices = [
            {
                "choice_id": "new-choice",
                "text": "新选项",
                "index": 0,
                "enabled": True,
            }
        ]
        payload = (
            {
                "speaker": "雪乃",
                "text": "启动后的第一句",
                "line_id": "line-new",
            }
            if event_type == "line_changed"
            else {"choices": new_choices}
        )
        first_event = _event(
            seq=1,
            event_type=event_type,
            session_id=session_id,
            game_id=game_id,
            payload=payload,
            ts="2026-04-21T08:30:01Z",
        )
        await asyncio.to_thread(_write_events, events_path, [first_event])
        current_state = (
            _session_state(
                speaker="雪乃",
                text="启动后的第一句",
                line_id="line-new",
                scene_id="scene-a",
                route_id="route-a",
                ts="2026-04-21T08:30:01Z",
            )
            if event_type == "line_changed"
            else _session_state(
                speaker="旧角色",
                text="不得暴露的启动前台词",
                choices=new_choices,
                line_id="line-old",
                scene_id="scene-a",
                route_id="route-a",
                is_menu_open=True,
                ts="2026-04-21T08:30:01Z",
            )
        )
        await asyncio.to_thread(
            _write_session,
            game_dir / "session.json",
            _session(
                game_id=game_id,
                session_id=session_id,
                last_seq=1,
                started_at="2000-01-01T00:00:00Z",
                state=current_state,
            ),
        )

        await plugin._poll_bridge(force=True)

        snapshot = plugin._snapshot_state()["latest_snapshot"]
        assert snapshot["scene_id"] == "scene-a"
        assert snapshot["route_id"] == "route-a"
        assert snapshot["text"] != "不得暴露的启动前台词"
        if event_type == "line_changed":
            assert snapshot["text"] == "启动后的第一句"
            assert snapshot["line_id"] == "line-new"
            assert snapshot["choices"] == []
        else:
            assert snapshot["text"] == ""
            assert snapshot["line_id"] == "line-old"
            assert snapshot["choices"] == new_choices
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
@pytest.mark.parametrize("started_at", ["2000-01-01T00:00:00Z", "invalid"])
async def test_session_appearing_after_empty_startup_scan_still_baselines_old_data(
    tmp_path: Path,
    started_at: str,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    await plugin.startup()
    try:
        await plugin._poll_bridge(force=True)
        assert plugin._startup_existing_session_ids == set()

        game_id = "demo.late-old"
        session_id = "sess-late-old"
        old_choices = [
            {
                "choice_id": "old-line#choice0",
                "text": "旧选项",
                "index": 0,
                "enabled": True,
            }
        ]
        old_line = _event(
            seq=1,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            payload={
                "speaker": "雪乃",
                "text": "启动扫描后才出现的旧台词",
                "line_id": "old-line",
                "scene_id": "old-scene",
                "route_id": "",
            },
            ts="2000-01-01T00:00:01Z",
        )
        old_choice_event = _event(
            seq=2,
            event_type="choices_shown",
            session_id=session_id,
            game_id=game_id,
            payload={
                "line_id": "old-line",
                "scene_id": "old-scene",
                "route_id": "",
                "choices": old_choices,
            },
            ts="2000-01-01T00:00:02Z",
        )
        game_dir = _create_game_dir(
            bridge_root,
            game_id=game_id,
            session_payload=_session(
                game_id=game_id,
                session_id=session_id,
                last_seq=2,
                started_at=started_at,
                state=_session_state(
                    speaker="雪乃",
                    text="启动扫描后才出现的旧台词",
                    choices=old_choices,
                    line_id="old-line",
                    scene_id="old-scene",
                    is_menu_open=True,
                    ts="2000-01-01T00:00:02Z",
                ),
            ),
            events=[old_line, old_choice_event],
        )
        events_before_poll = (game_dir / "events.jsonl").read_bytes()

        await plugin._poll_bridge(force=True)

        mounted = plugin._snapshot_state()
        status = await plugin.galgame_get_status()
        snapshot = await plugin.galgame_get_snapshot()
        history = await plugin.galgame_get_history(limit=20, include_events=True)
        assert mounted["active_session_id"] == session_id
        assert mounted["latest_snapshot"] == {}
        assert mounted["history_events"] == []
        assert mounted["history_lines"] == []
        assert mounted["history_observed_lines"] == []
        assert mounted["history_choices"] == []
        assert mounted["events_byte_offset"] == len(events_before_poll)
        assert mounted["last_seq"] == 2
        assert isinstance(status, Ok)
        assert isinstance(snapshot, Ok)
        assert isinstance(history, Ok)
        assert status.value["effective_current_line"] == {}
        assert snapshot.value["effective_current_line"] == {}
        assert history.value["stable_lines"] == []
        assert history.value["choices"] == []
        assert (game_dir / "events.jsonl").read_bytes() == events_before_poll
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_late_discovered_preexisting_session_resumes_after_candidate_gap(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    await plugin.startup()
    try:
        await plugin._poll_bridge(force=True)
        assert plugin._startup_existing_session_ids == set()
        session_id = "late-preexisting"
        game_id = "demo.alpha"
        game_dir = _create_game_dir(
            bridge_root,
            game_id=game_id,
            session_payload=_session(
                game_id=game_id,
                session_id=session_id,
                last_seq=1,
                started_at="2000-01-01T00:00:00Z",
                state=_session_state(
                    text="old line",
                    line_id="line-old",
                    scene_id="scene-a",
                ),
            ),
            events=[
                _event(
                    seq=1,
                    event_type="line_changed",
                    session_id=session_id,
                    game_id=game_id,
                    ts="2000-01-01T00:00:01Z",
                    payload={
                        "text": "old line",
                        "line_id": "line-old",
                        "scene_id": "scene-a",
                    },
                )
            ],
        )
        session_path = game_dir / "session.json"
        events_path = game_dir / "events.jsonl"

        await plugin._poll_bridge(force=True)
        _append_event(
            events_path,
            _event(
                seq=2,
                event_type="line_changed",
                session_id=session_id,
                game_id=game_id,
                ts="2026-04-21T08:35:02Z",
                payload={
                    "text": "before gap",
                    "line_id": "line-before-gap",
                    "scene_id": "scene-a",
                },
            ),
        )
        _write_session(
            session_path,
            _session(
                game_id=game_id,
                session_id=session_id,
                last_seq=2,
                started_at="2000-01-01T00:00:00Z",
                state=_session_state(
                    text="before gap",
                    line_id="line-before-gap",
                    scene_id="scene-a",
                ),
            ),
        )
        await plugin._poll_bridge(force=True)

        session_path.unlink()
        await plugin._poll_bridge(force=True)
        assert plugin._snapshot_state()["last_seq"] == 2

        _append_event(
            events_path,
            _event(
                seq=3,
                event_type="line_changed",
                session_id=session_id,
                game_id=game_id,
                ts="2026-04-21T08:35:03Z",
                payload={
                    "text": "during gap",
                    "line_id": "line-during-gap",
                    "scene_id": "scene-a",
                },
            ),
        )
        _write_session(
            session_path,
            _session(
                game_id=game_id,
                session_id=session_id,
                last_seq=3,
                started_at="2000-01-01T00:00:00Z",
                state=_session_state(
                    text="during gap",
                    line_id="line-during-gap",
                    scene_id="scene-a",
                ),
            ),
        )
        await plugin._poll_bridge(force=True)

        history = await plugin.galgame_get_history(limit=20, include_events=True)
        assert isinstance(history, Ok)
        assert [event["seq"] for event in history.value["events"]] == [2, 3]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_preexisting_boundary_keeps_event_appended_after_candidate_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    await plugin.startup()
    try:
        await plugin._poll_bridge(force=True)
        game_id = "demo.boundary-race"
        session_id = "sess-boundary-race"
        old_line = _event(
            seq=1,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            payload={
                "speaker": "雪乃",
                "text": "候选快照内的旧台词",
                "line_id": "line-old",
                "scene_id": "scene-a",
                "route_id": "",
            },
            ts="2000-01-01T00:00:01Z",
        )
        new_line = _event(
            seq=2,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            payload={
                "speaker": "雪乃",
                "text": "候选读取后追加的新台词",
                "line_id": "line-new",
                "scene_id": "scene-a",
                "route_id": "",
            },
            ts="2026-04-21T08:30:02Z",
        )
        game_dir = _create_game_dir(
            bridge_root,
            game_id=game_id,
            session_payload=_session(
                game_id=game_id,
                session_id=session_id,
                last_seq=1,
                started_at="2000-01-01T00:00:00Z",
                state=_session_state(
                    speaker="雪乃",
                    text="候选快照内的旧台词",
                    line_id="line-old",
                    scene_id="scene-a",
                    ts="2000-01-01T00:00:01Z",
                ),
            ),
            events=[old_line],
        )
        events_path = game_dir / "events.jsonl"
        boundary_calls = 0

        def _append_during_boundary(path: Path, **kwargs: object) -> EventStreamBoundary:
            nonlocal boundary_calls
            boundary_calls += 1
            if boundary_calls == 1:
                _append_event(events_path, new_line)
            return read_events_boundary(path, **kwargs)

        monkeypatch.setattr(
            "plugin.plugins.galgame_plugin.plugin_core.snapshot_events_boundary",
            _append_during_boundary,
        )

        await plugin._poll_bridge(force=True)

        mounted = plugin._snapshot_state()
        history = await plugin.galgame_get_history(limit=20, include_events=True)
        assert boundary_calls == 1
        assert mounted["last_seq"] == 2
        assert mounted["latest_snapshot"]["line_id"] == "line-new"
        assert mounted["events_byte_offset"] == events_path.stat().st_size
        assert isinstance(history, Ok)
        assert [event["seq"] for event in history.value["events"]] == [2]
        assert [line["line_id"] for line in history.value["stable_lines"]] == [
            "line-new"
        ]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_preexisting_boundary_uses_seq_high_water_from_captured_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    await plugin.startup()
    try:
        await plugin._poll_bridge(force=True)
        game_id = "demo.checkpoint-ahead"
        session_id = "sess-checkpoint-ahead"
        old_line = _event(
            seq=1,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            payload={"text": "old line", "line_id": "line-old", "scene_id": "scene-a"},
            ts="2000-01-01T00:00:01Z",
        )
        new_line = _event(
            seq=2,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            payload={"text": "checkpoint ahead line", "line_id": "line-new", "scene_id": "scene-a"},
            ts="2026-04-21T08:30:02Z",
        )
        game_dir = _create_game_dir(
            bridge_root,
            game_id=game_id,
            session_payload=_session(
                game_id=game_id,
                session_id=session_id,
                last_seq=2,
                started_at="2000-01-01T00:00:00Z",
                state=_session_state(
                    text="checkpoint ahead line",
                    line_id="line-new",
                    scene_id="scene-a",
                ),
            ),
            events=[old_line],
        )
        events_path = game_dir / "events.jsonl"
        captured_size = events_path.stat().st_size
        _append_event(events_path, new_line)

        def _captured_boundary(path: Path, **kwargs: object) -> EventStreamBoundary:
            return read_events_boundary(
                path,
                **{**kwargs, "snapshot_file_size": captured_size},
            )

        monkeypatch.setattr(
            "plugin.plugins.galgame_plugin.plugin_core.snapshot_events_boundary",
            _captured_boundary,
        )

        await plugin._poll_bridge(force=True)

        mounted = plugin._snapshot_state()
        assert mounted["last_seq"] == 2
        assert mounted["latest_snapshot"]["line_id"] == "line-new"
        assert [event["seq"] for event in mounted["history_events"]] == [2]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_first_attachment_validates_boundary_checkpoint_before_tailing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    await plugin.startup()
    try:
        await plugin._poll_bridge(force=True)
        game_id = "demo.attachment-rewrite"
        session_id = "sess-attachment-rewrite"
        old_line = _event(
            seq=1,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            payload={"text": "old attachment line", "line_id": "line-old", "scene_id": "scene-a"},
            ts="2000-01-01T00:00:01Z",
        )
        game_dir = _create_game_dir(
            bridge_root,
            game_id=game_id,
            session_payload=_session(
                game_id=game_id,
                session_id=session_id,
                last_seq=1,
                started_at="2000-01-01T00:00:00Z",
                state=_session_state(text="old attachment line", line_id="line-old"),
            ),
            events=[old_line],
        )
        events_path = game_dir / "events.jsonl"
        old_size = events_path.stat().st_size
        replacement = _event(
            seq=1,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            payload={
                "text": "replacement " + ("x" * old_size),
                "line_id": "line-replacement",
                "scene_id": "scene-a",
            },
            ts="2026-04-21T08:31:01Z",
        )
        real_tail = tail_events_jsonl
        tail_calls = 0

        def _rewrite_before_first_tail(path: Path, **kwargs: object):
            nonlocal tail_calls
            tail_calls += 1
            if tail_calls == 1:
                _write_events(events_path, [replacement])
                _write_session(
                    game_dir / "session.json",
                    _session(
                        game_id=game_id,
                        session_id=session_id,
                        last_seq=1,
                        started_at="2000-01-01T00:00:00Z",
                        state=_session_state(
                            text=str(replacement["payload"]["text"]),
                            line_id="line-replacement",
                            scene_id="scene-a",
                        ),
                    ),
                )
                assert events_path.stat().st_size >= old_size
            return real_tail(path, **kwargs)

        monkeypatch.setattr(
            "plugin.plugins.galgame_plugin.plugin_core.tail_events_jsonl",
            _rewrite_before_first_tail,
        )

        await plugin._poll_bridge(force=True)
        resetting = plugin._snapshot_state()
        assert resetting["stream_reset_pending"] is True
        assert resetting["history_events"] == []

        await plugin._poll_bridge(force=True)
        recovered = plugin._snapshot_state()
        assert recovered["stream_reset_pending"] is False
        assert recovered["latest_snapshot"]["line_id"] == "line-replacement"
        assert [event["seq"] for event in recovered["history_events"]] == [1]
    finally:
        await plugin.shutdown()


@pytest.mark.plugin_unit
def test_positive_checkpoint_scan_stops_at_candidate_snapshot(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    game_id = "demo.positive-checkpoint"
    session_id = "sess-positive-checkpoint"
    _append_event(
        events_path,
        _event(
            seq=1,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            payload={"text": "candidate snapshot", "line_id": "line-1"},
            ts="2026-04-21T08:30:01Z",
        ),
    )
    snapshot_file_size = events_path.stat().st_size
    for seq in (2, 3):
        _append_event(
            events_path,
            _event(
                seq=seq,
                event_type="line_changed",
                session_id=session_id,
                game_id=game_id,
                payload={"text": f"racing event {seq}", "line_id": f"line-{seq}"},
                ts=f"2026-04-21T08:30:0{seq}Z",
            ),
        )

    boundary = read_events_boundary(
        events_path,
        session_id=session_id,
        last_seq=1,
        events_limit=1,
        snapshot_file_size=snapshot_file_size,
    )

    assert boundary.offset == snapshot_file_size
    assert boundary.file_size == events_path.stat().st_size
    tail = tail_events_jsonl(
        events_path,
        offset=boundary.offset,
        line_buffer=b"",
    )
    assert [int(event.get("seq") or 0) for event in tail.events] == [2, 3]


@pytest.mark.plugin_unit
def test_candidate_scan_captures_events_boundary_before_session_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    game_id = "demo.candidate-snapshot-race"
    session_id = "sess-candidate-snapshot-race"
    initial_event = _event(
        seq=1,
        event_type="line_changed",
        session_id=session_id,
        game_id=game_id,
        payload={"text": "candidate snapshot", "line_id": "line-1"},
        ts="2026-04-21T08:30:01Z",
    )
    game_dir = _create_game_dir(
        bridge_root,
        game_id=game_id,
        session_payload=_session(
            game_id=game_id,
            session_id=session_id,
            last_seq=1,
            started_at="2000-01-01T00:00:00Z",
            state=_session_state(text="candidate snapshot", line_id="line-1"),
        ),
        events=[initial_event],
    )
    events_path = game_dir / "events.jsonl"
    initial_file_size = events_path.stat().st_size
    original_read_session_json = galgame_service.read_session_json

    def _read_session_after_racing_events(path: Path):
        result = original_read_session_json(path)
        for seq in (2, 3):
            _append_event(
                events_path,
                _event(
                    seq=seq,
                    event_type="line_changed",
                    session_id=session_id,
                    game_id=game_id,
                    payload={
                        "text": f"racing event {seq}",
                        "line_id": f"line-{seq}",
                    },
                    ts=f"2026-04-21T08:30:0{seq}Z",
                ),
            )
        return result

    monkeypatch.setattr(
        galgame_service,
        "read_session_json",
        _read_session_after_racing_events,
    )

    _game_ids, candidates, warnings = galgame_service.scan_session_candidates(
        bridge_root
    )

    assert warnings == []
    candidate = candidates[game_id]
    assert candidate.events_file_size == initial_file_size
    boundary = read_events_boundary(
        candidate.events_path,
        session_id=session_id,
        last_seq=1,
        events_limit=1,
        snapshot_file_size=candidate.events_file_size,
    )
    tail = tail_events_jsonl(
        candidate.events_path,
        offset=boundary.offset,
        line_buffer=b"",
    )
    assert [int(event.get("seq") or 0) for event in tail.events] == [2, 3]


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_startup_session_id_normalization_preserves_preexisting_boundary(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    game_id = "demo.spaced-session"
    raw_session_id = "  sess-spaced  "
    old_line = _event(
        seq=1,
        event_type="line_changed",
        session_id=raw_session_id,
        game_id=game_id,
        payload={
            "speaker": "雪乃",
            "text": "启动前旧台词",
            "line_id": "line-old",
            "scene_id": "scene-a",
            "route_id": "",
        },
        ts="2000-01-01T00:00:01Z",
    )
    game_dir = _create_game_dir(
        bridge_root,
        game_id=game_id,
        session_payload=_session(
            game_id=game_id,
            session_id=raw_session_id,
            last_seq=1,
            started_at="2000-01-01T00:00:00Z",
            state=_session_state(
                text="启动前旧台词",
                line_id="line-old",
                scene_id="scene-a",
            ),
        ),
        events=[old_line],
    )
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    await plugin.startup()
    try:
        await plugin._poll_bridge(force=True)
        assert plugin._startup_existing_session_ids == {
            ("bridge_sdk", game_id, "sess-spaced")
        }
        assert plugin._snapshot_state()["latest_snapshot"] == {}

        _write_session(
            game_dir / "session.json",
            _session(
                game_id=game_id,
                session_id=raw_session_id,
                last_seq=1,
                started_at="2999-01-01T00:00:00Z",
                state=_session_state(
                    text="不应因时间戳变化越过启动边界",
                    line_id="line-future",
                    scene_id="scene-a",
                ),
            ),
        )

        await plugin._poll_bridge(force=True)

        assert plugin._snapshot_state()["latest_snapshot"] == {}
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_boundary_retry_discards_stale_checkpoint_before_processing_new_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    game_id = "demo.boundary-retry"
    session_id = "sess-boundary-retry"
    game_dir = _create_game_dir(
        bridge_root,
        game_id=game_id,
        session_payload=_session(
            game_id=game_id,
            session_id=session_id,
            last_seq=3,
            started_at="2000-01-01T00:00:00Z",
            state=_session_state(
                speaker="雪乃",
                text="边界建立前的旧台词",
                line_id="line-old",
                scene_id="scene-a",
                ts="2000-01-01T00:00:03Z",
            ),
        ),
        events=[
            _event(
                seq=3,
                event_type="line_changed",
                session_id=session_id,
                game_id=game_id,
                payload={
                    "speaker": "雪乃",
                    "text": "边界建立前的旧台词",
                    "line_id": "line-old",
                    "scene_id": "scene-a",
                    "route_id": "",
                },
                ts="2000-01-01T00:00:03Z",
            )
        ],
    )
    events_path = game_dir / "events.jsonl"
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    plugin._persist.persist_runtime(
        session_id=session_id,
        events_byte_offset=0,
        events_file_size=0,
        last_seq=100,
        dedupe_window=[],
        last_error={},
    )
    boundary_calls = 0

    def _flaky_boundary(path: Path, **kwargs: object) -> EventStreamBoundary:
        nonlocal boundary_calls
        boundary_calls += 1
        if boundary_calls == 1:
            return EventStreamBoundary(error="simulated boundary read failure")
        return read_events_boundary(path, **kwargs)

    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.plugin_core.snapshot_events_boundary",
        _flaky_boundary,
    )
    await plugin.startup()
    try:
        await plugin._poll_bridge(force=True)
        after_failure = plugin._snapshot_state()
        assert boundary_calls == 1
        assert after_failure["warmup_session_id"] == ""
        assert after_failure["last_seq"] == 100

        await plugin._poll_bridge(force=True)
        after_recovery = plugin._snapshot_state()
        assert boundary_calls == 2
        assert after_recovery["warmup_session_id"] == session_id
        assert after_recovery["history_lines"] == []
        assert after_recovery["last_seq"] == 3

        new_line = _event(
            seq=4,
            event_type="line_changed",
            session_id=session_id,
            game_id=game_id,
            payload={
                "speaker": "雪乃",
                "text": "边界恢复后的新台词",
                "line_id": "line-new",
                "scene_id": "scene-a",
                "route_id": "",
            },
            ts="2026-04-21T08:30:04Z",
        )
        with events_path.open("ab") as handle:
            handle.write(
                json.dumps(new_line, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
        _write_session(
            game_dir / "session.json",
            _session(
                game_id=game_id,
                session_id=session_id,
                last_seq=4,
                started_at="2000-01-01T00:00:00Z",
                state=_session_state(
                    speaker="雪乃",
                    text="边界恢复后的新台词",
                    line_id="line-new",
                    scene_id="scene-a",
                    ts="2026-04-21T08:30:04Z",
                ),
            ),
        )

        await plugin._poll_bridge(force=True)

        history = await plugin.galgame_get_history(limit=20, include_events=True)
        assert isinstance(history, Ok)
        assert [event["seq"] for event in history.value["events"]] == [4]
        assert [line["line_id"] for line in history.value["stable_lines"]] == ["line-new"]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
@pytest.mark.parametrize("replacement_prefix", [b"", b'{"seq":1'])
async def test_truncation_sets_stream_reset_pending(
    tmp_path: Path,
    replacement_prefix: bytes,
) -> None:
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
    await plugin._poll_bridge(force=True)
    active_line = _event(
        seq=3,
        event_type="line_changed",
        session_id=session_id,
        game_id=game_id,
        payload={
            "speaker": "雪乃",
            "text": "活跃流台词",
            "line_id": "line-active",
            "scene_id": "scene-a",
            "route_id": "",
        },
        ts="2026-04-21T08:30:03Z",
    )
    _append_event(game_dir / "events.jsonl", active_line)
    _write_session(
        game_dir / "session.json",
        _session(
            game_id=game_id,
            session_id=session_id,
            last_seq=3,
            state=_session_state(
                speaker="雪乃",
                text="活跃流台词",
                line_id="line-active",
                scene_id="scene-a",
                ts="2026-04-21T08:30:03Z",
            ),
        ),
    )
    await plugin._poll_bridge(force=True)
    before_reset = plugin._snapshot_state()
    assert before_reset["latest_snapshot"]
    assert before_reset["history_events"]
    assert before_reset["history_lines"]

    (game_dir / "events.jsonl").write_bytes(replacement_prefix)
    await plugin._poll_bridge(force=True)
    status = await plugin.galgame_get_status()
    assert isinstance(status, Ok)
    assert status.value["stream_reset_pending"] is True
    resetting = plugin._snapshot_state()
    assert resetting["active_session_meta"]["stream_generation"] == 1
    assert resetting["latest_snapshot"] == {}
    assert resetting["history_events"] == []
    assert resetting["history_lines"] == []
    assert resetting["history_observed_lines"] == []
    assert resetting["history_choices"] == []
    assert resetting["dedupe_window"] == []
    assert resetting["events_byte_offset"] == 0

    await plugin._poll_bridge(force=True)
    still_resetting = plugin._snapshot_state()
    assert still_resetting["stream_reset_pending"] is True
    assert still_resetting["active_session_meta"]["stream_generation"] == 1

    replacement = _event(
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
        ts="2026-04-21T08:31:01Z",
    )
    _write_events(game_dir / "events.jsonl", [replacement])
    _write_session(
        game_dir / "session.json",
        _session(
            game_id=game_id,
            session_id=session_id,
            last_seq=1,
            state=_session_state(
                speaker="雪乃",
                text="旧台词",
                line_id="line-1",
                scene_id="scene-a",
                ts="2026-04-21T08:31:01Z",
            ),
        ),
    )

    await plugin._poll_bridge(force=True)

    recovered = plugin._snapshot_state()
    assert recovered["stream_reset_pending"] is False
    assert recovered["active_session_meta"]["stream_generation"] == 1
    assert [event["seq"] for event in recovered["history_events"]] == [1]

    await plugin._poll_bridge(force=True)
    assert plugin._snapshot_state()["active_session_meta"]["stream_generation"] == 1


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_empty_partial_stream_reset_rearms_same_session_generation(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    game_id = "demo.partial-reset"
    session_id = "sess-partial-reset"
    game_dir = _create_game_dir(
        bridge_root,
        game_id=game_id,
        session_payload=_session(
            game_id=game_id,
            session_id=session_id,
            last_seq=0,
            started_at="2000-01-01T00:00:00Z",
            state=_session_state(text="", line_id=""),
        ),
        events=[],
    )
    events_path = game_dir / "events.jsonl"
    first_partial = b'{"session_id":"sess-partial-reset","seq":1'
    events_path.write_bytes(first_partial)
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    await plugin.startup()
    try:
        await plugin._poll_bridge(force=True)
        assert plugin._snapshot_state()["events_byte_offset"] == len(first_partial)

        events_path.write_bytes(b"")
        await plugin._poll_bridge(force=True)
        assert plugin._snapshot_state()["active_session_meta"]["stream_generation"] == 1
        await plugin._poll_bridge(force=True)
        assert plugin._snapshot_state()["stream_reset_pending"] is False

        second_partial = b'{"session_id":"sess-partial-reset","seq":1,"type"'
        events_path.write_bytes(second_partial)
        await plugin._poll_bridge(force=True)
        assert plugin._snapshot_state()["events_byte_offset"] == len(second_partial)

        events_path.write_bytes(b"")
        await plugin._poll_bridge(force=True)
        resetting_again = plugin._snapshot_state()
        assert resetting_again["stream_reset_pending"] is True
        assert resetting_again["active_session_meta"]["stream_generation"] == 2
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_same_session_rewrite_detected_after_file_size_catches_up(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    game_id = "demo.rewritten"
    session_id = "sess-rewritten"
    game_dir = _create_game_dir(
        bridge_root,
        game_id=game_id,
        session_payload=_session(
            game_id=game_id,
            session_id=session_id,
            last_seq=1,
            state=_session_state(text="startup line", line_id="line-1"),
        ),
        events=[
            _event(
                seq=1,
                event_type="line_changed",
                session_id=session_id,
                game_id=game_id,
                payload={"text": "startup line", "line_id": "line-1"},
                ts="2026-04-21T08:30:01Z",
            )
        ],
    )
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    await plugin.startup()
    await plugin._poll_bridge(force=True)
    active_event = _event(
        seq=2,
        event_type="line_changed",
        session_id=session_id,
        game_id=game_id,
        payload={"text": "active line", "line_id": "line-2"},
        ts="2026-04-21T08:30:02Z",
    )
    _append_event(game_dir / "events.jsonl", active_event)
    _write_session(
        game_dir / "session.json",
        _session(
            game_id=game_id,
            session_id=session_id,
            last_seq=2,
            state=_session_state(text="active line", line_id="line-2"),
        ),
    )
    await plugin._poll_bridge(force=True)
    old_offset = int(plugin._snapshot_state()["events_byte_offset"])

    replacement = _event(
        seq=1,
        event_type="line_changed",
        session_id=session_id,
        game_id=game_id,
        payload={
            "text": "replacement line " + ("x" * old_offset),
            "line_id": "replacement-1",
        },
        ts="2026-04-21T08:31:01Z",
    )
    _write_events(game_dir / "events.jsonl", [replacement])
    assert (game_dir / "events.jsonl").stat().st_size >= old_offset
    _write_session(
        game_dir / "session.json",
        _session(
            game_id=game_id,
            session_id=session_id,
            last_seq=1,
            state=_session_state(
                text=str(replacement["payload"]["text"]),
                line_id="replacement-1",
            ),
        ),
    )

    await plugin._poll_bridge(force=True)
    resetting = plugin._snapshot_state()
    assert resetting["stream_reset_pending"] is True
    assert resetting["active_session_meta"]["stream_generation"] == 1
    assert resetting["latest_snapshot"] == {}
    assert resetting["history_events"] == []

    await plugin._poll_bridge(force=True)
    recovered = plugin._snapshot_state()
    assert recovered["stream_reset_pending"] is False
    assert recovered["active_session_meta"]["stream_generation"] == 1
    assert [event["seq"] for event in recovered["history_events"]] == [1]


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
    await plugin._poll_bridge(force=True)

    with plugin._state_lock:
        plugin._state.last_seen_data_monotonic = time.monotonic() - 5.0

    await plugin._poll_bridge(force=True)
    stale_status = await plugin.galgame_get_status()
    assert isinstance(stale_status, Ok)
    assert stale_status.value["connection_state"] == "stale"

    _append_event(
        game_dir / "events.jsonl",
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
    assert "算了，没事。" not in context["scene_summary_seed"]
    assert "暂时没有足够台词上下文" in context["scene_summary_seed"]


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


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_preexisting_session_resumes_saved_cursor_after_reattach(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    alpha_old = _event(
        seq=1,
        event_type="line_changed",
        session_id="sess-alpha",
        game_id="demo.alpha",
        ts="2000-01-01T00:00:01Z",
        payload={
            "speaker": "Yukino",
            "text": "alpha pre-start line",
            "line_id": "alpha-old",
            "scene_id": "scene-alpha",
            "route_id": "",
        },
    )
    beta_old = _event(
        seq=1,
        event_type="line_changed",
        session_id="sess-beta",
        game_id="demo.beta",
        ts="2000-01-01T00:00:01Z",
        payload={
            "speaker": "Yukino",
            "text": "beta pre-start line",
            "line_id": "beta-old",
            "scene_id": "scene-beta",
            "route_id": "",
        },
    )
    alpha_dir = _create_game_dir(
        bridge_root,
        game_id="demo.alpha",
        session_payload=_session(
            game_id="demo.alpha",
            session_id="sess-alpha",
            last_seq=1,
            started_at="2000-01-01T00:00:00Z",
            state=_session_state(
                text="alpha pre-start line",
                line_id="alpha-old",
                scene_id="scene-alpha",
            ),
        ),
        events=[alpha_old],
    )
    _create_game_dir(
        bridge_root,
        game_id="demo.beta",
        session_payload=_session(
            game_id="demo.beta",
            session_id="sess-beta",
            last_seq=1,
            started_at="2000-01-01T00:00:00Z",
            state=_session_state(
                text="beta pre-start line",
                line_id="beta-old",
                scene_id="scene-beta",
            ),
        ),
        events=[beta_old],
    )

    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    await plugin.startup()
    try:
        await plugin._poll_bridge(force=True)
        assert isinstance(await plugin.galgame_bind_game(game_id="demo.alpha"), Ok)
        await plugin._poll_bridge(force=True)
        alpha_boundary = plugin._snapshot_state()["events_byte_offset"]

        assert isinstance(await plugin.galgame_bind_game(game_id="demo.beta"), Ok)
        await plugin._poll_bridge(force=True)

        alpha_new = _event(
            seq=2,
            event_type="line_changed",
            session_id="sess-alpha",
            game_id="demo.alpha",
            ts="2026-04-21T08:35:02Z",
            payload={
                "speaker": "Yukino",
                "text": "alpha line while inactive",
                "line_id": "alpha-new",
                "scene_id": "scene-alpha",
                "route_id": "",
            },
        )
        _append_event(alpha_dir / "events.jsonl", alpha_new)
        _write_session(
            alpha_dir / "session.json",
            _session(
                game_id="demo.alpha",
                session_id="sess-alpha",
                last_seq=2,
                started_at="2000-01-01T00:00:00Z",
                state=_session_state(
                    text="alpha line while inactive",
                    line_id="alpha-new",
                    scene_id="scene-alpha",
                    ts="2026-04-21T08:35:02Z",
                ),
            ),
        )

        assert isinstance(await plugin.galgame_bind_game(game_id="demo.alpha"), Ok)
        await plugin._poll_bridge(force=True)

        resumed = plugin._snapshot_state()
        history = await plugin.galgame_get_history(limit=20, include_events=True)
        assert resumed["events_byte_offset"] > alpha_boundary
        assert resumed["active_session_id"] == "sess-alpha"
        assert resumed["last_seq"] == 2
        assert isinstance(history, Ok)
        assert [event["seq"] for event in history.value["events"]] == [2]
        assert [line["line_id"] for line in history.value["stable_lines"]] == [
            "alpha-new"
        ]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_preexisting_reattach_validates_saved_checkpoint_in_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    alpha_dir = _create_game_dir(
        bridge_root,
        game_id="demo.alpha",
        session_payload=_session(
            game_id="demo.alpha",
            session_id="sess-alpha",
            last_seq=1,
            started_at="2000-01-01T00:00:00Z",
            state=_session_state(
                text="alpha cached line",
                line_id="alpha-old",
                scene_id="scene-alpha",
            ),
        ),
        events=[
            _event(
                seq=1,
                event_type="line_changed",
                session_id="sess-alpha",
                game_id="demo.alpha",
                ts="2000-01-01T00:00:01Z",
                payload={
                    "speaker": "Yukino",
                    "text": "alpha cached line",
                    "line_id": "alpha-old",
                    "scene_id": "scene-alpha",
                    "route_id": "",
                },
            )
        ],
    )
    _create_game_dir(
        bridge_root,
        game_id="demo.beta",
        session_payload=_session(
            game_id="demo.beta",
            session_id="sess-beta",
            last_seq=1,
            started_at="2000-01-01T00:00:00Z",
            state=_session_state(
                text="beta cached line",
                line_id="beta-old",
                scene_id="scene-beta",
            ),
        ),
        events=[
            _event(
                seq=1,
                event_type="line_changed",
                session_id="sess-beta",
                game_id="demo.beta",
                ts="2000-01-01T00:00:01Z",
                payload={
                    "speaker": "Yukino",
                    "text": "beta cached line",
                    "line_id": "beta-old",
                    "scene_id": "scene-beta",
                    "route_id": "",
                },
            )
        ],
    )

    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    await plugin.startup()
    try:
        await plugin._poll_bridge(force=True)
        assert isinstance(await plugin.galgame_bind_game(game_id="demo.alpha"), Ok)
        await plugin._poll_bridge(force=True)
        saved_offset = int(plugin._snapshot_state()["events_byte_offset"])

        assert isinstance(await plugin.galgame_bind_game(game_id="demo.beta"), Ok)
        await plugin._poll_bridge(force=True)

        events_path = alpha_dir / "events.jsonl"
        replacement = _event(
            seq=1,
            event_type="line_changed",
            session_id="sess-alpha",
            game_id="demo.alpha",
            ts="2026-04-21T08:36:01Z",
            payload={
                "speaker": "Yukino",
                "text": "replacement after checkpoint " + ("x" * saved_offset),
                "line_id": "alpha-replacement",
                "scene_id": "scene-alpha",
                "route_id": "",
            },
        )
        rewrote_stream = False
        alpha_tail_checkpoints: list[str] = []
        alpha_tail_reset_results: list[bool] = []
        standalone_checkpoints: list[str] = []
        real_tail = tail_events_jsonl

        def _rewrite_after_saved_checkpoint(path: Path, *, offset: int) -> str:
            nonlocal rewrote_stream
            checkpoint = read_events_checkpoint(path, offset=offset)
            if path == events_path and offset == saved_offset and not rewrote_stream:
                standalone_checkpoints.append(checkpoint)
                rewrote_stream = True
                _write_events(events_path, [replacement])
                _write_session(
                    alpha_dir / "session.json",
                    _session(
                        game_id="demo.alpha",
                        session_id="sess-alpha",
                        last_seq=1,
                        started_at="2000-01-01T00:00:00Z",
                        state=_session_state(
                            text=str(replacement["payload"]["text"]),
                            line_id="alpha-replacement",
                            scene_id="scene-alpha",
                            ts="2026-04-21T08:36:01Z",
                        ),
                    ),
                )
                assert events_path.stat().st_size >= saved_offset
                assert read_events_checkpoint(
                    events_path,
                    offset=saved_offset,
                ) != checkpoint
            return checkpoint

        def _capture_alpha_tail_checkpoint(path: Path, **kwargs: object):
            if path == events_path:
                alpha_tail_checkpoints.append(
                    str(kwargs.get("expected_checkpoint") or "")
                )
            result = real_tail(path, **kwargs)
            if path == events_path:
                alpha_tail_reset_results.append(bool(result.reset_detected))
            return result

        monkeypatch.setattr(
            "plugin.plugins.galgame_plugin.plugin_core.read_stream_checkpoint",
            _rewrite_after_saved_checkpoint,
        )
        monkeypatch.setattr(
            "plugin.plugins.galgame_plugin.plugin_core.tail_events_jsonl",
            _capture_alpha_tail_checkpoint,
        )

        assert isinstance(await plugin.galgame_bind_game(game_id="demo.alpha"), Ok)
        resetting = plugin._snapshot_state()
        assert resetting["stream_reset_pending"] is True
        assert resetting["latest_snapshot"] == {}
        assert resetting["history_events"] == []
        assert resetting["history_lines"] == []
        assert resetting["history_observed_lines"] == []
        assert resetting["history_choices"] == []
        assert resetting["dedupe_window"] == []
        await plugin._poll_bridge(force=True)
        recovered = plugin._snapshot_state()
        assert rewrote_stream is True
        assert alpha_tail_checkpoints and alpha_tail_checkpoints[0]
        assert alpha_tail_checkpoints[0] == standalone_checkpoints[0]
        assert alpha_tail_reset_results[:2] == [True, False]
        assert recovered["stream_reset_pending"] is False
        assert recovered["latest_snapshot"]["line_id"] == "alpha-replacement"
        assert [event["seq"] for event in recovered["history_events"]] == [1]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    "replacement_padding",
    [0, 1024],
    ids=["shrunk", "regrown-past-cursor"],
)
async def test_preexisting_session_rebases_saved_cursor_after_inactive_truncation(
    tmp_path: Path,
    replacement_padding: int,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    alpha_dir = _create_game_dir(
        bridge_root,
        game_id="demo.alpha",
        session_payload=_session(
            game_id="demo.alpha",
            session_id="sess-alpha",
            last_seq=1,
            started_at="2000-01-01T00:00:00Z",
            state=_session_state(
                text="alpha old " + "x" * 512,
                line_id="alpha-old",
                scene_id="scene-alpha",
            ),
        ),
        events=[
            _event(
                seq=1,
                event_type="line_changed",
                session_id="sess-alpha",
                game_id="demo.alpha",
                ts="2000-01-01T00:00:01Z",
                payload={
                    "speaker": "Yukino",
                    "text": "alpha old " + "x" * 512,
                    "line_id": "alpha-old",
                    "scene_id": "scene-alpha",
                    "route_id": "",
                },
            )
        ],
    )
    _create_game_dir(
        bridge_root,
        game_id="demo.beta",
        session_payload=_session(
            game_id="demo.beta",
            session_id="sess-beta",
            last_seq=1,
            started_at="2000-01-01T00:00:00Z",
            state=_session_state(
                text="beta old",
                line_id="beta-old",
                scene_id="scene-beta",
            ),
        ),
        events=[
            _event(
                seq=1,
                event_type="line_changed",
                session_id="sess-beta",
                game_id="demo.beta",
                ts="2000-01-01T00:00:01Z",
                payload={
                    "speaker": "Yukino",
                    "text": "beta old",
                    "line_id": "beta-old",
                    "scene_id": "scene-beta",
                    "route_id": "",
                },
            )
        ],
    )

    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    await plugin.startup()
    try:
        await plugin._poll_bridge(force=True)
        assert isinstance(await plugin.galgame_bind_game(game_id="demo.alpha"), Ok)
        await plugin._poll_bridge(force=True)
        saved_offset = plugin._snapshot_state()["events_byte_offset"]

        assert isinstance(await plugin.galgame_bind_game(game_id="demo.beta"), Ok)
        await plugin._poll_bridge(force=True)

        alpha_new = _event(
            seq=2,
            event_type="line_changed",
            session_id="sess-alpha",
            game_id="demo.alpha",
            ts="2026-04-21T08:36:02Z",
            payload={
                "speaker": "Yukino",
                "text": "alpha after rotation",
                "line_id": "alpha-new",
                "scene_id": "scene-alpha",
                "route_id": "",
                "padding": "y" * replacement_padding,
            },
        )
        events_path = alpha_dir / "events.jsonl"
        _write_events(events_path, [alpha_new])
        if replacement_padding:
            assert events_path.stat().st_size >= saved_offset
        else:
            assert events_path.stat().st_size < saved_offset
        _write_session(
            alpha_dir / "session.json",
            _session(
                game_id="demo.alpha",
                session_id="sess-alpha",
                last_seq=2,
                started_at="2000-01-01T00:00:00Z",
                state=_session_state(
                    text="alpha after rotation",
                    line_id="alpha-new",
                    scene_id="scene-alpha",
                    ts="2026-04-21T08:36:02Z",
                ),
            ),
        )

        assert isinstance(await plugin.galgame_bind_game(game_id="demo.alpha"), Ok)
        await plugin._poll_bridge(force=True)

        resumed = plugin._snapshot_state()
        history = await plugin.galgame_get_history(limit=20, include_events=True)
        assert resumed["events_byte_offset"] == events_path.stat().st_size
        assert resumed["stream_reset_pending"] is False
        assert resumed["last_seq"] == 2
        assert isinstance(history, Ok)
        assert [event["seq"] for event in history.value["events"]] == [2]
        assert [line["line_id"] for line in history.value["stable_lines"]] == [
            "alpha-new"
        ]
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    "replacement_bytes",
    [b"", b'{"seq":2,"type":"line_changed"'],
    ids=["empty", "partial-record"],
)
async def test_invalid_reattach_checkpoint_clears_cached_gameplay_immediately(
    tmp_path: Path,
    replacement_bytes: bytes,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    old_line = _event(
        seq=1,
        event_type="line_changed",
        session_id="sess-alpha",
        game_id="demo.alpha",
        ts="2000-01-01T00:00:01Z",
        payload={
            "speaker": "Yukino",
            "text": "abandoned cached line",
            "line_id": "alpha-old",
            "scene_id": "scene-alpha",
            "route_id": "",
        },
    )
    alpha_dir = _create_game_dir(
        bridge_root,
        game_id="demo.alpha",
        session_payload=_session(
            game_id="demo.alpha",
            session_id="sess-alpha",
            last_seq=1,
            started_at="2000-01-01T00:00:00Z",
            state=_session_state(
                text="abandoned cached line",
                line_id="alpha-old",
                scene_id="scene-alpha",
            ),
        ),
        events=[old_line],
    )
    _create_game_dir(
        bridge_root,
        game_id="demo.beta",
        session_payload=_session(
            game_id="demo.beta",
            session_id="sess-beta",
            last_seq=1,
            started_at="2000-01-01T00:00:00Z",
            state=_session_state(
                text="beta line",
                line_id="beta-old",
                scene_id="scene-beta",
            ),
        ),
        events=[
            _event(
                seq=1,
                event_type="line_changed",
                session_id="sess-beta",
                game_id="demo.beta",
                ts="2000-01-01T00:00:01Z",
                payload={
                    "text": "beta line",
                    "line_id": "beta-old",
                    "scene_id": "scene-beta",
                },
            )
        ],
    )
    plugin = GalgameBridgePlugin(_Ctx(plugin_dir, _make_effective_config(bridge_root)))
    await plugin.startup()
    try:
        await plugin._poll_bridge(force=True)
        assert isinstance(await plugin.galgame_bind_game(game_id="demo.alpha"), Ok)
        await plugin._poll_bridge(force=True)
        active_line = _event(
            seq=2,
            event_type="line_changed",
            session_id="sess-alpha",
            game_id="demo.alpha",
            ts="2026-04-21T08:35:02Z",
            payload={
                "speaker": "Yukino",
                "text": "cached active line",
                "line_id": "alpha-active",
                "scene_id": "scene-alpha",
                "route_id": "",
            },
        )
        _append_event(alpha_dir / "events.jsonl", active_line)
        _write_session(
            alpha_dir / "session.json",
            _session(
                game_id="demo.alpha",
                session_id="sess-alpha",
                last_seq=2,
                started_at="2000-01-01T00:00:00Z",
                state=_session_state(
                    text="cached active line",
                    line_id="alpha-active",
                    scene_id="scene-alpha",
                    ts="2026-04-21T08:35:02Z",
                ),
            ),
        )
        await plugin._poll_bridge(force=True)
        assert (
            plugin._snapshot_state()["latest_snapshot"]["line_id"]
            == "alpha-active"
        )

        assert isinstance(await plugin.galgame_bind_game(game_id="demo.beta"), Ok)
        await plugin._poll_bridge(force=True)

        events_path = alpha_dir / "events.jsonl"
        events_path.write_bytes(replacement_bytes)
        _write_session(
            alpha_dir / "session.json",
            _session(
                game_id="demo.alpha",
                session_id="sess-alpha",
                last_seq=3,
                started_at="2000-01-01T00:00:00Z",
                state=_session_state(
                    text="replacement not complete",
                    line_id="alpha-new",
                    scene_id="scene-alpha",
                ),
            ),
        )

        assert isinstance(await plugin.galgame_bind_game(game_id="demo.alpha"), Ok)
        await plugin._poll_bridge(force=True)

        resumed = plugin._snapshot_state()
        assert resumed["active_session_id"] == "sess-alpha"
        assert resumed["stream_reset_pending"] is bool(replacement_bytes)
        assert resumed["events_byte_offset"] == 0
        assert resumed["events_file_size"] == len(replacement_bytes)
        assert resumed["last_seq"] == 0
        assert resumed["latest_snapshot"] == {}
        assert resumed["history_events"] == []
        assert resumed["history_lines"] == []
        assert resumed["history_observed_lines"] == []
        assert resumed["history_choices"] == []
        assert resumed["dedupe_window"] == []
    finally:
        await plugin.shutdown()
