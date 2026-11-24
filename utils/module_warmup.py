# -*- coding: utf-8 -*-
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

"""Start background module warmup.

The startup chain imports only what greeting really needs; heavy SDKs (google-genai plus
the mcp, translatepy etc. it drags in) became lazy imports on first use. After the
service is ready, this module starts a daemon thread that runs those "first-use" imports
ahead of time in the background, so users don't pay the import latency mid-interaction
when they actually use the features.

Only the SDKs the current configuration can reach are warmed. Every SDK stays imported
for the rest of the process once loaded, so warming one nobody uses costs resident
memory for nothing (all of them together were measured at about 68 MiB). Each entry in
``MAIN_SERVER_WARMUP`` that is not always needed has a gate in ``_ENTRY_GATES``; the
gates read the saved configuration in the warmup thread, never on the event loop. A gate
that cannot decide (its config read raises) counts as "needed": an unneeded import only
costs memory, while a missed one puts the import back on the first use.

Configuration can change while the process runs (a Gemini or Claude model picked in the
API settings, proactive sources switched on). ``request_config_rewarm`` re-runs the
gates after such a save and warms whatever has become reachable.

GIL note: pure-Python modules hold the GIL while being parsed, but the bulk here is C
extension dlopen (which releases the GIL) and file IO, while the event loop is mostly
awaiting IO, so a low-priority daemon thread can make progress in the gaps without
stalling the loop. This is best-effort warmup, not a correctness path — any failure is
swallowed; the lazy import on first use remains the single source of truth.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import threading
import time
from functools import cached_property
from typing import Callable, Iterable, Mapping

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

# Heavy modules to warm after main_server is ready. Importing genai brings in mcp,
# so listing genai makes a separate mcp entry unnecessary.
#
# translatepy is deliberately absent: it only serves subtitle translation in the
# China region, whose switch lives in the frontend (off by default) and is
# invisible here, so warming it kept ~28 MiB resident for users who never turn
# subtitles on. translate_text imports it on first use, off the event loop.
MAIN_SERVER_WARMUP: tuple[str, ...] = (
    # openai/anthropic were once top-level utils/llm_client imports, at the front of the merged startup's
    # serial chain (~0.7s total, mostly pydantic model construction). Warm lazy imports here so the first real
    # LLM call (greeting, etc.) need not wait for imports.
    "openai",
    "anthropic",
    "google.genai",
    "google.genai.types",
    "googletrans",
    # Heavy feature-route dependencies (voice-clone TTS / NetEase Cloud / web scraping / Bilibili) moved from router /
    # util module tops into handlers; warm them here so the first feature use need not wait for imports.
    "dashscope",
    "dashscope.audio.tts_v2",
    "pyncm_async",
    "bs4",
    "bilibili_api",
    # Compiling 21 banned-topic templates (four with ~51KB regex source each) took
    # 294-298ms. Previously at config/prompts/prompts_directives module scope, this occupied
    # memory_server's eager import chain before port binding. After making it lazy,
    # warm it here so first directive extraction need not wait.
    "config.prompts.prompts_directives:DIRECTIVE_PATTERNS",
)

# Entry -> name of the gate deciding whether the current configuration needs it.
# Entries absent here are always warmed: openai, the directive patterns, and
# pyncm_async / bs4, which chat song requests (/api/music/search and
# /api/music/play/netease) import on the event loop whatever the proactive
# settings say, and no switch turns that off.
_ENTRY_GATES: dict[str, str] = {
    "anthropic": "anthropic",
    "google.genai": "gemini",
    "google.genai.types": "gemini",
    "googletrans": "translate_global",
    "dashscope": "cosyvoice_clone",
    "dashscope.audio.tts_v2": "cosyvoice_clone",
    "bilibili_api": "proactive_bilibili",
}

# Text-model slots built through utils.llm_client. Realtime and the TTS slots are
# handled by their own gates; image generation never goes through these SDKs.
_LLM_SLOTS: tuple[str, ...] = (
    "conversation",
    "memory",
    "game_main",
    "game_summary",
    "recognition",
    "decision",
    "vision",
    "agent",
    "agent_small",
    "agent_advanced",
    "game_recognition",
)

_COSYVOICE_PROVIDERS = frozenset({"cosyvoice", "cosyvoice_intl"})


class WarmupConfig:
    """The saved configuration the gates read, loaded lazily and at most once per pass.

    Every read happens on first access, in the warmup thread. A read that raises
    makes each gate depending on it raise too, which ``evaluate_gates`` counts as
    "needed" -- with one exception: ``core_api_type`` falls back to
    ``CORE_API_TYPE`` when the realtime slot cannot be read.
    """

    @cached_property
    def _cm(self):
        from utils.config_manager import get_config_manager

        return get_config_manager()

    @cached_property
    def core_config(self) -> Mapping[str, object]:
        # get_core_config() answers a corrupt or unreadable file with the defaults,
        # which would read as "no Claude, no Gemini". Check the file first so that
        # case counts as a failed read. A missing file really is the defaults.
        path = self._cm.get_config_path("core_config.json")
        try:
            with open(path, "r", encoding="utf-8") as stream:
                saved = json.load(stream)
        except FileNotFoundError:
            pass
        else:
            if not isinstance(saved, dict):
                raise ValueError("core_config.json does not hold an object")
        return self._cm.get_core_config() or {}

    @cached_property
    def realtime_config(self) -> Mapping[str, object]:
        return self._cm.get_model_api_config("realtime", _core_config=dict(self.core_config)) or {}

    @cached_property
    def llm_slot_configs(self) -> tuple[Mapping[str, object], ...]:
        core = dict(self.core_config)
        return tuple(
            self._cm.get_model_api_config(slot, _core_config=core) or {}
            for slot in _LLM_SLOTS
        )

    @cached_property
    def china_region(self) -> bool | None:
        """The region region-split features run in; None while it may still change.

        ``translate_text`` and the proactive scrapers follow ``is_china_region``.
        Until the locale probe has a lasting verdict that answer is provisional,
        so neither side can be ruled out.
        """
        from utils import language_utils

        if language_utils._detect_china_region() is None:
            return None
        return language_utils.is_china_region()

    @cached_property
    def core_api_type(self) -> str:
        """The session's core api type, as start_session resolves it.

        The realtime slot's ``api_type`` first; when that slot cannot be read,
        ``CORE_API_TYPE`` alone. Only this value falls back: other reads of the
        realtime slot (its base_url, the Gemini check) still raise, so the gates
        using them count as "needed".
        """
        try:
            realtime_type = self.realtime_config.get("api_type")
        except Exception:
            realtime_type = None
        return str(realtime_type or self.core_config.get("CORE_API_TYPE") or "")

    @cached_property
    def current_voice_id(self) -> str:
        """The voice a session would start with ('' for none), as start_session resolves it.

        The current character's voice, minus a free preset outside free mode
        (``_apply_voice_id_for_route``); when that leaves none, the custom-API
        voice ``TTS_VOICE_ID`` under the same conditions as start_session. Reads
        characters.json, core_config and (for the core api type) the realtime slot.
        """
        from utils.config_manager.reserved_schema import get_reserved
        from utils.tts.native_voice_registry import is_free_preset_voice_id
        from utils.voice_config import read_legacy_voice_id

        characters = self._cm.load_characters() or {}
        profile = (characters.get("猫娘") or {}).get(characters.get("当前猫娘"))
        voice_id = ""
        if isinstance(profile, dict):
            voice_id = read_legacy_voice_id(
                get_reserved(profile, "voice_id", default="", legacy_keys=("voice_id",))
            )
        # A free preset is not sent outside free mode (a YUI voice left from the
        # free tier, say); start_session clears it before the fallback below.
        if voice_id and is_free_preset_voice_id(voice_id) and self.core_api_type != "free":
            voice_id = ""
        if voice_id:
            return voice_id
        # No voice on the card: start_session falls back to the custom-API voice
        # (main_logic/core/lifecycle.py), and that voice goes through the same routing.
        from utils.config_manager._shared import _as_bool
        from utils.gptsovits_config import is_gsv_disabled_voice_id

        core = self.core_config
        fallback = str(core.get("TTS_VOICE_ID") or "")
        if (
            fallback
            and not is_gsv_disabled_voice_id(fallback)
            and (_as_bool(core.get("ENABLE_CUSTOM_API"), False) or core.get("GPTSOVITS_ENABLED"))
        ):
            return fallback
        return ""

    @cached_property
    def current_voice_meta(self) -> Mapping[str, object] | None:
        """Stored metadata of the current character's voice (see _get_voice_meta)."""
        from utils.voice_config import is_imported_voice_ref

        voice_id = self.current_voice_id
        if not voice_id:
            return None
        meta = self._cm.get_imported_voice(voice_id) if is_imported_voice_ref(voice_id) else None
        if meta is None:
            meta = (self._cm.get_voices_for_current_api() or {}).get(voice_id)
        return meta if isinstance(meta, dict) else None

    @cached_property
    def current_voice_is_stock(self) -> bool:
        """The current voice is a free preset or the realtime provider's own voice.

        Those never reach the CosyVoice worker (``_has_custom_tts`` is False for
        a provider-native voice, ``get_tts_worker`` skips free presets).
        """
        from utils.tts.native_voice_registry import (
            is_free_preset_voice_id,
            resolve_native_voice_for_routing,
        )

        voice_id = self.current_voice_id
        if is_free_preset_voice_id(voice_id):
            return True
        _, native = resolve_native_voice_for_routing(
            self.core_api_type,
            voice_id,
            self._cm.voice_id_exists_in_any_storage,
            realtime_base_url=str(self.realtime_config.get("base_url") or ""),
        )
        return bool(native)

    @cached_property
    def conversation_settings(self) -> Mapping[str, object]:
        from utils.preferences import load_global_conversation_settings

        return load_global_conversation_settings(strict=True) or {}


