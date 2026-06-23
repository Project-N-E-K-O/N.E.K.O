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

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


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
