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

"""
OpenClaw Agent adapter.

In this project, "OpenClaw" is the compatibility name for the external
QwenPaw service. The adapter keeps the existing OpenClaw-facing interface
for N.E.K.O, while supporting both QwenPaw's legacy Responses-compatible API
and its v2 console streaming API.
"""

from __future__ import annotations

import asyncio
import re
import threading
import uuid
from typing import Any, Dict, Optional

import httpx

from config import OPENCLAW_MAGIC_INTENT_MAX_TOKENS
from utils.file_utils import robust_json_loads
from utils.llm_client import create_chat_llm_async, strip_thinking_segments
from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Agent")

DEFAULT_OPENCLAW_URL = "http://127.0.0.1:8088"
DEFAULT_TIMEOUT = 300.0
DEFAULT_OPENCLAW_CHANNEL = "console"
QWENPAW_API_PREFIX = "/api/agent"
QWENPAW_PROCESS_ENDPOINT_PATH = f"{QWENPAW_API_PREFIX}/process"
QWENPAW_RESPONSES_ENDPOINT_PATH = f"{QWENPAW_API_PREFIX}/compatible-mode/v1/responses"
QWENPAW_HEALTH_ENDPOINT_PATH = f"{QWENPAW_API_PREFIX}/health"
QWENPAW_VERSION_ENDPOINT_PATH = "/api/version"
QWENPAW_CONSOLE_CHAT_ENDPOINT_PATH = "/api/console/chat"
OPENCLAW_SESSION_CACHE_FILE = "openclaw_sessions.json"
MAGIC_COMMANDS = frozenset({"/clear", "/new", "/stop", "/daemon approve"})
MAGIC_COMMAND_REACTIONS = {
    "/clear": "喵呜？刚才发生了什么？Neko 的脑袋清空空啦！",
    "/new": "好的喵！旧的话题存档啦，主人想聊点什么新鲜事？",
    "/stop": "呼... 终于可以休息了，任务已经强制掐掉了喵！",
    "/daemon approve": "收到许可！Neko 这就放手去干喵！",
}
MAGIC_COMMAND_TASK_DESCRIPTIONS = {
    "/clear": "清除当前 QwenPaw 上下文",
    "/new": "开启新的 QwenPaw 话题会话",
    "/stop": "停止当前 QwenPaw 后台任务",
    "/daemon approve": "批准当前 QwenPaw 高风险动作",
}
MAGIC_INTENT_SYSTEM_PROMPT = """# Role
You are a high-accuracy automation assessment agent, and your task is to determine whether the user input contains control commands for the backend system state.

# Strategy
Prefer false negatives over false positives. Only trigger when the user explicitly asks to manipulate system state.
Only two commands may be inferred from free text: /stop and /daemon approve.
- Trigger example: "取消这个任务" -> /stop
- Trigger example (only right after the backend asked for permission): "同意" -> /daemon approve
- Misfire trap: "我忘了带伞" / "雨停了" / "换个话题吧" -> do NOT trigger

# Output
Output strict JSON only:
{"is_magic_intent": boolean, "command": string|null}
"""


def _normalize_timeout(value: Any, default: float) -> float:
    try:
        timeout = float(value)
        return timeout if timeout > 0 else default
    except (TypeError, ValueError):
        return default


def _resolve_qwenpaw_urls(raw_url: str) -> tuple[str, str, str, str]:
    normalized = str(raw_url or "").strip().rstrip("/")
    if not normalized:
        normalized = DEFAULT_OPENCLAW_URL

    api_root = normalized
    for suffix in (
        QWENPAW_PROCESS_ENDPOINT_PATH,
        QWENPAW_RESPONSES_ENDPOINT_PATH,
        QWENPAW_HEALTH_ENDPOINT_PATH,
        QWENPAW_CONSOLE_CHAT_ENDPOINT_PATH,
        QWENPAW_VERSION_ENDPOINT_PATH,
        QWENPAW_API_PREFIX,
        "/api",
    ):
        if api_root.endswith(suffix):
            api_root = api_root[: -len(suffix)].rstrip("/")
            break

    if not api_root:
        api_root = DEFAULT_OPENCLAW_URL.rstrip("/")

    process_url = f"{api_root}{QWENPAW_PROCESS_ENDPOINT_PATH}"
    responses_url = f"{api_root}{QWENPAW_RESPONSES_ENDPOINT_PATH}"
    health_url = f"{api_root}{QWENPAW_HEALTH_ENDPOINT_PATH}"
    return api_root, process_url, responses_url, health_url


