# -*- coding: utf-8 -*-
"""
OpenClaw Agent adapter.

In this project, "OpenClaw" is the compatibility name for the external
QwenPaw service. The adapter keeps the existing OpenClaw-facing interface
for N.E.K.O, while the transport is implemented with QwenPaw's RESTful
Responses-compatible API.
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
from utils.llm_client import create_chat_llm
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


class OpenClawAdapter:
    def __init__(self) -> None:
        self.base_url = DEFAULT_OPENCLAW_URL
        self.process_url = f"{DEFAULT_OPENCLAW_URL}{QWENPAW_PROCESS_ENDPOINT_PATH}"
        self.responses_url = f"{DEFAULT_OPENCLAW_URL}{QWENPAW_RESPONSES_ENDPOINT_PATH}"
        self.health_url = f"{DEFAULT_OPENCLAW_URL}{QWENPAW_HEALTH_ENDPOINT_PATH}"
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
                response = client.get(self.health_url)
                if response.is_success:
                    self.last_error = None
                    return {
                        "enabled": True,
                        "ready": True,
                        "reasons": [f"OpenClaw(QwenPaw) reachable ({self.health_url})"],
                        "provider": "qwenpaw",
                    }
                self.last_error = f"HTTP {response.status_code}"
                return {
                    "enabled": True,
                    "ready": False,
                    "reasons": [f"OpenClaw(QwenPaw) responded {response.status_code} ({self.health_url})"],
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
            cfg = get_config_manager().get_model_api_config("summary")
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
            llm = create_chat_llm(
                model=model,
                base_url=base_url,
                api_key=api_key or None,
                temperature=0,
                max_completion_tokens=OPENCLAW_MAGIC_INTENT_MAX_TOKENS,
                max_retries=0,
                extra_body=None,
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
        if any(token in lowered for token in ("我忘了", "我忘记", "雨停了", "停电了", "新的一天", "你的看法")):
            return {"is_magic_intent": False, "command": None, "source": "rule"}

        mapping = [
            ("/clear", ("忘了刚才的事", "忘掉刚才的事", "清除我们的聊天记录", "清除聊天记录", "删掉刚才的记录", "清空聊天记录")),
            ("/new", ("换个话题", "重新开始", "说点别的", "聊点别的", "重新开个话题")),
            ("/stop", ("别找了", "快停下来", "取消这个任务", "取消这个搜索", "算了别查了", "停止搜索", "停下来")),
            ("/daemon approve", ("删吧", "准了", "去执行", "去执行吧", "没问题，去执行", "没问题去执行")),
        ]
        for command, triggers in mapping:
            if any(token in text for token in triggers):
                if command == "/daemon approve" and "同意" in text and "执行" not in text and "删" not in text and "准" not in text:
                    return {"is_magic_intent": False, "command": None, "source": "rule"}
                return {"is_magic_intent": True, "command": command, "source": "rule"}

        if text in {"我同意", "同意", "没问题"}:
            return {"is_magic_intent": True, "command": "/daemon approve", "source": "rule"}

        return {"is_magic_intent": False, "command": None, "source": "rule"}

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
        cleaned = re.sub(r"<think>.*?</think>", "", str(text or ""), flags=re.IGNORECASE | re.DOTALL).strip()
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
        return latest

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
        timeout = httpx.Timeout(self.http_timeout, connect=min(10.0, self.http_timeout))
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                headers=self._build_request_headers(),
                proxy=None,
                trust_env=False,
            ) as client:
                response = await client.post(self.responses_url, json=responses_payload)
                if response.status_code in (404, 405):
                    process_response = await client.post(self.process_url, json=process_payload)
                    process_response.raise_for_status()
                    data = self._parse_process_sse_payload(process_response.text)
                else:
                    response.raise_for_status()
                    data = response.json()
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
