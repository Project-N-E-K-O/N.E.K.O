# Linux 桌面运行时

Linux 桌面问题横跨两层：

| 层 | 所有权 |
| --- | --- |
| 打包 Python 后端 | 服务、存储、import、后端日志 |
| workflow 选定 N.E.K.O.-PC revision 的 Electron shell | AppImage、窗口、透明区、输入法 |

没有先确认进程与 artifact revision，不要用后端改动修 Electron 窗口/输入问题。

```bash
printf 'session=%s desktop=%s wayland=%s display=%s\n' \
  "$XDG_SESSION_TYPE" "$XDG_CURRENT_DESKTOP" "$WAYLAND_DISPLAY" "$DISPLAY"
env | grep -E '^(GTK_IM_MODULE|GTK_IM_MODULE_FILE|QT_IM_MODULE|XMODIFIERS|LANG|LC_)='
```

选择 Electron 主 browser 进程，而不是 `--type=renderer`、GPU/zygote 子进程或 `projectneko_server` helper。可附命令行和相关环境，但不得发布 `/proc/<pid>/environ` 中的 token/API Key。

同一 artifact 比较 Wayland 与 X11/XWayland。若 X11 正常而 Wayland 拦截点击，应收敛到 Electron/compositor 输入区域，记录桌面环境、compositor、`--ozone-platform` 和命中区域。

输入法问题要区分候选窗与文字真正提交到 React 输入框，并记录 Steam 进程环境与 Fcitx5/IBus/GTK/GLib。不要把全局替换系统库或扩大 `LD_LIBRARY_PATH` 当通用修复。

后端在 import 前检查文件系统编码。非 UTF-8 时 `launcher_core/bootstrap.py` 设置 `PYTHONUTF8=1` 并只 re-exec 一次。

```bash
locale
uv run python -c "import sys; print(sys.getfilesystemencoding(), sys.flags.utf8_mode)"
```
