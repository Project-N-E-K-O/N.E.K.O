"""Capturing the battle: window lookup, the frame ring, and the tool result.

The security-critical test in here is the recall handle one. The game's
in-battle chat is inside the frame the model reads, so a teammate can type
anything into it. If the recall tool took a path fragment, that would be a
working chain from "teammate types a line" to "arbitrary local file is read
and shipped to the model provider".
"""

from __future__ import annotations

import base64
import ctypes
import sys
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_wows.domain.contracts import WowsConfig
from plugin.plugins.neko_wows.vision import capture as capture_module
from plugin.plugins.neko_wows.vision import store as store_module
from plugin.plugins.neko_wows.vision import tool as tool_module
from plugin.plugins.neko_wows.vision import window as window_module
from plugin.plugins.neko_wows.vision.store import ShotStore
from plugin.plugins.neko_wows.vision.tool import (
    REASON_CAPTURE_FAILED,
    REASON_DISABLED,
    REASON_RATE_LIMITED,
    REASON_SHOT_EXPIRED,
    SOURCE_FULLSCREEN,
    SOURCE_GAME_WINDOW,
    ScreenshotService,
    facts_to_telemetry,
)
from plugin.plugins.neko_wows.vision.window import GameWindow, find_game_window


# ===========================================================================
# window lookup
# ===========================================================================


def _candidate(**overrides) -> dict:
    entry = {
        "hwnd": 101,
        "title": "World of Warships",
        "minimized": False,
        "rect": (0, 0, 1920, 1080),
        "process_name": "WorldOfWarships64.exe",
        "exe_path": r"D:\Games\World_of_Warships\WorldOfWarships64.exe",
    }
    entry.update(overrides)
    return entry


@pytest.fixture
def fake_windows(monkeypatch):
    def _install(entries):
        monkeypatch.setattr(
            window_module, "_enumerate_candidates", lambda: list(entries),
        )
    return _install


def test_finds_the_game_window(fake_windows):
    fake_windows([_candidate()])
    found = find_game_window()
    assert isinstance(found, GameWindow)
    assert found.hwnd == 101
    assert found.width == 1920
    assert found.height == 1080


def test_ignores_other_processes(fake_windows):
    fake_windows([_candidate(process_name="chrome.exe")])
    assert find_game_window() is None


def test_process_match_is_case_insensitive(fake_windows):
    fake_windows([_candidate(process_name="worldofwarships.exe")])
    assert find_game_window() is not None


def test_minimized_game_is_not_a_window(fake_windows):
    """Its rectangle is off-screen garbage; the caller wants the fullscreen
    fallback instead."""
    fake_windows([_candidate(minimized=True)])
    assert find_game_window() is None


def test_degenerate_rectangle_is_rejected(fake_windows):
    fake_windows([_candidate(rect=(0, 0, 0, 0))])
    assert find_game_window() is None


def test_game_dir_cross_check_rejects_an_impostor(fake_windows):
    fake_windows([_candidate(exe_path=r"C:\Temp\WorldOfWarships64.exe")])
    assert find_game_window(game_dir=r"D:\Games\World_of_Warships") is None


def test_game_dir_cross_check_accepts_the_real_one(fake_windows):
    fake_windows([_candidate()])
    assert find_game_window(game_dir=r"D:\Games\World_of_Warships") is not None


def test_unreadable_exe_path_still_matches_on_process_name(fake_windows):
    """psutil raises AccessDenied on elevated processes. Refusing there would
    break capture for anyone running the game as admin."""
    fake_windows([_candidate(exe_path="")])
    assert find_game_window(game_dir=r"D:\Games\World_of_Warships") is not None


def test_missing_pywin32_is_just_no_window(monkeypatch):
    monkeypatch.setattr(window_module, "_enumerate_candidates", lambda: [])
    assert find_game_window() is None


def test_first_matching_window_wins(fake_windows):
    fake_windows([
        _candidate(process_name="explorer.exe"),
        _candidate(hwnd=202),
    ])
    assert find_game_window().hwnd == 202


