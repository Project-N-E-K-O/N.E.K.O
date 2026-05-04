from __future__ import annotations

import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from plugin.plugins.galgame_plugin import memory_reader as galgame_memory_reader
from plugin.plugins.galgame_plugin.memory_reader import (
    DetectedGameProcess,
    MemoryReaderBridgeWriter,
    MemoryReaderManager,
    _default_process_scanner,
)
from plugin.plugins.galgame_plugin.reader import read_session_json, tail_events_jsonl
from plugin.plugins.galgame_plugin.service import build_config
from plugin.plugins.galgame_plugin.textractor_support import (
    _download_file,
    inspect_textractor_installation,
    install_textractor,
)


pytestmark = pytest.mark.plugin_unit


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


class _CapturingLogger(_Logger):
    def __init__(self) -> None:
        self.messages: list[tuple[str, tuple[object, ...]]] = []

    def info(self, *args, **kwargs):
        del kwargs
        self.messages.append(("info", args))

    def warning(self, *args, **kwargs):
        del kwargs
        self.messages.append(("warning", args))

    def debug(self, *args, **kwargs):
        del kwargs
        self.messages.append(("debug", args))


class _FakeTextractorHandle:
    def __init__(self, lines: list[str] | None = None) -> None:
        self.lines = list(lines or [])
        self.writes: list[str] = []
        self.returncode: int | None = None
        self.terminated = False

    async def write(self, payload: str) -> None:
        self.writes.append(payload)

    async def readline(self, timeout: float) -> str | None:
        del timeout
        if not self.lines:
            return None
        return self.lines.pop(0)

    def poll(self) -> int | None:
        return self.returncode

    async def terminate(self) -> None:
        self.terminated = True
        if self.returncode is None:
            self.returncode = 0

    async def wait(self, timeout: float) -> int | None:
        del timeout
        return self.returncode


class _FakePopenProcess:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(b"")
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class _CallableWinApi:
    def __init__(self, func) -> None:
        self._func = func
        self.restype = None

    def __call__(self, *args):
        return self._func(*args)


def _make_config(
    bridge_root: Path,
    *,
    enabled: bool = True,
    textractor_path: str = "",
    auto_detect: bool = True,
    poll_interval_seconds: float = 1.0,
) -> object:
    return build_config(
        {
            "galgame": {
                "bridge_root": str(bridge_root),
            },
            "memory_reader": {
                "enabled": enabled,
                "textractor_path": textractor_path,
                "auto_detect": auto_detect,
                "poll_interval_seconds": poll_interval_seconds,
            },
        }
    )


@pytest.mark.asyncio
async def test_default_process_factory_uses_no_window_flag_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_popen(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakePopenProcess(*args, **kwargs)

    monkeypatch.setattr(galgame_memory_reader.sys, "platform", "win32")
    monkeypatch.setattr(
        galgame_memory_reader.subprocess,
        "CREATE_NO_WINDOW",
        0x08000000,
        raising=False,
    )
    monkeypatch.setattr(galgame_memory_reader.subprocess, "Popen", _fake_popen)

    handle = await galgame_memory_reader._default_process_factory("TextractorCLI.exe")
    await handle.terminate()
    await handle.wait(timeout=0.1)

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["creationflags"] == 0x08000000


def test_textractor_job_object_uses_kill_on_close(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    class _Kernel32:
        def __init__(self) -> None:
            self.CreateJobObjectW = _CallableWinApi(lambda *_args: 456)
            self.SetInformationJobObject = _CallableWinApi(self._set_info)
            self.AssignProcessToJobObject = _CallableWinApi(self._assign)
            self.CloseHandle = _CallableWinApi(lambda *args: calls.append(("close", args)) or 1)

        def _set_info(self, *args):
            calls.append(("set_info", args))
            return 1

        def _assign(self, *args):
            calls.append(("assign", args))
            return 1

    process = SimpleNamespace(_handle=123)
    monkeypatch.setattr(galgame_memory_reader.sys, "platform", "win32")
    monkeypatch.setattr(
        galgame_memory_reader.ctypes,
        "windll",
        SimpleNamespace(kernel32=_Kernel32()),
        raising=False,
    )

    handle = galgame_memory_reader._create_kill_on_close_job_for_process(process)
    assert handle == 456
    assert [name for name, _args in calls] == ["set_info", "assign"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform_value", "auto_detect", "textractor_exists", "expected_detail", "warning_fragment"),
    [
        (False, True, True, "unsupported_platform", "Windows-only"),
        (True, False, True, "manual_pid_unimplemented", "auto_detect=false"),
        (True, True, False, "invalid_textractor_path", "TextractorCLI.exe"),
    ],
)
async def test_memory_reader_manager_returns_recoverable_warnings_for_unavailable_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform_value: bool,
    auto_detect: bool,
    textractor_exists: bool,
    expected_detail: str,
    warning_fragment: str,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    textractor_path = tmp_path / "TextractorCLI.exe"
    if textractor_exists:
        textractor_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "programfiles"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "programfiles_x86"))

    manager = MemoryReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            textractor_path=str(textractor_path),
            auto_detect=auto_detect,
        ),
        platform_fn=lambda: platform_value,
    )

    result = await manager.tick(bridge_sdk_available=False)

    assert result.runtime["status"] == "idle"
    assert result.runtime["detail"] == expected_detail
    assert warning_fragment in result.warnings[0]


