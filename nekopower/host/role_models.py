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

"""Which model the task agent of a session is sent to, by the session's preset.

Every preset (``templates.py``: the built-in ones and the user's templates)
has a setting of its own, kept in ``role_models.json`` next to the ledger:

* a slot of the model panel of the plugin server (``plugin_models.json``):
  one that follows the task agent model of the main app, one that follows
  its small task agent model, or one the user filled in. Only a slot that
  declares tool calling can be set, and is used;
* ``inherit``: the model of the session that started this one, as in
  Claude Code. Not for the presets no session starts (``NOT_FOR_CHILDREN``);
* nothing: the preset's default, which is what its template says
  (``Template.model``). ``agent`` is the slot that follows the task agent
  model of the main app, or that model itself when there is no such slot
  (the user removed it, or the plugin server never ran); ``inherit`` the
  model of the session that started it, else that of its project's lead,
  else ``agent``; ``advanced`` the slot that follows the advanced task agent
  model of the main app and declares tool calling, else that model itself,
  else ``agent`` (when that model cannot be read or names none, or there
  are no slots to read it by).

A copy of a session (preset ``shadow``) has no setting: it is sent where the
session it was copied from is, or what was cached of the conversation is lost.

Out of the box every session is so sent to the task agent model of the main
app, as before there were settings, but a planner's: it is sent to the
advanced one, which is the task agent model itself until the user sets it.

A session's model is settled when it is made and kept on its row of the
ledger (``Session.model_*``): which slot, the model's name, a digest of its
address and name, whether what is sent goes with the built-in free key, and
why the setting could not be used if it could not. Addresses and keys are
read anew each time a session is opened and stay in memory: never in the
ledger, a log, the board or the text of an exception.

The five ways a slot can fail (``FALLBACK_REASONS``, the codes of
``plugin.server.infrastructure.model_slot_resolver``) all lead to the
preset's default, with the code kept on the session for its card.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# A preset's setting: a slot's id, or one of these.
INHERIT = "inherit"
# The defaults a template can name (``templates.MODELS``).
AGENT = "agent"
ADVANCED = "advanced"
# Why a slot could not be used (``model_slot_resolver``), kept on a session
# whose preset's default was used in its place.
SLOT_NOT_FOUND = "slot_not_found"
MODEL_CONFIG_UNREADABLE = "model_config_unreadable"
HOST_API_NOT_CONFIGURED = "host_api_not_configured"
HOST_API_UNSUPPORTED = "host_api_unsupported"
CAPABILITY_MISSING = "capability_missing"
# The host's own: a session's slot, which it was settled on, now sends it
# to another address or model, and not by following the tier of the main
# app it followed then.
# Slots can be rewritten by more than the user (a plugin's page is served
# from the plugin server's origin), and what was said in the session is not
# to go somewhere the user never chose for it.
SLOT_CHANGED = "slot_changed"
FALLBACK_REASONS: tuple[str, ...] = (
    SLOT_NOT_FOUND,
    MODEL_CONFIG_UNREADABLE,
    HOST_API_NOT_CONFIGURED,
    HOST_API_UNSUPPORTED,
    CAPABILITY_MISSING,
    SLOT_CHANGED,
)
# Those of them that say the slot is not to be used again: a session settled
# on it is settled on its preset's default in its place. The others (a file
# or a tier that cannot be read just now) leave it as it was settled, to be
# tried again the next time.
LASTING_REASONS: frozenset[str] = frozenset({SLOT_NOT_FOUND, CAPABILITY_MISSING, SLOT_CHANGED})
# What a slot has to declare for a task agent to be sent to it.
REQUIRED_CAPABILITIES: tuple[str, ...] = ("tool_calling",)
# What a slot declares when the model behind it takes pictures.
IMAGE_INPUT = "image_input"
# The tier of the main app a slot follows when it follows the task agent model.
AGENT_TIER = "agent"
# The one it follows when it follows the advanced task agent model.
ADVANCED_TIER = "agent_advanced"
_SLOT_PREFIX = "slot_"
_FILE_VERSION = 1

ModelConfigProvider = Callable[[], Awaitable[dict[str, Any] | None]]


class SlotUnavailable(Exception):
    """A slot cannot be used; ``code`` (one of ``FALLBACK_REASONS``) says why. Never a credential in it."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class ModelSlots(Protocol):
    """The slots of the model panel, as the host reads them (``PluginModelSlots``)."""

    async def list(self) -> list[Any]:
        """Return every slot in panel order (``SlotSummary``); raises ``SlotUnavailable`` when they cannot be read."""

    async def resolve(self, slot_id: str) -> Any:
        """Return what a slot resolves to now (``ResolvedModel``) for a task agent; raises ``SlotUnavailable``."""

    async def tier(self, host_api: str) -> dict[str, Any] | None:
        """Return the configuration of a tier of the main app a slot follows, shaped as ``model_config`` is."""