# ===========================================================================
# capture backends
# ===========================================================================


@pytest.fixture
def game_window():
    return GameWindow(
        hwnd=101,
        left=10,
        top=20,
        right=810,
        bottom=620,
        title="World of Warships",
    )


def test_capture_jpeg_uses_mss_first(monkeypatch, game_window):
    calls = []
    frame = object()
    region = {"left": 10, "top": 20, "width": 800, "height": 600}

    def _grab_with_mss(actual_region):
        calls.append(("mss", actual_region))
        return frame

    monkeypatch.setattr(capture_module, "_grab_with_mss", _grab_with_mss)
    monkeypatch.setattr(
        capture_module,
        "_grab_with_printwindow",
        lambda _window: pytest.fail("PrintWindow should not run after mss succeeds"),
    )
    monkeypatch.setattr(
        capture_module,
        "_to_jpeg",
        lambda image: b"mss-jpeg" if image is frame else pytest.fail("wrong frame"),
    )

    assert capture_module.capture_jpeg(game_window) == b"mss-jpeg"
    assert calls == [("mss", region)]


def test_capture_jpeg_falls_back_to_printwindow(monkeypatch, game_window):
    calls = []
    printwindow_frame = object()

    def _grab_with_mss(_region):
        calls.append("mss")
        raise RuntimeError("desktop grab failed")

    def _grab_with_printwindow(window):
        calls.append("printwindow")
        assert window is game_window
        return printwindow_frame

    def _to_jpeg(image):
        calls.append("jpeg")
        assert image is printwindow_frame
        return b"printwindow-jpeg"

    monkeypatch.setattr(capture_module, "_grab_with_mss", _grab_with_mss)
    monkeypatch.setattr(
        capture_module, "_grab_with_printwindow", _grab_with_printwindow)
    monkeypatch.setattr(capture_module, "_to_jpeg", _to_jpeg)

    assert capture_module.capture_jpeg(game_window) == b"printwindow-jpeg"
    assert calls == ["mss", "printwindow", "jpeg"]


def test_capture_jpeg_returns_none_when_all_backends_fail(
    monkeypatch,
    game_window,
):
    calls = []

    def _fail(name):
        def _grab(_target):
            calls.append(name)
            raise RuntimeError(f"{name} failed")
        return _grab

    monkeypatch.setattr(capture_module, "_grab_with_mss", _fail("mss"))
    monkeypatch.setattr(
        capture_module, "_grab_with_printwindow", _fail("printwindow"))

    assert capture_module.capture_jpeg(game_window) is None
    assert calls == ["mss", "printwindow"]


def test_capture_jpeg_uses_primary_monitor_for_fullscreen(monkeypatch):
    calls = []
    frame = object()

    def _grab_with_mss(region):
        calls.append(region)
        return frame

    monkeypatch.setattr(capture_module, "_grab_with_mss", _grab_with_mss)
    monkeypatch.setattr(
        capture_module,
        "_grab_with_printwindow",
        lambda _window: pytest.fail("fullscreen capture has no window fallback"),
    )
    monkeypatch.setattr(
        capture_module,
        "_to_jpeg",
        lambda image: b"fullscreen-jpeg"
        if image is frame
        else pytest.fail("wrong frame"),
    )

    assert capture_module.capture_jpeg(None) == b"fullscreen-jpeg"
    assert calls == [None]


