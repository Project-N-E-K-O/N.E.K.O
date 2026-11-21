# 安装 / 升级 / 卸载事务，运行时配置，profile 覆盖编辑器

## 1. 共用的操作锁

`plugin/server/application/plugins/operation_lock.py`。安装、上传并安装、市场替换、启动、配置写入、自启动开关这类会改插件安装状态的操作，都在这把锁下面跑（搜 `@serialized_plugin_operation` 和 `plugin_operation_lock.hold()` 能看到全部）。新增这类操作时也要进这把锁。

- 它是两层：进程内一把先来先服务的锁，加一把跨进程的文件锁。一次获取只算**一个**截止时间，两层共用。分别给预算的话，本地和跨进程都有竞争时会等两遍，名义 20 秒的请求可能在前端已经放弃之后才去改状态。
- 对同一个 asyncio 任务可重入（嵌套调用，比如「上传并安装」里面再调「安装」，不会自己锁自己）。持有者按任务认，新起的任务不算持有者。
- ⚠️ `@serialized_plugin_operation` 包装的函数，拿到锁之后就屏蔽取消。所以**不要在外面套 `asyncio.wait_for`**：超时触发时里面的改动不会停，调用方却以为失败了。需要时限就把截止时间作为参数传进去，在内部分步钳制。`lifecycle_service.start_plugin` 里对 `reload_all` 的处理就是这个写法。

**市场升级：下载和校验在锁外，替换事务在锁内。** 进了锁要重新核对「这次升级是照着哪一行安装记录计划的」：`plugin/server/routes/market_bridge.py` 的 `_market_entry_fingerprint` 把安装记录的 root、渠道、目录名、插件 id、包 id、安装和更新时间、是否已移除、版本、包哈希拼成指纹，和计划时的不一致就以 409 `plugin_upgrade_plan_changed` 拒绝。给安装记录加新字段时，想一下它该不该进这个指纹。

## 2. 卸载和包 profile

- 卸载是一个事务（`plugin/server/application/plugins/installation_transactions/uninstall.py`）：先停插件，把代码目录挪到暂存位置，更新安装来源记录，刷新注册表，最后才提交。中途失败按相反顺序还原。提交之后删不掉的暂存目录留有提交标记，之后由 `retry_deferred_plugin_code_cleanup_sync` 重试清理。
- **卸载不碰包 profile 目录。** 包 profile 现在被当作旧的导出物，不是运行时状态，卸载时原样保留，不暂存也不删除。守这条的测试是 `plugin/tests/unit/server/test_package_profile_independence.py`（安装 → 卸载 → 重装，状态保留、包 profile 不受影响）。
- 安装记录上的 `profile_installed` 是三态（`plugin/server/application/install_source/models.py`）：`None` 表示这一行是在记录 profile 归属之前写下的老行；`True` 表示安装时确实解出过 profile；`False` 表示这个包没有 profile。⚠️ 升级重写这一行时要**沿用**原值，不能把 `None` 塌成 `False`（`install_source/manager.py` 里导入和市场两条写入路径都是这么做的）。改其中一条路径时另一条要一起改。
- 校验「自己造的暂存目录名」的正则，要和 id 的合法字符集对齐。包 id 允许带点（`plugin/neko_plugin_cli/core/install.py` 的 `_SAFE_PACKAGE_ID_RE`）。不对齐的话，`com.example.foo` 这种 id 留下的暂存目录每次启动都被判成不安全、拒绝清理，永久占盘（`uninstall.py` 里的 `_DEFERRED_PROFILE_STAGING_NAME_PATTERN` 就是为此允许点）。

## 3. 运行时配置是清单的一份快照

理解配置链路要先知道这个前提：

- 插件第一次初始化运行时配置时（`plugin/server/infrastructure/config_paths.py` 的 `ensure_plugin_layout_runtime_config`），有 `config.example.toml` 就拷它，**没有就把整份 `plugin.toml` 拷过去**。
- 内置插件里只有 `web_search` 带了 `config.example.toml`。其余插件的运行时配置等于「安装那一刻的清单快照」，插件升级后新增的默认值不在里面。

