# -*- coding: utf-8 -*-
"""
Wire concrete higher-layer helpers into ``config._runtime`` at app startup.

Background — layering
---------------------
``config`` lives at L0 (foundation) and must not import from ``utils`` (L1).
A few prompt-builders inside ``config/prompts/`` legitimately need to call
helpers that live higher up (language detection, tokenize-aware truncation).
``config._runtime`` exposes a ``register_X(fn)`` registry; this module wires
the concrete impls from ``utils.language_utils`` / ``utils.tokenize`` at app
startup. Called from ``app/__init__.py`` so every server entrypoint
(main_server / memory_server / agent_server / monitor) — whether spawned as
separate processes or merged in launcher — gets the bindings before any
prompt builder actually runs.

Plugin-side and main_routers-side event-bus consumers self-register at their
own module-import time (``plugin/core/state.py``, ``main_routers/system_router.py``)
to keep the dispatch path alive for direct importers (testbench / ad-hoc
scripts) that don't go through this entrypoint at all. The registries dedupe
on identity, so calling them again here would be a no-op.

Idempotency: a single per-block flag tracks success. A failed block's flag
stays False so a later call retries (transient import-order / partial-env
fixes); a successful block short-circuits to avoid double-registration.

This module is allowed to import from any layer because it lives in the L6
``app`` (entrypoint) layer, the highest in the stack.
"""
from __future__ import annotations

# Per-block "successfully installed" flags. Currently only one block, but
# kept as a dict so future bindings (other lower-layer DI registries) can
# slot in without restructuring.
_INSTALLED: dict[str, bool] = {
    "config_runtime": False,
}


def install_runtime_bindings() -> None:
    """Install runtime bindings, retrying any block that previously failed.

    Safe to call repeatedly — successful blocks short-circuit.
    """

    # ---- config._runtime ← utils.language_utils + utils.tokenize ----------
    if not _INSTALLED["config_runtime"]:
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
            _INSTALLED["config_runtime"] = True
        except (ImportError, ModuleNotFoundError, AttributeError):
            # Expected silent path: entrypoint or test env doesn't ship the
            # full utils surface. The resolvers in config._runtime fall back
            # to defaults. Flag stays False so a later call can retry.
            pass
        except Exception:
            # Anything else (signature mismatch, real bug in a register_*
            # impl, etc.) is a regression we want loud — log with traceback
            # but DON'T re-raise; flag stays False so a later call can retry
            # if the underlying issue gets fixed in-process. The logger lives
            # in utils so we import it lazily to keep this block resilient
            # against a missing utils itself (caught by the silent path above).
            try:
                from utils.logger_config import get_module_logger
                get_module_logger(__name__, "App").warning(
                    "install_runtime_bindings(config_runtime) failed unexpectedly",
                    exc_info=True,
                )
            except Exception:
                pass
