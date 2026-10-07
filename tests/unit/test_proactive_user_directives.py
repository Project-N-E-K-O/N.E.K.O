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
"""  # noqa: DOCSTRING_CJK  # 引的是用户实际会说的那句话，换成英文就不是那个 term 了

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
    # ⚠️ 抽取侧**会**存下这类 term（"别再讲这个了" → ``这个``），而且那个行为被
    # 既有测试成片钉着（繁简一致、复合词守卫的主用例都拿 ``这件事`` 当载体），
    # 不该由本 PR 顺手改掉。但拿汉语最高频的词做子串匹配，等于让主动搭话在这条
    # 指令的整个生命周期里（递增 TTL 后最长 30 天）全面静默，而用户今天没有界面
    # 能看到、更别说删掉它。所以判据落在消费侧：软约束照旧注入，硬拦截跳过。
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
    """  # noqa: DOCSTRING_CJK  # 引的是用户实际会说的那句话
    # ⚠️ 前提断言：这条测试的意义全在"抽取侧确实会产出这个 term"上。抽取行为
    # 哪天变了（正则收紧 / 长度门变化），没有这句的话测试会静默变成空转绿。
    hits = extract_directives(utterance)
    assert hits, f"{utterance!r} 不再被抽成 ban_topic，本测试的前提已失效"
    terms = [t for _, _, t in hits]
    _install_directives(monkeypatch, terms)

    assert _proactive_directive_hits("Neko", draft) == []


