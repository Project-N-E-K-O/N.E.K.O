
# 开发者注意事项

以下是当前仓库契约。历史事故细节应沉淀到所属规则、测试或设计记录，而不是复制易过期的模型和版本表。

## 强制规则

### Python 统一通过 uv

```bash
uv run python launcher.py
uv run pytest
uv run ruff check .
```

项目 Python 不使用裸 `python`、`pytest` 或临时 `pip` 命令。

### 八语言 i18n

运行时 locale 为：

```text
en, ja, ko, zh-CN, zh-TW, ru, pt, es
```

面向用户的 i18n 修改必须更新 `static/locales/` 中的全部文件。插件管理器 locale 使用自己的同步组。CI 会检查同步修改。

### 隐私敏感输出

原始对话或其他用户隐私文本只能使用 `print`，不得通过项目 `logger` 输出。确认不含原始隐私内容的系统事件可以使用配置好的 logger。

### Prompt 水印

翻译或重排 system prompt 时必须保留 `======以上为`。

### 结构对偶

Provider、backend 和 feature 实现应结构成对。拆分、重命名、配置或打包其中一条路径时，要审查同类 provider。

## 运行时边界

- 浏览器开发使用 `/`、单页面/单窗口，默认端口 48911。
- Electron 使用 `/chat`、`/subtitle` 等独立路由和窗口。
- 静态路径、初始化、IPC 和构建产物必须同时适配两种环境。
- `frontend/react-neko-chat/` 是唯一聊天实现；`index.html` 和 `chat.html` 都挂载到 `#react-chat-window-root`。
- 遗留 `#chat-container` 已隐藏并废弃；`app-chat-adapter.js` 负责桥接旧 `appendMessage()` 调用。

## 后端边界

- API 资源路径不以 `/` 结尾，避免反向代理后的 Starlette 重定向。
- 异步处理器中不得直接执行阻塞工作。
- 启动期间的同步配置写入，如果也用于异步路径，应提供对应的异步 `a*` 接口。
- 主包层级和 `main_logic/core/` facade 契约由 CI 检查。
- 受限的 LLM 构造/调用点不得传入 `temperature=`；同时关注输出、超时和输入预算。

## 记忆系统

对话事件持久化、投影、召回候选、证据/反思、人格以及维护队列属于不同层。修改前阅读[记忆系统](/zh-CN/architecture/memory-system)和已实现的设计记录。不要把插件内一小时上下文误写成语义记忆召回。

## 前端边界

项目并非“只有原生 JavaScript”，而是包含静态/Jinja JavaScript、React 聊天和 Vue 插件管理器。先确认所属子树和测试。不要依赖固定延时猜测 DOM 就绪；遵循现有生命周期与事件机制。

## Steam 与打包

成就和云存档可能产生外部不可逆影响。使用现有测试钩子和 staging 流程，不要随意用真实账户测试破坏性行为。打包修改须遵循 [Nuitka 打包](./nuitka-packaging)和当前构建 workflow。

## 验证

先运行最小相关测试，再按风险扩大测试和构建。权威静态门禁是 `.github/workflows/analyze.yml`；插件测试、文档构建、桌面打包和 Docker 分别由不同 workflow 负责。
