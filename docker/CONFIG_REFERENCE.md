# 🔧 N.E.K.O. 配置项参考

本文档说明 N.E.K.O. 的配置文件放在哪里、有哪些主要配置项、各自怎样生效，以 Docker 部署为主。

## 📍 配置文件位置

### 代码中的配置定义

1. **`config/` 包** - 内置默认值。`config/__init__.py` 只是兼容门面，名字分别定义在各领域模块里，再由它统一导出；和本文相关的是下面几个：
   - `config/network.py`：服务端口（`MAIN_SERVER_PORT` 等）
   - `config/model_defaults.py`：默认模型名、URL、API Key（`DEFAULT_*`）
   - `config/api_profiles.py`：`core_config.json` 的默认结构 `DEFAULT_CORE_CONFIG`，服务商内置配置 `DEFAULT_CORE_API_PROFILES` / `DEFAULT_ASSIST_API_PROFILES`
   - `config/character_defaults.py`：用户配置文件清单 `CONFIG_FILES`、默认角色 `DEFAULT_CHARACTERS_CONFIG`

2. **`config/api_providers.json`** - 服务商目录，随代码和镜像一起发布（容器内为 `/app/config/api_providers.json`）
   - 各服务商的 URL、模型名、API Key 字段映射等
   - 由 `utils/api_config_loader.py` 的 `get_core_api_profiles()` / `get_assist_api_profiles()` 读取；文件缺失或 JSON 格式错误时回退到 `config/api_profiles.py` 的内置配置

3. **`utils/config_manager/`** - 配置加载和管理逻辑（包）
   - `storage_roots.py`：确定运行时数据根目录。`get_config_path()` 先找运行时根目录下的 `config/<文件名>`，找不到才退到项目目录的 `config/<文件名>`；`save_json_config()` 总是写入运行时根目录
   - `migrations.py`：运行时根目录缺少某个配置文件时，由 `migrate_config_files()` 从项目 `config/` 目录复制一份
   - `core_config.py`：`get_core_config()` 以默认值为底，合并 `core_config.json` 和所选服务商的配置；`get_model_api_config()` 按模型类型给出最终使用的 URL / 模型 / Key

### 用户配置文件

用户配置保存在**运行时数据根目录**下的 `config/` 子目录里。Docker 部署时：

- 容器内路径：`/home/neko/.local/share/N.E.K.O/config/`。`docker/docker-compose.yml`（以及 `docker/README_Docker.md` 里的 `docker run` 示例）用 `NEKO_STORAGE_SELECTED_ROOT` / `NEKO_STORAGE_ANCHOR_ROOT` 把根目录固定为 `/home/neko/.local/share/N.E.K.O`
- 宿主机路径：compose 把 `./neko-home` 挂载到 `/home/neko`，所以对应 compose 文件所在目录下的 `neko-home/.local/share/N.E.K.O/config/`
- 镜像内的 `/app/config/` 是代码目录（`config` 包，含 `api_providers.json` 和 `characters/` 角色模板），属于镜像层，容器重建后改动会丢失，下表的用户配置文件不放在这里。构建镜像时，`.dockerignore` 已排除 `config/core_config.json`、`characters.json`、`user_preferences.json`、`voice_storage.json`。例外：可选的 `livestream_config.json`、`meme_moderation_config.json` 只从 `/app/config/` 读取（`utils/api_config_loader.py`），要在容器里用就得放到这个路径（例如单独挂载这个文件）

| 文件 | 内容 | 如何产生 / 修改 |
|------|------|----------------|
| `core_config.json` | 服务商选择（`coreApi` / `assistApi`）、API Key（`coreApiKey`、`assistApiKey*`）、MCP Token（`mcpToken`），以及自定义 API、TTS 等选项；默认结构见 `DEFAULT_CORE_CONFIG` | 文件不存在时，由 `docker/entrypoint.sh` 的 `setup_configuration` 在启动时根据 `NEKO_*` 环境变量生成（未设置的变量取默认值：服务商 `qwen`、Key 为空）；之后在 Web UI 中修改（`POST /api/config/core_api`） |
| `characters.json` | 用户档案（界面中的「我的档案」）与角色设定 | 运行时根目录里还没有这个文件时，服务启动时由 `migrate_config_files()` 按检测到的语言（先 Steam 语言，再系统语言）从 `config/characters/<语言>.json` 复制；系统语言探测不到或不在支持列表里时一律按英文处理。Docker 镜像（`debian:bookworm`，未设置 `LANG`）里没有 Steam，默认会复制英文模板 `en.json`。只有语言探测或复制出错、且项目 `config/` 下也没有 `characters.json` 时，读取时才使用内置默认角色。之后通过 Web UI 的角色管理修改（`/api/characters`） |
| `user_preferences.json` | 各模型的位置、缩放等显示偏好，以及 `__global_conversation__` 条目（全局对话设置、界面语言覆盖 `uiLanguage`） | Web UI（`/api/config/preferences`、`/api/config/conversation-settings`）；`uiLanguage` 只由桌面版托盘经 `PUT /api/config/ui-language` 写入，Web 前端只读取它、不调用这个接口 |
| `voice_storage.json` | 音色克隆、音色设计得到的自定义音色，按 API Key 分组保存（部分服务商用带前缀的 Key 末 8 位或固定分组名，如 `__VLLM_OMNI__`） | Web UI 的音色克隆 / 音色设计（`/api/characters/voice_clone`、`/api/characters/voice_design` 等） |
| `workshop_config.json` | 创意工坊 / 模组目录路径（`default_workshop_folder`、`user_mod_folder` 等） | 不存在时只返回默认值、不写文件；保存设置时写入（`POST /api/steam/workshop/config`） |

## 📋 完整配置项列表

### 1. 核心 API 配置

> **关于"环境变量"列**：这些 `NEKO_*` 变量 Python 代码不读取，只由 Docker 镜像的 `docker/entrypoint.sh` 在数据根下还没有 `core_config.json`（或设置了 `NEKO_FORCE_ENV_UPDATE`）时用来生成这个文件，之后以文件为准。生效条件和坑见「🐳 Docker 部署配置方式 → 方式 1」。

| 配置项 | 配置文件字段 | 环境变量 | 默认值 | 说明 |
|-------|------------|---------|--------|------|
| 核心 API Key | `coreApiKey` | `NEKO_CORE_API_KEY` | `""` | 核心（实时语音）API 的密钥；`coreApi` 为 `free` 时自动使用内置的 `free-access`，不用填 |
| 核心 API 提供商 | `coreApi` | `NEKO_CORE_API` | `"qwen"` | 实时语音模型的提供商。可选：`free`、`qwen`、`qwen_intl`、`openai`、`step`、`gemini`、`glm`、`grok`（`config/api_providers.json` 中 `core_api_providers` 的键） |
| 辅助 API 提供商 | `assistApi` | `NEKO_ASSIST_API` | `"qwen"` | 对话、摘要、纠错、情感、视觉、Agent 等模型默认使用的提供商。可选：`free`、`qwen`、`qwen_intl`、`openai`、`glm`、`step`、`silicon`、`gemini`、`kimi`、`kimi_code`、`deepseek`、`doubao`、`minimax`、`minimax_intl`、`mimo`、`claude`、`grok`、`openrouter`、`orcarouter`、`requesty`、`opper`（`assist_api_providers` 的键，不含只提供 TTS 的 `vllm_omni`） |

