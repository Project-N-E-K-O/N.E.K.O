# -*- coding: utf-8 -*-
"""
Wire concrete higher-layer helpers into ``config._runtime`` /
``main_logic.agent_event_bus`` at app startup.

Background — layering
---------------------
A few modules at the bottom of the dependency stack (``config`` at L0,
``main_logic`` at L2) need to call helpers that live at higher layers
(``utils.language_utils`` at L1; ``plugin.core.state`` at L4;
``main_routers.system_router`` at L3). Importing those directly would invert
the dependency layering (lower importing higher) and in some cases close a
cycle. ``scripts/check_module_layering.py`` enforces the ordering.

Pattern: the lower modules expose a ``register_X(fn)`` registry; this module
does all the registration in one place and is called from ``app/__init__.py``
so every server entrypoint (main_server / memory_server / agent_server /
monitor) — whether spawned as separate processes or merged in launcher — gets
the bindings before any prompt builder / event dispatcher actually runs.

Each binding block is wrapped in its own try/except so an entrypoint that
doesn't ship a particular layer (e.g. memory_server has no plugin runtime,
agent_server has no main_routers) still installs the bindings it CAN install
and silently skips the rest.

This module is allowed to import from any layer because it lives in the L6
``app`` (entrypoint) layer, which is the highest in the stack.
"""
from __future__ import annotations

_BOUND = False


def install_runtime_bindings() -> None:
    """Idempotent — safe to call from every entrypoint and from tests."""
    global _BOUND
    if _BOUND:
        return
    _BOUND = True

    # ---- config._runtime ← utils.language_utils + utils.tokenize ----------
    try:
        from config._runtime import (
            register_global_language_resolver,
            register_language_normalizer,
            register_steam_language_resolver,
            register_truncate_to_tokens,
        )
        from utils.language_utils import (
            _get_steam_language,
            get_global_language_full,
            normalize_language_code,
        )
        from utils.tokenize import truncate_to_tokens

        register_global_language_resolver(get_global_language_full)
        register_steam_language_resolver(_get_steam_language)
        register_language_normalizer(normalize_language_code)
        register_truncate_to_tokens(truncate_to_tokens)
    except Exception:
        # Some entrypoints (or test environments) may not ship the full utils
        # surface; the resolvers in config._runtime fall back to defaults.
        pass

    # ---- main_logic.agent_event_bus ← plugin.core.state -------------------
    try:
        from main_logic.agent_event_bus import register_user_utterance_sink
        from plugin.core.state import state as plugin_state

        register_user_utterance_sink(plugin_state.add_user_context_event)
    except Exception:
        # memory_server / agent_server don't ship plugin runtime — leaving
        # the sink unregistered is the correct "no plugin consumers here"
        # behaviour; main_logic's dispatcher silently no-ops.
        pass

    # ---- main_logic.agent_event_bus ← main_routers.system_router ---------
    try:
        from main_logic.agent_event_bus import register_text_user_message_hook
        from main_routers.system_router import _maybe_apply_mini_game_invite_keyword

        register_text_user_message_hook(_maybe_apply_mini_game_invite_keyword)
    except Exception:
        # Only main_server hosts main_routers. Other entrypoints skip this
        # hook; the dispatcher returns None when no hook matches.
        pass
