import asyncio
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_live.adapters.bili_auth_service import BiliAuthService
from plugin.plugins.neko_live.adapters.neko_dispatcher import NekoDispatcher
from plugin.plugins.neko_live.core.contracts import (
    InteractionRequest,
    LiveConfig,
    SafetyDecision,
    ViewerEvent,
    ViewerIdentity,
    ViewerProfile,
)
from plugin.plugins.neko_live.core.permission_gate import PermissionGate
from plugin.plugins.neko_live.core.pipeline import LivePipeline
from plugin.plugins.neko_live.core.pipeline_failure_results import fail_dispatcher
from plugin.plugins.neko_live.core.safety_guard import SafetyGuard
from plugin.plugins.neko_live.modules import bili_identity as bili_identity_module
from plugin.plugins.neko_live.modules.bili_identity import BiliIdentityModule


def test_safety_config_update_preserves_in_flight_count():
    audit = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    guard = SafetyGuard(LiveConfig(queue_limit=5), audit)
    guard.queue_size = 4

    guard.update(LiveConfig(queue_limit=2))

    assert guard.queue_size == 4


def test_safety_snapshot_prunes_expired_failure_records(monkeypatch, patch_module_clock):
    audit = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    guard = SafetyGuard(LiveConfig(safety_window_seconds=10), audit)
    guard._pipeline_failures = [80.0, 95.0]
    guard._output_failures = [70.0]
    # 打到真正读时钟的模块上：snapshot() 的过期裁剪在 safety_guard_failures 里
    from plugin.plugins.neko_live.core import safety_guard_failures

    patch_module_clock(monkeypatch, safety_guard_failures, monotonic=lambda: 100.0)

    snapshot = guard.snapshot()

    assert snapshot["pipeline_failures"] == 1
    assert snapshot["output_failures"] == 0


def test_safety_auto_stop_trips_once_for_an_in_flight_failure_burst(
    monkeypatch,
    patch_module_clock,
):
    records = []
    audit = SimpleNamespace(
        record=lambda op, message="", **kwargs: records.append(
            {"op": op, "message": message, **kwargs}
        )
    )
    guard = SafetyGuard(
        LiveConfig(
            safety_window_seconds=10,
            safety_pipeline_failure_limit=2,
        ),
        audit,
    )
    from plugin.plugins.neko_live.core import safety_guard_failures

    ticks = iter((100.0, 101.0, 102.0, 103.0))
    patch_module_clock(
        monkeypatch,
        safety_guard_failures,
        monotonic=lambda: next(ticks),
    )

    guard.record_failure("pipeline", "pipeline_failed: first")
    guard.record_failure("pipeline", "pipeline_failed: threshold")
    guard.record_failure("pipeline", "pipeline_failed: already_tripped")

    assert guard.status() == "tripped"
    assert guard.snapshot()["pipeline_failures"] == 3
    assert [record["op"] for record in records].count("safety_auto_stop") == 1


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


class _CredentialCheckStub:
    dedeuserid = "42"

    def __init__(self, *, valid: bool = True) -> None:
        self.valid = valid
        self.validity_checks = 0

    async def check_valid(self) -> bool:
        self.validity_checks += 1
        return self.valid


def _bili_auth_for_credential(credential: object) -> BiliAuthService:
    async def provide_credential() -> object:
        return credential

    return BiliAuthService(
        credential_provider=provide_credential,
        credential_saver=lambda _payload: True,
        credential_reloader=lambda: None,
    )


@pytest.mark.asyncio
async def test_bili_credential_normal_profile_success_does_not_add_validity_request(
    monkeypatch: pytest.MonkeyPatch,
):
    from bilibili_api import user as bili_user_module

    credential = _CredentialCheckStub()

    class User:
        def __init__(self, *, uid: int, credential: object) -> None:
            assert uid == 42
            assert credential is not None

        async def get_user_info(self) -> dict[str, str]:
            return {"name": "alice"}

    monkeypatch.setattr(bili_user_module, "User", User)

    result = await _bili_auth_for_credential(credential).check_credential()

    assert result["logged_in"] is True
    assert result["username"] == "alice"
    assert credential.validity_checks == 0


@pytest.mark.asyncio
async def test_bili_credential_profile_failure_falls_back_to_valid_credential(
    monkeypatch: pytest.MonkeyPatch,
):
    from bilibili_api import user as bili_user_module

    credential = _CredentialCheckStub(valid=True)

    class User:
        def __init__(self, *, uid: int, credential: object) -> None:
            assert uid == 42
            assert credential is not None

        async def get_user_info(self) -> dict[str, str]:
            raise RuntimeError("temporary profile outage with private detail")

    monkeypatch.setattr(bili_user_module, "User", User)

    result = await _bili_auth_for_credential(credential).check_credential()

    assert result == {
        "logged_in": True,
        "uid": "42",
        "username": "",
        "message": "credential valid; account profile temporarily unavailable",
    }
    assert credential.validity_checks == 1
    assert "private detail" not in repr(result)


