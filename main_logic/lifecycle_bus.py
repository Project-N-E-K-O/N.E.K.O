# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""In-process named-event pub/sub for session lifecycle signals.

Generic and plugin-agnostic. ``emit(name, **data)`` invokes every handler
subscribed to ``name``; ``subscribe(name, handler)`` registers one and
returns an unsubscribe callable.

Signals in use today (one bus instance per session / ``LLMSessionManager``):

* ``voice_play_start`` / ``voice_play_end`` — FRONTEND-reported real audio
  playback boundaries. ``voice_play_end`` means the browser's audio queue
  has fully drained (she has actually STOPPED talking), which is strictly
  later than the realtime API's ``response.done`` (generation finished but
  buffered audio still playing). Proactive delivery keys off this so a new
  cue never fires while she is still audibly speaking.
* ``text_start`` / ``text_end`` — BACKEND text-delivery boundaries (offline
  client), so the same pacing applies to text mode.

A future PR may bridge this bus cross-process so plugin child processes can
subscribe; today there is no main→plugin event channel, only the
request/response downlink, so the bus stays in-process.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger("main_logic.lifecycle_bus")

Handler = Callable[..., Any]


class LifecycleEventBus:
    def __init__(self, *, name: str = "") -> None:
        self._name = name
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event: str, handler: Handler) -> Callable[[], None]:
        self._subs[event].append(handler)

        def _unsubscribe() -> None:
            try:
                self._subs[event].remove(handler)
            except ValueError:
                # Idempotent unsubscribe: the handler was already removed
                # (e.g. double-call). Nothing to do — intentionally benign.
                pass

        return _unsubscribe

    def emit(self, event: str, **data: Any) -> None:
        handlers = self._subs.get(event)
        if not handlers:
            return
        # Snapshot so a handler that (un)subscribes during dispatch can't
        # mutate the list we're iterating. Isolate handler failures — one
        # bad subscriber must not stop the rest from seeing the signal.
        for handler in list(handlers):
            try:
                handler(**data)
            except Exception:
                logger.exception(
                    "[lifecycle_bus%s] handler for %r raised",
                    f":{self._name}" if self._name else "",
                    event,
                )
