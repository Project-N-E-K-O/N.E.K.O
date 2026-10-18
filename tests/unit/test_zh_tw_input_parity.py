"""Traditional/Simplified parity for the user-input matchers (issue #2500).

Second batch of the "0 hit" class: tables and regexes that get matched against
what the user actually typed. Simplified and Traditional are distinct code
points, so a Simplified-only lexicon does not degrade for a Traditional writer —
the feature simply does not exist for them.

As in ``test_zh_tw_guard_parity``, the assertions are **parity** rather than
per-case expected values: none of these matchers is supposed to care about
orthography, so parity holds by construction while a hand-written expectation
would drift as the lexicons grow.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# brain/openclaw_adapter.py — zero-LLM magic-command classifier
# ---------------------------------------------------------------------------

MAGIC_PAIRS = [
    # ⚠️ /clear trigger phrases are absent here: they irreversibly clear upstream conversation context and still use free-text
    # substring matching, so deliberately remain simplified Chinese. See test_clear_triggers_stay_simplified_only.
    # Taiwan uses 「搜尋」, so this is not a character-form conversion of 「搜索」.
    ("停止搜索", "停止搜尋"),
    ("取消这个任务", "取消這個任務"),
    ("停下来", "停下來"),
    ("没问题", "沒問題"),
    # approve's substring branch now uses a whole-clause whitelist, with traditional forms added; whole-clause matching no longer
    # broadens exposure when adding traditional forms. See test_whole_clause_whitelist_kills_free_text_misfires.
    ("去执行", "去執行"),
    ("去执行吧", "去執行吧"),
    ("删吧", "刪吧"),
    ("准了", "準了"),
]


@pytest.mark.parametrize(("simplified", "traditional"), MAGIC_PAIRS)
def test_magic_commands_resolve_the_same_in_both_scripts(simplified, traditional):
    from brain.openclaw_adapter import OpenClawAdapter

    resolved = OpenClawAdapter.rule_magic_command(simplified)
    assert resolved is not None, f"{simplified}: 简体侧本身就没命中，用例前提不成立"
    assert OpenClawAdapter.rule_magic_command(traditional) == resolved


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("我忘了带钥匙", "我忘記帶鑰匙"),
        ("雨停了", "雨停了"),
        ("停电了", "停電了"),
        ("想听听你的看法", "想聽聽你的看法"),
    ],
)
def test_high_precision_negatives_still_suppress_in_both_scripts(simplified, traditional):
    """The conservative negative list has to move with the trigger list, or the
    Traditional side loses its suppression while gaining the triggers."""
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(simplified) is None
    assert OpenClawAdapter.rule_magic_command(traditional) is None


@pytest.mark.parametrize(
    "text",
    [
        # /clear — irreversibly wipes the upstream QwenPaw session context
        "忘了剛才的事", "清空聊天記錄", "清除聊天記錄", "刪掉剛才的記錄",
        "我想知道如何清除聊天記錄",
    ],
)
def test_clear_triggers_stay_simplified_only(text):
    """⚠️ Deliberate gap: ``/clear``'s triggers are NOT backfilled to Traditional.

    ``/clear`` is the one command still judged by *substring containment over
    free text*, and it irreversibly wipes the upstream session context. A plain
    question — 「我想知道如何清除聊天记录」 — already returns ``/clear`` on the
    Simplified side; adding Traditional triggers would double the exposure of
    that pre-existing hole.

    ``/daemon approve``, ``/stop`` and ``/new`` are no longer in this list: they
    moved to the whole-clause whitelist, where a Traditional entry cannot fire
    from inside an unrelated sentence, so backfilling them is safe. ``/clear``
    can follow the same route, but that was scoped out of this change
    deliberately rather than smuggled in.

    Traditional users can still reach it by typing the literal magic word
    ``/clear`` (whole-string match in ``normalize_magic_command``).

    ⚠️ Do NOT lean on \"the LLM classifier still runs after a None\" — that is not
    unconditional. See test_a_rule_miss_can_skip_the_llm_classifier_entirely.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


def test_a_rule_miss_can_skip_the_llm_classifier_entirely():
    """⚠️ Pins that the rule table is NOT merely a zero-LLM fast path.

    ``rule_magic_command`` is also the only OpenClaw signal in
    ``_deterministic_action_signal``, and the cheap pre-gate in
    ``_analyze_and_execute_inner`` returns None on
    ``external_intent < threshold and not _deterministic_action_signal(...)`` —
    which sits BEFORE ``classify_magic_intent``. So on a low-external-intent turn
    a rule miss means the LLM classifier is never reached, and narrowing the
    table costs real recall rather than one extra assessment.

    Asserted structurally (the gate ordering in the source) plus behaviourally
    (the signal really does flip with the table).
    """  # noqa: DOCSTRING_CJK
    import inspect

    from brain.openclaw_adapter import OpenClawAdapter
    from brain.task_executor import DirectTaskExecutor

    source = inspect.getsource(DirectTaskExecutor._analyze_and_execute_inner)
    gate_at = source.index("_deterministic_action_signal")
    llm_at = source.index("classify_magic_intent")
    assert gate_at < llm_at, "前置闸不再位于 LLM magic 分类器之前，这条测试的前提变了"

    executor = object.__new__(DirectTaskExecutor)
    executor.plugin_list = []
    signal = executor._deterministic_action_signal
    # In the table → brake exemption; outside it → no exemption (the whole turn is skipped when external_intent is low)
    assert signal("停下来", openclaw_enabled=True, user_plugin_enabled=False) is True
    assert OpenClawAdapter.rule_magic_command("我准了假") is None
    assert signal("我准了假", openclaw_enabled=True, user_plugin_enabled=False) is False


@pytest.mark.parametrize(
    "text",
    [
        # A question *about* restarting is not a request to restart. Both scripts.
        "這個遊戲要怎麼重新開始？", "这个游戏要怎么重新开始？",
    ],
)
def test_question_about_a_command_no_longer_triggers_it(text):
    """Was recorded-not-fixed while the judgement was substring containment;
    the whole-clause whitelist fixes it in both scripts at once.

    The clause is 這個遊戲要怎麼重新開始 — not a whitelist entry — so it no
    longer dispatches. This IS a Simplified behaviour change, and a deliberate
    one: the prior test docstring called it out as \"narrowing it is a separate,
    script-neutral change\", which is exactly what this is.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


def test_traditional_can_still_approve_by_whole_sentence():
    """Bare affirmations stay a valid approval in both scripts.

    ⚠️ These four are still an unconditional approve at the classifier layer and
    a whole-clause whitelist cannot narrow them — they ARE whole clauses. What
    stops a stray 「没问题」 from approving something is the dispatch-side live
    task gate; see test_approve_is_dropped_without_a_live_openclaw_task.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    for text in ("沒問題", "同意", "我同意", "没问题"):
        assert OpenClawAdapter.rule_magic_command(text) == "/daemon approve", text


def test_legitimate_approvals_survive_the_whole_clause_switch():
    """Every approval spelling that worked under substring containment, plus the
    Traditional counterparts the substring table never had."""
    from brain.openclaw_adapter import OpenClawAdapter

    approve = "/daemon approve"
    for text in (
        # were already approving on the Simplified side
        "去执行吧", "删吧", "准了", "没问题，去执行", "没问题去执行",
        # Traditional, newly reachable
        "去執行吧", "刪吧", "準了", "沒問題，去執行", "沒問題去執行",
        # leading function words are stripped, so these still land
        "那你去执行吧", "先去執行吧", "我同意，去执行",
    ):
        assert OpenClawAdapter.rule_magic_command(text) == approve, text


