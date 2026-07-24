from __future__ import annotations

import asyncio
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
def test_sender_token_bucket_preserves_average_rate_under_jitter(monkeypatch):
    sender, client = _enabled_sender()
    sender._min_interval = 1 / 60
    sender._send_tokens = 0.0
    sender._last_token_refill_ts = 0.0
    timestamps = iter(index / 144 for index in range(145))
    monkeypatch.setattr(vmc_sender_module.time, "monotonic", lambda: next(timestamps))

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
    assert "if (state.exprBuf.length >= 256) break" in source
    assert "state.retiringExpressionNames.delete(name)" in source
    assert "message.type === 'frame_ack'" in source
    assert "messageType: 'release'" in source
    assert "Math.ceil(expressionNames.length / 256)" in source
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
    assert "this._lastRenderTime" not in manager_source
    assert "t_pose_generation: state.tPoseGeneration" in source


class _FakeSender:
    def __init__(self) -> None:
        self.frames: list[dict[str, object]] = []
        self.frame_received = threading.Event()
        self.t_pose_generation = 0
        self.t_pose_duration_sec = 2.0
        self.force_values: list[bool] = []

    async def ensure_config_loaded(self) -> None:
        return None

    def send_frame(
        self,
        payload: dict[str, object],
        *,
        force: bool = False,
    ) -> bool:
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


@pytest.fixture
def vmc_client(monkeypatch):
    sender = _FakeSender()
    monkeypatch.setattr(vmc_router, "_active_vmc_publisher", None)
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
def test_release_frame_bypasses_throttle_and_is_acknowledged(vmc_client):
    client, sender = vmc_client
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
                "payload": {"bones": [], "expressions": []},
            }
        )
        assert websocket.receive_json() == {
            "type": "frame_ack",
            "sequence": 7,
            "sent": True,
        }
    assert sender.frames == [{"bones": [], "expressions": []}]
    assert sender.force_values == [True]


@pytest.mark.unit
def test_release_is_ordered_after_an_in_flight_frame(vmc_client):
    client, sender = vmc_client
    normal_started = threading.Event()
    allow_normal_to_finish = threading.Event()
    original_send_frame = sender.send_frame

    def ordered_send_frame(payload, *, force=False):
        if not force:
            normal_started.set()
            assert allow_normal_to_finish.wait(timeout=1.0)
        return original_send_frame(payload, force=force)

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
