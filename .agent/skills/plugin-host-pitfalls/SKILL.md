---
name: plugin-host-pitfalls
description: 改插件宿主和插件框架（`plugin/core`、`plugin/server`、`plugin/sdk`、`plugin/neko_plugin_cli`、`frontend/plugin-manager`）之前读。讲导入命名空间、路径根、打包期元数据、安装卸载事务、hosted UI 打包契约、插件页面写路由这些不显然的不变式，以及评审里反复出现、已经定过的取舍。写插件本身看 `neko-plugin`。
---

# 插件宿主：坑和既定规则

这份文档给改**宿主 / 框架**的人看。写一个插件怎么做，在 `.agent/skills/neko-plugin/`，这里不重复。

已有文档，先读它们，本文只补它们没写的：

- 插件页面写路由的 CSRF 和来源校验：`docs/design/security/local-mutation-auth.md`
- 宿主和插件子进程之间的控制通道：`docs/design/plugin-control-rpc.md`
- 插件用模型槽位：`docs/design/plugin-model-slots.md`
- 回复尾图：`docs/plugin-reply-tail.md`
- 打包：`docs/contributing/nuitka-packaging.md`

## 必须守住的不变式

1. **插件入口只经 `plugin.core.host._import_plugin_module(module_path, config_path, logger)` 导入，并且要带上 `plugin.toml` 的路径。** 内置插件的模块名是 `plugin.plugins.<id>`，装在用户目录的插件会被改写成顶层 `plugins.<id>`，后者靠「按 `plugin.toml` 所在目录加载」才找得到。不带 `config_path` 就退化成普通 `import`，用户插件会找不到模块。共享的插件根目录**不许**加进 `sys.path`。详见 `references/import-and-roots.md`。

2. **`plugin/settings.py` 的路径根有两种读法并存。** `USER_PLUGIN_CONFIG_ROOT` 这类模块属性是导入时算好的固定值；`get_user_plugin_exec_root()` 这类函数每次调用都重新读环境变量。两种都有代码在用。⚠️ 测试夹具只改其中一种，另一种会悄悄指向开发机上真实的用户目录，表现得像产品 bug。详见 `references/import-and-roots.md`。

3. **宿主刷新插件列表时不 import 插件。** 入口的元数据在打包时求值一次，写进包里的 `plugin.meta.json`，宿主只读这个文件。只有「启动某个插件」和「换源校验」两处按需 import。想在刷新路径上加任何会执行插件代码的东西，先读 `references/packaged-metadata.md`，里面列了已经否掉的方案。

4. **安装、升级、卸载、启动共用一把操作锁（`plugin/server/application/plugins/operation_lock.py`）。** 拿到锁之后包装层会屏蔽取消，所以不要在外面再套 `asyncio.wait_for`：超时只会把一个真实结果错报成超时，而里面的改动还在继续。需要时限就把截止时间传进去。详见 `references/install-and-config.md`。

5. **插件页面会直接调用的写路由用 `PluginPageMutationGuardedRoute`，token 可选；只给插件管理器和 CLI 用、会改代码或进程生命周期的路由用 `PluginMutationGuardedRoute`，token 必需。** 市场上已发布的插件页面不带 token，所以「把页面路由的 token 改成默认必需」不收。理由和细节都在 `docs/design/security/local-mutation-auth.md`。

6. **hosted TSX 的导入导出契约有两处实现，必须一致**：前端的 `hostedTsxModule.mjs`（运行时和检查脚本共用）和后端 `ui_query_service.py` 里的 `_hosted_*` 扫描器。装到用户机器上的第三方插件只走后端这一条，不经过检查脚本，所以契约不能只在检查脚本里守。详见 `references/hosted-ui-host-side.md`。

7. **hosted surface 调插件入口只能走 `api.call` 桥，并且只能调标了 `@ui.action` 的入口。** surface 跑在没有同源权限的沙箱 iframe 里，直接 `fetch('/runs')` 在浏览器里会被 CORS 拦掉。详见 `references/hosted-ui-host-side.md`。

8. **任务大脑（NekoClaw）挑插件时只看正在运行的插件。** 所以内置插件 `auto_start` 的默认值直接决定它会不会每次都进选择提示词。给一个带很多入口的内置插件开自启动之前，先算这笔开销。详见 `references/host-boundaries.md`。

