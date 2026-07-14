"""Slop reduction engine + dialog wiring contract.

The engine rewrites AI-writing clichés in the *outgoing* assistant history so the
model stops imitating its own stock phrases. Two hard invariants this file pins:

1. **promptOnly** — the input messages and their elements are NEVER mutated; only
   a fresh copy is returned. The persisted ``_conversation_history`` and the
   tool-loop's working list must survive untouched.
2. **assistant-only** — system instructions and the user's own words pass through
   verbatim; only the cat's past turns are rewritten.

Plus: malformed rules can never break a turn, ``dry_run`` is side-effect-free,
unknown languages are a no-op, and the ``_dialog_slop_lang`` context var actually
routes through ``ChatOpenAI._params``.
"""
from __future__ import annotations

import random

import pytest

from utils import slop_filter as sf
from utils.llm_client import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    reset_dialog_slop_lang,
    set_dialog_slop_lang,
)


# A deterministic synthetic rule set: single-entry pools → exact assertions.
_HEART = {
    "id": "ZZ_001",
    "name": "heart pounding",
    "find": r"(他|她)的心脏疯狂跳动",
    "replace": [r"\1的胸口闷闷地一沉"],  # \1 carries the captured subject
}
_SMILE = {
    "id": "ZZ_002",
    "name": "corner of mouth",
    "find": r"嘴角勾起一抹弧度",
    "replace": ["神色柔和下来"],
}
_RULES = [_HEART, _SMILE]


def _assistant_dict(text):
    return {"role": "assistant", "content": text}


# ---------------------------------------------------------------------------
# assistant-only + backref
# ---------------------------------------------------------------------------

def test_rewrites_assistant_dict_and_preserves_backref():
    msgs = [_assistant_dict("他的心脏疯狂跳动，久久不能平静。")]
    out = sf.apply_slop_reduction(msgs, "zz", rules=_RULES)
    assert out[0]["content"] == "他的胸口闷闷地一沉，久久不能平静。"


def test_user_and_system_messages_untouched():
    msgs = [
        {"role": "system", "content": "他的心脏疯狂跳动"},   # instruction — must NOT change
        {"role": "user", "content": "他的心脏疯狂跳动"},     # user's words — must NOT change
        _assistant_dict("他的心脏疯狂跳动"),                 # assistant — should change
    ]
    out = sf.apply_slop_reduction(msgs, "zz", rules=_RULES)
    assert out[0]["content"] == "他的心脏疯狂跳动"
    assert out[1]["content"] == "他的心脏疯狂跳动"
    assert out[2]["content"] == "他的胸口闷闷地一沉"


# ---------------------------------------------------------------------------
# promptOnly: no mutation of inputs
# ---------------------------------------------------------------------------

def test_input_list_and_dicts_not_mutated():
    original = _assistant_dict("嘴角勾起一抹弧度")
    msgs = [original]
    out = sf.apply_slop_reduction(msgs, "zz", rules=_RULES)
    # The original dict object is untouched; a new one is returned.
    assert original["content"] == "嘴角勾起一抹弧度"
    assert out is not msgs
    assert out[0] is not original
    assert out[0]["content"] == "神色柔和下来"


def test_basemessage_object_cloned_not_mutated():
    original = AIMessage(content="嘴角勾起一抹弧度")
    out = sf.apply_slop_reduction([original], "zz", rules=_RULES)
    assert original.content == "嘴角勾起一抹弧度"      # original object intact
    assert out[0] is not original
    assert out[0].content == "神色柔和下来"
    assert out[0].type == "ai"                        # clone keeps the role


def test_humanmessage_object_passes_through_same_identity():
    original = HumanMessage(content="他的心脏疯狂跳动")
    out = sf.apply_slop_reduction([original], "zz", rules=_RULES)
    assert out[0] is original  # unchanged role → same object, allocation-free


# ---------------------------------------------------------------------------
# multimodal content (list of parts)
# ---------------------------------------------------------------------------

def test_multimodal_text_part_rewritten_image_part_kept():
    msg = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "嘴角勾起一抹弧度"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ],
    }
    out = sf.apply_slop_reduction([msg], "zz", rules=_RULES)
    assert out[0]["content"][0]["text"] == "神色柔和下来"
    assert out[0]["content"][1] == msg["content"][1]  # image part untouched


# ---------------------------------------------------------------------------
# robustness: a malformed rule can never break the turn
# ---------------------------------------------------------------------------

def test_uncompilable_rule_is_skipped():
    bad = {"id": "ZZ_BAD", "name": "broken", "find": r"(unbalanced", "replace": ["x"]}
    msgs = [_assistant_dict("他的心脏疯狂跳动")]
    # The good rule still applies; the broken one is silently skipped.
    out = sf.apply_slop_reduction(msgs, "zz", rules=[bad, _HEART])
    assert out[0]["content"] == "他的胸口闷闷地一沉"


