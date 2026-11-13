# Desktop silent startup: Electron integration proposal

**Proposal**: implementation and tests prepared against an installed 0.9.1 desktop
package; integration into the Electron source repository is pending.

N.E.K.O.'s loading window is always on top, independently of the character's
Always on Top setting. A user who launches the app at sign-in can therefore have
the startup artwork cover their current work. Add an optional **Silent startup**
switch under **Settings → General → System**, immediately after Launch at Startup.

## Repository boundary

The public [desktop build workflow](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/.github/workflows/build-desktop.yml)
checks out `Project-N-E-K-O/N.E.K.O.-PC` for the Electron frontend. The public
[Tauri README](https://github.com/Project-N-E-K-O/N.E.K.O.-Tauri#readme) also directs
Electron-specific changes to that repository. On 2026-11-13, that repository
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
  delayed loading callbacks recreating it. A repeat launch or macOS Dock
  activation during startup explicitly reveals the current loading progress.
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

The production renderer's `patch()` returns `ipcRenderer.invoke(PATCH, ...)`
unchanged. In `src/main/unified-settings-window.js`, `handlePatch()` validates
that the sender is the settings window, catches action errors, and returns
`{ ok, snapshot: desktopSettingsActions.getSnapshot() }` for both accepted and
failed writes. Rejected senders receive `{ ok: false }` before any write.
`saveSilentStartup()` consumes that authoritative snapshot. A successful reply
without a snapshot merges the saved value into the existing snapshot; an IPC
rejection leaves it unchanged, including when it is absent, and enables retry.

## Startup feedback call sites

The following paths were inspected in the recorded desktop source and the
0.9.1 installation available on 2026-11-13:

| Path | Feedback with silent startup enabled |
| --- | --- |
| `createLoadingWindow()` → `updateLoadingWindowStatus()` | Initialize status before skipping window allocation; subsequent progress remains in memory. |
| `startPythonBackend()` in `backend-runtime.js` | Readiness waits and automatic retries publish progress. Terminal process, spawn, reported-failure and readiness-timeout errors reject to the main startup catch. |
| `app.whenReady()` → `startPythonBackend().catch(...)` | Clear the pending-start flag, hide any revealed splash, and await `loadFailureDialog.show()` before quitting. The dialog is independent of the splash. |
| `createLoadFailureDialog()` in `load-failure-dialog.js` | Show native `dialog.showMessageBox()`; if it rejects, use `dialog.showErrorBox()`. |
| Remote `waitForServerReady()` rejection | Still call `startMainWindow()`; the pet lifecycle's `did-fail-load` handler invokes `showLoadFailure` for a failed main-frame load. |
| `second-instance` / `activate` | The baseline only focused existing windows and had no activation handler. The patch calls `revealSilentStartupProgress()` to lazily create a splash while a silent start is pending, preserving its latest status. |
| `startMainWindow()` → `closeLoadingWindow()` | Clear pending startup before window handoff; later activation cannot resurrect a splash. |

Initial progress and automatic retry messages remain hidden by choice. Explicit
repeat launches reveal progress; terminal backend errors use the independent
native dialog. A settings change after startup does not create a loading window.

## Patch and source provenance

- Electron source patch: `desktop-silent-startup/electron-silent-startup.patch`
- Source file hashes: `desktop-silent-startup/source-manifest.json`
- Node regression tests: `desktop-silent-startup/startup.test.cjs`
- Isolated Electron UI test: `desktop-silent-startup/electron-ui.cjs`

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
  NEKO_STARTUP_TEST_OUTPUT=/path/to/external-test-output \
  electron /path/to/electron-ui.cjs
```

The UI fixture requires an untouched before-source directory and defaults to a
new system temporary output directory when no output path is provided.

If the preflight fails, port the five small changes onto current source rather
than forcing a patch or replacing whole files. The tests need the Electron
source modules; they are not runnable against this Python repository alone.

## Validation

Validation refreshed on 2026-11-13:

- `git apply --check` passed against the hash-recorded baseline. All five
  generated files match the manifest's after hashes.
- The current installed 0.9.1 source already contains the original silent-startup
  patch and additional local changes. In an extracted copy, reversing that
  original patch and applying the revised patch both passed without forcing or
  replacing files. The revised behavior tests also passed on that copy.
  `source-manifest.json` records the inspected contract modules and the current
  installation's compatibility hashes. Upstream `N.E.K.O.-PC` HEAD remains
  inaccessible (HTTP 404), so maintainers must repeat this check there.
- Twenty Node tests passed: persistence/recreation, rejected and thrown writes,
  strict booleans, combined silent creation/status/cleanup, repeated launch and
  activation, terminal failure callbacks, native-dialog fallback, missing
  snapshots, rollback/retry, and eight-locale copy.
- The isolated Electron 42.6.1 fixture runs the actual settings renderer, CSS,
  `createUnifiedSettingsWindowController()`, and settings actions. It checks
  production success/failure reply snapshots, rejected senders, pending/double
  clicks, write exceptions, transport rejection, keyboard focus restoration,
  reload hydration, and exact label/description/saving/saved/error text in all
  eight locales, with overflow checks and English dark mode.
- Startup tests execute the production event handlers and failure callback with
  controlled window/dialog dependencies. Full packaged backend failure,
  cross-platform startup and OS login behavior remain native acceptance items.

The earlier 2026-11-04 macOS arm64 restart of the original patch logged
`[LoadingWindow] skipped: silent startup enabled`, followed by backend readiness
and normal desktop-window creation. The revised patch was validated in isolated
source copies and Electron fixtures; the installed app was left unchanged.

Synthetic before/after UI screenshots are available in
[PR #3621](https://github.com/Project-N-E-K-O/N.E.K.O/pull/3621).

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
