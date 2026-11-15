# -*- coding: utf-8 -*-
"""Contract for user ban-topic directives reaching the proactive-chat path.

Before this, "别再提 X" only ever landed in ``_build_initial_prompt``'s system
prompt, and proactive chat assembles its Phase 2 prompt separately — so the one
path where the character speaks *unprompted* was the one path with no knowledge
of what the user just asked it to drop.

Two levels, neither of which may spend an extra LLM round-trip:

1. **Soft** — the rendered directives block is appended to the Phase 2 memory
   context (covered by ``test_service_injects_directive_block_into_phase2``).
2. **Hard** — a draft that still contains a banned term is dropped outright at
   the output gate. Dropped, never regenerated: regen is a paid LLM call, and
   by the time this fires the model has already seen the ban and ignored it.
"""  # noqa: DOCSTRING_CJK  # Quotes actual user speech; translating it changes the tested term.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import memory.anti_repeat as anti_repeat_module
import memory.user_directives as user_directives_module
from config.prompts.prompts_directives import (
    extract_directives,
    is_semantically_empty_term,
    term_needs_case_sensitive_match,
)
from main_logic.proactive_chat.contracts import PROACTIVE_REASON_PASS_USER_DIRECTIVE
from main_logic.proactive_chat.generation import (
    _append_directives_section,
    _guard_phase2_output,
    _proactive_directive_hits,
)
from utils.llm_client import HumanMessage, SystemMessage


class _NeverPreemptedState:
    @staticmethod
    def is_proactive_preempted(*_args):
        return False


def _install_directives(monkeypatch, terms):
    """Point the module-level manager accessor at a stub holding ``terms``."""
    manager = MagicMock()
    manager.get_active_terms.return_value = list(terms)
    monkeypatch.setattr(
        user_directives_module,
        "get_user_directives_manager",
        lambda: manager,
    )
    return manager


def _install_quiet_corpus(monkeypatch):
    """Anti-repeat that never fires, so any drop is attributable to the ban gate."""
    corpus = MagicMock()
    corpus.apreload = AsyncMock()
    corpus.score_unanswered_proactive_draft.return_value = (
        anti_repeat_module.UnansweredProactiveRepeatSignal(
            triggered=False,
            match_count=0,
            considered_count=0,
            best_similarity=0.0,
            repeated_terms=(),
        )
    )
    corpus.score_draft.return_value = (0.0, {})
    monkeypatch.setattr(
        anti_repeat_module, "get_anti_repeat_corpus", lambda: corpus,
    )
    return corpus


async def _run_guard(
    *,
    lanlan_name,
    response_text,
    make_llm,
    source_tag="CHAT",
    selected_music_link=None,
    music_content=None,
):
    mgr = SimpleNamespace(
        current_speech_id="sid",
        state=_NeverPreemptedState(),
        last_user_message_time=None,
        proactive_engagement_observation_started_at=100.0,
        handle_new_message=AsyncMock(),
    )
    output = await _guard_phase2_output(
        mgr=mgr,
        proactive_sid="sid",
        lanlan_name=lanlan_name,
        response_text=response_text,
        full_text=response_text,
        source_tag=source_tag,
        active_channels=[],
        selected_music_link=selected_music_link,
        selected_meme_link=None,
        music_content=music_content,
        meme_content=None,
        is_playing_music=False,
        music_cooldown=False,
        expects_source_tag=False,
        make_llm=make_llm,
        messages=[
            SystemMessage(content="system"),
            HumanMessage(content="begin"),
        ],
        human_text="begin",
        screenshot_b64=None,
        phase2_use_vision=False,
        phase2_disable_thinking=True,
        proactive_lang="zh",
        master_name="博士",
    )
    return output, mgr


# ── matcher ──────────────────────────────────────────────────────────


def test_matcher_finds_banned_term_in_draft(monkeypatch):
    _install_directives(monkeypatch, ["加班"])
    assert _proactive_directive_hits("Neko", "今天又加班到很晚吧？") == ["加班"]


def test_matcher_is_case_insensitive(monkeypatch):
    _install_directives(monkeypatch, ["Work"])
    assert _proactive_directive_hits("Neko", "how was WORK today") == ["Work"]


def test_matcher_clean_draft_has_no_hits(monkeypatch):
    _install_directives(monkeypatch, ["加班"])
    assert _proactive_directive_hits("Neko", "今天天气真好啊") == []


def test_matcher_returns_every_hit(monkeypatch):
    _install_directives(monkeypatch, ["加班", "股票", "前任"])
    hits = _proactive_directive_hits("Neko", "聊聊加班和股票吧")
    assert set(hits) == {"加班", "股票"}


