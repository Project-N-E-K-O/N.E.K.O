import asyncio
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_live.adapters.bili_auth_service import BiliAuthService
from plugin.plugins.neko_live.adapters.neko_dispatcher import NekoDispatcher
from plugin.plugins.neko_live.core.contracts import (
    InteractionRequest,
    RoastConfig,
    SafetyDecision,
    ViewerEvent,
    ViewerIdentity,
    ViewerProfile,
)
from plugin.plugins.neko_live.core.permission_gate import PermissionGate
from plugin.plugins.neko_live.core.pipeline import RoastPipeline
from plugin.plugins.neko_live.core.pipeline_failure_results import fail_dispatcher
from plugin.plugins.neko_live.core.safety_guard import SafetyGuard
from plugin.plugins.neko_live.modules.bili_identity import BiliIdentityModule


def test_safety_config_update_preserves_in_flight_count():
    audit = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    guard = SafetyGuard(RoastConfig(queue_limit=5), audit)
    guard.queue_size = 4

    guard.update(RoastConfig(queue_limit=2))

    assert guard.queue_size == 4


def test_safety_snapshot_prunes_expired_failure_records(monkeypatch):
    audit = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    guard = SafetyGuard(RoastConfig(safety_window_seconds=10), audit)
    guard._pipeline_failures = [80.0, 95.0]
    guard._output_failures = [70.0]
    monkeypatch.setattr(
        "plugin.plugins.neko_live.core.safety_guard_failures.time.monotonic",
        lambda: 100.0,
    )

    snapshot = guard.snapshot()

    assert snapshot["pipeline_failures"] == 1
    assert snapshot["output_failures"] == 0


def test_dispatcher_failure_exposes_only_stable_exception_type():
    records = []
    ctx = SimpleNamespace(
        safety_guard=SimpleNamespace(record_failure=lambda kind, message: records.append((kind, message))),
        record_result=lambda result: records.append(result),
    )
    event = ViewerEvent(uid="1")
    identity = ViewerIdentity(uid="1", nickname="viewer")
    profile = ViewerProfile(uid="1", nickname="viewer")
    request = InteractionRequest(
        event=event,
        identity=identity,
        profile=profile,
        prompt_text="prompt",
        live_mode="co_stream",
        strength="normal",
    )
    steps = []

    result = fail_dispatcher(
        ctx,
        event,
        identity,
        profile,
        request,
        steps,
        RuntimeError("authorization: Bearer secret-token"),
    )

    assert result.reason == "output_failed:RuntimeError"
    assert "secret-token" not in str(records)


@pytest.mark.asyncio
async def test_dispatcher_respects_non_deliverable_request():
    class Plugin:
        def push_message(self, **_kwargs):
            raise AssertionError("non-deliverable requests must not be pushed")

    event = ViewerEvent(uid="1", nickname="tester")
    identity = ViewerIdentity(uid="1", nickname="tester")
    profile = ViewerProfile(uid="1", nickname="tester")
    request = InteractionRequest(
        event=event,
        identity=identity,
        profile=profile,
        prompt_text="nope",
        live_mode="co_stream",
        strength="normal",
        should_push=False,
        reason="upstream skip",
    )

    result = await NekoDispatcher(Plugin()).push_roast(request)

    assert result == "skipped_to_neko(reason=upstream skip)"


@pytest.mark.asyncio
async def test_bili_login_check_none_state_stays_waiting():
    class Events:
        NONE = object()
        SCAN = object()
        CONF = object()
        TIMEOUT = object()
        DONE = object()

    class Session:
        async def check_state(self):
            return Events.NONE

    service = BiliAuthService(
        credential_provider=lambda: None,
        credential_saver=lambda _payload: True,
        credential_reloader=lambda: None,
    )
    service._login_session = Session()
    service._login_generated_at = 0.0
    service._require_login_sdk = lambda: (object, Events)

    result = await service.login_check()

    assert result["status"] == "waiting"


@pytest.mark.asyncio
async def test_bili_login_check_clears_session_when_credential_save_fails():
    class Events:
        NONE = object()
        SCAN = object()
        CONF = object()
        TIMEOUT = object()
        DONE = object()

    class Credential:
        sessdata = "sess"
        bili_jct = "jct"
        dedeuserid = "42"
        buvid3 = "buvid"

    class Session:
        async def check_state(self):
            return Events.DONE

        def get_credential(self):
            return Credential()

    cleanup_calls = 0

    async def save_fails(_payload):
        return False

    async def no_credential():
        return None

    async def reload_unused():
        raise AssertionError("credential reload should not run after save failure")

    def cleanup():
        nonlocal cleanup_calls
        cleanup_calls += 1

    service = BiliAuthService(
        credential_provider=no_credential,
        credential_saver=save_fails,
        credential_reloader=reload_unused,
        cleanup_callback=cleanup,
    )
    service._login_session = Session()
    service._require_login_sdk = lambda: (object, Events)

    with pytest.raises(RuntimeError):
        await service.login_check()

    assert service._login_session is None
    assert cleanup_calls == 1