def _needs_anthropic(cfg: WarmupConfig) -> bool:
    """Any text slot speaks the Anthropic Messages API (see AnthropicChatClient)."""
    from utils.llm_client.anthropic_client import _is_anthropic_endpoint

    return any(
        _is_anthropic_endpoint(slot.get("base_url"), slot.get("provider_type"))
        for slot in cfg.llm_slot_configs
    )


def _needs_gemini(cfg: WarmupConfig) -> bool:
    """Gemini realtime / Gemini ASR, or a text slot routed to the native genai SDK."""
    # CORE_API_TYPE == gemini selects the Gemini realtime client and, through the
    # core ASR route, the Gemini ASR worker.
    if str(cfg.core_config.get("CORE_API_TYPE") or "").strip().lower() == "gemini":
        return True
    if str(cfg.realtime_config.get("api_type") or "").strip().lower() == "gemini":
        return True
    # The route-string test _should_use_genai_sdk runs before its import probe.
    from utils.genai_route import is_native_genai_route

    return any(
        is_native_genai_route(str(slot.get("model") or ""), str(slot.get("base_url") or ""))
        for slot in cfg.llm_slot_configs
    )


def _needs_translate_global(cfg: WarmupConfig) -> bool:
    # Subtitle translation uses googletrans outside the China region (translatepy
    # inside it, which is never warmed; see MAIN_SERVER_WARMUP). googletrans is
    # tiny, so it is warmed whenever the region may be anything but China.
    return cfg.china_region is not True


