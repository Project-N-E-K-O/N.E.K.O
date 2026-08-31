"""Frames Bus SDK — the last few frames the host pushed to the model provider.

Read the contract before building on this:

* These are COPIES of frames the host already sent. A plugin cannot ask for a
  capture, and a frame the session's throttle dropped was never sent, so it
  never appears here.
* ``source`` says where the picture came from, and it is not decoration.
  ``screen`` / ``camera`` / ``user`` are things the user shared with the
  character; ``plugin`` is media a plugin handed the model -- a tool result or
  callback attachment -- and the plugin that produced it is very likely not the
  one reading this. Tool-result frames additionally carry
  ``metadata["tool_name"]``. Check ``source`` before treating pixels as "what
  the user is looking at".
* This is NOT a log and NOT a queue. Frames are dropped by design at four
  points — the message-plane PUB socket is lossy for slow joiners and at HWM,
  the bridge publish is NOBLOCK, the bridge send queue is bounded and refuses
  frames once it is behind, and the store itself keeps only a handful
  (MESSAGE_PLANE_FRAMES_STORE_MAXLEN). A reader that assumes it will observe
  every frame, or that a frame it saw once is still there, will be wrong.
* Use ``generation`` / ``id`` to dedupe. ``generation`` comes from the
  session's own frame counter and only advances for cached ambient frames, so
  one-shot cue images can repeat a generation — ``id`` is the per-record key.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .types import BusRecord
from ._client_base import _PluginBusList, BusRpcClientBase


@dataclass(frozen=True, slots=True)
class FrameRecord(BusRecord):
    frame_id: Optional[str] = None
    image_base64: Optional[str] = None
    mime: Optional[str] = None
    captured_at: Optional[float] = None
    turn_id: Optional[str] = None
    generation: Optional[int] = None
    lanlan_name: Optional[str] = None

    @staticmethod
    def _shape(payload: Dict[str, Any], index: Optional[Dict[str, Any]] = None) -> "FrameRecord":
        idx = index if isinstance(index, dict) else {}

        ts_raw = payload.get("captured_at")
        if ts_raw is None:
            ts_raw = payload.get("timestamp")
        if ts_raw is None:
            ts_raw = idx.get("timestamp")
        timestamp: Optional[float] = float(ts_raw) if isinstance(ts_raw, (int, float)) else None

        source = payload.get("source") or idx.get("source")
        frame_id = payload.get("id") or idx.get("id")

        # Payload first, index second. A ``light=True`` read carries only the
        # index, and the index is projected from this very payload at publish
        # time, so the two cannot disagree. Without this fallback such a read
        # reported ``generation=None`` for a frame that has one, which quietly
        # defeats the dedupe the field is documented for.
        generation = payload.get("generation")
        if generation is None:
            generation = idx.get("generation")
        try:
            generation_int = int(generation) if generation is not None else None
        except (TypeError, ValueError):
            generation_int = None

        image_b64 = payload.get("image_base64")
        metadata = payload.get("metadata")

        return FrameRecord(
            kind="frame",
            type=str(payload.get("type") or idx.get("type") or "provider_frame"),
            timestamp=timestamp,
            plugin_id=None,  # frames are host-produced; no plugin owns one
            source=str(source) if source is not None else None,
            priority=0,
            content=None,
            metadata=metadata if isinstance(metadata, dict) else {},
            raw=payload,
            frame_id=str(frame_id) if frame_id is not None else None,
            image_base64=image_b64 if isinstance(image_b64, str) else None,
            mime=str(payload["mime"]) if isinstance(payload.get("mime"), str) else None,
            captured_at=timestamp,
            turn_id=str(payload["turn_id"]) if isinstance(payload.get("turn_id"), str) else None,
            generation=generation_int,
            lanlan_name=(
                str(payload["lanlan_name"]) if isinstance(payload.get("lanlan_name"), str) else None
            ),
        )

    @staticmethod
    def from_raw(raw: Dict[str, Any]) -> "FrameRecord":
        return FrameRecord._shape(raw if isinstance(raw, dict) else {"raw": raw})

    @staticmethod
    def from_index(index: Dict[str, Any], payload: Optional[Dict[str, Any]] = None) -> "FrameRecord":
        # The payload carries the image; the index carries only scalars. A
        # ``light=True`` query therefore yields records with image_base64=None
        # rather than a broken record — the caller asked for the index only.
        # Both dedupe keys survive that trip: ``id`` and ``generation`` are
        # projected into the index by TopicStore._extract_index.
        return FrameRecord._shape(payload if isinstance(payload, dict) else {}, index)

    def dump(self) -> Dict[str, Any]:
        base = BusRecord.dump(self)
        base["frame_id"] = self.frame_id
        base["image_base64"] = self.image_base64
        base["mime"] = self.mime
        base["captured_at"] = self.captured_at
        base["turn_id"] = self.turn_id
        base["generation"] = self.generation
        base["lanlan_name"] = self.lanlan_name
        return base


class FrameList(_PluginBusList[FrameRecord]):
    # 帧是快照，链式操作就地算完，不重放。文档里写的
    # ``frames = await bus.frames.get(...)`` 之后 ``.sort(...).limit(1)``
    # 走的正是这条路：没有这个开关，物化时会在事件循环里同步调
    # ``FrameClient.get()``，拿回一个协程当 list 用。conversations 那侧不需要
    # 同样的开关——它的列表根本不挂 plan，天生就是 eager。
    _snapshot_chain = True


class FrameClient(BusRpcClientBase):
    """``ctx.bus.frames.get()`` — pull only; there is no push subscription.

    Rides BusRpcClientBase unchanged, which pins ``topic="all"``. That is why
    the frames store publishes to ``"all"`` and not to a per-source topic: a
    second topic under an existing store would be invisible to every client
    except MessageClient, which is the only one that parameterises topic.
    """

    _store_name = "frames"
    _record_cls = FrameRecord
    _list_cls = FrameList
    _policy_prefix = "bus.frames"
