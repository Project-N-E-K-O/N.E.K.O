import asyncio
from types import SimpleNamespace

import pytest

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
from plugin.plugins.neko_live.core.pipeline_session import PipelineSessionTracker


@pytest.mark.asyncio
async def test_pipeline_session_releases_idle_uid_lock_entry():
    session = PipelineSessionTracker()

    lock = await session.acquire_uid_lock("42")
    assert session._uid_locks

    session.release_uid_lock("42", lock)

    assert session._uid_locks == {}


@pytest.mark.asyncio
async def test_pipeline_session_keeps_one_lock_for_waiting_same_uid_tasks():
    session = PipelineSessionTracker()
    first = await session.acquire_uid_lock("42")
    waiting = asyncio.create_task(session.acquire_uid_lock("42"))
    await asyncio.sleep(0)

    assert not waiting.done()
    session.release_uid_lock("42", first)

    second = await asyncio.wait_for(waiting, timeout=1)
    assert second is first
    session.release_uid_lock("42", second)
    assert session._uid_locks == {}


def test_live_status_offline_gate_only_allows_verified_support_signals():
    ctx = SimpleNamespace(
        live_connection_snapshot=lambda: {"connected": True},
        live_status_summary=lambda _snapshot: {
            "summary": "cannot_stream",
            "reason": "live_room_offline",
            "can_output": False,
            "live_status": "offline",
        },
    )
    pipeline = RoastPipeline(ctx)

    normal = ViewerEvent(
        uid="1",
        nickname="u1",
        danmaku_text="hello",
        source="live_danmaku",
        raw={"event_type": "danmaku"},
    )
    gift = ViewerEvent(
        uid="2",
        nickname="u2",
        source="live_danmaku",
        raw={"event_type": "gift", "gift_name": "Small Heart"},
    )

    blocked = pipeline._live_status_gate_decision(normal)
    assert blocked is not None
    assert blocked.reason == "live_status.live_room_offline"
    assert pipeline._live_status_gate_decision(gift) is None


@pytest.mark.asyncio
async def test_pipeline_records_dry_run_as_dispatcher_outcome_not_pushed():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

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
            return None

    class Dispatcher:
        async def push_roast(self, _request):
            return "dry_run(target=none, ai_behavior=respond)"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="test",
                live_mode=event.live_mode,
                strength="normal",
                dry_run=True,
            )

    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True, dry_run=True),
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
        ViewerEvent(uid="42", nickname="dry", danmaku_text="hi", source="live_danmaku")
    )

    assert result.status == "dry_run"
    assert result.accepted is False
    assert ctx.results[0].status == "dry_run"
    assert not any(step.id == "viewer_profile.mark_roasted" for step in result.steps)


@pytest.mark.asyncio
async def test_pipeline_skips_first_danmaku_when_roast_and_followup_modules_are_disabled():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

    class Safety:
        def before_event(self, _event):
            return SafetyDecision(True)

        def after_event(self):
            return None

    class ViewerProfileModule:
        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname)

        async def has_roasted(self, _uid):
            return False

    config = RoastConfig(
        live_enabled=True,
        avatar_roast_enabled=False,
        danmaku_response_enabled=False,
    )
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
        results=[],
    )
    ctx.record_result = ctx.results.append

    result = await RoastPipeline(ctx).handle_event(
        ViewerEvent(
            uid="42",
            nickname="disabled",
            danmaku_text="hello",
            source="live_danmaku",
        )
    )

    assert result.status == "skipped"
    assert result.reason == "danmaku_response.disabled"
    assert any(step.id == "module_gate" for step in result.steps)
    assert ctx.results == [result]

@pytest.mark.asyncio
async def test_pipeline_public_result_profile_reflects_successful_first_roast():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

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
            return None

    class Dispatcher:
        async def push_roast(self, _request):
            return "queued_to_neko(first roast)"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="first roast",
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
        ViewerEvent(uid="42", nickname="first", danmaku_text="hi", source="live_danmaku")
    )

    assert result.status == "pushed"
    assert result.profile is not None
    assert result.profile.roast_count == 1
    assert result.profile.last_result == "queued_to_neko(first roast)"
    assert result.dispatcher_latency_ms is not None
    assert result.dispatcher_latency_ms >= 0
    public_result = result.to_public_dict()
    assert public_result["profile"]["roast_count"] == 1
    assert public_result["profile"]["last_result"] == "queued_to_neko(first roast)"
    assert public_result["dispatcher_latency_ms"] is not None