def test_matcher_never_raises_when_memory_unavailable(monkeypatch):
    """Recall failure must degrade to "no hit", not take the turn down."""
    def _boom():
        raise RuntimeError("memory dir gone")

    monkeypatch.setattr(
        user_directives_module, "get_user_directives_manager", _boom,
    )
    assert _proactive_directive_hits("Neko", "今天又加班到很晚吧？") == []


@pytest.mark.parametrize("term,draft", [
    ("这个", "这个周末有什么安排吗？"),
    ("這個", "這個週末有什麼安排嗎？"),
    ("那个", "那个电影你看了吗？"),
    ("这件事", "这件事我一直想问你。"),
    ("this", "how is this going for you"),
    ("それ", "それは面白いですね"),
    ("esto", "esto es interesante"),
])
def test_bare_referents_never_hard_block(monkeypatch, term, draft):
    """A bare referent must not hard-block output, however often it matches."""
    # Extraction does store such terms: "别再讲这个了" yields ``这个``. Existing
    # simplified/traditional/compound guards explicitly test ``这件事`` and pin this behavior;
    # this PR must not alter it. But substring checks for highly frequent Chinese words
    # silence proactive chat throughout the directive's escalating TTL, up to 30 days, without any
    # current UI to inspect/delete it. Fix consumption: retain soft prompt constraints but skip hard blocking.
    _install_directives(monkeypatch, [term])
    assert _proactive_directive_hits("Neko", draft) == []


@pytest.mark.parametrize("utterance,draft", [
    ("stop talking about me", "Hey, let me know how the build went!"),
    ("stop talking about us", "Want us to pick a movie tonight?"),
    ("以后别提我们了", "我们要不要一起看个电影？"),
    ("别再说你自己了", "你自己最近还好吗？"),
    ("别再提自己了", "自己一个人别硬扛啊。"),
])
def test_pronoun_directives_never_silence_ordinary_drafts(
    monkeypatch, utterance, draft,
):
    """Personal pronouns are the same axis as demonstratives, and hurt more.

    "别再提我了" / "stop talking about me" is one of the most natural ways to
    use this feature, and the term it yields is ``me`` — which matches three
    out of three perfectly ordinary English drafts. Word boundaries do not
    save it: ``me`` *is* a whole word. That is the identical P1 this table was
    first built for (``it`` matching ``favorite``), and with the escalating
    TTL the blast radius went from 3 days to 30.
    """  # noqa: DOCSTRING_CJK  # Quotes the user's actual spoken phrase.
    # Premise assertion: this test matters only if extraction actually produces this term. Future regex/
    # length changes could otherwise make it silently vacuous.
    hits = extract_directives(utterance)
    assert hits, f"{utterance!r} 不再被抽成 ban_topic，本测试的前提已失效"
    terms = [t for _, _, t in hits]
    _install_directives(monkeypatch, terms)

    assert _proactive_directive_hits("Neko", draft) == []


@pytest.mark.parametrize("term,blocked_draft,ignored_draft", [
    # Country names versus pronouns.
    ("US", "The US economy is wild these days.", "Want us to pick a movie?"),
    # Films "我们" (Us) / "她" (Her).
    ("Us", "Want to watch Us tonight?", "Want us to pick a movie?"),
    ("Her", "Her is still my favorite film.", "Did you ask her about it?"),
    # #3013 R4: IT industry versus pronoun it.
    ("IT", "The IT department replied.", "How is it going for you?"),
])
def test_capitalized_terms_are_names_not_pronouns(
    monkeypatch, term, blocked_draft, ignored_draft,
):
    """A capitalized term is a proper name: still gated, but matched case-sensitively.

    Both halves are load-bearing and neither works alone:

    - exempting it (case-folding down onto the pronoun table) silently drops
      the hard gate for a topic the user explicitly banned — strictly worse
      than before the pronoun entries existed;
    - gating it while still matching case-insensitively fires on every
      ordinary ``us`` / ``her`` / ``it`` in the draft, which is the
      proactive-silence P1 the table was built to prevent.
    """
    _install_directives(monkeypatch, [term])
    assert _proactive_directive_hits("Neko", blocked_draft) == [term]
    assert _proactive_directive_hits("Neko", ignored_draft) == []


@pytest.mark.parametrize("term", ["us", "her", "me", "it", "this"])
def test_lowercase_pronouns_stay_exempt(monkeypatch, term):
    """Control for the rule above: the lowercase spelling is still the pronoun."""
    # Without this, inverting exemption capitalization rules could still pass the tests above.
    _install_directives(monkeypatch, [term])
    assert _proactive_directive_hits(
        "Neko", "Want us to pick a movie, or should I ask her about it?",
    ) == []


