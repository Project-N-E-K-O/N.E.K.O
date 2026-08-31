from __future__ import annotations

import queue
import os
import secrets
import socket
import sys
import threading
import time
import uuid

import ormsgpack
import zmq
from plugin.logging_config import get_logger

from plugin.message_plane.stores import FRAMES_STORE_NAME, FRAMES_TOPIC
from plugin.settings import (
    MESSAGE_PLANE_BRIDGE_ENABLED,
    MESSAGE_PLANE_FRAMES_BRIDGE_MAX_PENDING,
    MESSAGE_PLANE_PAYLOAD_MAX_BYTES,
    MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT,
)

logger = get_logger("server.messaging.plane_bridge")

_RUNTIME_ERRORS = (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError)

# The ingest credential. The bridge is the only writer to the ingest socket, and
# the message plane it authenticates against is started by the same process (see
# ServerLifecycleService._start_message_plane), so a process-local secret is
# enough — plugin children reach the host over their own per-host uplink and have
# no business on this socket.
#
# ⚠️ "never leaves this process" is NOT free, which an earlier version of this
# comment claimed it was. Plugin hosts are started with a bare
# ``multiprocessing.Process`` (plugin/core/host.py), so on POSIX they are
# FORKED — the child inherits this module already imported, with this exact
# token, and plugin code can simply ``import`` it and write authenticated deltas
# straight to ingest. That bypasses the per-host uplink's identity stamping,
# which is the whole point of routing plugin messages through the host.
#
# So the child re-mints. Its value then matches nothing the plane accepts, which
# is the correct outcome: the two legitimate readers (``_dumps`` here, and
# ServerLifecycleService handing it to the ingest server) both live in the
# process that starts the plane, and neither has a counterpart in a child.
_INGEST_AUTH_TOKEN = secrets.token_urlsafe(32)


def _scrub_inherited_plane_credentials() -> None:
    """Blank the copies of the ingest token the child inherits elsewhere.

    Reminting the module global is not enough on its own: the same credential
    is handed to ``build_message_plane_runner`` at startup and lives on in
    ``plugin.server.lifecycle._service._message_plane_runner._auth_token``, and
    again on the ingest server that runner holds. Both are module-level
    singletons, so a forked plugin child inherits them and can read the real
    token straight off them.

    Swept generically over the runner's own attributes rather than named one by
    one: the runner keeps its servers in ``_rpc`` / ``_ingest`` / ``_pub``, and
    a fourth added later would otherwise keep the credential reachable with
    nothing failing to say so.

    Reads ``sys.modules`` instead of importing, for the same reason the host
    credential scrub in ``zmq_transport`` does: this runs in an
    ``after_in_child`` hook, where an import can deadlock on the import lock a
    parent thread held at fork time.
    """
    mod = sys.modules.get("plugin.server.lifecycle")
    service = getattr(mod, "_service", None) if mod is not None else None
    runner = getattr(service, "_message_plane_runner", None)
    if runner is None:
        return

    def _blank(obj: object) -> None:
        if obj is not None and hasattr(obj, "_auth_token"):
            try:
                obj._auth_token = ""
            except Exception:
                pass

    _blank(runner)
    try:
        held = list(vars(runner).values())
    except Exception:
        held = []
    for value in held:
        _blank(value)
    # 顺手把引用本身摘掉：子进程永远不该操作这个 plane。
    try:
        service._message_plane_runner = None
    except Exception:
        pass


def _remint_ingest_token_in_child() -> None:
    """Give a forked child a credential the host's plane will reject."""
    global _INGEST_AUTH_TOKEN
    _INGEST_AUTH_TOKEN = secrets.token_urlsafe(32)
    _scrub_inherited_plane_credentials()


# 注册这件事本身要可观测。本仓两个 pytest job 都跑 windows-latest，而 Windows
# 走 spawn——子进程无论如何都会自己重铸，于是"钩子根本没注册"这个变异在
# Windows 上完全测不出来。把注册结果记成模块状态，守卫就能在任何平台断言它，
# 哪怕真正的 fork 行为只有 POSIX 上跑得到。
_FORK_HOOK_REGISTERED = False

