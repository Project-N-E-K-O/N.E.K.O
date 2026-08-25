from __future__ import annotations

import asyncio
import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from plugin.plugins.study_companion import entry_ocr_entries
from plugin.plugins.study_companion.entry_ocr_entries import _OcrEntriesMixin
from plugin.plugins.study_companion.interactive_screenshot import (
    InteractiveCaptureError,
    InteractiveScreenshotClient,
    _resolve_default_base_url,
)
from plugin.plugins.study_companion.models import OcrSnapshot
from plugin.plugins.study_companion.state import build_initial_state
from plugin.sdk.plugin import Err, Ok


pytestmark = pytest.mark.unit

PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
LOCALES = ("en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW")


def _png_data_url() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (3, 2), "white").save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def test_interactive_capture_resolves_only_a_valid_local_main_server_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEKO_MAIN_SERVER_PORT", "65432")
    assert _resolve_default_base_url() == "http://127.0.0.1:65432"

    monkeypatch.setenv("NEKO_MAIN_SERVER_PORT", "invalid")
    monkeypatch.setenv("MAIN_SERVER_PORT", "65536")
    assert _resolve_default_base_url() == "http://127.0.0.1:48911"

    with pytest.raises(ValueError, match="loopback"):
        InteractiveScreenshotClient(base_url="https://example.com")


@pytest.mark.asyncio
async def test_interactive_capture_waits_then_posts_the_bounded_selection_contract() -> None:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"success": True, "data": _png_data_url()},
        )

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)

    client = InteractiveScreenshotClient(
        base_url="http://127.0.0.1:48911",
        lanlan_name="requesting",
        transport=httpx.MockTransport(_handler),
        sleep=_sleep,
    )
    result = await client.capture_region()

    assert sleeps == [2.0]
    assert len(requests) == 1
    assert requests[0].url == "http://127.0.0.1:48911/api/screenshot/interactive"
    assert json.loads(requests[0].content) == {
        "selection_only": True,
        "copy_to_clipboard": False,
        "session_timeout_ms": 45000,
        "lanlan_name": "requesting",
    }
    assert result.canceled is False
    assert result.image is not None
    assert result.image.size == (3, 2)
    assert result.image.mode == "RGB"


@pytest.mark.asyncio
async def test_interactive_capture_keeps_cancel_as_a_domain_result() -> None:
    client = InteractiveScreenshotClient(
        activation_delay_seconds=0,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"success": False, "canceled": True},
            )
        ),
    )

    result = await client.capture_region()

    assert result.canceled is True
    assert result.image is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [(409, "capture_busy"), (503, "no_renderer"), (504, "renderer_timeout")],
)
async def test_interactive_capture_surfaces_safe_core_error_codes(
    status_code: int,
    error_code: str,
) -> None:
    client = InteractiveScreenshotClient(
        activation_delay_seconds=0,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                status_code,
                json={"success": False, "error": error_code},
            )
        ),
    )

    with pytest.raises(InteractiveCaptureError, match=error_code):
        await client.capture_region()


@pytest.mark.asyncio
async def test_interactive_capture_maps_bridge_error_to_localized_code() -> None:
    client = InteractiveScreenshotClient(
        base_url="http://127.0.0.1:48911",
        activation_delay_seconds=0,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                502,
                json={"success": False, "error": "bridge_error"},
            )
        ),
    )

    with pytest.raises(InteractiveCaptureError, match="no_renderer"):
        await client.capture_region()


@pytest.mark.asyncio
async def test_interactive_capture_rejects_missing_or_invalid_image_data() -> None:
    for payload in (
        {"success": True},
        {"success": True, "data": "data:text/plain;base64,SGVsbG8="},
        {"success": True, "data": "data:image/png;base64,not-base64"},
    ):
        client = InteractiveScreenshotClient(
            activation_delay_seconds=0,
            transport=httpx.MockTransport(
                lambda _request, payload=payload: httpx.Response(200, json=payload)
            ),
        )
        with pytest.raises(InteractiveCaptureError):
            await client.capture_region()


class _Pipeline:
    def __init__(self) -> None:
        self.fullscreen_calls = 0
        self.images: list[object] = []

    def capture_snapshot(self) -> OcrSnapshot:
        self.fullscreen_calls += 1
        return OcrSnapshot(text="fullscreen", status="ok", captured_at="full-time")

    def snapshot_from_image(self, image: object) -> OcrSnapshot:
        self.images.append(image)
        return OcrSnapshot(text="selected text", status="ok", captured_at="selected-time")


def _entry_harness(pipeline: _Pipeline) -> SimpleNamespace:
    async def _persist_state() -> None:
        harness.persisted += 1

    async def _update_screen_classification(text: str, update_empty: bool = False):
        return {"text": text, "update_empty": update_empty}

    harness = SimpleNamespace(
        _ocr_pipeline=pipeline,
        _supervision=None,
        _lock=asyncio.Lock(),
        _state=build_initial_state(),
        _persist_state=_persist_state,
        _update_screen_classification=_update_screen_classification,
        persisted=0,
    )
    return harness


@pytest.mark.asyncio
async def test_ocr_entry_interactive_path_uses_only_the_selected_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_image = object()
    pipeline = _Pipeline()
    harness = _entry_harness(pipeline)

    async def _capture(*, lanlan_name=None):
        assert lanlan_name is None
        return SimpleNamespace(image=selected_image, canceled=False)

    monkeypatch.setattr(entry_ocr_entries, "capture_interactive_region", _capture)

    result = await _OcrEntriesMixin.study_ocr_snapshot(
        harness,
        capture_mode="interactive",
    )

    assert isinstance(result, Ok)
    assert result.value["text"] == "selected text"
    assert pipeline.images == [selected_image]
    assert pipeline.fullscreen_calls == 0
    assert harness._state.last_ocr_text == "selected text"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code",
    ["no_renderer", "main_server_unavailable", "interactive_unavailable"],
)
async def test_ocr_entry_falls_back_to_fullscreen_when_interactive_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    error_code: str,
) -> None:
    pipeline = _Pipeline()
    harness = _entry_harness(pipeline)

    async def _capture(*, lanlan_name=None):
        raise InteractiveCaptureError(f"interactive_capture: {error_code}")

    monkeypatch.setattr(entry_ocr_entries, "capture_interactive_region", _capture)

    result = await _OcrEntriesMixin.study_ocr_snapshot(
        harness,
        capture_mode="interactive",
    )

    assert isinstance(result, Ok)
    assert result.value["text"] == "fullscreen"
    assert result.value["capture_mode_requested"] == "interactive"
    assert result.value["capture_mode_used"] == "fullscreen"
    assert result.value["interactive_fallback_reason"] == error_code
    assert pipeline.fullscreen_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "router_error",
    [
        "interactive screenshot is only supported on macOS or Windows",
        "backend is configured as remote (NEKO_ACTIVITY_TRACKER_REMOTE); local interactive screenshot disabled",
    ],
)
async def test_interactive_capture_normalizes_unsupported_deployments(
    router_error: str,
) -> None:
    client = InteractiveScreenshotClient(
        activation_delay_seconds=0,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                501,
                json={"success": False, "error": router_error},
            )
        ),
    )

    with pytest.raises(InteractiveCaptureError, match="interactive_unavailable"):
        await client.capture_region()


@pytest.mark.asyncio
async def test_ocr_entry_routes_interactive_capture_to_requesting_lanlan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_image = object()
    captured_lanlans: list[str | None] = []
    harness = _entry_harness(_Pipeline())

    async def _capture(*, lanlan_name=None):
        captured_lanlans.append(lanlan_name)
        return SimpleNamespace(image=selected_image, canceled=False)

    monkeypatch.setattr(entry_ocr_entries, "capture_interactive_region", _capture)

    result = await _OcrEntriesMixin.study_ocr_snapshot(
        harness,
        capture_mode="interactive",
        _ctx={"lanlan_name": "requesting"},
    )

    assert isinstance(result, Ok)
    assert captured_lanlans == ["requesting"]


@pytest.mark.asyncio
async def test_ocr_entry_ui_capture_ignores_cached_lanlan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_lanlans: list[str | None] = []
    harness = _entry_harness(_Pipeline())
    harness._resolve_study_target_lanlan = lambda _kwargs: "stale-character"

    async def _capture(*, lanlan_name=None):
        captured_lanlans.append(lanlan_name)
        return SimpleNamespace(image=object(), canceled=False)

    monkeypatch.setattr(entry_ocr_entries, "capture_interactive_region", _capture)

    result = await _OcrEntriesMixin.study_ocr_snapshot(
        harness,
        capture_mode="interactive",
    )

    assert isinstance(result, Ok)
    assert captured_lanlans == [None]


