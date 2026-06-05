from __future__ import annotations

from _galgame_test_support import *

@pytest.mark.plugin_unit
def test_compute_memory_reader_game_id_avoids_windows_invalid_path_characters() -> None:
    game_id = compute_memory_reader_game_id("RenPy Demo.exe")
    assert game_id.startswith("mem-")
    assert ":" not in game_id
    assert len(game_id.removeprefix("mem-")) == 16


@pytest.mark.plugin_unit
def test_memory_reader_append_event_respects_update_snapshot_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = MemoryReaderBridgeWriter(bridge_root=tmp_path, time_fn=lambda: 1710000000.0)
    snapshot_writes = {"count": 0}
    original_write_snapshot = writer._write_session_snapshot

    def _counted_write_snapshot() -> None:
        snapshot_writes["count"] += 1
        original_write_snapshot()

    monkeypatch.setattr(writer, "_write_session_snapshot", _counted_write_snapshot)
    writer.start_session(
        DetectedGameProcess(
            pid=4242,
            name="RenPy Demo.exe",
            create_time=1709999999.0,
            engine="renpy",
        )
    )
    writes_after_start = snapshot_writes["count"]

    assert writer.emit_heartbeat(ts="2026-04-21T08:31:05Z") is True
    assert snapshot_writes["count"] == writes_after_start

    assert writer.emit_line("雪乃：今天也一起回家吧。", ts="2026-04-21T08:31:06Z") is True
    assert snapshot_writes["count"] == writes_after_start + 1


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("platform_value", "expected_enabled"),
    [
        ("win32", True),
        ("darwin", False),
        ("linux", False),
    ],
)
def test_build_config_uses_platform_default_memory_reader_enablement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    platform_value: str,
    expected_enabled: bool,
) -> None:
    monkeypatch.setattr(galgame_service.sys, "platform", platform_value)
    cfg = build_config({"galgame": {"bridge_root": str(tmp_path / "bridge")}})
    assert cfg.memory_reader_enabled is expected_enabled


@pytest.mark.plugin_unit
def test_build_config_explicit_memory_reader_enabled_overrides_platform_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(galgame_service.sys, "platform", "win32")
    cfg = build_config(
        {
            "galgame": {"bridge_root": str(tmp_path / "bridge")},
            "memory_reader": {"enabled": False},
        }
    )
    assert cfg.memory_reader_enabled is False


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("platform_value", "expected_enabled"),
    [
        ("win32", True),
        ("darwin", False),
        ("linux", False),
    ],
)
def test_build_config_uses_platform_default_ocr_reader_enablement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    platform_value: str,
    expected_enabled: bool,
) -> None:
    monkeypatch.setattr(galgame_service.sys, "platform", platform_value)
    cfg = build_config({"galgame": {"bridge_root": str(tmp_path / "bridge")}})
    assert cfg.ocr_reader_enabled is expected_enabled