@pytest.mark.parametrize("term,exempt,case_sensitive", [
    # Table match plus lowercase -> exempt hard blocking.
    ("us", True, False),
    ("it", True, False),
    ("me", True, False),
    # Table match plus uppercase -> no exemption, with case-sensitive matching required.
    ("US", False, True),
    ("Us", False, True),
    ("IT", False, True),
    # No table match -> both False, using normal case-insensitive matching.
    ("work", False, False),
    ("Work", False, False),
    ("加班", False, False),
])
def test_exemption_and_case_sensitivity_are_complementary(
    term, exempt, case_sensitive,
):
    """For any term, at most one of the two rules applies — never both.

    The pair has to stay complementary or one of two P1s comes back: exempting
    a capitalized name silently drops the ban, and case-folding it back down
    fires on every ordinary pronoun in the draft. Note the third group — the
    case-sensitive rule must NOT widen to "any capitalized term", or an
    IME-capitalized ``Work`` stops matching ``work``.
    """
    assert is_semantically_empty_term(term) is exempt
    assert term_needs_case_sensitive_match(term) is case_sensitive
    assert not (exempt and case_sensitive)


def test_pronoun_skip_does_not_disarm_the_gate_for_content_terms(monkeypatch):
    """Control: the same drafts must still be blocked by a real content term."""
    # Without this control, disabling the entire hard gate also passes, confirmed by mutation testing.
    _install_directives(monkeypatch, ["加班"])
    assert _proactive_directive_hits("Neko", "我们今天还要加班吗？") == ["加班"]


def test_accented_pronoun_is_matched_regardless_of_unicode_form(monkeypatch):
    """A decomposed accented pronoun must still be recognised as empty."""
    # is_semantically_empty_term must normalize NFC; decomposed IME/pasted accents differ by
    # code points from composed table entries, silently missing lookup and hard-blocking every
    # draft for that Spanish pronoun, repeating the same P1 under another trigger.
    import unicodedata

    decomposed = unicodedata.normalize("NFD", "él")
    assert decomposed != "él", "前提：两种 Unicode 形式确实不同"
    _install_directives(monkeypatch, [decomposed])

    assert _proactive_directive_hits("Neko", "él dijo algo interesante") == []


def test_hits_degrade_but_still_block_when_script_fold_is_unavailable(
    monkeypatch,
):
    """``Never raises`` must hold in the very case the lazy import cites."""
    # Importing memory.script_fold claimed to tolerate narrow entrypoints where memory modules
    # might not load, yet once sat outside try, violating its own stated scenario.
    # Pin fallback direction too: keep matching without folding, never disable the gate;
    # identical-script ``加班`` must still block.
    _install_directives(monkeypatch, ["加班"])
    import builtins

    real_import = builtins.__import__

    def _boom_on_script_fold(name, *args, **kwargs):
        if name == "memory.script_fold":
            raise ImportError("simulated narrow entrypoint")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom_on_script_fold)

    assert _proactive_directive_hits("Neko", "今天又加班到很晚吧？") == ["加班"]


def test_bare_referent_does_not_mask_a_real_term_in_the_same_list(monkeypatch):
    """Skipping a bare referent must not skip the specific terms beside it."""
    # Control: lists contain both ``这个`` and ``加班``; skip the former and block the latter.
    # Fails if any demonstrative incorrectly bypasses the entire directive list.
    _install_directives(monkeypatch, ["这个", "加班"])
    assert _proactive_directive_hits("Neko", "这个周末还要加班吗？") == ["加班"]


def test_bare_referent_still_reaches_the_soft_prompt_block(monkeypatch):
    """The soft block keeps every term — only the hard gate is selective."""
    # Soft constraints retain ``这个`` because models have current-turn context to resolve
    # its referent; output substring gates do not. Different criteria are intentional.
    manager = MagicMock()
    manager.render_prompt_block.return_value = "\n\n[...]\n- 这个"
    monkeypatch.setattr(
        user_directives_module, "get_user_directives_manager", lambda: manager,
    )
    assert "这个" in _append_directives_section("ctx", "Neko", "zh")


@pytest.mark.parametrize("term,draft", [
    # Classic substrings that must not count as whole words.
    ("it", "my favorite part is waiting for you"),
    ("ex", "here is an example for the next round"),
    ("art", "let us start the party"),
    ("work", "the network is down"),
])
def test_latin_terms_match_on_word_boundaries(monkeypatch, term, draft):
    """A Latin term must not match inside a longer word."""
    # Latin scripts use word boundaries; bare substring bans of it match
    # favorite/waiting and silence all proactive drafts, observed in all three adversarial samples.
    # Unspaced CJK lacks those boundaries and requires substrings; separate both criteria.
    _install_directives(monkeypatch, [term])
    assert _proactive_directive_hits("Neko", draft) == []