@pytest.mark.parametrize("term,blocked_draft,ignored_draft", [
    # 国名 vs 代词
    ("US", "The US economy is wild these days.", "Want us to pick a movie?"),
    # 电影《我们》/《她》
    ("Us", "Want to watch Us tonight?", "Want us to pick a movie?"),
    ("Her", "Her is still my favorite film.", "Did you ask her about it?"),
    # #3013 R4：IT 行业 vs 代词 it
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
    # 没有这条，把"含大写才豁免"写反（变成"只豁免大写"）也能让上面那组全绿。
    _install_directives(monkeypatch, [term])
    assert _proactive_directive_hits(
        "Neko", "Want us to pick a movie, or should I ask her about it?",
    ) == []


@pytest.mark.parametrize("term,exempt,case_sensitive", [
    # 撞表 + 全小写 → 豁免硬闸
    ("us", True, False),
    ("it", True, False),
    ("me", True, False),
    # 撞表 + 含大写 → 不豁免，且必须大小写敏感
    ("US", False, True),
    ("Us", False, True),
    ("IT", False, True),
    # 不撞表 → 两者都 False，走普通的大小写不敏感路径
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
    # 没有这条对照，上面那组只要"硬闸整个失效"就会全绿 —— 变异实测过这个方向。
    _install_directives(monkeypatch, ["加班"])
    assert _proactive_directive_hits("Neko", "我们今天还要加班吗？") == ["加班"]


def test_accented_pronoun_is_matched_regardless_of_unicode_form(monkeypatch):
    """A decomposed accented pronoun must still be recognised as empty."""
    # ⚠️ ``is_semantically_empty_term`` 走 NFC 归一，否则 IME / 粘贴来的分解形式
    # （e + 组合重音符）与表里的合成形式逐码位不等，查表静默落空，这个西语代词
    # 会退回去硬拦截每一条草稿 —— 正是上面那条 P1 的重演，只是换了触发条件。
    import unicodedata

    decomposed = unicodedata.normalize("NFD", "él")
    assert decomposed != "él", "前提：两种 Unicode 形式确实不同"
    _install_directives(monkeypatch, [decomposed])

    assert _proactive_directive_hits("Neko", "él dijo algo interesante") == []


def test_hits_degrade_but_still_block_when_script_fold_is_unavailable(
    monkeypatch,
):
    """``Never raises`` must hold in the very case the lazy import cites."""
    # ⚠️ ``from memory.script_fold import fold_script`` 的理由写的就是"memory 层
    # 在偏窄的 entrypoint 下未必加载"，而它一度落在 try 之外 —— 契约在它自己举的
    # 那个场景下不成立。降级方向也钉住：不折叠**继续匹配**，不是放弃整道闸，
    # 所以同字形的 ``加班`` 仍然拦得住。
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
    # 对照：整条 list 里既有 ``这个`` 又有 ``加班``，前者跳过、后者照拦。
    # 写成"list 里出现指代词就整条放行"的话这条会红。
    _install_directives(monkeypatch, ["这个", "加班"])
    assert _proactive_directive_hits("Neko", "这个周末还要加班吗？") == ["加班"]


def test_bare_referent_still_reaches_the_soft_prompt_block(monkeypatch):
    """The soft block keeps every term — only the hard gate is selective."""
    # 软约束里保留 ``这个`` 是有意义的：模型读 prompt 时手上有本轮上下文，
    # 能自己解析所指；而出口子串匹配没有那个上下文。两侧判据不同是设计。
    manager = MagicMock()
    manager.render_prompt_block.return_value = "\n\n[...]\n- 这个"
    monkeypatch.setattr(
        user_directives_module, "get_user_directives_manager", lambda: manager,
    )
    assert "这个" in _append_directives_section("ctx", "Neko", "zh")


@pytest.mark.parametrize("term,draft", [
    # 子串会命中、但作为独立词不该命中的经典例子
    ("it", "my favorite part is waiting for you"),
    ("ex", "here is an example for the next round"),
    ("art", "let us start the party"),
    ("work", "the network is down"),
])
def test_latin_terms_match_on_word_boundaries(monkeypatch, term, draft):
    """A Latin term must not match inside a longer word."""
    # ⚠️ 拉丁文字用空格分词，裸子串会撞进更长的词里：ban 掉 ``it`` 会让
    # ``favorite`` / ``waiting`` 全部命中，主动搭话直接全静默（对抗审查实测
    # 3/3 草稿被 drop）。CJK 连写没有词边界、只能子串，两边判据必须分开。
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
    # 没有这条，"拉丁 term 一律不拦"也能让上面那组通过。
    # ⚠️ 这里不放 ``it``：它同时也在纯指代词表里，两道判据都会跳过它 —— 那是
    # 对的（``it`` 本来就什么都不指），但拿它当"词边界生效"的对照组会把两条
    # 判据搅在一起，删掉词边界这段代码这条也不会红。
    _install_directives(monkeypatch, [term])
    assert _proactive_directive_hits("Neko", draft) == [term]


@pytest.mark.parametrize("term,draft", [
    ("遊戲", "今天要不要一起玩游戏？"),   # 繁体 term / 简体草稿
    ("游戏", "今天要不要一起玩遊戲？"),   # 简体 term / 繁体草稿
    ("加班", "今天又加班到很晚吧？"),      # 同形对照
])
def test_hard_gate_folds_script(monkeypatch, term, draft):
    """Traditional and Simplified must reach each other at the gate."""
    # 用户换个输入法说"别再提遊戲"，落盘 term 是繁体，而角色按 locale 输出
    # 简体"游戏"——不折的话逐字不等、直接漏杀，用户明确禁掉的话题照样被推到
    # 脸上。memory.script_fold 就是为这条造的（#2584 召回侧同款问题）。
    _install_directives(monkeypatch, [term])
    assert _proactive_directive_hits("Neko", draft) == [term]


@pytest.mark.parametrize("term_form,draft_form", [
    ("NFD", "NFC"),
    ("NFC", "NFD"),
    ("NFD", "NFD"),
])
def test_accented_terms_match_across_unicode_forms(monkeypatch, term_form, draft_form):
    """Composed and decomposed accents must compare equal at the gate."""
    # 西 / 葡的重音字母可以有两种等价编码：合成的 é，或 e + 组合重音符。两者逐
    # 字节不等，不归一的话一个重音 term 会**静默永不命中**——用户明确 ban 掉的
    # 话题照样被推送，而且没有任何征兆（codex）。
    import unicodedata as ud
    base = "fútbol"
    term = ud.normalize(term_form, base)
    draft = ud.normalize(draft_form, f"vamos a ver el {base} hoy")
    _install_directives(monkeypatch, [term])
    assert _proactive_directive_hits("Neko", draft) == [term]


def test_matcher_empty_draft_short_circuits(monkeypatch):
    manager = _install_directives(monkeypatch, ["加班"])
    assert _proactive_directive_hits("Neko", "   ") == []
    # 空稿子连读都不该读一次盘
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
    # lanlan_name / lang 必须**透传**：写死成 "default" 或 "zh" 的话，多角色
    # 与非中文用户会静默拿到别人的（或空的）禁令表。
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


# 本 PR 链路上会承载 ban term（或含 term 的整段文本）的变量名。
# ⚠️ 第一版只有前三个 —— 于是「把草稿正文打进 stdout」那两处即使修好了，守卫也
# **接不住**：下一个人把 chars={len(full_text)} 改回 {full_text[:300]} 照样全绿。
# 守卫覆盖不全等于守卫失效，这是本 PR 里同一个教训的第三次。
_SENSITIVE_PRINT_NAMES = frozenset({
    # prompt 侧（注入了禁令块）
    "system_prompt", "prompt", "messages",
    "phase2_memory_context", "phase2_system_prompt",
    # 禁令本身
    "block", "terms", "active_terms",
    "directive_hits", "regen_directive_hits",
    # 模型草稿（命中 ban term 时逐字含它，且打印发生在闸之前）
    "full_text", "response_text", "cleaned", "draft",
})

# ⚠️⚠️ 落盘的 logger **比 stdout 更该防** —— 本 PR 自己在 `_report_if_kept` 和
# 出口闸的注释里就是这么写的（日志进 logs/ 持久化，用户报 bug 时可能整包交出去）。
# 而守卫第一版只匹配 `print`：``logger.info(...)`` 的 AST 是
# ``Call(func=Attribute(...))``，永远进不了那个分支，判据整个是反的。
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
        # ``messages[0]`` / ``system_prompt.upper()`` 照样把正文渲染出去；
        # 剥到最里层的那个名字来判。
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
    "print(system_prompt)",                      # 裸传参
    'print("debug", messages[0])',               # 下标
    'print(f"{system_prompt.upper()}")',         # 属性 / 方法调用
    'print(f"{system_prompt}")',                 # f-string 插值
    'print("head " + system_prompt)',            # 拼接
    'print("%s" % system_prompt)',               # 旧式格式化
    'print("{}".format(system_prompt))',         # format
    'print("x", file=None, end=prompt)',         # 关键字参数
    # ⚠️ logger 一路比 stdout 更该防（落盘、可能随 bug 报告外流）
    'logger.info("draft=%s", full_text)',
    'logger.warning(f"terms={directive_hits}")',
    'active_logger.debug("%s", response_text)',
    'self.logger.error("block: " + block)',
])
def test_print_guard_catches_every_leak_shape(snippet):
    """⚠️ The guard itself must not be shape-blind — it is the only regression net."""
    # 第一版只认 f-string 插值一种写法，上面除第四条外全部能绕过去（coderabbit）。
    # 守卫覆盖不全等于守卫失效，而它的唯一存在意义就是防未来回归。
    assert _print_calls_exposing(snippet), f"漏网：{snippet}"


@pytest.mark.parametrize("snippet", [
    'print(f"prompt_chars={len(system_prompt)}")',   # 只输出规模
    'print(f"n={len(messages)}")',
    'print("model=", model_name)',                   # 无关变量
    'print(f"{actual_model} | {use_vision}")',
    'logger.info("blocked (%d terms)", len(directive_hits))',   # 计数
    'logger.debug("[UserDirectives] sink failed: %s", exc)',    # 只有异常
    'logger.info("injected (lifetime=%s)", lifetime)',
])
def test_print_guard_allows_safe_shapes(snippet):
    """Control: the guard must not fire on size-only or unrelated output."""
    # 过严的守卫会逼后来人绕开写晦涩代码，同样是一种失效 —— 这组钉住那一侧。
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

    # ⚠️ 扫**所有本 PR 往里注入过禁令、或经手禁令文本**的模块，不是只扫一个。
    # 第一版只扫 generation.py —— 而注入点在 service.py、中途注入在 notify.py、
    # 落盘与告警在 user_directives.py，三个模块一行没扫过。
    #
    # ⚠️ 刻意**不含** ``proactive_chat.break_reminders``：它的 :332 现在就有一句
    # 打印整段 system_prompt，但那个 prompt 由 character_prompt + env_notice 纯
    # 模板拼成、**不含禁令块**（本 PR 没往休息提醒注入），所以今天不是泄漏。
    # 给它接禁令是 #3013 R2 的事 —— **做那件事的时候必须把它加进下面这张表**，
    # 否则接上的当天就会静默泄漏。
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
    # 顺序是纯位置性质，行为测试抓不到：把闸挪回复读检测后面，所有 drop/pass
    # 用例照样全绿（泄漏发生在闸执行之前，闸自己的行为不变）。所以按 AST 的
    # 出现顺序钉死。
    import ast
    import inspect

    gen_module = inspect.getmodule(_proactive_directive_hits)
    tree = ast.parse(inspect.getsource(gen_module))

    # ⚠️ 只在 `_guard_phase2_output` 的**子树**里比行号，不是整个模块。
    # 拿模块级 walk 比 lineno 只保证"文件里的先后"，两个方向都会坏：
    #   · 假绿 —— 有人把某道闸挪到本函数之外、行号却更靠前的辅助函数里，断言照过；
    #   · 假红 —— 模块里新增任何 record_anti_repeat_decision，只要行号小于出稿闸
    #     就会被算成"闸之前的检测"，而它根本不在这条执行路径上。
    # 判据要的是**执行顺序**，那只在同一个函数作用域内才由行号决定（coderabbit）。
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

    # ⚠️ 只看 `_guard_phase2_output` **自己作用域**里的调用，遇到嵌套 callable
    # 就不再下降。`ast.walk` 会走进 `record_regen_effect` 的函数体，把体内那句
    # `record_anti_repeat_decision` 当成一个检测点 —— 但那是**定义位置**，不是
    # **调用位置**。今天它恰好落在两闸之间所以没出错，那是位置巧合：把这个嵌套
    # 函数的 def 挪到闸之前，守卫立刻误报（coderabbit）。
    # 判据要的是执行顺序，那由**调用点**决定；嵌套函数的调用点本身在直接作用域
    # 里，照常会被收集到。
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
            # 出稿侧：落盘 RepeatSignature 的记录点 + 产生片段的检测器
            "record_anti_repeat_decision",
            "_find_similar_recent_proactive_chat",
        }:
            initial_detectors.append(node.lineno)
        elif name == "_score_regenerated_draft":
            # 改写稿的复读评分入口
            regen_detectors.append(node.lineno)
        elif name == "record_regen_effect":
            # ⚠️ 只算**带改写稿片段**的那些（outcome 名含 after_regen）。
            # `abandoned_user_interaction` / `regen_failed` 是 regen 流程的
            # 中止记录，发生时改写稿还不存在或已废弃，不涉及片段落盘；而且
            # 它们必然排在 ban 闸之前 —— 闸要检查 `cleaned`，那要等 regen 跑完。
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

    # ⚠️ 判据按**两侧各自**算，不能拿"regen 闸之前不许有任何检测点"一刀切：
    # 出稿闸放行之后本来就要跑出稿侧的复读检测，那些位置在 regen 闸之前是对的。
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
    # 注入点在 ``handle_proactive_chat`` 这个巨型函数里，没有便宜的端到端驱动
    # 方式；把调用点删掉的话，上面那几条 helper 单测**照样全绿**。这条按 AST
    # 确认 service 里确实存在这次调用，并且结果被赋回 phase2_memory_context
    # （只调用不接收返回值等于没注入）。
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
    # ⚠️ ``make_llm_calls == 0`` 是这条测试的重点，不是附带断言。改成 regen
    # 会让每一次命中都变成一次真实账单支出，而命中的前提已经是"模型看过禁令
    # 还是提了"——再赌一次它改口不划算。
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
    # ⚠️ body 里只回**条数**，不回 term 原文：被 ban 的话题按定义就是用户
    # 明说不想再听的东西（前任 / 病名 / 逝者姓名），而这个 body 会经
    # /api/proactive_chat 出到前端。日志侧同理。
    assert output.result.body["directive_match_count"] == 1
    assert "directive_terms" not in output.result.body
    assert make_llm_calls == 0
    # drop 时要把 TTS / 轮次收尾掉，与既有 drop 路径同构
    mgr.handle_new_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_clean_draft_passes_the_gate(monkeypatch):
    """Control: directives active but the draft misses them -> passes through."""
    # 少了这条，"永远 drop" 也能让上面那条通过。
    _install_directives(monkeypatch, ["加班"])
    _install_quiet_corpus(monkeypatch)

    async def make_llm(**_kwargs):
        raise AssertionError("clean draft should not regen")

    output, _mgr = await _run_guard(
        lanlan_name="ban-gate-pass-test",
        response_text="博士今天的晚饭吃了什么呀？",
        make_llm=make_llm,
    )
    # result is None == "这道闸没拦，继续正常投递流程"
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
    # 同一段在有禁令时会被 drop（见 test_banned_draft_is_dropped_...），
    # 没禁令时必须原样放行。
    assert output.result is None


async def _run_material_exempt_guard(monkeypatch, response_text, terms):
    """Drive the guard with a parameter set that genuinely takes the exempt path."""
    # ⚠️ ``dedup_tag`` 不等于 ``source_tag``：``source_tag='MUSIC'`` 但
    # ``selected_music_link is None`` 会被降级成 ``'CHAT'``，于是根本进不了
    # 豁免分支。第一版这条测试就是这么写的，"把闸挪到豁免之后"的变异从它下面
    # 整个溜了过去。所以这里必须给一条真的 music link。
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
    # 豁免生效时台词侧 BM25 被整段跳过（``score_draft`` 不会被调用）。没有这条
    # 前提断言，下面那条"豁免也拦"就可能在一个从未豁免的路径上空转。
    _output, corpus = await _run_material_exempt_guard(
        monkeypatch, "这首歌真好听，一起听听看？", ["前任"],
    )
    corpus.score_draft.assert_not_called()


@pytest.mark.asyncio
async def test_material_exempt_channel_is_still_gated(monkeypatch):
    """The MUSIC/MEME material exemption does NOT exempt user directives."""
    # ``exempt_text_dedup`` 的判据是"素材新鲜就别拿台词复读度卡它"，针对的是
    # BM25 对模板化开场白的误杀；而用户 ban 的话题跟素材新不新鲜无关——推歌
    # 台词里提到用户说过别提的事，照样该拦。把这道闸挪到豁免分支之后，这条会红。
    output, _corpus = await _run_material_exempt_guard(
        monkeypatch, "这首歌让我想起博士的前任了，听听看？", ["前任"],
    )
    assert (
        output.result.body["reason_code"] == PROACTIVE_REASON_PASS_USER_DIRECTIVE
    )


@pytest.mark.asyncio
async def test_regen_output_is_gated_too(monkeypatch):
    """A regen that introduces a banned topic is dropped too (dual of the first gate)."""
    # 走到 regen 的草稿本来是干净的，但 avoidance prompt 只针对 BM25 词、不认
    # 用户禁令，重写完全可能把禁题带进来。
    _install_directives(monkeypatch, ["前任"])
    corpus = _install_quiet_corpus(monkeypatch)
    # 让首稿越过 BM25 regen 阈值，从而真的走进 regen 分支
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
