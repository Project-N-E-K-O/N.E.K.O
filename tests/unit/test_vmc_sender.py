from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from main_logic import vmc_sender as vmc_sender_module
from main_logic.vmc_sender import VmcSender
from main_routers import vmc_router
from main_routers.system_router import _shared as system_router_shared
from tests.fake_clock import patch_module_clock


class _RecordingOscClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, list[object]]] = []
        self.closed = False

    def send_message(self, address: str, values: list[object]) -> None:
        self.messages.append((address, values))

    def close(self) -> None:
        self.closed = True


def _enabled_sender() -> tuple[VmcSender, _RecordingOscClient]:
    sender = VmcSender(config_dir=None)
    client = _RecordingOscClient()
    sender._enabled = True
    sender._client = client
    sender._min_interval = 0.0
    return sender, client


def _poll_until(predicate, *, timeout: float = 1.0, interval: float = 0.005):
    """Poll until predicate is truthy; on timeout hand the last value back to the caller."""
    # Windows 的事件循环时钟精度只有 ~15ms，固定 sleep 既可能等于零等待也可能超发
    # 一倍，等不出「后台已经做完」这个确定性，只能轮询 + deadline。
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(interval)


@pytest.mark.unit
def test_frame_is_encoded_with_vmc_names_coordinates_and_zero_blends():
    sender, client = _enabled_sender()

    assert sender.send_frame(
        {
            "root": {
                "px": 1,
                "py": 2,
                "pz": 3,
                "qx": 0.1,
                "qy": 0.2,
                "qz": 0.3,
                "qw": 0.9,
            },
            "bones": [
                {
                    "name": "hips",
                    "px": 4,
                    "py": 5,
                    "pz": 6,
                    "qx": 0.4,
                    "qy": 0.5,
                    "qz": 0.6,
                    "qw": 0.7,
                },
                {
                    "name": "leftThumbMetacarpal",
                    "px": 0,
                    "py": 0,
                    "pz": 0,
                    "qx": 0,
                    "qy": 0,
                    "qz": 0,
                    "qw": 1,
                },
                {
                    "name": "leftThumbProximal",
                    "px": 0,
                    "py": 0,
                    "pz": 0,
                    "qx": 0,
                    "qy": 0,
                    "qz": 0,
                    "qw": 1,
                },
                {"name": "notABone"},
            ],
            "expressions": [
                {"name": "happy", "value": 0.75},
                {"name": "blinkLeft", "value": 0},
                {"name": "CustomCase", "value": 0.25},
            ],
        }
    )

    messages = client.messages
    assert messages[0] == ("/VMC/Ext/OK", [1])
    assert messages[1][0] == "/VMC/Ext/T"
    assert isinstance(messages[1][1][0], float)
    assert (
        "/VMC/Ext/Root/Pos",
        ["root", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
    ) in messages
    assert ("/VMC/Ext/Bone/Pos", ["Hips", 4.0, 5.0, -6.0, -0.4, -0.5, 0.6, 0.7]) in messages
    assert (
        "/VMC/Ext/Bone/Pos",
        ["LeftThumbProximal", 0.0, 0.0, -0.0, -0.0, -0.0, 0.0, 1.0],
    ) in messages
    assert (
        "/VMC/Ext/Bone/Pos",
        ["LeftThumbIntermediate", 0.0, 0.0, -0.0, -0.0, -0.0, 0.0, 1.0],
    ) in messages
    assert not any(values and values[0] == "LeftThumbMetacarpal" for _, values in messages)
    assert not any(values and values[0] == "notABone" for _, values in messages)
    assert ("/VMC/Ext/Blend/Val", ["Joy", 0.75]) in messages
    assert ("/VMC/Ext/Blend/Val", ["Blink_L", 0.0]) in messages
    assert ("/VMC/Ext/Blend/Val", ["CustomCase", 0.25]) in messages
    assert messages[-1] == ("/VMC/Ext/Blend/Apply", [])


@pytest.mark.unit
def test_invalid_or_non_finite_transforms_are_dropped():
    sender, client = _enabled_sender()
    sender.send_frame(
        {
            "root": {
                "px": 0,
                "py": 0,
                "pz": float("nan"),
                "qx": 0,
                "qy": 0,
                "qz": 0,
                "qw": 1,
            },
            "bones": [{"name": "Hips", "px": 0}],
            "expressions": [],
        }
    )
    addresses = [address for address, _ in client.messages]
    assert "/VMC/Ext/Root/Pos" in addresses
    assert "/VMC/Ext/Bone/Pos" not in addresses


@pytest.mark.unit
def test_old_t_pose_frame_cannot_clear_newer_request():
    sender, _ = _enabled_sender()
    old_generation = sender.request_t_pose(2.0)
    new_generation = sender.request_t_pose(3.0)

    sender.send_frame(
        {
            "t_pose": True,
            "t_pose_generation": old_generation,
            "bones": [],
            "expressions": [],
        }
    )
    status = sender.status()
    assert status["t_pose_requested"] is True
    assert status["t_pose_generation"] == new_generation
    assert status["t_pose_duration_sec"] == 3.0

    sender.send_frame(
        {
            "t_pose": True,
            "t_pose_generation": new_generation,
            "bones": [],
            "expressions": [],
        }
    )
    assert sender.status()["t_pose_requested"] is False


@pytest.mark.unit
def test_source_release_reports_unavailable_after_zeroing_expressions():
    sender, client = _enabled_sender()
    sender.send_frame(
        {
            "expressions": [{"name": "happy", "value": 0.8}],
        }
    )

    assert sender.send_frame(
        {
            "expressions": [{"name": "happy", "value": 0}],
            "source_released": True,
        },
        force=True,
    )

    assert client.messages[-3:] == [
        ("/VMC/Ext/Blend/Val", ["Joy", 0.0]),
        ("/VMC/Ext/Blend/Apply", []),
        ("/VMC/Ext/OK", [0]),
    ]
    assert sender._active_expression_names == set()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disable_zeroes_active_expressions_and_reports_unavailable():
    sender, client = _enabled_sender()
    sender.send_frame(
        {
            "expressions": [{"name": "happy", "value": 0.8}],
        }
    )

    status = await sender.disable()

    assert status["enabled"] is False
    assert client.messages[-3:] == [
        ("/VMC/Ext/Blend/Val", ["Joy", 0.0]),
        ("/VMC/Ext/Blend/Apply", []),
        ("/VMC/Ext/OK", [0]),
    ]
    assert client.closed is True


@pytest.mark.unit
def test_disconnected_publisher_terminal_state_is_generation_guarded():
    sender, client = _enabled_sender()
    sender.set_publisher_generation(7)
    sender.send_frame(
        {
            "expressions": [{"name": "happy", "value": 0.8}],
        },
        publisher_generation=7,
    )

    assert sender.send_terminal_state(publisher_generation=6) is False
    assert sender._active_expression_names == {"Joy"}
    assert sender.send_terminal_state(publisher_generation=7) is True

    assert client.messages[-3:] == [
        ("/VMC/Ext/Blend/Val", ["Joy", 0.0]),
        ("/VMC/Ext/Blend/Apply", []),
        ("/VMC/Ext/OK", [0]),
    ]
    assert sender.enabled is True
    assert client.closed is False
    assert sender._active_expression_names == set()


@pytest.mark.unit
def test_sender_token_bucket_preserves_average_rate_under_jitter(monkeypatch):
    sender, client = _enabled_sender()
    sender._min_interval = 1 / 60
    sender._send_tokens = 0.0
    sender._last_token_refill_ts = 0.0
    timestamps = iter(index / 144 for index in range(145))
    patch_module_clock(monkeypatch, vmc_sender_module, monotonic=lambda: next(timestamps))

    sent_count = sum(
        sender.send_frame({"bones": [], "expressions": []})
        for _ in range(145)
    )

    assert 59 <= sent_count <= 61
    assert sum(address == "/VMC/Ext/OK" for address, _ in client.messages) == sent_count


@pytest.mark.unit
def test_persisted_endpoint_config_is_loaded_once(monkeypatch):
    persisted = {"host": "192.0.2.10", "port": 40000, "send_rate_hz": 60}

    async def fake_read_json(_path):
        return persisted.copy()

    monkeypatch.setattr(vmc_sender_module, "read_json_async", fake_read_json)
    sender = VmcSender(config_dir=Path("test-config"))

    asyncio.run(sender.ensure_config_loaded())
    assert sender.host == "192.0.2.10"
    assert sender.port == 40000
    assert sender.send_rate_hz == 60

    persisted.update(host="192.0.2.11", port=41000, send_rate_hz=20)
    asyncio.run(sender.ensure_config_loaded())
    assert sender.host == "192.0.2.10"


@pytest.mark.unit
def test_legacy_default_rate_is_migrated_to_60_hz(monkeypatch):
    async def fake_read_json(_path):
        return {"host": "127.0.0.1", "port": 39539, "send_rate_hz": 30}

    monkeypatch.setattr(vmc_sender_module, "read_json_async", fake_read_json)
    sender = VmcSender(config_dir=Path("test-config"))

    asyncio.run(sender.ensure_config_loaded())
    assert sender.send_rate_hz == 60


@pytest.mark.unit
def test_frontend_vmc_root_is_decoupled_from_webpage_scene_transform():
    source = Path("static/vrm/vrm-vmc-sender.js").read_text(encoding="utf-8")
    assert "root: VMC_LOCAL_ROOT" in source
    assert "vrm.scene.position" not in source
    assert "vrm.scene.quaternion" not in source


@pytest.mark.unit
def test_frontend_vmc_reconnect_uses_bounded_backoff_and_auth_refresh():
    source = Path("static/vrm/vrm-vmc-sender.js").read_text(encoding="utf-8")
    assert "RECONNECT_MAX_DELAY_MS" in source
    assert "if (state.reconnectTimer) return Promise.resolve(false)" in source
    assert "if (!token && state.enabled) scheduleReconnect(true)" in source
    assert "state.reconnectRefreshAuth || !!refreshAuth" in source
    assert "ensureWebSocket(shouldRefreshAuth)" in source
    assert "if (state.ws === socket) scheduleReconnect(false)" in source
    assert "scheduleReconnect(event.code === 4403)" in source


@pytest.mark.unit
def test_frontend_status_and_expression_state_have_race_guards():
    source = Path("static/vrm/vrm-vmc-sender.js").read_text(encoding="utf-8")
    manager_source = Path("static/vrm/vrm-manager.js").read_text(encoding="utf-8")
    assert "controlGeneration !== state.controlGeneration" in source
    assert "requestSequence !== state.statusRequestSequence" in source
    assert "if (!state.enabled || !state.sourceActive)" in source
    assert "releaseVrm: releaseSource" in source
    assert "window.vrmVmcSender.releaseVrm" in manager_source
    assert "state.currentVrm !== vrm" in source
    assert "state.retiringExpressionNames" in source
    assert "if (state.exprBuf.length >= MAX_EXPRESSIONS_PER_FRAME) break" in source
    assert "state.retiringExpressionNames.delete(name)" in source
    assert "message.type === 'frame_ack'" in source
    assert "messageType: 'release'" in source
    assert "Math.ceil(expressionNames.length / MAX_EXPRESSIONS_PER_FRAME)" in source
    assert "if (!await result.ackPromise) return false" in source
    assert "source_released: index === expressionChunks.length - 1" in source
    assert "else if (!state.enabled || !state.releaseInProgress) closeWebSocket()" in source
    assert "samplingSuspensionGeneration" in source
    assert (
        "samplingSuspensionGeneration\n"
        "                    === state.samplingSuspensionGeneration"
    ) in source
    assert "state.nextSampleTs += state.minIntervalSec" in source
    assert "this._nextRenderTime += frameInterval" in manager_source
    assert "this._lastRenderTime" in manager_source
    assert "window.__NEKO_VMC_ACTIVE__ === true" in manager_source
    assert "t_pose_generation: state.tPoseGeneration" in source


@pytest.mark.unit
def test_vmc_disabled_path_is_lazy_and_has_no_background_work():
    init_source = Path("static/vrm/vrm-init.js").read_text(encoding="utf-8")
    loader_source = Path("static/vrm/vrm-vmc-loader.js").read_text(
        encoding="utf-8"
    )
    sender_source = Path("static/vrm/vrm-vmc-sender.js").read_text(
        encoding="utf-8"
    )
    manager_source = Path("static/vrm/vrm-manager.js").read_text(
        encoding="utf-8"
    )

    assert "'/static/vrm/vrm-vmc-loader.js'" in init_source
    assert "'/static/vrm/vrm-vmc-sender.js'" not in init_source
    assert "/static/vrm/vrm-vmc-sender.js?v=" in loader_source
    assert "fetch(" not in loader_source
    assert "setInterval(" not in loader_source
    assert "setTimeout(" not in loader_source
    assert "syncStatusFromBackend();" not in sender_source
    assert (
        sender_source.index("if (!state.enabled) return;")
        < sender_source.index("markSourceActive();")
    )
    assert "window.__NEKO_VMC_ACTIVE__ === true" in manager_source
    assert "&& window.__NEKO_VMC_ACTIVE__ === true" in manager_source


class _FakeSender:
    def __init__(self) -> None:
        self.frames: list[dict[str, object]] = []
        self.frame_received = threading.Event()
        self.terminal_sent = threading.Event()
        self.t_pose_generation = 0
        self.t_pose_duration_sec = 2.0
        self.force_values: list[bool] = []
        self.publisher_generation = 0
        self.terminal_generations: list[int] = []

    async def ensure_config_loaded(self) -> None:
        return None

    def set_publisher_generation(self, generation: int) -> None:
        self.publisher_generation = generation

    def send_terminal_state(self, *, publisher_generation: int) -> bool:
        if publisher_generation != self.publisher_generation:
            return False
        self.terminal_generations.append(publisher_generation)
        self.terminal_sent.set()
        return True

    def send_frame(
        self,
        payload: dict[str, object],
        *,
        force: bool = False,
        publisher_generation: int | None = None,
    ) -> bool:
        if (
            publisher_generation is not None
            and publisher_generation != self.publisher_generation
        ):
            return False
        self.frames.append(payload)
        self.force_values.append(force)
        self.frame_received.set()
        return True

    def request_t_pose(self, duration_sec=None) -> int:
        self.t_pose_generation += 1
        if duration_sec is not None:
            self.t_pose_duration_sec = duration_sec
        return self.t_pose_generation

    def status(self) -> dict[str, object]:
        return {
            "t_pose_duration_sec": self.t_pose_duration_sec,
            "t_pose_generation": self.t_pose_generation,
        }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_frame_worker_survives_unexpected_sender_exception():
    class FlakySender:
        def __init__(self) -> None:
            self.payloads: list[dict[str, object]] = []

        def send_frame(self, payload, *, force=False):
            self.payloads.append(payload)
            if len(self.payloads) == 1:
                raise RuntimeError("unexpected sender failure")
            return True

    class RecordingWebSocket:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message):
            self.messages.append(message)

    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    sender = FlakySender()
    websocket = RecordingWebSocket()
    worker = asyncio.create_task(
        vmc_router._vmc_frame_worker(queue, sender, websocket)
    )
    try:
        await queue.put(
            {
                "payload": {"frame": 1},
                "force": False,
                "require_ack": True,
                "sequence": 1,
                "completion": None,
            }
        )
        await queue.put(
            {
                "payload": {"frame": 2},
                "force": False,
                "require_ack": True,
                "sequence": 2,
                "completion": None,
            }
        )
        await asyncio.wait_for(queue.join(), timeout=1.0)
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker

    assert sender.payloads == [{"frame": 1}, {"frame": 2}]
    assert websocket.messages == [
        {"type": "frame_ack", "sequence": 1, "sent": False},
        {"type": "frame_ack", "sequence": 2, "sent": True},
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_frame_worker_cancellation_drains_in_flight_thread():
    class BlockingSender:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.allow_finish = threading.Event()
            self.finished = threading.Event()

        def send_frame(self, payload, *, force=False):
            self.started.set()
            assert self.allow_finish.wait(timeout=1.0)
            self.finished.set()
            return True

    class NoopWebSocket:
        async def send_json(self, message):
            return None

    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    sender = BlockingSender()
    worker = asyncio.create_task(
        vmc_router._vmc_frame_worker(queue, sender, NoopWebSocket())
    )
    await queue.put(
        {
            "payload": {"frame": 1},
            "force": False,
            "require_ack": False,
            "sequence": 1,
            "completion": None,
        }
    )
    assert await asyncio.to_thread(sender.started.wait, 1.0)

    worker.cancel()
    await asyncio.sleep(0.02)
    assert worker.done() is False

    sender.allow_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert sender.finished.is_set()


@pytest.fixture
def vmc_client(monkeypatch):
    sender = _FakeSender()
    monkeypatch.setattr(vmc_router, "_active_vmc_publisher", None)
    monkeypatch.setattr(vmc_router, "_publisher_generation", 0)
    monkeypatch.setattr(vmc_router, "_pending_terminal_task", None)
    monkeypatch.setattr(vmc_router, "AUTOSTART_CSRF_TOKEN", "vmc-test-token")
    monkeypatch.setattr(
        system_router_shared,
        "AUTOSTART_CSRF_TOKEN",
        "vmc-test-token",
    )
    monkeypatch.setattr(vmc_router, "get_vmc_sender", lambda: sender)
    app = FastAPI()
    app.include_router(vmc_router.router)
    with TestClient(app) as client:
        yield client, sender


@pytest.mark.unit
def test_dedicated_websocket_authenticates_and_forwards_frames(vmc_client):
    client, sender = vmc_client
    with client.websocket_connect(
        "/api/vmc/ws",
        headers={"Origin": "http://testserver"},
    ) as websocket:
        websocket.send_json({"type": "auth", "csrf_token": "vmc-test-token"})
        assert websocket.receive_json() == {"type": "ready"}
        websocket.send_json(
            {
                "type": "frame",
                "sequence": 1,
                "payload": {"bones": []},
            }
        )
        assert sender.frame_received.wait(timeout=1.0)
    assert sender.frames == [{"bones": []}]


@pytest.mark.unit
def test_disconnected_publisher_sends_terminal_state_after_grace(
    vmc_client,
    monkeypatch,
):
    client, sender = vmc_client
    monkeypatch.setattr(
        vmc_router,
        "_PUBLISHER_DISCONNECT_GRACE_SECONDS",
        0.05,
    )

    with client.websocket_connect(
        "/api/vmc/ws",
        headers={"Origin": "http://testserver"},
    ) as websocket:
        websocket.send_json({"type": "auth", "csrf_token": "vmc-test-token"})
        assert websocket.receive_json() == {"type": "ready"}

    assert sender.terminal_sent.wait(timeout=1.0)
    assert sender.terminal_generations == [1]


@pytest.mark.unit
def test_reconnect_during_grace_cancels_terminal_state(vmc_client, monkeypatch):
    client, sender = vmc_client
    # 宽限期给 1.0s 而不是 0.1s：successor 的握手+认证耗时不受本测试控制，
    # 一旦它比宽限期还慢，终态就先发出去了，红的原因跟被测逻辑无关。
    monkeypatch.setattr(
        vmc_router,
        "_PUBLISHER_DISCONNECT_GRACE_SECONDS",
        1.0,
    )
    headers = {"Origin": "http://testserver"}
    auth = {"type": "auth", "csrf_token": "vmc-test-token"}

    with client.websocket_connect("/api/vmc/ws", headers=headers) as primary:
        primary.send_json(auth)
        assert primary.receive_json() == {"type": "ready"}

    # 断开由事件循环异步收尾，任务不是立刻挂上的；拿到句柄才能证明它后来被取消。
    pending_terminal = _poll_until(lambda: vmc_router._pending_terminal_task)
    assert pending_terminal is not None

    with client.websocket_connect("/api/vmc/ws", headers=headers) as successor:
        successor.send_json(auth)
        assert successor.receive_json() == {"type": "ready"}
        # 路由在回 ready 之前就调了 _cancel_pending_terminal_task，所以这里不必靠固定
        # sleep 去赌「宽限期是否已过」（Windows 定时器精度 ~15ms，赌不出确定性）：
        # 任务在远小于宽限期的时间里结束，只能是被取消掉的。
        assert _poll_until(pending_terminal.done, timeout=0.4), (
            "宽限期内的重连没有取消掉待发终态任务"
        )
        assert sender.terminal_generations == []


@pytest.mark.unit
def test_release_frame_bypasses_throttle_and_is_acknowledged(
    vmc_client,
    monkeypatch,
):
    client, sender = vmc_client
    monkeypatch.setattr(
        vmc_router,
        "_PUBLISHER_DISCONNECT_GRACE_SECONDS",
        0.05,
    )
    with client.websocket_connect(
        "/api/vmc/ws",
        headers={"Origin": "http://testserver"},
    ) as websocket:
        websocket.send_json({"type": "auth", "csrf_token": "vmc-test-token"})
        assert websocket.receive_json() == {"type": "ready"}
        websocket.send_json(
            {
                "type": "release",
                "sequence": 7,
                "payload": {
                    "bones": [],
                    "expressions": [],
                    "source_released": True,
                },
            }
        )
        assert websocket.receive_json() == {
            "type": "frame_ack",
            "sequence": 7,
            "sent": True,
        }
    time.sleep(0.1)
    assert sender.frames == [
        {
            "bones": [],
            "expressions": [],
            "source_released": True,
        }
    ]
    assert sender.force_values == [True]
    assert sender.terminal_generations == []


@pytest.mark.unit
def test_normal_frame_after_release_restores_disconnect_cleanup(
    vmc_client,
    monkeypatch,
):
    client, sender = vmc_client
    monkeypatch.setattr(
        vmc_router,
        "_PUBLISHER_DISCONNECT_GRACE_SECONDS",
        0.05,
    )
    with client.websocket_connect(
        "/api/vmc/ws",
        headers={"Origin": "http://testserver"},
    ) as websocket:
        websocket.send_json({"type": "auth", "csrf_token": "vmc-test-token"})
        assert websocket.receive_json() == {"type": "ready"}
        websocket.send_json(
            {
                "type": "release",
                "sequence": 1,
                "payload": {
                    "expressions": [],
                    "source_released": True,
                },
            }
        )
        assert websocket.receive_json() == {
            "type": "frame_ack",
            "sequence": 1,
            "sent": True,
        }
        websocket.send_json(
            {
                "type": "frame",
                "sequence": 2,
                "require_ack": True,
                "payload": {"expressions": [{"name": "happy", "value": 1}]},
            }
        )
        assert websocket.receive_json() == {
            "type": "frame_ack",
            "sequence": 2,
            "sent": True,
        }

    assert sender.terminal_sent.wait(timeout=1.0)
    assert sender.terminal_generations == [1]


@pytest.mark.unit
def test_release_is_ordered_after_an_in_flight_frame(vmc_client):
    client, sender = vmc_client
    normal_started = threading.Event()
    allow_normal_to_finish = threading.Event()
    original_send_frame = sender.send_frame

    def ordered_send_frame(
        payload,
        *,
        force=False,
        publisher_generation=None,
    ):
        if not force:
            normal_started.set()
            assert allow_normal_to_finish.wait(timeout=1.0)
        return original_send_frame(
            payload,
            force=force,
            publisher_generation=publisher_generation,
        )

    sender.send_frame = ordered_send_frame
    with client.websocket_connect(
        "/api/vmc/ws",
        headers={"Origin": "http://testserver"},
    ) as websocket:
        websocket.send_json({"type": "auth", "csrf_token": "vmc-test-token"})
        assert websocket.receive_json() == {"type": "ready"}
        websocket.send_json(
            {
                "type": "frame",
                "sequence": 1,
                "payload": {"kind": "normal"},
            }
        )
        assert normal_started.wait(timeout=1.0)
        websocket.send_json(
            {
                "type": "release",
                "sequence": 2,
                "payload": {"kind": "release"},
            }
        )
        allow_normal_to_finish.set()
        assert websocket.receive_json() == {
            "type": "frame_ack",
            "sequence": 2,
            "sent": True,
        }

    assert sender.frames == [{"kind": "normal"}, {"kind": "release"}]
    assert sender.force_values == [False, True]


@pytest.mark.unit
def test_publisher_lease_expires_without_frames(vmc_client, monkeypatch):
    client, _ = vmc_client
    monkeypatch.setattr(
        vmc_router,
        "_WS_PUBLISHER_IDLE_TIMEOUT_SECONDS",
        0.05,
    )
    with client.websocket_connect(
        "/api/vmc/ws",
        headers={"Origin": "http://testserver"},
    ) as websocket:
        websocket.send_json({"type": "auth", "csrf_token": "vmc-test-token"})
        assert websocket.receive_json() == {"type": "ready"}
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()
    assert exc_info.value.code == 4428


@pytest.mark.unit
def test_dedicated_websocket_rejects_bad_auth(vmc_client):
    client, _ = vmc_client
    with client.websocket_connect(
        "/api/vmc/ws",
        headers={"Origin": "http://testserver"},
    ) as websocket:
        websocket.send_json({"type": "auth", "csrf_token": "wrong"})
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()
    assert exc_info.value.code == 4403


@pytest.mark.unit
def test_only_one_websocket_can_publish_and_standby_can_take_over(vmc_client):
    client, _ = vmc_client
    headers = {"Origin": "http://testserver"}
    auth = {"type": "auth", "csrf_token": "vmc-test-token"}

    with client.websocket_connect("/api/vmc/ws", headers=headers) as primary:
        primary.send_json(auth)
        assert primary.receive_json() == {"type": "ready"}

        with client.websocket_connect("/api/vmc/ws", headers=headers) as standby:
            standby.send_json(auth)
            with pytest.raises(WebSocketDisconnect) as exc_info:
                standby.receive_json()
        assert exc_info.value.code == vmc_router._PUBLISHER_BUSY_CLOSE_CODE

    with client.websocket_connect("/api/vmc/ws", headers=headers) as successor:
        successor.send_json(auth)
        assert successor.receive_json() == {"type": "ready"}


@pytest.mark.unit
def test_vmc_mutations_require_csrf(vmc_client):
    client, _ = vmc_client
    response = client.post(
        "/api/vmc/enable",
        headers={"Origin": "http://testserver"},
        json={},
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "csrf_validation_failed"


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        {"host": ""},
        {"host": "bad host"},
        {"host": "http://127.0.0.1"},
        {"port": 0},
        {"port": True},
        {"send_rate_hz": 121},
        {"send_rate_hz": "60"},
    ],
)
def test_vmc_enable_rejects_invalid_explicit_endpoint_values(vmc_client, payload):
    client, _ = vmc_client
    response = client.post(
        "/api/vmc/enable",
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": "vmc-test-token",
        },
        json=payload,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "invalid_vmc_endpoint"


