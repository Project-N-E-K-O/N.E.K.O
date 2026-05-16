from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

import plugin.plugins.galgame_plugin as galgame_plugin_module
import plugin.plugins.galgame_plugin.game_llm_agent as game_llm_agent_module
from plugin.plugins.galgame_plugin import local_input_actuator as local_input
from plugin.plugins.galgame_plugin import ocr_reader as galgame_ocr_reader
from plugin.plugins.galgame_plugin import service as galgame_service
from plugin.plugins.galgame_plugin.host_agent_adapter import (
    HostAgentAdapter,
    HostAgentError,
    _tls_verify_for_base_url,
)
from plugin.plugins.galgame_plugin.llm_gateway import (
    LLMGateway,
    _LLM_RESPONSE_CACHE_MAX_ITEMS,
)
from plugin.plugins.galgame_plugin.memory_reader import (
    compute_memory_reader_game_id,
    DetectedGameProcess,
    MemoryReaderBridgeWriter,
    MemoryReaderManager,
)
from plugin.plugins.galgame_plugin.models import (
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_ASPECT_NEAREST,
    OCR_CAPTURE_PROFILE_MATCH_SOURCE_BUCKET_EXACT,
    OCR_CAPTURE_PROFILE_SAVE_SCOPE_PROCESS_FALLBACK,
    OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET,
    DATA_SOURCE_BRIDGE_SDK,
    DATA_SOURCE_MEMORY_READER,
    DATA_SOURCE_OCR_READER,
    OCR_CAPTURE_PROFILE_STAGE_CONFIG,
    OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
    OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    OCR_CAPTURE_PROFILE_STAGE_GAME_OVER,
    OCR_CAPTURE_PROFILE_STAGE_MENU,
    OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
    OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
    OCR_CAPTURE_PROFILE_STAGE_TITLE,
    OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
    OCR_CAPTURE_PROFILE_WINDOW_BUCKETS_KEY,
    STORE_OCR_CAPTURE_PROFILES,
    STORE_OCR_WINDOW_TARGET,
    build_ocr_capture_profile_bucket_key,
)
from plugin.plugins.galgame_plugin.ocr_reader import (
    DetectedGameWindow,
    OcrReaderBridgeWriter,
    OcrReaderManager,
    _coerce_aihong_menu_choices,
    _looks_like_aihong_menu_status_only_text,
    _looks_like_noise_ocr_text,
)
from plugin.plugins.galgame_plugin.reader import (
    expand_bridge_root,
    read_session_json,
    tail_events_jsonl,
)
from plugin.plugins.galgame_plugin.service import (
    _default_bridge_root_raw,
    build_config,
    build_explain_context,
    build_suggest_context,
    build_summarize_context,
    resolve_effective_current_line,
)
from plugin.sdk.plugin import Err, Ok

GalgameBridgePlugin = galgame_plugin_module.GalgameBridgePlugin
GameLLMAgent = game_llm_agent_module.GameLLMAgent

_PLUGIN_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "galgame_plugin"


@pytest.fixture(autouse=True)
def _isolate_galgame_runtime_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "runtime_data"))
    monkeypatch.delenv("NEKO_STORAGE_ANCHOR_ROOT", raising=False)


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


