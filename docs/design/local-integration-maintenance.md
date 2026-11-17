# Maintaining a local integration branch

`scripts/maintain_local.py` keeps local commits and upstream history together
without replacing the checkout, automatically stashing changes, or publishing
anything. It has no user-specific paths or account settings. It requires Python
3.11, Git, and the official HTTPS repository configured as `origin` (Git transport
rewrites such as `insteadOf` are supported); build/verify also use
the existing Node and uv toolchains. Run it from a checkout, not an installed app.

Use a persistent `local/…` branch for personal changes and explicit file lists
when committing. Keep credentials, conversations, model downloads, and generated
build output outside Git. Public PR branches should contain one reviewable
feature, based on an identified official commit rather than the local branch.

```sh
uv run --no-sync python scripts/maintain_local.py status
uv run --no-sync python scripts/maintain_local.py backup --backups ../neko-backups
uv run --no-sync python scripts/maintain_local.py fetch
uv run --no-sync python scripts/maintain_local.py review
```

The backup contains a verified HEAD bundle, separate index/working patches, and
an untracked-file archive. The bundle includes only commits reachable from HEAD;
commits unique to other local branches are excluded even if those branches still
have valid refs. Back up those branches and ignored files separately. Unresolved
index conflicts are rejected before any backup is created. Backups preserve file
and index contents, not in-progress merge/rebase metadata. Store sensitive backups
privately. `review` pins both tips and the common ancestor and lists changed,
overlapping, and high-risk paths. It does not read
private file contents, change the index, or infer that nonoverlapping edits are
behaviorally compatible. Renames are expanded to both paths.

`merge --backups ../neko-backups` requires a clean `local/…` branch and backs it
up before merging the fetched official commit with `--no-commit`. Resolve each
conflict with both behaviors in mind. Inspect the staged diff and run relevant
tests before committing. `git merge --abort` returns to the pre-merge state;
there is no automatic ours/theirs resolution, reset, push, or deployment.

`build` rebuilds both frontends with their lockfiles and records the source commit
and output hashes in Git metadata. `verify --regression path/to/policy.json`
executes a trusted local manifest and records each result. Without the flag it
uses `local-desktop-ui/regression.json`, then the tracked example. The example
checks the maintenance tool and committed, staged and unstaged whitespace; it is
**not** a release gate for N.E.K.O. Add tests for changed upstream paths and local features. Verification
does not substitute for real desktop, audio, or visual acceptance.

Manifests require a non-empty `suites` list. Each suite has a unique filename-safe
`name`, a non-empty `command` array of string arguments, and an optional existing
`cwd` (relative to the checkout by default). The whole manifest is validated before
running commands. Verification compares HEAD, index entries, tracked differences,
and untracked paths and contents, including symlink targets. Ignored artifacts are
excluded. A pending merge requires tracked working files to match the index before
testing; stage resolved changes first. Source changes during tests fail verification.
Every attempt invalidates the previous `verified.json`; only a successful run
publishes a new success receipt.

To recover a backup, clone its `history.bundle` into a separate directory. Apply
`staged.patch` with `git apply --index` only if it is non-empty, then apply
`working.patch` only if it is non-empty. Finally unpack the untracked archive.
Inspect the receipt first and preserve the current checkout.
Application packaging and deployment remain the responsibility of the existing
platform build workflows or a separately maintained local release process.
