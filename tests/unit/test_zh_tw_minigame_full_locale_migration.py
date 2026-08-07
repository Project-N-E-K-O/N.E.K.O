# -*- coding: utf-8 -*-
"""The rest of the minigame family stops collapsing the locale (issue #2500 step 2).

The first pass migrated only the soccer passive guard. An audit of the whole family
(transitive closure over the four minigame prompt modules, then every call site's
locale source in main_routers/game_router/) found five more root producers still
handing over a SHORT code, so every zh-TW row behind them stayed unreachable data:

  1. /api/{game_type}/realtime-context  — _resolve_game_prompt_language
  2. /api/soccer/quick-lines            — request_language_full was computed for badminton only
  3. session entry entry["user_language"] — recent-history labels, anger-cap copy, chat event prompt
  4. context organizer                  — char_info["user_language"]
  5. soccer pregame fallback leg        — char_info["user_language"]

None of the five was a regression: a SHORT code in meant the normalizer collapsed
it exactly as before. They were simply not migrated.

Two guards, deliberately different in shape, because neither alone covers the family:
the AST scan is precise but sees one assignment hop, and the producer invariants are
coarse but cross function boundaries. Both are validated against the pre-migration
shape rather than merely asserted to pass.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

import utils.llm_client as llm_client_module
from config.prompts.prompts_minigame_route import (
    get_game_chat_event_user_prompt,
    get_game_context_organizer_system_prompt,
)
from main_routers.game_router import game_context as gr_game_context
from main_routers.game_router import session_pool as gr_session_pool

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GAME_ROUTER_DIR = REPO_ROOT / "main_routers" / "game_router"

# Sources that yield a FULL locale. Any of these in the expression makes it
# compliant -- `full or short` is a deliberate degrade (an entry built before the
# field existed still has only the short code), not a collapse of the script.
_FULL_PRODUCERS = (
    "user_language_full",
    "_resolve_game_prompt_locale",
    "_extract_request_language_full",
    "_archive_prompt_language",
    "_entry_prompt_locale",
    "prompt_locale",
)
_SHORT_PRODUCERS = ("_resolve_game_prompt_language", "_absorb_request_language")


def _minigame_accessor_names() -> set[str]:
    """Public accessors reaching ``_normalize_prompt_lang``, via AST transitive closure.

    Discovered rather than listed: a hand-written list only covers the functions
    that existed the day it was written.
    """
    reaching = {"_normalize_prompt_lang", "_localized_template", "_labels"}
    trees = {
        name: ast.parse((REPO_ROOT / "config" / "prompts" / f"{name}.py").read_text(encoding="utf-8"))
        for name in (
            "prompts_soccer",
            "prompts_badminton",
            "prompts_minigame_route",
            "prompts_minigame_common",
        )
    }
    for _ in range(6):
        for tree in trees.values():
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                called = {
                    node.func.id
                    for node in ast.walk(fn)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                }
                if called & reaching:
                    reaching.add(fn.name)
    return {name for name in reaching if not name.startswith("_")}


def _is_short_only(expr_src: str) -> bool:
    if any(marker in expr_src for marker in _FULL_PRODUCERS):
        return False
    if any(marker in expr_src for marker in _SHORT_PRODUCERS):
        return True
    index = 0
    while True:
        index = expr_src.find("user_language", index)
        if index < 0:
            return False
        if not expr_src[index:].startswith("user_language_full"):
            return True
        index += 1


def _short_only_lookups(root: pathlib.Path) -> list[str]:
    """Locale arguments handed to a minigame accessor from a short-only source."""
    accessors = _minigame_accessor_names()
    offenders: list[str] = []
    for path in sorted(root.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            assigns: dict[str, str] = {}
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                ):
                    assigns[node.targets[0].id] = ast.get_source_segment(src, node.value) or ""
            for node in ast.walk(fn):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in accessors
                ):
                    continue
                args = list(node.args) + [
                    kw.value for kw in node.keywords if kw.arg in (None, "lang", "language")
                ]
                for arg in args:
                    arg_src = ast.get_source_segment(src, arg) or ""
                    resolved = assigns.get(arg_src, arg_src) if isinstance(arg, ast.Name) else arg_src
                    if _is_short_only(resolved):
                        flat = " ".join(resolved.split())[:70]
                        offenders.append(f"{path.name}:{node.lineno} {node.func.id}(<- {flat})")
    return sorted(set(offenders))


def test_no_minigame_prompt_lookup_is_fed_by_a_short_only_source():
    """Auto-discovered, not a checklist.

    LIMIT, stated so a green is not read as more than it is: this resolves one
    hop (a local assignment in the same function). A locale arriving as a plain
    parameter and passed straight on is invisible here -- those paths are pinned
    by ``test_short_locale_producers_are_gone_from_the_prompt_paths`` instead.
    """
    offenders = _short_only_lookups(GAME_ROUTER_DIR)
    assert offenders == [], (
        "these hand a minigame prompt accessor a SHORT-only locale, so every zh-TW "
        "row behind them stays unreachable: " + "; ".join(offenders)
    )


def test_the_ast_guard_can_actually_fail(tmp_path):
    """A guard whose shapes do not match real code is worth nothing.

    Feeds the detector the exact shape runtime.py carried before this change.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def f(entry):\n"
        "    return get_game_chat_event_user_prompt(entry.get('user_language'))\n",
        encoding="utf-8",
    )
    assert _short_only_lookups(tmp_path), "detector failed to flag a known-bad shape"