class _Ctx:
    plugin_id = "galgame_plugin"
    metadata = {}
    bus = None

    def __init__(self, plugin_dir: Path, effective_config: dict[str, object]) -> None:
        self.logger = _Logger()
        self.config_path = plugin_dir / "plugin.toml"
        self._effective_config = {
            "plugin": {"store": {"enabled": True}, "database": {"enabled": False}},
            "plugin_state": {"backend": "memory"},
        }
        self._config = effective_config
        self.pushed_messages: list[dict[str, object]] = []
        self.entry_calls: list[dict[str, object]] = []
        self.entry_handler = None

    async def get_own_config(self, timeout: float = 5.0):
        return {"config": self._config}

    async def get_own_base_config(self, timeout: float = 5.0):
        return {"config": self._config}

    async def get_own_profiles_state(self, timeout: float = 5.0):
        return {"profiles": [], "active": None}

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
        return {"profile_name": profile_name, "config": self._config}

    async def get_own_effective_config(self, profile_name: str | None = None, timeout: float = 5.0):
        return {"config": self._config}

    async def update_own_config(self, updates, timeout: float = 10.0):
        self._config = dict(self._config)
        self._config.update(dict(updates or {}))
        return {"config": self._config}

    async def query_plugins(self, filters, timeout: float = 5.0):
        return {"plugins": []}

    async def trigger_plugin_event(self, **kwargs):
        self.entry_calls.append(dict(kwargs))
        if self.entry_handler is None:
            raise RuntimeError("no fake trigger_plugin_event configured")
        handler = self.entry_handler
        if callable(handler):
            result = handler(**kwargs)
            if hasattr(result, "__await__"):
                return await result
            return result
        return handler

    async def get_system_config(self, timeout: float = 5.0):
        return {}

    async def query_memory(self, bucket_id: str, query: str, timeout: float = 5.0):
        return {"items": []}

    async def run_update(self, **kwargs):
        return {"ok": True}

    async def export_push(self, **kwargs):
        return {"ok": True}

    async def finish(self, **kwargs):
        return {"ok": True}

    def push_message(self, **kwargs):
        self.pushed_messages.append(dict(kwargs))
        return {"ok": True}

    def update_status(self, status):
        return None


def _session_state(
    *,
    speaker: str = "",
    text: str = "",
    choices: list[dict[str, object]] | None = None,
    scene_id: str = "boot",
    line_id: str = "",
    route_id: str = "",
    is_menu_open: bool = False,
    screen_type: str = "",
    screen_ui_elements: list[dict[str, object]] | None = None,
    screen_confidence: float = 0.0,
    ts: str = "2026-04-21T08:30:00Z",
) -> dict[str, object]:
    return {
        "speaker": speaker,
        "text": text,
        "choices": list(choices or []),
        "scene_id": scene_id,
        "line_id": line_id,
        "route_id": route_id,
        "is_menu_open": is_menu_open,
        "save_context": {
            "kind": "unknown",
            "slot_id": "",
            "display_name": "",
        },
        "screen_type": screen_type,
        "screen_ui_elements": list(screen_ui_elements or []),
        "screen_confidence": screen_confidence,
        "ts": ts,
    }


def _session(
    *,
    game_id: str,
    session_id: str,
    last_seq: int,
    state: dict[str, object],
    started_at: str = "2026-04-21T08:30:00Z",
) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "game_id": game_id,
        "game_title": game_id,
        "engine": "renpy",
        "session_id": session_id,
        "started_at": started_at,
        "last_seq": last_seq,
        "locale": "ja-JP",
        "bridge_sdk_version": "1.0.0",
        "state": state,
    }


def _event(
    *,
    seq: int,
    event_type: str,
    session_id: str,
    game_id: str,
    payload: dict[str, object],
    ts: str,
) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "seq": seq,
        "ts": ts,
        "type": event_type,
        "session_id": session_id,
        "game_id": game_id,
        "payload": payload,
    }


def _write_session(path: Path, payload: dict[str, object], *, bom: bool = False) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if bom:
        text = "\ufeff" + text
    path.write_text(text, encoding="utf-8")


def _write_events(
    path: Path,
    events: list[dict[str, object]],
    *,
    trailing: bytes = b"",
    crlf: bool = False,
) -> int:
    line_end = b"\r\n" if crlf else b"\n"
    data = b"".join(
        json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + line_end
        for event in events
    )
    data += trailing
    path.write_bytes(data)
    return len(data)