@pytest.mark.asyncio
async def test_pipeline_once_per_uid_gate_is_atomic_for_concurrent_events():
    class Audit:
        def __init__(self):
            self.records = []

        def record(self, op, message="", level="info", detail=None):
            self.records.append(
                {
                    "op": op,
                    "message": message,
                    "level": level,
                    "detail": detail or {},
                }
            )

    class Safety:
        def before_event(self, _event):
            return SafetyDecision(True)

        def before_output(self, _event):
            return SafetyDecision(True)

        def after_event(self):
            return None

        def record_failure(self, _kind, _message):
            return None

    class ViewerProfileModule:
        def __init__(self):
            self.roasted = set()

        async def upsert(self, identity):
            return ViewerProfile(
                uid=identity.uid,
                nickname=identity.nickname,
                avatar_url=identity.avatar_url,
            )

        async def has_roasted(self, uid):
            return uid in self.roasted

        async def mark_roasted(self, uid, _output):
            self.roasted.add(uid)

    class Dispatcher:
        def __init__(self):
            self.calls = 0

        async def push_roast(self, _request):
            self.calls += 1
            await asyncio.sleep(0)
            return "queued_to_neko(test)"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="test",
                live_mode=event.live_mode,
                strength="normal",
            )

    config = RoastConfig(live_enabled=True, roast_once_per_uid=True)
    ctx = SimpleNamespace(
        audit=Audit(),
        config=config,
        permission_gate=PermissionGate(config),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(
            resolve=lambda event: asyncio.sleep(
                0,
                result=ViewerIdentity(uid=event.uid, nickname=event.nickname),
            )
        ),
        viewer_profile=ViewerProfileModule(),
        avatar_roast=AvatarRoast(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append
    pipeline = RoastPipeline(ctx)
    event = ViewerEvent(
        uid="42",
        nickname="same",
        danmaku_text="hi",
        source="live_danmaku",
    )

    first, second = await asyncio.gather(
        pipeline.handle_event(event),
        pipeline.handle_event(event),
    )

    statuses = sorted([first.status, second.status])
    assert statuses == ["pushed", "skipped"]
    assert ctx.dispatcher.calls == 1


@pytest.mark.parametrize("failure_mode", ("returns_false", "raises"))
@pytest.mark.asyncio
async def test_pipeline_mark_roasted_failure_keeps_success_result(
    failure_mode: str,
):
    class Audit:
        def __init__(self):
            self.records = []

        def record(self, op, message="", level="info", detail=None):
            self.records.append(
                {
                    "op": op,
                    "message": message,
                    "level": level,
                    "detail": detail or {},
                }
            )

    class Safety:
        def before_event(self, _event):
            return SafetyDecision(True)

        def before_output(self, _event):
            return SafetyDecision(True)

        def after_event(self):
            return None

        def record_failure(self, _kind, _message):
            return None

    class ViewerProfileModule:
        async def upsert(self, identity):
            return ViewerProfile(
                uid=identity.uid,
                nickname=identity.nickname,
                avatar_url=identity.avatar_url,
            )

        async def has_roasted(self, _uid):
            return False

        async def mark_roasted(self, _uid, _output):
            if failure_mode == "raises":
                raise OSError("disk full")
            return False

    class Dispatcher:
        async def push_roast(self, _request):
            return "queued_to_neko(test)"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="test",
                live_mode=event.live_mode,
                strength="normal",
            )

    config = RoastConfig(live_enabled=True, roast_once_per_uid=True)
    ctx = SimpleNamespace(
        audit=Audit(),
        config=config,
        permission_gate=PermissionGate(config),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(
            resolve=lambda event: asyncio.sleep(
                0,
                result=ViewerIdentity(uid=event.uid, nickname=event.nickname),
            )
        ),
        viewer_profile=ViewerProfileModule(),
        avatar_roast=AvatarRoast(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append

    result = await RoastPipeline(ctx).handle_event(
        ViewerEvent(
            uid="42",
            nickname="same",
            danmaku_text="hi",
            source="live_danmaku",
        )
    )

    assert result.status == "pushed"
    assert any(
        step.id == "viewer_profile.mark_roasted" and step.status == "failed"
        for step in result.steps
    )
    assert any(
        record["op"] == "viewer_profile_mark_failed"
        for record in ctx.audit.records
    )


@pytest.mark.asyncio
async def test_bili_identity_avatar_fetch_tolerates_ctx_release():
    module = BiliIdentityModule()

    class Cache:
        def get(self, _key):
            return None

        def put(self, _key, _data, _mime):
            raise AssertionError("cache should not be accessed after ctx release")

    module.ctx = SimpleNamespace(
        avatar_cache=Cache(),
        config=SimpleNamespace(avatar_fetch_timeout_seconds=1),
        audit=SimpleNamespace(record=lambda *args, **kwargs: None),
    )

    def _fetch_avatar(_url, _timeout):
        module.ctx = None
        return b"avatar", "image/png"

    module._fetch_avatar = _fetch_avatar
    module._inspect_avatar = lambda _data: (True, False)

    identity = await module.resolve(
        ViewerEvent(
            uid="7",
            nickname="七",
            avatar_url="https://example.test/a.png",
        )
    )

    assert identity.avatar_bytes == b"avatar"
    assert identity.avatar_mime == "image/png"


@pytest.mark.asyncio
async def test_bili_identity_skips_avatar_download_when_analysis_is_disabled():
    module = BiliIdentityModule()
    module.ctx = SimpleNamespace(
        avatar_cache=SimpleNamespace(
            get=lambda _key: (_ for _ in ()).throw(
                AssertionError("avatar cache must not be read")
            )
        ),
        config=SimpleNamespace(
            avatar_analysis_enabled=False,
            avatar_fetch_timeout_seconds=1,
        ),
        audit=SimpleNamespace(record=lambda *args, **kwargs: None),
    )
    module._fetch_avatar = lambda *_args: (_ for _ in ()).throw(
        AssertionError("avatar must not be downloaded")
    )

    identity = await module.resolve(
        ViewerEvent(
            uid="7",
            nickname="viewer",
            avatar_url="https://example.test/a.png",
        )
    )

    assert identity.avatar_url == "https://example.test/a.png"
    assert identity.avatar_bytes is None


@pytest.mark.asyncio
async def test_bili_identity_ignores_undecodable_avatar_bytes():
    module = BiliIdentityModule()
    module.ctx = SimpleNamespace(
        avatar_cache=SimpleNamespace(get=lambda _key: None, put=lambda *_args: None),
        config=SimpleNamespace(avatar_fetch_timeout_seconds=1),
        audit=SimpleNamespace(record=lambda *args, **kwargs: None),
    )
    module._fetch_avatar = lambda _url, _timeout: (
        b"<html>not image</html>",
        "text/html",
    )

    identity = await module.resolve(
        ViewerEvent(
            uid="7",
            nickname="viewer",
            avatar_url="https://example.test/a.png",
        )
    )

    assert identity.avatar_bytes is None
    assert identity.avatar_vision_ok is False
    assert "avatar_fetch_failed: ValueError" in identity.error


def test_bili_identity_rejects_private_avatar_url():
    with pytest.raises(ValueError):
        BiliIdentityModule._fetch_avatar(
            "http://127.0.0.1/avatar.png",
            timeout=1,
        )


def test_bili_identity_avatar_fetch_uses_validated_resolved_ip(monkeypatch):
    opened = {}

    def fake_getaddrinfo(host, port, type=0):
        assert host == "cdn.example.test"
        assert port == 8443
        return [(None, None, None, "", ("8.8.8.8", port))]

    class Response:
        status = 200

        def read(self, _limit):
            return b"png"

        def getheader(self, name):
            return "image/png" if name == "content-type" else ""

    class Connection:
        def request(self, method, path, headers):
            opened["method"] = method
            opened["path"] = path
            opened["host"] = headers["Host"]

        def getresponse(self):
            return Response()

        def close(self):
            opened["closed"] = True

    def fake_open(parsed, resolved_ip, port, timeout):
        opened["hostname"] = parsed.hostname
        opened["resolved_ip"] = resolved_ip
        opened["port"] = port
        opened["timeout"] = timeout
        return Connection()

    monkeypatch.setattr(
        "plugin.plugins.neko_live.modules.bili_identity.socket.getaddrinfo",
        fake_getaddrinfo,
    )
    monkeypatch.setattr(
        BiliIdentityModule,
        "_open_avatar_connection",
        staticmethod(fake_open),
    )

    data, mime = BiliIdentityModule._fetch_avatar(
        "https://cdn.example.test:8443/avatar.png?size=small",
        timeout=3,
    )

    assert data == b"png"
    assert mime == "image/png"
    assert opened == {
        "hostname": "cdn.example.test",
        "resolved_ip": "8.8.8.8",
        "port": 8443,
        "timeout": 3,
        "method": "GET",
        "path": "/avatar.png?size=small",
        "host": "cdn.example.test:8443",
        "closed": True,
    }
