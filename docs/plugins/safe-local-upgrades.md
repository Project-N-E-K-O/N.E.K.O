# Rollback-safe local plugin upgrades

N.E.K.O. treats a second import of the same executable plugin as an upgrade, not as a request to create a suffixed copy such as `my_plugin_1`. Executable plugin directories must continue to match the Python package referenced by `[plugin].entry`; changing only the directory name produces a package that the registry cannot load.

This page is the canonical maintainer reference for local package replacement. Plugin-specific migration notes should link here instead of copying the transaction rules.

## User flow

The Plugin Manager always requests an install plan before changing files. The plan has one of four actions:

| Action | Meaning | User-visible result |
| --- | --- | --- |
| `install` | No installed plugin occupies the target identity or directory. | Install immediately. |
| `upgrade` | Exactly one installed plugin matches the packaged identity and target directory. | Show the current and target versions and require explicit confirmation. |
| `override_builtin` | A single-plugin Market package safely replaces the effective built-in source with the same identity. | Show the built-in and Market versions and require explicit confirmation. |
| `blocked` | The package cannot be installed without an ambiguous or unsafe replacement. | Stop before modifying the installation. |

An upgrade confirmation includes a token derived from the package bytes, destination path, and installed `plugin.toml`. The server rebuilds the plan before installation. If the package or installed target changed after confirmation, the token no longer matches and the upgrade is rejected.

## Code, state, and effective source

User plugin code is installed under `.neko-plugin-installations/plugins`; runtime state remains under `plugins/<plugin_id>/{config,data,cache}`. Installation, upgrade, rollback, and ordinary uninstall never move, copy, replace, or delete that state directory. Configured executable and state roots that resolve to the same path, or with either root nested inside the other, fail closed with `PLUGIN_EXEC_STATE_ROOT_COLLISION`.

The registry selects exactly one effective source for each declared plugin ID: user installation first, then built-in. A Market override shadows the built-in plugin without renaming either source. Uninstalling the Market copy removes only user code and its package profile, then refreshes the registry so the built-in copy becomes effective again.

Legacy user code found in the former state-root layout is copied through validated staging into the new executable root before registration. The source is retained, and an atomic layout ledger prevents an uninstalled migrated plugin from being resurrected on a later startup.

## Upgrade transaction

For a confirmed upgrade, the server performs these steps in order:

1. Determine whether the plugin is currently running.
2. Stop it when necessary.
3. Move the existing plugin directory and package profile directory to timestamped backups.
4. Install the package into the original executable directory.
5. Validate that the installed plugin ID and directory still match the confirmed plan.
6. Merge preserved profile contents into the new profile directory.
7. Restart the plugin when it was running before the upgrade.
8. Remove backups after the new installation is valid and running.

Backup cleanup failures are warnings after a successful upgrade; they do not roll back a valid installation.

## Failure and rollback

Failures during backup, installation, validation, profile preservation, or restart trigger reverse-order restoration of every transaction target. A plugin that was running before the upgrade is restarted from the restored installation when possible.

Built-in source switching has its own transaction. It promotes user code and the package profile, records the Market source, refreshes the registry, and starts the Market version only after validating the plan again. Failure removes only artifacts created by that transaction, restores the lock snapshot and effective built-in source, and restores the previous running state. Runtime state is never a rollback target.

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
- the same plugin ID is installed in multiple directories;
- a single-plugin package does not contain exactly one plugin;
- an executable directory name, package ID, or configured root escapes its allowed root.
- `override_identity_mismatch`: package ID, plugin ID, archive directory, or builtin directory differ;
- `override_previous_ids_not_supported`: a builtin override declares `[plugin].previous_ids`;
- `override_entry_missing`: the declared entry module is absent from the package;
- `override_builtin_identity_mismatch`: the builtin manifest ID differs from its canonical ID;
- `multiple_builtin_sources`: more than one builtin directory declares the same plugin ID.

Bundles with conflicts are not upgraded transactionally as a group. Upgrade each plugin using a single-plugin package.

## Stable identity and renamed plugins

Keep these values aligned for every executable plugin:

```toml
[plugin]
id = "my_plugin"
entry = "plugin.plugins.my_plugin:MyPlugin"
previous_ids = ["old_plugin_id"] # optional collision guard
```

The installation directory must be `my_plugin`. `previous_ids` is an install-time guard: it prevents accidental side-by-side installation while a legacy ID is present. It is not a runtime alias and does not migrate or delete old data automatically.

## API contract

- `POST /plugin-cli/install-plan` returns the action, identity, versions, block reason, legacy IDs, and confirmation token.
- `POST /plugin-cli/install` performs a first install directly, or requires `confirm_upgrade=true` plus the current `confirmation_token` for an upgrade.
- A blocked plan returns `PLUGIN_INSTALL_BLOCKED` without changing files.
- A missing confirmation returns `PLUGIN_UPGRADE_CONFIRMATION_REQUIRED`.
- A stale token returns `PLUGIN_UPGRADE_PLAN_CHANGED`.
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
| Legacy layout migration | `plugin/server/application/plugins/layout_migration.py` |
| Built-in source switching and rollback | `plugin/server/application/plugins/source_switch.py` |

## Validation

Changes to this flow should cover at least:

- first install, confirmed upgrade, cancelled confirmation, and stale-token rejection;
- package, directory, legacy-ID, duplicate-installation, and bundle conflicts;
- failures at backup, install, validation, profile preservation, restart, and cleanup;
- complete and incomplete rollback reporting;
- plugin and profile restoration, including a plugin that was running before the upgrade;
- Plugin Manager behavior and locale-key parity.

Use the focused backend suites under `plugin/tests/unit/server/`, the CLI workflow integration tests, the Plugin Manager Vitest suite, TypeScript type checking, and the frontend i18n check.