def test_printwindow_failure_releases_every_gdi_resource(monkeypatch, game_window):
    calls = []
    previous_bitmap = object()

    class _Bitmap:
        def CreateCompatibleBitmap(self, _source_dc, width, height):
            calls.append(f"create_bitmap:{width}x{height}")

        def GetHandle(self):
            return 123

    bitmap = _Bitmap()

    class _SaveDc:
        def SelectObject(self, selected):
            if selected is previous_bitmap:
                calls.append("restore_bitmap")
                return bitmap
            calls.append("select_bitmap")
            return previous_bitmap

        def GetSafeHdc(self):
            return 456

        def DeleteDC(self):
            calls.append("save_dc_delete")

    save_dc = _SaveDc()

    class _MfcDc:
        def CreateCompatibleDC(self):
            return save_dc

        def DeleteDC(self):
            calls.append("mfc_dc_delete")

    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            GetWindowDC=lambda _hwnd: 789,
            DeleteObject=lambda _handle: calls.append("delete_object"),
            ReleaseDC=lambda _hwnd, _hdc: calls.append("release_dc"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32ui",
        SimpleNamespace(
            CreateDCFromHandle=lambda _hdc: _MfcDc(),
            CreateBitmap=lambda: bitmap,
        ),
    )
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace())
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(
            user32=SimpleNamespace(
                PrintWindow=lambda *_args: calls.append("printwindow") or 0,
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="PrintWindow returned 0"):
        capture_module._grab_with_printwindow(game_window)

    assert calls == [
        "create_bitmap:800x600",
        "select_bitmap",
        "printwindow",
        "restore_bitmap",
        "save_dc_delete",
        "delete_object",
        "release_dc",
    ]


# ===========================================================================
# the frame ring
# ===========================================================================


def test_save_and_load_round_trip(tmp_path):
    store = ShotStore(tmp_path, retain=5)
    record = store.save(b"jpegbytes")
    assert record is not None
    assert store.load(record.shot_id) == b"jpegbytes"


def test_oldest_frames_are_evicted(tmp_path):
    store = ShotStore(tmp_path, retain=2)
    first = store.save(b"a")
    store.save(b"b")
    third = store.save(b"c")

    assert store.load(first.shot_id) is None
    assert store.load(third.shot_id) == b"c"
    assert len(list(tmp_path.glob("*.jpg"))) == 2


def test_evicted_frame_leaves_no_file_behind(tmp_path):
    store = ShotStore(tmp_path, retain=1)
    first = store.save(b"a")
    store.save(b"b")
    assert not first.path.exists()


def test_recent_is_newest_first(tmp_path):
    store = ShotStore(tmp_path, retain=5)
    store.save(b"a")
    second = store.save(b"b")
    assert store.recent(2)[0].shot_id == second.shot_id


def test_clear_empties_the_directory(tmp_path):
    store = ShotStore(tmp_path, retain=5)
    store.save(b"a")
    store.save(b"b")
    assert store.clear() == 2
    assert list(tmp_path.glob("*.jpg")) == []
    assert store.recent(5) == []


def test_lowering_retain_evicts_immediately(tmp_path):
    store = ShotStore(tmp_path, retain=5)
    for _ in range(5):
        store.save(b"x")
    store.apply_retain(2)
    assert len(list(tmp_path.glob("*.jpg"))) == 2


@pytest.mark.parametrize("probe", [
    "../../../../Windows/System32/config/SAM",
    r"..\..\secrets.txt",
    "C:/Windows/win.ini",
    r"C:\Windows\win.ini",
    "/etc/passwd",
    "a/b",
    "shot_1/../shot_2",
    "shot_1.jpg",
    "shot_",
    "shot_abc",
    "",
    None,
    123,
    ["shot_1"],
])
def test_load_refuses_anything_that_is_not_a_minted_handle(tmp_path, probe):
    store = ShotStore(tmp_path, retain=5)
    store.save(b"real")
    assert store.load(probe) is None


def test_load_never_touches_the_filesystem_for_a_bad_handle(tmp_path, monkeypatch):
    """Belt and braces: prove the rejection happens before any path use, not
    just that the result is None."""
    store = ShotStore(tmp_path, retain=5)
    store.save(b"real")

    def _explode(*args, **kwargs):
        raise AssertionError("a rejected handle must not reach the filesystem")

    monkeypatch.setattr(store_module.Path, "read_bytes", _explode)
    assert store.load("../../etc/passwd") is None


def test_a_valid_looking_but_unknown_handle_is_refused(tmp_path):
    store = ShotStore(tmp_path, retain=5)
    store.save(b"real")
    assert store.load("shot_9999") is None


# ===========================================================================
# the tool result
# ===========================================================================


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def service(tmp_path, monkeypatch):
    """A service whose capture and window lookup are under test control."""
    state = {"jpeg": b"framebytes", "window": None}

    monkeypatch.setattr(tool_module, "find_game_window", lambda _dir: state["window"])
    monkeypatch.setattr(tool_module, "capture_jpeg", lambda _win: state["jpeg"])

    cfg = WowsConfig()
    cfg.screenshot_enabled = True
    cfg.screenshot_min_interval_seconds = 15.0
    cfg.screenshot_retain_count = 5

    clock = _Clock()
    svc = ScreenshotService(
        cfg,
        ShotStore(tmp_path, retain=5),
        lambda: {"in_battle": True, "own_hp_ratio": 0.42},
        clock=clock,
    )
    svc.state = state
    svc.clock = clock
    return svc


def test_disabled_service_returns_disabled_and_writes_nothing(tmp_path):
    cfg = WowsConfig()
    svc = ScreenshotService(cfg, ShotStore(tmp_path, retain=5), lambda: {})
    out = svc.look()
    assert out["output"]["reason"] == REASON_DISABLED
    assert "images" not in out
    assert list(tmp_path.glob("*.jpg")) == []


def test_successful_look_returns_a_frame_and_telemetry(service):
    out = service.look()
    assert out["output"]["ok"] is True
    assert out["output"]["telemetry"] == {"in_battle": True, "own_hp_ratio": 0.42}
    assert out["output"]["source"] == SOURCE_FULLSCREEN
    image = out["images"][0]
    assert base64.b64decode(image["data_b64"]) == b"framebytes"
    assert image["mime"] == "image/jpeg"
    assert image["vision_prompt"] == tool_module.WOWS_VISION_PROMPT


def test_look_reports_the_game_window_as_the_source(service):
    service.state["window"] = GameWindow(
        hwnd=1, left=0, top=0, right=800, bottom=600, title="World of Warships",
    )
    out = service.look()
    assert out["output"]["source"] == SOURCE_GAME_WINDOW
    assert out["output"]["window_title"] == "World of Warships"


def test_the_result_tells_the_model_how_to_look_again(service):
    out = service.look()
    assert out["output"]["shot_id"] in out["output"]["recall_hint"]
    assert "wows_recall_screenshot" in out["output"]["recall_hint"]


def test_second_look_inside_the_window_is_rate_limited(service):
    service.look()
    out = service.look()
    assert out["output"]["reason"] == REASON_RATE_LIMITED
    assert out["output"]["retry_after_seconds"] == pytest.approx(15.0)
    assert "images" not in out


def test_rate_limited_result_still_carries_telemetry(service):
    """She should be able to answer from the numbers rather than stall."""
    service.look()
    out = service.look()
    assert out["output"]["telemetry"]["own_hp_ratio"] == 0.42


def test_cooldown_expires(service):
    service.look()
    service.clock.advance(15.1)
    assert service.look()["output"]["ok"] is True


def test_a_failed_capture_does_not_start_the_cooldown(service):
    """Otherwise one transient failure locks her out for the whole interval."""
    service.state["jpeg"] = None
    assert service.look()["output"]["reason"] == REASON_CAPTURE_FAILED
    service.state["jpeg"] = b"framebytes"
    assert service.look()["output"]["ok"] is True


def test_capture_failure_reports_a_reason_not_an_exception(service):
    service.state["jpeg"] = None
    out = service.look()
    assert out["output"]["ok"] is False
    assert out["output"]["reason"] == REASON_CAPTURE_FAILED
    assert out["is_error"] is False


def test_broken_telemetry_does_not_fail_the_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_module, "find_game_window", lambda _dir: None)
    monkeypatch.setattr(tool_module, "capture_jpeg", lambda _win: b"frame")

    def _boom():
        raise RuntimeError("transport down")

    cfg = WowsConfig()
    cfg.screenshot_enabled = True
    svc = ScreenshotService(cfg, ShotStore(tmp_path, retain=5), _boom)
    out = svc.look()
    assert out["output"]["ok"] is True
    assert out["output"]["telemetry"]["in_battle"] is False