@pytest.mark.parametrize("term,draft", [
    ("ex", "my ex called me"),
    ("art", "the art class was fun"),
    ("work", "how was work today"),
    ("my ex", "I saw my ex yesterday"),
])
def test_latin_terms_still_match_as_whole_words(monkeypatch, term, draft):
    """Control: the same terms must still hit when they stand alone."""
    # Without this, disabling all Latin-term blocking also passes above.
    # Do not use it here: the semantically empty table independently exempts it, correctly,
    # but that conflates two criteria in a word-boundary control, letting deletion of
    # boundary logic escape detection.
    _install_directives(monkeypatch, [term])
    assert _proactive_directive_hits("Neko", draft) == [term]


@pytest.mark.parametrize("term,draft", [
    ("遊戲", "今天要不要一起玩游戏？"),   # Traditional term / simplified draft.
    ("游戏", "今天要不要一起玩遊戲？"),   # Simplified term / traditional draft.
    ("加班", "今天又加班到很晚吧？"),      # Identical-script control.
])
def test_hard_gate_folds_script(monkeypatch, term, draft):
    """Traditional and Simplified must reach each other at the gate."""
    # User IMEs may save traditional ``遊戲`` while locale produces simplified
    # ``游戏``. Unfolded code points differ, missing explicitly forbidden topics.
    # memory.script_fold exists for this, matching #2584's recall issue.
    _install_directives(monkeypatch, [term])
    assert _proactive_directive_hits("Neko", draft) == [term]


@pytest.mark.parametrize("term_form,draft_form", [
    ("NFD", "NFC"),
    ("NFC", "NFD"),
    ("NFD", "NFD"),
])
def test_accented_terms_match_across_unicode_forms(monkeypatch, term_form, draft_form):
    """Composed and decomposed accents must compare equal at the gate."""
    # Spanish/Portuguese accents have equivalent composed é versus e+combining-mark encodings.
    # Without normalization, byte-different terms silently never match, allowing
    # explicit banned topics without any failure signal (codex).
    import unicodedata as ud
    base = "fútbol"
    term = ud.normalize(term_form, base)
    draft = ud.normalize(draft_form, f"vamos a ver el {base} hoy")
    _install_directives(monkeypatch, [term])
    assert _proactive_directive_hits("Neko", draft) == [term]


def test_matcher_empty_draft_short_circuits(monkeypatch):
    manager = _install_directives(monkeypatch, ["加班"])
    assert _proactive_directive_hits("Neko", "   ") == []
    # Empty drafts should not even read disk.
    manager.get_active_terms.assert_not_called()


# ── soft injection into the Phase 2 prompt ───────────────────────────


def test_directive_block_appended_to_phase2_memory_context(monkeypatch):
    manager = MagicMock()
    manager.render_prompt_block.return_value = "\n\n[用户最近明确表示过...]\n- 加班"
    monkeypatch.setattr(
        user_directives_module, "get_user_directives_manager", lambda: manager,
    )
    out = _append_directives_section("原有记忆上下文", "Neko", "zh")
    assert out.startswith("原有记忆上下文")
    assert "加班" in out
    # Pass lanlan_name/lang unchanged; hardcoded default/zh silently reads another
    # character's or empty directives for multicharacter/non-Chinese users.
    manager.render_prompt_block.assert_called_once_with("Neko", "zh")


def test_directive_block_falls_back_to_default_bucket(monkeypatch):
    """An unnamed character reads "default" — matching the sink's bucket rule."""
    manager = MagicMock()
    manager.render_prompt_block.return_value = ""
    monkeypatch.setattr(
        user_directives_module, "get_user_directives_manager", lambda: manager,
    )
    _append_directives_section("ctx", "", "en")
    manager.render_prompt_block.assert_called_once_with("default", "en")


def test_no_directives_leaves_memory_context_byte_identical(monkeypatch):
    manager = MagicMock()
    manager.render_prompt_block.return_value = ""
    monkeypatch.setattr(
        user_directives_module, "get_user_directives_manager", lambda: manager,
    )
    assert _append_directives_section("原有记忆上下文", "Neko", "zh") == "原有记忆上下文"


def test_injection_failure_keeps_original_context(monkeypatch):
    def _boom():
        raise RuntimeError("disk gone")

    monkeypatch.setattr(
        user_directives_module, "get_user_directives_manager", _boom,
    )
    assert _append_directives_section("原有记忆上下文", "Neko", "zh") == "原有记忆上下文"


