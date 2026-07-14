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
"""Non-streaming and streaming reasoning-trace stripping."""

from __future__ import annotations
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_THINK_PAIRED_RE = re.compile(r"<think(?:ing)?\s*>.*?</think(?:ing)?\s*>", re.IGNORECASE | re.DOTALL)

_THINK_DANGLING_CLOSE_RE = re.compile(r"^.*?</think(?:ing)?\s*>", re.IGNORECASE | re.DOTALL)

_THINK_ANY_CLOSE_RE = re.compile(r"</think(?:ing)?\s*>", re.IGNORECASE)

def strip_thinking_segments(text: str | None) -> str:
    """Remove leaked chain-of-thought from a *non-streaming* model reply.

    Handles two shapes:
      1. Well-formed ``<think>...</think>`` blocks (any count).
      2. Qwen3.5/3.6 leak: reasoning dumped into ``content`` with only a
         dangling ``</think>`` (no opening tag) before the answer.

    Conservative — only acts when a think tag is present, so clean replies
    (qwen3-vl-*, gpt, claude, etc.) pass through untouched. Streaming is *not*
    covered here on purpose: when the chain-of-thought arrives token-by-token
    in ``delta.content`` with no delimiter there's nothing reliable to strip.
    """
    if not text:
        return text or ""
    s = str(text)
    # 1) drop well-formed blocks first
    s = _THINK_PAIRED_RE.sub("", s)
    # 2) any close tag still present is unmatched → preceding text is thinking
    if _THINK_ANY_CLOSE_RE.search(s):
        s = _THINK_DANGLING_CLOSE_RE.sub("", s, count=1)
    return s.strip()

class ThinkingStreamStripper:
    """Streaming-safe sibling of :func:`strip_thinking_segments`.

    ``strip_thinking_segments`` only runs on a *whole* non-streaming reply.
    Focus (thinking-on) turns stream token-by-token straight into TTS + the UI, so a
    provider that leaks chain-of-thought into ``content`` would speak its
    reasoning aloud. Only the Qwen3.5/3.6/3.7 hybrids do this: they dump the
    whole CoT into ``content`` terminated by a lone ``</think>`` (clean
    providers route reasoning to the separate ``reasoning_content`` field,
    which the streaming loop already withholds). So this holds **all** content
    until the first ``</think>`` (or a paired ``<think>...</think>``) is seen —
    dropping everything up to and including it — then passes the real answer
    through untouched, chunk by chunk.

    Engage it ONLY for ``thinking_on`` turns on a leak-prone model
    (``config.providers.leaks_thinking_in_content``): for clean providers the
    close tag never arrives, so holding-until-``</think>`` would withhold the
    whole answer until ``flush``. If a leak-prone model didn't think this turn
    (no close tag), ``flush`` returns the held buffer intact so nothing is lost.
    Split tags across chunks are safe — the buffer accumulates until matched.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._passthrough = False

    def feed(self, text: str) -> str:
        """Return the emittable slice of ``text`` (``""`` while still buffering)."""
        if self._passthrough:
            return text
        if not text:
            return ""
        self._buf += text
        m = _THINK_ANY_CLOSE_RE.search(self._buf)
        if m:
            # Everything up to and including the first close tag is the leaked
            # CoT (covers both the dangling shape and a paired <think>...</think>,
            # whose opening tag sits earlier in the buffer). Release the tail and
            # stream freely from here on.
            tail = self._buf[m.end():]
            self._buf = ""
            self._passthrough = True
            return tail
        return ""

    def flush(self) -> str:
        """Drain any held content at stream end (no close tag ever arrived)."""
        if self._passthrough:
            return ""
        residual = self._buf
        self._buf = ""
        return residual

    def reset(self) -> None:
        """Forget held state — used at a tool-round boundary, where the next
        segment is a fresh semantic unit and the one-shot CoT preamble (if any)
        is already behind us."""
        self._buf = ""
        self._passthrough = False
