"""Assembling what the character gets when she looks at the battle.

Two channels, deliberately split by what each is good at:

* the **picture** carries what telemetry cannot see — minimap dispositions,
  smoke, torpedo wakes, fire and flooding icons, where the team actually is;
* the **text** carries the numbers, because they come from telemetry and are
  exact, whereas reading an HP bar off a JPEG is a guess.

The vision prompt says so explicitly. A model asked to look at a battle frame
will happily invent "about 40% health" from a red bar, and that invented
number would then compete with the true one sitting right beside it.
"""

from __future__ import annotations

import base64
import time
from typing import Any, Callable

from .capture import capture_jpeg
from .store import ShotStore
from .window import find_game_window

REASON_DISABLED = "disabled"
REASON_RATE_LIMITED = "rate_limited"
REASON_CAPTURE_FAILED = "capture_failed"
REASON_STORE_FAILED = "store_failed"
REASON_SHOT_EXPIRED = "shot_expired"

SOURCE_GAME_WINDOW = "game_window"
SOURCE_FULLSCREEN = "fullscreen"

WOWS_VISION_PROMPT = (
    "这是《战舰世界》的战斗画面。读图时先看小地图，再看主画面，按下面顺序描述"
    "遥测读不到的态势：\n"
    "1. 【必看】小地图：敌我舰船分布、推线/撤退方向、哪一侧空虚或被打穿、"
    "占点与舰队重心；\n"
    "2. 烟雾、鱼雷航迹、水花与炮口火光这类临时信息；\n"
    "3. 自身状态图标：着火、进水、主炮/舵机损坏、消耗品冷却；\n"
    "4. 主画面里队友的相对位置，自己是不是脱队或被包夹；\n"
    "5. 准星附近有没有可打的目标，弹着散布大概情况。\n"
    "血量、距离、存活数这些数字以随附文本中的遥测为准，不要从画面上估读，"
    "也不要复述它们。只说画面里看得见而数据里没有的东西。"
)


