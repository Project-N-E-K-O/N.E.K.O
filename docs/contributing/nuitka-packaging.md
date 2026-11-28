# Nuitka Packaging

The tracked packaging contract is the desktop workflow plus its preparation and verification scripts. There is no tracked `build_nuitka.bat` in the current repository, so do not cite one as an authoritative second entrypoint.

## Python package names

Directories containing importable `.py` files use underscore names and contain `__init__.py`. Hyphenated import packages fail normal Python naming and interact badly with data inclusion. `tests/unit/test_no_hyphen_python_packages.py` enforces this source rule.

Do not use `--include-data-dir` to ship importable Python. Nuitka filters code-like suffixes from data directories. Compile packages normally; use raw-data inclusion only for an intentionally interpreted/sandboxed source payload with an explicit runtime contract.

## Built-in plugin staging

Current desktop workflows run:

1. `scripts/prepare_nuitka_plugins.py prepare`
2. compile generated `build_nuitka_launcher.py`
3. `scripts/prepare_nuitka_plugins.py install` into the built distribution
4. `scripts/check_nuitka_dist.py <dist> --plugin-stage build/nuitka-plugins`

The staging script applies each plugin's `[tool.neko.build]` rules and generates selective exclusions. Do not restore blanket `--include-data-dir=plugin/plugins=plugin/plugins` or blanket `--nofollow-import-to=plugin.plugins`; both bypass the staged contract in different ways.

## Assets and dynamic imports

A new runtime asset or dynamic import may require coordinated updates to:

- Nuitka include options in both cross-platform and Linux-only workflows;
- `scripts/prepare_nuitka_plugins.py` for plugin payloads;
- `scripts/check_nuitka_dist.py` required-asset verification;
- targeted import/package tests.

Embedding and tiktoken assets are prepared and verified separately by the workflow.

## Diagnose safely

The packaged launcher starts multiple services. Killing only the parent can leave processes holding `dist/Xiao8`. Prefer static checks before launching:

```bash
uv run python scripts/check_nuitka_dist.py dist/Xiao8 --plugin-stage build/nuitka-plugins
uv run pytest tests/unit/test_no_hyphen_python_packages.py -q
```

If a packaged run is necessary, record the exact artifact/revision and shut down all child services before rebuilding. Never fix a stale locked distribution with an unreviewed recursive delete.

## Local Nuitka build

CI normally builds the desktop backend, but the same build can be reproduced on a local machine. `.github/workflows/build-desktop.yml` is the authoritative source of the Nuitka options (the `Build with Nuitka (Unix)` and `Build with Nuitka (Windows)` steps); the steps below only give their order. Run every command from the repository root. A C compiler is required (MSVC on Windows, gcc or clang elsewhere).

1. Environment: `uv sync --group galgame`, then `uv pip install --python .venv nuitka==4.2.2 ordered-set zstandard`. The Nuitka version is pinned; use the version named in the workflow.
2. Frontend: `./build_frontend.sh` (Linux/macOS) or `.\build_frontend.bat` (Windows).
3. Offline assets and seed configs, as in the workflow: run `scripts/prepare_embedding_model.py` (arguments are in the `Prepare embedding model assets` step), `scripts/prepare_voice_turn_assets.py` and `scripts/prepare_speaker_model.py`; warm the tiktoken cache into `data/tiktoken_cache/` (see the embedding models page); create neutral `config/characters.json`, `config/core_config.json` (`{"coreApi": "free", "assistApi": "free"}`, no keys) and `config/user_preferences.json` (`[]`) if they do not exist.
4. `uv run --no-sync python scripts/prepare_nuitka_plugins.py prepare` stages the built-in plugins and generates `build_nuitka_launcher.py`.
5. Delete the `steamworks/` native libraries of the other platforms, then compile: `uv run --no-sync python -m nuitka --standalone --output-dir=dist --output-filename=projectneko_server <options from the workflow> build_nuitka_launcher.py`. On macOS the workflow uses `--mode=app --macos-app-mode=background` instead of `--standalone`.
6. Finish the layout: rename `dist/build_nuitka_launcher.dist` to `dist/Xiao8`, run `uv run --no-sync python scripts/prepare_nuitka_plugins.py install --destination-dir dist/Xiao8/plugin/plugins`, then verify with `uv run --no-sync python scripts/check_nuitka_dist.py dist/Xiao8 --plugin-stage build/nuitka-plugins`.

Keep the options in the three Nuitka steps (`build-desktop.yml` Unix and Windows, `build-desktop-linux.yml`) in step with each other, and write down in the PR description when a change also needs to reach a local packaging script.
