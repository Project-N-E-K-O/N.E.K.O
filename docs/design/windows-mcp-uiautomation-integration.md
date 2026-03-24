# Windows MCP + UIAutomation 集成设计方案

## 1. 现状分析

### 1.1 当前 Agent 执行链

```
用户消息 → main_server (ZMQ) → agent_server
  → DirectTaskExecutor.analyze_and_execute()
    → 并行评估:
      ├─ _assess_computer_use()  → ComputerUseDecision
      ├─ _assess_browser_use()   → BrowserUseDecision
      └─ _assess_user_plugin()   → UserPluginDecision
    → 决策优先级: UserPlugin > BrowserUse > ComputerUse
  → agent_server dispatch (队列调度 / 直接执行)
```

### 1.2 当前 ComputerUse 的工作方式

`brain/computer_use.py` 中的 `ComputerUseAdapter.run_instruction()`:

```
循环 (最多 50 步):
  1. pyautogui.screenshot()          ← 硬编码，前台截图
  2. compress_screenshot() → jpg
  3. VLM predict() → thought + action + code
  4. exec(code) with _ScaledPyAutoGUI ← 硬编码，前台操作
```

**问题：**
- 每步都需要 VLM 推理（慢、费 token）
- 只能操作前台（抢占用户桌面）
- 通过坐标操作（受 DPI/分辨率影响）
- 没有语义理解（不知道控件是什么，只看像素）

### 1.3 已有的 MCP 基础设施

`plugin/plugins/mcp_adapter/` 已实现完整的 MCP Client：
- 支持 stdio / SSE / streamable-http 三种 transport
- 自动发现 MCP server 的 tools 并注册为 NEKO entries
- 通过 UserPlugin 执行路径调用 MCP tools
- 有 reconnect、error handling、payload normalization

---

## 2. 设计目标

1. **引入 UIAutomation 快车道**：标准 Windows 控件操作直接走 UIAutomation（毫秒级，零 VLM token）
2. **保留 VLM 兜底**：UIAutomation 搞不定的场景（自绘 UI、游戏等）回退到现有 ComputerUse
3. **复用已有 MCP adapter**：不重新造轮子，通过 mcp_adapter 桥接 Windows MCP 服务器
4. **支持后台窗口操作**：UIAutomation 天然支持后台控件交互，为未来虚拟桌面方案铺路
5. **对现有代码侵入最小**：不改 ComputerUse 核心逻辑，新增平行路径

---

## 3. 架构设计

### 3.1 整体架构

```
                          DirectTaskExecutor.analyze_and_execute()
                                        │
                    ┌───────────────────┼───────────────────┐
                    │                   │                   │
              _assess_uia()    _assess_computer_use()  _assess_browser_use()
              (NEW - 新增)       (现有)                  (现有)
                    │                   │                   │
              UIADecision        CUDecision           BUDecision
                    │                   │                   │
                    ▼                   ▼                   ▼

  决策优先级: UserPlugin > UIA > BrowserUse > ComputerUse
                                ^^^
                            (新增，高于 BU/CU)

  执行路径:
  ┌─────────────────────────────────────────────────────┐
  │ execution_method == "uia"                           │
  │   → 通过 mcp_adapter 调用 Windows MCP Server        │
  │   → MCP Server 内部走 UIAutomation / pywinauto      │
  │   → 返回操作结果（文本/截图/状态）                    │
  └─────────────────────────────────────────────────────┘
  ┌─────────────────────────────────────────────────────┐
  │ execution_method == "computer_use"                  │
  │   → 现有 VLM + pyautogui 流程（完全不变）            │
  └─────────────────────────────────────────────────────┘
```

### 3.2 Windows MCP Server 选型

推荐 **pywinauto-mcp** 或 **Windows-MCP.Net** 作为外部 MCP Server，理由：

| 特性 | pywinauto-mcp | Windows-MCP.Net |
|------|--------------|-----------------|
| UIAutomation 支持 | ✓ (pywinauto 底层) | ✓ (.NET UIA) |
| 后台窗口操作 | 部分支持 | 部分支持 |
| OCR | 需额外配置 | 内置 |
| MCP 协议版本 | 3.1 (stdio) | stdio |
| 语言 | Python | C# (.NET 8) |
| 与 NEKO 技术栈 | 同栈（Python） | 异栈 |

**建议优先用 pywinauto-mcp**（Python 同栈，如需定制可以直接改源码）。

### 3.3 MCP Server 配置

在 NEKO 的 MCP adapter 配置中添加 Windows 桌面自动化 server：