def test_default_process_scanner_orders_candidates_by_create_time_then_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Proc:
        def __init__(self, info: dict[str, object], modules: list[str]) -> None:
            self.info = info
            self._modules = modules

        def memory_maps(self, grouped: bool = False):
            del grouped
            return [SimpleNamespace(path=module) for module in self._modules]

    fake_psutil = SimpleNamespace(
        process_iter=lambda fields: [
            _Proc(
                {
                    "pid": 30,
                    "name": "OldGame.exe",
                    "cmdline": ["OldGame.exe"],
                    "create_time": 10.0,
                },
                ["UnityPlayer.dll"],
            ),
            _Proc(
                {
                    "pid": 20,
                    "name": "python.exe",
                    "cmdline": ["python.exe", "renpy"],
                    "create_time": 20.0,
                },
                [],
            ),
            _Proc(
                {
                    "pid": 10,
                    "name": "UnityGame.exe",
                    "cmdline": ["UnityGame.exe"],
                    "create_time": 20.0,
                },
                ["UnityPlayer.dll"],
            ),
        ]
    )
    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.memory_reader.psutil",
        fake_psutil,
    )

    detected = _default_process_scanner()

    assert [(item.pid, item.engine) for item in detected] == [
        (10, "unity"),
        (20, "renpy"),
        (30, "unity"),
    ]


def test_default_process_scanner_excludes_unity_crash_handler_and_crashpad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Proc:
        def __init__(self, info: dict[str, object], modules: list[str]) -> None:
            self.info = info
            self._modules = modules

        def memory_maps(self, grouped: bool = False):
            del grouped
            return [SimpleNamespace(path=module) for module in self._modules]

    fake_psutil = SimpleNamespace(
        process_iter=lambda fields: [
            _Proc(
                {
                    "pid": 99,
                    "name": "UnityCrashHandler64.exe",
                    "cmdline": ["UnityCrashHandler64.exe"],
                    "create_time": 30.0,
                },
                ["UnityPlayer.dll"],
            ),
            _Proc(
                {
                    "pid": 98,
                    "name": "crashpad_handler",
                    "cmdline": ["crashpad_handler"],
                    "create_time": 29.0,
                },
                [],
            ),
            _Proc(
                {
                    "pid": 10,
                    "name": "TheWeepingSwan.exe",
                    "cmdline": ["TheWeepingSwan.exe"],
                    "create_time": 20.0,
                },
                ["UnityPlayer.dll", "Assembly-CSharp.dll"],
            ),
        ]
    )
    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.memory_reader.psutil",
        fake_psutil,
    )

    detected = _default_process_scanner()

    assert [(item.pid, item.name, item.engine) for item in detected] == [
        (10, "TheWeepingSwan.exe", "unity"),
    ]


