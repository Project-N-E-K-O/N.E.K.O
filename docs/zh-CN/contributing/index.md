
# 参与贡献

Project N.E.K.O. 接受范围明确的代码、文档、翻译、测试和内容工具贡献。

## 工作流程

1. Fork 仓库，并从当前 `main` 创建聚焦单一问题的分支。
2. 阅读 `.agent/rules/neko-guide.md`，以及与任务匹配的 `.agent/skills/*/SKILL.md`。
3. 使用 Python 3.11 和 `uv sync` 准备环境；使用仓库脚本构建前端。
4. 在功能所属模块内做最小且结构对偶的修改。
5. 先运行针对性测试，再运行相关 lint 或构建检查。
6. 创建 PR，说明行为变化、风险和验证结果。

所有 Python 命令都通过 `uv run` 执行。面向用户的 i18n 修改必须同时更新八个 locale 文件。

## PR 门禁

- 修改 `app/`、`main_logic/` 或 `memory/` 下的 Python 时，PR 描述必须包含 `scripts/check_pr_report.py` 要求的回归报告章节。
- 计数文件超过 20 个时，必须填写非空的“不拆分理由”。新文件、识别出的测试文件和同步修改的 i18n locale 组按门禁规则排除。
- 静态分析和插件测试以当前 workflow 为准，不要沿用旧检查清单。

修改相应领域前，请阅读[测试](./testing)、[代码规范](./code-style)和 [Nuitka 打包](./nuitka-packaging)。

## 报告问题与社区协作

请通过 [GitHub Issues](https://github.com/Project-N-E-K-O/N.E.K.O/issues) 提交可复现的问题或范围明确的提案。提供运行环境、精确 revision/构建产物、预期与实际行为、脱敏日志以及最小复现。

贡献受仓库当前 `LICENSE` 约束；不要从本文档推导额外的分发承诺。