@pytest.mark.asyncio
async def test_pipeline_records_dispatcher_skip_as_skipped_not_pushed():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

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
            return None

    class Dispatcher:
        async def push_roast(self, _request):
            return "skipped_to_neko(reason=non-deliverable)"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="test",
                live_mode=event.live_mode,
                strength="normal",
                should_push=False,
                reason="non-deliverable",
            )

    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True, dry_run=False),
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
        ViewerEvent(uid="42", nickname="skip", danmaku_text="hi", source="live_danmaku")
    )

    assert result.status == "skipped"
    assert result.accepted is False
    assert result.reason == "non-deliverable"
    assert ctx.results[0].status == "skipped"
    assert not any(step.id == "viewer_profile.mark_roasted" for step in result.steps)

@pytest.mark.asyncio
async def test_pipeline_routes_repeat_live_danmaku_to_danmaku_response():
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
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url, roast_count=1)

        async def has_roasted(self, _uid):
            return True

        async def mark_roasted(self, _uid, _output):
            raise AssertionError("repeat danmaku responses must not mark avatar roast")

    class Dispatcher:
        def __init__(self):
            self.requests = []

        async def push_roast(self, request):
            self.requests.append(request)
            return "queued_to_neko(danmaku_response)"

    class AvatarRoast:
        def build_request(self, *_args):
            raise AssertionError("repeat danmaku must not use avatar_roast")

    class DanmakuResponse:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"reply to: {event.danmaku_text}",
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
        danmaku_response=DanmakuResponse(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append

    result = await RoastPipeline(ctx).handle_event(
        ViewerEvent(uid="42", nickname="same", danmaku_text="还在吗", source="live_danmaku", live_mode="solo_stream")
    )

    assert result.status == "pushed"
    assert result.request is not None
    assert result.request.prompt_text == "reply to: 还在吗"
    assert result.request.allow_avatar_image is False
    assert any(step.id == "viewer_gate" and step.status == "ok" and step.message == "repeat_danmaku" for step in result.steps)
    assert any(step.id == "danmaku_response" and step.status == "ok" for step in result.steps)
    assert not any(step.id == "viewer_profile.mark_roasted" for step in result.steps)
    assert ctx.dispatcher.requests == [result.request]
    assert ctx.results == [result]

@pytest.mark.asyncio
async def test_pipeline_paces_consecutive_solo_first_roasts_to_danmaku_response():
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
            self.mark_calls = []

        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, uid):
            return uid in self.roasted

        async def mark_roasted(self, uid, _output):
            self.mark_calls.append(uid)
            self.roasted.add(uid)

    class Dispatcher:
        def __init__(self):
            self.requests = []

        async def push_roast(self, request):
            self.requests.append(request)
            return f"queued_to_neko({request.prompt_text})"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"avatar_roast:{event.uid}",
                live_mode=event.live_mode,
                strength="normal",
            )

    class DanmakuResponse:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"danmaku_response:{event.danmaku_text}",
                live_mode=event.live_mode,
                strength="normal",
            )

    viewer_profile = ViewerProfileModule()
    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=viewer_profile,
        avatar_roast=AvatarRoast(),
        danmaku_response=DanmakuResponse(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append
    pipeline = RoastPipeline(ctx)
    now = [100.0]
    pipeline._now = lambda: now[0]

    first = await pipeline.handle_event(ViewerEvent(uid="42", nickname="first", danmaku_text="第一次来", source="live_danmaku", live_mode="solo_stream"))
    now[0] += 10.0
    second = await pipeline.handle_event(ViewerEvent(uid="77", nickname="second", danmaku_text="猫猫在吗", source="live_danmaku", live_mode="solo_stream"))

    assert first.status == "pushed"
    assert second.status == "pushed"
    assert first.request is not None
    assert second.request is not None
    assert first.request.prompt_text == "avatar_roast:42"
    assert second.request.prompt_text == "danmaku_response:猫猫在吗"
    assert any(step.id == "viewer_gate" and step.status == "ok" and step.message == "entrance_pacing" for step in second.steps)
    assert any(step.id == "danmaku_response" and step.status == "ok" for step in second.steps)
    assert viewer_profile.mark_calls == ["42", "77"]

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
            self.mark_calls = 0

        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, uid):
            return uid in self.roasted

        async def mark_roasted(self, uid, _output):
            self.mark_calls += 1
            self.roasted.add(uid)

    class Dispatcher:
        def __init__(self):
            self.calls = 0
            self.prompts = []

        async def push_roast(self, request):
            self.calls += 1
            self.prompts.append(request.prompt_text)
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

    class DanmakuResponse:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="danmaku response",
                live_mode=event.live_mode,
                strength="normal",
            )

    viewer_profile = ViewerProfileModule()
    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=viewer_profile,
        avatar_roast=AvatarRoast(),
        danmaku_response=DanmakuResponse(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append
    pipeline = RoastPipeline(ctx)
    event = ViewerEvent(uid="42", nickname="same", danmaku_text="hi", source="live_danmaku")

    first, second = await asyncio.gather(pipeline.handle_event(event), pipeline.handle_event(event))

    statuses = sorted([first.status, second.status])
    assert statuses == ["pushed", "pushed"]
    assert ctx.dispatcher.calls == 2
    assert sorted(ctx.dispatcher.prompts) == ["danmaku response", "test"]
    assert viewer_profile.mark_calls == 1
    assert any(step.id == "danmaku_response" and step.status == "ok" for step in second.steps)

@pytest.mark.asyncio
async def test_pipeline_dry_run_repeat_live_danmaku_uses_session_first_roast_marker():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

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
            self.mark_calls = 0

        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, _uid):
            return False

        async def mark_roasted(self, _uid, _output):
            self.mark_calls += 1

    class Dispatcher:
        def __init__(self):
            self.requests = []

        async def push_roast(self, request):
            self.requests.append(request)
            return f"dry_run({request.prompt_text})"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="avatar_roast",
                live_mode=event.live_mode,
                strength="normal",
                dry_run=True,
            )

    class DanmakuResponse:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="danmaku_response",
                live_mode=event.live_mode,
                strength="normal",
                dry_run=True,
            )

    viewer_profile = ViewerProfileModule()
    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True, dry_run=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True, dry_run=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=viewer_profile,
        avatar_roast=AvatarRoast(),
        danmaku_response=DanmakuResponse(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append
    pipeline = RoastPipeline(ctx)

    first = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="first", source="live_danmaku"))
    second = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="second", source="live_danmaku"))

    assert first.status == "dry_run"
    assert second.status == "dry_run"
    assert any(step.id == "avatar_roast" and step.status == "ok" for step in first.steps)
    assert any(step.id == "viewer_gate" and step.status == "ok" and step.message == "repeat_danmaku" for step in second.steps)
    assert any(step.id == "danmaku_response" and step.status == "ok" for step in second.steps)
    assert not any(step.id == "viewer_profile.mark_roasted" for step in first.steps + second.steps)
    assert viewer_profile.mark_calls == 0