# Variables carrying ban terms or containing text along this PR's pipeline.
# Initially only the first three were guarded; even repaired draft-stdout sites remained
# unprotected, letting chars={len(full_text)} revert to full_text[:300] while tests stayed green.
# Incomplete guard coverage is guard failure, the third instance of that lesson in this PR.
_SENSITIVE_PRINT_NAMES = frozenset({
    # Prompt side containing injected directive blocks.
    "system_prompt", "prompt", "messages",
    "phase2_memory_context", "phase2_system_prompt",
    # Directives themselves.
    "block", "terms", "active_terms",
    "directive_hits", "regen_directive_hits",
    # Model drafts contain matched terms verbatim and print before gates.
    "full_text", "response_text", "cleaned", "draft",
})

# Persistent logger output needs stronger protection than stdout, as this PR's _report_if_kept/output-gate
# comments acknowledge: logs persist under logs/ and may be bundled into bug reports.
# The initial guard matched print only, while logger.info's AST is
# Call(func=Attribute(...)), entirely bypassing the branch and reversing the intended criterion.
_SINK_ATTRS = frozenset({
    "debug", "info", "warning", "error", "exception", "critical", "log",
})


def _exposed_names(node, inside_len=False):
    """Yield sensitive identifiers an expression would actually render.

    ``len(x)`` only reveals a size, so anything under it is exempt; everything
    else that reaches the output — a bare name, a subscript, an attribute, a
    method call on it — counts as exposing the body.
    """
    import ast

    if isinstance(node, ast.Call):
        is_len = isinstance(node.func, ast.Name) and node.func.id == "len"
        for child in ast.iter_child_nodes(node):
            yield from _exposed_names(child, inside_len or is_len)
        return
    if inside_len:
        return
    if isinstance(node, ast.Name):
        if node.id in _SENSITIVE_PRINT_NAMES:
            yield node.id
        return
    if isinstance(node, (ast.Subscript, ast.Attribute)):
        # messages[0]/system_prompt.upper() also expose bodies;
        # inspect the innermost base name.
        base = node
        while isinstance(base, (ast.Subscript, ast.Attribute)):
            base = base.value
        if isinstance(base, ast.Name) and base.id in _SENSITIVE_PRINT_NAMES:
            yield base.id
            return
    for child in ast.iter_child_nodes(node):
        yield from _exposed_names(child, inside_len)


def _is_output_sink(func) -> bool:
    """Whether a call target writes outside the process (stdout or a log file)."""
    import ast

    if isinstance(func, ast.Name) and func.id == "print":
        return True
    # logger.info(...) / self.logger.warning(...) / active_logger.debug(...)
    return isinstance(func, ast.Attribute) and func.attr in _SINK_ATTRS


def _print_calls_exposing(source: str):
    """Return ``[(lineno, name)]`` for every sink call that would render a body."""
    import ast

    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and _is_output_sink(node.func)):
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            for name in _exposed_names(arg):
                found.append((getattr(node, "lineno", "?"), name))
    return found


@pytest.mark.parametrize("snippet", [
    "print(system_prompt)",                      # Direct arguments.
    'print("debug", messages[0])',               # Subscripts.
    'print(f"{system_prompt.upper()}")',         # Attributes/method calls.
    'print(f"{system_prompt}")',                 # f-string interpolation.
    'print("head " + system_prompt)',            # Concatenation.
    'print("%s" % system_prompt)',               # Legacy formatting.
    'print("{}".format(system_prompt))',         # format
    'print("x", file=None, end=prompt)',         # Keyword arguments.
    # Protect logger more than stdout because persistence can escape through bug reports.
    'logger.info("draft=%s", full_text)',
    'logger.warning(f"terms={directive_hits}")',
    'active_logger.debug("%s", response_text)',
    'self.logger.error("block: " + block)',
])
def test_print_guard_catches_every_leak_shape(snippet):
    """⚠️ The guard itself must not be shape-blind — it is the only regression net."""
    # Initial f-string-only guards missed every form above except the fourth (coderabbit).
    # Incomplete coverage defeats guards whose sole purpose is preventing future regressions.
    assert _print_calls_exposing(snippet), f"漏网：{snippet}"


