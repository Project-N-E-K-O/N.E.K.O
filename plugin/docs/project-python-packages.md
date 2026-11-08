# Workbench Python dependencies

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
  An explicitly empty dependency list also clears the old vendor contents.
- Existing URL, Git and local source declarations are retained verbatim; change
  these in an editor. Unsupported lossless TOML layouts are reported explicitly.
- Declaration writes preserve surrounding TOML content and comments, use the
  dependency filesystem lock and reject stale file revisions.

Stop the project's plugin before synchronizing. The project remains reserved
until the task has reconciled the resulting vendor and plugin metadata. Starting,
reloading, building, publishing, prechecking and removing that project respect the
reservation. Other projects can continue to operate.

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

An explicit check walks active dependency metadata, including extras and markers,
and applies the existing Market file restrictions to vendor files selected for
packaging. A successful check is tied to its declaration revision and installed
inventory. Test the plugin's actual functions after changing dependencies:
distribution metadata does not exercise imports and runtime paths.

## Task lifetime and tools

Tasks have bounded logs, real execution stages, a time budget and cancellation.
The browser reconnects to the current task after navigation or refresh. Task
history is in server memory; after a server restart, disk contents remain the
source of truth. Canceling terminates installer subprocess trees. The existing
staging, backup and recovery rules protect ordinary vendor directories; linked
or mounted vendors retain their documented in-place update limitations.

Source servers use their Python interpreter and existing uv, with the existing
pip fallback when uv is unavailable. A packaged server prepares a matching
CPython interpreter in application data. If uv is missing, an explicit sync can
download a pinned uv wheel from PyPI, verify its SHA-256 and extract only the
executable and licenses. Python preparation uses `--no-bin --no-registry`, so it
does not register a system interpreter. Opening the tab performs no downloads.

## Implementation

- `plugin/neko_plugin_cli/core/python_packages.py`: local inspection, checks and
  lossless declaration updates.
- `plugin/neko_plugin_cli/core/vendor_sync.py`: shared synchronization, filesystem
  ownership, staging and replacement; the CLI is a thin adapter.
- `plugin/neko_plugin_cli/core/market_files.py`: shared Market file rules.
- `plugin/server/application/plugin_project_python_packages.py`: project identity,
  task lifetime, cancellation and runtime reconciliation.
- `plugin/server/infrastructure/python_package_tools.py`: installer discovery and
  application-owned tool preparation.

The `/plugin-projects/{project_id}/python-packages` routes expose local state,
requirement writes and synchronization/check tasks. Existing admin and mutation
guards apply. Project directories are resolved from the server-owned project
registry, rather than accepting arbitrary installation destinations.
