"""配额掉落规则引擎（NEKO 本地）。

【已退役 · 统一券经济】对话掉落的「判定 + 发券」已整体迁到 NEKO-PC Electron 私有客户端的
forge-dropper（直接消费既有 WS 帧做判定，再调云端 ``POST /api/forge/credits/grant`` 发券）。
NEKO 这一侧因此不再做任何判定、不再直接调云端、不再广播 ``card_drop_available`` 自动开卡——
否则 docker 自部署（无官方客户端）会白白调用云端、规则也会暴露在公开仓。

hook：
- ``on_text_message(lanlan_name, text) -> None``：**已退役为 no-op**（仅保留 hook 注册以兼容）。
  原 word_count + keywords 判定 / ``cloud_sync`` / ``ux_state`` / 规则加载均废弃，下方保留这些
  定义仅为兼容老 import，可后续随 ``config/quota_rules.yaml`` 一并删除。
- ``on_utterance(bucket, event) -> None``：M2-j v1 留位（emotion 触发 v2 再开）。
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
    """掉落触发时把「掉了一张卡」事件 WS 广播给前端，让前端起开卡演出。

    经 main_logic.agent_event_bus 的 WS 广播器 seam 推送（app 启动时注册真正的
    _broadcast_to_all_connected），在当前 event loop 上 fire-and-forget 调度
    （与 cloud_sync.send_drop_hint 同套路，互不阻塞）；低层不 import app，避免层级倒挂。
    前端 app-websocket.js onmessage 据 type == 'card_drop_available' 分发到开卡模态。
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
    """统一的"触发掉落"路径：cooldown 检查 → 更新 state → fire-and-forget 调云端 + 推前端。"""
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
    """register_text_user_message_hook 入口。必须返回 None 不抢现有消费者。

    【已退役 · 统一券经济】对话掉落的「判定 + 发券」全部迁到 **NEKO-PC Electron 私有客户端**
    的 forge-dropper：它直接消费既有 WS 帧（``neko-assistant-turn-start`` /
    ``neko-assistant-emotion-ready`` / ``game_window_state_change``）做「随机心情 combo +
    聊满N轮 / 挂机 / 小游戏 保底」判定，并由 Electron 直接调云端
    ``POST /api/forge/credits/grant`` 发券（服务器抽稀有度 + 落库 + 扣每日发券额度；仅登录用户）。

    NEKO 这一侧因此不再做任何判定、不再直接调云端、不再广播 ``card_drop_available`` 自动开卡——
    否则 docker 自部署（无官方客户端）会白白调用云端、规则也会暴露在公开仓。本 hook 保持 no-op；
    下方 ``_maybe_drop`` / ``cloud_sync`` / ``ux_state`` / 规则加载均已废弃（保留定义仅为兼容老
    import，可后续随 ``config/quota_rules.yaml`` 一并删除）。
    """
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
