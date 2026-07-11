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

"""Embedded user-plugin server hosting and plugin name cache.

Split out of the former monolithic ``app/agent_server.py``: the dedicated
uvicorn thread for the plugin HTTP server, plugin lifecycle start/stop,
the friendly-name cache and the deferred-task binding helper.
``_plugin_name_cache`` / ``_plugin_name_cache_time`` are rebindable module
globals owned here and are deliberately NOT re-exported by the package
facade (a facade snapshot would go stale on every rebind).
"""

import os
import sys
import time
import asyncio
import threading
from typing import Dict

import httpx

from config import USER_PLUGIN_SERVER_PORT
from . import _shared
from ._shared import (
    logger,
    PLUGIN_NAME_CACHE_TTL,
    _set_capability,
    _get_throttled_logger,
)

# Repo root — three levels up from this file (app/agent_server/plugin_host.py);
# the former monolith computed two levels up from app/agent_server.py.
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 插件名称缓存（避免频繁 HTTP 调用）
_plugin_name_cache: Dict[str, str] = {}
_plugin_name_cache_time: float = 0.0
_plugin_name_cache_lock = asyncio.Lock()


def _bind_deferred_task(plugin_id: str, reminder_id: str, agent_task_id: str) -> None:
    """Bind agent_task_id to the reminder record via the plugin service, for the callback when the daemon fires.
    bind_task is a fast operation (file write only); after triggering run, poll briefly for completion."""
    try:
        import time as _time
        with httpx.Client(timeout=5.0, proxy=None, trust_env=False) as client:
            # 1. 触发 bind_task entry
            resp = client.post(
                f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/runs",
                json={
                    "plugin_id": plugin_id,
                    "entry_id": "bind_task",
                    "args": {"reminder_id": reminder_id, "agent_task_id": agent_task_id},
                },
            )
            if resp.status_code != 200:
                logger.warning("[Deferred] bind_task start HTTP %s", resp.status_code)
                return
            run_id = resp.json().get("run_id")
            if not run_id:
                return
            # 2. 短暂轮询等待完成（bind_task 应在 <1s 内完成）
            for _ in range(20):
                _time.sleep(0.1)
                r = client.get(f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/runs/{run_id}")
                if r.status_code == 200:
                    if r.json().get("status", "") in ("succeeded", "failed", "canceled", "timeout"):
                        break
            logger.info("[Deferred] bind_task done: plugin=%s reminder=%s agent_task=%s", plugin_id, reminder_id, agent_task_id)
    except Exception as e:
        logger.warning("[Deferred] bind failed: plugin=%s reminder=%s error=%s", plugin_id, reminder_id, e)


async def _get_plugin_friendly_name(plugin_id: str) -> str | None:
    """Get the plugin's friendly name (for HUD display)

    Fetches the plugin list from the embedded plugin service's /plugins endpoint
    over HTTP, with caching to reduce request count.
    """
    global _plugin_name_cache, _plugin_name_cache_time

    now = time.time()
    async with _plugin_name_cache_lock:
        if _plugin_name_cache and (now - _plugin_name_cache_time) < PLUGIN_NAME_CACHE_TTL:
            return _plugin_name_cache.get(plugin_id)

    new_cache = {}
    cache_time = now
    try:
        async with httpx.AsyncClient(timeout=1.0, proxy=None, trust_env=False) as client:
            resp = await client.get(f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugins")
            if resp.status_code == 200:
                data = resp.json()
                plugins = data.get("plugins", [])
                for p in plugins:
                    if isinstance(p, dict):
                        pid = p.get("id")
                        pname = p.get("name")
                        if pid and pname:
                            new_cache[pid] = pname
                        elif pid:
                            new_cache[pid] = pid
                async with _plugin_name_cache_lock:
                    _plugin_name_cache = new_cache
                    _plugin_name_cache_time = cache_time
                return new_cache.get(plugin_id)
    except Exception as e:
        logger.warning("[AgentServer] Failed to fetch plugin names from port %s: %s", USER_PLUGIN_SERVER_PORT, e)

    # HTTP 调用失败，尝试本地 state（兼容某些部署场景）
    try:
        from plugin.core.state import state
        with state.acquire_plugins_read_lock():
            meta = state.plugins.get(plugin_id)
            if isinstance(meta, dict):
                return meta.get("name") or meta.get("id")
    except Exception:
        pass

    return None


async def _get_plugin_display_id(plugin_id: str) -> str:
    return (await _get_plugin_friendly_name(plugin_id)) or plugin_id


async def _start_embedded_user_plugin_server() -> None:
    """Start the plugin HTTP server in a dedicated thread with its own event loop.

    This isolates plugin HTTP handling from the agent's main event loop so that
    heavy agent work (LLM calls, task execution, ZMQ) cannot starve plugin
    requests and vice-versa.
    """
    if _shared.Modules.user_plugin_http_server is not None:
        return

    _plugin_package_root = os.path.join(_repo_root, "plugin")
    if _plugin_package_root not in sys.path:
        sys.path.insert(1, _plugin_package_root)

    try:
        from plugin.server.http_app import build_plugin_server_app
        import uvicorn
    except Exception as exc:
        raise RuntimeError(f"failed to import embedded user plugin server: {exc}") from exc

    if _shared.Modules.user_plugin_app is None:
        _shared.Modules.user_plugin_app = build_plugin_server_app()

    config = uvicorn.Config(
        _shared.Modules.user_plugin_app,
        host="127.0.0.1",
        port=USER_PLUGIN_SERVER_PORT,
        log_config=None,
        backlog=4096,
        timeout_keep_alive=30,
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    _shared.Modules.user_plugin_http_server = server

    ready = threading.Event()
    startup_error: list[BaseException] = []

    def _run_in_thread() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _shared.Modules._plugin_server_loop = loop

        async def _serve_and_signal():
            task = asyncio.ensure_future(server.serve())
            while not getattr(server, "started", False) and not task.done():
                await asyncio.sleep(0.05)
            if getattr(server, "started", False):
                ready.set()
            await task

        try:
            loop.run_until_complete(_serve_and_signal())
        except Exception as exc:
            startup_error.append(exc)
            logger.warning("[Agent] Embedded plugin server thread exited: %s", exc)
        finally:
            ready.set()  # unblock waiter even on failure
            loop.close()

    t = threading.Thread(target=_run_in_thread, name="plugin-server", daemon=True)
    t.start()
    _shared.Modules.user_plugin_http_task = t

    started = await asyncio.to_thread(ready.wait, 10.0)
    if not started or startup_error or not getattr(server, "started", False):
        server.should_exit = True
        detail = str(startup_error[0]) if startup_error else "timeout or server not started"
        raise RuntimeError(f"embedded user plugin server failed: {detail}")

    logger.info("[Agent] Embedded user plugin server started on 127.0.0.1:%s (isolated thread)", USER_PLUGIN_SERVER_PORT)


async def _stop_embedded_user_plugin_server() -> None:
    """Stop the plugin HTTP server running in its dedicated thread."""
    server = _shared.Modules.user_plugin_http_server
    thread = _shared.Modules.user_plugin_http_task
    _shared.Modules.user_plugin_http_server = None
    _shared.Modules.user_plugin_http_task = None

    if server is not None:
        server.should_exit = True

    if thread is None:
        return

    await asyncio.to_thread(thread.join, 10.0)
    if thread.is_alive():
        logger.warning("[Agent] Embedded user plugin server thread did not exit in time")
        if server is not None:
            server.force_exit = True


async def _ensure_plugin_lifecycle_started() -> bool:
    """Start the plugin lifecycle (load & run plugins). Returns True on success."""
    if _shared.Modules.plugin_lifecycle_started:
        return True
    if _shared.Modules._plugin_lifecycle_lock is None:
        _shared.Modules._plugin_lifecycle_lock = asyncio.Lock()
    async with _shared.Modules._plugin_lifecycle_lock:
        if _shared.Modules.plugin_lifecycle_started:
            return True
        try:
            from plugin.server.lifecycle import startup as plugin_lifecycle_startup
            await plugin_lifecycle_startup()
            _shared.Modules.plugin_lifecycle_started = True
            logger.info("[Agent] Plugin lifecycle started")
            return True
        except Exception as exc:
            logger.error("[Agent] Plugin lifecycle startup failed: %s", exc)
            return False


async def _ensure_plugin_lifecycle_stopped() -> None:
    """Stop the plugin lifecycle (stop plugin processes, cleanup)."""
    if not _shared.Modules.plugin_lifecycle_started:
        return
    if _shared.Modules._plugin_lifecycle_lock is None:
        _shared.Modules._plugin_lifecycle_lock = asyncio.Lock()
    async with _shared.Modules._plugin_lifecycle_lock:
        if not _shared.Modules.plugin_lifecycle_started:
            return
        try:
            from plugin.server.lifecycle import shutdown as plugin_lifecycle_shutdown
            await plugin_lifecycle_shutdown()
            logger.info("[Agent] Plugin lifecycle stopped")
        except Exception as exc:
            logger.warning("[Agent] Plugin lifecycle shutdown error: %s", exc)
        finally:
            _shared.Modules.plugin_lifecycle_started = False


async def _fire_user_plugin_capability_check() -> None:
    """Probe the user plugin server to determine if user_plugin capability is ready."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.0), proxy=None, trust_env=False) as client:
            r = await client.get(f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugins")
            if r.status_code == 200:
                data = r.json()
                plugins = data.get("plugins", []) if isinstance(data, dict) else []
                if plugins:
                    _set_capability("user_plugin", True, "")
                    logger.debug("[Agent] UserPlugin capability check passed (%d plugins)", len(plugins))
                else:
                    _set_capability("user_plugin", False, "AGENT_NO_PLUGINS_FOUND")
                    logger.debug("[Agent] UserPlugin capability check: no plugins found")
            else:
                _set_capability("user_plugin", False, "AGENT_PLUGIN_SERVER_ERROR")
                _get_throttled_logger().warning(
                    "user_plugin_capability_check_failed",
                    "[Agent] UserPlugin capability check failed: status %s",
                    r.status_code,
                )
    except Exception as e:
        _set_capability("user_plugin", False, "AGENT_PLUGIN_SERVER_ERROR")
        logger.debug("[Agent] UserPlugin capability check error: %s", e)
