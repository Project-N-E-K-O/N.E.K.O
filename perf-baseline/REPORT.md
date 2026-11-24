# N.E.K.O 插件系统性能基线报告（debug 级）

> ⚠️ **测试环境是 Linux 虚拟机，不是 Windows。** 绝对数字只能当参考，热点的排序和代码位置有参考价值。可能受平台影响的结论都标了 🐧，标注含义见 §1.3。
> ⚠️ **虚拟机没有显卡，浏览器走的是软件渲染（SwiftShader / CPU 合成）。** 帧率只能说明"主线程有没有卡住"，不代表真机帧率。

- 被测版本：`Project-N-E-K-O/N.E.K.O` main **f47f73729615f828da14be2d7e47016e38d57939**（2026-11-24 08:57 +0800）；桌面壳 `N.E.K.O.-PC` main **8ed0bbb3ef3be14bf98926e701cdd77340ad94d3**（只用于"PC 壳模式"，见 §8）。
- 测量日期：2026-11-24。只观察、只记录，没有改任何产品代码。
- **测量脚本**：放在独立分支 [`perf/plugin-baseline-scripts`](https://github.com/Project-N-E-K-O/N.E.K.O/tree/perf/plugin-baseline-scripts)，提交 **87d6de3e6658ad583ed12348787f3b94f6e21b57**。这个分支基于 main **f47f73729615f828da14be2d7e47016e38d57939**（就是本报告测的提交），只新增了 `perf-baseline/` 目录，**不开 PR、不合并**，用完由维护者删除。脚本跨平台（Windows / Linux），Windows 实机复测的步骤见分支里的 `perf-baseline/README.md`。
- 隐私：每次测量都用全新的临时 HOME 目录，角色和插件数据全是合成的；没有调用任何模型 API。唯一的外部网络请求是内置插件 web_search 启动时自带的国家查询（`plugin/plugins/web_search/__init__.py:81` 请求 ipwho.is），不是模型 API。附件里没有密钥或 token：接口 URL 里带的本机临时 bridge token 已经统一替换成 `<redacted>`。

---

## 0. 结论速览（按影响从大到小）

| # | 结论 | 影响 | 证据 | 代码位置 |
|---|---|---|---|---|
| 1 | **没有 `plugin.meta.json` 的插件，每次启动都会另起一个 Python 解释器扫元数据**，单个 0.3 s CPU。这个扫描进程约 76% 的 CPU 花在导入整个 `plugin.server.application.plugins` 包（连带 FastAPI 和 pydantic），而它本身只需要 `_worker_main`。插件多的时候，这是热启动里最大的一块：10 个自启插件的热启动从 1.18 s 拖到 1.75 s，单个插件启动耗时 120 ms → 455 ms 🐧 | 高 | §2.4、§2.5 py-spy | `plugin/server/application/plugins/metadata_scanner.py:44-52`（引导代码 `from plugin.server.application.plugins.metadata_scanner import _worker_main`）；包入口 `plugin/server/application/plugins/__init__.py:1-7` 无条件导入 lifecycle_service、hot_reload_service 等；触发条件在 `lifecycle_service.py:1430-1478`；只有已经存在的打包元数据才会回写（`lifecycle_service.py:393-405` → `packaged_metadata_needs_rebuild`） |
| 2 | **模拟 Windows 的 spawn 启动方式后，插件子进程启动慢一倍多，内存翻 2.4 倍。** Linux 默认用 fork，子进程和父进程共享已导入的模块；Windows 只能 spawn，每个子进程都要从头导入。10+1 个插件热启动 1.75 s → 2.75 s，单插件 455 → 996 ms；整棵进程树 PSS 约 344 MB（fork）→ 826 MB（spawn）。真机 Windows 上大概率还要更慢（杀毒扫描、文件系统） 🐧🐧 | 高（只影响 Windows） | §2.6 | `plugin/core/host.py:2066-2082`（用默认 multiprocessing 上下文） |
| 3 | **能力静态分析（capability analysis）冷启动时在插件服务进程里吃约 1.3 s CPU**（21 个插件），从启动第 1.5 s 跑到第 4.1 s，和插件拉起、面板首批请求抢 GIL。冷启动时第一个分析任务的墙钟时间 0.85–1.3 s，就是被抢的结果 | 中 | §2.5 py-spy（`CapabilityAnalysisService._run` 261 个样本 ≈ 1.3 s） | `plugin/server/application/plugins/capability_analysis_service.py:108-113`（单线程池）、`:193`（`_run`）；主要耗在 `plugin/core/capability_analysis.py:278`（`_references`）、`:572`（`_emit_references`）、`ast.parse` |
| 4 | **插件服务光导入就要 0.38 s（热）/ 0.87–0.91 s（冷）**，占冷启动的三分之一。其中 FastAPI + pydantic 建模型约 0.12 s，`plugin.server.routes` 0.12 s，`console_proxy` 0.06 s | 中 | §2.7 `-X importtime` | `plugin/server/http_app.py` 顶部导入；`plugin/server/routes/console_proxy.py` |
| 5 | **面板页面切换的时间几乎全是设计好的过渡动画**：多数页 180–190 ms、带分区动画的页 360–400 ms，后端接口只占 40–75 ms，前端没有长任务。只有插件详情页每次进入都有约 80 ms 的长任务（首次 160 ms） | 低到中 | §4 | `src/motion/policy.ts:30-33`（page 220 ms、section/card 300 ms）、`src/components/layout/AppLayout.vue:81-85`；详情页 `src/views/PluginDetail.vue` |
| 6 | **离开插件详情页时会多发一个 `GET /plugins/undefined`（404）**：`pluginId` 的 watch 用了 `flush: 'sync'`，路由离开时组件还没卸载，`route.params.id` 已经变成 undefined，于是又触发了 `loadDetail()`。每次离开一次，两种模式都能复现 | 低（多一次请求、多一次错误路径） | §4.3 | `frontend/plugin-manager/src/views/PluginDetail.vue:267`、`:655-680` |
| 7 | **首屏会把 8 种语言的 authoring 等文案整包加载**（304 KB 的 `authoring-*.js`，占首屏字节的 27%）：每个语言文件都 `import { authoringMessages } from '../authoring'` 再取其中一种，打包器摇不掉其余 7 种。首屏总共 53 个请求、JS 1.02 MB、CSS 123 KB，没有 gzip。本机 localhost 下面板 0.11 s 就能操作，所以眼下影响小 | 低 | §3 | `src/i18n/locales/en-US.ts:5,13`（其余 7 个语言文件同理）、`src/i18n/authoring.ts` |
| 8 | **装卸插件时插件服务的 RSS 会上涨，但会封顶**：前 10 轮 +17 MB，之后每轮约 +50 KB（60 轮共 +19.1 MB）。线程数、句柄数不变，Python 堆（tracemalloc）20 轮只涨 0.85 MB。判断是分配器高水位，不是泄漏 | 低 | §6.2 | tracemalloc 前几名见 §6.2（`registry_service.py:772`、`monitoring/metrics.py:345`、`message_plane/ingest_server.py:30`） |
| 9 | 冷启动第一次调 `get_config_manager()` 会跑一整套配置迁移（约 110–130 ms），记在"自启分组"这一步里；另外 `ConfigManager` 会被反复新建（一次热启动 34 次，一次 5 分钟的内存场景 168 次） | 低 | §2.5 | `plugin/server/infrastructure/autostart_approvals.py:60-70` → `utils/config_manager/__init__.py:268-276`；`plugin/settings.py:99` → `utils/config_manager/__init__.py:291-295` |
| 10 | 前端没有发现内存泄漏：连续切页时 JS 堆冲到 61 MB、DOM 节点 1.9 万，静置 15 s 后回到 16 MB、486 个节点；堆快照前后对比（切页后→装卸后）总量 23.46 → 23.42 MB | — | §6.3 | — |

PC 壳（Electron 41）模式只拿到了"打开面板到可交互"这一项（中位 207 ms）。在 Xvfb、没有窗口管理器的虚拟桌面里，面板窗口被节流到约 1 帧/秒，帧率和切页耗时没法测，见 §8。

---

## 1. 测试环境

### 1.1 机器

| 项 | 值 |
|---|---|
| 类型 | **KVM 虚拟机**（`systemd-detect-virt` = kvm），**不是 Windows** |
| CPU | Intel Core Ultra 7 265K，虚拟机分到 12 核（`nproc` = 12） |
| 内存 | 29 GiB（`free -h`：total 29Gi），无 swap |
| 显卡 | **没有**；Chromium 用 SwiftShader 软件渲染 |
| 系统 / 内核 | Ubuntu 24.04.5 LTS / 6.8.0-149-generic |
| 文件系统 | ext4（虚拟磁盘） |
| Python | 主仓 venv 是 CPython **3.11.18**（由 `uv` 按 `pyproject.toml` 选定；系统自带的 3.12 没用） |
| Node | v22.23.4 |
| 浏览器模式 | Playwright 1.64.1 自带的 Chrome for Testing **156.0.8078.4**（headless shell） |
| PC 壳模式 | Electron **41.2.0**（Chromium **146.0.7680.179**），`electron-forge start` 开发模式，Xvfb 1920×1080×24 |
| 前端构建 | `frontend/plugin-manager` 用 `vite build --sourcemap` 构建（加 sourcemap 只多了 .map 文件，运行的代码不变），用来把剖析结果对回 `.vue`/`.ts` 源文件 |

### 1.2 插件三档

| 档 | 内容 | 自启 / 运行中的插件子进程 |
|---|---|---|
| tier0（0 个） | 主仓的本地副本，删掉 `plugin/plugins/` 下所有内置插件目录（只在本机副本里删，没动仓库） | 0 |
| tier1（自带） | main 自带的 11 个内置插件 | 1（只有 web_search 自启） |
| tier2（约 10 个） | 内置 11 个，加上用户目录里 10 个 `memo_reminder` 的改名副本（`perf_memo_1..10`，`auto_start = true`，**删掉了 plugin.meta.json**，相当于手动拷进来或开发中的插件） | 11 |
| tier2-keepmeta | 同 tier2，但副本**保留** `plugin.meta.json` | 11 |

### 1.3 平台标注

- 🐧 进程拉起：Linux 用 **fork** 起插件子进程（Python 3.11 在 Linux 上的默认做法），Windows 只能 **spawn**。§2.6 用 spawn 模拟了一次，差别很大。
- 🐧 文件系统：ext4 加 Linux 页缓存。Windows 的 NTFS 和杀毒实时扫描会让"冷启动 / 导入 / 扫描子进程"更慢。
- 🐧 GPU / 帧率：软件渲染，帧率只看主线程有没有卡。
- 🐧 窗口：PC 壳在 Xvfb、没有窗口管理器的环境下跑，窗口叠放和节流行为跟真实桌面不一样（§8）。
- 🐧 CPU：12 核的新 CPU，单线程性能明显比普通笔记本强。

---

## 2. 插件服务启动

### 2.1 方法

- **独立模式**：`plugin/user_plugin_server.py`，用包装脚本 `perf_boot.py` 起。它通过 import 钩子给关键函数计时，不改源码，再用 `runpy` 执行原入口。计时点：
  - 进程拉起：从 shell 记下 `T0` 到 Python 主模块开始执行，包含 `uv run` 和解释器启动；
  - 导入和建 app：主模块开始执行到 uvicorn `Server.startup`；
  - lifespan 各步：迁移和对账、消息平面、注册表刷新（发现插件、读 manifest）、自启分组、批量启动插件（单个插件取 `_start_plugin_under_lock`）、StartupReconciler、默认模型槽；
  - 静态分析：每个插件的 `CapabilityAnalysisService._run`。
- 就绪的定义：`health` 指 `/health` 第一次返回 200；`all-running` 指 `/plugins` 里 running 的数量达到这一档的预期。
- **冷启动**：换一个全新的数据目录，清掉 `plugin/`、`utils/`、`config/` 下的 `__pycache__`，`echo 3 > /proc/sys/vm/drop_caches` 清页缓存。**热启动**：沿用同一个数据目录，缓存都是热的。
- 样本数：每档冷启动 5 次、热启动 10 次（tier2 热启动的 `/health` 有 1 次没采到，n=9）。spawn 和 keepmeta 两个变体：冷 3 次、热 10 次。
- 剖析：py-spy 0.4.3（200 Hz，`--subprocesses`，只记 CPU 活跃样本），每档冷、热各录 1 次，附件里有火焰图（`flame-*.svg`）。

### 2.2 独立模式总耗时（毫秒，中位 / P90 [最小–最大]）

| 档 | 冷：/health | 冷：全部插件运行 | 热：/health | 热：全部插件运行 |
|---|---|---|---|---|
| tier0 | 1546 / 1571 [1489–1571] | 1607 / 1642 | 628 / 642 [607–645] | 674 / 690 [647–692] |
| tier1 | 2518 / 2633 [2406–2633] | 2969 / 3253 [2621–3253] | 934 / 982 [869–993] | 1001 / 1057 [942–1064] |
| tier2 | 2923 / 3267 [2777–3267] | 3251 / 3682 [3113–3682] | 1665 / 1819 [1543–1914] | 1752 / 1893 [1628–1991] |
| tier2-keepmeta | 3054 / 3083 [2981–3083] | 3376 / 3445 | 1104 / 1144 [1048–1153] | **1181 / 1225** [1125–1226] |
| tier2 + spawn 🐧 | 4051 / 4084 [4020–4084] | 4330 / 4365 | 2687 / 2737 [2649–2738] | **2752 / 2803** [2731–2805] |

注意：`/health` 要等 lifespan 全部跑完才返回（插件批量启动包含在内），所以"服务就绪"和"插件都起来了"只差几十毫秒。能力静态分析在后台线程里继续跑，tier2 冷启动要到第 **4105 ms**（P90 4539）才结束，热启动到第 1189 ms。

### 2.3 分阶段拆解（毫秒，中位，括号里是 P90）

| 阶段 | tier0 冷 | tier0 热 | tier1 冷 | tier1 热 | tier2 冷 | tier2 热 | tier2-keepmeta 热 | tier2 spawn 热 |
|---|---|---|---|---|---|---|---|---|
| 进程拉起（uv + 解释器） 🐧 | 221 (226) | 41 (43) | 212 (215) | 40 (42) | 120 (213) | 36 (41) | 41 | 39 |
| 导入 + 建 app | 1152 (1161) | 520 (524) | 1155 (1179) | 524 (531) | 1201 (1341) | 577 (609) | 521 | 520 |
| └ 其中 `import plugin.server.http_app` | 872 | 380 | 883 | 383 | 910 | 420 | 381 | 380 |
| lifespan 合计 | 136 (142) | 39 (41) | 1124 (1247) | 343 (370) | 1548 (1861) | 1070 (1231) | 508 | 2094 |
| └ 迁移和对账 | 8 | 7 | 9 | 6 | 5 | 3 | 7 | 7 |
| └ 消息平面（ZeroMQ） | 19 | 12 | 19 | 11 | 18 | 10 | 11 | 11 |
| └ 注册表刷新（发现、读 manifest） | 3 | 1 | 48 | 28 | 62 | 40 | 44 | 38 |
| └ 自启分组（含首次配置迁移） | 0 | 0 | 109 | 20 | 121 | 32 | 30 | 30 |
| └ 批量启动插件 | — | — | 910 (961) | 256 (287) | 1323 (1606) | 961 (1070) | **388** | **1981** |
| └ 单个插件启动（中位 / 最大） | — | — | 837 | 207 | 560 / 673 | 455 / 486 | **120 / 208** | **996 / 1018** |
| └ StartupReconciler | 3 | 3 | 12 (119) | 10 | 16 | 12 | 20 | 12 |
| └ 默认模型槽 | 93（首次） | 8 | 5 | 0.3 | 5 | 0.4 | 0.8 | 0.2 |
| 静态分析：首个开始到最后结束 | — | — | 1775 | 14 (265) | 2602 (2901) | 488 (1039) | 183 | 2031 |

怎么读这张表：
- 冷热差别主要在"导入"（磁盘 I/O 和 `.pyc` 编译，0.52 → 0.9–1.2 s）和"进程拉起"（uv 冷启动 0.2 s）。
- 插件多了以后，热启动的增量几乎全在"批量启动插件"。keepmeta 和 tier2 唯一的区别是有没有 `plugin.meta.json`，批量启动差了 **573 ms**，单个插件差了 **335 ms**，这就是 §2.4 说的扫描子进程。
- tier1 冷启动单个插件要 837 ms：web_search 启动时会联网查一次国家（ipwho.is），这个数字可能受网络影响。

### 2.4 热点：每个插件启动都起一个扫描用的 Python 子进程

**现象**（py-spy，tier2 热启动，附件 `pyspy.json`、`flame-t2-warm.svg`）：
- 插件服务主进程只有 260 个样本（1.3 s CPU），但旁边还有 10 个 `python -c "import os,sys;…;from plugin.server.application.plugins.metadata_scanner import _worker_main;…"` 子进程，**每个 54–59 个样本（约 0.3 s CPU），合计约 2.8 s CPU**，是主进程的两倍多。
- 拆开一个扫描子进程的样本（59 个）：45 个在 `plugin/server/application/plugins/__init__.py`，它导入 lifecycle_service 和 hot_reload_service，34 个在 `fastapi/__init__.py`，29 个在 pydantic 建模型。真正干活的 `metadata_scanner.py` 只有 14 个。
- 主进程这边，`_terminate_worker_tree` 用 `psutil.Process.children(recursive=True)` 收尸，这一步要扫遍 `/proc` 下的所有进程，热启动里占了 **34 个样本（约 170 ms）**，进程多的机器上会更慢（`metadata_scanner.py:124`）🐧。

**为什么会触发**：`start_plugin` 先读打包好的元数据（`plugin.meta.json`），读不到或者不可用，就起一个隔离进程扫一遍（`lifecycle_service.py:1430-1478`）。只有元数据文件已经存在、只是过期或者是在别的机器上打包的，扫完才会回写，下次就不用再扫（`_snapshot_package_tree_for_rebuild` → `packaged_metadata_needs_rebuild`，`lifecycle_service.py:393-405`）。**本来就没有 `plugin.meta.json` 的插件**（手动拷进来的、开发中的、源码安装的）**每次启动都要扫**。实测 tier2 每次热启动都起了 10 个扫描子进程，扫完插件目录里也没有生成 `plugin.meta.json`。

**数据对比**（tier2 热启动，n=10）：批量启动 961 → 388 ms，单个插件 455 → 120 ms，全部运行 1752 → 1181 ms（keepmeta）。

**可以考虑的方向**（只是建议，没有验证）：让扫描子进程的入口不经过 `plugin.server.application.plugins/__init__.py`（比如把 `_worker_main` 放到一个不触发包初始化的模块里，或者把包的 `__init__` 改成延迟导入），预计每个子进程能少 70% 左右的 CPU；或者对没有元数据的插件，扫完以后在宿主自己的缓存目录里留一份。

### 2.5 热点：主进程 CPU 都花在哪（py-spy，tier2 冷启动，632 个样本 ≈ 3.2 s CPU）

| 包含时间（样本数，200 Hz） | 函数 |
|---|---|
| 261（≈1.3 s） | `CapabilityAnalysisService._run`（`capability_analysis_service.py:193`） |
| 240 | └ `analyze_capabilities`（`plugin/core/capability_analysis.py:1402`） |
| 129 | └└ `_references` / `scope`（`capability_analysis.py:278-279`） |
| 125 | └└ `_emit_references`（`capability_analysis.py:572`） |
| 59 | └└ `ast.parse` |
| 46 | └└ `_PermissionCollector.scan` |
| 38 | └└ `_matches`（`capability_analysis.py:959`，自身时间 38） |
| 228 | `<module> user_plugin_server.py`（导入链，见 §2.7） |
| 31 | `_get_autostart_plugin_ids_sync` → `is_autostart_approved` → 首次 `get_config_manager()` 触发配置迁移（`migrate_openclaw_url_port` 13，`migrate_config_files` 11） |
| 30 | `create_plugin_host` |

自身时间前几名：importlib 97、`dataclasses._create_fn` 83（导入时建 dataclass）、`ast.parse` 59、pydantic `match_type` 54、字节码编译 43、`capability_analysis._matches` 38。

静态分析在单线程池里排队跑（`capability_analysis_service.py:108-113`），和事件循环同在一个进程，会抢 GIL。冷启动时排在第一个的插件，分析墙钟时间要 0.85–1.3 s，后面的每个只要 27–250 ms（`lifekit` 281 ms、`music_pusher` 156 ms、`mcp_adapter` 123 ms 最慢），说明第一个任务大部分时间在等 GIL。用户如果在冷启动后几秒内打开面板，接口响应会受影响（这一点没有单独测）。

### 2.6 🐧 用 spawn 模拟 Windows 的子进程启动方式

做法：包装脚本里先调 `multiprocessing.set_start_method('spawn')`，再启动服务。代码里用的是默认的 multiprocessing 上下文（`plugin/core/host.py:2066-2082`），所以这一步就能切换启动方式，不用改源码。

| tier2 热启动（n=10） | fork（Linux 默认） | spawn（Windows 只能用这种） |
|---|---|---|
| 全部插件运行 | 1752 ms | **2752 ms** |
| 批量启动 | 961 ms | 1981 ms |
| 单个插件启动 | 455 ms | 996 ms |
| 进程树 PSS 合计 | 344 MB（keepmeta 那组的数据） | **826 MB**（14 个进程，RSS 合计 1061 MB） |

fork 出来的子进程和父进程共享已经导入的模块页（每个 `perf_memo` 子进程独占内存 USS 只有约 17.5 MB）；spawn 出来的每个子进程都要自己导入一遍框架。这一节的数字只说明方向，Windows 真机还要叠加杀毒扫描和 NTFS 的开销，**必须到 Windows 上复测**。

### 2.7 导入耗时（`python -X importtime`，热，前几名累计）

| 累计 ms | 模块 |
|---|---|
| 381 | `plugin.server.http_app`（总共 1265 个模块，自身时间合计 648 ms，子进程的也算在里面） |
| 124 | `plugin.server.routes` |
| 117 | `fastapi`（其中 `fastapi.openapi.models` 自身 48 ms，是 pydantic 建模型） |
| 103 | `plugin.server.lifecycle` |
| 57 | `plugin.server.routes.console_proxy`（`httpx` 56） |
| 54 | `plugin.server.application.plugins`（扫描子进程导入的也是这个包） |
| 31 | `plugin.server.routes.plugin_projects`（`market_bridge` 24） |
| 28 | `uvicorn` |
| 23 | `rich.progress` |

### 2.8 浏览器模式全栈启动（launcher.py）

`launcher.py` 会拉起主服务（48911）、记忆服务、agent 服务。**正式运行时插件服务是 agent 进程里的一个线程**（`app/agent_server/plugin_host.py:107-198`，`_start_embedded_user_plugin_server`），不是独立进程。从拉起开始，到 48911 和 48916 都能响应：

| 档 | n | 中位 | P90 | 范围 |
|---|---|---|---|---|
| tier1 | 5 | 4038 ms | 4248 | 3874–4248 |
| tier2 | 5 | 4065 ms | 4114 | 3961–4114 |

（每次都清页缓存、换全新数据目录，`__pycache__` 没清。）
**契约点**：全新数据目录第一次启动时，主服务处于"存储位置待选择"的受限状态，面板入口 `/api/agent/user_plugin/dashboard` 返回 409（`storage_startup_blocked`）。测试脚本先调 `POST /api/storage/location/select` 选当前目录才能继续。真实用户第一次启动也是这个流程。

---

## 3. 插件管理面板：首次打开到可交互

### 3.1 方法

- 浏览器模式：每次都开一个全新的 Chromium 上下文（HTTP 缓存是空的），访问 `http://127.0.0.1:48916/ui/`（48911 上锤子按钮打开的也是这个地址，经主服务 307 跳转）。
- 页面里预先注入计数脚本：统计还没结束的 fetch/XHR、DOM 变动、长任务（PerformanceObserver）。**"可交互"的定义**：侧栏导航已经渲染，并且连续 500 ms 没有进行中的请求、没有 DOM 变动、没有长任务；取最后一次活动的时间和 FCP 中较晚的那个。另外记录同一个上下文里刷新一次的"热打开"时间。
- 每档 10 次，第 1 次同时录 Chrome trace（devtools.timeline 加 V8 采样），附件里有摘要（`trace-browser-*.json`）。

### 3.2 结果（毫秒，中位 / P90 [最小–最大]，n=10）

| 档 | 可交互 | FCP | DOMContentLoaded | 热打开 | 长任务 | 脚本执行 | 布局 | 样式重算 |
|---|---|---|---|---|---|---|---|---|
| tier0 | 103 / 137 [100–199] | 28 / 32 | 52 | 63 / 67 | 0 | 20 | 7.0 | 3.2 |
| tier1 | 105 / 110 [103–305] | 32 / 60 | 50 | 76 / 78 | 0 | 20 | 7.0 | 3.2 |
| tier2 | 109 / 112 [105–231] | 60 / 60 | 51 | 75 / 76 | 0 | 21 | 7.1 | 3.1 |
| PC 壳 tier2 🐧 | 207 / 211 [204–292]；从调用 `openPowerWindow()` 算起 225 / 228 | — | 132 | 109 / 123 | 1 个，约 55 | — | — | — |

- 首屏资源（三档一样）：**53 个请求**（壳 55 个）：JS 36 个 1,019,712 字节，CSS 8 个 123,160 字节，图片 2 个，文档 1 个，XHR 6 个、fetch 1 个（`/console/switches`、`/plugin/metrics`、`/plugins?summary=true`、`/console/timeline/start`、`/console/board`、`/console/timeline?limit=1000`、`/health`，服务端 4–12 ms）。**静态资源没有压缩**（FastAPI StaticFiles 不做 gzip），本机访问无所谓，走远程访问时会有影响。
- 首屏最大的几个文件（未压缩）：`authoring-*.js` 304 KB、`vue-i18n` 130 KB、`use-global-config`（element-plus）88 KB、`i18n` 82 KB、`en-US` 76 KB、`axios` 57 KB、`index.css` 56 KB。
- **`authoring-*.js` 是 8 种语言的文案合在一起**（sourcemap：`src/i18n/authoring.ts` 157 KB，`model-api.ts` 34 KB，`plugin-consent.ts` 31 KB 等）。每个语言文件都写 `import { authoringMessages } from '../authoring'`，再取 `authoringMessages['en-US']`（`src/i18n/locales/en-US.ts:5,13`），打包器没法摇掉其余 7 种语言。
- trace（tier2 冷打开，主线程自身时间）：脚本执行 64 ms，编译 1.3 ms，HTML 解析 0.5 ms，绘制 2.1 ms，GC 0.9 ms，没有超过 50 ms 的任务。按组件归因（V8 采样，经 sourcemap）：`src/main.ts`、`AppLayout.vue`、`router/index.ts`、`PowerBar.vue`、`FamiliarColony.vue`，每个都在 10 ms 以内，`@vue/runtime-core` 38 ms，`axios` 17 ms。**首次打开没有明显热点**，在这台机器上主要是网络往返和模块加载。
- PC 壳慢一倍：多了一次经 48911 的 307 跳转，窗口创建也要时间，另外有一个约 55 ms 的长任务。🐧 壳在 Xvfb 下帧被节流（§8），FCP 拿不到。

---

## 4. 页面和页签切换

### 4.1 方法

- 浏览器模式，tier2 全栈（launcher）。面板打开后依次点侧栏的 13 个入口，然后进插件详情页（web_search），再依次点它的 6 个页签，最后回首页。**第 0 轮是首次访问**（要加载懒加载的 chunk，同时录 trace），**之后 10 轮是稳态**。
- 每一步记录：**总耗时**（点击到连续 400 ms 没有请求、没有 DOM 变动、没有长任务，取最后一次活动的时间）、**接口等待**（最后一个接口响应的时间）、**接口之后的前端时间**（总耗时减接口等待）、长任务时间。服务端时间取 CDP Network 的 `receiveHeadersEnd − sendEnd`。
- 注意：页面切换带过渡动画（`src/motion/policy.ts:30-33`：page 220 ms，section/card 300 ms，加上逐项错开），所以"接口之后的前端时间"里大部分是动画，不是计算。我另外按"最后一次增删节点"算过一版"内容就绪"，和总耗时基本一样：Vue Transition 动画结束时才移除旧节点，所以这一版也包含动画。

### 4.2 结果（tier2，毫秒；稳态取中位 / P90 (最小–最大)，n=10）

| 页面 / 页签 | 首次访问 | 稳态总耗时 | 接口等待 中位/P90 | 接口之后的前端 | 长任务 | 接口数 |
|---|---|---|---|---|---|---|
| Familiar space | 222 | 191 / 194 (190–343) | 54 / 54 | 138 | 0 | 2 |
| Tasks | 198 | 182 / 191 | 41 / 50 | 140 | 0 | 2 |
| Familiar runtime | 389 | 366 / 369 (359–622) | 43 / 47 | 322 | 0 | 1 |
| MCP tools | 203 | 182 / 500 (178–547) | 54 / 500 | 126 | 0 | 1.3 |
| **Plugins（列表）** | **547** | 354 / 357 | 73 / 79 | 282 | 0 | 5 |
| Overview | 194 | 186 / 189 | 54 / 56 | 132 | 0 | 4 |
| Plugin forge | 197 | 185 / 186 | 41 / 43 | 144 | 0 | 1 |
| Knowledge | 440 | 399 / 402 (376–820) | 72 / 88 | 328 | 0 | 4.1 |
| Plugin API | 407 | 373 / 377 | 57 / 222 | 318 | 0 | 2.2 |
| Runs | 197 | 180 / 191 | 0 | 180 | 0 | 0 |
| Server Logs | 407 | 382 / 386 | 0（日志走 WebSocket） | 382 | 0 | 0 |
| MCP Adapter（插件提供的界面） | — | 371 / 378 | 74 | 298 | 0 | 3 |
| **插件详情（web_search）** | **728** | 337 / 354 | 146 / 354 | 192 | **82** | 9.3 |
| └ 页签 Basic Info | 19 | 17 / 24 | 0 | 17 | 0 | 0 |
| └ 页签 Permissions | 29 | 356 / 357 | 44 / 47 | 311 | 0 | 1 |
| └ 页签 Entry Points | 71 | 46 / 50 | 0 | 46 | 0 | 0 |
| └ 页签 Performance | 52 | 52 / 54 | 0 | 52 | 0 | 0 |
| └ 页签 Config | 40 | 53 / 55 | 0 | 53 | 0 | 0 |
| └ 页签 Logs | 52 | 50 / 292 | 0 / 292 | 44 | 0 | 0.3 |
| 回首页 | 192 | 194 / 196 | 64 / 67 | 129 | 0 | 7 |

tier0 和 tier1 的数字基本一样（附件 `browser.json`）。tier0 的 Server Logs 例外，要 850–1150 ms：独立模式的服务日志更多，日志页靠 WebSocket 推送，耗时跟日志量走。

**最慢的组件 / 页面**：
1. **插件详情页**：首次 728 ms，稳态 337 ms，**每次进入都有长任务**（中位 82 ms）。首次访问时 trace 里有 162 ms 和 54 ms 两个长任务。拆 162 ms 那个：`RunMicrotasks` 113 ms（Vue 调度器在微任务里渲染和打补丁）、强制样式和布局 `Document::UpdateStyleAndLayout` 14 ms 加 `ForcedStyleAndLayout` 12 ms（JS 读了布局信息，说明有布局抖动）。采样调用栈上的应用代码是 `src/motion/runtime.ts:132`（`finish`）和 `@vue/runtime-core` 的 `mountComponent`/`patch`。这一页一次要发 9 个请求（`/plugins/web_search`、`/plugins`、`/surfaces`、`/plugin/status`、`/ui-info`、config 系列）。
2. **插件列表页**：首次 547 ms（要加载 `PluginList-*.js` 66 KB），稳态 354 ms，其中 282 ms 是卡片入场的错开动画。
3. **Knowledge、Plugin API、Familiar runtime、Permissions 页签**：稳态 360–400 ms，都是 section/card 级别的 300 ms 动画，接口只占 40–70 ms。

**最慢的接口**（tier2 稳态，服务端时间 ms，n=10–30）：

| 接口 | 中位 | P90 | 范围 |
|---|---|---|---|
| `GET /console/board` | 18.0 | 19.6 | 15.5–20.3 |
| `GET /market/knowledge/status` | 15.7 | 16.7 | 13.9–17.9 |
| `GET /console/switches` | 14.8 | 17.0 | 12.7–17.2 |
| `GET /market/knowledge/packs/jobs` | 13.8 | 14.0 | 12.1–15.3 |
| `GET /console/timeline/start` | 13.8 | 15.1 | 11.6–15.8 |
| `GET /plugins`（n=30） | 13.1 | 22.1 | 8.9–24.8 |
| `GET /plugin/web_search/config/base/effective` | 13.0 | 15.5 | 11.5–16.1 |
| `GET /plugin/web_search/config` | 11.7 | 14.6 | 10.7–14.9 |
| `GET /console/commissions` | 9.1 | 10.9 | 7.6–**80.3** |

接口都很快（这台机器上不到 25 ms），**切页的时间主要花在前端动画上**。只有插件详情页是接口和长任务一起拖慢的。

### 4.3 多余的请求：`GET /plugins/undefined`

每次从插件详情页回首页，都会多发一个 `GET /plugins/undefined?locale=en-US`（tier2 浏览器模式 11 轮出现 22 次，PC 壳模式 11 轮每轮 1 次）。原因：`pluginId = computed(() => route.params.id)`（`PluginDetail.vue:267`），它的 watch 用了 `flush: 'sync'`（`PluginDetail.vue:655-680`）。路由离开时 Transition 的离场动画还在播，组件没卸载，`id` 已经变成 undefined，watch 里的 `if (detailMounted) void loadDetail()` 就又发了一次请求。

---

## 5. 动画帧率

> ⚠️ **没有 GPU，用的是软件渲染**（headless Chromium + SwiftShader）。下面的帧率只说明主线程和合成线程有没有卡住，**不能当作真机帧率**。🐧

### 5.1 方法

- 浏览器模式，tier2 全栈。先把每个页面都访问一遍（懒加载的 chunk 已经加载好），再测稳态。每轮三个场景：**首页静置 5 s**；**页面切换**（13 个入口连续点两遍，每次间隔 350 ms）；**插件列表滚动**（21 张卡片，`.plugin-list-shell__body` 滚到底 662 px 再滚回来，滚轮 120 px 一格、间隔 30 ms，来回 3 次）。
- 用 rAF 时间戳算帧间隔；超过 25 ms 算掉帧。每个场景 10 轮，第 0 轮录 trace，按时间对齐后给每次掉帧找对应的长任务和采样调用栈。
- 页面切换场景跑了两次独立测量（两次 launcher 全栈），两组数字都列出来。

### 5.2 结果（n=10 轮）

| 场景 | 帧率 中位 | 帧间隔 中位 / P90 / 最大 | 超过 25 ms 的帧 | 超过 50 ms 的帧 | 帧间隔分布（≤17 / 25–33 / 33–50 / 50–100 ms） |
|---|---|---|---|---|---|
| 首页静置 | 60.0 | 16.7 / 16.7 / 16.8 | 0 / 3004 | 0 | 3004 / 0 / 0 / 0 |
| 页面切换（第 1 组） | 58.5 | 16.7 / 16.7 / **50.1** | 135 / 6220（2.17%） | 3（0.05%） | 6085 / 117 / 15 / 3 |
| 页面切换（第 2 组） | 59.8 | 16.7 / 16.7 / 33.4 | 13 / 5807（0.22%） | 0 | 5794 / 13 / 0 / 0 |
| 列表滚动 | 60.0 | 16.7 / 16.7 / 16.8 | 0 / 2954 | 0 | 2954 / 0 / 0 / 0 |

**掉帧发生在哪**（第 1 组第 0 轮 trace，13 次掉帧，挑代表性的）：
- 页面切换开始后第 4517 ms，一帧 50 ms，叠着 31.9、10.3、43.8 ms 三个任务。采样里的应用代码：`src/components/logs/LogViewer.vue`（进日志页时初始化）、`@vue/runtime-dom` 的属性补丁。
- 第 4884 ms，一帧 33 ms，叠着 31 ms 的任务：`src/motion/runtime.ts:57`（`pinInScrollport`，在动画里读几何信息）。
- 第 4951 ms，一帧 33 ms：`src/components/plugin/hosted/tsxRuntime.ts`（插件托管界面 MCP Adapter 的 TSX 运行时）。
- 其余几次 33 ms 的掉帧没有对应的长任务，只是 rAF 和合成节奏抖了一下（软件渲染下常见）。
- 第 2 组第 0 轮的 trace 里没有掉帧，整段只有 1 个 111 ms 的长任务（在场景之间，不在测量窗口里）。

结论：**首页和列表滚动很稳**。页面切换偶尔掉到 30 fps 左右，都对应日志页、插件托管界面、动画运行时读几何信息这几个时刻；两组的掉帧比例差了 10 倍（2.17% 和 0.22%），说明这一项受环境噪声影响大，真机上要重测。PC 壳的帧率在本环境测不了（§8）。

---

## 6. 内存曲线

### 6.1 方法

- tier2 独立模式（方便把插件服务进程和插件子进程分开统计）。`mem_sampler.py` 每 0.5 s 用 psutil 采一次各进程的 RSS / USS / PSS 和线程数。插件子进程按服务日志里的 "Plugin X process started (pid: N)" 对应到插件。
- 面板页面：每 0.5 s 通过 CDP `Performance.getMetrics` 采 JS 堆、DOM 节点数、监听器数、文档数。三个时刻各拍一张堆快照（拍之前先强制 GC）。
- 阶段和事件点：服务启动 → 全部插件运行 → 静置 10 s → 打开面板 → 静置 60 s → **连续切页**（13 页 × 30 轮，间隔 250 ms）→ 静置 15 s → **装卸插件 10 轮**（复制插件目录 → `POST /plugins/refresh` → `POST /plugin/<id>/start` → 等 4 s → stop → 删除目录 → refresh → 等 4 s）→ 静置 → 关闭面板 → 静置 20 s。
  - 装卸是"拷目录 + 刷新"，相当于手动安装和卸载。`DELETE /plugin/<id>` 对手动拷进来的插件返回 "Plugin ownership could not be verified"，所以没有走市场安装和包上传的路径。
  - 装卸阶段面板停在首页。原计划停在插件列表页，脚本按链接找列表入口没找到（侧栏入口是 `<button>`，没有 href），回退到了首页。
- 图：附件 `mem-timeline.png`（三个子图共用时间轴，虚线是事件点）；数据：`mem-timeline.csv`（每秒一行）、`panel-mem-timeline.csv`。

### 6.2 插件服务进程和插件子进程

| 阶段（事件点） | 服务 RSS 起→止 MB | 服务 USS 起→止 MB | 插件子进程 USS 合计 MB（个数） |
|---|---|---|---|
| 启动 → 全部运行（第 2.1 s） | 0 → 96.6 | 0 → 34.9 | 215.8（11） |
| 静置 10 s | 102.5 → 107.5 | 59.4 → 64.5 | 216.5 |
| 打开面板 | 108.4 → 111.1 | 66.0 → 68.4 | 216.6 |
| 面板静置 60 s | 111.1 → 111.0 | 68.4 → 68.4 | 215.7 |
| **连续切页（约 2 分钟，1133 个请求）** | 111.0 → **115.9** | 68.4 → 75.8 | 216.0 |
| 静置 15 s | 115.9 → 115.9 | 75.8 | 216.0 |
| **装卸 10 轮** | 119.8 → **127.7** | 77.7 → 88.2（中间被 fork 共享拉低，见下） | 216 ↔ 244.7（12 个，来回） |
| 之后静置到结束 | 127.7 → 127.7 | 88.2 | 216.1 |

- 装卸期间服务 USS 时高时低，是 fork 的写时复制造成的：新的子进程和父进程共享页面，父进程的"独占"内存就变少了。所以**这一阶段要看 RSS**。
- 插件子进程很稳定：每个 `perf_memo` 的 USS 约 17.5 MB（RSS 约 83 MB，大部分和父进程共享），web_search 36.7 MB，330 s 里几乎没变。装卸时新 fork 出来的子进程 USS 是 24–28 MB，因为父进程这时已经变大了。🐧 spawn 模式下每个子进程的独占内存要大得多（§2.6）。
- **长时间装卸**（`soak.sh`，60 轮，每轮装上启动 1 s 再卸掉）：

  | 轮数 | 0 | 10 | 20 | 30 | 40 | 50 | 60 |
  |---|---|---|---|---|---|---|---|
  | 服务 RSS MB | 107.3 | 124.0 | 124.8 | 125.4 | 126.0 | 126.2 | 126.4 |
  | 服务 PSS MB | 71.1 | 89.4 | 90.2 | 90.8 | 91.4 | 91.8 | 92.0 |
  | 线程 / 文件句柄 | 41 / 236 | 42 / 242 | 42 / 238 | 42 / 242 | 42 / 242 | 42 / 238 | 42 / 238 |

  前 10 轮涨 17 MB，之后每轮约 50 KB。线程和句柄不涨，**是在封顶，不是线性泄漏**。
- **tracemalloc**（单独跑一次，25 层调用栈；A=预热后，B=又装卸 20 轮后，C=再跑 30 轮面板接口后）：A→B，Python 追踪到的内存只多了 **850 KB**；B→C 反而少了 508 KB。A→B 增长最多的几处：`json/decoder.py:353` 405 KB（解析 JSON 的结果，B→C 又释放了 391 KB）、`plugin/server/application/plugins/registry_service.py:772` 72 KB、`packaging/version.py:207` 65 KB、`plugin/server/monitoring/metrics.py:345` 58 KB、`tomllib` 43 KB、`plugin/message_plane/ingest_server.py:30` 38 KB、`plugin/utils/time_utils.py:12` 28 KB。RSS 涨的部分大多不在 Python 对象里（分配器碎片、C 扩展），开着 tracemalloc 时 RSS 读数被它自己的开销放大了，不能直接用。

### 6.3 面板页面

| 阶段 | JS 堆 起→止（峰值）MB | DOM 节点 | 事件监听器 | 文档数 |
|---|---|---|---|---|
| 打开 | 6.05 | 375 | 130 | 4 |
| 静置 60 s | 6.08 → 6.26（6.82） | 375 → 348 | 130 → 165 | 1 |
| **连续切页** | 5.54 → 47.1（**61.4**） | 348 → **19079** | 85 → **5052** | 5 |
| 静置 15 s | 47.1 → **16.0** | **486** | **87** | 1 |
| 装卸阶段（停在首页） | 16.1 → 16.4 | 487 | 87 → 307 | 1 |
| 最后静置 | 16.4 → 16.7 | 487 | 307 → 347 | 1 |

- 连续切页时 DOM 节点和监听器冲得很高，但都是还没回收的旧页面，GC 之后就回到原来的水平。**没有泄漏**。
- 停在首页时监听器每 3 s 涨 10 个左右，GC 后掉回去（第一段静置里，第 44 s 一次 GC 从 190 掉到 85）。来源是首页的轮询请求（每个 XHR 对象都带监听器，测试脚本自己也给每个 XHR 加了一个），**不是泄漏**。
- 堆快照对比（拍之前先 GC）：静置后 → 切页后 8.28 → 23.46 MB，主要是懒加载 chunk 编译出来的代码 `(code)` +7.95 MB、`system` +2.4 MB、数组和字符串约 +1.2 MB，以及 DevTools 网络记录（`NetworkResourcesData`、`XHRReplayData`，约 0.8 MB，是测试开了 CDP Network 带来的）。**切页后 → 装卸后 23.46 → 23.42 MB，没有增长。** 打包后的类名 `ve` 多了 4827 个（415 KB），可能是某个被缓存的对象，没有追到源码。

---

## 7. 热点和代码位置汇总（按影响排序）

1. **扫描子进程导入整个服务包**：`metadata_scanner.py:44-52`、`plugins/__init__.py:1-7`、`lifecycle_service.py:1430-1478`、`:393-405`。证据：§2.4 py-spy、keepmeta 对比。
2. **spawn 下子进程启动和内存翻倍** 🐧：`plugin/core/host.py:2066-2082`。证据：§2.6。
3. **能力静态分析冷启动吃 1.3 s CPU、抢 GIL**：`capability_analysis_service.py:108-113,193`、`plugin/core/capability_analysis.py:278,572,959,1402`。证据：§2.5。
4. **服务导入 0.38–0.9 s**：`plugin/server/http_app.py` 的导入链（routes、fastapi、lifecycle、console_proxy）。证据：§2.7。
5. **扫描子进程收尸要遍历所有进程**：`metadata_scanner.py:124`（`psutil.Process.children(recursive=True)`），热启动约 170 ms CPU 🐧。证据：§2.4。
6. **插件详情页每次进入有约 80 ms 长任务，接口 9 个**：`src/views/PluginDetail.vue`、`src/motion/runtime.ts:57,132`。证据：§4.2 trace。
7. **切页时间被动画主导**：`src/motion/policy.ts:30-33`、`AppLayout.vue:81-85`。证据：§4.2。
8. **离开详情页时请求 `/plugins/undefined`**：`PluginDetail.vue:267,655-680`。证据：§4.3。
9. **首屏加载 8 种语言的文案（304 KB）**：`src/i18n/locales/*.ts:5,13`、`src/i18n/authoring.ts`。证据：§3.2。
10. **装卸后 RSS 涨到封顶（+17 MB）**：tracemalloc 前几名见 §6.2。
11. **首次配置迁移和反复新建 ConfigManager**：`autostart_approvals.py:60-70`、`utils/config_manager/__init__.py:268-276,291-295`、`plugin/settings.py:99`。

---

## 8. PC 壳（N.E.K.O.-PC，Electron 41）模式：现象和契约点

PC 壳是闭源仓库，这里只写现象和契约点。

- **能跑起来**：在主仓先起好全栈后端（launcher.py），再在 PC 仓库里用 `npm ci` 和 `electron-forge start` 起壳，DISPLAY 指向 Xvfb，再加 `--remote-debugging-port=9222`。壳在 Linux 上会先自行重启一次来带上 X11 启动参数，所以要直接把这组参数（`--ozone-platform=x11 --neko-linux-x11-flags-applied …`）传进去，Xvfb 也要单独起，不能用 `xvfb-run`，否则重启后的进程会丢掉显示器。`chrome-sandbox` 要设成 root 所有、4755 权限。
- **契约点**：壳只接管已经在外面起好的后端（三个服务都健康，并且属于同一个实例）。面板由 Pet 页面的 `AgentHUD.openPowerWindow()` 打开，先请求 `48911/api/agent/user_plugin/dashboard`，再 307 跳到 `48916/ui/`，窗口名固定为 `neko_plugin_dashboard`。数据目录选择那一步（§2.8）在壳模式下同样会挡住面板入口。
- **时间**：从后端就绪到 Pet 窗口出现约 14.6 s，包含开发模式下 webpack 编译主进程和渲染进程，不能代表打包版。面板打开到可交互中位 207 ms（P90 211，n=10），从调用 `openPowerWindow()` 算起 225 ms。
- **测不了的部分** 🐧：在 Xvfb、没有窗口管理器的环境里，面板窗口（1280×864，位置 (320,108)）完全被全屏的 Pet 和聊天窗口盖住。**这个窗口的 rAF 只有约 1 帧/秒**，trace 里 `BeginMainThreadFrame` 也是约 1 次/秒；同一时刻聊天窗口有 61 帧/秒。加上 `--disable-renderer-backgrounding --disable-background-timer-throttling --disable-backgrounding-occluded-windows` 还是一样。结果是 Playwright 每次点击都要等约 1.1 s 的稳定帧，切页数据（1.2–1.9 s）和帧率在这个环境里都没有意义，报告里没有采用。**要在真实桌面（有窗口管理器、有 GPU）上确认**：面板窗口被透明的置顶 Pet 窗口盖住时，会不会也被节流。

---

## 9. 局限和没覆盖的部分

- 不是 Windows（见开头）。spawn 只是用来模拟 Windows 的启动方式，没有 Windows 的文件系统和杀毒开销。
- 没有 GPU，帧率是软件渲染下的数字。
- 需要模型配置才能走到的路径都没测：插件调用模型网关（`/model-gateway`）、需要 API key 的插件功能、Agent 真正执行任务。
- 没有走"市场安装 / 上传包"的安装路径（§6.1），装卸是用拷目录加刷新模拟的。
- tier2 的 10 个插件都是 `memo_reminder` 的副本，功能简单。真实第三方插件依赖更多，子进程内存和导入时间会更大。
- 浏览器模式的面板 trace 用的是 headless Chromium 156，PC 壳是 Chromium 146，版本不同。
- 采样剖析的调用栈里有一部分算到了测试脚本自己的轮询（Playwright `evaluate`），归因组件时已经排除，但"(program)"和原生帧的占比不能直接当成页面开销。

---

## 10. 复现步骤

**推荐用分支上的跨平台脚本**（Windows、Linux 都能跑），详细的安装、运行、输出说明和耗时都在 `perf-baseline/README.md` 里。简要步骤：

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git && cd N.E.K.O
git checkout perf/plugin-baseline-scripts          # 87d6de3，基于 main f47f737
uv sync
(cd frontend/plugin-manager && npm ci && npx vite build --sourcemap)
uv tool install py-spy
uv run perf-baseline/bench.py setup                # 装 Playwright 用的 Chromium
uv run perf-baseline/bench.py all                  # 整套；Linux 上加 --with-spawn 模拟 Windows 的 spawn
# 结果：perf-baseline/results/analysis/results.md、summary.json、mem-timeline.png、flame-*.svg
# PC 壳：uv run perf-baseline/bench.py panel --shell --pc-repo <N.E.K.O.-PC 路径> --tiers 2   （Linux 无显示器时加 --xvfb）
```

**本报告的数字是怎么来的**：报告里的数字是先用一版只能在 Linux 上跑的 bash/Node 脚本测的（附件 `neko-perf-tools.txt`）。之后把脚本改写成分支上这版跨平台 Python 脚本，在同一台虚拟机上完整跑了一遍 `all --with-spawn`（33 分钟，没有失败的步骤），和报告对得上：

| 指标 | 报告里的数字 | 分支脚本重跑 |
|---|---|---|
| tier0 / tier1 / tier2 热启动（全部插件运行，中位） | 674 / 1001 / 1752 ms | 631 / 960 / 1668 ms |
| tier2 热启动，单个插件启动：无元数据 / 保留元数据 / spawn | 455 / 120 / 996 ms | 423 / 115 / 1000 ms |
| tier2 热启动进程树 PSS：fork / spawn | 344 / 826 MB | 338 / 819 MB |
| 面板首次打开到可交互（浏览器，tier2） | 109 ms | 106 ms |
| 切页时 `GET /plugins/undefined` 出现次数（11 轮） | 每次离开详情页 1 次 | 11 次 |
| 内存场景：服务 RSS（静置 → 切页后 → 装卸后） | 107.5 → 115.9 → 127.7 MB | 107.0 → 115.1 → 122.4 MB（装卸 3 轮） |
| 60 轮装卸后服务 RSS | 126.4 MB（PSS 92.0） | 123.7 MB（PSS 88.3） |

（这次重跑用的是 Python 版 Playwright 1.65.2 自带的 Chromium 159，原测量用的是 Chromium 156。）

旧版 bash/Node 脚本的用法（仅 Linux）：把 `neko-perf-tools.txt` 按 `===== FILE: tools/<名字> =====` 拆回 `tools/` 目录，依次运行 `tools/run_all.sh`、`tools/run_extra.sh`、`tools/tm_scenario.sh`、`tools/soak.sh`，最后运行 `tools/analyze.py`。脚本里写死了 `/home/coder/work/review-327` 这个路径，换机器时要全局替换。

## 11. 附件（都挂在需求 #326 上）

| 文件 | 内容 |
|---|---|
| `neko-plugin-perf-baseline.md` | 本报告 |
| `mem-timeline.png` | §6 的内存曲线：插件服务、插件子进程、面板 JS 堆，虚线是事件点 |
| `flame-t2-warm.png`、`flame-t2-cold.png`、`flame-t2-warm-spawn.png` | py-spy 火焰图（tier2 热 / 冷 / spawn 热）。热启动那张右边 10 座"山"就是 10 个扫描子进程 |
| `neko-perf-summary.json` | 所有汇总数据：启动分阶段统计、全栈启动、面板打开 / 切页 / 帧率、py-spy 前 N 名（服务进程和扫描子进程）、importtime、内存分阶段、堆快照对比、tracemalloc 对比、trace 摘要（长任务和组件归因） |
| `mem-timeline.csv`、`panel-mem-timeline.csv`、`soak-60.csv` | 内存采样（服务和子进程每秒一行、面板每 0.5 s 一行、60 轮装卸） |
| `neko-perf-tools.txt` | 最初那版只能在 Linux 上跑的 bash/Node 脚本（本报告的数字就是用它测的）。跨平台版本在分支 `perf/plugin-baseline-scripts` 上 |
| `neko-perf-data.tar.gz.base64.txt` | 原始数据包（base64 编码；解码：先删掉第一行说明，再 `base64 -d … > neko-perf-data.tar.gz`）：每次启动的 `phases.jsonl` / `summary.json` / `tree-mem.txt`、py-spy 原始栈 `pyspy.raw`、importtime 原文、面板测量原始 JSON、内存原始采样 |

**没有附上**原始 Chrome trace（每份 60–215 MB）和堆快照（每份 16–29 MB），文件太大。报告里用的都是它们的摘要（在 `neko-perf-summary.json` 的 `traces`、`panel_heap_diff` 里），需要原始文件可以按 §10 重新生成。附件里接口 URL 带的本机临时 token 已经替换成 `<redacted>`。
