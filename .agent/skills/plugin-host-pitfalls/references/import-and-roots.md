# 导入命名空间、路径根、发布检查、哪些插件进分发包

## 1. 两套导入命名空间

- 清单里的规范写法永远是 `plugin.plugins.<id>:Class`。
- 内置插件（仓库 `plugin/plugins/<id>/`）就按这个名字导入。
- 装在用户目录的插件，`plugin/core/entry_points.py` 的 `normalize_plugin_entry_point` 会把入口改写成顶层 `plugins.<id>:Class`。

`plugin/plugins/__init__.py` 存在，所以 `plugin.plugins` 是普通包。顶层的 `plugins` 则是插件子进程里现造出来的空命名空间（`plugin/core/source_imports.py` 的 `ensure_plugins_namespace`，`__path__` 是空的）。被选中的那个插件按它 `plugin.toml` 所在目录直接从文件加载，然后同时挂在 `plugins.<id>` 和 `plugin.plugins.<id>` 两个名字下。

现行规则：

- **共享的插件根目录不进 `sys.path`。** `plugin/core/registry.py` 的 `_prepare_plugin_import_roots` 是故意留空的；`plugin/core/host.py` 的 `_prepare_child_plugin_import_roots` 还会把继承来的候选根从 `sys.path` 里摘掉。理由：根目录一旦上了路径，没被选中的兄弟插件全都变成顶层模块，用户目录里一个叫 `types.py` 或 `utils/` 的东西就能盖住标准库和宿主模块。
- **所有入口导入走 `_import_plugin_module`，并传 `plugin.toml` 路径。** 现在的调用点是：子进程启动（`host.py`）、`registry._import_plugin_entry_module`、`metadata_scanner`。`config_path=None` 时它就是普通 `importlib.import_module`，没有任何兜底。新增导入点（比如给扩展或别的包类型加载代码）要照这个规矩来。
- 目录名和包名允许差一个连字符：`plugin_package_matches_directory` 认 `my-cool-plugin` 目录对应 `my_cool_plugin` 包，别的差异一律当配置错误。

踩过的坑（#1679）：用户插件「插件管理器刚打开显示失败，重载后恢复」。根因是当时把每个插件根的父目录都塞进了 `sys.path`，内置根的父目录是 `plugin/`，顶层 `import plugins` 被钉到了内置目录，`plugins.<用户插件id>` 找不到。现在的结构就是为了不再有这个问题。

回归测试在 `plugin/tests/unit/core/test_host_plugin_import_fallback.py` 和 `plugin/tests/unit/core/test_source_only_imports.py`。改导入逻辑后跑这两个文件。

相关的一条：`host._find_project_root` 从 `plugin.toml` 往上找不到仓库根时，按宿主文件 `plugin/core/host.py` 自身的位置推出仓库根，而不是从插件的 `plugin.toml` 往上数固定层数。按层数数的话，用户目录里的插件会数到用户的文档目录，而这个值会被插到子进程 `sys.path` 最前面。

## 2. 路径根：两种读法

`plugin/settings.py` 里和用户插件有关的根：

| 函数 | 环境变量 | 模块属性（导入时算好） |
|---|---|---|
| `get_user_plugin_exec_root()` | `PLUGIN_CONFIG_ROOT` | `USER_PLUGIN_EXEC_ROOT`、`USER_PLUGIN_CONFIG_ROOT`（同一个值的旧名字） |
| `get_user_package_profiles_root()` | `PACKAGE_PROFILES_ROOT` | `USER_PACKAGE_PROFILES_ROOT` |
| `get_user_plugin_packages_root()` | `PLUGIN_PACKAGES_ROOT` | `USER_PLUGIN_PACKAGES_ROOT` |
| `get_plugin_state_root()` | 无 | `PLUGIN_STATE_ROOT` |

几条要点：

- 用户插件的**代码**装在执行根（默认是用户数据根下的 `.neko-plugin-installations/plugins`），插件的**持久状态**在状态根（用户数据根下的 `plugins`）。两者必须分开，`ensure_plugin_exec_state_roots_separated` 就是用来在写包之前拦住重叠的。安装、升级、回滚、卸载的代码绝不能把状态根当成替换目标。
- 设了 `PLUGIN_CONFIG_ROOT` 之后，另外两个根在没单独设置时默认落在它的同级目录。
- `PLUGIN_CONFIG_ROOTS` 把用户根和内置根都列出来，顺序是用户根在前。这只是运行时找插件的优先级。