@pytest.mark.unit
def test_vmc_mutations_reject_malformed_or_non_object_json(vmc_client):
    client, _ = vmc_client
    headers = {
        "Origin": "http://testserver",
        "X-CSRF-Token": "vmc-test-token",
        "Content-Type": "application/json",
    }

    malformed = client.post("/api/vmc/enable", headers=headers, content="{")
    assert malformed.status_code == 400
    assert malformed.json()["error_code"] == "invalid_json_body"

    non_object = client.post("/api/vmc/enable", headers=headers, json=[])
    assert non_object.status_code == 400
    assert non_object.json()["error_code"] == "invalid_json_body"


@pytest.mark.unit
@pytest.mark.parametrize(
    "duration",
    [-1, 0, True, "2", pytest.param(10**400, id="huge-int")],
)
def test_vmc_t_pose_rejects_invalid_duration(vmc_client, duration):
    client, _ = vmc_client
    response = client.post(
        "/api/vmc/t_pose",
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": "vmc-test-token",
        },
        json={"duration_sec": duration},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "invalid_vmc_t_pose"


@pytest.mark.unit
def test_vmc_t_pose_rejects_non_finite_duration(vmc_client):
    client, _ = vmc_client
    response = client.post(
        "/api/vmc/t_pose",
        headers={
            "Content-Type": "application/json",
            "Origin": "http://testserver",
            "X-CSRF-Token": "vmc-test-token",
        },
        content='{"duration_sec": Infinity}',
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "invalid_vmc_t_pose"


@pytest.mark.unit
def test_vmc_t_pose_returns_request_generation(vmc_client):
    client, _ = vmc_client
    response = client.post(
        "/api/vmc/t_pose",
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": "vmc-test-token",
        },
        json={"duration_sec": 3},
    )
    assert response.status_code == 200
    assert response.json()["t_pose_generation"] == 1
    assert response.json()["t_pose_duration_sec"] == 3.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_vmc_client_construction_runs_off_event_loop(monkeypatch):
    sender = VmcSender(config_dir=None)
    client = _RecordingOscClient()
    event_loop_thread = threading.get_ident()
    build_threads: list[int] = []

    def fake_build_client(_host, _port):
        build_threads.append(threading.get_ident())
        return client

    monkeypatch.setattr(sender, "_build_client", fake_build_client)
    await sender.enable()

    assert build_threads
    assert build_threads[0] != event_loop_thread
    assert sender._client is client


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_endpoint_reconfiguration_preserves_working_sender(
    monkeypatch,
):
    sender = VmcSender(config_dir=None)
    client = _RecordingOscClient()
    sender._enabled = True
    sender._client = client
    sender._host = "127.0.0.1"
    sender._port = 39539

    def fail_build(_host, _port):
        raise OSError("DNS failure")

    monkeypatch.setattr(sender, "_build_client", fail_build)
    with pytest.raises(OSError, match="DNS failure"):
        await sender.enable(host="missing.invalid", port=40000)

    assert sender.enabled is True
    assert sender.host == "127.0.0.1"
    assert sender.port == 39539
    assert sender._client is client
    assert client.closed is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disable_waits_for_sender_lock_off_event_loop():
    sender = VmcSender(config_dir=None)
    client = _RecordingOscClient()
    sender._enabled = True
    sender._client = client
    lock_acquired = threading.Event()

    def hold_sender_lock():
        with sender._send_lock:
            lock_acquired.set()
            time.sleep(0.2)

    holder = threading.Thread(target=hold_sender_lock)
    holder.start()
    assert lock_acquired.wait(timeout=1.0)

    heartbeat_ran = False

    async def heartbeat():
        nonlocal heartbeat_ran
        await asyncio.sleep(0.02)
        heartbeat_ran = True

    heartbeat_task = asyncio.create_task(heartbeat())
    await sender.disable()
    heartbeat_ran_before_disable_return = heartbeat_ran
    await heartbeat_task
    holder.join(timeout=1.0)

    assert heartbeat_ran_before_disable_return is True
    assert client.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_config_save_failure_does_not_contradict_runtime_state(
    monkeypatch,
):
    sender = VmcSender(config_dir=None)
    client = _RecordingOscClient()
    sender._enabled = True
    sender._client = client

    async def fail_save():
        raise PermissionError("read-only config")

    monkeypatch.setattr(sender, "save_config", fail_save)

    enabled_status = await sender.enable(send_rate_hz=30)
    assert enabled_status["enabled"] is True
    assert enabled_status["send_rate_hz"] == 30
    assert sender._client is client

    disabled_status = await sender.disable()
    assert disabled_status["enabled"] is False
    assert sender._client is None
    assert client.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enable_callback_fires_only_on_disabled_to_enabled_transition(
    monkeypatch,
):
    """The browser wake-up broadcast must be one-shot, not per-request.

    A plugin re-posting /api/vmc/enable to retune the endpoint should not
    re-broadcast: the sampler is already running and a redundant
    syncStatusFromBackend() round-trip buys nothing.
    """
    fired: list[bool] = []

    async def record(enabled: bool) -> None:
        fired.append(enabled)

    sender = VmcSender(config_dir=None, on_enabled_callback=record)
    monkeypatch.setattr(
        sender, "_build_client", lambda _host, _port: _RecordingOscClient()
    )

    await sender.enable(host="127.0.0.1", port=39539, send_rate_hz=60)
    assert fired == [True]

    # Already enabled: retuning the endpoint is not a transition.
    fired.clear()
    await sender.enable(port=39540)
    assert fired == []

    # Off and on again is a fresh transition; the sampler needs waking.
    await sender.disable()
    await sender.enable()
    assert fired == [True]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enable_survives_a_failing_callback():
    """A broken WebSocket fan-out must not fail the plugin's enable request."""

    async def explode(_enabled: bool) -> None:
        raise RuntimeError("no sessions connected")

    sender = VmcSender(config_dir=None, on_enabled_callback=explode)
    sender._build_client = lambda _host, _port: _RecordingOscClient()

    status = await sender.enable()
    assert status["enabled"] is True
    assert sender.enabled is True


