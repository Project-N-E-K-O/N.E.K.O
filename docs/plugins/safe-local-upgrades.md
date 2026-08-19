# Rollback-safe local plugin upgrades

N.E.K.O. treats a second import of the same logical plugin as a replacement, not as a request to create a suffixed copy such as `my_plugin_1`. Package installation requires the directory under `payload/plugins/` to exactly match `[plugin].id`. The version string is display and release metadata; two packages with the same version but different bytes are still different replacement inputs.

This page is the canonical maintainer reference for local package replacement. Plugin-specific migration notes should link here instead of copying the transaction rules.

## User flow

The Plugin Manager always requests an install plan before changing files. The plan has one of three actions:

| Action | Meaning | User-visible result |
| --- | --- | --- |
| `install` | No installed plugin occupies the target identity or directory. | Install immediately. |
| `upgrade` | Exactly one installed plugin matches the packaged identity and target directory. | Show the current and target versions and require explicit confirmation. |
| `blocked` | The package cannot be installed without an ambiguous or unsafe replacement. | Stop before modifying the installation. |

An upgrade confirmation includes a token derived from the package bytes, destination path, and a recursive snapshot of the complete installed target. The snapshot covers relative paths, regular-file contents, and symbolic-link targets without following links. The server rebuilds the plan before installation. If the package or any file in the installed target changed after confirmation, the token no longer matches and the replacement is rejected.

## Installation ownership

The install plan reports one of three target ownership states:

| Ownership | Meaning | Replacement behavior |
| --- | --- | --- |
| `new` | No target exists. | A new managed user installation can be created. |
| `managed` | The target is recorded in N.E.K.O.'s installation inventory. | A normal replacement confirmation is required. |
| `unmanaged` | A directory exists, but N.E.K.O. has no installation claim for it. This commonly means a manually copied development checkout. | The UI must explicitly warn that local source, vendored dependencies, and assets will be replaced. The full-target confirmation snapshot protects edits made after the dialog opens. |

Do not use the installed application's user-plugin directory as the primary working copy of a plugin repository. Develop in a source checkout or standalone repository, build a `.neko-plugin`, and import that artifact for installation testing. Importing over an unmanaged directory is intentionally possible, but it is a destructive promotion into a managed installation after explicit confirmation.

For one logical plugin ID, only one candidate is selected for execution. A managed user installation may override the built-in candidate; N.E.K.O. does not create a second executable ID by suffixing the directory. Removing that user installation removes its payload claim and falls back to the built-in candidate when one exists. The shipped built-in files are not physically deleted.

Top-level `config/`, `data/`, and `cache/` directories receive preservation handling when a user overlay is replaced or removed and when an old manifest-less state-only target is adopted. For compatibility with historical plugins, N.E.K.O. records hashes for package-owned files shipped in these directories. Unmodified package-owned files follow the new package, while files created by the user or plugin runtime are preserved. If a package-owned file was modified locally and the new package also claims that path, replacement stops with `PLUGIN_PACKAGE_STATE_CONFLICT` and restores the previous version. New plugins should still place immutable assets under a clearly package-owned path such as `resources/` and reserve `data/` for mutable runtime state.

The ownership receipt is host-managed metadata in `plugin-installations.json`; it is not written into the plugin source directory and does not require a new package format. This mechanism distinguishes file ownership, but it is not a general data-schema migration transaction. Plugins must not assume that N.E.K.O. provides `pre_upgrade`, `migrate`, or business-data rollback hooks.

## Upgrade transaction

For a confirmed upgrade, the server performs these steps in order:

1. Determine whether the plugin is currently running.
2. Stop it when necessary.
3. Move the existing plugin directory and package profile directory to timestamped backups.
4. Install the package into the original executable directory.
5. Validate that the installed plugin ID and directory still match the confirmed plan.
6. Apply the package-state ownership receipt: update unmodified package assets, preserve runtime-created files, and reject locally modified asset collisions.
7. Merge preserved profile contents into the new profile directory.
8. Restart the plugin when it was running before the upgrade.
9. Atomically record the new installation claim and ownership receipt.
10. Remove backups after the new installation is valid and running.

Backup cleanup failures are warnings after a successful upgrade; they do not roll back a valid installation.