@pytest.mark.parametrize("snippet", [
    'print(f"prompt_chars={len(system_prompt)}")',   # Sizes only.
    'print(f"n={len(messages)}")',
    'print("model=", model_name)',                   # Unrelated variables.
    'print(f"{actual_model} | {use_vision}")',
    'logger.info("blocked (%d terms)", len(directive_hits))',   # Counts.
    'logger.debug("[UserDirectives] sink failed: %s", exc)',    # Exceptions only.
    'logger.info("injected (lifetime=%s)", lifetime)',
])
def test_print_guard_allows_safe_shapes(snippet):
    """Control: the guard must not fire on size-only or unrelated output."""
    # Overstrict guards encourage obscure evasions and also fail; pin that boundary here.
    assert not _print_calls_exposing(snippet), f"误报：{snippet}"


def test_no_module_prints_a_directive_bearing_prompt():
    """⚠️ The Phase 2 prompt carries ban terms — it must never be printed whole.

    This PR injects the directive block into the Phase 2 system prompt, so any
    pre-existing debug print of that prompt starts leaking the one class of text
    the user explicitly asked never to hear again (an ex, an illness, a name).
    Same criterion the output gate and ``_report_if_kept`` already follow; this
    one arrives *indirectly* via the prompt, which is why a first sweep that
    only grepped direct variables missed it.
    """
    import importlib
    import inspect

    # Scan every module receiving/injecting directive text, not just one.
    # Initial coverage scanned only generation.py, omitting service.py injection, notify.py midturn
    # injection, and user_directives.py persistence/warnings entirely.
    #
    # Deliberately exclude proactive_chat.break_reminders, whose line 332 prints system_prompt,
    # assembled only from character_prompt/env_notice templates without directive blocks;
    # this PR does not inject rest reminders, so it is not currently leakage.
    # #3013 R2 will connect directives there; add it to this table at that moment,
    # or the new connection silently leaks immediately.
    targets = [
        inspect.getmodule(_proactive_directive_hits),   # generation.py
        importlib.import_module("main_logic.proactive_chat.service"),
        importlib.import_module("main_logic.core.notify"),
        user_directives_module,
    ]
    offenders = []
    for mod in targets:
        for lineno, name in _print_calls_exposing(inspect.getsource(mod)):
            offenders.append((mod.__name__, lineno, name))
    assert not offenders, (
        "这些 print / logger 会把禁令原文或含它的正文写出进程："
        f"{offenders} —— 只输出计数 / 长度 / 角色名，别输出正文"
    )


def test_ban_gate_runs_before_any_repeat_detection():
    """⚠️ The ban gate must precede every repeat detector, on both draft paths.

    Repeat detectors persist a ``RepeatSignature`` (which carries the draft
    ``phrase`` verbatim) into ``anti_repeat_effects.json`` when they block. A
    draft that trips both a ban term and a repeat check would therefore have
    that fragment written to disk before the ban gate ever ran — the gate would
    stop delivery but not the leak, and disk outlives the session.
    """
    # Ordering is positional and invisible to behavior tests: moving gates after repetition checks
    # leaves drop/pass tests green because leakage precedes gate execution without changing behavior.
    # Pin AST occurrence order.
    import ast
    import inspect

    gen_module = inspect.getmodule(_proactive_directive_hits)
    tree = ast.parse(inspect.getsource(gen_module))

    # Compare lines only in _guard_phase2_output's subtree, not the whole module.
    # Module-wide lineno ordering fails both ways:
    #   False pass: move gates into earlier external helpers and assertions still pass.
    #   False failure: any earlier unrelated record_anti_repeat_decision is counted
    #   before the gate despite being outside this execution path.
    # Execution ordering follows line numbers only within the same function scope (coderabbit).
    guard_fn = next(
        (node for node in ast.walk(tree)
         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
         and node.name == "_guard_phase2_output"),
        None,
    )
    assert guard_fn is not None, (
        "找不到 _guard_phase2_output —— 函数被改名的话这条守卫会静默失去目标，"
        "必须在这里就断言失败"
    )

    # Inspect only _guard_phase2_output's own scope, stopping at nested callables.
    # ast.walk descends into record_regen_effect and mistakes its inner
    # record_anti_repeat_decision definition position for execution.
    # Today it happens to lie between gates; moving the nested definition
    # earlier immediately triggers false errors (coderabbit).
    # Execution follows call sites, whose nested-helper invocation is already collected
    # in the direct enclosing scope.
    def _direct_calls(fn_node):
        stack = list(ast.iter_child_nodes(fn_node))
        while stack:
            node = stack.pop()
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ):
                continue
            if isinstance(node, ast.Call):
                yield node
            stack.extend(ast.iter_child_nodes(node))

    gate_lines, initial_detectors, regen_detectors = [], [], []
    for node in _direct_calls(guard_fn):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        if name == "_proactive_directive_hits":
            gate_lines.append(node.lineno)
        elif name in {
            # Draft side: persisted RepeatSignature records and fragment-producing detectors.
            "record_anti_repeat_decision",
            "_find_similar_recent_proactive_chat",
        }:
            initial_detectors.append(node.lineno)
        elif name == "_score_regenerated_draft":
            # Regenerated-draft repetition-scoring entry.
            regen_detectors.append(node.lineno)
        elif name == "record_regen_effect":
            # Count only regenerated-fragment outcomes whose names contain after_regen.
            # abandoned_user_interaction/regen_failed are aborted regeneration records;
            # revised drafts do not exist or were discarded, with no fragment persistence.
            # They necessarily precede bans, which must inspect cleaned only after regeneration.
            first = node.args[0] if node.args else None
            outcome = getattr(first, "value", None)
            if isinstance(outcome, str) and "after_regen" in outcome:
                regen_detectors.append(node.lineno)

    assert len(gate_lines) == 2, (
        f"预期两道闸（出稿 + regen），实际 {len(gate_lines)} 处：{gate_lines}"
    )
    assert initial_detectors and regen_detectors, (
        "没扫到复读检测/记录点，守卫失去意义"
    )
    first_gate, second_gate = sorted(gate_lines)

    # Evaluate each side independently rather than banning every detector before regenerated-output gates;
    # ordinary-draft repetition checks correctly follow draft bans but precede regenerated bans.
    before_first = [ln for ln in initial_detectors if ln < first_gate]
    assert not before_first, (
        f"出稿侧 ban 闸（行 {first_gate}）之前就有复读检测/记录：{before_first} —— "
        "含 ban term 的片段会先被写进 anti_repeat_effects.json"
    )
    regen_before_gate = [ln for ln in regen_detectors if ln < second_gate]
    assert not regen_before_gate, (
        f"regen 侧 ban 闸（行 {second_gate}）之前有 regen 侧检测/记录："
        f"{regen_before_gate} —— 同一条判据，改写稿的片段同样会落盘"
    )