@pytest.mark.asyncio
async def test_pipeline_dry_run_session_marker_can_be_cleared_for_fresh_validation():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

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
            raise AssertionError("dry_run must not persist first-roast state")

    class Dispatcher:
        async def push_roast(self, request):
            return f"dry_run({request.prompt_text})"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="avatar_roast",
                live_mode=event.live_mode,
                strength="normal",
                dry_run=True,
            )

    class DanmakuResponse:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="danmaku_response",
                live_mode=event.live_mode,
                strength="normal",
                dry_run=True,
            )

    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True, dry_run=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True, dry_run=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=ViewerProfileModule(),
        avatar_roast=AvatarRoast(),
        danmaku_response=DanmakuResponse(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append
    pipeline = RoastPipeline(ctx)

    first = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="first", source="live_danmaku"))
    pipeline.clear_dry_run_session_state()
    second = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="fresh first", source="live_danmaku"))

    assert first.status == "dry_run"
    assert second.status == "dry_run"
    assert any(step.id == "avatar_roast" and step.status == "ok" for step in second.steps)
    assert not any(step.id == "danmaku_response" for step in second.steps)

def test_pipeline_session_state_clear_resets_entrance_pacing_marker():
    pipeline = RoastPipeline(SimpleNamespace())
    now = [100.0]
    pipeline._now = lambda: now[0]

    pipeline._record_avatar_roast_sent()
    now[0] += 10.0
    assert pipeline._entrance_pacing_active() is True

    pipeline.clear_dry_run_session_state()

    assert pipeline._entrance_pacing_active() is False

