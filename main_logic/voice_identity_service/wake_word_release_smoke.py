"""Offline release gate executed by the frozen binary, before app startup."""

from __future__ import annotations

import os
import asyncio
from pathlib import Path
import tempfile
import time
import shutil


async def check_preference_worker(model_dir: Path | None = None) -> None:
    """Exercise the production five-second spawn budget in the frozen binary."""
    from config.voice_wake_word import wake_word_preference
    from .resource_manager import VoiceResourceManager, _run_worker
    from .wake_word_bundle import MODEL_SHA256, resolve_cached_model_dir

    # The model smoke uses managed deployment settings; preference acceptance
    # uses a disposable cache and never modifies the user's actual preference.
    names = ("NEKO_WAKE_WORD_MODEL_DIR", "NEKO_WAKE_WORD_ENABLED")
    previous = {name: os.environ.pop(name, None) for name in names}
    try:
        with tempfile.TemporaryDirectory(prefix="neko-wake-preference-") as directory:
            root = Path(directory).resolve()
            manager = VoiceResourceManager(lambda: False, cache_root=root)
            try:
                for enabled in (True, False):
                    started = time.monotonic()
                    result = await manager.save_preference(enabled)
                    if result["enabled"] is not enabled or wake_word_preference(root)["enabled"] is not enabled:
                        raise ValueError("wake_preference_smoke_failed")
                    print(f"WAKE_WORD_PREFERENCE_SMOKE_READY enabled={enabled} elapsed={time.monotonic() - started:.3f}", flush=True)
                if model_dir is not None:
                    directory = root / "versions" / MODEL_SHA256
                    await asyncio.to_thread(shutil.copytree, model_dir, directory)
                    started = time.monotonic()
                    result = await _run_worker("publish", False, str(root), MODEL_SHA256.encode("ascii"), timeout=5)
                    if not result["installed"] or resolve_cached_model_dir(root) != directory:
                        raise ValueError("wake_publication_smoke_failed")
                    print(f"WAKE_WORD_PUBLICATION_SMOKE_READY elapsed={time.monotonic() - started:.3f}", flush=True)
            finally:
                await manager.close()
    finally:
        for name, value in previous.items():
            if value is not None:
                os.environ[name] = value


def main() -> int:
    try:
        import sherpa_onnx
        from main_logic.voice_input.wake_word.sherpa_backend import SUPPORTED_RUNTIME_VERSION, SherpaWakeWordConfig, validate_wake_word_resources
        from config.voice_wake_word import DEFAULT_WAKE_WORD_KEYWORDS
        if sherpa_onnx.__version__ != SUPPORTED_RUNTIME_VERSION or sherpa_onnx.version != SUPPORTED_RUNTIME_VERSION:
            raise ValueError("WAKE_WORD_RUNTIME_FIX_REQUIRED")
        model_dir = os.environ.get("NEKO_WAKE_WORD_MODEL_DIR", "")
        if not model_dir:
            raise ValueError("WAKE_WORD_MODEL_MISSING")
        validate_wake_word_resources(SherpaWakeWordConfig(model_dir=model_dir, keywords=DEFAULT_WAKE_WORD_KEYWORDS))
        asyncio.run(check_preference_worker(Path(model_dir)))
    except Exception:
        print("WAKE_WORD_RELEASE_SMOKE_FAILED", flush=True)
        return 1
    print("WAKE_WORD_RELEASE_SMOKE_READY", flush=True)
    return 0