def _needs_cosyvoice_clone(cfg: WarmupConfig) -> bool:
    """The current character's voice can reach the CosyVoice worker (see get_tts_worker)."""
    from utils.voice_config import is_imported_voice_ref

    voice_id = cfg.current_voice_id
    if not voice_id:
        return False
    meta = cfg.current_voice_meta
    if meta is not None:
        return meta.get("provider") in _COSYVOICE_PROVIDERS
    # An imported voice with no stored record gets the "unavailable" worker.
    if is_imported_voice_ref(voice_id):
        return False
    # Any other voice without a stored record still goes to the CosyVoice worker
    # when it is custom (not a free preset, not the provider's own voice). This can
    # also warm for a few voices routed elsewhere (GPT-SoVITS, xAI custom voices);
    # that costs memory only, a miss would cost the first sentence.
    return not cfg.current_voice_is_stock


def _proactive_flags(settings: Mapping[str, object], *names: str) -> bool:
    """Proactive chat is on and at least one of ``names`` is on.

    A key the user never saved counts as on: the frontend applies its own
    default, which the backend cannot see.
    """
    def on(name: str) -> bool:
        return settings.get(name) is not False

    return on("proactiveChatEnabled") and any(on(name) for name in names)


def _needs_proactive_bilibili(cfg: WarmupConfig) -> bool:
    # Bilibili feeds the video and personal-update sources, China region only.
    if not _proactive_flags(
        cfg.conversation_settings,
        "proactiveVideoChatEnabled",
        "proactivePersonalChatEnabled",
    ):
        return False
    return cfg.china_region is not False