def test_default_process_scanner_detects_kirikiri_from_xp3_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game_dir = tmp_path / "SenrenBanka"
    game_dir.mkdir()
    exe_path = game_dir / "SenrenBanka.exe"
    exe_path.write_text("", encoding="utf-8")
    (game_dir / "data.xp3").write_text("", encoding="utf-8")

    class _Proc:
        def __init__(self, info: dict[str, object]) -> None:
            self.info = info

        def memory_maps(self, grouped: bool = False):
            del grouped
            return []

    fake_psutil = SimpleNamespace(
        process_iter=lambda fields: [
            _Proc(
                {
                    "pid": 1144400,
                    "name": "SenrenBanka.exe",
                    "cmdline": [str(exe_path)],
                    "create_time": 20.0,
                    "exe": str(exe_path),
                }
            )
        ]
    )
    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.memory_reader.psutil",
        fake_psutil,
    )
    with galgame_memory_reader._KIRIKIRI_DIR_CACHE_LOCK:
        galgame_memory_reader._KIRIKIRI_DIR_CACHE.clear()

    detected = _default_process_scanner()

    assert len(detected) == 1
    assert detected[0].engine == "kirikiri"
    assert detected[0].exe_path == str(exe_path)
    assert detected[0].detection_reason == "detected_kirikiri_common_xp3"


def test_default_process_scanner_detects_senren_banka_from_process_preset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exe_path = tmp_path / "SenrenBanka.exe"

    class _Proc:
        def __init__(self, info: dict[str, object]) -> None:
            self.info = info

        def memory_maps(self, grouped: bool = False):
            del grouped
            return []

    fake_psutil = SimpleNamespace(
        process_iter=lambda fields: [
            _Proc(
                {
                    "pid": 1144400,
                    "name": "SenrenBanka.exe",
                    "cmdline": [str(exe_path)],
                    "create_time": 20.0,
                    "exe": str(exe_path),
                }
            )
        ]
    )
    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.memory_reader.psutil",
        fake_psutil,
    )

    detected = _default_process_scanner()

    assert len(detected) == 1
    assert detected[0].engine == "kirikiri"
    assert detected[0].detection_reason == "detected_kirikiri_preset_senren_banka"


def test_default_process_scanner_detects_senren_banka_from_steam_app_preset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exe_path = tmp_path / "SteamLibrary" / "steamapps" / "common" / "1144400" / "Game.exe"

    class _Proc:
        def __init__(self, info: dict[str, object]) -> None:
            self.info = info

        def memory_maps(self, grouped: bool = False):
            del grouped
            return []

    fake_psutil = SimpleNamespace(
        process_iter=lambda fields: [
            _Proc(
                {
                    "pid": 1144401,
                    "name": "Game.exe",
                    "cmdline": [str(exe_path)],
                    "create_time": 20.0,
                    "exe": str(exe_path),
                }
            )
        ]
    )
    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.memory_reader.psutil",
        fake_psutil,
    )

    detected = _default_process_scanner()

    assert len(detected) == 1
    assert detected[0].engine == "kirikiri"
    assert detected[0].detection_reason == "detected_kirikiri_preset_senren_banka"


def test_memory_reader_bridge_writer_emits_stable_bridge_schema_and_choice_ids(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    writer = MemoryReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 1710000000.0)
    process = DetectedGameProcess(
        pid=4242,
        name="RenPy Demo.exe",
        create_time=1710000000.0,
        engine="renpy",
    )

    writer.start_session(process)
    assert writer.emit_line("雪乃：一起回家吧。", ts="2026-04-22T01:00:00Z") is True
    assert writer.emit_choices(["去教室", "去天台"], ts="2026-04-22T01:00:01Z") is True

    session_path = bridge_root / writer.game_id / "session.json"
    events_path = bridge_root / writer.game_id / "events.jsonl"
    session = read_session_json(session_path).session
    assert session is not None
    assert session["protocol_version"] == 1
    assert session["bridge_sdk_version"].startswith("memory-reader-")
    assert session["metadata"]["source"] == "memory_reader"
    assert session["metadata"]["game_process_name"] == "RenPy Demo.exe"
    assert session["metadata"]["game_pid"] == 4242
    assert session["state"]["scene_id"] == "mem:unknown_scene"
    assert session["state"]["line_id"].startswith("mem:")
    assert session["state"]["choices"][0]["choice_id"] == f"{session['state']['line_id']}#choice0"
    assert session["state"]["choices"][1]["choice_id"] == f"{session['state']['line_id']}#choice1"

    events = tail_events_jsonl(events_path, offset=0, line_buffer=b"").events
    assert [event["type"] for event in events] == [
        "session_started",
        "line_changed",
        "choices_shown",
    ]
    assert events[-1]["payload"]["choices"][0]["text"] == "去教室"