def _make_plugin_dirs(tmp_path: Path) -> tuple[Path, Path]:
    plugin_dir = tmp_path / "plugin_cfg"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.toml").write_text("", encoding="utf-8")
    static_dir = plugin_dir / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><title>ui</title>", encoding="utf-8")
    bridge_root = tmp_path / "bridge_root"
    bridge_root.mkdir()
    return plugin_dir, bridge_root


def _clear_bridge_root(bridge_root: Path) -> None:
    for child in bridge_root.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _copy_bridge_fixture_scenario(bridge_root: Path, scenario: str) -> Path:
    scenario_root = _PLUGIN_FIXTURE_ROOT / scenario
    if not scenario_root.is_dir():
        raise AssertionError(f"missing bridge fixture scenario: {scenario}")
    copied_game_dir: Path | None = None
    for child in scenario_root.iterdir():
        target = bridge_root / child.name
        if child.is_dir():
            shutil.copytree(child, target)
            copied_game_dir = target
        else:
            shutil.copy2(child, target)
    if copied_game_dir is None:
        raise AssertionError(f"bridge fixture scenario is empty: {scenario}")
    return copied_game_dir


def _make_effective_config(bridge_root: Path, **overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "galgame": {
            "bridge_root": str(bridge_root),
            "active_poll_interval_seconds": 0.1,
            "idle_poll_interval_seconds": 0.1,
            "stale_after_seconds": 0.2,
            "history_events_limit": 500,
            "history_lines_limit": 200,
            "history_choices_limit": 50,
            "dedupe_window_limit": 64,
            "warmup_replay_bytes_limit": 65536,
            "warmup_replay_events_limit": 50,
            "default_mode": "companion",
            "push_notifications": True,
        },
        "llm": {
            "llm_call_timeout_seconds": 15,
            "llm_max_in_flight": 2,
            "llm_request_cache_ttl_seconds": 2,
            "target_entry_ref": "",
        },
        "memory_reader": {
            "enabled": False,
            "textractor_path": "",
            "auto_detect": True,
            "poll_interval_seconds": 1,
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            merged = dict(config[key])  # type: ignore[index]
            merged.update(value)
            config[key] = merged
        else:
            config[key] = value
    return config


def _enable_injected_ocr_reader(plugin: GalgameBridgePlugin, *, trigger_mode: str) -> None:
    assert plugin._cfg is not None
    plugin._cfg.ocr_reader_enabled = True
    plugin._cfg.ocr_reader_trigger_mode = trigger_mode


def _create_game_dir(
    bridge_root: Path,
    *,
    game_id: str,
    session_payload: dict[str, object],
    events: list[dict[str, object]] | None = None,
) -> Path:
    game_dir = bridge_root / game_id
    game_dir.mkdir(parents=True, exist_ok=True)
    _write_session(game_dir / "session.json", session_payload)
    _write_events(game_dir / "events.jsonl", events or [])
    return game_dir


def _shared_state(
    *,
    mode: str = "choice_advisor",
    push_notifications: bool = True,
    connection_state: str = "active",
    stream_reset_pending: bool = False,
    game_id: str = "demo.alpha",
    session_id: str = "sess-a",
    last_seq: int = 2,
    snapshot: dict[str, object] | None = None,
    history_lines: list[dict[str, object]] | None = None,
    history_observed_lines: list[dict[str, object]] | None = None,
    history_choices: list[dict[str, object]] | None = None,
    history_events: list[dict[str, object]] | None = None,
    active_data_source: str | None = None,
    ocr_reader_runtime: dict[str, object] | None = None,
    memory_reader_runtime: dict[str, object] | None = None,
) -> dict[str, object]:
    snapshot_value = snapshot or _session_state(
        speaker="雪乃",
        text="当前台词",
        scene_id="scene-a",
        line_id="line-1",
        ts="2026-04-21T08:30:02Z",
    )
    shared = {
        "mode": mode,
        "push_notifications": push_notifications,
        "current_connection_state": connection_state,
        "stream_reset_pending": stream_reset_pending,
        "active_game_id": game_id,
        "active_session_id": session_id,
        "last_seq": last_seq,
        "latest_snapshot": snapshot_value,
        "history_events": list(history_events or []),
        "history_lines": list(history_lines or []),
        "history_observed_lines": list(history_observed_lines or []),
        "history_choices": list(history_choices or []),
        "screen_type": str(snapshot_value.get("screen_type") or ""),
        "screen_ui_elements": list(snapshot_value.get("screen_ui_elements") or []),
        "screen_confidence": float(snapshot_value.get("screen_confidence") or 0.0),
        "ocr_reader_runtime": dict(ocr_reader_runtime or {}),
        "memory_reader_runtime": dict(memory_reader_runtime or {}),
    }
    if active_data_source is not None:
        shared["active_data_source"] = active_data_source
    return shared


class _FakeHostAdapter:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.started: list[str] = []
        self.cancelled: list[str] = []
        self.tasks: dict[str, dict[str, object]] = {}
        self._counter = 0

    async def get_computer_use_availability(self, *, timeout: float = 1.5):
        if self.ready:
            return {"ready": True, "reasons": []}
        return {"ready": False, "reasons": ["computer_use unavailable"]}

    async def run_computer_use_instruction(self, instruction: str, *, lanlan_name: str = "", timeout: float = 5.0):
        self._counter += 1
        task_id = f"task-{self._counter}"
        self.started.append(instruction)
        self.tasks[task_id] = {"id": task_id, "status": "running", "result": None}
        return {"task_id": task_id, "status": "running"}

    async def get_task(self, task_id: str, *, timeout: float = 2.0):
        return dict(self.tasks[task_id])

    async def cancel_task(self, task_id: str, *, timeout: float = 5.0):
        self.cancelled.append(task_id)
        self.tasks[task_id] = {"id": task_id, "status": "cancelled", "error": "Cancelled by test"}
        return {"success": True, "task_id": task_id, "status": "cancelled"}

    async def shutdown(self) -> None:
        return None


class _FakeLLMGateway:
    def __init__(
        self,
        *,
        suggest_payload: dict[str, object] | None = None,
        reply_payload: dict[str, object] | None = None,
        summarize_payload: dict[str, object] | None = None,
        delay: float = 0.0,
        summary_delay: float = 0.0,
    ) -> None:
        self.suggest_payload = suggest_payload or {"degraded": True, "choices": [], "diagnostic": "no llm"}
        self.reply_payload = reply_payload or {"degraded": True, "reply": "fallback", "diagnostic": "no llm"}
        self.summarize_payload = summarize_payload or {
            "degraded": True,
            "summary": "",
            "diagnostic": "no llm",
        }
        self.delay = delay
        self.summary_delay = summary_delay
        self.suggest_calls: list[dict[str, object]] = []
        self.reply_calls: list[dict[str, object]] = []
        self.summarize_calls: list[dict[str, object]] = []

    async def suggest_choice(self, context: dict[str, object]):
        self.suggest_calls.append(dict(context))
        if self.delay:
            await asyncio.sleep(self.delay)
        return dict(self.suggest_payload)

    async def agent_reply(self, context: dict[str, object]):
        self.reply_calls.append(dict(context))
        if self.delay:
            await asyncio.sleep(self.delay)
        return dict(self.reply_payload)

    async def summarize_scene(self, context: dict[str, object]):
        self.summarize_calls.append(dict(context))
        if self.summary_delay:
            await asyncio.sleep(self.summary_delay)
        return dict(self.summarize_payload)


class _BlockingSummaryGateway(_FakeLLMGateway):
    def __init__(self) -> None:
        super().__init__()
        self.summary_started = asyncio.Event()
        self.release_summary = asyncio.Event()

    async def summarize_scene(self, context: dict[str, object]):
        self.summarize_calls.append(dict(context))
        self.summary_started.set()
        await self.release_summary.wait()
        scene_id = str(context.get("scene_id") or "unknown")
        return {"degraded": False, "summary": f"llm summary for {scene_id}", "diagnostic": ""}


def _run_in_new_loop(awaitable):
    with asyncio.Runner() as runner:
        return runner.run(awaitable)


async def _drain_agent_summary_tasks(agent: GameLLMAgent) -> None:
    for _ in range(4):
        tasks = list(agent._summary_tasks)
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)


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


class _FakeCaptureBackend:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.capture_calls = 0

    def is_available(self) -> bool:
        return self.available

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}:{target.pid}"

    def capture_frame(self, target: DetectedGameWindow, profile) -> str:
        del profile
        self.capture_calls += 1
        return f"frame:{target.hwnd}"