- `assistApi` 没写或为空时，`coreApi` 为 `free` 则取 `free`，否则取 `qwen`；填了未知值会回退到 `qwen`。entrypoint 生成的文件总会写 `assistApi`（没设 `NEKO_ASSIST_API` 时写 `"qwen"`），所以用环境变量选免费版时要同时设 `NEKO_ASSIST_API=free`，否则辅助 API 是 `qwen`，需要为它提供阿里云百炼的 Key（`free-access` 不会被拿来回退）。
- 小游戏模型默认分别跟随对话、摘要模型；自定义音色 TTS 没有单独配置、也没有 Qwen（含国际版）Key 时，同样回退到辅助 API。
- Web UI 里，辅助 API 下拉框不列 `vllm_omni`；当 Steam 可用且 Steam 报告的 IP 地区为中国大陆时，核心和辅助 API 下拉框还会隐藏 `api_key_registry` 中标了 `restricted` 的提供商。这些只是界面过滤，直接编辑文件或用环境变量不受影响。

### 2. 各提供商的 API Keys

环境变量的生效方式见第 1 节的说明。只有下表前 7 个字段有对应的环境变量。

| 配置项 | 配置文件字段 | 环境变量 | 默认值 |
|-------|------------|---------|--------|
| 阿里云百炼 API Key | `assistApiKeyQwen` | `NEKO_ASSIST_API_KEY_QWEN` | `""` |
| OpenAI API Key | `assistApiKeyOpenai` | `NEKO_ASSIST_API_KEY_OPENAI` | `""` |
| 智谱 API Key | `assistApiKeyGlm` | `NEKO_ASSIST_API_KEY_GLM` | `""` |
| 阶跃星辰 API Key | `assistApiKeyStep` | `NEKO_ASSIST_API_KEY_STEP` | `""` |
| 硅基流动 API Key | `assistApiKeySilicon` | `NEKO_ASSIST_API_KEY_SILICON` | `""` |
| Grok（xAI）API Key | `assistApiKeyGrok` | `NEKO_ASSIST_API_KEY_GROK` | `""` |
| 豆包（火山方舟）API Key | `assistApiKeyDoubao` | `NEKO_ASSIST_API_KEY_DOUBAO` | `""` |
| 其余提供商的 Key | `assistApiKeyQwenIntl`、`assistApiKeyDeepseek`、`assistApiKeyGemini`、`assistApiKeyKimi`、`assistApiKeyKimiCode`、`assistApiKeyMinimax`、`assistApiKeyMinimaxIntl`、`assistApiKeyMimo`、`assistApiKeyMimoTokenPlan`、`assistApiKeyElevenlabs`、`assistApiKeyClaude`、`assistApiKeyOpenrouter`、`assistApiKeyOrcarouter`、`assistApiKeyRequesty`、`assistApiKeyOpper`、`assistApiKeyDoubaoTts` | -（在 Web UI 里设置或直接编辑文件） | `""` |

- 提供商和字段的对应关系见 `config/api_providers.json` 的 `api_key_registry`；`assistApiKeyMimoTokenPlan` 是 `useMimoTokenPlan` 为 true 时 MiMo 改用的 Key。
- 除 Requesty 外，当前 `coreApi` / `assistApi` 对应的 Key 字段留空时，运行时改用 `coreApiKey`（值为 `free-access` 时不回退）；其他提供商的 Key 字段不回退。MiniMax（含国际版）、MiMo（含 Token Plan）、ElevenLabs、豆包 TTS 这几个字段本身始终不回退，用这些提供商的语音时要单独填写；不过 MiniMax、MiMo 被选为 `assistApi` 而 Key 留空时，辅助模型的请求仍会用 `coreApiKey` 兜底（`free-access` 除外）。
- Requesty 的文本和 Agent 请求只使用专用字段 `assistApiKeyRequesty`，不会借用主服务商的 `coreApiKey`。Docker 仅设置 `NEKO_CORE_API_KEY` 和 `NEKO_ASSIST_API=requesty` 不足以使用 Requesty；请在 Web UI 的密钥簿中填写 Requesty Key，或在持久化的 `core_config.json` 中设置该字段。当前 entrypoint 不支持 `NEKO_ASSIST_API_KEY_REQUESTY`。Requesty 的文本 Key 不会写入 TTS 使用的 `AUDIO_API_KEY`，音频凭据保留原有默认值和核心 Key 回退逻辑。
- `mcpToken`（`NEKO_MCP_TOKEN`）：entrypoint 仍会写入这个字段，配置接口也仍会保存它，但当前代码只是把它转存成 `get_core_config()` 返回值里的 `MCP_ROUTER_API_KEY`，没有任何地方读取，填不填都不影响运行。

### 3. 服务器端口配置

端口常量定义在 `config/network.py`，经 `config/__init__.py` 再导出，模块导入时由 `_read_port_env()` 解析，一般不需要改。取值优先级：`NEKO_<常量名>` > 兼容的裸名 `<常量名>` > 桌面版端口设置写入的 `port_config.json` > 默认值；非整数或不在 1–65535 范围内的值会被跳过。

| 配置项 | 代码常量 | 环境变量 | 默认值 | 说明 |
|-------|---------|---------|--------|------|
| 主服务器端口 | `MAIN_SERVER_PORT` | `NEKO_MAIN_SERVER_PORT` | `48911` | 主服务（Web UI 与 API） |
| 记忆服务器端口 | `MEMORY_SERVER_PORT` | `NEKO_MEMORY_SERVER_PORT` | `48912` | 记忆服务 |
| 监控服务器端口 | `MONITOR_SERVER_PORT` | `NEKO_MONITOR_SERVER_PORT` | `48913` | `app/monitor.py` 的同步服务，主服务通过 WebSocket 连它的 `/sync/<角色名>` 推送同步消息；Docker 入口不启动它 |
| 评论服务器端口 | `COMMENTER_SERVER_PORT` | `NEKO_COMMENTER_SERVER_PORT` | `48914` | 弹幕同步通道的目标端口。主服务启动同步连接时关掉了这条通道，仓库里也没有监听这个端口的服务，目前没用到 |
| 工具服务器端口 | `TOOL_SERVER_PORT` | `NEKO_TOOL_SERVER_PORT` | `48915` | Agent 服务 |
| 用户插件服务器端口 | `USER_PLUGIN_SERVER_PORT` | `NEKO_USER_PLUGIN_SERVER_PORT` | `48916` | 用户插件服务，跑在 Agent 服务进程里 |

