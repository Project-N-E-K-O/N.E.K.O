# Desktop silent startup: Electron integration proposal

Status: implementation and tests prepared against an installed 0.9.1 desktop
package; integration into the Electron source repository is pending.

N.E.K.O.'s loading window is always on top, independently of the character's
Always on Top setting. A user who launches the app at sign-in can therefore have
the startup artwork cover their current work. Add an optional **Silent startup**
switch under **Settings → General → System**, immediately after Launch at Startup.

## Repository boundary

The public [desktop build workflow](../../.github/workflows/build-desktop.yml)
checks out `Project-N-E-K-O/N.E.K.O.-PC` for the Electron frontend. The public
[Tauri README](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri#readme) also directs
Electron-specific changes to that repository. On 2026-11-04, that repository
returned HTTP 404 to this contributor. The source paths below are from the
installed desktop package, whose package metadata declares MIT; they are not
Python backend or shared Web UI paths in this repository.

This proposal carries a small, applicable source patch and tests for desktop
maintainers to review and transfer. Merging these documents alone does **not**
change the Electron release. No desktop build workflow or release ref is changed.
The current Electron source revision and cross-platform CI must be checked by a
maintainer with access before shipping.

## Behavior

- Persist a strict boolean `silentStartup` in the existing desktop configuration.
  Missing, malformed and fresh-install values default to `false`, preserving the
  existing startup experience. Both manual launches and sign-in launches use it.
- When enabled, return from `createLoadingWindow()` **before allocating a
  BrowserWindow**. This prevents a brief visible/focused splash and avoids
  delayed loading callbacks recreating it.
- Continue backend discovery/startup and create the existing pet/chat windows
  when ready. Silent startup does not mean hiding the character after startup.
- Keep the original error dialog, permission requests and recovery paths.
  The feature only suppresses the loading artwork. Existing no-window status
  updates and cleanup already tolerate a null loading window.
- Apply preference changes on the next launch, without restarting immediately or
  changing login items, character always-on-top behavior, window mode or audio.

## Settings and persistence

The renderer uses the existing settings IPC contract and trusted-sender checks.
The main-process snapshot includes `silentStartup`; its patch handler accepts
only booleans, saves a copy before publishing the in-memory change, and reports
failure to the existing settings controller. Failed or thrown writes leave the
live preference unchanged. Detailed configuration logging is disabled for this
write.

The switch uses the existing card/toggle design. An associated label and
help/status description support screen readers; a visible keyboard focus ring
supports keyboard navigation. During a save it is disabled to prevent repeated
writes. Success states explain that the next launch is affected; errors restore
the authoritative snapshot and display a retry message. Existing settings search
finds the option. All eight desktop locales are included: Simplified/Traditional
Chinese, English, Japanese, Korean, Russian, Spanish and Portuguese.

## Patch and source provenance

- [Electron source patch](desktop-silent-startup/electron-silent-startup.patch)
- [Source file hashes](desktop-silent-startup/source-manifest.json)
- [Node regression tests](desktop-silent-startup/startup.test.cjs)
- [Isolated Electron UI test](desktop-silent-startup/electron-ui.cjs)

Only five desktop files change: `src/main.js`,
`src/main/desktop-settings-actions.js`, `src/main/tray-settings-pet-locales.js`,
`src/renderer/settings/settings.js`, and `src/renderer/settings/settings.css`.
The manifest identifies the exact before/after contents; an installed package
baseline must not be assumed to equal current upstream HEAD.

From the matching Electron source checkout:

```sh
git apply --check /path/to/electron-silent-startup.patch
git apply /path/to/electron-silent-startup.patch
NEKO_DESKTOP_SOURCE="$PWD" node --test /path/to/startup.test.cjs

# With Electron 42.6.1 and an untouched source copy for the before screenshot:
NEKO_DESKTOP_SOURCE="$PWD" \
  NEKO_DESKTOP_BASE_SOURCE=/path/to/untouched-desktop-source \
  electron /path/to/electron-ui.cjs
```

If the preflight fails, port the five small changes onto current source rather
than forcing a patch or replacing whole files. The tests need the Electron
source modules; they are not runnable against this Python repository alone.

## Validation

Ten Node tests passed against the patched desktop source, covering default and
malformed values, persistence across action recreation, preservation of unrelated
configuration, rejected/thrown writes, boolean validation, absent configuration,
zero splash allocation, the original non-silent allocation path, null-window
status/cleanup, and all eight locales.

A Chromium fixture and then an isolated Electron 42.6.1 fixture rendered the
actual before/after settings source and CSS,
with an IPC adapter connected to the actual desktop settings action module and
synthetic configuration. The Electron fixture used real ipcMain/ipcRenderer
messages and its own user-data directory; it also verified rejected IPC
rollback and disabled double clicks during a save. The fixtures verified default-off, pending-disabled, on/off saves,
failed-write rollback, keyboard activation, reload hydration, search, all eight
locales without horizontal overflow, and English dark mode. These fixtures are
**not** a test of production Electron IPC sender validation or OS login items.

A local macOS arm64 0.9.1 installation was updated with the five-file patch and
restarted with silent startup enabled. The runtime logged
`[LoadingWindow] skipped: silent startup enabled`, then confirmed the backend
ready and created the normal desktop windows. The archive was checked file by
file: 3,095 packed entries, with only the five intended source files changed;
16 unpacked entries retained their original metadata and files. The original
archive was backed up. Windows/Linux behavior, first-run permissions, injected
startup failures and OS login launches still need native integration testing.

## UI evidence

Screenshots are kept on the fork evidence branch, outside the proposed upstream
source tree, following the repository screenshot-artifact policy.
All screenshots use synthetic preferences and a signed-out placeholder account;
no real conversations, credentials or user data are shown.

Before:

![Before: system settings without silent startup](https://raw.githubusercontent.com/lgw2023/N.E.K.O/ff2c702c17f3e7d7b7a4a6a6ba692e1f80f3a891/docs/design/desktop-silent-startup/before.png)

After enabling:

![After: silent startup enabled and saved](https://raw.githubusercontent.com/lgw2023/N.E.K.O/ff2c702c17f3e7d7b7a4a6a6ba692e1f80f3a891/docs/design/desktop-silent-startup/after-enabled.png)

Rejected configuration write:

![Failed save restores the previous switch state](https://raw.githubusercontent.com/lgw2023/N.E.K.O/ff2c702c17f3e7d7b7a4a6a6ba692e1f80f3a891/docs/design/desktop-silent-startup/save-error.png)

English, dark appearance:

![English dark appearance](https://raw.githubusercontent.com/lgw2023/N.E.K.O/ff2c702c17f3e7d7b7a4a6a6ba692e1f80f3a891/docs/design/desktop-silent-startup/locale-en.png)

## Acceptance before an official release

1. Transfer the patch to current `N.E.K.O.-PC` source and run its own CI.
2. Verify on macOS, Windows and Linux that missing/false preserves the splash,
   true creates no splash, and both paths still create the configured character.
3. Verify manual and OS login launches, repeat launches during startup, remote
   backend operation and a deliberate backend failure. Failure dialogs must
   remain available with the splash suppressed.
4. Verify write failures and keyboard/screen-reader behavior through the real
   Electron settings IPC, including rejected senders.
5. Build/sign through the normal release workflow. A local archive patch is
   verification evidence, not an official signed distribution.
