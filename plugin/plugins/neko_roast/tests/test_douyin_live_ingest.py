from __future__ import annotations

import asyncio
import json
import re
import urllib.error
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

import pytest

from plugin.plugins.neko_roast.core.contracts import InteractionResult, PipelineStep, RoastConfig, ViewerEvent
from plugin.plugins.neko_roast.core.event_bus import EventBus
from plugin.plugins.neko_roast.core.runtime import RoastRuntime
from plugin.plugins.neko_roast.modules.douyin_identity import DouyinIdentityModule
from plugin.plugins.neko_roast.modules.douyin_live_ingest import DouyinLiveIngestModule
from plugin.plugins.neko_roast.modules.douyin_live_ingest.bridge_adapter import DouyinLiveBridgeAdapter
from plugin.plugins.neko_roast.modules.douyin_live_ingest.event_model import (
    DouyinLiveProviderEvent,
    is_routable_event_type,
    is_status_only_event_type,
    safe_payload,
    to_live_event,
    to_provider_event,
)
from plugin.plugins.neko_roast.modules.douyin_live_ingest.public_projection import (
    is_public_hostname,
    safe_public_bool,
    safe_public_float,
    safe_public_int,
)
from plugin.plugins.neko_roast.modules.douyin_live_ingest.retry_policy import (
    DouyinReconnectPolicy,
    DouyinReconnectState,
)
from plugin.plugins.neko_roast.modules.douyin_live_ingest.room_ref import DouyinRoomRef, parse_douyin_room_ref
from plugin.plugins.neko_roast.modules.douyin_live_ingest.transport_event import (
    DouyinTransportEvent,
    DouyinTransportStartRequest,
    DouyinTransportState,
)
from plugin.plugins.neko_roast.modules.douyin_live_ingest.webcast import (
    DouyinWebcastInfo,
    fetch_webcast_info,
    parse_webcast_info,
    room_page_url,
)
from plugin.plugins.neko_roast.modules.live_bridge.process_supervisor import BridgeProcessState
from plugin.plugins.neko_roast.modules.live_events import LiveEventsModule
from plugin.plugins.neko_roast.modules.live_support_events import LiveSupportEventsModule


class _ConfigApi:
    async def dump(self, timeout: float = 0) -> dict:
        return {"neko_roast": {}}


class _Plugin:
    def __init__(self, data_dir: Path) -> None:
        self.config = _ConfigApi()
        self.ctx = None
        self.logger = None
        self._data_dir = data_dir
        self.pushed_messages: list[dict] = []
        self.output_channel_ready = True

    def data_path(self) -> Path:
        return self._data_dir

    def push_message(self, **kwargs):
        self.pushed_messages.append(kwargs)
        return None


class _Audit:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, op, message="", level="info", detail=None) -> None:
        self.records.append({"op": op, "message": message, "level": level, "detail": detail or {}})


class _Safety:
    def output_cooldown_remaining(self, now=None) -> float:
        return 0.0


class _LiveEventsCtx:
    def __init__(self) -> None:
        self.audit = _Audit()
        self.event_bus = EventBus(self.audit)
        self.safety_guard = _Safety()
        self.config = RoastConfig(rate_limit_seconds=0)
        self.payloads: list[dict] = []

    async def handle_live_payload(self, payload: dict):
        self.payloads.append(payload)
        return None


async def _drain(hub: LiveEventsModule) -> None:
    for task in list(hub._tasks):
        if not task.done():
            await task


async def _drain_support(module: LiveSupportEventsModule) -> None:
    for task in list(module._tasks):
        if not task.done():
            await task


class _MissingBridgeSupervisor:
    async def start(self):
        return BridgeProcessState(ok=False, last_error="bundled bridge executable is missing")

    async def stop(self):
        return BridgeProcessState()


class _BytesResponse:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self.data


def test_douyin_transport_decision_points_are_documented():
    root = Path(__file__).resolve().parents[1]
    source = (root / "docs" / "live-center-roadmap.md").read_text(encoding="utf-8")

    assert "抖音 bridge-only Decision Points" in source
    assert "内置 MIT `douyinLive` 本地进程 + localhost WS" in source
    assert "v1 不做 `webcast/im/fetch`、protobuf、ack、heartbeat、JS signature" in source
    assert "生命周期" in source
    assert "事件范围" in source
    assert "守门规则" in source


def test_douyin_ingest_module_documentation_tracks_safety_boundary():
    root = Path(__file__).resolve().parents[1]
    source = (root / "docs" / "modules" / "douyin_live_ingest.md").read_text(encoding="utf-8")

    assert "public_projection" in source
    assert "Direct Douyin WebSocket/protobuf/ack/heartbeat transport is intentionally not kept" in source
    assert "Router-facing configured room references are public projections too" in source
    assert "`DouyinRoomRef.to_dict()` is also a public projection" in source
    assert "Gift events publish only safe gift summary fields and stay signal-only" in source
    assert "cookie, authorization header, token, signature" in source
    assert "Events without a safe room target are dropped before EventBus publish" in source
    assert "`normalize()` must derive `ViewerEvent` fields only from `safe_payload()` output" in source
    assert "`to_live_event()` must re-sanitize direct `DouyinLiveProviderEvent` fields" in source
    assert "Numeric `room_id` / `webcast_room_id`" in source
    assert "Room-page metadata fetches must use a bounded scalar timeout" in source
    assert "metadata parsing must not stringify object inputs" in source
    assert "string-only validation and CR/LF rejection" in source
    assert "Unknown event types normalize to `unknown`" in source
    assert "Allowed public text fields are string-only" in source
    assert "bridge connection plan" in source
    assert "public-host only" in source
    assert "External bridge wrapper" in source
    assert "`status()` and `listener_state()` are public projections" in source
    assert "retry policy" in source
    assert "Public `state` must be a string known lifecycle label" in source
    assert "Numeric public fields must be finite and non-negative" in source
    assert "Boolean public fields" in source
    assert "truthy string or object must not be treated as an exhausted retry state" in source
    assert "without params, query, fragment, username, or password" in source
    assert "Douyin_Spider" in source


def test_douyin_development_documentation_tracks_event_safety_boundary():
    root = Path(__file__).resolve().parents[1]
    source = (root / "docs" / "development.md").read_text(encoding="utf-8")

    assert "Provider-neutral update" in source
    assert "modules/live_events/provider_event.py" in source
    assert "already-sanitized dict events are both accepted" in source
    assert "room-topic prompt examples must use those helpers" in source
    assert "metadata fetch" in source
    assert "`Cookie`" in source
    assert "`urlopen`" in source
    assert "`runtime_config.update_config()`" in source
    assert "Douyin `live_room_ref`" in source
    assert "`live_status_summary()`" in source
    assert "`room_ref` / `room_id`" in source
    assert "`live_connection_snapshot()`" in source
    assert "EventBus" in source
    assert "fetch timeout" in source
    assert "`room_id` / `webcast_room_id`" in source
    assert "`unknown`" in source
    assert "bridge connection plan" in source
    assert "bridge URL" in source
    assert "`status()` / `listener_state()`" in source
    assert "retry policy" in source
    assert "`state`" in source
    assert "retry policy" in source
    assert "params/query/fragment" in source
    assert "`source_url` / `name` / `nickname` / `avatar_url`" in source


