# Plugin CLI and Debugging

The command reference and the debugging order. What each check covers and
how to test is in `plugin-checks-and-tests.md`; creating a plugin is in
`plugin-creation-workflow.md`.

## Invocation

The CLI runs only from a N.E.K.O source checkout: `uv run neko-plugin
<command> <plugin>` from the checkout root, where `<plugin>` is an id under
the checkout's plugin root or a path to `<plugin_dir>`. For a plugin project
created outside the checkout while a checkout is reachable (Route A in
`AGENTS.md`), use the command lines in its Verify block as written. Confirm
arguments with `--help`; help output may be preceded by app start-up log lines, which are
not a failure unless the command exits non-zero or prints `[FAIL]`.

Without a checkout (desktop build) no `neko-plugin` command runs: say so
instead of guessing. App buttons still work (they are not the CLI):
Start/Reload on the plugin page, its Logs tab, and Build package in the Plugin
studio. To run checks, offer the setup below.

## Setting Up Route A

A source checkout is the recommended way to develop plugins: every check runs
there. Ask the user first and do nothing until they agree; it installs tools
and downloads N.E.K.O's source. Source of these steps: the
[README quick start](https://github.com/Project-N-E-K-O/N.E.K.O#quick-start-source).

- Tools: [git](https://git-scm.com/downloads) and
  [uv](https://docs.astral.sh/uv/) (it fetches Python 3.11 if needed); to run
  N.E.K.O from source, also Node.js `^22.12.0 || >=24.0.0`.
- Time: for a non-developer, plan on roughly half an hour to an hour the first
  time, mostly installing tools and downloading (a rough estimate; it depends
  on the connection).

For checks only:

```bash
git clone --filter=blob:none https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync
```

Then run the Verify lines of the plugin's `AGENTS.md` from that folder,
replacing `<path-to-this-directory>` with the plugin's path.

To also try the plugin in the source version:

1. Fully quit the desktop app first; the two cannot run at the same time.
2. In the checkout: `./build_frontend.sh` (Windows: `.\build_frontend.bat`),
   then `uv run python launcher.py`.
3. Open the address the launcher prints in a browser.

The source version mostly uses the same user data as the desktop app, so
installed plugins and settings are usually still there.

## Commands

| Command | What it does |
|---|---|
| `init <id> --type plugin\|adapter --name "<Name>"` | scaffold a plugin (only when none exists yet) |
| `setup-repo <plugin>` | add missing support files (README, tests, `.gitignore`, `.vscode`, `--github-actions`) to an existing plugin |
| `create-repo <plugin>` | create the GitHub repository with `gh` and set it as `origin` (the Market needs a public repository) |
| `check <plugin>` | manifest, entries, dependencies; `-s/--strict` makes missing support files errors |
| `check -r <plugin>` | release check: strict check, tests in `tests/`, package build, hash verification; `--skip-tests`; `--market-release` adds repository, tag, and workflow rules |
| `lint <plugin>` | the pinned ruff check Market CI runs (`--ignore-noqa`: `# noqa` has no effect) |
| `sync <plugin> --clean` | rebuild `vendor/` from `pyproject.toml` |
| `precheck <plugin>` | lint, `sync --clean`, and `check -r`, as Market CI runs them |
| `build <plugin>` | build a `.neko-plugin` package into the CLI's own `target/` folder |
| `analyze <plugin> <plugin>...` | bundle candidates: ids, SDK versions, shared dependencies |
| `publish <plugin>` | push the release tag, wait for the GitHub Release, notify the Market |
| `install <package>` | installs nothing: prints that packages are imported through the Plugin Center and exits with code 2 |

Validation order before reporting done: `check` → `lint` → `sync --clean` →
`precheck`. Outside the checkout's built-in plugin folder, `check -r` and
`precheck` fail on uncommitted changes (CI builds the pushed commit, not the
working tree); do not commit for the user unasked, report the result.
Dependencies, Market rules, and limits: `dependencies-and-market.md`.

## Debugging Order

1. Is the plugin running, stopped, manual-start, crashed, or load-failed?
   Read its log (Logs tab) first.
2. Read `plugin.toml` and check the Identity Lock (`core-plugin-contract.md`).
3. Read the entry class and its decorators.
4. Run `check` unless the symptom clearly points elsewhere.
5. Match the symptom: start-up, loops, timers, reload in `runtime-model.md`;
   dependency errors in `dependencies-and-market.md`; the symptom table in
   `AGENTS.md`.
6. Reproduce with the smallest command or plugin test.
7. Fix only inside `<plugin_dir>`. If the cause is in N.E.K.O itself, stop
   and escalate (`execution-boundary.md`).

Host source, platform tests, and logs are read-only context. They are useful
when the plugin's behaviour contradicts these guides; the code wins.
