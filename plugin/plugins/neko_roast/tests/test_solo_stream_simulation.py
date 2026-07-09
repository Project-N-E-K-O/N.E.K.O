from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pytest

from plugin.plugins.neko_roast.core.contracts import ViewerEvent, ViewerIdentity
from plugin.plugins.neko_roast.core.runtime import RoastRuntime


class ConfigApi:
    async def dump(self, timeout: float = 0) -> dict:
        return {"neko_roast": {}}

    async def update(self, payload: dict) -> None:
        return None

    async def profile_ensure_active(self, _profile: str, payload: dict, timeout: float = 0) -> None:
        return None


class Plugin:
    def __init__(self, tmp_path: Path) -> None:
        self.config = ConfigApi()
        self.ctx = None
        self.logger = None
        self._data_path = tmp_path
        self.pushed_messages: list[dict] = []
        self.output_channel_ready = True

    def data_path(self) -> Path:
        return self._data_path

    def push_message(self, **kwargs: Any) -> None:
        self.pushed_messages.append(kwargs)


class FakeIngest:
    def __init__(self) -> None:
        self.room_id = 1817654314
        self.viewer_count = 23

    def is_listening(self) -> bool:
        return self.room_id > 0

    def listener_state(self) -> dict:
        return {
            "state": "connected",
            "connected": True,
            "room_id": self.room_id,
            "viewer_count": self.viewer_count,
        }

    async def start_listening(self, room_id: int) -> bool:
        self.room_id = room_id
        return True

    async def stop_listening(self) -> None:
        self.room_id = 0

    def normalize(self, payload: dict) -> ViewerEvent:
        return ViewerEvent(
            uid=str(payload.get("uid") or ""),
            nickname=str(payload.get("nickname") or ""),
            avatar_url=str(payload.get("avatar_url") or ""),
            danmaku_text=str(payload.get("danmaku_text") or ""),
            source="live_danmaku",
            live_mode="solo_stream",
            seen_at=str(payload.get("seen_at") or ""),
            raw=dict(payload),
        )


class FakeDispatcher:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.outputs: list[tuple[str, str]] = []

    def output_channel_status(self) -> dict:
        return {"ready": True, "reason": "", "detail": "fake-memory-dispatcher"}

    async def push_roast(self, request: Any) -> str:
        route = str(request.event.source or "")
        if str(request.metadata.get("support_event_type") or ""):
            route = "live_support_events"
        if route == "live_danmaku":
            route = "avatar_roast" if request.allow_avatar_image else "danmaku_response"
        self.counts[route] += 1
        output = self._line_for(route, request, self.counts[route])
        self.outputs.append((route, output))
        return output

    @staticmethod
    def _line_for(route: str, request: Any, count: int) -> str:
        text = str(request.event.danmaku_text or "").strip().casefold()
        pools = {
            "avatar_roast": (
                "new paw gets the box stamp",
                "that name peeked in softly",
                "room brightens one notch",
                "neko marks this fresh paw",
                "you entered with bell energy",
                "take seat by the cat nest",
                "first step, soft landing",
                "cat radar noticed you",
                "new face, tiny spotlight",
                "welcome to warm corner",
            ),
            "warmup_hosting": (
                "neko softens the room light",
                "cat nest is open now",
            ),
            "idle_hosting": (
                "neko listens to one sec air",
                "screen corner hides snack",
                "neko stamps the quiet air",
                "this second wants a blink",
                "cat nest temp feels right",
                "the lamp gets a softer name",
                "neko guards the little pause",
                "the desk is behaving tonight",
            ),
            "active_engagement": (
                "pick rain or lamp for nest",
                "give neko a three sec pose",
                "desk vote: cup or keyboard",
                "name this second soft lamp",
                "tag cat nest with one word",
                "window glow or table glow",
            ),
            "danmaku_response": (
                "neko caught that line",
                "that laugh twitched one ear",
                "neko nods once",
                "that line warmed the nest",
                "neko is here, tail online",
                "neko gently passes",
                "that six landed lightly",
                "cat paws leave that knot",
                "neko sees a wire ball",
                "you chat, neko listens",
            ),
            "live_support_events": (
                "neko tucks that support in",
                "support caught, tiny bow",
                "neko thanks that bright ping",
            ),
        }
        if route == "danmaku_response":
            if "?" in text or "what" in text or "still here" in text:
                pool = (
                    "neko is here, tail online",
                    "feels like a tiny lamp",
                    "neko votes quiet option",
                    "stream feels like soft radio",
                )
            elif text in {"6", "lol", "haha"}:
                pool = (
                    "that laugh twitched one ear",
                    "that six landed lightly",
                    "now the cat nest has sound",
                )
            elif "nuclear" in text or "terraria" in text or "guide" in text or "computer" in text:
                pool = (
                    "cat paws leave that knot",
                    "neko sees a wire ball",
                    "that topic can sit far away",
                )
            elif "@" in text:
                pool = (
                    "you chat, neko listens",
                    "neko will not steal chat",
                )
            else:
                pool = pools["danmaku_response"]
            return pool[(count - 1) % len(pool)]
        pool = pools.get(route, ("signal-only",))
        return pool[(count - 1) % len(pool)]