def test_pipeline_entrance_pacing_interval_follows_activity_level():
    now = [100.0]

    quiet_pipeline = RoastPipeline(SimpleNamespace(config=RoastConfig(activity_level="quiet")))
    quiet_pipeline._now = lambda: now[0]
    quiet_pipeline._record_avatar_roast_sent()
    now[0] += 50.0
    assert quiet_pipeline._entrance_pacing_active() is True

    now[0] = 100.0
    active_pipeline = RoastPipeline(SimpleNamespace(config=RoastConfig(activity_level="active")))
    active_pipeline._now = lambda: now[0]
    active_pipeline._record_avatar_roast_sent()
    now[0] += 35.0
    assert active_pipeline._entrance_pacing_active() is False

@pytest.mark.asyncio
async def test_pipeline_session_marker_prevents_repeat_avatar_roast_when_persist_fails():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

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
            self.mark_calls = 0

        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, _uid):
            return False

        async def mark_roasted(self, _uid, _output):
            self.mark_calls += 1
            raise RuntimeError("store temporarily unavailable")

    class Dispatcher:
        async def push_roast(self, request):
            return f"queued_to_neko({request.prompt_text})"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"avatar_roast:{event.danmaku_text}",
                live_mode=event.live_mode,
                strength="normal",
            )

    class DanmakuResponse:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"danmaku_response:{event.danmaku_text}",
                live_mode=event.live_mode,
                strength="normal",
            )

    viewer_profile = ViewerProfileModule()
    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=viewer_profile,
        avatar_roast=AvatarRoast(),
        danmaku_response=DanmakuResponse(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append
    pipeline = RoastPipeline(ctx)

    first = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="first", source="live_danmaku"))
    second = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="second", source="live_danmaku"))

    assert first.status == "pushed"
    assert second.status == "pushed"
    assert first.request is not None
    assert second.request is not None
    assert first.request.prompt_text == "avatar_roast:first"
    assert second.request.prompt_text == "danmaku_response:second"
    assert viewer_profile.mark_calls == 1
    assert any(step.id == "viewer_gate" and step.status == "ok" and step.message == "repeat_danmaku" for step in second.steps)

@pytest.mark.asyncio
async def test_pipeline_routes_same_session_followup_to_danmaku_response_even_when_once_disabled():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

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
            return None

    class Dispatcher:
        async def push_roast(self, request):
            return f"queued_to_neko({request.prompt_text})"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"avatar_roast:{event.danmaku_text}",
                live_mode=event.live_mode,
                strength="normal",
            )

    class DanmakuResponse:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"danmaku_response:{event.danmaku_text}",
                live_mode=event.live_mode,
                strength="normal",
            )

    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=False),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=False)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=ViewerProfileModule(),
        avatar_roast=AvatarRoast(),
        danmaku_response=DanmakuResponse(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append
    pipeline = RoastPipeline(ctx)

    first = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="first", source="live_danmaku"))
    second = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="second", source="live_danmaku"))

    assert first.status == "pushed"
    assert second.status == "pushed"
    assert first.request is not None
    assert second.request is not None
    assert first.request.prompt_text == "avatar_roast:first"
    assert second.request.prompt_text == "danmaku_response:second"
    assert any(step.id == "viewer_gate" and step.status == "ok" and step.message == "repeat_danmaku" for step in second.steps)

