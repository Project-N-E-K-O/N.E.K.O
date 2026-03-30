# -*- coding: utf-8 -*-
"""
OpenClaw Agent adapter.

This adapter talks to the external OpenClaw HTTP service directly instead of
going through the user plugin runtime.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Dict, Optional

import httpx

from utils.config_manager import get_config_manager
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Agent")

DEFAULT_OPENCLAW_URL = "http://127.0.0.1:8089"
DEFAULT_TIMEOUT = 300.0


def _normalize_timeout(value: Any, default: float) -> float:
    try:
        timeout = float(value)
        return timeout if timeout > 0 else default
    except (TypeError, ValueError):
        return default


class OpenClawAdapter:
    def __init__(self) -> None:
        self.base_url = DEFAULT_OPENCLAW_URL
        self.timeout = DEFAULT_TIMEOUT
        self.http_timeout = max(DEFAULT_TIMEOUT + 15.0, DEFAULT_TIMEOUT)
        self.default_sender_id = "neko_user"
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

        raw_url = cfg.get("OPENCLAW_URL", cfg.get("openclawUrl"))
        if isinstance(raw_url, str) and raw_url.strip():
            self.base_url = raw_url.strip().rstrip("/")
        else:
            self.base_url = DEFAULT_OPENCLAW_URL

        self.timeout = _normalize_timeout(
            cfg.get("OPENCLAW_TIMEOUT", cfg.get("openclawTimeout", DEFAULT_TIMEOUT)),
            DEFAULT_TIMEOUT,
        )
        self.http_timeout = max(self.timeout + 15.0, self.timeout)
        raw_sender = cfg.get("OPENCLAW_DEFAULT_SENDER_ID", cfg.get("openclawDefaultSenderId"))
        self.default_sender_id = raw_sender.strip() if isinstance(raw_sender, str) and raw_sender.strip() else "neko_user"

    def is_available(self) -> Dict[str, Any]:
        self.reload_config()
        try:
            with httpx.Client(timeout=httpx.Timeout(3.0, connect=1.5), proxy=None, trust_env=False) as client:
                response = client.get(f"{self.base_url}/health")
                if response.is_success:
                    self.last_error = None
                    return {
                        "enabled": True,
                        "ready": True,
                        "reasons": [f"OpenClaw reachable ({self.base_url})"],
                        "provider": "openclaw",
                    }
                self.last_error = f"HTTP {response.status_code}"
                return {
                    "enabled": True,
                    "ready": False,
                    "reasons": [f"OpenClaw responded {response.status_code} ({self.base_url})"],
                    "provider": "openclaw",
                }
        except Exception as exc:
            self.last_error = str(exc)
            return {
                "enabled": True,
                "ready": False,
                "reasons": [f"OpenClaw unavailable: {exc}"],
                "provider": "openclaw",
            }

    def _load_session_cache(self) -> Dict[str, str]:
        if self._session_cache is None:
            cfg = get_config_manager().load_json_config("openclaw_sessions.json", default_value={})
            self._session_cache = cfg if isinstance(cfg, dict) else {}
        return self._session_cache

    def _save_session_cache(self) -> None:
        if self._session_cache is None:
            return
        get_config_manager().save_json_config("openclaw_sessions.json", self._session_cache)

    @staticmethod
    def _build_session_key(role_name: Optional[str], sender_id: str) -> str:
        role = str(role_name or "").strip() or "__default_role__"
        sender = str(sender_id or "").strip() or "neko_user"
        return f"{role}::{sender}"

    def get_or_create_persistent_session_id(self, *, role_name: Optional[str], sender_id: str) -> str:
        with self._session_lock:
            cache = self._load_session_cache()
            session_key = self._build_session_key(role_name, sender_id)
            session_id = str(cache.get(session_key) or "").strip()
            if session_id:
                return session_id
            session_id = uuid.uuid4().hex
            cache[session_key] = session_id
            self._save_session_cache()
            logger.info(
                "[OpenClaw] Created persistent session: role=%s sender=%s session=%s",
                role_name or "__default_role__",
                sender_id,
                session_id,
            )
            return session_id

    async def stop_running(
        self,
        *,
        sender_id: Optional[str] = None,
        session_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        role_name: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.reload_config()
        sender = sender_id or self.default_sender_id
        resolved_session_id = session_id or self.get_or_create_persistent_session_id(
            role_name=role_name,
            sender_id=sender,
        )
        payload = {
            "channel_id": "neko",
            "sender_id": sender,
            "session_id": resolved_session_id,
            "text": "/stop",
            "task_id": task_id or "",
            "meta": {
                "reply_timeout": min(30.0, self.timeout),
                "conversation_id": conversation_id or resolved_session_id,
                "role_name": role_name or "",
            },
        }
        timeout = httpx.Timeout(min(30.0, self.http_timeout), connect=min(10.0, self.http_timeout))
        try:
            async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False) as client:
                response = await client.post(f"{self.base_url}/neko/send", json=payload)
                response.raise_for_status()
                data = response.json() if response.content else {}
        except httpx.TimeoutException:
            self.last_error = "OpenClaw stop message timed out"
            return {"success": False, "error": self.last_error}
        except httpx.HTTPStatusError as exc:
            self.last_error = f"OpenClaw stop message returned HTTP {exc.response.status_code}"
            return {"success": False, "error": self.last_error}
        except Exception as exc:
            self.last_error = f"OpenClaw stop message failed: {exc}"
            return {"success": False, "error": self.last_error}

        self.last_error = None
        return {
            "success": True,
            "session_id": resolved_session_id,
            "sender_id": sender,
            "raw": data if isinstance(data, dict) else {"data": data},
        }

    async def run_instruction(
        self,
        instruction: str,
        *,
        sender_id: Optional[str] = None,
        session_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        role_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.reload_config()
        sender = sender_id or self.default_sender_id
        resolved_session_id = session_id or self.get_or_create_persistent_session_id(
            role_name=role_name,
            sender_id=sender,
        )
        payload = {
            "channel_id": "neko",
            "sender_id": sender,
            "session_id": resolved_session_id,
            "text": instruction,
            "meta": {
                "reply_timeout": self.timeout,
                "conversation_id": conversation_id or resolved_session_id,
                "role_name": role_name or "",
            },
        }
        timeout = httpx.Timeout(self.http_timeout, connect=min(10.0, self.http_timeout))
        try:
            async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False) as client:
                response = await client.post(f"{self.base_url}/neko/send", json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            self.last_error = f"OpenClaw request timed out ({self.timeout}s)"
            return {"success": False, "error": self.last_error}
        except httpx.HTTPStatusError as exc:
            self.last_error = f"OpenClaw returned HTTP {exc.response.status_code}"
            return {"success": False, "error": self.last_error}
        except Exception as exc:
            self.last_error = f"OpenClaw connection failed: {exc}"
            return {"success": False, "error": self.last_error}

        if not isinstance(data, dict):
            self.last_error = "OpenClaw returned a non-object JSON response"
            return {"success": False, "error": self.last_error, "raw": data}

        reply = data.get("reply")
        reply_text = reply.strip() if isinstance(reply, str) else ""
        if not reply_text:
            self.last_error = "OpenClaw did not return a final reply"
            return {"success": False, "error": self.last_error, "raw": data}

        self.last_error = None
        return {
            "success": True,
            "reply": reply_text,
            "sender_id": data.get("sender_id") or sender,
            "session_id": data.get("session_id") or resolved_session_id,
            "raw": data,
        }
