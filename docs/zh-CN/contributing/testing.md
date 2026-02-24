# 测试

N.E.K.O. 拥有全面的测试套件，涵盖单元测试、前端集成测试和端到端流程测试。

## 环境搭建

```bash
# Install dependencies
uv sync

# Install Playwright browsers (for frontend & e2e tests)
uv run playwright install
```

### 测试用 API 密钥

```bash
cp tests/api_keys.json.template tests/api_keys.json
# Edit tests/api_keys.json with your API keys
```

此文件已被 gitignore 忽略，不会被提交。

## 运行测试

::: warning
所有测试命令必须使用 `uv run` 以确保使用正确的 Python 环境。
:::

```bash
# All tests (excluding e2e)
uv run pytest tests/ -s

# Unit tests only
uv run pytest tests/unit -s

# Frontend integration tests
uv run pytest tests/frontend -s

# End-to-end tests (requires explicit flag)
uv run pytest tests/e2e --run-e2e -s
```

## 测试结构

```
tests/
├── conftest.py                # 共享 fixtures（服务器生命周期、页面、数据目录）
├── api_keys.json              # API 密钥（已被 gitignore 忽略）
├── unit/
│   ├── test_providers.py      # 多服务商 API 连接测试
│   ├── test_text_chat.py      # OmniOfflineClient 文本 + 视觉聊天
│   ├── test_voice_session.py  # OmniRealtimeClient WebSocket 会话
│   └── test_video_session.py  # OmniRealtimeClient 视频/屏幕流
├── frontend/
│   ├── test_api_settings.py   # API 密钥设置页面
│   ├── test_chara_settings.py # 角色管理页面
│   ├── test_memory_browser.py # 记忆浏览器页面
│   ├── test_voice_clone.py    # 语音克隆页面
│   └── test_emotion.py        # Live2D + VRM 情感管理页面
├── e2e/
│   └── test_e2e_full_flow.py  # 完整应用旅程（8 个阶段）
├── utils/
│   ├── llm_judger.py          # 基于 LLM 的响应质量评估器
│   └── audio_streamer.py      # 音频流测试工具
└── test_inputs/
    ├── script.md              # 音频测试录制脚本
    └── screenshot.png         # 视觉测试截图
```

## 测试类别

### 单元测试（`tests/unit/`）

测试核心后端组件的独立功能：

- **服务商连接**：验证到所有支持的服务商的 API 连接
- **文本聊天**：使用文本和视觉输入测试 `OmniOfflineClient`
- **语音会话**：测试 `OmniRealtimeClient` WebSocket 连接
- **视频会话**：测试屏幕共享和视频流

### 前端测试（`tests/frontend/`）

使用 Playwright 测试 Web UI 页面：

- **API 设置**：密钥输入、服务商切换、保存/加载
- **角色设置**：CRUD 操作、性格编辑
- **记忆浏览器**：记忆文件列表、编辑、保存
- **语音克隆**：上传界面、语音预览
- **情感管理**：Live2D 和 VRM 情感映射

### 端到端测试（`tests/e2e/`）

完整的用户旅程测试，覆盖整个系统。这些测试需要 `--run-e2e` 标志，因为它们会：

- 启动真实的服务器进程
- 进行实际的 API 调用
- 运行时间较长

## 测试工具

### LLM 评判器（`tests/utils/llm_judger.py`）

基于 LLM 的评估器，用于评估响应质量。在端到端测试中使用，验证角色响应是否上下文合适、符合人设且事实合理。

### Playwright 模式

前端测试遵循**先侦察后操作**的模式：

1. 导航到页面
2. 等待 `networkidle`（对于 JS 渲染的内容至关重要）
3. 检查渲染后的 DOM
4. 使用发现的选择器执行操作

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('http://localhost:48911')
    page.wait_for_load_state('networkidle')
    # Now safe to interact with the page
```

::: tip
在 CI 环境中务必以 headless 模式启动 Chromium。在检查任何动态内容之前等待 `networkidle`。
:::

## 编写新测试

1. 将测试文件放在合适的子目录中（`unit/`、`frontend/`、`e2e/`）
2. 使用 pytest 标记：`@pytest.mark.unit`、`@pytest.mark.frontend`、`@pytest.mark.e2e`
3. 使用 `conftest.py` 中的共享 fixtures 处理服务器生命周期和页面配置
4. 遵循现有命名规范：`test_<module>_<feature>.py`