def host_model_config(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return what a session is opened with from a model configuration of the main app; None when it names no model.

    The address is taken as configured, ``http://`` included.
    """
    if not config or not config.get("model") or not config.get("base_url"):
        return None
    return {
        "model": config["model"],
        "base_url": config["base_url"],
        "api_key": config.get("api_key"),
        "provider_type": config.get("provider_type"),
    }


class PluginModelSlots:
    """The slots of ``plugin_models.json``, read in a worker thread (``model_slot_resolver``).

    ``config_manager`` is where the files and the tiers of the main app are
    read from; the process's own when it is None.
    """

    def __init__(self, config_manager: Any = None) -> None:
        self._config_manager = config_manager

    def _manager(self) -> Any:
        if self._config_manager is not None:
            return self._config_manager
        from utils.config_manager import get_config_manager

        return get_config_manager(migrate=False)

    async def list(self) -> list[Any]:
        from plugin.server.infrastructure.model_slot_resolver import ModelSlotUnavailable, list_model_slots

        try:
            return await asyncio.to_thread(list_model_slots, config_manager=self._config_manager)
        except ModelSlotUnavailable as exc:
            raise SlotUnavailable(exc.code) from None

    async def resolve(self, slot_id: str) -> Any:
        from plugin.server.infrastructure.model_slot_resolver import ModelSlotUnavailable, resolve_model_slot

        try:
            return await asyncio.to_thread(
                resolve_model_slot, slot_id, require=REQUIRED_CAPABILITIES, config_manager=self._config_manager
            )
        except ModelSlotUnavailable as exc:
            # Its message says which capability is missing, or what is wrong
            # with the file; the code is all that is kept.
            raise SlotUnavailable(exc.code) from None

    async def tier(self, host_api: str) -> dict[str, Any] | None:
        from plugin.server.domain.model_config import HOST_API_MODEL_TYPES

        model_type = HOST_API_MODEL_TYPES.get(host_api)
        if model_type is None:
            return None
        config = await asyncio.to_thread(lambda: self._manager().get_model_api_config(model_type))
        return host_model_config(config)


def model_ident(base_url: Any, model: Any) -> str:
    """Return what stands for a model's address and name where neither may be kept: a digest of its key."""
    from nekopower.host.model_capabilities import model_key

    return hashlib.sha256(model_key(base_url, model).encode("utf-8")).hexdigest()[:16]


def is_free_key(api_key: Any) -> bool:
    """Return whether a key is the built-in free one, whose requests count against the free daily quota."""
    from plugin.server.infrastructure.host_model_slots import is_free_host_key

    return is_free_host_key(str(api_key or ""))


@dataclass(frozen=True)
class Choice:
    """The model a session is sent to, as it is settled; with what it is called on (in memory only).

    ``slot`` is the slot of the model panel ("" for the task agent model of
    the main app itself, or its advanced one), ``agent`` whether it is that model (that slot, or
    the slot that follows it), ``tier`` the tier of the main app it is
    (that model, or a slot that follows a tier; "" for a slot the user
    filled in), ``fallback`` why the setting of the preset could not be
    used. ``config`` is what the session is opened with, its key included;
    None when no model is configured.
    """

    slot: str
    agent: bool
    name: str
    ident: str
    free: bool
    fallback: str = ""
    config: dict[str, Any] | None = field(default=None, repr=False, compare=False)
    tier: str = AGENT_TIER

    def columns(self) -> dict[str, Any]:
        """Return what is kept of it on a session's row: no address, no key."""
        return {
            "model_slot": self.slot,
            "model_name": self.name,
            "model_ident": self.ident,
            "model_free": int(self.free),
            "model_fallback": self.fallback,
            "model_tier": self.tier,
        }


def _choice(
    slot: str, agent: bool, config: dict[str, Any] | None, fallback: str = "", *, tier: str = AGENT_TIER
) -> Choice:
    if not config or not config.get("model"):
        return Choice(
            slot=slot, agent=agent, name="", ident="", free=False, fallback=fallback, config=None, tier=tier,
        )
    return Choice(
        slot=slot,
        agent=agent,
        name=str(config.get("model")),
        ident=model_ident(config.get("base_url"), config.get("model")),
        free=is_free_key(config.get("api_key")),
        fallback=fallback,
        config=config,
        tier=tier,
    )


def recorded(session: Any) -> bool:
    """Return whether a session's model was settled (``Session.model_ident``)."""
    return bool(getattr(session, "model_ident", ""))


def same_as_kept(session: Any, choice: Choice) -> bool:
    """Return whether a session's row says what a choice would have it say."""
    return all(getattr(session, name, None) == value for name, value in choice.columns().items())


def _protocol_provider(protocol: Any) -> str:
    """Return the provider type the client is made with for a slot's protocol (``utils.llm_client``)."""
    return "anthropic" if protocol == "anthropic_messages" else "openai_compatible"


def _note_a_write_that_failed(writing: "asyncio.Future[None]") -> None:
    """Log a write of the settings that failed, the caller having given up on it or not."""
    if not writing.cancelled() and writing.exception() is not None:
        logger.warning("writing a preset model setting failed: %s", type(writing.exception()).__name__)


class RoleModelStore:
    """The setting of every preset, kept in a file next to the ledger: a slot's id or ``inherit`` by preset."""

    def __init__(self, path: Path | Callable[[], Path]) -> None:
        self._path_source = path
        self._lock = asyncio.Lock()

    def _path(self) -> Path:
        source = self._path_source
        return Path(source() if callable(source) else source)

    def _read(self, *, strict: bool = False, path: Path | None = None) -> dict[str, str]:
        """Return the settings in the file. One that cannot be read is read as none, or raises with ``strict``."""
        import json

        try:
            raw = json.loads((path or self._path()).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            if strict:
                # About to be written: what is in the file is not known, and
                # would be lost.
                raise OSError("the preset model settings cannot be read; nothing is written") from exc
            # Read as nothing set: every preset goes by its default meanwhile.
            logger.warning("the preset model settings cannot be read (%s); the defaults are used", type(exc).__name__)
            return {}
        roles = raw.get("roles") if isinstance(raw, dict) else None
        if not isinstance(roles, dict):
            if strict:
                # Not settings as this build writes them: not written over either.
                raise OSError("the preset model settings are not as they are written; nothing is written")
            return {}
        return {
            str(name): value
            for name, value in roles.items()
            if isinstance(value, str) and (value == INHERIT or value.startswith(_SLOT_PREFIX))
        }

    def _write(self, roles: dict[str, str], path: Path | None = None) -> None:
        from utils.file_utils import atomic_write_json

        path = path or self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {"version": _FILE_VERSION, "roles": roles})

    async def all(self) -> dict[str, str]:
        """Return every setting there is, by preset."""
        return await asyncio.to_thread(self._read)

    async def get(self, preset: str) -> str | None:
        """Return a preset's setting: a slot's id, ``inherit``, or None for its default."""
        return (await self.all()).get(preset)

    async def put(self, preset: str, value: str | None) -> None:
        """Write a preset's setting; None goes back to its default.

        Raises ``OSError`` when the file is there and cannot be read: the
        other settings in it would be lost. Done to the end once begun, the
        caller cancelled or not: the write in its thread cannot be stopped,
        and the next one must not begin before it is over.
        """
        writing = asyncio.ensure_future(self._put(preset, value))
        # Told of where whoever asked gave up before it was over.
        writing.add_done_callback(_note_a_write_that_failed)
        await asyncio.shield(writing)

    async def _put(self, preset: str, value: str | None) -> None:
        async with self._lock:
            # One file from the read to the write: what is read where the
            # data used to be is not written where it is now.
            path = self._path()
            roles = await asyncio.to_thread(lambda: self._read(strict=True, path=path))
            if value is None:
                roles.pop(preset, None)
            else:
                roles[preset] = value
            await asyncio.to_thread(self._write, roles, path)


class RoleModels:
    """Settles which model a session of a preset is sent to; see the module docstring.

    ``agent_config`` is the task agent model of the main app (what every
    session was sent to before there were settings); ``slots`` the slots of
    the model panel, None where there are none to read (then every preset
    is sent to ``agent_config``). ``default_of`` returns a preset's default
    (``AGENT``, ``ADVANCED`` or ``INHERIT``) for a project, as its template says.
    """

    def __init__(
        self,
        *,
        store: RoleModelStore,
        slots: ModelSlots | None,
        agent_config: ModelConfigProvider,
        default_of: Callable[[str, str | None], Awaitable[str]],
    ) -> None:
        self.store = store
        self.slots = slots
        self._agent_config = agent_config
        self._default_of = default_of

    async def agent(self, fallback: str = "") -> Choice:
        """Return the task agent model of the main app itself, the slot that follows it aside."""
        return _choice("", True, await self._agent_config(), fallback)

    async def slot(self, slot_id: str) -> Choice:
        """Return a slot's model as it resolves now; raises ``SlotUnavailable``.

        A slot that does not declare that it takes pictures is opened as one
        that does not; one that does is taken at its word only as far as the
        host has not learned otherwise (``ModelCapabilityStore``).
        """
        if self.slots is None:
            raise SlotUnavailable(MODEL_CONFIG_UNREADABLE)
        resolved = await self.slots.resolve(slot_id)
        tier = str(resolved.host_api) if resolved.source == "host_follow" else ""
        if tier:
            if resolved.host_api == AGENT_TIER:
                # What every session was opened with before there were
                # slots, read as it was: the same request goes out.
                config = await self._agent_config()
                agent = True
            else:
                try:
                    config = await self.slots.tier(str(resolved.host_api))
                except SlotUnavailable:
                    raise
                except Exception:  # noqa: BLE001 - the tier cannot be read just now
                    raise SlotUnavailable(MODEL_CONFIG_UNREADABLE) from None
                agent = False
        else:
            config = {
                "model": resolved.model,
                "base_url": resolved.base_url,
                "api_key": resolved.api_key,
                "provider_type": _protocol_provider(resolved.protocol),
            }
            agent = False
        if not config or not config.get("model"):
            # The tier was there as the slot was resolved, and is not now.
            raise SlotUnavailable(HOST_API_NOT_CONFIGURED)
        if IMAGE_INPUT not in tuple(resolved.capabilities):
            config = {**config, "supports_images": False}
        return _choice(slot_id, agent, config, tier=tier)

    async def agent_slot(self) -> str | None:
        """Return the slot that follows the task agent model of the main app, None when there is none; raises ``SlotUnavailable``."""
        if self.slots is None:
            return None
        for item in await self.slots.list():
            if item.source == "host_follow" and item.host_api == AGENT_TIER:
                return str(item.slot_id)
        return None

    async def agent_default(self, fallback: str = "") -> Choice:
        """Return the default ``agent``: the slot that follows the task agent model, else that model itself."""
        try:
            slot_id = await self.agent_slot()
        except SlotUnavailable as exc:
            return await self.agent(fallback or exc.code)
        if slot_id is None:
            # Removed by the user, or never there: as before there were slots.
            return await self.agent(fallback)
        try:
            found = await self.slot(slot_id)
        except SlotUnavailable as exc:
            return await self.agent(fallback or exc.code)
        if found.tier != AGENT_TIER:
            # It followed the task agent model as the slots were listed, and
            # does not as it is read again: what it says now nobody chose.
            return await self.agent(fallback or SLOT_CHANGED)
        return dataclass_replace(found, fallback=fallback)

    async def advanced(self) -> Choice:
        """Return the advanced task agent model of the main app itself; raises ``SlotUnavailable``.

        ``MODEL_CONFIG_UNREADABLE`` where it cannot be read (or there are no
        slots to read it by), ``HOST_API_NOT_CONFIGURED`` where it names no model.
        """
        if self.slots is None:
            raise SlotUnavailable(MODEL_CONFIG_UNREADABLE)
        try:
            config = await self.slots.tier(ADVANCED_TIER)
        except SlotUnavailable:
            raise
        except Exception:  # noqa: BLE001 - the tier cannot be read just now
            raise SlotUnavailable(MODEL_CONFIG_UNREADABLE) from None
        if not config or not config.get("model"):
            raise SlotUnavailable(HOST_API_NOT_CONFIGURED)
        return _choice("", False, config, tier=ADVANCED_TIER)

    async def advanced_default(self, fallback: str = "") -> Choice:
        """Return the default ``advanced``: the slot that follows the advanced task agent model, else that model.

        Only a slot that declares tool calling counts. Where neither can be
        used, the default ``agent``, with why; where there are no slots, that
        default as it is without them.
        """
        if self.slots is None:
            return await self.agent_default(fallback)
        try:
            listed = await self.slots.list()
        except SlotUnavailable as exc:
            return await self.agent_default(fallback or exc.code)
        slot_id = next(
            (
                str(item.slot_id)
                for item in listed
                if item.source == "host_follow"
                and item.host_api == ADVANCED_TIER
                and set(REQUIRED_CAPABILITIES) <= set(item.capabilities)
            ),
            None,
        )
        try:
            if slot_id is None:
                found = await self.advanced()
            else:
                found = await self.slot(slot_id)
        except SlotUnavailable as exc:
            return await self.agent_default(fallback or exc.code)
        if found.tier != ADVANCED_TIER:
            # Listed as following the advanced model, and read as another:
            # what it says now nobody chose.
            return await self.agent_default(fallback or SLOT_CHANGED)
        return dataclass_replace(found, fallback=fallback)

    async def choose(
        self,
        preset: str,
        project: str | None,
        inherited: Callable[[], Awaitable[Choice | None]],
        *,
        use_setting: bool = True,
        fallback: str = "",
        settings: dict[str, str] | None = None,
    ) -> Choice:
        """Return the model a new session of a preset is sent to.

        ``inherited`` returns the model of the session that started it, else
        that of its project's lead; None when there is neither. Without
        ``use_setting`` the preset's default is taken whatever is set (the
        setting it was settled by can no longer be used: ``fallback`` says
        why).
        """
        if not use_setting:
            setting = None
        elif settings is not None:
            setting = settings.get(preset)
        else:
            setting = await self.store.get(preset)
        if setting is not None and setting != INHERIT:
            try:
                return await self.slot(setting)
            except SlotUnavailable as exc:
                logger.info("the model slot set for preset %s cannot be used (%s); its default is", preset, exc.code)
                fallback = exc.code
                setting = None
        wanted = setting or await self._default_of(preset, project)
        if wanted == INHERIT:
            found = await inherited()
            if found is not None:
                return dataclass_replace(found, fallback=fallback)
        if wanted == ADVANCED:
            return await self.advanced_default(fallback)
        return await self.agent_default(fallback)

    async def describe(
        self, preset: str, project: str | None = None, settings: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Return what a new session of a preset that no session starts would be sent to, as a page shows it.

        ``{"follows": "starter"}`` where it takes the model of the session
        that starts it (which is then no model of its own to name); else
        ``{"follows": None, "slot", "model", "agent", "free"}``. With
        ``fallback``: why the setting could not be used (None when it
        could). Names and codes only: no address, no key.
        """

        async def starter() -> Choice:
            # Told apart by what no other choice is: no slot, not the task
            # agent model, and no tier of the main app.
            return Choice(slot="", agent=False, name="", ident="", free=False, tier="")

        choice = await self.choose(preset, project, starter, settings=settings)
        if not choice.slot and not choice.agent and not choice.tier:
            return {"follows": "starter", "fallback": choice.fallback or None}
        return {
            "follows": None,
            "slot": choice.slot or None,
            "model": choice.name or None,
            "agent": choice.agent,
            "free": choice.free,
            "fallback": choice.fallback or None,
        }

    async def again(self, slot_id: str, ident: str, tier: str) -> Choice:
        """Return the model a settled session is sent to now, by the slot it was settled on; raises ``SlotUnavailable``.

        ``ident`` and ``tier`` are what it was settled on (``Choice``). A
        slot that now sends it to another address or model is not followed
        there (``SLOT_CHANGED``), unless it still follows the tier of the
        main app it followed then: what the user sets for the main app is
        taken as it always was. A slot made to follow a tier since, or
        another tier, is not: slots are written by more than the user. No
        slot is the task agent model itself, or the advanced one where that
        is the tier it was settled on.
        """
        if not slot_id:
            return await (self.advanced() if tier == ADVANCED_TIER else self.agent())
        found = await self.slot(slot_id)
        if found.tier != tier:
            # Made to follow a tier since, or another one, or to follow
            # none: whatever it says now was not chosen for this session,
            # even where it is the same model for now.
            raise SlotUnavailable(SLOT_CHANGED)
        if found.ident != ident and not found.tier:
            raise SlotUnavailable(SLOT_CHANGED)
        return found


def dataclass_replace(choice: Choice, **changes: Any) -> Choice:
    """Return a choice with ``changes``, what it is called on kept."""
    import dataclasses

    return dataclasses.replace(choice, **changes)