@pytest.mark.unit
def test_set_vmc_enabled_callback_does_not_construct_the_singleton(monkeypatch):
    """Routers register at import time, before the config manager exists.

    Building the singleton then would resolve config_dir to None and silently
    disable vmc_config.json persistence for the life of the process.
    """
    monkeypatch.setattr(vmc_sender_module, "_singleton", None)
    monkeypatch.setattr(vmc_sender_module, "_enabled_callback", None)

    async def noop(_enabled: bool) -> None:
        return None

    vmc_sender_module.set_vmc_enabled_callback(noop)
    assert vmc_sender_module._singleton is None
    assert vmc_sender_module._enabled_callback is noop

    # The parked callback is applied when the singleton is finally built.
    sender = vmc_sender_module.get_vmc_sender()
    assert sender._on_enabled_callback is noop


@pytest.mark.unit
def test_set_vmc_enabled_callback_patches_an_existing_singleton(monkeypatch):
    existing = VmcSender(config_dir=None)
    monkeypatch.setattr(vmc_sender_module, "_singleton", existing)
    monkeypatch.setattr(vmc_sender_module, "_enabled_callback", None)

    async def noop(_enabled: bool) -> None:
        return None

    vmc_sender_module.set_vmc_enabled_callback(noop)
    assert existing._on_enabled_callback is noop


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enable_broadcast_wakes_browser_samplers(monkeypatch):
    """The wake-up rides the chat WebSocket, not the isolated /api/vmc/ws.

    The dedicated VMC socket only exists once the browser is already sampling,
    so it cannot carry the signal that starts sampling in the first place.
    """
    from app.main_server import character_runtime

    sent: list[dict] = []

    async def fake_broadcast(payload: dict) -> int:
        sent.append(payload)
        return 2

    monkeypatch.setattr(
        character_runtime, "_broadcast_to_all_connected", fake_broadcast
    )

    await character_runtime._broadcast_vmc_enabled(True)
    assert sent == [{"type": "vmc_state_changed", "enabled": True}]

    # Disable needs no broadcast: the browser learns it from its own poll and
    # waking a sampler for a dead sender would only burn frames.
    sent.clear()
    await character_runtime._broadcast_vmc_enabled(False)
    assert sent == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enable_broadcast_swallows_transport_failures(monkeypatch):
    from app.main_server import character_runtime

    async def explode(_payload: dict) -> int:
        raise RuntimeError("event loop closed")

    monkeypatch.setattr(
        character_runtime, "_broadcast_to_all_connected", explode
    )

    await character_runtime._broadcast_vmc_enabled(True)