所以解析生效配置时（`plugin/server/infrastructure/config_resolver.py` 的 `_resolve_plugin_config_core`）：

- 先做 `deep_merge(清单, 运行时配置)`：清单里新增的键会浮出来，已保存的值仍由运行时配置覆盖。效果是「新默认值出现，但不盖掉用户保存过的值」。
- 然后把 `[plugin]` 段强制换成当前清单的 `[plugin]`。所以 `[plugin]` 段永远取当前清单，不受陈旧拷贝影响；profile 也不允许定义顶层 `plugin` 段。

profile 编辑器的基线要用 `GET /plugin/{id}/config/base/effective`（清单默认值合并运行时配置，不含 profile 覆盖），不是只有运行时配置的 `/config/base`。用后者的话，升级后新增的配置项在编辑器里看不到。

## 4. 覆盖合并的语义（后端为准）

`plugin/server/infrastructure/config_merge.py` 的 `deep_merge`：

- 值是 `"__DELETE__"`：删掉这个键。
- 值是带 `__replace__ = true` 的表：整张表替换，不逐键合并。
- 值是**空表**，而基线同名键是表：结果是空表，也就是**清空**，不是「不变」。
- 其余的表逐键递归合并；标量和数组直接覆盖。

⚠️ profile 覆盖是按顶层段逐段合并的（`config_profiles.py`），`deep_merge` 只在顶层段**以下**起作用。所以顶层段**里面**的字段照上面的规则走：段内某个字段写成 `__DELETE__` 会被删掉，段内嵌套表上的 `__replace__` 和空表也生效。不生效的是把标记用在顶层段本身上：整段的值写成 `__DELETE__`、在顶层段这张表上写 `__replace__`，都只是普通数据；顶层段写成空表是无操作，不会清空这一段。

前端 `frontend/plugin-manager/src/utils/configEditor.ts` 里的 `applyProfileOverlay` / `deepMerge` / `replacesBaseTable` 是照着后端写的镜像。改后端合并规则时前端这份要一起改；两边有出入时，「保存后会发生什么」以后端为准。

## 5. profile 覆盖编辑器的一条判据

`frontend/plugin-manager/src/components/plugin/ConfigValueEditor.vue`。整个组件收敛到一条判据：**当前位置是「可继承」还是「替换」**。

- **可继承（对象树）**：`modelValue` 只装覆盖。某个键没被覆盖时它是 `undefined`，显示时回落到基线（`displayValue`），写回时只动覆盖。「重置」等于把这个键从覆盖里摘掉，退回继承。
- **替换（数组；数组项内部；带 `__replace__` 或显式为空的嵌套表）**：覆盖一旦存在，它就是生效值的全部，基线不再逐位、逐字段继承，只在「重置」时用来显式写回。这个状态靠 `replaceSemantics` 属性往下传。

改这个文件前对照这四个踩过的坑：

1. 父层把基线子树塞给子层当 `modelValue`。子层一 `{...props.modelValue}` 就把整段清单默认值固化进了 profile，用户只改了一个字段。
2. 「重置」在可继承位置是删键，在替换位置必须**写回基线值**。数组没有逐位回填，删键等于把那一项从生效配置里抹掉。
3. 重置掉某个嵌套表里最后一个覆盖项，会留下一张空表。后端对嵌套空表是清空（见上一节），存下去整段就没了。所以可继承位置要向上冒泡剪掉空表。
4. ⚠️ 数组显示时拿基线补尾（按覆盖和基线里较长的那个取长度）。结果是用户删掉末项，界面立刻从基线填回来，下次编辑再写回去，**末项永远删不掉**。数组是整体替换：有覆盖就只看覆盖，没覆盖才整份继承基线。

这个组件的 vitest 在 CI 里跑（`npm run test:unit`），布局类的回归靠 `npm run test:boot:e2e`。
