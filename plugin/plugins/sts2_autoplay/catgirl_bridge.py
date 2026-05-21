from __future__ import annotations

from hashlib import sha1
from typing import Any


class _SafeFormatDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class STS2CatgirlBridge:
    def __init__(self, i18n: Any = None, *, source_id: str = "sts2_autoplay") -> None:
        self._i18n = i18n
        self._source_id = source_id

    def t(self, key: str, *, default: str = "", **params: Any) -> str:
        if self._i18n is not None:
            return self._i18n.t(key, default=default, **params)
        if params and default:
            try:
                return default.format_map(_SafeFormatDict(params))
            except Exception:
                return default
        return default or key

    def build_sync_packet(self, snapshot: dict[str, Any], *, standby: bool = False) -> dict[str, Any]:
        classification = snapshot.get("classification") if isinstance(snapshot.get("classification"), dict) else {}
        situation_summary = snapshot.get("situation_summary") if isinstance(snapshot.get("situation_summary"), dict) else {}
        screen = snapshot.get("screen", "unknown")
        screen_class = classification.get("screen_class", "unknown")
        sync_priority = classification.get("sync_priority", "low")
        summary_kind = situation_summary.get("kind", "general")
        should_sync = self._should_sync(
            screen_class=screen_class,
            sync_priority=sync_priority,
            summary_kind=summary_kind,
            standby=standby,
        )
        reason = self._sync_reason(screen_class=screen_class, sync_priority=sync_priority, summary_kind=summary_kind, standby=standby)
        delivery = self._delivery_mode(standby=standby, sync_priority=sync_priority)
        message = self._build_message(screen=screen, summary_text=str(situation_summary.get("text") or ""))
        fingerprint = self._fingerprint(
            screen=screen,
            screen_class=screen_class,
            floor=snapshot.get("floor"),
            act=snapshot.get("act"),
            summary_kind=summary_kind,
            summary_text=str(situation_summary.get("text") or ""),
            standby=standby,
        )
        payload = {
            "source": self._source_id,
            "channel": "game_to_neko_queue",
            "screen": screen,
            "summary_kind": summary_kind,
            "trigger": "",
            "message": message,
            "strategy": {},
            "player_operation": {},
            "player": {},
            "enemies": [],
            "cards": [],
            "delivery": delivery,
            "ai_behavior": "respond" if sync_priority == "high" and not standby else "read",
        }
        companion_evaluation = snapshot.get("companion_evaluation") if isinstance(snapshot.get("companion_evaluation"), dict) else {}
        strategy_context = snapshot.get("strategy_context") if isinstance(snapshot.get("strategy_context"), dict) else {}
        payload["strategy"] = {
            "name": str(strategy_context.get("strategy_name") or companion_evaluation.get("strategy_name") or ""),
            "scene": str(strategy_context.get("scene_name") or ""),
            "event_override": bool(strategy_context.get("event_override")),
            "enemy_override": bool(strategy_context.get("enemy_override")),
        }
        if companion_evaluation:
            payload["companion_evaluation"] = companion_evaluation
            primary_message = str(companion_evaluation.get("primary_message") or "").strip()
            if primary_message:
                payload["message"] = self._host_reply_text(primary_message)
            payload["trigger"] = str(companion_evaluation.get("trigger") or "")
        player = situation_summary.get("payload", {}).get("player") if isinstance(situation_summary.get("payload"), dict) else {}
        if isinstance(player, dict):
            payload["player"] = {
                "current_hp": player.get("current_hp"),
                "max_hp": player.get("max_hp"),
                "block": player.get("block"),
            }
        enemies = situation_summary.get("payload", {}).get("enemies") if isinstance(situation_summary.get("payload"), dict) else []
        if isinstance(enemies, list):
            payload["enemies"] = [
                {
                    "name": str(enemy.get("name") or enemy.get("enemy_id") or ""),
                    "intent": str(enemy.get("intent") or enemy.get("move_id") or ""),
                }
                for enemy in enemies[:3]
                if isinstance(enemy, dict)
            ]
        cards = situation_summary.get("payload", {}).get("playable_card_summaries") if isinstance(situation_summary.get("payload"), dict) else []
        if isinstance(cards, list):
            payload["cards"] = [
                {
                    "name": str(card.get("name") or ""),
                    "cost": self._card_cost_text(card if isinstance(card, dict) else {}),
                    "effect": str((card.get("effect") if isinstance(card, dict) else "") or ""),
                }
                for card in cards[:5]
                if isinstance(card, dict)
            ]
        return {
            "should_sync": should_sync,
            "should_comment": bool(companion_evaluation.get("should_comment", should_sync)) if companion_evaluation else should_sync,
            "mode": "queue_only",
            "delivery": delivery,
            "fingerprint": fingerprint,
            "reason": reason,
            "force": sync_priority == "high" or bool(companion_evaluation.get("should_comment")) if companion_evaluation else sync_priority == "high",
            "min_interval_seconds": self._min_interval_seconds(screen_class=screen_class, sync_priority=sync_priority, standby=standby),
            "payload": payload,
        }

    def _should_sync(self, *, screen_class: str, sync_priority: str, summary_kind: str, standby: bool) -> bool:
        if standby:
            return summary_kind in {"combat", "event", "reward", "selection"} or sync_priority in {"high", "medium"}
        if screen_class in {"combat", "reward", "selection", "shop", "rest"}:
            return True
        if summary_kind in {"event", "map"}:
            return True
        if sync_priority == "high":
            return True
        return False

    def _sync_reason(self, *, screen_class: str, sync_priority: str, summary_kind: str, standby: bool) -> str:
        if standby:
            return "standby_summary"
        if sync_priority == "high":
            return "high_priority"
        if screen_class in {"combat", "reward", "selection", "shop", "rest"}:
            return f"screen_class:{screen_class}"
        return f"summary_kind:{summary_kind}"

    def _min_interval_seconds(self, *, screen_class: str, sync_priority: str, standby: bool) -> float:
        if sync_priority == "high":
            return 0.0
        if standby:
            return 8.0
        if screen_class == "combat":
            return 10.0
        if screen_class in {"reward", "selection", "shop", "rest", "run_navigation"}:
            return 20.0
        return 15.0

    def _delivery_mode(self, *, standby: bool, sync_priority: str) -> str:
        return "passive"

    def _host_reply_text(self, text: str, *, limit: int = 20) -> str:
        normalized = str(text or "").strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3].rstrip() + "..."

    def _build_message(self, *, screen: str, summary_text: str) -> str:
        screen_label = str(screen or "unknown")
        body = summary_text.strip() or self.t("sync.no_summary", default="当前暂无局势摘要。")
        short_body = body[:12].rstrip("，。； ") if len(body) > 12 else body
        return f"[{screen_label}] {self._host_reply_text(short_body, limit=20)}"

    def _card_cost_text(self, card: dict[str, Any]) -> str:
        if bool(card.get("costs_x")):
            cost_text = "X费"
        else:
            cost_text = f"{card.get('energy_cost', 0)}费"
        if bool(card.get("star_costs_x")):
            cost_text += "，耗星X"
        else:
            star_cost = card.get("star_cost")
            try:
                if star_cost is not None and int(star_cost) > 0:
                    cost_text += f"，耗星{int(star_cost)}"
            except Exception:
                pass
        return cost_text

    def _fingerprint(self, *, screen: Any, screen_class: Any, floor: Any, act: Any, summary_kind: Any, summary_text: str, standby: bool) -> str:
        normalized = " ".join(str(summary_text or "").split())
        payload = f"{screen}|{screen_class}|{floor}|{act}|{summary_kind}|{int(bool(standby))}|{normalized}"
        return sha1(payload.encode("utf-8")).hexdigest()[:16]



__all__ = ["STS2CatgirlBridge"]
