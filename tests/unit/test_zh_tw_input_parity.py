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
    # ⚠️ /clear 的触发词不在这里：它不可逆地清掉上游会话上下文，判据又还是自由文本
    # 子串，所以刻意保持简体。见 test_clear_triggers_stay_simplified_only。
    # 台湾用「搜尋」，所以这一条不是「搜索」的字形转换。
    ("停止搜索", "停止搜尋"),
    ("取消这个任务", "取消這個任務"),
    ("停下来", "停下來"),
    ("没问题", "沒問題"),
    # approve 的子串支已改成整子句白名单，繁体随之补齐——整子句判据下补繁体不再
    # 放大暴露面。见 test_whole_clause_whitelist_kills_free_text_misfires。
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

    ⚠️ Do NOT lean on "the LLM classifier still runs after a None" — that is not
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
    # 表内 → 刹车豁免；表外 → 不豁免（低 external_intent 时整轮被跳过）
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
    one: the prior test docstring called it out as "narrowing it is a separate,
    script-neutral change", which is exactly what this is.
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

    A "reject when a negator precedes the trigger" guard was tried first and an
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
    # ⚠️ 第三张表也要钉。它单独出现永远不授权，但它决定了「应答 + 动作」这一整类
    # 说法认不认——往里加词同样是扩大 approve 的命中面，必须让评审在同一个 commit 里
    # 说清楚。漏掉它的那一轮，`對` 缺失活了整整一个 PR。
    assert _APPROVE_COMPANIONS == frozenset({
        "好", "好的", "好吧", "行", "行了", "可以", "嗯",
        "对", "對", "没错", "沒錯", "没意见", "沒意見",
        "批准", "允许", "允許",
    })
    single_chars = sorted(
        w for w in (_APPROVE_ACTIONS | _APPROVE_AFFIRMATIONS) if len(w) < 2
    )
    assert not single_chars, f"单字条目会让任意祈使句落到批准上：{single_chars}"
    # 应答表里有单字（好 / 行 / 对 / 嗯）是有意的——它们单独一句永远是 None，
    # 只能陪同动作子句出现。这条断言把「单字仅限应答表」钉住。
    from brain.openclaw_adapter import OpenClawAdapter

    assert all(
        OpenClawAdapter.rule_magic_command(word) is None
        for word in sorted(_APPROVE_COMPANIONS)
    ), "应答词单独成句必须是 None"