if hasattr(os, "register_at_fork"):
    # POSIX only; on spawn platforms the child re-imports and mints its own
    # anyway, so there is nothing to undo.
    os.register_at_fork(after_in_child=_remint_ingest_token_in_child)
    _FORK_HOOK_REGISTERED = True


def ingest_auth_token() -> str:
    """Credential the locally started message plane must accept."""
    return _INGEST_AUTH_TOKEN


def _dumps(obj: object) -> bytes:
    if not isinstance(obj, dict):
        raise TypeError("message-plane ingest payload must be an object")
    authenticated = dict(obj)
    authenticated["_auth"] = _INGEST_AUTH_TOKEN
    return ormsgpack.packb(authenticated)


def _parse_tcp_endpoint(endpoint: str) -> tuple[str, int] | None:
    ep = str(endpoint)
    if not ep.startswith("tcp://"):
        return None
    rest = ep[len("tcp://") :]
    if ":" not in rest:
        return None
    host, port_s = rest.rsplit(":", 1)
    try:
        port = int(port_s)
    except (ValueError, TypeError):
        return None
    host = host.strip() or "127.0.0.1"
    return host, port


class _Bridge:
    def __init__(self) -> None:
        self._endpoint = str(MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT)
        self._enabled = bool(MESSAGE_PLANE_BRIDGE_ENABLED)
        self._q: "queue.Queue[dict[str, object]]" = queue.Queue(maxsize=4096)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self._enabled:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        t = threading.Thread(target=self._run, daemon=True)
        self._thread = t
        t.start()

    def stop(self) -> None:
        try:
            self._stop.set()
        except _RUNTIME_ERRORS:
            pass

    def enqueue_delta(
        self,
        *,
        store: str,
        topic: str,
        payload: dict[str, object],
        max_queue_depth: int | None = None,
    ) -> bool:
        """Queue one record for the ingest socket. Returns whether it was queued.

        ``max_queue_depth`` lets a lossy, oversized producer yield to the small
        records that share this queue: when the bridge is already ``N`` records
        behind, the caller is refused instead of adding to the backlog. Left at
        ``None`` (every pre-existing caller) the only bound is the queue's own
        maxsize, exactly as before.
        """
        if not self._enabled:
            return False
        if max_queue_depth is not None:
            try:
                if self._q.qsize() >= int(max_queue_depth):
                    return False
            except NotImplementedError:
                # qsize() is documented as unreliable/absent on some platforms.
                # Losing the ceiling degrades to the old behaviour; refusing the
                # record instead would be worse.
                pass
        msg = {
            "v": 1,
            "kind": "delta_batch",
            "from": "control_plane",
            "ts": time.time(),
            "batch_id": str(uuid.uuid4()),
            "items": [
                {
                    "store": str(store),
                    "topic": str(topic),
                    "payload": dict(payload) if isinstance(payload, dict) else {"value": payload},
                }
            ],
        }
        try:
            self._q.put_nowait(msg)
        except queue.Full:
            return False
        return True

    def enqueue_snapshot(
        self,
        *,
        store: str,
        topic: str,
        items: list[dict[str, object]],
        mode: str = "replace",
    ) -> None:
        if not self._enabled:
            return
        msg = {
            "v": 1,
            "kind": "snapshot",
            "from": "control_plane",
            "ts": time.time(),
            "store": str(store),
            "topic": str(topic),
            "mode": str(mode),
            "items": list(items) if isinstance(items, list) else [],
        }
        try:
            self._q.put_nowait(msg)
        except queue.Full:
            return

    def _wait_tcp_ready(self, endpoint: str) -> None:
        parsed = _parse_tcp_endpoint(endpoint)
        if parsed is None:
            return
        host, port = parsed
        while not self._stop.is_set():
            try:
                with socket.create_connection((host, port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.2)

    def _run(self) -> None:
        try:
            self._wait_tcp_ready(self._endpoint)
        except _RUNTIME_ERRORS:
            pass
        if self._stop.is_set():
            return

        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.PUSH)
        sock.linger = 0
        try:
            sock.connect(self._endpoint)
        except (RuntimeError, ValueError, TypeError, AttributeError, OSError, zmq.ZMQError) as err:
            try:
                logger.warning("[message_plane_bridge] connect failed: {}", err)
            except _RUNTIME_ERRORS:
                pass
            try:
                sock.close(0)
            except (RuntimeError, ValueError, TypeError, AttributeError, OSError, zmq.ZMQError):
                pass
            return

        try:
            while not self._stop.is_set():
                try:
                    msg = self._q.get(timeout=0.2)
                except queue.Empty:
                    continue
                except _RUNTIME_ERRORS:
                    continue
                try:
                    sock.send(_dumps(msg), flags=zmq.NOBLOCK)
                except (RuntimeError, ValueError, TypeError, AttributeError, OSError, zmq.ZMQError):
                    continue
        finally:
            try:
                sock.close(0)
            except (RuntimeError, ValueError, TypeError, AttributeError, OSError, zmq.ZMQError):
                pass