@pytest.mark.parametrize(
    "text",
    [
        # 「准了」inside an unrelated word or sentence
        "我准了假下周去旅游", "领导批准了我的申请", "这标准了不起", "他的水准了得",
        # 「删吧」inside an unrelated word
        "删吧台的记录", "那个删吧的老哥",
        # 「去执行」reported, questioned, or explicitly refused
        "他说去执行了", "可以去执行吗", "拒绝去执行这种命令", "禁止去执行危险操作",
        "军人必须去执行命令", "这个方案没人去执行", "这标准了不起，但不要去执行",
        # the shapes that broke the negator-blacklist attempt
        "去執行？我不要", "要去執行嗎？", "他說去執行", "別去執行", "拒絕去執行",
        # /stop — a world event stopping, not a command
        "雨停下来了", "雨停下來了", "電梯停下來了", "公交车停下来了我要上车了",
        "他跑着跑着突然停下来", "我想让时间停下来", "音乐停下来之后房间好安静",
        "心跳停下來那一刻", "钥匙别找了我已经拿到了", "新闻说救援队停止搜索了",
        "他喊快停下来的时候已经晚了",
        # /new — a game/match/life restarting, or a comment about the phrase
        "比賽即將重新開始", "比赛即将重新开始", "遊戲重新開始倒數",
        "我想重新开始新的人生", "这局输了要重新开始吗", "下半場重新開始了",
        "他老是换个话题就想蒙混过去", "我不喜歡別人換個話題的樣子",
        "他除了工作说点别的都不会",
    ],
)
def test_whole_clause_whitelist_kills_free_text_misfires(text):
    """⚠️ The core regression set for this change.

    Every one of these dispatched a magic command before the switch — 22/22 for
    /stop, 16/16 for /new and 17/28 for /daemon approve on the adversarial set.
    They are ordinary sentences a user really types; the trigger word just
    happens to appear inside one.

    A \"reject when a negator precedes the trigger\" guard was tried first and an
    adversarial pass broke it on 196 inputs: negation to the *right* of the
    trigger (「去執行？我不要」), the anchor landing on an unrelated substring
    (「这标准了不起，但不要去执行」 anchors on 「准了」), and questions
    (「要去執行嗎？」) all sailed through — while it *also* rejected
    「没错，去执行」, i.e. the affirmations an approval context is literally
    built out of negation words. A blacklist cannot work here.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


def test_approve_whitelist_content_is_pinned():
    """⚠️ Equality, not containment, and the two tables must stay separate.

    These are the entire judgement for ``/daemon approve``: every entry is a
    phrase that, said alone, dispatches a real high-risk action upstream.
    Widening is a security decision, so adding a word must turn a test red.

    ⚠️ No bare single characters. An earlier revision derived the tables by
    closing them under the clause normalizer, which put 删 / 刪 / 准 / 準 in —
    and then 帮我删一下 (a fresh delete request) dispatched an approval. The
    tables are literal now; the *lookup* widens, not the table.

    Broad affirmations (可以 / 好 / 好的 / 行) are deliberately absent.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import (
        _APPROVE_ACTIONS,
        _APPROVE_AFFIRMATIONS,
        _APPROVE_COMPANIONS,
    )

    assert _APPROVE_AFFIRMATIONS == frozenset({"同意", "我同意", "没问题", "沒問題"})
    assert _APPROVE_ACTIONS == frozenset({
        "删吧", "刪吧", "准了", "準了",
        "去执行", "去执行吧", "去執行", "去執行吧",
        "没问题去执行", "沒問題去執行",
    })
    # ⚠️ Pin the third table too. It never authorizes alone, but determines whether the entire "acknowledgment + action"
    # pattern is recognized—adding words likewise broadens approve matching, and the reviewer must receive an explanation in the same commit.
    # When it was omitted, missing `對` survived an entire PR.
    assert _APPROVE_COMPANIONS == frozenset({
        "好", "好的", "好吧", "行", "行了", "可以", "嗯",
        "对", "對", "没错", "沒錯", "没意见", "沒意見",
        "批准", "允许", "允許",
    })
    single_chars = sorted(
        w for w in (_APPROVE_ACTIONS | _APPROVE_AFFIRMATIONS) if len(w) < 2
    )
    assert not single_chars, f"单字条目会让任意祈使句落到批准上：{single_chars}"
    # Single characters (好 / 行 / 对 / 嗯) in the acknowledgment table are intentional—they always yield None alone,
    # and can appear only alongside an action clause. This assertion pins "single characters only in the acknowledgment table".
    from brain.openclaw_adapter import OpenClawAdapter

    assert all(
        OpenClawAdapter.rule_magic_command(word) is None
        for word in sorted(_APPROVE_COMPANIONS)
    ), "应答词单独成句必须是 None"


@pytest.mark.parametrize(
    "text",
    [
        # Bare acknowledgments match only the exact full clause: even trimming either end is forbidden, or the catgirl's own stock phrase
        # 「没问题喵~」during proactive conversation would approve itself. These all yielded None before the refactor and must remain so.
        "没问题喵~", "没问题喵！", "沒問題喔", "同意~", "我同意喵", "没问题啦",
        "沒問題囉", "同意啦", "不如同意", "那就同意", "马上同意", "同意了",
        # Single-character derivation once turned these into approvals—they are **new deletion requests**, not approvals
        "帮我删了", "帮我删一下", "删一下", "删啦", "删", "准", "刪", "準",
        "幫我刪了", "请删了", "那就删了吧", "删了吧", "快删了", "准一下", "删喵",
    ],
)
def test_approve_never_widens_beyond_the_pre_change_behaviour(text):
    """⚠️ A hardening change that broadens high-risk command matching defeats its purpose.

    Every input here returned None before the change. The clause normalizer made
    them approvals in an intermediate revision — via the table closure (删吧 -> 删)
    and via tail stripping on the bare affirmations (没问题喵~ -> 没问题).
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


# **All** simplified/traditional variant characters appearing in these three whitelists, as a closed set. opencc is not a repository dependency (only
# temporarily installed using `uv run --with` in scripts/gen_activity_fold_map.py), so importorskip
# would always skip this guard in CI—effectively no test. Use a self-contained closed set: if a new form is missing from this table,
# bidirectional folding below cannot produce its counterpart and the assertion directly identifies the missing entry.
_T2S = {
    "沒": "没", "問": "问", "題": "题", "刪": "删", "準": "准", "執": "执",
    "別": "别", "來": "来", "這": "这", "個": "个", "務": "务", "尋": "寻",
    "說": "说", "開": "开", "話": "话", "點": "点", "換": "换",
    "對": "对", "錯": "错", "見": "见", "許": "许",
}
# Characters identical in simplified and traditional whitelist entries, listed separately. Like _FUNCTION_NEUTRAL_CHARS, this **detects unlisted forms**.
# When the fold table cannot produce a counterpart, _fold returns the entry itself, which is naturally present in the table—so a one-sided entry
# using an unlisted character silently passes. `對` escaped this way: absent from _T2S, `对` could not fold into `對`,
# and the guard failed to detect that _APPROVE_COMPANIONS lacked the traditional side.
_CLAUSE_NEUTRAL_CHARS = set(
    "下了以任停允去取可同吧嗯好始快意我批找搜新查止消的算索聊行重"
)
_S2T = {simplified: traditional for traditional, simplified in _T2S.items()}
# ⚠️ Taiwan uses 「搜尋」 rather than 「搜索」—a **vocabulary** difference, not a character-form conversion, so folding cannot derive it.
# Exempt only these two pairs separately; do not use the exemption set as a dumping ground. Explain why each addition is not a character-form pair.
_LEXICAL_NOT_A_FOLD = frozenset({
    "停止搜索", "停止搜尋", "取消这个搜索", "取消這個搜尋",
    # ⚠️ 准 maps from **one simplified to multiple traditional forms**: the permission sense remains 「准」 (批准 / 准許 / 不准),
    # while 「準」 means accuracy. Thus `批准` is identical on both sides; mechanically folded `批準` is not a word and must not enter
    # the whitelist just to manufacture symmetry—that would add an unnecessary approve entry.
    # (The table containing both `准了`/`準了` is different: it also recognizes a possible user typo,
    #   broadening recall rather than satisfying a symmetry requirement.)
    "批准",
})


def test_clause_whitelists_are_script_symmetric():
    """Auto-discovered, not a checklist: fold every entry BOTH ways and require
    the counterpart to be in the same table.

    A missing counterpart means the command silently does not exist for users of
    one script — the exact failure #2500 is about. Folding both directions
    catches it whichever side was forgotten; a pairwise list only catches the
    pairs somebody remembered to write down.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import (
        _APPROVE_ACTIONS,
        _APPROVE_AFFIRMATIONS,
        _APPROVE_COMPANIONS,
        _STOP_CLAUSES,
    )

    def _fold(text, table):
        return "".join(table.get(char, char) for char in text)

    tables = (
        # ⚠️ approve uses **three** tables. Omitting one does not fail any assertion, yet still broadens approve matching
        # —`對` slipped through an entire round this way: no guard covered companions.
        ("approve_actions", _APPROVE_ACTIONS),
        ("approve_affirmations", _APPROVE_AFFIRMATIONS),
        ("approve_companions", _APPROVE_COMPANIONS),
        ("stop", _STOP_CLAUSES),
    )

    # ⚠️ Unlisted character forms must fail, or this guard does nothing for them: when _fold cannot derive a counterpart, it returns the entry
    # itself, which is naturally in the table → silent pass. Fixing companions alone is insufficient: `對` was also absent from
    # _T2S then, and the two gaps together let it survive.
    unknown = {
        char
        for _, entries in tables
        for entry in entries
        for char in entry
        if "㐀" <= char <= "鿿"
        and char not in _T2S
        and char not in _S2T
        and char not in _CLAUSE_NEUTRAL_CHARS
    }
    assert not unknown, (
        f"这些字形不在折叠表也不在中性清单里，简繁对称无法验证 → {sorted(unknown)}"
    )

    for name, entries in tables:
        missing = []
        for entry in sorted(entries):
            if entry in _LEXICAL_NOT_A_FOLD:
                continue
            for direction, table in (("t2s", _T2S), ("s2t", _S2T)):
                counterpart = _fold(entry, table)
                if counterpart not in entries:
                    missing.append(f"{entry} --{direction}--> {counterpart}")
        assert not missing, f"{name}: 对侧字形缺失 → {missing}"