```json
{
  "name": "windows-desktop",
  "transport": "stdio",
  "command": "python",
  "args": ["-m", "pywinauto_mcp"],
  "enabled": true
}
```

mcp_adapter 会自动发现该 server 暴露的 tools（如 `automation_windows`, `automation_elements`, `automation_mouse`, `automation_keyboard`, `automation_visual`）并注册为 NEKO entries。

---

## 4. 代码改动详细设计

### 4.1 新增 `UIADecision` 数据类

**文件**: `brain/task_executor.py`

```python
@dataclass
class UIADecision:
    """UIAutomation (via Windows MCP) 可行性评估结果"""
    has_task: bool = False
    can_execute: bool = False
    task_description: str = ""
    tool_name: str = ""        # MCP tool 名称，如 "automation_elements"
    tool_args: Dict = None     # MCP tool 参数
    reason: str = ""
```

### 4.2 新增 `_assess_uia()` 方法

**文件**: `brain/task_executor.py`

核心思路：判断任务是否可以通过 UIAutomation 的语义操作完成（而非截图+坐标）。

```python
async def _assess_uia(
    self,
    conversation: str,
    uia_tools: List[Dict[str, Any]],  # 从 mcp_adapter 获取的 windows-desktop tools
) -> UIADecision:
    """
    评估任务是否适合 UIAutomation（精确控件操作）而非 VLM+坐标。

    适合 UIA 的场景：
    - 打开/关闭/切换已知应用
    - 点击已知名称的按钮/菜单
    - 在文本框中输入内容
    - 读取窗口/控件中的文本
    - 操作标准 Windows 控件（列表、树形、选项卡等）

    不适合 UIA 的场景：
    - 操作目标是屏幕上某个视觉位置（"点击左上角的图标"）
    - 目标应用使用自绘 UI（游戏、某些 Electron 应用）
    - 需要视觉理解（"看看屏幕上显示了什么"）
    """
    if not uia_tools:
        return UIADecision(has_task=False, can_execute=False, reason="No UIA tools available")

    # ... LLM 评估逻辑，与 _assess_computer_use 结构类似
    # 但 system_prompt 专注于判断"这个任务能否通过控件名/控件类型精确操作完成"
```

**评估 prompt 的关键区分点**：

```
你是一个 Windows 桌面自动化评估 agent。判断用户的任务是否可以通过
UIAutomation（按控件名称/类型精确操作）完成，还是必须通过截图+坐标操作。

可以用 UIAutomation 的例子：
- "打开记事本" → 可以，通过 shell 或 automation_system 启动
- "在搜索框里输入xxx" → 可以，定位 Edit 控件并输入
- "点击'保存'按钮" → 可以，按名称定位 Button 控件并 Invoke
- "把音量调到50%" → 可以，通过 automation_system

必须用截图+坐标的例子：
- "点击屏幕中间那个红色图标" → 不行，需要视觉定位
- "看看网页上显示了什么" → 不行，需要截图理解
- "在游戏里移动角色" → 不行，游戏 UI 没有 UIA 控件树
```

### 4.3 修改 `analyze_and_execute()` 决策逻辑

**文件**: `brain/task_executor.py`

在现有的并行评估中增加 UIA 分支：

```python
# === 新增：获取 UIA (Windows MCP) 工具列表 ===
uia_tools = []
uia_enabled = agent_flags.get("uia_enabled", False)
if uia_enabled:
    # 从 mcp_adapter 获取 windows-desktop server 的 tools
    uia_tools = await self._get_uia_tools()

# 并行评估
if uia_enabled and uia_tools:
    assessment_tasks.append(('uia', self._assess_uia(conversation, uia_tools)))

# ... 现有的 cu / bu / up 评估 ...

# 决策优先级调整：
# 1. UserPlugin (精确匹配用户插件)
# 2. UIA (精确控件操作，零 VLM 开销)    ← 新增
# 3. BrowserUse (网页自动化)
# 4. ComputerUse (VLM + 坐标，兜底)
```

### 4.4 新增 UIA 执行路径

**文件**: `agent_server.py`

在 `_do_analyze_and_plan()` 的 dispatch 逻辑中新增：

```python
elif result.execution_method == 'uia':
    # UIA 任务通过 mcp_adapter plugin 执行
    # 复用 user_plugin 的执行路径，因为 MCP tools 已经注册为 NEKO entries
    if Modules.agent_flags.get("uia_enabled", False) and Modules.task_executor:
        # ... 与 user_plugin dispatch 类似的逻辑
        # 通过 _execute_user_plugin() 调用 mcp_adapter 暴露的 entry
```

### 4.5 agent_flags 新增开关

**文件**: `agent_server.py` (Modules 类)