**可选 Monitor 服务的监听与认证**（定义在 `config/network.py`，读取方式同上：`NEKO_<常量名>` 优先，兼容裸名）：

| 配置项 | 代码常量 | 环境变量 | 默认值 | 说明 |
|-------|---------|---------|--------|------|
| Monitor 监听地址 | `MONITOR_HOST` | `NEKO_MONITOR_HOST` | `0.0.0.0` | IPv6 带不带方括号均可；主服务直接连这个地址，通配地址换成同协议族的回环地址 |
| Monitor 完整权限 token | `MONITOR_TOKEN` | `NEKO_MONITOR_TOKEN` | 空 | 为空时不认证（兼容旧行为）；设置后除静态资源外所有路由都要认证，包括主服务写入的 `/sync*`，主服务会自动携带 |
| Monitor 只读 token | `MONITOR_VIEWER_TOKEN` | `NEKO_MONITOR_VIEWER_TOKEN` | 空 | 仅在设置了 `NEKO_MONITOR_TOKEN` 时生效；只能看 viewer，不能写 `/sync*`，分享 viewer 链接请用它 |

浏览器首次访问、cookie 会话和反向代理的细节见 `docs/zh-CN/config/environment-vars.md`。

> **Docker 中**：`docker/entrypoint.sh` 只启动记忆、主服务、Agent 三个进程，对外由 Nginx 提供访问。容器内 `NGINX_PORT` 默认 80，`NGINX_SSL_PORT` 默认 443；官方 compose 把宿主机 48911 映射到 80、48912 映射到 443。Nginx 反代配置里只有主服务的上游端口跟随 `NEKO_MAIN_SERVER_PORT`，记忆（48912）、Agent（48915）、插件（48916）的上游端口都写死了，所以在容器里不要改这三个端口。

> **插件安全与 NAS 兼容**：官方 HTTP/HTTPS 代理同时转发插件操作与 `/security/csrf-token`，浏览器自动获取和附加校验 token。**不用官方 Docker、自己写 Nginx/Caddy 规则时，必须把 `/security/csrf-token` 转发到插件服务（48916）**：它不在原有插件代理前缀（`plugins?|plugin/|plugin-cli/|…`）之下，落到主服务会导致插件启停、安装等需要 token 的操作失败，插件管理器会提示无法获取安全令牌。通过 NAS IP 和宿主机映射端口访问不需要额外配置 Origin/token。外层 HTTPS 反代转容器 HTTP 时，非 loopback NAS 地址允许 hostname 兜底，不比较协议与端口；这也意味着同一 NAS hostname 的其他应用端口进入来源信任边界。自定义域名沿用 `NEKO_TRUSTED_HOSTS`；公网访问认证与网络隔离仍由部署层负责。此校验防跨站操作，不提供用户登录认证。Vite `5173` 仅在开发者显式设置 `NEKO_PLUGIN_MUTATION_ALLOWED_ORIGINS` 后允许，不应加入普通 NAS 部署配置。插件页面直接调用的路由（`/runs`、`ui-api`、插件配置等）默认只校验来源、不强制 token，以保障市场插件继续可用；公网部署可设置 `NEKO_PLUGIN_PAGE_MUTATION_REQUIRE_TOKEN=1` 强制 token，代价是尚未适配的插件页面会失效。完整合同见 [`local-mutation-auth.md`](../docs/design/security/local-mutation-auth.md)。

### 4. 模型配置

各用途用哪个模型，由当前选中提供商的 profile 决定：`coreApi` 决定实时语音模型（`get_core_config()` 快照里的 `CORE_MODEL`），`assistApi` 决定对话、摘要、纠错、情感、视觉和 Agent 模型（`CONVERSATION_MODEL`、`SUMMARY_MODEL`、`CORRECTION_MODEL`、`EMOTION_MODEL`、`VISION_MODEL`、`AGENT_MODEL`）。各提供商的 profile 来自 `config/api_providers.json`，来源和回退规则见第 5 节。

- `config/model_defaults.py` 的 `DEFAULT_*` 常量是快照的初始值，会被选中提供商 profile 里的同名项覆盖；内置 profile 都带有上面这些模型项。`assistApi` 填了未知值时会先回退到 `qwen` 再覆盖，`coreApi` 没有这层回退：它不是 `core_api_providers` 的键时（例如拼错），`CORE_URL` / `CORE_MODEL` 会停在默认值（`wss://dashscope.aliyuncs.com/api-ws/v1/realtime`、`qwen3-omni-flash-realtime`）。
- 没有能单独指定某个模型的环境变量。要给某个用途换模型或提供商，用第 7 节的自定义模型配置。

> 历史上的 `ROUTER_MODEL` / `SEMANTIC_MODEL` / `RERANKER_MODEL` /
> `SETTING_PROPOSER_MODEL` / `SETTING_VERIFIER_MODEL` 已于 2026-04 全部退环境
> （见 `config/model_defaults.py` 中「模型配置常量」处的注释）。memory 子系统的 LLM 调用按 tier
> （`summary` / `correction` / `emotion`）走 `config_manager.get_model_api_config(<tier>)`，
> 嵌入服务走本地 ONNX（`memory/embeddings.py` 的 `EmbeddingService`）。

### 5. API 提供商详细配置

提供商列表，以及各提供商的默认地址和默认模型，以程序目录下的 `config/api_providers.json` 为准（由 `utils/api_config_loader.py` 读取）。Docker 镜像里它是 `/app/config/api_providers.json`，属于镜像内容，不在持久化卷中：在容器里直接改，重建容器后会丢失；放进用户配置目录（`core_config.json` 所在目录）也不会被读取。

json 里的字段名是小写（如 `openrouter_url`、`agent_model`），对应下文的大写键（`OPENROUTER_URL`、`AGENT_MODEL`）。`config/api_profiles.py` 的 `DEFAULT_CORE_API_PROFILES` / `DEFAULT_ASSIST_API_PROFILES` 是内置底值。核心 profile 只在 json 读不到（或没有 `core_api_providers`）时整体改用；辅助 profile 则总是以内置值为底、再用 json 的同名字段覆盖，json 里缺的提供商也会补进来。当前 json 为每个提供商写全了这些字段，所以实际生效的地址和模型名以 json 为准；内置值里的模型名与 json 并不同步，下表以 json 为准。

#### 核心 API 提供商

`coreApi` 的取值即下表的提供商键。

| 提供商 | URL | 模型 | 说明 |
|-------|-----|------|------|
| free | wss://www.lanlan.tech/core | free-model | 免费版，无需填 Key（profile 自带 `free-access`）；判定为非中国大陆网络时改走 `www.lanlan.app` |
| qwen | wss://dashscope.aliyuncs.com/api-ws/v1/realtime | qwen3.8-omni-flash-realtime | 阿里云百炼（国内） |
| qwen_intl | wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime | qwen3.8-omni-flash-realtime | 阿里云国际版 |
| openai | wss://api.openai.com/v1/realtime | gpt-realtime-2.1 | OpenAI |
| step | wss://api.stepfun.com/v1/realtime | stepaudio-3-realtime-preview | 阶跃星辰 |
| gemini | -（无 `core_url`） | gemini-3.8-live | Google，经 google-genai SDK 连接 |
| glm | wss://open.bigmodel.cn/api/paas/v4/realtime | glm-realtime-plus | 智谱 |
| grok | wss://api.x.ai/v1/realtime | grok-voice-latest | xAI |

