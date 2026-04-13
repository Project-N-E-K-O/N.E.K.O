# AI 辅助开发

N.E.K.O. 在仓库根目录提供了 `.agent/` 目录。支持此约定的 AI 编程助手——如 **Claude Code**、**Cursor**、**Windsurf** 等——会自动加载其中定义的规则和技能集，使 AI 直接遵循项目的编码规范。

## 目录结构

```
.agent/
├── rules/
│   └── neko-guide.md        # 核心开发规范（始终加载）
└── skills/                  # 按任务划分的技能集
    ├── i18n/
    ├── frontend-design/
    ├── vrm-physics/
    ├── tts-error-reporting/
    └── ...                  # 共约 20 个技能
```

## 规则

`.agent/rules/neko-guide.md` 标记为 `trigger: always_on`，即任何兼容的 AI Agent 在每次会话开始时都会加载它。涵盖内容：

- **i18n**：修改翻译字符串时必须同步更新全部 6 个 locale 文件。
- **`uv run`**：始终使用 `uv run` 运行 Python，不使用系统 Python。
- **代码对偶性**：并行的 provider 必须保持结构对称。
- **core 层纯净性**：`core.py` 中不允许出现 provider 特定逻辑。
- **禁止数字后缀**：应将共享逻辑抽取为方法。
- **Electron 与开发模式**：前端改动必须兼顾两种运行环境。

这些规则与 `CLAUDE.md`（专门面向 Claude Code）以及项目的[代码风格](/zh-CN/contributing/code-style)指南保持一致。

## 技能集

`.agent/skills/` 下的每个子目录包含一个 `SKILL.md`，定义了：

- **触发条件** — 例如编辑 i18n 文件、处理 VRM 物理、构建前端 UI 时。
- **领域知识** — 相关的参考资料、模式和注意事项。
- **分步工作流** — 该领域常见任务的处理流程。

技能由 AI Agent 在当前任务匹配触发条件时按需加载。

## 给贡献者

如果你使用 AI 编程助手为 N.E.K.O. 贡献代码，`.agent/` 目录意味着 AI 会自动：

1. 遵循项目的代码风格和架构规则。
2. 修改翻译字符串时同步更新全部 6 个 i18n locale。
3. 使用 `uv run` 执行任何 Python 命令。
4. 尊重 Electron 与开发模式的区分。

无需额外配置——打开项目即可开始编码。

## 其他 AI 工具

`.agent/` 约定是一个新兴标准。如果你的 AI 工具尚未原生支持，可以手动将 `.agent/rules/neko-guide.md` 作为系统提示词或项目上下文文件加载。内容为纯 Markdown，不依赖特定工具。