class _FakeBackgroundHashFrame:
    def __init__(self, background_hash: str) -> None:
        self.info = {"galgame_source_background_hash": background_hash}


class _FakeBackgroundHashCaptureBackend:
    def __init__(self, hashes: list[str]) -> None:
        self.hashes = list(hashes)
        self.capture_calls = 0

    def is_available(self) -> bool:
        return True

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}:{target.pid}"

    def capture_frame(self, target: DetectedGameWindow, profile) -> _FakeBackgroundHashFrame:
        del target, profile
        self.capture_calls += 1
        if not self.hashes:
            return _FakeBackgroundHashFrame("")
        if len(self.hashes) == 1:
            return _FakeBackgroundHashFrame(self.hashes[0])
        return _FakeBackgroundHashFrame(self.hashes.pop(0))


class _FakePrintWindowBlankCaptureBackend:
    def __init__(self) -> None:
        self.capture_calls = 0

    def is_available(self) -> bool:
        return True

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}:{target.pid}"

    def capture_frame(self, target: DetectedGameWindow, profile):
        del target, profile
        from PIL import Image

        self.capture_calls += 1
        frame = Image.new("RGB", (24, 24), (0, 0, 0))
        frame.info["galgame_capture_backend_kind"] = "printwindow"
        frame.info["galgame_capture_backend_detail"] = "selected"
        return frame