#### 辅助 API 提供商

`assistApi` 的取值为 `assist_api_providers` 的键：free、qwen、qwen_intl、openai、glm、step、silicon、gemini、kimi、kimi_code、deepseek、doubao、minimax、minimax_intl、mimo、claude、grok、openrouter、orcarouter、requesty、opper。另有 `vllm_omni` 只供 TTS 使用，不出现在辅助 API 下拉框里，它的模型槽位全部为空。

每个提供商有一个接口地址 `OPENROUTER_URL` 和 6 个模型槽位：
- `CONVERSATION_MODEL` - 文本对话模型
- `SUMMARY_MODEL` - 摘要模型
- `CORRECTION_MODEL` - 纠错模型
- `EMOTION_MODEL` - 情感模型
- `VISION_MODEL` - 视觉模型
- `AGENT_MODEL` - Agent 模型（键鼠控制、浏览器控制等，见第 6 节）

补充说明：
- `claude`、`kimi_code` 带 `provider_type: "anthropic"`，按 Anthropic Messages 协议调用。
- `qwen_intl` 另有候选地址 `https://dashscope-us.aliyuncs.com/compatible-mode/v1`。在 Web UI 保存 API 设置时会测试候选地址，测通的记入 `core_config.json` 的 `resolvedProviderUrls`，运行时优先使用。
- `mimo` 被选为 `assistApi` 且 `useMimoTokenPlan` 为 `true` 时改用 Token Plan 地址（默认 `https://token-plan-cn.xiaomimimo.com/v1`；`resolvedProviderUrls` 里记有 sgp / ams 候选时用记下的那个），Key 取 `assistApiKeyMimoTokenPlan`。
- `free` 的辅助地址 `https://www.lanlan.tech/text/v1` 同样按网络区域改写，Agent 槽位除外（见第 6 节）。

### 6. Computer Use（键鼠控制）配置

键鼠控制由 `brain/computer_use.py` 的 `ComputerUseAdapter` 实现：每一步把屏幕截图交给一次多模态模型调用，由模型直接给出思考、动作和要执行的 pyautogui 代码，元素定位也由这个模型完成，不再区分"规划模型"和"定位模型"。它和浏览器控制（`brain/browser_use_adapter.py`）共用 **Agent 模型**槽位，都通过 `get_model_api_config("agent")` 取模型、URL 和 API Key，所用模型需要能接收图片输入。

> 旧版文档里的 `computerUseModel` / `computerUseModelUrl` / `computerUseModelApiKey` / `computerUseGroundModel` / `computerUseGroundUrl` / `computerUseGroundApiKey` 已没有任何代码读取，写进 `core_config.json` 不会生效。

功能开关不在 `core_config.json` 里：Agent 总开关 `analyzer_enabled` 和键鼠控制子开关 `computer_use_enabled` 在界面上切换，最后一次的选择存在与 `core_config.json` 同一目录的 `agent_runtime_intent.json`，重启后自动恢复；设置环境变量 `NEKO_DISABLE_AGENT_AUTO_RESTORE=1`（Agent 服务直接读 `os.environ`）可跳过启动后的自动恢复。

#### 默认配置（跟随 `assistApi`）

未开启自定义 API 时，Agent 模型取 `assistApi` 对应提供商的 `AGENT_MODEL`（为空时退回该提供商的 `VISION_MODEL`），URL 取该提供商的 `OPENROUTER_URL`，API Key 取该提供商的辅助 Key（`assistApiKey*`，未填时通常回退到 `coreApiKey`，但 Requesty 必须填写专用 `assistApiKeyRequesty`；`free` 用自带的 `free-access`）。各提供商的默认值（`config/api_providers.json`）：

| 提供商 | Agent 模型 | API URL |
|-------|-----------|---------|
| free | free-agent-model | https://www.lanlan.tech/text/v1（不做海外域名改写） |
| qwen | qwen3.8-flash | https://dashscope.aliyuncs.com/compatible-mode/v1 |
| qwen_intl | qwen3.8-flash | https://dashscope-intl.aliyuncs.com/compatible-mode/v1 |
| openai | gpt-5.6-terra | https://api.openai.com/v1 |
| glm | glm-5v-turbo | https://open.bigmodel.cn/api/paas/v4 |
| step | step-5-preview | https://api.stepfun.com/v1 |
| silicon | Qwen/Qwen3.5-122B-A10B | https://api.siliconflow.cn/v1 |
| gemini | gemini-3.5-flash | https://generativelanguage.googleapis.com/v1beta/openai/ |
| kimi | kimi-k2.6 | https://api.moonshot.cn/v1 |
| kimi_code | kimi-for-coding | https://api.kimi.com/coding |
| deepseek | deepseek-flash | https://api.deepseek.com/v1 |
| doubao | doubao-seed-2-0-lite-260428 | https://ark.cn-beijing.volces.com/api/v3 |
| minimax | MiniMax-M3 | https://api.minimaxi.com/v1 |
| minimax_intl | MiniMax-M3 | https://api.minimax.io/v1 |
| mimo | mimo-v2.5 | https://api.xiaomimimo.com/v1 |
| claude | claude-sonnet-5 | https://api.anthropic.com/v1 |
| grok | grok-4.3 | https://api.x.ai/v1 |
| openrouter | google/gemini-3-flash-preview | https://openrouter.ai/api/v1 |
| orcarouter | anthropic/claude-sonnet-5 | https://api.orcarouter.ai/v1 |
| requesty | google/gemini-3-flash-preview | https://router.requesty.ai/v1 |
| opper | gemini-3.8-flash | https://api.opper.ai/v3/compat |

#### 自定义 Agent 模型

在 `core_config.json` 中把 `enableCustomApi` 设为 `true` 后，下列字段才生效（为 `false` 时一律忽略）：

| 配置文件字段 | 说明 |
|------------|------|
| `agentModelProvider` | `follow_assist` / `follow_core`（地址和 Key 跟随辅助 / 核心 API 的提供商，模型名不跟随）、某个辅助提供商键，或 `custom` |
| `agentModelUrl` | Agent 模型的 API 地址，留空沿用默认地址；`follow_*` 时忽略 |
| `agentModelId` | Agent 模型名，留空沿用默认模型（`assistApi` 提供商的 Agent 模型）。选 `follow_core` 时模型名不会跟着换，需在这里填核心提供商对应的模型；`follow_assist` 且 `assistApi` 为 `free` 时本字段被忽略 |
| `agentModelApiKey` | 按下面的规则参与取 Key |

