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

"""Whether the model task agents use can see pictures.

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

Stored in ``<data root>/nekoclaw/model_capabilities.json``. It is read once,
on first use, and never at startup. A file that cannot be read counts as
empty: the worst that follows is one refused request, which is how it would
have been learned anyway.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FILE_VERSION = 1


def model_key(base_url: Any, model: Any) -> str:
    """Return the key a model is remembered by: its address and its name.

    The scheme and the host of an address are written in any case; its path
    is not, and two paths that differ by case may be two services.
    """
    address = str(base_url or "").strip().rstrip("/")
    # The first "://": a later one belongs to the path or to what follows it.
    scheme, separator, rest = address.partition("://")
    if not separator or "/" in scheme:
        # No scheme: the address begins with the host.
        scheme, separator, rest = "", "", address
    authority, slash, path = rest.partition("/")
    # Before the "@", if there is one: a name and a password, kept as written.
    credentials, at, host = authority.rpartition("@")
    return f"{scheme.lower()}{separator}{credentials}{at}{host.lower()}{slash}{path}|{str(model or '').strip()}"


class ModelCapabilityStore:
    """What was learned about models, kept across restarts."""

    def __init__(self, path: Path | Callable[[], Path]) -> None:
        self._path_source = path
        self._models: dict[str, dict[str, Any]] | None = None
        self._lock = asyncio.Lock()

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
        from utils.asyncio_executor import to_thread_through_cancellation

        models = await self._loaded()
        key = model_key(base_url, model)
        async with self._lock:
            entry = models.get(key)
            if self._believed(entry):
                # Known already, or set by hand (only a hand writes true).
                return False
            models[key] = {
                "images": False,
                "source": "refused",
                "noted_at": int(time.time()),
                "reason": " ".join(str(reason or "").split())[:300],
            }
            try:
                await to_thread_through_cancellation(self._write, {name: dict(value) for name, value in models.items()})
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - kept in memory; written with the next change
                logger.warning("the model capability file could not be written", exc_info=True)
        return True
