# Linux 桌面运行时

本文整理 Linux 桌面版 AppImage / Steam 包的排障线索，主要面向维护者和 issue triage。适用场景包括 KDE / Wayland 点击穿透异常、透明窗口拦截输入、Toast 不可点击，以及中文/日文/韩文输入法无法提交文字。

相关 issue：[#396](https://github.com/Project-N-E-K-O/N.E.K.O/issues/396)、[#1276](https://github.com/Project-N-E-K-O/N.E.K.O/issues/1276)、[#1279](https://github.com/Project-N-E-K-O/N.E.K.O/issues/1279)。

## 运行时分层

Linux 桌面发行包通常由两层组成：

| 层级 | 典型职责 |
|------|----------|
| Python 后端包 | API 服务、存储、Steamworks、日志、运行时诊断 |
| Electron 桌面壳 | AppImage 入口、Chromium 参数、透明窗口、桌面输入法集成 |

调试 Linux 桌面专属问题时，先判断问题属于哪一层。文本无法进入 Electron 输入框、透明窗口挡住下层应用、Toast 可见但点不到，通常属于 Electron / AppImage 层；服务启动、存储迁移、Steamworks、Python import 报错，则通常属于后端包。

## 快速环境快照

先让反馈者提供这些信息：

```bash
echo "XDG_SESSION_TYPE=$XDG_SESSION_TYPE"
echo "XDG_CURRENT_DESKTOP=$XDG_CURRENT_DESKTOP"
echo "WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
echo "DISPLAY=$DISPLAY"
echo "GTK_IM_MODULE=$GTK_IM_MODULE"
echo "GTK_IM_MODULE_FILE=$GTK_IM_MODULE_FILE"
echo "QT_IM_MODULE=$QT_IM_MODULE"
echo "XMODIFIERS=$XMODIFIERS"
echo "SteamAppId=$SteamAppId"
```

对正在运行的桌面壳进程，检查真实环境变量和启动参数。要采样 **Electron 主浏览器进程**，既不要采样 `projectneko_server` 等后端 helper，也不要采样 Electron 子进程。透明窗口和 GTK 输入法模块都驻留在主浏览器进程里，renderer/GPU/zygote 子进程（带 `--type=...`）并不持有它们；而 `pgrep -n`（取最新 PID）通常会命中子进程，导致下面的检查误报「输入法/input-shape 未加载」。匹配时要锁定 N.E.K.O 的 AppImage/AppRun（不要用泛词 `electron`，否则会误命中 VS Code、Discord 等其他 Electron 应用），再保留命令行不含 `--type=` 的进程：

```bash
mains=()
for p in $(pgrep -f 'AppRun|AppImage|N[.]E[.]K[.]O|n[.]e[.]k[.]o'); do
  tr '\0' '\n' < "/proc/$p/cmdline" | grep -q -- '--type=' || mains+=("$p")
done
if [ "${#mains[@]}" -eq 0 ]; then
  echo "N.E.K.O desktop shell (main Electron process) was not found" >&2
  exit 1
elif [ "${#mains[@]}" -gt 1 ]; then
  echo "找到多个候选，请挑一个、手动设 pid=<PID> 后重跑：" >&2
  for p in "${mains[@]}"; do
    printf '  %s  %s\n' "$p" "$(tr '\0' ' ' < "/proc/$p/cmdline")" >&2
  done
  exit 1
fi
pid="${mains[0]}"
# 保留 AppRun/AppImage 是为了兜住「Steam 启动、命令行不含字面 app 名」的情况，
# 但它们也可能命中无关应用。若唯一匹配看起来不像 N.E.K.O，就告警，让人先核对
# 下面打印的 cmdline 再采信诊断结果。
if ! tr '\0' '\n' < "/proc/$pid/cmdline" | grep -qiE 'n[.]?e[.]?k[.]?o'; then
  echo "警告：pid $pid 仅由泛化的 AppRun/AppImage 规则命中，请确认下面的 cmdline 确属 N.E.K.O。" >&2
fi
tr '\0' '\n' < "/proc/$pid/environ" | sort | grep -E 'DISPLAY|WAYLAND|GTK_IM|QT_IM|XMODIFIERS|Steam'
tr '\0' ' ' < "/proc/$pid/cmdline"; echo
```

检查输入法相关动态库是否实际加载：

```bash
grep -E 'im-fcitx|Fcitx5GClient|gtk|glib|ibus' "/proc/$pid/maps" | sort -u
```

## X11 与 Wayland 点击穿透

Linux 透明窗口点击穿透需要按合成器分别处理。

在 X11 / XWayland 下，可靠路径通常是 X11 input shape，例如 `ShapeInput` helper。它可以让可见的宠物或交互矩形接收事件，而透明区域的点击继续落到下层窗口。

在 Wayland 下，Electron 的 `setIgnoreMouseEvents` 和 `setShape` 对全屏透明宠物窗口可能不够可靠。受影响的 KDE Plasma 环境中，即使应用持续更新 input region，合成器仍可能把鼠标事件交给透明 Electron surface，导致 N.E.K.O. 下方窗口全部点不到。遇到 Wayland 下全局挡点击时，不要先假设 renderer 的命中矩形错误，应先和 X11 / XWayland 行为对比。

建议排查顺序：

1. 用发行包默认方式复现。
2. 让 Electron 壳强制 X11 / XWayland，例如 `--ozone-platform=x11`。
3. 确认进程是否有 X11 display，以及是否出现预期的 X11 input-shape helper 日志。
4. 如果 X11 正常、Wayland 全局挡点击，把 issue 收敛到 Wayland 合成器 / Electron input-region 行为，不要混入后端启动问题。

## Steam 与 CJK 输入法

Steam 版 CJK 输入可能出现“普通终端启动正常，Steam 启动不正常”的情况。Steam runtime 会影响继承环境变量和动态库解析，Chromium 也可能回退到 XIM。

Fcitx 的基础输入法环境应包含：

```bash
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
export INPUT_METHOD=fcitx
```

对 X11 / XWayland 桌面包，优先使用原生 GTK IM module，而不是只依赖 XIM。XIM 可能能调出候选框，但文字 commit 仍然进不了 Electron 文本输入框。Fcitx5 原生路径成功时，Electron 进程中通常能看到 `im-fcitx5.so` 和 `libFcitx5GClient.so.2`。

如果只在 Steam 内原生 Fcitx 加载失败，应检查 GLib 版本不匹配——但要在 **Steam runtime 的**库解析环境下跑，而不是普通终端。`ldd -r` 是按当前环境解析 relocation 的，在普通终端跑可能给出假「干净」结果，恰好掩盖本节要诊断的「仅 Steam 下不匹配」。从 Steam 启动的 N.E.K.O 进程（上面快照里的 `$pid`）导入 `LD_LIBRARY_PATH`，或直接从 Steam 启动的 shell 里跑：

```bash
export LD_LIBRARY_PATH="$(tr '\0' '\n' < "/proc/$pid/environ" | sed -n 's/^LD_LIBRARY_PATH=//p')"
ldd -r /path/to/im-fcitx5.so | grep -E 'not found|undefined symbol|glib|gobject'
```

已见过的失败特征包括：

```text
undefined symbol: g_once_init_leave_pointer
Loading IM context type 'fcitx' failed
GLib version too old
```

这些报错表示模块已被找到，但无法在 Steam runtime 可见的库集合里初始化。稳妥的发行包修复不应依赖任意宿主机 GTK / Fcitx 模块，因为它可能由比 Steam runtime 更新的 GLib 编译。可行方向包括：

1. 在 Electron 壳中打包兼容的 GTK IM module 及必要依赖库。
2. 启动时生成指向该模块的 `GTK_IM_MODULE_FILE` cache。
3. 收窄 `LD_LIBRARY_PATH`，只优先必要的兼容库。
4. 尽早记录选中的 IM module 路径和初始化失败原因，避免用户只看到“无法输入中文”。

## Bug Report 应包含的信息

点击穿透问题建议包含：

- 桌面环境和 session 类型。
- X11 / XWayland 与 Wayland 行为是否不同。
- Electron 启动参数，尤其是 `--ozone-platform`。
- input-shape 或 mouse-through 初始化附近日志。

输入法问题建议包含：

- 输入法框架和版本，例如 Fcitx5 或 IBus。
- 上文列出的进程环境变量。
- 候选框是否能出现。
- commit 后的文字是否进入聊天输入框。
- 若使用 `GTK_IM_MODULE=fcitx` 或 `ibus`，提供所选 GTK IM module 的 `ldd -r` 输出。

把这两类问题拆开记录会让修复更安全：点击穿透主要是窗口 / 合成器行为，CJK 输入主要是 AppImage / Steam 环境和原生库加载行为。