Agent API Key 的取法：
- `follow_assist`：与默认配置相同，取辅助 API 的 Key；`follow_core`：取核心 API 的 Key。两者都忽略 `agentModelApiKey`。
- 具体提供商：依次取该提供商的 `assistApiKey*`、`agentModelApiKey`；两者都为空、且该提供商正是当前的 `coreApi` 或 `assistApi` 时，再回退到 `coreApiKey`（`minimax`、`minimax_intl`、`mimo`、`requesty` 不回退）。前提是 Agent 地址（`agentModelUrl`，留空即默认地址）与该提供商的 `openrouter_url` 或其候选地址一致，否则只用 `agentModelApiKey`。
- `custom`，或没有设置 `agentModelProvider`：只用 `agentModelApiKey`，为空串也照样使用。

#### 配置示例

辅助 API 用 qwen，Agent 单独改用智谱：

```json
{
  "assistApi": "qwen",
  "assistApiKeyQwen": "your-qwen-api-key",
  "assistApiKeyGlm": "your-glm-api-key",
  "enableCustomApi": true,
  "agentModelProvider": "glm",
  "agentModelUrl": "https://open.bigmodel.cn/api/paas/v4",
  "agentModelId": "glm-5v-turbo"
}
```

> **注意**：
> - `enableCustomApi` 是各模型槽位自定义字段的总开关（GPT-SoVITS 和豆包语音除外，见第 7 节），打开后其它槽位里已保存的自定义值也会一起生效。
> - 手工打开 `enableCustomApi` 但不想改 Agent 时，请把 `agentModelProvider` 设为 `follow_assist`。不设置时，`agentModelApiKey`（默认是空串）会把默认的 Agent Key 覆盖掉，Agent 可用性检查会报「Agent API Key 未配置或不可用」。
> - 选具体提供商时地址不会自动切换：`agentModelUrl` 留空时请求仍发往默认地址（`assistApi` 提供商的地址）。所选提供商与 `assistApi` 不同时，要把 `agentModelUrl` 填成所选提供商的地址，请求才会发往它，也才会取用它的 `assistApiKey*`。

> **Docker 部署**：镜像（`docker/Dockerfile`、`docker/Dockerfile.full`）和 `docker/entrypoint.sh` 都不启动 X 显示服务，也不设置 `DISPLAY`，因此默认 Docker 部署下 pyautogui 无法导入，键鼠控制不可用（可用性检查返回未就绪，原因码 `AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE`）。

### 7. 自定义模型配置（高级）

在 Web UI 的 API 设置页（`/api_key`）勾选「启用自定义API配置」后，可以给每个用途单独指定提供商、地址、模型和 Key。这些设置保存在 `core_config.json` 中：总开关是 `enableCustomApi`（缺省为 `false`），每个用途（槽位）各有 4 个字段：`<前缀>ModelProvider`、`<前缀>ModelUrl`、`<前缀>ModelId`、`<前缀>ModelApiKey`。以摘要为例，就是 `summaryModelProvider`、`summaryModelUrl`、`summaryModelId`、`summaryModelApiKey`。

> ⚠️ 下表「快照键」一列（如 `SUMMARY_MODEL`、`SUMMARY_MODEL_URL`、`SUMMARY_MODEL_API_KEY`）只是 `get_core_config()` 返回的内存字典里的键。它们既不是环境变量，也不是 `core_config.json` 的字段，写进环境变量或 JSON 都不会生效。

| 用途 | 字段前缀 | 快照键（另有对应的 `_URL` / `_API_KEY`） | `get_model_api_config()` 的 `model_type` | 自定义未生效时回退到 |
|------|---------|---------|---------|---------|
| 对话 | `conversation` | `CONVERSATION_MODEL` | `conversation` | 辅助 API |
| 摘要 | `summary` | `SUMMARY_MODEL` | `summary` | 辅助 API |
| 游戏主模型 | `gameMain` | `GAME_MAIN_MODEL` | `game_main` | 对话槽 |
| 游戏摘要 | `gameSummary` | `GAME_SUMMARY_MODEL` | `game_summary` | 摘要槽 |
| 纠错 | `correction` | `CORRECTION_MODEL` | `correction` | 辅助 API |
| 情感 | `emotion` | `EMOTION_MODEL` | `emotion` | 辅助 API |
| 视觉 | `vision` | `VISION_MODEL` | `vision` | 辅助 API |
| Agent | `agent` | `AGENT_MODEL` | `agent` | 辅助 API |
| 实时语音 | `omni` | `REALTIME_MODEL` | `realtime` | 核心 API |
| TTS | `tts` | `TTS_MODEL` | `tts_default` / `tts_custom` | 核心 API / 辅助 API |
| 图像生成 | `image` | `IMAGE_GENERATION_CONFIG`（单个字典，无 `_URL` / `_API_KEY`） | `image` | 不回退，视为关闭 |

生效规则（`utils/config_manager/core_config.py`）：

- 只有 `enableCustomApi` 为 `true` 时，`get_core_config()` 才用这些字段覆盖快照。TTS 有两个不看总开关的例外：
  - GPT-SoVITS：`ttsModelProvider` 为 `gptsovits`，或 `ttsModelProvider` 为空 / `follow_assist` / `follow_core` 且老字段 `gptsovitsEnabled` 为 `true` 时，TTS 地址直接取 `ttsModelUrl`（留空为 `http://127.0.0.1:9881`），`get_model_api_config('tts_custom')` 也按自定义配置返回。
  - 豆包语音：`ttsModelProvider`（或 `ttsProvider`）为 `doubao_tts` 时，TTS 派发（`main_logic/tts_client/workers/doubao.py`）不经过上面的快照覆盖，直接从 `core_config.json` 读 `ttsModelUrl`（服务地址，留空为 `https://openspeech.bytedance.com`）、`ttsModelId`（资源 ID，留空为 `seed-icl-2.0`）和 `ttsVoiceId`（音色，留空时用角色自己的音色 ID）。Key 在 `ttsModelProvider` 为 `doubao_tts` 时取 `ttsModelApiKey`，为空再取 `assistApiKeyDoubaoTts`。如果角色用的是其他服务商的克隆音色，或者 `assistApi` 为 `mimo`，就会先走那一路。