def test_the_accessor_discovery_is_not_empty():
    """An empty accessor set would make the scan above vacuously green."""
    accessors = _minigame_accessor_names()
    assert len(accessors) >= 20
    assert "get_soccer_quick_lines_prompt" in accessors
    assert "get_game_chat_event_user_prompt" in accessors


def test_short_locale_producers_are_gone_from_the_prompt_paths():
    """Covers the cross-function pass-through the AST scan cannot see.

    ``_resolve_game_prompt_language`` and a bare ``entry["user_language"]`` were the
    two short-only producers reaching minigame prompts through a parameter hop
    (realtime-context; recent-history labels and the anger-cap copy). Both have a
    full-code twin now, so neither belongs on these paths. ``char_info.py`` is
    excluded on purpose: it is where the SHORT field is legitimately produced.
    """
    callers = [
        path.name
        for path in sorted(GAME_ROUTER_DIR.glob("*.py"))
        if path.name != "char_info.py"
        and "_resolve_game_prompt_language(" in path.read_text(encoding="utf-8")
    ]
    assert callers == [], (
        f"these still call the SHORT resolver instead of _resolve_game_prompt_locale: {callers}"
    )

    runtime_src = (GAME_ROUTER_DIR / "runtime.py").read_text(encoding="utf-8")
    assert 'entry.get("user_language")' not in runtime_src, (
        "runtime.py reads the entry's SHORT locale directly; use _entry_prompt_locale"
    )
    assert "_extract_request_language_full(data) if _is_badminton" not in runtime_src, (
        "quick-lines computes the full locale for badminton only, so soccer's zh-TW "
        "quick-lines rows stay unreachable behind that gate"
    )


def test_every_entry_write_site_sets_the_full_locale():
    """``_entry_prompt_locale`` is only as good as what the entry actually carries.

    Found by mutation: dropping ``user_language_full`` from the entry dict left the
    whole suite green, because every other test here hands the helper a hand-rolled
    entry. Both write sites -- construction and refresh -- are pinned here, and the
    count is asserted so a third one added later cannot quietly skip the field.
    """
    src = (GAME_ROUTER_DIR / "session_pool.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    short_writes, full_writes = [], []
    for node in ast.walk(tree):
        # entry["user_language"] = ... / entry["user_language_full"] = ...
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "entry"
                    and isinstance(target.slice, ast.Constant)
                ):
                    if target.slice.value == "user_language":
                        short_writes.append(node.lineno)
                    elif target.slice.value == "user_language_full":
                        full_writes.append(node.lineno)
        # {'user_language': ..., 'user_language_full': ...}
        if isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
            if "user_language" in keys:
                short_writes.append(node.lineno)
                if "user_language_full" in keys:
                    full_writes.append(node.lineno)

    assert short_writes, "no entry write site found -- the scan stopped matching the code"
    assert len(full_writes) == len(short_writes), (
        "every place session_pool writes the entry's short locale must write the full "
        f"one too; short at lines {sorted(short_writes)}, full at {sorted(full_writes)}"
    )


