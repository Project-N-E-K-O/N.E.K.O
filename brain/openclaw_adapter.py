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
- Trigger example: "忘了刚才的事吧" -> /clear
- Misfire trap: "我忘了带伞" / "雨停了" -> do NOT trigger

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


# ── magic-command 规则分类器的词表 ─────────────────────────────────
# 模块级不是为了复用，是为了**可断言**：函数内的局部 tuple 测试拿不到，
# 缺一侧字形只能靠人眼发现。（这条和 utils/music_crawlers.py 的路由词表同理。）
#
# ⚠️ 这些表撞的是用户实际打出来的字，简繁不同码位，两侧必须同批收词。

# 命中即整轮判定为「非 magic」。高精度优先：宁可保守，不冒进扩展。
_HIGH_PRECISION_NON_MAGIC = (
    "我忘了", "我忘记", "我忘記", "雨停了", "停电了", "停電了",
    "新的一天", "你的看法",
)

# ── 整子句白名单 ────────────────────────────────────────────────────
# `/daemon approve`、`/stop`、`/new` 的判据从**子串包含**改成**整子句白名单**。
#
# 子串包含撞的是自由文本，实测在这个 commit 之前：`/stop` 22/22、`/new` 16/16、
# `/daemon approve` 17/28 条日常句子会误命中——「雨停下来了」「比賽即將重新開始」
# 「我准了假下周去旅游」「领导批准了我的申请」全部触发。连仓库自己的 UI 文案都会
# 中招：static/locales/{zh-CN,zh-TW}.json 里 4257 条**不含任何命令意图**的产品文案
# 各有 6 条命中，其中一条还是教程 day6 的台词（"随时都可以戳一下让我停下来"）。
#
# 之前试过用「否定词出现在触发词之前就拒绝」来兜，196 条对抗输入把它打穿了：否定
# 落在触发词右边（`去執行？我不要`）、锚点落在无关子串上（`这标准了不起，但不要去
# 执行` 命中的是「准了」）、疑问句（`要去執行嗎？`）全部照过，反方向还误伤了
# `没错，去执行` 这类**审批语境里靠否定词构成的肯定语**。黑名单在这里结构性走不通。
#
# 现在的判据：按标点/空白切子句 → 每个子句剥掉首部虚词和尾部语气词 → 查白名单。
# 词汇量**一个没加**，还是原来那些词，只是要求它们**独立成句**而不是嵌在任意
# 长句里。`/clear` 不在本批（不在本次拍板的三条里），仍走子串包含。
# ⚠️ 中文省略号 …／⋯ 和破折号 ——／— 都是**句中分隔符**，不只是句尾装饰：
# `同意……去执行` / `好吧——换个话题` 在旧实现里靠子串命中，不切它整条就落空。
_CLAUSE_SPLIT = re.compile(r"[，,。．.！!？?；;、：:…⋯‥—–―─\-\s]+")