class ScreenshotService:
    """Rate limiting, capture orchestration, and the tool result shape."""

    def __init__(
        self,
        cfg,
        store: ShotStore,
        telemetry_provider: Callable[[], dict[str, Any]],
        *,
        logger=None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self._store = store
        self._telemetry = telemetry_provider
        self._logger = logger
        self._clock = clock
        self._last_capture_at: float | None = None

    def apply_config(self, cfg) -> None:
        self.cfg = cfg
        self._store.apply_retain(cfg.screenshot_retain_count)

    # ------------------------------------------------------------------
    def look(self) -> dict[str, Any]:
        if not self.cfg.screenshot_enabled:
            return _failure(REASON_DISABLED)

        remaining = self._cooldown_remaining()
        if remaining > 0:
            # Not an error: a battle moves slowly enough that the previous
            # frame is usually still true, and saying so lets her answer from
            # what she already knows instead of stalling.
            return _failure(
                REASON_RATE_LIMITED,
                retry_after_seconds=round(remaining, 1),
                telemetry=self._safe_telemetry(),
            )

        window = find_game_window(self.cfg.game_dir)
        jpeg = capture_jpeg(window)
        if jpeg is None:
            return _failure(REASON_CAPTURE_FAILED, telemetry=self._safe_telemetry())

        record = self._store.save(jpeg)
        if record is None:
            return _failure(REASON_STORE_FAILED, telemetry=self._safe_telemetry())

        self._last_capture_at = self._clock()
        return _success(
            output={
                "ok": True,
                "shot_id": record.shot_id,
                "captured_at": record.captured_at,
                "source": SOURCE_GAME_WINDOW if window else SOURCE_FULLSCREEN,
                "window_title": window.title if window else "",
                "telemetry": self._safe_telemetry(),
                "recall_hint": (
                    f"画面只在这一轮可见。之后想再看这张，用 "
                    f"wows_recall_screenshot 传 shot_id={record.shot_id}。"
                ),
            },
            jpeg=jpeg,
        )

    def recall(self, shot_id: Any) -> dict[str, Any]:
        """Re-inject an earlier frame. Not rate limited: it captures nothing
        new, and the cost of re-reading a file already on disk is nil."""
        if not self.cfg.screenshot_enabled:
            return _failure(REASON_DISABLED)
        jpeg = self._store.load(shot_id)
        if jpeg is None:
            return _failure(
                REASON_SHOT_EXPIRED,
                available=[r.shot_id for r in self._store.recent(5)],
            )
        return _success(
            output={
                "ok": True,
                "shot_id": shot_id,
                "recalled": True,
                "telemetry": self._safe_telemetry(),
            },
            jpeg=jpeg,
        )

    def status(self) -> dict[str, Any]:
        """Panel view of the screenshot subsystem."""
        return {
            "enabled": bool(self.cfg.screenshot_enabled),
            "min_interval_seconds": self.cfg.screenshot_min_interval_seconds,
            "retain_count": self.cfg.screenshot_retain_count,
            "cooldown_remaining_seconds": round(self._cooldown_remaining(), 1),
            "recent": [r.as_dict() for r in self._store.recent(20)],
        }

    # ------------------------------------------------------------------
    def _cooldown_remaining(self) -> float:
        interval = float(self.cfg.screenshot_min_interval_seconds or 0.0)
        if interval <= 0 or self._last_capture_at is None:
            return 0.0
        elapsed = self._clock() - self._last_capture_at
        return max(0.0, interval - elapsed)

    def _safe_telemetry(self) -> dict[str, Any]:
        """Telemetry must never be the reason a capture fails."""
        try:
            snapshot = self._telemetry()
        except Exception as exc:
            self._log("warning", f"telemetry snapshot failed: {exc}")
            return {"in_battle": False, "error": "telemetry unavailable"}
        return snapshot if isinstance(snapshot, dict) else {"in_battle": False}

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            return
        method = getattr(self._logger, level, None)
        if callable(method):
            try:
                method(message)
            except Exception:
                pass


def facts_to_telemetry(facts) -> dict[str, Any]:
    """Flatten a ``WowsFacts`` into the exact numbers worth pairing with a frame.

    Only fields the picture cannot supply reliably. Everything here is
    authoritative — the vision prompt tells the model to trust these over
    anything it thinks it reads off the screen.
    """
    if facts is None:
        return {"in_battle": False}

    telemetry: dict[str, Any] = {"in_battle": True}

    def put(key: str, value: Any) -> None:
        if value is not None:
            telemetry[key] = value

    put("own_hp_ratio", _rounded(facts.own_hp_ratio, 3))
    put("own_health", _rounded(facts.own_health, 0))
    put("own_max_health", _rounded(facts.own_max_health, 0))
    put("own_alive", facts.own_alive)
    put("own_speed_kn", _rounded(facts.own_speed, 1))
    put("own_heading_deg", _rounded(facts.own_heading_deg, 1))
    put("alive_allies", facts.alive_allies)
    put("alive_enemies", facts.alive_enemies)
    # Spotted now — not the same as alive. 0 means nobody lit up, not a wipe.
    put("visible_enemies", facts.visible_enemies)
    put("nearest_ally_distance_m", _rounded(facts.nearest_ally_distance_m, 0))
    put("distance_to_boundary_m", _rounded(facts.distance_to_boundary_m, 0))
    put("own_broadside_angle_deg", _rounded(facts.own_broadside_angle_deg, 1))
    put("damage_inflicted", _rounded(facts.damage_inflicted, 0))
    put("ammo_type", facts.ammo_type)

    if facts.nearest_enemy is not None:
        telemetry["nearest_enemy"] = _bearing(facts.nearest_enemy)
    if facts.threats_in_scan_range:
        telemetry["threats"] = [_bearing(t) for t in facts.threats_in_scan_range[:6]]
    return telemetry


def _bearing(threat) -> dict[str, Any]:
    ship = getattr(threat, "ship", None)
    return {
        "name": getattr(ship, "name", "") or "",
        "distance_m": _rounded(threat.distance_m, 0),
        "bearing_deg": _rounded(threat.bearing_deg, 0),
    }


def _rounded(value, digits: int):
    if value is None:
        return None
    try:
        result = round(float(value), digits)
    except (TypeError, ValueError):
        return None
    return int(result) if digits == 0 else result


def _failure(reason: str, **extra: Any) -> dict[str, Any]:
    output: dict[str, Any] = {"ok": False, "reason": reason}
    output.update(extra)
    return {"output": output, "is_error": False}


def _success(*, output: dict[str, Any], jpeg: bytes) -> dict[str, Any]:
    return {
        "output": output,
        "is_error": False,
        "images": [{
            "data_b64": base64.b64encode(jpeg).decode("ascii"),
            "mime": "image/jpeg",
            "vision_prompt": WOWS_VISION_PROMPT,
        }],
    }


__all__ = [
    "REASON_CAPTURE_FAILED",
    "REASON_DISABLED",
    "REASON_RATE_LIMITED",
    "REASON_SHOT_EXPIRED",
    "REASON_STORE_FAILED",
    "SOURCE_FULLSCREEN",
    "SOURCE_GAME_WINDOW",
    "WOWS_VISION_PROMPT",
    "ScreenshotService",
    "facts_to_telemetry",
]