def test_approve_requires_every_clause_but_stop_and_new_only_the_last():
    """⚠️ The asymmetry is load-bearing, not an oversight.

    ``/daemon approve`` runs a high-risk action upstream, so it is fail-closed:
    ANY clause outside the whitelist kills it, which is what stops
    「我不同意，去执行」 from approving. ``/stop`` and ``/new`` only halt a task
    or change the subject, so they read the trailing imperative — otherwise
    「我还没同意，停止搜索」 would stop dispatching at all.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    # approve: fail-closed on a non-whitelisted clause anywhere
    assert OpenClawAdapter.rule_magic_command("我不同意，去执行") is None
    assert OpenClawAdapter.rule_magic_command("我不同意，去執行") is None
    assert OpenClawAdapter.rule_magic_command("同意，去执行") == "/daemon approve"
    # stop / new: the TRAILING clause decides — a whitelist phrase sitting in a
    # non-final clause is narration, not an imperative, and must not dispatch.
    # ⚠️ These four cases alone distinguish "last clause" from "any clause": only they fail if changed to any(...).
    assert OpenClawAdapter.rule_magic_command("我还没同意，停止搜索") == "/stop"
    assert OpenClawAdapter.rule_magic_command("我不同意这个方案，取消这个任务") == "/stop"
    assert OpenClawAdapter.rule_magic_command("停下来，这是我当时唯一的念头") is None
    assert OpenClawAdapter.rule_magic_command("停下來，這是我當時唯一的念頭") is None
    # ⚠️ `/new` was removed from the free-text path, so it has no counterpart here; the last-clause rule remains pinned by the two
    # /stop cases above (changing to any(...) fails 「停下来，这是我当时唯一的念头」).
    assert OpenClawAdapter.rule_magic_command("換個話題，他總是這麼逃避") is None
    assert OpenClawAdapter.rule_magic_command("别找了，他说，然后转身走了") is None
    assert OpenClawAdapter.rule_magic_command("重新开始，说起来简单做起来难") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # leading function words stripped
        ("请停下来", "/stop"), ("請停下來", "/stop"),
        ("帮我停下来", "/stop"), ("幫我停下來", "/stop"),
        ("那你去执行吧", "/daemon approve"),
        # ⚠️ Plural/honorific second-person forms must appear as **whole words** before single-character `你`, or `你们停下来`
        # loses its first character to `你`, leaving `们停下来`. This is the same pitfall as `那么`/`快点`.
        ("你们停下来", "/stop"), ("你們停下來", "/stop"),
        ("您去执行吧", "/daemon approve"), ("你们去执行吧", "/daemon approve"),
        # ⚠️ Strip multi-character prefixes as whole words. Placing `那` before `那么` makes the regex consume the first character and leave
        # `么` attached (producing clause 「么停下来」), breaking the entire rule. The same applies to `快`/`快点`.
        ("那么停下来吧", "/stop"), ("快点停下来", "/stop"), ("快點停下來", "/stop"),
        ("快点去执行", "/daemon approve"),
        # Imperative adverbs: the most common Chinese imperative modifiers; omitting them from the closed set restricts these commands to bare imperatives
        ("赶紧停下来", "/stop"), ("馬上取消這個任務", "/stop"),
        ("立刻停止搜尋", "/stop"), ("现在别找了", "/stop"),
        ("能不能停下来", "/stop"), ("拜託停下來", "/stop"), ("我想取消这个任务", "/stop"),
        # ⚠️ `要不要` must precede `要不`, or it is consumed into `要停下来`—the fifth time these tables have stumbled over
        # "a multi-character word placed after its first character/prefix" (那么·快点·我想·你们·要不要).
        ("要不要停下来？", "/stop"), ("要不要停下來", "/stop"),
        ("马上去执行", "/daemon approve"),
        # trailing particles stripped
        ("停下来吧", "/stop"), ("停下來吧", "/stop"),
        # Consultation/question tails: 「…好吗 / …行不行」 are the most common polite imperative forms
        ("停下来好吗", "/stop"), ("停下來好嗎", "/stop"),
        ("停下来行不行", "/stop"), ("停下来好不好", "/stop"),
        # ⚠️ Particles also have simplified and traditional sides: accepting only traditional 「囉」 matches a sentence in traditional but not simplified form
        ("停下來囉", "/stop"), ("停下来啰", "/stop"), ("停下来咯", "/stop"),
        ("停下來咯", "/stop"), ("停下来喽", "/stop"),
        # ...but stripping must not resurrect a misfire
        ("雨停下来了", None), ("我准了假", None), ("比賽即將重新開始", None),
        ("我想重新开始新的人生", None), ("我想让时间停下来", None),
        ("可以去执行吗", None), ("能不能去執行嗎？我還沒決定", None),
    ],
)
def test_clause_normalization_strips_only_function_words(text, expected):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ The most common Chinese authorization pattern is "acknowledgment clause + command clause"; before the refactor it used substring matching.
        # Preserve `没错，去执行` especially—it is the example explicitly cited in this file's argument that blacklists reject valid inputs,
        # yet the whitelist approach also rejected it, contradicting the comment's stated behavior.
        ("没错，去执行", "/daemon approve"), ("沒錯，去執行", "/daemon approve"),
        ("没意见，去执行", "/daemon approve"), ("好的，去执行", "/daemon approve"),
        ("行，去执行", "/daemon approve"), ("可以，去执行", "/daemon approve"),
        ("嗯，去执行", "/daemon approve"), ("对，去执行", "/daemon approve"),
        ("批准，去执行", "/daemon approve"), ("可以，删吧", "/daemon approve"),
        ("好的，去执行吧", "/daemon approve"),
        # Forms without separators use the neutral-prefix table
        ("好的去执行", "/daemon approve"), ("可以去执行", "/daemon approve"),
        ("同意去执行", "/daemon approve"), ("批准去执行", "/daemon approve"),
        ("允许去执行", "/daemon approve"), ("没错去执行", "/daemon approve"),
        # ⚠️ Acknowledgment words **cannot authorize alone**: these all yielded None before the refactor (the old exact whole-sentence table
        # contained only those four entries); treating them as authorization broadens approval matching.
        ("好的", None), ("可以", None), ("行", None), ("嗯", None), ("对", None),
        ("批准", None), ("好的，好的", None), ("好的喵~", None),
        # ⚠️ Trimming decorations from acknowledgment clauses is safe (they never authorize alone), and these forms matched substrings in the old
        # implementation, so exact character-for-character matching would lose them. Bare acknowledgments cannot be relaxed this way—compare
        # `没问题喵~` in test_a_question_never_approves.
        ("好的喵~，去执行", "/daemon approve"), ("OK，去执行", "/daemon approve"),
        ("okay，去执行", "/daemon approve"),
        # ⚠️ Negation symbols can also decorate the **acknowledgment** clause: ❌ in `可以❌，去執行` negates that
        # acknowledgment. Its decoration table therefore also uses the strict set—👌 and ❌ cannot be distinguished at this layer,
        # so strip neither category. Cost: `好的👌，去执行` is lost relative to the old implementation.
        ("可以❌，去執行", None), ("可以❌，去执行", None), ("好的🚫，去执行", None),
        ("好的👌，去执行", None), ("请❌，去执行", None),
        # Latin-prefix capitalization forms are an open set; lowercase the entire candidate instead of enumerating them
        ("oK去执行", "/daemon approve"), ("oKaY去执行", "/daemon approve"),
        ("Okay去执行", "/daemon approve"),
        # Mixed Chinese/English forms without separators use the neutral-prefix table
        ("OK去执行", "/daemon approve"), ("ok去执行", "/daemon approve"),
        # ⚠️ Neutral prefixes also count as acknowledgment clauses **when standalone**: `请，去执行` matched substrings in the old
        # implementation, while the same word attached directly (`请去执行`) has always worked. Apply one rule: if removing it does not change
        # "who is authorized to do what", standing alone does not change that either.
        ("请，去执行", "/daemon approve"), ("麻烦，去执行", "/daemon approve"),
        ("拜託，去執行", "/daemon approve"), ("那，去执行", "/daemon approve"),
        ("你，去执行", "/daemon approve"), ("马上，去执行", "/daemon approve"),
        # Polite words alone still do not authorize
        ("请", None), ("麻烦", None), ("那", None), ("请，麻烦", None),
        ("okay去执行", "/daemon approve"),
        ("好的喵~", None),
        # ⚠️ Single-character acknowledgments **are not stripped as prefixes**: `对方去执行` is not authorization
        ("对方去执行", None), ("好人去执行", None),
    ],
)
def test_an_affirmative_clause_needs_a_real_command_beside_it(text, expected):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ Decorations at clause ends: ellipses, em dashes, quotes, brackets, emoji. They are neither in
        # _CLAUSE_SPLIT nor particles, preventing the whole clause from matching the whitelist—yet these are among the most common
        # endings in Chinese chat. Strip using the complement of "word characters" rather than enumerating symbols (an open set).
        ("去执行…", "/daemon approve"), ("去执行……", "/daemon approve"),
        ("去执行~", "/daemon approve"), ("去执行！", "/daemon approve"),
        ("去执行。", "/daemon approve"),
        # ⚠️ Sentence endings can also negate: `去執行❌` means "do not execute" too. Symbols with negation semantics
        # form an open set (❌✖✗🚫⛔🙅🆖…), so a blacklist cannot cover them all—approve's trailing decorations therefore use
        # a closed punctuation set **explicitly without semantics**; never strip emoji. Cost: `去执行👌` is lost relative to the old
        # implementation (👌 and ❌ cannot be distinguished at this layer), recorded here.
        ("去執行❌", None), ("去执行❌", None), ("去執行🚫", None),
        ("去执行✖", None), ("删吧❌", None), ("准了❌", None),
        ("去执行👌", None),
        ("停下来…", "/stop"), ("停下來……", "/stop"), ("停下來——", "/stop"),
        ("別找了…", "/stop"), ("「停下來」", "/stop"), ("“停下来”", "/stop"),
        # The whitelist still applies after stripping decorations—decoration is no master key
        ("雨停下来了…", None), ("我准了假👌", None), ("比賽即將重新開始……", None),
        # ⚠️ Chinese ellipses are **in-sentence separators**, not just trailing decorations
        ("同意……去执行", "/daemon approve"), ("同意⋯⋯去执行", "/daemon approve"),
        # Em dashes likewise separate within a sentence
        ("同意——去执行", "/daemon approve"), ("同意—去执行", "/daemon approve"),
        ("停下來——別找了", "/stop"),
        ("停下來……別找了", "/stop"),
        # ⚠️⚠️ approve **strips trailing decorations only**: the leading position carries semantics. `❌去執行` means "do not execute",
        # and `「去執行」` **mentions** the phrase rather than commanding it. Stripping all of these as decorations would turn them into authorizations.
        ("❌去執行", None), ("❌去执行", None), ("🚫去執行🚫", None),
        ("🚫去执行", None), ("「去執行」", None), ("「去执行」", None),
        ("『去执行』", None), ("（去执行）", None),
        # /stop and /new have minor consequences; continue trimming both ends
        ("「停下來」", "/stop"), ("『別找了』", "/stop"), # ⚠️ Whitespace also separates clauses, making particles standalone final clauses; the last-clause rule must skip back past them
        ("停下来 吧", "/stop"), ("停下來 👍", "/stop"), # ⚠️ To strip this tail, try **all** matching suffixes and take the longest: in `_TAIL_TOKENS`, `了` precedes
        # `好了`; taking only the first match strips `好了` to `好`, so this tail is not recognized as
        # "pure particles" and is treated as a command clause instead. This is the same pitfall as in _clause_hits.
        ("停下来 好了", "/stop"), ("別找了 好了", "/stop"),
        # Polite endings are also pure particles: `谢谢` must not be treated as a command clause
        ("停下来谢谢", "/stop"), ("停下來，謝謝", "/stop"), ("别找了 谢谢", "/stop"), ("停下来多谢", "/stop"),
        ("别找了 吧", "/stop"),
        # ⚠️ **Do not use this for approve**: it would approve `同意 吧` as a bare acknowledgment (the old implementation returned None).
        # The cost is also losing `去执行 吧`; between the two, choose fail-closed.
        ("同意 吧", None), ("去执行 吧", None),
    ],
)
def test_decorative_characters_do_not_hide_a_command(text, expected):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


def test_peeling_is_bounded_for_pathological_input():
    """⚠️ The classifier runs on the user-input path; word-stripping candidates must have a hard upper bound.

    Every peel step pushes another prefix of the clause, so the candidate count
    grows with the run of trailing particles and each one costs a slice — a 20k
    character tail used to stall the event loop for seconds.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter
    from tests.wall_clock import fastest_run

    # ⚠️ Assert both bounds through **behavior**: observable outcomes are deterministic and independent of machine speed.
    # _command_clause's bound: an excessively long tail cannot be fully stripped → do not skip it → return None. Place this before all long inputs
    # so removing the bound fails immediately, without first running the two 60k cases (minutes without a bound).
    # (I previously wrote a timing-based version and claimed "the unbounded version is not slow", using measurements from **before changing the word-stripping logic**.
    # Each step now adds a full-string regex; in actual tests the unbounded version cannot even finish the 120k case,
    # so the bound is necessary.)
    assert OpenClawAdapter.rule_magic_command("停下来 " + "吧" * 5) == "/stop"
    assert OpenClawAdapter.rule_magic_command("停下来 " + "吧" * 200) is None

    def _peel(text):
        # ⚠️ Assert results inside the timed call: each long input runs once to check both behavior and elapsed time.
        # _clause_hits's bound: without a bound, over 2000 ``吧`` particles can be stripped all the way back to whitelisted ``去执行`` /
        # ``停下来``, returning /daemon approve or /stop (mutation-verified); put the two 20k cases first,
        # so removing the bound fails on the first call.
        assert OpenClawAdapter.rule_magic_command(text) is None, text[:8]

    # Timing is a fallback; use the fastest of multiple runs (see tests/wall_clock.py).
    # ⚠️ Previously, one sample of all four long inputs **combined** was compared with one second, but this machine needs 0.4–0.9 seconds without load
    # (the ``随便说说 `` case alone varies from 0.2–0.6 seconds), so one scheduling preemption under xdist caused a false failure.
    # Two thresholds now: each case < 1 second ensures "one message does not stall the event loop"; the sum of the four fastest times < 2 seconds
    # catches partial regressions where every case falls to 0.9 seconds, which individual thresholds miss (3.6 seconds total). The local sum is about
    # 0.4 seconds; the old "combined single sample < 1 second" usually passed CI (including Windows),
    # so taking the fastest times and relaxing to 2 seconds provides over twice the margin. Removing the bound makes the two 20k cases take 5–11 seconds locally.
    texts = (
        "去执行" + "吧" * 20000,  # Exercise word stripping in _clause_hits
        "停下来" + "啊呀" * 10000,
        # ⚠️ Also exercise word stripping in _command_clause: once spaces split particles into standalone final clauses, the loop skipping backward past
        # them is **another** word-stripping site requiring its own bound (caught by mutation testing).
        "停下来 " + "吧" * 60000,
        "随便说说 " + "啊" * 60000,
    )
    # ⚠️ The early-stop threshold is **the per-case share of the total budget** (2 seconds / 4 cases = 0.5 seconds), not the individual 1 second:
    # with 1 second, a sample preempted to 0.9 seconds is recorded as the "fastest", and four such
    # inflated samples exceed 2 seconds, falsely failing the aggregate threshold without a regression (Greptile P1). Stop at the share: if
    # all four stop early, their sum must be < 2 seconds; a case taking 0.5–1 seconds keeps retrying for a faster sample.
    # Individual local cases take 0.05–0.35 seconds and still stop after one run without load.
    total = 0.0
    for text in texts:
        elapsed = fastest_run(
            lambda text=text: _peel(text), repeat=5, stop_below=2.0 / len(texts)
        )
        assert elapsed < 1.0, f"{text[:8]!r}… 最快也要 {elapsed:.2f}s，剥词的界没生效"
        total += elapsed
    assert total < 2.0, f"四条病态输入最快值之和 {total:.2f}s，剥词整体变慢了"