9. **新增的 SDK 语义一律做成 opt-in。** 不带新参数的既有调用方，行为要逐字节不变。判断「是不是回归」要去翻现有调用方的代码，不要只靠推理。详见 `references/host-boundaries.md`。

10. **插件不直接操作私聊记忆。** SDK 给插件的记忆接口是只读的；聊天平台类插件要写记忆，走按主体划分的群组记忆。详见 `references/host-boundaries.md`。

## 测试和 CI 覆盖到哪

- `plugin/**` 有改动且 PR 的目标分支是 `main` 时，`.github/workflows/plugin-tests.yml` 跑 `uv run pytest plugin/tests -q`（Windows）和一组进程启动用例（Linux）。目标分支不是 `main` 的 PR 不会自动跑，要手动触发：`gh workflow run plugin-tests.yml --ref <分支>`。
- `frontend/plugin-manager` 的 vitest（`npm run test:unit`）和启动 e2e（`npm run test:boot:e2e`）在 `.github/workflows/unit-tests.yml` 里。
- ⚠️ 这几条**没有任何 workflow 会跑**，改了相关代码要自己在本地跑：`npm run check-hosted-tsx`、`npm run test:hosted-script`、`npm run test:hosted:e2e`、`scripts/check-hosted-ui.sh`。
- plugin-manager 的 `vite.config.ts` 在 `VITEST === 'true'` 时关掉 Element Plus 组件自动导入。原因：自动导入会把模板里的 `<el-tabs>` 编译成显式 import，绕过测试里用 `app.component()` 注册的全局桩。别在测试环境把它打开。
- 跑 `check --release --market-release` 的测试必须先清掉 `GITHUB_REPOSITORY`、`GITHUB_REF_NAME`、`GITHUB_REF`，否则本地绿、CI 红。详见 `references/import-and-roots.md`。

## 源码和测试地图

`neko-plugin` skill 会被快照进第三方插件项目，那边没有主仓源码，所以主仓路径的地图放在这里，不放进那边。

源码（改之前先读）：

- manifest 结构和语义规则：`plugin/config/schema.py`、`plugin/config/plugin_toml_semantics.py`
- 运行时注册、子进程、入口导入：`plugin/core/registry.py`、`plugin/core/host.py`、`plugin/core/entry_points.py`、`plugin/core/entry_executor.py`
- 触发执行：`plugin/runs/trigger_service.py`
- 启停和注册表刷新：`plugin/server/application/plugins/lifecycle_service.py`、`registry_service.py`
- UI 查询和 action：`plugin/server/application/plugins/ui_query_service.py`、`plugin/server/routes/plugin_ui.py`、`plugin/core/ui_manifest.py`
- 配置 profile 和热更新：`plugin/server/infrastructure/config_profiles*.py`、`plugin/server/application/config/`
- 运行日志：`plugin/server/routes/logs.py`
- CLI：`plugin/neko_plugin_cli/cli.py` 注册子命令，各命令在 `commands/` 下；`release_cmd.py`、`validate_cmd.py` 是被 `check`/`publish` 调用的内部实现，不单独注册
- Hosted TSX 运行时和类型：`frontend/plugin-manager/src/components/plugin/hosted/`、`plugin/sdk/hosted-ui/index.d.ts`

测试（改宿主代码前先搜）：

| 改动面 | 测试 |
|---|---|
| CLI 打包流程、仓库内插件打包 | `plugin/tests/integration/test_neko_plugin_cli_workflow.py`、`test_neko_plugin_cli_repo_plugins.py` |
| CLI 命令契约 | `plugin/tests/unit/test_neko_plugin_cli_cli.py`、`test_neko_plugin_cli_deps.py`、`test_neko_plugin_cli_public.py` |
| 加载、启动失败 | `plugin/tests/unit/server/test_plugins_lifecycle_service.py` |
| 注册表 | `plugin/tests/unit/server/test_plugin_registry_service.py` |
| 触发执行 | `plugin/tests/unit/server/test_trigger_service.py` |
| 入口参数、超时、SDK、装饰器、router | `plugin/tests/unit/core/`、`plugin/tests/unit/sdk/` |
| manifest 和配置修改 | `plugin/tests/unit/server/test_config_updates.py`、`test_config_validation.py` |
| UI manifest 和查询 | `plugin/tests/unit/server/test_plugin_ui_manifest.py`、`test_plugin_ui_query_service.py` |
| 插件中心的 CLI 路由、源码定位 | `plugin/tests/unit/server/test_plugin_cli_route.py`、`test_plugin_cli_source_resolver.py` |
| 某个内置插件 | `plugin/tests/unit/plugins/test_<插件名>*.py` |
| `neko-plugin` skill 和 agent 指引的文字 | `plugin/tests/unit/test_neko_plugin_agent_guides.py`（快照洁净、子命令、硬约束标题）、`test_neko_plugin_cli_agent_kit.py` |

