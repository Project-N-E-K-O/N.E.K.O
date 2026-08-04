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

# `/daemon approve` 的「同意」守卫。
#
# ⚠️⚠️ 这道守卫**目前不可达**，别把它算进安全预算：它要求文本含「同意」且
# **不含**下面任何一个动作词，而 12 个 approve 触发词每一个都自带至少一个
# （删吧→删、准了→准、去执行→执行，繁体同理），所以 `not any(...)` 恒为 False。
# 这是既有状况，不是本次改动造成的；留在这里是为了不在 zh-TW 批次里夹带行为
# 变更，处置见下面 `mapping` 上方那段说明。
APPROVE_GUARD_TOKENS = ("执行", "執行", "删", "刪", "准", "準")


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
        # ⚠️ 下面几张表撞的是用户实际打出来的字，简繁是不同码位——只列简体等于
        # 这套口令对繁中用户完全不存在（实测繁中 10/10 全 MISS）。
        if any(token in lowered for token in _HIGH_PRECISION_NON_MAGIC):
            return {"is_magic_intent": False, "command": None, "source": "rule"}

        mapping = [
            # ⚠️ `/clear` 的触发词也**刻意保持简体**，理由同下面的 approve。
            #
            # 它会不可逆地清掉整段对话历史，而判据同样是对自由文本做子串包含：
            # 实测 main 上 `我想知道如何清除聊天记录`（一句提问）就会返回 /clear。
            # 补繁体等于把这个既有缺陷的暴露面翻倍——`我想知道如何清除聊天記錄`
            # 在本批之前是 None（Codex P2）。
            #
            # 繁中用户仍可直接打字面 magic word `/clear`（走 normalize_magic_command，
            # 整句精确匹配）；而且这只是零 LLM 的 pre-gate，返回 None 之后 LLM
            # 分类器照常跑，真的 clear 意图不会丢。
            ("/clear", ("忘了刚才的事", "忘掉刚才的事", "清除我们的聊天记录", "清除聊天记录", "删掉刚才的记录", "清空聊天记录")),
            ("/new", (
                "换个话题", "換個話題", "重新开始", "重新開始",
                "说点别的", "說點別的", "聊点别的", "聊點別的",
                "重新开个话题", "重新開個話題",
            )),
            # 台湾用「搜尋」不用「搜索」，所以繁体那条不是「搜索」的字形转换。
            ("/stop", (
                "别找了", "別找了", "快停下来", "快停下來",
                "取消这个任务", "取消這個任務", "取消这个搜索", "取消這個搜尋",
                "算了别查了", "算了別查了", "停止搜索", "停止搜尋",
                "停下来", "停下來",
            )),
            # ⚠️⚠️ approve 的触发词**刻意保持简体**，不在本批补繁体。
            #
            # 这不是漏了。这条命令会让上游真的去执行一个高风险动作，而它的触发
            # 判据是**对自由文本做子串包含**——已实测在 main 上就会把
            # `我准了假`（「准了」）、`删吧台的记录`（「删吧」）、`他说去执行`、
            # `可以去执行吗`、`拒绝去执行`、`禁止去执行` 全部判成批准。补繁体等
            # 于把这个既有缺陷的暴露面翻倍。
            #
            # 试过用「否定词出现在触发词之前就拒绝」来兜，对抗性验证跑了 196 条
            # 输入把它打穿了：否定放在触发词右边（`去執行？我不要`）、锚点落在
            # 无关子串上（`这标准了不起，但不要去执行` 命中的是「准了」）、疑问句
            # （`要去執行嗎？`）全部照过；反方向还误伤了 `没错，去执行` /
            # `没意见，去执行` 这类**审批语境里靠否定词构成的肯定语**。黑名单在
            # 这里是结构性走不通的。
            #
            # 繁中用户仍可通过下面那条**整句精确匹配**批准（`沒問題` / `同意`），
            # 那条形状是对的：整句、无子串、无自由文本。
            # 根治要把 approve 整条从子串包含改成规范化整句白名单，那会改变简中
            # 用户的现有行为，不塞进 zh-TW 批次——见 issue #2500 的跟进项。
            ("/daemon approve", ("删吧", "准了", "去执行", "去执行吧", "没问题，去执行", "没问题去执行")),
        ]
        for command, triggers in mapping:
            if any(token in text for token in triggers):
                if command == "/daemon approve" and "同意" in text and not any(
                    token in text for token in APPROVE_GUARD_TOKENS
                ):
                    return {"is_magic_intent": False, "command": None, "source": "rule"}
                return {"is_magic_intent": True, "command": command, "source": "rule"}

        # 整句精确匹配：没有子串、没有自由文本，所以补繁体是零风险的。
        if text in {"我同意", "同意", "没问题", "沒問題"}:
            return {"is_magic_intent": True, "command": "/daemon approve", "source": "rule"}

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
