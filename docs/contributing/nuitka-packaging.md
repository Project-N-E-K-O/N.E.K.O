# Nuitka Packaging Caveats

N.E.K.O ships its Python backend as a Nuitka standalone executable, then wraps
it in Electron for Windows distribution. Nuitka has a few default behaviors
that quietly break things if you don't know about them. **Read this before
adding new directories or dynamic imports.**

## Rule 1: Directories containing `.py` files must use underscore names

Python package names cannot contain hyphens, so
`--include-package=plugin.my-tool.public` is rejected by Nuitka. The natural
fallback — `--include-data-dir=plugin/my-tool` — is also a trap: Nuitka's
`--include-data-dir` **filters out `.py`, `.pyc`, `.pyd`, `.so`, `.dll`** and
other code suffixes by default (see the `default_ignored_suffixes` tuple in
`nuitka/freezer/IncludedDataFiles.py` of your installed Nuitka — it is the
upstream default, not project config). The bundled dist ends up with only
the non-code files (`.md`, `.json`), runtime imports raise
`ModuleNotFoundError`, and the build looks fine until end users open the
affected feature.

**Real bug**: `plugin/neko-plugin-cli/` historically held a `public/` Python
package. Server callers did `sys.path.insert(_CLI_ROOT)` then
`from public import ...`. In source mode it worked; in Nuitka standalone it
silently dropped the entire `public/` package, the embedded user plugin
server failed to start, and the plugin management UI was unreachable.

**Required form**: any directory holding `.py` source uses underscores and
has an `__init__.py`. If you need a hyphenated name for an external CLI tool,
expose it via `pyproject.toml [project.scripts]` mapping; keep the underlying
Python package name with underscores.

`tests/unit/test_no_hyphen_python_packages.py` enforces this at PR time.

## Rule 2: Don't mix `.py` source with `--include-data-dir`

If you ever genuinely need to ship `.py` source files as data (rare — usually
sandboxed runtime plugins), use `--include-raw-dir=` instead, which skips the
default suffix filter. For everything else, prefer
`--include-package=<dotted.name>` so Nuitka compiles the modules into the
binary.

## Rule 3: Built-in plugins must go through the shared staging step

Two build entrypoints exist:

- `build_nuitka.bat` — local maintainer script, **gitignored** (contains
  signing paths, machine-specific settings).
- `.github/workflows/build-desktop.yml` — CI build for Linux/macOS/Windows
  release artifacts.

Both must run `scripts/prepare_nuitka_plugins.py prepare` before Nuitka and
compile the generated `build_nuitka_launcher.py`. The helper applies every
plugin's `[tool.neko.build]` rules to a shared staging directory and emits
selective `--nofollow-import-to` directives for Python modules excluded by
those same rules. After Nuitka finishes, `prepare_nuitka_plugins.py install`
replaces `dist/Xiao8/plugin/plugins` with the filtered runtime payload.

Do not add `--include-data-dir=plugin/plugins=plugin/plugins` back to either
build: it bypasses the plugin rules and still drops `.py` files. Do not add a
blanket `--nofollow-import-to=plugin.plugins` either, because built-in plugins
are dynamically imported and must be compiled.

The CI workflow then runs `scripts/check_nuitka_dist.py --plugin-stage ...` to
verify that the installed plugin tree matches the filtered stage byte for
byte. New non-plugin assets still require explicit updates in both build
entrypoints and in the required-asset checker.

## Rule 4: Don't run the bundled exe casually for diagnosis

The launcher spawns multiple subprocesses (`memory_server`, `agent_server`,
`main_server`, plugin server, etc.). Killing only the launcher leaves
children alive holding file locks on `dist/Xiao8/`. The next build's
`rmdir /s /q dist\Xiao8` then partially fails, and the subsequent
`move dist\launcher.dist dist\Xiao8` lands the new build *inside* the
leftover directory rather than replacing it — producing a half-broken
nested bundle that boots but is missing config/static/templates.

For diagnosing packaging issues, prefer:

- `scripts/check_nuitka_dist.py dist/Xiao8` for asset inventory
- `grep -r <symbol> dist/Xiao8/` for content checks
- Log files in `data/` if you must run the exe — and explicitly kill all
  `projectneko_server`, `neko_main_server`, `neko_memory_server`,
  `neko_agent_server` processes afterwards.

## Defense in depth

The historical neko-plugin-cli bug (PR #1115, "rename neko-plugin-cli
→ neko_plugin_cli") sat in production for weeks because nothing alerted on
it. We now have three layers:

1. **Build-time check** — `scripts/check_nuitka_dist.py` runs in CI after
   Nuitka, verifying the dist root contains every critical directory, every
   built-in plugin has its `plugin.toml`, and the installed plugin payload
   exactly matches the filtered stage.
2. **Source-level lint** — `tests/unit/test_no_hyphen_python_packages.py`
   fails at PR time if any tracked directory with a hyphen name contains
   `.py` files.
3. **This document** — read before adding packaging-relevant code.