@pytest.mark.unit
def test_enable_broadcast_is_wired_up_at_import_time():
    """Importing the app layer must register the broadcast hook.

    The wiring sits in character_runtime rather than beside the
    ``/api/vmc/enable`` route it serves, because a router (L3) cannot import
    ``app`` (L6) without tripping check_module_layering. Nothing else asserts
    the callback is installed, so a move that dropped the registration would
    leave the UDP sender running with no frame source while every other VMC
    test stayed green.
    """
    from app.main_server import character_runtime

    assert (
        vmc_sender_module._enabled_callback is character_runtime._broadcast_vmc_enabled
    )


@pytest.mark.unit
def test_model_info_sent_once_per_model():
    """``/VMC/Ext/VRM`` announces the model on change, not on every frame."""
    sender, client = _enabled_sender()

    frame_a = {
        "bones": [],
        "expressions": [],
        "model": {"path": "/models/alice.vrm", "title": "Alice"},
    }
    assert sender.send_frame(frame_a)
    assert ("/VMC/Ext/VRM", ["/models/alice.vrm", "Alice"]) in client.messages

    client.messages.clear()
    assert sender.send_frame(frame_a)
    assert not any(address == "/VMC/Ext/VRM" for address, _ in client.messages)

    frame_b = {
        "bones": [],
        "expressions": [],
        "model": {"path": "/models/bob.vrm", "title": "Bob"},
    }
    client.messages.clear()
    assert sender.send_frame(frame_b)
    assert ("/VMC/Ext/VRM", ["/models/bob.vrm", "Bob"]) in client.messages

    client.messages.clear()
    assert sender.send_frame(frame_b)
    assert not any(address == "/VMC/Ext/VRM" for address, _ in client.messages)


