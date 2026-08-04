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
_CLAUSE_SPLIT = re.compile(r"[，,。．.！!？?；;、\s]+")

# 首部虚词是**闭集**：代词 + 祈使副词 + 收件人短语。不是黑名单——它只负责剥掉
# 「我们/那你/赶紧」这类不改变祈使内容的前缀，剥完还得整条命中白名单才算数。
#
# ⚠️⚠️ **多字词必须排在它的首字前面**。正则多选支按书写顺序匹配，`那` 排在 `那么`
# 前面时，「那么停下来吧」会被 `那` 吃掉首字、剩下一个 `么` 粘在后面
# （子句变成「么停下来」），整条判据失效。`快` / `快点` 同理。加词时照抄这个顺序。
_CLAUSE_LEAD = re.compile(
    r"^(?:"
    r"那么|那麼|快点|快點|我们|我們|咱们|咱們|我想|我要|"
    r"能不能|可不可以|帮我|幫我|给我|給我|麻烦|麻煩|拜托|拜託|"
    r"赶紧|趕緊|马上|馬上|立刻|立即|现在|現在|尽快|盡快|"
    r"直接|还是|還是|不如|干脆|乾脆|要不|想|"
    r"那|就|先|快|请|請|你|妳|我|咱"
    r")+"
)

# ⚠️ 尾部语气词只用于 `/stop` 和 `/new`，**不用于 `/daemon approve`**——见
# `_APPROVE_CLAUSES` 上方那段。多字的征询尾（好吗/行不行）同样要排在单字前面。
# ⚠️ 语气词也是简繁两侧的东西：囉/啰/咯/喽/嘍、呗/唄 必须同批收，否则同一句话
# 繁体命中简体不命中——那正是这一系列改动要修的毛病。
_CLAUSE_TAIL = re.compile(
    r"(?:"
    r"好不好|好吗|好嗎|行不行|行吗|行嗎|可以吗|可以嗎|怎么样|怎麼樣|好了|"
    r"吧|啊|呀|喔|哦|呢|嘛|囉|啰|咯|喽|嘍|呗|唄|嘞|啦|了|一下|喵|吗|嗎|~|～"
    r")+$"
)


def _normalize_clause(clause: str, *, strip_tail: bool = True) -> str:
    """Strip leading function words and (optionally) trailing particles."""
    text = str(clause or "").strip()
    text = _CLAUSE_LEAD.sub("", text)
    if strip_tail:
        text = _CLAUSE_TAIL.sub("", text)
    return text.strip()


def _clause_hits(clause: str, table: frozenset, *, strip_tail: bool = True) -> bool:
    """Match a clause against a table: raw, lead-stripped, then lead+tail.

    ⚠️ All three forms are load-bearing, and never the other way round (deriving
    the stripped spellings INTO the table). The tables hold literal spellings and
    some of those end in a particle the tail regex eats — 别找了 -> 别找,
    删吧 -> 删. An earlier revision closed the tables under the normalizer, which
    put bare single characters like 删 and 准 in them; 帮我删一下 (a fresh delete
    request, not an approval) then dispatched /daemon approve. Keep the tables
    literal and widen the *lookup* instead.

    ⚠️ Lead-only is a separate probe from lead+tail, not an intermediate step:
    现在别找了 lead-strips to 别找了, which IS a table entry, but stripping the
    tail as well takes it to 别找, which is not.
    """
    text = str(clause or "").strip()
    if not text:
        return False
    if text in table:
        return True
    lead_only = _normalize_clause(text, strip_tail=False)
    if lead_only in table:
        return True
    return strip_tail and _normalize_clause(text, strip_tail=True) in table


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


def _approve_clause_hits(clause: str) -> bool:
    """Whether one clause counts toward /daemon approve.

    Bare affirmations match the clause verbatim; action phrases also match after
    the leading function words are stripped. Never strip the tail here — see the
    note above _APPROVE_AFFIRMATIONS.
    """
    text = str(clause or "").strip()
    if text in _APPROVE_AFFIRMATIONS:
        return True
    return _clause_hits(text, _APPROVE_ACTIONS, strip_tail=False)


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
        if all(_approve_clause_hits(c) for c in clauses):
            return {"is_magic_intent": True, "command": "/daemon approve", "source": "rule"}
        if _clause_hits(clauses[-1], _NEW_CLAUSES):
            return {"is_magic_intent": True, "command": "/new", "source": "rule"}
        # 台湾用「搜尋」不用「搜索」，所以繁体那条不是「搜索」的字形转换。
        if _clause_hits(clauses[-1], _STOP_CLAUSES):
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