⚠️ **两种读法都有代码在用**：`plugin/server/application/plugin_cli/` 读的是模块属性（`paths.py` 每次读 `settings.USER_*`，`service.py` 还在导入时又抄了一份到 `_INSTALL_*`）；卸载事务、安装来源、布局迁移调的是 `get_user_*_root()` 函数。

所以写夹具或端到端脚本时：

- 只 `monkeypatch.setattr(plugin_settings, "USER_PACKAGE_PROFILES_ROOT", tmp)`：调函数的那些路径仍然读环境变量，会指向真实用户目录。
- 只设环境变量：模块属性是导入时的旧值，读属性的那些路径不会变。
- 稳妥做法是两样都做，然后在夹具里自检，例如断言 `plugin_settings.get_user_package_profiles_root() == tmp.resolve()`，并确认被测代码实际用到的那个值也在临时目录下。

踩过的坑：一个夹具只改了模块属性，删除流程从真实根找 profile，找不到就跳过，结果看起来是「删除没清 profile、重装 409」，查了半天产品代码，其实是夹具没打对。现有测试里只改属性的夹具（如 `plugin/tests/integration/test_market_bridge_e2e.py`、`plugin/tests/unit/server/test_plugin_cli_route.py` 里的 `_patch_plugin_cli_settings`）能过，是因为它们的用例没走到读环境变量的分支，别照抄去测别的路径。

## 3. CLI 发布检查会回落读 CI 环境变量

`plugin/neko_plugin_cli/commands/release_cmd.py` 的市场发布检查，在参数没给的时候会读 `GITHUB_REPOSITORY`（判断市场仓库名）和 `GITHUB_REF_NAME`（和 `plugin.toml` 的 version 比对）。

⚠️ 后果：跑 `check --release --market-release` 的测试如果不清这些变量，本地全绿、CI 全红。CI 的 PR 运行里 `GITHUB_REF_NAME` 是 `<PR 号>/merge`，`GITHUB_REPOSITORY` 是本仓库，检查会拿它们去和夹具插件比，报「release tag 和 version 不一致」「市场仓库名不对」。

做法：照 `plugin/tests/unit/test_neko_plugin_cli_init.py` 里那个 autouse 夹具，把 `GITHUB_REPOSITORY`、`GITHUB_REF_NAME`、`GITHUB_REF`、`NEKO_PLUGIN_MARKET_SECURITY_CHECKS` 清掉；需要这些变量的用例自己显式设。

本地复现 CI 的红：带上这两个环境变量再跑那个测试文件。

## 4. 哪些插件会进分发包

市场下载的插件不会被打进桌面分发包，有四层各自独立的保证：

1. 安装位置不同。市场插件装在用户的执行根，打包只读仓库里的 `plugin/plugins`。
2. `scripts/prepare_nuitka_plugins.py` 的 `_skip_reason` 只收 git 跟踪的顶层目录，没跟踪的一律跳过，原因记在 manifest 的 `skipped_entries` 里。
3. 编译完之后，`prepare_nuitka_plugins.py install` 把分发目录里的 `plugin/plugins` 整个换成暂存内容，不是合并。
4. `scripts/check_nuitka_dist.py` 的 `_MARKETPLACE_ONLY_PLUGIN_IDS` 列了只在市场发布的插件 id，它们出现在分发目录里就报 `marketplace-only plugin bundled`。

⚠️ 一个缺口（读代码得出，没有对分发产物做过实测）：被跳过的目录只写进了 `skipped_entries`，没有进 `excluded_modules`，而构建参数里有 `--include-package=plugin`。所以**物理上躺在 `plugin/plugins/` 下、没被 git 跟踪的本地目录，它的 `.py` 仍可能被编译进可执行文件**（源码和资源不会出现在分发目录的 `plugin/plugins` 里，运行时也加载不到它）。市场插件不受影响，受影响的只有开发机上的本地残留。在本机打正式包之前，把 `plugin/plugins/` 下不该发的本地目录挪走。

把一个内置插件移出仓库的既定做法：删掉入库的文件，再把整个目录加进 `.gitignore`（和已有的那几个本地安装插件目录放一组）。开发机上的残留目录由开发者自己处理。

移出仓库的插件如果还要在市场发布：`.github/workflows/plugin-market-verify.yml` 和 `plugin-market-release.yml` 是给**所有**市场插件复用的 `workflow_call`，里面不许出现针对某个插件 id 的特判。插件需要额外的构建产物，就在插件自己的仓库里准备好。