@pytest.mark.asyncio
async def test_bili_credential_profile_failure_rejects_invalid_credential(
    monkeypatch: pytest.MonkeyPatch,
):
    from bilibili_api import user as bili_user_module

    credential = _CredentialCheckStub(valid=False)

    class User:
        def __init__(self, *, uid: int, credential: object) -> None:
            assert uid == 42
            assert credential is not None

        async def get_user_info(self) -> dict[str, str]:
            raise RuntimeError("profile unavailable")

    monkeypatch.setattr(bili_user_module, "User", User)

    result = await _bili_auth_for_credential(credential).check_credential()

    assert result == {
        "logged_in": False,
        "message": "credential may be invalid; please login again",
    }
    assert credential.validity_checks == 1


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

    config = LiveConfig(live_enabled=True, roast_once_per_uid=True)
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
    pipeline = LivePipeline(ctx)
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

    config = LiveConfig(live_enabled=True, roast_once_per_uid=True)
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

    result = await LivePipeline(ctx).handle_event(
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
async def test_bili_identity_skips_profile_lookup_for_avatar_when_analysis_is_disabled():
    module = BiliIdentityModule()
    module.ctx = SimpleNamespace(
        config=SimpleNamespace(
            avatar_analysis_enabled=False,
            avatar_roast_enabled=True,
            avatar_fetch_timeout_seconds=1,
        ),
        audit=SimpleNamespace(record=lambda *args, **kwargs: None),
    )

    async def fail_profile(_uid: str) -> dict:
        raise AssertionError("disabled avatar analysis must not fetch an avatar URL")

    module._fetch_profile_by_uid = fail_profile  # type: ignore[method-assign]

    identity = await module.resolve(
        ViewerEvent(uid="7", nickname="viewer", source="live_danmaku")
    )

    assert identity.nickname == "viewer"
    assert identity.avatar_url == ""


@pytest.mark.asyncio
async def test_bili_identity_support_resolution_skips_unneeded_profile_and_image_fetch():
    module = BiliIdentityModule()
    module.ctx = SimpleNamespace(
        avatar_cache=SimpleNamespace(
            get=lambda _key: (_ for _ in ()).throw(
                AssertionError("avatar cache must not be read")
            )
        ),
        config=SimpleNamespace(
            avatar_analysis_enabled=True,
            avatar_roast_enabled=True,
            avatar_fetch_timeout_seconds=1,
        ),
        audit=SimpleNamespace(record=lambda *args, **kwargs: None),
    )

    async def fail_profile(_uid: str) -> dict:
        raise AssertionError("complete support identity must not fetch profile")

    module._fetch_profile_by_uid = fail_profile  # type: ignore[method-assign]
    module._fetch_avatar = lambda *_args: (_ for _ in ()).throw(
        AssertionError("support event must not download avatar")
    )

    identity = await module.resolve(
        ViewerEvent(uid="7", nickname="supporter", source="live_danmaku"),
        fetch_avatar_image=False,
    )

    assert identity.nickname == "supporter"
    assert identity.avatar_bytes is None


@pytest.mark.asyncio
async def test_bili_identity_support_resolution_may_fetch_name_without_image_bytes():
    module = BiliIdentityModule()
    module.ctx = SimpleNamespace(
        avatar_cache=SimpleNamespace(
            get=lambda _key: (_ for _ in ()).throw(
                AssertionError("avatar cache must not be read")
            )
        ),
        config=SimpleNamespace(
            avatar_analysis_enabled=True,
            avatar_roast_enabled=True,
            avatar_fetch_timeout_seconds=1,
        ),
        audit=SimpleNamespace(record=lambda *args, **kwargs: None),
    )
    profile_calls = 0

    async def fetch_profile(_uid: str) -> dict:
        nonlocal profile_calls
        profile_calls += 1
        return {
            "name": "supporter",
            "face": "https://i0.hdslb.com/avatar.png",
        }

    module._fetch_profile_by_uid = fetch_profile  # type: ignore[method-assign]
    module._fetch_bili_avatar = lambda *_args: (_ for _ in ()).throw(
        AssertionError("support event must not download avatar")
    )

    identity = await module.resolve(
        ViewerEvent(uid="7", nickname="", source="live_danmaku"),
        fetch_avatar_image=False,
    )

    assert profile_calls == 1
    assert identity.nickname == "supporter"
    assert identity.avatar_url == "https://i0.hdslb.com/avatar.png"
    assert identity.avatar_bytes is None


@pytest.mark.asyncio
async def test_bili_identity_profile_hint_cache_is_bounded_public_and_expires(
    monkeypatch: pytest.MonkeyPatch,
    patch_module_clock,
):
    clock = [100.0]
    patch_module_clock(
        monkeypatch,
        bili_identity_module,
        monotonic=lambda: clock[0],
    )
    module = BiliIdentityModule()
    module.ctx = SimpleNamespace(
        config=SimpleNamespace(
            avatar_analysis_enabled=True,
            avatar_roast_enabled=True,
            avatar_fetch_timeout_seconds=1,
        ),
        audit=SimpleNamespace(record=lambda *args, **kwargs: None),
    )
    profile_calls = 0

    async def fetch_profile(uid: str) -> dict:
        nonlocal profile_calls
        profile_calls += 1
        return {
            "name": f"viewer-{uid}",
            "face": f"https://i0.hdslb.com/{uid}.png",
            "pendant": "public-pendant",
            "email": "must-not-be-cached@example.invalid",
        }

    module._fetch_profile_by_uid = fetch_profile  # type: ignore[method-assign]

    first = await module.resolve(
        ViewerEvent(uid="7", nickname="", source="live_danmaku"),
        fetch_avatar_image=False,
    )
    second = await module.resolve(
        ViewerEvent(uid="7", nickname="", source="live_danmaku"),
        fetch_avatar_image=False,
    )

    assert first.nickname == second.nickname == "viewer-7"
    assert profile_calls == 1
    assert module.status()["profile_hint_cache"] == {
        "items": 1,
        "max_items": 128,
        "ttl_seconds": 900.0,
        "hits": 1,
        "misses": 1,
    }
    assert "email" not in next(iter(module._profile_hints.values()))[1]

    clock[0] += 901.0
    await module.resolve(
        ViewerEvent(uid="7", nickname="", source="live_danmaku"),
        fetch_avatar_image=False,
    )
    assert profile_calls == 2

    for index in range(128):
        await module.resolve(
            ViewerEvent(uid=str(1000 + index), nickname="", source="live_danmaku"),
            fetch_avatar_image=False,
        )
    assert len(module._profile_hints) == 128
    assert "7" not in module._profile_hints


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


@pytest.mark.asyncio
async def test_bili_identity_routes_bili_cdn_avatar_through_proxy_client():
    module = BiliIdentityModule()
    module.ctx = SimpleNamespace(
        avatar_cache=SimpleNamespace(get=lambda _key: None, put=lambda *_args: None),
        config=SimpleNamespace(avatar_fetch_timeout_seconds=1),
        audit=SimpleNamespace(record=lambda *args, **kwargs: None),
    )
    module._fetch_avatar = lambda *_args: (_ for _ in ()).throw(
        AssertionError("Bilibili CDN avatars must use the proxy-aware path")
    )

    async def fetch_bili_avatar(url, timeout):
        assert url == "https://i0.hdslb.com/bfs/face/avatar.jpg"
        assert timeout == 1
        return b"avatar", "image/jpeg"

    module._fetch_bili_avatar = fetch_bili_avatar
    module._inspect_avatar = lambda _data: (True, False)

    identity = await module.resolve(
        ViewerEvent(
            uid="7",
            nickname="viewer",
            avatar_url="https://i0.hdslb.com/bfs/face/avatar.jpg",
        )
    )

    assert identity.avatar_bytes == b"avatar"
    assert identity.avatar_mime == "image/jpeg"
    assert identity.avatar_vision_ok is True


@pytest.mark.asyncio
async def test_bili_identity_proxy_fetch_streams_allowlisted_avatar(monkeypatch):
    opened = {}

    class Response:
        status_code = 200
        url = "https://i0.hdslb.com/bfs/face/avatar.jpg"
        headers = {"content-type": "image/jpeg", "content-length": "6"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_bytes(self):
            yield b"avatar"

    class Client:
        def stream(self, method, url, **kwargs):
            opened.update(method=method, url=url, kwargs=kwargs)
            return Response()

    monkeypatch.setattr(
        "plugin.plugins.neko_live.modules.bili_identity._external_http_client",
        lambda: Client(),
    )

    data, mime = await BiliIdentityModule._fetch_bili_avatar(
        "https://i0.hdslb.com/bfs/face/avatar.jpg",
        timeout=3,
    )

    assert data == b"avatar"
    assert mime == "image/jpeg"
    assert opened["method"] == "GET"
    assert opened["url"] == "https://i0.hdslb.com/bfs/face/avatar.jpg"
    assert opened["kwargs"]["timeout"] == 3
    assert opened["kwargs"]["follow_redirects"] is False


@pytest.mark.asyncio
async def test_bili_identity_proxy_fetch_rejects_private_redirect(monkeypatch):
    class Response:
        status_code = 302
        url = "https://i0.hdslb.com/bfs/face/avatar.jpg"
        headers = {"location": "http://127.0.0.1/private.png"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class Client:
        def stream(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(
        "plugin.plugins.neko_live.modules.bili_identity._external_http_client",
        lambda: Client(),
    )

    with pytest.raises(ValueError, match="avatar_redirect_not_allowed"):
        await BiliIdentityModule._fetch_bili_avatar(
            "https://i0.hdslb.com/bfs/face/avatar.jpg",
            timeout=3,
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/avatar.png",
        "https://hdslb.com.attacker.example/avatar.png",
        "https://user@i0.hdslb.com/avatar.png",
        "https://i0.hdslb.com:8080/avatar.png",
    ],
)
def test_bili_identity_proxy_allowlist_rejects_unsafe_urls(url):
    assert BiliIdentityModule._is_bili_avatar_url(url) is False
