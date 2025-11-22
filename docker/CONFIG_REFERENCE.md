# 🔧 N.E.K.O. 配置项完整参考

本文档列出了 N.E.K.O. 项目所有可配置的项目。

## 📍 配置文件位置

### 代码中的配置定义

1. **`config/__init__.py`** - 所有默认常量的定义
   - 服务器端口
   - 模型名称
   - API 提供商配置
   
2. **`utils/config_manager.py`** - 配置加载和管理逻辑
   - `get_core_config()` 方法：合并默认值和用户配置
   - 第 569-728 行：完整的配置加载流程

3. **`config/api_providers.json`** - API 服务商配置
   - 各个提供商的 URL、模型名称等

### 用户配置文件

4. **`config/core_config.json`** - 用户的运行时配置
   - API Keys
   - API 提供商选择
   - MCP Token

5. **`config/characters.json`** - 角色和人设配置
6. **`config/user_preferences.json`** - 用户偏好设置
7. **`config/voice_storage.json`** - 语音配置存储

## 📋 完整配置项列表

### 1. 核心 API 配置

| 配置项 | 配置文件字段 | 环境变量 | 默认值 | 说明 |
|-------|------------|---------|--------|------|
| 核心 API Key | `coreApiKey` | `NEKO_CORE_API_KEY` | `""` | 核心 API 的密钥 |
| 核心 API 提供商 | `coreApi` | `NEKO_CORE_API` | `"qwen"` | 可选: qwen, openai, glm, step, free |
| 辅助 API 提供商 | `assistApi` | `NEKO_ASSIST_API` | `"qwen"` | 用于记忆、情感分析等 |

### 2. 各提供商的 API Keys

| 配置项 | 配置文件字段 | 环境变量 | 默认值 |
|-------|------------|---------|--------|
| 阿里云 API Key | `assistApiKeyQwen` | `NEKO_ASSIST_API_KEY_QWEN` | `""` |
| OpenAI API Key | `assistApiKeyOpenai` | `NEKO_ASSIST_API_KEY_OPENAI` | `""` |
| 智谱 API Key | `assistApiKeyGlm` | `NEKO_ASSIST_API_KEY_GLM` | `""` |
| 阶跃星辰 API Key | `assistApiKeyStep` | `NEKO_ASSIST_API_KEY_STEP` | `""` |
| 硅基流动 API Key | `assistApiKeySilicon` | `NEKO_ASSIST_API_KEY_SILICON` | `""` |
| MCP Router Token | `mcpToken` | `NEKO_MCP_TOKEN` | `""` |

### 3. 服务器端口配置

这些配置定义在 `config/__init__.py`，通常不需要修改。

| 配置项 | 代码常量 | 环境变量 | 默认值 | 说明 |
|-------|---------|---------|--------|------|
| 主服务器端口 | `MAIN_SERVER_PORT` | `NEKO_MAIN_SERVER_PORT` | `48911` | Web UI 访问端口 |
| 记忆服务器端口 | `MEMORY_SERVER_PORT` | `NEKO_MEMORY_SERVER_PORT` | `48912` | 记忆管理服务 |
| 监控服务器端口 | `MONITOR_SERVER_PORT` | `NEKO_MONITOR_SERVER_PORT` | `48913` | 监控服务 |
| 评论服务器端口 | `COMMENTER_SERVER_PORT` | - | `48914` | 评论服务 |
| 工具服务器端口 | `TOOL_SERVER_PORT` | `NEKO_TOOL_SERVER_PORT` | `48915` | Agent 服务 |

### 4. MCP Router 配置

| 配置项 | 代码常量 | 环境变量 | 默认值 |
|-------|---------|---------|--------|
| MCP Router URL | `MCP_ROUTER_URL` | `NEKO_MCP_ROUTER_URL` | `http://localhost:3283` |

### 5. 模型配置

#### 直接被导入使用的模型（config/__init__.py 第 26-30 行）

| 配置项 | 代码常量 | 默认值 | 用途 |
|-------|---------|--------|------|
| 路由模型 | `ROUTER_MODEL` | `openai/gpt-4.1` | 记忆路由决策 |
| 设置提议模型 | `SETTING_PROPOSER_MODEL` | `qwen-max` | 设置更新提议 |
| 设置验证模型 | `SETTING_VERIFIER_MODEL` | `qwen-max` | 设置更新验证 |
| 语义模型 | `SEMANTIC_MODEL` | `text-embedding-v4` | 语义嵌入 |
| 重排序模型 | `RERANKER_MODEL` | `qwen-plus` | 搜索结果重排序 |

#### 通过 config_manager 动态获取的模型（config/__init__.py 第 33-36 行）

| 配置项 | 代码常量 | 环境变量 | 默认值 | 用途 |
|-------|---------|---------|--------|------|
| 摘要模型 | `DEFAULT_SUMMARY_MODEL` | `NEKO_SUMMARY_MODEL` | `qwen-plus` | 对话摘要 |
| 纠错模型 | `DEFAULT_CORRECTION_MODEL` | `NEKO_CORRECTION_MODEL` | `qwen-max` | 文本纠错 |
| 情感模型 | `DEFAULT_EMOTION_MODEL` | `NEKO_EMOTION_MODEL` | `qwen-turbo` | 情感分析 |
| 视觉模型 | `DEFAULT_VISION_MODEL` | `NEKO_VISION_MODEL` | `qwen3-vl-plus-2025-09-23` | 图像理解 |

#### 未使用的占位符模型（config/__init__.py 第 39-40 行）

| 配置项 | 代码常量 | 默认值 | 状态 |
|-------|---------|--------|------|
| 全模态模型 | `DEFAULT_OMNI_MODEL` | `""` | 暂未使用 |
| TTS 模型 | `DEFAULT_TTS_MODEL` | `""` | 暂未使用 |

