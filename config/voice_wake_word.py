"""Local wake-word deployment configuration (optional until models are installed).

Set NEKO_WAKE_WORD_MODEL_DIR to the provisioned sherpa-onnx model directory
before starting the server. An empty/unset directory keeps voiceprint-only
activation. Model loading and validation belong to the asynchronous detector.
"""

from __future__ import annotations

import os
import json
from pathlib import Path
from config.resource_file_lock import ResourceFileLockBusy, canonical_resource_root, resource_file_lock


# Phonetic token sequences, not English letter-by-letter spelling of the name.
# Near pronunciations are intentionally accepted and share the Chinese wake label.
# These are model vocabulary inputs; actual recognition quality needs recordings.
DEFAULT_WAKE_WORD_KEYWORDS = (
    "y ōu y í @悠宜",
    "Y UW1 IY0 @yui",
    "y ōu y ú @悠宜",
    "l iú y ú @悠宜",
)


def wake_word_model_dir() -> str | None:
    """Return the opt-in path without filesystem I/O on the session setup path."""
    value = os.environ.get("NEKO_WAKE_WORD_MODEL_DIR", "").strip()
    return value or None


def wake_word_cache_root() -> Path:
    """Per-user resource directory; this function performs no filesystem I/O."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "N.E.K.O" / "voice-resources" / "wake-word"


def wake_word_preference(cache_root: Path | None = None) -> dict:
    """Read on a worker thread. Deployment settings retain precedence."""
    explicit = wake_word_model_dir()
    override = os.environ.get("NEKO_WAKE_WORD_ENABLED", "").strip().lower()
    if explicit or override:
        valid = override in {"", "0", "1", "false", "true"}
        return {"enabled": bool(explicit) if not override else override in {"1", "true"},
                "managed": True, "reason": None if valid else "wake_preference_invalid"}
    try:
        path = (cache_root or wake_word_cache_root()) / "preference.json"
        if path.is_symlink():
            raise ValueError
        if not path.exists():
            return {"enabled": False, "managed": False, "reason": None}
        if path.stat().st_size > 1024:
            raise ValueError
        data = json.loads(path.read_text(encoding="utf-8"))
        if (set(data) != {"schema", "enabled"} or type(data["schema"]) is not int
                or data["schema"] != 1 or type(data["enabled"]) is not bool):
            raise ValueError
        return {"enabled": data["enabled"], "managed": False, "reason": None}
    except (OSError, ValueError, TypeError, RuntimeError):
        # A read failure must never become an implicit write of disabled state.
        return {"enabled": False, "managed": False, "reason": "wake_preference_unavailable"}


def save_wake_word_preference(enabled: bool, cache_root: Path | None = None) -> dict:
    """Atomic preference write, independent of Owner profile/filter settings."""
    if type(enabled) is not bool:
        raise ValueError("invalid_enabled")
    try:
        root = cache_root or wake_word_cache_root()
    except (OSError, RuntimeError) as exc:
        raise ValueError("wake_preference_unavailable") from exc
    before = wake_word_preference(root)
    if before["managed"]:
        raise ValueError("wake_preference_managed")
    if before["reason"] and before["reason"] != "wake_preference_unavailable":
        raise ValueError(before["reason"])
    root = canonical_resource_root(root)
    if (root / "preference.json").is_symlink():
        raise ValueError("resource_cache_unsafe")
    root.mkdir(parents=True, exist_ok=True)
    pending = root / "preference.pending"
    try:
        with resource_file_lock(root / "preference.lock"):
            if pending.is_symlink() or pending.exists() and (not pending.is_file() or pending.stat().st_size > 1024):
                raise ValueError("resource_cache_unsafe")
            try:
                with pending.open("w", encoding="utf-8") as out:
                    json.dump({"schema": 1, "enabled": enabled}, out)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(pending, root / "preference.json")
            finally:
                pending.unlink(missing_ok=True)
    except ResourceFileLockBusy as exc:
        raise ValueError("resource_operation_busy") from exc
    return {"enabled": enabled, "managed": False, "reason": None}


__all__ = ["DEFAULT_WAKE_WORD_KEYWORDS", "wake_word_model_dir", "wake_word_cache_root", "wake_word_preference", "save_wake_word_preference"]
