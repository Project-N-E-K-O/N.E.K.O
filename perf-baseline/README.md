# perf-baseline：N.E.K.O 插件系统性能基线测量脚本

> 这是需求 #326 的性能基线测量脚本，**只放在 `perf/plugin-baseline-scripts` 分支上，不开 PR、不合并**，用完由维护者删除。

这些脚本用来测量插件服务启动、插件管理面板打开、页面切换、动画帧率和内存曲线。Windows、Linux、macOS 上都能跑，入口统一是：

```
uv run perf-baseline/bench.py <命令> [参数]
```

- 分支基于 main `f47f73729615f828da14be2d7e47016e38d57939`，Linux 基线报告测的就是这个提交。在别的提交上也能跑，`env` 会记下实际的提交号。
- 脚本**不改仓库里的任何文件**。测量产物在 `perf-baseline/results/`（已加进 `.gitignore`），临时数据在系统临时目录下的 `neko-perf/`。
- **不会碰你的真实数据**：每次都给 N.E.K.O 进程一个全新的临时数据目录，具体做法见文末「隐私和隔离」。
- 测试时不调用任何模型 API。只有内置插件 web_search 启动时会自己联网查一次国家（ipwho.is），这是插件原有的行为。

---

## 1. Windows 实机：准备（第一次大约 15–20 分钟）

下面的命令都在 **PowerShell** 里执行。npm 和 npx 写成 `npm.cmd`、`npx.cmd`：Windows PowerShell 5.1 默认的执行策略会拦住 `npm.ps1`。

1. **安装工具**（已经装过的跳过）

   ```powershell
   winget install --id Git.Git -e
   winget install --id astral-sh.uv -e
   winget install --id OpenJS.NodeJS.LTS -e      # Node 22 或更高，只用来构建插件管理面板
   # 重新打开 PowerShell，让 PATH 生效
   uv tool install py-spy                         # Python 采样剖析器（boot --pyspy 要用）
   ```

   可选：
   - Sysinternals **RAMMap**（https://learn.microsoft.com/sysinternals/downloads/rammap），用来做"冷启动前清空系统文件缓存"，见第 4 节。
   - 测 PC 桌面壳要用 **N.E.K.O.-PC 源码仓库**（闭源，需要权限），见第 5 节。

2. **取代码、装依赖、构建面板**

   ```powershell
   git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
   cd N.E.K.O
   git checkout perf/plugin-baseline-scripts
   uv sync
   cd frontend\plugin-manager
   npm.cmd ci
   npx.cmd vite build --sourcemap      # 带 sourcemap，剖析结果才能对应到 .vue/.ts 源文件
   cd ..\..
   ```

3. **装测量脚本自己的依赖**（psutil、Playwright、matplotlib 会自动装进 uv 的独立环境，不进项目的 .venv）

   ```powershell
   uv run perf-baseline\bench.py setup     # 下载 Playwright 用的 Chromium（约 150 MB）
   uv run perf-baseline\bench.py env       # 记录机器信息，生成 results\env.json
   ```

4. **测之前**：退出正在运行的 N.E.K.O（托盘也要退出）。脚本要用 48911、48916（PC 壳模式还有 9222）这几个端口，被占用时会直接报错退出。测量期间尽量不要用电脑，也不要开别的大程序。

## 2. 一键跑完整套

```powershell
uv run perf-baseline\bench.py all
```

