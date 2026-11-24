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
