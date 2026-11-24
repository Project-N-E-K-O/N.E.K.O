# -*- coding: utf-8 -*-
"""translatepy is imported on the first translation, not by the startup warmup.

The first China-region subtitle translation pays the import (~0.12-0.25 s); it
must happen in a worker thread so the event loop, which serves every other
request, is not held for that time. Nothing here imports translatepy itself.
"""
from __future__ import annotations

import threading

import pytest

from utils import language_utils
from utils.module_warmup import MAIN_SERVER_WARMUP, _ENTRY_GATES

pytestmark = pytest.mark.unit


def test_translatepy_is_not_warmed_at_startup():
    for entry in MAIN_SERVER_WARMUP + tuple(_ENTRY_GATES):
        assert not entry.partition(":")[0].startswith("translatepy"), entry


@pytest.mark.asyncio
async def test_first_china_translation_imports_translatepy_off_the_event_loop(monkeypatch):
    loop_thread = threading.get_ident()
    import_threads: list[int] = []

    def _fake_ensure() -> bool:
        import_threads.append(threading.get_ident())
        return True

    async def _fake_translate(text, source_lang, target_lang):
        return "你好"

    monkeypatch.setattr(language_utils, "is_china_region", lambda: True)
    monkeypatch.setattr(language_utils, "_ensure_translatepy", _fake_ensure)
    monkeypatch.setattr(language_utils, "translate_with_translatepy", _fake_translate)

    translated, _ = await language_utils.translate_text("hello there", "zh", "en")

    assert translated == "你好"
    assert import_threads, "translatepy availability was never checked"
    assert loop_thread not in import_threads, "translatepy import ran on the event loop"


def test_first_imports_from_two_threads_are_serialised(monkeypatch):
    """Two first translations can call _ensure_translatepy from worker threads at once.

    Check, import and record must run as one: otherwise one thread's transient
    import error can overwrite the other's success.
    """
    import sys
    import time
    import types

    inside = {"now": 0, "max": 0}
    lock = threading.Lock()

    class _SlowModule(types.ModuleType):
        def __getattr__(self, name):
            with lock:
                inside["now"] += 1
                inside["max"] = max(inside["max"], inside["now"])
            time.sleep(0.05)
            with lock:
                inside["now"] -= 1
            return type(name, (), {})

    names = [
        "translatepy",
        "translatepy.translators",
        "translatepy.translators.microsoft",
        "translatepy.translators.bing",
        "translatepy.translators.reverso",
        "translatepy.translators.libre",
        "translatepy.translators.mymemory",
        "translatepy.translators.translatecom",
    ]
    for name in names:
        monkeypatch.setitem(sys.modules, name, _SlowModule(name))
    monkeypatch.setattr(language_utils, "TranslatepyTranslator", None)
    monkeypatch.setattr(language_utils, "CHINA_ACCESSIBLE_SERVICES", None)
    monkeypatch.setattr(language_utils, "TRANSLATEPY_AVAILABLE", None)

    results: list[bool] = []
    threads = [threading.Thread(target=lambda: results.append(language_utils._ensure_translatepy())) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert results == [True, True]
    assert inside["max"] == 1, "two threads imported translatepy at the same time"
