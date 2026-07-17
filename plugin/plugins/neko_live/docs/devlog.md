# neko_live 开发日志

> 记开发中发现的、**不在本插件职责内**的问题（多为宿主 / SDK 侧）、当前修复状态，以及插件侧保留的兼容取舍。
> 本插件自身设计/进度见 `development.md` / `live-center-roadmap.md`。

## 2026-06-17 — 宿主侧存储问题（影响观众档案持久化）

排查面板顶部「插件存储未启用，观众档案不会持久保存」时，发现两个 **宿主 / SDK 层** 问题（neko_live 代码本身正确）：

### 1. PluginStore `store.enabled` 构造期冻结
- `NekoPluginBase.__init__` → SDK `sdk/shared/core/base.py:82` 在构造插件实例时即 `PluginStore(enabled=resolve_store_enabled(ctx._effective_config))` 一次性定死。
- 此刻 `ctx._effective_config` 尚未就绪 → 即便 `plugin.toml` 声明 `[plugin.store] enabled = true`，也解析成 `False`，之后不再重算 → KV store 全程短路、不落盘（数据目录里从无 `store.db`）。
- 既有规避：`memo_reminder/__init__.py:195-203`、`lifekit/__init__.py:97-104` 在 `startup()` 里手动 `self.store.enabled = True`（注释明写"如果配置中明确启用但 init 时未生效"）。
- **neko_live 的处理**：观众档案改为 **不依赖 PluginStore**，直接 JSON 文件持久化（`stores/viewer_store.py`），从根上绕开此 bug；故本插件无需该规避。
- **core / SDK 修复**：`Fix plugin host config and data root handling (#1884)` / `08b317f6` 已进入当前 `Roast` 分支；host 会在 `_effective_config` 就绪后刷新 runtime helpers，避免 `store.enabled` 停留在构造期旧值。

### 2. 插件数据未跟随「选定存储根」（selected_root）
- `config_manager` 的选定根（用户在 app 里选的数据目录，如 `D:\…\Documents\N.E.K.O`）统管 memory / config / live2d…（`config_manager.py:1046-1080` 解析 `selected_root`）。
- 但插件运行时数据走 SDK 的 `resolve_runtime_data_root`（`sdk/shared/core/base_runtime.py:79`），实测落到 `%LOCALAPPDATA%\N.E.K.O\plugins\…`，**没跟随 selected_root**（插件子进程读不到该策略 → 回退默认锚点）。
- 现象：猫记忆在 `D:\Documents\N.E.K.O\memory\{角色}`，插件数据却在 `C:\AppData\Local\N.E.K.O\plugins\` —— 两套根、分家。
- 设计意图本是二者都跟随 selected_root（`resolve_runtime_data_root` 本就 `return policy_root or anchor_root`），属 **宿主侧不一致 / bug**。
- **对 neko_live 的影响**：观众档案默认落点曾在 AppData\Local 侧。`Fix plugin host config and data root handling (#1884)` / `08b317f6` 已进入当前 `Roast` 分支，host 会在插件子进程启动前刷新 storage layout env，使插件运行时数据跟随当前 storage layout。neko_live 当前仍固定使用插件默认目录；插件侧已回归默认目录、自定义目录失败回退、实际路径状态和失败 tmp 清理，`viewer_store_dir` UI 入口按当前产品范围继续隐藏。
- **core / host 修复**：已合并到当前分支；验证切片见 `plugin/tests/unit/core/test_host_storage_layout_env.py` 与 `plugin/tests/unit/sdk/plugin/test_sdk_v2_plugin_base.py`。

### 3. 插件面板无「选择文件夹」能力 → 已用插件后端 tkinter 自解
- 宿主 hosted-ui **未向插件面板暴露**原生文件夹选择器：`showOpenDialog`/`openDirectory` 仅在 NEKO-PC 主程序自己的数据根选择逻辑（`preload-common.js`/`storage-gate.js`），不对插件 surface 开放；hosted-ui runtime 也拿不到 Electron dialog，`<input type=file webkitdirectory>` 只给相对名。
- **当前状态（in-plugin）**：`pick_folder` 动作和 `viewer_store_dir` 字段仍保留以保持兼容，但面板「档案存储」卡已不再暴露“浏览…”和保存自定义目录入口；只读展示当前默认目录。
- **已知 caveat**：① 对话框靠 `-topmost` 尽力压过 NEKO-PC 的常驻置顶窗口（screen-saver 级），**极端情况下可能仍被猫窗盖住**，需 alt-tab/任务栏唤起；② 会阻塞到用户选完（HTTP action 等待）。
- **更优长期方案**：宿主给 hosted-ui 暴露一个 `pick_directory` 动作（Electron `dialog.showOpenDialog({properties:['openDirectory']})`），由主程序窗口体系弹框、无 z-order 问题。tkinter 方案是 in-scope 兜底。

> 关联：项目记忆 `neko-roast-store-disabled-diagnosis.md`。