@pytest.mark.parametrize(
    "text",
    [
        # ⚠️ Whitelisted entries' **fragments left by particle stripping** are not valid commands; they also returned None before the refactor
        # (the old table contained only the full 「别找了」「算了别查了」).
        # If lookup changes from "normalize the query then compare" to "close the table under normalized forms", all these
        # fragments match—the same mistake that inserted single-character 「删 / 准」 into the approve table.
        "别找", "別找", "算了别查", "算了別查",
        # The same applies to single-character verbs—they once entered the approve table through table closure
        "删", "刪", "准", "準", "删了吧", "删一下",
    ],
)
def test_truncated_table_entries_are_not_commands(text):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ Strip particles **one at a time, checking the table after each**. Consuming the entire suffix at once also consumes the character belonging
        # to the whitelisted entry itself: stripping all of `了吧` from `别找了吧` yields `别找`, while the table
        # contains `别找了`—a perfectly natural request would then fail to stop the task.
        ("别找了吧", "/stop"), ("別找了吧", "/stop"),
        ("算了别查了吧", "/stop"), ("算了別查了吧", "/stop"),
        ("快别找了吧", "/stop"), ("别找了啊", "/stop"), ("別找了囉", "/stop"),
        ("准了吧", "/daemon approve"), ("準了吧", "/daemon approve"),
        # ⚠️ At every step, try **all** matching suffixes, not just the one selected by the regex.
        # Alternatives take priority from the left: `行吗` matches first in `停下来行吗`, stripping it to `停下来行`,
        # from which `停下来` can no longer be derived. Reordering the word list cannot fix this; `行吗` itself must be included.
        # (The approve side cannot test this—interrogatives unconditionally veto approval; see
        # test_a_question_never_approves。）
        ("停下来行吗", "/stop"), ("停下來行嗎", "/stop"), ("停下来吗", "/stop"), ("停下來嗎", "/stop"), ],
)
def test_particles_are_peeled_one_at_a_time(text, expected):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


