# 插件写路由守卫、依赖同步、入口元数据、FastAPI 路由树

插件开发本身的规范见 `.agent/skills/neko-plugin/`。这里只记宿主这侧容易被反复质疑的既定规则。

## 1. 插件服务器写路由的守卫档位

设计、两档 route class 各管哪些路由、NAS / 代理下的信任取舍，都写在 `docs/design/security/local-mutation-auth.md`，实现在 `plugin/server/infrastructure/mutation_auth.py`。动之前读那份文档。最常被重新提起的三点：

- 新增写路由**按调用方选档位**：插件页面自己会直接调用的用 `PluginPageMutationGuardedRoute`（可信来源即放行，token 可选，带了就必须正确）；只给插件管理器和 CLI 用的用 `PluginMutationGuardedRoute`。
- 市场里已发布的插件页面不带 token，所以「插件页面路由强制 token」不是可以直接做的安全加固。更严的公网部署用 `NEKO_PLUGIN_PAGE_MUTATION_REQUIRE_TOKEN=1`。
- 只靠 hostname 兜底、又没有 `Sec-Fetch-Site` 头的请求是放行的。自动评审对这一点会给出互相矛盾的意见（一边要求缺头拒绝，一边要求别拒合法插件），按文档里的现行规则回复，不要来回改。

## 2. `neko-plugin` 依赖同步（`plugin/neko_plugin_cli/commands/deps_cmd.py`）

- **本项目强制用 uv。** 往 `vendor/` 装依赖一律是 `uv pip install --python <目标解释器> --target <目录>`。这是 uv 自己的安装器，不是 pip，目标解释器里不需要有 pip，读的是 uv 的配置。
- 只有找不到 uv（先看 `UV` 环境变量，再看 PATH）时才回退到目标解释器的 `python -m pip`，并在前面打中英日三语的醒目警告，所有输出之后再提醒一次。uv 安装失败**不**回退 pip。
- 评审提「uv 不读 pip 的配置文件或 `PIP_INDEX_URL`，可能造成依赖来源不一致」时，回复「以 uv 配置为准」，不改。以前做过一层「对照 pip 设置」的逻辑，自动评审在上面十几轮都收敛不了，已经整层删掉，不要再加回来。
- uv 是从工作目录找项目配置的，而 `uv run --project` 不会把项目传下去。做法是给 `uv pip install` 传 `--project <N.E.K.O 根目录>`，**不改工作目录**。改工作目录会连带弄坏相对路径的 requirement、`--python`、PATH 里的相对项和 `UV_*` 设置。
- `vendor/` 的替换是事务式的：先装进 staging 目录，再把旧 `vendor/` 改名成备份（改名前先写一个 pending 标记），最后把 staging 改名成 `vendor/`；失败就把备份改回去。
- ⚠️ `vendor/` 是链接、junction 或挂载点时**原地安装**，并打警告说明出错无法回滚。不要改成直接拒绝，那相对以前的行为是回退。带 `--clean` 时先装进 staging，成功后才清空回填。
- ⚠️ 打包规则里**不要排除 `vendor/bin`**。插件作者自己放在那里的文件会被漏掉。
- 同步过程产生的临时目录名字集中在 `plugin/utils/source_paths.py` 的 `VENDOR_SYNC_GLOBS` / `is_vendor_sync_path`，build / pack / publish / validate 等共用这一份，不要各写各的。
- 本机验证的坑（Windows）：在很深的临时目录里建带 pip 的虚拟环境，会因为路径长度上限把 pip 装坏，端到端验证换短路径。不要靠改 `LOCALAPPDATA` 之类的环境变量来隔离 `uv run`，缓存和数据目录会建到假路径上。

## 3. 插件入口元数据

打包元数据（`plugin.meta.json`）的 schema 规则、`/plugins` 接口里「已注册 handler 优先、静态预览补剩下的」这条，归在插件宿主那份文档里（`.agent/skills/plugin-host-pitfalls/`）。这里只补它没有的两条：

- 配置文件里的 `[[entries]]` 表只在服务端生效，**插件子进程看不到**：子进程里的入口元数据来自 `collect_entries()` 和成员扫描（`plugin/core/host.py`），只有装饰器写的那份。想让配置里的入口契约在子进程里生效，得把生效后的契约随配置发进子进程，不能让子进程自己读 `plugin.toml`（那样拿不到用户的运行时覆盖）。
- 配置声明的入口字段统一经 `plugin/core/registry.py` 的 `_normalized_entry_declaration` 规范化，`scan_static_metadata` 和入口预览共用这一个函数。不要在别处再写一套类型转换。

## 4. FastAPI 路由树

- ⚠️ 仓库现在锁定的 FastAPI 版本里，`include_router` 不再把子路由复制进父路由的 `routes`，而是放一个表示「被包含的路由器」的节点。route class 在运行时照常生效，只是**直接遍历 `router.routes` / `app.routes` 看不到子路由**。写成 `for route in router.routes` 的结构测试会得到空集，然后「全部满足」地通过或莫名失败。
- 做法：测试里检查路由一律用 `tests/fastapi_routes.py` 的 `iter_routes(routes)`。它在新旧两种布局下都能用；要判断 route class 时，元素可能是带上下文的包装，真实路由用 `getattr(item, "route", item)` 取（例子：`plugin/tests/unit/server/test_plugin_mutation_auth.py`）；取路径用同文件的 `effective_path()`（WebSocket 路由在树形布局下 `path` 是空的）。

## 5. PR 的检查跑在「PR 分支加最新 main」的合并结果上

本地绿、CI 红，而且本地怎么都复现不了时，先看 PR 开出来之后 main 上合了什么，尤其是依赖升级。把 main 合进分支再跑。第 4 节那个坑就是这样暴露的：分支上还是旧版 FastAPI，CI 的合并结果里已经是新版。

## 6. 公共知识库的边界

- 知识库是 Memory Server 下的独立子系统（`knowledge/` 包，路由在 `app/memory_server/knowledge_routes.py`）。Main 这侧只有 HTTP 薄层：`main_logic/public_knowledge.py`、`main_routers/public_knowledge_router.py`；插件侧经 `plugin/server/routes/knowledge_bridge.py`。
- `scripts/check_module_layering.py` 禁止 `main_logic/`、`main_routers/`、`app/main_server/` import `knowledge`。需要知识库的数据就走 HTTP，不要为了省一次调用去 import。
- 架构说明见 `docs/architecture/knowledge-architecture.md`。
