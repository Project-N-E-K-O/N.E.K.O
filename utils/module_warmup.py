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

GIL note: pure-Python modules hold the GIL while being parsed, but the bulk here is C
extension dlopen (which releases the GIL) and file IO, while the event loop is mostly
awaiting IO, so a low-priority daemon thread can make progress in the gaps without
stalling the loop. This is best-effort warmup, not a correctness path — any failure is
swallowed; the lazy import on first use remains the single source of truth.
"""
from __future__ import annotations

import importlib
import os
import threading
import time

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

# Heavy modules to warm after main_server is ready. Importing genai brings in mcp,
# so listing genai makes a separate mcp entry unnecessary. translatepy subtranslators each have data tables; list them individually
# so all enter the sys.modules cache.
MAIN_SERVER_WARMUP: tuple[str, ...] = (
    # openai/anthropic were once top-level utils/llm_client imports, at the front of the merged startup's
    # serial chain (~0.7s total, mostly pydantic model construction). Warm lazy imports here so the first real
    # LLM call (greeting, etc.) need not wait for imports.
    "openai",
    "anthropic",
    "google.genai",
    "google.genai.types",
    "translatepy",
    "translatepy.translators.microsoft",
    "translatepy.translators.bing",
    "translatepy.translators.reverso",
    "translatepy.translators.libre",
    "translatepy.translators.mymemory",
    "translatepy.translators.translatecom",
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

_warmup_lock = threading.Lock()
_warmup_started = False


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


def start_background_warmup(modules, *, label: str = "server") -> bool:
    """Start a daemon thread to warm up ``modules``; runs only once per process.

    Returns whether a thread was actually started (``False`` if it already ran).
    """
    # Skip warming in tests: real heavy imports in daemon threads slow tests and log errors
    # after test logging teardown. Warming is a pure optimization without a behavior contract, so skipping is safe.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False

    global _warmup_started
    with _warmup_lock:
        if _warmup_started:
            return False
        _warmup_started = True

    module_list = tuple(modules)

    def _run() -> None:
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
            except Exception as exc:
                logger.debug("[warmup:%s] skip %s: %s", label, name, exc)
            # Yield between modules, giving the running event loop a chance to reclaim the GIL.
            time.sleep(0)
        logger.info(
            "[warmup:%s] done: %d/%d modules in %.0f ms",
            label, loaded, len(module_list), (time.monotonic() - t0) * 1000,
        )

    threading.Thread(target=_run, name=f"module-warmup-{label}", daemon=True).start()
    return True