@pytest.mark.unit
def test_model_info_is_truncated():
    """Oversized paths/titles cannot bloat the UDP datagram."""
    sender, client = _enabled_sender()

    assert sender.send_frame(
        {
            "bones": [],
            "expressions": [],
            "model": {"path": "x" * 600, "title": "t" * 300},
        }
    )
    sent = [values for address, values in client.messages if address == "/VMC/Ext/VRM"]
    assert len(sent) == 1
    assert len(sent[0][0]) == 512
    assert len(sent[0][1]) == 256


@pytest.mark.unit
@pytest.mark.parametrize(
    "model",
    [None, {}, {"path": "/a.vrm"}, {"path": 123, "title": "Alice"}],
)
def test_model_info_ignores_malformed_payloads(model):
    """A frame without usable model metadata sends no /VMC/Ext/VRM."""
    sender, client = _enabled_sender()

    assert sender.send_frame({"bones": [], "expressions": [], "model": model})
    assert not any(address == "/VMC/Ext/VRM" for address, _ in client.messages)


@pytest.mark.unit
def test_model_info_is_reannounced_after_endpoint_change():
    """A retuned endpoint is a fresh receiver: it must be told the model again."""
    sender, client = _enabled_sender()

    frame = {
        "bones": [],
        "expressions": [],
        "model": {"path": "/models/alice.vrm", "title": "Alice"},
    }
    assert sender.send_frame(frame)
    assert ("/VMC/Ext/VRM", ["/models/alice.vrm", "Alice"]) in client.messages

    # Same model, new UDP client: the announcement cache belonged to the
    # retired endpoint, so the replacement must not inherit it.
    replacement = _RecordingOscClient()
    sender._replace_client(replacement)

    assert sender.send_frame(frame)
    assert ("/VMC/Ext/VRM", ["/models/alice.vrm", "Alice"]) in replacement.messages