def _sim_age(clock: dict[str, float], value: Any) -> float | None:
    text = str(value or "")
    if not text.startswith("sim:"):
        return None
    return max(0.0, clock["t"] - float(text.split(":", 1)[1]))


async def _build_sim_runtime(tmp_path: Path) -> RoastRuntime:
    runtime = RoastRuntime(Plugin(tmp_path))
    runtime.bili_live_ingest = FakeIngest()
    runtime.dispatcher = FakeDispatcher()

    async def fake_resolve(event: ViewerEvent) -> ViewerIdentity:
        has_avatar = event.uid in {"u01", "u03", "u05", "u06", "u09", "u10", "u11", "u13"}
        return ViewerIdentity(
            uid=event.uid,
            nickname=event.nickname or event.uid,
            name=event.nickname or event.uid,
            avatar_url=f"https://example.invalid/{event.uid}.png" if has_avatar else "",
            avatar_bytes=b"fake-avatar" if has_avatar else None,
            avatar_mime="image/png" if has_avatar else "",
            fetched=True,
        )

    async def fake_topics(limit: int = 6) -> dict:
        return {
            "success": True,
            "videos": [
                {"title": "cat nest desk object choice", "bvid": "BV_ROOM_OBJECT"},
                {"title": "three-letter code for a quiet room", "bvid": "BV_ROOM_CODE"},
                {"title": "screen corner hides a tiny snack", "bvid": "BV_SCREEN_SNACK"},
                {"title": "neko three second steady pose", "bvid": "BV_POSE"},
            ],
        }

    runtime.bili_identity.resolve = fake_resolve
    runtime._active_engagement_topic_fetcher = fake_topics
    for module in (
        runtime.bili_live_ingest,
        runtime.bili_identity,
        runtime.viewer_profile,
        runtime.avatar_roast,
        runtime.danmaku_response,
        runtime.active_engagement,
        runtime.warmup_hosting,
        runtime.live_events,
    ):
        if hasattr(module, "ctx"):
            module.ctx = runtime

    runtime.config.live_room_id = 1817654314
    runtime.config.live_enabled = True
    runtime.config.live_mode = "solo_stream"
    runtime.config.dry_run = False
    runtime.config.activity_level = "standard"
    runtime.config.rate_limit_seconds = 0
    runtime.safety_guard.set_connected(True)
    runtime.safety_guard.resume()
    runtime._live_listener_started_at = 0.0
    return runtime


async def _run_solo_stream_simulation(runtime: RoastRuntime) -> None:
    clock = {"t": 0.0}
    runtime._live_state_now = lambda: clock["t"]
    runtime._idle_hosting_now = lambda: clock["t"]
    runtime._active_engagement_now = lambda: clock["t"]
    runtime.pipeline._now = lambda: clock["t"]
    runtime._iso_age_sec = lambda value: _sim_age(clock, value)

    def stamp_latest(t: float) -> None:
        if not runtime.recent_results:
            return
        runtime.recent_results[-1]["created_at"] = f"sim:{float(t)}"
        event = runtime.recent_results[-1].get("event") or {}
        if isinstance(event, dict):
            event["seen_at"] = f"sim:{float(t)}"

    async def send(t: float, uid: str, nickname: str, text: str, event_type: str = "danmaku") -> None:
        clock["t"] = float(t)
        await runtime.handle_live_payload(
            {
                "uid": uid,
                "nickname": nickname,
                "danmaku_text": text,
                "event_type": event_type,
                "seen_at": f"sim:{float(t)}",
            }
        )
        stamp_latest(t)

    async def host(t: float, kind: str) -> None:
        clock["t"] = float(t)
        before = len(runtime.recent_results)
        if kind == "warmup":
            await runtime.maybe_trigger_warmup_hosting()
        elif kind == "idle":
            await runtime.maybe_trigger_idle_hosting()
        else:
            await runtime.maybe_trigger_active_engagement()
        if len(runtime.recent_results) > before:
            stamp_latest(t)

    schedule = [
        (5, "host", "warmup"),
        (20, "msg", "u01", "viewer01", "first visit, neko is quiet"),
        (42, "msg", "u02", "viewer02", "lol"),
        (70, "msg", "u01", "viewer01", "continue that"),
        (105, "msg", "u03", "viewer03", "neko what does this stream feel like?"),
        (145, "msg", "u04", "viewer04", "@viewer02 look at this"),
        (190, "msg", "u05", "viewer05", "@neko today feels like a tiny radio"),
        (280, "host", "active"),
        (390, "host", "idle"),
        (500, "host", "idle"),
        (620, "host", "active"),
        (700, "msg", "u02", "viewer02", "6"),
        (760, "msg", "u06", "risk", "nuclear plant guide"),
        (830, "msg", "u06", "risk", "build a computer in Terraria"),
        (900, "msg", "u07", "gift", "sent a flower", "gift"),
        (960, "msg", "u08", "sc", "SC: neko keep going", "super_chat"),
        (1100, "host", "idle"),
        (1210, "host", "idle"),
        (1330, "host", "active"),
        (1450, "msg", "u01", "viewer01", "neko are you still here"),
        (1510, "msg", "u09", "viewer09", "first time, touching cat paw"),
        (1600, "msg", "u10", "viewer10", "first time, queueing for cat"),
        (1740, "host", "idle"),
        (1860, "host", "active"),
        (1980, "host", "idle"),
        (2100, "msg", "u11", "viewer11", "that last line was cute"),
        (2160, "msg", "u12", "viewer12", "nice"),
        (2280, "host", "active"),
        (2400, "host", "idle"),
        (2520, "msg", "u13", "viewer13", "first time, is the nest still open?"),
        (2640, "host", "idle"),
        (2700, "host", "active"),
    ]
    for item in schedule:
        if item[1] == "host":
            await host(float(item[0]), str(item[2]))
        else:
            await send(
                float(item[0]),
                str(item[2]),
                str(item[3]),
                str(item[4]),
                str(item[5]) if len(item) > 5 else "danmaku",
            )