@pytest.mark.parametrize(
    "text",
    [
        # 裸应答只认整条子句原样：剥首尾都不行，否则主动搭话轮里猫娘自己的口癖
        # 「没问题喵~」就会自批准。这些在改造前全是 None，必须保持。
        "没问题喵~", "没问题喵！", "沒問題喔", "同意~", "我同意喵", "没问题啦",
        "沒問題囉", "同意啦", "不如同意", "那就同意", "马上同意", "同意了",
        # 单字派生曾把这些变成批准 —— 它们是**新的删除请求**，不是批准
        "帮我删了", "帮我删一下", "删一下", "删啦", "删", "准", "刪", "準",
        "幫我刪了", "请删了", "那就删了吧", "删了吧", "快删了", "准一下", "删喵",
    ],
)
def test_approve_never_widens_beyond_the_pre_change_behaviour(text):
    """⚠️ 收口改动扩大高风险命令的命中面是本末倒置。

    Every input here returned None before the change. The clause normalizer made
    them approvals in an intermediate revision — via the table closure (删吧 -> 删)
    and via tail stripping on the bare affirmations (没问题喵~ -> 没问题).
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


# 这三张白名单里出现过的**全部**简繁异体字，闭集。opencc 不是本仓库依赖（只在
# scripts/gen_activity_fold_map.py 里用 `uv run --with` 临时装），所以 importorskip
# 会让这条守卫在 CI 上永远跳过——等于没写。改成闭集自带：新加的字形没进这张表时，
# 下面的双向折叠会折不出对侧形态，断言直接报出缺哪条。
_T2S = {
    "沒": "没", "問": "问", "題": "题", "刪": "删", "準": "准", "執": "执",
    "別": "别", "來": "来", "這": "这", "個": "个", "務": "务", "尋": "寻",
    "說": "说", "開": "开", "話": "话", "點": "点", "換": "换",
    "對": "对", "錯": "错", "見": "见", "許": "许",
}
# 白名单里简繁同形的字，单列。用途和 _FUNCTION_NEUTRAL_CHARS 一样：**发现表外字形**。
# 折叠表折不出对侧时 _fold 返回词条本身，而词条本身当然在表里 —— 于是一个用了表外字
# 的单侧词条会静默通过。`對` 就是这么漏进来的：它不在 _T2S 里，所以 `对` 折不出 `對`，
# 守卫查不出 _APPROVE_COMPANIONS 少了繁体侧。
_CLAUSE_NEUTRAL_CHARS = set(
    "下了以任停允去取可同吧嗯好始快意我批找搜新查止消的算索聊行重"
)
_S2T = {simplified: traditional for traditional, simplified in _T2S.items()}
# ⚠️ 台湾用「搜尋」不用「搜索」——这是**词汇**差异，不是字形转换，折叠折不出来。
# 只有这两组，单独豁免；别把豁免集当垃圾桶，每加一条都要说明为什么不是字形对。
_LEXICAL_NOT_A_FOLD = frozenset({
    "停止搜索", "停止搜尋", "取消这个搜索", "取消這個搜尋",
    # ⚠️ 准 是**一简对多繁**：許可義的繁体就写作「准」（批准 / 准許 / 不准），
    # 「準」是準確義。所以 `批准` 两侧同形，机械折叠折出来的 `批準` 不是词，不能收
    # 进白名单去凑对称——收了等于给 approve 白加一个词条。
    # （表里同时有 `准了`/`準了` 是另一回事：那是把用户可能打错的写法一起认了，
    #   属于放宽召回，不是对称性要求。）
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
        # ⚠️ approve 的判据是**三**张表。少列一张不会让任何断言变红，而 approve 的命中
        # 面照样变宽——`對` 就是这么漏了一整轮：companions 一张守卫都没盖到。
        ("approve_actions", _APPROVE_ACTIONS),
        ("approve_affirmations", _APPROVE_AFFIRMATIONS),
        ("approve_companions", _APPROVE_COMPANIONS),
        ("stop", _STOP_CLAUSES),
    )

    # ⚠️ 表外字形必须报错，否则这条守卫在它身上是空转的：_fold 折不出对侧时返回词条
    # 本身，而词条本身当然在表里 → 静默通过。补完 companions 还不够，`對` 当时也不在
    # _T2S 里，两个漏洞叠在一起才让它活下来。
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
    # ⚠️ 这四条是「末子句」和「任意子句」判据的唯一区分点：换成 any(...) 时只有它们会红。
    assert OpenClawAdapter.rule_magic_command("我还没同意，停止搜索") == "/stop"
    assert OpenClawAdapter.rule_magic_command("我不同意这个方案，取消这个任务") == "/stop"
    assert OpenClawAdapter.rule_magic_command("停下来，这是我当时唯一的念头") is None
    assert OpenClawAdapter.rule_magic_command("停下來，這是我當時唯一的念頭") is None
    # ⚠️ `/new` 已从自由文本路径摘除，这里不再有它的对照；末子句判据仍由上面
    # 那两条 /stop 钉住（换成 any(...) 时「停下来，这是我当时唯一的念头」会红）。
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
        # ⚠️ 第二人称的复数/敬称写法要**整词**排在单字 `你` 前面，否则 `你们停下来`
        # 被 `你` 吃掉首字、剩下 `们停下来`。这条和 `那么`/`快点` 是同一个坑。
        ("你们停下来", "/stop"), ("你們停下來", "/stop"),
        ("您去执行吧", "/daemon approve"), ("你们去执行吧", "/daemon approve"),
        # ⚠️ 多字前缀必须整词剥。`那` 排在 `那么` 前面时正则会吃掉首字、留下一个
        # `么` 粘在后面（子句变成「么停下来」），整条判据失效。`快`/`快点` 同理。
        ("那么停下来吧", "/stop"), ("快点停下来", "/stop"), ("快點停下來", "/stop"),
        ("快点去执行", "/daemon approve"),
        # 祈使副词：中文祈使句最常见的修饰，闭集缺了它们等于这套口令只认「裸命令」
        ("赶紧停下来", "/stop"), ("馬上取消這個任務", "/stop"),
        ("立刻停止搜尋", "/stop"), ("现在别找了", "/stop"),
        ("能不能停下来", "/stop"), ("拜託停下來", "/stop"), ("我想取消这个任务", "/stop"),
        # ⚠️ `要不要` 必须排在 `要不` 前面，否则被咬成 `要停下来`——这是这套表
        # 第五次栽在「多字词排在它的首字/前缀后面」上（那么·快点·我想·你们·要不要）。
        ("要不要停下来？", "/stop"), ("要不要停下來", "/stop"),
        ("马上去执行", "/daemon approve"),
        # trailing particles stripped
        ("停下来吧", "/stop"), ("停下來吧", "/stop"),
        # 征询/疑问尾：「…好吗 / …行不行」是最常见的礼貌祈使口吻
        ("停下来好吗", "/stop"), ("停下來好嗎", "/stop"),
        ("停下来行不行", "/stop"), ("停下来好不好", "/stop"),
        # ⚠️ 语气词也是简繁两侧的东西：只收繁体「囉」会让同一句话繁体命中简体不命中
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
        # ⚠️ 中文里最常见的授权说法是「应答子句 + 命令子句」，改造前靠子串命中。
        # `没错，去执行` 尤其要保住——它正是本文件论证「黑名单会误伤」时点名的例子，
        # 白名单方案曾把它一起误伤，注释和行为对不上。
        ("没错，去执行", "/daemon approve"), ("沒錯，去執行", "/daemon approve"),
        ("没意见，去执行", "/daemon approve"), ("好的，去执行", "/daemon approve"),
        ("行，去执行", "/daemon approve"), ("可以，去执行", "/daemon approve"),
        ("嗯，去执行", "/daemon approve"), ("对，去执行", "/daemon approve"),
        ("批准，去执行", "/daemon approve"), ("可以，删吧", "/daemon approve"),
        ("好的，去执行吧", "/daemon approve"),
        # 不带分隔符的写法走中性首部表
        ("好的去执行", "/daemon approve"), ("可以去执行", "/daemon approve"),
        ("同意去执行", "/daemon approve"), ("批准去执行", "/daemon approve"),
        ("允许去执行", "/daemon approve"), ("没错去执行", "/daemon approve"),
        # ⚠️ 但应答词**不能单独授权**：这些在改造前都是 None（旧的整句精确匹配表
        # 只有那四条），当成授权就是扩大批准面。
        ("好的", None), ("可以", None), ("行", None), ("嗯", None), ("对", None),
        ("批准", None), ("好的，好的", None), ("好的喵~", None),
        # ⚠️ 应答子句剥两端装饰是安全的（它单独出现永远不算授权），而这些形态在旧
        # 实现里靠子串命中，逐字原样匹配会丢。裸应答不能这么放宽——对比
        # test_a_question_never_approves 里的 `没问题喵~`。
        ("好的喵~，去执行", "/daemon approve"), ("OK，去执行", "/daemon approve"),
        ("okay，去执行", "/daemon approve"),
        # ⚠️ 否定符号也能落在**应答**子句上：`可以❌，去執行` 里的 ❌ 否定的是那句
        # 应答。所以应答子句的装饰表也用严格那张——👌 和 ❌ 在这一层分不出来，
        # 只能整类不剥。代价：`好的👌，去执行` 相对旧实现丢了。
        ("可以❌，去執行", None), ("可以❌，去执行", None), ("好的🚫，去执行", None),
        ("好的👌，去执行", None), ("请❌，去执行", None),
        # 拉丁前缀的大小写写法是开集，靠整体小写候选覆盖而不是枚举
        ("oK去执行", "/daemon approve"), ("oKaY去执行", "/daemon approve"),
        ("Okay去执行", "/daemon approve"),
        # 中英混排且不带分隔符的写法走中性首部表
        ("OK去执行", "/daemon approve"), ("ok去执行", "/daemon approve"),
        # ⚠️ 中性首部词**独立成句**时也算应答子句：`请，去执行` 在旧实现里靠子串
        # 命中，而同样的词贴着写（`请去执行`）一直是通的。判据一致：剥掉它不改变
        # 「谁被授权做什么」，那么它单独成句同样不改变。
        ("请，去执行", "/daemon approve"), ("麻烦，去执行", "/daemon approve"),
        ("拜託，去執行", "/daemon approve"), ("那，去执行", "/daemon approve"),
        ("你，去执行", "/daemon approve"), ("马上，去执行", "/daemon approve"),
        # 但礼貌词单独出现仍不算授权
        ("请", None), ("麻烦", None), ("那", None), ("请，麻烦", None),
        ("okay去执行", "/daemon approve"),
        ("好的喵~", None),
        # ⚠️ 单字应答**不做前缀剥离**：`对方去执行` 不是授权
        ("对方去执行", None), ("好人去执行", None),
    ],
)
def test_an_affirmative_clause_needs_a_real_command_beside_it(text, expected):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 子句两端的装饰性字符：省略号、破折号、引号、括号、emoji。它们既不在
        # _CLAUSE_SPLIT 也不是语气词，整条就落不到白名单上——而中文聊天里这是最常见
        # 的收尾方式之一。用「非词字符」的补集来剥，不去枚举符号（符号是开集）。
        ("去执行…", "/daemon approve"), ("去执行……", "/daemon approve"),
        ("去执行~", "/daemon approve"), ("去执行！", "/daemon approve"),
        ("去执行。", "/daemon approve"),
        # ⚠️ 句尾同样能带否定：`去執行❌` 也是「别执行」。「哪些符号带否定语义」
        # 是开集（❌✖✗🚫⛔🙅🆖……），黑名单堵不完——所以 approve 的句尾装饰改成
        # 一张**明确无语义**的标点闭集，emoji 一律不剥。代价：`去执行👌` 相对旧
        # 实现丢了（👌 和 ❌ 在这一层区分不了），记账在此。
        ("去執行❌", None), ("去执行❌", None), ("去執行🚫", None),
        ("去执行✖", None), ("删吧❌", None), ("准了❌", None),
        ("去执行👌", None),
        ("停下来…", "/stop"), ("停下來……", "/stop"), ("停下來——", "/stop"),
        ("別找了…", "/stop"), ("「停下來」", "/stop"), ("“停下来”", "/stop"),
        # 剥完装饰仍要过白名单——装饰不是万能钥匙
        ("雨停下来了…", None), ("我准了假👌", None), ("比賽即將重新開始……", None),
        # ⚠️ 中文省略号是**句中分隔符**，不只是句尾装饰
        ("同意……去执行", "/daemon approve"), ("同意⋯⋯去执行", "/daemon approve"),
        # 破折号同理，也是句中分隔符
        ("同意——去执行", "/daemon approve"), ("同意—去执行", "/daemon approve"),
        ("停下來——別找了", "/stop"),
        ("停下來……別找了", "/stop"),
        # ⚠️⚠️ approve **只剥句尾装饰**：句首那一格是语义位。`❌去執行` 是「别执行」，
        # `「去執行」` 是在**提及**这个词而不是下令。一律当装饰剥掉就全变成了授权。
        ("❌去執行", None), ("❌去执行", None), ("🚫去執行🚫", None),
        ("🚫去执行", None), ("「去執行」", None), ("「去执行」", None),
        ("『去执行』", None), ("（去执行）", None),
        # /stop 与 /new 后果小，两端照剥
        ("「停下來」", "/stop"), ("『別找了』", "/stop"), # ⚠️ 空白也是分隔符，会把语气词切成独立末子句；末子句判据要往回跳过它们
        ("停下来 吧", "/stop"), ("停下來 👍", "/stop"), # ⚠️ 剥这段尾巴要试**所有**匹配的词尾并取最长：`_TAIL_TOKENS` 里 `了` 排在
        # `好了` 前面，只取第一个匹配会把 `好了` 剥成 `好`，于是这段尾巴不被认成
        # 「纯语气词」、反倒被当成命令子句。和 _clause_hits 里那个坑是同一个。
        ("停下来 好了", "/stop"), ("別找了 好了", "/stop"),
        # 礼貌收尾也是纯语气：`谢谢` 不该被当成命令子句
        ("停下来谢谢", "/stop"), ("停下來，謝謝", "/stop"), ("别找了 谢谢", "/stop"), ("停下来多谢", "/stop"),
        ("别找了 吧", "/stop"),
        # ⚠️ 但**不给 approve 用**：那样 `同意 吧` 会变成裸应答被批准（旧实现是 None）。
        # 代价是 `去执行 吧` 也丢了，二选一，选了 fail-closed 那边。
        ("同意 吧", None), ("去执行 吧", None),
    ],
)
def test_decorative_characters_do_not_hide_a_command(text, expected):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


def test_peeling_is_bounded_for_pathological_input():
    """⚠️ 分类器跑在用户输入路径上，剥词的候选集必须有硬上界。

    Every peel step pushes another prefix of the clause, so the candidate count
    grows with the run of trailing particles and each one costs a slice — a 20k
    character tail used to stall the event loop for seconds.
    """  # noqa: DOCSTRING_CJK
    import time

    from brain.openclaw_adapter import OpenClawAdapter

    start = time.perf_counter()
    # 走 _clause_hits 的剥词
    assert OpenClawAdapter.rule_magic_command("去执行" + "吧" * 20000) is None
    assert OpenClawAdapter.rule_magic_command("停下来" + "啊呀" * 10000) is None
    # ⚠️ 也要走 _command_clause 的剥词：空格把语气词切成独立末子句之后，往回跳过
    # 它们的那段循环是**另一处**剥词，界得单独加（变异验证抓出来的）。
    OpenClawAdapter.rule_magic_command("停下来 " + "吧" * 60000)
    OpenClawAdapter.rule_magic_command("随便说说 " + "啊" * 60000)
    # ⚠️ _command_clause 里那段剥词的界按**行为**断言而不是按耗时。
    # 耗时断言在这里不可靠：阈值要写多大取决于机器，而它的可观察后果是确定的——
    # 超长尾巴剥不完 → 不跳过它 → 返回 None。
    # （早先我按耗时写过一版并据此宣称「无界版也不慢」，那是拿**改剥词逻辑之前**的
    # 数字说话。现在每步都多一次整串正则，无界版实测连 120k 那一档都跑不完，
    # 界是必需的。）
    assert OpenClawAdapter.rule_magic_command("停下来 " + "吧" * 5) == "/stop"
    assert OpenClawAdapter.rule_magic_command("停下来 " + "吧" * 200) is None
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"病态输入耗时 {elapsed:.2f}s，剥词的界没生效"


@pytest.mark.parametrize(
    "text",
    [
        # ⚠️ 表内条目**被语气词截短后的残形**不是有效命令，改造前也是 None
        # （旧表里只有完整的「别找了」「算了别查了」）。
        # 一旦查表改成「把表闭包到归一化形态」而不是「把查询归一化后去比对」，这些
        # 残形会全部命中——那正是单字「删 / 准」混进 approve 表的同一个错误。
        "别找", "別找", "算了别查", "算了別查",
        # 单字动词同理——它们曾因表闭包进过 approve 表
        "删", "刪", "准", "準", "删了吧", "删一下",
    ],
)
def test_truncated_table_entries_are_not_commands(text):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 语气词必须**逐个剥、每剥一个查一次表**。一次性把词尾整串吃掉会连表内
        # 条目自带的那个字一起吃掉：`别找了吧` 的 `了吧` 被整串剥成 `别找`，而表里
        # 的条目是 `别找了`——一句再自然不过的话就停不掉任务了。
        ("别找了吧", "/stop"), ("別找了吧", "/stop"),
        ("算了别查了吧", "/stop"), ("算了別查了吧", "/stop"),
        ("快别找了吧", "/stop"), ("别找了啊", "/stop"), ("別找了囉", "/stop"),
        ("准了吧", "/daemon approve"), ("準了吧", "/daemon approve"),
        # ⚠️ 每一步要对**所有**能匹配的词尾各试一次，不能只试正则挑中的那一个。
        # 多选支从左优先：`停下来行吗` 里 `行吗` 会先命中、把它剥成 `停下来行`，
        # 再也剥不出 `停下来`。换词表顺序解决不了，`行吗` 本身必须收。
        # （approve 侧不能拿来测这个——疑问式对批准是一票否决，见
        # test_a_question_never_approves。）
        ("停下来行吗", "/stop"), ("停下來行嗎", "/stop"), ("停下来吗", "/stop"), ("停下來嗎", "/stop"), ],
)
def test_particles_are_peeled_one_at_a_time(text, expected):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


@pytest.mark.parametrize(
    ("clause", "table_name"),
    [
        # ⚠️ 这条**直接测查表层**，不经 rule_magic_command。
        # 「多选支从左优先咬掉内容字」这个 bug 只在**以「行」结尾的表内条目**上发作
        # （`去执行吗` 被 `行吗` 咬成 `去执`），而那些形式现在被疑问否决先拦掉了——
        # 从 rule_magic_command 那一层再也观察不到，变异测试会误判成等价变异。
        # 判据和否决是两层，分开钉，否则哪天否决被调整，剥词的 bug 会静默复活。
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
        # 标点
        "去執行？", "去执行？", "刪吧？", "删吧？", "准了？", "準了？", "去執行?",
        "没问题，去执行？", "沒問題，去執行？", "同意？", "我同意？",
        # ⚠️ 光认标点不够：归一化会把疑问语气整个抹掉。句末语气词……
        "去執行嗎", "去执行吗", "刪吧嗎", "删吧吗", "準了嗎", "准了吗",
        # ……正反问 / 选择问（首部或句中），剥完同样落在表内的动作短语上
        "能不能去執行", "能不能去执行", "可不可以去執行", "可不可以去执行",
        "去執行行不行", "去执行好不好", "要不要去执行", "是不是该去执行",
        "是否可以去执行", "去执行怎么样", "去執行怎麼樣",
        # ⚠️ 试探/提议型首部词同理——归一化把它们剥掉之后，一句「要不就去执行？」
        # 的**提议**就变成了授权。这些也全是首部虚词表新放行出来的暴露面。
        "要不去執行", "要不去执行", "要不去执行吧", "要不然去執行",
        "不如去執行", "不如去执行", "不如刪吧", "不如删吧", "不如準了",
        "還是去執行", "还是去执行", "还是去执行吧", "乾脆去執行", "干脆去执行",
        # ⚠️ 第一人称意图前缀：它们改变的是**谁打算做**，不是加强祈使语气。
        # 「我想去執行」是在陈述自己的打算，不是授权别人去做。
        "我想去執行", "我想去执行", "我要去執行", "我要去执行", "想去执行",
        "我想删吧", "我要準了",
        # ⚠️ 光挡 `我想`/`我要` 不够，**裸的第一人称主语**同理：「我去執行」是用户在说
        # 自己要去做，不是授权 agent 去做。第二人称留着——`你去执行吧` 恰恰是授权。
        "我去執行", "我去执行", "我删吧", "我刪吧", "我准了",
        "咱去执行", "我们去执行吧", "我們去執行吧", "咱们去执行",
        # ⚠️ 体标记 `了`：`去執行了` 是在报告「已经执行了」，是陈述不是授权。
        "去執行了", "去执行了", "去執行了喔", "删吧了", "去执行了吧",
    ],
)
def test_a_question_never_approves(text):
    """⚠️ 问句和提议都不是授权。

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


