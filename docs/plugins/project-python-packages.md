# Workbench Python dependencies

> This page is maintained in English only for now.

The authoring workbench's **Dependencies** tab edits the registered project's
`pyproject.toml` and manages its own `vendor/`. The plugin keeps using the host
runtime. Source installations and packaged applications use the same dependency
inspection and transactional synchronization implementation.

## Authoring

- Add a package with a name, version constraint, extras and environment marker,
  or enter a complete PEP 508 declaration.
- Save declarations without installing, or save and start a synchronization task.
- Synchronization rebuilds `vendor/` from all declarations. Version ranges may
  resolve to newer versions, including related dependencies.
- Remove a declaration and synchronize to remove packages no longer needed.
  An explicitly empty dependency list also clears the old vendor contents
  (workbench sync and `neko-plugin sync --clean`; the syncs that
  `neko-plugin precheck` and `neko-plugin publish` run by default leave
  vendor/ alone then).
  A missing declaration preserves hand-maintained vendor files; dynamic dependency
  declarations are rejected before synchronization.
- Existing URL, Git and local source declarations are retained verbatim; change
  these in an editor. Unsupported lossless TOML layouts are reported explicitly.
- Invalid declarations can be inspected, but must be removed or corrected before
  saving the dependency list.
- Declaration writes preserve surrounding TOML content and comments, use the
  dependency filesystem lock and reject stale file revisions. Symlinked project
  files must be edited externally. Revision checks detect external edits through
  the final read; editors that do not use this lock can still write after it.

Stop the project's plugin before synchronizing. The project remains reserved
until the task has reconciled the resulting vendor and plugin metadata. Starting,
reloading, building, publishing, prechecking and removing that project respect the
reservation. Other projects can continue to operate.

The project reservation is narrower than "this source directory":

- It is keyed by the workbench project ID, which a data root assigns when the
  project is registered. Another N.E.K.O instance with a different data root
  (an isolated instance, or another OS user) that registers the same source
  directory holds a different reservation. Builds, publishes and syncs of that
  source in the two instances do not exclude each other.
- Two synchronizations of the same directory by one OS user are still kept
  apart by the dependency lock, which the CLI and the workbench both take on the
  plugin directory's identity. That lock lives in a per-user directory, so
  syncs started by different OS users are not serialized.
- The reservation does not see plugin hosts started by another instance, and it
  does not follow a source moved away from its registered path.

When sharing a source across instances, stop every host using it in every
instance and keep them stopped until synchronization finishes. Cross-instance
host-lifetime coordination is outside this feature.

## Inspection and checks

The initial view reads local declaration and distribution metadata only. It
distinguishes direct requirements from other installed packages; the latter can
include transitive dependencies and manually installed packages.

Installed packages are explored through a searchable dependency tree and a
separate details pane. The server resolves active environment markers and extras
once into an adjacency graph; the frontend expands paths lazily, marks shared
packages and stops cycles at an ancestor reference. Packages outside the declared
dependency closure remain accessible as other installed roots. Narrow screens
stack the tree above the details.

An explicit check walks active dependency metadata, including extras and markers.
A successful check is tied to its declaration revision and installed
inventory. Test the plugin's actual functions after changing dependencies:
distribution metadata does not exercise imports and runtime paths.

## Task lifetime and tools

Tasks have bounded logs, real execution stages, a time budget and cancellation.
Preparation checks the deadline between streamed chunks and caps request I/O
timeouts by the remaining budget. An already pending network read can take up to
its bounded timeout (at most 10 seconds) before that checkpoint runs.
The browser reconnects to the current task after navigation or refresh. Task
history is in server memory; after a server restart, disk contents remain the
source of truth. Canceling terminates installer subprocess trees. The existing
staging, backup and recovery rules protect ordinary vendor directories; linked
or mounted vendors retain their documented in-place update limitations.

Installer tools come from the shared plugin tool layer
(`plugin/server/infrastructure/plugin_tool_runtime.py`), which other packaged
plugin tools use as well. Source servers use their Python interpreter and
existing uv, with the existing pip fallback when uv is unavailable. A packaged
server prepares the tools on the first sync that has packages to install: it
downloads a pinned uv release from PyPI (SHA-256 checked against PyPI HTTPS
metadata; a compatible uv already installed is only a fallback when that
download fails, so on a slow network the fallback waits for that download to
fail), then installs the CPython release matching the application's
exact version and architecture with `--no-bin --no-registry`, so no system
interpreter is registered. Preparation can be canceled with the task and
counts against the task's time budget. The task reports the layer's phases
(downloading uv, checking uv, installing Python, checking Python; any other
phase shows as "Preparing") and, when it fails, the layer's error code. Opening the tab performs no downloads or
version-probe subprocesses. It reads the layer's status and shows a notice
only while the tools still need preparing, are being prepared, failed or are
unsupported; ready tools, and source checkouts, show no notice.

In a packaged build, installer children run with the layer's environment:
inherited `UV_*` variables and variables that redirect Python (`PYTHONPATH`,
`VIRTUAL_ENV` and the like) are removed. The package install itself
(`uv pip install`) gets the user's package index settings back
(`UV_INDEX_URL`, `UV_EXTRA_INDEX_URL`, `UV_DEFAULT_INDEX`, `UV_INDEX`), so a
PyPI mirror set that way still applies, as long as it needs no other `UV_*`
setting: credentials of a named index (`UV_INDEX_<NAME>_USERNAME` /
`_PASSWORD`), `UV_NATIVE_TLS`, `UV_INDEX_STRATEGY` and `UV_KEYRING_PROVIDER`
are still removed (credentials inside the index URL itself work). Preparing uv
and Python never sees any of them. The desktop build checks (in
`scripts/check_nuitka_dist.py`) that the pinned uv can download the build's
Python patch release. The tools live under `plugin-runtime/` in the
application data root, which storage migration treats as regenerable.

## Implementation

- `plugin/neko_plugin_cli/core/python_packages.py`: local inspection, checks and
  lossless declaration updates.
- `plugin/neko_plugin_cli/core/vendor_sync.py`: shared synchronization, filesystem
  ownership, staging and replacement; the CLI is a thin adapter.
- `plugin/server/application/plugin_project_python_packages.py`: project identity,
  task lifetime, cancellation and runtime reconciliation.
- `plugin/server/infrastructure/plugin_tool_runtime.py` (shared): uv and Python
  for packaged plugin tools; the dependency task calls `prepare_tool_runtime()`
  and the page reads `read_tool_runtime_status()`.

The `/plugin-projects/{project_id}/python-packages` routes expose local state,
requirement writes and synchronization/check tasks. Existing admin and mutation
guards apply. Project directories are resolved from the server-owned project
registry, rather than accepting arbitrary installation destinations.
