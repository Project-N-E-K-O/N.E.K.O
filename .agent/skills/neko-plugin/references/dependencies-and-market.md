# Dependencies and Market

How the host loads third-party packages, what a Market release must look
like, and why a green local `check` does not mean the Market accepts the
plugin. Every rule below names the code it comes from; when this page and the
code disagree, the code wins.

## Dependencies

- **Declare them in `pyproject.toml` `[project].dependencies`.** `check`
  rejects `requirements.txt` for packages
  ([validate_cmd.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/neko_plugin_cli/commands/validate_cmd.py)).
- **Only `vendor/` counts.** When the plugin starts, the host checks the
  declared dependencies against the plugin's own `vendor/` directory only, not
  against the host's Python environment; anything missing fails the start
  with `MissingPythonDependencies`
  ([python_dependencies.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/core/python_dependencies.py),
  [registry.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/core/registry.py)).
  Do not lean on a package that merely happens to be importable from the host:
  undeclared, it is not shipped with the plugin and can disappear with any
  N.E.K.O update.
- **The host never installs anything.** `vendor/` is filled by
  `neko-plugin sync <plugin> --clean`, which removes the old `vendor/` first
  so dropped dependencies do not linger. Run it after every dependency change,
  then reload the plugin. Without a source checkout there is no `sync`: tell
  the user, and either drop the dependency or move to a checkout.
- **Pure Python only.** The Market release workflow rebuilds `vendor/` with
  `sync --clean` on a Linux runner
  ([plugin-market-release.yml](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/.github/workflows/plugin-market-release.yml)),
  so a compiled wheel would ship Linux binaries to Windows and macOS users
  (and binary files are on the Market's reject list below). Prefer a pure-Python library, or
  call a service over HTTP.
- **Do not commit `vendor/`.** The generated `.gitignore` excludes it; the
  release workflow and `precheck` regenerate it, so a committed copy only adds
  stale, possibly binary files.

## Market Identity

From [release_cmd.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/neko_plugin_cli/commands/release_cmd.py)
and the Market workflows
([plugin-market-release.yml](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/.github/workflows/plugin-market-release.yml)):

| Item | Rule | Checked by |
|---|---|---|
| Plugin id | `^[a-z][a-z0-9_]*$`, at most 100 characters | `check -r` (error for both); the Market workflows reject an id not matching the pattern before anything else |
| Version | `[plugin].version`, at most 50 characters | `check -r --market-release` (error) |
| Release tag | `v<version>` (a leading `v`/`V` is stripped) and equal to `[plugin].version`; the generated release workflow runs only on `v*` tags | the Market workflows fail the release; `check -r --market-release` errors when `GITHUB_REF_NAME` is set and differs (warns when it is unset) |
| GitHub repository | conventionally `n.e.k.o_plugin_<id>` | `check -r --market-release` (warning only) |
| Release workflow | `.github/workflows/release.yml` at the repository root | `check -r --market-release` (error) |

The id is permanent: users' data, config, and Market listing hang off it.
Pick it once (see the Identity Lock in `core-plugin-contract.md`).

## Size and File Limits

The CLI mirrors the limits of the Market's security scan
([release_cmd.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/plugin/neko_plugin_cli/commands/release_cmd.py)).
That scan is currently paused on the Market side (it uses its earlier review
flow), so these limits are not enforced today; it can be switched back on
without a plugin update, so stay within them:

| Limit | Value |
|---|---|
| One file | 2 MiB (common media, font, and model assets are exempt) |
| Package (`.neko-plugin`) | 32 MiB |
| Files and directories in the source archive or package | 5000 |
| Unpacked package | 128 MiB |
| Binary files (compiled modules, executables, archives, wheels) | none allowed |

Large assets belong outside the repository (download them at runtime into
`self.cache_path(...)`) or in a smaller format.

## A Green `check` Is Not a Market Pass

- Plain `check` validates the manifest and entries; it does not build, test,
  or apply release rules. `check -r` adds tests, the package build, and the
  hash check; `precheck` runs lint, `sync --clean`, and `check -r` the way
  Market CI does.
- The size and binary limits above are skipped locally unless the
  environment variable `NEKO_PLUGIN_MARKET_SECURITY_CHECKS=1` is set. Even
  then `precheck` scans only the built package; the committed repository
  (including files outside `<plugin_dir>`) is scanned only by
  `check -r --market-release`.
- The Market runs its own review on the published release, which the CLI does
  not reproduce. Report "local checks passed", never "the Market will accept
  it".

Release steps: [Plugin CLI guide](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/docs/plugins/cli.md).