# ── 首部虚词：**两套白名单**，approve 用窄的那套 ──────────────────────
#
# ⚠️⚠️ 这里刻意是白名单而不是「宽剥离 + 否决表」。之前那版对所有命令共用一张宽表，
# 再拿一张否决表去挡不该批准的语气，连着三轮 Codex P1 才把洞补到看起来齐：先是漏
# 问号、再是漏无标点疑问（`去執行嗎`）、再是漏试探提议（`要不去執行`），最后又漏第一
# 人称意图（`我想去執行`）。根因不是词收得不够，是**方向反了**——「一个前缀会不会
# 把祈使句变成非授权」是开集，黑名单堵不完；本文件里 approve 那段注释自己写过这句话。
#
# 现在改成：approve 只剥**中性**前缀，剥完还得整条命中动作短语表；想让 approve 多认
# 一种说法，必须往中性表里加词——一次可见、可评审的动作，而不是忘记往否决表加词。
#
# ⚠️ **多字词必须排在它的首字前面**。正则多选支按书写顺序匹配，`那` 排在 `那么` 前面
# 时，「那么停下来吧」会被 `那` 吃掉首字、剩下一个 `么` 粘在后面（子句变成「么停下来」），
# 整条判据失效。`快`/`快点`、`我`/`我想` 同理。加词时照抄这个顺序。
#
# 中性 = 剥掉它**不改变「谁被授权做什么」**的词：连接副词、**第二人称**主语、
# 收件人短语、礼貌前缀、决断型副词。决断型是祈使句的强调而不是提议
# （「马上去执行」是命令，「不如去执行」是建议），在 main 上就是 approve。
#
# ⚠️ **第一人称主语不在这里**（我 / 咱 / 我们 / 我們 / 咱们 / 咱們）。`我去執行` 是
# 用户在说**自己**要去做，不是授权 agent 去做；剥掉 `我` 之后它和 `去執行` 一模一样。
# 第二人称留着，因为 `你去执行吧` 恰恰是指向 agent 的授权。这条判据（剥掉它会不会
# 把授权变成非授权）是这两张表的唯一入表标准，加词时逐词问一遍。
_NEUTRAL_LEAD = (
    # 应答式前缀（`好的去执行` 这种不带分隔符的写法）。⚠️ 只收**双字及以上**：
    # 单字的 好 / 行 / 对 / 嗯 当前缀太糙（`对方去执行` 会被剥成 `方去执行`，
    # `行不行` 更是词尾），它们只作为**应答子句**参与，见 _APPROVE_COMPANIONS——
    # 那条路要求有分隔符，语义上也更接近「先应一声再下令」。
    # 中英混排的应答前缀也常见：`OK去执行` / `okay去执行` 在旧实现里靠子串命中。
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
# 仅 /stop 与 /new：试探提议（要不/不如/还是/干脆）、第一人称意图（我想/我要/想）、
# 第一人称主语（我/咱/我们/咱们）。
# ⚠️ 这些**改变的是「谁打算做」和「是不是在提议」**，不是加强祈使语气。对停任务、换话题
# 而言「不如换个话题」「我想取消这个任务」「我们换个话题吧」是再正常不过的说法；对批准
# 而言「我想去執行」是陈述打算、「我去執行」是宣告自己动手、「要不去執行」是在抛一个提议，
# 都不是授权。
# ⚠️ 多字必须排在单字前面：`我想` / `我要` / `我们` 要在 `我` 之前。
_SOFT_LEAD = (
    # ⚠️ `要不要` 必须排在 `要不然|要不` 前面，否则 `要不要停下来` 被 `要不` 咬成
    # `要停下来`。这已经是这套表第五次栽在「多字词排在它的首字/前缀后面」上了
    # （那么·快点·我想·你们·要不要），加词时照抄这个顺序。
    r"要不要|能不能|可不可以|要不然|要不|不如|还是|還是|干脆|乾脆|"
    r"我想|我要|我们|我們|咱们|咱們|想|我|咱"
)
_CLAUSE_LEAD_NEUTRAL = re.compile(rf"^(?:{_NEUTRAL_LEAD})+")
_CLAUSE_LEAD_SOFT = re.compile(rf"^(?:{_SOFT_LEAD}|{_NEUTRAL_LEAD})+")

# ── 尾部语气词：同样**两套白名单** ────────────────────────────────────
# ⚠️ 多字的征询尾（好吗/行不行）要排在单字前面，同 _NEUTRAL_LEAD 那条。
# ⚠️ 语气词也是简繁两侧的东西：囉/啰/咯/喽/嘍、呗/唄 必须同批收，否则同一句话
# 繁体命中简体不命中——那正是这一系列改动要修的毛病。
# 中性语气词两边都剥；疑问尾只给 /stop 和 /new 剥。`停下来行吗` 对停任务是最常见的
# 礼貌祈使，而 `去執行嗎` 对批准是个**问句**——归一化把 `嗎` 剥掉之后两者就没法区分了。
# ⚠️ 中性词尾里**没有体标记 `了`**。`去執行了` 是在报告「已经去执行了」，剥掉 `了`
# 之后它和授权的 `去執行` 没法区分。`好了` 留着——「去执行好了」是「那就去执行吧」的
# 口语说法，是授权。表内条目 `准了` / `準了` 自带的那个 `了` 靠**原样命中**，不靠剥。
_NEUTRAL_TAIL = (
    r"好了|吧|啊|呀|喔|哦|嘛|囉|啰|咯|喽|嘍|呗|唄|嘞|啦|一下|喵|"
    r"谢谢|謝謝|多谢|多謝|感谢|感謝|"
    # 句尾礼貌语。和 `谢谢` 同一类：它们在 `_NEUTRAL_LEAD` 里已经是首部词，但
    # `停下来，拜托了` 是把同一个词放到句尾——`_command_clause` 只剥得掉 `了`，剩下的
    # `拜托` 就成了命令子句，整条落空。
    # ⚠️ 这里只收句尾真的这么说的四个词，没有把首部表整张镜像过来：`停下来，烦请` /
    # `换个话题，劳驾` 不是中文的说法，收进来只是让表变长、判据变糊。
    # ⚠️ 边界记账：`拜托你了` 这种中间插了人称的写法仍然落空（剥完 `了` 剩 `拜托你`）。
    # 「礼貌语 + 任意人称/程度词」是开集，不打算枚举——这跟本文件里前缀那段是同一个论证。
    r"拜托|拜託|麻烦|麻煩|"
r"耶|唷|哟|喲|欸|诶|咧|哈|噢|呐|吶|呦|哒|噠|齁|捏|~|～"
)
# 仅 /stop 与 /new：疑问尾 + 体标记 `了`。
_SOFT_TAIL = (
    r"好不好|好吗|好嗎|行不行|行吗|行嗎|可以吗|可以嗎|怎么样|怎麼樣|吗|嗎|呢|了"
)
_TAIL_ALTERNATION = f"{_SOFT_TAIL}|{_NEUTRAL_TAIL}"
# ⚠️⚠️ 词尾**只以 token 元组的形式存在，没有对应的正则**，是刻意的：这里唯一正确的
# 剥法是下面 `_clause_hits` 那套逐个试，留一个 `(?:…)+$` 的正则在旁边只会被人拿去用，
# 然后重新踩进下面这两个坑。同理这里没有 `_normalize_clause` 之类的通用归一化函数——
# 判据只有一份，就在 `_clause_hits` 里。
#
# 剥词尾不能用正则一次性吃掉整串，也不能只试正则挑中的那一个词尾。两个坑：
#
# 1. 整串吃会连表内条目自带的那个字一起吃掉：`别找了吧` 的 `了吧` 被整串剥成
#    `别找`，而表里的条目是 `别找了`——一句再自然不过的话就停不掉任务了（Codex P2）。
# 2. 只试正则挑中的那一个也不行：多选支从左优先，`去执行吗` 会被 `行吗` 命中，
#    剥成 `去执`——它吃掉的是「执**行**」的字。这跟首部咬断多字词是同一类错误，
#    只是方向相反，而且换词表顺序解决不了（`行吗` 本身必须收）。
#
# 所以改成：每一步对**所有**能匹配的词尾各试一次，每剥一个查一次表。
_NEUTRAL_TAIL_TOKENS = tuple(_NEUTRAL_TAIL.split("|"))
_TAIL_TOKENS = tuple(_TAIL_ALTERNATION.split("|"))

# ⚠️ 子句两端的**装饰性字符**：省略号 … 、破折号 ——、引号「」『』“”、括号（）、
# emoji、颜文字符号……它们既不是 _CLAUSE_SPLIT 里的分隔符，也不是语气词，于是
# `停下来…` / `换个话题👍` / `「停下來」` 整条落不到白名单上——中文聊天里这是最常见
# 的收尾方式之一，`/stop` 和 `/new` 在其余方面都严格窄于旧实现，唯独这一类是大面积
# 回归。用「非词字符」这个**补集**来剥，而不是去枚举符号：符号是开集，枚举必漏。
# `\w` 在 Python3 下含 CJK，所以这条只吃标点、符号与 emoji。
# ⚠️ `[\W_]` 而不是 `\W`：Python 的 `\w` **包含下划线**，所以 `\W` 剥不掉它。
# Markdown 强调是聊天里最常见的写法之一，`_停下来_` / `__停下來__` 因此整条落空；
# 分开的尾标记 `停下来 _` 更糟——那个 `_` 会被当成命令子句本身。
_DECORATION = re.compile(r"^[\W_]+|[\W_]+$")
# ⚠️⚠️ approve 的装饰剥离是**另一套**，只剥句尾、且只认一张**闭集**标点表。
#
# 句首那一格是语义位：`❌去執行` / `🚫去執行` 是「别执行」，`「去執行」` 是在**提及**
# 这个词而不是在下令。但句尾同样能带否定：`去執行❌` / `去執行🚫` 也是「别执行」。
# 用 `\W`（非词字符的补集）去剥，两端都会把这些符号当装饰吃掉——本 PR 一度如此，
# 繁体侧 main 是 None，等于我新开的口子。
#
# 「哪些符号带否定语义」是**开集**（❌✖✗🚫⛔🙅🆖🚷……），黑名单堵不完——这跟本文件
# 上面那段关于前缀的论证是同一件事。所以 approve 反过来只认一张**明确无语义**的标点
# 表；emoji 一律不剥，代价是 `去执行👌` 相对旧实现丢了（👌 和 ❌ 在这一层区分不了）。
# /stop 与 /new 后果小（掐任务 / 换话题），仍用 `_DECORATION` 两端宽剥，`「停下來」` → /stop。
# ⚠️ 这里**只有一张**严格表：曾经还有一张只剥句尾的宽表 `_DECORATION_TAIL`，等 approve
# 的两条支路都切到严格表之后它就没人用了。没有删的话，下一个人会顺手拿它去「放宽一点」，
# 正好把刚堵上的否定符号洞重新打开。
_DECORATION_TAIL_APPROVE = re.compile(r"[…⋯‥~～!！.。·・、,，\s]+$")

# ⚠️ 剥词尾的候选是原串的各级前缀，句尾语气词能连着写（`去执行吧吧吧吧…`），所以
# 候选数随词尾连串长度增长、每个候选还要做一次切片——对超长输入是二次的，实测 20k
# 字能在事件循环里卡住秒级。分类器跑在用户输入路径上，给它一个硬上界；表内最长条目
# 才 6 个字，正常命令远够不到这个数。
_MAX_PEEL_CANDIDATES = 32

# ⚠️ 问号必须**单独**否决 approve：它是分隔符，切子句时就没了，剥不剥词尾都留不下痕迹
# （`去執行？` 切完就是 `去執行`）。其余非授权语气——疑问尾、正反问、试探提议、第一
# 人称意图——由上面那两套窄白名单**结构性**挡住，不再靠否决表逐条枚举。
# 这个区别是有意的：黑名单在这里堵不完（本文件 approve 那段注释自己写过），
# 白名单要放宽必须显式加词。
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
    # ⚠️ 表保持字面量，widen 的是**查表**不是表。表里有些条目自带语气词尾，
    # 归一化会把它们吃掉（别找了→别找、删吧→删）。上一版反过来做——把表闭包到
    # 归一化形态——于是单字「删 / 准」进了 approve 表，`帮我删一下`（一条**新的
    # 删除请求**，不是对待审批动作的应答）就派成了 /daemon approve。
    #
    # ⚠️ 语气词**逐个剥、每剥一个查一次表**，不能一次性剥光：`别找了吧` 一次剥完
    # 是 `别找`（不在表内），逐个剥则先得到 `别找了`（表内条目）就停住。
    #
    # ⚠️ lead-only 是**独立一档**：`现在别找了` 剥掉首部才露出表内的 `别找了`。
    text = str(clause or "").strip()
    if not text:
        return False
    lead = lead_re if lead_re is not None else _CLAUSE_LEAD_SOFT
    tokens = tail_tokens if tail_tokens is not None else _TAIL_TOKENS
    decoration = decoration_re if decoration_re is not None else _DECORATION
    seen: set = set()
    pending = []
    # 三档起点：原样 / 剥掉两端装饰字符 / 再剥掉首部虚词。
    bare = decoration.sub("", text).strip()
    # ⚠️ 拉丁字母的前缀（OK / okay）大小写写法是开集（oK / oKaY / OKay…），枚举必漏。
    # 多试一个整体小写的候选，中文不受影响。
    lowered = bare.lower()
    starts = [text, bare, lead.sub("", bare).strip()]
    if lowered != bare:
        starts += [lowered, lead.sub("", lowered).strip()]
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
            # 剥掉一个语气词之后可能又露出装饰字符（`去执行吧…` → `去执行吧` → …），
            # 所以每一步都再剥一次两端装饰。
            peeled = decoration.sub("", current[: -len(token)]).strip()
            if not peeled or peeled == current:
                continue
            if peeled in table:
                return True
            if peeled not in seen:
                seen.add(peeled)
                pending.append(peeled)
    return False


# ⚠️ 这三张表撞的是用户实际打出来的字，简繁不同码位，两侧必须同批收词。
# ⚠️ 词汇量**刻意不扩**：每一条都是改造前那两支（子串表 + 整句精确匹配表）里已有
# 的词，字面照抄。「可以 / 好 / 好的 / 行」这类更宽的应答词**没有**加进来——那是
# 扩大批准面，得单独评估，不夹带在这次收口里。
#
# ⚠️⚠️ approve 的两支**判据不同**，别合成一张表。
#
# 裸应答（同意 / 我同意 / 没问题 / 沒問題）在改造前走的是**整句精确匹配**，所以
# `没问题喵~` / `同意~` / `沒問題喔` / `不如同意` / `那就同意` 在 main 上全是 None。
# 一旦对它们做首尾归一化，这些统统变成批准——收口改动反而扩大高风险命令的命中面，
# 本末倒置。主动搭话轮尤其致命：task_executor 在 proactive 轮把意图换成猫娘自己那句
# 台词再喂进分类器，「没问题喵~」正是她的日常口癖，等于自批准。
# 所以裸应答只认**整条子句原样**，一个字都不剥。
_APPROVE_AFFIRMATIONS = frozenset({"同意", "我同意", "没问题", "沒問題"})

# ⚠️ **应答子句**：只能陪跑，不能单独授权。
#
# 中文里最常见的授权说法是「应答子句 + 命令子句」——`好的，去执行` / `没错，去执行` /
# `可以，删吧`。改造前它们靠子串 `去执行` 命中；改成 all-clauses fail-closed 之后，
# `好的` 这一子句不在任何表里，整条就废了。**这不是收紧，是纯粹的召回损失**：`没错，
# 去执行` 恰恰是本文件上面那段论证里点名「黑名单方案会误伤」的例子，白名单方案把它
# 一起误伤了，注释和行为对不上。
#
# 但它们**不能**当成裸应答：`好的` / `可以` 单独一句在改造前是 None（旧的整句精确
# 匹配表只有那四条），当成授权就是扩大批准面。所以规则是：
#   一条应答子句只有在**同一句话里还存在至少一个动作短语或裸应答子句**时才算数。
# `好的，去执行` → 通过；`好的` → None；`好的，好的` → None。
# ⚠️ 和裸应答同理，应答子句只认**整条子句原样**，`好的喵~` 不算。
_APPROVE_COMPANIONS = frozenset({
    "好", "好的", "好吧", "行", "行了", "可以", "嗯", "对", "對", "没错", "沒錯",
    "没意见", "沒意見", "批准", "允许", "允許",
    # ⚠️ OK / okay 不在这里：它们已经进了 _NEUTRAL_LEAD，独立成句时由上面
    # 「纯中性首部词 → companion」那一档接住，重复登记只会多一条判据、两处漂移。
})

# 动作短语支：这些在改造前是**子串**触发，只要句子里含就命中，所以对它们剥首部虚词
# 不可能超出旧行为的召回。繁体条目是本批新增（旧表繁体全空）——整子句判据下补繁体
# 不再放大暴露面，这是本次改动明确要补的那一格。
_APPROVE_ACTIONS = frozenset({
    "删吧", "刪吧", "准了", "準了",
    "去执行", "去執行", "去执行吧", "去執行吧",
    "没问题去执行", "沒問題去執行",
})
_STOP_CLAUSES = frozenset({
    "别找了", "別找了", "快停下来", "快停下來", "停下来", "停下來",
    "取消这个任务", "取消這個任務", "取消这个搜索", "取消這個搜尋",
    "算了别查了", "算了別查了", "停止搜索", "停止搜尋",
})
_NEW_CLAUSES = frozenset({
    "换个话题", "換個話題", "重新开始", "重新開始",
    "说点别的", "說點別的", "聊点别的", "聊點別的",
    "重新开个话题", "重新開個話題",
})

# ⚠️ `/clear` 仍走**子串包含**，触发词也仍**刻意保持简体**。
#
# 它会不可逆地清掉上游 QwenPaw 的整段会话上下文，而判据是对自由文本做子串包含：
# 实测 `我想知道如何清除聊天记录`（一句提问）就会返回 /clear。补繁体等于把这个既有
# 缺陷的暴露面翻倍——`我想知道如何清除聊天記錄` 目前是 None。
#
# 上面那套整子句白名单同样适用于它，但 /clear 不在本次拍板的三条里，不擅自扩——
# 单独评估。繁中用户仍可直接打字面 magic word `/clear`（走 normalize_magic_command，
# 整句精确匹配）。
#
# ⚠️ 别把「LLM 分类器还会兜底」当安全垫——它**不是无条件的**。rule_magic_command 同时
# 是 task_executor._deterministic_action_signal 的唯一 openclaw 信号，而那个廉价前置闸
# （task_executor.py 的 `external_intent < AGENT_EXTERNAL_GATE_THRESHOLD and not
# _deterministic_action_signal(...)` → `return None`）跑在 classify_magic_intent
# **之前**。规则漏掉 + 小模型把这轮读成闲聊 = 整轮评估直接跳过，LLM 那一路根本到不了。
# 也就是说规则表收窄的代价在低 external_intent 的短句上是实打实的，不是「多跑一次」。
_CLEAR_TRIGGERS = (
    "忘了刚才的事", "忘掉刚才的事", "清除我们的聊天记录",
    "清除聊天记录", "删掉刚才的记录", "清空聊天记录",
)


def _approve_clause_kind(clause: str) -> Optional[str]:
    """Classify one clause for /daemon approve: action, affirmation, or companion.

    The three cannot share one judgement — see the notes above
    _APPROVE_AFFIRMATIONS and _APPROVE_COMPANIONS.
    """
    text = str(clause or "").strip()
    if text in _APPROVE_AFFIRMATIONS:
        return "affirmation"
    # ⚠️ 应答子句走**完整**归一化（装饰 + 首部 + 语气词），`好的喵~，去执行` /
    # `OK，去执行` 在旧实现里都是 approve。它和裸应答的区别在于：应答子句单独出现
    # 永远不算授权（下面 any(action|affirmation) 那道），所以放宽它的写法不会扩大
    # 批准面；裸应答则相反，`没问题喵~` 一旦成立就是主动搭话轮的自批准。
    #
    # 这里用**窄**首部表是保守选择，不是安全必需：改成宽表跑 140239 条新旧差分，
    # 简体侧扩大仍是 0（应答子句 + 动作子句的任意组合在旧实现里都被子串命中过）。
    # 所以别为这一行写「必须是窄表」的测试——那是个等价变异，钉不住。
    # ⚠️ 独立成句的**中性首部词**也算应答子句：`请，去执行` / `麻烦，去执行` /
    # `那，去执行` 在旧实现里靠子串命中，而同样的词直接贴着写（`请去执行`）一直是通的。
    # 它们本来就被判定为「剥掉不改变谁被授权做什么」，单独成句自然也不改变。
    # 和其它应答子句一样：单独出现永远不算授权，必须同句里还有动作短语或裸应答。
    # ⚠️ 装饰用**严格**那张，和下面的应答/动作查表一致：`可以❌` 里的 ❌ 否定的是
    # 这句应答，宽表会把它当装饰剥掉、于是整条被判成批准。
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
        # ⚠️ 应答子句也用**严格**装饰表。早先这里放宽过（理由是应答子句单独出现
        # 永远不算授权），但否定符号照样能落在它头上：`可以❌，去執行` 里的 ❌ 否定的
        # 是那句应答，整条却仍被判成批准。👌 和 ❌ 在这一层区分不了，只能整类不剥。
        # 代价：`好的👌，去执行` 相对旧实现丢了。
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
    # 动作短语可以剥首尾：它们在改造前是**子串**触发，句子里含就命中，所以归一化
    # 不可能超出旧召回。裸应答不行——那一支改造前是整句精确匹配。
    # ⚠️ 但只剥**中性**的那一套：试探提议、第一人称意图、疑问尾都不剥，剥了就分不出
    # 「去執行」（授权）和「我想去執行」（陈述打算）/「要不去執行」（提议）/「去執行嗎」（问句）。
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
    # ⚠️ 空白也是子句分隔符，所以 `停下来 吧` / `停下來 👍` / `换个话题 喵` 会把语气词
    # 切成独立的末子句，末子句判据于是只看到 `吧`／`👍`／`喵`，整条落空——旧实现靠
    # 子串是命中的。往回跳过这些「只有语气词/装饰」的尾巴。
    # ⚠️ **不给 approve 用**：approve 是全子句 fail-closed，把 `同意 吧` 的 `吧` 丢掉
    # 会让它变成裸应答 `同意` 从而被批准，而旧实现里它是 None——那是扩大批准面。
    # 代价记账：`去执行 吧`（命令中间打了空格）在旧实现里是 approve，现在是 None。
    # 两者只能二选一，选了 fail-closed 那一边。
    for clause in reversed(clauses):
        stripped = _DECORATION.sub("", clause).strip()
        if not stripped:
            continue
        # ⚠️ 剥词要试**所有**能匹配的词尾、并且优先剥最长的那个，不能像早先那样
        # 只取词表里第一个匹配到的：`_TAIL_TOKENS` 里 `了` 排在 `好了` 前面，
        # `换个话题 好了` 的尾巴会被剥成 `好`，于是这段尾巴不被认成「纯语气词」、
        # 反倒被当成命令子句，整条落空。这跟 _clause_hits 里那个坑是同一个。
        # ⚠️ 硬上界是**必需**的，不是最坏情况护栏：每一步都要对整串跑一次
        # _DECORATION 正则，无界版实测连 12 万个语气词那一档都跑不完（600s 超时）。
        # rule_magic_command 在用户输入路径上，且被廉价前置闸同步调用。
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
                extra_body=None,
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
        normalized = OpenClawAdapter.normalize_magic_command(text)
        if normalized:
            return {"is_magic_intent": True, "command": normalized, "source": "rule"}

        lowered = text.lower()
        if not lowered:
            return {"is_magic_intent": False, "command": None, "source": "rule"}

        # 高精度优先：词表宁可保守，也不冒进扩展。
        # ⚠️ 这张表和下面几张一样撞的是用户实际打出来的字，简繁不同码位，两侧必须
        # 同批收词——只列简体等于这道抑制对繁中用户完全不存在。
        if any(token in lowered for token in _HIGH_PRECISION_NON_MAGIC):
            return {"is_magic_intent": False, "command": None, "source": "rule"}

        if any(token in text for token in _CLEAR_TRIGGERS):
            return {"is_magic_intent": True, "command": "/clear", "source": "rule"}

        clauses = _split_clauses(text)
        if not clauses:
            return {"is_magic_intent": False, "command": None, "source": "rule"}

        # ⚠️⚠️ approve 用**全部子句**都必须在白名单里（fail-closed），其余两条只看
        # **末子句**（祈使句尾）。这个不对称是按后果严重性定的：approve 会让上游真的
        # 去执行一个高风险动作，而 /stop 只是掐任务、/new 只是换话题。
        #
        # 差别看得见的地方：`我不同意，去执行` 在 approve 下是 None（有子句不在表里），
        # 换成末子句判据就会变成批准。反过来 `我还没同意，停止搜索` 必须仍是 /stop——
        # 前半句只是铺垫，祈使落在末子句上。
        #
        # ⚠️ 已知限制，没修：`停下来，我自己来`（先下命令、再补一句理由）在改造前
        # 命中 /stop，现在是 None。它和 `停下来，这是我当时唯一的念头`（叙述）在**任何
        # 子句位置判据下都不可区分**——两句的祈使短语都在首子句。子串包含把前者接住
        # 是顺带的，代价是把后者也接住。要真的分开得看语义，不是这一层能做的事。
        # ⚠️ 问号一票否决 approve。它是子句分隔符，切完就没了（`去執行？` → `去執行`），
        # 窄白名单看不见它。其余非授权语气（疑问尾 / 正反问 / 试探提议 / 第一人称意图）
        # 由 _approve_clause_hits 的窄首尾表结构性挡住，不在这里枚举。
        # ⚠️ 每个子句都得有归属（fail-closed），且**至少有一个**是动作短语或裸应答——
        # 光有应答子句不算授权（`好的` / `好的，好的` → None），必须有实质内容。
        approve_kinds = [_approve_clause_kind(c) for c in clauses]
        if (
            not _APPROVE_QUESTION_MARK.search(text)
            and all(approve_kinds)
            and any(kind in ("action", "affirmation") for kind in approve_kinds)
        ):
            return {"is_magic_intent": True, "command": "/daemon approve", "source": "rule"}
        # ⚠️ 末子句要跳过「只有语气词/装饰」的尾巴，见 _command_clause。
        command_clause = _command_clause(clauses)
        if _clause_hits(command_clause, _NEW_CLAUSES):
            return {"is_magic_intent": True, "command": "/new", "source": "rule"}
        # 台湾用「搜尋」不用「搜索」，所以繁体那条不是「搜索」的字形转换。
        if _clause_hits(command_clause, _STOP_CLAUSES):
            return {"is_magic_intent": True, "command": "/stop", "source": "rule"}

        return {"is_magic_intent": False, "command": None, "source": "rule"}

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

        llm_result = await self._classify_magic_intent_with_llm(text)
        if isinstance(llm_result, dict):
            return llm_result
        return self._classify_magic_intent_with_rules(text)

    async def stop_running(
        self,
        *,
        sender_id: Optional[str] = None,
        session_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        role_name: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.last_error = None
        sender = sender_id or self.default_sender_id
        resolved_session_id = session_id or conversation_id
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
        conversation_id: Optional[str] = None,
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
        del conversation_id
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