def test_memory_line_id_collision_suffix_has_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(galgame_memory_reader, "_MEMORY_LINE_ID_MAX_COLLISION_SUFFIX", 2)
    writer = MemoryReaderBridgeWriter(bridge_root=tmp_path)
    text = "same normalized line"
    normalized = galgame_memory_reader.normalize_text(text)
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    widths = list(range(12, len(digest) + 1, 4))
    if widths[-1] != len(digest):
        widths.append(len(digest))
    for width in widths:
        writer._line_id_owner[f"mem:{digest[:width]}"] = "other"
    for suffix in range(1, 3):
        writer._line_id_owner[f"mem:{digest}#{suffix}"] = "other"

    with pytest.raises(RuntimeError, match="collision limit"):
        writer._line_id_for_text(text)


def test_parse_textractor_line_accepts_extended_metadata() -> None:
    parsed, error = MemoryReaderManager._parse_textractor_line(
        "[1:0:0:FFFFFFFFFFFFFFFF:FFFFFFFFFFFFFFFF:Clipboard:HB0@0] dialogue text"
    )

    assert error == ""
    assert parsed is not None
    assert parsed.pid == 1
    assert parsed.hook_addr == "0"
    assert parsed.ctx == "0"
    assert parsed.sub_ctx == "FFFFFFFFFFFFFFFF"
    assert parsed.text == "dialogue text"


def test_memory_writer_tracks_text_seq_separately_from_heartbeat(tmp_path: Path) -> None:
    writer = MemoryReaderBridgeWriter(
        bridge_root=tmp_path,
        time_fn=lambda: 1710000000.0,
    )
    writer.start_session(
        DetectedGameProcess(
            pid=4242,
            name="RenPy Demo.exe",
            create_time=1709999999.0,
            engine="renpy",
        )
    )

    assert writer.last_text_seq == 0
    assert writer.last_text_ts == ""
    assert writer.emit_line("first memory line", ts="2026-04-29T01:00:00Z") is True
    text_seq = writer.last_text_seq
    text_ts = writer.last_text_ts
    assert text_seq == writer.last_seq
    assert text_ts == "2026-04-29T01:00:00Z"

    assert writer.emit_heartbeat(ts="2026-04-29T01:00:01Z") is True
    assert writer.last_seq == text_seq + 1
    assert writer.last_text_seq == text_seq
    assert writer.last_text_ts == text_ts

    session = read_session_json(tmp_path / writer.game_id / "session.json").session
    assert session is not None
    assert session["state"]["text"] == "first memory line"


@pytest.mark.asyncio
async def test_memory_reader_manager_attaches_consumes_textractor_output_and_emits_heartbeat(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    textractor_path = tmp_path / "TextractorCLI.exe"
    textractor_path.write_text("", encoding="utf-8")
    handle = _FakeTextractorHandle(
        [
            "[4242:100:0:0] 雪乃：今天也一起回家吧。",
            "[4242:100:0:0] 雪乃：今天也一起回家吧。",
        ]
    )
    clock = {"now": 1710000000.0}

    async def _process_factory(path: str):
        del path
        return handle

    manager = MemoryReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            textractor_path=str(textractor_path),
            auto_detect=True,
            poll_interval_seconds=0.5,
        ),
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
    )

    first = await manager.tick(bridge_sdk_available=False)
    assert first.should_rescan is True
    assert first.runtime["status"] == "active"
    assert handle.writes == ["attach -P4242\n"]

    game_id = first.runtime["game_id"]
    events_path = bridge_root / game_id / "events.jsonl"
    first_events = tail_events_jsonl(events_path, offset=0, line_buffer=b"").events
    assert [event["type"] for event in first_events] == ["session_started", "line_changed"]

    clock["now"] += 1.0
    second = await manager.tick(bridge_sdk_available=False)
    assert second.should_rescan is True
    second_events = tail_events_jsonl(events_path, offset=0, line_buffer=b"").events
    assert [event["type"] for event in second_events] == [
        "session_started",
        "line_changed",
        "heartbeat",
    ]

    await manager.shutdown()
    assert handle.terminated is True