## Failure and rollback

Failures during backup, installation, validation, profile preservation, or restart trigger reverse-order restoration of every transaction target. A plugin that was running before the upgrade is restarted from the restored installation when possible.

The API reports rollback state separately from the upgrade failure:

| `rollback_status` | Meaning |
| --- | --- |
| `not_needed` | Upgrade completed; no rollback ran. |
| `completed` | Upgrade failed, and the previous plugin/profile state was restored. |
| `incomplete` | Upgrade failed, and at least one directory or running-state restoration step also failed. Manual inspection is required. |

The Plugin Manager must never present `incomplete` as a successful recovery.

## Blocked cases

The planner fails closed when it finds any of the following:

- a bundle contains an installed plugin or colliding executable directory;
- `[plugin].previous_ids` names a legacy plugin that is still installed;
- the destination directory contains a different plugin ID;
- a directory under `payload/plugins/` differs from the `[plugin].id` inside its `plugin.toml`;
- the same plugin ID is installed in multiple directories;
- a single-plugin package does not contain exactly one plugin;
- an executable directory name, package ID, or configured root escapes its allowed root.

Bundles with conflicts are not upgraded transactionally as a group. Upgrade each plugin using a single-plugin package.

## Stable identity and renamed plugins

Keep these values aligned for every executable plugin:

```toml
[plugin]
id = "my_plugin"
entry = "plugin.plugins.my_plugin:MyPlugin"
previous_ids = ["old_plugin_id"] # optional collision guard
```

The installation directory and archive payload directory must both be `my_plugin`. `previous_ids` is an install-time guard: it prevents accidental side-by-side installation while a legacy ID is present. It is not a runtime alias and does not migrate or delete old data automatically.

Market installation also binds the Market-declared plugin ID and version to the packaged `plugin.toml`. An ID or version mismatch is rejected before the plugin is committed or activated. A client-provided conflict policy cannot authorize a renamed executable copy.

## API contract

- `POST /plugin-cli/install-plan` returns the action, identity, versions, block reason, legacy IDs, and confirmation token.
- `POST /plugin-cli/install` performs a first install directly, or requires `confirm_upgrade=true` plus the current `confirmation_token` for an upgrade.
- A blocked plan returns `PLUGIN_INSTALL_BLOCKED` without changing files.
- A missing confirmation returns `PLUGIN_UPGRADE_CONFIRMATION_REQUIRED`.
- A stale token returns `PLUGIN_UPGRADE_PLAN_CHANGED`.
- A locally modified package-owned state file returns `PLUGIN_PACKAGE_STATE_CONFLICT` after restoring the previous version.
- A failed transaction returns `PLUGIN_UPGRADE_ROLLED_BACK` with `stage` and `rollback_status` details.

Both endpoints require administrator authorization. User-facing errors must not expose package contents, configuration values, credentials, confirmation tokens, or unrestricted local paths.

## Maintainer map

| Responsibility | Canonical implementation |
| --- | --- |
| Plan classification and confirmation token | `plugin/server/application/plugin_cli/install_plan.py` |
| Transaction, backup, restore, and restart | `plugin/server/application/plugins/upgrade_support.py` |
| Install orchestration and path policy | `plugin/server/application/plugin_cli/service.py` |
| HTTP request/response models | `plugin/server/routes/plugin_cli.py` |
| Plugin Manager confirmation and result messages | `frontend/plugin-manager/src/composables/usePackageManager.ts` |

## Validation

Changes to this flow should cover at least:

- first install, confirmed upgrade, cancelled confirmation, and stale-token rejection;
- package, directory, legacy-ID, duplicate-installation, and bundle conflicts;
- failures at backup, install, validation, profile preservation, restart, and cleanup;
- complete and incomplete rollback reporting;
- plugin and profile restoration, including a plugin that was running before the upgrade;
- package-owned state assets changing across releases, runtime-created files, and locally modified package-asset conflicts;
- Plugin Manager behavior and locale-key parity.

Use the focused backend suites under `plugin/tests/unit/server/`, the CLI workflow integration tests, the Plugin Manager Vitest suite, TypeScript type checking, and the frontend i18n check.
