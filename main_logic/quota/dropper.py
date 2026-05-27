"""配额掉落规则引擎（NEKO 本地）。

提供两个 hook：
- ``on_text_message(lanlan_name, text) -> None``：每次用户文本消息触发，按
  ``config/quota_rules.yaml`` 的 word_count + keywords 两类规则判定，命中后
  写本地 UX state + 异步调云端 drop-hint。
- ``on_utterance(bucket, event) -> None``：M2-j v1 留位（emotion 触发 v2 再开）。

启用门槛：需同时 ``NEKO_QUOTA_DROPPER_ENABLED=1`` 且 ``NEKO_SOCIAL_BASE_URL`` 已配。
"""

from __future__ import annotations

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


def _maybe_drop(lanlan_name: str | None, trigger_type: str, cooldown_sec: int, *, reset_word: bool = False) -> bool:
    """统一的"触发掉落"路径：cooldown 检查 → 更新 state → fire-and-forget 调云端。"""
    if not ux_state.can_trigger(trigger_type, cooldown_sec):
        return False
    snapshot = ux_state.record_drop(trigger_type, reset_word_count=reset_word)
    logger.info(
        "quota: drop fired trigger=%s lanlan=%s dropped_today=%d",
        trigger_type, lanlan_name, snapshot.get("dropped_count", -1),
    )
    cloud_sync.send_drop_hint(lanlan_name, trigger_type)
    return True


def on_text_message(lanlan_name: str, text: str) -> None:
    """register_text_user_message_hook 入口。必须返回 None 不抢现有消费者。

    规则：
    - word_count：累计字符数达 threshold 触发（消耗后归零累计）
    - keywords：text 中包含任一关键词触发
    """
    if not _enabled():
        return None
    if not isinstance(text, str) or not text.strip():
        return None

    rules = _load_rules().get("rules") or {}

    # ---- keywords（优先级高于 word_count；命中即触发，不消耗字数累计）----
    kw_cfg = rules.get("keywords") or {}
    if kw_cfg.get("enabled", True):
        kw_list = kw_cfg.get("list") or []
        for kw in kw_list:
            if not isinstance(kw, str):
                continue
            if kw and kw in text:
                _maybe_drop(
                    lanlan_name, "keywords",
                    int(kw_cfg.get("cooldown_sec", 1800)),
                )
                break  # 多个 keyword 同句只算一次

    # ---- word_count（每次累加；过阈值且 cooldown 满足才触发）----
    wc_cfg = rules.get("word_count") or {}
    if wc_cfg.get("enabled", True):
        threshold = int(wc_cfg.get("threshold", 800))
        cooldown = int(wc_cfg.get("cooldown_sec", 600))
        new_total = ux_state.add_word_count(len(text))
        if new_total >= threshold:
            _maybe_drop(
                lanlan_name, "word_count", cooldown,
                reset_word=True,
            )

    return None


def on_utterance(bucket: str, event: dict) -> None:
    """register_user_utterance_sink 入口。M2-j v1 仅打 debug，无实际触发。

    M2-j v2 计划：接入情感强度判定（plugin/core/state.py 的 emotion 数值），
    当 window 内累计 emotion_intensity >= 阈值时触发 emotion drop。
    """
    if not _enabled():
        return
    # 留位：未来从 event 里读 emotion / intensity 字段
    logger.debug("dropper: utterance event ignored (emotion rule not yet enabled): bucket=%s", bucket)
