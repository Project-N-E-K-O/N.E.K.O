"""Local quota-drop rule engine placeholder.

The conversation drop decision and credit grant path has moved to the private
NEKO-PC Electron forge-dropper. NEKO no longer decides drops, calls the cloud
directly, or broadcasts ``card_drop_available`` automatically from this module.

Hooks:
- ``on_text_message(lanlan_name, text) -> None`` is now a compatibility no-op.
- ``on_utterance(bucket, event) -> None`` remains a placeholder for future
  emotion-triggered rules.
"""

from __future__ import annotations

import asyncio
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from main_logic.quota import cloud_sync, ux_state

logger = logging.getLogger("neko.quota.dropper")

_RULES_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "quota_rules.yaml"
)


def _enabled() -> bool:
    if os.environ.get("NEKO_QUOTA_DROPPER_ENABLED", "0") not in ("1", "true", "TRUE", "yes"):
        return False
    return bool(os.environ.get("NEKO_SOCIAL_BASE_URL", "").strip())


@lru_cache(maxsize=1)
def _load_rules() -> dict[str, Any]:
    try:
        with _RULES_PATH.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("dropper: failed to load %s: %s", _RULES_PATH, exc)
        return {}


def _emit_card_drop_event(lanlan_name: str | None, trigger_type: str) -> None:
    """Broadcast a card-drop event to frontend clients when a drop fires.

    The event uses the websocket broadcaster registered in
    ``main_logic.agent_event_bus`` and is scheduled fire-and-forget on the
    current loop, keeping this lower-level module independent from ``app``.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _do() -> None:
        try:
            from main_logic.agent_event_bus import broadcast_ws_event
            await broadcast_ws_event({
                "type": "card_drop_available",
                "lanlan_name": lanlan_name,
                "trigger_type": trigger_type,
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("dropper: card_drop emit failed: %s", exc)

    loop.create_task(_do())


def _maybe_drop(lanlan_name: str | None, trigger_type: str, cooldown_sec: int, *, reset_word: bool = False) -> bool:
    """Run the shared drop path: cooldown, state update, cloud hint, and event."""
    if not ux_state.can_trigger(trigger_type, cooldown_sec):
        return False
    snapshot = ux_state.record_drop(trigger_type, reset_word_count=reset_word)
    logger.info(
        "quota: drop fired trigger=%s lanlan=%s dropped_today=%d",
        trigger_type, lanlan_name, snapshot.get("dropped_count", -1),
    )
    cloud_sync.send_drop_hint(lanlan_name, trigger_type)
    _emit_card_drop_event(lanlan_name, trigger_type)
    return True


def on_text_message(lanlan_name: str, text: str) -> None:
    """Entry point for ``register_text_user_message_hook``; always return None.

    The conversation drop decision and credit grant path lives in the private
    NEKO-PC Electron forge-dropper. This hook remains a no-op for compatibility
    so public NEKO builds do not expose rules or call cloud grant endpoints.
    """
    return None


def on_utterance(bucket: str, event: dict) -> None:
    """Entry point for ``register_user_utterance_sink``.

    The current M2-j placeholder only logs debug information. Future versions can
    add emotion-intensity based rules.
    """
    if not _enabled():
        return
    # 留位：未来从 event 里读 emotion / intensity 字段
    logger.debug("dropper: utterance event ignored (emotion rule not yet enabled): bucket=%s", bucket)