def test_replacement_with_bad_backref_falls_back_to_literal():
    # \2 has no group 2 in the pattern → expand fails → literal template used,
    # never a crash.
    rule = {"id": "ZZ_X", "name": "x", "find": r"心脏", "replace": [r"胸口\2"]}
    out = sf.apply_slop_reduction([_assistant_dict("心脏")], "zz", rules=[rule])
    assert out[0]["content"] == r"胸口\2"


# ---------------------------------------------------------------------------
# dry_run + no-op paths
# ---------------------------------------------------------------------------

def test_dry_run_returns_original_unchanged():
    msgs = [_assistant_dict("他的心脏疯狂跳动")]
    out = sf.apply_slop_reduction(msgs, "zz", rules=_RULES, dry_run=True)
    assert out is msgs
    assert out[0]["content"] == "他的心脏疯狂跳动"


def test_empty_rules_is_identity():
    msgs = [_assistant_dict("他的心脏疯狂跳动")]
    assert sf.apply_slop_reduction(msgs, "zz", rules=[]) is msgs


def test_no_assistant_turn_returns_same_list():
    msgs = [{"role": "user", "content": "他的心脏疯狂跳动"}]
    assert sf.apply_slop_reduction(msgs, "zz", rules=_RULES) is msgs


def test_each_occurrence_picks_independently_from_pool():
    rule = {"id": "ZZ_P", "name": "pool", "find": r"X", "replace": ["a", "b"]}
    msgs = [_assistant_dict("X" * 200)]
    rng = random.Random(1234)
    out = sf.apply_slop_reduction(msgs, "zz", rules=[rule], rng=rng)
    body = out[0]["content"]
    assert set(body) == {"a", "b"}  # both pool entries appear across occurrences


# ---------------------------------------------------------------------------
# gating helper: resolve_dialog_slop_lang
# ---------------------------------------------------------------------------

def test_resolve_dialog_slop_lang_off_when_switch_disabled(monkeypatch):
    monkeypatch.setattr(sf, "is_slop_filter_enabled", lambda: False)
    assert sf.resolve_dialog_slop_lang(lambda: "zh-CN") is None


def test_resolve_dialog_slop_lang_short_code_when_enabled(monkeypatch):
    monkeypatch.setattr(sf, "is_slop_filter_enabled", lambda: True)
    monkeypatch.setattr(sf, "get_rules_for_language", lambda lang: [_HEART] if lang == "zh" else [])
    assert sf.resolve_dialog_slop_lang(lambda: "zh-CN") == "zh"
    assert sf.resolve_dialog_slop_lang(lambda: "ko-KR") is None  # no rules → skip
    assert sf.resolve_dialog_slop_lang(lambda: None) is None


# ---------------------------------------------------------------------------
# llm_client wiring: the context var actually routes through _params
# ---------------------------------------------------------------------------

def _bare_chat_openai(model="m", base_url="https://example.test"):
    from utils.llm_client import ChatOpenAI
    obj = ChatOpenAI.__new__(ChatOpenAI)
    obj.model = model
    obj.base_url = base_url
    obj.temperature = None
    obj.max_completion_tokens = None
    obj.max_tokens = None
    obj.extra_body = {}
    obj.tools = None
    obj.tool_choice = None
    obj.enable_cache_control = False
    return obj


def test_params_applies_slop_only_when_contextvar_armed(monkeypatch):
    monkeypatch.setattr(sf, "get_rules_for_language", lambda lang: _RULES if lang == "zz" else [])

    client = _bare_chat_openai()
    msgs = [
        SystemMessage(content="你是只猫娘"),
        AIMessage(content="他的心脏疯狂跳动"),
    ]

    # Disarmed → assistant content untouched.
    p_off = client._params(msgs)
    assert p_off["messages"][1]["content"] == "他的心脏疯狂跳动"

    # Armed → assistant content rewritten, system prompt still intact.
    token = set_dialog_slop_lang("zz")
    try:
        p_on = client._params(msgs)
    finally:
        reset_dialog_slop_lang(token)
    assert p_on["messages"][0]["content"] == "你是只猫娘"
    assert p_on["messages"][1]["content"] == "他的胸口闷闷地一沉"


# ---------------------------------------------------------------------------
# real rule tables compile + are well-formed (runs once assembled)
# ---------------------------------------------------------------------------