@pytest.mark.plugin_unit
def test_build_config_explicit_ocr_reader_enabled_overrides_platform_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(galgame_service.sys, "platform", "win32")
    cfg = build_config(
        {
            "galgame": {"bridge_root": str(tmp_path / "bridge")},
            "ocr_reader": {"enabled": False},
        }
    )
    assert cfg.ocr_reader_enabled is False


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_memory_reader_auto_discovers_textractor_from_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path_dir = tmp_path / "bin"
    path_dir.mkdir()
    textractor_path = path_dir / "TextractorCLI.exe"
    textractor_path.write_text("", encoding="utf-8")
    textractor_path.chmod(0o755)
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    cfg = build_config(
        {
            "galgame": {"bridge_root": str(tmp_path / "bridge_root")},
            "memory_reader": {
                "enabled": True,
                "textractor_path": "",
                "auto_detect": True,
                "poll_interval_seconds": 1,
                "engine_hooks": {"renpy": ["/HREN@Demo.dll"]},
            },
        }
    )
    captured_paths: list[str] = []
    handle = _FakeTextractorHandle()

    async def _process_factory(path: str):
        captured_paths.append(path)
        return handle

    manager = MemoryReaderManager(
        logger=_Logger(),
        config=cfg,
        process_factory=_process_factory,
        process_scanner=lambda: [
            DetectedGameProcess(
                pid=4242,
                name="RenPy Demo.exe",
                create_time=1709999999.0,
                engine="renpy",
            )
        ],
        time_fn=lambda: 1710000000.0,
        platform_fn=lambda: True,
        writer=MemoryReaderBridgeWriter(
            bridge_root=cfg.bridge_root,
            time_fn=lambda: 1710000000.0,
        ),
    )

    result = await manager.tick(bridge_sdk_available=False)

    assert captured_paths == [str(textractor_path)]
    assert handle.writes == ["attach -P4242\n", "/HREN@Demo.dll -P4242\n"]
    assert result.runtime["status"] == "attaching"
    await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_memory_reader_auto_discovers_textractor_from_localappdata_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    local_appdata = tmp_path / "LocalAppData"
    textractor_path = local_appdata / "Programs" / "Textractor" / "TextractorCLI.exe"
    textractor_path.parent.mkdir(parents=True, exist_ok=True)
    textractor_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "ProgramFiles"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "ProgramFilesX86"))

    cfg = build_config(
        {
            "galgame": {"bridge_root": str(tmp_path / "bridge_root")},
            "memory_reader": {
                "enabled": True,
                "textractor_path": "",
                "auto_detect": True,
                "poll_interval_seconds": 1,
                "engine_hooks": {"renpy": ["/HREN@Demo.dll"]},
            },
        }
    )
    captured_paths: list[str] = []

    async def _process_factory(path: str):
        captured_paths.append(path)
        return _FakeTextractorHandle()

    manager = MemoryReaderManager(
        logger=_Logger(),
        config=cfg,
        process_factory=_process_factory,
        process_scanner=lambda: [
            DetectedGameProcess(
                pid=4242,
                name="RenPy Demo.exe",
                create_time=1709999999.0,
                engine="renpy",
            )
        ],
        time_fn=lambda: 1710000000.0,
        platform_fn=lambda: True,
        writer=MemoryReaderBridgeWriter(
            bridge_root=cfg.bridge_root,
            time_fn=lambda: 1710000000.0,
        ),
    )

    result = await manager.tick(bridge_sdk_available=False)

    assert captured_paths == [str(textractor_path)]
    assert result.runtime["status"] == "attaching"
    await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_memory_reader_keeps_recoverable_idle_state_when_textractor_autodiscovery_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty-local"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "empty-program-files"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "empty-program-files-x86"))

    cfg = build_config(
        {
            "galgame": {"bridge_root": str(tmp_path / "bridge_root")},
            "memory_reader": {
                "enabled": True,
                "textractor_path": "",
                "auto_detect": True,
                "poll_interval_seconds": 1,
            },
        }
    )
    factory_calls: list[str] = []

    async def _process_factory(path: str):
        factory_calls.append(path)
        return _FakeTextractorHandle()

    manager = MemoryReaderManager(
        logger=_Logger(),
        config=cfg,
        process_factory=_process_factory,
        process_scanner=lambda: [],
        time_fn=lambda: 1710000000.0,
        platform_fn=lambda: True,
        writer=MemoryReaderBridgeWriter(
            bridge_root=cfg.bridge_root,
            time_fn=lambda: 1710000000.0,
        ),
    )

    try:
        result = await manager.tick(bridge_sdk_available=False)

        assert factory_calls == []
        assert result.runtime["status"] == "idle"
        assert result.runtime["detail"] == "invalid_textractor_path"
        assert result.warnings == ["memory_reader TextractorCLI.exe is invalid or missing"]
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_windows_default_memory_reader_config_autodiscovers_textractor_and_takes_over_without_bridge_sdk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(galgame_service.sys, "platform", "win32")
    path_dir = tmp_path / "bin"
    path_dir.mkdir()
    textractor_path = path_dir / "TextractorCLI.exe"
    textractor_path.write_text("", encoding="utf-8")
    textractor_path.chmod(0o755)
    monkeypatch.setenv("PATH", str(path_dir))

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={
            "auto_detect": True,
            "poll_interval_seconds": 1,
            "engine_hooks": {"renpy": ["/HREN@Demo.dll"]},
        },
    )
    del cfg["memory_reader"]["enabled"]  # type: ignore[index]
    del cfg["memory_reader"]["textractor_path"]  # type: ignore[index]

    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(ctx._config)
    clock = {"now": 1710000000.0}
    expected_snapshot_text = "Windows default config takeover."
    good_handle = _FakeTextractorHandle(
        [f"[4242:100:0:0] {expected_snapshot_text}"]
    )

    async def _process_factory(path: str):
        assert path == str(textractor_path)
        return good_handle

    plugin._memory_reader_manager = MemoryReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        process_factory=_process_factory,
        process_scanner=lambda: [
            DetectedGameProcess(
                pid=4242,
                name="RenPy Demo.exe",
                create_time=1709999999.0,
                engine="renpy",
            )
        ],
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        writer=MemoryReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )

    try:
        await plugin._poll_bridge(force=True)
        status = await plugin.galgame_get_status()
        snapshot = await plugin.galgame_get_snapshot()

        assert isinstance(status, Ok)
        assert isinstance(snapshot, Ok)
        assert status.value["memory_reader_enabled"] is True
        assert status.value["active_data_source"] == DATA_SOURCE_MEMORY_READER
        assert status.value["memory_reader_runtime"]["status"] == "active"
        assert snapshot.value["snapshot"]["text"] == expected_snapshot_text
        assert good_handle.writes == ["attach -P4242\n", "/HREN@Demo.dll -P4242\n"]
    finally:
        await plugin._memory_reader_manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_windows_default_memory_reader_config_stays_idle_when_textractor_autodiscovery_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(galgame_service.sys, "platform", "win32")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty-local"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "empty-program-files"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "empty-program-files-x86"))

    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={
            "auto_detect": True,
            "poll_interval_seconds": 1,
        },
        ocr_reader={"enabled": False},
    )
    del cfg["memory_reader"]["enabled"]  # type: ignore[index]
    del cfg["memory_reader"]["textractor_path"]  # type: ignore[index]

    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    try:
        plugin._memory_reader_manager = MemoryReaderManager(
            logger=plugin.logger,
            config=plugin._cfg,
            process_scanner=lambda: [],
            time_fn=lambda: 1710000000.0,
            platform_fn=lambda: True,
            writer=MemoryReaderBridgeWriter(
                bridge_root=bridge_root,
                time_fn=lambda: 1710000000.0,
            ),
        )

        await plugin._poll_bridge(force=True)
        status = await plugin.galgame_get_status()

        assert isinstance(status, Ok)
        assert status.value["memory_reader_enabled"] is True
        assert status.value["active_data_source"] == "none"
        assert status.value["memory_reader_runtime"]["status"] == "idle"
        assert status.value["memory_reader_runtime"]["detail"] == "invalid_textractor_path"
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_ocr_reader_fallback_activates_when_bridge_sdk_and_memory_reader_are_missing(
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
            "textractor_path": "",
        },
        ocr_reader={
            "enabled": True,
            "install_target_dir": str(install_root),
            "poll_interval_seconds": 999.0,
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    monkeypatch.setattr(galgame_plugin_module, "MemoryReaderManager", _NoopMemoryReaderManager)
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    try:
        clock = {"now": 1710000000.0}
        plugin._ocr_reader_manager = OcrReaderManager(
            logger=plugin.logger,
            config=plugin._cfg,
            time_fn=lambda: clock["now"],
            platform_fn=lambda: True,
            window_scanner=lambda: [
                DetectedGameWindow(
                    hwnd=101,
                    title="OCR Demo Window",
                    process_name="DemoGame.exe",
                    pid=4242,
                )
            ],
            capture_backend=_FakeCaptureBackend(),
            ocr_backend=_FakeOcrBackend(
                [
                    "雪乃：来自 OCR 的台词。",
                    "雪乃：来自 OCR 的台词。",
                ]
            ),
            writer=OcrReaderBridgeWriter(
                bridge_root=bridge_root,
                time_fn=lambda: clock["now"],
            ),
        )
        _clear_bridge_root(bridge_root)

        await plugin._poll_bridge(force=True)
        clock["now"] += 1.0
        await plugin._poll_bridge(force=True)
        clock["now"] += 1.0
        await plugin._poll_bridge(force=True)

        status = await plugin.galgame_get_status()
        snapshot = await plugin.galgame_get_snapshot()

        assert isinstance(status, Ok)
        assert isinstance(snapshot, Ok)
        assert status.value["active_data_source"] == DATA_SOURCE_OCR_READER
        assert status.value["summary"].startswith("已通过 OCR 读取连接（降级模式）")
        assert snapshot.value["snapshot"]["scene_id"] == "ocr:unknown_scene"
        assert snapshot.value["snapshot"]["line_id"].startswith("ocr:")
        assert snapshot.value["snapshot"]["text"] == "来自 OCR 的台词。"
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_memory_reader_text_freshness_resets_when_session_changes(
    tmp_path: Path,
) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    ctx = _Ctx(
        plugin_dir,
        _make_effective_config(
            bridge_root,
            memory_reader={"enabled": True},
            ocr_reader={
                "enabled": True,
                "no_text_takeover_after_seconds": 30.0,
            },
        ),
    )
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(ctx._config)
    now = time.monotonic()

    first_runtime = {
        "status": "active",
        "detail": "receiving_text",
        "game_id": "mem-demo",
        "session_id": "mem-session-a",
        "last_text_seq": 3,
    }
    assert plugin._update_memory_reader_text_freshness(
        first_runtime,
        now_monotonic=now,
    ) is True
    assert first_runtime["last_text_recent"] is True

    second_runtime = {
        "status": "active",
        "detail": "attached_idle_after_text",
        "game_id": "mem-demo",
        "session_id": "mem-session-b",
        "last_text_seq": 3,
    }
    assert plugin._update_memory_reader_text_freshness(
        second_runtime,
        now_monotonic=now + 1.0,
    ) is False
    assert second_runtime["last_text_recent"] is False


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_memory_reader_fallback_activates_when_bridge_sdk_is_missing(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    textractor_path = tmp_path / "TextractorCLI.exe"
    textractor_path.write_text("", encoding="utf-8")
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={
            "enabled": True,
            "textractor_path": str(textractor_path),
            "engine_hooks": {"renpy": ["/HREN@Demo.dll"]},
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(ctx._config)
    clock = {"now": 1710000000.0}
    handle = _FakeTextractorHandle(
        ["[4242:100:0:0] 雪乃：来自内存读取的台词。"]
    )
    async def _process_factory(path: str):
        del path
        return handle
    plugin._memory_reader_manager = MemoryReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        process_factory=_process_factory,
        process_scanner=lambda: [
            DetectedGameProcess(
                pid=4242,
                name="RenPy Demo.exe",
                create_time=1709999999.0,
                engine="renpy",
            )
        ],
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        writer=MemoryReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )

    try:
        await plugin._poll_bridge(force=True)
        status = await plugin.galgame_get_status()
        snapshot = await plugin.galgame_get_snapshot()

        assert isinstance(status, Ok)
        assert isinstance(snapshot, Ok)
        assert status.value["memory_reader_enabled"] is True
        assert status.value["active_data_source"] == DATA_SOURCE_MEMORY_READER
        assert status.value["summary"].startswith("已通过内存读取连接（降级模式）")
        assert status.value["memory_reader_runtime"]["status"] == "active"
        assert snapshot.value["snapshot"]["text"] == "来自内存读取的台词。"
        assert handle.writes == ["attach -P4242\n", "/HREN@Demo.dll -P4242\n"]
    finally:
        await plugin._memory_reader_manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.plugin_unit
async def test_bridge_sdk_session_preempts_memory_reader_candidate(tmp_path: Path) -> None:
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    textractor_path = tmp_path / "TextractorCLI.exe"
    textractor_path.write_text("", encoding="utf-8")
    cfg = _make_effective_config(
        bridge_root,
        memory_reader={
            "enabled": True,
            "textractor_path": str(textractor_path),
            "engine_hooks": {"renpy": ["/HREN@Demo.dll"]},
        },
    )
    ctx = _Ctx(plugin_dir, cfg)
    plugin = GalgameBridgePlugin(ctx)
    plugin._cfg = build_config(ctx._config)
    clock = {"now": 1710000000.0}
    handle = _FakeTextractorHandle(
        ["[4242:100:0:0] 雪乃：先走内存读取链路。"]
    )
    async def _process_factory(path: str):
        del path
        return handle
    plugin._memory_reader_manager = MemoryReaderManager(
        logger=plugin.logger,
        config=plugin._cfg,
        process_factory=_process_factory,
        process_scanner=lambda: [
            DetectedGameProcess(
                pid=4242,
                name="RenPy Demo.exe",
                create_time=1709999999.0,
                engine="renpy",
            )
        ],
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        writer=MemoryReaderBridgeWriter(
            bridge_root=bridge_root,
            time_fn=lambda: clock["now"],
        ),
    )

    try:
        await plugin._poll_bridge(force=True)
        memory_reader_status = await plugin.galgame_get_status()
        assert isinstance(memory_reader_status, Ok)
        assert memory_reader_status.value["active_data_source"] == DATA_SOURCE_MEMORY_READER

        _create_game_dir(
            bridge_root,
            game_id="demo.bridge",
            session_payload=_session(
                game_id="demo.bridge",
                session_id="sdk-sess",
                last_seq=3,
                state=_session_state(
                    speaker="桥接",
                    text="Bridge SDK 已接管。",
                    line_id="sdk-line",
                    scene_id="sdk-scene",
                ),
            ),
        )

        clock["now"] += 1.0
        await plugin._poll_bridge(force=True)
        status = await plugin.galgame_get_status()

        assert isinstance(status, Ok)
        assert status.value["active_data_source"] == DATA_SOURCE_BRIDGE_SDK
        assert status.value["active_session_id"] == "sdk-sess"
        assert status.value["memory_reader_runtime"]["detail"] == "bridge_sdk_available"
        assert handle.terminated is True
    finally:
        await plugin._memory_reader_manager.shutdown()