```python
agent_flags: Dict[str, Any] = {
    "computer_use_enabled": False,
    "browser_use_enabled": False,
    "user_plugin_enabled": False,
    "uia_enabled": False,          # ← 新增
}
```

前端设置界面对应新增一个"Windows 智能操作 (UIAutomation)"开关。

### 4.6 UIA 工具发现

**文件**: `brain/task_executor.py`

```python
async def _get_uia_tools(self) -> List[Dict[str, Any]]:
    """从 mcp_adapter 获取 windows-desktop MCP server 的工具列表"""
    plugins = await self.plugin_list_provider(force_refresh=False)
    uia_tools = []
    for p in plugins:
        if not isinstance(p, dict):
            continue
        pid = p.get("id", "")
        # mcp_adapter 注册的 entries 的 id 格式为 "mcp_{server_name}_{tool_name}"
        # 匹配 windows-desktop server 的 tools
        entries = p.get("entries", [])
        for entry in entries:
            eid = entry.get("id", "") if isinstance(entry, dict) else ""
            if "automation_" in eid or "windows" in pid.lower():
                uia_tools.append(entry)
    return uia_tools
```

---

## 5. 执行流程对比

### 场景 A: "打开计算器"

**现有流程 (ComputerUse):**
```
1. pyautogui.screenshot()              → 300ms
2. VLM: "看到桌面，需要打开计算器"       → 2-5s, ~500 tokens
3. exec: pyautogui.hotkey('win')        → 100ms
4. pyautogui.screenshot()              → 300ms
5. VLM: "看到开始菜单搜索框"            → 2-5s, ~500 tokens
6. exec: pyautogui.write('calculator')  → 200ms
7. pyautogui.screenshot()              → 300ms
8. VLM: "看到搜索结果"                  → 2-5s, ~500 tokens
9. exec: pyautogui.press('enter')       → 100ms
总计: ~10-18s, ~1500 tokens, 3次 VLM 调用
```

**新流程 (UIA via MCP):**
```
1. LLM assess: "打开计算器 → UIA 可处理"  → 1-2s, ~200 tokens (一次性)
2. MCP call: automation_system.launch("calc.exe") → 200ms
总计: ~1-2s, ~200 tokens, 1次 LLM 调用 (仅评估)
```

### 场景 B: "点击屏幕上那个蓝色按钮"

**新流程：**
```
1. LLM assess: "蓝色按钮 → 需要视觉定位 → UIA 不适合"
2. Fallback to ComputerUse (现有流程，不变)
```

---

## 6. 未来扩展：虚拟桌面集成点

当前设计中，UIA 路径与 ComputerUse 路径完全解耦。未来接入虚拟桌面时：

```
ScreenBackend (抽象层)
  ├─ ForegroundBackend     → pyautogui (现有)
  └─ VirtualDesktopBackend → CreateDesktop / RDP / Agent Workspace
                                     ↑
                              UIA 路径天然支持后台窗口
                              只需要将 MCP Server 启动在
                              虚拟桌面的会话中即可
```

UIA 路径（通过 MCP Server）只需要把 MCP Server 进程启动在虚拟桌面/RDP 会话里，NEKO 侧代码零改动。

---

## 7. 改动文件清单

| 文件 | 改动 | 工作量 |
|------|------|--------|
| `brain/task_executor.py` | 新增 UIADecision, _assess_uia(), _get_uia_tools(), 修改 analyze_and_execute() 决策逻辑 | 中 |
| `agent_server.py` | 新增 uia dispatch 分支, agent_flags 新增 uia_enabled | 小 |
| `config/` | MCP server 配置新增 windows-desktop | 小 |
| `static/` + `templates/` | 前端设置新增 UIA 开关 | 小 |
| `brain/result_parser.py` | 新增 parse_uia_result() | 小 |
| (外部) pywinauto-mcp | 安装 + 配置为 MCP Server | 配置 |

**总工作量估算：2-3 天核心开发，1 天集成测试。**

---

## 8. 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| pywinauto-mcp 的 UIA 工具覆盖不全 | 可以 fork 后补充自定义 tools；MCP 协议让替换 server 实现零成本 |
| LLM 评估误判（该走 UIA 的走了 CU，或反过来） | 初期可以加规则硬匹配（如"打开xxx"直接走 UIA），减少 LLM 依赖 |
| Windows MCP Server 进程崩溃 | mcp_adapter 已有 reconnect 机制 |
| 部分应用 UIA 控件树残缺 | assess 阶段已考虑 fallback to CU |
| Docker 部署环境无 Windows | UIA 路径自动禁用，不影响现有功能 |