def test_recall_returns_the_stored_frame(service):
    shot_id = service.look()["output"]["shot_id"]
    out = service.recall(shot_id)
    assert out["output"]["recalled"] is True
    assert base64.b64decode(out["images"][0]["data_b64"]) == b"framebytes"


def test_recall_is_not_rate_limited(service):
    """It captures nothing new; making her wait to re-read a file on disk
    would be pure friction."""
    shot_id = service.look()["output"]["shot_id"]
    assert service.recall(shot_id)["output"]["ok"] is True


def test_recall_of_an_unknown_handle_says_so(service):
    service.look()
    out = service.recall("shot_9999")
    assert out["output"]["reason"] == REASON_SHOT_EXPIRED
    assert "images" not in out


def test_recall_lists_what_is_still_available(service):
    shot_id = service.look()["output"]["shot_id"]
    out = service.recall("shot_9999")
    assert shot_id in out["output"]["available"]


def test_recall_refuses_a_path(service):
    service.look()
    out = service.recall("../../../etc/passwd")
    assert out["output"]["reason"] == REASON_SHOT_EXPIRED
    assert "images" not in out


def test_recall_is_disabled_with_the_feature(service):
    shot_id = service.look()["output"]["shot_id"]
    service.cfg.screenshot_enabled = False
    assert service.recall(shot_id)["output"]["reason"] == REASON_DISABLED


