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
"""Memory review, farewell summary, and powerful-memory toggle endpoints."""

import asyncio
import json
from fastapi import Request
from utils.cloudsave_runtime import MaintenanceModeError
from fastapi.responses import JSONResponse

from main_routers import memory_router as _mr
from ._shared import (
    logger,
    router,
)

class _InvalidMemoryToggleValue(ValueError):
    """A readable configuration contains a setting that can be explicitly repaired."""


def _load_memory_toggle_config(config_manager):
    """Default a missing file to an empty object without hiding read errors."""
    from utils.config_manager.json_update import load_json_config_snapshot
    return load_json_config_snapshot(config_manager, 'core_config.json')


def _load_memory_toggle_enabled(config_manager, key):
    """Default missing settings to on, but reject invalid stored values."""
    config_data = _load_memory_toggle_config(config_manager)
    enabled = config_data.get(key, True)
    if not isinstance(enabled, bool):
        raise _InvalidMemoryToggleValue('Invalid memory setting value')
    return enabled


@router.get('/farewell-summary')
async def get_farewell_summary():
    """Read-only memory summary of the active character for the desktop exit farewell card.

    ``memory_count`` / ``conversation_count`` / ``flashback``; a field that
    cannot be read is omitted (``flashback`` is ``null``). No LLM, sync or
    writes: the desktop waits at most 1.2 s for this during quit.
    """
    try:
        from main_logic.farewell_summary import build_farewell_summary

        return await build_farewell_summary()
    except Exception as exc:
        logger.warning("farewell-summary unavailable: %s", type(exc).__name__)
        return {"flashback": None}


@router.get('/review_config')
async def get_review_config():
    """Get the memory review configuration."""
    try:
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        async with _mr._memory_toggle_write_lock:
            enabled = await asyncio.to_thread(
                _mr._load_memory_toggle_enabled, config_manager, 'recent_memory_auto_review'
            )
        return {"enabled": enabled}
    except Exception as e:
        logger.error(f"读取记忆整理配置失败: {e}")
        error = {"error": "Failed to read memory review configuration"}
        if isinstance(e, _InvalidMemoryToggleValue):
            error["code"] = "invalid_memory_setting"
        return JSONResponse(error, status_code=503)


@router.post('/review_config')
async def update_review_config(request: Request):
    """Update the memory review configuration."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"success": False, "error": "Request body must be JSON"}, status_code=400)
    if not isinstance(data, dict) or not isinstance(data.get('enabled'), bool):
        return JSONResponse({"success": False, "error": "enabled must be a boolean"}, status_code=400)
    enabled = data['enabled']

    try:
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        async with _mr._memory_toggle_write_lock:
            def _set_review(config_data):
                config_data['recent_memory_auto_review'] = enabled

            await config_manager.aupdate_json_config('core_config.json', _set_review)

            logger.info(f"记忆整理配置已更新: enabled={enabled}")
            return {"success": True, "enabled": enabled}
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.error(f"更新记忆整理配置失败: {e}")
        return {"success": False, "error": str(e)}


@router.get('/powerful_memory_config')
async def get_powerful_memory_config():
    """Get the powerful-memory toggle. Defaults to True (for backward compatibility with existing users)."""
    try:
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        async with _mr._memory_toggle_write_lock:
            enabled = await asyncio.to_thread(
                _mr._load_memory_toggle_enabled, config_manager, 'powerful_memory_enabled'
            )
        return {"enabled": enabled}
    except Exception as e:
        logger.error(f"读取强力记忆配置失败: {e}")
        error = {"error": "Failed to read powerful memory configuration"}
        if isinstance(e, _InvalidMemoryToggleValue):
            error["code"] = "invalid_memory_setting"
        return JSONResponse(error, status_code=503)


@router.post('/powerful_memory_config')
async def update_powerful_memory_config(request: Request):
    """Update the powerful-memory toggle.

    Turning it off stops all new LLM paths introduced by the evidence RFC
    (Stage-2 / promote_merge / rebuttal / negative-keyword / fact_dedup /
    persona corrections), keeping check_feedback for proactive-chat responses
    as the only evidence channel. When switching on→off, reset confirmed_at of
    all confirmed reflections to now to avoid an immediate bulk promote.
    """
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"success": False, "error": "Request body must be JSON"}, status_code=400)
    if not isinstance(data, dict) or not isinstance(data.get('enabled'), bool):
        return JSONResponse({"success": False, "error": "enabled must be a boolean"}, status_code=400)
    enabled = data['enabled']

    try:
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        async with _mr._memory_toggle_write_lock:
            config_data = await asyncio.to_thread(
                _load_memory_toggle_config, config_manager
            )

            prev_enabled = config_data.get('powerful_memory_enabled', True)

            # ON -> OFF: migrate first, resetting every character's confirmed-reflection confirmed_at
            # to now so time-driven fallback gets a full 14-day countdown;
            # save configuration only after success. Otherwise failed migration leaves configuration
            # False, and later disable clicks never enter prev_enabled and not enabled again,
            # permanently missing old confirmed_at anchors; old confirmed reflections may immediately
            # promote through time-driven fallback. Require atomic success or failure of both.
            # Call memory_server via HTTP: this router lives in main_server; directly importing
            # memory_server gets a fresh copy with reflection_engine None, making migration
            # silently noop. memory_server runs separately at MEMORY_SERVER_PORT,
            # where startup hooks initialize reflection_engine.
            # Only a confirmed false may skip migration; legacy values are unknown.
            if prev_enabled is not False and not enabled:
                try:
                    from config import MEMORY_SERVER_PORT
                    from utils.internal_http_client import get_internal_http_client
                    client = get_internal_http_client()
                    resp = await client.post(
                        f"http://127.0.0.1:{MEMORY_SERVER_PORT}/internal/memory/reset_confirmed_at",
                        timeout=10.0,
                    )
                    if resp.status_code != 200:
                        logger.warning(
                            f"强力记忆切换 migration HTTP 状态码 {resp.status_code}，配置未保存"
                        )
                        return {
                            "success": False,
                            "error": f"migration HTTP {resp.status_code}",
                        }
                    payload = resp.json()
                    if not isinstance(payload, dict) or not payload.get('ok'):
                        err = payload.get('error', 'migration returned ok=false') if isinstance(payload, dict) else 'migration payload invalid'
                        logger.warning(f"强力记忆切换 migration 失败，配置未保存: {err}")
                        return {"success": False, "error": err}
                    migrated = int(payload.get('count', 0))
                    logger.info(
                        f"强力记忆切换 ON→OFF：已重置 {migrated} 条 confirmed "
                        f"reflection 的 confirmed_at 锚点"
                    )
                except Exception as e:
                    logger.warning(f"强力记忆切换 migration 异常，配置未保存: {e}")
                    return {"success": False, "error": str(e)}

            # Persist configuration only after successful migration or a non-ON->OFF change, keeping
            # frontend toggle state consistent with actual reflection_engine state.
            # config_data above determines prev_enabled only. Reread the file after migration before
            # writing, preserving fields others saved during the migration seconds.
            # Only this route writes the key and _memory_toggle_write_lock serializes it,
            # so prev_enabled remains valid at persistence time.
            def _set_powerful(fresh_config):
                fresh_config['powerful_memory_enabled'] = enabled

            await config_manager.aupdate_json_config('core_config.json', _set_powerful)

            logger.info(f"强力记忆配置已更新: enabled={enabled} (prev={prev_enabled})")
            return {"success": True, "enabled": enabled}
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.error(f"更新强力记忆配置失败: {e}")
        return {"success": False, "error": str(e)}