_bridge = _Bridge()


def start_bridge() -> None:
    _bridge.start()


def stop_bridge() -> None:
    _bridge.stop()


def refresh_ingest_endpoint() -> str:
    """Re-read the ingest endpoint the plane actually bound, and return it.

    ``_bridge`` is a module-level singleton built when this module is first
    imported, and it freezes ``MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT`` — a value
    ``plugin.settings`` read from the environment at ITS import. That is before
    ``build_message_plane_runner()`` runs, and the runner moves the plane to a
    fallback port when the configured one is occupied
    (``_resolve_endpoint_with_fallback``), publishing the new address by
    writing ``NEKO_MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT`` back into the
    environment.

    Without this, a port collision sends every queued record — plugin messages,
    frames, conversations — to the occupied endpoint while ``push_message()``
    has already answered ``submitted=True``, which is the exact silent
    non-delivery this whole path exists to remove.

    Deliberately explicit rather than a re-read inside ``start()``: callers that
    set ``_endpoint`` themselves (the in-process transport tests bind inproc
    addresses) must not have it overwritten from the environment underneath
    them. The lifecycle service is the one place that knows the runner has
    already chosen, so it is the one place that asks.
    """
    endpoint = os.getenv(
        "NEKO_MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT",
        os.getenv("NEKO_MESSAGE_PLANE_INGEST", str(MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT)),
    )
    _bridge._endpoint = str(endpoint)
    return _bridge._endpoint


def publish_record(*, store: str, record: dict[str, object], topic: str = "all") -> bool:
    """Queue one record for the plane. Returns whether it was accepted.

    The result used to be dropped. It is reported now because one caller --
    the plugin message uplink -- is the only thing standing between
    ``push_message()`` and the character actually speaking, and a silent
    refusal there is indistinguishable from a message that was delivered.
    """
    return bool(_bridge.enqueue_delta(store=store, topic=topic, payload=record))


def publish_snapshot(
    *,
    store: str,
    records: list[dict[str, object]],
    topic: str = "all",
    mode: str = "replace",
) -> None:
    _bridge.enqueue_snapshot(store=store, topic=topic, items=records, mode=mode)


# ── Provider frames ────────────────────────────────────────────────────
#
# What travels here is a COPY of a frame the host already pushed to the model
# provider. Nothing on this path can ask for a capture: a frame the session's
# throttle dropped was never sent, so it is never copied, and the bytes are the
# post-compression ones the provider actually received.
#
# The contract for readers is "the last few frames the provider got", never a
# log. Everything below is lossy on purpose -- see ``publish_frame``.

# One warning per minute per reason. A frame-rate bug would otherwise turn the
# terminal drop reasons into a log flood, which is how the ingest server's own
# always-on warnings are throttled too.
_FRAME_WARN_THROTTLE_SECONDS = 60.0
_frame_warn_last: dict[str, float] = {}
_frame_warn_suppressed: dict[str, int] = {}


def _warn_frame_drop(reason: str, detail: str) -> None:
    now = time.time()
    last = _frame_warn_last.get(reason, 0.0)
    if last and (now - last) < _FRAME_WARN_THROTTLE_SECONDS:
        _frame_warn_suppressed[reason] = _frame_warn_suppressed.get(reason, 0) + 1
        return
    suppressed = _frame_warn_suppressed.pop(reason, 0)
    _frame_warn_last[reason] = now
    try:
        if suppressed:
            logger.warning(
                "[message_plane_bridge] frame dropped reason={} {} suppressed={}",
                reason, detail, suppressed,
            )
        else:
            logger.warning("[message_plane_bridge] frame dropped reason={} {}", reason, detail)
    except _RUNTIME_ERRORS:
        pass