@pytest.mark.asyncio
async def test_memory_reader_manager_skips_legacy_hook_codes_for_kirikiri(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    textractor_path = tmp_path / "TextractorCLI.exe"
    textractor_path.write_text("", encoding="utf-8")
    handle = _FakeTextractorHandle([])

    async def _process_factory(path: str):
        del path
        return handle

    manager = MemoryReaderManager(
        logger=_Logger(),
        config=build_config(
            {
                "galgame": {"bridge_root": str(bridge_root)},
                "memory_reader": {
                    "enabled": True,
                    "textractor_path": str(textractor_path),
                    "hook_codes": ["/HQ14+3C@GameAssembly.dll#0x33A440"],
                    "auto_detect": True,
                },
            }
        ),
        process_factory=_process_factory,
        process_scanner=lambda: [
            DetectedGameProcess(
                pid=4242,
                name="SenrenBanka.exe",
                create_time=1709999999.0,
                engine="kirikiri",
                exe_path=str(tmp_path / "SenrenBanka.exe"),
                detection_reason="detected_kirikiri_xp3",
            )
        ],
        time_fn=lambda: 1710000000.0,
        platform_fn=lambda: True,
    )

    first = await manager.tick(bridge_sdk_available=False)

    assert handle.writes == ["attach -P4242\n"]
    assert first.runtime["hook_code_count"] == 0
    assert first.runtime["hook_code_detail"] == "hook_codes_skipped_for_engine"


@pytest.mark.asyncio
async def test_memory_reader_manager_sends_engine_hook_codes_for_unity(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    textractor_path = tmp_path / "TextractorCLI.exe"
    textractor_path.write_text("", encoding="utf-8")
    handle = _FakeTextractorHandle([])

    async def _process_factory(path: str):
        del path
        return handle

    manager = MemoryReaderManager(
        logger=_Logger(),
        config=build_config(
            {
                "galgame": {"bridge_root": str(bridge_root)},
                "memory_reader": {
                    "enabled": True,
                    "textractor_path": str(textractor_path),
                    "hook_codes": [],
                    "engine_hooks": {
                        "unity": ["/HQ14+3C@GameAssembly.dll#0x33A440"],
                        "kirikiri": [],
                    },
                    "auto_detect": True,
                },
            }
        ),
        process_factory=_process_factory,
        process_scanner=lambda: [
            DetectedGameProcess(
                pid=4242,
                name="UnityGame.exe",
                create_time=1709999999.0,
                engine="unity",
                detection_reason="detected_unity_module",
            )
        ],
        time_fn=lambda: 1710000000.0,
        platform_fn=lambda: True,
    )

    first = await manager.tick(bridge_sdk_available=False)

    assert handle.writes == [
        "attach -P4242\n",
        "/HQ14+3C@GameAssembly.dll#0x33A440 -P4242\n",
    ]
    assert first.runtime["hook_code_count"] == 1
    assert first.runtime["hook_code_detail"] == "hook_codes_sent"


@pytest.mark.asyncio
async def test_memory_reader_manual_target_rebounds_by_process_signature(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    textractor_path = tmp_path / "TextractorCLI.exe"
    textractor_path.write_text("", encoding="utf-8")
    exe_path = str(tmp_path / "SenrenBanka.exe")
    old_process = DetectedGameProcess(
        pid=100,
        name="SenrenBanka.exe",
        create_time=1709999900.0,
        engine="kirikiri",
        exe_path=exe_path,
        detection_reason="detected_kirikiri_xp3",
    )
    new_process = DetectedGameProcess(
        pid=200,
        name="SenrenBanka.exe",
        create_time=1710000000.0,
        engine="kirikiri",
        exe_path=exe_path,
        detection_reason="detected_kirikiri_xp3",
    )
    inventory = {"items": [old_process]}
    handle = _FakeTextractorHandle([])

    async def _process_factory(path: str):
        del path
        return handle

    manager = MemoryReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            textractor_path=str(textractor_path),
            auto_detect=True,
        ),
        process_factory=_process_factory,
        process_scanner=lambda: [],
        process_inventory_scanner=lambda: list(inventory["items"]),
        time_fn=lambda: 1710000000.0,
        platform_fn=lambda: True,
    )
    target = manager.resolve_manual_process_target(process_key=old_process.process_key)
    manager.update_process_target(target)
    inventory["items"] = [new_process]

    first = await manager.tick(bridge_sdk_available=False)

    assert first.runtime["pid"] == 200
    assert first.runtime["target_selection_mode"] == "manual"
    assert first.runtime["target_selection_detail"] == "manual_target_rebound"
    assert manager.current_process_target()["pid"] == 200
    assert handle.writes == ["attach -P200\n"]


@pytest.mark.asyncio
async def test_memory_reader_manager_marks_idle_after_text_on_heartbeat(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    textractor_path = tmp_path / "TextractorCLI.exe"
    textractor_path.write_text("", encoding="utf-8")
    handle = _FakeTextractorHandle(["[4242:100:0:0] first memory line"])
    clock = {"now": 1710000000.0}

    async def _process_factory(path: str):
        del path
        return handle

    manager = MemoryReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            textractor_path=str(textractor_path),
            auto_detect=True,
            poll_interval_seconds=0.5,
        ),
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
    )

    first = await manager.tick(bridge_sdk_available=False)
    assert first.runtime["detail"] == "receiving_text"
    text_seq = first.runtime["last_text_seq"]
    assert text_seq > 0

    clock["now"] += 1.0
    second = await manager.tick(bridge_sdk_available=False)

    assert second.runtime["detail"] == "attached_idle_after_text"
    assert second.runtime["last_text_seq"] == text_seq
    assert second.runtime["last_seq"] > text_seq

    await manager.shutdown()