@pytest.mark.parametrize(
    ("clause", "table_name"),
    [
        # ⚠️ This **tests the lookup layer directly**, bypassing rule_magic_command.
        # The "left-priority alternative consumes a content character" bug affects only **whitelisted entries ending in 「行」**
        # (`行吗` consumes `去执行吗` into `去执`), and these forms are now blocked by the interrogative veto first—
        # invisible from rule_magic_command, so mutation testing would incorrectly classify the mutation as equivalent.
        # Matching and vetoing are separate layers; pin both separately, or adjusting the veto someday silently revives the stripping bug.
        ("去执行吗", "_APPROVE_ACTIONS"), ("去執行嗎", "_APPROVE_ACTIONS"),
        ("去执行行不行", "_APPROVE_ACTIONS"), ("去執行好不好", "_APPROVE_ACTIONS"),
        ("停下来行吗", "_STOP_CLAUSES"), ("停下來行嗎", "_STOP_CLAUSES"),
    ],
)
def test_the_lookup_layer_peels_every_matching_tail(clause, table_name):
    from brain.openclaw_adapter import (
        _APPROVE_ACTIONS,
        _STOP_CLAUSES,
        _clause_hits,
    )

    tables = {
        "_APPROVE_ACTIONS": _APPROVE_ACTIONS,
        "_STOP_CLAUSES": _STOP_CLAUSES,
    }
    assert _clause_hits(clause, tables[table_name]), f"{clause} 剥不出 {table_name} 里的条目"


@pytest.mark.parametrize(
    "text",
    [
        # Punctuation
        "去執行？", "去执行？", "刪吧？", "删吧？", "准了？", "準了？", "去執行?",
        "没问题，去执行？", "沒問題，去執行？", "同意？", "我同意？",
        # ⚠️ Recognizing punctuation alone is insufficient: normalization erases interrogative mood entirely. Sentence-final particles…
        "去執行嗎", "去执行吗", "刪吧嗎", "删吧吗", "準了嗎", "准了吗",
        # …affirmative-negative / alternative questions (at the start or inside) also strip down to whitelisted action phrases
        "能不能去執行", "能不能去执行", "可不可以去執行", "可不可以去执行",
        "去執行行不行", "去执行好不好", "要不要去执行", "是不是该去执行",
        "是否可以去执行", "去执行怎么样", "去執行怎麼樣",
        # ⚠️ Tentative/proposal prefixes behave the same: after normalization strips them, the **proposal** 「要不就去执行？」
        # becomes authorization. These are all newly exposed cases admitted by the prefix-function-word table.
        "要不去執行", "要不去执行", "要不去执行吧", "要不然去執行",
        "不如去執行", "不如去执行", "不如刪吧", "不如删吧", "不如準了",
        "還是去執行", "还是去执行", "还是去执行吧", "乾脆去執行", "干脆去执行",
        # ⚠️ First-person intent prefixes change **who intends to act**, rather than intensifying an imperative.
        # 「我想去執行」 states one's own intention; it does not authorize someone else to act.
        "我想去執行", "我想去执行", "我要去執行", "我要去执行", "想去执行",
        "我想删吧", "我要準了",
        # ⚠️ Blocking `我想`/`我要` alone is insufficient; **bare first-person subjects** behave likewise: 「我去執行」 says the user
        # will act, rather than authorizing the agent. Retain second person—`你去执行吧` precisely expresses authorization.
        "我去執行", "我去执行", "我删吧", "我刪吧", "我准了",
        "咱去执行", "我们去执行吧", "我們去執行吧", "咱们去执行",
        # ⚠️ Aspect marker `了`: `去執行了` reports "already executed", a statement rather than authorization.
        "去執行了", "去执行了", "去執行了喔", "删吧了", "去执行了吧",
    ],
)
def test_a_question_never_approves(text):
    """⚠️ Neither questions nor proposals are authorization.

    Normalization erases the mood entirely and everything lands on a whitelisted
    action phrase: 去執行嗎 loses its 嗎, 能不能去執行 loses its 能不能, 要不去執行
    loses its 要不. The Traditional spellings here were all None on main — the
    whole-clause switch newly exposed them, which is exactly backwards for a
    hardening change.

    Vetoing the whole utterance is fail-closed and costs nothing: nobody granting
    permission phrases it as a question or floats it as a suggestion.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


# Simplified/traditional variant characters in the four function-word tables, as a closed set. List identical forms separately to **detect unlisted
# forms**—the previous guard's blind spot: when folding cannot derive the counterpart, _fold returns the entry itself, which naturally
# belongs to the table, so a new one-sided entry using an unlisted character silently passes.
_FUNCTION_T2S = {
    "錯": "错", "見": "见", "許": "许", "麼": "么", "點": "点", "這": "这",
    "幫": "帮", "給": "给", "煩": "烦", "託": "托", "勞": "劳", "駕": "驾",
    "請": "请", "趕": "赶", "緊": "紧", "馬": "马", "現": "现", "盡": "尽",
    "務": "务", "記": "记", "繼": "继", "續": "续", "們": "们", "還": "还",
    "問": "问",
    "乾": "干", "囉": "啰", "嘍": "喽", "唄": "呗", "喲": "哟", "噠": "哒",
    "吶": "呐", "嗎": "吗", "樣": "样", "沒": "没", "欸": "诶", "謝": "谢",
}
_FUNCTION_NEUTRAL_CHARS = set(
    "一上下不了以你允先准刻即可同吧呀呢呦咧咯咱哈哦唷啊啦喔喵嘛嘞噢在好如妳定就得"
    "心必忙快怎您想意我批拜捏接放是替有然的直立耶能脆行要那麻齁多感否"
)


def test_function_word_tables_are_pinned():
    """⚠️ Pin the four function-word tables by equality—the **only** guard against adding a new prefix category.

    The per-category assertions below only catch tokens someone already thought
    of: they check that known hedges live in the soft tables. A brand-new hedge
    (或许 / 恐怕 / 说不定 …) dropped into the neutral table is in *neither* list,
    so every one of those assertions sails past it — verified by mutation: adding
    或许|恐怕|说不定 to _NEUTRAL_LEAD left the whole suite green while
    「或许去执行」 became a real approval.

    Equality is what closes that. Any edit to these tables now turns this red and
    the reviewer has to state, in the same commit, which category the new token
    belongs to and what its cross-script counterpart is.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import (
        _NEUTRAL_LEAD,
        _NEUTRAL_TAIL,
        _SOFT_LEAD,
        _SOFT_TAIL,
    )

    assert set(_NEUTRAL_LEAD.split("|")) == {
        "OKAY", "Okay", "okay", "OK", "Ok", "ok",
        "没错", "沒錯", "没意见", "沒意見", "批准", "允许", "允許", "同意",
        "好的", "好吧", "行了", "可以",
        "那么", "那麼", "快点", "快點", "就这么", "就這麼", "这就", "這就",
        "帮我", "幫我", "帮忙", "幫忙", "给我", "給我", "替我",
        "麻烦", "麻煩", "拜托", "拜託", "劳驾", "勞駕", "有劳", "有勞",
        "烦请", "煩請",
        "赶紧", "趕緊", "赶快", "趕快", "马上", "馬上", "立刻", "立即",
        "现在", "現在", "尽快", "盡快", "直接",
        "务必", "務必", "记得", "記得", "一定", "放心", "继续", "繼續",
        "你们", "你們", "您们", "您們", "您",
        "那", "就", "先", "快", "请", "請", "你", "妳",
    }
    assert set(_SOFT_LEAD.split("|")) == {
        "要不要", "能不能", "可不可以", "要不然", "要不", "不如", "还是", "還是",
        "是否可以", "是否能", "是否", "能否", "可否",
        "请问", "請問",
        "干脆", "乾脆", "我想", "我要", "我们", "我們", "咱们", "咱們",
        "想", "我", "咱",
    }
    assert set(_NEUTRAL_TAIL.split("|")) == {
        "好了", "吧", "啊", "呀", "喔", "哦", "嘛", "囉", "啰", "咯", "喽",
        "嘍", "呗", "唄", "嘞", "啦", "一下", "喵",
        "谢谢", "謝謝", "多谢", "多謝", "感谢", "感謝",
        "拜托", "拜託", "麻烦", "麻煩",
        "耶", "唷", "哟", "喲", "欸", "诶", "咧", "哈", "噢", "呐", "吶",
        "呦", "哒", "噠", "齁", "捏", "~", "～",
    }
    assert set(_SOFT_TAIL.split("|")) == {
        "好不好", "好吗", "好嗎", "行不行", "行吗", "行嗎", "可以吗", "可以嗎",
        "怎么样", "怎麼樣", "吗", "嗎", "呢", "了",
    }