_GATES: dict[str, Callable[[WarmupConfig], bool]] = {
    "anthropic": _needs_anthropic,
    "gemini": _needs_gemini,
    "translate_global": _needs_translate_global,
    "cosyvoice_clone": _needs_cosyvoice_clone,
    "proactive_bilibili": _needs_proactive_bilibili,
}


def evaluate_gates(
    cfg: WarmupConfig | None = None,
    only: Iterable[str] | None = None,
) -> dict[str, bool]:
    """Run the gates (all, or those named in ``only``) against one config snapshot.

    A gate that raises counts as needed. Config is read lazily, so gates left
    out cost no reads.
    """
    cfg = WarmupConfig() if cfg is None else cfg
    wanted = set(_GATES) if only is None else set(only)
    results: dict[str, bool] = {}
    for name, gate in _GATES.items():
        if name not in wanted:
            continue
        try:
            results[name] = bool(gate(cfg))
        except Exception as exc:
            logger.debug("[warmup] gate %s undecided, warming anyway: %s", name, exc)
            results[name] = True
    return results


def select_warmup_entries(
    entries: Iterable[str],
    gate_results: Mapping[str, bool],
) -> tuple[str, ...]:
    """Keep ungated entries and those whose gate passed (a missing result passes)."""
    selected = []
    for entry in entries:
        gate = _ENTRY_GATES.get(entry)
        if gate is None or gate_results.get(gate, True):
            selected.append(entry)
    return tuple(selected)


_warmup_lock = threading.Lock()
# Config-gated warmup of MAIN_SERVER_WARMUP: started once by start_main_server_warmup,
# re-run by request_config_rewarm. One worker at a time; a request arriving while
# it runs makes it go round once more.
_gated_started = False
_gated_running = False
_gated_pending = False
# Gates the pending re-check covers (None: all), merged across the requests it absorbs.
_gated_pending_scope: frozenset[str] | None = None
# The startup pass never ran (its thread did not start): re-check everything once.
_startup_pass_missing = False
# Entries whose import failed; not retried on later config saves.
_failed_entries: set[str] = set()
# Failed imports per entry for errors other than "not installed".
_failure_counts: dict[str, int] = {}
_MAX_IMPORT_FAILURES = 2


def _warm_one(name: str) -> None:
    """Warm one entry: ``"module"``, or ``"module:attribute"``.

    The attribute form is for lazily-evaluated heavyweights -- a batch of regex
    compilations, say. Such a module is usually in ``sys.modules`` already,
    dragged in by something else, with only the expensive part deferred to first
    access; importing it again is a cache hit that runs no code, so warming it
    would do nothing. The attribute has to actually be read.
    """
    module_name, _, attr = name.partition(":")
    module = importlib.import_module(module_name)
    if attr:
        getattr(module, attr)