@pytest.mark.unit
@pytest.mark.asyncio
async def test_model_info_is_reannounced_after_disable_and_reenable():
    """disable() ends the stream; the next receiver starts with no model context."""
    sender, client = _enabled_sender()

    frame = {
        "bones": [],
        "expressions": [],
        "model": {"path": "/models/alice.vrm", "title": "Alice"},
    }
    assert sender.send_frame(frame)
    assert ("/VMC/Ext/VRM", ["/models/alice.vrm", "Alice"]) in client.messages

    await sender.disable()
    assert sender._model_info is None
    assert sender._model_info_sent is False

    reenabled = _RecordingOscClient()
    sender._enabled = True
    sender._client = reenabled
    sender._min_interval = 0.0

    assert sender.send_frame(frame)
    assert ("/VMC/Ext/VRM", ["/models/alice.vrm", "Alice"]) in reenabled.messages


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enable_broadcast_cannot_hang_the_control_endpoint(monkeypatch):
    """A backpressured chat socket must not stall POST /api/vmc/enable.

    ``_broadcast_to_all_connected`` gathers ``send_json()`` calls with no
    per-socket timeout, so a stuck socket never raises — it simply never
    returns. Only a bounded wait keeps the endpoint responsive.
    """
    monkeypatch.setattr(vmc_sender_module, "_ENABLED_CALLBACK_TIMEOUT_SEC", 0.05)

    async def never_returns(_enabled: bool) -> None:
        await asyncio.Event().wait()

    sender = VmcSender(config_dir=None, on_enabled_callback=never_returns)
    sender._enabled = True

    started = time.monotonic()
    # Wrap the call itself: without the sender's own wait_for, this await never
    # returns, and an unbounded test would hang the suite instead of failing.
    # The outer budget is deliberately well above the 0.05s timeout under test
    # so it only trips on a missing timeout, never on scheduling noise.
    try:
        await asyncio.wait_for(sender._notify_enabled_changed(True), timeout=5.0)
    except asyncio.TimeoutError:
        pytest.fail(
            "_notify_enabled_changed() never returned: the enable broadcast has "
            "no per-call timeout, so one backpressured socket stalls "
            "POST /api/vmc/enable forever"
        )
    elapsed = time.monotonic() - started

    # Bound the wait near the configured timeout, not merely "not forever":
    # a 1.0s ceiling on a 0.05s timeout would still pass if the timeout were
    # ignored and something else happened to unblock the await.
    assert elapsed < 0.5, f"notification took {elapsed:.3f}s for a 0.05s timeout"
    # The state enable() already committed survives a failed notification.
    assert sender.enabled is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enable_reports_the_state_it_returns_in(monkeypatch):
    """enable() must not answer with a snapshot taken before the broadcast.

    The broadcast runs outside ``_lock`` so a backpressured socket cannot stall
    the control endpoint. That window lets a concurrent ``disable()`` finish
    first — and a snapshot captured before the broadcast would then report
    ``enabled: True`` after the disable response already said ``False``.
    """
    disable_started = asyncio.Event()

    async def disable_midway(_enabled: bool) -> None:
        # Stand in for the real fan-out: run a disable while enable() is
        # parked outside the lock, exactly as a concurrent request would.
        disable_started.set()
        await sender.disable()

    sender = VmcSender(config_dir=None, on_enabled_callback=disable_midway)
    sender._build_client = lambda _host, _port: _RecordingOscClient()

    status = await sender.enable()

    assert disable_started.is_set(), "the callback never ran; the test proves nothing"
    assert sender.enabled is False
    assert status["enabled"] is False, (
        "enable() returned a pre-broadcast snapshot: it claims the sender is on "
        "after a concurrent disable() already reported it off"
    )


