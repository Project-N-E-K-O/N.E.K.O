# NEKO Live 贡献指南

这是一份给第一次参与 NEKO Live 开发的短入口。架构、PR 规则和测试门禁的完整权威来源仍是 [`development.md`](development.md) 与 [`AGENTS.md`](../AGENTS.md)；本文只说明如何安全地开始一个独立贡献。

## 1. 开始前

1. 阅读 [`developer-guide.md`](developer-guide.md)，建立模块和数据流心智模型。
2. 阅读 [`development.md`](development.md) 中的“不变量”“禁止堆叠式 PR”“模块 Owner 与 Review Gate”。
3. 检查 [`live-center-roadmap.md`](live-center-roadmap.md)，确认任务属于当前阶段；不要自行提前实现长期计划。
4. 在 Issue 或 Draft PR 中写清目标、非目标、预期改动文件、验证方式和回滚 / 降级方式。
5. 开始修改前确认工作区干净；不得覆盖或混入其他人的本地改动。

## 2. 分支与 PR

NEKO Live 只接受独立式 PR，不接受 stacked PR 或逻辑堆叠链。

```powershell
git fetch upstream
git switch -c <type>/<short-topic> upstream/main
# 修改、验证、普通 commit
git push -u origin <type>/<short-topic>
```

- PR base 必须是 `Project-N-E-K-O/N.E.K.O:main`，或维护者明确指定的 release branch。
- 一个 PR 必须能独立 review、测试、合并和回滚，不依赖另一个未合并 PR。
- 有前置依赖时，先合并前置 PR，再从更新后的主线创建后续分支。
- 不使用 rebase、amend、squash、force-push 或改写历史维护开放 PR 链。
- `main` 推进时，只在当前独立 PR 分支中普通 merge 最新主线并解决一次冲突。

Draft 转 Ready 前必须填写：改动范围、测试结果、文档影响、已知风险和回滚 / 降级方式。超过仓库 PR 文件门槛时，按全局 PR 模板说明为什么不拆分。

## 3. 先选低风险任务

新贡献者优先选择：

- 模块文档补充或纠错；
- fixture、EventBus / live_events 测试样本和回归用例；
- Dashboard 小型只读展示和文案修正；
- 8 locale 同步；
- 不改变核心行为的 docs-only 或 tests-only PR。

第一周不建议直接修改 Protected Modules。确需触碰时，先开 Draft PR，由对应 Owner 确认边界后再实现。

## 4. 高风险区域

以下改动必须由 NEKO Live Code Owner 和 `development.md` 中对应的 Owner 角色 review：

- EventBus、contracts、module registry 等核心架构；
- B 站 / 抖音事件接入、直播协议和事件归一化；
- `live_events` 选择权和评分；
- `live_support_events` 的礼物证据、去重、连击、优先队列和会话回收；
- Pipeline、Safety Guard、Dispatcher 和输出质量合约；
- Runtime、直播控制、配置持久化和 Hosted UI action；
- 凭据、观众档案、audit 与隐私边界；
- Hosted UI 导航外壳和 `panel_compat.tsx` 分发入口。

如果改动引入新的产品行为、生产依赖、持久数据、平台写能力、浏览器自动化、额外进程或明显 CPU / 内存 / 网络成本，先写设计和 Decision Points，获得维护者确认后再实现。

## 5. 实现约束

- Provider 只负责接入和安全归一化，不直接让 NEKO 开口。
- 所有输出继续经过 Pipeline、Safety Guard、`dry_run` 和 Dispatcher。
- 普通弹幕文本不能伪造 Gift / SC / Guard 事实。
- 不记录原始对话、cookie、token、头像字节或其它敏感 payload。
- 新增或修改用户可见 i18n key 时同步全部 8 个 locale。
- 修改 `panel.tsx` 时同步完整功能的 `panel_compat.tsx`，不能把兼容入口退化成 fallback 壳。
- 不整体复制旧 `bilibili_danmaku` / `bilibili_dm` 大文件；迁移能力必须拆成有边界、可测试的小模块。

## 6. 提交前验证

从仓库根目录运行：

```powershell
uv run pytest plugin/plugins/neko_roast/tests -q
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_roast
git diff --check
```

Python 命令统一使用 `uv run`。如果只改文档，可以在 PR 中明确说明未运行代码测试；修改 Python、UI、i18n、配置、manifest、契约或 runtime 行为时必须执行对应聚焦测试和完整插件门禁。

涉及分发边界时，还要运行 `tests/test_distribution_boundaries.py`，确认 `plugin.toml.lock`、本地截图、日志和其它运行态文件没有进入 Git index 或插件包。

## 7. Review 交付信息

PR 描述至少应让 reviewer 快速回答：

- 这个 PR 只解决哪一个问题？
- 哪些行为明确没有改变？
- 是否触碰 Protected Modules，谁负责 review？
- 哪些自动测试和人工场景已经验证？
- 失败时如何降级或回滚？
- 哪些文档、locale 或分发边界随实现同步更新？

无法回答这些问题时，PR 保持 Draft。
