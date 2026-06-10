# Linux Desktop Runtime

This page collects Linux desktop diagnostics for the packaged AppImage or Steam build. It is aimed at maintainers and issue triage, especially when a report involves KDE/Wayland click-through, transparent windows, or Chinese/Japanese/Korean input methods.

Related reports include [#396](https://github.com/Project-N-E-K-O/N.E.K.O/issues/396), [#1276](https://github.com/Project-N-E-K-O/N.E.K.O/issues/1276), and [#1279](https://github.com/Project-N-E-K-O/N.E.K.O/issues/1279).

## Runtime Layers

The Linux desktop release is assembled from two layers:

| Layer | Typical responsibility |
|-------|------------------------|
| Python backend bundle | API servers, storage, Steamworks, logs, runtime diagnostics |
| Electron desktop shell | AppImage entrypoint, Chromium flags, transparent windows, desktop IME integration |

When debugging a Linux-only desktop bug, first confirm which layer owns the failing behavior. Text cannot be committed into an Electron input field, a transparent window blocks clicks, or a toast ignores mouse input usually belongs to the Electron/AppImage layer. Server startup, storage, Steamworks, and Python import failures usually belong to the backend bundle.

## Quick Environment Snapshot

Ask reporters for the following before changing code:

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

For a running desktop shell process, inspect the real environment and arguments. Target the **main Electron browser process**, not a `projectneko_server` backend helper nor an Electron child process. The transparent window and the GTK input-method modules live in the main browser process, while renderer/GPU/zygote children (`--type=...`) do not own them, so `pgrep -n` (newest PID) would usually land on a child and make the checks below report missing IM/input-shape state by mistake. Match N.E.K.O's AppImage/AppRun specifically (avoid the generic `electron` term so an unrelated Electron app such as VS Code or Discord is not picked up), then keep the matching process(es) without a `--type=` flag:

```bash
mains=()
for p in $(pgrep -f 'AppRun|AppImage|N[.]E[.]K[.]O|n[.]e[.]k[.]o'); do
  tr '\0' '\n' < "/proc/$p/cmdline" | grep -q -- '--type=' || mains+=("$p")
done
if [ "${#mains[@]}" -eq 0 ]; then
  echo "N.E.K.O desktop shell (main Electron process) was not found" >&2
  exit 1
elif [ "${#mains[@]}" -gt 1 ]; then
  echo "Multiple candidates found; pick one, set pid=<PID> manually and re-run:" >&2
  for p in "${mains[@]}"; do
    printf '  %s  %s\n' "$p" "$(tr '\0' ' ' < "/proc/$p/cmdline")" >&2
  done
  exit 1
fi
pid="${mains[0]}"
# AppRun/AppImage are kept so a Steam-launched build whose command line lacks
# the literal app name is still matched, but they can also hit an unrelated
# app. If the sole match does not look like N.E.K.O, warn and verify the
# cmdline printed below before trusting the diagnostics.
if ! tr '\0' '\n' < "/proc/$pid/cmdline" | grep -qiE 'n[.]?e[.]?k[.]?o'; then
  echo "Warning: pid $pid matched only a generic AppRun/AppImage rule; confirm the cmdline below is N.E.K.O." >&2
fi
tr '\0' '\n' < "/proc/$pid/environ" | sort | grep -E 'DISPLAY|WAYLAND|GTK_IM|QT_IM|XMODIFIERS|Steam'
tr '\0' ' ' < "/proc/$pid/cmdline"; echo
```

For input-method library loading, also inspect mapped libraries:

```bash
grep -E 'im-fcitx|Fcitx5GClient|gtk|glib|ibus' "/proc/$pid/maps" | sort -u
```

## Click-Through On X11 And Wayland

Linux transparent-window click-through must be treated as compositor-specific.

On X11 or XWayland, the reliable path is an X11 input shape, such as a `ShapeInput` helper. This allows the visible pet or interaction rectangle to receive events while the rest of the transparent window lets clicks reach applications below it.

On Wayland, Electron APIs such as `setIgnoreMouseEvents` and `setShape` can be insufficient for a full-screen transparent pet window. Some compositors, including KDE Plasma in affected reports, can still route pointer input to the transparent Electron surface even when the app continuously updates the input region. If a Wayland session reports that all windows below N.E.K.O. are unclickable, do not assume the renderer's hit-test rectangles are wrong until X11/XWayland has been compared.

Suggested triage:

1. Reproduce with the packaged default.
2. Reproduce with X11/XWayland forced by the Electron shell, for example `--ozone-platform=x11`.
3. Confirm whether the process has an X11 display and the expected X11 input-shape helper logs.
4. If X11 works and Wayland blocks input globally, keep the issue scoped to Wayland compositor/Electron input-region behavior rather than backend startup.

## Steam And CJK Input Methods

CJK input in the Steam build can fail even when the same desktop environment works in a normal terminal launch. The Steam runtime can change inherited environment variables and library resolution, and Chromium may fall back to XIM.

The baseline input-method environment for Fcitx should include:

```bash
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
export INPUT_METHOD=fcitx
```

For X11/XWayland desktop builds, prefer native GTK IM modules over XIM when possible. XIM can show a candidate window but still fail to commit text into Electron text fields. A successful native Fcitx5 path normally shows `im-fcitx5.so` and `libFcitx5GClient.so.2` mapped in the Electron process.

If native Fcitx fails only inside Steam, check for GLib version mismatches — but run the check with the **Steam runtime's** library resolution, not your plain terminal's. `ldd -r` resolves relocations against the current environment, so a normal-terminal run can report a false clean result and hide exactly the Steam-only mismatch this section is diagnosing. Import `LD_LIBRARY_PATH` from the Steam-launched N.E.K.O process (`$pid` from the snapshot above), or run the check from a shell started by Steam:

```bash
export LD_LIBRARY_PATH="$(tr '\0' '\n' < "/proc/$pid/environ" | sed -n 's/^LD_LIBRARY_PATH=//p')"
ldd -r /path/to/im-fcitx5.so | grep -E 'not found|undefined symbol|glib|gobject'
```

Known failure signatures include:

```text
undefined symbol: g_once_init_leave_pointer
Loading IM context type 'fcitx' failed
GLib version too old
```

These errors mean the module was found but could not initialize against the libraries visible inside the Steam runtime. A robust packaged fix should avoid depending on an arbitrary host GTK/Fcitx module that was compiled against a newer GLib than the Steam runtime provides. Practical options are:

1. Bundle a compatible GTK IM module and its required libraries with the Electron shell.
2. Generate a `GTK_IM_MODULE_FILE` cache that points at the bundled module.
3. Keep `LD_LIBRARY_PATH` narrow so only the required compatible libraries are preferred.
4. Log the selected IM module path and initialization failure early, before users only see "cannot type Chinese".

## What To Include In A Bug Report

For click-through bugs, include:

- Desktop environment and session type.
- Whether X11/XWayland and Wayland behave differently.
- Electron command-line flags, especially `--ozone-platform`.
- Logs around input-shape or mouse-through initialization.

For input-method bugs, include:

- Input method framework and version, such as Fcitx5 or IBus.
- The process environment variables listed above.
- Whether the candidate window appears.
- Whether committed text reaches the chat input.
- `ldd -r` output for the selected GTK IM module if `GTK_IM_MODULE=fcitx` or `ibus` is used.

Keeping these reports separated makes fixes safer: click-through is mostly window/compositor behavior, while CJK input is mostly AppImage/Steam environment and native-library loading.