@pytest.mark.asyncio
async def test_solo_stream_realistic_simulation_keeps_routes_and_quality_stable(tmp_path: Path) -> None:
    runtime = await _build_sim_runtime(tmp_path)

    await _run_solo_stream_simulation(runtime)

    rows = list(runtime.recent_results)
    routes = Counter(str(row.get("response_module") or "-") for row in rows)
    statuses = Counter(str(row.get("status") or "-") for row in rows)
    live_by_uid: dict[str, list[str]] = defaultdict(list)
    outputs: list[str] = []
    for row in rows:
        event = row.get("event") if isinstance(row.get("event"), dict) else {}
        if event.get("source") == "live_danmaku":
            live_by_uid[str(event.get("uid") or "")].append(str(row.get("response_module") or ""))
        if row.get("status") == "pushed":
            outputs.append(str(row.get("output") or ""))

    repeated_avatar = [
        (uid, route_list)
        for uid, route_list in live_by_uid.items()
        if route_list.count("avatar_roast") > 1
    ]
    forbidden = (
        "public shaming",
        "labor camp",
        "punishment",
        "trial",
        "公开示众",
        "劳改",
        "劳动改造",
        "审判",
        "处刑",
        "惩罚",
    )
    generic_host_bait = (
        "anyone here",
        "send danmaku",
        "what do you want",
        "有人吗",
        "发弹幕",
        "想听什么",
    )

    assert statuses == {"pushed": 17}
    assert routes["warmup_hosting"] == 0
    assert routes["avatar_roast"] == 9
    assert routes["danmaku_response"] == 6
    assert routes["idle_hosting"] == 0
    assert routes["active_engagement"] == 0
    assert routes["live_support_events"] == 2
    assert repeated_avatar == []
    assert live_by_uid["u01"] == ["avatar_roast", "danmaku_response", "danmaku_response"]
    assert live_by_uid["u06"] == ["avatar_roast", "danmaku_response"]
    assert all(len(output) <= 28 for output in outputs)
    assert not any(token in output for output in outputs for token in forbidden)
    assert not any(token in output.casefold() for output in outputs for token in generic_host_bait)
    assert len(outputs) - len(set(outputs)) <= 3
    assert runtime.plugin.pushed_messages == []
    assert len(runtime.dispatcher.outputs) == 17


@pytest.mark.asyncio
async def test_solo_stream_simulation_dashboard_readiness_uses_profile_count(tmp_path: Path) -> None:
    runtime = await _build_sim_runtime(tmp_path)

    await _run_solo_stream_simulation(runtime)
    dashboard = await runtime.dashboard_state()

    profiles = dashboard["recent_profiles"]
    readiness = dashboard["solo_test_readiness"]
    items = {item["id"]: item for item in readiness["items"]}

    assert len(profiles) == 13
    assert readiness["summary"] == "ready_for_live_test"
    assert readiness["profile_count"] == len(profiles)
    assert items["test_isolation"]["status"] == "warning"
    assert items["test_isolation"]["reason"] == "viewer_profiles_present"