# 四张虚词表里出现过的简繁异体字，闭集。中性字形（简繁同形）单列，用于**发现表外
# 字形**——这是上一版守卫的盲区：折不出对侧形态时 _fold 返回词条本身，而词条本身当然
# 在表里，于是新加一个用了表外字的单侧词条会静默通过。
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
    """⚠️ 等值钉死四张虚词表——这是**唯一**能挡住「新加一类前缀」的守卫。

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
    """⚠️ 虚词表也是简繁两侧的东西，不只子句白名单。

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
    """⚠️ 结构性守卫：approve 的窄表**不得**包含试探/意图前缀。

    Three rounds of Codex P1 landed on the same shape — a wide strip set plus a
    veto list that kept missing a category (punctuation, then bare interrogative
    particles, then tentative proposals, then first-person intent). "Which prefix
    turns an imperative into a non-authorization" is an open set; a blacklist
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

    # ⚠️ 入表判据只有一条：**剥掉它会不会把授权变成非授权**。下面按类逐一钉住，
    # 因为这一系列 Codex P1 全是「又发现一类漏在中性表里」——问句、试探提议、
    # 第一人称意图、裸第一人称主语、体标记，一轮一类。
    for token in (
        # 试探提议：是在抛方案，不是批准
        "要不", "不如", "还是", "還是", "干脆", "乾脆", "能不能", "可不可以",
        # 第一人称意图：陈述自己的打算
        "我想", "我要", "想",
        # 裸第一人称主语：宣告自己动手，不是授权 agent
        "我", "咱", "我们", "我們", "咱们", "咱們",
    ):
        assert token in soft_lead, f"{token} 不在 soft 首部表里"
        assert token not in narrow_lead, f"{token} 混进了 approve 的窄首部表"
    for token in (
        # 疑问尾：问句不是授权
        "吗", "嗎", "呢", "好不好", "行不行", "可以吗", "怎么样",
        # 体标记：`去執行了` 是在报告已发生，不是授权
        "了",
    ):
        assert token in soft_tail, f"{token} 不在 soft 词尾表里"
        assert token not in narrow_tail, f"{token} 混进了 approve 的窄词尾表"

    # 反向：第二人称主语必须留在中性表——`你去执行吧` 正是指向 agent 的授权。
    # 复数/敬称写法一并要有，否则单字 `你` 会把 `你们` 咬断。
    for token in ("你", "妳", "你们", "你們", "您", "您们", "您們"):
        assert token in narrow_lead, f"{token} 是第二人称，不该被挪走"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 冒号也是子句边界。`同意：去执行` 在改造前靠子串命中，不切冒号的话整条
        # 落不到任何白名单条目上（Codex P2）。
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
    "tentative proposal vs. emphasised command", not "has an adverb".
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == "/daemon approve", text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 问号否决**只作用于 approve**：对 /stop 和 /new 而言，
        # 「停下来好吗？」是完全正常的礼貌祈使，不能一并毙掉。
        ("停下来好吗？", "/stop"), ("停下來好嗎？", "/stop"),
        ("能不能停下来？", "/stop"),
        # 无标点的疑问式同理——「能不能停下来」是最常见的礼貌祈使之一
        ("能不能停下来", "/stop"), ("可不可以停下來", "/stop"),
        ("停下来吗", "/stop"), ("停下来行不行", "/stop"),
        # 试探/提议型对 /stop 和 /new 同样是完全正常的祈使
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
        # 繁体那条不在这里：/clear 的触发词刻意保持简体（见
        # test_destructive_command_triggers_stay_simplified_only），所以
        # 「我不同意，清空聊天記錄」本来就该是 None，不是被否定短语压掉的。
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
        # 只要求「可迭代且非空」——这几张表只做成员查找，将来改成 tuple/frozenset
        # 是自然的优化，钉死 list 会无谓地红（CodeRabbit nitpick）。
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
    """⚠️ `/new` 与 `/clear` 只认字面命令，自由文本一律不触发。

    Three measured facts multiplied: the highest misfire rate (6 of 14 pure-chat
    "change topic" phrasings fired, and all six meant the *chat* topic), no local
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
        # ⚠️ 不带斜杠的裸词已不再是命令，见
        # test_a_typed_command_rejects_bare_words_and_anything_extra。
    ],
)
def test_the_literal_commands_all_still_resolve(text, expected):
    """摘掉的是自由文本推断，不是命令本身——四条字面命令必须原样可用。"""  # noqa: DOCSTRING_CJK
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
    """⚠️ 分档是纯函数：状态在 agent_server，brain 不能伸手去拿。

    The ambiguous tier is the set of imperatives that are word-for-word identical
    when addressed to the character instead of the agent, so the dispatcher asks
    for corroboration there. The addressed tier and the literal command never
    need it — the registry lies exactly when /stop matters most.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.stop_trigger_tier(text) == tier, text


def test_the_two_stop_tiers_are_disjoint_and_cover_the_table():
    """两档必须是对整张表的划分：既不重叠，也不能漏。"""  # noqa: DOCSTRING_CJK
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
    """⚠️⚠️ 打出来的命令必须**保证**到达，不能让小模型有否决权。

    ``classify_magic_intent`` used to await the LLM leg unconditionally, and any
    dict it returned — including "not a command" — was final, so the rule layer
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
    """提示词管不住模型：LLM 硬返回 /new 也要被毙掉。"""  # noqa: DOCSTRING_CJK
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

    # ⚠️ 否决之后会**回落规则层**，所以 source 是 "rule" 而不是 veto 常量——
    # 关键断言是命令确实没出去，而不是它从哪一层出去的。
    assert result.get("command") is None, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/stop", "/stop"), ("/new", "/new"), ("/clear", "/clear"),
        ("/daemon approve", "/daemon approve"), ("/approve", "/daemon approve"),
        # 大小写与首尾空白不算「后缀」
        ("/STOP", "/stop"), (" /stop ", "/stop"), ("/Daemon Approve", "/daemon approve"),
    ],
)
def test_a_typed_command_must_be_slash_prefixed_and_bare(text, expected):
    """⚠️ 用户打出来的 magic command：必须 `/` 开头，且整条输入就是那个命令。"""  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.parse_typed_magic_command(text) == expected, text


