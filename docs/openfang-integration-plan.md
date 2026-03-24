# OpenFang 集成方案 — N.E.K.O. × OpenFang

## 总体思路

将 OpenFang 作为 N.E.K.O. 的**无头 Agent 执行后端**：抹除其人格，NEKO 通过 A2A 协议下发指令，OpenFang 利用其 53 个内置工具 + 60 个 Skill 执行任务，定期同步状态回 NEKO。API Key 由 NEKO 侧统一管理并下发。

```
┌──────────────────────────────────────────────────────────────┐
│                    N.E.K.O. (主控端)                          │
│                                                              │
│  main_server ←──ZMQ──→ agent_server                          │
│       ↑                     │                                │
│       │ WebSocket           │ HTTP/A2A                       │
│       │ (用户对话)           ↓                                │
│       │              ┌─────────────────┐                     │
│       │              │ OpenFangAdapter  │ ← 新增适配层        │
│       │              └────────┬────────┘                     │
│       │                       │                              │
└───────┼───────────────────────┼──────────────────────────────┘
        │                       │ REST API (localhost:50051)
        │                       ↓
┌───────┼───────────────────────────────────────────────────────┐
│       │            OpenFang (执行端)                           │
│       │                                                       │
│  ┌────┴────┐   ┌──────────┐   ┌──────────┐   ┌───────────┐  │
│  │ A2A API │   │ 53 Tools │   │ 60 Skills│   │ WASM沙箱  │  │
│  └─────────┘   └──────────┘   └──────────┘   └───────────┘  │
│                                                               │
│  人格: 已抹除 (纯执行者)                                       │
│  LLM Key: 由 NEKO 下发                                        │
│  任务: 仅接受 A2A 指令                                         │
└───────────────────────────────────────────────────────────────┘
```

---

## Phase 0: 环境准备与 OpenFang 部署

### 0.1 OpenFang 安装集成

**目标**: 将 OpenFang 二进制打包进 NEKO 的发行版中，用户无需手动安装。

**方案**:
- OpenFang 是 ~32MB 单一二进制，可直接嵌入 NEKO 安装包
- 在 `launcher.py` 中增加 OpenFang 进程管理（启动/停止/健康检查）
- OpenFang 随 NEKO 启动自动拉起，随 NEKO 关闭自动终止

**涉及文件**:
- `launcher.py` — 增加 OpenFang 子进程管理
- 新增 `openfang/` 目录存放二进制和默认配置

```python
# launcher.py 新增
class OpenFangProcessManager:
    """管理 OpenFang daemon 生命周期"""

    def __init__(self):
        self.process = None
        self.binary_path = self._find_binary()
        self.config_path = os.path.join(DATA_DIR, "openfang", "config.toml")

    def start(self):
        """随 NEKO 启动 OpenFang daemon"""
        self._ensure_config()
        self.process = subprocess.Popen(
            [self.binary_path, "start", "--config", self.config_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

    def stop(self):
        """优雅关闭"""
        if self.process:
            # 先尝试 HTTP API 关闭
            requests.post("http://127.0.0.1:50051/api/shutdown")
            self.process.wait(timeout=10)

    def health_check(self) -> bool:
        try:
            r = requests.get("http://127.0.0.1:50051/api/health", timeout=2)
            return r.status_code == 200
        except:
            return False
```

### 0.2 OpenFang 默认配置生成

**目标**: NEKO 侧自动生成 OpenFang 的 config.toml，抹除人格、锁定为纯执行模式。

```toml
# data/openfang/config.toml (由 NEKO 自动生成)

# 基础设置
home_dir = "~/.neko/openfang"
data_dir = "~/.neko/openfang/data"
log_level = "info"
api_listen = "127.0.0.1:50051"   # 只绑定 localhost，安全
api_key = ""                      # 由 NEKO 启动时动态设置

# 默认模型 — 由 NEKO 下发配置覆盖
[default_model]
provider = "openai"               # 占位，启动后由 NEKO push
model = "gpt-4o"

# 关闭所有 Channel — OpenFang 只通过 API 接受指令
# 不配置任何 [channels.*] 段

# 关闭所有 Hands 自主调度 — 由 NEKO 控制
[hands]
# 全部不启用

# 内存配置
[memory]
embedding_model = "all-MiniLM-L6-v2"
consolidation_threshold = 10000
decay_rate = 0.5
```

