"""Standalone validation script for the OCR capture + recognition pipeline.

Usage (from repo root with venv activated):
    python -m plugin.plugins.galgame_plugin.test_ocr_pipeline

This will:
1. Scan for visible game-sized windows
2. Capture the largest candidate window
3. Run Tesseract OCR on the cropped text region
4. Print the raw OCR result so you can verify the pipeline works

Prerequisites:
- Tesseract OCR installed and languages available (e.g. chi_sim, eng)
- Windows with pywin32 and Pillow in the venv
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from plugin.plugins.galgame_plugin.models import (
    DEFAULT_OCR_CAPTURE_BOTTOM_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_LEFT_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_RIGHT_INSET_RATIO,
    DEFAULT_OCR_CAPTURE_TOP_RATIO,
    GalgameConfig,
)
from plugin.plugins.galgame_plugin.ocr_reader import (
    OcrCaptureProfile,
    OcrReaderManager,
    Win32CaptureBackend,
    TesseractOcrBackend,
    _default_window_scanner,
)
from plugin.plugins.galgame_plugin.rapidocr_support import DEFAULT_RAPIDOCR_OCR_VERSION


def _noop_logger():
    class NoopLogger:
        def debug(self, *a, **k): pass
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
    return NoopLogger()


async def main() -> None:
    print("=" * 60)
    print("OCR Pipeline Validation")
    print("=" * 60)

    windows = _default_window_scanner()
    if not windows:
        print("No suitable capture target found.")
        print("Make sure a game window is visible and larger than 400x300.")
        return

    print(f"Found {len(windows)} candidate window(s):")
    for i, w in enumerate(windows[:5]):
        print(f"  {i+1}. hwnd={w.hwnd} pid={w.pid} title={w.title!r} process={w.process_name!r}")

    target = windows[0]
    print(f"\nUsing target: {target.title!r} ({target.process_name or 'unknown'})")

    capture = Win32CaptureBackend()
    if not capture.is_available():
        print("ERROR: Win32 capture backend is not available.")
        return

    profile = OcrCaptureProfile(
        left_inset_ratio=DEFAULT_OCR_CAPTURE_LEFT_INSET_RATIO,
        right_inset_ratio=DEFAULT_OCR_CAPTURE_RIGHT_INSET_RATIO,
        top_ratio=DEFAULT_OCR_CAPTURE_TOP_RATIO,
        bottom_inset_ratio=DEFAULT_OCR_CAPTURE_BOTTOM_INSET_RATIO,
    )

    print("\nCapturing frame...")
    try:
        frame = capture.capture_frame(target, profile)
        print(f"  Captured size: {frame.size}")
    except Exception as exc:
        print(f"  CAPTURE FAILED: {exc}")
        return

    # Save crop for visual inspection
    debug_path = Path("ocr_debug_capture.png")
    frame.save(str(debug_path))
    print(f"  Saved cropped frame to: {debug_path.resolve()}")

    # Check Tesseract
    ocr = TesseractOcrBackend(
        tesseract_path="",
        install_target_dir_raw="",
        languages="chi_sim+jpn+eng",
    )
    if not ocr.is_available():
        print("\nERROR: Tesseract OCR is not available.")
        print("  - Make sure tesseract.exe is installed")
        print("  - Required .traineddata files exist in tessdata/")
        return

    print("\nRunning OCR...")
    try:
        text = ocr.extract_text(frame)
    except Exception as exc:
        print(f"  OCR FAILED: {exc}")
        return

    print("\n" + "-" * 60)
    print("OCR RESULT:")
    print("-" * 60)
    print(text if text else "(empty)")
    print("-" * 60)

    # Optional: dry-run through manager tick logic
    print("\nDry-run manager tick...")
    config = GalgameConfig(
        bridge_root=Path.home() / "galgame-bridge-test",
        active_poll_interval_seconds=1.0,
        idle_poll_interval_seconds=3.0,
        stale_after_seconds=15.0,
        history_events_limit=500,
        history_lines_limit=200,
        history_choices_limit=50,
        dedupe_window_limit=64,
        warmup_replay_bytes_limit=65536,
        warmup_replay_events_limit=50,
        default_mode="companion",
        push_notifications=True,
        llm_call_timeout_seconds=15.0,
        llm_max_in_flight=2,
        llm_request_cache_ttl_seconds=2.0,
        llm_target_entry_ref="",
        reader_mode="auto",
        memory_reader_enabled=False,
        memory_reader_textractor_path="",
        memory_reader_install_release_api_url="",
        memory_reader_install_target_dir="",
        memory_reader_install_timeout_seconds=60.0,
        memory_reader_auto_detect=True,
        memory_reader_hook_codes=[],
        memory_reader_poll_interval_seconds=1.0,
        ocr_reader_enabled=True,
        ocr_reader_backend_selection="auto",
        ocr_reader_capture_backend="auto",
        ocr_reader_tesseract_path="",
        ocr_reader_install_manifest_url="",
        ocr_reader_install_target_dir="",
        ocr_reader_install_timeout_seconds=60.0,
        ocr_reader_poll_interval_seconds=2.0,
        ocr_reader_trigger_mode="after_advance",
        ocr_reader_no_text_takeover_after_seconds=30.0,
        ocr_reader_languages="chi_sim+jpn+eng",
        ocr_reader_left_inset_ratio=DEFAULT_OCR_CAPTURE_LEFT_INSET_RATIO,
        ocr_reader_right_inset_ratio=DEFAULT_OCR_CAPTURE_RIGHT_INSET_RATIO,
        ocr_reader_top_ratio=DEFAULT_OCR_CAPTURE_TOP_RATIO,
        ocr_reader_bottom_inset_ratio=DEFAULT_OCR_CAPTURE_BOTTOM_INSET_RATIO,
        rapidocr_enabled=True,
        rapidocr_install_manifest_url="",
        rapidocr_install_target_dir="",
        rapidocr_install_timeout_seconds=180.0,
        rapidocr_engine_type="onnxruntime",
        rapidocr_lang_type="ch",
        rapidocr_model_type="mobile",
        rapidocr_ocr_version=DEFAULT_RAPIDOCR_OCR_VERSION,
    )
    mgr = OcrReaderManager(logger=_noop_logger(), config=config)
    tick = await mgr.tick(bridge_sdk_available=False, memory_reader_runtime={})
    print(f"  Manager status: {tick.runtime.get('status')}")
    print(f"  Manager detail: {tick.runtime.get('detail')}")
    if tick.warnings:
        for w in tick.warnings:
            print(f"  Warning: {w}")

    print("\nValidation complete.")


if __name__ == "__main__":
    asyncio.run(main())