@pytest.mark.parametrize(
    "text",
    [
        # 不带斜杠的裸词 —— 它们是普通英文单词
        "stop", "new", "clear", "approve", "daemon approve", "Stop", "CLEAR",
        # 带了别的东西就不是「裸的」
        "/stop now", "/stop 一下", "/stopp", "/stop!", "/stop.", "请 /stop",
        "stop/", "/ stop", "//stop", "/openclaw stop", "/qwenpaw stop",
        "帮我 /stop", "/daemon approve please",
    ],
)
def test_a_typed_command_rejects_bare_words_and_anything_extra(text):
    """⚠️ 8 个 locale 里残留的 9 条误命中全部来自不带斜杠的 `Stop` / `Clear` 按钮标签。

    Accepting the slashless words meant an English UI string — or an English chat
    line — counted as a *typed* command, which also handed it the explicit
    exemption that bypasses the approval gate entirely.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.parse_typed_magic_command(text) is None, text
    assert OpenClawAdapter.rule_magic_command(text) is None, text


def test_no_ui_string_in_any_locale_is_a_magic_command():
    """⚠️ 8 个 locale 的全部文案：一条命令意图都没有，命中数必须是 0。"""  # noqa: DOCSTRING_CJK
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
    # ⚠️ 用 __file__ 锚定，别用相对路径：`cd tests && pytest ...` 时 glob 会是空的，
    # 断言变成「语料没加载到」的伪失败。同文件的
    # test_no_magic_command_fires_on_the_projects_own_ui_copy 已经是这个写法。
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
        # 明确档在前、模糊收尾在后 —— 整句仍算「明确」
        ("取消这个任务，停下来", "addressed"),
        ("停止搜索，别找了", "addressed"),
        ("算了别查了，停下来吧", "addressed"),
        ("取消這個搜尋，停下來", "addressed"),
        # 只有模糊说法
        ("停下来", "ambiguous"), ("别找了，停下来", "ambiguous"),
    ],
)
def test_an_addressed_phrase_anywhere_makes_the_whole_utterance_addressed(text, tier):
    """⚠️ 明确档扫**所有**子句，模糊档只看末子句。

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
    """分档扫全句不会把叙述变成命令——分类器那层先按末子句判据否掉了。"""  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


