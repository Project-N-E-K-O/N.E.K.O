import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_json_async, read_json_async
from utils.logger_config import get_module_logger


logger = get_module_logger(__name__, "Main")

STATE_VERSION = 1
STATE_FILENAME = "new_character_greeting_state.json"

_lock = asyncio.Lock()


def _state_path(config_manager) -> Path:
    return Path(config_manager.local_state_dir) / STATE_FILENAME


def _empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "pending": {}}


def _normalize_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _empty_state()
    pending = raw.get("pending")
    if not isinstance(pending, dict):
        pending = {}
    return {"version": STATE_VERSION, "pending": dict(pending)}


async def _load_state(config_manager) -> dict[str, Any]:
    path = _state_path(config_manager)
    if not await asyncio.to_thread(path.exists):
        return _empty_state()
    try:
        return _normalize_state(await read_json_async(path))
    except Exception as exc:
        logger.warning("load new character greeting state failed: %s", exc)
        return _empty_state()


async def _save_state(config_manager, state: dict[str, Any]) -> None:
    path = _state_path(config_manager)
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    await atomic_write_json_async(path, _normalize_state(state), ensure_ascii=False, indent=2)


def _clean_name(character_name: str) -> str:
    return str(character_name or "").strip()


async def mark_pending(config_manager, character_name: str, source: str = "") -> None:
    name = _clean_name(character_name)
    if not name:
        return
    async with _lock:
        state = await _load_state(config_manager)
        pending = state.setdefault("pending", {})
        pending[name] = {
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": str(source or "").strip(),
        }
        await _save_state(config_manager, state)


async def has_pending(config_manager, character_name: str) -> bool:
    name = _clean_name(character_name)
    if not name:
        return False
    async with _lock:
        state = await _load_state(config_manager)
        return name in state.get("pending", {})


async def remove_pending(config_manager, character_name: str) -> None:
    name = _clean_name(character_name)
    if not name:
        return
    async with _lock:
        state = await _load_state(config_manager)
        pending = state.setdefault("pending", {})
        if name in pending:
            pending.pop(name, None)
            await _save_state(config_manager, state)


async def rename_pending(config_manager, old_name: str, new_name: str) -> None:
    old = _clean_name(old_name)
    new = _clean_name(new_name)
    if not old or not new or old == new:
        return
    async with _lock:
        state = await _load_state(config_manager)
        pending = state.setdefault("pending", {})
        if old not in pending:
            return
        pending[new] = pending.pop(old)
        await _save_state(config_manager, state)
