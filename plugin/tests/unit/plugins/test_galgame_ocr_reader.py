from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import fields
import hashlib
import json
import logging
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from plugin.plugins.galgame_plugin import ocr_capture as galgame_ocr_capture
from plugin.plugins.galgame_plugin import ocr_backends as galgame_ocr_backends
from plugin.plugins.galgame_plugin import ocr_bridge_writer as galgame_ocr_bridge_writer
from plugin.plugins.galgame_plugin import ocr_capture_backends as galgame_ocr_capture_backends
from plugin.plugins.galgame_plugin import dialogue_library as galgame_dialogue_library
from plugin.plugins.galgame_plugin.ocr_capture_backends import printwindow as galgame_printwindow_backend
from plugin.plugins.galgame_plugin.ocr_capture_backends import dxcam as galgame_dxcam_backend
from plugin.plugins.galgame_plugin.ocr_capture_backends import pyautogui as galgame_pyautogui_backend
from plugin.plugins.galgame_plugin.ocr_capture_backends import _helpers as galgame_ocr_capture_helpers
from plugin.plugins.galgame_plugin import ocr_rapidocr_backend as galgame_ocr_rapidocr_backend
from plugin.plugins.galgame_plugin import ocr_reader as galgame_ocr_reader
from plugin.plugins._shared.rapidocr import rapidocr_support as galgame_rapidocr_support
from plugin.server.routes import _install_task_store as galgame_install_tasks
from plugin.plugins.galgame_plugin.models import (
    DEFAULT_OCR_CAPTURE_BOTTOM_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_TOP_RATIO,
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
    OCR_TRIGGER_MODE_AFTER_ADVANCE,
    OCR_TRIGGER_MODE_INTERVAL,
    READER_MODE_AUTO,
    READER_MODE_OCR,
)
from plugin.plugins.galgame_plugin.ocr_reader import (
    DetectedGameWindow,
    OcrBackendDescriptor,
    OcrCaptureProfile,
    OcrExtractionResult,
    OcrReaderBridgeWriter,
    OcrReaderManager,
    OcrReaderRuntime,
    OcrWindowTarget,
    OcrTextBox,
    SelectedOcrBackendPlan,
    _OcrLangDetector,
    _classify_cjk_text,
    _is_window_on_primary_monitor,
    _rapidocr_text_from_output,
    _score_ocr_text,
)
from plugin.plugins.galgame_plugin.reader import read_session_json, tail_events_jsonl
from plugin.plugins.galgame_plugin.screen_awareness_training import (
    evaluate_screen_awareness_model,
    train_screen_awareness_model,
)
from plugin.plugins.galgame_plugin.screen_classifier import (
    ScreenClassification,
    classify_screen_awareness_model,
    classify_screen_from_ocr,
    _layout_features,
    _normalized_bounds,
)
from plugin.plugins.galgame_plugin.service import build_config


pytestmark = pytest.mark.plugin_unit

TEST_WAIT_TIMEOUT = 1.0
DEFAULT_OCR_TEST_LANGUAGES = "chi_sim+jpn+eng"


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
        self.infos: list[tuple[object, ...]] = []
        self.warnings: list[tuple[object, ...]] = []

    def info(self, *args, **kwargs):
        del kwargs
        self.infos.append(args)

    def warning(self, *args, **kwargs):
        del kwargs
        self.warnings.append(args)


class _ExplodingLogger(_Logger):
    def debug(self, *args, **kwargs):
        raise RuntimeError("logger debug failed")

    def warning(self, *args, **kwargs):
        raise RuntimeError("logger warning failed")

    def info(self, *args, **kwargs):
        raise RuntimeError("logger info failed")


class _FormattingLogger(_Logger):
    def __init__(self) -> None:
        self.messages: list[str] = []

    def warning(self, message, *args, **kwargs):
        del kwargs
        self.messages.append(str(message).format(*args))


class _BadLogArg:
    def __repr__(self) -> str:
        raise RuntimeError("repr failed")

    def __str__(self) -> str:
        raise RuntimeError("str failed")


class _FakeCaptureBackend:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.capture_calls: list[tuple[int, dict[str, float]]] = []

    def is_available(self) -> bool:
        return self.available

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}:{target.pid}"

    def capture_frame(self, target: DetectedGameWindow, profile) -> str:
        self.capture_calls.append((target.hwnd, profile.to_dict()))
        return f"frame:{target.hwnd}:{len(self.capture_calls)}"


class _ExplodingAvailabilityCaptureBackend(_FakeCaptureBackend):
    def is_available(self) -> bool:
        raise RuntimeError("probe failed")


class _FakeOcrBackend:
    def __init__(self, texts: list[str] | None = None, *, available: bool = True) -> None:
        self._texts = list(texts or [])
        self.available = available
        self.calls = 0

    def is_available(self) -> bool:
        return self.available

    def extract_text(self, image: str) -> str:
        del image
        self.calls += 1
        if not self._texts:
            return ""
        if len(self._texts) == 1:
            return self._texts[0]
        return self._texts.pop(0)


def test_log_warning_falls_back_when_argument_formatting_fails() -> None:
    manager = object.__new__(OcrReaderManager)
    logger = _FormattingLogger()
    manager._logger = logger

    manager._log_warning("ocr_reader backend failed: {}", _BadLogArg())

    assert len(logger.messages) == 1
    assert logger.messages[0].startswith("ocr_reader backend failed: <")


def test_dxcam_create_timeout_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def _create_with_timeout(_dxcam_module, *, timeout_seconds: float):
        nonlocal calls
        del timeout_seconds
        calls += 1
        raise TimeoutError("dxcam_create_timed_out_after_0.1s")

    monkeypatch.setitem(
        sys.modules,
        "dxcam",
        SimpleNamespace(create=lambda **_kwargs: object()),
    )
    monkeypatch.setattr(
        galgame_dxcam_backend,
        "_create_dxcam_camera_with_timeout",
        _create_with_timeout,
    )

    backend = galgame_dxcam_backend.DxcamCaptureBackend(logger=_Logger())
    with pytest.raises(TimeoutError):
        backend._camera_instance()

    assert calls == 1
    assert backend._consecutive_failures == 1
    assert backend._last_failure_time > 0.0


def test_dxcam_create_none_after_retries_records_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def _create_with_timeout(_dxcam_module, *, timeout_seconds: float):
        nonlocal calls
        del timeout_seconds
        calls += 1
        return None

    monkeypatch.setitem(sys.modules, "dxcam", SimpleNamespace(create=lambda **_kwargs: object()))
    monkeypatch.setattr(
        galgame_dxcam_backend,
        "_create_dxcam_camera_with_timeout",
        _create_with_timeout,
    )
    monkeypatch.setattr(galgame_dxcam_backend.time, "sleep", lambda _seconds: None)

    backend = galgame_dxcam_backend.DxcamCaptureBackend(logger=_Logger())
    with pytest.raises(RuntimeError, match="returned None after retries"):
        backend._camera_instance()

    assert calls == 3
    assert backend._consecutive_failures == 1
    assert backend._last_failure_time > 0.0


def test_dxcam_create_timeout_rate_limits_after_failure_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def _create_with_timeout(_dxcam_module, *, timeout_seconds: float):
        nonlocal calls
        del timeout_seconds
        calls += 1
        raise TimeoutError("dxcam_create_timed_out_after_0.1s")

    monkeypatch.setitem(sys.modules, "dxcam", SimpleNamespace(create=lambda **_kwargs: object()))
    monkeypatch.setattr(
        galgame_dxcam_backend,
        "_create_dxcam_camera_with_timeout",
        _create_with_timeout,
    )
    monkeypatch.setattr(
        galgame_dxcam_backend,
        "_require_visible_capture_target",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        galgame_dxcam_backend,
        "_target_screen_capture_rect",
        lambda _target: (0, 0, 100, 100),
    )

    backend = galgame_dxcam_backend.DxcamCaptureBackend(logger=_Logger())
    target = DetectedGameWindow(hwnd=1, pid=1, width=100, height=100)
    for expected_failures in (1, 2, 3):
        with pytest.raises(TimeoutError):
            backend.capture_frame(target, OcrCaptureProfile())
        assert backend._consecutive_failures == expected_failures
    with pytest.raises(RuntimeError, match="dxcam rate limited"):
        backend.capture_frame(target, OcrCaptureProfile())

    assert calls == 3


def _make_config(
    bridge_root: Path,
    *,
    enabled: bool = True,
    backend_selection: str = "auto",
        install_target_dir: str = "",
    poll_interval_seconds: float = 999.0,
    no_text_takeover_after_seconds: float = 30.0,
    background_scene_change_distance: object = 28,
    languages: str = DEFAULT_OCR_TEST_LANGUAGES,
    rapidocr_enabled: bool = True,
    rapidocr_install_target_dir: str = "",
    llm_vision_enabled: bool = False,
    llm_vision_max_image_px: int = 768,
    screen_templates: list[dict[str, object]] | None = None,
    screen_awareness_sample_collection_enabled: bool = False,
    screen_awareness_sample_dir: str = "",
    screen_awareness_model_enabled: bool = False,
    screen_awareness_model_path: str = "",
    screen_awareness_model_min_confidence: float = 0.55,
    screen_type_transition_emit: bool = True,
    known_screen_timeout_seconds: float = 5.0,
    reader_mode: str = READER_MODE_AUTO,
    trigger_mode: str = OCR_TRIGGER_MODE_AFTER_ADVANCE,
) -> object:
    return build_config(
        {
            "galgame": {
                "bridge_root": str(bridge_root),
                "reader_mode": reader_mode,
            },
            "llm": {
                "vision_enabled": llm_vision_enabled,
                "vision_max_image_px": llm_vision_max_image_px,
            },
            "ocr_reader": {
                "enabled": enabled,
                "backend_selection": backend_selection,
                "install_target_dir": install_target_dir,
                "poll_interval_seconds": poll_interval_seconds,
                "no_text_takeover_after_seconds": no_text_takeover_after_seconds,
                "background_scene_change_distance": background_scene_change_distance,
                "languages": languages,
                "screen_templates": list(screen_templates or []),
                "screen_awareness_sample_collection_enabled": screen_awareness_sample_collection_enabled,
                "screen_awareness_sample_dir": screen_awareness_sample_dir,
                "screen_awareness_model_enabled": screen_awareness_model_enabled,
                "screen_awareness_model_path": screen_awareness_model_path,
                "screen_awareness_model_min_confidence": screen_awareness_model_min_confidence,
                "screen_type_transition_emit": screen_type_transition_emit,
                "known_screen_timeout_seconds": known_screen_timeout_seconds,
                "trigger_mode": trigger_mode,
            },
            "rapidocr": {
                "enabled": rapidocr_enabled,
                "install_target_dir": rapidocr_install_target_dir,
                "engine_type": "onnxruntime",
                "lang_type": "ch",
                "model_type": "mobile",
                "ocr_version": "PP-OCRv5",
            },
        }
    )


def _install_fake_ocr_runtime(root: Path, *, languages: str = DEFAULT_OCR_TEST_LANGUAGES) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    executable = root / "ocr-runtime.exe"
    executable.write_text("", encoding="utf-8")
    tessdata_dir = root / "ocrdata"
    tessdata_dir.mkdir(parents=True, exist_ok=True)
    for language in [item.strip() for item in languages.split("+") if item.strip()]:
        (tessdata_dir / f"{language}.traineddata").write_text("", encoding="utf-8")
    return executable


@pytest.fixture
def ocr_runtime_root(tmp_path: Path) -> Path:
    install_root = tmp_path / "OcrRuntime"
    _install_fake_ocr_runtime(install_root)
    return install_root


def _read_events(events_path: Path) -> list[dict[str, object]]:
    result = tail_events_jsonl(events_path, offset=0, line_buffer=b"")
    return result.events


def _window() -> list[DetectedGameWindow]:
    return [
        DetectedGameWindow(
            hwnd=101,
            title="Demo Window",
            process_name="DemoGame.exe",
            pid=4242,
        )
    ]


