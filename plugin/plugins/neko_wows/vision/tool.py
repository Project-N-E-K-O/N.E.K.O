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
SOURCE_LIVE_SHARE = "live_share"

WOWS_VISION_PROMPT = (
    "这是《战舰世界》的战斗画面。先对照随附的主事件和遥测，画面只补遥测读不到"
    "的东西：烟雾、鱼雷航迹、水花与炮口火光、自身状态图标（着火、进水、主炮/"
    "舵机损坏）。自己界面上看得见的消耗品冷却可以提，但绝不要声称敌方开了雷达、"
    "水听或其他消耗品。不要把小地图解说当成这条要说的话：不要数船、不要编方位、"
    "不要把点亮数讲成主事件。血量、距离与当前点亮数以随附文本中的遥测为准；"
    "未确认沉没数量只是花名册与最后已知记录的上限，不代表确认存活。"
    "不要从画面上估读或复述这些数字。"
    "小地图上敌舰图标亮起只表示被点亮/被发现，绝不等于对方开了雷达。"
    "消耗品实时状态当前不可用，不要提雷达是否开启。"
    "只说画面里看得见而数据里没有的东西。"
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
        live_frame_provider: Callable[[], bytes | None] | None = None,
        on_result: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self._store = store
        self._telemetry = telemetry_provider
        self._logger = logger
        self._clock = clock
        self._live_frame = live_frame_provider
        self._on_result = on_result
        self._last_capture_at: float | None = None

    def apply_config(self, cfg) -> None:
        self.cfg = cfg
        self._store.apply_retain(cfg.screenshot_retain_count)

    # ------------------------------------------------------------------
    def look(self) -> dict[str, Any]:
        if not self.cfg.screenshot_enabled:
            result = _failure(REASON_DISABLED)
        else:
            remaining = self._cooldown_remaining()
            if remaining > 0:
                # Not an error: a battle moves slowly enough that the previous
                # frame is usually still true, and saying so lets her answer from
                # what she already knows instead of stalling.
                result = _failure(
                    REASON_RATE_LIMITED,
                    retry_after_seconds=round(remaining, 1),
                    telemetry=self._safe_telemetry(),
                )
            else:
                jpeg, source, window = self._acquire()
                if jpeg is None:
                    result = _failure(
                        REASON_CAPTURE_FAILED, telemetry=self._safe_telemetry())
                else:
                    record = self._store.save(jpeg)
                    if record is None:
                        result = _failure(
                            REASON_STORE_FAILED, telemetry=self._safe_telemetry())
                    else:
                        self._last_capture_at = self._clock()
                        result = _success(
                            output={
                                "ok": True,
                                "shot_id": record.shot_id,
                                "captured_at": record.captured_at,
                                "source": source,
                                "window_title": window.title if window else "",
                                "size_bytes": record.size_bytes,
                                "telemetry": self._safe_telemetry(),
                                "recall_hint": (
                                    f"画面只在这一轮可见。之后想再看这张，用 "
                                    f"wows_recall_screenshot 传 shot_id={record.shot_id}。"
                                ),
                            },
                            jpeg=jpeg,
                        )
        self._report("look", result)
        return result

    def recall(self, shot_id: Any) -> dict[str, Any]:
        """Re-inject an earlier frame. Not rate limited: it captures nothing
        new, and the cost of re-reading a file already on disk is nil."""
        if not self.cfg.screenshot_enabled:
            result = _failure(REASON_DISABLED)
        else:
            jpeg = self._store.load(shot_id)
            if jpeg is None:
                result = _failure(
                    REASON_SHOT_EXPIRED,
                    available=[r.shot_id for r in self._store.recent(5)],
                )
            else:
                result = _success(
                    output={
                        "ok": True,
                        "shot_id": shot_id,
                        "recalled": True,
                        "telemetry": self._safe_telemetry(),
                    },
                    jpeg=jpeg,
                )
        self._report("recall", result)
        return result

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
    def _acquire(self) -> tuple[bytes | None, str, Any]:
        """Get a frame, preferring one the host already has.

        When the user is sharing their screen the host is capturing at 1Hz
        anyway, so grabbing the window ourselves would be a second capture of
        the same moment — and one that fails more often, since PrintWindow on a
        fullscreen DirectX client is unreliable in a way the share is not.

        Falling back is unconditional. A share that stopped between the check
        and the fetch, a frame that failed to decode, a host that went away:
        all of them just mean we take the screenshot ourselves, the way we did
        before the share existed.
        """
        if self._live_frame is not None:
            try:
                frame = self._live_frame()
            except Exception as exc:
                self._log("warning", f"live frame unavailable, capturing: {exc}")
                frame = None
            if frame:
                return frame, SOURCE_LIVE_SHARE, None

        window = find_game_window(self.cfg.game_dir)
        return (
            capture_jpeg(window),
            SOURCE_GAME_WINDOW if window else SOURCE_FULLSCREEN,
            window,
        )

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

    def _report(self, action: str, result: dict[str, Any]) -> None:
        output = result.get("output") if isinstance(result, dict) else None
        if not isinstance(output, dict):
            output = {}
        ok = bool(output.get("ok"))
        reason = str(output.get("reason") or "")
        if ok:
            outcome = "recalled" if action == "recall" else "ok"
            extras = []
            if output.get("shot_id"):
                extras.append(f"shot_id={output['shot_id']}")
            if output.get("source"):
                extras.append(f"source={output['source']}")
            if output.get("window_title"):
                extras.append(f"window={output['window_title']}")
            if output.get("size_bytes") is not None:
                extras.append(f"bytes={output['size_bytes']}")
            suffix = f" {' '.join(extras)}" if extras else ""
            self._log("info", f"screenshot {action} {outcome}{suffix}")
        elif reason == REASON_RATE_LIMITED:
            self._log(
                "info",
                f"screenshot {action} skipped cooldown "
                f"retry_after={output.get('retry_after_seconds')}s",
            )
        elif reason == REASON_DISABLED:
            self._log("info", f"screenshot {action} skipped {REASON_DISABLED}")
        else:
            self._log(
                "warning",
                f"screenshot {action} failed reason={reason or 'unknown'}",
            )
        callback = self._on_result
        if not callable(callback):
            return
        try:
            callback(action, output)
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
    put("allies_not_confirmed_sunk", facts.allies_not_confirmed_sunk)
    put("enemies_not_confirmed_sunk", facts.enemies_not_confirmed_sunk)
    put("confirmed_visible_allies", facts.confirmed_visible_allies)
    put("confirmed_visible_enemies", facts.confirmed_visible_enemies)
    if facts.confirmed_visible_allies is not None:
        put("team_counts_confirmed", facts.team_counts_confirmed)
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
    spoken = getattr(ship, "spoken_name", None) if ship is not None else None
    raw_name = getattr(ship, "name", "") or ""
    payload: dict[str, Any] = {
        "name": spoken or raw_name,
        "distance_m": _rounded(threat.distance_m, 0),
        "bearing_deg": _rounded(threat.bearing_deg, 0),
    }
    relative = getattr(threat, "relative_bearing_deg", None)
    if relative is not None:
        payload["relative_bearing_deg"] = _rounded(relative, 0)
    sector = getattr(threat, "relative_sector", None)
    if sector:
        payload["relative_sector"] = sector
    return payload


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
    "SOURCE_LIVE_SHARE",
    "WOWS_VISION_PROMPT",
    "ScreenshotService",
    "facts_to_telemetry",
]