def _find_raw_bytes(value: object, path: str = "") -> str | None:
    """Return the path of the first ``bytes``-like value, else ``None``.

    A frame must ride as ONE base64 string. A raw twin packs cleanly and takes
    the wire cost from 1.33x the image to about 2.3x (measured on
    game_agent_minecraft), which is what pushes a large frame past
    MESSAGE_PLANE_PAYLOAD_MAX_BYTES -- and that drop happens on the other side
    of a socket, so the publisher is told nothing. Refusing the record here
    turns a silent far-side loss into a local, named error.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        return path or "<root>"
    if isinstance(value, dict):
        for key, item in value.items():
            found = _find_raw_bytes(item, f"{path}.{key}" if path else str(key))
            if found is not None:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            found = _find_raw_bytes(item, f"{path}[{i}]")
            if found is not None:
                return found
        return None
    return None


def build_frame_record(
    *,
    image_base64: str,
    source: str,
    captured_at: float | None = None,
    turn_id: str | None = None,
    generation: int | None = None,
    mime: str = "image/jpeg",
    lanlan_name: str | None = None,
    frame_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    """Shape one frames-store record.

    Split out from :func:`publish_frame` so the wire shape can be asserted
    without a bridge, and so the size probe measures the same dict that would
    have been queued.

    ``timestamp`` mirrors ``captured_at`` because ``TopicStore._extract_index``
    indexes on ``timestamp``; ``id`` is what that indexer picks up for the
    record id, so a puller can dedupe without unpacking the payload.
    """
    ts = float(captured_at) if isinstance(captured_at, (int, float)) else time.time()
    record: dict[str, object] = {
        "kind": "frame",
        "type": "provider_frame",
        "source": str(source or "unknown"),
        "timestamp": ts,
        "captured_at": ts,
        "id": str(frame_id or uuid.uuid4()),
        "mime": str(mime or "image/jpeg"),
        "image_base64": str(image_base64 or ""),
    }
    if turn_id is not None:
        record["turn_id"] = str(turn_id)
    if generation is not None:
        record["generation"] = int(generation)
    if lanlan_name is not None:
        record["lanlan_name"] = str(lanlan_name)
    if isinstance(metadata, dict) and metadata:
        record["metadata"] = dict(metadata)
    return record


def publish_frame(record: dict[str, object]) -> bool:
    """Queue one provider frame for the ``frames`` store. Returns acceptance.

    Three refusals, all deliberate and all lossy:

    * ``raw_bytes`` -- the record carries a bytes-like value somewhere. See
      :func:`_find_raw_bytes`.
    * ``payload_too_big`` -- the packed record exceeds
      MESSAGE_PLANE_PAYLOAD_MAX_BYTES. Ingest measures the same bound on the
      far side and would drop it there; measuring here means the failure is
      attributable and costs no queue slot and no socket write.
    * ``bridge_backlog`` -- the shared send queue is already
      MESSAGE_PLANE_FRAMES_BRIDGE_MAX_PENDING records deep. Not warned: it is
      ordinary host-side back-pressure, and the contract already says frames
      are dropped under load.

    ``False`` never means "retry". A frame that did not make it is simply gone;
    the next one is along shortly.
    """
    if not isinstance(record, dict):
        _warn_frame_drop("not_a_record", f"type={type(record).__name__}")
        return False

    offending = _find_raw_bytes(record)
    if offending is not None:
        _warn_frame_drop("raw_bytes", f"at={offending}")
        return False

    try:
        size = len(ormsgpack.packb(record))
    except (TypeError, ValueError) as err:
        _warn_frame_drop("pack_error", f"err={type(err).__name__}: {err}")
        return False
    limit = int(MESSAGE_PLANE_PAYLOAD_MAX_BYTES)
    if size > limit:
        _warn_frame_drop(
            "payload_too_big",
            f"source={record.get('source')} size={size} limit={limit}",
        )
        return False

    return _bridge.enqueue_delta(
        store=FRAMES_STORE_NAME,
        topic=FRAMES_TOPIC,
        payload=record,
        max_queue_depth=int(MESSAGE_PLANE_FRAMES_BRIDGE_MAX_PENDING),
    )