- 开启 `enableCustomApi` 后，如果某个槽的模型和 URL 都非空，`get_model_api_config(model_type)` 就返回这套配置，并标记 `is_custom: true`；否则（包括未开启时）按上表回退。「辅助 API」指辅助提供商的地址与 Key（`OPENROUTER_URL` / `OPENROUTER_API_KEY`），加上该用途的模型；「核心 API」指 `CORE_URL` / `CORE_API_KEY` / `CORE_MODEL`。`tts_custom` 回退时，会先尝试已保存的 Qwen（或 Qwen 国际版）Key；它回退后返回的 `model` 都是 `CORE_MODEL` 占位值。
- Agent 槽是例外：没开 `enableCustomApi` 时，它也直接使用快照里的 `AGENT_MODEL` / `AGENT_MODEL_URL` / `AGENT_MODEL_API_KEY`。这几项默认取自辅助 API，此时 `is_custom` 为 `false`。
- `<前缀>ModelProvider` 的取值：
  - `follow_core` / `follow_assist`：跟随核心 / 辅助 API 提供商的地址与 Key。实时语音和 TTS 两个槽例外：选 `follow_*` 时不写入地址，槽位保持未配置，直接按上表回退（`tts_default` 走核心 API，`tts_custom` 先试已保存的 Qwen Key、再走辅助 API），与选的是 `follow_core` 还是 `follow_assist` 无关（启用 GPT-SoVITS 时除外）；
  - `follow_conversation` / `follow_summary`：照搬对话 / 摘要槽，是两个游戏槽的默认值；
  - `custom`：使用本槽填写的地址、模型和 Key。地址或模型 ID 留空时不会置空，而是沿用快照里原有的值：模型名照旧（对话、摘要等用途来自辅助 profile），地址除 Agent 槽外原本为空，于是整个槽回退。所以两项都要填；
  - 具名提供商（如 `qwen`、`openai`）：本槽 URL 属于该提供商时，优先用 API 管理簿里该提供商的 Key（`assistApiKey*`），否则用本槽的 `<前缀>ModelApiKey`。TTS 槽始终用本槽的 Key。
- 实时语音槽只能跟随核心 API，或选 `custom` 自填端点。选具名提供商或 `follow_assist`，都会按 `follow_core` 处理。
- 图像生成槽同样要求 `enableCustomApi` 为 `true`，且 `imageModelProvider` 不能为空，也不能是 `disabled`（可选 `openai`、`qwen`、`qwen_intl`、`custom`）。具名提供商用 API 管理簿里该提供商的 Key，`custom` 用 `imageModelApiKey`；不会借用对话模型或其他槽的配置。

示例（摘要改用自己的 OpenAI 兼容端点；手工打开 `enableCustomApi` 时要同时写上 `agentModelProvider`，原因见第 6 节的注意事项）：

```json
{
  "enableCustomApi": true,
  "summaryModelProvider": "custom",
  "summaryModelUrl": "https://api.example.com/v1",
  "summaryModelId": "your-model-id",
  "summaryModelApiKey": "your-api-key",
  "agentModelProvider": "follow_assist"
}
```

## 🔄 配置优先级

模型与 API 相关的配置（`get_core_config()` 的结果）按以下顺序组装，后面的步骤可以覆盖前面写入的值：

1. **代码默认值**：`config/model_defaults.py` 的 `DEFAULT_*` 常量（快照初始值），以及 `config/api_profiles.py` 的 `DEFAULT_CORE_CONFIG`（`core_config.json` 的字段模板）。
2. **`core_config.json`**：选定提供商（`coreApi` / `assistApi`），并提供各个 Key。读取的是运行时数据根下的 `config/core_config.json`（Docker 默认为 `/home/neko/.local/share/N.E.K.O/config/core_config.json`）；这个文件不存在时，才改读项目目录 `config/` 下的同名文件。
3. **提供商 profile**：按第 2 步选定的提供商，从 `config/api_providers.json` 取出（回退规则见第 5 节），覆盖第 1 步的地址和模型；`free` 的 profile 还会用占位值 `free-access` 覆盖 Key。这个文件随代码发布（Docker 镜像内是 `/app/config/api_providers.json`），读取后缓存在进程内（`utils/api_config_loader.py` 的 `get_config`）。主服务在响应 `GET /api/config/api_providers`（打开 API 设置页等页面时会请求）时会强制重读；记忆、Agent 服务进程不会重读。
4. **自定义模型配置**：`enableCustomApi` 为 `true` 时，`core_config.json` 里各槽的 `<前缀>Model*` 字段覆盖对应用途的模型、地址和 Key（见第 7 节）。

**关于环境变量**：没有「环境变量覆盖一切」的机制。第 1、2 节表里的 `NEKO_*` API 变量只用于由 entrypoint 生成初始的 `core_config.json`（见「🐳 Docker 部署配置方式 → 方式 1」）。另有少数模块直接用 `os.getenv` 读取自己的环境变量，例如：

- 端口：`config/network.py` 的 `_read_port_env`，优先级为 `NEKO_<名称>` > `<名称>` > Electron 写入的 `port_config.json` > 默认值；
- 数据根位置：`utils/config_manager/storage_roots.py` 读取 `NEKO_STORAGE_SELECTED_ROOT` / `NEKO_STORAGE_ANCHOR_ROOT`，Linux 上还会参考 `XDG_DATA_HOME`；
- 语音识别：`main_logic/asr_client/__init__.py` 的 Soniox Key 读 `SONIOX_API_KEY`，区域读 `ASR_USER_REGION` / `SONIOX_REGION`。

## 📝 配置加载流程

`get_core_config()` 定义在 `utils/config_manager/core_config.py` 的 `CoreConfigMixin` 中，通过 `get_config_manager()` 返回的单例调用。它每次被调用都会重新读取 `core_config.json`（不缓存），按以下顺序组装一份快照字典：

1. 用 `config/model_defaults.py` 的 `DEFAULT_*` 常量，初始化 `CORE_URL`、`CORE_MODEL`、`SUMMARY_MODEL` 等大写键。
2. 以 `DEFAULT_CORE_CONFIG` 为模板，叠加 `core_config.json` 的内容；文件缺失、无法解析或内容不是 JSON 对象时，只用模板。
3. 填入 Key：`coreApiKey` → `CORE_API_KEY`，`assistApiKey*` → `ASSIST_API_KEY_*`，`mcpToken` → `MCP_ROUTER_API_KEY`。某个提供商没有单独保存 Key、而它正是当前的 `coreApi` 或 `assistApi` 时，改用 `coreApiKey`。`coreApiKey` 是免费线路的占位值 `free-access` 时不做这种回退；MiniMax、MiMo、ElevenLabs、豆包语音、Requesty 的 Key 也从不回退。
4. 按 `coreApi` 取核心 profile，按 `assistApi` 取辅助 profile，用 `config.update()` 覆盖前面的地址和模型。`free` 的 profile 还带占位 Key `free-access`：核心为 `free` 时覆盖 `CORE_API_KEY`，辅助为 `free` 时覆盖 `AUDIO_API_KEY` / `OPENROUTER_API_KEY`。文件里没写 `assistApi` 时，`coreApi` 为 `free` 则取 `free`，否则取 `qwen`；未知的 `assistApi` 回退到 `qwen`。`resolvedProviderUrls` 里如果存有连通性测试选定的地址，并且它属于该提供商的候选地址，就用它替换默认地址。
5. 辅助提供商的 Key 写入 `OPENROUTER_API_KEY` / `AUDIO_API_KEY`；通常仍为空时改用 `coreApiKey`（`free-access` 除外）。Requesty 的 `OPENROUTER_API_KEY` 必须使用专用 Key，空值保持为空；Requesty 的文本 Key 不写入 `AUDIO_API_KEY`，音频凭据保留原有默认值和核心 Key 回退。Agent 的模型、地址和 Key 默认跟随辅助 API。
6. `enableCustomApi` 为 `true` 时，应用第 7 节的自定义模型字段（GPT-SoVITS 的 TTS 地址不受这个开关限制）。
7. 改写免费线路地址：只处理域名为 `lanlan.tech` 或其子域（如 `www.lanlan.tech`）的 `*_URL`。直播模式生效时（`config/livestream_config.json` 里，或没有这个文件时 `api_providers.json` 的 `livestream_config` 里，`enabled` 为 `true` 且 `server_prefix` 非空），路径为 `/core`、`/text/v1`、`/tts` 的地址先改用 `server_prefix` 开头的地址；否则在判定为非中国大陆网络时，把域名中的 `lanlan.tech` 换成 `lanlan.app`。`AGENT_MODEL_URL` 不做区域改写。