def test_function_word_tables_are_script_symmetric():
    """⚠️ Function-word tables also need simplified/traditional coverage, just like clause whitelists.

    The file's whole thesis is that these tables hit the characters a user
    actually types, so both scripts must be collected in the same pass — yet
    until now only the *clause* whitelists had a symmetry guard. Dropping 現在 /
    嘍 / 可以嗎 / 給我 individually left the suite green while their Simplified
    twins were covered: exactly the asymmetry this series exists to kill.

    ⚠️ Unknown characters fail loudly. The previous guard folded with a partial
    map and returned the entry unchanged when a character was missing — and the
    entry is of course in its own table, so a one-sided addition using a new
    character passed silently. Here every CJK character must be accounted for.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import (
        _NEUTRAL_LEAD,
        _NEUTRAL_TAIL,
        _SOFT_LEAD,
        _SOFT_TAIL,
    )

    s2t = {v: k for k, v in _FUNCTION_T2S.items()}
    tables = {
        "neutral_lead": _NEUTRAL_LEAD,
        "soft_lead": _SOFT_LEAD,
        "neutral_tail": _NEUTRAL_TAIL,
        "soft_tail": _SOFT_TAIL,
    }

    unknown = {
        char
        for table in tables.values()
        for word in table.split("|")
        for char in word
        if "㐀" <= char <= "鿿"
        and char not in _FUNCTION_T2S
        and char not in s2t
        and char not in _FUNCTION_NEUTRAL_CHARS
    }
    assert not unknown, (
        f"这些字形不在折叠表也不在中性清单里，简繁对称无法验证 → {sorted(unknown)}"
    )

    def _fold(text, mapping):
        return "".join(mapping.get(char, char) for char in text)

    for name, table in tables.items():
        entries = set(table.split("|"))
        missing = []
        for entry in sorted(entries):
            for direction, mapping in (("t2s", _FUNCTION_T2S), ("s2t", s2t)):
                counterpart = _fold(entry, mapping)
                if counterpart not in entries:
                    missing.append(f"{entry} --{direction}--> {counterpart}")
        assert not missing, f"{name}: 对侧字形缺失 → {missing}"


def test_narrow_and_wide_lead_sets_are_disjoint_where_it_matters():
    """⚠️ Structural guard: approve's narrow table **must not** contain tentative/intent prefixes.

    Three rounds of Codex P1 landed on the same shape — a wide strip set plus a
    veto list that kept missing a category (punctuation, then bare interrogative
    particles, then tentative proposals, then first-person intent). \"Which prefix
    turns an imperative into a non-authorization\" is an open set; a blacklist
    cannot close it. The fix was two whitelists, and this test pins that they
    stay separated: widening approve now means editing the narrow table, which is
    a visible, reviewable act.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import (
        _NEUTRAL_LEAD,
        _NEUTRAL_TAIL,
        _SOFT_LEAD,
        _SOFT_TAIL,
    )

    narrow_lead = set(_NEUTRAL_LEAD.split("|"))
    soft_lead = set(_SOFT_LEAD.split("|"))
    narrow_tail = set(_NEUTRAL_TAIL.split("|"))
    soft_tail = set(_SOFT_TAIL.split("|"))

    assert not (narrow_lead & soft_lead), "宽首部词漏进了 approve 的窄首部表"
    assert not (narrow_tail & soft_tail), "宽词尾漏进了 approve 的窄词尾表"

    # ⚠️ One criterion alone determines inclusion: **does stripping it change authorization into non-authorization**? Pin each category below,
    # because every Codex P1 in this series found "another category mistakenly in the neutral table"—questions, tentative proposals,
    # first-person intent, bare first-person subjects, aspect markers; one category per round.
    for token in (
        # Tentative proposal: offering an option, not approving
        "要不", "不如", "还是", "還是", "干脆", "乾脆", "能不能", "可不可以",
        # First-person intent: stating one's own plans
        "我想", "我要", "想",
        # Bare first-person subject: announcing one's own action, not authorizing the agent
        "我", "咱", "我们", "我們", "咱们", "咱們",
    ):
        assert token in soft_lead, f"{token} 不在 soft 首部表里"
        assert token not in narrow_lead, f"{token} 混进了 approve 的窄首部表"
    for token in (
        # Interrogative tail: a question is not authorization
        "吗", "嗎", "呢", "好不好", "行不行", "可以吗", "怎么样",
        # Aspect marker: `去執行了` reports a past event, not authorization
        "了",
    ):
        assert token in soft_tail, f"{token} 不在 soft 词尾表里"
        assert token not in narrow_tail, f"{token} 混进了 approve 的窄词尾表"

    # Conversely, second-person subjects must stay in the neutral table—`你去执行吧` authorizes the agent directly.
    # Include plural/honorific forms too, or single-character `你` will split `你们`.
    for token in ("你", "妳", "你们", "你們", "您", "您们", "您們"):
        assert token in narrow_lead, f"{token} 是第二人称，不该被挪走"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ Colons are clause boundaries too. `同意：去执行` matched substrings before the refactor; without splitting on colons, the entire input
        # matches no whitelist entry (Codex P2).
        ("同意：去执行", "/daemon approve"), ("沒問題:去執行", "/daemon approve"),
        ("没问题：去执行", "/daemon approve"), ("同意:删吧", "/daemon approve"),
        ("先说明一下：停下来", "/stop"), ],
)
def test_colons_are_clause_boundaries(text, expected):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


@pytest.mark.parametrize(
    "text",
    ["马上去执行", "赶紧去执行", "立刻去执行", "现在就去执行", "直接去执行",
     "那就去执行吧", "先去執行吧", "馬上去執行", "趕緊去執行", "盡快去執行"],
)
def test_decisive_adverbs_still_approve(text):
    """⚠️ The hedge veto must not swallow decisive adverbs.

    馬上 / 趕緊 / 立刻 / 現在 / 盡快 / 直接 / 那就 / 先 intensify an imperative
    rather than propose one; every Simplified spelling here approves on main, so
    rejecting them would be pure recall loss with no safety gain. The line is
    \"tentative proposal vs. emphasised command\", not \"has an adverb\".
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == "/daemon approve", text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ The question-mark veto **applies only to approve**: for /stop and /new,
        # 「停下来好吗？」 is a perfectly normal polite imperative and must not be rejected along with approvals.
        ("停下来好吗？", "/stop"), ("停下來好嗎？", "/stop"),
        ("能不能停下来？", "/stop"),
        # The same applies to questions without punctuation—「能不能停下来」 is among the most common polite imperatives
        ("能不能停下来", "/stop"), ("可不可以停下來", "/stop"),
        ("停下来吗", "/stop"), ("停下来行不行", "/stop"),
        # Tentative/proposal forms are likewise perfectly normal imperatives for /stop and /new
        ("要不停下来", "/stop"), ("不如停下來", "/stop"), ("還是停下來吧", "/stop"),
        ("乾脆停下來", "/stop"),
    ],
)
def test_the_question_veto_is_scoped_to_approve(text, expected):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


def test_no_magic_command_fires_on_the_projects_own_ui_copy():
    """⚠️ Auto-discovered corpus, not a hand-written list.

    static/locales/{zh-CN,zh-TW}.json is ~4257 strings of product copy with zero
    command intent. Before the whole-clause switch, 6 strings in EACH script
    dispatched a magic command — including the day6 tutorial line 「随时都可以戳
    一下让我停下来」, i.e. N.E.K.O.'s own script would have halted a task.

    This corpus grows with the product, so it keeps finding regressions a fixed
    adversarial list cannot. It is a floor, not a ceiling: UI copy is written
    prose, and real speech carries these phrases far more densely.
    """  # noqa: DOCSTRING_CJK
    import json
    from pathlib import Path

    from brain.openclaw_adapter import OpenClawAdapter

    def _walk(node):
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for value in node.values():
                yield from _walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from _walk(value)

    repo_root = Path(__file__).resolve().parents[2]
    checked = 0
    hits = []
    for locale in ("zh-CN", "zh-TW"):
        path = repo_root / "static" / "locales" / f"{locale}.json"
        if not path.exists():
            pytest.skip(f"{path} missing")
        for text in _walk(json.loads(path.read_text(encoding="utf-8"))):
            if not text.strip():
                continue
            checked += 1
            command = OpenClawAdapter.rule_magic_command(text)
            if command:
                hits.append((locale, command, text[:80]))

    assert checked > 1000, f"语料没读到，只有 {checked} 条"
    assert not hits, f"UI 文案触发了 magic command：{hits}"


@pytest.mark.parametrize("text", ["别找了", "別找了", "算了别查了", "算了別查了"])
def test_stop_triggers_containing_a_negator_are_untouched(text):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == "/stop"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("我还没同意，停止搜索", "/stop"),
        ("我還沒同意，停止搜尋", "/stop"),
        # The traditional case is absent here: /clear triggers deliberately stay simplified (see
        # test_destructive_command_triggers_stay_simplified_only), so
        # 「我不同意，清空聊天記錄」 should already return None; a negation phrase is not what blocks it.
    ],
)
def test_negation_does_not_suppress_the_other_commands(text, expected):
    """⚠️ The negation check is scoped to the approve branch on purpose.

    A first attempt put the negated-approval phrases in the global
    high-precision list, which is consulted before *every* mapping — so an
    unrelated "I don't agree with the plan, change the topic" stopped
    dispatching ``/new`` at all (Codex P2). Only ``/daemon approve`` executes
    anything, so only it gets the fail-closed treatment.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected


# ---------------------------------------------------------------------------
# utils/music_crawlers.py — which crawler a keyword routes to
# ---------------------------------------------------------------------------

ROUTING_TABLES = [
    "ROUTING_STRONG_CLASSICAL_KEYWORDS",
    "ROUTING_INSTRUMENT_KEYWORDS",
    "ROUTING_MODERN_STYLE_KEYWORDS",
    "ROUTING_INDIE_KEYWORDS",
    "ROUTING_CHINESE_KEYWORDS",
]

# Simplified -> Traditional for exactly the characters these five tables use.
# Explicit rather than converter-driven: a converter would just restate itself.
#
# ⚠️ 杰 is deliberately absent. It is *not* a 1:1 mapping — 周杰倫 keeps 杰 while
# 林俊傑 takes 傑, so a character map gets one of the two wrong whichever way it
# is set. Both names are listed below instead.
_ROUTING_CHAR_MAP = str.maketrans({
    "贝": "貝", "扎": "札", "响": "響", "协": "協", "鸣": "鳴", "钢": "鋼",
    "说": "說", "电": "電", "松": "鬆", "独": "獨", "众": "眾", "环": "環",
    "华": "華", "语": "語", "国": "國", "伦": "倫", "邓": "鄧",
    "陈": "陳", "张": "張", "学": "學", "刘": "劉", "静": "靜",
    "荣": "榮", "谦": "謙", "赵": "趙", "许": "許", "莹": "瑩", "闽": "閩",
})

# Entries a plain character map cannot produce: proper names whose Taiwan
# rendering is a different choice of character, not a different spelling.
_TAIWAN_RENDERINGS = {
    "莫扎特": "莫札特",
    "周杰伦": "周杰倫",
    "林俊杰": "林俊傑",
}

# Rows that belong to a *different language's* section of the same table, where
# Chinese conversion rules do not apply. `中国語` is the Japanese word for
# "Chinese language" — 国 is correct there and must not become 國.
_NOT_CHINESE_ROWS = {"中国語"}


@pytest.mark.parametrize("table_name", ROUTING_TABLES)
def test_every_simplified_routing_keyword_has_a_traditional_sibling(table_name):
    from utils import music_crawlers

    table = getattr(music_crawlers, table_name)
    present = {entry.lower() for entry in table}
    missing = []
    converted_any = False
    for entry in table:
        if entry in _NOT_CHINESE_ROWS:
            continue
        if not any("一" <= ch <= "鿿" for ch in entry):
            continue  # latin / kana / hangul row
        traditional = _TAIWAN_RENDERINGS.get(entry, entry.translate(_ROUTING_CHAR_MAP))
        if traditional == entry:
            continue  # identical in both scripts
        converted_any = True
        if traditional.lower() not in present:
            missing.append((entry, traditional))
    assert converted_any, f"{table_name}: 字符映射没转出任何东西，用例已失效"
    assert not missing, f"{table_name} 缺繁体对应条目：{missing}"


def test_routing_tables_are_module_level_so_they_can_be_asserted():
    """They used to be locals inside the scheduler, where nothing could see a
    missing entry until a user reported bad routing."""
    from utils import music_crawlers

    for name in ROUTING_TABLES:
        table = getattr(music_crawlers, name)
        # Require only "iterable and nonempty"—these tables only perform membership lookups, so changing to tuple/frozenset later
        # is a natural optimization; requiring list would cause needless failures (CodeRabbit nitpick).
        assert isinstance(table, (list, tuple, set, frozenset)), name
        assert table, f"{name}: 表为空"


@pytest.mark.parametrize(
    "text",
    [
        "换个话题", "換個話題", "换个话题吧", "重新开始", "重新開始", "重新开始吧",
        "说点别的", "說點別的", "聊点别的", "聊點別的", "重新开个话题",
        "我们换个话题好不好", "不聊这个了，换个话题", "这局输了，重新开始",
        "忘了刚才的事", "忘掉刚才的事", "清除聊天记录", "清空聊天记录",
        "删掉刚才的记录", "清除我们的聊天记录", "忘了刚才的事吧",
    ],
)
def test_new_and_clear_are_unreachable_from_free_text(text):
    """⚠️ `/new` and `/clear` recognize only literal commands; free text never triggers them.

    Three measured facts multiplied: the highest misfire rate (6 of 14 pure-chat
    \"change topic\" phrasings fired, and all six meant the *chat* topic), no local
    state to gate on at all, and an irreversible effect — ``/new`` overwrites the
    one pointer to the upstream session in place, after which a later ``/stop``
    lands on the new session and cannot reach the job still running in the old.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/new", "/new"), ("/clear", "/clear"),
        ("/stop", "/stop"), ("/daemon approve", "/daemon approve"),
        ("/approve", "/daemon approve"),
        # ⚠️ Bare words without a slash are no longer commands; see
        # test_a_typed_command_rejects_bare_words_and_anything_extra。
    ],
)
def test_the_literal_commands_all_still_resolve(text, expected):
    """Remove free-text inference while keeping the commands themselves—all four literal commands must remain usable unchanged."""  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


@pytest.mark.parametrize(
    ("text", "tier"),
    [
        ("停下来", "ambiguous"), ("停下來", "ambiguous"),
        ("快停下来", "ambiguous"), ("别找了", "ambiguous"), ("別找了", "ambiguous"),
        ("取消这个任务", "addressed"), ("取消這個任務", "addressed"),
        ("停止搜索", "addressed"), ("停止搜尋", "addressed"),
        ("算了别查了", "addressed"), ("取消这个搜索", "addressed"),
        ("/stop", None), ("stop", None), ("今天天气真好", None),
    ],
)
def test_stop_phrasings_are_split_into_two_tiers(text, tier):
    """⚠️ Tiering is a pure function: state lives in agent_server, and brain must not reach into it.

    The ambiguous tier is the set of imperatives that are word-for-word identical
    when addressed to the character instead of the agent, so the dispatcher asks
    for corroboration there. The addressed tier and the literal command never
    need it — the registry lies exactly when /stop matters most.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.stop_trigger_tier(text) == tier, text


def test_the_two_stop_tiers_are_disjoint_and_cover_the_table():
    """The two tiers must partition the entire table: neither overlapping nor omitting entries."""  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import (
        _STOP_ADDRESSED,
        _STOP_AMBIGUOUS,
        _STOP_CLAUSES,
    )

    assert _STOP_ADDRESSED & _STOP_AMBIGUOUS == frozenset()
    assert _STOP_ADDRESSED | _STOP_AMBIGUOUS == _STOP_CLAUSES


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/stop", "/stop"), ("/new", "/new"), ("/clear", "/clear"),
        ("/daemon approve", "/daemon approve"), ("/approve", "/daemon approve"),
    ],
)
def test_a_typed_command_never_depends_on_the_llm(text, expected):
    """⚠️⚠️ Typed commands must be **guaranteed** delivery; the small model cannot have veto power.

    ``classify_magic_intent`` used to await the LLM leg unconditionally, and any
    dict it returned — including \"not a command\" — was final, so the rule layer
    never got a say. A model having an off moment silently swallowed a typed
    ``/stop`` while the upstream job kept running.

    It also broke the free-text veto added alongside: a typed ``/new`` went out
    to the LLM, came back, and was killed as if it had been *inferred* from free
    text. Deciding the literal form before the LLM fixes both and saves a call.
    """  # noqa: DOCSTRING_CJK
    import asyncio

    from brain.openclaw_adapter import OpenClawAdapter

    adapter = OpenClawAdapter.__new__(OpenClawAdapter)
    called = []

    async def _hostile_llm(_self, user_text):
        called.append(user_text)
        return {"is_magic_intent": False, "command": None}

    original = OpenClawAdapter._classify_magic_intent_with_llm
    OpenClawAdapter._classify_magic_intent_with_llm = _hostile_llm
    try:
        result = asyncio.run(OpenClawAdapter.classify_magic_intent(adapter, text))
    finally:
        OpenClawAdapter._classify_magic_intent_with_llm = original

    assert result.get("command") == expected, text
    assert called == [], "字面命令不该把它送进 LLM"