def _warm_entries(module_list: tuple[str, ...], label: str) -> None:
    t0 = time.monotonic()
    loaded = 0
    for name in module_list:
        start = time.monotonic()
        try:
            _warm_one(name)
            loaded += 1
            logger.debug(
                "[warmup:%s] %s (%.0f ms)",
                label, name, (time.monotonic() - start) * 1000,
            )
        except ModuleNotFoundError as exc:
            logger.debug("[warmup:%s] skip %s: %s", label, name, exc)
            # Not installed: retrying on every config save would only repeat the
            # failure (the lazy import on first use still reports it).
            _failed_entries.add(name)
        except Exception as exc:
            # Possibly transient (an antivirus briefly locking a DLL, say): leave
            # it to a later pass -- but not forever: one that keeps failing (a
            # missing VC++ runtime, say) stops being retried after a couple of tries.
            logger.debug("[warmup:%s] skip %s: %s", label, name, exc)
            _failure_counts[name] = _failure_counts.get(name, 0) + 1
            if _failure_counts[name] >= _MAX_IMPORT_FAILURES:
                _failed_entries.add(name)
        # Yield between modules, giving the running event loop a chance to reclaim the GIL.
        time.sleep(0)
    # Config re-checks run on every relevant settings save; keep them out of the
    # INFO log unless they actually imported something.
    level = logging.INFO if label == "main" or module_list else logging.DEBUG
    logger.log(
        level,
        "[warmup:%s] done: %d/%d modules in %.0f ms",
        label, loaded, len(module_list), (time.monotonic() - t0) * 1000,
    )


def _missing(entry: str) -> bool:
    return entry not in sys.modules and entry not in _failed_entries


def _unloaded_gates(scope: Iterable[str] | None = None) -> set[str]:
    """Gates (within ``scope``, all when None) that still have a module to import."""
    gates = {gate for entry, gate in _ENTRY_GATES.items() if _missing(entry)}
    return gates if scope is None else gates & set(scope)


def _unloaded_plain_entries() -> list[str]:
    """Ungated module entries the startup pass never reached (its thread did not start)."""
    return [
        entry for entry in MAIN_SERVER_WARMUP
        if entry not in _ENTRY_GATES and ":" not in entry and _missing(entry)
    ]


def _pending_gated_entries(
    skip_loaded: bool,
    label: str,
    scope: Iterable[str] | None = None,
) -> tuple[str, ...]:
    if not skip_loaded:
        entries = select_warmup_entries(MAIN_SERVER_WARMUP, evaluate_gates())
        skipped = [entry for entry in MAIN_SERVER_WARMUP if entry not in entries]
        logger.log(
            logging.INFO if label == "main" else logging.DEBUG,
            "[warmup:%s] not needed by current config: %s",
            label, ", ".join(skipped) or "(none)",
        )
        return entries
    # A re-check only has to pick up what became reachable since the last pass:
    # gated modules not loaded yet, for the gates in ``scope``. Gates left out
    # (out of scope, or all their modules loaded) are not evaluated, so they cost
    # no config reads. Ungated modules still missing are retried too.
    results = evaluate_gates(only=_unloaded_gates(scope))
    plain = set(_unloaded_plain_entries())
    if _startup_pass_missing:
        # The startup pass never ran: the "module:attribute" entries need a pass too.
        plain |= {entry for entry in MAIN_SERVER_WARMUP if ":" in entry}
    entries = tuple(
        entry for entry in MAIN_SERVER_WARMUP
        if entry in plain
        or (
            entry in _ENTRY_GATES
            and _missing(entry)
            and results.get(_ENTRY_GATES[entry], False)
        )
    )
    logger.debug("[warmup:%s] newly needed: %s", label, ", ".join(entries) or "(none)")
    return entries


def _merge_scope(a: frozenset[str] | None, b: frozenset[str] | None) -> frozenset[str] | None:
    return None if a is None or b is None else a | b


