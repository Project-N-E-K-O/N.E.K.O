"""Optional metadata-only sync timing; observation failures never affect sync."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import re
import threading
import time
from uuid import uuid4


_ID = re.compile(r"[0-9a-f]{32}\Z")
_EVENTS = frozenset({
    "source_committed", "session_begin", "session_end", "channel_wait", "channel_enter", "channel_end",
    "http_begin", "http_send", "http_headers", "http_body_complete", "http_end",
    "upload_ack_committed", "download_committed", "history_notify_begin", "history_notify_end",
    "assistant_source_selected", "assistant_source_excluded", "assistant_source_finished",
    "local_reply_committed",
})
_ROUTES = frozenset({"identity", "bindings", "body_batch", "deletion", "changes", "profile", "recovery", "character", "other"})
_trace = None


class ChatSyncTrace:
    """Append small clock-stamped events only when a process explicitly opts in."""

    def __init__(self, path=None):
        self.path = path
        self.clock_id = "desktop-" + uuid4().hex
        self._sequence = 0
        self._lock = threading.Lock()
        self._context = ContextVar("chat_sync_trace_context", default={})

    @property
    def enabled(self):
        return bool(self.path)

    def span_id(self):
        return uuid4().hex if self.enabled else None

    @staticmethod
    def instant():
        """Capture the observed boundary before any optional metadata lookup."""
        return time.time_ns() / 1_000_000, time.monotonic_ns() / 1_000_000

    @contextmanager
    def scope(self, **fields):
        if not self.enabled:
            yield
            return
        token = self._context.set({**self._context.get(), **fields})
        try:
            yield
        finally:
            self._context.reset(token)

    def emit(self, event, *, at=None, **fields):
        """Drop unknown fields and untrusted free text, including exception text."""
        if not self.enabled or event not in _EVENTS:
            return
        try:
            wall, mono = at or self.instant()
            context = self._context.get()
            details = {**context, **fields}
            if fields.get("span_id") and context.get("span_id") != fields["span_id"]:
                details["parent_span_id"] = context.get("span_id")
            safe = {}
            for key in ("span_id", "parent_span_id", "character_uid", "message_id", "local_message_id"):
                value = details.get(key)
                if isinstance(value, str) and _ID.fullmatch(value):
                    safe[key] = value
            if details.get("operation") in ("body", "pull", "deletion"):
                safe["operation"] = details["operation"]
            if details.get("route") in _ROUTES:
                safe["route"] = details["route"]
            if details.get("outcome") in ("success", "error", "cancelled"):
                safe["outcome"] = details["outcome"]
            if details.get("source_origin") in ("chat", "proactive", "agent", "avatar", "system", "tool", "mirror"):
                safe["source_origin"] = details["source_origin"]
            if details.get("source_reason") in (
                "nonchat_origin", "unknown_origin", "mirror", "invalid_identity", "invalid_text",
                "saved", "save_unavailable", "save_failed", "terminal_excluded",
                "local_saved", "local_save_unavailable", "local_save_failed",
            ):
                safe["source_reason"] = details["source_reason"]
            for key in ("cursor", "status"):
                value = details.get(key)
                if type(value) is int and 0 <= value <= 2**53 - 1:
                    safe[key] = value
            with self._lock:
                self._sequence += 1
                record = {"v": 1, "event": event, "clock_id": self.clock_id, "seq": self._sequence,
                          "wall_ms": wall, "mono_ms": mono, **safe}
                raw = (json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
                # One append has no fsync and does not touch a business database.
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    if os.write(descriptor, raw) != len(raw):
                        return
                finally:
                    os.close(descriptor)
        except Exception:
            # Sequence gaps expose failed observations to the report reader.
            pass


def get_trace():
    """Read the process-only option once, without changing user configuration."""
    global _trace
    if _trace is None:
        _trace = ChatSyncTrace(os.environ.get("NEKO_CHAT_SYNC_TRACE_FILE"))
    return _trace


def route_class(path):
    """Classify known routes without persisting a URL, query, or arbitrary path."""
    path = path.split("?", 1)[0]
    if path == "/v1/chat-sync/identity":
        return "identity"
    if "/recovery/" in path:
        return "recovery"
    if "/batches/" in path:
        return "body_batch"
    if "/deletions/" in path:
        return "deletion"
    for suffix, label in (("/bindings", "bindings"), ("/changes", "changes"), ("/profile", "profile")):
        if path.endswith(suffix):
            return label
    return "character" if re.fullmatch(r"/prototype/chat-sync/characters/[0-9a-f]{32}", path) else "other"