@pytest.mark.asyncio
async def test_textractor_stdout_reader_logs_regular_exception() -> None:
    class _FailingStdout:
        def readline(self):
            raise RuntimeError("reader failed")

    class _FakeProcess:
        stdout = _FailingStdout()
        stdin = None
        returncode = 0

    logger = _CapturingLogger()
    handle = galgame_memory_reader._AsyncioTextractorHandle(
        _FakeProcess(),
        logger=logger,
    )

    assert await handle.readline(timeout=1.0) is None
    assert any(
        level == "warning"
        and args[0] == "memory_reader Textractor stdout reader failed: {}"
        for level, args in logger.messages
    )


@pytest.mark.asyncio
async def test_memory_reader_manager_marks_attached_without_text_when_only_textractor_logs_exist(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    textractor_path = tmp_path / "TextractorCLI.exe"
    textractor_path.write_text("", encoding="utf-8")
    handle = _FakeTextractorHandle(
        [
            "Textractor: attached to target process",
        ]
    )
    logger = _CapturingLogger()

    async def _process_factory(path: str):
        del path
        return handle

    manager = MemoryReaderManager(
        logger=logger,
        config=_make_config(
            bridge_root,
            enabled=True,
            textractor_path=str(textractor_path),
            auto_detect=True,
            poll_interval_seconds=0.5,
        ),
        process_factory=_process_factory,
        process_scanner=lambda: [
            DetectedGameProcess(
                pid=27452,
                name="TheLamentingGeese.exe",
                create_time=1709999999.0,
                engine="unity",
            )
        ],
        time_fn=lambda: 1710000000.0,
        platform_fn=lambda: True,
    )

    first = await manager.tick(bridge_sdk_available=False)

    assert first.runtime["status"] == "active"
    assert first.runtime["detail"] == "attached_no_text_yet"
    game_id = first.runtime["game_id"]
    events_path = bridge_root / game_id / "events.jsonl"
    events = tail_events_jsonl(events_path, offset=0, line_buffer=b"").events
    assert [event["type"] for event in events] == ["session_started"]
    assert any(level == "debug" and args[0] == "memory_reader Textractor log: {}" for level, args in logger.messages)

    await manager.shutdown()


def test_inspect_textractor_installation_reports_custom_install_target(tmp_path: Path) -> None:
    install_root = tmp_path / "TextractorCustom"
    executable = install_root / "TextractorCLI.exe"
    install_root.mkdir(parents=True, exist_ok=True)
    executable.write_text("", encoding="utf-8")

    status = inspect_textractor_installation(
        configured_path="",
        install_target_dir_raw=str(install_root),
        platform_fn=lambda: True,
    )

    assert status["install_supported"] is True
    assert status["installed"] is True
    assert status["detected_path"] == str(executable)
    assert status["target_dir"] == str(install_root)


@pytest.mark.asyncio
async def test_install_textractor_downloads_and_extracts_latest_release_zip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_root = tmp_path / "TextractorInstalled"
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "programfiles"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "programfiles_x86"))
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    archive_path = archive_root / "Textractor.zip"
    inner_dir = archive_root / "Textractor"
    inner_dir.mkdir()
    (inner_dir / "TextractorCLI.exe").write_text("stub", encoding="utf-8")
    (inner_dir / "Textractor.exe").write_text("stub", encoding="utf-8")

    import zipfile

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(inner_dir / "TextractorCLI.exe", arcname="Textractor/TextractorCLI.exe")
        archive.write(inner_dir / "Textractor.exe", arcname="Textractor/Textractor.exe")

    release_payload = {
        "name": "Textractor v1.0.0",
        "assets": [
            {
                "name": "Textractor-x64.zip",
                "browser_download_url": "https://example.test/Textractor-x64.zip",
            }
        ],
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.test/latest":
            return httpx.Response(200, json=release_payload)
        if str(request.url) == "https://example.test/Textractor-x64.zip":
            return httpx.Response(200, content=archive_path.read_bytes())
        raise AssertionError(f"unexpected request: {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        result = await install_textractor(
            logger=_Logger(),
            configured_path="",
            install_target_dir_raw=str(install_root),
            release_api_url="https://example.test/latest",
            timeout_seconds=5.0,
            platform_fn=lambda: True,
            client_factory=lambda: client,
        )
    finally:
        await client.aclose()

    assert result["installed"] is True
    assert result["already_installed"] is False
    assert result["asset_name"] == "Textractor-x64.zip"
    assert result["detected_path"] == str(install_root / "TextractorCLI.exe")
    assert (install_root / "TextractorCLI.exe").is_file()


@pytest.mark.asyncio
async def test_download_file_resumes_with_http_range(tmp_path: Path) -> None:
    destination = tmp_path / "Textractor.zip"
    original = b"abcdefghij"
    destination.write_bytes(original[:4])
    requests: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.headers.get("Range") or ""))
        if request.headers.get("Range") == "bytes=4-":
            return httpx.Response(
                206,
                content=original[4:],
                headers={
                    "Content-Range": "bytes 4-9/10",
                    "Content-Length": str(len(original[4:])),
                },
            )
        raise AssertionError(f"unexpected headers: {dict(request.headers)}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        result = await _download_file(
            client,
            url="https://example.test/Textractor.zip",
            destination=destination,
            timeout_seconds=5.0,
        )
    finally:
        await client.aclose()

    assert requests == ["bytes=4-"]
    assert result["resumed"] is True
    assert result["resume_from"] == 4
    assert result["total_bytes"] == 10
    assert destination.read_bytes() == original


@pytest.mark.asyncio
async def test_download_file_rejects_sha256_mismatch(tmp_path: Path) -> None:
    destination = tmp_path / "Textractor.zip"

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"bad archive")

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(RuntimeError, match="checksum mismatch"):
            await _download_file(
                client,
                url="https://example.test/Textractor.zip",
                destination=destination,
                timeout_seconds=5.0,
                expected_sha256="0" * 64,
            )
    finally:
        await client.aclose()

    assert not destination.exists()