def _run_gated_warmup(label: str, scope: frozenset[str] | None = None) -> None:
    """Worker body: warm the gated selection, again while requests keep arriving."""
    global _gated_running, _gated_pending, _gated_pending_scope, _startup_pass_missing
    # Only the startup pass walks the whole selection (the "module:attribute"
    # entries need it); a config re-check is about what is not loaded yet.
    skip_loaded = label != "main"
    while True:
        full_pass = scope is None
        try:
            _warm_entries(_pending_gated_entries(skip_loaded, label, scope), label)
            if full_pass:
                # Only once a full pass got through; a failed one leaves it for the next.
                _startup_pass_missing = False
        except Exception as exc:  # best effort, like every other warmup failure
            logger.debug("[warmup:%s] gated warmup failed: %s", label, exc)
        skip_loaded = True
        label = "config"
        with _warmup_lock:
            if not _gated_pending:
                _gated_running = False
                return
            _gated_pending = False
            scope, _gated_pending_scope = _gated_pending_scope, None


def _under_pytest() -> bool:
    # Skip warming in tests: real heavy imports in daemon threads slow tests and log errors
    # after test logging teardown. Warming is a pure optimization without a behavior contract, so skipping is safe.
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _spawn_gated_worker(label: str, scope: frozenset[str] | None = None) -> None:
    threading.Thread(
        target=_run_gated_warmup, args=(label, scope),
        name=f"module-warmup-{label}", daemon=True,
    ).start()


def start_main_server_warmup() -> bool:
    """Warm the part of ``MAIN_SERVER_WARMUP`` the current config needs; once per process."""
    if _under_pytest():
        return False

    global _gated_started, _gated_running, _startup_pass_missing
    with _warmup_lock:
        if _gated_started:
            return False
        _gated_started = True
        _gated_running = True
    try:
        _spawn_gated_worker("main")
    except Exception as exc:
        # Leave the re-check usable: a stuck "running" flag would park every
        # later request as pending with no worker to pick it up. The next
        # re-check runs over every gate and also warms what this pass never
        # reached, including the "module:attribute" entries.
        with _warmup_lock:
            _gated_running = False
            _startup_pass_missing = True
        logger.debug("[warmup] startup warmup not started: %s", exc)
        return False
    return True


def request_config_rewarm(reason: str = "config", only: Iterable[str] | None = None) -> bool:
    """Re-run the gates after a config save; returns whether a worker was started.

    ``only`` limits the re-check to the gates a writer can affect (a character
    save only touches the voice), so frequent writers do not re-read every
    model slot. A no-op until ``start_main_server_warmup`` has run in this
    process, so the shared config writers can call it unconditionally: in a split
    deployment the memory and agent servers never warm these SDKs. Never blocks
    and never raises (callers sit right after a successful save); the gates read
    config in the worker thread.
    """
    if _under_pytest():
        return False
    global _gated_running, _gated_pending, _gated_pending_scope
    # After a startup pass that never ran, the first re-check covers everything.
    scope = None if only is None or _startup_pass_missing else frozenset(only)
    # Nothing left that this change could add: no thread, no config reads --
    # unless an earlier request was kept waiting after its thread failed to
    # start, which is checked under the lock below.
    nothing_new = (
        not _startup_pass_missing
        and not _unloaded_gates(scope)
        and not _unloaded_plain_entries()
    )

    with _warmup_lock:
        if not _gated_started:
            return False
        if nothing_new and not _gated_pending:
            return False
        if _gated_running:
            _gated_pending_scope = scope if not _gated_pending else _merge_scope(_gated_pending_scope, scope)
            _gated_pending = True
            return False
        if _gated_pending:
            # Left over from a re-check whose thread failed to start: take it along.
            scope = _merge_scope(_gated_pending_scope, scope)
            _gated_pending, _gated_pending_scope = False, None
        _gated_running = True
    try:
        logger.debug("[warmup] re-checking warm targets after %s change", reason)
        _spawn_gated_worker("config", scope)
    except Exception as exc:
        # No worker exists to consume this request (or one queued meanwhile):
        # keep it pending, so the next config save starts a worker that covers it.
        with _warmup_lock:
            _gated_running = False
            _gated_pending_scope = scope if not _gated_pending else _merge_scope(_gated_pending_scope, scope)
            _gated_pending = True
        logger.debug("[warmup] re-check not started: %s", exc)
        return False
    return True