def test_every_typed_command_key_starts_with_a_slash():
    """⚠️ `parse_typed_magic_command` 里那句 `startswith("/")` 是**冗余**的防御。

    Mutation testing flagged it as equivalent: every key in the table already
    starts with "/", so a slashless word misses the lookup anyway. Rather than
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
        # 模糊词在**非末**子句，末子句不是命令 → 整句不该被分成模糊档
        "别找了，我自己来", "停下来，这是我当时唯一的念头", "快停下来，他喊道",
        "別找了，我自己來",
    ],
)
def test_the_ambiguous_tier_only_reads_the_trailing_clause(text):
    """⚠️ 明确档扫全句、模糊档只看末子句——这个不对称是有意的。

    Scanning every clause for the ambiguous tier would label a narrated 停下来 as
    "ambiguous" instead of None. Under the rule path that costs nothing (the
    classifier already returned None), but on the LLM path it silently flips such
    an utterance from "no corroboration needed" to "needs corroboration" — a
    behaviour change nobody asked for, and one no dispatch test would notice.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.stop_trigger_tier(text) is None, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [("取消这个任务", "/stop"), ("停止搜索", "/stop"), ("同意，去执行", "/daemon approve")],
)
def test_a_vetoed_llm_command_falls_back_to_the_rules(text, expected):
    """⚠️ 否决破坏性命令，不该连合法的取消一起丢。

    When the LLM ignores its prompt and answers ``/new`` for 取消这个任务, vetoing
    that is right — but finalizing the veto as "not magic" throws away a command
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