def test_all_static_rules_are_valid():
    import re

    prompts_slop = pytest.importorskip("config.prompts.prompts_slop")
    rules_by_lang = prompts_slop.SLOP_RULES
    assert isinstance(rules_by_lang, dict) and rules_by_lang

    seen_ids = set()
    cjk_langs = {"zh", "ja"}
    for lang, rules in rules_by_lang.items():
        for rule in rules:
            rid = rule.get("id")
            assert rid and rid not in seen_ids, f"duplicate/empty id: {rid}"
            seen_ids.add(rid)

            flags = int(rule.get("flags", 0) or 0)
            try:
                compiled = re.compile(rule["find"], flags)
            except re.error as exc:  # pragma: no cover - fails loudly with which rule
                pytest.fail(f"{rid} pattern does not compile: {exc}\n{rule['find']!r}")

            # CJK rules must not lean on \b (no word spaces) — guard against drift.
            if lang in cjk_langs:
                assert r"\b" not in rule["find"], f"{rid}: \\b in a CJK pattern"

            pool = rule.get("replace")
            assert isinstance(pool, list) and len(pool) >= 5, f"{rid}: thin replace pool"

            # Every backref a pool entry uses must exist as a capture group.
            ngroups = compiled.groups
            for entry in pool:
                for ref in re.findall(r"\\(\d+)", entry):
                    assert int(ref) <= ngroups, f"{rid}: \\{ref} but only {ngroups} groups"
                # zh replacements must stay gender-neutral: the subject is
                # carried by a backref (\1), never a hardcoded 他/她, so a
                # male/1st/2nd-person turn is never misgendered in the rewrite.
                if lang == "zh":
                    assert "她" not in entry and "他" not in entry, (
                        f"{rid}: hardcoded gendered pronoun in replacement {entry!r}"
                    )


# ---------------------------------------------------------------------------
# in-flight tool-call turns are left alone (prefix the user already saw)
# ---------------------------------------------------------------------------

def test_openai_tool_call_prefix_not_rewritten():
    msgs = [
        {"role": "assistant", "content": "他的心脏疯狂跳动", "tool_calls": [{"id": "c1"}]},
    ]
    out = sf.apply_slop_reduction(msgs, "zz", rules=_RULES)
    assert out is msgs  # untouched → same list identity (no rewrite happened)


def test_anthropic_tool_use_block_turn_not_rewritten():
    msgs = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "他的心脏疯狂跳动"},
                {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
            ],
        },
    ]
    out = sf.apply_slop_reduction(msgs, "zz", rules=_RULES)
    assert out[0]["content"][0]["text"] == "他的心脏疯狂跳动"  # text block left alone


# ---------------------------------------------------------------------------
# Traditional Chinese is skipped (shared zh rules are Simplified)
# ---------------------------------------------------------------------------

def test_resolve_dialog_slop_lang_skips_traditional_chinese(monkeypatch):
    monkeypatch.setattr(sf, "is_slop_filter_enabled", lambda: True)
    monkeypatch.setattr(sf, "get_rules_for_language", lambda lang: [_HEART] if lang == "zh" else [])
    assert sf.resolve_dialog_slop_lang(lambda: "zh-CN") == "zh"     # Simplified → apply
    assert sf.resolve_dialog_slop_lang(lambda: "zh-TW") is None     # Traditional → skip
    assert sf.resolve_dialog_slop_lang(lambda: "zh-Hant") is None


# ---------------------------------------------------------------------------
# dialog-slop is suspended around tool handlers so a nested LLM call the handler
# makes (plugin / agent) is treated as a non-dialog call → no-op
# ---------------------------------------------------------------------------

def test_suspend_dialog_slop_hides_lang_from_nested_tool_calls():
    import asyncio
    from utils.llm_client import peek_dialog_slop_lang
    from main_logic.omni_offline_client import _suspend_dialog_slop

    async def _run():
        token = set_dialog_slop_lang("en")  # arm as a dialog turn would
        try:
            assert peek_dialog_slop_lang() == "en"
            with _suspend_dialog_slop():
                # the tool handler (same task) must see a non-dialog call
                assert peek_dialog_slop_lang() is None
                # …and any task it spawns for a nested LLM call inherits None
                assert await asyncio.create_task(_peek()) is None
            assert peek_dialog_slop_lang() == "en"  # re-armed for the tool loop
            # restored even if the handler raised
            try:
                with _suspend_dialog_slop():
                    raise RuntimeError("boom")
            except RuntimeError:
                pass
            assert peek_dialog_slop_lang() == "en"
        finally:
            reset_dialog_slop_lang(token)
        assert peek_dialog_slop_lang() is None

    async def _peek():
        from utils.llm_client import peek_dialog_slop_lang as _p
        return _p()

    asyncio.run(_run())
