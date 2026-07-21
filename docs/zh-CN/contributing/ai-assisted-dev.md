
# AI 辅助开发

仓库把可供机器读取的项目规范放在 `.agent/` 下。

```text
.agent/
├── rules/
│   └── neko-guide.md
└── skills/
    ├── i18n/
    ├── neko-plugin/
    ├── ui-system-refactor/
    └── ...
```

不要假设某个编辑器或编码 Agent 会自动加载这些文件。

## Agent 必须完成的准备

1. 阅读当前 revision 的 `.agent/rules/neko-guide.md`。
2. 在 `.agent/skills/` 中查找任务领域，完整阅读匹配的 `SKILL.md` 及其引用文件。
3. 修改前检查当前实现归属、测试和 workflow。
4. 保留用户已有改动，diff 不超出请求范围。
5. 如实报告执行过的命令，以及未执行的验证。

不要要求 Agent 阅读不存在的 `CLAUDE.md`、把规则复制到未跟踪的编辑器文件，或依赖某家工具声称的自动加载行为。

## 提示词起点

> 阅读 `.agent/rules/neko-guide.md`，然后在 `.agent/skills/` 中查找并遵循与任务匹配的 skill。修改前追踪当前实现和测试。所有 Python 命令使用 `uv run`；i18n 修改同步八个 locale；保留 system prompt 水印；只写入当前仓库能证明的结论。

AI 生成的修改与人工修改接受相同审查。作者仍需对隐私、安全、许可证、正确性和测试证据负责。