def test_service_actually_calls_the_injection_helper():
    """Static guard: a correct helper proves nothing about it being wired up."""
    # Injection lives in enormous handle_proactive_chat without cheap end-to-end driving;
    # deleting its call leaves helper tests green. Use AST to verify the service
    # actually calls and assigns results back to phase2_memory_context,
    # since discarding return values means no injection.
    import ast
    import inspect

    import main_logic.proactive_chat.service as service_module

    tree = ast.parse(inspect.getsource(service_module))
    assigned = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "_append_directives_section":
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "phase2_memory_context" in targets:
            assigned = True
            break
    assert assigned, (
        "service.py 必须把 _append_directives_section 的返回值赋回 "
        "phase2_memory_context —— 否则主动搭话的 prompt 里没有用户禁令"
    )


# ── output gate ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_banned_draft_is_dropped_without_any_llm_call(monkeypatch):
    """Core contract: a hit drops the message, and spends no LLM call."""
    # make_llm_calls==0 is the central assertion: regenerating makes every match a real
    # expense after the model already saw and ignored directives,
    # making another gamble on compliance poor value.
    _install_directives(monkeypatch, ["加班"])
    _install_quiet_corpus(monkeypatch)
    make_llm_calls = 0

    async def make_llm(**_kwargs):
        nonlocal make_llm_calls
        make_llm_calls += 1
        raise AssertionError("ban gate must not trigger a regen LLM call")

    output, mgr = await _run_guard(
        lanlan_name="ban-gate-test",
        response_text="博士今天又加班到这么晚，要注意身体呀。",
        make_llm=make_llm,
    )

    assert output.result.body["reason_code"] == PROACTIVE_REASON_PASS_USER_DIRECTIVE
    # Return counts only, never raw banned terms, representing things users explicitly
    # rejected hearing such as ex-partners, illnesses, or deceased names. Bodies reach
    # frontend /api/proactive_chat, and logs follow the same rule.
    assert output.result.body["directive_match_count"] == 1
    assert "directive_terms" not in output.result.body
    assert make_llm_calls == 0
    # Drops finalize TTS/turn state, matching existing drop paths.
    mgr.handle_new_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_clean_draft_passes_the_gate(monkeypatch):
    """Control: directives active but the draft misses them -> passes through."""
    # Without this, unconditional dropping also passes above.
    _install_directives(monkeypatch, ["加班"])
    _install_quiet_corpus(monkeypatch)

    async def make_llm(**_kwargs):
        raise AssertionError("clean draft should not regen")

    output, _mgr = await _run_guard(
        lanlan_name="ban-gate-pass-test",
        response_text="博士今天的晚饭吃了什么呀？",
        make_llm=make_llm,
    )
    # None means this gate did not block; continue ordinary delivery.
    assert output.result is None
    assert output.response_text == "博士今天的晚饭吃了什么呀？"