常用命令：

- 整个插件子系统：`uv run pytest plugin/tests -q`；unit + integration：`uv run pytest -c plugin/tests/pytest.ini plugin/tests/unit plugin/tests/integration -q`
- e2e 要显式打开：`uv run pytest -c plugin/tests/pytest.ini plugin/tests/e2e --run-plugin-e2e -q`（连已启动的实例时加 `PLUGIN_E2E_BASE_URL=http://127.0.0.1:48911/ui`）
- 对内置插件跑仓库规范脚本时要显式传路径，例如 `uv run python scripts/check_async_blocking.py plugin/plugins/<id>`。很多规范脚本默认排除 `plugin/plugins`，因为那里可能放第三方代码。
- hosted UI 全套检查目前只针对 MCP Adapter 的夹具：`scripts/check-hosted-ui.sh`。

## 排查入口

| 症状 | 先看哪里 |
|---|---|
| 用户目录里的插件「刚打开显示加载失败，重载后又好了」 | 是不是有新的导入点没经过 `_import_plugin_module`，或没传 `plugin.toml` 路径 |
| 夹具里「删除没清干净」「重装 409」这类只在测试里出现的怪事 | 夹具的路径根有没有同时覆盖两种读法 |
| 改过元数据复用逻辑后，插件启动成功但一个入口都没有 | 插件 id 冲突后被改名，包内 handler 的 key 还是旧 id，没有回落扫描；对照 `lifecycle_service._read_packaged_isolated_metadata` |
| 未运行的插件，入口的参数 schema 是占位的 | 包里没有可用的 `plugin.meta.json`（缺失、源码改过没重新生成、schema 版本不对）。看插件服务日志里 `packaged plugin metadata` / `plugin source` 开头的行，里面写了回落原因 |
| surface 上每个按钮都 500 `PLUGIN_UI_CONTEXT_QUERY_FAILED` | `@ui.context` 的 provider 缺失或抛错；现在应该降级成警告，看 `context_error` |
| surface 调入口 403 `PLUGIN_UI_ACTION_FORBIDDEN` | 入口没标 `@ui.action`，或 surface 没有 `action:call` 权限 |
| 插件服务日志里 `[MESSAGE FORWARD]` 的 `Content length: 0` | 多半是只带图片的推送，不是丢内容。见 `references/host-boundaries.md` |
| 保存 profile 后某段配置被清空 | 覆盖里留了空表；嵌套层的空表是「替换」不是「不变」。见 `references/install-and-config.md` |

## references 索引

- `references/import-and-roots.md`：改导入、路径解析、CLI 发布检查、打包进分发包的插件范围时读。
- `references/packaged-metadata.md`：改插件发现、入口元数据、`plugin.meta.json`、新装插件自启动时读。
- `references/install-and-config.md`：改安装 / 升级 / 卸载事务、运行时配置、profile 覆盖编辑器时读。
- `references/hosted-ui-host-side.md`：改 hosted TSX 打包器、surface 桥、UI context / action 时读。
- `references/host-boundaries.md`：改宿主和别的子系统的交界（任务大脑、记忆、消息转发日志、回复尾图、知识包市场、市场 CI）时读；也收了几条评审里已经驳回过的意见。
- `references/adjacent-fences.md`：改记忆服务的角色释放（改名 / 删除角色前释放 SQLite 句柄）或创意工坊内容目录占用时读。这两块不属于插件宿主，但插件会打到它们。
