import asyncio
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_roast.adapters.bili_auth_service import BiliAuthService
from plugin.plugins.neko_roast.adapters.neko_dispatcher import NekoDispatcher
from plugin.plugins.neko_roast.core.contracts import InteractionRequest, RoastConfig, SafetyDecision, ViewerEvent, ViewerIdentity, ViewerProfile, utc_now_iso
from plugin.plugins.neko_roast.core.module_registry import ModuleRegistry
from plugin.plugins.neko_roast.core.permission_gate import PermissionGate
from plugin.plugins.neko_roast.core.pipeline import RoastPipeline
from plugin.plugins.neko_roast.modules.bili_identity import BiliIdentityModule


def test_roast_config_defaults_to_dry_run_for_real_room_safety():
    assert RoastConfig().dry_run is True
    assert RoastConfig.from_mapping({}).dry_run is True
    assert RoastConfig.from_mapping(None).dry_run is True


def test_roast_config_preserves_explicit_dry_run_false_for_real_output_window():
    assert RoastConfig.from_mapping({"dry_run": False}).dry_run is False


def test_roast_config_preserves_explicit_avatar_timeout_zero():
    assert RoastConfig.from_mapping({"avatar_fetch_timeout_seconds": 0}).avatar_fetch_timeout_seconds == 0


def test_utc_now_iso_returns_timezone_aware_utc_timestamp():
    assert utc_now_iso().endswith("+00:00")


def test_viewer_identity_public_dict_does_not_expose_email():
    public = ViewerIdentity(uid="1", nickname="tester", email="private@example.test").to_public_dict()

    assert "email" not in public


def test_permission_gate_requires_developer_tools_for_sandbox():
    gate = PermissionGate(RoastConfig(developer_tools_enabled=False))

    allowed, reason = gate.allows_source("developer_sandbox")

    assert allowed is False
    assert reason == "developer tools are disabled"

    gate.update(RoastConfig(developer_tools_enabled=True))
    assert gate.allows_source("developer_sandbox") == (True, "")


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
async def test_module_toggle_failure_keeps_previous_state_and_success_clears_degraded():
    class Module:
        id = "demo"
        title = "Demo"
        version = "1"
        enabled = False
        domain = "interaction"
        fail = True

        async def setup(self, ctx):
            return None

        async def teardown(self):
            return None

        async def on_enable(self, ctx):
            if self.fail:
                raise RuntimeError("boom")

        async def on_disable(self):
            return None

        def status(self):
            return {}

        def config_schema(self):
            return []

    module = Module()
    registry = ModuleRegistry()
    registry.register(module)

    assert await registry.enable("demo", ctx=None) is False
    assert module.enabled is False
    assert registry.is_degraded("demo") is True

    module.fail = False
    assert await registry.enable("demo", ctx=None) is True
    assert module.enabled is True
    assert registry.is_degraded("demo") is False


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
async def test_pipeline_once_per_uid_gate_is_atomic_for_concurrent_events():
    class Audit:
        def __init__(self):
            self.records = []

        def record(self, op, message="", level="info", detail=None):
            self.records.append({"op": op, "message": message, "level": level, "detail": detail or {}})

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
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

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

    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=ViewerProfileModule(),
        avatar_roast=AvatarRoast(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append
    pipeline = RoastPipeline(ctx)
    event = ViewerEvent(uid="42", nickname="same", danmaku_text="hi", source="live_danmaku")

    first, second = await asyncio.gather(pipeline.handle_event(event), pipeline.handle_event(event))

    statuses = sorted([first.status, second.status])
    assert statuses == ["pushed", "skipped"]
    assert ctx.dispatcher.calls == 1


@pytest.mark.asyncio
async def test_pipeline_mark_roasted_failure_keeps_success_result():
    class Audit:
        def __init__(self):
            self.records = []

        def record(self, op, message="", level="info", detail=None):
            self.records.append({"op": op, "message": message, "level": level, "detail": detail or {}})

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
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, _uid):
            return False

        async def mark_roasted(self, _uid, _output):
            raise OSError("disk full")

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

    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=ViewerProfileModule(),
        avatar_roast=AvatarRoast(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append

    result = await RoastPipeline(ctx).handle_event(
        ViewerEvent(uid="42", nickname="same", danmaku_text="hi", source="live_danmaku")
    )

    assert result.status == "pushed"
    assert any(step.id == "viewer_profile.mark_roasted" and step.status == "failed" for step in result.steps)
    assert any(record["op"] == "viewer_profile_mark_failed" for record in ctx.audit.records)


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

    identity = await module.resolve(ViewerEvent(uid="7", nickname="七", avatar_url="https://example.test/a.png"))

    assert identity.avatar_bytes == b"avatar"
    assert identity.avatar_mime == "image/png"


@pytest.mark.asyncio
async def test_bili_identity_ignores_undecodable_avatar_bytes():
    module = BiliIdentityModule()
    module.ctx = SimpleNamespace(
        avatar_cache=SimpleNamespace(get=lambda _key: None, put=lambda *_args: None),
        config=SimpleNamespace(avatar_fetch_timeout_seconds=1),
        audit=SimpleNamespace(record=lambda *args, **kwargs: None),
    )
    module._fetch_avatar = lambda _url, _timeout: (b"<html>not image</html>", "text/html")

    identity = await module.resolve(ViewerEvent(uid="7", nickname="viewer", avatar_url="https://example.test/a.png"))

    assert identity.avatar_bytes is None
    assert identity.avatar_vision_ok is False
    assert "avatar_fetch_failed: ValueError" in identity.error


def test_bili_identity_rejects_private_avatar_url():
    with pytest.raises(ValueError):
        BiliIdentityModule._fetch_avatar("http://127.0.0.1/avatar.png", timeout=1)