def test_status_reports_the_ring_for_the_panel(service):
    service.look()
    status = service.status()
    assert status["enabled"] is True
    assert len(status["recent"]) == 1
    assert status["cooldown_remaining_seconds"] == pytest.approx(15.0)


def test_apply_config_retunes_the_ring(service, tmp_path):
    for _ in range(4):
        service.clock.advance(20)
        service.look()
    service.cfg.screenshot_retain_count = 2
    service.apply_config(service.cfg)
    assert len(service.status()["recent"]) == 2


# ===========================================================================
# telemetry flattening
# ===========================================================================


def test_no_facts_means_not_in_battle():
    assert facts_to_telemetry(None) == {"in_battle": False}


def test_facts_are_flattened_and_rounded():
    from plugin.plugins.neko_wows.domain.facts import ThreatBearing, WowsFacts
    from plugin.plugins.neko_wows.domain.snapshot import Ship

    facts = WowsFacts(
        own_hp_ratio=0.4237,
        own_health=21456.7,
        alive_allies=5,
        alive_enemies=7,
        nearest_enemy=ThreatBearing(
            ship=Ship(name="Yamato"), distance_m=8123.4, bearing_deg=47.6,
        ),
    )
    telemetry = facts_to_telemetry(facts)
    assert telemetry["in_battle"] is True
    assert telemetry["own_hp_ratio"] == 0.424
    assert telemetry["own_health"] == 21457
    assert telemetry["alive_allies"] == 5
    assert telemetry["nearest_enemy"] == {
        "name": "Yamato", "distance_m": 8123, "bearing_deg": 48,
    }


def test_absent_fields_are_omitted_rather_than_sent_as_null():
    from plugin.plugins.neko_wows.domain.facts import WowsFacts

    telemetry = facts_to_telemetry(WowsFacts())
    assert "own_hp_ratio" not in telemetry
    assert "nearest_enemy" not in telemetry
    assert telemetry == {"in_battle": True}


# ===========================================================================
# plugin wiring
# ===========================================================================


def _declared_tools():
    from plugin.sdk.plugin.llm_tool import collect_llm_tool_methods
    from plugin.plugins.neko_wows import NekoWowsPlugin

    plugin = object.__new__(NekoWowsPlugin)
    return {meta.name: meta for meta, _method in collect_llm_tool_methods(plugin)}


def test_both_screenshot_tools_are_declared():
    names = _declared_tools()
    assert "wows_look_at_battle" in names
    assert "wows_recall_screenshot" in names


def test_look_takes_no_arguments():
    """She only decides whether to look, never what to look at — a parameter
    would just be something for the model to get wrong."""
    meta = _declared_tools()["wows_look_at_battle"]
    assert meta.parameters == {"type": "object", "properties": {}}


def test_recall_requires_a_handle_and_nothing_else():
    meta = _declared_tools()["wows_recall_screenshot"]
    assert meta.parameters["required"] == ["shot_id"]
    assert meta.parameters["additionalProperties"] is False
    assert set(meta.parameters["properties"]) == {"shot_id"}


def test_recall_schema_does_not_invite_a_path():
    """The description steers the model; a hint like 'file path' here would
    be actively harmful even though the store would reject it."""
    meta = _declared_tools()["wows_recall_screenshot"]
    blob = (meta.description + str(meta.parameters)).lower()
    for word in ("path", "路径", "文件名", "filename", "directory"):
        assert word not in blob


@pytest.mark.asyncio
async def test_look_tool_is_inert_while_disabled(tmp_path, monkeypatch):
    from plugin.plugins.neko_wows import NekoWowsPlugin
    from plugin.plugins.neko_wows.vision.store import ShotStore

    def _never():
        raise AssertionError("a disabled tool must not capture anything")

    monkeypatch.setattr(tool_module, "capture_jpeg", lambda _win: _never())

    plugin = object.__new__(NekoWowsPlugin)
    plugin.cfg = WowsConfig()
    plugin.shots = ShotStore(tmp_path, retain=5)
    plugin.screenshots = ScreenshotService(plugin.cfg, plugin.shots, lambda: {})

    out = await NekoWowsPlugin.wows_look_at_battle(plugin)

    assert out["output"]["reason"] == REASON_DISABLED
    assert list(tmp_path.glob("*.jpg")) == []


@pytest.mark.asyncio
async def test_look_tool_returns_the_callback_envelope(tmp_path, monkeypatch):
    """The shape has to be exactly what ``llm_tool_callback`` recognizes as an
    envelope, or the images get wrapped as data and never reach the model."""
    from plugin.plugins.neko_wows import NekoWowsPlugin
    from plugin.plugins.neko_wows.vision.store import ShotStore

    monkeypatch.setattr(tool_module, "find_game_window", lambda _dir: None)
    monkeypatch.setattr(tool_module, "capture_jpeg", lambda _win: b"frame")

    cfg = WowsConfig()
    cfg.screenshot_enabled = True
    plugin = object.__new__(NekoWowsPlugin)
    plugin.cfg = cfg
    plugin.shots = ShotStore(tmp_path, retain=5)
    plugin.screenshots = ScreenshotService(cfg, plugin.shots, lambda: {"in_battle": False})

    out = await NekoWowsPlugin.wows_look_at_battle(plugin)

    assert "output" in out and "images" in out
    assert out["is_error"] is False
    assert out["images"][0]["mime"] == "image/jpeg"


@pytest.mark.asyncio
async def test_recall_tool_reaches_the_store(tmp_path, monkeypatch):
    from plugin.plugins.neko_wows import NekoWowsPlugin
    from plugin.plugins.neko_wows.vision.store import ShotStore

    monkeypatch.setattr(tool_module, "find_game_window", lambda _dir: None)
    monkeypatch.setattr(tool_module, "capture_jpeg", lambda _win: b"frame")

    cfg = WowsConfig()
    cfg.screenshot_enabled = True
    plugin = object.__new__(NekoWowsPlugin)
    plugin.cfg = cfg
    plugin.shots = ShotStore(tmp_path, retain=5)
    plugin.screenshots = ScreenshotService(cfg, plugin.shots, lambda: {})

    shot_id = (await NekoWowsPlugin.wows_look_at_battle(plugin))["output"]["shot_id"]
    out = await NekoWowsPlugin.wows_recall_screenshot(plugin, shot_id=shot_id)

    assert base64.b64decode(out["images"][0]["data_b64"]) == b"frame"
