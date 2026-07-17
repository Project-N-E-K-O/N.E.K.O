# Linux Desktop Runtime

Linux desktop reports cross two layers:

| Layer | Owns |
| --- | --- |
| Packaged Python backend | Servers, storage, imports, backend logs |
| Electron shell from the workflow-selected N.E.K.O.-PC revision | AppImage entrypoint, windows, transparency, input methods |

Do not patch backend code for an Electron window/input problem before identifying the owning process and exact artifact revision.

## Environment snapshot

```bash
printf 'session=%s desktop=%s wayland=%s display=%s\n' \
  "$XDG_SESSION_TYPE" "$XDG_CURRENT_DESKTOP" "$WAYLAND_DISPLAY" "$DISPLAY"
env | grep -E '^(GTK_IM_MODULE|GTK_IM_MODULE_FILE|QT_IM_MODULE|XMODIFIERS|LANG|LC_)='
```

Select the main Electron browser process, not a `--type=renderer`, GPU/zygote child, or `projectneko_server` backend helper. Include its command line and relevant environment in reports, but never publish tokens/API keys from `/proc/<pid>/environ`.

## Transparent click-through

Compare the same artifact under native Wayland and X11/XWayland. If X11 works but Wayland blocks input, scope the issue to Electron/compositor input-region behavior. Record desktop environment, compositor version, Electron flags such as `--ozone-platform`, and whether the entire transparent window or only a visible region captures clicks.

## CJK input

Record whether the candidate window appears and whether text commits into the React chat input. Steam can change inherited environment and library resolution, so inspect the Steam-launched Electron process. Useful evidence includes IM framework/version and mapped Fcitx5/IBus/GTK/GLib libraries.

Do not recommend globally replacing system libraries or broadly changing `LD_LIBRARY_PATH` as a generic fix. Make packaging fixes in the Electron repository when that layer owns the failure.

## Non-ASCII paths

Before application import, `launcher_core/bootstrap.py` checks filesystem encoding. If it is not UTF-8, it sets `PYTHONUTF8=1` and re-execs once.

```bash
locale
uv run python -c "import sys; print(sys.getfilesystemencoding(), sys.flags.utf8_mode)"
```

For remaining packaged-build errors, include artifact/commit and launcher output. `sys.getdefaultencoding()` does not diagnose filesystem path encoding.