---

## Phase 1: OpenFangAdapter — 适配层

### 1.1 新增 `brain/openfang_adapter.py`

**核心职责**: 封装所有与 OpenFang 的 HTTP 通信，提供与现有 ComputerUseAdapter / BrowserUseAdapter 一致的接口。

```python
"""
brain/openfang_adapter.py
OpenFang Agent 执行后端适配器

职责:
1. 管理与 OpenFang daemon 的连接
2. 通过 A2A API 下发任务
3. 轮询/SSE 监听任务状态
4. API Key 下发与配置同步
"""

import httpx
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Callable
from utils.config_manager import get_config_manager

logger = logging.getLogger("openfang_adapter")

@dataclass
class OpenFangTaskStatus:
    task_id: str
    status: str          # "pending" | "running" | "completed" | "failed" | "cancelled"
    result: Optional[str] = None
    error: Optional[str] = None
    steps_taken: int = 0
    agent_name: Optional[str] = None
    artifacts: List[Dict] = field(default_factory=list)


class OpenFangAdapter:
    """
    OpenFang A2A 适配器

    遵循 NEKO agent adapter 接口约定:
    - is_available() -> Dict[str, Any]
    - run_instruction(instruction, session_id) -> Dict[str, Any]
    - cancel_running() -> None
    - check_connectivity() -> bool
    """

    DEFAULT_BASE_URL = "http://127.0.0.1:50051"

    def __init__(self, base_url: str = None):
        self.base_url = base_url or self.DEFAULT_BASE_URL
        self.init_ok = False
        self.last_error: Optional[str] = None
        self._active_tasks: Dict[str, str] = {}   # neko_task_id -> openfang_task_id
        self._api_key: Optional[str] = None        # OpenFang Bearer token
        self._config_synced = False

    # ─────────────────────────────────────────────
    #  接口方法 (与 ComputerUseAdapter 对齐)
    # ─────────────────────────────────────────────

    def is_available(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "ready": self.init_ok,
            "reasons": [self.last_error] if self.last_error else [],
            "provider": "openfang",
            "version": self._cached_version or "unknown",
            "tools_count": self._cached_tools_count or 0
        }

    async def run_instruction(
        self,
        instruction: str,
        session_id: Optional[str] = None,
        on_progress: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        通过 A2A 向 OpenFang 提交任务并等待结果

        流程:
        1. POST /a2a/tasks/send 创建任务
        2. SSE 流式监听 或 轮询 GET /a2a/tasks/{id}
        3. 返回最终结果
        """
        try:
            # Step 1: 提交任务
            task_payload = {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": instruction}]
                },
                "metadata": {
                    "source": "neko",
                    "session_id": session_id
                }
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self.base_url}/a2a/tasks/send",
                    json=task_payload,
                    headers=self._auth_headers()
                )
                resp.raise_for_status()
                task_data = resp.json()
                of_task_id = task_data["id"]

            # Step 2: 轮询等待完成
            result = await self._poll_task(of_task_id, on_progress)

            return {
                "success": result.status == "completed",
                "result": result.result or "",
                "steps": result.steps_taken,
                "agent_name": result.agent_name,
                "artifacts": result.artifacts,
                "error": result.error
            }

        except httpx.HTTPStatusError as e:
            self.last_error = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            return {"success": False, "error": self.last_error}
        except Exception as e:
            self.last_error = str(e)
            return {"success": False, "error": self.last_error}

    async def cancel_running(self, neko_task_id: str = None) -> None:
        """取消正在运行的任务"""
        tasks_to_cancel = (
            [self._active_tasks[neko_task_id]] if neko_task_id
            else list(self._active_tasks.values())
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            for of_id in tasks_to_cancel:
                try:
                    await client.post(
                        f"{self.base_url}/a2a/tasks/{of_id}/cancel",
                        headers=self._auth_headers()
                    )
                except Exception as e:
                    logger.warning(f"Cancel task {of_id} failed: {e}")

    def check_connectivity(self) -> bool:
        """同步健康检查 (在线程池中调用)"""
        try:
            with httpx.Client(timeout=3.0) as client:
                r = client.get(
                    f"{self.base_url}/api/health",
                    headers=self._auth_headers()
                )
                self.init_ok = r.status_code == 200
                if self.init_ok:
                    self.last_error = None
                    # 缓存版本和工具数
                    status = r.json()
                    self._cached_version = status.get("version")
                    self._cached_tools_count = status.get("tools_count")
                return self.init_ok
        except Exception as e:
            self.last_error = str(e)
            self.init_ok = False
            return False

    # ─────────────────────────────────────────────
    #  配置同步 (NEKO → OpenFang)
    # ─────────────────────────────────────────────

    async def sync_config(self) -> bool:
        """
        将 NEKO 侧的 API Key 和模型配置同步到 OpenFang

        通过 OpenFang 的 POST /api/providers/{name}/key 端点
        运行时推送，不需要重启 OpenFang daemon
        """
        cm = get_config_manager()

        provider_mappings = {
            # neko_provider_key -> openfang_provider_name
            "openai": "openai",
            "qwen": "openai",      # 阿里走 OpenAI compatible
            "stepfun": "openai",   # 阶跃走 OpenAI compatible
            "gemini": "gemini",
            "glm": "openai",      # 智谱走 OpenAI compatible
            "anthropic": "anthropic"
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                synced_count = 0

                for neko_key, of_provider in provider_mappings.items():
                    api_cfg = cm.get_model_api_config_by_provider(neko_key)
                    if not api_cfg or not api_cfg.get("api_key"):
                        continue

                    # 推送 API Key
                    resp = await client.post(
                        f"{self.base_url}/api/providers/{of_provider}/key",
                        json={
                            "api_key": api_cfg["api_key"],
                            "base_url": api_cfg.get("base_url", ""),
                            "model": api_cfg.get("model", "")
                        },
                        headers=self._auth_headers()
                    )
                    if resp.status_code == 200:
                        synced_count += 1

                self._config_synced = synced_count > 0
                logger.info(f"[OpenFang] Config synced: {synced_count} providers")
                return self._config_synced

        except Exception as e:
            logger.error(f"[OpenFang] Config sync failed: {e}")
            return False

    async def push_agent_manifest(self, agent_config: Dict) -> Optional[str]:
        """
        向 OpenFang 注册/更新一个无人格执行 Agent

        抹除人格: system_prompt 只保留执行指令，不含角色扮演
        """
        manifest = {
            "name": agent_config.get("name", "neko-executor"),
            "system_prompt": (
                "You are a task executor. You receive instructions and execute them "
                "precisely using available tools. Do not engage in conversation. "
                "Do not add personality or opinions. Report results factually. "
                "If a task cannot be completed, explain why concisely."
            ),
            "model": agent_config.get("model"),
            "temperature": 0.1,      # 低温度 = 精确执行
            "tools": agent_config.get("tools", []),  # 可选: 限制可用工具集
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/agents",
                    json=manifest,
                    headers=self._auth_headers()
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("id")
        except Exception as e:
            logger.error(f"[OpenFang] Push agent manifest failed: {e}")
            return None

    # ─────────────────────────────────────────────
    #  内部方法
    # ─────────────────────────────────────────────

    async def _poll_task(
        self,
        task_id: str,
        on_progress: Optional[Callable] = None,
        interval: float = 1.0,
        timeout: float = 300.0
    ) -> OpenFangTaskStatus:
        """轮询任务状态直到完成/失败/超时"""
        elapsed = 0.0

        async with httpx.AsyncClient(timeout=10.0) as client:
            while elapsed < timeout:
                resp = await client.get(
                    f"{self.base_url}/a2a/tasks/{task_id}",
                    headers=self._auth_headers()
                )
                data = resp.json()
                status = data.get("status", "unknown")

                if on_progress:
                    on_progress({
                        "task_id": task_id,
                        "status": status,
                        "elapsed": elapsed
                    })

                if status in ("completed", "failed", "cancelled"):
                    return OpenFangTaskStatus(
                        task_id=task_id,
                        status=status,
                        result=self._extract_result(data),
                        error=data.get("error"),
                        steps_taken=data.get("steps", 0),
                        agent_name=data.get("agent_name"),
                        artifacts=data.get("artifacts", [])
                    )

                await asyncio.sleep(interval)
                elapsed += interval

        # 超时 → 取消
        await self.cancel_running()
        return OpenFangTaskStatus(
            task_id=task_id,
            status="failed",
            error=f"Task timed out after {timeout}s"
        )

    def _extract_result(self, task_data: Dict) -> str:
        """从 A2A 任务响应中提取文本结果"""
        parts = task_data.get("result", {}).get("parts", [])
        texts = [p["text"] for p in parts if p.get("type") == "text"]
        return "\n".join(texts) if texts else ""

    def _auth_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers
```