class _FakeOcrBackend:
    def __init__(self, texts: list[str] | None = None) -> None:
        self._texts = list(texts or [])

    def is_available(self) -> bool:
        return True

    def extract_text(self, image: str) -> str:
        del image
        if not self._texts:
            return ""
        if len(self._texts) == 1:
            return self._texts[0]
        return self._texts.pop(0)


class _NoopMemoryReaderManager:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        self.target: dict[str, object] = {}

    def update_process_target(self, target: dict[str, object]) -> None:
        self.target = dict(target or {})

    def current_process_target(self) -> dict[str, object]:
        return dict(self.target)

    def update_config(self, config) -> None:
        del config

    async def tick(self, **kwargs) -> SimpleNamespace:
        del kwargs
        return SimpleNamespace(
            warnings=[],
            should_rescan=False,
            runtime={
                "enabled": True,
                "status": "disabled",
                "detail": "test_noop_memory_reader",
            },
        )

    async def shutdown(self) -> None:
        return None


class _FakeAdvanceInputMonitor:
    def __init__(self, events: list[Any] | None = None) -> None:
        self.events = list(events or [])
        self.running = True

    def ensure_running(self) -> bool:
        self.running = True
        return True

    def is_running(self) -> bool:
        return self.running

    def last_seq(self) -> int:
        return max((int(getattr(event, "seq", 0) or 0) for event in self.events), default=0)

    def events_after(self, seq: int) -> list[Any]:
        self.ensure_running()
        return [event for event in self.events if int(getattr(event, "seq", 0) or 0) > seq]


class _FakeImage:
    def __init__(self, size: tuple[int, int], *, crop_box: tuple[int, int, int, int] | None = None) -> None:
        self.size = size
        self.crop_box = crop_box or (0, 0, size[0], size[1])

    def crop(self, box: tuple[int, int, int, int]):
        return _FakeImage(
            (max(0, box[2] - box[0]), max(0, box[3] - box[1])),
            crop_box=box,
        )


