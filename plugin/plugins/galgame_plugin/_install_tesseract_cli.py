"""Standalone Tesseract installer CLI.
Run from repo root with venv activated:
    python -m plugin.plugins.galgame_plugin._install_tesseract_cli
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from plugin.plugins.galgame_plugin.tesseract_support import (
    DEFAULT_TESSERACT_LANGUAGES,
    install_tesseract,
    inspect_tesseract_installation,
)


class SimpleLogger:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): print("[INFO]", *a)
    def warning(self, *a, **k): print("[WARN]", *a)
    def error(self, *a, **k): print("[ERR]", *a)


async def progress(event: dict) -> None:
    phase = event.get("phase", "?")
    message = event.get("message", "")
    progress_pct = event.get("progress", 0.0) * 100
    print(f"[{phase:12s}] {message} ({progress_pct:.0f}%)")


async def main() -> None:
    print("Checking current Tesseract installation...")
    status = inspect_tesseract_installation(
        configured_path="",
        install_target_dir_raw="",
        languages=DEFAULT_TESSERACT_LANGUAGES,
    )
    print(f"Status: {status['detail']}")
    print(f"Detected path: {status['detected_path'] or '(none)'}")
    print(f"Target dir: {status['target_dir'] or '(none)'}")

    if status["installed"]:
        print("Tesseract is already installed.")
        return

    print("\nStarting Tesseract installation (this may take a few minutes)...")
    try:
        result = await install_tesseract(
            logger=SimpleLogger(),
            configured_path="",
            install_target_dir_raw="",
            manifest_url="",
            timeout_seconds=300,
            languages=DEFAULT_TESSERACT_LANGUAGES,
            force=False,
            progress_callback=progress,
        )
        print(f"\nInstallation succeeded!")
        print(f"Summary: {result.get('summary')}")
        print(f"Path: {result.get('detected_path')}")
    except Exception as exc:
        print(f"\nInstallation failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