### 1.2 OpenFang Decision 数据结构

```python
# brain/task_executor.py 新增

@dataclass
class OpenFangDecision:
    """OpenFang 多 Agent 执行决策"""
    has_task: bool
    can_execute: bool
    task_description: str = ""
    suggested_tools: List[str] = field(default_factory=list)  # 建议使用的工具
    reason: str = ""
```

---

## Phase 2: 接入 DirectTaskExecutor 决策链

### 2.1 新增 `_assess_openfang()` 评估方法

在 `brain/task_executor.py` 的 `DirectTaskExecutor` 类中添加:

```python
async def _assess_openfang(
    self,
    conversation: str,
    openfang_available: bool,
    available_tools: List[str]  # OpenFang 侧可用工具列表
) -> OpenFangDecision:
    """
    评估是否应该路由到 OpenFang 执行

    OpenFang 擅长:
    - 需要多步推理 + 工具调用的复合任务
    - 文件处理 (CSV/JSON/数据库)
    - Web 搜索与信息聚合
    - 代码执行 (sandboxed)
    - 邮件/消息发送 (如果配置了 channel)

    不适合路由到 OpenFang:
    - 需要宿主 GUI 的任务 (截屏、点击) → ComputerUse
    - 需要可视化浏览器的任务 → BrowserUse
    - 简单对话/闲聊 → 主对话引擎
    """
    if not openfang_available:
        return OpenFangDecision(has_task=False, can_execute=False, reason="OpenFang 不可用")

    system_prompt = f"""你是一个任务路由器。判断以下对话中用户的最新请求是否适合由 OpenFang 多 Agent 系统执行。

OpenFang 可用工具: {', '.join(available_tools[:30])}

适合 OpenFang 的任务:
- 数据处理、文件操作、格式转换
- Web 搜索、信息收集、研究类任务
- 代码生成与执行
- 发送消息/邮件 (需要 API 调用的)
- 多步骤复合任务

不适合 OpenFang 的任务:
- 需要看到屏幕/鼠标点击的 GUI 操作
- 纯对话、情感交流、角色扮演
- 需要实时浏览器可视化的任务

输出 JSON: {{"has_task": bool, "can_execute": bool, "task_description": "简述任务", "suggested_tools": ["tool1", "tool2"], "reason": "判断理由"}}"""

    # ... LLM 调用逻辑 (复用现有的 _get_client/_get_model 模式) ...
```

### 2.2 修改 `analyze_and_execute()` 并行评估

```python
# brain/task_executor.py — analyze_and_execute 方法修改

# 现有的并行评估列表:
assessment_tasks = []

if computer_use_enabled and computer_use_available:
    assessment_tasks.append(('cu', self._assess_computer_use(conversation, True)))

if browser_use_enabled and browser_use_available:
    assessment_tasks.append(('bu', self._assess_browser_use(conversation, True)))

if user_plugin_enabled and user_plugin_available:
    assessment_tasks.append(('up', self._assess_user_plugin(conversation, True, plugin_list)))

# ===== 新增 OpenFang 评估 =====
if openfang_enabled and openfang_available:
    assessment_tasks.append(('of', self._assess_openfang(
        conversation, True, openfang_tools_list
    )))

# 执行所有评估 (并行)
results = await asyncio.gather(*[t[1] for t in assessment_tasks], return_exceptions=True)

# 决策优先级 (调整后):
# UserPlugin > OpenFang > BrowserUse > ComputerUse
#
# 理由:
# - UserPlugin 是用户自定义的，优先级最高
# - OpenFang 有沙箱保护 + 丰富工具集，比直接操作宿主更安全
# - BrowserUse 次之
# - ComputerUse 直接操作宿主 GUI，风险最高，优先级最低
```

---

## Phase 3: agent_server.py 注册与路由

### 3.1 Modules 类扩展

```python
# agent_server.py

class Modules:
    computer_use: Optional[ComputerUseAdapter] = None
    browser_use: Optional[BrowserUseAdapter] = None
    openfang: Optional[OpenFangAdapter] = None          # ← 新增
    task_executor: Optional[DirectTaskExecutor] = None
    agent_bridge: Optional[AgentServerEventBridge] = None

    task_registry: Dict[str, Dict[str, Any]] = {}
    agent_flags: Dict[str, Any] = {
        "computer_use_enabled": False,
        "browser_use_enabled": False,
        "user_plugin_enabled": False,
        "openfang_enabled": False,                      # ← 新增
    }
    capability_cache: Dict[str, Dict[str, Any]] = {
        "computer_use": {"ready": False, "reason": "未初始化"},
        "browser_use": {"ready": False, "reason": "未初始化"},
        "user_plugin": {"ready": False, "reason": "未初始化"},
        "openfang": {"ready": False, "reason": "未初始化"},  # ← 新增
    }
```

### 3.2 启动流程

```python
# agent_server.py — startup_event()

async def startup_event():
    # ... 现有初始化 ...

    # ===== OpenFang 初始化 =====
    try:
        from brain.openfang_adapter import OpenFangAdapter

        of_url = os.getenv("OPENFANG_BASE_URL", "http://127.0.0.1:50051")
        Modules.openfang = OpenFangAdapter(base_url=of_url)

        # 异步检查连接
        asyncio.create_task(_init_openfang())
    except ImportError:
        logger.info("[OpenFang] adapter not available, skipping")
    except Exception as e:
        logger.warning(f"[OpenFang] init failed: {e}")

    # 注入到 task_executor
    if Modules.task_executor and Modules.openfang:
        Modules.task_executor.openfang = Modules.openfang


async def _init_openfang():
    """后台初始化 OpenFang 连接 + 配置同步"""
    adapter = Modules.openfang
    if not adapter:
        return

    # 等待 OpenFang daemon 就绪 (最多 30s)
    for i in range(30):
        if adapter.check_connectivity():
            break
        await asyncio.sleep(1)

    if not adapter.init_ok:
        logger.warning("[OpenFang] daemon not reachable after 30s")
        Modules.capability_cache["openfang"] = {
            "ready": False,
            "reason": "OpenFang daemon 未就绪"
        }
        return

    # 同步 API Key 配置
    await adapter.sync_config()

    # 注册无人格执行 Agent
    agent_id = await adapter.push_agent_manifest({
        "name": "neko-executor",
        "model": None,  # 使用 OpenFang 默认模型
    })

    if agent_id:
        Modules.capability_cache["openfang"] = {
            "ready": True,
            "reason": "",
            "agent_id": agent_id
        }
        Modules.agent_flags["openfang_enabled"] = True
        logger.info(f"[OpenFang] ✅ Ready, executor agent: {agent_id}")
```

### 3.3 任务执行路由

```python
# agent_server.py — 任务分发逻辑

async def _dispatch_task(task_result: TaskResult, ...):
    """根据 execution_method 分发到对应执行器"""

    if task_result.execution_method == "openfang":
        await _run_openfang_task(
            task_id=task_result.task_id,
            instruction=task_result.task_description,
            conversation_id=conversation_id,
            lanlan_name=lanlan_name
        )
    elif task_result.execution_method == "computer_use":
        # ... 现有逻辑 ...
    elif task_result.execution_method == "browser_use":
        # ... 现有逻辑 ...


async def _run_openfang_task(
    task_id: str,
    instruction: str,
    conversation_id: str = None,
    lanlan_name: str = None
):
    """执行 OpenFang 任务并推送结果"""

    # 注册到 task_registry
    Modules.task_registry[task_id] = {
        "id": task_id,
        "type": "openfang",
        "status": "running",
        "instruction": instruction,
        "start_time": datetime.now().isoformat()
    }

    # 进度回调 → 通过 ZMQ 推送到 main_server
    def on_progress(info):
        if Modules.agent_bridge:
            Modules.agent_bridge.push_event({
                "event_type": "task_update",
                "task_id": task_id,
                "lanlan_name": lanlan_name,
                "status": info["status"],
                "channel": "openfang",
                "elapsed": info.get("elapsed", 0)
            })

    try:
        result = await Modules.openfang.run_instruction(
            instruction=instruction,
            session_id=conversation_id,
            on_progress=on_progress
        )

        # 更新 registry
        Modules.task_registry[task_id]["status"] = "completed" if result["success"] else "failed"
        Modules.task_registry[task_id]["result"] = result
        Modules.task_registry[task_id]["end_time"] = datetime.now().isoformat()

        # 推送结果到 main_server
        if Modules.agent_bridge:
            Modules.agent_bridge.push_event({
                "event_type": "task_result",
                "task_id": task_id,
                "lanlan_name": lanlan_name,
                "status": "completed" if result["success"] else "failed",
                "channel": "openfang",
                "summary": result.get("result", "")[:500],
                "detail": result.get("result", ""),
                "error_message": result.get("error")
            })
    except Exception as e:
        logger.error(f"[OpenFang] Task {task_id} failed: {e}")
        Modules.task_registry[task_id]["status"] = "failed"
        Modules.task_registry[task_id]["error"] = str(e)
```

### 3.4 新增 API 端点

```python
# agent_server.py — 新增路由

@router.get("/openfang/availability")
async def openfang_availability():
    """检查 OpenFang 可用性"""
    if not Modules.openfang:
        return {"enabled": False, "ready": False, "reason": "adapter 未加载"}
    return Modules.openfang.is_available()


@router.post("/openfang/run")
async def openfang_run(request: Request):
    """直接通过 OpenFang 执行任务 (绕过路由决策)"""
    body = await request.json()
    instruction = body.get("instruction")
    if not instruction:
        return JSONResponse({"error": "instruction required"}, status_code=400)

    task_id = f"of_{uuid.uuid4().hex[:12]}"
    asyncio.create_task(_run_openfang_task(
        task_id=task_id,
        instruction=instruction,
        conversation_id=body.get("conversation_id"),
        lanlan_name=body.get("lanlan_name")
    ))

    return {"success": True, "task_id": task_id, "status": "running"}


@router.post("/openfang/sync_config")
async def openfang_sync_config():
    """手动触发配置同步"""
    if not Modules.openfang:
        return {"success": False, "error": "adapter 未加载"}
    ok = await Modules.openfang.sync_config()
    return {"success": ok}
```

---

## Phase 4: 配置同步机制

### 4.1 API Key 下发流程

```
用户在 NEKO 前端设置 API Key
    ↓
main_server 保存到 api_providers.json
    ↓
main_server 通知 agent_server: POST /notify_config_changed
    ↓
agent_server 调用 Modules.openfang.sync_config()
    ↓
OpenFangAdapter 逐个调用 POST /api/providers/{name}/key
    ↓
OpenFang 运行时更新，无需重启
```

### 4.2 `config_manager.py` 扩展

```python
# utils/config_manager.py — 新增

def get_openfang_config(self) -> Dict[str, Any]:
    """获取 OpenFang 相关配置"""
    return {
        "enabled": self._core_config.get("openfang_enabled", False),
        "base_url": self._core_config.get("openfang_base_url", "http://127.0.0.1:50051"),
        "auto_start": self._core_config.get("openfang_auto_start", True),
        "binary_path": self._core_config.get("openfang_binary_path", ""),
    }
```

### 4.3 `core_config.json` 新增字段

```json
{
  "openfang_enabled": true,
  "openfang_base_url": "http://127.0.0.1:50051",
  "openfang_auto_start": true,
  "openfang_binary_path": ""
}
```

---

## Phase 5: 前端 UI 集成

### 5.1 设置页面

在 NEKO 的设置 Web UI 中新增 OpenFang 配置区:

```
┌─────────────────────────────────────────┐
│ 🐺 OpenFang Agent 执行后端               │
│                                         │
│ 状态: ● 已连接 (v0.3.30)                │
│ 可用工具: 53 个                          │
│                                         │
│ [✓] 启用 OpenFang 任务执行               │
│ [✓] 随 NEKO 自动启动                     │
│                                         │
│ 端口: [50051]                            │
│                                         │
│ [同步配置] [查看日志] [重启]              │
└─────────────────────────────────────────┘
```

### 5.2 任务状态展示

在 NEKO 聊天界面中，当 OpenFang 执行任务时显示:

```
┌─────────────────────────────────────────┐
│ 🐺 OpenFang 正在执行...                  │
│                                         │
│ 任务: 搜索最近的 AI 新闻并整理摘要        │
│ 状态: ⏳ 运行中 (12s)                    │
│ 步骤: 3/? (web_search → parse → ...)    │
│                                         │
│ [取消]                                   │
└─────────────────────────────────────────┘
```

---

## Phase 6: 安全与边界

### 6.1 权限隔离

```
NEKO (宿主环境)           OpenFang (受控环境)
─────────────────         ──────────────────
✅ GUI 操作               ❌ 无 GUI 访问
✅ 用户文件系统            ⚠️ 仅限 sandbox 目录
✅ 浏览器控制              ❌ 无浏览器 (除非启用 Browser Hand)
✅ 语音/Live2D             ❌ 无多媒体
✅ 用户对话/人格            ❌ 无人格 (纯执行者)
```

### 6.2 OpenFang Agent Manifest 安全约束

```toml
# NEKO 推送给 OpenFang 的 agent manifest

[agent]
name = "neko-executor"
system_prompt = "You are a task executor. Execute instructions precisely. No conversation."
temperature = 0.1

[capabilities]
# 限制文件系统访问范围
file_read = ["/tmp/neko-workspace/*"]
file_write = ["/tmp/neko-workspace/*"]
net_connect = ["*"]             # 网络访问: 按需收紧
shell_exec = true               # 代码执行: 在 WASM 沙箱中
```

---

## 实施优先级与里程碑

### Milestone 1 — 基础连通 (1-2 周)
- [ ] OpenFang 二进制打包 + launcher 集成
- [ ] `brain/openfang_adapter.py` 核心实现
- [ ] 配置同步 (API Key 下发)
- [ ] `/openfang/run` 直接执行端点
- [ ] 手动测试: NEKO → OpenFang → 执行 → 返回结果

### Milestone 2 — 智能路由 (1 周)
- [ ] `_assess_openfang()` 评估逻辑
- [ ] `analyze_and_execute()` 集成 OpenFang 决策分支
- [ ] 决策优先级调优 (UserPlugin > OpenFang > Browser > CUA)
- [ ] ZMQ 事件推送 (进度/结果)

### Milestone 3 — 前端集成 (1 周)
- [ ] 设置页面 OpenFang 配置区
- [ ] 聊天界面任务状态展示
- [ ] 错误处理与用户提示

### Milestone 4 — 安全加固 + 打磨 (1 周)
- [ ] Agent manifest 权限约束
- [ ] 任务超时与取消机制
- [ ] 日志与调试工具
- [ ] 文档更新

---

## 风险与缓解

| 风险 | 严重度 | 缓解方案 |
|------|--------|----------|
| OpenFang pre-1.0，API 可能 breaking change | 高 | 锁定特定版本，adapter 层做兼容 |
| OpenFang daemon 崩溃 | 中 | launcher 做进程守护，自动重启 |
| 两套 LLM 调用导致 token 消耗翻倍 | 中 | 路由评估用轻量模型 (qwen-flash)，仅确认路由的任务才调用 OpenFang |
| 配置同步失败 (API Key) | 低 | 重试机制 + 用户手动同步按钮 |
| OpenFang 工具执行出错无法调试 | 中 | 保留 OpenFang 日志，提供 [查看日志] 入口 |