@pytest.mark.parametrize(
    "entry, expected",
    [
        ({"user_language": "zh", "user_language_full": "zh-TW"}, "zh-TW"),
        ({"user_language": "zh", "user_language_full": "zh-CN"}, "zh-CN"),
        # 老 entry（建于该字段存在之前）退回短码，而不是 None。
        ({"user_language": "zh"}, "zh"),
        ({"user_language": "zh", "user_language_full": None}, "zh"),
        ({}, ""),
        (None, ""),
    ],
)
def test_entry_prompt_locale_prefers_the_full_code(entry, expected):
    assert gr_session_pool._entry_prompt_locale(entry) == expected


def test_entry_prompt_locale_reaches_the_traditional_template():
    """Returning 'zh-TW' is not the point -- selecting the Traditional row is.

    Asserted through ``get_game_context_organizer_system_prompt`` rather than
    ``get_game_chat_event_user_prompt``: the latter is one of the paired
    above/below input watermarks, which are deliberately the SAME Chinese string
    in all eight locales (they mark a data-block boundary for the model, they are
    not copy), so it can never tell the two scripts apart. See the note above
    ``PREGAME_CONTEXT_INPUT_WATERMARK`` in prompts_minigame_common.
    """
    entry = {"user_language": "zh", "user_language_full": "zh-TW"}
    locale = gr_session_pool._entry_prompt_locale(entry)
    traditional = get_game_context_organizer_system_prompt(locale)
    assert traditional == get_game_context_organizer_system_prompt("zh-TW")
    assert traditional != get_game_context_organizer_system_prompt("zh")

    # 水印表照旧逐 locale 相同 —— 这是有意的，不是没翻。
    assert get_game_chat_event_user_prompt("zh-TW") == get_game_chat_event_user_prompt("zh")


class _FakeResult:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, sink):
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def ainvoke(self, messages):
        self._sink.append(messages)
        return _FakeResult('{"rollingSummary":"x","signals":{}}')


def test_context_organizer_uses_the_traditional_prompt(monkeypatch):
    """End-to-end through the real ``_run_game_context_organizer_ai`` body."""
    sink: list = []
    monkeypatch.setattr(
        gr_game_context,
        "_get_game_route_summary_llm_info",
        lambda *a, **k: {
            "model": "m",
            "base_url": "http://localhost",
            "api_key": "k",
            "provider_type": "openai",
            "api_type": "openai",
            "user_language": "zh",
            "user_language_full": "zh-TW",
        },
    )

    async def _fake_create(*args, **kwargs):
        return _FakeLLM(sink)

    monkeypatch.setattr(llm_client_module, "create_chat_llm_async", _fake_create)

    state = {"lanlan_name": "Lan", "game_type": "soccer", "session_id": "s1"}
    asyncio.run(gr_game_context._run_game_context_organizer_ai(state, []))

    assert sink, "LLM 没被调用，说明前置分支提前返回了"
    system_text = sink[0][0].content
    assert system_text == get_game_context_organizer_system_prompt("zh-TW")
    assert system_text != get_game_context_organizer_system_prompt("zh")


def test_context_organizer_stays_simplified_for_a_simplified_session(monkeypatch):
    sink: list = []
    monkeypatch.setattr(
        gr_game_context,
        "_get_game_route_summary_llm_info",
        lambda *a, **k: {
            "model": "m",
            "base_url": "http://localhost",
            "api_key": "k",
            "provider_type": "openai",
            "api_type": "openai",
            "user_language": "zh",
            "user_language_full": "zh-CN",
        },
    )

    async def _fake_create(*args, **kwargs):
        return _FakeLLM(sink)

    monkeypatch.setattr(llm_client_module, "create_chat_llm_async", _fake_create)

    state = {"lanlan_name": "Lan", "game_type": "soccer", "session_id": "s1"}
    asyncio.run(gr_game_context._run_game_context_organizer_ai(state, []))
    assert sink[0][0].content == get_game_context_organizer_system_prompt("zh")
