# Issue #2994 implementation contract

## Authority

- Worktree: `D:\Users\zheng\Desktop\code\neko-core\N.E.K.O-issue-2994-authoritative`
- Branch: `codex/issue-2994-uninstall-ownership`
- Baseline: `upstream/main` at `6b6812b3e8735c6aadbe4b158224aff42e136b86`
- Revalidated: `upstream/main` at `56f068d2`; intervening changes do not touch
  the plugin modules in this design.
- Issue: <https://github.com/Project-N-E-K-O/N.E.K.O/issues/2994>
- Latest scope review: <https://github.com/Project-N-E-K-O/N.E.K.O/issues/2994#issuecomment-5461019156>
- Canonical design: `docs/design/plugin-lifecycle-ownership-transactions.md`

All new #2994 code, tests, and durable documentation belong in this worktree.
Older N.E.K.O worktrees are read-only evidence until their diffs are classified.

## Current candidate and ownership contract

For each `plugin_id`, this task supports at most one builtin slot and one user
slot. The user slot channel is `manual`, `imported`, or `market`. Arbitrary
multiple user candidates are outside scope.

File ownership is derived from existing `root_id + channel`:

| Candidate | N.E.K.O may uninstall by default |
|---|---|
| builtin | No |
| manual | No |
| imported | Yes |
| market | Yes |

Do not add a persisted `managed` field. Manual takeover must use the existing
confirmation flow, explain that the directory will become N.E.K.O-managed, and
restore the original manual directory and lock entry on failure.

Unknown ownership is not managed. A missing/degraded install-source manager,
missing or removed entry, unknown channel, or entry/path identity mismatch must
fail closed before filesystem mutation. The existing path guard remains a
second independent check.

## Required implementation order

1. Centralize and test uninstall ownership using existing fields, including
   fail-closed unknown ownership.
2. Require plan-bound confirmation for manual takeover in both local-package
   and Market entry points; revalidate ownership under the operation lock.
3. Move package-file uninstall and profile transaction knowledge out of
   `lifecycle_service` behind a narrow structured operation with an explicit
   commit point.
4. Keep uninstall and replacement as distinct flows that share locking,
   lifecycle coordination, phase reporting, and compensation primitives.
5. Move constant lifecycle dependencies and common identity validation inside
   the replacement transaction boundary.
6. Remove the Market `dict[str, Any] + **kwargs` call path.
7. Confirm real compatibility consumers before changing runtime ID-conflict
   behavior.

## Pull request delivery contract

The user explicitly authorized combining the first two safety slices in the
current pull request. PR 3 and PR 4 remain sequential and must start from the
updated target branch after the combined safety PR is merged. Do not mix
investigation-only runtime ID work into these pull requests.

| Order | Suggested branch | Pull request title | Required outcome |
|---|---|---|---|
| PR 1+2 | `codex/issue-2994-uninstall-ownership` | `fix(plugin): enforce ownership and confirm manual takeover` | Uninstall is fail-closed; local-package and Market manual takeover require bound confirmation and restore manual ownership on failure. |
| PR 3 | `codex/issue-2994-uninstall-transaction` | `refactor(plugin): move uninstall into an installation transaction` | Code, installer-owned profile and source record share an explicit uninstall commit point; lifecycle no longer owns package-file transaction order. |
| PR 4 | `codex/issue-2994-replace-boundary` | `refactor(plugin): narrow plugin replacement boundary` | Replacement owns constant lifecycle dependencies, both callers pass only varying inputs, and Market no longer expands an untyped keyword dictionary. |

Runtime ID-conflict compatibility is a decision gate after PR 4, not a promised
PR 5. First identify concrete consumers and record the evidence in Issue #2994.
Only if a behavior change is justified should it receive a separate issue or
small pull request based on the then-current target branch.

### PR 1 hard scope

Allowed:

- add the final reusable ownership policy under the installation transaction
  boundary and call it from the current lifecycle delete path;
