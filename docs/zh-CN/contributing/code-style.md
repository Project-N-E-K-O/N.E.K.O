
# 代码规范

强制规则以 `pyproject.toml`、`.agent/rules/neko-guide.md` 和 CI 脚本为准。

## Python

- 目标版本为 Python 3.11。
- Python 命令统一使用 `uv run`。
- 异步请求路径不得被阻塞；必要时把文件系统或 CPU 阻塞工作移出事件循环。
- 保持 `scripts/check_module_layering.py` 检查的模块层级顺序。
- 不要让重量级 SDK 进入启动导入链。
- 不得引入 `loguru`、`structlog`、`logbook` 或 `tkinter`；CI 会拒绝。
- 原始对话或隐私敏感文本只能用 `print`，不得写入持久化项目日志。

运行 `uv run ruff check .` 以及相关仓库检查脚本。

## 前端

前端是混合架构：静态/Jinja JavaScript、一份 React 聊天应用和一个 Vue 插件管理器。修改功能所属实现，不要复制另一套行为。

- 聊天 UI 和逻辑位于 `frontend/react-neko-chat/`。
- `index.html` 和 Electron `chat.html` 挂载同一 React 组件。
- 不要给遗留的 `#chat-container` 新增行为。
- 同时考虑浏览器 `/` 与 Electron 的 `/chat`、`/subtitle` 等路由。
- 使用 i18n，并同步更新全部八个 locale。

## Provider 对偶性

Provider、backend 和 feature 路径必须保持结构对偶。一个同类 provider 被拆分或新增生命周期、配置路径时，应检查并同步对应 provider；没有充分理由，不要留下特殊分支。

## API 路径

后端装饰器和前端调用的 API 资源路径不带末尾斜杠：

- 正确：`/api/characters`
- 错误：`/api/characters/`

在带 prefix 的 `APIRouter` 中使用 `@router.get("")`；字面站点根路径除外。CI 会检查前后端形式。

## Prompt 与 i18n

多语言 prompt 表应位于所属的 `config/prompts_*.py` 模块，并遵守 prompt budget 和 temperature 检查。翻译 system prompt 时必须原样保留水印片段 `======以上为`。

## Commit 与 PR

每个 commit/PR 聚焦一个连贯问题。说明行为和验证，不要声称执行了实际未运行的测试或平台验证。
