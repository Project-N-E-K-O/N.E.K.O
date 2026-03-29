# Contributing to Project N.E.K.O.

[中文版](#中文) | [English](#english)

---

<a id="english"></a>

Thank you for your interest in contributing to Project N.E.K.O.!

## Talk to Us First

**Before writing any code, please reach out to us.** We want to make sure your effort is aligned with our roadmap and doesn't duplicate ongoing work.

- **Discord**: [Join Us](https://discord.gg/5kgHfepNJr) — best for international contributors
- **QQ Group**: [1022939659](https://qm.qq.com/q/HxeaMdSkQW) — best for Chinese-speaking contributors

For bugs or feature requests, please [open an Issue](https://github.com/Project-N-E-K-O/N.E.K.O/issues/new/choose) first. **PRs without a corresponding Issue may be closed.**

## Workflow

1. **Open an Issue** (or comment on an existing one) to discuss what you want to do
2. Get a thumbs-up from a maintainer
3. Fork the repository and create a feature branch: `git checkout -b feature/your-feature`
4. Make your changes and test locally
5. Push and open a Pull Request against `main`, referencing the Issue

## Development Setup

> Full developer documentation: [project-neko.online](https://project-neko.online)

**Requirements**: Python 3.11 (other versions not supported), [uv](https://docs.astral.sh/uv/)

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync

# Start services
uv run python memory_server.py
uv run python main_server.py
# Optional: uv run python agent_server.py
```

Visit `http://localhost:48911` to configure API keys and start using.

## Code Style

- Follow existing patterns in the file you're editing
- All user-facing strings must support i18n (Chinese, English, Japanese at minimum)
- Add comments only where the logic isn't self-evident

## Other Contributions

We also welcome non-code contributions:
- **Live2D / VRM / MMD models**
- **Voice recordings** for voice cloning
- **Translations** for UI and documentation
- **Character persona packs** for the Workshop
- **Plugins** built with the Plugin SDK

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).

---

<a id="中文"></a>

# 参与贡献

感谢你有兴趣为猫娘计划做出贡献！

## 先跟我们聊聊

**写代码之前，请先联系我们。** 我们希望确保你的工作与项目方向一致，避免重复劳动。

- **QQ群**：[1022939659](https://qm.qq.com/q/HxeaMdSkQW)
- **Discord**：[加入我们](https://discord.gg/5kgHfepNJr)

Bug 或功能建议请先[提交 Issue](https://github.com/Project-N-E-K-O/N.E.K.O/issues/new/choose)。**没有对应 Issue 的 PR 可能会被直接关闭。**

## 工作流程

1. **提交 Issue**（或在已有 Issue 下评论）讨论你想做的事
2. 等待维护者确认
3. Fork 仓库并创建功能分支：`git checkout -b feature/your-feature`
4. 修改代码并本地测试
5. Push 并向 `main` 提交 Pull Request，引用对应的 Issue

## 开发环境

> 完整开发者文档：[project-neko.online](https://project-neko.online)

**要求**：Python 3.11（不支持其他版本）、[uv](https://docs.astral.sh/uv/)

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync

# 启动服务
uv run python memory_server.py
uv run python main_server.py
# 可选：uv run python agent_server.py
```

访问 `http://localhost:48911` 配置 API Key 后即可使用。

## 代码风格

- 遵循所修改文件的现有风格
- 所有面向用户的字符串需支持 i18n（至少覆盖中文、英文、日文）
- 仅在逻辑不自明时添加注释

## 其他贡献

我们同样欢迎非代码贡献：
- **Live2D / VRM / MMD 模型**
- **语音录音**（用于语音克隆）
- **翻译**（UI 和文档）
- **角色人设包**（用于创意工坊）
- **插件开发**（使用 Plugin SDK）

## 许可证

参与贡献即表示你同意你的贡献将在 [MIT 许可证](LICENSE) 下发布。
