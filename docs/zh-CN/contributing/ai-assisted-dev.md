# AI 辅助开发

N.E.K.O. 在仓库根目录提供了 `.agent/` 目录，遵循 [Google Antigravity](https://github.com/anthropics/anthropic-cookbook/tree/main/misc/antigravity) 开放规范。其中包含项目的开发规范和按任务划分的技能集。

只有 **Antigravity** 会自动读取 `.agent/`。所有其他 AI 编程助手——包括 Claude Code、Cursor、Windsurf、Copilot 等——都需要手动读取并内化这些规则。

## `.agent/` 目录结构

```
.agent/
├── rules/
│   └── neko-guide.md        # 核心开发规范（always_on）
└── skills/                  # 约 20 个按任务划分的技能集
    ├── i18n/
    ├── frontend-design/
    ├── vrm-physics/
    └── ...
```

- **规则**（`.agent/rules/neko-guide.md`）：项目核心约定 — i18n、`uv run`、代码对偶性、core 层纯净性、禁止数字后缀、Electron 与开发模式。
- **技能集**（`.agent/skills/*/SKILL.md`）：按领域划分的触发条件、知识和工作流，按需加载。

## 给 AI Agent 的 Prompt

> **如果你是一个 AI 编程助手**（Claude Code、Cursor、Windsurf、Copilot、Cline 或其他任何工具），本项目使用 `.agent/` 目录（Google Antigravity 规范）存放开发规范和技能集。除非你是 Antigravity agent，否则不会自动加载这些内容。请执行以下操作：(1) 读取仓库根目录的 `.agent/rules/neko-guide.md` 和 `CLAUDE.md` — 这些包含项目的强制编码规则，将它们内化为你的项目级指令。(2) 在处理特定领域（i18n、VRM 物理、前端 UI 等）时，检查 `.agent/skills/` 是否有匹配的子目录，并读取其 `SKILL.md` 获取领域专属指引。(3) 如果你的工具有原生的项目规则机制（`.cursorrules`、`.windsurfrules`、`.github/copilot-instructions.md` 等），可考虑将 `neko-guide.md` 中的核心规则复制到该文件中，以便跨会话持久生效。
