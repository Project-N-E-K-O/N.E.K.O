"""Self radio-command detector for War Thunder fixed chat messages.

Only whitelisted command semantics are emitted. Sender names and raw chat text
stay in BattleState.raw/chat and must not enter BattleEvent payloads.
"""

from __future__ import annotations

from typing import Any

from ...core.contracts import BattleEvent, BattleState
from .._base import DiscreteDetector

_POINTS = ("A", "B", "C", "D")

_SIMPLE_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cover_me", ("掩护我", "coverme")),
    ("need_help", ("需要支援", "请求支援", "需要帮助", "needhelp", "ineedhelp", "requestinghelp")),
    ("return_to_base", ("返回基地", "返航", "返回机场", "returntobase", "returningtothebase")),
    ("repairing", ("正在维修", "我在维修", "repairing", "repairingnow")),
    ("follow_me", ("跟着我", "跟随我", "followme")),
    ("thanks", ("谢谢", "感谢", "thanks", "thankyou")),
    ("affirmative", ("收到", "是", "肯定", "affirmative", "yes")),
    ("negative", ("拒绝", "不行", "否定", "negative", "no")),
    ("well_done", ("干得好", "干得漂亮", "干得不错", "welldone", "goodjob", "nicework")),
)


def _compact(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "".join(ch for ch in text if ch.isalnum())


def _chat_items(state: BattleState) -> list[dict[str, Any]]:
    return [item for item in state.chat if isinstance(item, dict)]


def _chat_id(item: dict[str, Any], fallback: int) -> int:
    try:
        return int(item.get("id"))
    except (TypeError, ValueError):
        return fallback


def _self_name(state: BattleState) -> str:
    combat = state.combat if isinstance(state.combat, dict) else {}
    self_info = combat.get("self") if isinstance(combat.get("self"), dict) else {}
    if str(self_info.get("source") or "").lower() != "manual":
        return ""
    return str(self_info.get("name") or combat.get("player_name") or "").strip()


def _sender_matches_self(item: dict[str, Any], self_name: str) -> bool:
    sender = item.get("sender")
    if sender is None:
        sender = item.get("from")
    return bool(sender is not None and _compact(sender) == _compact(self_name))


def _message_text(item: dict[str, Any]) -> str:
    for key in ("msg", "text", "message"):
        value = item.get(key)
        if value is not None:
            return str(value)
    return ""


def _point_command(normalized: str) -> tuple[str, str] | None:
    for point in _POINTS:
        p = point.lower()
        if (
            f"进攻{p}点" in normalized
            or f"攻击{p}点" in normalized
            or f"attackthe{p}point" in normalized
            or f"attack{p}point" in normalized
            or f"attackpoint{p}" in normalized
        ):
            return "attack_point", point
        if (
            f"防守{p}点" in normalized
            or f"防御{p}点" in normalized
            or f"保卫{p}点" in normalized
            or f"defendthe{p}point" in normalized
            or f"defend{p}point" in normalized
            or f"defendpoint{p}" in normalized
        ):
            return "defend_point", point
    return None


def parse_radio_command(text: str) -> dict[str, str] | None:
    normalized = _compact(text)
    if not normalized:
        return None

    point_command = _point_command(normalized)
    if point_command is not None:
        command, point = point_command
        return {"command": command, "point": point}

    for command, variants in _SIMPLE_COMMANDS:
        if normalized in variants:
            return {"command": command}
    return None


class RadioCommandDetector(DiscreteDetector):
    """Promote own fixed radio messages into safe player-command events."""

    id = "player_radio_command"

    def __init__(self) -> None:
        self._last_id: int = -1

    def reset(self) -> None:
        self._last_id = -1

    def detect(self, prev: BattleState, cur: BattleState) -> BattleEvent | None:
        if not cur.is_alive():
            self.reset()
            return None

        self_name = _self_name(cur)
        if not self_name:
            return None

        items = _chat_items(cur)
        if not items:
            return None

        ids = [_chat_id(item, index + 1) for index, item in enumerate(items)]
        max_id = max(ids, default=-1)
        if max_id < self._last_id:
            self.reset()

        newest_payload: dict[str, Any] | None = None
        newest_id = -1
        for item, item_id in zip(items, ids):
            if item_id <= self._last_id:
                continue
            if not _sender_matches_self(item, self_name):
                continue
            parsed = parse_radio_command(_message_text(item))
            if parsed is None:
                continue
            if item_id > newest_id:
                newest_payload = {
                    **parsed,
                    "domain": cur.domain,
                    "source": "self_radio",
                }
                newest_id = item_id

        self._last_id = max(self._last_id, max_id)
        if newest_payload is None:
            return None
        return BattleEvent(
            "player_radio_command",
            payload=newest_payload,
            ts=cur.timestamp or 0.0,
            level="warning",
        )