@pytest.mark.unit
def test_expression_name_map_covers_vrm_presets():
    """VRM 1.0 preset names must reach receivers as VRM 0.x blendshape names."""
    sender, client = _enabled_sender()

    assert sender.send_frame(
        {
            "bones": [],
            "expressions": [
                {"name": "neutral", "value": 1.0},
                {"name": "surprised", "value": 0.5},
                {"name": "lookUp", "value": 0.25},
            ],
        }
    )
    assert ("/VMC/Ext/Blend/Val", ["Neutral", 1.0]) in client.messages
    assert ("/VMC/Ext/Blend/Val", ["Surprised", 0.5]) in client.messages
    assert ("/VMC/Ext/Blend/Val", ["LookUp", 0.25]) in client.messages


@contextlib.contextmanager
def _captured_warnings():
    """Collect vmc_sender warnings.

    N.E.K.O. loggers do not propagate to root, so caplog's root-mounted
    handler never receives these records; attach the collector to the module
    logger itself instead.
    """
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collector(level=logging.WARNING)
    logger = vmc_sender_module.logger
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


@pytest.mark.unit
def test_bone_overflow_warns_once():
    """Dropping bones must be audible in the log, but only on the first frame."""
    sender, client = _enabled_sender()
    cap = vmc_sender_module._MAX_BONES_PER_FRAME
    frame = {
        "bones": [
            {
                "name": "hips",
                "px": 0.0, "py": 0.0, "pz": 0.0,
                "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0,
            }
        ]
        * (cap + 5),
        "expressions": [],
    }

    with _captured_warnings() as records:
        assert sender.send_frame(frame)
        assert sender.send_frame(frame)

    warnings = [r for r in records if "bones" in r.getMessage()]
    assert len(warnings) == 1
    assert str(cap) in warnings[0].getMessage()
    assert len([a for a, _ in client.messages if a == "/VMC/Ext/Bone/Pos"]) == cap * 2


