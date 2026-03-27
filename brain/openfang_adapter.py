# -*- coding: utf-8 -*-
"""
OpenFang Agent 执行后端适配器

职责 (仅通信，不管进程):
1. 通过 A2A API 下发任务
2. 轮询任务状态
3. API Key 下发与配置同步
4. 健康检查 (仅检测连通性，不负责启停 — 启停由 Electron 管理)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Callable

import httpx

from config import OPENFANG_BASE_URL, TOOL_SERVER_PORT
from utils.config_manager import get_config_manager

# OpenFang LLM proxy — 运行在 agent_server 上，补全 OpenAI 兼容性字段
_LLM_PROXY_BASE_URL = f"http://127.0.0.1:{TOOL_SERVER_PORT}/openfang-llm-proxy"

# ── Provider 检测 ──────────────────────────────────────────
# OpenFang 原生支持多种 provider: anthropic, openai, groq, gemini, deepseek, ollama 等
# 根据用户配置的 agent API base_url 推断最合适的 provider 和是否需要 proxy

def _detect_provider_info(base_url: str, model: str) -> dict:
    """
    根据用户配置的 agent API 推断 OpenFang provider 和是否需要 LLM proxy。

    Returns:
        {
            "provider": "openai" | "anthropic" | "gemini" | "deepseek" | "groq" | ...,
            "needs_proxy": bool,      # 是否需要经过 LLM proxy（补全兼容性字段）
            "effective_url": str,     # 给 OpenFang 的 base_url（proxy URL 或直连）
            "api_key_env": str,       # 环境变量名 (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.)
        }
    """
    url_lower = base_url.lower()
    model_lower = model.lower()

    # Anthropic 原生 API — OpenFang 直接支持，无需 proxy
    if "anthropic.com" in url_lower or "api.anthropic" in url_lower:
        return {
            "provider": "anthropic",
            "needs_proxy": False,
            "effective_url": base_url,
            "api_key_env": "ANTHROPIC_API_KEY",
        }

    # OpenAI 原生 API — OpenFang 直接支持，无需 proxy
    if "api.openai.com" in url_lower:
        return {
            "provider": "openai",
            "needs_proxy": False,
            "effective_url": base_url,
            "api_key_env": "OPENAI_API_KEY",
        }

    # Groq
    if "groq.com" in url_lower or "groq" in model_lower:
        return {
            "provider": "groq",
            "needs_proxy": False,
            "effective_url": base_url,
            "api_key_env": "GROQ_API_KEY",
        }

    # Gemini / Google AI
    if "generativelanguage.googleapis.com" in url_lower or "gemini" in model_lower:
        return {
            "provider": "gemini",
            "needs_proxy": False,
            "effective_url": base_url,
            "api_key_env": "GEMINI_API_KEY",
        }

    # DeepSeek
    if "deepseek.com" in url_lower or "deepseek" in model_lower:
        return {
            "provider": "deepseek",
            "needs_proxy": False,
            "effective_url": base_url,
            "api_key_env": "DEEPSEEK_API_KEY",
        }

    # Ollama (local) — detect localhost, 127.0.0.1, 0.0.0.0, [::1]
    _loopback_hosts = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]")
    _is_loopback = any(h in url_lower for h in _loopback_hosts)
    if _is_loopback and ("11434" in url_lower or "ollama" in model_lower):
        return {
            "provider": "ollama",
            "needs_proxy": False,
            "effective_url": base_url,
            "api_key_env": "",
        }

    # OpenRouter / lanlan.app / 其他 OpenAI-compatible 代理
    # 这些端点可能转发到不同模型（如 Gemini），返回格式可能不完全兼容
    # 需要经过 proxy 补全 completion_tokens、修复 malformed_function_call 等
    return {
        "provider": "openai",  # OpenFang 用 openai driver 处理 OpenAI-compatible 端点
        "needs_proxy": True,
        "effective_url": _LLM_PROXY_BASE_URL,
        "api_key_env": "OPENAI_API_KEY",
    }

logger = logging.getLogger("openfang_adapter")


# ──────────────────────────────────────────────────────────────
#  Data models
# ──────────────────────────────────────────────────────────────

@dataclass
class OpenFangTaskStatus:
    """A2A 任务状态快照"""
    task_id: str
    status: str  # "pending" | "running" | "completed" | "failed" | "cancelled"
    result: Optional[str] = None
    error: Optional[str] = None
    steps_taken: int = 0
    agent_name: Optional[str] = None
    artifacts: List[Dict] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────
#  Adapter
# ──────────────────────────────────────────────────────────────

class OpenFangAdapter:
    """
    OpenFang A2A 适配器

    接口约定 (与 ComputerUseAdapter / BrowserUseAdapter 对齐):
    - is_available()             -> Dict[str, Any]
    - run_instruction(...)       -> Dict[str, Any]
    - cancel_running(...)        -> None
    - check_connectivity()       -> bool

    本适配器 **不管理** OpenFang 进程生命周期。
    进程由 Electron main process 管理，本层只做 HTTP 通信。
    """

    def __init__(self, base_url: Optional[str] = None):
        self.base_url: str = base_url or OPENFANG_BASE_URL
        self.init_ok: bool = False
        self.last_error: Optional[str] = None

        # neko_task_id -> openfang_task_id 映射
        self._active_tasks: Dict[str, str] = {}
        self._api_key: Optional[str] = None  # OpenFang Bearer token
        self._config_synced: bool = False
        self._cached_version: Optional[str] = None
        self._cached_tools_count: Optional[int] = None
        self._cached_tools_list: List[str] = []
        self._executor_agent_id: Optional[str] = None
        self._last_synced_config_hash: Optional[str] = None  # 检测配置变更

    # ──────────────────────────────────────────
    #  公开接口
    # ──────────────────────────────────────────

    def is_available(self) -> Dict[str, Any]:
        """返回当前可用性状态 (与 ComputerUseAdapter 格式对齐)。"""
        return {
            "enabled": True,
            "ready": self.init_ok,
            "reasons": [self.last_error] if self.last_error else [],
            "provider": "openfang",
            "version": self._cached_version or "unknown",
            "tools_count": self._cached_tools_count or 0,
        }

    def get_tools_list(self) -> List[str]:
        """返回 OpenFang 侧可用工具名称列表 (缓存)。"""
        return list(self._cached_tools_list)

    def _compute_config_hash(self) -> str:
        """计算当前 agent API 配置的 hash，用于检测变更。
        注: get_model_api_config() 每次调用时从文件/内存重新读取，无缓存过期问题。
        """
        import hashlib
        cm = get_config_manager()
        agent_cfg = cm.get_model_api_config('agent')
        key_fields = f"{agent_cfg.get('model', '')}|{agent_cfg.get('base_url', '')}|{agent_cfg.get('api_key', '')}"
        return hashlib.md5(key_fields.encode()).hexdigest()

    async def _ensure_config_synced(self) -> None:
        """每次执行任务前检查配置是否变化，有变化则重新同步到 OpenFang。"""
        current_hash = self._compute_config_hash()
        if current_hash != self._last_synced_config_hash:
            logger.info("[OpenFang] Config change detected (hash %s → %s), re-syncing...",
                        self._last_synced_config_hash, current_hash)
            try:
                ok = await self.sync_config()
                if ok:
                    self._last_synced_config_hash = current_hash
                    # 也尝试重新注册 executor agent
                    await self.push_agent_manifest()
                    logger.info("[OpenFang] Config re-sync completed, executor_agent_id=%s",
                                self._executor_agent_id)
            except Exception as e:
                logger.warning("[OpenFang] Config re-sync failed: %s", e)

    async def run_instruction(
        self,
        instruction: str,
        session_id: Optional[str] = None,
        on_progress: Optional[Callable] = None,
        timeout: float = 300.0,
        local_task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        向 OpenFang 提交任务并等待结果。

        优先使用 POST /api/agents/{id}/message（直接路由到已注册的 neko-executor），
        fallback 到 POST /a2a/tasks/send（可能路由到默认 assistant agent）。
        """
        try:
            # 检查配置是否变化，变化则重新同步
            await self._ensure_config_synced()
            # 方案 A：直接给注册的 agent 发消息（确保用 openai provider）
            print(f"[OpenFang DEBUG] executor_agent_id={self._executor_agent_id}")
            if self._executor_agent_id:
                result = await self._send_direct_message(instruction, timeout, local_task_id=local_task_id)
                print(f"[OpenFang DEBUG] _send_direct_message returned: {result is not None}, type={type(result).__name__}")
                if result is not None:
                    return result
                print("[OpenFang DEBUG] Direct message returned None, falling back to A2A")

            # 方案 B：通过 A2A 协议（路由到默认 agent）
            print("[OpenFang DEBUG] Using A2A fallback")
            return await self._send_via_a2a(instruction, session_id, on_progress, timeout, local_task_id=local_task_id)

        except httpx.HTTPStatusError as e:
            self.last_error = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            return {"success": False, "error": self.last_error}
        except Exception as e:
            self.last_error = str(e)
            return {"success": False, "error": self.last_error}

    async def _send_direct_message(
        self, instruction: str, timeout: float, local_task_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        POST /api/agents/{id}/message — 直接给指定 agent 发消息。
        同步阻塞直到 agent 完成。返回 None 表示此方式不可用。
        """
        agent_id = self._executor_agent_id
        if not agent_id:
            return None

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
                resp = await client.post(
                    f"{self.base_url}/api/agents/{agent_id}/message",
                    json={"message": instruction},
                    headers=self._auth_headers(),
                )
                if resp.status_code == 404:
                    # 端点不存在或 agent 已被清理
                    logger.debug("[OpenFang] /api/agents/%s/message returned 404", agent_id)
                    return None
                print(f"[OpenFang DEBUG] /api/agents/{agent_id}/message HTTP {resp.status_code}")
                resp.raise_for_status()

                # 先看 raw text
                raw_text = resp.text[:2000]
                print(f"[OpenFang DEBUG] RAW TEXT (first 2000): {raw_text}")

                data = resp.json()
                import json as _json
                print(f"[OpenFang DEBUG] RAW JSON type={type(data).__name__}, keys={list(data.keys()) if isinstance(data, dict) else 'N/A'}")

                # ── 异步任务检测 ──
                # 如果返回了 task_id / id 且 status 不是 completed，
                # 说明这是异步执行，需要轮询。
                if isinstance(data, dict):
                    task_id = data.get("task_id") or data.get("id")
                    task_status = data.get("status", "")
                    if task_id and task_status not in ("completed", "done", "finished", "success", ""):
                        print(f"[OpenFang DEBUG] Direct message returned async task: id={task_id}, status={task_status}")
                        print(f"[OpenFang DEBUG] Switching to poll mode for task {task_id}")
                        self._active_tasks[task_id] = task_id
                        if local_task_id:
                            self._active_tasks[local_task_id] = task_id
                        try:
                            poll_result = await self._poll_task(task_id, timeout=timeout)
                        finally:
                            self._active_tasks.pop(task_id, None)
                            if local_task_id:
                                self._active_tasks.pop(local_task_id, None)
                        return {
                            "success": poll_result.status == "completed",
                            "result": poll_result.result or "",
                            "steps": poll_result.steps_taken,
                            "agent_name": poll_result.agent_name or "neko-executor",
                            "artifacts": poll_result.artifacts,
                            "error": poll_result.error,
                            "remote_task_id": task_id,
                        }
                    # 如果 id 存在且 status 为空（可能是 fire-and-forget），也轮询
                    if task_id and not task_status and not data.get("response") and not data.get("result"):
                        print(f"[OpenFang DEBUG] Direct message returned task id={task_id} with no result, trying poll")
                        self._active_tasks[task_id] = task_id
                        if local_task_id:
                            self._active_tasks[local_task_id] = task_id
                        try:
                            poll_result = await self._poll_task(task_id, timeout=timeout)
                        finally:
                            self._active_tasks.pop(task_id, None)
                            if local_task_id:
                                self._active_tasks.pop(local_task_id, None)
                        if poll_result.result:
                            return {
                                "success": poll_result.status == "completed",
                                "result": poll_result.result or "",
                                "steps": poll_result.steps_taken,
                                "agent_name": poll_result.agent_name or "neko-executor",
                                "artifacts": poll_result.artifacts,
                                "error": poll_result.error,
                                "remote_task_id": task_id,
                            }

                # ── 同步结果解析 ──
                result_text = ""
                if isinstance(data, str):
                    result_text = data
                elif isinstance(data, dict):
                    # 用增强版 _extract_result 来统一解析
                    result_text = self._extract_result(data)
                    # 如果 _extract_result 也没找到，试基本字段
                    if not result_text:
                        result_text = (
                            data.get("response") or
                            data.get("result") or
                            data.get("message") or
                            data.get("text") or
                            data.get("output") or
                            ""
                        )
                        if isinstance(result_text, dict):
                            result_text = str(result_text)

                print(f"[OpenFang DEBUG] Direct message final result_text len={len(result_text)}, first 200: {result_text[:200]}")

                success = resp.status_code == 200 and bool(result_text)
                logger.info("[OpenFang] Direct message completed: agent=%s, len=%d, success=%s",
                            agent_id, len(result_text), success)

                return {
                    "success": success,
                    "result": result_text,
                    "steps": data.get("iterations", 1) if isinstance(data, dict) else 1,
                    "agent_name": "neko-executor",
                    "artifacts": data.get("artifacts", []) if isinstance(data, dict) else [],
                    "error": data.get("error") if isinstance(data, dict) else None,
                }

        except httpx.TimeoutException:
            logger.warning("[OpenFang] Direct message timed out after %.0fs", timeout)
            return {"success": False, "error": f"Agent timed out after {timeout}s"}
        except (httpx.ConnectError, httpx.ConnectTimeout, ConnectionRefusedError, OSError) as e:
            # 连接级别错误: endpoint 不可用，允许 fallback 到 A2A
            logger.debug("[OpenFang] Direct message connect-level failure: %s", e)
            return None
        except httpx.HTTPStatusError as e:
            # 5xx 网关级别错误也允许 fallback
            if e.response.status_code in (502, 503, 504):
                logger.debug("[OpenFang] Direct message 5xx gateway error: %s", e)
                return None
            logger.warning("[OpenFang] Direct message HTTP error: %s", e)
            return {"success": False, "error": f"HTTP {e.response.status_code}: {str(e)[:200]}"}
        except Exception as e:
            # 其他错误（解析/逻辑错误）: 不 fallback，直接返回失败避免双执行
            logger.warning("[OpenFang] Direct message failed (no fallback): %s", e)
            return {"success": False, "error": str(e)[:500]}

    async def _send_via_a2a(
        self,
        instruction: str,
        session_id: Optional[str],
        on_progress: Optional[Callable],
        timeout: float,
        local_task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """通过 A2A 协议提交任务（路由到 OpenFang 默认 agent）。"""
        # OpenFang A2A reads message from request["params"]["message"]["parts"]
        # Must wrap in "params" object — NOT top-level "message" or "messages"
        task_payload = {
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": instruction}],
                },
                "sessionId": session_id,
            },
        }
        print(f"[OpenFang DEBUG] A2A payload instruction: {instruction[:200]}")

        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            resp = await client.post(
                f"{self.base_url}/a2a/tasks/send",
                json=task_payload,
                headers=self._auth_headers(),
            )
            print(f"[OpenFang DEBUG] A2A /a2a/tasks/send HTTP {resp.status_code}")
            resp.raise_for_status()
            task_data = resp.json()
            print(f"[OpenFang DEBUG] A2A send response keys={list(task_data.keys()) if isinstance(task_data, dict) else 'N/A'}")
            print(f"[OpenFang DEBUG] A2A send response (first 1000): {str(task_data)[:1000]}")
            of_task_id = task_data["id"]

        self._active_tasks[of_task_id] = of_task_id
        if local_task_id:
            self._active_tasks[local_task_id] = of_task_id
        try:
            result = await self._poll_task(of_task_id, on_progress, timeout=timeout)
        finally:
            self._active_tasks.pop(of_task_id, None)
            if local_task_id:
                self._active_tasks.pop(local_task_id, None)
        print(f"[OpenFang DEBUG] A2A poll result: status={result.status}, result_len={len(result.result or '')}, error={result.error}")

        return {
            "success": result.status == "completed",
            "result": result.result or "",
            "steps": result.steps_taken,
            "agent_name": result.agent_name,
            "artifacts": result.artifacts,
            "error": result.error,
            "remote_task_id": of_task_id,
        }

    def register_local_task(self, local_id: str, remote_id: str) -> None:
        """注册本地 task_id 到远程 OpenFang task_id 的映射，供 cancel 使用。"""
        self._active_tasks[local_id] = remote_id

    def unregister_local_task(self, local_id: str) -> None:
        """移除本地 task_id 映射。"""
        self._active_tasks.pop(local_id, None)

    async def cancel_running(self, task_id: Optional[str] = None) -> None:
        """取消正在运行的任务。task_id=None 时取消所有。"""
        if task_id and task_id in self._active_tasks:
            targets = [self._active_tasks[task_id]]
        else:
            targets = list(self._active_tasks.values())

        async with httpx.AsyncClient(timeout=5.0) as client:
            for of_id in targets:
                try:
                    await client.post(
                        f"{self.base_url}/a2a/tasks/{of_id}/cancel",
                        headers=self._auth_headers(),
                    )
                except Exception as e:
                    logger.warning("Cancel task %s failed: %s", of_id, e)

    def check_connectivity(self) -> bool:
        """
        同步健康检查 (可在线程池中调用)。
        仅检测连通性，不负责启停 — 启停是 Electron 的事。
        """
        try:
            with httpx.Client(timeout=3.0) as client:
                r = client.get(
                    f"{self.base_url}/api/health",
                    headers=self._auth_headers(),
                )
                self.init_ok = r.status_code == 200
                if self.init_ok:
                    self.last_error = None
                    data = r.json()
                    self._cached_version = data.get("version")
                    self._cached_tools_count = data.get("tools_count")
                return self.init_ok
        except Exception as e:
            self.last_error = str(e)
            self.init_ok = False
            return False

    # ──────────────────────────────────────────
    #  配置同步 (NEKO Python → OpenFang)
    # ──────────────────────────────────────────

    async def sync_config(self) -> bool:
        """
        将 NEKO 的 Agent LLM 配置推送到 OpenFang（三层保障）。

        根据用户配置自动检测 provider 类型：
        - Anthropic/OpenAI/Groq/Gemini/DeepSeek → 直连（OpenFang 原生支持）
        - OpenRouter/lanlan.app 等代理 → 经过 LLM proxy（修复兼容性问题）

        1. POST /api/providers/{provider}/key  — 运行时推送 API key
        2. PUT  /api/providers/{provider}/url  — 运行时覆盖 base_url
        3. 写 ~/.openfang/config.toml           — 设置 [default_model] + [provider_urls]
        """
        cm = get_config_manager()
        agent_cfg = cm.get_model_api_config('agent')

        api_key = (agent_cfg.get("api_key") or "").strip()
        base_url = (agent_cfg.get("base_url") or "").strip()
        model = (agent_cfg.get("model") or "").strip()
        if not model:
            logger.warning("[OpenFang] Agent model not configured, cannot sync")
            return False

        if not api_key or not base_url:
            logger.warning("[OpenFang] Agent API 未配置 (key=%s, url=%s), 跳过同步",
                           "set" if api_key else "empty", "set" if base_url else "empty")
            return False

        # 自动检测 provider 和是否需要 proxy
        pinfo = _detect_provider_info(base_url, model)
        self._provider_info = pinfo
        provider = pinfo["provider"]
        effective_url = pinfo["effective_url"]
        needs_proxy = pinfo["needs_proxy"]

        logger.info("[OpenFang] Detected provider=%s, needs_proxy=%s, model=%s, url=%s",
                    provider, needs_proxy, model, base_url)

        ok = False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # (1) Push API key — 尝试推送到检测到的 provider
                key_pushed = False
                provider_key_url = f"{self.base_url}/api/providers/{provider}/key"
                for payload in [
                    {"key": api_key},
                    {"api_key": api_key},
                    {"key": api_key, "base_url": base_url, "model": model},
                    api_key,  # plain string body
                ]:
                    try:
                        if isinstance(payload, str):
                            resp = await client.post(
                                provider_key_url,
                                content=payload,
                                headers={**self._auth_headers(), "Content-Type": "text/plain"},
                            )
                        else:
                            resp = await client.post(
                                provider_key_url,
                                json=payload,
                                headers=self._auth_headers(),
                            )
                        if resp.status_code == 200:
                            key_pushed = True
                            logger.info("[OpenFang] API key synced to %s (format=%s): model=%s",
                                        provider, type(payload).__name__, model)
                            break
                    except Exception as ep:
                        logger.debug("[OpenFang] Key push attempt failed: %s", ep)

                # 如果检测到的 provider push 失败，也尝试 openai（通用后备）
                if not key_pushed and provider != "openai":
                    for payload in [{"key": api_key}, api_key]:
                        try:
                            if isinstance(payload, str):
                                resp = await client.post(
                                    f"{self.base_url}/api/providers/openai/key",
                                    content=payload,
                                    headers={**self._auth_headers(), "Content-Type": "text/plain"},
                                )
                            else:
                                resp = await client.post(
                                    f"{self.base_url}/api/providers/openai/key",
                                    json=payload,
                                    headers=self._auth_headers(),
                                )
                            if resp.status_code == 200:
                                key_pushed = True
                                logger.info("[OpenFang] API key synced to openai fallback")
                                break
                        except Exception:
                            pass

                if not key_pushed:
                    logger.warning("[OpenFang] All key push formats failed, relying on config.toml")

                # (2) Override provider base_url
                push_url = effective_url  # proxy URL 或直连 URL
                print(f"[OpenFang DEBUG] Pushing provider URL: {push_url} (real: {base_url}, proxy={needs_proxy})")
                try:
                    resp2 = await client.put(
                        f"{self.base_url}/api/providers/{provider}/url",
                        json={"base_url": push_url},
                        headers=self._auth_headers(),
                    )
                    if resp2.status_code == 200:
                        logger.info("[OpenFang] Provider %s URL set to: %s", provider, push_url)
                except Exception as e2:
                    logger.debug("[OpenFang] PUT provider url failed (non-fatal): %s", e2)

        except Exception as e:
            logger.error("[OpenFang] HTTP config sync failed: %s", e)

        # (3) Write config.toml
        file_written = False
        try:
            self._write_openfang_model_config(api_key, base_url, model)
            file_written = True
        except Exception as e:
            logger.debug("[OpenFang] config.toml write failed (non-fatal): %s", e)

        # (4) 通过 API 热更新 default_model（确保运行中的 OpenFang 用新 model）
        reload_ok = False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # 用 /api/config/set 设置 default_model.model + provider + base_url
                for path_key, value in [
                    ("default_model.model", model),
                    ("default_model.provider", provider),
                    ("default_model.base_url", effective_url),
                ]:
                    try:
                        r = await client.post(
                            f"{self.base_url}/api/config/set",
                            json={"path": path_key, "value": value},
                            headers=self._auth_headers(),
                        )
                        if r.status_code == 200:
                            logger.info("[OpenFang] config/set %s = %s", path_key, value[:60])
                        else:
                            logger.debug("[OpenFang] config/set %s → %d: %s",
                                         path_key, r.status_code, r.text[:100])
                    except Exception as e_set:
                        logger.debug("[OpenFang] config/set %s failed: %s", path_key, e_set)

                # 兜底: 触发一次 config reload
                try:
                    r2 = await client.post(
                        f"{self.base_url}/api/config/reload",
                        headers=self._auth_headers(),
                    )
                    logger.info("[OpenFang] config/reload → %d: %s",
                                r2.status_code, r2.text[:200])
                    reload_ok = r2.status_code == 200
                except Exception as e_reload:
                    logger.debug("[OpenFang] config/reload failed: %s", e_reload)
        except Exception as e:
            logger.debug("[OpenFang] Hot-update default_model failed (non-fatal): %s", e)

        ok = any([key_pushed, file_written, reload_ok])
        self._config_synced = ok
        if ok:
            self._last_synced_config_hash = self._compute_config_hash()
        return ok

    @staticmethod
    def _write_openfang_model_config(api_key: str, base_url: str, model: str) -> None:
        """
        确保 ~/.openfang/config.toml 包含 [default_model] 和 [provider_urls] 配置。
        根据 provider 类型决定是否通过 proxy。
        """
        import os, re
        home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""
        config_dir = os.path.join(home, ".openfang")
        config_path = os.path.join(config_dir, "config.toml")

        os.makedirs(config_dir, exist_ok=True, mode=0o700)
        try:
            os.chmod(config_dir, 0o700)
        except OSError:
            pass  # Windows 不支持 POSIX 权限

        content = ""
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read()

        # 根据 base_url 检测 provider
        pinfo = _detect_provider_info(base_url, model)
        provider = pinfo["provider"]
        effective_url = pinfo["effective_url"]
        api_key_env = pinfo["api_key_env"]

        dm_block = (
            f'[default_model]\n'
            f'provider = "{provider}"\n'
            f'model = "{model}"\n'
            f'api_key = "{api_key}"\n'
        )
        if api_key_env:
            dm_block += f'api_key_env = "{api_key_env}"\n'
        dm_block += f'base_url = "{effective_url}"\n'
        if "[default_model]" in content:
            # Replace existing [default_model] section (up to next section or EOF)
            content = re.sub(
                r'\[default_model\][^\[]*',
                dm_block + "\n",
                content,
                count=1,
            )
        else:
            content = content.rstrip() + "\n\n" + dm_block

        # --- [provider_urls] section ---
        pu_block = f'[provider_urls]\n{provider} = "{effective_url}"\n'
        if "[provider_urls]" in content:
            content = re.sub(
                r'\[provider_urls\][^\[]*',
                pu_block + "\n",
                content,
                count=1,
            )
        else:
            content = content.rstrip() + "\n\n" + pu_block

        # --- Set env vars for this process (may be inherited by children) ---
        os.environ["OPENAI_API_KEY"] = api_key
        os.environ["NEKO_OPENFANG_KEY"] = api_key

        with open(config_path, "w", encoding="utf-8") as f:
            f.write(content)
        try:
            os.chmod(config_path, 0o600)
        except OSError:
            pass  # Windows 不支持 POSIX 权限

        logger.info("[OpenFang] config.toml updated: default_model=openai/%s, provider_urls.openai=%s",
                    model, base_url)

    @staticmethod
    def _ensure_openfang_env_var(var_name: str, value: str) -> None:
        """
        确保 OpenFang 进程能读到指定环境变量。
        由于 OpenFang 由 Electron 启动（非 Python 子进程），os.environ 不可达。
        写入 ~/.openfang/.env 文件 + 当前进程的 os.environ。
        """
        import os
        os.environ[var_name] = value

        home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""
        env_path = os.path.join(home, ".openfang", ".env")
        try:
            # 读现有 .env 内容
            existing = ""
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    existing = f.read()
            # 替换或追加
            import re
            pattern = rf'^{re.escape(var_name)}=.*$'
            new_line = f'{var_name}={value}'
            if re.search(pattern, existing, re.MULTILINE):
                existing = re.sub(pattern, new_line, existing, flags=re.MULTILINE)
            else:
                existing = existing.rstrip() + f"\n{new_line}\n"
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(existing)
            try:
                os.chmod(env_path, 0o600)
            except OSError:
                pass  # Windows
            logger.info("[OpenFang] .env updated: %s=***%s", var_name, value[-4:] if len(value) > 4 else "****")
        except Exception as e:
            logger.debug("[OpenFang] .env write failed (non-fatal): %s", e)

    async def push_agent_manifest(self, agent_config: Optional[Dict] = None) -> Optional[str]:
        """
        向 OpenFang 注册一个无人格执行 Agent。
        使用 TOML manifest 格式，明确指定 openai provider。
        返回 agent_id 或 None。
        """
        agent_config = agent_config or {}

        # 从 NEKO agent config 取 model 名称
        cm = get_config_manager()
        neko_agent_cfg = cm.get_model_api_config('agent')
        model_name = (agent_config.get("model") or neko_agent_cfg.get("model") or "").strip()
        if not model_name:
            logger.warning("[OpenFang] No model configured, cannot push agent manifest")
            return None
        agent_base_url = (neko_agent_cfg.get("base_url") or "").strip()

        agent_name = agent_config.get("name", "neko-executor")
        system_prompt = (
            "You are a task executor. You receive instructions and execute them "
            "precisely using available tools. Do not engage in conversation. "
            "Do not add personality or opinions. Report results factually. "
            "If a task cannot be completed, explain why concisely."
        )

        # Build TOML manifest string for OpenFang's SpawnRequest
        # Per-agent config uses [model] section (NOT [default_model] — that's global only)
        neko_api_key = (neko_agent_cfg.get("api_key") or "").strip()

        # 根据用户配置检测 provider：Anthropic/OpenAI 直连，OpenRouter 等走 proxy
        pinfo = getattr(self, '_provider_info', None) or _detect_provider_info(agent_base_url, model_name)
        provider = pinfo["provider"]
        effective_url = pinfo["effective_url"]
        api_key_env = pinfo["api_key_env"]

        manifest_toml = (
            f'name = "{agent_name}"\n'
            f'system_prompt = """\n{system_prompt}\n"""\n'
            f'\n'
            f'[model]\n'
            f'provider = "{provider}"\n'
            f'model = "{model_name}"\n'
            f'temperature = 0.3\n'
            f'max_tokens = 4096\n'
        )
        # 写 .env 供 Electron 下次重启注入 OpenFang 环境
        if neko_api_key and api_key_env:
            self._ensure_openfang_env_var(api_key_env, neko_api_key)
        # base_url: proxy URL (需要兼容性修补) 或直连 URL (原生支持的 provider)
        manifest_toml += f'base_url = "{effective_url}"\n'
        print(f"[OpenFang DEBUG] manifest_toml:\n{manifest_toml}")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # 先清理可能残留的旧 agent（上次启动遗留，配置过时）
                # 尝试多种 DELETE 路径和 kill 端点
                old_id = await self._find_agent_id_by_name(client, agent_name)
                print(f"[OpenFang DEBUG] push_agent_manifest: old_id={old_id}")
                delete_targets = [
                    f"{self.base_url}/api/agents/{agent_name}",
                    f"{self.base_url}/api/agents/name/{agent_name}",
                ]
                if old_id:
                    delete_targets.insert(0, f"{self.base_url}/api/agents/{old_id}")
                    delete_targets.append(f"{self.base_url}/api/agents/{old_id}/kill")
                deleted = False
                for dp in delete_targets:
                    try:
                        dr = await client.delete(dp, headers=self._auth_headers())
                        if dr.status_code in (200, 204):
                            logger.info("[OpenFang] Deleted stale agent '%s' via %s", agent_name, dp)
                            deleted = True
                            break
                        logger.debug("[OpenFang] DELETE %s → %d", dp, dr.status_code)
                    except Exception:
                        pass
                if deleted:
                    await asyncio.sleep(1)  # 等 OpenFang 清理资源

                # 尝试 manifest_toml 格式创建
                resp = await client.post(
                    f"{self.base_url}/api/agents",
                    json={"manifest_toml": manifest_toml},
                    headers=self._auth_headers(),
                )
                print(f"[OpenFang DEBUG] POST /api/agents (manifest_toml) HTTP {resp.status_code}: {resp.text[:300]}")

                if resp.status_code >= 400:
                    resp_text = resp.text[:200]
                    logger.debug("[OpenFang] manifest_toml rejected (%d: %s), trying JSON fallback",
                                 resp.status_code, resp_text)

                    # 如果是 "already exists"，获取已有 agent 的 ID 并 PATCH 更新配置
                    if "already exists" in resp_text.lower() or resp.status_code == 409:
                        existing_id = await self._find_agent_id_by_name(client, agent_name)
                        if existing_id:
                            # PATCH 更新 model/provider 确保用 openai
                            await self._patch_agent_model(client, existing_id, model_name, agent_base_url)
                            self._executor_agent_id = existing_id
                            logger.info("[OpenFang] Reusing & patched existing agent: id=%s, name=%s",
                                        existing_id, agent_name)
                            return existing_id

                    # Fallback: JSON 格式 — 使用检测到的 provider/effective_url
                    resp = await client.post(
                        f"{self.base_url}/api/agents",
                        json={
                            "name": agent_name,
                            "system_prompt": system_prompt,
                            "model": model_name,
                            "provider": provider,
                            "base_url": effective_url,
                            "temperature": 0.1,
                            "tools": agent_config.get("tools", []),
                        },
                        headers=self._auth_headers(),
                    )

                    # 再次检查 "already exists"
                    if resp.status_code >= 400:
                        resp_text2 = resp.text[:200]
                        if "already exists" in resp_text2.lower() or resp.status_code == 409:
                            existing_id = await self._find_agent_id_by_name(client, agent_name)
                            if existing_id:
                                await self._patch_agent_model(client, existing_id, model_name, agent_base_url)
                                self._executor_agent_id = existing_id
                                logger.info("[OpenFang] Reusing & patched existing agent (fallback): id=%s", existing_id)
                                return existing_id

                resp.raise_for_status()
                data = resp.json()
                self._executor_agent_id = data.get("id") or data.get("agent_id")
                print(f"[OpenFang DEBUG] Agent created successfully: data={str(data)[:500]}")
                print(f"[OpenFang DEBUG] _executor_agent_id set to: {self._executor_agent_id}")
                logger.info("[OpenFang] Agent registered: id=%s, model=openai/%s",
                            self._executor_agent_id, model_name)
                return self._executor_agent_id
        except Exception as e:
            logger.error("[OpenFang] Push agent manifest failed: %s", e)
            return None

    async def _find_agent_id_by_name(self, client: httpx.AsyncClient, name: str) -> Optional[str]:
        """查询 OpenFang 已注册 agent 列表，按 name 匹配返回 id。"""
        try:
            resp = await client.get(
                f"{self.base_url}/api/agents",
                headers=self._auth_headers(),
            )
            if resp.status_code == 200:
                agents = resp.json()
                if isinstance(agents, list):
                    for a in agents:
                        if isinstance(a, dict) and a.get("name") == name:
                            return a.get("id")
                elif isinstance(agents, dict):
                    # 可能是 {agents: [...]} 格式
                    for a in agents.get("agents", []):
                        if isinstance(a, dict) and a.get("name") == name:
                            return a.get("id")
        except Exception as e:
            logger.debug("[OpenFang] Failed to list agents: %s", e)
        return None

    async def _patch_agent_model(
        self, client: httpx.AsyncClient, agent_id: str, model: str, base_url: str
    ) -> None:
        """PATCH /api/agents/{id} 更新 agent 的 model/provider 配置。"""
        pinfo = getattr(self, '_provider_info', None) or _detect_provider_info(base_url, model)
        patch_payload = {
            "model": {
                "model": model,
                "provider": pinfo["provider"],
            }
        }
        effective_url = pinfo["effective_url"]
        if effective_url:
            patch_payload["model"]["base_url"] = effective_url
        try:
            resp = await client.patch(
                f"{self.base_url}/api/agents/{agent_id}",
                json=patch_payload,
                headers=self._auth_headers(),
            )
            if resp.status_code == 200:
                logger.info("[OpenFang] PATCH agent %s: model=openai/%s", agent_id, model)
            else:
                logger.debug("[OpenFang] PATCH agent %s returned %d: %s",
                             agent_id, resp.status_code, resp.text[:100])
        except Exception as e:
            logger.debug("[OpenFang] PATCH agent %s failed: %s", agent_id, e)

    async def fetch_tools_list(self) -> List[str]:
        """从 OpenFang 拉取可用工具列表并缓存。"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.base_url}/api/skills",
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                # 期望返回 list[dict] 或 list[str]
                if isinstance(data, list):
                    self._cached_tools_list = [
                        (item.get("name", str(item)) if isinstance(item, dict) else str(item))
                        for item in data
                    ]
                else:
                    self._cached_tools_list = []
                self._cached_tools_count = len(self._cached_tools_list)
                return self._cached_tools_list
        except Exception as e:
            logger.warning("[OpenFang] fetch_tools_list failed: %s", e)
            return []

    # ──────────────────────────────────────────
    #  内部方法
    # ──────────────────────────────────────────

    async def _poll_task(
        self,
        task_id: str,
        on_progress: Optional[Callable] = None,
        interval: float = 1.0,
        timeout: float = 300.0,
    ) -> OpenFangTaskStatus:
        """轮询任务状态直到完成/失败/超时。"""
        elapsed = 0.0

        async with httpx.AsyncClient(timeout=10.0) as client:
            while elapsed < timeout:
                resp = await client.get(
                    f"{self.base_url}/a2a/tasks/{task_id}",
                    headers=self._auth_headers(),
                )
                data = resp.json()
                status = data.get("status", "unknown")
                print(f"[OpenFang DEBUG] _poll_task iteration: status={status}, elapsed={elapsed:.1f}s")
                print(f"[OpenFang DEBUG] _poll_task raw data keys={list(data.keys()) if isinstance(data, dict) else 'N/A'}")
                print(f"[OpenFang DEBUG] _poll_task raw data (first 1500): {str(data)[:1500]}")

                if on_progress:
                    try:
                        on_progress({
                            "task_id": task_id,
                            "status": status,
                            "elapsed": elapsed,
                        })
                    except Exception:
                        pass  # 回调异常不影响轮询

                if status in ("completed", "failed", "cancelled"):
                    return OpenFangTaskStatus(
                        task_id=task_id,
                        status=status,
                        result=self._extract_result(data),
                        error=data.get("error"),
                        steps_taken=data.get("steps", 0),
                        agent_name=data.get("agent_name"),
                        artifacts=data.get("artifacts", []),
                    )

                await asyncio.sleep(interval)
                elapsed += interval

        # 超时 → 尝试取消
        try:
            await self.cancel_running(task_id)
        except Exception:
            pass
        return OpenFangTaskStatus(
            task_id=task_id,
            status="failed",
            error=f"Task timed out after {timeout}s",
        )

    @staticmethod
    def _extract_result(task_data: Dict) -> str:
        """从 A2A 任务响应中提取文本结果。"""
        print(f"[OpenFang DEBUG] _extract_result input keys={list(task_data.keys())}")
        result_field = task_data.get("result")
        print(f"[OpenFang DEBUG] _extract_result 'result' field type={type(result_field).__name__}, value(500)={str(result_field)[:500]}")

        # 尝试多种格式解析
        # Format 1: {"result": {"parts": [{"type":"text","text":"..."}]}}  (A2A standard)
        if isinstance(result_field, dict):
            parts = result_field.get("parts", [])
            if parts:
                texts = [p["text"] for p in parts if isinstance(p, dict) and p.get("type") == "text"]
                if texts:
                    print(f"[OpenFang DEBUG] _extract_result matched Format1 (parts), texts={len(texts)}")
                    return "\n".join(texts)
            # Format 1b: {"result": {"message": "...", "content": "..."}}
            for key in ("message", "content", "text", "output", "response"):
                v = result_field.get(key)
                if v and isinstance(v, str):
                    print(f"[OpenFang DEBUG] _extract_result matched Format1b (result.{key})")
                    return v

        # Format 2: {"result": "plain string"}
        if isinstance(result_field, str) and result_field.strip():
            print(f"[OpenFang DEBUG] _extract_result matched Format2 (plain string)")
            return result_field

        # Format 3: result in top-level fields
        for key in ("output", "response", "message", "content", "text", "answer"):
            v = task_data.get(key)
            if v and isinstance(v, str) and v.strip():
                print(f"[OpenFang DEBUG] _extract_result matched Format3 (top-level '{key}')")
                return v

        # Format 4: messages array — OpenFang uses role="agent" (not "assistant")
        # Content can be in "content" (string) or "parts" array with type="text"
        messages = task_data.get("messages", [])
        if isinstance(messages, list) and messages:
            agent_texts = []
            for m in messages:
                if isinstance(m, dict) and m.get("role") in ("assistant", "agent"):
                    # Try "parts" array first (OpenFang A2A format)
                    parts = m.get("parts", [])
                    if isinstance(parts, list) and parts:
                        for part in parts:
                            if isinstance(part, dict) and part.get("type") == "text":
                                t = part.get("text", "").strip()
                                if t:
                                    agent_texts.append(t)
                    # Fallback: "content" field
                    if not agent_texts:
                        c = m.get("content", "")
                        if isinstance(c, str) and c.strip():
                            agent_texts.append(c)
                        elif isinstance(c, list):
                            for part in c:
                                if isinstance(part, dict) and part.get("type") == "text":
                                    t = part.get("text", "").strip()
                                    if t:
                                        agent_texts.append(t)
            if agent_texts:
                print(f"[OpenFang DEBUG] _extract_result matched Format4 (messages array), count={len(agent_texts)}")
                return "\n".join(agent_texts)

        # Format 5: history array (OpenFang specific?)
        history = task_data.get("history", [])
        if isinstance(history, list) and history:
            texts = []
            for entry in history:
                if isinstance(entry, dict):
                    for key in ("content", "text", "message"):
                        v = entry.get(key)
                        if v and isinstance(v, str):
                            texts.append(v)
                            break
            if texts:
                print(f"[OpenFang DEBUG] _extract_result matched Format5 (history), count={len(texts)}")
                return "\n".join(texts)

        print(f"[OpenFang DEBUG] _extract_result: NO FORMAT MATCHED, all_keys={list(task_data.keys())}")
        return ""

    def _auth_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers
