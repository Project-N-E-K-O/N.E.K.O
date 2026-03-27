# OpenFang 集成方案 — N.E.K.O. × OpenFang (v2)

## 总体思路

将 OpenFang 作为 N.E.K.O. 的**无头 Agent 执行后端**：抹除其人格，NEKO 通过 A2A 协议下发指令，OpenFang 利用其 53 个内置工具 + 60 个 Skill 执行任务，定期同步状态回 NEKO。API Key 由 NEKO 侧统一管理并下发。

### 架构总览

```text
┌─────────────────────────────────────────────────────────────────┐
│                   Electron (N.E.K.O.-PC)                        │
│                                                                 │
│   ┌─────────────────────┐       ┌─────────────────────┐        │
│   │  child_process #1   │       │  child_process #2   │        │
│   │  Python launcher.py │       │  OpenFang binary    │        │
│   └─────────┬───────────┘       └─────────┬───────────┘        │
│             │                             │                     │
│     ┌───────┼───────────┐                 │                     │
│     │       │           │                 │                     │
│   :48912  :48911      :48915           :50051                   │
│   memory  main_srv    agent_srv       openfang                  │
│     │       │           │                 │                     │
│     └───────┼───────────┘                 │                     │
│             │          HTTP (localhost)    │                     │
│             │  ┌──────────────────────┐   │                     │
│             └──│  OpenFangAdapter     │───┘                     │
│                │  (brain/ 纯通信层)    │                         │
│                └──────────────────────┘                         │
└─────────────────────────────────────────────────────────────────┘

关键原则:
  • Electron 并行 spawn 两个进程，互不依赖进程树
  • Python 侧只负责「跟 OpenFang 通信」，不负责「管 OpenFang 的命」
  • OpenFang 崩了 → NEKO 对话不受影响，只是 agent 执行暂时不可用
  • Python 崩了 → OpenFang 仍在，Electron 可独立重启 Python
```

### 对比旧方案

```text
❌ 旧: Electron → Python → OpenFang (三层套娃，生命周期耦合)
✅ 新: Electron ──┬── Python (现有)     两个平级子进程
                  └── OpenFang (新增)    独立生命周期
```

---

## Phase 0: Electron 侧进程管理

### 0.1 OpenFang 二进制打包

**目标**: 将 OpenFang ~32MB 二进制嵌入 N.E.K.O.-PC 的发行包，用户无需手动安装。

**打包位置**:
```text
N.E.K.O.-PC/
├── resources/
│   ├── openfang/
│   │   ├── openfang.exe          # Windows
│   │   ├── openfang-darwin        # macOS
│   │   ├── openfang-linux         # Linux
│   │   └── config.default.toml    # 默认配置模板
│   └── python/                    # 现有 Python 后端
└── ...
```

### 0.2 Electron Main Process — OpenFangManager

**位置**: N.E.K.O.-PC 的 Electron main process（与现有 Python launcher 管理器平级）

```typescript
// electron/main/openfang-manager.ts

import { spawn, ChildProcess } from 'child_process';
import { app } from 'electron';
import path from 'path';
import http from 'http';

interface OpenFangStatus {
  running: boolean;
  version?: string;
  port: number;
  pid?: number;
}

export class OpenFangManager {
  private process: ChildProcess | null = null;
  private port: number = 50051;
  private configPath: string;
  private binaryPath: string;
  private healthCheckTimer: NodeJS.Timeout | null = null;
  private restartCount: number = 0;
  private maxRestarts: number = 5;

  constructor() {
    const resourcesPath = process.resourcesPath || path.join(app.getAppPath(), 'resources');
    const platform = process.platform;

    this.binaryPath = path.join(resourcesPath, 'openfang',
      platform === 'win32' ? 'openfang.exe' :
      platform === 'darwin' ? 'openfang-darwin' : 'openfang-linux'
    );

    // 配置存放在用户数据目录，跟 Python 侧的 port_config.json 同级
    const userDataPath = app.getPath('userData'); // %APPDATA%/N.E.K.O 或 ~/Library/Application Support/N.E.K.O
    this.configPath = path.join(userDataPath, 'openfang', 'config.toml');
  }

  /**
   * 启动 OpenFang daemon
   * 由 Electron app.whenReady() 调用，与 Python launcher 并行
   */
  async start(): Promise<void> {
    this.ensureConfig();

    this.process = spawn(this.binaryPath, [
      'start',
      '--config', this.configPath
    ], {
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: false,  // 不 detach，Electron 退出时自动回收
    });

    this.process.stdout?.on('data', (data: Buffer) => {
      console.log(`[OpenFang] ${data.toString().trim()}`);
    });

    this.process.stderr?.on('data', (data: Buffer) => {
      console.error(`[OpenFang] ${data.toString().trim()}`);
    });

    this.process.on('exit', (code: number | null) => {
      console.log(`[OpenFang] exited with code ${code}`);
      this.process = null;

      // 非正常退出 → 自动重启 (带上限)
      if (code !== 0 && code !== null && this.restartCount < this.maxRestarts) {
        this.restartCount++;
        console.log(`[OpenFang] restarting (attempt ${this.restartCount}/${this.maxRestarts})...`);
        setTimeout(() => this.start(), 2000);
      }
    });

    // 等待 health check 通过 (OpenFang 冷启动 ~180ms)
    await this.waitForHealth(10_000);
    this.startHealthMonitor();
  }

  /**
   * 优雅关闭
   */
  async stop(): Promise<void> {
    if (this.healthCheckTimer) {
      clearInterval(this.healthCheckTimer);
      this.healthCheckTimer = null;
    }

    if (!this.process) return;

    try {
      // 先通过 API 优雅关闭
      await this.httpPost('/api/shutdown');
      // 等待进程退出
      await new Promise<void>((resolve) => {
        const timer = setTimeout(() => {
          this.process?.kill('SIGKILL');
          resolve();
        }, 5000);
        this.process?.on('exit', () => {
          clearTimeout(timer);
          resolve();
        });
      });
    } catch {
      // API 不通，直接 kill
      this.process?.kill('SIGTERM');
    }

    this.process = null;
  }

  /**
   * 健康检查
   */
  async healthCheck(): Promise<OpenFangStatus> {
    try {
      const data = await this.httpGet('/api/health');
      return {
        running: true,
        version: data.version,
        port: this.port,
        pid: this.process?.pid
      };
    } catch {
      return { running: false, port: this.port };
    }
  }

  /**
   * 获取 OpenFang 端口 (供 Python 侧使用)
   */
  getPort(): number {
    return this.port;
  }

  // ─────────────── 内部方法 ───────────────

  private ensureConfig(): void {
    const fs = require('fs');
    const dir = path.dirname(this.configPath);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    if (!fs.existsSync(this.configPath)) {
      // 从默认模板生成，抹除人格、关闭 channels
      const template = fs.readFileSync(
        path.join(path.dirname(this.binaryPath), 'config.default.toml'), 'utf-8'
      );
      fs.writeFileSync(this.configPath, template);
    }
  }

  private async waitForHealth(timeoutMs: number): Promise<void> {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const status = await this.healthCheck();
      if (status.running) {
        console.log(`[OpenFang] ready in ${Date.now() - start}ms (v${status.version})`);
        return;
      }
      await new Promise(r => setTimeout(r, 200));
    }
    console.warn('[OpenFang] health check timed out');
  }

  private startHealthMonitor(): void {
    // 每 30s 检查一次
    this.healthCheckTimer = setInterval(async () => {
      const status = await this.healthCheck();
      if (!status.running && this.restartCount < this.maxRestarts) {
        console.warn('[OpenFang] daemon down, restarting...');
        this.restartCount++;
        await this.start();
      }
    }, 30_000);
  }

  private httpGet(path: string): Promise<any> {
    return new Promise((resolve, reject) => {
      http.get(`http://127.0.0.1:${this.port}${path}`, { timeout: 3000 }, (res) => {
        let body = '';
        res.on('data', (chunk) => body += chunk);
        res.on('end', () => {
          try { resolve(JSON.parse(body)); }
          catch { reject(new Error('Invalid JSON')); }
        });
      }).on('error', reject);
    });
  }

  private httpPost(path: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const req = http.request(`http://127.0.0.1:${this.port}${path}`, {
        method: 'POST', timeout: 3000
      }, () => resolve());
      req.on('error', reject);
      req.end();
    });
  }
}
```

### 0.3 Electron 启动流程改造

```typescript
// electron/main/index.ts (伪代码，展示集成点)

import { OpenFangManager } from './openfang-manager';

const openfangManager = new OpenFangManager();

app.whenReady().then(async () => {
  // ===== 并行启动两个后端 =====
  const [pythonResult, openfangResult] = await Promise.allSettled([
    pythonLauncher.start(),       // 现有: spawn Python launcher.py
    openfangManager.start()       // 新增: spawn OpenFang binary
  ]);

  // Python 是必须的，OpenFang 是可选的
  if (pythonResult.status === 'rejected') {
    showErrorDialog('Python 后端启动失败');
    return;
  }

  if (openfangResult.status === 'rejected') {
    console.warn('[OpenFang] 启动失败，agent 执行功能将不可用');
    // 继续运行，降级模式
  }

  // 将 OpenFang 端口通过环境变量 / IPC 告知 Python 侧
  // 方案 A: 写入 port_config.json (Python 侧已有读取逻辑)
  writePortConfig({ openfang_port: openfangManager.getPort() });

  // 方案 B: 通过 NEKO_EVENT 机制通知 (如果 Python 侧监听)
  // pythonProcess.stdin.write(JSON.stringify({ event: 'openfang_ready', port: 50051 }));

  createMainWindow();
});

app.on('before-quit', async () => {
  // 并行关闭
  await Promise.allSettled([
    pythonLauncher.stop(),
    openfangManager.stop()
  ]);
});
```

### 0.4 端口通信桥接

Electron 需要把 OpenFang 的端口号传给 Python 侧。最简方案是复用现有的 `port_config.json`：

```json
// %APPDATA%/N.E.K.O/port_config.json
{
  "MAIN_SERVER_PORT": 48911,
  "MEMORY_SERVER_PORT": 48912,
  "TOOL_SERVER_PORT": 48915,
  "OPENFANG_PORT": 50051
}
```

Python 侧 `config/__init__.py` 已有读取 port_config.json 的逻辑，新增一个字段即可：

```python
# config/__init__.py 新增
OPENFANG_PORT = _read_port_env("OPENFANG_PORT", 50051)
OPENFANG_BASE_URL = f"http://127.0.0.1:{OPENFANG_PORT}"
```

### 0.5 OpenFang 默认配置

```toml
# resources/openfang/config.default.toml
# 由 Electron 首次启动时复制到用户数据目录
# NEKO 专用配置：抹除人格、关闭 channels、纯 API 执行模式

# 基础设置
home_dir = "~/.neko/openfang"
data_dir = "~/.neko/openfang/data"
log_level = "info"
api_listen = "127.0.0.1:50051"    # 只绑定 localhost
api_key = ""                       # 启动后由 Python 侧动态推送

# 默认模型 — 占位，启动后由 NEKO sync_config 覆盖
[default_model]
provider = "openai"
model = "gpt-4o"

# 关闭所有 Channel — OpenFang 只通过 A2A API 接受指令
# (不配置任何 [channels.*] 段)

# 关闭所有 Hands 自主调度 — 完全由 NEKO 控制
[hands]
# 全部不启用

# 内存配置
[memory]
embedding_model = "all-MiniLM-L6-v2"
consolidation_threshold = 10000
decay_rate = 0.5
```

---

## Phase 1: OpenFangAdapter — Python 侧适配层

### 1.1 新增 `brain/openfang_adapter.py`

**核心职责**: 封装所有与 OpenFang 的 HTTP 通信，提供与现有 ComputerUseAdapter / BrowserUseAdapter 一致的接口。**不管进程生命周期**——那是 Electron 的事。

```python
"""
brain/openfang_adapter.py
OpenFang Agent 执行后端适配器

职责 (仅通信，不管进程):
1. 通过 A2A API 下发任务
2. 轮询/SSE 监听任务状态
3. API Key 下发与配置同步
4. 健康检查 (仅检测连通性，不负责启停)
"""

import httpx
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Callable
from config import OPENFANG_BASE_URL
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

    注意: 本适配器不管理 OpenFang 进程生命周期。
    进程由 Electron main process 管理，本层只做 HTTP 通信。
    """

    def __init__(self, base_url: str = None):
        self.base_url = base_url or OPENFANG_BASE_URL
        self.init_ok = False
        self.last_error: Optional[str] = None
        self._active_tasks: Dict[str, str] = {}   # neko_task_id -> openfang_task_id
        self._api_key: Optional[str] = None        # OpenFang Bearer token
        self._config_synced = False
        self._cached_version: Optional[str] = None
        self._cached_tools_count: Optional[int] = None

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
        2. 轮询 GET /a2a/tasks/{id} 直到完成
        3. 返回结果
        """
        try:
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
            [self._active_tasks[neko_task_id]] if neko_task_id and neko_task_id in self._active_tasks
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
        """
        同步健康检查 (在线程池中调用)
        仅检测连通性，不负责启停——启停是 Electron 的事
        """
        try:
            with httpx.Client(timeout=3.0) as client:
                r = client.get(
                    f"{self.base_url}/api/health",
                    headers=self._auth_headers()
                )
                self.init_ok = r.status_code == 200
                if self.init_ok:
                    self.last_error = None
                    status = r.json()
                    self._cached_version = status.get("version")
                    self._cached_tools_count = status.get("tools_count")
                return self.init_ok
        except Exception as e:
            self.last_error = str(e)
            self.init_ok = False
            return False

    # ─────────────────────────────────────────────
    #  配置同步 (NEKO Python → OpenFang)
    # ─────────────────────────────────────────────

    async def sync_config(self) -> bool:
        """
        将 NEKO 侧的 API Key 和模型配置推送到 OpenFang

        通过 POST /api/providers/{name}/key 端点
        运行时推送，不需要重启 OpenFang daemon
        """
        cm = get_config_manager()

        provider_mappings = {
            # neko_provider_key -> openfang_provider_name
            "openai": "openai",
            "qwen": "openai",      # 阿里走 OpenAI compatible
            "stepfun": "openai",   # 阶跃走 OpenAI compatible
            "gemini": "gemini",
            "glm": "openai",       # 智谱走 OpenAI compatible
            "anthropic": "anthropic"
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                synced_count = 0

                for neko_key, of_provider in provider_mappings.items():
                    api_cfg = cm.get_model_api_config_by_provider(neko_key)
                    if not api_cfg or not api_cfg.get("api_key"):
                        continue

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
        向 OpenFang 注册一个无人格执行 Agent
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
            "temperature": 0.1,
            "tools": agent_config.get("tools", []),
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
    suggested_tools: List[str] = field(default_factory=list)
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
    - 邮件/消息发送

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

    # ===== OpenFang 初始化 (仅通信层，不管进程) =====
    try:
        from brain.openfang_adapter import OpenFangAdapter

        Modules.openfang = OpenFangAdapter()  # 从 config 读 OPENFANG_BASE_URL

        # 异步等待 OpenFang 就绪 (Electron 已并行启动它)
        asyncio.create_task(_init_openfang())
    except ImportError:
        logger.info("[OpenFang] adapter not available, skipping")
    except Exception as e:
        logger.warning(f"[OpenFang] init failed: {e}")

    # 注入到 task_executor
    if Modules.task_executor and Modules.openfang:
        Modules.task_executor.openfang = Modules.openfang


async def _init_openfang():
    """
    后台等待 OpenFang 连通 + 配置同步

    注意: 不负责启动 OpenFang，只等它就绪。
    OpenFang 由 Electron 并行启动，通常比 Python 先就绪 (180ms vs 数秒)。
    """
    adapter = Modules.openfang
    if not adapter:
        return

    # 等待连通 (最多 30s，通常 <1s 因为 Electron 并行启动)
    for i in range(30):
        if adapter.check_connectivity():
            break
        await asyncio.sleep(1)

    if not adapter.init_ok:
        logger.warning("[OpenFang] not reachable after 30s (Electron may not have started it)")
        Modules.capability_cache["openfang"] = {
            "ready": False,
            "reason": "OpenFang 未就绪 (检查 Electron 侧是否启用)"
        }
        return

    # 同步 API Key
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
        logger.info(f"[OpenFang] Ready, executor agent: {agent_id}")
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

    Modules.task_registry[task_id] = {
        "id": task_id,
        "type": "openfang",
        "status": "running",
        "instruction": instruction,
        "start_time": datetime.now().isoformat()
    }

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

        Modules.task_registry[task_id]["status"] = "completed" if result["success"] else "failed"
        Modules.task_registry[task_id]["result"] = result
        Modules.task_registry[task_id]["end_time"] = datetime.now().isoformat()

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

```text
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

### 4.2 `config/__init__.py` 新增

```python
# config/__init__.py

# OpenFang 端口 (由 Electron 写入 port_config.json)
OPENFANG_PORT = _read_port_env("OPENFANG_PORT", 50051)
OPENFANG_BASE_URL = f"http://127.0.0.1:{OPENFANG_PORT}"
```

### 4.3 `core_config.json` 新增字段

```json
{
  "openfang_enabled": true,
  "openfang_auto_start": true
}
```

注意: `openfang_base_url` 和 `openfang_binary_path` 不再需要放在 Python 侧配置中——端口通过 port_config.json 从 Electron 传入，二进制路径由 Electron 管理。

---

## Phase 5: 前端 UI 集成

### 5.1 设置页面

在 NEKO 的设置 Web UI 中新增 OpenFang 配置区:

```text
┌─────────────────────────────────────────┐
│ OpenFang Agent 执行后端                  │
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

**"重启"按钮**通过 Electron IPC 调用 `openfangManager.stop()` + `openfangManager.start()`，不经过 Python。

### 5.2 任务状态展示

在 NEKO 聊天界面中，当 OpenFang 执行任务时显示:

```text
┌─────────────────────────────────────────┐
│ OpenFang 正在执行...                     │
│                                         │
│ 任务: 搜索最近的 AI 新闻并整理摘要        │
│ 状态: 运行中 (12s)                       │
│ 步骤: 3/? (web_search → parse → ...)    │
│                                         │
│ [取消]                                   │
└─────────────────────────────────────────┘
```

---

## Phase 6: 安全与边界

### 6.1 权限隔离

```text
NEKO (宿主环境)               OpenFang (受控环境)
──────────────────            ──────────────────
GUI 操作: ✅                  GUI 访问: ❌
用户文件系统: ✅               仅限 sandbox 目录
浏览器控制: ✅                 无浏览器 (除非启用 Browser Hand)
语音/Live2D: ✅                无多媒体
用户对话/人格: ✅               无人格 (纯执行者)
进程管理: ❌ (Electron管)      进程管理: ❌ (Electron管)
```

### 6.2 OpenFang Agent Manifest 安全约束

```toml
# NEKO 推送给 OpenFang 的 agent manifest

[agent]
name = "neko-executor"
system_prompt = "You are a task executor. Execute instructions precisely. No conversation."
temperature = 0.1

[capabilities]
file_read = ["/tmp/neko-workspace/*"]
file_write = ["/tmp/neko-workspace/*"]
net_connect = ["api.openai.com", "generativelanguage.googleapis.com", "dashscope.aliyuncs.com"]  # 最小权限; 开发环境可设 ["*"]
shell_exec = true               # 在 WASM 沙箱中执行
```

---

## 实施优先级与里程碑

### Milestone 1 — Electron 侧 + 基础连通 (1-2 周)
- [ ] N.E.K.O.-PC: `OpenFangManager` 类实现
- [ ] N.E.K.O.-PC: 并行启动逻辑 (`Promise.allSettled`)
- [ ] N.E.K.O.-PC: OpenFang 二进制打包到 resources/
- [ ] N.E.K.O.-PC: port_config.json 增加 OPENFANG_PORT
- [ ] Python 侧: `brain/openfang_adapter.py` 核心实现
- [ ] Python 侧: `config/__init__.py` 读取 OPENFANG_PORT
- [ ] 配置同步 (API Key 下发)
- [ ] 手动测试: Electron → 并行启动 → NEKO ↔ OpenFang 通信

### Milestone 2 — 智能路由 (1 周)
- [ ] `_assess_openfang()` 评估逻辑
- [ ] `analyze_and_execute()` 集成 OpenFang 决策分支
- [ ] 决策优先级调优 (UserPlugin > OpenFang > Browser > CUA)
- [ ] ZMQ 事件推送 (进度/结果)

### Milestone 3 — 前端集成 (1 周)
- [ ] 设置页面 OpenFang 配置区
- [ ] 聊天界面任务状态展示
- [ ] Electron IPC: 重启/停止 OpenFang
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
| OpenFang daemon 崩溃 | 中 | Electron 侧进程守护，自动重启 (上限 5 次) |
| 两套 LLM 调用导致 token 消耗翻倍 | 中 | 路由评估用轻量模型，仅确认路由的任务才调 OpenFang |
| 配置同步失败 (API Key) | 低 | 重试机制 + 用户手动同步按钮 |
| OpenFang 工具执行出错无法调试 | 中 | 保留 OpenFang 日志，提供 [查看日志] 入口 |
| Electron 升级后二进制兼容性 | 低 | OpenFang 是独立进程，不依赖 Electron runtime |

---

## 与旧方案的关键差异总结

| 项目 | 旧方案 (v1) | 新方案 (v2) |
|------|-------------|-------------|
| 进程管理者 | Python launcher.py | Electron main process |
| 启动方式 | 串行 (Python → OpenFang) | 并行 (Electron 同时 spawn 两者) |
| 生命周期耦合 | Python 崩 = OpenFang 崩 | 互相独立 |
| launcher.py 改动 | 新增 OpenFangProcessManager | 无改动 |
| N.E.K.O.-PC 改动 | 无 | 新增 OpenFangManager |
| Python adapter 职责 | 通信 + 进程管理 | 仅通信 |
| 端口传递 | 环境变量 | port_config.json (复用现有机制) |
| 总套壳层数 | 3 层 | 2 层 |
