# Contributing to Project N.E.K.O.

[中文版](#中文) | [English](#english)

---

<a id="english"></a>

Thank you for your interest in contributing to Project N.E.K.O.!

## Talk to Us First

**Before writing any code, please reach out to us.** We want to make sure your effort is aligned with our roadmap and doesn't duplicate ongoing work.

- **Discord**: [Join Us](https://discord.gg/5kgHfepNJr) — best for international contributors
- **QQ Group**: [1022939659](https://qm.qq.com/q/HxeaMdSkQW) — best for Chinese-speaking contributors

For bugs or feature requests, [opening an Issue](https://github.com/Project-N-E-K-O/N.E.K.O/issues/new/choose) first is recommended (not required) so we can give early feedback and avoid duplicated effort.

## Workflow

1. **Open an Issue** (or comment on an existing one) to discuss what you want to do
2. Get a thumbs-up from a maintainer
3. Fork the repository and create a feature branch: `git checkout -b feature/your-feature`
4. Make your changes and test locally
5. Push and open a Pull Request against `main` (reference the Issue if there is one)

## Development Setup

> Full developer documentation: [project-neko.online](https://project-neko.online)

**Requirements**: Python 3.11 (other versions not supported), [uv](https://docs.astral.sh/uv/)

> [!IMPORTANT]
> **⚡ Clone with a partial clone!** This repo has a large history (`.git` ~390 MB) due to old binary assets. Use `--filter=blob:none` so you only download what you actually need — **much smaller and faster**, with zero impact on development:
> ```bash
> git clone --filter=blob:none https://github.com/Project-N-E-K-O/N.E.K.O.git
> ```

```bash
# ⚡ Strongly prefer the partial-clone command above (drop --filter only if you need full history)
git clone --filter=blob:none https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync

# Start services
uv run python app/memory_server.py
uv run python app/main_server.py
# Optional: uv run python app/agent_server.py
```

Visit `http://localhost:48911` to configure API keys and start using.

## Code Style

- Follow existing patterns in the file you're editing
- All user-facing strings must support i18n (Chinese, English, Japanese at minimum)
- Add comments only where the logic isn't self-evident

## Required Reports in the PR Description

Two project rules are enforced by CI (`scripts/check_pr_report.py`). Both reports go in the **PR description** (use the template that auto-fills when you open a PR):

1. **Regression report** — if your PR changes any `*.py` under `app/`, `main_logic/`, `main_routers/`, or `memory/` (our highest-risk modules), the description must contain a non-empty **"回归报告 / Regression Report"** section covering: what changed, the rationale / necessity, before-and-after behaviour, and the potential regressions.
2. **Why-not-split rationale** — if your PR changes more than 20 counted files, the description must contain a non-empty **"不拆分理由 / Why Not Split"** section explaining why it isn't broken into smaller PRs. New files, i18n locale files, and test files (anywhere, under the main tree or `plugin/`) don't count toward the limit.

Notes:
- CI only checks that the section exists and is non-empty — a maintainer reviews the report's substance (these paths are routed via [CODEOWNERS](.github/CODEOWNERS)). Don't fill it with placeholders (`不适用` / `N/A` / `TBD` fail the check when the rule is triggered).
- If a rule doesn't apply, write "不适用 / N/A" or delete that section.
- Maintainers can apply the `report-exempt` label to skip the gate for pure renames, bulk reformatting, or generated code.

## Other Contributions

We also welcome non-code contributions:
- **Live2D / VRM / MMD models**
- **Voice recordings** for voice cloning
- **Translations** for UI and documentation
- **Character persona packs** for the Workshop
- **Plugins** built with the Plugin SDK

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).

---

<a id="中文"></a>

# 参与贡献

感谢你有兴趣为猫娘计划做出贡献！

## 先跟我们聊聊

**写代码之前，请先联系我们。** 我们希望确保你的工作与项目方向一致，避免重复劳动。

- **QQ群**：[1022939659](https://qm.qq.com/q/HxeaMdSkQW)
- **Discord**：[加入我们](https://discord.gg/5kgHfepNJr)

Bug 或功能建议建议先[提交 Issue](https://github.com/Project-N-E-K-O/N.E.K.O/issues/new/choose)（非必须），方便我们尽早给反馈、避免重复劳动。

## 工作流程

1. **提交 Issue**（或在已有 Issue 下评论）讨论你想做的事
2. 等待维护者确认
3. Fork 仓库并创建功能分支：`git checkout -b feature/your-feature`
4. 修改代码并本地测试
5. Push 并向 `main` 提交 Pull Request（有对应 Issue 就引用）

## 开发环境

> 完整开发者文档：[project-neko.online](https://project-neko.online)

**要求**：Python 3.11（不支持其他版本）、[uv](https://docs.astral.sh/uv/)

> [!IMPORTANT]
> **⚡ 请用「部分克隆」!** 本仓库历史体积较大(`.git` 约 390 MB,源于历史中的旧版二进制资源)。加 `--filter=blob:none` 只下载真正用到的文件,**体积更小、速度更快**,且不影响开发:
> ```bash
> git clone --filter=blob:none https://github.com/Project-N-E-K-O/N.E.K.O.git
> ```

```bash
# ⚡ 强烈推荐用上方「部分克隆」命令（确需完整历史再去掉 --filter）
git clone --filter=blob:none https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync

# 启动服务
uv run python app/memory_server.py
uv run python app/main_server.py
# 可选：uv run python app/agent_server.py
```

访问 `http://localhost:48911` 配置 API Key 后即可使用。

## 代码风格

- 遵循所修改文件的现有风格
- 所有面向用户的字符串需支持 i18n（至少覆盖中文、英文、日文）
- 仅在逻辑不自明时添加注释

## PR 描述里的必填报告

两条规范由 CI 强制（`scripts/check_pr_report.py`），报告都写在 **PR 描述**里（开 PR 时模板会自动带出）：

1. **回归报告** —— 凡是改动了 `app/`、`main_logic/`、`main_routers/`、`memory/` 下任一 `*.py`（项目最高风险模块），PR 描述必须有非空的 **「回归报告 / Regression Report」** 一节，逐项说明：改动了什么、理由 / 必要性、前后表现对比、潜在回归点。
2. **不拆分理由** —— 单个 PR 改动计入上限的文件超过 20 个，PR 描述必须有非空的 **「不拆分理由 / Why Not Split」** 一节，说明为什么不拆成更小的 PR。新增文件、i18n locale 文件和测试文件（无论在主目录还是 `plugin/` 下）不计入这个上限。

说明：
- CI 只验区块存在且非空，报告质量由维护者评审兜底（这些路径经 [CODEOWNERS](.github/CODEOWNERS) 强制指派维护者）。别拿占位符糊弄（触发条件成立时 `不适用` / `N/A` / `TBD` 会被判失败）。
- 不适用的那一节写「不适用 / N/A」或直接删除。
- 维护者可对纯重命名、批量格式化、生成代码等打 `report-exempt` 标签，跳过整条门禁。

## 其他贡献

我们同样欢迎非代码贡献：
- **Live2D / VRM / MMD 模型**
- **语音录音**（用于语音克隆）
- **翻译**（UI 和文档）
- **角色人设包**（用于创意工坊）
- **插件开发**（使用 Plugin SDK）

## 许可证

参与贡献即表示你同意你的贡献将在 [Apache 2.0 许可证](LICENSE) 下发布。