@pytest.mark.asyncio
async def test_ocr_entry_cancel_preserves_all_study_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _Pipeline()
    harness = _entry_harness(pipeline)
    harness._state.last_ocr_text = "existing input"
    harness._state.last_ocr_at = "existing-time"

    async def _capture(*, lanlan_name=None):
        assert lanlan_name is None
        return SimpleNamespace(image=None, canceled=True)

    monkeypatch.setattr(entry_ocr_entries, "capture_interactive_region", _capture)

    result = await _OcrEntriesMixin.study_ocr_snapshot(
        harness,
        capture_mode="interactive",
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "canceled"
    assert harness._state.last_ocr_text == "existing input"
    assert harness._state.last_ocr_at == "existing-time"
    assert harness.persisted == 0
    assert pipeline.images == []
    assert pipeline.fullscreen_calls == 0


@pytest.mark.asyncio
async def test_ocr_entry_fullscreen_default_remains_backward_compatible() -> None:
    pipeline = _Pipeline()
    harness = _entry_harness(pipeline)

    result = await _OcrEntriesMixin.study_ocr_snapshot(harness)

    assert isinstance(result, Ok)
    assert result.value["text"] == "fullscreen"
    assert pipeline.fullscreen_calls == 1
    assert pipeline.images == []


@pytest.mark.asyncio
async def test_ocr_entry_rejects_unknown_capture_mode_without_fallback() -> None:
    pipeline = _Pipeline()
    harness = _entry_harness(pipeline)

    result = await _OcrEntriesMixin.study_ocr_snapshot(
        harness,
        capture_mode="window",
    )

    assert isinstance(result, Err)
    assert pipeline.fullscreen_calls == 0


def test_ocr_entry_and_static_ui_expose_interactive_timeout_contract() -> None:
    meta = _OcrEntriesMixin.study_ocr_snapshot.__neko_event_meta__
    capture_mode = meta.input_schema["properties"]["capture_mode"]
    main_js = (PLUGIN_DIR / "static" / "main.js").read_text(encoding="utf-8")

    assert capture_mode == {
        "type": "string",
        "enum": ["fullscreen", "interactive"],
        "default": "fullscreen",
    }
    assert meta.timeout == 90.0
    assert set(meta.llm_result_fields or ()) >= {
        "capture_mode_requested",
        "capture_mode_used",
        "interactive_fallback_reason",
    }
    assert "study_ocr_snapshot: 100000" in main_js
    assert "callPlugin('study_ocr_snapshot', { capture_mode: 'interactive' })" in main_js
    assert "if (data.status === 'canceled')" in main_js
    assert "await refreshStatus({ updateReply: false });" in main_js
    assert "function interactiveOcrErrorMessage(error)" in main_js
    assert "function interactiveOcrFallbackMessage(reason)" in main_js
    assert "errorText.includes('capture_busy')" in main_js
    assert "errorText.includes('renderer_timeout')" in main_js
    assert "errorText.includes('SCREENSHOT_OVERLAY_SESSION_TIMEOUT')" in main_js
    assert "errorText.includes('no_renderer')" in main_js
    assert "data.capture_mode_used === 'fullscreen'" in main_js
    assert "data.interactive_fallback_reason" in main_js
    assert "if (!localizedMessage)" in main_js
    assert "throw error;" in main_js
    run_ocr = main_js[
        main_js.index("async function runOcr(options = {})") : main_js.index(
            "async function explainText(options = {})"
        )
    ]
    explain_text = main_js[
        main_js.index("async function explainText(options = {})") : main_js.index(
            "async function generateQuestion()"
        )
    ]
    coach_action = main_js[
        main_js.index("async function handleNekoCoachAction(action)") : main_js.index(
            "const documentController ="
        )
    ]
    bind_button = main_js[
        main_js.index("function bindButton(button, handler)") : main_js.index(
            "async function handleNekoCoachAction(action)"
        )
    ]
    canceled = run_ocr[
        run_ocr.index("if (data.status === 'canceled')") : run_ocr.index(
            "const n = data.capture_mode_used === 'fullscreen'"
        )
    ]
    assert "return data;" in canceled
    assert "studyInput.value" not in canceled
    assert "setReply(" not in canceled
    assert "studyInput.value = data.text;" in run_ocr
    assert "let ocrN = '';" in main_js
    assert "let ocrT = '';" in main_js
    assert "ocrN = '';" not in canceled
    assert "const s = data.status || 'unknown';" in run_ocr
    assert "['ok', 'empty'].includes(s)" in run_ocr
    assert "const text = String(data.text || '').trim();" in run_ocr
    assert "if (text)" in run_ocr
    assert "ocrN = n;" in run_ocr
    assert "ocrT = text;" in run_ocr
    assert "ocrN = ocrT = '';" in run_ocr
    assert "if (!n) throw error;" in run_ocr
    assert run_ocr.index("await refreshStatus({ updateReply: false }).catch") < run_ocr.index(
        "setStatus(n || tf('ui.status.ocr_result'"
    )
    assert "{ status: s }" in run_ocr
    assert "const n = (options.notice || (text === ocrT ? ocrN : '')).trim();" in explain_text
    assert "studyInput.addEventListener('input', () => { ocrN = ocrT = ''; });" in main_js
    assert "setReply(n ? `${n}\\n\\n${pending}` : pending);" in explain_text
    assert "error.n = n;" in explain_text
    assert "setStatus(data.degraded" in explain_text
    assert "await refreshStatus({ updateReply: false }).catch((error) => { if (!n) throw error; setStatus(t('ui.status.reply_ready', 'Reply ready')); });" in explain_text
    assert "[error?.n,formatPluginError(error)].filter(Boolean).join('\\n\\n')" in bind_button
    assert "await explainText({ notice: ocrN });" in coach_action
    assert "if (kind === 'study') ocrN = ocrT = '';" in main_js
    assert "generateQuestion()" not in run_ocr


def test_interactive_ocr_status_strings_exist_in_all_eight_locales() -> None:
    required = {
        "ui.status.preparing_ocr_selection",
        "ui.status.ocr_canceled",
        "ui.status.ocr_fallback_fullscreen",
        "ui.error.interactive_ocr_busy",
        "ui.error.interactive_ocr_timeout",
        "ui.error.interactive_ocr_unavailable",
    }
    for locale in LOCALES:
        bundle = json.loads(
            (PLUGIN_DIR / "i18n" / f"{locale}.json").read_text(encoding="utf-8")
        )
        assert required <= set(bundle), locale
        assert all(str(bundle[key]).strip() for key in required), locale