@pytest.mark.unit
def test_expression_overflow_warns_once():
    """A VRM with hundreds of custom expressions must not silently lose them."""
    sender, client = _enabled_sender()
    cap = vmc_sender_module._MAX_EXPRESSIONS_PER_FRAME
    frame = {
        "bones": [],
        "expressions": [{"name": f"custom{i}", "value": 0.0} for i in range(cap + 5)],
    }

    with _captured_warnings() as records:
        assert sender.send_frame(frame)
        assert sender.send_frame(frame)

    warnings = [r for r in records if "expressions" in r.getMessage()]
    assert len(warnings) == 1
    assert len([a for a, _ in client.messages if a == "/VMC/Ext/Blend/Val"]) == cap * 2


@pytest.mark.unit
def test_no_overflow_warning_for_normal_frames():
    """A full humanoid rig is well under the cap and must stay quiet."""
    sender, _client = _enabled_sender()
    frame = {
        "bones": [
            {
                "name": name,
                "px": 0.0, "py": 0.0, "pz": 0.0,
                "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0,
            }
            for name in vmc_sender_module._VRM_BONE_NAMES
        ],
        "expressions": [{"name": "happy", "value": 1.0}],
    }

    with _captured_warnings() as records:
        assert sender.send_frame(frame)

    assert not [r for r in records if "only the first" in r.getMessage()]
    # 完整人形骨架有 55 根，必须全部送出，不能被 cap 削掉。
    sent = [a for a, _ in _client.messages if a == "/VMC/Ext/Bone/Pos"]
    assert len(sent) == len(vmc_sender_module._VRM_BONE_NAMES)


@pytest.mark.unit
def test_expression_cap_matches_between_sampler_and_sender():
    """The sampler truncates first; the backend cap is only the second gate.

    With the constant written independently on each side, raising the backend
    one alone has no effect — the frame was already silently truncated in the
    browser. This turns the "keep these in sync" comment into an executable
    assertion.
    """
    source = Path("static/vrm/vrm-vmc-sender.js").read_text(encoding="utf-8")
    match = re.search(r"const MAX_EXPRESSIONS_PER_FRAME = (\d+);", source)
    assert match is not None, "sampler lost its named expression cap"
    assert int(match.group(1)) == vmc_sender_module._MAX_EXPRESSIONS_PER_FRAME


@pytest.mark.unit
@pytest.mark.parametrize(
    "suite_name",
    ["vmc_websocket_isolation.test.cjs", "vmc_expression_budget.test.cjs"],
)
def test_vmc_frontend_node_suites(suite_name: str) -> None:
    """pytest entry point for the two node:test suites.

    ``unit-tests.yml`` only runs ``pytest tests/unit``, so a ``.test.cjs`` file
    with no pytest caller never executes in CI no matter what it asserts. Both
    of these suites landed without one — they were green locally and dead in the
    pipeline. Parametrised by file name rather than globbed, because these two
    are the VMC pair; other suites keep their own entry points next to the code
    they cover.
    """
    import shutil

    from tests.node_harness import run_node_script

    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node not found")

    suite_path = Path(__file__).resolve().parents[2] / "tests" / "frontend" / suite_name
    result = run_node_script(
        node_path,
        suite_path.read_text(encoding="utf-8"),
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
