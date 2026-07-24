# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
# Licensed under the Apache License, Version 2.0

"""VMC Protocol OSC sender used by the dedicated ``/api/vmc/ws`` channel.

The browser samples three-vrm transforms in three.js' right-handed coordinate
system.  This module validates the payload, converts it to Unity/VMC's
left-handed convention and emits VMC 2.0 compatible OSC messages over UDP.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_json_async, read_json_async
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 39539
_DEFAULT_SEND_RATE_HZ = 60
_CONFIG_FILENAME = "vmc_config.json"
_CONFIG_VERSION = 2
_LOCAL_ROOT_TRANSFORM = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]

_VRM_BONE_NAMES = (
    "hips", "spine", "chest", "upperChest", "neck", "head",
    "leftEye", "rightEye", "jaw",
    "leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand",
    "rightShoulder", "rightUpperArm", "rightLowerArm", "rightHand",
    "leftUpperLeg", "leftLowerLeg", "leftFoot", "leftToes",
    "rightUpperLeg", "rightLowerLeg", "rightFoot", "rightToes",
    "leftThumbMetacarpal", "leftThumbProximal", "leftThumbDistal",
    "leftIndexProximal", "leftIndexIntermediate", "leftIndexDistal",
    "leftMiddleProximal", "leftMiddleIntermediate", "leftMiddleDistal",
    "leftRingProximal", "leftRingIntermediate", "leftRingDistal",
    "leftLittleProximal", "leftLittleIntermediate", "leftLittleDistal",
    "rightThumbMetacarpal", "rightThumbProximal", "rightThumbDistal",
    "rightIndexProximal", "rightIndexIntermediate", "rightIndexDistal",
    "rightMiddleProximal", "rightMiddleIntermediate", "rightMiddleDistal",
    "rightRingProximal", "rightRingIntermediate", "rightRingDistal",
    "rightLittleProximal", "rightLittleIntermediate", "rightLittleDistal",
)

# VMC requires the exact Unity HumanBodyBones member spelling. VRM 1.0 renamed
# the first two thumb joints, so those six names cannot use title-casing:
# ``thumbMetacarpal`` maps to Unity's ``ThumbProximal`` and VRM's
# ``thumbProximal`` maps to Unity's ``ThumbIntermediate``.
_BONE_NAME_MAP = {name: name[0].upper() + name[1:] for name in _VRM_BONE_NAMES}
_BONE_NAME_MAP.update(
    {
        "leftThumbMetacarpal": "LeftThumbProximal",
        "leftThumbProximal": "LeftThumbIntermediate",
        "leftThumbDistal": "LeftThumbDistal",
        "rightThumbMetacarpal": "RightThumbProximal",
        "rightThumbProximal": "RightThumbIntermediate",
        "rightThumbDistal": "RightThumbDistal",
    }
)
_BONE_NAME_MAP.update({unity: unity for unity in set(_BONE_NAME_MAP.values())})

# VMC blendshape names use the VRM 0.x preset vocabulary even for VRM 1.0.
_EXPRESSION_NAME_MAP = {
    "happy": "Joy",
    "angry": "Angry",
    "sad": "Sorrow",
    "relaxed": "Fun",
    "aa": "A",
    "ih": "I",
    "ou": "U",
    "ee": "E",
    "oh": "O",
    "blink": "Blink",
    "blinkLeft": "Blink_L",
    "blinkRight": "Blink_R",
}


class VmcSender:
    """Process-wide, lazily configured VMC UDP sender."""

    def __init__(self, config_dir: Path | None) -> None:
        self._config_path = config_dir / _CONFIG_FILENAME if config_dir else None
        self._enabled = False
        self._host = _DEFAULT_HOST
        self._port = _DEFAULT_PORT
        self._send_rate_hz = _DEFAULT_SEND_RATE_HZ
        self._min_interval = 1.0 / _DEFAULT_SEND_RATE_HZ
        self._send_tokens = 2.0
        self._last_token_refill_ts = time.monotonic()
        self._started_at = time.monotonic()
        self._client: Any = None
        self._lock = asyncio.Lock()
        self._config_load_lock = asyncio.Lock()
        self._send_lock = threading.Lock()
        self._t_pose_lock = threading.Lock()
        self._config_loaded = False
        self._t_pose_requested = False
        self._t_pose_duration_sec = 2.0
        self._t_pose_generation = 0
        self._active_expression_names: set[str] = set()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def send_rate_hz(self) -> int:
        return self._send_rate_hz

    @property
    def config_path(self) -> Path | None:
        return self._config_path

    def status(self) -> dict[str, Any]:
        with self._t_pose_lock:
            t_pose_requested = self._t_pose_requested
            t_pose_duration_sec = self._t_pose_duration_sec
            t_pose_generation = self._t_pose_generation
        return {
            "enabled": self._enabled,
            "host": self._host,
            "port": self._port,
            "send_rate_hz": self._send_rate_hz,
            "config_path": str(self._config_path) if self._config_path else None,
            "t_pose_requested": t_pose_requested,
            "t_pose_duration_sec": t_pose_duration_sec,
            "t_pose_generation": t_pose_generation,
        }

    def request_t_pose(self, duration_sec: float | None = None) -> int:
        with self._t_pose_lock:
            if (
                duration_sec is not None
                and math.isfinite(duration_sec)
                and duration_sec > 0
            ):
                self._t_pose_duration_sec = min(float(duration_sec), 10.0)
            self._t_pose_generation += 1
            self._t_pose_requested = True
            return self._t_pose_generation

    async def ensure_config_loaded(self) -> None:
        """Load persisted endpoint settings once before any API operation."""
        if self._config_loaded:
            return
        async with self._config_load_lock:
            if self._config_loaded:
                return
            await self.load_config()
            self._config_loaded = True

    async def load_config(self) -> None:
        if not self._config_path:
            return
        try:
            data = await read_json_async(self._config_path)
            if not isinstance(data, dict):
                return
            host = data.get("host")
            if isinstance(host, str) and host:
                self._host = host
            port = data.get("port")
            if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535:
                self._port = port
            rate = data.get("send_rate_hz")
            if isinstance(rate, int) and not isinstance(rate, bool) and 1 <= rate <= 120:
                # Version 1 used 30 Hz as its implicit default. Migrate that
                # value so existing installations pick up the new 60 Hz
                # default instead of remaining silently pinned to 30.
                if data.get("config_version") is None and rate == 30:
                    rate = _DEFAULT_SEND_RATE_HZ
                self._send_rate_hz = rate
                self._min_interval = 1.0 / rate
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("VMC config load failed (%s); using defaults", exc)

    async def save_config(self) -> None:
        if not self._config_path:
            return
        await atomic_write_json_async(
            self._config_path,
            {
                "config_version": _CONFIG_VERSION,
                "enabled": self._enabled,
                "host": self._host,
                "port": self._port,
                "send_rate_hz": self._send_rate_hz,
            },
            ensure_ascii=False,
            indent=2,
        )

    async def _save_config_best_effort(self) -> None:
        """Persist settings without contradicting an applied runtime change."""
        try:
            await self.save_config()
        except Exception as exc:
            # The UDP operation has already completed and cannot always be
            # rolled back (the prior socket may be closed). Report the runtime
            # truth to callers and retry persistence on the next mutation.
            logger.warning("VMC config save failed; runtime state remains active: %s", exc)

    async def enable(
        self,
        host: str | None = None,
        port: int | None = None,
        send_rate_hz: int | None = None,
    ) -> dict[str, Any]:
        await self.ensure_config_loaded()
        async with self._lock:
            candidate_host = host if host is not None else self._host
            candidate_port = port if port is not None else self._port
            candidate_rate = (
                send_rate_hz
                if send_rate_hz is not None
                else self._send_rate_hz
            )
            endpoint_changed = (
                candidate_host != self._host
                or candidate_port != self._port
            )
            replacement_client: Any = None
            if endpoint_changed or self._client is None:
                # SimpleUDPClient resolves hostnames with socket.getaddrinfo in
                # its constructor. Keep that potentially blocking DNS lookup
                # off FastAPI's event-loop thread. Build before mutating any
                # live state so a failed reconfiguration leaves the working
                # sender untouched.
                replacement_client = await asyncio.to_thread(
                    self._build_client,
                    candidate_host,
                    candidate_port,
                )
            if replacement_client is not None:
                # A frame worker can hold _send_lock across the OSC datagrams
                # for one frame. Wait for the atomic swap off the event loop.
                await asyncio.to_thread(
                    self._replace_client,
                    replacement_client,
                )
            self._host = candidate_host
            self._port = candidate_port
            self._send_rate_hz = candidate_rate
            self._min_interval = 1.0 / candidate_rate
            self._enabled = True
            self._started_at = time.monotonic()
            self._send_tokens = 2.0
            self._last_token_refill_ts = self._started_at
            await self._save_config_best_effort()
            logger.info(
                "VMC sender enabled -> %s:%d @ %dHz",
                self._host,
                self._port,
                self._send_rate_hz,
            )
            return self.status()

    async def disable(self) -> dict[str, Any]:
        await self.ensure_config_loaded()
        async with self._lock:
            # Serialize the terminal VMC state behind any in-flight frame,
            # then close the socket off FastAPI's event-loop thread.
            await asyncio.to_thread(self._disable_client)
            await self._save_config_best_effort()
            logger.info("VMC sender disabled")
            return self.status()

    def _build_client(
        self,
        host: str | None = None,
        port: int | None = None,
    ) -> Any:
        from pythonosc.udp_client import SimpleUDPClient

        return SimpleUDPClient(
            host if host is not None else self._host,
            port if port is not None else self._port,
        )

    def _replace_client(self, replacement: Any) -> None:
        """Retire the prior endpoint, then atomically install ``replacement``."""
        with self._send_lock:
            prior = self._client
            if prior is not None and prior is not replacement:
                self._send_terminal_state_to_client(prior)
                self._close_specific_client(prior)
                self._active_expression_names.clear()
            self._client = replacement

    def _disable_client(self) -> None:
        """Publish an unavailable state and close the client under the send lock."""
        with self._send_lock:
            self._enabled = False
            if self._client is not None:
                self._send_terminal_state_to_client(self._client)
            self._active_expression_names.clear()
            self._close_client_locked()

    def _close_client(self) -> None:
        with self._send_lock:
            self._enabled = False
            if self._client is not None:
                self._send_terminal_state_to_client(self._client)
            self._active_expression_names.clear()
            self._close_client_locked()

    def _close_client_locked(self) -> None:
        if self._client is None:
            return
        client = self._client
        self._client = None
        self._close_specific_client(client)

    @staticmethod
    def _close_specific_client(client: Any) -> None:
        try:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        except Exception as exc:
            logger.debug("VMC client close error (ignored): %s", exc)

    def _send_terminal_state_to_client(self, client: Any) -> None:
        """Best-effort VMC teardown for a connectionless UDP receiver."""
        try:
            if self._active_expression_names:
                for name in sorted(self._active_expression_names):
                    client.send_message("/VMC/Ext/Blend/Val", [name, 0.0])
                client.send_message("/VMC/Ext/Blend/Apply", [])
        except Exception as exc:
            logger.warning("VMC expression reset send failed: %s", exc)
        try:
            # Attempt the authoritative unavailable state even if an optional
            # blend reset above failed.
            client.send_message("/VMC/Ext/OK", [0])
        except Exception as exc:
            logger.warning("VMC unavailable state send failed: %s", exc)

    def send_frame(
        self,
        payload: dict[str, Any],
        *,
        force: bool = False,
    ) -> bool:
        """Validate and synchronously send one browser-sampled frame.

        The dedicated WebSocket calls this through ``asyncio.to_thread`` and
        awaits completion, so at most one frame per connection is in flight.
        Returns whether the frame passed the sender throttle.
        """
        with self._send_lock:
            return self._send_frame_locked(payload, force=force)

    def _send_frame_locked(
        self,
        payload: dict[str, Any],
        *,
        force: bool,
    ) -> bool:
        """Send under ``_send_lock`` so multiple 3D pages cannot interleave OSC."""
        if not self._enabled or self._client is None:
            return False
        now = time.monotonic()
        if not force and self._min_interval > 0:
            elapsed = max(0.0, now - self._last_token_refill_ts)
            self._last_token_refill_ts = now
            self._send_tokens = min(
                2.0,
                self._send_tokens + elapsed / self._min_interval,
            )
            if self._send_tokens < 1.0:
                return False
            self._send_tokens -= 1.0

        try:
            self._client.send_message("/VMC/Ext/OK", [1])
            self._client.send_message("/VMC/Ext/T", [float(now - self._started_at)])
            if bool(payload.get("t_pose")):
                payload_generation = payload.get("t_pose_generation")
                if (
                    isinstance(payload_generation, int)
                    and not isinstance(payload_generation, bool)
                ):
                    with self._t_pose_lock:
                        if payload_generation == self._t_pose_generation:
                            self._t_pose_requested = False
            # Webpage layout owns vrm.scene transforms. VMC has an independent
            # local origin, so never trust a browser-provided display root.
            self._send_root()
            bones = payload.get("bones")
            if isinstance(bones, list):
                for bone in bones[:64]:
                    self._send_bone(bone)
            expressions = payload.get("expressions")
            if isinstance(expressions, list):
                for expression in expressions[:256]:
                    sent_expression = self._send_blend_val(expression)
                    if sent_expression is not None:
                        name, value = sent_expression
                        if value > 0:
                            self._active_expression_names.add(name)
                        else:
                            self._active_expression_names.discard(name)
                self._client.send_message("/VMC/Ext/Blend/Apply", [])
            if payload.get("source_released") is True:
                self._client.send_message("/VMC/Ext/OK", [0])
                self._active_expression_names.clear()
            return True
        except Exception as exc:
            logger.warning("VMC frame send failed: %s", exc)
            return False

    def _send_root(self) -> None:
        self._client.send_message(
            "/VMC/Ext/Root/Pos",
            ["root", *_LOCAL_ROOT_TRANSFORM],
        )

    def _send_bone(self, bone: Any) -> None:
        if not isinstance(bone, dict):
            return
        name = bone.get("name")
        unity_name = _BONE_NAME_MAP.get(name) if isinstance(name, str) else None
        if unity_name is None:
            return
        transform = self._extract_transform(bone)
        if transform is not None:
            self._client.send_message("/VMC/Ext/Bone/Pos", [unity_name, *transform])

    def _send_blend_val(
        self,
        expression: Any,
    ) -> tuple[str, float] | None:
        if not isinstance(expression, dict):
            return None
        name = expression.get("name")
        value = expression.get("value")
        if not isinstance(name, str) or not name or len(name) > 128:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
        vmc_name = _EXPRESSION_NAME_MAP.get(name, name)
        clamped_value = max(0.0, min(1.0, numeric))
        self._client.send_message(
            "/VMC/Ext/Blend/Val",
            [vmc_name, clamped_value],
        )
        return vmc_name, clamped_value

    @staticmethod
    def _extract_transform(data: dict[str, Any]) -> list[float] | None:
        """Convert a three.js RH transform to Unity/VMC LH coordinates.

        Reflection across the XY plane maps position ``(x,y,z)`` to
        ``(x,y,-z)`` and quaternion ``(x,y,z,w)`` to ``(-x,-y,z,w)``.
        """
        try:
            values = [
                float(data[key])
                for key in ("px", "py", "pz", "qx", "qy", "qz", "qw")
            ]
        except (KeyError, TypeError, ValueError):
            return None
        if not all(math.isfinite(value) for value in values):
            return None
        px, py, pz, qx, qy, qz, qw = values
        return [px, py, -pz, -qx, -qy, qz, qw]


_singleton: VmcSender | None = None


def get_vmc_sender() -> VmcSender:
    global _singleton
    if _singleton is not None:
        return _singleton
    try:
        from utils.config_manager import get_config_manager

        config_dir = getattr(get_config_manager(), "config_dir", None)
        config_dir = Path(config_dir) if config_dir is not None else None
    except Exception as exc:
        logger.warning("Failed to resolve config_dir for VmcSender: %s", exc)
        config_dir = None
    _singleton = VmcSender(config_dir)
    return _singleton


def reset_vmc_sender_cache() -> None:
    global _singleton
    if _singleton is not None:
        _singleton._close_client()
    _singleton = None