各模块再用 `get_model_api_config(model_type)`，从这份快照里取出某个用途最终生效的 `model` / `api_key` / `base_url` / `is_custom`，规则见第 7 节。

## 🐳 Docker 部署配置方式

容器内的服务以 `neko` 用户运行，运行时配置保存在数据根的 `config/` 目录。使用仓库自带的 `docker/docker-compose.yml` 时，数据根是 `/home/neko/.local/share/N.E.K.O`（由 `NEKO_STORAGE_SELECTED_ROOT` 指定）。它随 `./neko-home:/home/neko` 挂载持久化，在宿主机上对应 `./neko-home/.local/share/N.E.K.O/`。

从旧版 `./N.E.K.O` + `./ssl` 双挂载升级的，先按 `README.MD`「从旧版本升级（挂载目录已变更）」迁移数据；否则容器会对着空的数据根启动，`core_config.json` 也会按环境变量重新生成。`docker/entrypoint.sh` 的 `detect_legacy_layout` / `warn_legacy_layout` 发现旧布局痕迹时会在启动日志里提示。

### 方式 1：环境变量（只用于生成初始的 core_config.json）

`docker/entrypoint.sh` 的 `setup_configuration` 只在两种情况下用下列变量写出 `core_config.json`：数据根下还没有这个文件，或者设置了非空的 `NEKO_FORCE_ENV_UPDATE`。其余情况下（包括正常重启）不会再用它们写入或修改 `core_config.json`。

| 环境变量 | 写入的字段 | 未设置时 |
|---------|-----------|---------|
| `NEKO_CORE_API_KEY` | `coreApiKey` | `""` |
| `NEKO_CORE_API` | `coreApi` | `"qwen"` |
| `NEKO_ASSIST_API` | `assistApi` | `"qwen"` |
| `NEKO_ASSIST_API_KEY_QWEN` / `_OPENAI` / `_GLM` / `_STEP` / `_SILICON` / `_GROK` / `_DOUBAO` | `assistApiKeyQwen` / `assistApiKeyOpenai` / `assistApiKeyGlm` / `assistApiKeyStep` / `assistApiKeySilicon` / `assistApiKeyGrok` / `assistApiKeyDoubao` | `""` |
| `NEKO_MCP_TOKEN` | `mcpToken` | `""` |

- `NEKO_FORCE_ENV_UPDATE` 取任何非空值（包括 `0`、`false`）都会**整份重写** `core_config.json`。重写后文件里只剩上表这些字段，在 Web UI 里保存过的其他设置（自定义模型、TTS 等）都会丢失。这个变量只要还在，每次启动都会重写，用完要删掉；使用前请先备份。
- 仓库自带的 `docker-compose.yml` 没有 `env_file:`，`environment:` 只透传以下变量：`TZ`、实例访问相关的 `NEKO_INSTANCE_ACCESS_KEY` / `NEKO_INSTANCE_PUBLIC_ORIGIN` / `NEKO_REQUIRE_HTTPS` / `NEKO_COMMUNITY_WEB_CLIENT_ID` / `NEKO_COMMUNITY_WEB_REDIRECT_URI`、自有域名相关的 `SSL_DOMAIN` / `NEKO_TRUSTED_HOSTS` / `NEKO_TRUSTED_ORIGINS`（留空时：`SSL_DOMAIN` 使用入口脚本默认值，`NEKO_TRUSTED_HOSTS` 回退到 `SSL_DOMAIN`，`NEKO_TRUSTED_ORIGINS` 保持为空），以及固定的 `XDG_DATA_HOME`、`NEKO_STORAGE_SELECTED_ROOT`、`NEKO_STORAGE_ANCHOR_ROOT`。上表中的 API 变量不在其中：只在 `docker/.env` 里写它们不会进入容器（`.env` 只用于 compose 文件里的 `${...}` 替换）。需要在 `neko-main` 服务已有的 `environment:` 列表里追加，例如：

  ```yaml
  - NEKO_CORE_API_KEY=${NEKO_CORE_API_KEY}
  - NEKO_CORE_API=${NEKO_CORE_API:-qwen}
  - NEKO_ASSIST_API=${NEKO_ASSIST_API:-qwen}
  ```

  用 `docker run` 时，改用 `-e NEKO_CORE_API_KEY=...` 传入。
- 启动日志里的 `Core API:` / `Assist API:` 每次启动都会打印，打印的是环境变量的值（未设置时为 `qwen`），不一定是文件里实际生效的值。

### 方式 2：Web UI（日常修改）

在 API 设置页（`/api_key`）保存时，`POST /api/config/core_api` 会先读出现有的 `core_config.json`，合并本次提交的字段，再写回数据根的 `config/core_config.json`。

### 方式 3：直接编辑持久化的配置文件

编辑宿主机上的 `./neko-home/.local/share/N.E.K.O/config/core_config.json`（字段见第 1、2、7 节），然后重启容器。

不要再挂载 `./config:/app/config`，原因有两点：

- `/app/config` 是镜像里 Python 包 `config` 的目录，包含 `__init__.py`、`model_defaults.py`、`api_profiles.py`、`api_providers.json` 等全部代码默认值。用宿主目录（例如只有一个 `core_config.json.example` 的 `docker/config/`）挂载会把它整个遮住，服务在导入 `config` 时就会失败。
- entrypoint 会在服务启动前确保数据根下已有 `core_config.json`，所以服务读的是那一份，不是 `/app/config` 下的。

旧版本留下的 `/app/config/core_config.json`，只会在数据根还没有 `core_config.json`、且未设置 `NEKO_FORCE_ENV_UPDATE` 时，由 entrypoint 的 `migrate_legacy_bootstrap_config` 复制过去一次。

## 🔍 查看当前配置

### 在 Docker 容器中

下面命令里的容器名 `neko`，来自 `docker-compose.yml` 的 `container_name`。

