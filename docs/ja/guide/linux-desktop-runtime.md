# Linux デスクトップランタイム

Linux desktop issue は 2 layer に分かれます。

| Layer | Owns |
| --- | --- |
| packaged Python backend | services/storage/imports/logs |
| workflow-selected N.E.K.O.-PC Electron shell | AppImage/windows/transparency/IME |

Process と artifact revision を確認せず Electron window/input issue を backend で修正しないでください。

```bash
printf 'session=%s desktop=%s wayland=%s display=%s\n' \
  "$XDG_SESSION_TYPE" "$XDG_CURRENT_DESKTOP" "$WAYLAND_DISPLAY" "$DISPLAY"
env | grep -E '^(GTK_IM_MODULE|GTK_IM_MODULE_FILE|QT_IM_MODULE|XMODIFIERS|LANG|LC_)='
```

Main Electron browser process を選び、renderer/GPU/zygote/`projectneko_server` helper と区別します。`/proc/<pid>/environ` の token/API Key は公開しません。

Same artifact で Wayland と X11/XWayland を比較します。X11 だけ正常なら Electron/compositor input-region に scope を絞り、desktop/compositor、`--ozone-platform`、hit region を記録します。

CJK input は candidate window と React input への commit を分け、Steam process environment と Fcitx5/IBus/GTK/GLib を記録します。System library 全体置換や broad `LD_LIBRARY_PATH` を generic fix にしません。

Backend は import 前に filesystem encoding を確認し、非 UTF-8 なら `launcher_core/bootstrap.py` が `PYTHONUTF8=1` で一度 re-exec します。

```bash
locale
uv run python -c "import sys; print(sys.getfilesystemencoding(), sys.flags.utf8_mode)"
```