@pytest.mark.asyncio
async def test_pipeline_avatar_roast_attempt_prevents_repeat_avatar_roast_when_dispatcher_fails():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

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
            self.mark_calls = 0

        async def upsert(self, identity):
            return ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)

        async def has_roasted(self, _uid):
            return False

        async def mark_roasted(self, _uid, _output):
            self.mark_calls += 1

    class Dispatcher:
        def __init__(self):
            self.calls = 0

        async def push_roast(self, request):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary output failure")
            return f"queued_to_neko({request.prompt_text})"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"avatar_roast:{event.danmaku_text}",
                live_mode=event.live_mode,
                strength="normal",
            )

    class DanmakuResponse:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"danmaku_response:{event.danmaku_text}",
                live_mode=event.live_mode,
                strength="normal",
            )

    viewer_profile = ViewerProfileModule()
    dispatcher = Dispatcher()
    ctx = SimpleNamespace(
        audit=Audit(),
        config=RoastConfig(live_enabled=True, roast_once_per_uid=True),
        permission_gate=PermissionGate(RoastConfig(live_enabled=True, roast_once_per_uid=True)),
        safety_guard=Safety(),
        bili_identity=SimpleNamespace(resolve=lambda event: asyncio.sleep(0, result=ViewerIdentity(uid=event.uid, nickname=event.nickname))),
        viewer_profile=viewer_profile,
        avatar_roast=AvatarRoast(),
        danmaku_response=DanmakuResponse(),
        dispatcher=dispatcher,
        results=[],
    )
    ctx.record_result = ctx.results.append
    pipeline = RoastPipeline(ctx)

    first = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="first", source="live_danmaku"))
    second = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="second", source="live_danmaku"))

    assert first.status == "failed"
    assert second.status == "pushed"
    assert first.request is not None
    assert second.request is not None
    assert first.request.prompt_text == "avatar_roast:first"
    assert second.request.prompt_text == "danmaku_response:second"
    assert viewer_profile.mark_calls == 0
    assert any(step.id == "viewer_gate" and step.status == "ok" and step.message == "repeat_danmaku" for step in second.steps)

@pytest.mark.asyncio
async def test_pipeline_avatar_roast_attempt_prevents_repeat_avatar_roast_when_output_gate_blocks():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

    class Safety:
        def __init__(self):
            self.output_checks = 0

        def before_event(self, _event):
            return SafetyDecision(True)

        def before_output(self, _event):
            self.output_checks += 1
            if self.output_checks == 1:
                return SafetyDecision(False, "safety.blocked")
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
            return None

    class Dispatcher:
        async def push_roast(self, request):
            return f"queued_to_neko({request.prompt_text})"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"avatar_roast:{event.danmaku_text}",
                live_mode=event.live_mode,
                strength="normal",
            )

    class DanmakuResponse:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text=f"danmaku_response:{event.danmaku_text}",
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
        danmaku_response=DanmakuResponse(),
        dispatcher=Dispatcher(),
        results=[],
    )
    ctx.record_result = ctx.results.append
    pipeline = RoastPipeline(ctx)

    first = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="first", source="live_danmaku"))
    second = await pipeline.handle_event(ViewerEvent(uid="42", nickname="same", danmaku_text="second", source="live_danmaku"))

    assert first.status == "skipped"
    assert second.status == "pushed"
    assert first.request is not None
    assert second.request is not None
    assert first.request.prompt_text == "avatar_roast:first"
    assert second.request.prompt_text == "danmaku_response:second"
    assert any(step.id == "viewer_gate.session_claim" and step.status == "ok" for step in first.steps)
    assert any(step.id == "viewer_gate" and step.status == "ok" and step.message == "repeat_danmaku" for step in second.steps)

@pytest.mark.asyncio
async def test_pipeline_records_idle_hosting_as_own_route():
    class Audit:
        def record(self, *_args, **_kwargs):
            return None

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
            return None

    class Dispatcher:
        async def push_roast(self, _request):
            return "queued_to_neko(idle_hosting)"

    class AvatarRoast:
        def build_request(self, event, identity, profile):
            return InteractionRequest(
                event=event,
                identity=identity,
                profile=profile,
                prompt_text="idle hosting prompt",
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
        ViewerEvent(uid="__neko_idle__", nickname="NEKO", source="idle_hosting", live_mode="solo_stream")
    )

    assert result.status == "pushed"
    assert any(step.id == "idle_hosting" and step.status == "ok" for step in result.steps)
    assert not any(step.id == "avatar_roast" for step in result.steps)

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