```bash
# 查看持久化的 core_config.json（宿主机上是 ./neko-home/.local/share/N.E.K.O/config/core_config.json）
docker exec neko cat /home/neko/.local/share/N.E.K.O/config/core_config.json

# 查看容器的环境变量（其中 NEKO_CORE_API* / NEKO_ASSIST_API* / NEKO_MCP_TOKEN 只是生成初始配置的输入，不代表当前生效的值）
docker exec neko env | grep NEKO_

# 查看 get_core_config() 组装出的运行时快照（⚠️ 输出包含明文 API Key）
docker exec -u neko neko /app/.venv/bin/python -c "
from utils.config_manager import get_config_manager
import json
print(json.dumps(get_config_manager().get_core_config(), indent=2, ensure_ascii=False))
"
```

- 用 `/app/.venv/bin/python`：依赖由 `uv sync` 装在 `/app/.venv`，entrypoint 启动服务用的也是这个解释器。
- 加 `-u neko`：镜像没有设置 `USER`，`docker exec` 默认以 root 运行。而 `get_config_manager()` 默认会执行迁移，可能往数据目录写文件；以 root 身份运行，会留下 `neko` 无法修改的文件。
- 把 `get_core_config()` 换成 `get_model_api_config('summary')` 这类调用，可以查看某个用途最终生效的 `model` / `base_url` / `api_key` / `is_custom`。
- 这条命令是在一个新起的 Python 进程里重新组装一份快照，不是正在运行的服务内存里的那份。免费线路的区域判定由后台探测线程给出，这个一次性进程读快照时通常还没有结论，会按大陆线路组装。所以在海外网络下，这里显示的是 `lanlan.tech` 地址，而服务实际用的是 `lanlan.app`。

### 在开发环境中

在仓库根目录用 `uv run python` 执行。它读的是本机真实的数据根，`get_config_manager()` 同样会执行迁移；输出包含明文 API Key。

```python
from utils.config_manager import get_config_manager
config = get_config_manager().get_core_config()
print(config)
```

## 📚 相关文件索引

- `config/__init__.py` - 配置包的兼容门面，从 `config/` 下各领域模块再导出名字
- `config/network.py` - 端口常量（`MAIN_SERVER_PORT` 等）及其环境变量覆盖（`_read_port_env`）
- `config/model_defaults.py` - 模型名、URL、Key 的默认值（`DEFAULT_*`）
- `config/api_profiles.py` - `DEFAULT_CORE_CONFIG`（core_config.json 默认结构）、`DEFAULT_CORE_API_PROFILES` / `DEFAULT_ASSIST_API_PROFILES`（服务商内置配置）、`DEFAULT_CONFIG_DATA`（按文件名汇总 `characters.json`、`core_config.json`、`user_preferences.json`、`voice_storage.json` 的内置默认内容；`get_core_config()` 以其中的 `core_config.json` 为模板，`voice_storage.json` 缺失时 `load_voice_storage()` 返回其中的默认值，`characters.json` 缺失时实际使用的是 `get_localized_default_characters()` 按语言本地化的副本）
- `config/character_defaults.py` - `CONFIG_FILES`（用户配置文件清单）、`DEFAULT_CHARACTERS_CONFIG`（默认角色）
- `config/characters/` - 生成 `characters.json` 时按语言选用的角色模板
- `config/api_providers.json` - 服务商目录（URL、模型名、Key 字段映射等）
- `utils/api_config_loader.py` - 读取 api_providers.json（`get_core_api_profiles` / `get_assist_api_profiles`）
- `utils/config_manager/storage_roots.py` - 运行时数据根目录，以及 `get_config_path` / `get_runtime_config_path` / `save_json_config`
- `utils/config_manager/migrations.py` - `migrate_config_files`：运行时目录缺少配置文件时从项目 `config/` 复制
- `utils/config_manager/core_config.py` - `get_core_config` / `get_model_api_config`：合并配置、按模型类型取配置
- `utils/config_manager/characters.py`、`voice_storage.py`、`workshop.py`，`utils/preferences.py` - 其余用户配置文件的读写
- `main_routers/config_router/` - 配置读写 API（包，路由前缀 `/api/config`）：`core_config.py`（`/core_api`、`/api_providers`）、`preferences.py`（`/preferences`、`/conversation-settings`）、`language.py`（`/ui-language`）等
- `main_routers/characters_router/` - 角色与音色 API（路由前缀 `/api/characters`），写 `characters.json`、`voice_storage.json`
- `main_routers/workshop_router/config_files.py` - 创意工坊配置读写（`/api/steam/workshop/config`）
- `docker/entrypoint.sh` - `migrate_legacy_bootstrap_config` / `setup_configuration`：启动时迁移旧版配置、用 `NEKO_*` 环境变量生成 core_config.json；`detect_legacy_layout` / `warn_legacy_layout`：提示旧挂载布局
- `docker/docker-compose.yml` - 数据卷挂载（`./neko-home:/home/neko`）和存储根目录环境变量
- `docker/env.template` - 环境变量模板（其中的变量要经 `environment:` 或 `docker run -e` 传进容器才会生效，见「Docker 部署配置方式 → 方式 1」）

## ⚠️ 注意事项

1. **API Key 以明文保存在配置文件中**
   - `core_config.json` 里存着各服务商的 Key 和 MCP Token，宿主机上对应 `neko-home/.local/share/N.E.K.O/config/core_config.json`
   - `voice_storage.json` 用 API Key 作为音色的分组名（阿里百炼 CosyVoice 的克隆 / 设计音色、按当前 `AUDIO_API_KEY` 保存的音色直接用完整 Key），同样含明文 Key，备份或分享这个文件前要注意
   - Web UI 读取配置的接口（`GET /api/config/core_api`）不回传完整 Key，只返回占位符和首尾部分字符的遮罩；但磁盘上的文件是明文，注意 `neko-home/` 的访问权限和备份

2. **配置文件不要提交到 Git**
   - 仓库 `.gitignore` 已忽略 `config/*.json`（`config/api_providers.json` 除外）、任意目录下名为 `.env` 的文件（含 `docker/.env`），以及容器持久化目录 `neko-home/`、`docker/neko-home/`（还有旧布局的 `N.E.K.O/`、`ssl/`）

3. **`NEKO_*` API 变量只在生成 core_config.json 时使用**
   - 生效条件、`NEKO_FORCE_ENV_UPDATE` 会整份重写配置、自带 compose 不会把 `docker/.env` 里的这些变量传进容器，见「🐳 Docker 部署配置方式 → 方式 1」
   - 本项目没有读取 Docker secrets（`/run/secrets/*`）的逻辑，挂载 secrets 不会生效

4. **未开启自定义 API 时，模型由服务商配置决定**
   - `enableCustomApi` 为 `false` 时各模型跟随所选服务商（第 4、5 节），`core_config.json` 里各槽位的 `<前缀>Model*` 字段不生效；不受这个开关限制的 TTS 例外和开启后的规则见第 7 节