def test_douyin_ingest_has_no_unapproved_transport_runtime_imports():
    root = Path(__file__).resolve().parents[1]
    source_dir = root / "modules" / "douyin_live_ingest"
    forbidden_imports = {
        "websocket",
        "google.protobuf",
        "betterproto",
        "execjs",
        "quickjs",
        "js2py",
        "selenium",
        "playwright",
        "pyppeteer",
        "requests_html",
        "undetected_chromedriver",
    }
    offenders: list[str] = []

    for path in sorted(source_dir.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^\s*(?:from|import)\s+([a-zA-Z0-9_.]+)", source, flags=re.MULTILINE):
            imported = match.group(1)
            if any(imported == token or imported.startswith(f"{token}.") for token in forbidden_imports):
                offenders.append(f"{path.relative_to(root)}:{imported}")

    assert offenders == []


def test_douyin_ingest_has_no_unapproved_dynamic_execution_or_background_tasks():
    root = Path(__file__).resolve().parents[1]
    source_dir = root / "modules" / "douyin_live_ingest"
    forbidden_tokens = {
        "importlib.import_module",
        "__import__(",
        "subprocess",
        "os.system",
        "popen(",
        "eval(",
        "exec(",
        "asyncio.create_task",
        "ensure_future",
        "node",
        "npm",
    }
    offenders: list[str] = []

    for path in sorted(source_dir.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        hits = sorted(token for token in forbidden_tokens if token in source)
        if hits:
            offenders.append(f"{path.relative_to(root)}:{','.join(hits)}")

    assert offenders == []


def test_douyin_ingest_has_no_vendored_transport_artifacts():
    root = Path(__file__).resolve().parents[1]
    source_dir = root / "modules" / "douyin_live_ingest"
    forbidden_suffixes = {".js", ".mjs", ".cjs", ".ts", ".proto", ".pb", ".wasm"}
    offenders: list[str] = []

    for path in sorted(source_dir.rglob("*")):
        if "__pycache__" in path.parts or not path.is_file():
            continue
        relative = path.relative_to(root)
        lowered_name = path.name.lower()
        if path.suffix.lower() in forbidden_suffixes:
            offenders.append(str(relative))
        if "douyin_spider" in lowered_name:
            offenders.append(str(relative))

    assert offenders == []


def test_douyin_identity_v1_does_not_fetch_profiles_or_avatars():
    root = Path(__file__).resolve().parents[1]
    source = (root / "modules" / "douyin_identity" / "__init__.py").read_text(encoding="utf-8")
    forbidden_tokens = {
        "urllib",
        "requests",
        "httpx",
        "aiohttp",
        "socket",
        "getaddrinfo",
        "avatar_cache",
        "avatar_bytes",
        "avatar_mime",
        "credential",
        "cookie",
    }

    assert [token for token in sorted(forbidden_tokens) if token in source] == []


def test_parse_douyin_room_ref_accepts_url_and_token():
    parsed_url = parse_douyin_room_ref("https://live.douyin.com/123456?from=copy")
    parsed_host = parse_douyin_room_ref("live.douyin.com/abc_def")
    parsed_token = parse_douyin_room_ref("room-42")

    assert parsed_url.ok is True
    assert parsed_url.room_ref == "123456"
    assert parsed_url.source == "url"
    assert parsed_host.room_ref == "abc_def"
    assert parsed_token.room_ref == "room-42"
    assert parsed_token.source == "token"


def test_parse_douyin_room_ref_rejects_unsupported_or_private_shapes():
    assert parse_douyin_room_ref("").ok is False
    assert parse_douyin_room_ref("https://example.com/123").message == "unsupported douyin room url"
    assert parse_douyin_room_ref("room?with=query").message == "invalid douyin room_ref"


def test_parse_douyin_room_ref_rejects_non_scalar_shapes():
    class _LooksLikeRoom:
        def __str__(self) -> str:
            return "room-42"

    assert parse_douyin_room_ref(42).room_ref == "42"
    assert parse_douyin_room_ref({"room_ref": "room-42"}).ok is False
    assert parse_douyin_room_ref(b"room-42").ok is False
    assert parse_douyin_room_ref(_LooksLikeRoom()).ok is False


def test_douyin_room_ref_to_dict_re_sanitizes_direct_fields():
    class _Truthy:
        def __bool__(self) -> bool:
            return True

    room_ref = DouyinRoomRef(
        ok=_Truthy(),  # type: ignore[arg-type]
        room_ref="room-42?cookie=must-not-leak",
        source="URL",
        message="failed Authorization: Bearer must-not-leak; token=must-not-leak " + ("x" * 200),
    )
    public = room_ref.to_dict()

    assert public["ok"] is False
    assert public["room_ref"] == ""
    assert public["source"] == "url"
    assert public["message"].startswith("failed [redacted]; [redacted] ")
    assert len(public["message"]) == 160
    assert "must-not-leak" not in json.dumps(public, ensure_ascii=False)


def test_douyin_normalize_prefixes_uid_and_drops_private_fields():
    module = DouyinLiveIngestModule()
    module.ctx = _LiveEventsCtx()

    event = module.normalize(
        {
            "uid": "123",
            "nickname": "viewer",
            "text": "hello",
            "avatar_url": "https://example.test/a.png",
            "cookie": "must-not-leak",
            "webcast_sign": "must-not-leak",
        }
    )

    assert event.uid == "douyin:123"
    assert event.nickname == "viewer"
    assert event.danmaku_text == "hello"
    assert event.raw == {
        "uid": "123",
        "nickname": "viewer",
        "text": "hello",
        "avatar_url": "https://example.test/a.png",
    }


def test_douyin_normalize_sanitizes_room_ref_before_viewer_event_raw():
    module = DouyinLiveIngestModule()
    module.ctx = _LiveEventsCtx()

    event = module.normalize(
        {
            "uid": "123",
            "text": "hello",
            "room_ref": "room-42?cookie=must-not-leak",
        }
    )

    assert event.raw["room_ref"] == ""
    assert "must-not-leak" not in json.dumps(event.raw, ensure_ascii=False)


def test_douyin_normalize_drops_unsafe_uid_before_viewer_event():
    module = DouyinLiveIngestModule()
    module.ctx = _LiveEventsCtx()

    event = module.normalize(
        {
            "uid": "cookie=must-not-leak",
            "text": "hello",
        }
    )

    assert event.uid == ""
    assert event.raw["uid"] == ""
    assert "must-not-leak" not in json.dumps(event.raw, ensure_ascii=False)
    assert "must-not-leak" not in event.uid


@pytest.mark.parametrize(
    "uid",
    [
        "authorization",
        "cookie",
        "odin_tt",
        "sessionid",
        "sessionid_ss",
        "sid_tt",
        "sign",
        "signature",
        "token",
        "ttwid",
        "uid_tt",
        "douyin:webcast_sign",
        "x-tt-token",
    ],
)
def test_douyin_normalize_drops_credential_marker_uid(uid: str) -> None:
    module = DouyinLiveIngestModule()
    module.ctx = _LiveEventsCtx()

    event = module.normalize({"uid": uid, "text": "hello"})

    assert event.uid == ""
    assert event.raw["uid"] == ""


@pytest.mark.parametrize(
    ("uid", "expected"),
    [
        ("signature-viewer", "douyin:signature-viewer"),
        ("viewer-token-42", "douyin:viewer-token-42"),
        ("douyin:cookie-monster", "douyin:cookie-monster"),
    ],
)
def test_douyin_normalize_keeps_uid_containing_credential_word(uid: str, expected: str) -> None:
    module = DouyinLiveIngestModule()
    module.ctx = _LiveEventsCtx()

    event = module.normalize({"uid": uid, "text": "hello"})

    assert event.uid == expected
    assert event.raw["uid"] == expected.removeprefix("douyin:")


def test_douyin_normalize_drops_object_uid_before_viewer_event():
    class _LooksLikeUid:
        def __str__(self) -> str:
            return "123"

    module = DouyinLiveIngestModule()
    module.ctx = _LiveEventsCtx()

    event = module.normalize({"uid": _LooksLikeUid(), "text": "hello"})

    assert event.uid == ""
    assert event.raw["uid"] == ""


@pytest.mark.parametrize(
    "avatar_url",
    [
        "data:image/png;base64,must-not-leak",
        "javascript:alert(1)",
        "http://localhost/avatar.png",
        "http://127.0.0.1/avatar.png",
        "http://192.168.1.2/avatar.png",
        "https://user:pass@example.test/avatar.png",
    ],
)
def test_douyin_normalize_drops_unsafe_avatar_url(avatar_url: str) -> None:
    module = DouyinLiveIngestModule()
    module.ctx = _LiveEventsCtx()

    event = module.normalize(
        {
            "uid": "123",
            "text": "hello",
            "avatar_url": avatar_url,
        }
    )

    assert event.avatar_url == ""
    assert event.raw["avatar_url"] == ""
    assert "must-not-leak" not in json.dumps(event.raw, ensure_ascii=False)


def test_douyin_normalize_strips_avatar_url_query_and_fragment() -> None:
    module = DouyinLiveIngestModule()
    module.ctx = _LiveEventsCtx()

    event = module.normalize(
        {
            "uid": "123",
            "text": "hello",
            "avatar_url": "https://example.test/avatar.png;param?token=must-not-leak#signature=must-not-leak",
        }
    )

    assert event.avatar_url == "https://example.test/avatar.png"
    assert event.raw["avatar_url"] == "https://example.test/avatar.png"
    assert "must-not-leak" not in json.dumps(event.raw, ensure_ascii=False)


def test_douyin_normalize_drops_object_avatar_url() -> None:
    class _LooksLikeAvatarUrl:
        def __str__(self) -> str:
            return "https://example.test/avatar.png"

    module = DouyinLiveIngestModule()
    module.ctx = _LiveEventsCtx()

    event = module.normalize(
        {
            "uid": "123",
            "text": "hello",
            "avatar_url": _LooksLikeAvatarUrl(),
        }
    )

    assert event.avatar_url == ""
    assert event.raw["avatar_url"] == ""


def test_douyin_normalize_drops_object_text_fields_before_viewer_event() -> None:
    class _LooksLikeText:
        def __str__(self) -> str:
            return "token=must-not-leak"

    module = DouyinLiveIngestModule()
    module.ctx = _LiveEventsCtx()

    event = module.normalize(
        {
            "uid": "123",
            "nickname": _LooksLikeText(),
            "text": _LooksLikeText(),
            "danmaku_text": _LooksLikeText(),
            "target_lanlan": _LooksLikeText(),
            "lanlan_name": _LooksLikeText(),
        }
    )

    assert event.uid == "douyin:123"
    assert event.nickname == ""
    assert event.danmaku_text == ""
    assert event.target_lanlan == ""
    assert event.raw == {
        "uid": "123",
        "nickname": "",
        "text": "",
        "danmaku_text": "",
        "target_lanlan": "",
        "lanlan_name": "",
    }
    assert "must-not-leak" not in json.dumps(event.to_dict(), ensure_ascii=False)


def test_douyin_normalize_rejects_non_dict_payload_before_viewer_event() -> None:
    class _IterablePayload:
        def __iter__(self):
            yield ("uid", "42")
            yield ("text", "hello")

    module = DouyinLiveIngestModule()
    module.ctx = _LiveEventsCtx()

    for payload in ([("uid", "42")], _IterablePayload()):
        event = module.normalize(payload)

        assert event.uid == ""
        assert event.nickname == ""
        assert event.danmaku_text == ""
        assert event.target_lanlan == ""
        assert event.raw == {}


def test_douyin_event_model_normalizes_aliases_and_gift_fields():
    event = to_provider_event(
        {
            "event_type": "chat",
            "uid": "123",
            "nickname": "viewer",
            "text": "hello",
            "giftName": "small heart",
            "num": "2",
            "total_coin": "600",
            "room_id": "7390000000000000000",
            "room_ref": "room-42",
            "cookie": "must-not-leak",
        },
        room_ref="fallback-room",
    )

    assert event.event_type == "danmaku"
    assert event.uid == "douyin:123"
    assert event.text == "hello"
    assert event.room_ref == "room-42"
    assert event.room_id == 7390000000000000000
    assert event.gift_name == "small heart"
    assert event.gift_count == 2
    assert event.gift_value == 600
    assert safe_payload({"signature": "secret", "protobuf": b"raw"}) == {}


def test_douyin_event_model_rejects_non_dict_payloads_without_stringifying():
    class _IterablePayload:
        def __iter__(self):
            yield ("event_type", "chat")
            yield ("uid", "42")
            yield ("text", "hello")
            yield ("room_ref", "room-42")

    assert safe_payload([("event_type", "chat"), ("uid", "42")]) == {}
    assert safe_payload(_IterablePayload()) == {}

    event = to_provider_event(_IterablePayload(), room_ref="room-42")
    live_event = to_live_event(event)

    assert event.event_type == "unknown"
    assert live_event.type == "unknown"
    assert live_event.payload["room_ref"] == "room-42"
    assert is_routable_event_type(live_event.type) is False


def test_douyin_event_model_requires_numeric_room_id_projection():
    event = to_provider_event(
        {
            "event_type": "chat",
            "uid": "42",
            "text": "hello",
            "room_ref": "room-42",
            "room_id": "7390000000000000000?cookie=must-not-leak",
            "webcast_room_id": "7390000000000000001",
        }
    )
    live_event = to_live_event(event)

    assert event.room_id == 0
    assert live_event.payload["room_ref"] == "room-42"
    assert "room_id" not in live_event.payload
    assert "must-not-leak" not in json.dumps(live_event.to_dict(), ensure_ascii=False)


def test_douyin_event_model_accepts_numeric_webcast_room_id_alias():
    event = to_provider_event(
        {
            "event_type": "chat",
            "uid": "42",
            "text": "hello",
            "room_ref": "room-42",
            "webcast_room_id": "7390000000000000001",
        }
    )

    assert event.room_id == 7390000000000000001
    assert to_live_event(event).payload["room_id"] == 7390000000000000001


def test_douyin_event_numeric_fields_do_not_stringify_objects():
    class _LooksLikeNumber:
        def __str__(self) -> str:
            return "7390000000000000001"

    event = to_provider_event(
        {
            "event_type": "gift",
            "uid": "42",
            "text": "hello",
            "room_ref": "room-42",
            "room_id": _LooksLikeNumber(),
            "webcast_room_id": "7390000000000000002",
            "num": _LooksLikeNumber(),
            "total_coin": _LooksLikeNumber(),
            "gift": {
                "num": _LooksLikeNumber(),
                "price": _LooksLikeNumber(),
            },
        }
    )
    live_event = to_live_event(event)

    assert event.room_id == 0
    assert event.gift_count == 0
    assert event.gift_value == 0
    assert "room_id" not in live_event.payload
    assert "gift_count" not in live_event.payload
    assert "gift_value" not in live_event.payload
    assert to_provider_event({"event_type": "gift", "room_ref": "room-42", "num": True}).gift_count == 0


def test_douyin_event_model_redacts_unknown_event_type_shape():
    event = to_provider_event(
        {
            "event_type": "cookie=must-not-leak",
            "uid": "42",
            "text": "hello",
            "room_ref": "room-42",
        }
    )

    assert event.event_type == "unknown"
    assert is_routable_event_type(event.event_type) is False
    assert is_status_only_event_type(event.event_type) is False
    assert "must-not-leak" not in json.dumps(asdict(event), ensure_ascii=False)


def test_douyin_event_type_does_not_stringify_objects_into_routable_aliases():
    class _LooksLikeChat:
        def __str__(self) -> str:
            return "chat"

    event = to_provider_event(
        {
            "event_type": _LooksLikeChat(),
            "uid": "42",
            "text": "hello",
            "room_ref": "room-42",
        }
    )

    assert event.event_type == "unknown"
    assert is_routable_event_type(_LooksLikeChat()) is False
    assert is_status_only_event_type(_LooksLikeChat()) is False


def test_douyin_event_type_helpers_normalize_aliases_before_routing():
    assert is_routable_event_type("chat") is True
    assert is_routable_event_type("danmu") is True
    assert is_routable_event_type("superchat") is True
    assert is_routable_event_type("sc") is True
    assert is_status_only_event_type("follow") is True
    assert is_routable_event_type("cookie=must-not-leak") is False
    assert is_status_only_event_type("cookie=must-not-leak") is False


def test_douyin_event_model_sanitizes_payload_room_ref_with_fallback():
    event = to_provider_event(
        {
            "event_type": "chat",
            "uid": "123",
            "text": "hello",
            "room_ref": "room-42?cookie=must-not-leak",
        },
        room_ref="fallback-room",
    )

    assert event.room_ref == "fallback-room"
    assert "must-not-leak" not in json.dumps(asdict(event), ensure_ascii=False)


def test_douyin_event_model_drops_nested_values_inside_allowed_fields():
    class _LooksLikeUid:
        def __str__(self) -> str:
            return "42"

    class _LooksLikeText:
        def __str__(self) -> str:
            return "must-not-leak"

    safe = safe_payload(
        {
            "uid": _LooksLikeUid(),
            "nickname": _LooksLikeText(),
            "text": _LooksLikeText(),
            "avatar_url": {"url": "https://example.test/a.png"},
            "gift_name": _LooksLikeText(),
            "gift_count": {"num": 9},
            "gift_value": ["900"],
            "content": "x" * 3000,
        }
    )

    assert safe == {
        "uid": "",
        "nickname": "",
        "text": "",
        "avatar_url": "",
        "gift_name": "",
        "gift_count": 0,
        "gift_value": 0,
        "content": "x" * 2048,
    }
    assert "must-not-leak" not in json.dumps(safe, ensure_ascii=False)


def test_douyin_event_model_redacts_sensitive_text_inside_allowed_fields():
    safe = safe_payload(
        {
            "uid": "42",
            "nickname": "viewer token=must-not-leak",
            "text": "hello Authorization: Bearer must-not-leak",
            "gift_name": "gift signature=must-not-leak",
        }
    )

    assert safe["uid"] == "42"
    assert safe["nickname"] == "viewer [redacted]"
    assert safe["text"] == "hello [redacted]"
    assert safe["gift_name"] == "gift [redacted]"
    assert "must-not-leak" not in json.dumps(safe, ensure_ascii=False)


def test_douyin_public_text_redacts_full_authorization_header():
    module = DouyinLiveIngestModule()
    module._last_error = "ws failed Authorization: Bearer must-not-leak, retry"
    module._reconnect.record_failure("retry Authorization: Bearer must-not-leak; next")

    public = module.status()
    dumped = json.dumps(public, ensure_ascii=False)

    assert public["last_error"] == "ws failed [redacted], retry"
    assert public["reconnect"]["last_reason"] == "retry [redacted]; next"
    assert "must-not-leak" not in dumped


def test_douyin_public_text_drops_object_messages_without_stringifying():
    class _LooksLikeSecret:
        def __str__(self) -> str:
            return "token=must-not-leak"

    module = DouyinLiveIngestModule()
    module._last_error = _LooksLikeSecret()
    module._reconnect.record_failure(_LooksLikeSecret())

    public = module.status()

    assert public["last_error"] == ""
    assert public["reconnect"]["last_reason"] == ""
    assert "must-not-leak" not in json.dumps(public, ensure_ascii=False)


def test_douyin_status_enabled_accepts_only_exact_boolean():
    class _Truthy:
        def __bool__(self) -> bool:
            return True

    module = DouyinLiveIngestModule()
    module.enabled = _Truthy()  # type: ignore[assignment]

    assert module.status()["enabled"] is False

    module.enabled = True

    assert module.status()["enabled"] is True


def test_douyin_event_model_live_event_projection_is_safe():
    event = to_provider_event(
        {
            "event_type": "gift",
            "open_id": "open-123",
            "user_name": "viewer",
            "gift": "small heart",
            "repeat_count": 2,
            "diamond_count": 600,
            "webcast_room_id": "7390000000000000001",
            "avatar_url": "https://example.test/avatar.png?token=must-not-leak#signature=must-not-leak",
            "cookie": "must-not-leak",
        },
        room_ref="room-42",
    )
    live_event = to_live_event(event, ts=123.0)

    assert live_event.type == "gift"
    assert live_event.uid == "douyin:open-123"
    assert live_event.payload == {
        "platform": "douyin",
        "uid": "douyin:open-123",
        "nickname": "viewer",
        "text": "",
        "event_label": "small heart",
        "room_ref": "room-42",
        "avatar_url": "https://example.test/avatar.png",
        "room_id": 7390000000000000001,
        "gift_name": "small heart",
        "gift_count": 2,
        "gift_value": 600,
    }
    assert "must-not-leak" not in json.dumps(live_event.to_dict(), ensure_ascii=False)


def test_douyin_live_event_re_sanitizes_direct_provider_event_fields():
    class _LooksLikeSecret:
        def __str__(self) -> str:
            return "token=must-not-leak"

    class _LooksLikeNumber:
        def __str__(self) -> str:
            return "7390000000000000001"

    event = DouyinLiveProviderEvent(
        event_type=_LooksLikeSecret(),  # type: ignore[arg-type]
        uid="douyin:cookie=must-not-leak",
        nickname=_LooksLikeSecret(),  # type: ignore[arg-type]
        text=_LooksLikeSecret(),  # type: ignore[arg-type]
        avatar_url=_LooksLikeSecret(),  # type: ignore[arg-type]
        room_ref="room-42?cookie=must-not-leak",
        room_id=_LooksLikeNumber(),  # type: ignore[arg-type]
        guard_level=_LooksLikeNumber(),  # type: ignore[arg-type]
        gift_name=_LooksLikeSecret(),  # type: ignore[arg-type]
        gift_count=_LooksLikeNumber(),  # type: ignore[arg-type]
        gift_value=_LooksLikeNumber(),  # type: ignore[arg-type]
    )

    live_event = to_live_event(event, ts=123.0)

    assert live_event.type == "unknown"
    assert live_event.uid == ""
    assert live_event.payload == {
        "platform": "douyin",
        "uid": "",
        "nickname": "",
        "text": "",
        "event_label": "",
        "room_ref": "",
    }
    assert isinstance(live_event.raw, dict)
    assert live_event.raw == {
        "event_type": "unknown",
        "uid": "",
        "nickname": "",
        "text": "",
        "avatar_url": "",
        "room_ref": "",
        "room_id": 0,
        "score": 1.0,
        "guard_level": 0,
        "gift_name": "",
        "gift_count": 0,
        "gift_value": 0,
    }
    assert "must-not-leak" not in json.dumps(live_event.to_dict(), ensure_ascii=False)
    assert "must-not-leak" not in json.dumps(live_event.raw, ensure_ascii=False)


def test_douyin_event_model_projects_nested_gift_summary_only():
    event = to_provider_event(
        {
            "event_type": "gift",
            "uid": "42",
            "user_name": "viewer",
            "gift": {
                "giftName": "small heart signature=must-not-leak",
                "num": "2",
                "total_coin": "600",
                "raw": {"cookie": "must-not-leak"},
            },
            "room_ref": "room-42",
        }
    )
    live_event = to_live_event(event, ts=123.0)

    assert event.gift_name == "small heart [redacted]"
    assert event.gift_count == 2
    assert event.gift_value == 600
    assert live_event.payload["gift_name"] == "small heart [redacted]"
    assert live_event.payload["gift_count"] == 2
    assert live_event.payload["gift_value"] == 600
    assert "must-not-leak" not in json.dumps(asdict(event), ensure_ascii=False)
    assert "raw" not in json.dumps(live_event.payload, ensure_ascii=False)


def test_douyin_event_model_uses_nested_gift_when_flat_summary_is_invalid():
    event = to_provider_event(
        {
            "event_type": "gift",
            "uid": "42",
            "gift_name": {"name": "must-not-leak"},
            "gift_count": ["2"],
            "gift_value": -1,
            "gift": {
                "name": "small heart",
                "repeat_count": "2",
                "price": "600",
            },
            "room_ref": "room-42",
        }
    )

    assert event.gift_name == "small heart"
    assert event.gift_count == 2
    assert event.gift_value == 600
    assert "must-not-leak" not in json.dumps(asdict(event), ensure_ascii=False)


def test_douyin_event_model_uses_first_valid_flat_gift_number_alias():
    event = to_provider_event(
        {
            "event_type": "gift",
            "uid": "42",
            "giftName": "small heart",
            "num": "-1",
            "repeat_count": "2",
            "total_coin": {"raw": 600},
            "diamond_count": "600",
            "room_ref": "room-42",
        }
    )

    assert event.gift_count == 2
    assert event.gift_value == 600


@pytest.mark.asyncio
async def test_douyin_identity_uses_sanitized_event_fields_only():
    module = DouyinIdentityModule()
    ingest = DouyinLiveIngestModule()
    ingest.ctx = _LiveEventsCtx()
    event = ingest.normalize(
        {
            "uid": "123",
            "nickname": "viewer",
            "avatar_url": "https://example.test/a.png",
            "cookie": "must-not-leak",
        }
    )

    identity = await module.resolve(event)
    public = identity.to_public_dict()

    assert identity.uid == "douyin:123"
    assert identity.nickname == "viewer"
    assert identity.name == "viewer"
    assert identity.avatar_url == "https://example.test/a.png"
    assert identity.source_url == "https://www.douyin.com/user/123"
    assert identity.avatar_bytes is None
    assert identity.fetched is True
    assert "must-not-leak" not in json.dumps(public, ensure_ascii=False)


@pytest.mark.asyncio
async def test_douyin_identity_drops_unsafe_uid_before_source_url():
    module = DouyinIdentityModule()
    event = ViewerEvent(uid="douyin:cookie=must-not-leak", nickname="viewer")

    identity = await module.resolve(event)
    public = identity.to_public_dict()

    assert identity.uid == ""
    assert identity.source_url == ""
    assert "must-not-leak" not in json.dumps(public, ensure_ascii=False)


def test_douyin_identity_status_enabled_accepts_only_exact_boolean():
    class _Truthy:
        def __bool__(self) -> bool:
            return True

    module = DouyinIdentityModule()
    module.enabled = _Truthy()  # type: ignore[assignment]

    assert module.status() == {"enabled": False, "avatar_fetch": False, "profile_fetch": False}

    module.enabled = True

    assert module.status() == {"enabled": True, "avatar_fetch": False, "profile_fetch": False}


@pytest.mark.asyncio
async def test_douyin_identity_drops_object_uid_before_source_url():
    class _LooksLikeUid:
        def __str__(self) -> str:
            return "douyin:123"

    module = DouyinIdentityModule()
    event = ViewerEvent(uid=_LooksLikeUid(), nickname="viewer")

    identity = await module.resolve(event)

    assert identity.uid == ""
    assert identity.source_url == ""


@pytest.mark.parametrize("uid", ["douyin:ttwid", "douyin:token", "douyin:sessionid"])
@pytest.mark.asyncio
async def test_douyin_identity_drops_credential_marker_uid(uid: str):
    module = DouyinIdentityModule()
    event = ViewerEvent(uid=uid, nickname="viewer")

    identity = await module.resolve(event)

    assert identity.uid == ""
    assert identity.source_url == ""


@pytest.mark.asyncio
async def test_douyin_identity_redacts_sensitive_nickname_text():
    module = DouyinIdentityModule()
    event = ViewerEvent(uid="douyin:123", nickname="viewer token=must-not-leak")

    identity = await module.resolve(event)
    public = identity.to_public_dict()

    assert identity.uid == "douyin:123"
    assert identity.nickname == "viewer [redacted]"
    assert identity.name == "viewer [redacted]"
    assert "must-not-leak" not in json.dumps(public, ensure_ascii=False)


@pytest.mark.asyncio
async def test_douyin_identity_drops_unsafe_avatar_url():
    module = DouyinIdentityModule()
    event = ViewerEvent(
        uid="douyin:123",
        nickname="viewer",
        avatar_url="data:image/png;base64,must-not-leak",
    )

    identity = await module.resolve(event)
    public = identity.to_public_dict()

    assert identity.uid == "douyin:123"
    assert identity.avatar_url == ""
    assert "must-not-leak" not in json.dumps(public, ensure_ascii=False)


@pytest.mark.asyncio
async def test_douyin_identity_drops_object_avatar_url():
    class _LooksLikeAvatarUrl:
        def __str__(self) -> str:
            return "https://example.test/avatar.png"

    module = DouyinIdentityModule()
    event = ViewerEvent(uid="douyin:123", nickname="viewer", avatar_url=_LooksLikeAvatarUrl())

    identity = await module.resolve(event)

    assert identity.uid == "douyin:123"
    assert identity.avatar_url == ""


@pytest.mark.asyncio
async def test_douyin_identity_strips_avatar_url_query_and_fragment():
    module = DouyinIdentityModule()
    event = ViewerEvent(
        uid="douyin:123",
        nickname="viewer",
        avatar_url="https://example.test/avatar.png?token=must-not-leak#signature=must-not-leak",
    )

    identity = await module.resolve(event)
    public = identity.to_public_dict()

    assert identity.uid == "douyin:123"
    assert identity.avatar_url == "https://example.test/avatar.png"
    assert "must-not-leak" not in json.dumps(public, ensure_ascii=False)


@pytest.mark.asyncio
async def test_douyin_provider_router_uses_douyin_identity_module(tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"
    event = runtime.live_provider.normalize(
        {
            "uid": "456",
            "nickname": "viewer",
            "text": "hello",
        }
    )

    identity = await runtime.live_provider.resolve_identity(event)

    assert identity.uid == "douyin:456"
    assert identity.nickname == "viewer"
    assert runtime.live_provider.identity_step_id() == "douyin_identity"


@pytest.mark.asyncio
async def test_douyin_bridge_webcast_uid_becomes_viewer_profile_key(tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"
    payload = DouyinLiveBridgeAdapter().map_message(
        {
            "method": "WebcastChatMessage",
            "user": {
                "id": "111111",
                "idStr": "111111",
                "webcastUid": "MS4wLjStable-opaque_uid.42",
                "nickname": "viewer",
                "avatarThumb": {
                    "urlList": [
                        "https://p3.douyinpic.com/aweme/100x100/avatar.jpeg?token=must-not-leak",
                    ]
                },
            },
            "content": "hello",
        },
        room_ref="room-42",
    )[0]

    event = runtime.live_provider.normalize(payload)
    identity = await runtime.live_provider.resolve_identity(event)
    public = identity.to_public_dict()

    assert event.uid == "douyin:MS4wLjStable-opaque_uid.42"
    assert identity.uid == "douyin:MS4wLjStable-opaque_uid.42"
    assert identity.nickname == "viewer"
    assert identity.avatar_url == "https://p3.douyinpic.com/aweme/100x100/avatar.jpeg"
    assert "111111" not in identity.uid
    assert "must-not-leak" not in json.dumps(public, ensure_ascii=False)


def test_parse_webcast_info_reads_render_data_without_exposing_raw_payload():
    render_data = {
        "app": {
            "roomStore": {
                "roomInfo": {
                    "roomId": "7390000000000000000",
                    "title": "morning live",
                    "status": 2,
                    "owner": {"nickname": "anchor"},
                    "cookie": "must-not-leak",
                    "webcast_sign": "must-not-leak",
                }
            }
        }
    }
    page = f'<html><script id="RENDER_DATA" type="application/json">{quote(json.dumps(render_data))}</script></html>'

    info = parse_webcast_info(page, room_ref="room-42")
    public = info.to_public_dict()

    assert info.ok is True
    assert info.webcast_room_id == "7390000000000000000"
    assert info.title == "morning live"
    assert info.anchor_name == "anchor"
    assert info.live_status == "live"
    assert "must-not-leak" not in json.dumps(public, ensure_ascii=False)
    assert "roomStore" not in json.dumps(public, ensure_ascii=False)


def test_parse_webcast_info_reads_escaped_room_store_fallback():
    page = '''
    <script>self.__pace_f.push([1,"
    {\\"roomStore\\":{\\"roomInfo\\":{\\"room\\":{\\"id_str\\":\\"7657807853096356658\\",\\"status\\":2,
    \\"status_str\\":\\"2\\",\\"title\\":\\"KPL test\\",\\"cover\\":{\\"url_list\\":[\\"https://example.invalid/secret?signature=hidden\\"]}},
    \\"web_rid\\":\\"66186758468\\",\\"anchor\\":{\\"id_str\\":\\"1168096188708809\\",\\"nickname\\":\\"anchor name\\"}}}}
    "])</script>
    '''

    info = parse_webcast_info(page, room_ref="66186758468")

    assert info.ok is True
    assert info.room_ref == "66186758468"
    assert info.webcast_room_id == "7657807853096356658"
    assert info.title == "KPL test"
    assert info.anchor_name == "anchor name"
    assert info.live_status == "live"
    public = info.to_public_dict()
    assert "signature" not in json.dumps(public, ensure_ascii=False)


def test_parse_webcast_info_reads_user_unique_id_from_escaped_room_store():
    page = (
        r'{"web_rid":"room-42","user_unique_id":"7000000000000000001",'
        r'"room":{"id_str":"7390000000000000000","status":2,"status_str":"2","title":"KPL test"}}'
    )

    info = parse_webcast_info(page, room_ref="room-42")

    assert info.ok is True
    assert info.user_unique_id == "7000000000000000001"


def test_parse_webcast_info_rejects_escaped_room_store_for_other_web_rid():
    page = '''
    <script>self.__pace_f.push([1,"
    {\\"roomStore\\":{\\"roomInfo\\":{\\"room\\":{\\"id_str\\":\\"7657807853096356658\\",\\"status\\":2,
    \\"status_str\\":\\"2\\",\\"title\\":\\"other\\"},\\"web_rid\\":\\"other-room\\",\\"anchor\\":{\\"nickname\\":\\"anchor\\"}}}}
    "])</script>
    '''

    info = parse_webcast_info(page, room_ref="66186758468")

    assert info.ok is False
    assert info.webcast_room_id == ""
    assert info.message == "douyin room metadata not found"


def test_parse_webcast_info_sanitizes_public_room_ref_projection():
    render_data = {
        "roomInfo": {
            "roomId": "7390000000000000000",
            "title": "morning live",
            "status": 2,
        }
    }

    info = parse_webcast_info(
        json.dumps(render_data),
        room_ref="https://live.douyin.com/room-42?cookie=must-not-leak",
    )
    public = info.to_public_dict()

    assert info.room_ref == "room-42"
    assert public["room_ref"] == "room-42"
    assert "must-not-leak" not in json.dumps(public, ensure_ascii=False)


def test_webcast_info_public_projection_requires_numeric_webcast_room_id():
    info = DouyinWebcastInfo(
        ok=True,
        room_ref="https://live.douyin.com/room-42?cookie=must-not-leak",
        webcast_room_id="7390000000000000000?cookie=must-not-leak",
        title="morning live",
        message="douyin room metadata found",
    )
    public = info.to_public_dict()
    status = info.to_live_room_status()

    assert info.ok is False
    assert public["ok"] is False
    assert public["room_ref"] == "room-42"
    assert public["webcast_room_id"] == ""
    assert status.room_id == 0
    assert "must-not-leak" not in json.dumps(public, ensure_ascii=False)


def test_webcast_room_id_projection_accepts_only_string_digits_or_positive_int():
    class _LooksLikeRoomId:
        def __str__(self) -> str:
            return "7390000000000000000"

    assert DouyinWebcastInfo(ok=True, webcast_room_id=7390000000000000000).webcast_room_id == "7390000000000000000"
    assert DouyinWebcastInfo(ok=True, webcast_room_id=True).webcast_room_id == ""
    assert DouyinWebcastInfo(ok=True, webcast_room_id=b"7390000000000000000").webcast_room_id == ""
    assert DouyinWebcastInfo(ok=True, webcast_room_id=_LooksLikeRoomId()).webcast_room_id == ""


def test_webcast_info_public_ok_accepts_only_exact_true():
    class _Truthy:
        def __bool__(self) -> bool:
            return True

    info = DouyinWebcastInfo(ok=_Truthy(), webcast_room_id="7390000000000000000")  # type: ignore[arg-type]
    public = info.to_public_dict()
    status = info.to_live_room_status()

    assert info.ok is False
    assert public["ok"] is False
    assert status.ok is False


def test_webcast_info_public_projection_sanitizes_text_fields():
    info = DouyinWebcastInfo(
        ok=True,
        room_ref="room-42",
        webcast_room_id="7390000000000000000",
        title="cookie=must-not-leak " + ("x" * 200),
        anchor_name="signature=must-not-leak",
        live_status="token=must-not-leak",
        message="metadata failed token=must-not-leak " + ("y" * 200),
    )
    public = info.to_public_dict()
    status = info.to_live_room_status()

    assert public["title"].startswith("[redacted] ")
    assert len(public["title"]) == 120
    assert public["anchor_name"] == "[redacted]"
    assert public["live_status"] == "unknown"
    assert public["message"].startswith("metadata failed [redacted] ")
    assert len(public["message"]) == 160
    assert status.title == public["title"]
    assert status.anchor_name == public["anchor_name"]
    assert status.live_status == "unknown"
    assert status.message == public["message"]
    assert "must-not-leak" not in json.dumps(public, ensure_ascii=False)
    assert "must-not-leak" not in json.dumps(status.to_dict(), ensure_ascii=False)


def test_parse_webcast_info_drops_nested_private_metadata_fields():
    render_data = {
        "roomInfo": {
            "roomId": "7390000000000000000",
            "title": {"cookie": "must-not-leak"},
            "owner": {"nickname": {"signature": "must-not-leak"}},
            "status": 2,
        }
    }

    info = parse_webcast_info(json.dumps(render_data), room_ref="room-42")
    public = info.to_public_dict()

    assert info.ok is True
    assert info.webcast_room_id == "7390000000000000000"
    assert info.title == ""
    assert info.anchor_name == ""
    assert info.live_status == "live"
    assert "must-not-leak" not in json.dumps(public, ensure_ascii=False)


def test_parse_webcast_info_rejects_non_string_html_without_stringifying():
    class _LooksLikeHtml:
        def __str__(self) -> str:
            return '{"roomInfo":{"roomId":"7390000000000000000","title":"must-not-leak"}}'

    info = parse_webcast_info(_LooksLikeHtml(), room_ref="room-42")
    public = info.to_public_dict()

    assert info.ok is False
    assert info.webcast_room_id == ""
    assert info.title == ""
    assert info.message == "douyin room metadata not found"
    assert "must-not-leak" not in json.dumps(public, ensure_ascii=False)


def test_parse_webcast_info_requires_numeric_room_id_before_public_projection():
    render_data = {
        "roomInfo": {
            "roomId": "cookie=must-not-leak",
            "title": "must-not-leak",
            "owner": {"nickname": "must-not-leak"},
            "status": 2,
        }
    }

    info = parse_webcast_info(json.dumps(render_data), room_ref="room-42")
    public = info.to_public_dict()

    assert info.ok is False
    assert info.webcast_room_id == ""
    assert info.title == ""
    assert info.anchor_name == ""
    assert info.message == "douyin room metadata not found"
    assert "must-not-leak" not in json.dumps(public, ensure_ascii=False)


def test_parse_webcast_info_returns_safe_missing_metadata_status():
    info = parse_webcast_info("<html>no render data</html>", room_ref="room-42")

    assert info.ok is False
    assert info.room_ref == "room-42"
    assert info.message == "douyin room metadata not found"


def test_room_page_url_quotes_room_ref_path_segment():
    assert room_page_url("room 42") == "https://live.douyin.com/room%2042"


def test_room_page_url_does_not_stringify_object_room_ref():
    class _LooksLikeRoomRef:
        def __str__(self) -> str:
            return "room-42?cookie=must-not-leak"

    url = room_page_url(_LooksLikeRoomRef())

    assert url == "https://live.douyin.com/"
    assert "must-not-leak" not in url


def test_fetch_webcast_info_bounds_urlopen_timeout(monkeypatch):
    class _LooksLikeTimeout:
        def __float__(self) -> float:
            return 3.0

    calls: list[float] = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self) -> bytes:
            return b'{"roomInfo":{"roomId":"7390000000000000000","title":"ok"}}'

    def fake_urlopen(request, *, timeout: float):
        calls.append(timeout)
        return _Response()

    monkeypatch.setattr(
        "plugin.plugins.neko_roast.modules.douyin_live_ingest.webcast.urllib.request.urlopen",
        fake_urlopen,
    )

    assert fetch_webcast_info("room-42", timeout=float("nan")).ok is True
    assert fetch_webcast_info("room-42", timeout=999).ok is True
    assert fetch_webcast_info("room-42", timeout=3).ok is True
    assert fetch_webcast_info("room-42", timeout="4.5").ok is True
    assert fetch_webcast_info("room-42", timeout=_LooksLikeTimeout()).ok is True

    assert calls == [8.0, 15.0, 3.0, 4.5, 8.0]


def test_fetch_webcast_info_rejects_unsafe_cookie_headers(monkeypatch):
    class _LooksLikeCookie:
        def __str__(self) -> str:
            return "ttwid=must-not-leak"

    observed_cookies: list[str | None] = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self) -> bytes:
            return b'{"roomInfo":{"roomId":"7390000000000000000","title":"ok"}}'

    def fake_urlopen(request, *, timeout: float):
        observed_cookies.append(request.get_header("Cookie"))
        return _Response()

    monkeypatch.setattr(
        "plugin.plugins.neko_roast.modules.douyin_live_ingest.webcast.urllib.request.urlopen",
        fake_urlopen,
    )

    assert fetch_webcast_info("room-42", cookie="ttwid=ok\r\nX-Bad: token=must-not-leak").ok is True
    assert fetch_webcast_info("room-42", cookie=_LooksLikeCookie()).ok is True
    assert fetch_webcast_info("room-42", cookie="ttwid=ok; sessionid=ok").ok is True

    assert observed_cookies == [None, None, "ttwid=ok; sessionid=ok"]


def test_fetch_webcast_info_returns_status_for_http_error(monkeypatch):
    def fake_urlopen(request, *, timeout: float):
        raise urllib.error.HTTPError(
            url=request.full_url,
            code=404,
            msg="Not Found token=must-not-leak",
            hdrs={},
            fp=None,
        )

    monkeypatch.setattr(
        "plugin.plugins.neko_roast.modules.douyin_live_ingest.webcast.urllib.request.urlopen",
        fake_urlopen,
    )

    info = fetch_webcast_info("room-42", cookie="ttwid=secret")

    assert info.ok is False
    assert info.room_ref == "room-42"
    assert info.message == "douyin room page fetch failed: HTTP 404"
    dumped = json.dumps(info.to_public_dict(), ensure_ascii=False)
    assert "must-not-leak" not in dumped
    assert "secret" not in dumped


def test_fetch_webcast_info_returns_status_for_url_error(monkeypatch):
    def fake_urlopen(request, *, timeout: float):
        raise urllib.error.URLError("connection failed token=must-not-leak")

    monkeypatch.setattr(
        "plugin.plugins.neko_roast.modules.douyin_live_ingest.webcast.urllib.request.urlopen",
        fake_urlopen,
    )

    info = fetch_webcast_info("room-42", cookie="ttwid=secret")

    assert info.ok is False
    assert info.room_ref == "room-42"
    assert info.message == "douyin room page fetch failed"
    dumped = json.dumps(info.to_public_dict(), ensure_ascii=False)
    assert "must-not-leak" not in dumped
    assert "secret" not in dumped


def test_douyin_public_projection_numeric_helpers_are_non_negative_and_finite():
    class _LooksLikeInt:
        def __int__(self) -> int:
            return 3

    class _LooksLikeFloat:
        def __float__(self) -> float:
            return 2.5

    assert safe_public_int("3") == 3
    assert safe_public_int(True) == 0
    assert safe_public_int(_LooksLikeInt()) == 0
    assert safe_public_int(b"3") == 0
    assert safe_public_int({"value": 3}) == 0
    assert safe_public_int(-1) == 0
    assert safe_public_int("bad") == 0
    assert safe_public_float("2.5") == 2.5
    assert safe_public_float(True) == 0.0
    assert safe_public_float(_LooksLikeFloat()) == 0.0
    assert safe_public_float(b"2.5") == 0.0
    assert safe_public_float([2.5]) == 0.0
    assert safe_public_float(float("nan")) == 0.0
    assert safe_public_float(float("inf")) == 0.0
    assert safe_public_float(-1.0) == 0.0


def test_douyin_public_projection_bool_helper_accepts_only_exact_true():
    class _Truthy:
        def __bool__(self) -> bool:
            return True

    assert safe_public_bool(True) is True
    assert safe_public_bool(False) is False
    assert safe_public_bool("true") is False
    assert safe_public_bool(1) is False
    assert safe_public_bool(_Truthy()) is False


def test_douyin_public_projection_host_helper_rejects_local_or_private_hosts():
    class _LooksLikePublicHost:
        def __str__(self) -> str:
            return "example.test"

    assert is_public_hostname("example.test") is True
    assert is_public_hostname(_LooksLikePublicHost()) is False
    assert is_public_hostname(b"example.test") is False
    assert is_public_hostname("intranet") is False
    assert is_public_hostname("localhost") is False
    assert is_public_hostname("app.localhost") is False
    assert is_public_hostname("127.0.0.1") is False
    assert is_public_hostname("192.168.1.2") is False
    assert is_public_hostname("") is False


def test_douyin_transport_event_reuses_safe_payload_boundary():
    event = DouyinTransportEvent(
        {
            "event_type": "chat",
            "uid": "123",
            "text": "hello",
            "cookie": "must-not-leak",
            "signature": "must-not-leak",
            "protobuf": b"must-not-leak",
        }
    )

    safe = event.safe_payload()

    assert safe == {"event_type": "chat", "uid": "123", "text": "hello"}
    assert "must-not-leak" not in json.dumps(safe, ensure_ascii=False)


def test_douyin_reconnect_policy_bounds_backoff_and_exhaustion():
    state = DouyinReconnectState(
        DouyinReconnectPolicy(
            max_retries=3,
            base_delay_seconds=1.0,
            max_delay_seconds=3.0,
            backoff_multiplier=2.0,
        )
    )

    state.record_failure("ws closed")
    assert state.retry_count == 1
    assert state.next_delay_seconds == 1.0
    assert state.exhausted is False

    state.record_failure("heartbeat timeout")
    assert state.retry_count == 2
    assert state.next_delay_seconds == 2.0
    assert state.exhausted is False

    state.record_failure("protobuf decode failed")
    assert state.retry_count == 3
    assert state.next_delay_seconds == 3.0
    assert state.exhausted is False

    state.record_failure("retry limit")
    assert state.retry_count == 4
    assert state.next_delay_seconds == 0.0
    assert state.exhausted is True


def test_douyin_reconnect_state_public_projection_is_bounded_and_safe():
    state = DouyinReconnectState(DouyinReconnectPolicy())
    state.record_failure("x" * 200)
    public = state.to_public_dict()

    assert public["retry_count"] == 1
    assert public["last_reason"] == "x" * 120
    assert public["policy"]["max_retries"] == 3
    assert "cookie" not in json.dumps(public, ensure_ascii=False).lower()


def test_douyin_reconnect_public_projection_coerces_invalid_numbers():
    state = DouyinReconnectState(
        DouyinReconnectPolicy(
            max_retries=-1,
            base_delay_seconds=float("nan"),
            max_delay_seconds=float("inf"),
            backoff_multiplier=-2.0,
        )
    )
    state.retry_count = -3
    state.next_delay_seconds = float("nan")
    state.exhausted = "yes"  # type: ignore[assignment]

    public = state.to_public_dict()

    assert public["policy"] == {
        "max_retries": 0,
        "base_delay_seconds": 0.0,
        "max_delay_seconds": 0.0,
        "backoff_multiplier": 0.0,
    }
    assert public["retry_count"] == 0
    assert public["next_delay_seconds"] == 0.0
    assert public["exhausted"] is False


def test_douyin_reconnect_public_projection_accepts_only_exact_boolean_exhausted():
    class _Truthy:
        def __bool__(self) -> bool:
            return True

    state = DouyinReconnectState(DouyinReconnectPolicy())
    state.exhausted = _Truthy()  # type: ignore[assignment]

    assert state.to_public_dict()["exhausted"] is False

    state.exhausted = True

    assert state.to_public_dict()["exhausted"] is True


def test_douyin_reconnect_record_failure_normalizes_non_boolean_exhausted_state():
    class _Truthy:
        def __bool__(self) -> bool:
            return True

    state = DouyinReconnectState(DouyinReconnectPolicy())
    state.exhausted = _Truthy()  # type: ignore[assignment]

    state.record_failure("ws closed")

    assert state.retry_count == 1
    assert state.next_delay_seconds == 1.0
    assert state.exhausted is False
    assert state.to_public_dict()["exhausted"] is False


def test_douyin_reconnect_policy_next_delay_never_returns_negative_or_non_finite():
    class _LooksLikeFloat:
        def __float__(self) -> float:
            return 1.0

    assert DouyinReconnectPolicy(base_delay_seconds=float("nan")).next_delay(1) == 0.0
    assert DouyinReconnectPolicy(max_delay_seconds=float("inf")).next_delay(1) == 0.0
    assert DouyinReconnectPolicy(backoff_multiplier=-1.0).next_delay(2) == 0.0
    assert DouyinReconnectPolicy(base_delay_seconds=-1.0).next_delay(1) == 0.0
    assert DouyinReconnectPolicy(base_delay_seconds=_LooksLikeFloat()).next_delay(1) == 0.0


def test_douyin_reconnect_state_invalid_retry_budget_exhausts_without_exception():
    class _LooksLikeInt:
        def __int__(self) -> int:
            return 3

    state = DouyinReconnectState(DouyinReconnectPolicy(max_retries={"raw": 3}))  # type: ignore[arg-type]
    object_budget_state = DouyinReconnectState(DouyinReconnectPolicy(max_retries=_LooksLikeInt()))  # type: ignore[arg-type]

    state.record_failure("ws closed")
    object_budget_state.record_failure("ws closed")

    assert state.retry_count == 1
    assert state.next_delay_seconds == 0.0
    assert state.exhausted is True
    assert object_budget_state.retry_count == 1
    assert object_budget_state.next_delay_seconds == 0.0
    assert object_budget_state.exhausted is True


def test_douyin_reconnect_state_redacts_transport_failure_secrets():
    state = DouyinReconnectState(DouyinReconnectPolicy())
    state.record_failure(
        "ws failed Cookie: ttwid=secret-cookie; odin_tt=hidden-token "
        "signature=secret-sign token=secret-token"
    )

    public = state.to_public_dict()
    dumped = json.dumps(public, ensure_ascii=False)

    assert "[redacted]" in public["last_reason"]
    assert "secret-cookie" not in dumped
    assert "hidden-token" not in dumped
    assert "secret-sign" not in dumped
    assert "secret-token" not in dumped


@pytest.mark.asyncio
async def test_douyin_start_listening_degrades_until_external_bridge_exists(tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"
    runtime.config.live_room_ref = "https://live.douyin.com/example"
    runtime.douyin_live_ingest._bridge_supervisor = _MissingBridgeSupervisor()

    started = await runtime.live_provider.start_listening(runtime.config.live_room_ref)

    assert started is False
    state = runtime.live_provider.listener_state()
    assert state["state"] == "unsupported"
    assert state["room_ref"] == "example"
    assert state["connection_plan"]["ready"] is False
    assert state["connection_plan"]["missing"] == ["bridge_executable"]
    assert state["connection_plan"]["message"] == "bundled bridge executable is missing"
    assert state["last_error"] == "bundled bridge executable is missing"
    assert state["reconnect"]["retry_count"] == 0
    assert state["reconnect"]["policy"]["max_retries"] == 3
    assert runtime.live_provider.status()["platform"] == "douyin"


@pytest.mark.asyncio
async def test_douyin_connect_snapshot_exposes_external_bridge_failure_state(tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"
    runtime.config.live_room_ref = "https://live.douyin.com/example"
    runtime.douyin_live_ingest._bridge_supervisor = _MissingBridgeSupervisor()

    snapshot = await runtime.connect_live_room()

    assert snapshot["connected"] is False
    assert snapshot["state"] == "unsupported"
    assert snapshot["last_error"] == "bundled bridge executable is missing"
    assert snapshot["connection_plan"]["ready"] is False
    assert snapshot["connection_plan"]["missing"] == ["bridge_executable"]
    assert snapshot["room_ref"] == "example"
    assert runtime.config.live_enabled is False
    assert "ttwid=" not in json.dumps(snapshot, ensure_ascii=False)


@pytest.mark.asyncio
async def test_douyin_connect_snapshot_sanitizes_configured_room_ref(tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"
    runtime.config.live_room_ref = "https://live.douyin.com/example?cookie=must-not-leak"
    runtime.douyin_live_ingest._bridge_supervisor = _MissingBridgeSupervisor()

    snapshot = await runtime.connect_live_room()
    dumped = json.dumps(snapshot, ensure_ascii=False)

    assert snapshot["room_ref"] == "example"
    assert "must-not-leak" not in dumped
    assert "cookie=must-not-leak" not in dumped


@pytest.mark.asyncio
async def test_douyin_connect_snapshot_normalizes_platform_alias(tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "dy"
    runtime.config.live_room_ref = "https://live.douyin.com/example"
    runtime.douyin_live_ingest._bridge_supervisor = _MissingBridgeSupervisor()

    snapshot = await runtime.connect_live_room()

    assert snapshot["platform"] == "douyin"
    assert snapshot["room_id"] == 0
    assert snapshot["room_ref"] == "example"


@pytest.mark.asyncio
async def test_douyin_start_listening_builds_bridge_connection_plan_without_metadata_fetch(monkeypatch, tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"
    runtime.douyin_credential = {"cookie": "ttwid=secret-cookie"}
    runtime.douyin_live_ingest.ctx = runtime
    calls: list[dict[str, str]] = []

    def fake_fetch(room_ref: str, *, cookie: str = "", timeout: float = 8.0) -> DouyinWebcastInfo:
        calls.append({"room_ref": room_ref, "cookie": cookie, "timeout": str(timeout)})
        return DouyinWebcastInfo(
            ok=True,
            room_ref=room_ref,
            webcast_room_id="7390000000000000000",
            title="morning live",
            anchor_name="anchor",
            live_status="live",
            message="douyin room metadata found",
        )

    monkeypatch.setattr(
        "plugin.plugins.neko_roast.modules.douyin_live_ingest.fetch_webcast_info",
        fake_fetch,
    )

    class _FakeTransport:
        async def start(self, request):
            return DouyinTransportState(state="unsupported", last_error="manual fake bridge")

        async def stop(self):
            return DouyinTransportState(state="disconnected")

    runtime.douyin_live_ingest._bridge_transport = _FakeTransport()

    started = await runtime.live_provider.start_listening("https://live.douyin.com/room-42")

    assert started is False
    assert calls == []
    state = runtime.live_provider.listener_state()
    assert state["state"] == "unsupported"
    assert state["connection_plan"]["webcast_room_id"] == ""
    assert state["connection_plan"]["ready"] is True
    assert state["connection_plan"]["missing"] == []
    assert state["connection_plan"]["message"] == "douyin external bridge transport ready"
    assert state["last_error"] == "manual fake bridge"
    assert state["reconnect"]["retry_count"] == 0
    assert "secret-cookie" not in json.dumps(state, ensure_ascii=False)


@pytest.mark.asyncio
async def test_douyin_start_listening_delegates_to_external_bridge_boundary(monkeypatch, tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"
    runtime.douyin_credential = {"cookie": "ttwid=secret-cookie"}
    runtime.douyin_live_ingest.ctx = runtime
    calls: list[dict[str, object]] = []

    def fake_fetch(room_ref: str, *, cookie: str = "", timeout: float = 8.0) -> DouyinWebcastInfo:
        return DouyinWebcastInfo(
            ok=True,
            room_ref=room_ref,
            webcast_room_id="7390000000000000000",
            title="morning live",
            anchor_name="anchor",
            live_status="live",
            message="douyin room metadata found",
        )

    class _FakeTransport:
        async def start(self, request):
            calls.append(
                {
                    "room_ref": request.room_ref,
                    "cookie": request.cookie,
                    "connection_ready": request.connection_plan.ready,
                }
            )
            return DouyinTransportState(state="connected", last_error="")

        async def stop(self):
            return DouyinTransportState(state="disconnected")

    monkeypatch.setattr(
        "plugin.plugins.neko_roast.modules.douyin_live_ingest.fetch_webcast_info",
        fake_fetch,
    )
    runtime.douyin_live_ingest._bridge_transport = _FakeTransport()

    started = await runtime.live_provider.start_listening("https://live.douyin.com/room-42")

    assert started is True
    assert calls == [
        {
            "room_ref": "room-42",
            "cookie": "ttwid=secret-cookie",
            "connection_ready": True,
        }
    ]
    state = runtime.live_provider.listener_state()
    assert state["state"] == "connected"
    assert state["last_error"] == ""
    assert "secret-cookie" not in json.dumps(state, ensure_ascii=False)


def test_douyin_live_bridge_adapter_maps_batch_chat_and_gift_payloads():
    adapter = DouyinLiveBridgeAdapter()

    payloads = adapter.map_message(
        {
            "data": [
                {
                    "type": "chat",
                    "user": {"uid": "123", "nickname": "viewer"},
                    "content": "hello bridge",
                    "roomId": "7390000000000000000",
                    "cookie": "must-not-leak",
                },
                {
                    "type": "gift",
                    "user": {"uid": "456", "nickname": "giver"},
                    "gift": {"name": "rose"},
                    "repeatCount": 3,
                    "diamondCount": 30,
                },
            ]
        },
        room_ref="room-42",
    )

    assert payloads == [
        {
            "event_type": "danmaku",
            "room_ref": "room-42",
            "uid": "123",
            "nickname": "viewer",
            "text": "hello bridge",
            "avatar_url": "",
            "gift_name": "",
            "gift_count": 0,
            "gift_value": 0,
            "room_id": 7390000000000000000,
        },
        {
            "event_type": "gift",
            "room_ref": "room-42",
            "uid": "456",
            "nickname": "giver",
            "text": "",
            "avatar_url": "",
            "gift_name": "rose",
            "gift_count": 3,
            "gift_value": 30,
            "room_id": 0,
        },
    ]
    assert "must-not-leak" not in json.dumps(payloads, ensure_ascii=False)


def test_douyin_live_bridge_adapter_maps_nested_gift_payload_variants():
    adapter = DouyinLiveBridgeAdapter()

    payloads = adapter.map_message(
        {
            "payload": {
                "method": "WebcastGiftMessage",
                "common": {
                    "user": {
                        "id": "789",
                        "nickName": "nested giver",
                        "avatarThumb": "https://p3.douyinpic.com/img/avatar.png?token=must-not-leak",
                    }
                },
                "giftInfo": {"displayName": "big heart", "diamondCount": "660"},
                "count": "2",
                "webRid": "room-42",
                "raw": {"cookie": "must-not-leak"},
            }
        },
        room_ref="fallback-room",
    )

    assert payloads == [
        {
            "event_type": "gift",
            "room_ref": "room-42",
            "uid": "789",
            "nickname": "nested giver",
            "text": "",
            "avatar_url": "https://p3.douyinpic.com/img/avatar.png",
            "gift_name": "big heart",
            "gift_count": 2,
            "gift_value": 660,
            "room_id": 0,
        }
    ]
    assert "must-not-leak" not in json.dumps(payloads, ensure_ascii=False)


def test_douyin_live_bridge_adapter_prefers_nested_user_avatar_thumb():
    adapter = DouyinLiveBridgeAdapter()

    payloads = adapter.map_message(
        {
            "method": "WebcastChatMessage",
            "avatarThumb": "https://p3.douyinpic.com/anchor.png",
            "user": {
                "id": "123",
                "nickName": "viewer",
                "avatarThumb": {
                    "urlList": [
                        "https://p26.douyinpic.com/user.png?token=must-not-leak",
                    ]
                },
            },
            "content": "hello",
        },
        room_ref="room-42",
    )

    assert payloads[0]["avatar_url"] == "https://p26.douyinpic.com/user.png"
    assert payloads[0]["uid"] == "123"
    assert payloads[0]["nickname"] == "viewer"
    dumped = json.dumps(payloads, ensure_ascii=False)
    assert "anchor.png" not in dumped
    assert "must-not-leak" not in dumped


def test_douyin_live_bridge_adapter_prefers_webcast_uid_over_placeholder_id():
    adapter = DouyinLiveBridgeAdapter()

    payloads = adapter.map_message(
        {
            "method": "WebcastChatMessage",
            "user": {
                "id": "111111",
                "idStr": "111111",
                "webcastUid": "MS4wLjStable-opaque_uid.42",
                "nickname": "viewer",
            },
            "content": "hello",
        },
        room_ref="room-42",
    )

    assert payloads[0]["uid"] == "MS4wLjStable-opaque_uid.42"
    assert payloads[0]["nickname"] == "viewer"
    assert payloads[0]["text"] == "hello"


def test_douyin_live_bridge_adapter_infers_gift_when_type_is_missing():
    adapter = DouyinLiveBridgeAdapter()

    payloads = adapter.map_message(
        {
            "user": {"uid": "456", "nickname": "giver"},
            "gift": {"giftName": "rose", "price": 30},
            "comboCount": 3,
        },
        room_ref="room-42",
    )

    assert payloads == [
        {
            "event_type": "gift",
            "room_ref": "room-42",
            "uid": "456",
            "nickname": "giver",
            "text": "",
            "avatar_url": "",
            "gift_name": "rose",
            "gift_count": 3,
            "gift_value": 30,
            "room_id": 0,
        }
    ]


def test_douyin_live_bridge_adapter_drops_unknown_method_nested_payloads():
    adapter = DouyinLiveBridgeAdapter()

    payloads = adapter.map_message(
        {
            "method": "WebcastResidentGuestMessage",
            "msgType": 1,
            "data": {
                "message": "resident guest",
                "user": {"uid": "123", "nickname": "viewer"},
            },
        },
        room_ref="room-42",
    )

    assert payloads == []


def test_douyin_live_bridge_adapter_maps_linker_contribute_as_gift_signal():
    adapter = DouyinLiveBridgeAdapter()

    payloads = adapter.map_message(
        {
            "common": {
                "method": "WebcastLinkerContributeMessage",
                "roomId": "7390000000000000000",
            },
            "method": "WebcastLinkerContributeMessage",
            "userId": "999",
            "totalScore": "300",
            "userContributeList": [{"userId": "456", "cookie": "must-not-leak"}],
            "quickInteract": {"receiveGiftUserId": "999"},
            "avatarThumb": "https://p3.douyinpic.com/anchor.png?token=must-not-leak",
        },
        room_ref="room-42",
    )

    assert payloads == [
        {
            "event_type": "gift",
            "room_ref": "room-42",
            "uid": "456",
            "nickname": "",
            "text": "",
            "avatar_url": "",
            "gift_name": "",
            "gift_count": 0,
            "gift_value": 300,
            "room_id": 0,
        }
    ]
    assert "must-not-leak" not in json.dumps(payloads, ensure_ascii=False)


def test_douyin_live_bridge_adapter_prefers_contributor_webcast_uid_for_gift_signal():
    adapter = DouyinLiveBridgeAdapter()

    payloads = adapter.map_message(
        {
            "common": {"method": "WebcastLinkerContributeMessage"},
            "method": "WebcastLinkerContributeMessage",
            "userContributeList": [
                {
                    "id": "111111",
                    "idStr": "111111",
                    "webcastUid": "MS4wLjGift-stable_uid.42",
                }
            ],
            "totalScore": "300",
        },
        room_ref="room-42",
    )

    assert payloads[0]["event_type"] == "gift"
    assert payloads[0]["uid"] == "MS4wLjGift-stable_uid.42"
    assert payloads[0]["gift_value"] == 300


@pytest.mark.asyncio
async def test_douyin_start_listening_uses_external_bridge_without_direct_attempt(monkeypatch, tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"
    runtime.douyin_credential = {"cookie": "ttwid=secret-cookie"}
    runtime.douyin_live_ingest.ctx = runtime
    calls: list[str] = []

    def fake_fetch(room_ref: str, *, cookie: str = "", timeout: float = 8.0) -> DouyinWebcastInfo:
        return DouyinWebcastInfo(
            ok=True,
            room_ref=room_ref,
            webcast_room_id="7390000000000000000",
            title="morning live",
            anchor_name="anchor",
            live_status="live",
            message="douyin room metadata found",
        )

    class _BridgeTransport:
        async def start(self, request):
            calls.append(f"bridge:{request.room_ref}")
            request.emit(
                DouyinTransportEvent(
                    {
                        "event_type": "chat",
                        "uid": "123",
                        "nickname": "viewer",
                        "text": "hello from bridge",
                        "room_ref": request.room_ref,
                    },
                    ts=123.0,
                )
            )
            return DouyinTransportState(state="connected", last_event_at=123.0, last_event_type="chat")

        async def stop(self):
            return DouyinTransportState(state="disconnected")

    monkeypatch.setattr(
        "plugin.plugins.neko_roast.modules.douyin_live_ingest.fetch_webcast_info",
        fake_fetch,
    )
    runtime.douyin_live_ingest._bridge_transport = _BridgeTransport()

    started = await runtime.live_provider.start_listening("https://live.douyin.com/room-42")

    assert started is True
    assert calls == ["bridge:room-42"]
    assert runtime.event_bus.status()["publish_count"] == 1
    state = runtime.live_provider.listener_state()
    assert state["state"] == "connected"
    assert state["last_error"] == ""
    assert runtime.live_provider.status()["last_published_event_type"] == "danmaku"
    assert "secret-cookie" not in json.dumps(state, ensure_ascii=False)


@pytest.mark.asyncio
async def test_douyin_start_listening_reports_external_bridge_error(monkeypatch, tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"
    runtime.douyin_credential = {"cookie": "ttwid=secret-cookie"}
    runtime.douyin_live_ingest.ctx = runtime

    def fake_fetch(room_ref: str, *, cookie: str = "", timeout: float = 8.0) -> DouyinWebcastInfo:
        return DouyinWebcastInfo(
            ok=True,
            room_ref=room_ref,
            webcast_room_id="7390000000000000000",
            title="morning live",
            anchor_name="anchor",
            live_status="live",
            message="douyin room metadata found",
        )

    class _FailingBridgeTransport:
        async def start(self, request):
            return DouyinTransportState(state="disconnected", last_error="external bridge failed: ConnectionRefusedError")

        async def stop(self):
            return DouyinTransportState(state="disconnected")

    monkeypatch.setattr(
        "plugin.plugins.neko_roast.modules.douyin_live_ingest.fetch_webcast_info",
        fake_fetch,
    )
    runtime.douyin_live_ingest._bridge_transport = _FailingBridgeTransport()

    started = await runtime.live_provider.start_listening("https://live.douyin.com/room-42")
    state = runtime.live_provider.listener_state()

    assert started is False
    assert state["state"] == "disconnected"
    assert state["last_error"] == "external bridge failed: ConnectionRefusedError"
    assert "secret-cookie" not in json.dumps(state, ensure_ascii=False)


@pytest.mark.asyncio
async def test_douyin_start_listening_does_not_require_plugin_cookie(monkeypatch, tmp_path):
    class _LooksLikeCookie:
        def __str__(self) -> str:
            return "ttwid=secret-cookie"

    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"
    runtime.douyin_credential = {"cookie": _LooksLikeCookie()}
    runtime.douyin_live_ingest.ctx = runtime
    calls: list[dict[str, str]] = []

    def fake_fetch(*args, **kwargs):
        raise AssertionError("non-string cookie must not reach metadata fetch")

    monkeypatch.setattr(
        "plugin.plugins.neko_roast.modules.douyin_live_ingest.fetch_webcast_info",
        fake_fetch,
    )

    class _FakeBridgeTransport:
        async def start(self, request):
            calls.append({"room_ref": request.room_ref, "cookie": request.cookie})
            return DouyinTransportState(state="connected", last_error="")

        async def stop(self):
            return DouyinTransportState(state="disconnected")

    runtime.douyin_live_ingest._bridge_transport = _FakeBridgeTransport()

    started = await runtime.live_provider.start_listening("https://live.douyin.com/room-42")
    state = runtime.live_provider.listener_state()

    assert started is True
    assert calls == [{"room_ref": "room-42", "cookie": ""}]
    assert state["state"] == "connected"
    assert state["last_error"] == ""
    assert "secret-cookie" not in json.dumps(state, ensure_ascii=False)


@pytest.mark.asyncio
async def test_douyin_connect_snapshot_exposes_unsupported_bridge_plan(monkeypatch, tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"
    runtime.config.live_room_ref = "room-42"
    runtime.douyin_credential = {"cookie": "ttwid=secret-cookie"}
    runtime.douyin_live_ingest.ctx = runtime

    def fake_fetch(room_ref: str, *, cookie: str = "", timeout: float = 8.0) -> DouyinWebcastInfo:
        return DouyinWebcastInfo(
            ok=True,
            room_ref=room_ref,
            webcast_room_id="7390000000000000000",
            title="morning live",
            anchor_name="anchor",
            live_status="live",
            message="douyin room metadata found",
        )

    monkeypatch.setattr(
        "plugin.plugins.neko_roast.modules.douyin_live_ingest.fetch_webcast_info",
        fake_fetch,
    )

    class _FakeBridgeTransport:
        async def start(self, request):
            return DouyinTransportState(state="unsupported", last_error="manual fake bridge")

        async def stop(self):
            return DouyinTransportState(state="disconnected")

    runtime.douyin_live_ingest._bridge_transport = _FakeBridgeTransport()

    snapshot = await runtime.connect_live_room()

    assert snapshot["connected"] is False
    assert snapshot["state"] == "unsupported"
    assert snapshot["connection_plan"]["webcast_room_id"] == ""
    assert snapshot["connection_plan"]["ready"] is True
    assert snapshot["connection_plan"]["missing"] == []
    assert snapshot["last_error"] == "manual fake bridge"
    dumped = json.dumps(snapshot, ensure_ascii=False)
    assert "secret-cookie" not in dumped


@pytest.mark.asyncio
async def test_douyin_stop_listening_clears_transient_connection_error(monkeypatch, tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"
    runtime.config.live_room_ref = "room-42"
    runtime.douyin_credential = {"cookie": "ttwid=secret-cookie"}
    runtime.douyin_live_ingest.ctx = runtime

    def fake_fetch(room_ref: str, *, cookie: str = "", timeout: float = 8.0) -> DouyinWebcastInfo:
        return DouyinWebcastInfo(
            ok=True,
            room_ref=room_ref,
            webcast_room_id="7390000000000000000",
            title="morning live",
            anchor_name="anchor",
            live_status="live",
            message="douyin room metadata found",
        )

    monkeypatch.setattr(
        "plugin.plugins.neko_roast.modules.douyin_live_ingest.fetch_webcast_info",
        fake_fetch,
    )

    class _FakeBridgeTransport:
        async def start(self, request):
            return DouyinTransportState(state="unsupported", last_error="manual fake bridge")

        async def stop(self):
            return DouyinTransportState(state="disconnected")

    runtime.douyin_live_ingest._bridge_transport = _FakeBridgeTransport()

    await runtime.live_provider.start_listening(runtime.config.live_room_ref)
    assert runtime.live_provider.listener_state()["state"] == "unsupported"
    assert runtime.live_provider.listener_state()["last_error"] == "manual fake bridge"

    await runtime.live_provider.stop_listening()
    snapshot = runtime.live_provider.listener_state()

    assert snapshot["state"] == "disconnected"
    assert snapshot["last_error"] == ""
    assert snapshot["connection_plan"] is None
    assert snapshot["reconnect"]["retry_count"] == 0


def test_douyin_transport_failure_uses_bounded_reconnect_state():
    module = DouyinLiveIngestModule()

    module.mark_transport_failure("ws closed")
    state = module.listener_state()

    assert state["state"] == "reconnecting"
    assert state["reconnect"]["retry_count"] == 1
    assert state["reconnect"]["next_delay_seconds"] == 1.0
    assert state["last_error"] == "ws closed"

    module.mark_transport_failure("heartbeat timeout")
    module.mark_transport_failure("decode failed")
    module.mark_transport_failure("retry limit")
    state = module.listener_state()

    assert state["state"] == "disconnected"
    assert state["reconnect"]["exhausted"] is True
    assert state["reconnect"]["next_delay_seconds"] == 0.0
    assert state["last_error"] == "douyin transport retry limit reached"


def test_douyin_transport_failure_does_not_treat_truthy_object_as_exhausted():
    class _Truthy:
        def __bool__(self) -> bool:
            return True

    module = DouyinLiveIngestModule()
    module._reconnect.exhausted = _Truthy()  # type: ignore[assignment]

    module.mark_transport_failure("ws closed")
    state = module.listener_state()

    assert state["state"] == "reconnecting"
    assert state["reconnect"]["retry_count"] == 1
    assert state["reconnect"]["exhausted"] is False
    assert state["last_error"] == "ws closed"


def test_douyin_transport_failure_last_error_is_redacted():
    module = DouyinLiveIngestModule()

    module.mark_transport_failure(
        "ws failed Cookie: ttwid=secret-cookie; odin_tt=hidden-token "
        "signature=secret-sign token=secret-token"
    )
    state = module.listener_state()
    dumped = json.dumps(state, ensure_ascii=False)

    assert state["state"] == "reconnecting"
    assert "[redacted]" in state["last_error"]
    assert "secret-cookie" not in dumped
    assert "hidden-token" not in dumped
    assert "secret-sign" not in dumped
    assert "secret-token" not in dumped


def test_douyin_status_and_listener_state_redact_internal_state_projection():
    module = DouyinLiveIngestModule()
    module._state = "connected token=must-not-leak"
    module._room_ref = "room-42?cookie=must-not-leak"
    module._last_error = "ws failed token=must-not-leak signature=must-not-leak"
    module._last_event_type = "cookie=must-not-leak"
    module._last_published_event_type = "chat"
    module._last_status_only_event_type = "like"
    module._last_event_at = float("nan")
    module._status_only_count = -3

    status = module.status()
    listener = module.listener_state()
    dumped = json.dumps({"status": status, "listener": listener}, ensure_ascii=False)

    assert status["state"] == "unknown"
    assert listener["state"] == "unknown"
    assert status["room_ref"] == ""
    assert listener["room_ref"] == ""
    assert status["last_error"] == "ws failed [redacted] [redacted]"
    assert listener["last_error"] == "ws failed [redacted] [redacted]"
    assert status["last_event_type"] == "unknown"
    assert status["last_published_event_type"] == "danmaku"
    assert status["last_status_only_event_type"] == "like"
    assert status["last_event_at"] == 0.0
    assert status["status_only_count"] == 0
    assert "must-not-leak" not in dumped


def test_douyin_status_drops_object_state_and_event_type_projection():
    class _LooksLikeConnected:
        def __hash__(self) -> int:
            return hash("connected")

        def __eq__(self, other: object) -> bool:
            return other == "connected"

        def __str__(self) -> str:
            return "connected"

    class _LooksLikeChat:
        def __str__(self) -> str:
            return "chat"

    module = DouyinLiveIngestModule()
    module._state = _LooksLikeConnected()
    module._last_event_type = _LooksLikeChat()
    module._last_published_event_type = _LooksLikeChat()
    module._last_status_only_event_type = _LooksLikeChat()

    status = module.status()
    listener = module.listener_state()

    assert status["state"] == "unknown"
    assert status["listening"] is False
    assert module.is_listening() is False
    assert listener["state"] == "unknown"
    assert status["last_event_type"] == ""
    assert status["last_published_event_type"] == ""
    assert status["last_status_only_event_type"] == ""


@pytest.mark.asyncio
async def test_douyin_provider_router_normalizes_room_ref_before_config_save(tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"

    config = await runtime.set_live_room("https://live.douyin.com/room-42?from=copy")

    assert config.live_room_ref == "room-42"
    assert config.live_room_id == 0
    assert runtime.live_provider.configured_room_ref() == "room-42"


@pytest.mark.asyncio
async def test_douyin_lookup_uses_parser_without_network(tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"

    bad = await runtime.lookup_live_room("https://example.com/not-douyin")

    assert bad["ok"] is False
    assert bad["message"] == "unsupported douyin room url"


@pytest.mark.asyncio
async def test_douyin_lookup_audit_uses_safe_normalized_room_ref(monkeypatch, tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"
    runtime.douyin_credential = {"cookie": "ttwid=secret-cookie"}
    runtime.douyin_live_ingest.ctx = runtime

    def fake_fetch(room_ref: str, *, cookie: str = "", timeout: float = 8.0) -> DouyinWebcastInfo:
        return DouyinWebcastInfo(
            ok=True,
            room_ref=room_ref,
            webcast_room_id="7390000000000000000",
            title="morning live",
            anchor_name="anchor",
            live_status="live",
            message="douyin room metadata found",
        )

    monkeypatch.setattr(
        "plugin.plugins.neko_roast.modules.douyin_live_ingest.fetch_webcast_info",
        fake_fetch,
    )

    result = await runtime.lookup_live_room("https://live.douyin.com/room-42?cookie=must-not-leak")

    assert result["ok"] is True
    record = next(item for item in runtime.audit.recent(10) if item["op"] == "live_room_lookup")
    assert record["detail"]["room_ref"] == "room-42"
    assert "must-not-leak" not in json.dumps(runtime.audit.recent(10), ensure_ascii=False)


@pytest.mark.asyncio
async def test_douyin_lookup_audit_does_not_store_invalid_raw_room_ref(tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"

    result = await runtime.lookup_live_room("https://example.com/not-douyin?cookie=must-not-leak")

    assert result["ok"] is False
    record = next(item for item in runtime.audit.recent(10) if item["op"] == "live_room_lookup")
    assert record["detail"]["room_ref"] == ""
    assert "must-not-leak" not in json.dumps(runtime.audit.recent(10), ensure_ascii=False)


@pytest.mark.asyncio
async def test_douyin_lookup_uses_webcast_fetch_result(monkeypatch, tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"
    calls: list[dict[str, str]] = []

    def fake_fetch(room_ref: str, *, cookie: str = "", timeout: float = 8.0) -> DouyinWebcastInfo:
        calls.append({"room_ref": room_ref, "cookie": cookie, "timeout": str(timeout)})
        return DouyinWebcastInfo(
            ok=True,
            room_ref=room_ref,
            webcast_room_id="7390000000000000000",
            title="morning live",
            anchor_name="anchor",
            live_status="live",
            message="douyin room metadata found",
        )

    monkeypatch.setattr(
        "plugin.plugins.neko_roast.modules.douyin_live_ingest.fetch_webcast_info",
        fake_fetch,
    )
    runtime.douyin_credential = {"cookie": "ttwid=secret-cookie"}
    runtime.douyin_live_ingest.ctx = runtime

    result = await runtime.lookup_live_room("https://live.douyin.com/room-42")

    assert result["ok"] is True
    assert result["platform"] == "douyin"
    assert result["room_ref"] == "room-42"
    assert result["room_id"] == 7390000000000000000
    assert result["title"] == "morning live"
    assert result["anchor_name"] == "anchor"
    assert result["live_status"] == "live"
    assert calls == [{"room_ref": "room-42", "cookie": "ttwid=secret-cookie", "timeout": "8.0"}]
    assert "secret-cookie" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.asyncio
async def test_douyin_provider_event_reaches_live_events_without_bili_types():
    ctx = _LiveEventsCtx()
    ingest = DouyinLiveIngestModule()
    hub = LiveEventsModule()
    await hub.setup(ctx)
    await ingest.setup(ctx)

    live_event = ingest.publish_provider_event(
        {
            "event_type": "chat",
            "uid": "123",
            "nickname": "viewer",
            "text": "hello from douyin",
            "room_ref": "room-42",
            "webcast_room_id": "7390000000000000000",
            "cookie": "must-not-leak",
        }
    )
    await _drain(hub)

    assert live_event is not None
    assert live_event.type == "danmaku"
    assert live_event.uid == "douyin:123"
    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["uid"] == "douyin:123"
    assert ctx.payloads[0]["event_type"] == "danmaku"
    assert ctx.payloads[0]["danmaku_text"] == "hello from douyin"
    assert ctx.payloads[0]["room_id"] == 7390000000000000000
    assert ctx.payloads[0]["room_ref"] == "room-42"
    assert "cookie" not in live_event.payload
    assert ingest.status()["last_event_type"] == "danmaku"
    assert ingest.status()["last_published_event_type"] == "danmaku"
    assert ingest.status()["last_status_only_event_type"] == ""


@pytest.mark.asyncio
async def test_douyin_transport_event_reaches_live_events_through_safe_boundary():
    ctx = _LiveEventsCtx()
    ingest = DouyinLiveIngestModule()
    hub = LiveEventsModule()
    await hub.setup(ctx)
    await ingest.setup(ctx)

    live_event = ingest.publish_transport_event(
        DouyinTransportEvent(
            {
                "event_type": "chat",
                "uid": "123",
                "nickname": "viewer",
                "text": "hello from transport",
                "room_ref": "room-42",
                "webcast_room_id": "7390000000000000000",
                "cookie": "must-not-leak",
                "protobuf": b"must-not-leak",
            },
            ts=123.5,
        )
    )
    await _drain(hub)

    assert live_event is not None
    assert live_event.type == "danmaku"
    assert live_event.ts == 123.5
    assert live_event.uid == "douyin:123"
    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["danmaku_text"] == "hello from transport"
    assert ctx.payloads[0]["room_ref"] == "room-42"
    assert ingest.status()["last_event_at"] == 123.5
    assert "must-not-leak" not in json.dumps(live_event.to_dict(), ensure_ascii=False)
    assert "must-not-leak" not in json.dumps(ingest.status(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_douyin_transport_event_rejects_non_transport_event_without_stringifying():
    ctx = _LiveEventsCtx()
    ingest = DouyinLiveIngestModule()
    hub = LiveEventsModule()
    await hub.setup(ctx)
    await ingest.setup(ctx)

    live_event = ingest.publish_transport_event({"event_type": "chat", "text": "hello"})  # type: ignore[arg-type]
    await _drain(hub)

    assert live_event is None
    assert ctx.payloads == []
    assert ingest.status()["last_event_type"] == "unknown"


@pytest.mark.asyncio
async def test_douyin_provider_event_drops_non_dict_payloads():
    ctx = _LiveEventsCtx()
    ingest = DouyinLiveIngestModule()
    hub = LiveEventsModule()
    await hub.setup(ctx)
    await ingest.setup(ctx)

    ingest._room_ref = "room-42"
    live_event = ingest.publish_provider_event([("event_type", "chat"), ("text", "hello")])
    await _drain(hub)

    assert live_event is None
    assert ctx.payloads == []
    assert ingest.status()["last_event_type"] == "unknown"
    assert ingest.status()["last_published_event_type"] == ""


@pytest.mark.asyncio
async def test_douyin_gift_event_reaches_live_events_as_safe_signal_only():
    ctx = _LiveEventsCtx()
    ingest = DouyinLiveIngestModule()
    hub = LiveEventsModule()
    support = LiveSupportEventsModule()
    await hub.setup(ctx)
    await support.setup(ctx)
    await ingest.setup(ctx)

    live_event = ingest.publish_provider_event(
        {
            "event_type": "gift",
            "uid": "123",
            "nickname": "viewer",
            "room_ref": "room-42",
            "giftName": "small heart",
            "num": 3,
            "total_coin": 900,
            "cookie": "must-not-leak",
            "signature": "must-not-leak",
            "protobuf": b"must-not-leak",
        }
    )
    await _drain(hub)
    await _drain_support(support)

    assert live_event is not None
    assert live_event.type == "gift"
    assert live_event.uid == "douyin:123"
    assert live_event.payload["gift_name"] == "small heart"
    assert live_event.payload["gift_count"] == 3
    assert live_event.payload["gift_value"] == 900
    assert len(ctx.payloads) == 1
    assert ctx.payloads[0]["trace_id"].startswith("tr_")
    expected_payload = {
        "uid": "douyin:123",
        "nickname": "viewer",
        "danmaku_text": "",
        "avatar_url": "",
        "room_id": 0,
        "room_ref": "room-42",
        "event_type": "gift",
        "gift_name": "small heart",
        "gift_count": 3,
        "gift_value": 900,
    }
    assert {key: ctx.payloads[0].get(key) for key in expected_payload} == expected_payload
    dumped = json.dumps(live_event.to_dict(), ensure_ascii=False)
    assert "must-not-leak" not in dumped
    await support.teardown()


@pytest.mark.asyncio
async def test_douyin_gift_support_event_enters_pipeline_without_raw_leak(tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"
    calls: list[ViewerEvent] = []

    async def record_call(event):
        calls.append(event)
        return InteractionResult(
            accepted=True,
            status="dry_run",
            event=event,
            steps=[PipelineStep("live_support_events", "dry_run")],
        )

    runtime.pipeline.handle_event = record_call

    result = await runtime.handle_live_payload(
        {
            "event_type": "gift",
            "uid": "123",
            "nickname": "viewer",
            "gift_name": "small heart",
            "gift_count": 3,
            "gift_value": 900,
            "cookie": "must-not-leak",
        }
    )

    assert len(calls) == 1
    assert result.status == "dry_run"
    assert result.event.uid == "douyin:123"
    assert result.event.raw["gift_name"] == "small heart"
    assert result.steps[0].id == "live_support_events"
    assert "must-not-leak" not in json.dumps(result.to_public_dict(), ensure_ascii=False)
    assert runtime.plugin.pushed_messages == []


@pytest.mark.parametrize("event_type", ["member", "follow", "like", "stats"])
@pytest.mark.asyncio
async def test_douyin_status_only_events_do_not_publish_to_live_events(event_type):
    ctx = _LiveEventsCtx()
    ingest = DouyinLiveIngestModule()
    hub = LiveEventsModule()
    await hub.setup(ctx)
    await ingest.setup(ctx)

    live_event = ingest.publish_provider_event(
        {
            "event_type": event_type,
            "uid": "123",
            "nickname": "viewer",
            "text": "status only",
            "room_ref": "room-42",
            "cookie": "must-not-leak",
        }
    )
    await _drain(hub)

    assert live_event is None
    assert ctx.payloads == []
    assert ctx.event_bus.status()["publish_count"] == 0
    status = ingest.status()
    assert status["last_event_type"] == event_type
    assert status["last_event_at"] > 0
    assert status["last_published_event_type"] == ""
    assert status["last_status_only_event_type"] == event_type
    assert status["status_only_count"] == 1
    assert "must-not-leak" not in json.dumps(status, ensure_ascii=False)

@pytest.mark.asyncio
async def test_douyin_dashboard_health_distinguishes_status_only_from_published(tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.config.live_platform = "douyin"
    runtime.douyin_live_ingest.ctx = runtime

    runtime.douyin_live_ingest.publish_provider_event(
        {
            "event_type": "like",
            "uid": "123",
            "room_ref": "room-42",
            "cookie": "must-not-leak",
            "signature": "must-not-leak",
        }
    )
    state = await runtime.dashboard_state()

    rows = {row["id"]: row for row in state["health_rows"]}
    assert rows["live_ingest"]["last_outcome"] == "like"
    assert rows["live_ingest"]["last_status_only_outcome"] == "like"
    assert rows["live_ingest"]["last_published_outcome"] == ""
    assert rows["event_bus"]["count"] == 0
    assert "must-not-leak" not in json.dumps(state, ensure_ascii=False)

    runtime.douyin_live_ingest.publish_provider_event(
        {
            "event_type": "chat",
            "uid": "123",
            "text": "hello",
            "room_ref": "room-42",
            "cookie": "must-not-leak",
            "protobuf": b"must-not-leak",
        }
    )
    state = await runtime.dashboard_state()

    rows = {row["id"]: row for row in state["health_rows"]}
    assert rows["live_ingest"]["last_outcome"] == "danmaku"
    assert rows["live_ingest"]["last_status_only_outcome"] == "like"
    assert rows["live_ingest"]["last_published_outcome"] == "danmaku"
    assert rows["event_bus"]["last_outcome"] == "danmaku"
    assert "must-not-leak" not in json.dumps(state, ensure_ascii=False)


@pytest.mark.asyncio
async def test_douyin_provider_event_requires_safe_room_ref_before_publish():
    ctx = _LiveEventsCtx()
    ingest = DouyinLiveIngestModule()
    hub = LiveEventsModule()
    await hub.setup(ctx)
    await ingest.setup(ctx)

    live_event = ingest.publish_provider_event(
        {
            "event_type": "chat",
            "uid": "123",
            "text": "must not publish",
            "room_ref": "room-42?cookie=must-not-leak",
            "cookie": "must-not-leak",
        }
    )
    await _drain(hub)

    assert live_event is None
    assert ctx.payloads == []
    assert ctx.event_bus.status()["publish_count"] == 0
    status = ingest.status()
    assert status["last_error"] == "douyin room_ref is required before publishing events"
    assert "must-not-leak" not in json.dumps(status, ensure_ascii=False)


@pytest.mark.asyncio
async def test_douyin_unknown_event_type_is_not_published_or_leaked():
    ctx = _LiveEventsCtx()
    ingest = DouyinLiveIngestModule()
    hub = LiveEventsModule()
    await hub.setup(ctx)
    await ingest.setup(ctx)

    live_event = ingest.publish_provider_event(
        {
            "event_type": "cookie=must-not-leak",
            "uid": "123",
            "text": "must not publish",
            "room_ref": "room-42",
        }
    )
    await _drain(hub)

    assert live_event is None
    assert ctx.payloads == []
    assert ctx.event_bus.status()["publish_count"] == 0
    status = ingest.status()
    assert status["last_event_type"] == "unknown"
    assert "must-not-leak" not in json.dumps(status, ensure_ascii=False)