- validate the exact active `LockEntry`, manager health, entry/path identity,
  channel and the existing path guard;
- add stable domain errors and ownership tests for builtin, manual, imported,
  market and unknown/degraded states.

Excluded:

- manual takeover behavior or confirmation UI;
- code/profile staging changes;
- moving the existing delete implementation;
- Registry, CAS, schema or candidate-model work.

PR 1 must preserve successful imported/market uninstall behavior. It is a
safety fix, not the uninstall refactor.

### PR 2 hard scope

Allowed:

- represent manual takeover as an existing replacement action with
  `reason="manual_takeover"`;
- bind confirmation to package, target, current content and the exact
  install-source ownership snapshot;
- re-read and compare that evidence under `serialized_plugin_operation`;
- apply the same rule to local-package and Market entry points;
- add the required user copy to every locale currently supported by those
  surfaces;
- prove failed replacement restores the original manual directory and
  `LockEntry`.

Excluded:

- a persisted `managed` or adoption field;
- `force=true` manual deletion;
- the uninstall transaction migration;
- replacement-interface cleanup that is not required to bind confirmation.

### PR 3 hard scope

Allowed:

- add the narrow `uninstall_plugin(plugin_id)` transaction and structured
  result/error types;
- move the current authoritative profile ownership, safety, deferred-cleanup
  and legacy-record behavior without weakening it;
- stage code by same-filesystem rename, restore it before commit on failure,
  and clean only transaction-owned committed staging artifacts;
- make source update, refresh, preference semantics and rollback ordering
  explicit;
- reduce `PluginLifecycleService.delete_plugin()` to orchestration-boundary
  invocation, error mapping, events and response compatibility;
- delete the replaced lifecycle file-transaction helpers in this same PR.

Excluded:

- retained-profile deletion as a new user operation;
- a general rollback framework or arbitrary cleanup queue;
- replacement-interface changes;
- runtime ID-conflict changes.

### PR 4 hard scope

Allowed:

- move the current proven replacement behavior into the final transaction
  boundary without changing its filesystem semantics;
- internalize constant lifecycle dependencies and shared identity validation;
- replace the Market `dict[str, Any] + **kwargs` path with a typed direct call;
- migrate both callers and the existing replacement regression suite;
- delete the old `upgrade_support` production path after both callers move.

Excluded:

- a general package-management facade or coordinator;
- new install modes, storage formats or product behavior;
- runtime ID auto-rename policy changes.

Each pull request description must repeat its hard scope, required tests and
explicit exclusions. A failure in an unrelated base check is recorded with
base-SHA evidence and is not repaired inside these pull requests.

## Behavioral contracts

- Removing a user candidate and restoring builtin preserves enabled/auto-start.
- Removing the last code candidate clears runtime preferences.
- Ordinary uninstall continues to preserve `config/data/cache`.
- Before the uninstall commit point, failure restores the code directory,
  installer-owned profile and original install-source entry when possible.
- A completed filesystem rollback does not imply runtime restart or business
  data rollback; those outcomes are reported separately.
- Cleanup failure after the commit point is reported as pending cleanup and
  must not reverse a logically completed uninstall.
- Tests use temporary directories and deterministic synchronization, never real
  user plugin state or real network access.

## Compatibility contract

- Preserve existing route actions and response fields; new result detail is
  additive during migration.
- Manual takeover uses an existing replacement action with a specific reason,
  not a new persisted adoption state.
- Old clients that cannot provide bound confirmation must fail safely on a
  manual takeover attempt.
- Any new user-visible copy must update all currently supported locales.

## Explicit exclusions

- New Registry, CAS, JSONL audit log, or schema migration.
- Persisted `managed` state.
- Arbitrary multiple user candidates or a new directory layout.
- General business-data snapshot or rollback framework.
- End-to-end performance claims derived from the existing scan/TOML microbenchmark.
