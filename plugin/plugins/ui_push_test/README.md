# UI 推送测试

一个手动测试插件，使用 Hosted TSX 面板调用插件自身的 `ui.action`，测试现有 SDK 的 Chat 卡片和 AgentHUD 插件内容。需要包含 `ctx.create_card()`、`ctx.create_view()` 的 N.E.K.O 版本，并使用与后端匹配的前端构建。

这里的「创建 Agent 内容」创建的是 AgentHUD 中的插件内容页，不会创建或执行 AI Agent 任务。

## 打开与测试

1. 启动 N.E.K.O 主服务和插件服务，打开目标角色的聊天界面。
2. 在插件管理器刷新插件列表，找到 **UI 推送测试**（`ui_push_test`），手动启动，打开它的面板。插件默认不自动启动。
3. 填写聊天界面当前角色的**准确名称**。插件会把内容定向发给该角色；名称错误时，本地提交可能成功，但目标界面不会显示。
4. 点击 **创建 Chat 卡片**：聊天中出现一张带占位文字和测试按钮的卡片。
5. 编辑 HTML、CSS、标题和摘要，点击 **推送 Chat 内容**：刚创建的卡片原地更新。再次编辑和推送可测试连续更新。
6. 点击卡片里的 **测试插件回调**，回到面板点击 **刷新记录**，应看到 `chat_callback`、卡片 ID、角色和 `run_id`。
7. 点击 **创建 Agent 内容**：现有 AgentHUD 的插件内容区域显示内容页。编辑后点击 **推送 Agent 内容**，检查同一页更新。
8. 点击 Agent 内容里的测试按钮，刷新面板应看到 `agent_callback`。点击 **关闭 Agent 内容**，检查页面关闭；之后可重新创建。

面板打开、刷新、启动和关闭插件本身都不会自动推送内容。所有推送均由测试按钮触发，无定时器、轮询、外部请求或 LLM 调用。插件声明 `passive = true`，不参与 Agent 自动选工具。

## 字段与显示行为

- **创建**使用占位内容以及当前标题、CSS；**推送**使用编辑器中的 HTML、CSS、标题和摘要。
- HTML/CSS 经过平台已有的受限渲染流程。无需写 JavaScript；插件自动附加一个声明式按钮：`data-neko-action="echo"`。
- 按钮绑定 `{entry: "echo", args: {surface, locale}}`；`echo` 同时声明 `@ui.action` 与 `@plugin_entry`，接收平台传入的 `_ctx`，把回调写入面板记录。
- Chat 每次创建产生新的卡片；推送更新当前角色最近创建的卡片。Agent 同一插件、同一角色只显示一个内容页，再次创建会替换旧页。
- 不同角色的句柄和更新计数分别保存。切换面板目标字段不会改变已创建内容的接收角色。
- 「已提交」仅表示本地 SDK 接受提交，不是最终显示确认。面板显示的是最近创建的 ID，不探测前端可见状态。
- 如果在前端手动关闭 Agent 页、清空聊天或刷新页面，重新创建后再推送。插件重启也会丢弃本地句柄与记录；已有前端内容不会因此自动关闭。
- 仅保留本次运行最近 30 条操作/回调记录。内容内回调完成后需手动刷新面板。

面板及回执提供简体中文、繁体中文、英语、日语、韩语、俄语、西班牙语和葡萄牙语。

## 开发与验证

在 N.E.K.O 仓库根目录执行：

```bash
uv run neko-plugin check ui_push_test
uv run python -m pytest plugin/plugins/ui_push_test/tests -q
uv run ruff check plugin/plugins/ui_push_test
node frontend/plugin-manager/scripts/check-hosted-tsx.mjs plugin/plugins/ui_push_test
node --test plugin/plugins/ui_push_test/tests/panel.test.mjs
uv run neko-plugin check ui_push_test --release
```

浏览器测试复用 `frontend/plugin-manager` 已安装的 esbuild / Playwright，需要可用的 Chromium；可用 `NEKO_TEST_CHROMIUM=/absolute/path/to/chrome` 指定已有浏览器。该测试使用真实 Hosted TSX 编译器和 UI Kit、模拟宿主桥接；Python 测试使用真实 SDK 句柄、模拟 IPC 提交端。它们不代替上面的主服务 → 聊天窗口人工联调。

插件没有额外 Python 依赖。实现位于 `__init__.py`，面板位于 `ui/panel.tsx`，配置位于 `plugin.toml`，翻译位于 `i18n/`。打包可运行：

```bash
uv run neko-plugin build ui_push_test
```