### 6. API 提供商详细配置

这些配置定义在 `config/__init__.py` 的 `DEFAULT_CORE_API_PROFILES` 和 `DEFAULT_ASSIST_API_PROFILES`。

#### 核心 API 提供商（第 87-121 行）

| 提供商 | URL | 模型 | 说明 |
|-------|-----|------|------|
| free | ws://47.100.209.206:9805 | free-model | 免费版 |
| qwen | wss://dashscope.aliyuncs.com/api-ws/v1/realtime | qwen3-omni-flash-realtime | 阿里云 |
| glm | wss://open.bigmodel.cn/api/paas/v4/realtime | glm-realtime-air | 智谱 |
| openai | wss://api.openai.com/v1/realtime | gpt-realtime | OpenAI |
| step | wss://api.stepfun.com/v1/realtime | step-audio-2 | 阶跃星辰 |

#### 辅助 API 提供商（第 123-160 行）

每个提供商都有4个模型配置：
- `SUMMARY_MODEL` - 摘要模型
- `CORRECTION_MODEL` - 纠错模型
- `EMOTION_MODEL` - 情感模型
- `VISION_MODEL` - 视觉模型

### 7. 自定义模型配置（高级）

这些配置允许为每个模型指定不同的提供商、URL 和 API Key。

| 配置项前缀 | 用途 | 可配置的字段 |
|-----------|------|-------------|
| `SUMMARY_MODEL_*` | 摘要模型 | PROVIDER, URL, API_KEY |
| `CORRECTION_MODEL_*` | 纠错模型 | PROVIDER, URL, API_KEY |
| `EMOTION_MODEL_*` | 情感模型 | PROVIDER, URL, API_KEY |
| `VISION_MODEL_*` | 视觉模型 | PROVIDER, URL, API_KEY |
| `OMNI_MODEL_*` | 全模态模型 | PROVIDER, URL, API_KEY |
| `TTS_MODEL_*` | TTS 模型 | PROVIDER, URL, API_KEY |

## 🔄 配置优先级

配置系统按以下优先级加载配置：

1. **环境变量**（最高优先级）
   - 格式：`NEKO_*`
   - 示例：`NEKO_CORE_API_KEY=xxx`

2. **用户配置文件**
   - 位置：`config/core_config.json`
   - 来源：用户通过 Web UI 或手动编辑

3. **API 提供商配置**
   - 位置：`config/api_providers.json`
   - 来源：项目内置或用户自定义

4. **代码默认值**（最低优先级）
   - 位置：`config/__init__.py`
   - 来源：代码中的常量定义

## 📝 配置加载流程

```python
# 在 utils/config_manager.py 的 get_core_config() 方法中：

# 1. 从 config/__init__.py 导入默认值
from config import DEFAULT_CORE_API_KEY, DEFAULT_SUMMARY_MODEL, ...

# 2. 创建初始配置字典（使用默认值）
config = {
    'CORE_API_KEY': DEFAULT_CORE_API_KEY,
    'SUMMARY_MODEL': DEFAULT_SUMMARY_MODEL,
    ...
}

# 3. 读取 core_config.json（用户配置）
core_cfg = load_json('core_config.json')

# 4. 更新 API Keys
if core_cfg.get('coreApiKey'):
    config['CORE_API_KEY'] = core_cfg['coreApiKey']

# 5. 根据选择的提供商，从 api_providers.json 加载配置
core_profile = get_core_api_profiles().get(core_cfg['coreApi'])
if core_profile:
    config.update(core_profile)  # 覆盖默认值

# 6. 处理环境变量（如果实现）
# 环境变量会覆盖上述所有配置
```

## 🐳 Docker 部署配置方式

### 方式 1：环境变量（推荐）

在 `docker-compose.yml` 或 `.env` 文件中设置：

```bash
NEKO_CORE_API_KEY=your-key
NEKO_CORE_API=qwen
NEKO_ASSIST_API=qwen
```

### 方式 2：挂载配置文件

```yaml
volumes:
  - ./config:/app/config
```

然后在 `./config/core_config.json` 中配置。

### 方式 3：混合使用

- 使用环境变量配置 API Keys（安全性）
- 使用配置文件配置其他选项（灵活性）

## 🔍 查看当前配置

### 在 Docker 容器中

```bash
# 查看配置文件
docker exec neko cat /app/config/core_config.json

# 查看环境变量
docker exec neko env | grep NEKO_

# 查看运行时配置
docker exec neko python -c "
from utils.config_manager import get_config_manager
import json
config = get_config_manager().get_core_config()
print(json.dumps(config, indent=2, ensure_ascii=False))
"
```

### 在开发环境中

```python
from utils.config_manager import get_config_manager
config_manager = get_config_manager()
config = config_manager.get_core_config()
print(config)
```

## 📚 相关文件索引

- `config/__init__.py` (第 14-69 行) - 端口和模型默认值
- `config/__init__.py` (第 87-160 行) - API 提供商配置
- `config/__init__.py` (第 102-112 行) - core_config.json 结构
- `utils/config_manager.py` (第 569-728 行) - 配置加载逻辑
- `config/api_providers.json` - API 提供商详细配置
- `main_server.py` (第 324-432 行) - 配置读写 API

## ⚠️ 注意事项

1. **不要在代码中硬编码 API Key**
   - 使用环境变量或配置文件

2. **配置文件不要提交到 Git**
   - `core_config.json` 已在 `.gitignore` 中

3. **Docker 部署时的安全性**
   - 使用 Docker secrets 存储敏感信息
   - 或使用环境变量（不要记录在日志中）

4. **模型名称必须与提供商匹配**
   - 不同提供商的模型名称不同
   - 参考 `api_providers.json` 中的配置