class _FakeImageCaptureBackend:
    def __init__(self, *, size: tuple[int, int] = (1000, 500), available: bool = True) -> None:
        self.available = available
        self.size = size
        self.calls: list[tuple[int, int, int, int]] = []

    def is_available(self) -> bool:
        return self.available

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}:{target.pid}"

    def capture_frame(self, target: DetectedGameWindow, profile) -> _FakeImage:
        del target
        width, height = self.size
        left = int(width * profile.left_inset_ratio)
        right = int(width * (1.0 - profile.right_inset_ratio))
        top = int(height * profile.top_ratio)
        bottom = int(height * (1.0 - profile.bottom_inset_ratio))
        box = (left, top, right, bottom)
        self.calls.append(box)
        return _FakeImage((max(0, right - left), max(0, bottom - top)), crop_box=box)


class _CropAwareOcrBackend:
    def __init__(self, resolver) -> None:
        self._resolver = resolver

    def is_available(self) -> bool:
        return True

    def extract_text(self, image: _FakeImage) -> str:
        return str(self._resolver(image) or "")


def _memory_reader_session(
    *,
    game_id: str,
    session_id: str,
    state: dict[str, object],
    last_seq: int,
) -> dict[str, object]:
    payload = _session(
        game_id=game_id,
        session_id=session_id,
        last_seq=last_seq,
        state=state,
    )
    payload["bridge_sdk_version"] = "memory-reader-0.1.0"
    payload["engine"] = "unknown"
    payload["metadata"] = {
        "source": "memory_reader",
        "game_process_name": "RenPy Demo.exe",
        "game_pid": 4242,
    }
    return payload


def _ocr_reader_session(
    *,
    game_id: str,
    session_id: str,
    state: dict[str, object],
    last_seq: int,
) -> dict[str, object]:
    payload = _session(
        game_id=game_id,
        session_id=session_id,
        last_seq=last_seq,
        state=state,
    )
    payload["bridge_sdk_version"] = "ocr-reader-0.1.0"
    payload["engine"] = "unknown"
    payload["metadata"] = {
        "source": DATA_SOURCE_OCR_READER,
        "process_name": "RenPy Demo.exe",
        "pid": 5252,
    }
    return payload


def _prepare_fake_tesseract_install(install_root: Path) -> None:
    install_root.mkdir(parents=True, exist_ok=True)
    (install_root / "tesseract.exe").write_text("", encoding="utf-8")
    tessdata_dir = install_root / "tessdata"
    tessdata_dir.mkdir(parents=True, exist_ok=True)
    for language in ("chi_sim", "jpn", "eng"):
        (tessdata_dir / f"{language}.traineddata").write_text("", encoding="utf-8")


def _read_bridge_events(events_path: Path) -> list[dict[str, Any]]:
    result = tail_events_jsonl(events_path, offset=0, line_buffer=b"")
    assert result.errors == []
    assert result.line_buffer == b""
    return result.events


def _summary_test_line(scene_id: str, index: int, *, session_id: str = "sess-a") -> dict[str, object]:
    del session_id
    return {
        "line_id": f"{scene_id}-line-{index}",
        "speaker": "Yukino",
        "text": f"{scene_id} dialogue line {index}.",
        "scene_id": scene_id,
        "route_id": "",
        "ts": f"2026-04-21T08:35:{index:02d}Z",
    }


def _summary_test_line_event(
    scene_id: str,
    index: int,
    *,
    seq: int,
    session_id: str = "sess-a",
) -> dict[str, object]:
    line = _summary_test_line(scene_id, index)
    return _event(
        seq=seq,
        event_type="line_changed",
        session_id=session_id,
        game_id="demo.alpha",
        payload={**line, "stability": "stable"},
        ts=str(line["ts"]),
    )


__all__ = [name for name in globals() if not name.startswith("__")]
