"""Regression tests for LLMSessionManager.request_fresh_screenshot.

Regression protection: some frontend capture paths (Electron main-process captureSourceAsDataUrl) return
native-resolution images whose base64 reaches about 1.4 MB. Returning these unchanged through WebSocket makes Phase 2
send them to the vision LLM, where proxy nginx rejects them with 413 Request Entity Too Large.
Both request_fresh_screenshot return paths must normalize to 720p/JPEG-80.
"""
import asyncio
import base64
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from main_logic.core import LLMSessionManager


def _png_b64(width: int, height: int) -> str:
    """Generate a PNG at the specified dimensions and return plain base64 without a data: prefix."""
    img = Image.new("RGB", (width, height))
    # Add a gradient resembling a real screenshot; solid-color JPEGs compress almost to zero.
    px = img.load()
    for y in range(0, height, 4):
        for x in range(0, width, 4):
            px[x, y] = ((x * 255) // width, (y * 255) // height, 128)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


@pytest.mark.unit
def test_ws_screenshot_is_downscaled_to_720p():
    """Normalize native-resolution WebSocket screenshots to 720p JPEG."""
    big_b64 = _png_b64(2560, 1440)  # Simulate a native 1440p image captured directly by Electron's main process.

    async def _run() -> str:
        fake = SimpleNamespace(_screenshot_future=None, lanlan_name="桃奈")

        async def _send_json(payload):
            # send_json is awaited after create_future and before wait_for;
            # resolve the future directly here to simulate the frontend returning a screenshot.
            assert payload['type'] == 'request_screenshot'
            assert payload['request_id']
            LLMSessionManager.resolve_screenshot_request(
                fake, big_b64, request_id=payload['request_id'],
            )

        fake.websocket = SimpleNamespace(send_json=_send_json)
        return (await LLMSessionManager.request_fresh_screenshot(fake, timeout=3.0)).b64

    out_b64 = asyncio.run(_run())

    assert out_b64, "应返回非空 base64"
    out_img = Image.open(BytesIO(base64.b64decode(out_b64)))
    # Must be normalized to 720p JPEG rather than retaining the original 1440p PNG.
    assert out_img.format == "JPEG"
    assert out_img.height == 720
    # Bytes should be well below nginx's default 1 MB body limit, the trigger for 413.
    assert len(base64.b64decode(out_b64)) < 1024 * 1024


@pytest.mark.unit
def test_ws_screenshot_already_small_stays_under_limit():
    """Small images already within 720p must return normally without enlargement."""
    small_b64 = _png_b64(1280, 720)

    async def _run() -> str:
        fake = SimpleNamespace(_screenshot_future=None, lanlan_name="桃奈")

        async def _send_json(payload):
            LLMSessionManager.resolve_screenshot_request(fake, small_b64)

        fake.websocket = SimpleNamespace(send_json=_send_json)
        return (await LLMSessionManager.request_fresh_screenshot(fake, timeout=3.0)).b64

    out_b64 = asyncio.run(_run())

    assert out_b64
    out_img = Image.open(BytesIO(base64.b64decode(out_b64)))
    assert out_img.format == "JPEG"
    assert out_img.height <= 720
    assert len(base64.b64decode(out_b64)) < 1024 * 1024


@pytest.mark.unit
@pytest.mark.parametrize('echo_id', [True, False])
@pytest.mark.parametrize(
    ('reply_kind', 'allow_fallback', 'local', 'expected_capture', 'expected_declined'),
    [
        ('declined', True, True, False, True),
        ('declined', False, True, False, True),
        ('empty', True, True, True, False),
        ('timeout', True, True, True, False),
        ('empty', False, True, False, False),
        ('timeout', False, True, False, False),
        ('empty', True, False, False, False),
    ],
)
def test_declines_and_window_bounds_preserve_only_legal_fallback(
    monkeypatch, echo_id, reply_kind, allow_fallback, local, expected_capture, expected_declined,
):
    from unittest.mock import AsyncMock, Mock
    from main_logic import core as core_facade
    from main_logic.core import proactive

    read = AsyncMock(return_value={'proactiveVisionEnabled': True})
    capture = Mock(return_value=Image.new('RGB', (320, 180), (20, 80, 180)))
    monkeypatch.setattr(core_facade, 'aload_global_conversation_settings', read)
    monkeypatch.setattr(proactive, 'capture_desktop_screenshot', capture)

    async def run():
        fake = SimpleNamespace(_screenshot_future=None, lanlan_name='Repro')

        async def send(_payload):
            if reply_kind != 'timeout':
                # Even a decline carrying nonempty data must not revive that image.
                LLMSessionManager.resolve_screenshot_request(
                    fake, 'IGNORE_THIS_IMAGE' if reply_kind == 'declined' else '',
                    declined=reply_kind == 'declined',
                    request_id=_payload['request_id'] if echo_id else None,
                )

        fake.websocket = SimpleNamespace(
            send_json=send,
            client=SimpleNamespace(host='127.0.0.1' if local else '192.0.2.1'),
        )
        result = await LLMSessionManager.request_fresh_screenshot(
            fake, timeout=0.01, allow_backend_fallback=allow_fallback,
        )
        assert fake._screenshot_future is None
        return result

    fresh = asyncio.run(run())
    assert fresh.declined is expected_declined
    assert capture.call_count == int(expected_capture)
    assert bool(fresh.b64) is expected_capture
    assert all(call.kwargs == {'strict': True} for call in read.await_args_list)
    if expected_capture:
        assert fresh.source == 'backend_fallback'
        assert Image.open(BytesIO(base64.b64decode(fresh.b64))).format == 'JPEG'


@pytest.mark.unit
@pytest.mark.parametrize('when', ['before', 'frontend_image', 'frontend_empty', 'backend_capture', 'backend_capture_error', 'read_error'])
def test_privacy_changes_and_read_errors_do_not_return_images(monkeypatch, when):
    from unittest.mock import Mock
    from main_logic import core as core_facade
    from main_logic.core import proactive

    allowed = when != 'before'

    async def read(*, strict=False):
        assert strict is True
        if when == 'read_error':
            raise OSError('synthetic preference read error')
        return {'proactiveVisionEnabled': allowed}

    def capture():
        nonlocal allowed
        if when in ('backend_capture', 'backend_capture_error'):
            allowed = False
        if when == 'backend_capture_error':
            raise OSError('synthetic capture error after privacy changed')
        return Image.new('RGB', (320, 180), (180, 80, 20))

    capture_mock = Mock(side_effect=capture)
    monkeypatch.setattr(core_facade, 'aload_global_conversation_settings', read)
    monkeypatch.setattr(proactive, 'capture_desktop_screenshot', capture_mock)
    sent = []

    async def run():
        fake = SimpleNamespace(_screenshot_future=None, lanlan_name='Repro')

        async def send(payload):
            nonlocal allowed
            sent.append(payload)
            if when in ('frontend_image', 'frontend_empty'):
                allowed = False
            LLMSessionManager.resolve_screenshot_request(
                fake, _png_b64(320, 180) if when == 'frontend_image' else '',
            )

        fake.websocket = SimpleNamespace(send_json=send, client=SimpleNamespace(host='127.0.0.1'))
        return await LLMSessionManager.request_fresh_screenshot(fake, timeout=0.01)

    fresh = asyncio.run(run())
    assert fresh.declined is True
    assert fresh.b64 == ''
    assert capture_mock.call_count == int(when in ('backend_capture', 'backend_capture_error'))
    assert len(sent) == int(when not in ('before', 'read_error'))


@pytest.mark.unit
@pytest.mark.parametrize('value,expected', [(True, True), (False, False), (None, False), ('true', False)])
def test_desktop_fallback_command_requires_a_boolean(value, expected):
    from main_logic.proactive_chat.contracts import ProactiveChatCommand

    assert ProactiveChatCommand.from_payload({}).screenshot_allow_desktop_fallback is True
    command = ProactiveChatCommand.from_payload({'screenshot_allow_desktop_fallback': value})
    assert command.screenshot_allow_desktop_fallback is expected


@pytest.mark.unit
@pytest.mark.parametrize('value', [False, True, None, 'false', 0])
def test_live_consent_is_strict_and_revocation_remains_visible_after_reenable(value):
    manager = SimpleNamespace(_proactive_vision_enabled=True, _proactive_vision_revocation_epoch=0)
    LLMSessionManager.update_proactive_vision_consent(manager, value)
    assert manager._proactive_vision_enabled is (False if value is False else True)
    assert manager._proactive_vision_revocation_epoch == int(value is False)
    LLMSessionManager.update_proactive_vision_consent(manager, False)
    LLMSessionManager.update_proactive_vision_consent(manager, False)
    LLMSessionManager.update_proactive_vision_consent(manager, True)
    assert manager._proactive_vision_revocation_epoch == 1
    assert manager._proactive_vision_enabled is True


@pytest.mark.unit
@pytest.mark.parametrize('reply_kind', ['before', 'empty', 'image', 'backend_capture'])
def test_live_revocation_blocks_capture_and_return_before_settings_persist(monkeypatch, reply_kind):
    from unittest.mock import AsyncMock, Mock
    from main_logic import core as core_facade
    from main_logic.core import proactive

    manager = SimpleNamespace(_screenshot_future=None, lanlan_name='Live',
                              _proactive_vision_enabled=True, _proactive_vision_revocation_epoch=0)
    monkeypatch.setattr(core_facade, 'aload_global_conversation_settings',
                        AsyncMock(return_value={'proactiveVisionEnabled': True}))

    def capture():
        LLMSessionManager.update_proactive_vision_consent(manager, False)
        return Image.new('RGB', (320, 180), (180, 80, 20))

    capture_mock = Mock(side_effect=capture)
    monkeypatch.setattr(proactive, 'capture_desktop_screenshot', capture_mock)

    async def send(_payload):
        if reply_kind in ('empty', 'image'):
            LLMSessionManager.update_proactive_vision_consent(manager, False)
        LLMSessionManager.resolve_screenshot_request(
            manager, _png_b64(320, 180) if reply_kind == 'image' else '',
        )

    send_mock = AsyncMock(side_effect=send)
    manager.websocket = SimpleNamespace(send_json=send_mock, client=SimpleNamespace(host='127.0.0.1'))
    if reply_kind == 'before':
        LLMSessionManager.update_proactive_vision_consent(manager, False)
    result = asyncio.run(LLMSessionManager.request_fresh_screenshot(manager))
    assert result.declined is True and not result.b64
    assert capture_mock.call_count == int(reply_kind == 'backend_capture')
    assert send_mock.await_count == int(reply_kind != 'before')


@pytest.mark.unit
@pytest.mark.parametrize('declined', [True, False])
def test_late_reply_records_only_explicit_declines_after_future_is_gone(declined):
    manager = SimpleNamespace(_screenshot_future=None, _proactive_vision_enabled=None,
                              _proactive_screenshot_request_id='current',
                              _proactive_vision_revocation_epoch=0)
    LLMSessionManager.resolve_screenshot_request(
        manager, '', declined=declined, request_id='current',
    )
    assert manager._proactive_vision_revocation_epoch == int(declined)
    assert manager._proactive_vision_enabled is None


@pytest.mark.unit
@pytest.mark.parametrize('reply_id', ['current', 'previous', None, '', 42])
@pytest.mark.parametrize('declined', [True, False])
@pytest.mark.parametrize('waiting', [True, False])
def test_screenshot_reply_correlation_precedes_all_side_effects(reply_id, declined, waiting):
    async def run():
        future = asyncio.get_running_loop().create_future() if waiting else None
        original_position = {'x': 1}
        position = {'x': 2}
        manager = SimpleNamespace(
            _screenshot_future=future, _proactive_screenshot_request_id='current',
            _proactive_vision_revocation_epoch=0, _avatar_position=original_position,
            _pending_screenshot_avatar_position=original_position,
        )
        LLMSessionManager.resolve_screenshot_request(
            manager, 'image', position, declined=declined, request_id=reply_id,
        )
        accepted = reply_id in ('current', None)
        assert manager._proactive_vision_revocation_epoch == int(accepted and declined)
        assert manager._avatar_position == (position if accepted and waiting else original_position)
        assert manager._pending_screenshot_avatar_position == (
            position if accepted and waiting else original_position
        )
        if waiting:
            assert future.done() is accepted
            if accepted:
                assert future.result().b64 == ('' if declined else 'image')
                assert future.result().declined is declined
            else:
                future.cancel()

    asyncio.run(run())


@pytest.mark.unit
@pytest.mark.parametrize('reply_id', ['finished', None])
def test_finished_round_replies_do_not_revoke_or_replace_positions(reply_id):
    manager = SimpleNamespace(
        _screenshot_future=None, _proactive_screenshot_request_id=None,
        _proactive_vision_revocation_epoch=0, _avatar_position={'x': 1},
    )
    LLMSessionManager.resolve_screenshot_request(
        manager, '', {'x': 2}, declined=True, request_id=reply_id,
    )
    assert manager._proactive_vision_revocation_epoch == 0
    assert manager._avatar_position == {'x': 1}


@pytest.mark.unit
def test_screenshot_timeout_preserves_id_for_current_round_late_decline(monkeypatch):
    from unittest.mock import AsyncMock
    from main_logic import core as core_facade

    monkeypatch.setattr(core_facade, 'aload_global_conversation_settings',
                        AsyncMock(return_value={'proactiveVisionEnabled': True}))

    async def run():
        manager = SimpleNamespace(
            _screenshot_future=None, _proactive_screenshot_request_id='current',
            _proactive_vision_revocation_epoch=0, lanlan_name='Repro',
            websocket=SimpleNamespace(send_json=AsyncMock()),
        )
        result = await LLMSessionManager.request_fresh_screenshot(
            manager, timeout=0.001, allow_backend_fallback=False, request_id='current',
        )
        assert not result.declined and not result.b64
        assert manager._screenshot_future is None
        assert manager._proactive_screenshot_request_id == 'current'
        LLMSessionManager.resolve_screenshot_request(
            manager, '', declined=True, request_id='current',
        )
        assert manager._proactive_vision_revocation_epoch == 1

    asyncio.run(run())


@pytest.mark.unit
def test_lifecycle_retires_screenshot_id_before_idle_and_preserves_new_owner():
    from unittest.mock import AsyncMock
    from main_logic.proactive_chat.delivery import ProactiveLifecycle

    async def run():
        manager = SimpleNamespace(_proactive_screenshot_request_id='first')

        async def fire(_event, **payload):
            assert payload['round_id'] == 'first'
            assert manager._proactive_screenshot_request_id is None
            manager._proactive_screenshot_request_id = 'second'

        manager.state = SimpleNamespace(fire=AsyncMock(side_effect=fire))
        lifecycle = ProactiveLifecycle(manager, 'DONE', 'Repro', screenshot_request_id='first')
        await lifecycle.safe_done()
        lifecycle.clear_screenshot_request()
        await lifecycle.safe_done()
        assert manager._proactive_screenshot_request_id == 'second'
        assert manager.state.fire.await_count == 1
        # A concurrent rejected request never owns the running round's ID.
        ProactiveLifecycle(manager, 'DONE', 'Repro').clear_screenshot_request()
        assert manager._proactive_screenshot_request_id == 'second'

    asyncio.run(run())


@pytest.mark.unit
@pytest.mark.parametrize('when', ['entry', 'privacy_read'])
def test_retired_screenshot_request_cannot_borrow_replacement_id(monkeypatch, when):
    from unittest.mock import AsyncMock, Mock
    from main_logic import core as core_facade
    from main_logic.core import proactive

    async def run():
        waiting = asyncio.get_running_loop().create_future()
        manager = SimpleNamespace(
            _screenshot_future=waiting, _proactive_screenshot_request_id='old',
            state=SimpleNamespace(proactive_round_id='old'), lanlan_name='Repro',
            websocket=SimpleNamespace(send_json=AsyncMock()),
        )

        def replace():
            manager._proactive_screenshot_request_id = 'new'
            manager.state.proactive_round_id = 'new'

        async def read(**_kwargs):
            replace()
            return {'proactiveVisionEnabled': True}

        read_mock = AsyncMock(side_effect=read)
        capture = Mock()
        monkeypatch.setattr(core_facade, 'aload_global_conversation_settings', read_mock)
        monkeypatch.setattr(proactive, 'capture_desktop_screenshot', capture)
        if when == 'entry':
            replace()
        result = await LLMSessionManager.request_fresh_screenshot(manager, request_id='old')
        assert result.declined and not result.b64
        assert manager._proactive_screenshot_request_id == 'new'
        assert manager._screenshot_future is waiting and not waiting.done()
        assert read_mock.await_count == int(when == 'privacy_read')
        manager.websocket.send_json.assert_not_awaited()
        capture.assert_not_called()
        waiting.cancel()

    asyncio.run(run())


@pytest.mark.unit
@pytest.mark.parametrize('outcome', ['reply', 'timeout', 'cancel', 'send_error'])
def test_old_screenshot_cleanup_cannot_clear_new_future_or_trigger_fallback(monkeypatch, outcome):
    from unittest.mock import AsyncMock, Mock
    from main_logic import core as core_facade
    from main_logic.core import proactive
    from main_logic.core._shared import FreshScreenshot

    monkeypatch.setattr(core_facade, 'aload_global_conversation_settings',
                        AsyncMock(return_value={'proactiveVisionEnabled': True}))
    capture = Mock()
    monkeypatch.setattr(proactive, 'capture_desktop_screenshot', capture)

    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        manager = SimpleNamespace(
            _screenshot_future=None, _proactive_screenshot_request_id='old',
            _proactive_vision_revocation_epoch=0, lanlan_name='Repro',
            state=SimpleNamespace(proactive_round_id='old'),
        )

        async def send(payload):
            assert payload['request_id'] == 'old'
            started.set()
            await release.wait()
            if outcome == 'send_error':
                raise OSError('synthetic transport error')

        manager.websocket = SimpleNamespace(send_json=send, client=SimpleNamespace(host='127.0.0.1'))
        task = asyncio.create_task(LLMSessionManager.request_fresh_screenshot(
            manager, request_id='old', timeout=0.005,
        ))
        await started.wait()
        old_future = manager._screenshot_future
        new_future = asyncio.get_running_loop().create_future()
        manager._proactive_screenshot_request_id = 'new'
        manager.state.proactive_round_id = 'new'
        manager._screenshot_future = new_future
        manager._pending_screenshot_avatar_position = {'x': 2}
        if outcome == 'reply':
            old_future.set_result(FreshScreenshot(b64='queued old frame'))
        if outcome == 'cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            result = await task
            assert result.declined and not result.b64
        assert manager._screenshot_future is new_future and not new_future.done()
        assert manager._pending_screenshot_avatar_position == {'x': 2}
        assert manager._proactive_vision_revocation_epoch == 0
        capture.assert_not_called()
        LLMSessionManager.resolve_screenshot_request(manager, '', declined=True, request_id='new')
        assert new_future.result().declined
        assert manager._proactive_vision_revocation_epoch == 1

    asyncio.run(run())
