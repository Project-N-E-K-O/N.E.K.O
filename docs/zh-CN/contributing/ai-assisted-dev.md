# AI 辅助开发

N.E.K.O. 为 AI 编程助手提供了内置配置。项目目前提供两个平台的配置，你也可以将其适配到任何其他 AI 工具。

## 已支持的平台

| 平台 | 配置位置 | 自动加载？ |
|------|---------|-----------|
| **Claude Code** | `.agent/rules/`、`.agent/skills/`、`CLAUDE.md` | 是 — Claude Code 原生读取 `.agent/` 和 `CLAUDE.md` |
| **Cursor** | `.cursor/skills/` | 是 — Cursor 原生读取 `.cursor/` |
| **其他工具** | — | 需要手动配置（见下文） |

## `.agent/` 目录（Anthropic 规范）

`.agent/` 遵循 [Anthropic Agent 规范](https://docs.anthropic.com/en/docs/claude-code)。**Claude Code** 会自动加载这些文件；其他工具不会。

```
.agent/
├── rules/
│   └── neko-guide.md        # 核心开发规范（always_on 触发器）
└── skills/                  # 约 20 个按任务划分的技能集
    ├── i18n/
    ├── frontend-design/
    ├── vrm-physics/
    └── ...
```

### 规则

`.agent/rules/neko-guide.md` 标记为 `trigger: always_on`，即 Claude Code 在每次会话开始时都会加载它。涵盖内容：

- **i18n**：修改翻译字符串时必须同步更新全部 6 个 locale 文件。
- **`uv run`**：始终使用 `uv run` 运行 Python，不使用系统 Python。
- **代码对偶性**：并行的 provider 必须保持结构对称。
- **core 层纯净性**：`core.py` 中不允许出现 provider 特定逻辑。
- **禁止数字后缀**：应将共享逻辑抽取为方法。
- **Electron 与开发模式**：前端改动必须兼顾两种运行环境。

### 技能集

`.agent/skills/` 下的每个子目录包含一个 `SKILL.md`，定义了触发条件、领域知识和分步工作流。它们在当前任务匹配时按需加载。

## 适配其他 AI 工具

如果你的 AI 编程助手**不**原生支持 `.agent/`，请按以下步骤将项目规则导入你的工具：

1. **读取核心规则** — 打开 `.agent/rules/neko-guide.md`，将其内容复制到你的工具的项目级提示词或规则文件中。例如：
   - **Windsurf**：粘贴到 `.windsurfrules`
   - **GitHub Copilot**：粘贴到 `.github/copilot-instructions.md`
   - **其他工具**：使用你的工具提供的"项目上下文"或"系统提示词"机制。

2. **浏览技能集** — 查看 `.agent/skills/` 中与你的工作相关的领域。每个 `SKILL.md` 都是自包含的 Markdown — 可以在处理相关领域时将其作为额外上下文提供给你的 AI 工具。

3. **参考 `CLAUDE.md`** — 仓库根目录的这个文件包含了最关键规则的简明摘要。如果你只想导入一个文件，它是最好的起点。

内容为纯 Markdown，不依赖特定工具 — 规则本身适用于任何 AI 助手。

## 核心规则速查

无论使用哪个 AI 工具，以下是不可违反的项目规则：

1. **i18n 完整性** — 修改任何翻译字符串时，必须同步更新全部 6 个 locale 文件（`en`、`ja`、`ko`、`zh-CN`、`zh-TW`、`ru`）。
2. **`uv run` 运行 Python** — 不要直接使用系统 Python。
3. **代码对偶性** — 如果一个 provider 有某个模式，所有 provider 必须对称地遵循。
4. **core 层纯净性** — `core.py` 不得包含 provider 特定的 import 或逻辑。
5. **双模式感知** — 前端改动必须同时在开发服务器（单窗口）和 Electron（多窗口）环境下工作。
