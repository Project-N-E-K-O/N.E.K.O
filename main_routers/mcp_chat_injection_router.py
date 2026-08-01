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

"""HTTP boundary for the MCP chat-injection toggle (mcp_adapter plugin).

The enabled state lives in the ``mcp_adapter`` plugin's own config
(``mcp_adapter.inject_mcp``). These endpoints relay reads and writes to
the embedded user-plugin server; writes use ``hot-update`` with
``mode="permanent"`` so the flag survives restarts and is applied at
runtime when the plugin process is running.
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Request

from config import USER_PLUGIN_SERVER_PORT
from main_routers.system_router import _validate_local_mutation_request

router = APIRouter()

_MCP_ADAPTER_PLUGIN_ID = "mcp_adapter"
_MCP_ADAPTER_CONFIG_URL = (
    f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugin/{_MCP_ADAPTER_PLUGIN_ID}/config"
)


def _plugin_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(3.0, connect=1.0),
        proxy=None,
        trust_env=False,
    )


def _coerce_enabled_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _extract_inject_mcp(config: Any) -> bool:
    if not isinstance(config, dict):
        return False
    adapter_config = config.get("mcp_adapter")
    if not isinstance(adapter_config, dict):
        return False
    return _coerce_enabled_flag(adapter_config.get("inject_mcp", False))


async def _read_inject_mcp_state() -> dict[str, Any]:
    async with _plugin_http_client() as client:
        response = await client.get(_MCP_ADAPTER_CONFIG_URL)
    if response.status_code != 200:
        return {"enabled": False, "available": False}
    try:
        data = response.json()
    except Exception:
        return {"enabled": False, "available": False}
    if not isinstance(data, dict):
        return {"enabled": False, "available": False}
    return {"enabled": _extract_inject_mcp(data.get("config")), "available": True}


@router.get("/api/mcp-chat-injection/state")
async def get_mcp_chat_injection_state() -> dict[str, Any]:
    try:
        state = await _read_inject_mcp_state()
    except Exception:
        state = {"enabled": False, "available": False}
    return {"success": True, "state": state}


@router.post("/api/mcp-chat-injection/enabled")
async def set_mcp_chat_injection_enabled(request: Request, payload: dict[str, Any]) -> Any:
    validation_error = _validate_local_mutation_request(
        request,
        payload=payload,
        error_defaults={"success": False},
    )
    if validation_error is not None:
        return validation_error
    enabled = _coerce_enabled_flag(payload.get("enabled"))
    try:
        async with _plugin_http_client() as client:
            response = await client.post(
                f"{_MCP_ADAPTER_CONFIG_URL}/hot-update",
                json={
                    "config": {"mcp_adapter": {"inject_mcp": enabled}},
                    "mode": "permanent",
                    "profile": None,
                },
            )
        if response.status_code != 200:
            return {"success": False, "error": f"plugin config update failed: HTTP {response.status_code}"}
        try:
            data = response.json()
        except Exception:
            data = None
        if not isinstance(data, dict) or data.get("success") is not True:
            return {"success": False, "error": "plugin config update failed"}
    except Exception as exc:
        return {"success": False, "error": f"plugin config update failed: {exc}"}
    return {"success": True, "state": {"enabled": enabled, "available": True}}