@pytest.mark.parametrize("text", ["换个话题", "忘了刚才的事", "重新开始", "清除聊天记录"])
def test_the_free_text_veto_still_holds_when_the_llm_misbehaves(text):
    """Prompts cannot constrain the model: even an LLM response insisting on /new must be vetoed."""  # noqa: DOCSTRING_CJK
    import asyncio

    from brain.openclaw_adapter import OpenClawAdapter

    adapter = OpenClawAdapter.__new__(OpenClawAdapter)

    async def _rogue_llm(_self, user_text):
        return {"is_magic_intent": True, "command": "/new"}

    original = OpenClawAdapter._classify_magic_intent_with_llm
    OpenClawAdapter._classify_magic_intent_with_llm = _rogue_llm
    try:
        result = asyncio.run(OpenClawAdapter.classify_magic_intent(adapter, text))
    finally:
        OpenClawAdapter._classify_magic_intent_with_llm = original

    # ⚠️ After vetoing, execution **falls back to the rule layer**, so source is "rule" rather than the veto constant—
    # the key assertion is that no command was dispatched, rather than which layer dispatched it.
    assert result.get("command") is None, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/stop", "/stop"), ("/new", "/new"), ("/clear", "/clear"),
        ("/daemon approve", "/daemon approve"), ("/approve", "/daemon approve"),
        # Case and surrounding whitespace do not count as a "suffix"
        ("/STOP", "/stop"), (" /stop ", "/stop"), ("/Daemon Approve", "/daemon approve"),
    ],
)
def test_a_typed_command_must_be_slash_prefixed_and_bare(text, expected):
    """⚠️ A user-typed magic command must start with `/`, and the entire input must be that command."""  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.parse_typed_magic_command(text) == expected, text


@pytest.mark.parametrize(
    "text",
    [
        # Bare words without a slash—they are ordinary English words
        "stop", "new", "clear", "approve", "daemon approve", "Stop", "CLEAR",
        # Adding anything else makes it no longer "bare"
        "/stop now", "/stop 一下", "/stopp", "/stop!", "/stop.", "请 /stop",
        "stop/", "/ stop", "//stop", "/openclaw stop", "/qwenpaw stop",
        "帮我 /stop", "/daemon approve please",
    ],
)
def test_a_typed_command_rejects_bare_words_and_anything_extra(text):
    """⚠️ All 9 remaining false matches across 8 locales come from slashless `Stop` / `Clear` button labels.

    Accepting the slashless words meant an English UI string — or an English chat
    line — counted as a *typed* command, which also handed it the explicit
    exemption that bypasses the approval gate entirely.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.parse_typed_magic_command(text) is None, text
    assert OpenClawAdapter.rule_magic_command(text) is None, text


def test_no_ui_string_in_any_locale_is_a_magic_command():
    """⚠️ All copy across 8 locales has zero command intent; the match count must be 0."""  # noqa: DOCSTRING_CJK
    import io
    import json
    from pathlib import Path

    from brain.openclaw_adapter import OpenClawAdapter

    def _walk(node, out):
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                _walk(value, out)
        elif isinstance(node, list):
            for value in node:
                _walk(value, out)

    strings: list[str] = []
    # ⚠️ Anchor with __file__, not relative paths: `cd tests && pytest ...` would otherwise yield an empty glob,
    # producing a false failure that the corpus was not loaded. In this file,
    # test_no_magic_command_fires_on_the_projects_own_ui_copy already follows this pattern.
    locales_dir = Path(__file__).resolve().parents[2] / "static" / "locales"
    for path in sorted(locales_dir.glob("*.json")):
        with io.open(path, encoding="utf-8") as handle:
            _walk(json.load(handle), strings)
    assert len(strings) > 30000, "语料没加载到，断言会变成空转"

    hits = [s for s in strings if s.strip() and OpenClawAdapter.rule_magic_command(s.strip())]
    assert hits == [], f"UI 文案被判成命令：{hits[:8]}"


@pytest.mark.parametrize(
    ("text", "tier"),
    [
        # An explicit-tier clause first and an ambiguous closing clause last—the whole sentence remains "explicit"
        ("取消这个任务，停下来", "addressed"),
        ("停止搜索，别找了", "addressed"),
        ("算了别查了，停下来吧", "addressed"),
        ("取消這個搜尋，停下來", "addressed"),
        # Ambiguous phrasing only
        ("停下来", "ambiguous"), ("别找了，停下来", "ambiguous"),
    ],
)
def test_an_addressed_phrase_anywhere_makes_the_whole_utterance_addressed(text, tier):
    """⚠️ Scan **all** clauses for the explicit tier, but only the last clause for the ambiguous tier.

    ``取消这个任务，停下来`` puts the unambiguous cancel first and a colloquial
    closer last; tiering on the trailing clause alone called it ambiguous, so in
    exactly the timeout/restart/TTL moments where nothing corroborates, the most
    deserving phrasing got dropped.

    Scanning every clause is safe here because the tier does **not** decide
    whether ``/stop`` fires — the classifier already did that on the trailing
    clause. ``我说了停止搜索，然后他就走了`` is None before this is ever reached.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.stop_trigger_tier(text) == tier, text


@pytest.mark.parametrize("text", ["我说了停止搜索，然后他就走了", "他让我取消这个任务，我没理"])
def test_a_narrated_addressed_phrase_never_becomes_a_command(text):
    """Scanning the whole sentence for tiering cannot turn narration into commands—the classifier already vetoes them using the last-clause rule."""  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


def test_every_typed_command_key_starts_with_a_slash():
    """⚠️ `startswith(\"/\")` in `parse_typed_magic_command` is a **redundant** defensive check.

    Mutation testing flagged it as equivalent: every key in the table already
    starts with \"/\", so a slashless word misses the lookup anyway. Rather than
    contrive a test around a redundant guard, pin the premise that makes it
    redundant — if someone ever adds a slashless alias, this turns red and they
    have to decide deliberately.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    keys = sorted(OpenClawAdapter._TYPED_MAGIC_COMMANDS)
    assert keys == ["/approve", "/clear", "/daemon approve", "/new", "/stop"]
    assert all(k.startswith("/") for k in keys), keys
    assert all(k == k.lower() for k in keys), "查表前会 lower()，键必须已经是小写"


@pytest.mark.parametrize(
    "text",
    [
        # An ambiguous phrase in a **nonfinal** clause, with no command in the last → do not classify the whole sentence as ambiguous
        "别找了，我自己来", "停下来，这是我当时唯一的念头", "快停下来，他喊道",
        "別找了，我自己來",
    ],
)
def test_the_ambiguous_tier_only_reads_the_trailing_clause(text):
    """⚠️ The explicit tier scans the whole sentence, while the ambiguous tier reads only the final clause—this asymmetry is deliberate.

    Scanning every clause for the ambiguous tier would label a narrated 停下来 as
    \"ambiguous\" instead of None. Under the rule path that costs nothing (the
    classifier already returned None), but on the LLM path it silently flips such
    an utterance from \"no corroboration needed\" to \"needs corroboration\" — a
    behaviour change nobody asked for, and one no dispatch test would notice.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.stop_trigger_tier(text) is None, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [("取消这个任务", "/stop"), ("停止搜索", "/stop"), ("同意，去执行", "/daemon approve")],
)
def test_a_vetoed_llm_command_falls_back_to_the_rules(text, expected):
    """⚠️ Vetoing destructive commands must not discard valid cancellation requests too.

    When the LLM ignores its prompt and answers ``/new`` for 取消这个任务, vetoing
    that is right — but finalizing the veto as \"not magic\" throws away a command
    the zero-LLM rules can identify perfectly well. Veto, then fall through.
    """  # noqa: DOCSTRING_CJK
    import asyncio

    from brain.openclaw_adapter import OpenClawAdapter

    adapter = OpenClawAdapter.__new__(OpenClawAdapter)

    async def _rogue_llm(_self, user_text):
        return {"is_magic_intent": True, "command": "/new"}

    original = OpenClawAdapter._classify_magic_intent_with_llm
    OpenClawAdapter._classify_magic_intent_with_llm = _rogue_llm
    try:
        result = asyncio.run(OpenClawAdapter.classify_magic_intent(adapter, text))
    finally:
        OpenClawAdapter._classify_magic_intent_with_llm = original

    assert result.get("command") == expected, text
    assert result.get("source") == "rule"