@pytest.mark.asyncio
async def test_no_active_directives_leaves_gate_transparent(monkeypatch):
    """With no directives at all, this gate must be fully transparent."""
    _install_directives(monkeypatch, [])
    _install_quiet_corpus(monkeypatch)

    async def make_llm(**_kwargs):
        raise AssertionError("should not regen")

    output, _mgr = await _run_guard(
        lanlan_name="ban-gate-empty-test",
        response_text="博士今天又加班到这么晚，要注意身体呀。",
        make_llm=make_llm,
    )
    # Identical text drops with directives, as test_banned_draft_is_dropped_... shows,
    # but without directives passes unchanged.
    assert output.result is None


async def _run_material_exempt_guard(monkeypatch, response_text, terms):
    """Drive the guard with a parameter set that genuinely takes the exempt path."""
    # dedup_tag differs from source_tag: MUSIC plus selected_music_link=None
    # downgrades to CHAT and never enters exemption branches.
    # The initial test did exactly that, letting gate-after-exemption mutations
    # escape. Supply a real music link here.
    _install_directives(monkeypatch, terms)
    corpus = _install_quiet_corpus(monkeypatch)
    monkeypatch.setattr(
        "main_logic.proactive_chat.generation._is_recent_proactive_material",
        lambda *_a, **_kw: False,
    )

    async def make_llm(**_kwargs):
        raise AssertionError("should not regen")

    output, _mgr = await _run_guard(
        lanlan_name="ban-gate-music-test",
        response_text=response_text,
        make_llm=make_llm,
        source_tag="MUSIC",
        selected_music_link={"title": "某首歌", "url": "https://example.invalid/s"},
        music_content={"title": "某首歌"},
    )
    return output, corpus


@pytest.mark.asyncio
async def test_material_exempt_path_is_actually_taken(monkeypatch):
    """Premise guard: confirm that parameter set really does take the exempt path."""
    # Exemption skips all dialogue BM25, never calling score_draft. Without this premise,
    # the exemption-still-blocks test may exercise a never-exempt path vacuously.
    _output, corpus = await _run_material_exempt_guard(
        monkeypatch, "这首歌真好听，一起听听看？", ["前任"],
    )
    corpus.score_draft.assert_not_called()


@pytest.mark.asyncio
async def test_material_exempt_channel_is_still_gated(monkeypatch):
    """The MUSIC/MEME material exemption does NOT exempt user directives."""
    # exempt_text_dedup exempts fresh material from dialogue repetition thresholds,
    # preventing BM25 template-introduction false positives. User bans do not depend on freshness;
    # music introductions mentioning forbidden topics must still block. Moving gates after exemptions fails here.
    output, _corpus = await _run_material_exempt_guard(
        monkeypatch, "这首歌让我想起博士的前任了，听听看？", ["前任"],
    )
    assert (
        output.result.body["reason_code"] == PROACTIVE_REASON_PASS_USER_DIRECTIVE
    )


@pytest.mark.asyncio
async def test_regen_output_is_gated_too(monkeypatch):
    """A regen that introduces a banned topic is dropped too (dual of the first gate)."""
    # Initial drafts reaching regeneration were clean, but avoidance prompts handle BM25 terms,
    # not user bans, so rewriting can introduce banned topics.
    _install_directives(monkeypatch, ["前任"])
    corpus = _install_quiet_corpus(monkeypatch)
    # Raise the first draft above BM25 regeneration thresholds to genuinely enter that branch.
    corpus.score_draft.side_effect = [(99.0, {"屏幕": 99.0}), (0.0, {})]

    class _FakeLlm:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def ainvoke(self, _messages):
            return SimpleNamespace(content="说起来博士的前任最近怎么样了？")

    make_llm_calls = 0

    async def make_llm(**_kwargs):
        nonlocal make_llm_calls
        make_llm_calls += 1
        return _FakeLlm()

    output, mgr = await _run_guard(
        lanlan_name="ban-gate-regen-test",
        response_text="屏幕上这个按钮好好看啊。",
        make_llm=make_llm,
    )

    assert make_llm_calls == 1, "只应有 BM25 那一次既有 regen，本闸不额外加"
    assert output.result.body["reason_code"] == PROACTIVE_REASON_PASS_USER_DIRECTIVE
    assert output.result.body["directive_match_count"] == 1
    assert "directive_terms" not in output.result.body
    mgr.handle_new_message.assert_awaited_once()