def _write_senren_banka_dialogue_library(
    library_dir: Path,
    *,
    line_id: str = "fixture:001",
    text: str = "这是千恋万花台词库的开发者测试行。",
    aliases: list[str] | None = None,
) -> Path:
    library_dir.mkdir(parents=True, exist_ok=True)
    (library_dir / "senren_banka.json").write_text(
        json.dumps(
            {
                "game_id": galgame_dialogue_library.SENREN_BANKA_GAME_ID,
                "title": "千恋＊万花",
                "lines": [
                    {
                        "id": line_id,
                        "text": text,
                        "aliases": aliases or [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    galgame_dialogue_library._load_dialogue_library_cached.cache_clear()
    return library_dir


def test_senren_banka_dialogue_library_matches_fixture_line_with_minor_ocr_error(
    tmp_path: Path,
) -> None:
    expected_line_id = "fixture:ocr-fuzzy"
    expected_text = "这是千恋万花台词库的开发者测试行。"
    library_dir = _write_senren_banka_dialogue_library(
        tmp_path / "dialogue_libraries",
        line_id=expected_line_id,
        text=expected_text,
    )

    match = galgame_dialogue_library.match_dialogue_library_for_target(
        "这是千恋万花台词库的开发者测试句。",
        process_name="SenrenBanka.exe",
        normalized_title="千恋＊万花",
        library_dir=library_dir,
    )

    assert match is not None
    assert match.game_id == galgame_dialogue_library.SENREN_BANKA_GAME_ID
    assert match.line_id == expected_line_id
    assert match.text == expected_text
    assert match.score >= 0.9


def test_senren_banka_dialogue_library_matches_normalized_traditional_title(
    tmp_path: Path,
) -> None:
    library_dir = _write_senren_banka_dialogue_library(tmp_path / "dialogue_libraries")

    match = galgame_dialogue_library.match_dialogue_library_for_target(
        "这是千恋万花台词库的开发者测试行。",
        process_name="",
        normalized_title="千戀＊萬花",
        library_dir=library_dir,
    )

    assert match is not None
    assert match.game_id == galgame_dialogue_library.SENREN_BANKA_GAME_ID


def test_dialogue_library_rejects_invalid_line(tmp_path: Path) -> None:
    library_dir = tmp_path / "dialogue_libraries"
    library_dir.mkdir()
    path = library_dir / "senren_banka.json"
    path.write_text(
        json.dumps(
            {
                "game_id": galgame_dialogue_library.SENREN_BANKA_GAME_ID,
                "title": "千恋＊万花",
                "lines": [{"id": "fixture:bad", "text": ""}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"line #1.*game_id=senren_banka"):
        galgame_dialogue_library.load_dialogue_library(path)


def test_dialogue_library_match_ignores_invalid_library_on_hot_path(tmp_path: Path) -> None:
    library_dir = tmp_path / "dialogue_libraries"
    library_dir.mkdir()
    (library_dir / "senren_banka.json").write_text("{", encoding="utf-8")

    assert (
        galgame_dialogue_library.match_dialogue_library_for_target(
            "这是千恋万花台词库的开发者测试句。",
            process_name="SenrenBanka.exe",
            normalized_title="千恋＊万花",
            library_dir=library_dir,
        )
        is None
    )


def test_dialogue_library_does_not_match_unrelated_game_target(tmp_path: Path) -> None:
    library_dir = _write_senren_banka_dialogue_library(tmp_path / "dialogue_libraries")

    assert (
        galgame_dialogue_library.match_dialogue_library_for_target(
            "这是千恋万花台词库的开发者测试句。",
            process_name="DemoGame.exe",
            normalized_title="Demo Window",
            library_dir=library_dir,
        )
        is None
    )


@pytest.mark.parametrize(
    "profile",
    [
        {"left_inset_ratio": -0.01},
        {"right_inset_ratio": 1.01},
        {"top_ratio": float("nan")},
        {"left_inset_ratio": 0.5, "right_inset_ratio": 0.5},
        {"top_ratio": 0.9, "bottom_inset_ratio": 0.1},
    ],
)
def test_ocr_capture_profile_rejects_invalid_ratios(profile: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        OcrCaptureProfile.from_dict(profile)


def test_ocr_writer_existing_last_seq_streams_events_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = OcrReaderBridgeWriter(bridge_root=tmp_path)
    writer.start_session(_window()[0])
    events_path = tmp_path / writer.game_id / "events.jsonl"
    events_path.write_bytes(b'{"seq": 2}\nnot-json\n{"seq": 7}\n')

    def _fail_read_bytes(_path: Path) -> bytes:
        raise AssertionError("events.jsonl should be streamed")

    monkeypatch.setattr(Path, "read_bytes", _fail_read_bytes)

    assert writer._existing_last_seq_unlocked() == 7


def test_memory_reader_last_text_recent_false_keeps_takeover_timestamp(
    tmp_path: Path,
) -> None:
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(tmp_path),
        platform_fn=lambda: True,
        window_scanner=lambda: [],
        capture_backend=None,
        ocr_backend=None,
    )
    manager._last_memory_reader_text_at = 123.0

    observed = manager._observe_memory_reader_text_progress(
        {
            "status": "active",
            "game_id": "demo",
            "session_id": "session",
            "last_text_seq": 5,
            "last_text_recent": False,
        },
        now=456.0,
    )

    assert observed is False
    assert manager._last_memory_reader_text_at == pytest.approx(123.0)


def test_manual_window_target_signature_ignores_pid_when_stable_fields_match() -> None:
    target = OcrWindowTarget(
        mode="manual",
        process_name="DemoGame.exe",
        normalized_title="demo window",
        pid=4242,
    )

    assert target.matches_signature(
        DetectedGameWindow(
            title="Demo Window",
            process_name="DemoGame.exe",
            pid=9999,
        )
    )
    assert target.matches_signature(
        DetectedGameWindow(
            title="Demo Window",
            process_name="DemoGame.exe",
            pid=4242,
        )
    )


def test_manual_window_target_signature_uses_pid_only_without_stable_fields() -> None:
    target = OcrWindowTarget(mode="manual", pid=4242)

    assert target.matches_signature(DetectedGameWindow(pid=4242))
    assert not target.matches_signature(DetectedGameWindow(pid=9999))


def _assert_poll_runtime_completed(runtime: dict[str, object]) -> None:
    assert runtime["last_poll_started_at"]
    assert runtime["last_poll_completed_at"]
    assert runtime["last_poll_duration_seconds"] >= 0.0
    assert runtime["last_poll_emitted_event"] is False


@pytest.mark.parametrize(
    ("ocr_text", "expected_stage"),
    [
        ("Start Game\nContinue\nConfig\nExit", OCR_CAPTURE_PROFILE_STAGE_TITLE),
        ("Save\nLoad\nPage 1\nSlot 01\nBack", OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD),
        ("BGM Volume\nVoice Volume\nText Speed\nWindow Mode\nBack", OCR_CAPTURE_PROFILE_STAGE_CONFIG),
        ("Gallery\nCG Mode\nScene Replay\nBack", OCR_CAPTURE_PROFILE_STAGE_GALLERY),
        ("Backlog\n雪乃：前の台詞。\n王生：もう一度確認する。", OCR_CAPTURE_PROFILE_STAGE_GALLERY),
        ("Mini Game\nScore\nCombo\nTime", OCR_CAPTURE_PROFILE_STAGE_MINIGAME),
        ("Game Over\nRetry\nReturn to Title", OCR_CAPTURE_PROFILE_STAGE_GAME_OVER),
        ("1. Save her.\n2. Leave.", OCR_CAPTURE_PROFILE_STAGE_MENU),
        ("雪乃：今天也一起回家吧。", OCR_CAPTURE_PROFILE_STAGE_DIALOGUE),
        ("", OCR_CAPTURE_PROFILE_STAGE_DEFAULT),
    ],
)
def test_screen_classifier_recognizes_common_ocr_text(ocr_text: str, expected_stage: str) -> None:
    classified = classify_screen_from_ocr(ocr_text)

    assert classified.screen_type == expected_stage
    if expected_stage == OCR_CAPTURE_PROFILE_STAGE_DEFAULT:
        assert classified.confidence == 0.0
    else:
        assert classified.confidence > 0.0


def test_screen_classifier_recognizes_backlog_dialogue_list_without_title() -> None:
    classified = classify_screen_from_ocr(
        "\n".join(
            [
                "雪乃：さっきの話だけど。",
                "王生：まだ覚えている。",
                "雪乃：本当に？",
                "王生：ああ、忘れない。",
            ]
        ),
        boxes=[
            OcrTextBox(text="雪乃：さっきの話だけど。", left=120, top=80, right=720, bottom=116),
            OcrTextBox(text="王生：まだ覚えている。", left=120, top=162, right=700, bottom=198),
            OcrTextBox(text="雪乃：本当に？", left=120, top=244, right=520, bottom=280),
            OcrTextBox(text="王生：ああ、忘れない。", left=120, top=326, right=760, bottom=362),
        ],
        bounds_metadata={"source_size": {"width": 1280, "height": 720}},
    )

    assert classified.screen_type == OCR_CAPTURE_PROFILE_STAGE_GALLERY
    assert classified.debug["reason"] == "backlog_dialogue_list"


def test_screen_classifier_recognizes_text_only_backlog_dialogue_history_sample() -> None:
    classified = classify_screen_from_ocr(
        "\n".join(
            [
                "【雪乃】先に帰るね。",
                "【王生】送っていくよ。",
                "【雪乃】大丈夫。",
                "【王生】でも心配だ。",
                "【雪乃】ありがとう。",
            ]
        )
    )

    assert classified.screen_type == OCR_CAPTURE_PROFILE_STAGE_GALLERY
    assert classified.debug["reason"] == "backlog_dialogue_list"


def test_screen_classifier_prefers_matching_screen_template() -> None:
    classified = classify_screen_from_ocr(
        "Archive\nSpecial\nBack",
        screen_templates=[
            {
                "id": "demo-gallery",
                "stage": OCR_CAPTURE_PROFILE_STAGE_GALLERY,
                "process_names": ["DemoGame.exe"],
                "keywords": ["Archive"],
                "min_keyword_hits": 1,
                "priority": 10,
            }
        ],
        template_context={
            "process_name": "DemoGame.exe",
            "window_title": "Demo Window",
            "width": 1280,
            "height": 720,
            "game_id": "demo",
        },
    )

    assert classified.screen_type == OCR_CAPTURE_PROFILE_STAGE_GALLERY
    assert classified.debug["reason"] == "screen_template"
    assert classified.debug["template"]["id"] == "demo-gallery"


def test_screen_classifier_matches_context_only_template_without_ocr_text() -> None:
    classified = classify_screen_from_ocr(
        "",
        screen_templates=[
            {
                "id": "demo-title",
                "stage": OCR_CAPTURE_PROFILE_STAGE_TITLE,
                "process_names": ["DemoGame.exe"],
                "width": 1280,
                "height": 720,
                "match_without_keywords": True,
            }
        ],
        template_context={
            "process_name": "DemoGame.exe",
            "window_title": "Demo Window",
            "width": 1280,
            "height": 720,
            "game_id": "demo",
        },
    )

    assert classified.screen_type == OCR_CAPTURE_PROFILE_STAGE_TITLE
    assert classified.debug["reason"] == "screen_template"


def test_screen_classifier_matches_template_region_against_ui_elements() -> None:
    classified = classify_screen_from_ocr(
        "Archive",
        boxes=[
            OcrTextBox(
                text="Archive",
                left=100.0,
                top=100.0,
                right=260.0,
                bottom=150.0,
            )
        ],
        bounds_metadata={
            "source_size": {"width": 1000.0, "height": 500.0},
            "capture_rect": {"left": 0.0, "top": 0.0, "right": 1000.0, "bottom": 500.0},
        },
        screen_templates=[
            {
                "id": "gallery-region",
                "stage": OCR_CAPTURE_PROFILE_STAGE_GALLERY,
                "process_names": ["DemoGame.exe"],
                "regions": [
                    {
                        "left": 0.08,
                        "top": 0.16,
                        "right": 0.30,
                        "bottom": 0.34,
                        "min_overlap": 0.4,
                    }
                ],
                "min_region_hits": 1,
            }
        ],
        template_context={"process_name": "DemoGame.exe"},
    )

    assert classified.screen_type == OCR_CAPTURE_PROFILE_STAGE_GALLERY
    assert classified.debug["reason"] == "screen_template"
    assert classified.debug["template"]["region_hits"] == 1


def test_screen_awareness_model_predicts_from_visual_feature_prototype() -> None:
    prediction = classify_screen_awareness_model(
        {
            "mean_luminance": 42.0,
            "luminance_std": 8.0,
            "texture_score": 3.0,
        },
        {
            "prototypes": [
                {
                    "id": "dark-transition",
                    "stage": OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
                    "features": {
                        "mean_luminance": 40.0,
                        "luminance_std": 10.0,
                        "texture_score": 4.0,
                    },
                    "confidence": 0.9,
                }
            ]
        },
        min_confidence=0.55,
    )

    assert prediction is not None
    assert prediction["stage"] == OCR_CAPTURE_PROFILE_STAGE_TRANSITION
    assert prediction["confidence"] >= 0.55


def test_screen_awareness_training_exports_model_and_evaluation_report(tmp_path: Path) -> None:
    samples_path = tmp_path / "samples.jsonl"
    output_path = tmp_path / "model.json"
    report_path = tmp_path / "report.json"
    records = [
        {
            "label": OCR_CAPTURE_PROFILE_STAGE_TITLE,
            "visual_features": {"mean_luminance": 180 + index, "luminance_std": 45, "texture_score": 22},
            "ocr_lines": ["Start", "Config"],
            "screen_ui_elements": [{"text": "Start"}],
        }
        for index in range(3)
    ] + [
        {
            "label": OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
            "visual_features": {"mean_luminance": 5 + index, "luminance_std": 2, "texture_score": 1},
            "ocr_lines": [],
            "screen_ui_elements": [],
        }
        for index in range(3)
    ]
    samples_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )

    trained = train_screen_awareness_model(
        samples_path,
        output_path,
        validation_ratio=0.0,
        min_samples_per_stage=2,
    )
    evaluated = evaluate_screen_awareness_model(
        samples_path,
        output_path,
        report_path=report_path,
    )

    assert output_path.is_file()
    assert report_path.is_file()
    assert len(trained["model"]["prototypes"]) == 2
    assert evaluated["evaluation"]["sample_count"] == 6
    assert evaluated["evaluation"]["accuracy"] >= 0.8


def test_screen_classifier_exports_limited_ui_elements_with_bounds() -> None:
    boxes = [
        OcrTextBox(
            text=f"Start {index}",
            left=index,
            top=index + 1,
            right=index + 20,
            bottom=index + 10,
        )
        for index in range(12)
    ]

    classified = classify_screen_from_ocr(
        "Start Game\nContinue\nConfig\nExit",
        boxes=boxes,
        bounds_metadata={
            "bounds_coordinate_space": "capture",
            "source_size": {"width": 1280.0, "height": 720.0},
            "capture_rect": {"left": 0.0, "top": 0.0, "right": 1280.0, "bottom": 720.0},
        },
    )

    assert classified.screen_type == OCR_CAPTURE_PROFILE_STAGE_TITLE
    assert len(classified.ui_elements) == 10
    assert classified.ui_elements[0]["bounds"] == {
        "left": 0.0,
        "top": 1.0,
        "right": 20.0,
        "bottom": 10.0,
    }
    assert classified.ui_elements[0]["bounds_coordinate_space"] == "capture"
    assert classified.ui_elements[0]["normalized_bounds"]["right"] == pytest.approx(20.0 / 1280.0)


def test_layout_features_do_not_mix_normalized_and_raw_bounds() -> None:
    layout = _layout_features(
        [
            {
                "text": "Start",
                "bounds": {"left": 100.0, "top": 100.0, "right": 220.0, "bottom": 140.0},
                "normalized_bounds": {
                    "left": 0.1,
                    "top": 0.2,
                    "right": 0.3,
                    "bottom": 0.25,
                },
            },
            {
                "text": "Config",
                "bounds": {"left": 110.0, "top": 180.0, "right": 230.0, "bottom": 220.0},
            },
            {
                "text": "Exit",
                "bounds": {"left": 120.0, "top": 260.0, "right": 240.0, "bottom": 300.0},
            },
        ]
    )

    assert layout["button_layout_score"] == 0.0

    raw_layout = _layout_features(
        [
            {
                "text": "Start",
                "bounds": {"left": 100.0, "top": 100.0, "right": 220.0, "bottom": 140.0},
            },
            {
                "text": "Config",
                "bounds": {"left": 110.0, "top": 180.0, "right": 230.0, "bottom": 220.0},
            },
            {
                "text": "Exit",
                "bounds": {"left": 120.0, "top": 260.0, "right": 240.0, "bottom": 300.0},
            },
        ]
    )

    assert raw_layout["button_layout_score"] > 0.0


def test_normalized_bounds_expands_clamped_degenerate_edges() -> None:
    normalized = _normalized_bounds(
        {"left": 1600.0, "top": 900.0, "right": 1700.0, "bottom": 930.0},
        {"source_size": {"width": 1280.0, "height": 720.0}},
    )

    assert normalized["left"] == pytest.approx(0.99)
    assert normalized["right"] == pytest.approx(1.0)
    assert normalized["top"] == pytest.approx(0.99)
    assert normalized["bottom"] == pytest.approx(1.0)
    assert normalized["left"] < normalized["right"]
    assert normalized["top"] < normalized["bottom"]


def test_screen_classifier_merges_full_frame_ocr_regions() -> None:
    boxes = [
        OcrTextBox(
            text="Start Game",
            left=120.0,
            top=220.0,
            right=360.0,
            bottom=260.0,
            score=0.91,
        )
    ]

    classified = classify_screen_from_ocr(
        "",
        ocr_regions=[
            {
                "source": "full_frame",
                "text": "Start Game\nConfig\nExit",
                "boxes": boxes,
                "bounds_metadata": {
                    "bounds_coordinate_space": "capture",
                    "source_size": {"width": 1280.0, "height": 720.0},
                    "capture_rect": {"left": 100.0, "top": 50.0, "right": 1380.0, "bottom": 770.0},
                    "window_rect": {"left": 100.0, "top": 50.0, "right": 1380.0, "bottom": 770.0},
                },
            }
        ],
    )

    assert classified.screen_type == OCR_CAPTURE_PROFILE_STAGE_TITLE
    assert classified.ui_elements[0]["text_source"] == "full_frame"
    assert classified.ui_elements[0]["normalized_bounds"]["left"] == pytest.approx(120.0 / 1280.0)
    assert classified.debug["sources"] == ["bottom_region", "full_frame"]


def test_screen_classifier_filters_window_chrome_noise_from_regions() -> None:
    boxes = [
        OcrTextBox("TheLamentingGeese口×", 12.0, 4.0, 1260.0, 28.0, 0.9),
        OcrTextBox("有人是猪马牛羊，有人是虎豹豺狼。", 140.0, 734.0, 780.0, 760.0, 0.96),
        OcrTextBox("16°℃", 40.0, 980.0, 74.0, 996.0, 0.88),
    ]

    classified = classify_screen_from_ocr(
        "",
        ocr_regions=[
            {
                "source": "full_frame",
                "text": "\n".join(box.text for box in boxes),
                "boxes": boxes,
                "bounds_metadata": {
                    "bounds_coordinate_space": "capture",
                    "source_size": {"width": 1296.0, "height": 999.0},
                    "capture_rect": {"left": 5.0, "top": 61.0, "right": 1301.0, "bottom": 1060.0},
                    "window_rect": {"left": 5.0, "top": 61.0, "right": 1301.0, "bottom": 1060.0},
                },
            }
        ],
        template_context={"window_title": "TheLamentingGeese"},
    )

    assert classified.raw_ocr_text == ["有人是猪马牛羊，有人是虎豹豺狼。"]
    assert [element["text"] for element in classified.ui_elements] == [
        "有人是猪马牛羊，有人是虎豹豺狼。"
    ]
    assert classified.debug["chrome_filtered_count"] == 2


def test_screen_classifier_detects_blank_visual_transition_without_ocr() -> None:
    classified = classify_screen_from_ocr(
        "",
        visual_features={
            "mean_luminance": 0.0,
            "luminance_std": 0.0,
            "texture_score": 0.0,
        },
    )

    assert classified.screen_type == OCR_CAPTURE_PROFILE_STAGE_TRANSITION
    assert classified.confidence >= 0.6
    assert classified.debug["reason"] == "visual_blank_transition"


def test_ocr_reader_manager_applies_screen_awareness_model_on_low_confidence(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    model_path = tmp_path / "screen-model.json"
    model_path.write_text(
        """
        {
          "prototypes": [
            {
              "id": "menu-dark",
              "stage": "menu_stage",
              "features": {
                "mean_luminance": 80,
                "luminance_std": 22,
                "texture_score": 18
              },
              "confidence": 0.92
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            screen_awareness_model_enabled=True,
            screen_awareness_model_path=str(model_path),
            screen_awareness_model_min_confidence=0.55,
        ),
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    extraction = OcrExtractionResult(
        text="",
        screen_visual_features={
            "mean_luminance": 82.0,
            "luminance_std": 21.0,
            "texture_score": 17.0,
        },
    )

    classified = manager._apply_screen_awareness_model(
        extraction,
        classification=ScreenClassification(),
        target=_window()[0],
    )

    assert classified.screen_type == OCR_CAPTURE_PROFILE_STAGE_MENU
    assert classified.debug["reason"] == "screen_awareness_model"
    assert manager._screen_awareness_model_detail == "matched"


def test_ocr_reader_screen_awareness_full_frame_runs_when_primary_is_full_window(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    capture_backend = _FakeCaptureBackend()
    ocr_backend = _FakeOcrBackend(["Start Game\nConfig"])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=capture_backend,
        ocr_backend=ocr_backend,
    )
    manager._config.ocr_reader_screen_awareness_full_frame_ocr = True
    manager._consecutive_no_text_polls = 1
    extraction = OcrExtractionResult(text="")

    manager._augment_extraction_with_screen_awareness(
        extraction,
        target=_window()[0],
        primary_profile=manager._full_window_profile(),
        plan=SelectedOcrBackendPlan(
            primary=OcrBackendDescriptor(
                kind="fake",
                backend=ocr_backend,
                available=True,
            )
        ),
        now=3000.0,
    )

    assert capture_backend.capture_calls
    assert extraction.screen_ocr_regions[0]["source"] == "full_frame"
    assert extraction.screen_ocr_regions[0]["text"] == "Start Game\nConfig"


def test_ocr_reader_manager_collects_desensitized_screen_awareness_sample(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    sample_dir = tmp_path / "samples"
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            screen_awareness_sample_collection_enabled=True,
            screen_awareness_sample_dir=str(sample_dir),
        ),
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    extraction = OcrExtractionResult(
        text="Start Game\nConfig",
        screen_visual_features={"mean_luminance": 120.0},
        screen_ocr_regions=[
            {
                "source": "full_frame",
                "text": "Start Game\nConfig",
                "ocr_confidence": 0.88,
            }
        ],
    )

    manager._collect_screen_awareness_sample(
        extraction,
        classification=ScreenClassification(
            screen_type=OCR_CAPTURE_PROFILE_STAGE_TITLE,
            confidence=0.8,
            raw_ocr_text=["Start Game", "Config"],
            debug={"reason": "title_keywords"},
        ),
        target=_window()[0],
        now=3000.0,
    )

    sample_path = sample_dir / "samples.jsonl"
    payload = sample_path.read_text(encoding="utf-8").strip()
    assert '"screen_type":"title_stage"' in payload
    assert '"visual_features":{"mean_luminance":120.0}' in payload
    assert manager._screen_awareness_sample_count == 1


def test_ocr_writer_emits_screen_classified_state_and_event(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    ui_elements = [
        {
            "element_id": f"button-{index}",
            "text": f"Button {index}",
            "bounds": {
                "left": float(index),
                "top": float(index + 1),
                "right": float(index + 20),
                "bottom": float(index + 10),
            },
        }
        for index in range(12)
    ]

    assert writer.emit_screen_classified(
        screen_type=OCR_CAPTURE_PROFILE_STAGE_TITLE,
        confidence=0.86,
        ui_elements=ui_elements,
        raw_ocr_text=["Start Game", "Continue", "Config", "Exit"],
        screen_debug={"reason": "title_keywords", "sources": ["full_frame"]},
        ts="2026-04-29T03:00:00Z",
    ) is True
    assert writer.emit_screen_classified(
        screen_type=OCR_CAPTURE_PROFILE_STAGE_TITLE,
        confidence=0.86,
        ui_elements=ui_elements,
        raw_ocr_text=["Start Game", "Continue", "Config", "Exit"],
        ts="2026-04-29T03:00:01Z",
    ) is False

    session = read_session_json(bridge_root / writer.game_id / "session.json")
    events = _read_events(bridge_root / writer.game_id / "events.jsonl")

    assert session.session is not None
    assert session.session["state"]["screen_type"] == OCR_CAPTURE_PROFILE_STAGE_TITLE
    assert session.session["state"]["screen_confidence"] == pytest.approx(0.86)
    assert session.session["state"]["screen_debug"]["reason"] == "title_keywords"
    assert len(session.session["state"]["screen_ui_elements"]) == 10
    assert events[-1]["type"] == "screen_classified"
    assert events[-1]["payload"]["screen_ui_elements"][0]["text"] == "Button 0"
    assert events[-1]["payload"]["screen_debug"]["sources"] == ["full_frame"]


def test_ocr_writer_emits_screen_classified_when_confidence_changes(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])

    assert writer.emit_screen_classified(
        screen_type=OCR_CAPTURE_PROFILE_STAGE_TITLE,
        confidence=0.86,
        ui_elements=[{"text": "Start Game"}],
        ts="2026-04-29T03:00:00Z",
    ) is True
    assert writer.emit_screen_classified(
        screen_type=OCR_CAPTURE_PROFILE_STAGE_TITLE,
        confidence=0.91,
        ui_elements=[{"text": "Start Game"}],
        ts="2026-04-29T03:00:01Z",
    ) is True

    session = read_session_json(bridge_root / writer.game_id / "session.json")
    events = _read_events(bridge_root / writer.game_id / "events.jsonl")

    assert session.session is not None
    assert session.session["state"]["screen_confidence"] == pytest.approx(0.91)
    assert [event["type"] for event in events][-2:] == [
        "screen_classified",
        "screen_classified",
    ]


def test_ocr_reader_emits_dialogue_transition_after_title_state(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    clock = {"now": 3000.0}
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: clock["now"])
    writer.start_session(_window()[0])
    writer.emit_screen_classified(
        screen_type=OCR_CAPTURE_PROFILE_STAGE_TITLE,
        confidence=0.86,
        ui_elements=[{"text": "Start Game"}],
        raw_ocr_text=["Start Game"],
        screen_debug={"reason": "title_keywords"},
        ts="2026-04-29T03:00:00Z",
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )

    classification, emitted = manager._emit_screen_classification_from_extraction(
        OcrExtractionResult(text="雪乃：你好。"),
        target=_window()[0],
        now=3001.0,
    )

    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    session = read_session_json(bridge_root / writer.game_id / "session.json").session

    assert classification.screen_type == OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
    assert emitted is True
    assert session is not None
    assert session["state"]["screen_type"] == OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
    assert events[-1]["type"] == "screen_classified"
    assert events[-1]["payload"]["screen_type"] == OCR_CAPTURE_PROFILE_STAGE_DIALOGUE


def test_ocr_reader_known_title_timeout_triggers_rescan_and_skip_bypass(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    clock = {"now": 3000.0}
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: clock["now"])
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            known_screen_timeout_seconds=2.0,
        ),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    title_classification = ScreenClassification(
        screen_type=OCR_CAPTURE_PROFILE_STAGE_TITLE,
        confidence=0.72,
        raw_ocr_text=["Start Game"],
    )
    target = _window()[0]
    active_backend = galgame_ocr_reader.OcrBackendDescriptor(
        kind="fake",
        available=True,
    )

    def finalize_at(now: float) -> galgame_ocr_reader.OcrReaderTickResult:
        clock["now"] = now
        return manager._finalize_tick_result(
            result=galgame_ocr_reader.OcrReaderTickResult(),
            now=now,
            poll_started_at=now,
            backend_plan=SelectedOcrBackendPlan(primary=active_backend),
            active_backend=active_backend,
            backend_detail_override="",
            target=target,
            aihong_two_stage_enabled=False,
            runtime_profile=OcrCaptureProfile(),
            runtime_capture_profile_selection=None,  # type: ignore[arg-type]
            selection=galgame_ocr_reader.WindowSelectionResult(target=target),
            emitted=False,
            guard_blocked=False,
            screen_classification=title_classification,
            screen_event_emitted=False,
            capture_attempted=True,
            capture_completed=True,
            capture_error=False,
            text_event_seq_before_capture=writer.last_seq,
            foreground_advance_stable_grace_active=False,
        )

    first = finalize_at(3000.0)
    second = finalize_at(3001.0)
    third = finalize_at(3002.1)

    assert first.should_rescan is False
    assert second.should_rescan is False
    assert third.should_rescan is True
    assert third.runtime["detail"] == "screen_classified_timeout_rescan"
    assert manager._should_skip_dialogue_for_screen_classification(title_classification) is False
    assert title_classification.debug["skip_dialogue_bypass_reason"] == (
        "known_screen_timeout_rescan"
    )


def test_ocr_writer_start_session_preserves_existing_game_events(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    clock = {"now": 3000.0}
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: clock["now"])
    window = _window()[0]
    writer.start_session(window)
    assert writer.emit_line(
        "Yukino: stable line.",
        ts="2026-04-29T03:00:01Z",
    ) is True
    game_id = writer.game_id
    events_path = bridge_root / game_id / "events.jsonl"
    first_events = _read_events(events_path)
    assert [event["type"] for event in first_events] == ["session_started", "line_changed"]

    clock["now"] = 3010.0
    writer.start_session(window)
    events = _read_events(events_path)

    assert [event["type"] for event in events] == [
        "session_started",
        "line_changed",
        "session_started",
    ]
    assert [event["seq"] for event in events] == [1, 2, 3]


def test_ocr_writer_discard_session_does_not_delete_existing_history(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    assert writer.emit_line(
        "Yukino: stable line.",
        ts="2026-04-29T03:00:01Z",
    ) is True
    game_id = writer.game_id

    writer.discard_session()
    events = _read_events(bridge_root / game_id / "events.jsonl")

    assert (bridge_root / game_id).is_dir()
    assert not (bridge_root / game_id / "session.json").exists()
    assert [event["type"] for event in events] == [
        "session_started",
        "line_changed",
        "session_ended",
    ]
    assert events[-1]["payload"]["discarded"] is True


def test_ocr_writer_discard_session_recovers_when_snapshot_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    game_id = writer.game_id

    def _fail_snapshot() -> None:
        raise RuntimeError("snapshot write failed")

    monkeypatch.setattr(writer, "_write_session_snapshot", _fail_snapshot)

    writer.discard_session()
    events = _read_events(bridge_root / game_id / "events.jsonl")

    assert writer.session_id == ""
    assert not (bridge_root / game_id / "session.json").exists()
    assert [event["type"] for event in events] == [
        "session_started",
        "session_ended",
    ]
    assert events[-1]["payload"]["discarded"] is True


def test_ocr_writer_end_session_resets_runtime_and_rejects_late_events(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    assert writer.emit_line("Yukino: stable line.", ts="2026-04-29T03:00:01Z") is True
    game_id = writer.game_id

    assert writer.end_session(ts="2026-04-29T03:00:02Z") is True
    runtime = writer.runtime()

    assert runtime.status == "idle"
    assert runtime.session_id == ""
    assert runtime.game_id == ""
    assert not (bridge_root / game_id / "session.json").exists()
    assert writer.emit_line("Yukino: late line.", ts="2026-04-29T03:00:03Z") is False
    events = _read_events(bridge_root / game_id / "events.jsonl")
    assert [event["type"] for event in events] == [
        "session_started",
        "line_changed",
        "session_ended",
    ]


def test_ocr_writer_advance_visual_scene_before_session_is_nonfatal(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)

    scene_id = writer.advance_visual_scene(ts="2026-04-29T03:00:00Z")

    assert scene_id == "ocr:unknown:scene-0001"
    assert writer.session_id == ""


def test_ocr_writer_scene_change_clears_previous_dialogue_state(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])

    assert writer.emit_line("Yukino: old line.", ts="2026-04-29T03:00:01Z") is True
    old_line_id = str(writer.current_state["line_id"])
    scene_id = writer.advance_visual_scene(
        ts="2026-04-29T03:00:02Z",
        background_hash="ffffffffffffffff",
    )
    state = writer.current_state

    assert state["scene_id"] == scene_id
    assert state["speaker"] == ""
    assert state["text"] == ""
    assert state["line_id"] == ""
    assert writer.emit_choices(["Save", "Leave"], ts="2026-04-29T03:00:03Z") is True
    assert writer.current_state["line_id"] != old_line_id


def test_ocr_reader_manager_remembers_short_lived_vision_snapshot(tmp_path: Path) -> None:
    from PIL import Image

    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    clock = {"now": 1000.0}
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            llm_vision_enabled=True,
            llm_vision_max_image_px=128,
        ),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: False,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )

    manager._remember_vision_snapshot(
        Image.new("RGB", (320, 160), color="white"),
        source="full_frame",
        now=clock["now"],
    )
    snapshot = manager.latest_vision_snapshot()

    assert snapshot["vision_image_base64"].startswith("data:image/jpeg;base64,")
    assert snapshot["width"] == 128
    assert snapshot["height"] == 64
    assert snapshot["byte_size"] > 0

    clock["now"] += 9.0

    assert manager.latest_vision_snapshot() == {}


def test_ocr_writer_line_observed_includes_confidence_and_text_source(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])

    assert writer.emit_line_observed(
        "雪乃：今天一起回家吗？",
        ts="2026-04-29T03:00:00Z",
        ocr_confidence=0.87,
        text_source="bottom_region",
    ) is True

    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    payload = events[-1]["payload"]
    assert events[-1]["type"] == "line_observed"
    assert payload["ocr_confidence"] == pytest.approx(0.87)
    assert payload["speaker_confidence"] >= 0.9
    assert payload["text_source"] == "bottom_region"


def test_ocr_choices_emit_does_not_fall_through_to_dialogue(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    window = _window()[0]
    writer.start_session(window)
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    manager._default_ocr_state.last_raw_text = "去东院！\n去西院！"
    manager._default_ocr_state.repeat_count = 1

    assert manager._consume_ocr_text("1. 去东院！\n2. 去西院！", now=3000.0) is True

    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    session = read_session_json(bridge_root / writer.game_id / "session.json").session

    assert events[-1]["type"] == "choices_shown"
    assert session is not None
    assert session["state"]["stability"] == "choices"
    assert session["state"]["is_menu_open"] is True
    assert [item["text"] for item in session["state"]["choices"]] == ["去东院！", "去西院！"]


def test_ocr_choice_candidates_do_not_pollute_dialogue_stability(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )

    assert manager._consume_ocr_text("1. Save her.\n2. Leave.", now=3000.0) is False
    assert manager._consume_ocr_text("1. Save her.\n2. Leave.", now=3001.0) is True

    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    session = read_session_json(bridge_root / writer.game_id / "session.json").session

    assert [event["type"] for event in events] == ["session_started", "choices_shown"]
    assert session is not None
    assert session["state"]["stability"] == "choices"
    assert [item["text"] for item in session["state"]["choices"]] == ["Save her.", "Leave."]


def test_ocr_session_snapshot_write_failure_is_nonfatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    logger = _CapturingLogger()
    writer = OcrReaderBridgeWriter(
        bridge_root=bridge_root,
        time_fn=lambda: 3000.0,
        logger=logger,
    )

    def _fail_replace(src, dst):
        del src, dst
        raise OSError("disk full")

    monkeypatch.setattr(galgame_ocr_reader.os, "replace", _fail_replace)

    window = _window()[0]
    expected_game_id = galgame_ocr_reader._ocr_game_id_from_process(
        window.process_name or window.title
    )

    with pytest.raises(RuntimeError, match="session snapshot write failed"):
        writer.start_session(window)

    assert logger.warnings
    assert logger.warnings[0][0] == "ocr_reader session snapshot write failed: {}"
    assert not (bridge_root / expected_game_id / "events.jsonl").exists()


def test_ocr_choice_candidates_use_single_read_threshold_when_requested(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )

    assert (
        manager._consume_ocr_text(
            "1. Save her.\n2. Leave.",
            now=3000.0,
            line_repeat_threshold=1,
        )
        is True
    )

    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    assert events[-1]["type"] == "choices_shown"


def test_aihong_choice_region_filter_keeps_middle_capture_boxes() -> None:
    boxes = [
        OcrTextBox("TheLamentingGeese口×", 10, 10, 280, 32, 0.9),
        OcrTextBox("爽快给他钱手", 430, 180, 620, 212, 0.92),
        OcrTextBox("不给钱手", 450, 250, 580, 282, 0.91),
        OcrTextBox("银两剩余：5两", 20, 500, 230, 532, 0.88),
        OcrTextBox("嗯...我该给他钱吗？", 250, 650, 780, 690, 0.9),
    ]

    filtered = galgame_ocr_reader._filter_boxes_to_region(
        boxes,
        source_height=720,
        top_ratio=0.20,
        bottom_inset_ratio=0.40,
    )

    assert [box.text for box in filtered] == ["爽快给他钱手", "不给钱手"]


def test_aihong_menu_stage_filters_fullscreen_ocr_to_choice_region(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    boxes = [
        OcrTextBox("TheLamentingGeese口×", 10, 10, 280, 32, 0.9),
        OcrTextBox("爽快给他钱手", 430, 180, 620, 212, 0.92),
        OcrTextBox("不给钱手", 450, 250, 580, 282, 0.91),
        OcrTextBox("银两剩余：5两", 20, 500, 230, 532, 0.88),
        OcrTextBox("嗯...我该给他钱吗？", 250, 650, 780, 690, 0.9),
    ]
    raw_text = "\n".join(box.text for box in boxes)

    result = manager._consume_aihong_menu_stage_text(
        raw_text,
        now=3000.0,
        boxes=boxes,
        choice_bounds_metadata={
            "source_size": {"width": 1280, "height": 720},
            "window_rect": {"left": 0, "top": 0, "right": 1280, "bottom": 720},
        },
        choice_repeat_threshold=1,
    )

    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    payload_choices = events[-1]["payload"]["choices"]

    assert result.emitted_kind == "choices"
    assert events[-1]["type"] == "choices_shown"
    assert [item["text"] for item in payload_choices] == ["爽快给他钱", "不给钱"]
    assert [item["bounds"]["top"] for item in payload_choices] == [180.0, 250.0]


def test_ocr_stability_keys_use_character_distance_not_prefix() -> None:
    assert galgame_ocr_reader._ocr_stability_keys_match("abcdefgh", "abcdefgh") is True
    assert galgame_ocr_reader._ocr_stability_keys_match("abcdefgh", "abcxefgh") is True
    assert galgame_ocr_reader._ocr_stability_keys_match("abcdefgh", "abcdefghi") is False
    assert galgame_ocr_reader._ocr_stability_keys_match("abcdefgh", "abcdwxyz") is False


def test_rapidocr_low_confidence_tokens_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    raw_output = [
        [
            [[0.0, 0.0], [30.0, 0.0], [30.0, 12.0], [0.0, 12.0]],
            "noise",
            0.12,
        ]
    ]

    with caplog.at_level(logging.DEBUG, logger=galgame_ocr_reader._LOGGER.name):
        tokens = galgame_ocr_reader._rapidocr_tokens_from_output(raw_output)

    assert tokens == []
    assert "rapidocr discarded 1 low-confidence token(s)" in caplog.text


def test_ocr_overlay_guard_does_not_drop_short_english_dialogue() -> None:
    assert galgame_ocr_reader._looks_like_game_overlay_text("I must save her.") is False
    assert galgame_ocr_reader._looks_like_game_overlay_text("Save her.") is False
    assert galgame_ocr_reader._looks_like_game_overlay_text("The system is collapsing.") is False
    assert galgame_ocr_reader._looks_like_game_overlay_text("Don't skip the ceremony.") is False
    assert galgame_ocr_reader._looks_like_game_overlay_text("Save") is True
    assert galgame_ocr_reader._looks_like_game_overlay_text("Save\nLoad") is True
    assert galgame_ocr_reader._looks_like_game_overlay_text("Auto Save") is True
    assert galgame_ocr_reader._looks_like_game_overlay_text("Quick Save") is True
    assert galgame_ocr_reader._looks_like_game_overlay_text("Fast Forward") is True


def test_ocr_overlay_guard_does_not_drop_chinese_dialogue_with_menu_words() -> None:
    assert galgame_ocr_reader._looks_like_game_overlay_text("这是自动校准命中的对白文本。") is False
    assert galgame_ocr_reader._looks_like_game_overlay_text("系统已经崩溃了。") is False
    assert galgame_ocr_reader._looks_like_game_overlay_text("菜单上的名字忽然亮了起来。") is False
    assert galgame_ocr_reader._looks_like_game_overlay_text("自动") is True
    assert galgame_ocr_reader._looks_like_game_overlay_text("菜单\n设置") is True
    assert galgame_ocr_reader._looks_like_game_overlay_text("系统设置") is True


def test_ocr_reader_auto_target_excludes_razer_monitor_window() -> None:
    candidate = DetectedGameWindow(
        hwnd=67284,
        title="RzMonitorForegroundWindow",
        process_name="RazerAppEngine.exe",
        pid=8200,
        class_name="RzMonitorForegroundWindowClass",
        exe_path=r"C:\Program Files\Razer\RazerAppEngine\RazerAppEngine.exe",
        width=0,
        height=0,
    )

    classified = galgame_ocr_reader._classify_window_candidate(candidate)

    assert classified.eligible is False
    assert classified.exclude_reason in {"excluded_helper_window", "excluded_non_game_process"}


def test_ocr_window_candidate_marks_minimized_window_as_excluded() -> None:
    candidate = DetectedGameWindow(
        hwnd=197524,
        title="TheLamentingGeese",
        process_name="TheLamentingGeese.exe",
        pid=30412,
        class_name="UnityWndClass",
        width=160,
        height=28,
        area=4480,
        is_minimized=True,
    )

    classified = galgame_ocr_reader._classify_window_candidate(candidate)

    assert classified.eligible is False
    assert classified.exclude_reason == "excluded_minimized_window"
    assert classified.category == "excluded_minimized_window"
    assert classified.to_dict()["is_minimized"] is True


def test_ocr_window_candidate_minimized_reason_takes_priority_over_small_window() -> None:
    candidate = DetectedGameWindow(
        hwnd=197524,
        title="TheLamentingGeese",
        process_name="TheLamentingGeese.exe",
        pid=30412,
        class_name="UnityWndClass",
        width=160,
        height=28,
        area=4480,
        is_minimized=True,
    )

    classified = galgame_ocr_reader._classify_window_candidate(candidate)

    assert classified.exclude_reason == "excluded_minimized_window"
    assert classified.exclude_reason != "excluded_small_or_hidden_window"


def test_default_window_scanner_retains_minimized_visible_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hwnd = 197524
    pid = 30412

    class _FakeWin32Gui:
        @staticmethod
        def IsWindowVisible(value: int) -> bool:
            return value == hwnd

        @staticmethod
        def IsIconic(value: int) -> bool:
            return value == hwnd

        @staticmethod
        def GetWindowRect(value: int) -> tuple[int, int, int, int]:
            assert value == hwnd
            return (-32000, -32000, -31840, -31972)

        @staticmethod
        def GetWindowText(value: int) -> str:
            assert value == hwnd
            return "TheLamentingGeese"

        @staticmethod
        def GetClassName(value: int) -> str:
            assert value == hwnd
            return "UnityWndClass"

        @staticmethod
        def EnumWindows(callback, payload) -> None:
            callback(hwnd, payload)

    class _FakeWin32Process:
        @staticmethod
        def GetWindowThreadProcessId(value: int) -> tuple[int, int]:
            assert value == hwnd
            return (1, pid)

    class _FakeProcess:
        def __init__(self, value: int) -> None:
            assert value == pid

        def name(self) -> str:
            return "TheLamentingGeese.exe"

        def exe(self) -> str:
            return r"C:\Games\TheLamentingGeese.exe"

    class _FakePsutil:
        Process = _FakeProcess

    monkeypatch.setitem(sys.modules, "win32gui", _FakeWin32Gui)
    monkeypatch.setitem(sys.modules, "win32process", _FakeWin32Process)
    monkeypatch.setattr(galgame_ocr_reader, "psutil", _FakePsutil)
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 0)

    results = galgame_ocr_reader._default_window_scanner()

    assert len(results) == 1
    assert results[0].is_minimized is True
    assert results[0].eligible is False
    assert results[0].exclude_reason == "excluded_minimized_window"
    assert results[0].to_dict()["is_minimized"] is True


def test_ocr_select_target_window_reports_memory_reader_minimized_window(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        platform_fn=lambda: True,
        window_scanner=lambda: [],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    minimized = galgame_ocr_reader._classify_window_candidate(
        DetectedGameWindow(
            hwnd=197524,
            title="TheLamentingGeese",
            process_name="TheLamentingGeese.exe",
            pid=30412,
            class_name="UnityWndClass",
            width=160,
            height=28,
            area=4480,
            is_minimized=True,
        )
    )

    selection = manager._select_target_window(
        [],
        excluded_windows=[minimized],
        memory_reader_runtime={
            "pid": 30412,
            "process_name": "TheLamentingGeese.exe",
        },
    )

    assert selection.target is None
    assert selection.selection_detail == "memory_reader_window_minimized"
    assert selection.last_exclude_reason == "excluded_minimized_window"


def test_ocr_select_target_window_does_not_pick_unrelated_window_when_memory_target_minimized(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        platform_fn=lambda: True,
        window_scanner=lambda: [],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    minimized = galgame_ocr_reader._classify_window_candidate(
        DetectedGameWindow(
            hwnd=197524,
            title="TheLamentingGeese",
            process_name="TheLamentingGeese.exe",
            pid=30412,
            class_name="UnityWndClass",
            width=160,
            height=28,
            area=4480,
            is_minimized=True,
        )
    )
    unrelated = DetectedGameWindow(
        hwnd=300,
        title="Other Window",
        process_name="OtherGame.exe",
        pid=5555,
        width=1280,
        height=720,
        area=921600,
    )

    selection = manager._select_target_window(
        [unrelated],
        excluded_windows=[minimized],
        memory_reader_runtime={
            "pid": 30412,
            "process_name": "TheLamentingGeese.exe",
        },
    )

    assert selection.target is None
    assert selection.selection_detail == "memory_reader_window_minimized"
    assert selection.last_exclude_reason == "excluded_minimized_window"


@pytest.mark.asyncio
async def test_ocr_reader_backpressure_skip_is_not_capture_error(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            install_target_dir=str(install_root),
            rapidocr_enabled=True,
        ),
        time_fn=lambda: 3001.0,
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=102,
                title="Demo Window",
                process_name="DemoGame.exe",
                pid=4243,
                width=1280,
                height=720,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3001.0),
    )
    pending: Future[OcrExtractionResult] = Future()
    with manager._capture_worker_lock:
        manager._capture_future = pending
        manager._capture_future_started_at = time.monotonic()
        manager._capture_future_timed_out = False
    manager._last_capture_error = "previous capture failed"
    manager._runtime.detail = "capture_failed"
    manager._runtime.last_capture_error = "previous capture failed"

    try:
        result = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

        assert result.runtime["detail"] == "capture_backpressure"
        assert result.runtime["last_capture_error"] == ""
        assert any("tick skipped" in warning and "still running" in warning for warning in result.warnings)
    finally:
        pending.cancel()
        await manager.shutdown()


@pytest.mark.asyncio
async def test_ocr_reader_capture_timeout_skips_stuck_worker_immediately(
    tmp_path: Path,
    ocr_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    started = threading.Event()
    release = threading.Event()

    class _BlockingOcrBackend(_FakeOcrBackend):
        def extract_text(self, image: str) -> str:
            del image
            self.calls += 1
            if self.calls == 1:
                started.set()
                release.wait(timeout=TEST_WAIT_TIMEOUT)
            return "雪乃：恢复后的台词。"

    backend = _BlockingOcrBackend()
    logger = _CapturingLogger()
    monkeypatch.setattr(galgame_ocr_reader, "_OCR_CAPTURE_TIMEOUT_SECONDS", 0.01)
    manager = OcrReaderManager(
        logger=logger,
        config=_make_config(
            bridge_root,
            install_target_dir=str(install_root),
            rapidocr_enabled=True,
        ),
        time_fn=lambda: 3001.0,
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=102,
                title="Demo Window",
                process_name="DemoGame.exe",
                pid=4243,
                width=1280,
                height=720,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=backend,
        writer=OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3001.0),
    )

    try:
        first = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
        assert started.wait(timeout=TEST_WAIT_TIMEOUT) is True
        assert first.runtime["detail"] == "capture_failed"
        assert "timed out" in first.runtime["last_capture_error"]

        second = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

        assert backend.calls == 1
        assert second.runtime["detail"] == "capture_backpressure"
        assert second.runtime["last_capture_error"] == ""
        assert not any(
            warning[0] == "ocr_reader replacing stuck capture worker after %.1fs timeout"
            for warning in logger.warnings
        )

        with manager._capture_worker_lock:
            manager._capture_future_started_at = time.monotonic() - 1.0
        third = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

        assert backend.calls == 2
        assert third.runtime["detail"] == "receiving_text"
        assert any(
            warning[0]
            == "ocr_reader rotating timed-out capture executor after {:.1f}s; cancel_requested={}"
            for warning in logger.warnings
        )
    finally:
        release.set()
        await manager.shutdown()


@pytest.mark.asyncio
async def test_ocr_reader_capture_timeout_recovers_cancellable_worker(
    tmp_path: Path,
    ocr_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    backend = _FakeOcrBackend(["雪乃：恢复后的台词。"])
    logger = _CapturingLogger()
    monkeypatch.setattr(galgame_ocr_reader, "_OCR_CAPTURE_TIMEOUT_SECONDS", 0.01)
    manager = OcrReaderManager(
        logger=logger,
        config=_make_config(
            bridge_root,
            install_target_dir=str(install_root),
            rapidocr_enabled=True,
        ),
        time_fn=lambda: 3001.0,
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=102,
                title="Demo Window",
                process_name="DemoGame.exe",
                pid=4243,
                width=1280,
                height=720,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=backend,
        writer=OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3001.0),
    )
    pending: Future[OcrExtractionResult] = Future()
    with manager._capture_worker_lock:
        manager._capture_future = pending
        manager._capture_future_started_at = time.monotonic() - 1.0
        manager._capture_future_timed_out = True

    try:
        result = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

        assert pending.cancelled()
        assert backend.calls == 1
        assert result.runtime["detail"] == "receiving_text"
        assert any(
            warning[0]
            == "ocr_reader rotating timed-out capture executor after {:.1f}s; cancel_requested={}"
            for warning in logger.warnings
        )
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_ocr_reader_capture_timeout_recovery_is_bounded(
    tmp_path: Path,
    ocr_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    backend = _FakeOcrBackend(["雪乃：不应执行。"])
    logger = _CapturingLogger()
    monkeypatch.setattr(galgame_ocr_reader, "_OCR_CAPTURE_TIMEOUT_SECONDS", 0.01)
    manager = OcrReaderManager(
        logger=logger,
        config=_make_config(
            bridge_root,
            install_target_dir=str(install_root),
            rapidocr_enabled=True,
        ),
        time_fn=lambda: 3001.0,
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=102,
                title="Demo Window",
                process_name="DemoGame.exe",
                pid=4243,
                width=1280,
                height=720,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=backend,
        writer=OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3001.0),
    )
    abandoned: Future[OcrExtractionResult] = Future()
    assert abandoned.set_running_or_notify_cancel()
    current: Future[OcrExtractionResult] = Future()
    assert current.set_running_or_notify_cancel()
    abandoned_executor = ThreadPoolExecutor(max_workers=1)
    current_executor = manager._capture_executor
    assert current_executor is not None
    with manager._capture_worker_lock:
        manager._abandoned_capture_workers = [(abandoned_executor, abandoned)]
        manager._capture_future = current
        manager._capture_future_started_at = time.monotonic() - 1.0
        manager._capture_future_timed_out = True

    try:
        result = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

        assert backend.calls == 0
        assert result.runtime["detail"] == "capture_failed"
        assert "recovery limit reached" in result.runtime["last_capture_error"]
        assert any(
            "capture timed out" in warning and "recovery limit reached" in warning
            for warning in result.warnings
        )
        with manager._capture_worker_lock:
            assert manager._capture_future is current
            assert manager._capture_executor is current_executor
            assert manager._capture_future_timed_out is True
            assert manager._abandoned_capture_workers == [
                (abandoned_executor, abandoned),
            ]

        current.set_result(OcrExtractionResult(text="late"))
        retry = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

        assert backend.calls == 1
        assert retry.runtime["detail"] == "receiving_text"
    finally:
        await manager.shutdown()


def test_rapidocr_line_grouping_uses_nearest_candidate_line() -> None:
    def box(left: int, top: int, right: int, bottom: int) -> list[list[int]]:
        return [[left, top], [right, top], [right, bottom], [left, bottom]]

    lines = galgame_ocr_reader._rapidocr_lines_from_output(
        [
            (box(0, 85, 10, 115), "上", 0.9),
            (box(0, 105, 10, 135), "下", 0.9),
            (box(15, 106, 25, 120), "近", 0.9),
        ]
    )

    assert [text for text, _score, _box in lines] == ["上", "下近"]


def test_rapidocr_short_text_score_filter_uses_text_weighted_average() -> None:
    def box(left: int, top: int, right: int, bottom: int) -> list[list[int]]:
        return [[left, top], [right, top], [right, bottom], [left, bottom]]

    output = [
        (box(0, 0, 20, 10), "雪乃", 0.70),
        (box(0, 20, 5, 30), ".", 0.30),
    ]

    assert _rapidocr_text_from_output(output) == "雪乃\n."


def test_aihong_menu_choice_parser_accepts_plain_text_without_status_lines() -> None:
    assert galgame_ocr_reader._coerce_aihong_menu_choices(
        ["往南跑", "躲进巷子里"]
    ) == ["往南跑", "躲进巷子里"]


def test_aihong_menu_choice_parser_rejects_unpunctuated_chinese_dialogue() -> None:
    assert (
        galgame_ocr_reader._coerce_aihong_menu_choices(
            ["你今天去哪里", "我还没想好", "不如一起走"]
        )
        == []
    )


def test_aihong_menu_choice_parser_keeps_prefixed_choices_with_pronouns() -> None:
    assert galgame_ocr_reader._coerce_aihong_menu_choices(
        ["1. 我选择留下", "2. 你先走吧"]
    ) == ["我选择留下", "你先走吧"]


def test_aihong_menu_choice_parser_can_disable_plain_text_choices() -> None:
    assert (
        galgame_ocr_reader._coerce_aihong_menu_choices(
            ["往南跑", "躲进巷子里"],
            allow_plain_text=False,
        )
        == []
    )
    assert galgame_ocr_reader._coerce_aihong_menu_choices(
        ["1. 往南跑", "2. 躲进巷子里"],
        allow_plain_text=False,
    ) == ["往南跑", "躲进巷子里"]


def test_capture_image_hash_normalizes_non_rgb_frames() -> None:
    from PIL import Image

    rgb = Image.new("RGB", (16, 16), (32, 64, 128))
    rgba = Image.new("RGBA", (16, 16), (32, 64, 128, 255))

    assert OcrReaderManager._capture_image_hash(rgb) == OcrReaderManager._capture_image_hash(rgba)


def test_capture_image_hash_rejects_non_image_frames() -> None:
    assert OcrReaderManager._capture_image_hash(object()) == ""


def test_capture_image_hash_accepts_stable_text_frames() -> None:
    assert OcrReaderManager._capture_image_hash("frame:1")
    assert OcrReaderManager._capture_image_hash("frame:1") == OcrReaderManager._capture_image_hash(
        "frame:1"
    )


def test_perceptual_hash_width_matches_requested_size() -> None:
    from PIL import Image

    image = Image.new("RGB", (16, 16), (255, 255, 255))

    assert len(galgame_ocr_reader._perceptual_hash_image(image, size=4)) == 4
    assert len(galgame_ocr_reader._perceptual_hash_image(image, size=8)) == 16
    assert len(galgame_ocr_capture._perceptual_hash_image(image, size=4)) == 4
    assert len(galgame_ocr_capture._perceptual_hash_image(image, size=8)) == 16


def test_ocr_compat_modules_reexport_reader_implementations() -> None:
    assert galgame_ocr_capture.Win32CaptureBackend is galgame_ocr_reader.Win32CaptureBackend
    assert galgame_ocr_capture._perceptual_hash_image is galgame_ocr_reader._perceptual_hash_image
    assert galgame_ocr_capture.CAPTURE_BACKEND_AUTO == galgame_ocr_reader._CAPTURE_BACKEND_AUTO

    assert galgame_ocr_backends.RapidOcrBackend is galgame_ocr_reader.RapidOcrBackend
    assert not hasattr(galgame_ocr_backends, "TesseractOcrBackend")
    assert galgame_ocr_backends._rapidocr_inference_lock() is galgame_ocr_reader._RAPIDOCR_INFERENCE_LOCK

    assert galgame_ocr_capture.__getattr__("utc_now_iso") is galgame_ocr_reader.utc_now_iso
    assert galgame_ocr_backends.__getattr__("_weighted_ocr_score") is galgame_ocr_reader._weighted_ocr_score
    with pytest.raises(AttributeError):
        galgame_ocr_capture.__getattr__("_missing_capture_symbol")
    with pytest.raises(AttributeError):
        galgame_ocr_backends.__getattr__("_missing_backend_symbol")

    key = galgame_ocr_backends._rapidocr_runtime_cache_key(
        install_target_dir_raw="compat-test",
        engine_type="onnxruntime",
        lang_type="japan",
        model_type="mobile",
        ocr_version="compat",
    )
    runtime = object()
    now = time.monotonic()
    galgame_ocr_backends._store_shared_rapidocr_runtime(key, runtime, now=now)

    assert galgame_ocr_backends._shared_rapidocr_runtime(key, now=now) is runtime


def test_ocr_line_id_collision_suffix_has_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(galgame_ocr_reader, "_OCR_LINE_ID_MAX_COLLISION_SUFFIX", 2)
    monkeypatch.setattr(galgame_ocr_bridge_writer, "_OCR_LINE_ID_MAX_COLLISION_SUFFIX", 2)
    writer = OcrReaderBridgeWriter(bridge_root=tmp_path)
    text = "same normalized line"
    normalized = galgame_ocr_reader.normalize_text(text)
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    widths = list(range(12, len(digest) + 1, 4))
    if widths[-1] != len(digest):
        widths.append(len(digest))
    for width in widths:
        writer._line_id_owner[f"ocr:{digest[:width]}"] = "other"
    for suffix in range(1, 3):
        writer._line_id_owner[f"ocr:{digest}#{suffix}"] = "other"

    with pytest.raises(RuntimeError, match="collision limit"):
        writer._line_id_for_text(text)


def test_background_hash_excludes_bottom_dialogue_region() -> None:
    from PIL import Image, ImageDraw

    background_profile = OcrReaderManager._background_capture_profile()
    assert background_profile.top_ratio == pytest.approx(0.0)
    assert background_profile.bottom_inset_ratio == pytest.approx(0.60)

    top_color = (20, 80, 140)
    first = Image.new("RGB", (160, 100), top_color)
    second = Image.new("RGB", (160, 100), top_color)
    draw_first = ImageDraw.Draw(first)
    draw_second = ImageDraw.Draw(second)
    draw_first.rectangle((0, 60, 159, 99), fill=(255, 255, 255))
    draw_second.rectangle((0, 60, 159, 99), fill=(0, 0, 0))

    profile = galgame_ocr_reader.OcrCaptureProfile(
        left_inset_ratio=0.0,
        right_inset_ratio=0.0,
        top_ratio=0.6,
        bottom_inset_ratio=0.0,
    )
    cropped_first = galgame_ocr_reader._crop_window_image(
        first,
        window_rect=(0, 0, 160, 100),
        profile=profile,
        backend_kind="test",
        backend_detail="test",
    )
    cropped_second = galgame_ocr_reader._crop_window_image(
        second,
        window_rect=(0, 0, 160, 100),
        profile=profile,
        backend_kind="test",
        backend_detail="test",
    )

    assert (
        cropped_first.info["galgame_source_background_hash"]
        == cropped_second.info["galgame_source_background_hash"]
    )
    assert cropped_first.info["galgame_full_frame_image"] is first
    assert cropped_second.info["galgame_full_frame_image"] is second


def test_screen_capture_rect_uses_client_area_and_clips_taskbar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _window()[0]
    for mod in (galgame_ocr_reader, galgame_ocr_capture_backends, galgame_ocr_capture_helpers):
        monkeypatch.setattr(mod, "_target_client_rect", lambda _t: (5, 92, 1301, 1060))
        monkeypatch.setattr(mod, "_target_window_uses_overlapped_chrome", lambda _t: True)
        monkeypatch.setattr(mod, "_target_monitor_work_rects", lambda _r: [])
        monkeypatch.setattr(mod, "_target_monitor_work_rect", lambda _t: (0, 0, 1920, 1040))

    rect = galgame_ocr_reader._target_screen_capture_rect(target)

    assert rect == (5, 92, 1301, 1040)


def test_screen_capture_rect_spanning_monitors_keeps_other_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _window()[0]
    monkeypatch.setattr(
        galgame_ocr_reader,
        "_target_client_rect",
        lambda _target: (1800, 100, 2600, 1060),
    )
    monkeypatch.setattr(
        galgame_ocr_capture_backends,
        "_target_client_rect",
        lambda _target: (1800, 100, 2600, 1060),
    )
    monkeypatch.setattr(
        galgame_ocr_capture_helpers,
        "_target_client_rect",
        lambda _target: (1800, 100, 2600, 1060),
    )
    monkeypatch.setattr(
        galgame_ocr_reader,
        "_target_window_uses_overlapped_chrome",
        lambda _target: True,
    )
    monkeypatch.setattr(
        galgame_ocr_capture_backends,
        "_target_window_uses_overlapped_chrome",
        lambda _target: True,
    )
    monkeypatch.setattr(
        galgame_ocr_capture_helpers,
        "_target_window_uses_overlapped_chrome",
        lambda _target: True,
    )
    monkeypatch.setattr(
        galgame_ocr_reader,
        "_target_monitor_work_rects",
        lambda _rect: [(0, 0, 1920, 1040), (1920, 0, 3840, 1080)],
    )
    monkeypatch.setattr(
        galgame_ocr_capture_backends,
        "_target_monitor_work_rects",
        lambda _rect: [(0, 0, 1920, 1040), (1920, 0, 3840, 1080)],
    )
    monkeypatch.setattr(
        galgame_ocr_capture_helpers,
        "_target_monitor_work_rects",
        lambda _rect: [(0, 0, 1920, 1040), (1920, 0, 3840, 1080)],
    )
    monkeypatch.setattr(
        galgame_ocr_reader,
        "_target_monitor_work_rect",
        lambda _target: (0, 0, 1920, 1040),
    )
    monkeypatch.setattr(
        galgame_ocr_capture_backends,
        "_target_monitor_work_rect",
        lambda _target: (0, 0, 1920, 1040),
    )
    monkeypatch.setattr(
        galgame_ocr_capture_helpers,
        "_target_monitor_work_rect",
        lambda _target: (0, 0, 1920, 1040),
    )

    rect = galgame_ocr_reader._target_screen_capture_rect(target)

    assert rect == (1800, 100, 2600, 1060)


def test_screen_capture_rect_ignores_invalid_monitor_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _window()[0]
    monkeypatch.setattr(
        galgame_ocr_reader,
        "_target_client_rect",
        lambda _target: (10, 20, 800, 600),
    )
    monkeypatch.setattr(
        galgame_ocr_capture_backends,
        "_target_client_rect",
        lambda _target: (10, 20, 800, 600),
    )
    monkeypatch.setattr(
        galgame_ocr_capture_helpers,
        "_target_client_rect",
        lambda _target: (10, 20, 800, 600),
    )
    monkeypatch.setattr(
        galgame_ocr_reader,
        "_target_window_uses_overlapped_chrome",
        lambda _target: True,
    )
    monkeypatch.setattr(
        galgame_ocr_capture_backends,
        "_target_window_uses_overlapped_chrome",
        lambda _target: True,
    )
    monkeypatch.setattr(
        galgame_ocr_capture_helpers,
        "_target_window_uses_overlapped_chrome",
        lambda _target: True,
    )
    monkeypatch.setitem(
        sys.modules,
        "win32api",
        SimpleNamespace(EnumDisplayMonitors=lambda *_args: [object()]),
    )

    rect = galgame_ocr_reader._target_screen_capture_rect(target)

    assert rect == (10, 20, 800, 600)


def test_printwindow_client_crop_keeps_profile_coordinates_in_client_space() -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (120, 100), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 119, 19), fill=(255, 0, 0))
    draw.rectangle((0, 20, 119, 99), fill=(0, 255, 0))

    client_image = galgame_ocr_reader._crop_image_to_screen_rect(
        image,
        image_rect=(10, 10, 130, 110),
        crop_rect=(10, 30, 130, 110),
    )
    cropped = galgame_ocr_reader._crop_window_image(
        client_image,
        window_rect=(10, 30, 130, 110),
        profile=galgame_ocr_reader.OcrCaptureProfile(
            left_inset_ratio=0.0,
            right_inset_ratio=0.0,
            top_ratio=0.0,
            bottom_inset_ratio=0.0,
        ),
        backend_kind="test",
        backend_detail="test",
    )

    assert cropped.size == (120, 80)
    assert cropped.getpixel((0, 0)) == (0, 255, 0)
    assert cropped.info["galgame_window_rect"] == {
        "left": 10.0,
        "top": 30.0,
        "right": 130.0,
        "bottom": 110.0,
    }


def test_printwindow_capture_crops_content_rect_after_full_window_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image, ImageDraw

    full_image = Image.new("RGB", (120, 100), (255, 0, 0))
    draw = ImageDraw.Draw(full_image)
    draw.rectangle((0, 20, 119, 99), fill=(0, 255, 0))
    target = DetectedGameWindow(hwnd=101, width=120, height=100)
    captured: list[tuple[int, tuple[int, int, int, int]]] = []

    def _raise_screen_rect(_target: DetectedGameWindow) -> tuple[int, int, int, int]:
        raise RuntimeError("screen rect unavailable")

    def _capture_full_window(
        _self: object,
        hwnd: int,
        window_rect: tuple[int, int, int, int],
    ):
        captured.append((hwnd, window_rect))
        return full_image.copy()

    monkeypatch.setattr(
        galgame_printwindow_backend,
        "_require_visible_capture_target",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        galgame_printwindow_backend,
        "_target_screen_capture_rect",
        _raise_screen_rect,
    )
    monkeypatch.setattr(
        galgame_printwindow_backend,
        "_target_content_rect",
        lambda _target: (10, 30, 130, 110),
    )
    monkeypatch.setattr(
        galgame_printwindow_backend,
        "_target_window_rect",
        lambda _target: (10, 10, 130, 110),
    )
    monkeypatch.setattr(
        galgame_printwindow_backend.PrintWindowCaptureBackend,
        "_capture_full_window",
        _capture_full_window,
    )

    image = galgame_printwindow_backend.PrintWindowCaptureBackend().capture_frame(
        target,
        OcrCaptureProfile(
            left_inset_ratio=0.0,
            right_inset_ratio=0.0,
            top_ratio=0.0,
            bottom_inset_ratio=0.0,
        ),
    )

    assert captured == [(101, (10, 10, 130, 110))]
    assert image.size == (120, 80)
    assert image.getpixel((0, 0)) == (0, 255, 0)


def test_galgame_printwindow_releases_window_dc_without_deleting_wrapped_hdc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    previous_bitmap = object()

    class _Bitmap:
        def CreateCompatibleBitmap(self, _source_dc, width: int, height: int) -> None:
            calls.append(f"create_bitmap:{width}x{height}")

        def GetInfo(self) -> dict[str, int]:
            return {"bmWidth": 2, "bmHeight": 2}

        def GetBitmapBits(self, _as_bytes: bool) -> bytes:
            return b"\x00" * 16

        def GetHandle(self) -> int:
            return 123

    bitmap = _Bitmap()

    class _MemDc:
        def SelectObject(self, obj):
            calls.append("restore_bitmap" if obj is previous_bitmap else "select_bitmap")
            return previous_bitmap

        def GetSafeHdc(self) -> int:
            return 456

        def BitBlt(self, *_args) -> None:
            calls.append("bitblt")

        def DeleteDC(self) -> None:
            calls.append("mem_delete")

    mem_dc = _MemDc()

    class _SourceDc:
        def CreateCompatibleDC(self):
            return mem_dc

        def DeleteDC(self) -> None:
            calls.append("source_delete")

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
            CreateDCFromHandle=lambda _hdc: _SourceDc(),
            CreateBitmap=lambda: bitmap,
        ),
    )
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(SRCCOPY=1))
    monkeypatch.setattr(
        galgame_printwindow_backend.sys,
        "getwindowsversion",
        lambda: SimpleNamespace(major=6, minor=2),
        raising=False,
    )

    image = galgame_printwindow_backend.PrintWindowCaptureBackend._capture_full_window(
        1234,
        (0, 0, 2, 2),
    )

    assert image.size == (2, 2)
    assert calls == [
        "create_bitmap:2x2",
        "select_bitmap",
        "bitblt",
        "restore_bitmap",
        "mem_delete",
        "delete_object",
        "release_dc",
    ]
    assert "source_delete" not in calls


def test_ocr_capture_samples_dialogue_background_hash_at_low_frequency(tmp_path: Path) -> None:
    now = {"value": 1000.0}
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(tmp_path / "bridge"),
        time_fn=lambda: now["value"],
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(
            [
                "雪乃：第一句台词。",
                "雪乃：第二句台词。",
                "雪乃：第三句台词。",
            ]
        ),
    )
    manager._last_background_hash_capture_at = now["value"]

    first = manager._capture_and_extract_text(
        _window()[0],
        OcrCaptureProfile(),
        SelectedOcrBackendPlan(),
        collect_background_hash=True,
        allow_separate_background_capture=True,
    )
    now["value"] += 1.0
    second = manager._capture_and_extract_text(
        _window()[0],
        OcrCaptureProfile(),
        SelectedOcrBackendPlan(),
        collect_background_hash=True,
        allow_separate_background_capture=True,
    )
    now["value"] += 1.1
    third = manager._capture_and_extract_text(
        _window()[0],
        OcrCaptureProfile(),
        SelectedOcrBackendPlan(),
        collect_background_hash=True,
        allow_separate_background_capture=True,
    )

    assert first.timing["background_hash_skipped"] is True
    assert second.timing["background_hash_skipped"] is True
    assert third.timing["background_hash_skipped"] is False
    assert len(capture_backend.capture_calls) == 4


def test_ocr_capture_keeps_full_frame_for_vision_classifier(tmp_path: Path) -> None:
    from PIL import Image

    full_frame = Image.new("RGB", (200, 120), "navy")
    cropped_frame = Image.new("RGB", (200, 48), "black")
    cropped_frame.info["galgame_full_frame_image"] = full_frame

    class _ImageCaptureBackend(_FakeCaptureBackend):
        def capture_frame(self, target: DetectedGameWindow, profile) -> object:
            self.capture_calls.append((target.hwnd, profile.to_dict()))
            return cropped_frame

    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(tmp_path / "bridge"),
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_ImageCaptureBackend(),
        ocr_backend=_FakeOcrBackend(["我和她相识在十三年前。"]),
    )

    extraction = manager._capture_and_extract_text(
        _window()[0],
        OcrCaptureProfile(top_ratio=0.6),
        SelectedOcrBackendPlan(),
        collect_background_hash=False,
        allow_separate_background_capture=False,
    )

    assert extraction.captured_image is full_frame
    assert extraction.text == "我和她相识在十三年前。"


def test_mouse_monitor_drains_pending_events_outside_hook_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 3000.0}
    monitor = galgame_ocr_reader._MouseWheelMonitor(time_fn=lambda: clock["now"])
    monkeypatch.setattr(galgame_ocr_reader, "_foreground_window_handle", lambda: 900)
    monkeypatch.setattr(galgame_ocr_reader, "_window_handle_from_point", lambda x, y: x + y)

    monitor._enqueue_pending_event(delta=120, kind="wheel", x=10, y=20)
    clock["now"] += 1.0
    monitor._enqueue_pending_event(kind="left_click", x=30, y=40)
    clock["now"] += 1.0
    monitor._enqueue_pending_event(kind="key", key_code=0x20)

    events = monitor.events_after(0)

    assert [event.kind for event in events] == ["wheel", "left_click", "key"]
    assert [event.seq for event in events] == [1, 2, 3]
    assert events[0].delta == 120
    assert events[0].foreground_hwnd == 900
    assert events[0].point_hwnd == 30
    assert events[1].point_hwnd == 70
    assert events[2].key_code == 0x20
    assert events[2].foreground_hwnd == 900


def test_custom_ocr_backend_plan_uses_backend_availability(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    backend = _FakeOcrBackend(available=False)
    backend.kind = "fake_custom"
    backend.detail = "dependency_missing"
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        ocr_backend=backend,
    )

    plan = manager._custom_ocr_backend_plan()

    assert plan.selection == "custom"
    assert plan.primary.backend is backend
    assert plan.primary.kind == "fake_custom"
    assert plan.primary.detail == "dependency_missing"
    assert plan.primary.available is False


def test_win32_capture_backend_explicit_selection_falls_back_with_detail() -> None:
    class _SelectedBackend:
        kind = "printwindow"

        def is_available(self) -> bool:
            return False

        def capture_frame(self, target, profile):  # pragma: no cover
            raise AssertionError("unavailable backend should not capture")

    class _FallbackBackend:
        kind = "dxcam"

        def is_available(self) -> bool:
            return True

        def capture_frame(self, target, profile):
            return "fallback-frame"

    backend = galgame_ocr_reader.Win32CaptureBackend(selection="printwindow")
    backend._backends = [_SelectedBackend(), _FallbackBackend()]
    backend._printwindow_backend = backend._backends[0]

    target = _window()[0]
    target.is_foreground = True
    frame = backend.capture_frame(target, galgame_ocr_reader.OcrCaptureProfile())

    assert frame == "fallback-frame"
    assert backend.last_backend_kind == "dxcam"
    assert backend.last_backend_detail == "printwindow_unavailable_fallback"


@pytest.mark.parametrize(
    ("rect", "expected"),
    [
        ((0, 0, 1920, 1080), (True, "")),
        ((1920, 0, 3840, 1080), (False, "window_entirely_in_right_secondary_monitor")),
        ((-1920, 0, 0, 1080), (False, "window_entirely_in_left_secondary_monitor")),
        ((0, 1080, 1920, 2160), (False, "window_entirely_in_bottom_secondary_monitor")),
        ((0, -1080, 1920, 0), (False, "window_entirely_in_top_secondary_monitor")),
        ((100, 100, 2000, 1000), (False, "window_spans_across_primary_and_secondary_monitor")),
    ],
)
def test_pyautogui_primary_monitor_guard(
    monkeypatch: pytest.MonkeyPatch,
    rect: tuple[int, int, int, int],
    expected: tuple[bool, str],
) -> None:
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(size=lambda: (1920, 1080)))

    assert _is_window_on_primary_monitor(rect) == expected


def test_pyautogui_capture_uses_screenshot_region(monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image

    screenshot_calls: list[tuple[int, int, int, int]] = []

    def screenshot(*, region):
        screenshot_calls.append(region)
        return Image.new("RGB", (region[2], region[3]), "white")

    monkeypatch.setitem(
        sys.modules,
        "pyautogui",
        SimpleNamespace(size=lambda: (1920, 1080), screenshot=screenshot),
    )
    monkeypatch.setattr(galgame_pyautogui_backend, "_require_visible_capture_target", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(galgame_pyautogui_backend, "_target_screen_capture_rect", lambda _target: (10, 20, 210, 120))

    frame = galgame_ocr_reader.PyAutoGuiCaptureBackend().capture_frame(
        _window()[0],
        galgame_ocr_reader.OcrCaptureProfile(
            top_ratio=0.0,
            bottom_inset_ratio=0.0,
            left_inset_ratio=0.0,
            right_inset_ratio=0.0,
        ),
    )

    assert screenshot_calls == [(10, 20, 200, 100)]
    assert frame.size == (200, 100)


def test_pyautogui_capture_rejects_secondary_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "pyautogui",
        SimpleNamespace(size=lambda: (1920, 1080), screenshot=lambda **_kwargs: None),
    )
    monkeypatch.setattr(galgame_pyautogui_backend, "_require_visible_capture_target", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(galgame_pyautogui_backend, "_target_screen_capture_rect", lambda _target: (1920, 0, 3840, 1080))

    with pytest.raises(RuntimeError, match="pyautogui: window_entirely_in_right_secondary_monitor"):
        galgame_ocr_reader.PyAutoGuiCaptureBackend().capture_frame(
            _window()[0],
            galgame_ocr_reader.OcrCaptureProfile(),
        )


def test_smart_capture_backend_non_windows_background_uses_filtered_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Backend:
        def __init__(self, kind: str) -> None:
            self.kind = kind
            self.calls = 0

        def is_available(self) -> bool:
            return False

        def capture_frame(self, target, profile):  # pragma: no cover
            self.calls += 1
            raise AssertionError(f"{self.kind} should not capture")

    class _ElectronBackend:
        kind = "electron"

        def __init__(self) -> None:
            self.calls = 0

        def is_available(self) -> bool:
            return True

        def capture_frame(self, target, profile):
            self.calls += 1
            return "electron-frame"

    monkeypatch.setattr(sys, "platform", "linux")
    backend = galgame_ocr_reader.Win32CaptureBackend(selection="smart")
    mss = _Backend("mss")
    electron = _ElectronBackend()
    backend._printwindow_backend = _Backend("printwindow")
    backend._backends = [mss, electron]

    target = _window()[0]
    target.is_foreground = False

    frame = backend.capture_frame(target, galgame_ocr_reader.OcrCaptureProfile())

    assert frame == "electron-frame"
    assert electron.calls == 1
    assert mss.calls == 0
    assert backend.last_backend_kind == "electron"
    assert backend.last_backend_detail == "selected"


def test_smart_capture_backend_linux_wayland_background_fails_without_window_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _PixelBackend:
        kind = "mss"

        def is_available(self) -> bool:
            return True

        def capture_frame(self, target, profile):  # pragma: no cover
            raise AssertionError("pixel backend should not capture background target")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.delenv("DISPLAY", raising=False)
    backend = galgame_ocr_reader.Win32CaptureBackend(selection="smart")
    backend._backends = [_PixelBackend()]

    target = _window()[0]
    target.is_foreground = False

    with pytest.raises(RuntimeError, match="background_capture_requires_window_backend"):
        backend.capture_frame(target, galgame_ocr_reader.OcrCaptureProfile())


def test_smart_capture_backend_linux_x11_background_allows_pixel_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Backend:
        def __init__(self, kind: str, *, available: bool) -> None:
            self.kind = kind
            self.available = available

        def is_available(self) -> bool:
            return self.available

        def capture_frame(self, target, profile):
            return f"{self.kind}-frame"

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    backend = galgame_ocr_reader.Win32CaptureBackend(selection="smart")
    backend._backends = [
        _Backend("electron", available=False),
        _Backend("mss", available=True),
    ]

    target = _window()[0]
    target.is_foreground = False

    frame = backend.capture_frame(target, galgame_ocr_reader.OcrCaptureProfile())

    assert frame == "mss-frame"
    assert backend.last_backend_kind == "mss"
    assert backend.last_backend_detail == "electron_unavailable_fallback"


def test_smart_capture_backend_macos_background_allows_pixel_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _PixelBackend:
        kind = "mss"

        def is_available(self) -> bool:
            return True

        def capture_frame(self, target, profile):
            return "mss-frame"

    monkeypatch.setattr(sys, "platform", "darwin")
    backend = galgame_ocr_reader.Win32CaptureBackend(selection="smart")
    backend._backends = [_PixelBackend()]

    target = _window()[0]
    target.is_foreground = False

    frame = backend.capture_frame(target, galgame_ocr_reader.OcrCaptureProfile())

    assert frame == "mss-frame"
    assert backend.last_backend_kind == "mss"


def test_smart_capture_backend_non_windows_foreground_prefers_window_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Backend:
        def __init__(self, kind: str, *, available: bool) -> None:
            self.kind = kind
            self.available = available

        def is_available(self) -> bool:
            return self.available

        def capture_frame(self, target, profile):
            return f"{self.kind}-frame"

    monkeypatch.setattr(sys, "platform", "linux")
    backend = galgame_ocr_reader.Win32CaptureBackend(selection="smart")
    backend._backends = [
        _Backend("mss", available=True),
        _Backend("electron", available=False),
        _Backend("pyautogui", available=True),
    ]

    target = _window()[0]
    target.is_foreground = True

    frame = backend.capture_frame(target, galgame_ocr_reader.OcrCaptureProfile())

    assert frame == "mss-frame"
    assert backend.last_backend_kind == "mss"
    assert backend.last_backend_detail == "electron_unavailable_fallback"


def test_smart_capture_backend_windows_background_keeps_printwindow_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Backend:
        def __init__(self, kind: str, *, available: bool = True) -> None:
            self.kind = kind
            self.available = available
            self.calls = 0

        def is_available(self) -> bool:
            return self.available

        def capture_frame(self, target, profile):
            self.calls += 1
            return f"{self.kind}-frame"

    monkeypatch.setattr(sys, "platform", "win32")
    backend = galgame_ocr_reader.Win32CaptureBackend(selection="smart")
    printwindow = _Backend("printwindow")
    dxcam = _Backend("dxcam")
    backend._printwindow_backend = printwindow
    backend._dxcam_backend = dxcam
    backend._backends = [dxcam, _Backend("mss"), _Backend("pyautogui"), printwindow]

    target = _window()[0]
    target.is_foreground = False

    frame = backend.capture_frame(target, galgame_ocr_reader.OcrCaptureProfile())

    assert frame == "printwindow-frame"
    assert printwindow.calls == 1
    assert dxcam.calls == 0
    assert backend.last_backend_kind == "printwindow"


def test_dxcam_camera_creation_is_serialized(monkeypatch: pytest.MonkeyPatch) -> None:
    camera = object()
    calls = 0

    class _DxcamModule:
        @staticmethod
        def create(*, output_color: str):
            nonlocal calls
            assert output_color == "RGB"
            calls += 1
            time.sleep(0.05)
            return camera

    monkeypatch.setitem(sys.modules, "dxcam", _DxcamModule)
    backend = galgame_ocr_reader.DxcamCaptureBackend()
    results: list[object] = []

    threads = [
        threading.Thread(target=lambda: results.append(backend._camera_instance()))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2.0)

    assert calls == 1
    assert results == [camera, camera]


def test_background_hash_scene_change_resets_no_text_counter_without_losing_scene(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    manager._last_background_hash = "0000000000000000"
    manager._consecutive_no_text_polls = 3
    manager._aihong_stage = galgame_ocr_reader._AIHONG_MENU_STAGE
    manager._aihong_dialogue_idle_polls = 4
    manager._aihong_menu_missing_polls = 3
    manager._aihong_menu_ocr_state.last_raw_text = "去东院\n去西院"
    manager._aihong_menu_ocr_state.repeat_count = 1

    assert (
        manager._observe_background_hash(
            "ffffffffffffffff",
            now=3000.0,
            confirm_polls=1,
            defer_scene_emit=True,
        )
        is False
    )

    assert manager._consecutive_no_text_polls == 0
    assert manager._last_background_hash == "ffffffffffffffff"
    assert manager._pending_visual_scene_hash == "ffffffffffffffff"
    assert manager._aihong_stage == galgame_ocr_reader._AIHONG_DIALOGUE_STAGE
    assert manager._aihong_dialogue_idle_polls == 0
    assert manager._aihong_menu_missing_polls == 0
    assert manager._aihong_menu_ocr_state.last_raw_text == ""


def test_aihong_menu_reset_clears_no_text_counter(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    manager._consecutive_no_text_polls = 3
    manager._aihong_stage = galgame_ocr_reader._AIHONG_MENU_STAGE

    manager._reset_aihong_menu_state()

    assert manager._consecutive_no_text_polls == 0
    assert manager._aihong_stage == galgame_ocr_reader._AIHONG_DIALOGUE_STAGE


def test_pending_visual_scene_commits_before_observed_line(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    manager._pending_visual_scene_hash = "ffffffffffffffff"
    manager._pending_visual_scene_at = 3000.0

    assert (
        manager._emit_line_from_ocr_text(
            "王生：先等等。",
            now=3000.0,
            repeat_threshold=2,
        )
        is False
    )
    assert manager._pending_visual_scene_hash == ""
    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    event_types = [event["type"] for event in events]
    assert event_types[-2:] == ["scene_changed", "line_observed"]
    scene_id = events[-2]["payload"]["scene_id"]
    assert events[-1]["payload"]["scene_id"] == scene_id
    assert manager._runtime.scene_ordering_diagnostic == (
        "pending_scene_committed_before_observed"
    )


def test_committed_pending_visual_scene_requests_rescan(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    target = _window()[0]
    active_backend = OcrBackendDescriptor(kind="fake", available=True)
    manager._pending_visual_scene_hash = "ffffffffffffffff"
    manager._pending_visual_scene_at = 3000.0

    manager._commit_pending_visual_scene(
        now=3000.0,
        diagnostic="pending_scene_committed_by_finalize",
    )
    result = manager._finalize_tick_result(
        result=galgame_ocr_reader.OcrReaderTickResult(),
        now=3000.0,
        poll_started_at=3000.0,
        backend_plan=SelectedOcrBackendPlan(primary=active_backend),
        active_backend=active_backend,
        backend_detail_override="",
        target=target,
        aihong_two_stage_enabled=False,
        runtime_profile=OcrCaptureProfile(),
        runtime_capture_profile_selection=None,  # type: ignore[arg-type]
        selection=galgame_ocr_reader.WindowSelectionResult(target=target),
        emitted=False,
        guard_blocked=False,
        screen_classification=ScreenClassification(),
        screen_event_emitted=False,
        capture_attempted=True,
        capture_completed=True,
        capture_error=False,
        text_event_seq_before_capture=writer.last_seq,
        foreground_advance_stable_grace_active=False,
    )

    assert result.should_rescan is True
    assert manager._visual_scene_committed is False


def test_pending_visual_scene_commits_before_stable_line_when_observed_disabled(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    manager._pending_visual_scene_hash = "eeeeeeeeeeeeeeee"
    manager._pending_visual_scene_at = 3000.0

    assert (
        manager._emit_line_from_ocr_text(
            "王生：稳定了。",
            now=3000.0,
            emit_observed=False,
            repeat_threshold=1,
        )
        is True
    )
    assert manager._pending_visual_scene_hash == ""
    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    event_types = [event["type"] for event in events]
    assert event_types[-2:] == ["scene_changed", "line_changed"]
    scene_id = events[-2]["payload"]["scene_id"]
    assert events[-1]["payload"]["scene_id"] == scene_id
    assert manager._runtime.scene_ordering_diagnostic == (
        "pending_scene_committed_before_stable"
    )


def test_pending_background_scene_is_suppressed_for_narration_continuation(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )

    assert manager._emit_line_from_ocr_text(
        "我患了一种病。",
        now=3000.0,
        repeat_threshold=1,
    )
    manager._pending_visual_scene_hash = "ffffffffffffffff"
    manager._pending_visual_scene_at = 3001.0

    assert manager._emit_line_from_ocr_text(
        "此病名为“兽视”，是极稀少的怪病。",
        now=3001.0,
        repeat_threshold=1,
    )

    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    scene_events = [event for event in events if event["type"] == "scene_changed"]
    assert scene_events == []
    assert manager._pending_visual_scene_hash == ""
    assert manager._runtime.scene_ordering_diagnostic == (
        "pending_scene_suppressed_by_dialogue_continuation"
    )


def test_pending_background_scene_is_suppressed_for_two_speaker_dialogue(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )

    assert manager._emit_line_from_ocr_text(
        "小明：你没事吧？",
        now=3000.0,
        repeat_threshold=1,
    )
    manager._pending_visual_scene_hash = "eeeeeeeeeeeeeeee"
    manager._pending_visual_scene_at = 3001.0

    assert manager._emit_line_from_ocr_text(
        "小红：我没事。",
        now=3001.0,
        repeat_threshold=1,
    )

    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    scene_events = [event for event in events if event["type"] == "scene_changed"]
    assert scene_events == []
    assert manager._pending_visual_scene_hash == ""
    stable_payloads = [
        event["payload"] for event in events if event["type"] == "line_changed"
    ]
    assert stable_payloads[-2]["speaker"] == "小明"
    assert stable_payloads[-1]["speaker"] == "小红"
    assert stable_payloads[-1]["scene_id"] == stable_payloads[-2]["scene_id"]


def test_pending_background_scene_commits_for_non_dialogue_boundary(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )

    assert manager._emit_line_from_ocr_text(
        "Alice: We should keep moving.",
        now=3000.0,
        repeat_threshold=1,
    )
    manager._pending_visual_scene_hash = "dddddddddddddddd"
    manager._pending_visual_scene_at = 3001.0
    writer.emit_screen_classified(
        screen_type=OCR_CAPTURE_PROFILE_STAGE_TITLE,
        confidence=0.95,
        ts=galgame_ocr_reader.utc_now_iso(3001.0),
    )

    assert manager._emit_line_from_ocr_text(
        "Alice: This is a new chapter.",
        now=3001.0,
        repeat_threshold=1,
    )

    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    event_types = [event["type"] for event in events]
    assert "scene_changed" in event_types
    scene_events = [event for event in events if event["type"] == "scene_changed"]
    assert scene_events[-1]["payload"]["reason"] == "background_changed"
    assert manager._pending_visual_scene_hash == ""
    assert manager._runtime.scene_ordering_diagnostic == (
        "pending_scene_committed_before_observed"
    )


def test_background_hash_distance_20_does_not_advance_visual_scene(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    manager._last_background_hash = "ff01010101010101"

    assert galgame_ocr_reader._BACKGROUND_SCENE_CHANGE_DISTANCE == 28
    assert (
        manager._hash_distance("ff01010101010101", "ff00000010787878")
        == 20
    )
    assert (
        manager._observe_background_hash(
            "ff00000010787878",
            now=3000.0,
            confirm_polls=1,
        )
        is False
    )

    assert manager._last_background_hash == "ff01010101010101"
    assert manager._pending_visual_scene_hash == ""
    assert manager._runtime.scene_ordering_diagnostic == "none"


def test_malformed_background_hash_distance_does_not_suppress_change() -> None:
    assert OcrReaderManager._hash_distance("not-a-hex-hash", "ffffffffffffffff") == 64


def test_large_background_hash_distance_still_enters_pending_visual_scene(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    manager._last_background_hash = "0000000000000000"

    assert (
        manager._observe_background_hash(
            "ffffffffffffffff",
            now=3000.0,
            confirm_polls=1,
        )
        is False
    )

    assert manager._last_background_hash == "ffffffffffffffff"
    assert manager._pending_visual_scene_hash == "ffffffffffffffff"
    assert manager._pending_visual_scene_distance == 64
    assert manager._runtime.scene_ordering_diagnostic == "background_hash_scene_pending"


def test_ocr_reader_background_scene_change_distance_defaults_to_28(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    config = build_config({"galgame": {"bridge_root": str(bridge_root)}})
    manager = OcrReaderManager(
        logger=_Logger(),
        config=config,
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    manager._last_background_hash = "ff01010101010101"

    assert config.ocr_reader_background_scene_change_distance == 28
    assert manager._background_scene_change_distance() == 28
    assert (
        manager._observe_background_hash(
            "ff00000010787878",
            now=3000.0,
            confirm_polls=1,
        )
        is False
    )

    assert manager._pending_visual_scene_hash == ""


def test_ocr_reader_background_scene_change_distance_can_be_lowered(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root, background_scene_change_distance=24),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    manager._last_background_hash = "0000000000000000"

    assert (
        manager._observe_background_hash(
            "0000000001ffffff",
            now=3000.0,
            confirm_polls=1,
        )
        is False
    )

    assert manager._hash_distance("0000000000000000", "0000000001ffffff") == 25
    assert manager._background_scene_change_distance() == 24
    assert manager._pending_visual_scene_hash == "0000000001ffffff"
    assert manager._pending_visual_scene_distance == 25


def test_ocr_reader_background_scene_change_distance_can_be_raised(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root, background_scene_change_distance=32),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    manager._last_background_hash = "0000000000000000"

    assert (
        manager._observe_background_hash(
            "000000000fffffff",
            now=3000.0,
            confirm_polls=1,
        )
        is False
    )

    assert manager._hash_distance("0000000000000000", "000000000fffffff") == 28
    assert manager._background_scene_change_distance() == 32
    assert manager._pending_visual_scene_hash == ""


@pytest.mark.parametrize("raw_value", [8, 999, "abc", True])
def test_ocr_reader_background_scene_change_distance_invalid_values_fall_back_to_default(
    tmp_path: Path,
    raw_value: object,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    config = _make_config(bridge_root, background_scene_change_distance=raw_value)

    assert config.ocr_reader_background_scene_change_distance == 28


def test_configured_background_scene_change_distance_does_not_change_force_threshold(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root, background_scene_change_distance=40),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    manager._last_background_hash = "0000000000000000"

    assert (
        manager._observe_background_hash(
            "ffffffffffffffff",
            now=3000.0,
            confirm_polls=1,
        )
        is False
    )
    assert manager._pending_visual_scene_hash == "ffffffffffffffff"
    assert manager._pending_visual_scene_distance == 64

    assert manager._emit_line_from_ocr_text(
        "王生：这是真正的新场景。",
        now=3000.1,
        repeat_threshold=1,
    )

    assert manager._pending_visual_scene_hash == ""
    assert manager._runtime.scene_ordering_diagnostic == (
        "pending_scene_committed_by_force_background_distance"
    )


def test_followup_background_hash_pending_scene_commits_before_line(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    manager._last_background_hash = "0000000000000000"

    assert (
        manager._observe_followup_background_hash(
            OcrExtractionResult(
                text="王生：新场景。",
                background_hash="ffffffffffffffff",
            ),
            now=3000.0,
            confirm_polls=1,
            defer_scene_emit=True,
        )
        is False
    )
    assert manager._pending_visual_scene_hash == "ffffffffffffffff"
    assert manager._runtime.scene_ordering_diagnostic == (
        "followup_background_hash_scene_pending"
    )
    assert (
        manager._emit_line_from_ocr_text(
            "王生：新场景。",
            now=3000.1,
            repeat_threshold=1,
        )
        is True
    )
    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    event_types = [event["type"] for event in events]
    assert event_types[-3:] == ["scene_changed", "line_observed", "line_changed"]
    scene_id = events[-3]["payload"]["scene_id"]
    assert events[-2]["payload"]["scene_id"] == scene_id
    assert events[-1]["payload"]["scene_id"] == scene_id
    assert manager._runtime.scene_ordering_diagnostic == (
        "followup_background_hash_scene_committed"
    )


def test_background_candidate_commits_before_first_new_scene_observed_line(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    manager._last_background_hash = "0000000000000000"
    manager._observe_background_hash(
        "00000000ffffffff",
        now=3000.0,
        confirm_polls=2,
    )
    assert manager._pending_background_candidate_hash == "00000000ffffffff"
    assert manager._pending_visual_scene_hash == ""

    assert (
        manager._emit_line_from_ocr_text(
            "王生：新场景台词。",
            now=3000.0,
            repeat_threshold=1,
        )
        is True
    )
    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    event_types = [event["type"] for event in events]
    assert event_types[-3:] == ["scene_changed", "line_observed", "line_changed"]
    scene_id = events[-3]["payload"]["scene_id"]
    assert events[-2]["payload"]["scene_id"] == scene_id
    assert events[-1]["payload"]["scene_id"] == scene_id
    assert events[-3]["payload"]["reason"] == "background_changed"
    assert manager._runtime.scene_ordering_diagnostic == (
        "background_candidate_committed_before_observed"
    )


def test_background_candidate_commits_before_first_new_scene_stable_line_when_observed_disabled(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    manager._last_background_hash = "0000000000000000"
    manager._observe_background_hash(
        "00000000ffffffff",
        now=3000.0,
        confirm_polls=2,
    )

    assert (
        manager._emit_line_from_ocr_text(
            "王生：新场景稳定台词。",
            now=3000.0,
            emit_observed=False,
            repeat_threshold=1,
        )
        is True
    )
    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    event_types = [event["type"] for event in events]
    assert event_types[-2:] == ["scene_changed", "line_changed"]
    scene_id = events[-2]["payload"]["scene_id"]
    assert events[-1]["payload"]["scene_id"] == scene_id


def test_background_candidate_is_suppressed_for_short_narration_continuation(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    assert manager._emit_line_from_ocr_text(
        "我患了一种病。",
        now=3000.0,
        repeat_threshold=1,
    )
    manager._last_background_hash = "0000000000000000"
    manager._observe_background_hash(
        "00000000ffffffff",
        now=3001.0,
        confirm_polls=2,
    )
    assert manager._pending_background_candidate_hash == "00000000ffffffff"

    assert manager._emit_line_from_ocr_text(
        "此病名为怪病。",
        now=3001.0,
        repeat_threshold=1,
    )
    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    scene_events = [event for event in events if event["type"] == "scene_changed"]
    assert scene_events == []
    assert manager._pending_background_candidate_hash == ""
    assert manager._runtime.scene_ordering_diagnostic == (
        "background_candidate_suppressed_by_dialogue_continuation"
    )


def test_background_candidate_is_suppressed_for_two_speaker_dialogue_continuation(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    assert manager._emit_line_from_ocr_text(
        "小明：你好。",
        now=3000.0,
        repeat_threshold=1,
    )
    manager._last_background_hash = "0000000000000000"
    manager._observe_background_hash(
        "00000000ffffffff",
        now=3001.0,
        confirm_polls=2,
    )

    assert manager._emit_line_from_ocr_text(
        "小红：你好呀。",
        now=3001.0,
        repeat_threshold=1,
    )
    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    scene_events = [event for event in events if event["type"] == "scene_changed"]
    assert scene_events == []
    assert manager._runtime.scene_ordering_diagnostic == (
        "background_candidate_suppressed_by_dialogue_continuation"
    )


def test_background_candidate_expires_without_affecting_later_line(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    manager._last_background_hash = "0000000000000000"
    manager._observe_background_hash(
        "00000000ffffffff",
        now=3000.0,
        confirm_polls=2,
    )
    assert manager._pending_background_candidate_hash == "00000000ffffffff"

    assert (
        manager._emit_line_from_ocr_text(
            "王生：过期后台词。",
            now=3026.0,
            repeat_threshold=1,
        )
        is True
    )
    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    scene_events = [event for event in events if event["type"] == "scene_changed"]
    assert scene_events == []
    assert manager._pending_background_candidate_hash == ""
    assert manager._pending_background_candidate_at == 0.0
    assert manager._pending_background_candidate_distance == 0
    assert manager._pending_background_candidate_base_hash == ""
    assert manager._pending_background_candidate_used is False
    assert manager._runtime.scene_ordering_diagnostic == "background_candidate_expired"


def test_background_candidate_clears_when_background_distance_falls_below_threshold(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    manager._last_background_hash = "0000000000000000"
    manager._observe_background_hash(
        "00000000ffffffff",
        now=3000.0,
        confirm_polls=2,
    )
    assert manager._pending_background_candidate_hash == "00000000ffffffff"

    manager._observe_background_hash(
        "0000000000000001",
        now=3001.0,
        confirm_polls=2,
    )
    assert manager._pending_background_candidate_hash == ""
    assert manager._pending_background_hash == ""
    assert manager._pending_background_change_count == 0


def test_background_candidate_does_not_duplicate_scene_changed_after_confirmed_pending_scene(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    manager._pending_visual_scene_hash = "eeeeeeeeeeeeeeee"
    manager._pending_visual_scene_at = 3000.0
    manager._last_background_hash = "0000000000000000"
    manager._observe_background_hash(
        "00000000ffffffff",
        now=3000.0,
        confirm_polls=2,
    )
    assert manager._pending_background_candidate_hash == "00000000ffffffff"

    assert (
        manager._emit_line_from_ocr_text(
            "王生：台词。",
            now=3000.0,
            repeat_threshold=1,
        )
        is True
    )
    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    scene_events = [event for event in events if event["type"] == "scene_changed"]
    assert len(scene_events) == 1
    assert scene_events[0]["payload"]["reason"] == "background_changed"
    # candidate 在 pending visual scene 存在时未被处理，应保留
    assert manager._pending_background_candidate_hash == "00000000ffffffff"


def test_background_candidate_uses_default_threshold_28_without_requiring_24(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    manager._last_background_hash = "0000000000000000"
    manager._observe_background_hash(
        "00000000ffffffff",
        now=3000.0,
        confirm_polls=2,
    )
    distance = manager._pending_background_candidate_distance
    assert distance >= 28 + 4

    assert (
        manager._emit_line_from_ocr_text(
            "王生：默认阈值台词。",
            now=3000.0,
            repeat_threshold=1,
        )
        is True
    )
    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    scene_events = [event for event in events if event["type"] == "scene_changed"]
    assert len(scene_events) == 1
    assert manager._runtime.scene_ordering_diagnostic == (
        "background_candidate_committed_before_observed"
    )


def test_background_candidate_overwritten_when_new_hash_exceeds_threshold(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    manager._last_background_hash = "0000000000000000"
    manager._observe_background_hash(
        "00000000ffffffff",
        now=3000.0,
        confirm_polls=2,
    )
    assert manager._pending_background_candidate_hash == "00000000ffffffff"

    manager._observe_background_hash(
        "ffffffff00000000",
        now=3001.0,
        confirm_polls=2,
    )
    assert manager._pending_background_candidate_hash == "ffffffff00000000"

    assert (
        manager._emit_line_from_ocr_text(
            "王生：覆盖后台词。",
            now=3001.0,
            repeat_threshold=1,
        )
        is True
    )
    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    scene_events = [event for event in events if event["type"] == "scene_changed"]
    assert len(scene_events) == 1
    assert scene_events[0]["payload"]["background_hash"] == "ffffffff00000000"


def test_background_candidate_force_distance_commits_immediately(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    assert manager._emit_line_from_ocr_text(
        "小明：你好。",
        now=3000.0,
        repeat_threshold=1,
    )
    manager._last_background_hash = "0000000000000000"
    manager._observe_background_hash(
        "ffffffffffffffff",
        now=3001.0,
        confirm_polls=2,
    )
    assert manager._pending_background_candidate_distance == 64

    assert (
        manager._emit_line_from_ocr_text(
            "小红：你好呀。",
            now=3001.0,
            repeat_threshold=1,
        )
        is True
    )
    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    scene_events = [event for event in events if event["type"] == "scene_changed"]
    assert len(scene_events) == 1
    assert manager._runtime.scene_ordering_diagnostic == (
        "background_candidate_committed_by_force_distance"
    )


def test_background_candidate_commits_after_realistic_19_second_delay(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    assert manager._emit_line_from_ocr_text(
        "王生：旧场景最后一句。",
        now=3000.0,
        repeat_threshold=1,
    )
    manager._last_background_hash = "0000000000000000"
    manager._observe_background_hash(
        "00000000ffffffff",
        now=3019.0,
        confirm_polls=2,
    )
    assert manager._pending_background_candidate_hash == "00000000ffffffff"

    assert (
        manager._emit_line_from_ocr_text(
            "王生：新场景第一句。",
            now=3019.0,
            repeat_threshold=1,
        )
        is True
    )
    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    scene_events = [event for event in events if event["type"] == "scene_changed"]
    assert len(scene_events) == 1
    latest_line = events[-1]
    assert latest_line["payload"]["scene_id"] == scene_events[0]["payload"]["scene_id"]


def test_regular_observed_line_without_pending_scene_does_not_emit_scene_changed(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )

    assert (
        manager._emit_line_from_ocr_text(
            "王生：普通台词。",
            now=3000.0,
            repeat_threshold=2,
        )
        is False
    )
    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    assert [event["type"] for event in events][-1:] == ["line_observed"]
    assert all(event["type"] != "scene_changed" for event in events)


def test_followup_confirm_uses_cleaned_dialogue_text() -> None:
    state = galgame_ocr_reader._StableOcrTextState(
        last_raw_text="王生：新 台词。",
        repeat_count=1,
        stable_text="王生：旧台词。",
    )

    manager = object.__new__(OcrReaderManager)
    manager._attached_window = None
    manager._runtime = OcrReaderRuntime()
    assert (
        manager._should_attempt_followup_confirm(
            "王生：新\n台词。",
            state=state,
        )
        is True
    )


def test_stable_text_promotes_distinct_line_without_waiting_for_repeat(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 3000.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )

    assert (
        manager._emit_line_from_ocr_text(
            "王生：旧台词。",
            now=3000.0,
            repeat_threshold=1,
        )
        is True
    )
    assert (
        manager._emit_line_from_ocr_text(
            "王生：新 台词。",
            now=3001.0,
            repeat_threshold=2,
        )
        is True
    )

    session = read_session_json(bridge_root / writer.game_id / "session.json").session
    assert session is not None
    assert session["state"]["speaker"] == "王生"
    assert session["state"]["text"] == "新 台词。"
    assert manager._default_ocr_state.stable_text == "王生：新 台词。"


@pytest.mark.asyncio
async def test_ocr_reader_runtime_exposes_stable_text_tracker(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
        ),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(["雪乃：你好。"]),
    )

    result = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    assert result.runtime["stable_ocr_last_raw_text"] == "雪乃：你好。"
    assert result.runtime["stable_ocr_repeat_count"] == 1
    assert result.runtime["stable_ocr_stable_text"] == ""
    assert result.runtime["stable_ocr_block_reason"] == "waiting_for_repeat"


@pytest.mark.asyncio
async def test_ocr_reader_senren_banka_dialogue_library_confirms_matched_line_immediately(
    tmp_path: Path,
    ocr_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    expected_text = "这是千恋万花台词库的开发者测试行。"
    observed_text = "这是千恋万花台词库的开发者测试句。"
    library_dir = _write_senren_banka_dialogue_library(
        tmp_path / "dialogue_libraries",
        line_id="fixture:ocr-runtime",
        text=expected_text,
    )
    monkeypatch.setattr(galgame_dialogue_library, "_DEFAULT_LIBRARY_DIR", library_dir)
    galgame_dialogue_library._load_dialogue_library_cached.cache_clear()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(ocr_runtime_root),
        ),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=101,
                title="千恋＊万花",
                process_name="SenrenBanka.exe",
                pid=28828,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend([observed_text]),
    )

    result = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    session = read_session_json(bridge_root / result.runtime["game_id"] / "session.json").session

    assert result.runtime["detail"] == "receiving_text"
    assert result.runtime["last_observed_line"]["text"] == expected_text
    assert result.runtime["last_stable_line"]["text"] == expected_text
    assert result.runtime["stable_ocr_stable_text"] == expected_text
    assert result.runtime["stable_ocr_block_reason"] == ""
    assert session is not None
    assert session["state"]["text"] == expected_text


def test_ocr_reader_starts_foreground_advance_monitor_for_real_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    started: list[bool] = []

    def _start(self) -> bool:
        started.append(True)
        return True

    monkeypatch.setattr(galgame_ocr_reader._MouseWheelMonitor, "start", _start)
    monkeypatch.setattr(galgame_ocr_reader._MouseWheelMonitor, "is_running", lambda self: True)

    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root, enabled=True),
        platform_fn=lambda: True,
        window_scanner=_window,
    )

    assert started == [True]
    assert manager._runtime.foreground_advance_monitor_running is True


def test_ocr_reader_does_not_autostart_foreground_monitor_in_interval_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    started: list[bool] = []

    def _start(self) -> bool:
        started.append(True)
        return True

    monkeypatch.setattr(galgame_ocr_reader._MouseWheelMonitor, "start", _start)

    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            trigger_mode=OCR_TRIGGER_MODE_INTERVAL,
            rapidocr_enabled=True,
        ),
        platform_fn=lambda: True,
        window_scanner=_window,
    )

    assert started == []
    assert manager._runtime.foreground_advance_monitor_running is False


def test_ocr_reader_update_config_stops_foreground_monitor_outside_after_advance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    started: list[bool] = []
    stopped: list[float] = []
    running = {"value": False}

    def _start(self) -> bool:
        started.append(True)
        running["value"] = True
        return True

    def _stop(self, *, join_timeout: float = 1.0) -> None:
        stopped.append(join_timeout)
        running["value"] = False

    monkeypatch.setattr(galgame_ocr_reader._MouseWheelMonitor, "start", _start)
    monkeypatch.setattr(galgame_ocr_reader._MouseWheelMonitor, "stop", _stop)
    monkeypatch.setattr(
        galgame_ocr_reader._MouseWheelMonitor,
        "is_running",
        lambda self: running["value"],
    )

    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            trigger_mode=OCR_TRIGGER_MODE_AFTER_ADVANCE,
            rapidocr_enabled=True,
        ),
        platform_fn=lambda: True,
        window_scanner=_window,
    )

    assert started == [True]
    assert manager._runtime.foreground_advance_monitor_running is True

    manager.update_config(
        _make_config(
            bridge_root,
            enabled=True,
            trigger_mode=OCR_TRIGGER_MODE_INTERVAL,
            rapidocr_enabled=True,
        )
    )

    assert stopped == [1.0]
    assert running["value"] is False
    assert manager._runtime.foreground_advance_monitor_running is False
    assert manager._runtime.foreground_advance_last_seq == 0


def test_ocr_reader_update_config_resets_auto_lang_detector_on_toggle(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root, rapidocr_enabled=False),
        platform_fn=lambda: True,
        window_scanner=_window,
    )
    detector = _OcrLangDetector(window_size=2, confirm_streak=1)
    manager._ocr_lang_detector = detector

    assert detector.feed("\uc720\ud0a4: \uc548\ub155\ud558\uc138\uc694") is None

    config = _make_config(bridge_root, rapidocr_enabled=False)
    config.rapidocr_auto_detect_lang = False
    manager.update_config(config)

    new_detector = manager._ocr_lang_detector
    assert new_detector.feed("\uc720\ud0a4: \ub2e4\uc74c\uc5d0 \ub610 \ub9cc\ub098\uc694") is None


def test_ocr_reader_interval_consume_does_not_lazy_start_foreground_monitor(
    tmp_path: Path,
) -> None:
    class _FakeMonitor:
        def __init__(self) -> None:
            self.ensure_calls = 0
            self.stop_calls: list[float] = []
            self.running = True

        def ensure_running(self) -> bool:
            self.ensure_calls += 1
            self.running = True
            return True

        def is_running(self) -> bool:
            return self.running

        def last_seq(self) -> int:
            return 7

        def events_after(self, seq: int) -> list[object]:
            del seq
            self.ensure_running()
            return [object()]

        def stop(self, *, join_timeout: float = 1.0) -> None:
            self.stop_calls.append(join_timeout)
            self.running = False

    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            trigger_mode=OCR_TRIGGER_MODE_INTERVAL,
        ),
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    fake_monitor = _FakeMonitor()
    manager._wheel_monitor = fake_monitor

    result = manager.consume_foreground_advance_inputs()

    assert result.triggered is False
    assert fake_monitor.ensure_calls == 0
    assert fake_monitor.stop_calls == [1.0]
    assert manager._runtime.foreground_advance_monitor_running is False
    assert manager._runtime.foreground_advance_last_seq == 0


def test_ocr_reader_after_advance_still_consumes_injected_monitor_events(
    tmp_path: Path,
) -> None:
    class _FakeMonitor:
        def __init__(self) -> None:
            self.ensure_calls = 0
            self.running = True
            self.events = [
                galgame_ocr_reader._MouseWheelEvent(
                    seq=1,
                    ts=100.0,
                    delta=-120,
                    foreground_hwnd=101,
                    point_hwnd=101,
                    kind="wheel",
                )
            ]

        def ensure_running(self) -> bool:
            self.ensure_calls += 1
            return True

        def is_running(self) -> bool:
            return self.running

        def last_seq(self) -> int:
            return 1

        def events_after(self, seq: int) -> list[object]:
            return [event for event in self.events if event.seq > seq]

    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            trigger_mode=OCR_TRIGGER_MODE_AFTER_ADVANCE,
        ),
        time_fn=lambda: 100.5,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    fake_monitor = _FakeMonitor()
    manager._wheel_monitor = fake_monitor

    result = manager.consume_foreground_advance_inputs()

    assert result.triggered is True
    assert fake_monitor.ensure_calls == 1
    assert manager._runtime.foreground_advance_monitor_running is True
    assert manager._runtime.foreground_advance_last_seq == 1


def test_mouse_monitor_stop_joins_and_clears_exited_thread() -> None:
    class _FakeThread:
        def __init__(self) -> None:
            self.alive = True
            self.join_calls: list[float] = []

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            self.join_calls.append(float(timeout or 0.0))
            self.alive = False

    monitor = galgame_ocr_reader._MouseWheelMonitor(time_fn=lambda: 0.0)
    thread = _FakeThread()
    monitor._thread = thread

    monitor.stop(join_timeout=0.2)

    assert thread.join_calls == [0.2]
    assert monitor._thread is None


def test_mouse_monitor_start_does_not_reuse_thread_that_is_stopping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _StuckThread:
        def __init__(self) -> None:
            self.join_calls: list[float] = []

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            self.join_calls.append(float(timeout or 0.0))

    monitor = galgame_ocr_reader._MouseWheelMonitor(time_fn=lambda: 0.0)
    thread = _StuckThread()
    monitor._thread = thread
    monitor._stop.set()
    monkeypatch.setattr(galgame_ocr_reader.os, "name", "nt")

    assert monitor.start() is False
    assert thread.join_calls == [0.25]


def test_ocr_reader_runtime_groups_fields_and_keeps_flat_compatibility() -> None:
    runtime = OcrReaderRuntime(
        enabled=True,
        status="active",
        pid=1234,
        capture_profile={"top_ratio": 0.5},
        stable_ocr_repeat_count=2,
        foreground_advance_consumed_count=4,
        foreground_advance_matched_count=3,
        foreground_advance_coalesced_count=2,
        foreground_advance_last_event_age_seconds=0.25,
        vision_classifier_enabled=True,
        vision_classifier_available=True,
        vision_classifier_detail="loaded",
        vision_classifier_last_label="dialogue",
        vision_classifier_last_confidence=0.91,
        vision_classifier_last_latency_ms=1.25,
    )

    assert len(fields(OcrReaderRuntime)) == 9
    assert runtime.status_state.enabled is True
    assert runtime.enabled is True
    assert runtime.window.pid == 1234
    assert runtime.capture_profile == {"top_ratio": 0.5}
    assert runtime.to_dict()["stable_ocr_repeat_count"] == 2
    assert runtime.target.foreground_advance_coalesced_count == 2
    assert runtime.to_dict()["foreground_advance_consumed_count"] == 4
    assert runtime.to_dict()["foreground_advance_matched_count"] == 3
    assert runtime.to_dict()["foreground_advance_coalesced_count"] == 2
    assert runtime.to_dict()["foreground_advance_last_event_age_seconds"] == 0.25
    assert runtime.capture.vision_classifier_detail == "loaded"
    assert runtime.to_dict()["vision_classifier_available"] is True
    assert runtime.to_dict()["vision_classifier_last_label"] == "dialogue"
    assert runtime.to_dict()["vision_classifier_last_confidence"] == 0.91
    assert runtime.to_dict()["vision_classifier_last_latency_ms"] == 1.25

    restored = OcrReaderRuntime(**runtime.to_dict())

    assert restored.foreground_advance_consumed_count == 4
    assert restored.foreground_advance_matched_count == 3
    assert restored.foreground_advance_coalesced_count == 2
    assert restored.vision_classifier_available is True
    assert restored.vision_classifier_detail == "loaded"

    runtime.status = "idle"

    assert runtime.status_state.status == "idle"


def test_ocr_reader_build_runtime_preserves_foreground_advance_diagnostics(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root, enabled=True),
        platform_fn=lambda: False,
        window_scanner=_window,
    )
    manager._runtime.foreground_advance_consumed_count = 4
    manager._runtime.foreground_advance_matched_count = 3
    manager._runtime.foreground_advance_coalesced_count = 2
    manager._runtime.foreground_advance_first_event_ts = 100.0
    manager._runtime.foreground_advance_last_event_ts = 100.2
    manager._runtime.foreground_advance_detected_at = 100.5
    manager._runtime.foreground_advance_last_event_age_seconds = 0.3

    rebuilt = manager._build_runtime(
        status="active",
        detail="",
        plan=SelectedOcrBackendPlan(),
    )

    assert rebuilt.foreground_advance_consumed_count == 4
    assert rebuilt.foreground_advance_matched_count == 3
    assert rebuilt.foreground_advance_coalesced_count == 2
    assert rebuilt.foreground_advance_first_event_ts == 100.0
    assert rebuilt.foreground_advance_last_event_ts == 100.2
    assert rebuilt.foreground_advance_detected_at == 100.5
    assert abs(rebuilt.foreground_advance_last_event_age_seconds - 0.3) < 1e-6


def test_ocr_reader_build_runtime_uses_config_enabled_state(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root, enabled=False),
        platform_fn=lambda: False,
        window_scanner=_window,
    )

    rebuilt = manager._build_runtime(
        status="disabled",
        detail="disabled_by_config",
        plan=SelectedOcrBackendPlan(),
    )

    assert rebuilt.enabled is False


def test_ocr_reader_build_runtime_exposes_vision_classifier_status(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root, enabled=True),
        platform_fn=lambda: False,
        window_scanner=_window,
    )
    manager._config.vision_classifier_enabled = True
    manager.vision_classifier = object()
    manager._vision_classifier_detail = "loaded"
    manager._vision_classifier_last_label = "dialogue"
    manager._vision_classifier_last_confidence = 0.97
    manager._vision_classifier_last_latency_ms = 1.4

    rebuilt = manager._build_runtime(
        status="active",
        detail="",
        plan=SelectedOcrBackendPlan(),
    )

    assert rebuilt.vision_classifier_enabled is True
    assert rebuilt.vision_classifier_available is True
    assert rebuilt.vision_classifier_detail == "loaded"
    assert rebuilt.vision_classifier_last_label == "dialogue"
    assert rebuilt.vision_classifier_last_confidence == 0.97
    assert rebuilt.vision_classifier_last_latency_ms == 1.4


def test_ocr_reader_logs_when_vision_classifier_loads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.plugins.galgame_plugin.core import vision as vision_module

    class _FakeVisionModelLoader:
        def __init__(self, model_dir: Path) -> None:
            self.model_dir = model_dir

    class _FakeVisionScreenClassifier:
        def __init__(self, loader, *, input_size, latency_check_ms) -> None:
            self.loader = loader
            self.input_size = input_size
            self.latency_check_ms = latency_check_ms

        def load(self, model_name: str) -> bool:
            self.model_name = model_name
            return True

    monkeypatch.setattr(vision_module, "VisionModelLoader", _FakeVisionModelLoader)
    monkeypatch.setattr(vision_module, "VisionScreenClassifier", _FakeVisionScreenClassifier)

    logger = _CapturingLogger()
    manager = object.__new__(OcrReaderManager)
    manager._logger = logger
    manager.vision_classifier = None
    manager._vision_classifier_detail = ""
    manager._config = SimpleNamespace(
        vision_classifier_enabled=True,
        vision_classifier_model_dir=str(
            Path("plugin")
            / "plugins"
            / "galgame_plugin"
            / "models"
            / "vision"
            / "screen_classifier"
        ),
        vision_classifier_input_size=[224, 224],
        vision_classifier_inference_timeout_ms=200.0,
        vision_classifier_model_name="v1_galgame",
    )

    manager._initialize_vision_classifier()

    assert manager._vision_classifier_detail == "loaded"
    assert manager.vision_classifier is not None
    assert logger.infos
    assert logger.infos[0][0] == "galgame vision classifier loaded: model_dir={} model_name={}"


def test_ocr_reader_update_config_reloads_vision_classifier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from plugin.plugins.galgame_plugin.core import vision as vision_module

    class _FakeVisionModelLoader:
        def __init__(self, model_dir: Path) -> None:
            self.model_dir = model_dir

    class _FakeVisionScreenClassifier:
        def __init__(self, loader, *, input_size, latency_check_ms) -> None:
            self.loader = loader
            self.input_size = input_size
            self.latency_check_ms = latency_check_ms
            self.model_name = ""

        def load(self, model_name: str) -> bool:
            self.model_name = model_name
            return True

    monkeypatch.setattr(vision_module, "VisionModelLoader", _FakeVisionModelLoader)
    monkeypatch.setattr(vision_module, "VisionScreenClassifier", _FakeVisionScreenClassifier)

    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root, enabled=True, rapidocr_enabled=False),
        platform_fn=lambda: False,
        window_scanner=_window,
    )
    assert manager.vision_classifier is None

    enabled_config = _make_config(bridge_root, enabled=True, rapidocr_enabled=False)
    enabled_config.vision_classifier_enabled = True
    enabled_config.vision_classifier_model_dir = str(tmp_path / "models")
    enabled_config.vision_classifier_model_name = "v2_galgame"
    enabled_config.vision_classifier_input_size = [192, 192]
    enabled_config.vision_classifier_inference_timeout_ms = 123.0

    manager.update_config(enabled_config)

    assert isinstance(manager.vision_classifier, _FakeVisionScreenClassifier)
    assert manager.vision_classifier.input_size == (192, 192)
    assert manager.vision_classifier.latency_check_ms == 123.0
    assert manager.vision_classifier.model_name == "v2_galgame"
    assert manager._vision_classifier_detail == "loaded"

    manager._vision_classifier_last_label = "dialogue"
    disabled_config = _make_config(bridge_root, enabled=True, rapidocr_enabled=False)

    manager.update_config(disabled_config)

    assert manager.vision_classifier is None
    assert manager._vision_classifier_detail == "disabled"
    assert manager._vision_classifier_last_label == ""


@pytest.mark.asyncio
async def test_ocr_reader_restarts_session_after_initial_capture_failure(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    clock = {"now": 3000.0}

    class _FailOnceCaptureBackend(_FakeCaptureBackend):
        def __init__(self) -> None:
            super().__init__()
            self._failed = False

        def capture_frame(self, target: DetectedGameWindow, profile) -> str:
            if not self._failed:
                self._failed = True
                raise RuntimeError("first capture failed")
            return super().capture_frame(target, profile)

    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
        ),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FailOnceCaptureBackend(),
        ocr_backend=_FakeOcrBackend(["王生：恢复了。", "王生：恢复了。"]),
    )

    await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
    assert manager._writer.session_id == ""

    for _ in range(2):
        clock["now"] += 1.0
        await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    events = _read_events(bridge_root / manager._writer.game_id / "events.jsonl")
    assert manager._writer.session_id
    assert events[-1]["type"] == "line_changed"


@pytest.mark.asyncio
async def test_ocr_reader_discards_new_failed_session_with_existing_history(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    seed_writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 2900.0)
    seed_writer.start_session(_window()[0])
    seed_game_id = seed_writer.game_id
    assert seed_writer.emit_line("Yukino: previous line.", ts="2026-04-29T02:00:01Z")
    assert seed_writer.end_session(ts="2026-04-29T02:00:02Z")
    seed_events = _read_events(bridge_root / seed_game_id / "events.jsonl")
    assert seed_events[-1]["seq"] > 1

    class _FailOnceCaptureBackend(_FakeCaptureBackend):
        def __init__(self) -> None:
            super().__init__()
            self._failed = False

        def capture_frame(self, target: DetectedGameWindow, profile) -> str:
            if not self._failed:
                self._failed = True
                raise RuntimeError("first capture failed")
            return super().capture_frame(target, profile)

    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(ocr_runtime_root),
        ),
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FailOnceCaptureBackend(),
        ocr_backend=_FakeOcrBackend(["Yukino: recovered.", "Yukino: recovered."]),
    )

    await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
    assert manager._writer.session_id == ""
    events_after_failure = _read_events(bridge_root / seed_game_id / "events.jsonl")
    assert events_after_failure[: len(seed_events)] == seed_events
    assert events_after_failure[-1]["type"] == "session_ended"
    assert events_after_failure[-1]["payload"]["discarded"] is True

    await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
    events = _read_events(bridge_root / seed_game_id / "events.jsonl")

    assert manager._writer.session_id
    assert "session_started" in [
        event["type"] for event in events[len(events_after_failure) :]
    ]
    assert events[: len(seed_events)] == seed_events


def test_build_config_defaults_ocr_languages_to_chi_sim_jpn_eng(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    cfg = build_config({"galgame": {"bridge_root": str(bridge_root)}})

    assert cfg.ocr_reader_languages == "chi_sim+jpn+eng"
    assert cfg.ocr_reader_backend_selection == "auto"
    assert cfg.ocr_reader_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE
    assert cfg.ocr_reader_screen_type_transition_emit is True
    assert cfg.ocr_reader_known_screen_timeout_seconds == pytest.approx(5.0)
    assert cfg.ocr_reader_top_ratio == pytest.approx(DEFAULT_OCR_CAPTURE_TOP_RATIO)
    assert cfg.ocr_reader_bottom_inset_ratio == pytest.approx(
        DEFAULT_OCR_CAPTURE_BOTTOM_INSET_RATIO
    )


def test_ocr_reader_manager_initializes_with_rapidocr_warmup_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    warmup_calls = []
    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.ocr_reader.RapidOcrBackend.warmup_async",
        lambda self, logger=None: warmup_calls.append(logger),
    )

    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            backend_selection="rapidocr",
            rapidocr_enabled=True,
        ),
    )

    assert manager._writer.bridge_root == bridge_root
    assert warmup_calls


@pytest.mark.asyncio
async def test_ocr_capture_worker_reuses_executor(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(["第一句。", "第二句。"]),
    )
    first_executor = manager._capture_executor

    first = await manager._capture_and_extract_text_with_timeout(
        _window()[0],
        OcrCaptureProfile(),
        SelectedOcrBackendPlan(),
        collect_background_hash=False,
    )
    second = await manager._capture_and_extract_text_with_timeout(
        _window()[0],
        OcrCaptureProfile(),
        SelectedOcrBackendPlan(),
        collect_background_hash=False,
    )

    try:
        assert first.text == "第一句。"
        assert second.text == "第二句。"
        assert manager._capture_executor is first_executor
    finally:
        await manager.shutdown()


def test_ocr_window_scan_inventory_uses_ttl_cache(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    now = {"value": 1000.0}
    calls = {"count": 0}

    def scanner() -> list[DetectedGameWindow]:
        calls["count"] += 1
        return _window()

    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: now["value"],
        platform_fn=lambda: True,
        window_scanner=scanner,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )

    try:
        assert manager._scan_window_inventory()[0]
        now["value"] += 1.0
        assert manager._scan_window_inventory()[0]
        now["value"] += 5.1
        assert manager._scan_window_inventory()[0]
        assert calls["count"] == 2
    finally:
        manager._shutdown_capture_worker()


def test_ocr_window_inventory_preserves_non_windows_scanner_foreground(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    foreground = DetectedGameWindow(
        hwnd=501,
        title="Foreground Game",
        process_name="Game.exe",
        pid=7501,
        width=1280,
        height=720,
        area=1280 * 720,
        is_foreground=True,
    )
    background = DetectedGameWindow(
        hwnd=502,
        title="Background Game",
        process_name="Game.exe",
        pid=7502,
        width=1280,
        height=720,
        area=1280 * 720,
        is_foreground=False,
    )

    def fail_windows_foreground_api() -> int:
        raise AssertionError("Windows foreground API should not run off Windows")

    monkeypatch.setattr(
        galgame_ocr_reader,
        "_foreground_window_handle",
        fail_windows_foreground_api,
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        platform_fn=lambda: False,
        window_scanner=lambda: [background, foreground],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )

    try:
        eligible, excluded = manager._scan_window_inventory()
        selection = manager._select_target_window(eligible, excluded_windows=excluded)
        assert eligible[0].hwnd == foreground.hwnd
        assert eligible[0].is_foreground is True
        assert selection.target is not None
        assert selection.target.hwnd == foreground.hwnd
        assert selection.selection_detail == "foreground_window"
    finally:
        manager._shutdown_capture_worker()


@pytest.mark.asyncio
async def test_ocr_reader_tick_skips_win32_foreground_probe_on_non_windows(
    tmp_path: Path,
    ocr_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    game_window = DetectedGameWindow(
        hwnd=501,
        title="Foreground Game",
        process_name="Game.exe",
        pid=7501,
        width=1280,
        height=720,
        area=1280 * 720,
        is_foreground=True,
    )

    def fail_windows_foreground_api() -> int:
        raise AssertionError("Windows foreground API should not run off Windows")

    monkeypatch.setattr(
        galgame_ocr_reader,
        "_foreground_window_handle",
        fail_windows_foreground_api,
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(ocr_runtime_root),
        ),
        platform_fn=lambda: False,
        window_scanner=lambda: [game_window],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )

    try:
        result = await manager.tick(
            bridge_sdk_available=False,
            memory_reader_runtime={},
        )
        assert result.runtime["status"] == "active"
        assert result.runtime["process_name"] == "Game.exe"
        assert result.runtime["target_is_foreground"] is True
    finally:
        manager._shutdown_capture_worker()


def test_rapidocr_runtime_cache_reuses_loaded_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_target_dir = str(tmp_path / "RapidOCR")
    runtime = object()
    load_calls = []

    def fake_load_runtime(**kwargs):
        load_calls.append(kwargs)
        return runtime, {}

    monkeypatch.setattr(galgame_ocr_reader, "load_rapidocr_runtime", fake_load_runtime)
    monkeypatch.setattr(galgame_ocr_rapidocr_backend, "load_rapidocr_runtime", fake_load_runtime)
    cache_key = (
        install_target_dir,
        "onnxruntime",
        "ch",
        "mobile",
        "PP-OCRv5",
    )
    galgame_ocr_reader._RAPIDOCR_RUNTIME_CACHE.pop(cache_key, None)
    try:
        first = galgame_ocr_reader.RapidOcrBackend(
            install_target_dir_raw=install_target_dir,
            engine_type="onnxruntime",
            lang_type="ch",
            model_type="mobile",
            ocr_version="PP-OCRv5",
        )
        second = galgame_ocr_reader.RapidOcrBackend(
            install_target_dir_raw=install_target_dir,
            engine_type="onnxruntime",
            lang_type="ch",
            model_type="mobile",
            ocr_version="PP-OCRv5",
        )

        assert first._ensure_runtime() is runtime
        assert second._ensure_runtime() is runtime
        assert len(load_calls) == 1
    finally:
        galgame_ocr_reader._RAPIDOCR_RUNTIME_CACHE.pop(cache_key, None)


def test_rapidocr_runtime_cache_reloads_after_idle_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_target_dir = str(tmp_path / "RapidOCR")
    runtimes = [object(), object()]
    load_calls = []
    now = {"value": 1000.0}

    def fake_load_runtime(**kwargs):
        load_calls.append(kwargs)
        return runtimes[len(load_calls) - 1], {}

    monkeypatch.setattr(galgame_ocr_reader, "load_rapidocr_runtime", fake_load_runtime)
    monkeypatch.setattr(galgame_ocr_rapidocr_backend, "load_rapidocr_runtime", fake_load_runtime)
    monkeypatch.setattr(galgame_ocr_reader.time, "monotonic", lambda: now["value"])
    cache_key = (
        install_target_dir,
        "onnxruntime",
        "ch",
        "mobile",
        "PP-OCRv5",
    )
    galgame_ocr_reader._RAPIDOCR_RUNTIME_CACHE.pop(cache_key, None)
    try:
        backend = galgame_ocr_reader.RapidOcrBackend(
            install_target_dir_raw=install_target_dir,
            engine_type="onnxruntime",
            lang_type="ch",
            model_type="mobile",
            ocr_version="PP-OCRv5",
        )

        assert backend._ensure_runtime() is runtimes[0]
        now["value"] += galgame_ocr_reader._RAPIDOCR_RUNTIME_IDLE_TTL_SECONDS + 1.0
        assert backend._ensure_runtime() is runtimes[1]
        assert len(load_calls) == 2
    finally:
        galgame_ocr_reader._RAPIDOCR_RUNTIME_CACHE.pop(cache_key, None)


def test_rapidocr_runtime_cache_key_normalizes_case_and_whitespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_target_dir = str(tmp_path / "RapidOCR")
    runtime = object()
    load_calls = []

    def fake_load_runtime(**kwargs):
        load_calls.append(kwargs)
        return runtime, {}

    monkeypatch.setattr(galgame_ocr_reader, "load_rapidocr_runtime", fake_load_runtime)
    monkeypatch.setattr(galgame_ocr_rapidocr_backend, "load_rapidocr_runtime", fake_load_runtime)
    cache_key = (
        install_target_dir,
        "onnxruntime",
        "ch",
        "mobile",
        "PP-OCRv5",
    )
    galgame_ocr_reader._RAPIDOCR_RUNTIME_CACHE.pop(cache_key, None)
    try:
        first = galgame_ocr_reader.RapidOcrBackend(
            install_target_dir_raw=f" {install_target_dir} ",
            engine_type="ONNXRUNTIME",
            lang_type="CH",
            model_type="Mobile",
            ocr_version=" PP-OCRv5 ",
        )
        second = galgame_ocr_reader.RapidOcrBackend(
            install_target_dir_raw=install_target_dir,
            engine_type="onnxruntime",
            lang_type="ch",
            model_type="mobile",
            ocr_version="PP-OCRv5",
        )

        assert first._ensure_runtime() is runtime
        assert second._ensure_runtime() is runtime
        assert len(load_calls) == 1
    finally:
        galgame_ocr_reader._RAPIDOCR_RUNTIME_CACHE.pop(cache_key, None)


def test_score_ocr_text_prefers_cjk_dialogue_over_ascii_gibberish() -> None:
    gibberish = "hs 四                 A y 3 8\n人~ x ai    アニ"
    chinese_dialogue = "她轻声说：今天先回去吧。"

    assert _score_ocr_text(chinese_dialogue) > _score_ocr_text(gibberish)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("こんにちは世界", "japan"),
        ("コンビニで買い物", "japan"),
        ("안녕하세요 세계", "korean"),
        ("你好世界今天天气真好", "ch"),
        ("这是我的东西你别动", "ch"),
        ("東西南北方向指示", "ch"),
        ("ERROR: file not found", "unknown"),
        ("... --- ...", "unknown"),
        ("", "unknown"),
        ("   ", "unknown"),
    ],
)
def test_classify_cjk_text(text: str, expected: str) -> None:
    assert _classify_cjk_text(text) == expected


def test_ocr_lang_detector_confirms_after_two_windows() -> None:
    detector = _OcrLangDetector(window_size=3, confirm_streak=2)

    assert detector.feed("こんにちは") is None
    assert detector.feed("元気です") is None
    assert detector.feed("私は学生です") is None
    assert detector.feed("おはよう") is None
    assert detector.feed("ありがとう") is None
    assert detector.feed("さようなら") == "japan"


def test_ocr_lang_detector_ignores_noise_lines() -> None:
    detector = _OcrLangDetector(window_size=3, confirm_streak=1)

    assert detector.feed("...") is None
    assert detector.feed("12345") is None
    assert detector.feed("ERROR") is None
    assert detector.feed("こんにちは世界") is None


def test_ocr_lang_detector_streak_resets_on_language_change() -> None:
    detector = _OcrLangDetector(window_size=3, confirm_streak=2)

    assert detector.feed("こんにちは") is None
    assert detector.feed("元気です") is None
    assert detector.feed("私は学生です") is None
    assert detector.feed("안녕하세요") is None
    assert detector.feed("반갑습니다") is None
    assert detector.feed("학생입니다") is None
    assert detector.feed("고마워요") is None
    assert detector.feed("괜찮습니다") is None
    assert detector.feed("다음에 봐요") == "korean"


def test_ocr_lang_detector_reset_clears_state() -> None:
    detector = _OcrLangDetector(window_size=3, confirm_streak=1)
    detector.feed("안녕하세요")
    detector.feed("반갑습니다")
    detector.feed("학생입니다")

    detector.reset()

    assert detector._streak == 0
    assert detector._buffer == []
    assert detector._last_detected is None


def test_ocr_lang_detector_records_confirmed_language_change() -> None:
    detector = _OcrLangDetector(window_size=1, confirm_streak=1)

    assert detector.feed("\u3053\u3093\u306b\u3061\u306f") == "japan"
    assert detector.last_switched_at is None
    assert detector.feed("\uc548\ub155\ud558\uc138\uc694") == "korean"
    assert detector.last_switched_at is not None


def test_rapidocr_auto_lang_skips_when_rapidocr_not_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    persisted: list[str] = []
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root, rapidocr_enabled=True),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        rapidocr_lang_changed_callback=persisted.append,
    )
    manager._ocr_lang_detector = _OcrLangDetector(window_size=1, confirm_streak=1)
    monkeypatch.setattr(
        galgame_ocr_reader,
        "inspect_rapidocr_installation",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected RapidOCR inspection")),
    )

    for text in [
        "\uc720\ud0a4: \uc548\ub155\ud558\uc138\uc694",
        "\uc720\ud0a4: \uc624\ub298\uc740 \uc88b\uc740 \ub0a0\uc774\uc5d0\uc694",
        "\uc720\ud0a4: \uc6b0\ub9ac\ub294 \ud568\uaed8 \uac08 \uc218 \uc788\uc5b4\uc694",
        "\uc720\ud0a4: \ub2e4\uc74c\uc5d0 \ub610 \ub9cc\ub098\uc694",
        "\uc720\ud0a4: \uc774\uc57c\uae30\ub97c \uacc4\uc18d\ud574\uc694",
    ]:
        manager._maybe_auto_switch_rapidocr_lang(text, rapidocr_active=False)

    assert manager._config.rapidocr_lang_type == "ch"
    assert manager._config.rapidocr_auto_detect_last_lang == ""
    assert persisted == []


def test_rapidocr_auto_lang_safe_logging_does_not_break_detection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_ExplodingLogger(),
        config=_make_config(bridge_root, rapidocr_enabled=True),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
    )
    manager._ocr_lang_detector = _OcrLangDetector(window_size=1, confirm_streak=1)
    monkeypatch.setattr(
        galgame_ocr_reader,
        "inspect_rapidocr_installation",
        lambda **_kwargs: {
            "installed": True,
            "detail": "installed",
            "detected_path": "C:/RapidOCR/site-packages/rapidocr_onnxruntime",
            "selected_model": "PP-OCRv5/korean/mobile",
        },
    )

    manager._maybe_auto_switch_rapidocr_lang(
        "\uc720\ud0a4: \uc548\ub155\ud558\uc138\uc694",
        rapidocr_active=True,
    )

    assert manager._config.rapidocr_lang_type == "korean"
    assert manager._config.rapidocr_auto_detect_last_lang == "korean"


def test_rapidocr_auto_lang_skips_when_rapidocr_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    persisted: list[str] = []
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root, rapidocr_enabled=False),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        rapidocr_lang_changed_callback=persisted.append,
    )
    manager._ocr_lang_detector = _OcrLangDetector(window_size=1, confirm_streak=1)
    monkeypatch.setattr(
        galgame_ocr_reader,
        "inspect_rapidocr_installation",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected RapidOCR inspection")),
    )

    for text in [
        "\uc720\ud0a4: \uc548\ub155\ud558\uc138\uc694",
        "\uc720\ud0a4: \uc624\ub298\uc740 \uc88b\uc740 \ub0a0\uc774\uc5d0\uc694",
        "\uc720\ud0a4: \uc6b0\ub9ac\ub294 \ud568\uaed8 \uac08 \uc218 \uc788\uc5b4\uc694",
        "\uc720\ud0a4: \ub2e4\uc74c\uc5d0 \ub610 \ub9cc\ub098\uc694",
        "\uc720\ud0a4: \uc774\uc57c\uae30\ub97c \uacc4\uc18d\ud574\uc694",
        "\uc720\ud0a4: \uc9c0\uae08 \uc900\ube44\uac00 \ub05d\ub0ac\uc5b4\uc694",
    ]:
        manager._maybe_auto_switch_rapidocr_lang(text, rapidocr_active=True)

    assert manager._config.rapidocr_lang_type == "ch"
    assert manager._config.rapidocr_auto_detect_last_lang == ""
    assert persisted == []


def test_rapidocr_auto_lang_switches_when_rapidocr_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    persisted: list[str] = []
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root, rapidocr_enabled=True),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        rapidocr_lang_changed_callback=persisted.append,
    )
    manager._ocr_lang_detector = _OcrLangDetector(window_size=3, confirm_streak=2)
    monkeypatch.setattr(
        galgame_ocr_reader,
        "inspect_rapidocr_installation",
        lambda **_kwargs: {
            "installed": True,
            "detail": "installed",
            "detected_path": "C:/RapidOCR/site-packages/rapidocr_onnxruntime",
            "selected_model": "PP-OCRv5/korean/mobile",
        },
    )

    for text in [
        "\uc720\ud0a4: \uc548\ub155\ud558\uc138\uc694",
        "\uc720\ud0a4: \uc624\ub298\uc740 \uc88b\uc740 \ub0a0\uc774\uc5d0\uc694",
        "\uc720\ud0a4: \uc6b0\ub9ac\ub294 \ud568\uaed8 \uac08 \uc218 \uc788\uc5b4\uc694",
        "\uc720\ud0a4: \ub2e4\uc74c\uc5d0 \ub610 \ub9cc\ub098\uc694",
        "\uc720\ud0a4: \uc774\uc57c\uae30\ub97c \uacc4\uc18d\ud574\uc694",
        "\uc720\ud0a4: \uc9c0\uae08 \uc900\ube44\uac00 \ub05d\ub0ac\uc5b4\uc694",
    ]:
        manager._maybe_auto_switch_rapidocr_lang(text, rapidocr_active=True)

    assert manager._config.rapidocr_lang_type == "korean"
    assert manager._config.rapidocr_auto_detect_last_lang == "korean"
    assert persisted == ["korean"]


def test_rapidocr_auto_lang_does_not_override_manual_disable_during_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    persisted: list[str] = []
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root, rapidocr_enabled=True),
        time_fn=lambda: 3000.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        rapidocr_lang_changed_callback=persisted.append,
    )
    manager._ocr_lang_detector = _OcrLangDetector(window_size=1, confirm_streak=1)

    def _inspect_and_disable(**_kwargs):
        manager._config.rapidocr_lang_type = "japan"
        manager._config.rapidocr_auto_detect_lang = False
        manager._config.rapidocr_auto_detect_last_lang = "japan"
        return {
            "installed": True,
            "detail": "installed",
            "detected_path": "C:/RapidOCR/site-packages/rapidocr_onnxruntime",
            "selected_model": "PP-OCRv5/ch/mobile",
        }

    monkeypatch.setattr(
        galgame_ocr_reader,
        "inspect_rapidocr_installation",
        _inspect_and_disable,
    )

    manager._maybe_auto_switch_rapidocr_lang(
        "\u96ea\u4e43: \u3053\u3093\u306b\u3061\u306f",
        rapidocr_active=True,
    )

    assert manager._config.rapidocr_lang_type == "japan"
    assert manager._config.rapidocr_auto_detect_lang is False
    assert manager._config.rapidocr_auto_detect_last_lang == "japan"
    assert persisted == []


def test_rapidocr_auto_lang_first_switch_not_blocked_by_startup_cooldown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    persisted: list[str] = []
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root, rapidocr_enabled=True),
        time_fn=lambda: 1.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        rapidocr_lang_changed_callback=persisted.append,
    )
    manager._ocr_lang_detector = _OcrLangDetector(window_size=1, confirm_streak=1)
    monkeypatch.setattr(galgame_ocr_reader.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(
        galgame_ocr_reader,
        "inspect_rapidocr_installation",
        lambda **_kwargs: {
            "installed": True,
            "detail": "installed",
            "detected_path": "C:/RapidOCR/site-packages/rapidocr_onnxruntime",
            "selected_model": "PP-OCRv5/korean/mobile",
        },
    )

    manager._maybe_auto_switch_rapidocr_lang(
        "\uc720\ud0a4: \uc548\ub155\ud558\uc138\uc694",
        rapidocr_active=True,
    )

    assert manager._config.rapidocr_lang_type == "korean"
    assert manager._config.rapidocr_auto_detect_last_lang == "korean"
    assert persisted == ["korean"]


async def test_rapidocr_auto_lang_session_end_clears_switch_cooldown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    clock = {"now": 1000.0}
    persisted: list[str] = []
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root, rapidocr_enabled=True),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        rapidocr_lang_changed_callback=persisted.append,
    )
    manager._writer.start_session(_window()[0])
    manager._ocr_lang_detector = _OcrLangDetector(window_size=1, confirm_streak=1)
    monkeypatch.setattr(galgame_ocr_reader.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        galgame_ocr_reader,
        "inspect_rapidocr_installation",
        lambda **_kwargs: {
            "installed": True,
            "detail": "installed",
            "detected_path": "C:/RapidOCR/site-packages/rapidocr_onnxruntime",
            "selected_model": "PP-OCRv5/mobile",
        },
    )

    manager._maybe_auto_switch_rapidocr_lang(
        "\uc720\ud0a4: \uc548\ub155\ud558\uc138\uc694",
        rapidocr_active=True,
    )
    assert manager._config.rapidocr_lang_type == "korean"

    await manager._end_session_if_needed(clock["now"])
    manager._config.rapidocr_lang_type = "ch"
    clock["now"] = 1001.0
    manager._maybe_auto_switch_rapidocr_lang(
        "\u96ea\u4e43: \u3053\u3093\u306b\u3061\u306f",
        rapidocr_active=True,
    )

    assert manager._config.rapidocr_lang_type == "japan"
    assert persisted == ["korean", "japan"]


def test_rapidocr_default_install_target_uses_app_docs_runtime_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_docs_dir = tmp_path / "AppDocs"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "EmptyLocalAppData"))
    monkeypatch.setattr(galgame_rapidocr_support, "is_windows_platform", lambda: True)
    monkeypatch.setattr(
        galgame_rapidocr_support,
        "get_config_manager",
        lambda: SimpleNamespace(app_docs_dir=app_docs_dir),
    )

    raw_target = galgame_rapidocr_support.default_rapidocr_install_target_raw(plugin_id="galgame_plugin")
    resolved = galgame_rapidocr_support.resolve_rapidocr_install_target("", plugin_id="galgame_plugin")

    assert raw_target == str(app_docs_dir / "runtimes" / "galgame_plugin" / "RapidOCR")
    assert resolved == app_docs_dir / "runtimes" / "galgame_plugin" / "RapidOCR"
    assert "LOCALAPPDATA" not in raw_target.upper()


def test_rapidocr_explicit_install_target_overrides_new_and_legacy_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_docs_dir = tmp_path / "AppDocs"
    explicit_target = tmp_path / "ExplicitRapidOCR"
    monkeypatch.setattr(
        galgame_rapidocr_support,
        "get_config_manager",
        lambda: SimpleNamespace(app_docs_dir=app_docs_dir),
    )

    assert (
        galgame_rapidocr_support.resolve_rapidocr_install_target(
            str(explicit_target),
            plugin_id="galgame_plugin",
        )
        == explicit_target
    )


def test_rapidocr_resolve_uses_legacy_install_when_new_target_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_docs_dir = tmp_path / "AppDocs"
    local_appdata = tmp_path / "LocalAppData"
    legacy_target = local_appdata / "Programs" / "N.E.K.O" / "RapidOCR"
    (legacy_target / "runtime" / "site-packages" / "rapidocr_onnxruntime").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(
        galgame_rapidocr_support,
        "get_config_manager",
        lambda: SimpleNamespace(app_docs_dir=app_docs_dir),
    )

    assert (
        galgame_rapidocr_support.resolve_rapidocr_install_target("", plugin_id="galgame_plugin")
        == legacy_target
    )


def test_rapidocr_resolve_prefers_populated_new_target_over_legacy_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_docs_dir = tmp_path / "AppDocs"
    new_target = app_docs_dir / "runtimes" / "galgame_plugin" / "RapidOCR"
    # Since #1517 the new target wins only when it actually has assets
    # (runtime package or models), not on bare directory existence.
    (new_target / "runtime" / "site-packages" / "rapidocr_onnxruntime").mkdir(parents=True)
    local_appdata = tmp_path / "LocalAppData"
    legacy_target = local_appdata / "Programs" / "N.E.K.O" / "RapidOCR"
    (legacy_target / "runtime" / "site-packages" / "rapidocr_onnxruntime").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(
        galgame_rapidocr_support,
        "get_config_manager",
        lambda: SimpleNamespace(app_docs_dir=app_docs_dir),
    )

    assert (
        galgame_rapidocr_support.resolve_rapidocr_install_target("", plugin_id="galgame_plugin")
        == new_target
    )


def test_rapidocr_resolve_empty_new_target_falls_back_to_populated_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_docs_dir = tmp_path / "AppDocs"
    new_target = app_docs_dir / "runtimes" / "galgame_plugin" / "RapidOCR"
    # An empty directory (e.g. left behind by an aborted install) must not
    # shadow a legacy install that still has working assets — #1517 semantics.
    new_target.mkdir(parents=True)
    local_appdata = tmp_path / "LocalAppData"
    legacy_target = local_appdata / "Programs" / "N.E.K.O" / "RapidOCR"
    (legacy_target / "runtime" / "site-packages" / "rapidocr_onnxruntime").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(
        galgame_rapidocr_support,
        "get_config_manager",
        lambda: SimpleNamespace(app_docs_dir=app_docs_dir),
    )

    assert (
        galgame_rapidocr_support.resolve_rapidocr_install_target("", plugin_id="galgame_plugin")
        == legacy_target
    )


def test_inspect_rapidocr_installation_reports_legacy_target_when_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_docs_dir = tmp_path / "AppDocs"
    local_appdata = tmp_path / "LocalAppData"
    legacy_target = local_appdata / "Programs" / "N.E.K.O" / "RapidOCR"
    package_dir = legacy_target / "runtime" / "site-packages" / "rapidocr_onnxruntime"
    package_dir.mkdir(parents=True)
    models_dir = package_dir / "models"
    models_dir.mkdir()
    for model_name in (
        "ch_PP-OCRv4_det_infer.onnx",
        "ch_ppocr_mobile_v2.0_cls_infer.onnx",
        "ch_PP-OCRv4_rec_infer.onnx",
    ):
        (models_dir / model_name).write_bytes(b"model")
    (legacy_target / "models").mkdir()
    (legacy_target / "install_state.json").write_text(
        json.dumps({"selected_model": "PP-OCRv5/ch/mobile"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(
        galgame_rapidocr_support,
        "get_config_manager",
        lambda: SimpleNamespace(app_docs_dir=app_docs_dir),
    )
    monkeypatch.setattr(
        galgame_rapidocr_support,
        "load_rapidocr_runtime",
        lambda **_kwargs: (object(), {"detected_path": str(package_dir)}),
    )
    # Force the bundled-spec branch off so the legacy plugin-isolated path
    # is exercised. In a uv-synced dev env rapidocr_onnxruntime is bundled
    # and would shadow the legacy fixture; we want to test the fallback
    # specifically. Pin lang to "ch" so this test stays about legacy path
    # detection rather than any future model-default change.
    monkeypatch.setattr(
        galgame_rapidocr_support.importlib.util,
        "find_spec",
        lambda _name: None,
    )

    status = galgame_rapidocr_support.inspect_rapidocr_installation(
        install_target_dir_raw="",
        lang_type="ch",
        ocr_version="PP-OCRv4",
        plugin_id="galgame_plugin",
        platform_fn=lambda: True,
    )

    assert status["installed"] is True
    assert status["detail"] == "installed"
    assert status["target_dir"] == str(legacy_target)
    assert status["detected_path"] == str(package_dir)


def test_inspect_rapidocr_installation_uses_current_model_selection_over_legacy_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_docs_dir = tmp_path / "AppDocs"
    target = app_docs_dir / "runtimes" / "galgame_plugin" / "RapidOCR"
    package_dir = target / "runtime" / "site-packages" / "rapidocr_onnxruntime"
    package_dir.mkdir(parents=True)
    (target / "models").mkdir()
    (target / "install_state.json").write_text(
        json.dumps(
            {
                "selected_model": "PP-OCRv5/ch/mobile",
                "lang_type": "ch",
                "model_type": "mobile",
                "ocr_version": "PP-OCRv5",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        galgame_rapidocr_support,
        "get_config_manager",
        lambda: SimpleNamespace(app_docs_dir=app_docs_dir),
    )
    monkeypatch.setattr(
        galgame_rapidocr_support.importlib.util,
        "find_spec",
        lambda _name: SimpleNamespace(origin=str(package_dir / "__init__.py")),
    )
    monkeypatch.setattr(
        galgame_rapidocr_support,
        "missing_rapidocr_model_files",
        lambda **_kwargs: [],
    )

    status = galgame_rapidocr_support.inspect_rapidocr_installation(
        install_target_dir_raw="",
        lang_type="japan",
        model_type="mobile",
        ocr_version="PP-OCRv5",
        plugin_id="galgame_plugin",
        platform_fn=lambda: True,
    )

    assert status["selected_model"] == "PP-OCRv5/japan/mobile"
    assert status["lang_type"] == "japan"
    assert status["model_type"] == "mobile"
    assert status["ocr_version"] == "PP-OCRv5"


def test_install_task_runtime_root_uses_app_docs_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_docs_dir = tmp_path / "AppDocs"
    monkeypatch.setattr(
        galgame_install_tasks,
        "get_config_manager",
        lambda: SimpleNamespace(app_docs_dir=app_docs_dir),
    )

    state_path = galgame_install_tasks.install_task_state_path(
        "run-1",
        kind="rapidocr_models",
        plugin_id="galgame_plugin",
    )

    assert state_path == (
        app_docs_dir
        / "plugin-runtime"
        / "plugin-installs"
        / "galgame_plugin"
        / "rapidocr_models-installs"
        / "run-1.json"
    )


@pytest.mark.asyncio
async def test_ocr_reader_manager_waits_before_taking_over_after_memory_reader_text(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    clock = {"now": 1000.0}
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
            no_text_takeover_after_seconds=30.0,
        ),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )

    await manager.tick(
        bridge_sdk_available=False,
        memory_reader_runtime={
            "status": "active",
            "detail": "receiving_text",
            "game_id": "mem-demo",
            "session_id": "mem-session",
            "last_seq": 2,
            "last_text_seq": 2,
        },
    )
    clock["now"] += 5.0
    result = await manager.tick(
        bridge_sdk_available=False,
        memory_reader_runtime={
            "status": "active",
            "detail": "attached_idle_after_text",
            "game_id": "mem-demo",
            "session_id": "mem-session",
            "last_seq": 3,
            "last_text_seq": 2,
        },
    )

    assert result.runtime["status"] == "idle"
    assert result.runtime["detail"] == "waiting_for_takeover_window"


@pytest.mark.asyncio
async def test_ocr_reader_manager_clears_memory_wait_on_new_idle_memory_session(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    clock = {"now": 1000.0}
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
            no_text_takeover_after_seconds=30.0,
        ),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(),
    )

    first = await manager.tick(
        bridge_sdk_available=False,
        memory_reader_runtime={
            "status": "active",
            "detail": "receiving_text",
            "game_id": "mem-demo",
            "session_id": "mem-session-a",
            "last_seq": 2,
            "last_text_seq": 2,
        },
    )
    assert first.runtime["detail"] == "memory_reader_active"

    clock["now"] += 5.0
    second = await manager.tick(
        bridge_sdk_available=False,
        memory_reader_runtime={
            "status": "active",
            "detail": "attached_idle_after_text",
            "game_id": "mem-demo",
            "session_id": "mem-session-b",
            "last_seq": 5,
            "last_text_seq": 4,
        },
    )

    assert second.runtime["detail"] != "waiting_for_takeover_window"
    assert capture_backend.capture_calls


@pytest.mark.asyncio
async def test_ocr_reader_manager_does_not_treat_memory_reader_heartbeats_as_live_text(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
        ),
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )

    result = await manager.tick(
        bridge_sdk_available=False,
        memory_reader_runtime={
            "status": "active",
            "detail": "attached_no_text_yet",
            "last_seq": 29,
        },
    )

    assert result.runtime["status"] == "active"
    assert result.runtime["detail"] == "attached_no_text_yet"
    assert result.runtime["ocr_context_state"] == "no_text"


@pytest.mark.asyncio
async def test_ocr_reader_manager_prefers_memory_reader_game_window_over_foreground_window(
    tmp_path: Path,
    ocr_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    windows = [
        DetectedGameWindow(
            hwnd=202,
            title="插件详情 - N.E.K.O 插件管理 - Google Chrome",
            process_name="chrome.exe",
            pid=1500,
        ),
        DetectedGameWindow(
            hwnd=101,
            title="哀鸿",
            process_name="TheLamentingGeese.exe",
            pid=28828,
        ),
    ]
    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.ocr_reader._foreground_window_handle",
        lambda: 202,
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
        ),
        platform_fn=lambda: True,
        window_scanner=lambda: list(windows),
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )

    result = await manager.tick(
        bridge_sdk_available=False,
        memory_reader_runtime={
            "status": "active",
            "detail": "attached_no_text_yet",
            "process_name": "TheLamentingGeese.exe",
            "pid": 28828,
            "last_seq": 29,
        },
    )

    assert result.runtime["status"] == "active"
    assert result.runtime["detail"] == "attached_no_text_yet"
    assert result.runtime["ocr_context_state"] == "no_text"
    assert result.runtime["process_name"] == "TheLamentingGeese.exe"
    assert result.runtime["pid"] == 28828


@pytest.mark.asyncio
async def test_ocr_reader_manager_locks_auto_target_when_user_focuses_other_window(
    tmp_path: Path,
    ocr_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    game_window = DetectedGameWindow(
        hwnd=101,
        title="哀鸿",
        process_name="TheLamentingGeese.exe",
        pid=28828,
    )
    rebound_game_window = DetectedGameWindow(
        hwnd=303,
        title=game_window.title,
        process_name=game_window.process_name,
        pid=game_window.pid,
    )
    other_window = DetectedGameWindow(
        hwnd=202,
        title="Other Tool",
        process_name="Other.exe",
        pid=1500,
    )
    foreground = {"hwnd": game_window.hwnd}
    windows = {"items": [game_window, other_window]}
    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.ocr_reader._foreground_window_handle",
        lambda: foreground["hwnd"],
    )
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
        ),
        platform_fn=lambda: True,
        window_scanner=lambda: list(windows["items"]),
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(),
    )

    first = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    assert first.runtime["process_name"] == "TheLamentingGeese.exe"
    assert first.runtime["target_selection_detail"] == "foreground_window"
    assert first.runtime["target_is_foreground"] is True
    assert first.runtime["locked_target"]["process_name"] == "TheLamentingGeese.exe"
    assert capture_backend.capture_calls[-1][0] == game_window.hwnd

    foreground["hwnd"] = other_window.hwnd
    windows["items"] = [other_window]
    second = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    assert second.runtime["status"] == "idle"
    assert second.runtime["detail"] == "waiting_for_valid_window"
    assert second.runtime["target_selection_detail"] == "locked_target_unavailable"
    assert len(capture_backend.capture_calls) == 1

    windows["items"] = [other_window, rebound_game_window]
    third = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    assert third.runtime["status"] == "active"
    assert third.runtime["process_name"] == "TheLamentingGeese.exe"
    assert third.runtime["pid"] == rebound_game_window.pid
    assert third.runtime["target_selection_detail"] == "locked_target_rebound"
    assert third.runtime["target_is_foreground"] is False
    assert capture_backend.capture_calls[-1][0] == rebound_game_window.hwnd


@pytest.mark.asyncio
async def test_ocr_reader_manager_applies_builtin_aihong_capture_profile(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
        ),
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=101,
                title="哀鸿",
                process_name="TheLamentingGeese.exe",
                pid=28828,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )

    result = await manager.tick(
        bridge_sdk_available=False,
        memory_reader_runtime={},
    )

    assert result.runtime["capture_profile"]["left_inset_ratio"] == pytest.approx(0.0)
    assert result.runtime["capture_profile"]["right_inset_ratio"] == pytest.approx(0.0)
    assert result.runtime["capture_profile"]["top_ratio"] == pytest.approx(0.60)
    assert result.runtime["capture_profile"]["bottom_inset_ratio"] == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_ocr_reader_manager_prefers_manual_capture_profile_over_builtin_aihong_profile(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
        ),
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=101,
                title="哀鸿",
                process_name="TheLamentingGeese.exe",
                pid=28828,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    manager.update_capture_profiles(
        {
            "TheLamentingGeese.exe": {
                "left_inset_ratio": 0.11,
                "right_inset_ratio": 0.09,
                "top_ratio": 0.41,
                "bottom_inset_ratio": 0.19,
            }
        }
    )

    result = await manager.tick(
        bridge_sdk_available=False,
        memory_reader_runtime={},
    )

    assert result.runtime["capture_profile"]["left_inset_ratio"] == pytest.approx(0.11)
    assert result.runtime["capture_profile"]["right_inset_ratio"] == pytest.approx(0.09)
    assert result.runtime["capture_profile"]["top_ratio"] == pytest.approx(0.41)
    assert result.runtime["capture_profile"]["bottom_inset_ratio"] == pytest.approx(0.19)


@pytest.mark.asyncio
async def test_aihong_menu_stage_accepts_plain_text_choices_after_dialogue_idle_polls(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    clock = {"now": 3000.0}
    writer = OcrReaderBridgeWriter(
        bridge_root=bridge_root,
        time_fn=lambda: clock["now"],
    )
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
            poll_interval_seconds=999.0,
        ),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=101,
                title="哀鸿",
                process_name="TheLamentingGeese.exe",
                pid=28828,
            )
        ],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(
            [
                "将军：那纸上所书，必是紧要军令。",
                "将军：那纸上所书，必是紧要军令。",
                "将军：那纸上所书，必是紧要军令。",
                "将军：那纸上所书，必是紧要军令。",
                "往南跑\n躲进巷子里",
                "往南跑\n躲进巷子里",
            ]
        ),
        writer=writer,
    )

    for _ in range(7):
        latest = await manager.tick(
            bridge_sdk_available=False,
            memory_reader_runtime={},
        )
        clock["now"] += 1.0

    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    session = read_session_json(bridge_root / writer.game_id / "session.json").session

    assert latest.runtime["capture_profile"]["top_ratio"] == pytest.approx(0.0)
    choice_event = next(event for event in reversed(events) if event["type"] == "choices_shown")
    payload = choice_event["payload"]
    assert [item["text"] for item in payload["choices"]] == ["往南跑", "躲进巷子里"]
    assert session is not None
    assert session["state"]["is_menu_open"] is True
    assert capture_backend.capture_calls[0][1]["top_ratio"] == pytest.approx(0.60)
    assert capture_backend.capture_calls[0][1]["right_inset_ratio"] == pytest.approx(0.0)
    assert capture_backend.capture_calls[-1][1]["top_ratio"] == pytest.approx(0.0)
    assert capture_backend.capture_calls[-1][1]["bottom_inset_ratio"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_aihong_menu_probe_rejects_dialogue_like_multiline_text(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    clock = {"now": 4000.0}
    writer = OcrReaderBridgeWriter(
        bridge_root=bridge_root,
        time_fn=lambda: clock["now"],
    )
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
            poll_interval_seconds=999.0,
        ),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=101,
                title="哀鸿",
                process_name="TheLamentingGeese.exe",
                pid=28828,
            )
        ],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(
            [
                "将军：那纸上所书，必是紧要军令。",
                "将军：那纸上所书，必是紧要军令。",
                "将军：那纸上所书，必是紧要军令。",
                "将军：那纸上所书，必是紧要军令。",
                "旁白：城门将破。\n将军：跟我来。",
                "将军：那纸上所书，必是紧要军令。",
                "旁白：城门将破。\n将军：跟我来。",
            ]
        ),
        writer=writer,
    )

    for _ in range(6):
        latest = await manager.tick(
            bridge_sdk_available=False,
            memory_reader_runtime={},
        )
        clock["now"] += 1.0

    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    session = read_session_json(bridge_root / writer.game_id / "session.json").session

    assert all(event["type"] != "choices_shown" for event in events)
    assert latest.runtime["capture_profile"]["top_ratio"] == pytest.approx(0.60)
    assert session is not None
    assert session["state"]["is_menu_open"] is False
    assert capture_backend.capture_calls[4][1]["top_ratio"] == pytest.approx(0.0)
    assert capture_backend.capture_calls[6][1]["top_ratio"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_aihong_menu_stage_returns_to_dialogue_profile_after_stable_line(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    clock = {"now": 5000.0}
    writer = OcrReaderBridgeWriter(
        bridge_root=bridge_root,
        time_fn=lambda: clock["now"],
    )
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
            poll_interval_seconds=999.0,
        ),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=101,
                title="哀鸿",
                process_name="TheLamentingGeese.exe",
                pid=28828,
            )
        ],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(
            [
                "将军：那纸上所书，必是紧要军令。",
                "将军：那纸上所书，必是紧要军令。",
                "将军：那纸上所书，必是紧要军令。",
                "将军：那纸上所书，必是紧要军令。",
                "往南跑\n躲进巷子里",
                "往南跑\n躲进巷子里",
                "将军：跟我来。",
                "将军：跟我来。",
                "将军：跟我来。",
            ]
        ),
        writer=writer,
    )

    for _ in range(12):
        latest = await manager.tick(
            bridge_sdk_available=False,
            memory_reader_runtime={},
        )
        clock["now"] += 1.0

    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    session = read_session_json(bridge_root / writer.game_id / "session.json").session

    assert events[-1]["type"] == "line_changed"
    assert latest.runtime["capture_profile"]["top_ratio"] == pytest.approx(0.60)
    assert session is not None
    assert session["state"]["text"] == "跟我来。"
    assert session["state"]["is_menu_open"] is False
    assert any(
        call[1]["top_ratio"] == pytest.approx(0.0)
        for call in capture_backend.capture_calls
    )
    assert latest.runtime["capture_profile"]["top_ratio"] == pytest.approx(0.60)


@pytest.mark.asyncio
async def test_ocr_reader_manager_starts_capture_and_emits_stable_line(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    clock = {"now": 1000.0}
    writer = OcrReaderBridgeWriter(
        bridge_root=bridge_root,
        time_fn=lambda: clock["now"],
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
            poll_interval_seconds=999.0,
        ),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(["雪乃：你好。", "雪乃：你好。"]),
        writer=writer,
    )

    first = await manager.tick(
        bridge_sdk_available=False,
        memory_reader_runtime={},
    )
    clock["now"] += 1.0
    second = await manager.tick(
        bridge_sdk_available=False,
        memory_reader_runtime={},
    )
    clock["now"] += 1.0
    third = await manager.tick(
        bridge_sdk_available=False,
        memory_reader_runtime={},
    )

    session_path = bridge_root / writer.game_id / "session.json"
    session = read_session_json(session_path).session

    assert first.runtime["status"] == "active"
    assert first.runtime["detail"] == "receiving_observed_text"
    assert first.runtime["ocr_context_state"] == "observed"
    assert first.runtime["consecutive_no_text_polls"] == 0
    assert first.runtime["last_observed_at"]
    assert first.runtime["last_capture_attempt_at"]
    assert first.runtime["last_capture_completed_at"]
    assert first.runtime["last_raw_ocr_text"] == "雪乃：你好。"
    assert first.runtime["last_observed_line"]["text"] == "你好。"
    assert second.runtime["status"] == "active"
    assert second.runtime["detail"] == "receiving_text"
    assert second.runtime["ocr_context_state"] == "stable"
    assert second.runtime["last_stable_line"]["text"] == "你好。"
    assert third.runtime["status"] == "active"
    assert third.runtime["game_id"].startswith("ocr-")
    assert session is not None
    assert session["metadata"]["source"] == "ocr_reader"
    assert session["bridge_sdk_version"].startswith("ocr-reader-")
    assert session["state"]["scene_id"] == "ocr:unknown_scene"
    assert str(session["state"]["line_id"]).startswith("ocr:")
    assert session["state"]["text"] == "你好。"


@pytest.mark.asyncio
async def test_ocr_reader_tick_treats_capture_backend_probe_error_as_unavailable(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(ocr_runtime_root),
        ),
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_ExplodingAvailabilityCaptureBackend(),
        ocr_backend=_FakeOcrBackend(["probe should not reach OCR"]),
    )

    result = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    assert result.runtime["status"] == "candidate"
    assert result.runtime["detail"] == "capture_backend_unavailable"
    assert result.warnings == ["ocr_reader capture backend is not available"]


@pytest.mark.asyncio
async def test_ocr_reader_manager_reports_capture_diagnostic_after_repeated_no_text(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    clock = {"now": 1200.0}
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
            poll_interval_seconds=999.0,
        ),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(["", "", ""]),
    )

    await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
    clock["now"] += 1.0
    second = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
    clock["now"] += 1.0
    third = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
    clock["now"] += 1.0
    fourth = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    assert second.runtime["detail"] == "attached_no_text_yet"
    assert second.runtime["consecutive_no_text_polls"] == 2
    assert third.runtime["detail"] == "ocr_capture_diagnostic_required"
    assert third.runtime["consecutive_no_text_polls"] == 3
    assert third.runtime["ocr_context_state"] == "diagnostic_required"
    assert fourth.runtime["ocr_capture_diagnostic_required"] is True
    assert fourth.runtime["last_capture_stage"]
    assert fourth.runtime["last_capture_profile"]


@pytest.mark.asyncio
async def test_ocr_reader_manager_emits_choices_after_stable_menu_detection(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    clock = {"now": 2000.0}
    writer = OcrReaderBridgeWriter(
        bridge_root=bridge_root,
        time_fn=lambda: clock["now"],
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
            poll_interval_seconds=999.0,
        ),
        time_fn=lambda: clock["now"],
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(
            [
                "雪乃：选一个吧。",
                "雪乃：选一个吧。",
                "1. 去左边\n2. 去右边",
                "1. 去左边\n2. 去右边",
            ]
        ),
        writer=writer,
    )

    for _ in range(5):
        await manager.tick(
            bridge_sdk_available=False,
            memory_reader_runtime={},
        )
        clock["now"] += 1.0

    events = _read_events(bridge_root / writer.game_id / "events.jsonl")
    session = read_session_json(bridge_root / writer.game_id / "session.json").session

    choice_event = next(event for event in reversed(events) if event["type"] == "choices_shown")
    payload = choice_event["payload"]
    assert len(payload["choices"]) == 2
    assert payload["choices"][0]["choice_id"].startswith(f"{payload['line_id']}#choice0")
    assert session is not None
    assert session["state"]["is_menu_open"] is True
    assert session["state"]["choices"][1]["text"] == "去右边"


def test_rapidocr_text_adapter_groups_lines_and_filters_low_confidence() -> None:
    low_confidence = [
        ([[0, 0], [10, 0], [10, 8], [0, 8]], "A", 0.30),
        ([[12, 0], [20, 0], [20, 8], [12, 8]], "B", 0.40),
    ]
    assert _rapidocr_text_from_output(low_confidence) == ""

    output = [
        ([[20, 10], [32, 10], [32, 24], [20, 24]], "Hello", 0.92),
        ([[2, 10], [16, 10], [16, 24], [2, 24]], "雪乃", 0.97),
        ([[2, 40], [18, 40], [18, 54], [2, 54]], "今天", 0.96),
        ([[20, 40], [36, 40], [36, 54], [20, 54]], "回家", 0.95),
    ]
    assert _rapidocr_text_from_output(output) == "雪乃Hello\n今天回家"


def test_rapidocr_text_adapter_drops_short_low_confidence_ui_marker_line() -> None:
    output = [
        (
            [[10, 20], [360, 20], [360, 42], [10, 42]],
            "此时此刻，王生正摆了一桌宴席。",
            0.96,
        ),
        (
            [[380, 60], [398, 60], [398, 78], [380, 78]],
            "三·",
            0.52,
        ),
    ]

    lines = galgame_ocr_reader._rapidocr_lines_from_output(output)

    assert [text for text, _score, _box in lines] == [
        "此时此刻，王生正摆了一桌宴席。"
    ]
    assert _rapidocr_text_from_output(output) == "此时此刻，王生正摆了一桌宴席。"


def test_rapidocr_text_adapter_drops_single_ascii_ui_marker_line() -> None:
    output = [
        (
            [[10, 20], [300, 20], [300, 42], [10, 42]],
            "我抬眼一望，摇了摇头，心中暗笑。",
            0.96,
        ),
        (
            [[320, 60], [334, 60], [334, 78], [320, 78]],
            "P",
            0.71,
        ),
    ]

    assert _rapidocr_text_from_output(output) == "我抬眼一望，摇了摇头，心中暗笑。"


def test_fix_ocr_punctuation_confusion_for_cjk_dialogue() -> None:
    assert galgame_ocr_reader._fix_ocr_punctuation_confusion("这是对白.") == "这是对白。"
    assert galgame_ocr_reader._fix_ocr_punctuation_confusion("但是, 另一个") == "但是、另一个"
    assert galgame_ocr_reader._fix_ocr_punctuation_confusion("真的吗?") == "真的吗？"
    assert galgame_ocr_reader._fix_ocr_punctuation_confusion("对白。") == "对白。"


def test_ocr_dialogue_detection_accepts_fixed_cjk_punctuation() -> None:
    fixed = galgame_ocr_reader._fix_ocr_punctuation_confusion("这是对白.")
    assert galgame_ocr_reader._looks_like_ocr_dialogue_normalized_text(fixed)


def test_drop_ocr_chrome_noise_lines_keeps_dialogue() -> None:
    raw_text = "TheLamentingGeese\n有人是猪马牛羊，有人是虎豹豺狼。\n16°℃"

    filtered = galgame_ocr_reader._drop_ocr_chrome_noise_lines(
        raw_text,
        window_title="TheLamentingGeese",
    )

    assert filtered == "有人是猪马牛羊，有人是虎豹豺狼。"


def test_drop_ocr_chrome_noise_lines_returns_empty_when_all_lines_filtered() -> None:
    filtered = galgame_ocr_reader._drop_ocr_chrome_noise_lines(
        "TheLamentingGeese\n16°C",
        window_title="TheLamentingGeese",
    )

    assert filtered == ""


@pytest.mark.asyncio
async def test_ocr_reader_manager_auto_mode_prefers_rapidocr_when_available(
    tmp_path: Path,
    ocr_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.ocr_reader.inspect_rapidocr_installation",
        lambda **kwargs: {
            "installed": True,
            "detail": "installed",
            "detected_path": "C:/RapidOCR/site-packages/rapidocr_onnxruntime",
            "selected_model": "PP-OCRv5/ch/mobile",
        },
    )
    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.ocr_reader.RapidOcrBackend.extract_text",
        lambda self, image: "雪乃：你好。",
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
            rapidocr_install_target_dir=str(tmp_path / "RapidOCR"),
        ),
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
    )

    first = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
    second = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
    third = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    assert first.runtime["backend_kind"] == "rapidocr"
    assert second.runtime["backend_kind"] == "rapidocr"
    assert third.runtime["backend_kind"] == "rapidocr"
    assert second.runtime["detail"] == "receiving_text"
    assert second.runtime["ocr_context_state"] == "stable"


def test_rapidocr_missing_model_files_are_not_marked_available(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            rapidocr_install_target_dir=str(tmp_path / "RapidOCR"),
        ),
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
    )

    descriptor = manager._rapidocr_descriptor(
        {
            "installed": False,
            "detail": "missing_model_files",
            "detected_path": "",
            "selected_model": "PP-OCRv5/ch/mobile",
        },
        enabled=True,
    )

    assert descriptor.available is False
    assert descriptor.detail == "missing_model_files"


@pytest.mark.asyncio
async def test_ocr_reader_manager_excludes_neko_self_window_and_waits_for_valid_target(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
        ),
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=202,
                title="Galgame Plugin - N.E.K.O Plugin Manager - Chrome",
                process_name="chrome.exe",
                pid=1500,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )

    result = await manager.tick(
        bridge_sdk_available=False,
        memory_reader_runtime={},
    )

    assert result.runtime["status"] == "idle"
    assert result.runtime["detail"] == "waiting_for_valid_window"
    assert result.runtime["candidate_count"] == 0
    assert result.runtime["excluded_candidate_count"] == 1
    assert result.runtime["last_exclude_reason"] == "excluded_self_window"


@pytest.mark.parametrize(
    "title",
    [
        "Galgame Play Assistant",
        "Galgame 游玩助手",
        "Galgame 遊玩助手",
        "Galgame プレイアシスタント",
        "Galgame 플레이 도우미",
        "Помощник для Galgame",
    ],
)
def test_ocr_window_inventory_excludes_galgame_plugin_ui_titles(
    tmp_path: Path,
    ocr_runtime_root: Path,
    title: str,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(ocr_runtime_root),
        ),
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=303,
                title=title,
                process_name="electron.exe",
                pid=1600,
                width=1280,
                height=900,
                area=1280 * 900,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )

    eligible, excluded = manager._scan_window_inventory()

    assert eligible == []
    assert len(excluded) == 1
    assert excluded[0].exclude_reason == "excluded_self_window"


@pytest.mark.asyncio
async def test_ocr_reader_manager_prefers_manual_target_and_rebinds_by_signature(
    tmp_path: Path,
    ocr_runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    manual_window = DetectedGameWindow(
        hwnd=777,
        title="Aiyoku no Eustia",
        process_name="Aiyoku.exe",
        pid=4455,
    )
    rebound_window = DetectedGameWindow(
        hwnd=778,
        title=manual_window.title,
        process_name=manual_window.process_name,
        pid=manual_window.pid,
    )
    other_window = DetectedGameWindow(
        hwnd=100,
        title="Other Game",
        process_name="Other.exe",
        pid=1,
    )
    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.ocr_reader._foreground_window_handle",
        lambda: other_window.hwnd,
    )
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
        ),
        platform_fn=lambda: True,
        window_scanner=lambda: [other_window, rebound_window],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
    )
    manager.update_window_target(
        {
            "mode": "manual",
            "window_key": manual_window.window_key,
            "process_name": manual_window.process_name,
            "normalized_title": manual_window.normalized_title,
            "pid": manual_window.pid,
            "last_known_hwnd": manual_window.hwnd,
            "selected_at": "2026-04-24T10:00:00Z",
        }
    )

    result = await manager.tick(
        bridge_sdk_available=False,
        memory_reader_runtime={},
    )

    assert result.runtime["status"] == "active"
    assert result.runtime["detail"] == "attached_no_text_yet"
    assert result.runtime["process_name"] == "Aiyoku.exe"
    assert result.runtime["target_selection_mode"] == "manual"
    assert result.runtime["target_selection_detail"] == "manual_target_rebound"
    assert result.runtime["manual_target"]["window_key"] == rebound_window.window_key
    assert result.runtime["manual_target"]["last_known_hwnd"] == 778
    assert result.runtime["manual_target"]["pid"] == rebound_window.pid
    assert manager.current_window_target()["window_key"] == rebound_window.window_key
    assert manager.current_window_target()["pid"] == rebound_window.pid


@pytest.mark.asyncio
async def test_ocr_reader_manager_auto_prefers_memory_reader_target_over_stale_manual_target(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    stale_manual_window = DetectedGameWindow(
        hwnd=777,
        title="TheLamentingGeese",
        process_name="TheLamentingGeese.exe",
        pid=19224,
    )
    current_memory_window = DetectedGameWindow(
        hwnd=888,
        title="Senren Banka",
        process_name="SenrenBanka.exe",
        pid=34284,
    )
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
            reader_mode=READER_MODE_AUTO,
        ),
        platform_fn=lambda: True,
        window_scanner=lambda: [stale_manual_window, current_memory_window],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(),
    )
    manager.update_window_target(
        {
            "mode": "manual",
            "window_key": stale_manual_window.window_key,
            "process_name": stale_manual_window.process_name,
            "normalized_title": stale_manual_window.normalized_title,
            "pid": stale_manual_window.pid,
            "last_known_hwnd": stale_manual_window.hwnd,
            "selected_at": "2026-04-29T09:50:58Z",
        }
    )

    result = await manager.tick(
        bridge_sdk_available=False,
        memory_reader_runtime={
            "status": "active",
            "pid": current_memory_window.pid,
            "process_name": current_memory_window.process_name,
            "last_text_recent": False,
        },
    )

    assert capture_backend.capture_calls[0][0] == current_memory_window.hwnd
    assert result.runtime["process_name"] == current_memory_window.process_name
    assert result.runtime["target_selection_mode"] == "auto"
    assert (
        result.runtime["target_selection_detail"]
        == "manual_target_overridden_by_memory_reader_pid"
    )
    assert manager.current_window_target()["mode"] == "auto"
    assert manager.current_window_target()["window_key"] == ""


@pytest.mark.asyncio
async def test_ocr_reader_manager_auto_does_not_fall_back_to_stale_manual_when_memory_target_missing(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    stale_manual_window = DetectedGameWindow(
        hwnd=777,
        title="TheLamentingGeese",
        process_name="TheLamentingGeese.exe",
        pid=19224,
    )
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
            reader_mode=READER_MODE_AUTO,
        ),
        platform_fn=lambda: True,
        window_scanner=lambda: [stale_manual_window],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(),
    )
    manager.update_window_target(
        {
            "mode": "manual",
            "window_key": stale_manual_window.window_key,
            "process_name": stale_manual_window.process_name,
            "normalized_title": stale_manual_window.normalized_title,
            "pid": stale_manual_window.pid,
            "last_known_hwnd": stale_manual_window.hwnd,
            "selected_at": "2026-04-29T09:50:58Z",
        }
    )

    result = await manager.tick(
        bridge_sdk_available=False,
        memory_reader_runtime={
            "status": "active",
            "pid": 34284,
            "process_name": "SenrenBanka.exe",
            "last_text_recent": False,
        },
    )

    assert capture_backend.capture_calls == []
    assert result.runtime["status"] == "idle"
    assert result.runtime["detail"] == "waiting_for_valid_window"
    assert (
        result.runtime["target_selection_detail"]
        == "manual_target_overridden_by_memory_reader_unavailable"
    )
    assert manager.current_window_target()["mode"] == "auto"
    assert manager.current_window_target()["window_key"] == ""


@pytest.mark.asyncio
async def test_ocr_reader_manager_ocr_mode_keeps_manual_target_over_memory_reader_runtime(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    manual_window = DetectedGameWindow(
        hwnd=777,
        title="TheLamentingGeese",
        process_name="TheLamentingGeese.exe",
        pid=19224,
    )
    memory_window = DetectedGameWindow(
        hwnd=888,
        title="Senren Banka",
        process_name="SenrenBanka.exe",
        pid=34284,
    )
    capture_backend = _FakeCaptureBackend()
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
            reader_mode=READER_MODE_OCR,
        ),
        platform_fn=lambda: True,
        window_scanner=lambda: [manual_window, memory_window],
        capture_backend=capture_backend,
        ocr_backend=_FakeOcrBackend(),
    )
    manager.update_window_target(
        {
            "mode": "manual",
            "window_key": manual_window.window_key,
            "process_name": manual_window.process_name,
            "normalized_title": manual_window.normalized_title,
            "pid": manual_window.pid,
            "last_known_hwnd": manual_window.hwnd,
            "selected_at": "2026-04-29T09:50:58Z",
        }
    )

    result = await manager.tick(
        bridge_sdk_available=False,
        memory_reader_runtime={
            "status": "active",
            "pid": memory_window.pid,
            "process_name": memory_window.process_name,
            "last_text_recent": False,
        },
    )

    assert capture_backend.capture_calls[0][0] == manual_window.hwnd
    assert result.runtime["process_name"] == manual_window.process_name
    assert result.runtime["target_selection_mode"] == "manual"
    assert result.runtime["target_selection_detail"] == "manual_target_exact"


@pytest.mark.asyncio
async def test_ocr_reader_manager_blocks_text_that_looks_like_neko_plugin_ui(
    tmp_path: Path,
    ocr_runtime_root: Path,
) -> None:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    install_root = ocr_runtime_root
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root)
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(
            bridge_root,
            enabled=True,
            install_target_dir=str(install_root),
            poll_interval_seconds=999.0,
        ),
        platform_fn=lambda: True,
        window_scanner=lambda: [
            DetectedGameWindow(
                hwnd=101,
                title="Real Game Window",
                process_name="DemoGame.exe",
                pid=4242,
            )
        ],
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(
            [
                "RapidOCR model download queued task",
                "RapidOCR model download queued task",
            ]
        ),
        writer=writer,
    )

    await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})
    second = await manager.tick(bridge_sdk_available=False, memory_reader_runtime={})

    session = read_session_json(bridge_root / writer.game_id / "session.json").session

    assert second.runtime["detail"] == "self_ui_guard_blocked"
    assert session is not None
    assert session["state"]["text"] == ""