- 在 12 核的 Linux 虚拟机上整套约 **35 分钟**（见第 8 节实测）。Windows 笔记本预计 **45–90 分钟**：spawn 方式起插件子进程更慢，杀毒软件也会拖慢。
- 跑完会自动执行 `analyze`，结果在 `perf-baseline\results\analysis\`：
  - `results.md`：所有表格（中位数、P90、范围）；
  - `summary.json`：全部汇总数据；
  - `mem-timeline.png`：内存曲线；
  - `flame-*.svg`：火焰图（用浏览器打开，鼠标悬停能看到函数名和占比）；
  - `mem-timeline.csv`、`panel-mem-timeline.csv`：内存原始采样。
- 可以用 `--skip` 跳过某些步骤，例如 `--skip soak,tracemalloc`。单项失败不会中断整套，失败原因写在 `results\bench.log` 里。
- 默认用 Playwright 自带的 Chromium（无头）。想用本机的 Edge 或 Chrome、并且开窗口用真实 GPU（帧率更接近用户实际看到的），加 `--browser msedge --headed`。

## 3. 每一步测什么、输出什么、要多久

括号里的时间是 Linux 虚拟机上的实测值。

| 命令 | 测什么 | 主要输出（都在 `results\` 下） | 耗时 |
|---|---|---|---|
| `env` | CPU、内存、GPU、系统版本、Python/uv/Node/py-spy/浏览器版本、仓库提交号 | `env.json` | <1 分钟 |
| `boot --tiers 0,1,2 --cold 5 --warm 10 --pyspy` | **插件服务启动**（独立模式 `plugin/user_plugin_server.py`）。三档插件：0 个 / 自带 11 个 / 自带 + 10 个 memo_reminder 副本。分阶段计时：进程拉起、导入和建 app、迁移、消息平面、注册表刷新、自启分组、批量启动插件（含单个插件）、StartupReconciler、默认模型槽、能力静态分析。另外按档、按冷热各录一次 py-spy | `boot\t<档>-<cold/warm>-<n>\phases.jsonl`、`summary.json`（含进程树内存）；`pyspy\<…>\pyspy.raw` | 约 5 分钟 |
| `boot --tiers 2 --keep-meta` | 对照组：插件副本保留 `plugin.meta.json`，用来量出元数据扫描子进程的开销 | `boot-keepmeta\…` | 约 1 分钟 |
| `importtime` | `python -X importtime` 的模块导入耗时 | `importtime\importtime.txt` | <1 分钟 |
| `stack --tiers 1,2 --reps 5` | **全栈启动**（`launcher.py`：主服务 48911、记忆服务、agent 进程内嵌插件服务 48916）到两个端口都能响应 | `stack\t<档>-summary.json` | 约 2 分钟 |
| `panel --modes open,switch --tiers 0,1,2` | **面板首次打开到可交互**（每次全新浏览器上下文；首屏请求数和字节数；长任务、脚本、布局、样式耗时；第 1 次录 Chrome trace）；**页面和页签切换**（13 个侧栏页 + 插件详情的 6 个页签；第 0 轮是首次访问并录 trace，后面是稳态；前端时间和接口时间分开记） | `browser\t<档>\open.json`、`switch.json`、`*.trace.json` | 约 7 分钟 |
| `panel --modes fps --tiers 2` | **动画帧率**：首页静置、页面切换、插件列表滚动；rAF 帧间隔分布，第 0 轮录 trace，把掉帧对应到长任务和调用栈 | `browser\t2\fps.json`、`fps-rep0.trace.json` | 约 5 分钟 |
| `mem` | **内存曲线**：插件服务进程、每个插件子进程（RSS/USS/PSS，每 0.5 s）、面板页面（JS 堆、DOM 节点、监听器），阶段依次是启动、静置、打开面板、连续切页、装卸插件 10 轮；拍 3 张堆快照 | `mem\proc-mem.csv`、`events.jsonl`、`panel-mem.json`、`heap-*.heapsnapshot` | 约 6 分钟 |
| `soak --cycles 60` | 反复装卸插件，每轮记一次服务进程的 RSS、线程数、句柄数，看有没有持续上涨 | `soak\soak.csv` | 约 4 分钟 |
| `tracemalloc` | 服务进程 Python 堆的增长来源（装卸 20 轮、面板接口 30 轮前后各拍一次快照） | `tracemalloc\tm-compare.json` | 约 2 分钟 |
| `analyze` | 汇总以上所有结果，生成表格、图表、火焰图，解析 trace（经 sourcemap 对应到组件） | `analysis\*` | 1–3 分钟 |

常用参数：`--tiers`、`--cold`、`--warm`、`--reps`、`--timeout`、`--out`。完整列表看 `uv run perf-baseline\bench.py -h`。

## 4. 冷启动和热启动

- **热启动**：沿用上一次的数据目录，Python 字节码缓存和系统文件缓存都是热的。
- **冷启动**：脚本每次都会换一个全新的数据目录，并清掉仓库里的 `__pycache__`。**系统文件缓存要另外清**：
  - Linux：如果有免密 sudo，脚本自动执行 `drop_caches`（只在 Linux 上有）。
  - Windows：没有自带的办法，三选一：
    1. **最准确**：重启电脑，登录后先跑一次 `uv run perf-baseline\bench.py boot --tiers 2 --cold 1 --warm 0`；
    2. 用 RAMMap 清 standby 列表（要管理员权限的 PowerShell）：`uv run perf-baseline\bench.py boot --cold-cmd "C:\Tools\RAMMap64.exe -Ew"`；
    3. 什么都不加：这时只清了数据目录和字节码缓存，结果里的 `cold_note` 会写明"OS file cache NOT dropped"，看数字时要注意。

## 5. 在 PC 桌面壳里测面板（可选）

PC 壳从源码运行时，会等一个已经在外面起好的后端，然后接上去。脚本会先起好隔离的后端，再在 PC 仓库里执行 `npx electron-forge start -- --remote-debugging-port=9222`，然后通过 CDP 接管。面板的打开方式和用户点锤子按钮一样，都是调 `window.AgentHUD.openPowerWindow()`。

```powershell
cd <N.E.K.O.-PC 仓库>
npm.cmd ci
cd <N.E.K.O 仓库>
uv run perf-baseline\bench.py panel --shell --pc-repo <N.E.K.O.-PC 仓库路径> --tiers 2 --modes open,switch,fps --reps 10
```

- 输出在 `results\electron\t2\`，格式和浏览器模式一样。
- 壳的用户数据目录也指到临时目录（`NEKO_USER_DATA_DIR`）。
- 打包好的安装版不在这套脚本里：它自带后端，会用你的真实数据。

## 6. 只能在 Linux 上做的步骤（都在 `linux_only.py` 里）

| 步骤 | 为什么只能在 Linux 上做 | 其他平台怎么替代 |
|---|---|---|
| 冷启动前 `echo 3 > /proc/sys/vm/drop_caches` | 用的是 Linux 内核接口，需要免密 sudo | Windows 见第 4 节 |
| `panel --shell --xvfb`：在没有显示器的机器上用 Xvfb 跑 PC 壳 | Xvfb 只有 Linux 有 | Windows、macOS 直接在桌面上跑 |
| PC 壳的 X11 启动参数（`--ozone-platform=x11 …`） | 壳在 Linux 上会先自己重启一次来带上这些参数，提前传进去才能保住 CDP 端口 | 其他平台不用 |
| `boot --start-method spawn` / `all --with-spawn` | 在 Linux 上模拟 Windows 的 spawn 方式起插件子进程 | Windows 本来就是 spawn，不用加 |
| `env` 里的 `/proc/cpuinfo`、`lspci`、`free -h` | 读的是 Linux 专有接口 | Windows 改用 PowerShell 的 `Get-CimInstance` 查询 |

## 7. 隐私和隔离

脚本起的每一个 N.E.K.O 进程（包括 `launcher.py` 起的主服务、记忆服务、agent 服务，插件子进程，元数据扫描子进程）都带着下面这些设置：

- `HOME`、`USERPROFILE`、`APPDATA`、`LOCALAPPDATA`、`XDG_*` 都指向 `%TEMP%\neko-perf\<场景>\`（Linux 上是 `/tmp/neko-perf/…`）。uv 自己的缓存和 Python 目录会提前固定下来，不受影响。
- `PYTHONPATH` 里加了 `perf-baseline/isolation`，每个 Python 进程启动时都会加载其中的 `sitecustomize.py`：
  - 把 `ConfigManager` 的标准数据目录、旧版数据目录、"文档"目录候选都换成临时目录。Windows 上旧版数据的查找会通过 `SHGetFolderPathW` 直接读真实的"文档"文件夹，环境变量拦不住，所以必须在这里拦（做法和仓库测试里的 `tests/real_root_isolation.py` 一样）；
  - 日志目录也指到临时目录；
  - 只在「0 个插件」这一档，把内置插件目录换成一个空目录（不动仓库文件）。
- 用到的角色和对话数据都是新目录里的默认值，测试不会产生任何真实对话。
- 测完可以直接删掉 `%TEMP%\neko-perf\` 和 `perf-baseline\results\`。
- `results\` 里可能有本机临时 bridge token（在接口 URL 里）。`analyze` 生成的 `summary.json` 和 `results.md` 已经把它们替换成 `<redacted>`；分享原始 JSON 前也请先替换。

## 8. 参考数字和注意事项

- Linux 基线（12 核 KVM 虚拟机、无 GPU、main f47f737）的完整报告在需求 #326 的附件 `neko-plugin-perf-baseline.md` 里。拿 Windows 的数字和它对比时，主要看各阶段的比例和热点排序。
- 用这套脚本在 Linux 上跑完整的 `all --with-spawn` 用了 33 分钟（1982 秒），中途没有失败的步骤。
- 杀毒软件（Defender）的实时扫描会明显拖慢冷启动和子进程拉起，**不要为了测试去关它**：用户实际的环境就是开着的。报告里写明当时杀毒是开着的就行。
- py-spy 在 Windows 上一般不需要管理员权限；如果报权限错误，用管理员身份打开 PowerShell 再跑。
- 无头浏览器的帧率只说明主线程有没有卡；要看真实帧率，加 `--headed`，并保持窗口在前台。
- 端口被占用：先退出 N.E.K.O，或者等上一次测量的进程退出（脚本只按 PID 停自己起的进程）。
