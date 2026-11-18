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

"""What is known about the model task agents use: whether it sees pictures, and how large its window is.

The keyboard-and-mouse tools work from screenshots, so they are only offered
to a task agent whose model can see them. Nothing in the configuration says
whether a model can, and asking the model costs a request, so this is how it
is decided:

* a model is taken to see pictures until its provider says otherwise. The
  agent model slot falls back to the vision model, and the keyboard-and-mouse
  loop this replaces sent screenshots to it without asking;
* when a provider says the model takes no pictures, the runtime sends that
  request once more without them and reports it (``model_capability``
  event). The model is then written down here, by its address and name,
  with what the provider said and when, and task agents started later are
  not offered the tools that need pictures. A provider that only would not
  take *those* pictures (their format, their size, how many there were) is
  not written down: that says nothing about the model;
* what was learned that way is believed for a while
  (``MODEL_IMAGE_REFUSAL_REMEMBERED_SECONDS``) and then tried again, so a
  model that was misjudged gets its tools back by itself;
* an entry can be set by hand: ``{"images": true}`` for a model keeps the
  tools on whatever a provider answered, ``{"images": false}`` keeps them
  off for good (it has no ``noted_at``, so it never lapses).

The context window a session is managed for is kept in the same entries:

* ``context_window``: what the user set for the model. Without it the
  setting ``TASK_AGENT_CONTEXT_WINDOW`` counts.
* ``learned_window``: what the model's provider said its window is, when it
  refused a request as too long (the runtime reads the number out of the
  refusal and reports it in a ``model_capability`` event). Believed for
  ``MODEL_CONTEXT_WINDOW_REMEMBERED_SECONDS``.

A session is managed for the smaller of the two (``context_window``), so a
model with a small window is not sent requests it must refuse. Nothing else
tells a model's window: the configuration has no field for it, and no table
of models is kept here.

Stored in ``<data root>/nekoclaw/model_capabilities.json``. It is read once,
on first use, and never at startup. A file that cannot be read counts as
empty: the worst that follows is one refused request, which is how it would
have been learned anyway.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FILE_VERSION = 1
# What an entry says about a model's window; kept when the rest of it is replaced.
_WINDOW_FIELDS = (
    "context_window",
    "learned_window",
    "window_noted_at",
    "compact_at",
    "window_reminded",
    "window_reminded_at",
)


# What a scheme is made of: a letter, then letters, digits, "+", "-" and ".".
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*")


def model_key(base_url: Any, model: Any) -> str:
    """Return the key a model is remembered by: its address and its name.

    The scheme and the host of an address are written in any case; its path
    is not, and two paths that differ by case may be two services.
    """
    address = str(base_url or "").strip().rstrip("/")
    # The first "://": a later one belongs to the path or to what follows it.
    scheme, separator, rest = address.partition("://")
    if not separator or not _SCHEME.fullmatch(scheme):
        # No scheme: the address begins with the host.
        scheme, separator, rest = "", "", address
    # The host ends where the path, the query or the fragment begins.
    ends = [at for at in (rest.find(mark) for mark in "/?#") if at != -1]
    end = min(ends) if ends else len(rest)
    authority, after = rest[:end], rest[end:]
    # Before the "@", if there is one: a name and a password, kept as written.
    credentials, at, host = authority.rpartition("@")
    return f"{scheme.lower()}{separator}{credentials}{at}{host.lower()}{after}|{str(model or '').strip()}"


class ModelCapabilityStore:
    """What was learned about models, kept across restarts."""

    def __init__(self, path: Path | Callable[[], Path]) -> None:
        self._path_source = path
        self._models: dict[str, dict[str, Any]] | None = None
        self._lock = asyncio.Lock()
        # The models whose smaller window the user is being told of this
        # moment (``claim_window_reminder``).
        self._reminding: set[str] = set()

    def _path(self) -> Path:
        source = self._path_source
        return Path(source() if callable(source) else source)

    def _read(self) -> dict[str, dict[str, Any]]:
        import json

        try:
            raw = json.loads(self._path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("the model capability file cannot be read (%s); starting empty", type(exc).__name__)
            return {}
        models = raw.get("models") if isinstance(raw, dict) else None
        if not isinstance(models, dict):
            return {}
        return {str(key): dict(value) for key, value in models.items() if isinstance(value, dict)}

    def _write(self, models: dict[str, dict[str, Any]]) -> None:
        from utils.file_utils import atomic_write_json

        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {"version": _FILE_VERSION, "models": models})

    async def _loaded(self) -> dict[str, dict[str, Any]]:
        if self._models is None:
            async with self._lock:
                if self._models is None:
                    self._models = await asyncio.to_thread(self._read)
        return self._models

    @staticmethod
    def _lapses_at(entry: dict[str, Any]) -> int | None:
        """Return when what was learned from a refusal stops being believed; None for what a hand wrote."""
        from config.nekoclaw_settings import MODEL_IMAGE_REFUSAL_REMEMBERED_SECONDS

        noted_at = entry.get("noted_at")
        if entry.get("images") is not False or isinstance(noted_at, bool) or not isinstance(noted_at, (int, float)):
            return None
        return int(noted_at) + MODEL_IMAGE_REFUSAL_REMEMBERED_SECONDS

    def _believed(self, entry: dict[str, Any] | None) -> bool:
        """Whether an entry still says anything: there is one, and it has not lapsed."""
        if entry is None or not isinstance(entry.get("images"), bool):
            return False
        lapses_at = self._lapses_at(entry)
        return lapses_at is None or time.time() < lapses_at

    async def sees_images(self, base_url: Any, model: Any) -> bool:
        """Return whether pictures are sent to this model: yes, unless it is known not to take them."""
        entry = (await self._loaded()).get(model_key(base_url, model))
        if not self._believed(entry):
            return True
        return entry.get("images") is not False

    async def describe(self, base_url: Any, model: Any) -> dict[str, Any]:
        """Return what is held about a model, for showing why it is taken to see pictures or not.

        ``sees``: whether pictures are sent to it. For a model taken not to
        see them: ``source`` (``"refused"`` when a provider said so, else
        ``"set by hand"``), and for a refusal ``reason`` (what the provider
        said), ``noted_at`` and ``lapses_at`` (seconds since the epoch).
        """
        entry = (await self._loaded()).get(model_key(base_url, model))
        if not self._believed(entry) or entry.get("images") is not False:
            return {"sees": True}
        lapses_at = self._lapses_at(entry)
        if lapses_at is None:
            return {"sees": False, "source": "set by hand"}
        return {
            "sees": False,
            "source": "refused",
            "reason": str(entry.get("reason") or ""),
            "noted_at": int(entry["noted_at"]),
            "lapses_at": lapses_at,
        }

    async def note_images_refused(self, base_url: Any, model: Any, reason: Any = "") -> bool:
        """Write down that this model's provider said it takes no pictures; return whether that was new.

        ``reason`` is what the provider said, kept (shortened) so that it can
        be seen later why the model was judged this way. An entry set by hand
        stays as it is: the user knows the model, and one refusal may have
        had another cause.
        """
        models = await self._loaded()
        key = model_key(base_url, model)
        async with self._lock:
            entry = models.get(key)
            if self._believed(entry):
                # Known already, or set by hand (only a hand writes true).
                return False
            models[key] = {
                # What is known of its window is another matter and stays.
                **{name: value for name, value in (entry or {}).items() if name in _WINDOW_FIELDS},
                "images": False,
                "source": "refused",
                "noted_at": int(time.time()),
                "reason": " ".join(str(reason or "").split())[:300],
            }
            await self._save(models)
        return True

    async def _save(self, models: dict[str, dict[str, Any]]) -> None:
        """Write the file; called with the lock held. What cannot be written stays in memory."""
        from utils.asyncio_executor import to_thread_through_cancellation

        try:
            await to_thread_through_cancellation(self._write, {name: dict(value) for name, value in models.items()})
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - kept in memory; written with the next change
            logger.warning("the model capability file could not be written", exc_info=True)

    # -- the context window -------------------------------------------------

    @staticmethod
    def _tokens(value: Any) -> int | None:
        """Return a window read from an entry, or None when it is not a usable number."""
        from config.nekoclaw_settings import TASK_AGENT_CONTEXT_WINDOW_MIN

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(value) or value <= 0:
            return None
        return max(TASK_AGENT_CONTEXT_WINDOW_MIN, int(value))

    def _learned(self, entry: dict[str, Any] | None) -> int | None:
        """Return the window a provider stated for a model, while that is believed."""
        from config.nekoclaw_settings import MODEL_CONTEXT_WINDOW_REMEMBERED_SECONDS

        if entry is None:
            return None
        learned = self._tokens(entry.get("learned_window"))
        noted_at = entry.get("window_noted_at")
        if learned is None or isinstance(noted_at, bool) or not isinstance(noted_at, (int, float)):
            return None
        if time.time() >= noted_at + MODEL_CONTEXT_WINDOW_REMEMBERED_SECONDS:
            return None
        return learned

    async def context_window(self, base_url: Any, model: Any) -> int:
        """Return the window a session with this model is managed for, in tokens.

        What the user set for the model, else what is written down for it
        (``TASK_AGENT_KNOWN_CONTEXT_WINDOWS``), else the setting; and no
        more than the model's provider said it takes, when that is known.
        """
        entry = (await self._loaded()).get(model_key(base_url, model))
        wanted = self._tokens((entry or {}).get("context_window")) or self._default_window(model)
        learned = self._learned(entry)
        return wanted if learned is None else min(wanted, learned)

    @staticmethod
    def _default_window(model: Any) -> int:
        """Return the window a model is managed for when the user set none: its own if written down, else the setting."""
        from config.nekoclaw_settings import TASK_AGENT_CONTEXT_WINDOW, TASK_AGENT_KNOWN_CONTEXT_WINDOWS

        known = TASK_AGENT_KNOWN_CONTEXT_WINDOWS.get(str(model or "").strip())
        return int(known) if known else TASK_AGENT_CONTEXT_WINDOW

    async def compact_at(self, base_url: Any, model: Any, window: int | None = None) -> int:
        """Return the length of a conversation with this model from which it is summed up, in tokens.

        What the user set for the model, when that lies inside the window
        its sessions are managed for. Else the same part of that window as
        the presets are of each other (``TASK_AGENT_COMPACT_AT_TOKENS`` of
        ``TASK_AGENT_CONTEXT_WINDOW``): a window that turned out smaller
        than the one the user's number was meant for is summed up in
        proportion, not at a point beyond its end.

        ``window`` is the window a session is in fact started with, when
        that is not what ``context_window`` says (the model's configuration
        names one): the point is of that window.
        """
        from config.nekoclaw_settings import TASK_AGENT_COMPACT_AT_TOKENS, TASK_AGENT_CONTEXT_WINDOW

        if window is None:
            window = await self.context_window(base_url, model)
        entry = (await self._loaded()).get(model_key(base_url, model))
        wanted = self._point((entry or {}).get("compact_at"))
        if wanted is not None and wanted < window:
            return wanted
        return max(1_000, round(window * TASK_AGENT_COMPACT_AT_TOKENS / TASK_AGENT_CONTEXT_WINDOW))

    @staticmethod
    def _point(value: Any) -> int | None:
        """Return a length read from an entry, or None when it is not a usable number."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(value) or value < 1_000:
            return None
        return int(value)

    async def set_compact_at(self, base_url: Any, model: Any, tokens: int | None) -> int:
        """Set the length from which conversations with this model are summed up; None goes back to the preset.

        Returns what is used from now on (``compact_at``). Sessions that
        are open keep what they were started with.
        """
        if tokens is not None and self._point(tokens) is None:
            raise ValueError("the length a conversation is summed up from is a number of tokens, 1000 at the least")
        models = await self._loaded()
        key = model_key(base_url, model)
        async with self._lock:
            entry = dict(models.get(key) or {})
            if tokens is None:
                entry.pop("compact_at", None)
            else:
                entry["compact_at"] = self._point(tokens)
            if entry:
                models[key] = entry
            else:
                models.pop(key, None)
            await self._save(models)
        return await self.compact_at(base_url, model)

    async def claim_window_reminder(self, base_url: Any, model: Any) -> int | None:
        """Return the window the user is to be told of for this model, to whoever asks first. Once.

        A model whose window was learned to be smaller than its sessions
        were managed for is managed for the smaller one from then on, by
        itself. The user is told so once for each model, to set the model's
        context length themselves: what was learned lapses, and may be
        further from the truth than what they know. None when nothing was
        learned, when they were told already, or when someone is telling
        them this moment.

        Whoever gets the window tells the user and then says so
        (``confirm_window_reminder``), or gives it back
        (``unclaim_window_reminder``). That the user was told is written
        down only then: a process that ends in between tells them once
        more the next time, and never not at all. Setting the window
        (``set_context_window``) clears the record: a model that turns out
        smaller again after that is told of again. It also takes back what
        was handed out here and not yet confirmed: that is of a window the
        user has looked at since, and is not put on record.
        """
        models = await self._loaded()
        key = model_key(base_url, model)
        async with self._lock:
            entry = models.get(key) or {}
            learned = self._learned(entry)
            if learned is None or entry.get("window_reminded_at") is not None or key in self._reminding:
                return None
            self._reminding.add(key)
        return learned

    async def confirm_window_reminder(self, base_url: Any, model: Any, window: int) -> None:
        """Put on record that the user was told of a model's window (``claim_window_reminder``), and of which."""
        models = await self._loaded()
        key = model_key(base_url, model)
        async with self._lock:
            if key not in self._reminding:
                # The user set the model's window while they were being
                # told: what they were told of is done with.
                return
            self._reminding.discard(key)
            entry = dict(models.get(key) or {})
            entry["window_reminded"] = int(window)
            entry["window_reminded_at"] = int(time.time())
            models[key] = entry
            await self._save(models)

    def unclaim_window_reminder(self, base_url: Any, model: Any) -> None:
        """Give back what ``claim_window_reminder`` handed out: the user was not told after all."""
        self._reminding.discard(model_key(base_url, model))

    async def describe_window(self, base_url: Any, model: Any) -> dict[str, Any]:
        """Return what decides a model's window, for showing it.

        ``window`` (what is used), ``default`` (what it is when the user set
        none: the model's own if that is written down, else the setting),
        ``set`` (what the user set, or None), and ``learned`` with ``learned_at`` and
        ``lapses_at`` when a provider stated one that is still believed.
        ``compact_at`` (the length a conversation is summed up from) and
        ``compact_at_set`` (what the user set for it, or None). ``reminded``
        and ``reminded_at`` when the user was told that the model's window
        is smaller than was assumed: the window they were told of.
        """
        from config.nekoclaw_settings import MODEL_CONTEXT_WINDOW_REMEMBERED_SECONDS

        entry = (await self._loaded()).get(model_key(base_url, model))
        described: dict[str, Any] = {
            "window": await self.context_window(base_url, model),
            "default": self._default_window(model),
            "set": self._tokens((entry or {}).get("context_window")),
            "compact_at": await self.compact_at(base_url, model),
            "compact_at_set": self._point((entry or {}).get("compact_at")),
        }
        if entry is not None and entry.get("window_reminded_at") is not None:
            described["reminded"] = entry.get("window_reminded")
            described["reminded_at"] = int(entry["window_reminded_at"])
        learned = self._learned(entry)
        if learned is not None and entry is not None:
            described["learned"] = learned
            described["learned_at"] = int(entry["window_noted_at"])
            described["lapses_at"] = int(entry["window_noted_at"]) + MODEL_CONTEXT_WINDOW_REMEMBERED_SECONDS
        return described

    async def set_context_window(self, base_url: Any, model: Any, tokens: int | None) -> int:
        """Set the window sessions with this model are managed for; None goes back to the setting.

        Returns what is used from now on (``context_window``). Sessions
        that are open keep the window they were started with.
        """
        if tokens is not None and self._tokens(tokens) is None:
            raise ValueError("a context window is a positive number of tokens")
        models = await self._loaded()
        key = model_key(base_url, model)
        async with self._lock:
            entry = dict(models.get(key) or {})
            if tokens is None:
                entry.pop("context_window", None)
            else:
                entry["context_window"] = self._tokens(tokens)
            # The user has looked at it: what they were told is done with,
            # and so is what they are being told this moment.
            entry.pop("window_reminded", None)
            entry.pop("window_reminded_at", None)
            self._reminding.discard(key)
            if entry:
                models[key] = entry
            else:
                models.pop(key, None)
            await self._save(models)
        return await self.context_window(base_url, model)

    async def note_context_window(self, base_url: Any, model: Any, tokens: Any) -> bool:
        """Write down the window a model's provider stated when it refused a request as too long.

        Returns whether that changed what is known. A smaller number
        replaces a larger one that is still believed, never the other way
        round: a provider states its limit, and the one that bites is the
        smallest it stated.
        """
        stated = self._tokens(tokens)
        if stated is None:
            return False
        models = await self._loaded()
        key = model_key(base_url, model)
        async with self._lock:
            entry = dict(models.get(key) or {})
            known = self._learned(entry)
            if known is not None and known <= stated:
                return False
            entry["learned_window"] = stated
            entry["window_noted_at"] = int(time.time())
            models[key] = entry
            await self._save(models)
        return True