def _extract_json_block(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text:
        return ""
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    if text.startswith("{") and text.endswith("}"):
        return text
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    return match.group(0) if match else text


# ── Vocabulary for the magic-command rule classifier ──
# Module scope provides **assertability**, not reuse: tests cannot access local tuples inside functions,
# leaving missing script variants detectable only by eye (same reasoning as utils/music_crawlers.py routing vocabulary).
#
# These tables match actual user input; simplified/traditional forms use different code points and must be added together.

# Any hit classifies the whole turn as non-magic. Prioritize precision and conservative behavior over expansion.
_HIGH_PRECISION_NON_MAGIC = (
    "我忘了", "我忘记", "我忘記", "雨停了", "停电了", "停電了",
    "新的一天", "你的看法",
)

# ── Whole-clause allowlists ──
# `/daemon approve`, `/stop`, and `/new` switch from **substring containment** to **whole-clause allowlists**.
#
# Substrings match free text. Before this commit, measurements found false hits in 22/22 everyday sentences for `/stop`, 16/16 for `/new`,
# and 17/28 for `/daemon approve`, including `雨停下来了`, `比賽即將重新開始`,
# `我准了假下周去旅游`, and `领导批准了我的申请`. Even the repository's own UI wording
# triggered: each of static/locales/{zh-CN,zh-TW}.json's 4,257 product strings containing **no command intent**
# produced six hits, including day6 tutorial dialogue ("随时都可以戳一下让我停下来").
#
# A prior fallback rejected negation before trigger words, but 196 adversarial inputs defeated it: negation
# after the trigger (`去執行？我不要`), anchors in unrelated substrings (`这标准了不起，但不要去
# 执行` matching `准了`), and questions (`要去執行嗎？`) all passed, while it wrongly rejected
# **affirmative approval expressed with negation**, such as `没错，去执行`. A denylist is structurally unsuitable here.
#
# Current criterion: split clauses on punctuation/whitespace, strip leading function words and trailing particles, then check allowlists.
# **No vocabulary was added**: the same words must now **stand as clauses**, rather than appear inside arbitrary
# long sentences. `/clear` is outside this batch's three agreed commands and retains substring containment.
# Chinese ellipses …/⋯ and dashes ——/— are **internal sentence separators**, not merely trailing decoration:
# `同意……去执行` / `好吧——换个话题` matched old substring rules but would fail without splitting them.
# `/` is also a clause boundary. This is safe: `normalize_magic_command` already intercepts and returns
# literal magic words **before** this path (the first action in `_classify_magic_intent_with_rules`);
# the splitter sees only slashes used as free-text separators (`好吧/换个话题`).
_CLAUSE_SPLIT = re.compile(r"[，,。．.！!？?；;、：:…⋯‥—–―─\-/\s]+")

# ── Leading function words: **two allowlists**, with the narrow one for approve ──
#
# Intentionally use an allowlist rather than broad stripping plus vetoes. The previous version shared a broad table across commands,
# then vetoed non-approval wording; three consecutive Codex P1 rounds only made the holes appear covered: missing
# question marks, unpunctuated questions (`去執行嗎`), tentative proposals (`要不去執行`), then first-person
# intent (`我想去執行`). The cause is **reversed reasoning**, not insufficient vocabulary: prefixes changing
# imperatives into non-authorization form an open set no denylist can exhaust, as this file's approve comment already stated.
#
# Now approve strips only **neutral** prefixes and still requires the entire remainder to match an action phrase. Recognizing another
# approval wording requires explicitly adding a neutral prefix, a visible reviewable change, rather than forgetting a veto entry.
#
# **Multicharacter words must precede their initial character**. Regex alternatives match written order: placing `那` before `那么`
# strips only the first character in `那么停下来吧`, leaving `么` attached (`么停下来`),
# invalidating the whole criterion. The same applies to `快`/`快点` and `我`/`我想`; preserve this order when adding words.
#
# Neutral means stripping it **does not change who is authorized to do what**: connecting adverbs, **second-person** subjects,
# recipient phrases, politeness prefixes, and decisive adverbs. Decisive forms emphasize an imperative rather than propose one
# (`马上去执行` is a command; `不如去执行` a suggestion), and main already classifies them as approve.
#
# **First-person subjects are excluded** (我 / 咱 / 我们 / 我們 / 咱们 / 咱們). `我去執行` means
# the user will act **personally**, not authorize the agent; stripping `我` makes it identical to `去執行`.
# Retain second-person subjects because `你去执行吧` directly authorizes the agent. Whether stripping changes authorization
# into non-authorization is the sole inclusion criterion for these two tables; ask it for each added word.
_NEUTRAL_LEAD = (
    # Response-like prefixes (`好的去执行` without a separator). Include only **two or more characters**:
    # single-character 好 / 行 / 对 / 嗯 are too coarse as prefixes (`对方去执行` becomes `方去执行`,
    # while `行不行` is a suffix). They participate only as **response clauses**; see _APPROVE_COMPANIONS,
    # whose separator requirement better represents acknowledging first, then commanding.
    # Mixed-language response prefixes are common too: `OK去执行` / `okay去执行` matched the previous substring implementation.
    r"OKAY|Okay|okay|OK|Ok|ok|"
    r"没错|沒錯|没意见|沒意見|批准|允许|允許|同意|好的|好吧|行了|可以|"
    r"那么|那麼|快点|快點|就这么|就這麼|这就|這就|"
    r"帮我|幫我|帮忙|幫忙|给我|給我|替我|麻烦|麻煩|拜托|拜託|"
    r"劳驾|勞駕|有劳|有勞|烦请|煩請|"
    r"赶紧|趕緊|赶快|趕快|马上|馬上|立刻|立即|现在|現在|尽快|盡快|直接|"
    r"务必|務必|记得|記得|一定|放心|继续|繼續|"
    r"你们|你們|您们|您們|您|"
    r"那|就|先|快|请|請|你|妳"
)
# Only /stop and /new: tentative proposals (要不/不如/还是/干脆), first-person intent (我想/我要/想),
# and first-person subjects (我/咱/我们/咱们).
# These change **who intends to act and whether it is a proposal**, rather than emphasize imperatives. For stopping or topic changes,
# `不如换个话题`, `我想取消这个任务`, and `我们换个话题吧` are ordinary requests; for approval,
# `我想去執行` states intent, `我去執行` announces personal action, and `要不去執行` proposes an option,
# none authorizing the agent.
# Multicharacter entries precede single characters: `我想` / `我要` / `我们` before `我`.
_SOFT_LEAD = (
    # `要不要` must precede `要不然|要不`, or `要不要停下来` loses `要不` and becomes
    # `要停下来`. This is the fifth failure caused by multicharacter words following their own initial characters/prefixes
    # (那么, 快点, 我想, 你们, 要不要); preserve ordering when adding words.
    r"要不要|能不能|可不可以|要不然|要不|不如|还是|還是|干脆|乾脆|"
    # Written/polite interrogative requests belong only to the **broad** table: `能否去執行` seeks input rather than authorizes;
    # putting it in the neutral table makes approve accept an entire class of questions.
    # Longer first: `是否可以` / `是否能` must precede `是否`, or stripping leaves `可以停下来`.
    r"是否可以|是否能|是否|能否|可否|"
    # `请问` must belong to **this** table, rather than relying on neutral `请`: the combined regex is
    # `^(?:SOFT|NEUTRAL)+`, trying SOFT first so `请问` consumes both characters before `请`;
    # letting neutral `请` consume first leaves `问能不能停下来`, which can no longer match.
    # This is the eighth failure caused by multicharacter words following their own prefixes.
    r"请问|請問|"
    r"我想|我要|我们|我們|咱们|咱們|想|我|咱"
)
_CLAUSE_LEAD_NEUTRAL = re.compile(rf"^(?:{_NEUTRAL_LEAD})+")
_CLAUSE_LEAD_SOFT = re.compile(rf"^(?:{_SOFT_LEAD}|{_NEUTRAL_LEAD})+")

# ── Trailing particles: likewise **two allowlists** ──
# Multicharacter interrogative endings (好吗/行不行) precede single characters, as with _NEUTRAL_LEAD.
# Particles also have simplified/traditional variants: 囉/啰/咯/喽/嘍 and 呗/唄 must enter together, or identical
# traditional wording matches while simplified wording does not, precisely the defect this change series addresses.
# Strip neutral particles for both cases, but interrogative endings only for /stop and /new. `停下来行吗` is a common
# polite stop request, while `去執行嗎` is an approval **question**; stripping `嗎` makes the distinction impossible.
# Neutral endings exclude aspect marker `了`. `去執行了` reports already having executed; stripping `了`
# makes it indistinguishable from authorization `去執行`. Retain `好了`: `去执行好了` colloquially means go ahead and execute,
# an authorization. Entries `准了` / `準了` retain their built-in `了` through **literal matching**, not stripping.
_NEUTRAL_TAIL = (
    r"好了|吧|啊|呀|喔|哦|嘛|囉|啰|咯|喽|嘍|呗|唄|嘞|啦|一下|喵|"
    r"谢谢|謝謝|多谢|多謝|感谢|感謝|"
    # Sentence-final politeness phrases, like `谢谢`, already occur as leading words in `_NEUTRAL_LEAD`, but
    # `停下来，拜托了` places the same phrase at the end. `_command_clause` can strip only `了`, leaving
    # `拜托` as a command clause and causing the whole sentence to fail.
    # Include only four phrases actually used sentence-finally, rather than mirroring the entire leading table: `停下来，烦请` /
    # `换个话题，劳驾` are not natural Chinese, and including them only enlarges the table and blurs its criteria.
    # Boundary accounting: forms inserting a person inside, such as `拜托你了`, still fail (`拜托你` remains after stripping `了`).
    # Politeness + arbitrary person/intensifier is an open set we do not enumerate, for the same reason given for prefixes here.
    r"拜托|拜託|麻烦|麻煩|"
r"耶|唷|哟|喲|欸|诶|咧|哈|噢|呐|吶|呦|哒|噠|齁|捏|~|～"
)
# Only /stop and /new: interrogative endings + aspect marker `了`.
_SOFT_TAIL = (
    r"好不好|好吗|好嗎|行不行|行吗|行嗎|可以吗|可以嗎|怎么样|怎麼樣|吗|嗎|呢|了"
)
_TAIL_ALTERNATION = f"{_SOFT_TAIL}|{_NEUTRAL_TAIL}"
# Endings deliberately exist **only as token tuples, with no corresponding regex**. The sole correct
# stripping algorithm tries each option in `_clause_hits` below. Leaving a `(?:…)+$` regex nearby invites reuse
# and both pitfalls below. Likewise there is no generic `_normalize_clause` function:
# the sole criterion lives in `_clause_hits`.
#
# Never greedily strip a whole ending sequence or try only the ending selected by a regex. Two pitfalls:
#
# 1. Greedy stripping consumes characters belonging to allowlisted entries: `别找了吧` loses all `了吧`, becoming
#    `别找`, while the entry is `别找了`; an entirely natural sentence then cannot stop a task (Codex P2).
# 2. Trying only the regex-selected ending also fails: left-priority alternatives match `行吗` in `去执行吗`,
#    leaving `去执` by consuming the final character of `执行`. This is the reverse of prefix truncation,
#    and vocabulary ordering cannot solve it because `行吗` itself must remain supported.
#
# Instead try **all** matching endings at each step, checking the table after removing each one.
_NEUTRAL_TAIL_TOKENS = tuple(_NEUTRAL_TAIL.split("|"))
_TAIL_TOKENS = tuple(_TAIL_ALTERNATION.split("|"))

# **Decorative characters** at clause ends include ellipses …, dashes ——, quotes 「」『』“”, parentheses （）,
# emoji, and emoticon symbols. They are neither _CLAUSE_SPLIT separators nor particles, so
# `停下来…` / `换个话题👍` / `「停下來」` fail the allowlist. Such endings are extremely common
# in Chinese chat; /stop and /new otherwise strictly narrow old behavior, but this category causes widespread
# regressions. Strip the **complement** of word characters rather than enumerating symbols, an open set inevitably missed.
# Python 3 `\w` includes CJK, so this removes only punctuation, symbols, and emoji.
# Use `[\W_]`, not `\W`: Python's `\w` **includes underscore**, so `\W` cannot strip it.
# Markdown emphasis is common in chat, making `_停下来_` / `__停下來__` fail entirely;
# separated trailing markers `停下来 _` are worse, treating `_` itself as a command clause.
_DECORATION = re.compile(r"^[\W_]+|[\W_]+$")
# Approve uses **different** decoration stripping, only sentence-finally and from a **closed** punctuation set.
#
# Leading symbols carry meaning: `❌去執行` / `🚫去執行` prohibit execution, while `「去執行」` **mentions**
# the phrase rather than commands. Trailing symbols can also negate: `去執行❌` / `去執行🚫` mean do not execute too.
# Stripping `\W` (the complement of word characters) removes those symbols as decoration at either end, as this PR once did;
# traditional variants returned None on main, so this introduced a new hole.
#
# Negating symbols form an **open set** (❌✖✗🚫⛔🙅🆖🚷…), impossible to exhaust with a denylist, matching the prefix
# argument above. Approve therefore accepts only a table of punctuation with **explicitly no semantic meaning**;
# never strip emoji. The cost is losing `去执行👌` relative to old behavior, since this layer cannot distinguish 👌 from ❌.
# /stop and /new have smaller consequences (stop task / change topic), retaining broad `_DECORATION` stripping at both ends: `「停下來」` -> /stop.
# There is **only one** strict table here: a former broad tail-only `_DECORATION_TAIL` became unused after both approve
# branches switched to the strict table. Leaving it invites the next person to casually broaden behavior,
# reopening the negating-symbol hole just closed.
_DECORATION_TAIL_APPROVE = re.compile(r"[…⋯‥~～!！.。·・、,，\s]+$")

# Suffix-stripping candidates are prefixes of the original string, and particles may repeat (`去执行吧吧吧吧…`), so
# candidate count grows with suffix-chain length and each candidate requires slicing: quadratic on long input. Measured 20k-character
# input blocks the event loop for seconds. Since this classifier handles user input, enforce a hard upper bound; the longest table entry
# is only six characters, far below it for ordinary commands.
_MAX_PEEL_CANDIDATES = 32

# Question marks must **independently** veto approve: splitting consumes them as separators, leaving no trace regardless of suffix stripping
# (`去執行？` becomes `去執行`). Other non-authorization forms (interrogative endings, affirmative-negative questions, tentative proposals, first-person
# intent) are **structurally** excluded by the two narrow allowlists, without enumerating vetoes.
# This distinction is intentional: denylists cannot exhaust these cases, as this file's approve comment already argued;
# broadening allowlists requires explicit vocabulary additions.
_APPROVE_QUESTION_MARK = re.compile(r"[?？]")


def _clause_hits(
    clause: str,
    table: frozenset,
    *,
    strip_tail: bool = True,
    lead_re: Any = None,
    tail_tokens: Any = None,
    decoration_re: Any = None,
) -> bool:
    """Match a clause against a table: raw, lead-stripped, then peeled tails.

    The widening happens here rather than by deriving stripped spellings INTO the
    table, and particles come off one at a time. See the notes below.
    """
    # Keep tables literal: widen the **lookup**, not the table. Some entries include particles
    # normalization would consume (别找了->别找, 删吧->删). The previous version instead closed the table over
    # normalized forms, adding single-character 删 / 准 to approve, so `帮我删一下` (a **new
    # deletion request**, not a response to an awaiting action) became /daemon approve.
    #
    # Strip particles **one at a time, checking after each**, never all at once: `别找了吧` fully stripped
    # yields `别找` (absent), while incremental stripping first yields `别找了` (present) and stops.
    #
    # Lead-only is **a separate level**: `现在别找了` reveals allowlisted `别找了` only after prefix stripping.
    text = str(clause or "").strip()
    if not text:
        return False
    lead = lead_re if lead_re is not None else _CLAUSE_LEAD_SOFT
    tokens = tail_tokens if tail_tokens is not None else _TAIL_TOKENS
    decoration = decoration_re if decoration_re is not None else _DECORATION
    seen: set = set()
    pending = []
    # Three starting levels: unchanged / strip outer decoration / additionally strip leading function words.
    bare = decoration.sub("", text).strip()
    # Latin prefix capitalization (OK / okay) is an open set (oK / oKaY / OKay…); enumeration inevitably misses variants.
    # Try one additional wholly lowercased candidate; Chinese is unaffected.
    lowered = bare.lower()
    # **Strip decoration again** after leading function words. Prefixes and wrappers coexist: stripping `请` from `请「停下来」`
    # leaves `「停下来」` with quotes, still missing the table. One decoration pass handles
    # only outermost decoration, not nesting with prefixes outside decoration.
    def _peel(source: str) -> list[str]:
        without_lead = lead.sub("", source).strip()
        return [without_lead, decoration.sub("", without_lead).strip()]

    starts = [text, bare, *_peel(bare)]
    if lowered != bare:
        starts += [lowered, *_peel(lowered)]
    for candidate in starts:
        if candidate and candidate not in seen:
            if candidate in table:
                return True
            seen.add(candidate)
            pending.append(candidate)
    if not strip_tail:
        return False
    while pending and len(seen) < _MAX_PEEL_CANDIDATES:
        current = pending.pop()
        for token in tokens:
            if len(current) <= len(token) or not current.endswith(token):
                continue
            # Removing a particle may expose decoration again (`去执行吧…` -> `去执行吧` -> …),
            # so strip outer decoration again at every step.
            peeled = decoration.sub("", current[: -len(token)]).strip()
            if not peeled or peeled == current:
                continue
            if peeled in table:
                return True
            if peeled not in seen:
                seen.add(peeled)
                pending.append(peeled)
    return False


# These three tables match actual typed characters; simplified/traditional variants use different code points and must enter together.
# Vocabulary is **deliberately unchanged**: every entry is copied literally from the previous two branches (substring and exact-sentence tables).
# Broader responses such as 可以 / 好 / 好的 / 行 were **not** added;
# expanding approval coverage requires separate assessment, outside this narrowing change.
#
# Approve's two branches use **different criteria**; do not merge their tables.
#
# Bare affirmations (同意 / 我同意 / 没问题 / 沒問題) previously used **whole-sentence exact matching**, so
# `没问题喵~` / `同意~` / `沒問題喔` / `不如同意` / `那就同意` all returned None on main.
# Normalizing their ends would approve all of them, perversely expanding a high-risk command while intending to narrow it.
# Proactive turns are especially vulnerable: task_executor replaces intent with the catgirl's own dialogue
# before classification, and her habitual `没问题喵~` would self-approve.
# Thus bare affirmations require **the entire unchanged clause**, stripping nothing.
_APPROVE_AFFIRMATIONS = frozenset({"同意", "我同意", "没问题", "沒問題"})

# **Response clauses** can accompany authorization but cannot authorize alone.
#
# Common Chinese authorization combines response + command clauses: `好的，去执行` / `没错，去执行` /
# `可以，删吧`. Previously substring `去执行` matched; after fail-closed all-clause checking,
# `好的` belongs to no table, invalidating the whole sentence. This is **pure recall loss, not narrowing**: `没错，
# 去执行` is explicitly identified above as a false rejection caused by denylists, and allowlists wrongly reject it
# too, contradicting the comments.
#
# But these **cannot** become bare affirmations: standalone `好的` / `可以` previously returned None (the old exact
# table contained only four entries), so treating them as authorization expands approval. Therefore:
#   A response clause counts only when **the same sentence includes at least one action phrase or bare affirmation clause**.
# `好的，去执行` -> pass; `好的` -> None; `好的，好的` -> None.
# Like bare affirmations, response clauses require **the entire unchanged clause**; `好的喵~` does not count.
_APPROVE_COMPANIONS = frozenset({
    "好", "好的", "好吧", "行", "行了", "可以", "嗯", "对", "對", "没错", "沒錯",
    "没意见", "沒意見", "批准", "允许", "允許",
    # OK / okay are excluded here: they already belong to _NEUTRAL_LEAD, and standalone occurrences use the
    # neutral-prefix-only companion case above. Duplicate registration adds another criterion and two places to drift.
})

# Action-phrase branch: these previously triggered as **substrings** anywhere in a sentence, so stripping their leading function words
# cannot exceed old recall. Traditional entries are new in this batch (the old table had none); adding them under whole-clause
# matching does not expand exposure, addressing the explicitly intended gap.
_APPROVE_ACTIONS = frozenset({
    "删吧", "刪吧", "准了", "準了",
    "去执行", "去執行", "去执行吧", "去執行吧",
    "没问题去执行", "沒問題去執行",
})
# /stop triggers have **two levels**, classified by whether the wording has other everyday conversational meanings.
#
# Phrases explicitly addressing a working agent are rare in everyday conversation, so wording alone suffices.
_STOP_ADDRESSED = frozenset({
    "取消这个任务", "取消這個任務", "取消这个搜索", "取消這個搜尋",
    "算了别查了", "算了別查了", "停止搜索", "停止搜尋",
})
# Everyday imperatives: whole-clause matching excludes narration (`雨停下来了` misses) but cannot distinguish users
# telling the **catgirl herself** to `停下来` from identical words directed at the agent.
# Dispatch therefore **additionally requires an actually running openclaw task** for this level; see channels/openclaw.py.
_STOP_AMBIGUOUS = frozenset({
    "停下来", "停下來", "快停下来", "快停下來", "别找了", "別找了",
})
_STOP_CLAUSES = _STOP_ADDRESSED | _STOP_AMBIGUOUS

# Only these two commands may be inferred from **free text**. `/new` and `/clear` require literal commands.
# This filter protects the LLM path: prompts restrict it to these two, but cannot enforce model behavior. Both excluded
# commands have irreversible effects (replacing upstream session pointers / clearing upstream context), so model obedience alone is insufficient.
_FREE_TEXT_INFERABLE = frozenset({"/stop", "/daemon approve"})


def _drop_commands_not_inferable_from_free_text(result: Dict[str, Any]) -> Dict[str, Any]:
    """Veto an inferred command that free text is not allowed to reach."""
    if not isinstance(result, dict) or not result.get("is_magic_intent"):
        return result
    command = OpenClawAdapter.normalize_magic_command(result.get("command"))
    if command in _FREE_TEXT_INFERABLE:
        return result
    logger.info(
        "[OpenClaw] magic intent %r vetoed: not inferable from free text",
        result.get("command"),
    )
    return None



def _approve_clause_kind(clause: str) -> Optional[str]:
    """Classify one clause for /daemon approve: action, affirmation, or companion.

    The three cannot share one judgement — see the notes above
    _APPROVE_AFFIRMATIONS and _APPROVE_COMPANIONS.
    """
    text = str(clause or "").strip()
    if text in _APPROVE_AFFIRMATIONS:
        return "affirmation"
    # Response clauses undergo **full** normalization (decoration + prefixes + particles): `好的喵~，去执行` /
    # `OK，去执行` both approved previously. Unlike bare affirmations, response clauses alone
    # never authorize (the any(action|affirmation) check below), so broadening their forms does not expand
    # approval coverage; allowing bare `没问题喵~` instead enables proactive-turn self-approval.
    #
    # The **narrow** prefix table is a conservative choice, not a safety requirement: differential testing 140,239 inputs with the broad table
    # still found zero expansion on simplified Chinese, since every response + action combination already matched old substrings.
    # Do not test that this line must use the narrow table: it is an equivalent mutation such tests cannot constrain.
    # Standalone **neutral leading words** also count as response clauses: `请，去执行` / `麻烦，去执行` /
    # `那，去执行` matched old substrings, while the same words attached directly (`请去执行`) have always worked.
    # They were already judged safe to strip without changing who authorizes what; standing alone changes nothing either.
    # As with other response clauses, they never authorize alone; the sentence still needs an action phrase or bare affirmation.
    # Use the **strict** decoration table, matching response/action lookup below: ❌ negates the response in `可以❌`,
    # whereas broad stripping removes it as decoration and classifies the whole sentence as approval.
    lead_only = _CLAUSE_LEAD_NEUTRAL.sub(
        "", _DECORATION_TAIL_APPROVE.sub("", text).strip()
    ).strip()
    if text and not lead_only:
        return "companion"
    if _clause_hits(
        text,
        _APPROVE_COMPANIONS,
        strip_tail=True,
        lead_re=_CLAUSE_LEAD_NEUTRAL,
        tail_tokens=_NEUTRAL_TAIL_TOKENS,
        # Response clauses also use **strict** decoration. Earlier broad stripping was justified because response clauses alone
        # never authorize, but negation can apply to them too: ❌ negates the response in `可以❌，去執行`,
        # yet the sentence still approved. This layer cannot distinguish 👌 from ❌, so retain the entire class.
        # Cost: lose `好的👌，去执行` relative to previous behavior.
        decoration_re=_DECORATION_TAIL_APPROVE,
    ):
        return "companion"
    return "action" if _approve_clause_hits(text) else None


def _approve_clause_hits(clause: str) -> bool:
    """Whether one clause is an approval **action phrase**.

    Action phrases go through the normal lead/tail probing; bare affirmations
    and companions must match verbatim and are handled by _approve_clause_kind.
    """
    text = str(clause or "").strip()
    # Action phrases may strip their ends: they previously triggered as **substrings** anywhere, so normalization
    # cannot exceed previous recall. Bare affirmations cannot, since they previously required exact whole sentences.
    # Strip only the **neutral** set, never tentative proposals, first-person intent, or interrogative endings; otherwise
    # `去執行` (authorization) becomes indistinguishable from `我想去執行` (intent), `要不去執行` (proposal), or `去執行嗎` (question).
    return _clause_hits(
        text,
        _APPROVE_ACTIONS,
        strip_tail=True,
        lead_re=_CLAUSE_LEAD_NEUTRAL,
        tail_tokens=_NEUTRAL_TAIL_TOKENS,
        decoration_re=_DECORATION_TAIL_APPROVE,
    )


def _command_clause(clauses: list) -> str:
    """The trailing clause that actually carries the command, for /stop and /new.

    Walks back over segments that are nothing but particles or decoration.
    """
    # Whitespace also separates clauses, so `停下来 吧` / `停下來 👍` / `换个话题 喵` split particles into
    # standalone final clauses. Final-clause matching then sees only `吧` / `👍` / `喵` and fails, unlike old
    # substring matching. Skip backward over tails containing only particles/decoration.
    # **Do not use for approve**: its all-clause fail-closed rule would discard `吧` in `同意 吧`,
    # exposing bare affirmation `同意` and approving a case that previously returned None, expanding authorization.
    # Explicit tradeoff: `去执行 吧` with internal whitespace previously approved but now returns None.
    # These outcomes are mutually exclusive; choose fail-closed behavior.
    for clause in reversed(clauses):
        stripped = _DECORATION.sub("", clause).strip()
        if not stripped:
            continue
        # Try **all** matching suffixes, longest first, rather than only the first matching table entry:
        # `_TAIL_TOKENS` places `了` before `好了`, so
        # `换个话题 好了` would reduce its tail to `好`, no longer recognized as particles only;
        # treating that tail as a command clause invalidates the sentence, the same pitfall as in _clause_hits.
        # A hard upper bound is **necessary**, not merely a worst-case guard: every step runs
        # _DECORATION over the whole string; unbounded tests already time out at 600s on 120,000 particles.
        # rule_magic_command handles user input and is called synchronously by the cheap preliminary gate.
        peeled = stripped
        for _ in range(_MAX_PEEL_CANDIDATES):
            matches = [t for t in _TAIL_TOKENS if peeled.endswith(t)]
            if not matches:
                break
            longest = max(matches, key=len)
            peeled = peeled[: -len(longest)] if len(peeled) > len(longest) else ""
            peeled = _DECORATION.sub("", peeled).strip()
            if not peeled:
                break
        if peeled:
            return clause
    return clauses[-1] if clauses else ""


def _split_clauses(text: str) -> list[str]:
    """Split an utterance into raw, non-empty clauses.

    Normalization happens per lookup (see _clause_hits), not here — approve and
    stop/new normalize differently.
    """
    parts = (p.strip() for p in _CLAUSE_SPLIT.split(str(text or "").strip()))
    return [p for p in parts if p]


class OpenClawAdapter:
    AUTH_ERROR_STATUS_CODES = frozenset({401, 403})

    def __init__(self) -> None:
        self.base_url = DEFAULT_OPENCLAW_URL
        self.process_url = f"{DEFAULT_OPENCLAW_URL}{QWENPAW_PROCESS_ENDPOINT_PATH}"
        self.responses_url = f"{DEFAULT_OPENCLAW_URL}{QWENPAW_RESPONSES_ENDPOINT_PATH}"
        self.health_url = f"{DEFAULT_OPENCLAW_URL}{QWENPAW_HEALTH_ENDPOINT_PATH}"
        self.version_url = f"{DEFAULT_OPENCLAW_URL}{QWENPAW_VERSION_ENDPOINT_PATH}"
        self.console_chat_url = f"{DEFAULT_OPENCLAW_URL}{QWENPAW_CONSOLE_CHAT_ENDPOINT_PATH}"
        self.api_variant = "unknown"
        self.timeout = DEFAULT_TIMEOUT
        self.http_timeout = max(DEFAULT_TIMEOUT + 15.0, DEFAULT_TIMEOUT)
        self.auth_token = ""
        self.default_sender_id = "neko_user"
        self.default_channel = DEFAULT_OPENCLAW_CHANNEL
        self.last_error: Optional[str] = None
        self._session_lock = threading.Lock()
        self._session_cache: Optional[Dict[str, str]] = None
        self.reload_config()

    def reload_config(self) -> None:
        try:
            cfg = get_config_manager().get_core_config()
            cfg = cfg if isinstance(cfg, dict) else {}
        except Exception as exc:
            logger.debug("[OpenClaw] Failed to load config, using defaults: %s", exc)
            cfg = {}

        raw_url = (
            cfg.get("QWENPAW_URL")
            or cfg.get("qwenpawUrl")
            or cfg.get("OPENCLAW_URL")
            or cfg.get("openclawUrl")
        )
        if isinstance(raw_url, str) and raw_url.strip():
            self.base_url, self.process_url, self.responses_url, self.health_url = _resolve_qwenpaw_urls(raw_url)
        else:
            self.base_url, self.process_url, self.responses_url, self.health_url = _resolve_qwenpaw_urls(DEFAULT_OPENCLAW_URL)
        self.version_url = f"{self.base_url}{QWENPAW_VERSION_ENDPOINT_PATH}"
        self.console_chat_url = f"{self.base_url}{QWENPAW_CONSOLE_CHAT_ENDPOINT_PATH}"

        self.timeout = _normalize_timeout(
            cfg.get(
                "QWENPAW_TIMEOUT",
                cfg.get("qwenpawTimeout", cfg.get("OPENCLAW_TIMEOUT", cfg.get("openclawTimeout", DEFAULT_TIMEOUT))),
            ),
            DEFAULT_TIMEOUT,
        )
        self.http_timeout = max(self.timeout + 15.0, self.timeout)
        raw_auth_token = (
            cfg.get("QWENPAW_AUTH_TOKEN")
            or cfg.get("qwenpawAuthToken")
            or cfg.get("OPENCLAW_AUTH_TOKEN")
            or cfg.get("openclawAuthToken")
            or cfg.get("authToken")
        )
        self.auth_token = (
            raw_auth_token.strip()
            if isinstance(raw_auth_token, str) and raw_auth_token.strip()
            else ""
        )
        raw_sender = (
            cfg.get("QWENPAW_DEFAULT_SENDER_ID")
            or cfg.get("qwenpawDefaultSenderId")
            or cfg.get("OPENCLAW_DEFAULT_SENDER_ID")
            or cfg.get("openclawDefaultSenderId")
        )
        self.default_sender_id = raw_sender.strip() if isinstance(raw_sender, str) and raw_sender.strip() else "neko_user"
        raw_channel = (
            cfg.get("QWENPAW_CHANNEL")
            or cfg.get("qwenpawChannel")
            or cfg.get("OPENCLAW_CHANNEL")
            or cfg.get("openclawChannel")
        )
        self.default_channel = (
            raw_channel.strip()
            if isinstance(raw_channel, str) and raw_channel.strip()
            else DEFAULT_OPENCLAW_CHANNEL
        )

    def _build_request_headers(self) -> Dict[str, str]:
        if not self.auth_token:
            return {}
        return {
            "x-openclaw-token": self.auth_token,
            "Authorization": f"Bearer {self.auth_token}",
        }

    def is_available(self) -> Dict[str, Any]:
        self.reload_config()
        try:
            with httpx.Client(
                timeout=httpx.Timeout(3.0, connect=1.5),
                headers=self._build_request_headers(),
                proxy=None,
                trust_env=False,
            ) as client:
                candidates = (
                    (self.health_url, "legacy"),
                    (self.version_url, "v2"),
                ) if self.api_variant == "legacy" else (
                    (self.version_url, "v2"),
                    (self.health_url, "legacy"),
                )
                response = None
                response_url = candidates[0][0]
                last_request_error: Optional[httpx.RequestError] = None
                for checked_url, variant in candidates:
                    try:
                        response = client.get(checked_url)
                    except httpx.RequestError as exc:
                        last_request_error = exc
                        continue
                    response_url = checked_url
                    if response.is_success:
                        if variant == "v2":
                            try:
                                version_payload = response.json()
                            except Exception:
                                version_payload = None
                            if not isinstance(version_payload, dict) or not version_payload.get("version"):
                                continue
                        self.api_variant = variant
                        self.last_error = None
                        return {
                            "enabled": True,
                            "ready": True,
                            "reasons": [f"OpenClaw(QwenPaw) reachable ({checked_url})"],
                            "status_code": response.status_code,
                            "provider": "qwenpaw",
                        }
                if response is None and last_request_error is not None:
                    raise last_request_error
                status_code = response.status_code if response is not None else 503
                self.last_error = f"HTTP {status_code}"
                return {
                    "enabled": True,
                    "ready": False,
                    "reasons": [f"OpenClaw(QwenPaw) responded {status_code} ({response_url})"],
                    "status_code": status_code,
                    "provider": "qwenpaw",
                }
        except Exception as exc:
            self.last_error = str(exc)
            return {
                "enabled": True,
                "ready": False,
                "reasons": [f"OpenClaw(QwenPaw) unavailable: {exc}"],
                "provider": "qwenpaw",
            }

    def _load_session_cache(self) -> Dict[str, str]:
        if self._session_cache is None:
            cfg = get_config_manager().load_json_config(OPENCLAW_SESSION_CACHE_FILE, default_value={})
            self._session_cache = cfg if isinstance(cfg, dict) else {}
        return self._session_cache

    def _save_session_cache(self) -> None:
        if self._session_cache is None:
            return
        get_config_manager().save_json_config(OPENCLAW_SESSION_CACHE_FILE, self._session_cache)

    @staticmethod
    def _build_session_key(role_name: Optional[str], sender_id: str) -> str:
        del role_name
        sender = str(sender_id or "").strip() or "neko_user"
        return f"user::{sender}"

    @staticmethod
    def _iter_legacy_session_keys(role_name: Optional[str], sender_id: str) -> list[str]:
        sender = str(sender_id or "").strip() or "neko_user"
        role = str(role_name or "").strip() or "__default_role__"
        return [
            f"{role}::{sender}",
            f"__default_role__::{sender}",
        ]

    def peek_persistent_session_id(self, *, role_name: Optional[str], sender_id: str) -> str:
        """Current persistent session id for this sender, or "" — never creates one.

        Read-only counterpart of get_or_create_persistent_session_id, for callers
        that need to correlate an existing record with the live session instead of
        starting one as a side effect of asking.
        """
        with self._session_lock:
            session_id, _ = self._get_cached_session_id(
                role_name=role_name,
                sender_id=sender_id,
            )
            return session_id or ""

    def _get_cached_session_id(self, *, role_name: Optional[str], sender_id: str) -> tuple[Optional[str], str]:
        cache = self._load_session_cache()
        session_key = self._build_session_key(role_name, sender_id)
        session_id = str(cache.get(session_key) or "").strip()
        if session_id:
            return session_id, session_key

        for legacy_key in self._iter_legacy_session_keys(role_name, sender_id):
            legacy_session = str(cache.get(legacy_key) or "").strip()
            if not legacy_session:
                continue
            cache[session_key] = legacy_session
            self._save_session_cache()
            logger.info(
                "[OpenClaw] Migrated legacy session mapping: legacy=%s sender=%s session=%s",
                legacy_key,
                sender_id,
                legacy_session,
            )
            return legacy_session, session_key
        return None, session_key

    def get_or_create_persistent_session_id(self, *, role_name: Optional[str], sender_id: str) -> str:
        with self._session_lock:
            cache = self._load_session_cache()
            session_id, session_key = self._get_cached_session_id(
                role_name=role_name,
                sender_id=sender_id,
            )
            if session_id:
                return session_id
            session_id = uuid.uuid4().hex
            cache[session_key] = session_id
            self._save_session_cache()
            logger.info(
                "[OpenClaw] Created persistent user session: sender=%s session=%s",
                sender_id,
                session_id,
            )
            return session_id

    def reset_persistent_session_id(self, *, role_name: Optional[str], sender_id: str) -> str:
        with self._session_lock:
            cache = self._load_session_cache()
            _, session_key = self._get_cached_session_id(
                role_name=role_name,
                sender_id=sender_id,
            )
            session_id = uuid.uuid4().hex
            cache[session_key] = session_id
            self._save_session_cache()
            logger.info(
                "[OpenClaw] Reset persistent user session: sender=%s session=%s",
                sender_id,
                session_id,
            )
            return session_id

    @staticmethod
    def normalize_magic_command(command: Any) -> Optional[str]:
        raw = str(command or "").strip()
        if not raw:
            return None
        lowered = raw.lower()
        if lowered in {"/clear", "clear"}:
            return "/clear"
        if lowered in {"/new", "new"}:
            return "/new"
        if lowered in {"/stop", "stop"}:
            return "/stop"
        if lowered in {"/daemon approve", "daemon approve", "/approve", "approve"}:
            return "/daemon approve"
        return raw if raw in MAGIC_COMMANDS else None

    # Magic commands **typed by users** must start with `/` and be **bare**: the entire input is that
    # command with nothing around it. Apply this strict criterion only when recognizing literal user commands;
    # keep `normalize_magic_command` permissive because other callers pass **internal command values**
    # (run_magic_command arguments, dialogue lookup, command fields parsed from LLM output), where
    # tightening would cause false rejections.
    #
    # Exclude slashless bare `stop` / `new` / `clear` / `approve`, which are **ordinary English words**;
    # all nine remaining false hits across eight locales came from them ("Stop" / "Clear" button labels).
    _TYPED_MAGIC_COMMANDS = {
        "/clear": "/clear",
        "/new": "/new",
        "/stop": "/stop",
        "/daemon approve": "/daemon approve",
        "/approve": "/daemon approve",
    }

    @staticmethod
    def parse_typed_magic_command(user_text: Any) -> Optional[str]:
        """The magic command a user typed verbatim, or None.

        Strict on purpose: leading "/" required, and the whole utterance must be
        the command — no bare word, no prefix, no trailing text.
        """  # noqa: DOCSTRING_CJK
        raw = str(user_text or "").strip()
        if not raw.startswith("/"):
            return None
        return OpenClawAdapter._TYPED_MAGIC_COMMANDS.get(raw.lower())

    @staticmethod
    def get_magic_command_feedback(command: str) -> str:
        normalized = OpenClawAdapter.normalize_magic_command(command) or ""
        return MAGIC_COMMAND_REACTIONS.get(normalized, "收到指令了喵！")

    @staticmethod
    def get_magic_command_task_description(command: str) -> str:
        normalized = OpenClawAdapter.normalize_magic_command(command) or ""
        return MAGIC_COMMAND_TASK_DESCRIPTIONS.get(normalized, "执行 QwenPaw 魔法命令")

    async def _classify_magic_intent_with_llm(self, user_text: str) -> Optional[Dict[str, Any]]:
        try:
            cfg = await get_config_manager().aget_model_api_config("summary")
        except Exception as exc:
            logger.debug("[OpenClaw] Failed to load summary model config for magic intent: %s", exc)
            return None

        model = str((cfg or {}).get("model") or "").strip()
        base_url = str((cfg or {}).get("base_url") or "").strip()
        api_key = str((cfg or {}).get("api_key") or "").strip()
        if not model or not base_url:
            return None

        llm = None
        try:
            llm = await create_chat_llm_async(
                model=model,
                base_url=base_url,
                api_key=api_key or None,
                temperature=0,
                max_completion_tokens=OPENCLAW_MAGIC_INTENT_MAX_TOKENS,
                max_retries=0,
                # Omit extra_body (different from extra_body=None), letting the factory infer provider-specific
                # thinking-disable syntax from the model name. This user-facing path has a 10s window; thinking models (GLM, Kimi,
                # Doubao, official DeepSeek V4, etc., with thinking enabled by default) almost inevitably consume it before falling back to rules,
                # wasting dispatch latency. Classification itself is a binary choice needing no thinking.
                # extra_body=None **skips** factory inference and preserves native model behavior, the opposite of the requirement.
                timeout=10,  # quick magic-intent classification on the user path
                provider_type=(cfg or {}).get("provider_type"),
            )
            response = await llm.ainvoke(
                [
                    {"role": "system", "content": MAGIC_INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": str(user_text or "").strip()},
                ]
            )
            parsed = robust_json_loads(_extract_json_block(response.content))
        except Exception as exc:
            logger.debug("[OpenClaw] Magic intent LLM classify failed, fallback to rules: %s", exc)
            return None
        finally:
            if llm is not None:
                try:
                    await llm.aclose()
                except Exception:
                    logger.debug("[OpenClaw] Failed to close magic intent LLM client", exc_info=True)

        if not isinstance(parsed, dict):
            return None
        normalized = self.normalize_magic_command(parsed.get("command"))
        if not parsed.get("is_magic_intent") or not normalized:
            return {"is_magic_intent": False, "command": None, "source": "llm"}
        return {"is_magic_intent": True, "command": normalized, "source": "llm"}

    @staticmethod
    def _classify_magic_intent_with_rules(user_text: str) -> Dict[str, Any]:
        text = str(user_text or "").strip()
        normalized = OpenClawAdapter.parse_typed_magic_command(text)
        if normalized:
            return {"is_magic_intent": True, "command": normalized, "source": "rule"}

        lowered = text.lower()
        if not lowered:
            return {"is_magic_intent": False, "command": None, "source": "rule"}

        # Prioritize precision: keep vocabulary conservative rather than expanding aggressively.
        # Like the tables below, this matches actual user-typed characters; simplified/traditional forms use different code points and must
        # enter together. Simplified-only entries make this suppression nonexistent for traditional Chinese users.
        if any(token in lowered for token in _HIGH_PRECISION_NON_MAGIC):
            return {"is_magic_intent": False, "command": None, "source": "rule"}

        # `/clear` and `/new` **no longer trigger from free text**, requiring literal commands
        # intercepted and returned immediately by `parse_typed_magic_command` at the beginning of this function.
        # Their removal follows three measured factors combined:
        #   · Highest false-trigger rate: 6/14 topic-change phrasings in ordinary chat matched, all referring to
        #     **chat topics**, not agent sessions. In Chinese corpora, /new triggers occur 4.3 times as frequently as /stop
        #     (30 versus 7 in 3.04 million characters).
        #   · No usable state: neither command has local preconditions to check, unlike /stop's running
        #     task or approve's recently completed task, leaving no state-based guard.
        #   · Irreversible consequences: /new overwrites the **only** upstream session pointer, without backup or restoration interface;
        #     subsequent /stop addresses the new session, leaving actual work in the old session **unstoppable**,
        #     while local state still reports canceled.
        # Users saying change the topic almost always mean chat, not resetting the agent session. Resetting requires typing the command.
        clauses = _split_clauses(text)
        if not clauses:
            return {"is_magic_intent": False, "command": None, "source": "rule"}

        # Approve requires **every clause** to be allowlisted (fail-closed); the other two examine only
        # the **last clause** (imperative ending). This asymmetry follows consequence severity: approve makes upstream
        # execute a high-risk action, while /stop stops a task and /new changes topic.
        #
        # Visible distinction: `我不同意，去执行` returns None for approve because one clause is unlisted;
        # last-clause-only matching would approve it. Conversely `我还没同意，停止搜索` must remain /stop:
        # its first clause sets context, while the final clause commands.
        #
        # Known unresolved limitation: `停下来，我自己来` (command followed by explanation) previously matched
        # /stop but now returns None. It is **indistinguishable by any clause-position criterion** from
        # narrative `停下来，这是我当时唯一的念头`: both imperatives occur first. Substring matching incidentally accepts
        # the former at the cost of accepting the latter. Distinguishing them requires semantics beyond this layer.
        # Any question mark vetoes approve: splitting consumes it (`去執行？` -> `去執行`),
        # leaving narrow allowlists blind. Other non-authorization forms (interrogative endings / affirmative-negative questions / proposals / first-person intent)
        # are structurally excluded by _approve_clause_hits' narrow prefix/suffix tables rather than enumerated here.
        # Every clause must be classified (fail-closed), with **at least one** action phrase or bare affirmation;
        # response clauses alone (`好的` / `好的，好的` -> None) lack substantive authorization.
        # Bare affirmations count only when **they are the entire sentence**, matching original input character for character.
        # _APPROVE_AFFIRMATIONS forbids normalization of bare affirmations, but **clause splitting normalizes them
        # implicitly**: `同意。` / `同意！` / `同意……` / `我同意——` each reduce to
        # a single affirmation clause and pass exact matching. All returned None on main (whole-sentence exact
        # matching rejected punctuation), so this expands simplified-Chinese coverage precisely for hesitant/incomplete
        # utterances; `同意……` expresses uncertainty.
        # Keep splitting with action clauses (`同意……去执行` still authorizes); require unchanged input only when the sole clause is a bare affirmation.
        bare_affirmation_only = (
            len(clauses) == 1
            and clauses[0] in _APPROVE_AFFIRMATIONS
            and text.strip() != clauses[0]
        )
        approve_kinds = [_approve_clause_kind(c) for c in clauses]
        # Multiple clauses must include an **action** clause; response + bare affirmation alone is insufficient.
        # Recall restored by this narrowing change is response + **action** (`好的，去执行`);
        # `好的，同意` / `嗯，同意` returned None on main, matching neither an exact whole sentence nor
        # 删吧/准了/去执行 substrings. Including them would expand coverage without authorization.
        needs_action = len(clauses) > 1
        if (
            not _APPROVE_QUESTION_MARK.search(text)
            and not bare_affirmation_only
            and all(approve_kinds)
            and any(
                kind == "action" or (kind == "affirmation" and not needs_action)
                for kind in approve_kinds
            )
        ):
            return {"is_magic_intent": True, "command": "/daemon approve", "source": "rule"}
        # Skip particle/decoration-only tails when selecting the final clause; see _command_clause.
        command_clause = _command_clause(clauses)
        # Taiwan uses `搜尋` rather than `搜索`, so the traditional entry is not a direct character conversion of `搜索`.
        if _clause_hits(command_clause, _STOP_CLAUSES):
            return {"is_magic_intent": True, "command": "/stop", "source": "rule"}

        return {"is_magic_intent": False, "command": None, "source": "rule"}

    @staticmethod
    def stop_trigger_tier(user_text: str) -> Optional[str]:
        """Which `/stop` tier an utterance matched: "addressed", "ambiguous", None.

        Pure function on purpose. The tier decides whether the dispatcher demands
        corroborating state (an actually running task), and that state lives in
        agent_server — brain must not reach into it, so brain answers "which kind
        of phrasing was this" and lets the caller combine it with what it knows.

        A literal magic word returns None: typing the command is unambiguous and
        must never be gated.
        """  # noqa: DOCSTRING_CJK
        text = str(user_text or "").strip()
        if not text or OpenClawAdapter.parse_typed_magic_command(text):
            return None
        clauses = _split_clauses(text)
        if not clauses:
            return None
        # The unambiguous tier scans **all** clauses, not just the last: `取消这个任务，停下来` starts with explicit
        # cancellation and ends ambiguously. Last-clause-only matching yields ambiguous and drops it without corroboration after timeout/restart/TTL
        # expiration, despite this being exactly the wording that should pass.
        # Whole-sentence scanning is safe here: tiering **does not decide whether /stop dispatches** (the classifier's last-clause criterion does),
        # only whether state corroboration is needed. `我说了停止搜索，然后他就走了` returns None at classification,
        # never reaching here.
        if any(_clause_hits(clause, _STOP_ADDRESSED) for clause in clauses):
            return "addressed"
        if _clause_hits(_command_clause(clauses), _STOP_AMBIGUOUS):
            return "ambiguous"
        return None

    @staticmethod
    def rule_magic_command(user_text: str) -> Optional[str]:
        """Public zero-LLM magic-command detector: the command a rule match would
        dispatch, or None. Wraps the rule classifier so callers that need a
        no-LLM magic-word check (e.g. the analyzer pre-gate) need not reach into
        the private helper or pay the LLM path. Covers both exact magic words and
        the natural-language phrase list (cancel-task, change-topic, approve, …)."""
        result = OpenClawAdapter._classify_magic_intent_with_rules(user_text)
        if isinstance(result, dict) and result.get("is_magic_intent"):
            return result.get("command")
        return None

    async def classify_magic_intent(self, user_text: str) -> Dict[str, Any]:
        text = str(user_text or "").strip()
        if not text:
            return {"is_magic_intent": False, "command": None, "source": "empty"}

        # Classify literal commands **first**, bypassing the LLM. Typed /stop is unambiguous;
        # a small model has no reason to review it. Previously the LLM path always ran
        # first, and any dict result, even not-a-command, prevented rules from running, causing
        # typed /stop to be **silently discarded**. Measured with an always-not-magic LLM stub:
        # /stop, stop, /new, /clear, and /daemon approve all disappeared.
        #
        # This also fixes false rejection by the free-text filter below: /new and /clear require literal commands,
        # but typed commands previously traversed the LLM and returned as inferred free text,
        # getting rejected too. They no longer reach that layer.
        literal = OpenClawAdapter.parse_typed_magic_command(text)
        if literal:
            return {"is_magic_intent": True, "command": literal, "source": "literal"}

        llm_result = await self._classify_magic_intent_with_llm(text)
        if isinstance(llm_result, dict):
            # After vetoing an LLM result, **fall back to rules**, rather than concluding not-a-command immediately. If the LLM
            # misclassifies `取消这个任务` as /new, vetoing the destructive command is correct, but the input
            # is a valid /stop recognized without LLM calls; terminating locally would discard legitimate cancellation too.
            vetoed = _drop_commands_not_inferable_from_free_text(llm_result)
            if vetoed is not None:
                return vetoed
        return self._classify_magic_intent_with_rules(text)

    async def stop_running(
        self,
        *,
        sender_id: Optional[str] = None,
        session_id: Optional[str] = None,
        role_name: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.last_error = None
        sender = sender_id or self.default_sender_id
        resolved_session_id = session_id
        if not resolved_session_id:
            resolved_session_id = await asyncio.to_thread(
                self.get_or_create_persistent_session_id,
                role_name=role_name,
                sender_id=sender,
            )
        return {
            "success": True,
            "session_id": resolved_session_id,
            "sender_id": sender,
            "task_id": task_id,
            "raw": {
                "note": "QwenPaw RESTful requests are cancelled client-side by N.E.K.O.",
                "role_name": role_name or "",
            },
        }

    @staticmethod
    def _strip_reasoning_trace(text: str) -> str:
        # Shared stripper handles both paired <think>...</think> and the
        # Qwen3.5/3.6 dangling-</think> leak shape; ReAct line filtering below
        # is openclaw-specific and stays here.
        cleaned = strip_thinking_segments(text)
        if not cleaned:
            return ""

        filtered_lines = []
        removed_trace = False
        for line in cleaned.splitlines():
            stripped = line.strip()
            lowered = stripped.lower()
            if lowered.startswith("final answer:"):
                content = stripped.split(":", 1)[1].strip()
                if content:
                    filtered_lines.append(content)
                removed_trace = True
                continue
            if any(lowered.startswith(prefix) for prefix in ("thought:", "thinking:", "analysis:", "observation:", "action:", "tool:")):
                removed_trace = True
                continue
            filtered_lines.append(line)

        candidate = "\n".join(filtered_lines).strip()
        return candidate if removed_trace and candidate else cleaned

    def _extract_reply_text(self, data: Dict[str, Any]) -> str:
        collected: list[str] = []

        def _collect_message_content(message_item: Any) -> None:
            if not isinstance(message_item, dict):
                return
            role = str(message_item.get("role") or "").strip().lower()
            if role and role != "assistant":
                return
            content = message_item.get("content")
            if not isinstance(content, list):
                return
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type") or "").strip()
                if part_type in {"output_text", "text", "input_text"}:
                    text = str(part.get("text") or "").strip()
                    if text:
                        collected.append(text)
                elif part_type == "refusal":
                    refusal = str(part.get("refusal") or "").strip()
                    if refusal:
                        collected.append(refusal)

        output = data.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "message":
                    continue
                _collect_message_content(item)

        message = data.get("message")
        if isinstance(message, dict):
            _collect_message_content(message)

        if not collected:
            raw_text = data.get("output_text")
            if isinstance(raw_text, str) and raw_text.strip():
                collected.append(raw_text.strip())

        return self._strip_reasoning_trace("\n".join(collected).strip())

    @staticmethod
    def _extract_error_message(data: Dict[str, Any]) -> str:
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        if isinstance(error, str) and error.strip():
            return error.strip()
        status = str(data.get("status") or "").strip().lower()
        if status == "failed":
            return "QwenPaw returned a failed response"
        return ""

    @staticmethod
    def _build_attachment_parts(attachments: Any) -> list[dict]:
        if not isinstance(attachments, list):
            return []

        parts: list[dict] = []
        for item in attachments:
            if isinstance(item, str):
                url = item.strip()
            elif isinstance(item, dict):
                url = str(item.get("url") or item.get("image_url") or item.get("data_url") or "").strip()
            else:
                url = ""
            if not url:
                continue
            parts.append({
                "type": "input_image",
                "image_url": url,
            })
        return parts

    @staticmethod
    def _build_process_attachment_parts(attachments: Any) -> list[dict]:
        if not isinstance(attachments, list):
            return []

        parts: list[dict] = []
        for item in attachments:
            if isinstance(item, str):
                url = item.strip()
            elif isinstance(item, dict):
                url = str(item.get("url") or item.get("image_url") or item.get("data_url") or "").strip()
            else:
                url = ""
            if not url:
                continue
            parts.append({
                "type": "image",
                "image_url": url,
            })
        return parts

    @staticmethod
    def _parse_process_sse_payload(raw_text: str) -> Dict[str, Any]:
        latest: Dict[str, Any] = {}
        latest_reply: Dict[str, Any] = {}
        for line in str(raw_text or "").splitlines():
            stripped = line.strip()
            if not stripped.startswith("data:"):
                continue
            payload = stripped[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                parsed = httpx.Response(200, content=payload.encode("utf-8")).json()
            except Exception:
                continue
            if isinstance(parsed, dict):
                latest = parsed
                if parsed.get("object") == "response":
                    latest_reply = parsed
                elif parsed.get("object") == "message":
                    latest_reply = {"message": parsed}
                elif any(key in parsed for key in ("output", "output_text", "message")):
                    latest_reply = parsed
        return latest_reply or latest

    def _build_responses_payload(
        self,
        *,
        session_id: str,
        user_id: str,
        channel: str,
        instruction: str,
        attachments: Optional[list] = None,
    ) -> Dict[str, Any]:
        message_content: list[dict] = []
        clean_instruction = str(instruction or "").strip()
        if clean_instruction:
            message_content.append(
                {
                    "type": "input_text",
                    "text": clean_instruction,
                }
            )
        attachment_parts = self._build_attachment_parts(attachments)
        if attachment_parts and not message_content:
            message_content.append(
                {
                    "type": "input_text",
                    "text": "请分析用户提供的图片内容，并根据图片完成任务。",
                }
            )
        message_content.extend(attachment_parts)
        return {
            "session_id": session_id,
            "conversation": {"id": session_id},
            "user_id": user_id,
            "channel": channel,
            "stream": False,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": message_content,
                }
            ],
        }

    def _build_process_payload(
        self,
        *,
        session_id: str,
        channel: str,
        instruction: str,
        attachments: Optional[list] = None,
    ) -> Dict[str, Any]:
        process_message_content: list[dict] = []
        clean_instruction = str(instruction or "").strip()
        if clean_instruction:
            process_message_content.append(
                {
                    "type": "text",
                    "text": clean_instruction,
                }
            )
        process_attachment_parts = self._build_process_attachment_parts(attachments)
        if process_attachment_parts and not process_message_content:
            process_message_content.append(
                {
                    "type": "text",
                    "text": "请分析用户提供的图片内容，并根据图片完成任务。",
                }
            )
        process_message_content.extend(process_attachment_parts)
        return {
            "session_id": session_id,
            "channel": channel,
            "stream": False,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": process_message_content,
                }
            ],
        }

    def _build_console_payload(
        self,
        *,
        session_id: str,
        user_id: str,
        channel: str,
        instruction: str,
        attachments: Optional[list] = None,
    ) -> Dict[str, Any]:
        payload = self._build_process_payload(
            session_id=session_id,
            channel=channel,
            instruction=instruction,
            attachments=attachments,
        )
        payload["user_id"] = user_id
        return payload

    async def run_instruction(
        self,
        instruction: str,
        *,
        attachments: Optional[list] = None,
        sender_id: Optional[str] = None,
        session_id: Optional[str] = None,
        role_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.reload_config()
        sender = sender_id or self.default_sender_id
        channel = self.default_channel
        resolved_session_id = session_id or await asyncio.to_thread(
            self.get_or_create_persistent_session_id,
            role_name=role_name,
            sender_id=sender,
        )
        responses_payload = self._build_responses_payload(
            session_id=resolved_session_id,
            user_id=sender,
            channel=channel,
            instruction=instruction,
            attachments=attachments,
        )
        process_payload = self._build_process_payload(
            session_id=resolved_session_id,
            channel=channel,
            instruction=instruction,
            attachments=attachments,
        )
        console_payload = self._build_console_payload(
            session_id=resolved_session_id,
            user_id=sender,
            channel=channel,
            instruction=instruction,
            attachments=attachments,
        )
        timeout = httpx.Timeout(self.http_timeout, connect=min(10.0, self.http_timeout))
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                headers=self._build_request_headers(),
                proxy=None,
                trust_env=False,
            ) as client:
                data = None
                console_candidate = (self.console_chat_url, console_payload, "sse", "v2")
                legacy_candidates = (
                    (self.responses_url, responses_payload, "json", "legacy"),
                    (self.process_url, process_payload, "sse", "legacy"),
                )
                candidates = (
                    (console_candidate, *legacy_candidates)
                    if self.api_variant == "v2"
                    else (*legacy_candidates, console_candidate)
                )
                last_response: Optional[httpx.Response] = None
                last_request_error: Optional[httpx.RequestError] = None

                for url, payload, response_format, variant in candidates:
                    try:
                        response = await client.post(url, json=payload)
                    except httpx.RequestError as exc:
                        last_request_error = exc
                        continue
                    last_response = response
                    if response.is_success:
                        data = (
                            response.json()
                            if response_format == "json"
                            else self._parse_process_sse_payload(response.text)
                        )
                        self.api_variant = variant
                        break
                    if response.status_code < 500 and response.status_code not in (404, 405):
                        response.raise_for_status()

                if data is None:
                    if last_response is not None:
                        last_response.raise_for_status()
                    if last_request_error is not None:
                        raise last_request_error
        except httpx.TimeoutException:
            self.last_error = f"OpenClaw(QwenPaw) request timed out ({self.timeout}s)"
            return {"success": False, "error": self.last_error}
        except httpx.HTTPStatusError as exc:
            self.last_error = f"OpenClaw(QwenPaw) returned HTTP {exc.response.status_code}"
            return {"success": False, "error": self.last_error}
        except Exception as exc:
            self.last_error = f"OpenClaw(QwenPaw) connection failed: {exc}"
            return {"success": False, "error": self.last_error}

        if not isinstance(data, dict):
            self.last_error = "OpenClaw(QwenPaw) returned a non-object JSON response"
            return {"success": False, "error": self.last_error, "raw": data}

        error_message = self._extract_error_message(data)
        reply_text = self._extract_reply_text(data)
        if not reply_text:
            self.last_error = error_message or "OpenClaw(QwenPaw) did not return a final reply"
            return {"success": False, "error": self.last_error, "raw": data}

        self.last_error = None
        return {
            "success": True,
            "reply": reply_text,
            "sender_id": data.get("sender_id") or sender,
            "session_id": data.get("session_id") or resolved_session_id,
            "raw": data,
        }

    async def run_magic_command(
        self,
        command: str,
        *,
        sender_id: Optional[str] = None,
        role_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized = self.normalize_magic_command(command)
        if not normalized:
            return {"success": False, "error": f"Unsupported magic command: {command}"}

        sender = sender_id or self.default_sender_id
        if normalized == "/new":
            active_session_id = await asyncio.to_thread(
                self.reset_persistent_session_id,
                role_name=role_name,
                sender_id=sender,
            )
        else:
            active_session_id = await asyncio.to_thread(
                self.get_or_create_persistent_session_id,
                role_name=role_name,
                sender_id=sender,
            )
        backend_result = await self.run_instruction(
            normalized,
            sender_id=sender,
            session_id=active_session_id,
            role_name=role_name,
        )
        if not backend_result.get("success"):
            return {
                **backend_result,
                "command": normalized,
                "display_reply": "",
            }

        display_reply = self.get_magic_command_feedback(normalized)
        return {
            "success": True,
            "command": normalized,
            "reply": display_reply,
            "display_reply": display_reply,
            "backend_reply": str(backend_result.get("reply") or ""),
            "sender_id": sender,
            "session_id": active_session_id,
            "raw": backend_result.get("raw"),
        }
