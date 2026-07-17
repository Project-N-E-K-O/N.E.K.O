# NEKO Live Identity Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish NEKO Live under the permanent internal identity `neko_live` with matching directory, Python entry, configuration root, API paths, tests, documentation, and distribution artifact.

**Architecture:** Perform one pre-release clean identity break with no runtime alias and no automatic old-data migration. A contract test defines the final identity, then the plugin directory and all executable identity references move together; business terms such as roast strength remain unchanged.

**Tech Stack:** Python 3.11+, TOML, pytest, Ruff, N.E.K.O plugin CLI, Hosted TSX, JSON locale bundles.

## Global Constraints

- Stable plugin ID is exactly `neko_live`.
- Plugin directory is exactly `plugin/plugins/neko_live`.
- Python entry is exactly `plugin.plugins.neko_live:NekoLivePlugin`.
- Configuration root is exactly `[neko_live]`.
- Install-time legacy collision guard is exactly `previous_ids = ["neko_roast"]`; it is not a runtime alias.
- Display name remains `NEKO Live`.
- Distribution filename is `neko-live-<version>.neko-plugin`.
- Do not register a `neko_roast` alias or load old and new IDs together.
- Do not automatically move or delete old test configuration/data.
- Keep business-domain `roast` names when they describe rating/reply behavior rather than plugin identity.
- Preserve all 8 plugin locale files and their key/placeholder parity.
- Python commands run through `uv run` from the repository root.

---

## File Structure

- Rename `plugin/plugins/neko_roast/` to `plugin/plugins/neko_live/`.
- Modify `plugin/plugins/neko_live/plugin.toml`: permanent ID, entry, and config root.
- Modify `plugin/plugins/neko_live/__init__.py`: `NekoLivePlugin` entry class and permanent `name`.
- Modify Python imports/tests/tools under `plugin/plugins/neko_live/`: package path and stable ID constants.
- Modify `.github/CODEOWNERS`: ownership follows the new directory.
- Modify `plugin/tests/unit/server/test_plugin_routes.py`: lifecycle route expectation uses `neko_live`.
- Modify `docs/superpowers/specs/2026-07-16-live-support-priority-scheduler-design.md`: current implementation path follows the new directory.
- Modify comments in `plugin/plugins/neko_warthunder/core/{instructions.py,safety_guard.py}` only where they name the old plugin identity.
- Add focused identity/distribution assertions under `plugin/plugins/neko_live/tests/`.

### Task 1: Add the Permanent Identity Contract

**Files:**
- Create before rename: `plugin/plugins/neko_roast/tests/test_plugin_identity.py`
- Modify after rename: `plugin/plugins/neko_live/tests/test_plugin_identity.py`

**Interfaces:**
- Consumes: plugin manifest and Python entry module.
- Produces: one test that prevents future drift among ID, directory, entry, class, config root, and display name.

- [ ] **Step 1: Write the final-state identity test against the current tree**

```python
def test_neko_live_uses_one_permanent_internal_identity() -> None:
    plugin_dir = Path(__file__).resolve().parents[1]
    manifest = tomllib.loads((plugin_dir / "plugin.toml").read_text(encoding="utf-8"))
    assert plugin_dir.name == "neko_live"
    assert manifest["plugin"]["id"] == "neko_live"
    assert manifest["plugin"]["name"] == "NEKO Live"
    assert manifest["plugin"]["entry"] == "plugin.plugins.neko_live:NekoLivePlugin"
    assert manifest["plugin"]["previous_ids"] == ["neko_roast"]
    assert "neko_live" in manifest
    assert "neko_roast" not in manifest
```

- [ ] **Step 2: Run the test and verify it fails on the old identity**

Run: `uv run pytest plugin/plugins/neko_roast/tests/test_plugin_identity.py -q`

Expected: FAIL because the current directory and manifest still use `neko_roast`.

- [ ] **Step 3: Commit the failing contract test**

```bash
git add plugin/plugins/neko_roast/tests/test_plugin_identity.py
git commit -m "test(neko-live): define permanent plugin identity"
```

### Task 2: Move the Package and Entry Identity Together

**Files:**
- Rename: `plugin/plugins/neko_roast/` -> `plugin/plugins/neko_live/`
- Modify: `plugin/plugins/neko_live/plugin.toml`
- Modify: `plugin/plugins/neko_live/__init__.py`
- Modify: all `*.py`, `*.toml`, `*.md`, `*.tsx`, and test files under `plugin/plugins/neko_live/` that contain identity/path references.

**Interfaces:**
- Consumes: failing identity contract.
- Produces: importable `plugin.plugins.neko_live:NekoLivePlugin` and config root `[neko_live]`.

- [ ] **Step 1: Rename the directory without copying it**

Run: `git mv plugin/plugins/neko_roast plugin/plugins/neko_live`

Expected: Git records one directory move; no `plugin/plugins/neko_roast` remains.

- [ ] **Step 2: Update manifest and entry class**

Final manifest anchors:

```toml
[plugin]
id = "neko_live"
name = "NEKO Live"
entry = "plugin.plugins.neko_live:NekoLivePlugin"
previous_ids = ["neko_roast"]

[neko_live]
live_platform = "bilibili"
```

Final Python entry anchors:

```python
@neko_plugin
class NekoLivePlugin(NekoPluginBase):
    name = "neko_live"
```

Change the dynamic import error comparison to `plugin.plugins.neko_live.core.runtime`.

- [ ] **Step 3: Mechanically update package imports and stable literals**

Replace:

```text
plugin.plugins.neko_roast -> plugin.plugins.neko_live
PLUGIN_ID = "neko_roast" -> PLUGIN_ID = "neko_live"
{"neko_roast": config} -> {"neko_live": config}
/plugin/neko_roast/ -> /plugin/neko_live/
_local_artifacts/neko_roast/ -> _local_artifacts/neko_live/
temp/neko_roast -> temp/neko_live
neko-roast:onboarding:v2 -> neko-live:onboarding:v2
NekoRoastPanel -> NekoLivePanel
```

Do not rename domain classes such as `RoastRuntime`, `RoastConfig`, `roast_strength`, or user-facing “锐评” concepts.

- [ ] **Step 4: Run identity and import collection checks**

```bash
uv run pytest plugin/plugins/neko_live/tests/test_plugin_identity.py plugin/plugins/neko_live/tests/test_smoke.py plugin/plugins/neko_live/tests/test_module_registry.py -q
uv run pytest --collect-only plugin/plugins/neko_live/tests -q
```

Expected: identity test PASS and all plugin tests collect without `ModuleNotFoundError`.

- [ ] **Step 5: Commit the package move**

```bash
git add plugin/plugins/neko_live plugin/plugins/neko_roast
git commit -m "refactor(neko-live): migrate permanent plugin identity"
```

### Task 3: Update Repository Integration Boundaries and Documentation

**Files:**
- Modify: `.github/CODEOWNERS`
- Modify: `plugin/tests/unit/server/test_plugin_routes.py`
- Modify: `docs/superpowers/specs/2026-07-16-live-support-priority-scheduler-design.md`
- Modify: `plugin/plugins/neko_warthunder/core/instructions.py`
- Modify: `plugin/plugins/neko_warthunder/core/safety_guard.py`
- Modify: command/path examples under `plugin/plugins/neko_live/docs/`

**Interfaces:**
- Consumes: new `neko_live` directory and stable ID.
- Produces: repository ownership, lifecycle examples, and developer commands that no longer direct contributors to the old path.

- [ ] **Step 1: Update CODEOWNERS and lifecycle route test**

```text
/plugin/plugins/neko_live/  @CN-Zephyr
```

The route test must call `start_plugin_endpoint("neko_live")` and assert `plugin_id == "neko_live"`.

- [ ] **Step 2: Update active documentation and cross-plugin comments**

Change executable paths and current-plugin references to `neko_live`. Historical design prose may mention the former ID only when explicitly labelled “旧 ID”; current commands must all use the new path.

- [ ] **Step 3: Add a stale-identity scan test**

Extend `test_plugin_identity.py`:

```python
def test_old_identity_is_absent_from_executable_plugin_files() -> None:
    plugin_dir = Path(__file__).resolve().parents[1]
    offenders = []
    for path in plugin_dir.rglob("*"):
        if path.suffix in {".py", ".toml", ".tsx"} and "neko_roast" in path.read_text(encoding="utf-8"):
            offenders.append(path.relative_to(plugin_dir).as_posix())
    assert offenders == []
```

- [ ] **Step 4: Run boundary tests and repository scan**

```bash
uv run pytest plugin/plugins/neko_live/tests/test_plugin_identity.py plugin/plugins/neko_live/tests/test_distribution_boundaries.py plugin/tests/unit/server/test_plugin_routes.py -q
rg -n "plugin\.plugins\.neko_roast|PLUGIN_ID = \"neko_roast\"|\[neko_roast\]|/plugin/plugins/neko_roast" plugin .github docs
```

Expected: tests PASS; scan output contains only the migration design or explicitly historical old-ID text.

- [ ] **Step 5: Commit integration updates**

```bash
git add .github/CODEOWNERS plugin/tests/unit/server/test_plugin_routes.py docs/superpowers/specs/2026-07-16-live-support-priority-scheduler-design.md plugin/plugins/neko_warthunder/core plugin/plugins/neko_live/docs plugin/plugins/neko_live/tests/test_plugin_identity.py
git commit -m "docs(neko-live): update identity integration paths"
```

### Task 4: Verify Plugin Behavior, Locales, and Distribution

**Files:**
- Modify: `plugin/plugins/neko_live/tests/test_distribution_boundaries.py`: tracked lock/config paths use `plugin/plugins/neko_live`.
- Create outside source tree: `exports/neko-live-identity-migration/neko-live-0.1.0.neko-plugin`.

**Interfaces:**
- Consumes: migrated plugin and completed safe-upgrade infrastructure.
- Produces: verified source tree and installable artifact under the permanent identity.

- [ ] **Step 1: Run full NEKO Live tests and Ruff**

```bash
uv run pytest plugin/plugins/neko_live/tests -q
uv run ruff check plugin/plugins/neko_live
```

Expected: all plugin tests PASS and Ruff reports no issues.

- [ ] **Step 2: Run plugin CLI and Hosted UI gates**

```bash
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_live
uv run pytest plugin/plugins/neko_live/tests/test_hosted_ui.py plugin/plugins/neko_live/tests/test_hosted_ui_script.py -q
```

Expected: plugin check reports 0 errors; Hosted UI tests PASS.

- [ ] **Step 3: Verify all 8 plugin locale bundles**

Run `uv run pytest plugin/plugins/neko_live/tests/test_smoke.py -q`; its locale assertions parse every JSON bundle and enforce required key coverage. Then run this exact locale-set check:

```powershell
uv run python -c "from pathlib import Path; expected={'en.json','ja.json','ko.json','zh-CN.json','zh-TW.json','ru.json','pt.json','es.json'}; actual={p.name for p in Path('plugin/plugins/neko_live/i18n').glob('*.json')}; assert actual == expected, (actual, expected)"
```

- [ ] **Step 4: Build with the user-facing distribution filename**

```bash
uv run python -m plugin.neko_plugin_cli.cli build plugin/plugins/neko_live exports/neko-live-identity-migration/neko-live-0.1.0.neko-plugin
uv run python -m plugin.neko_plugin_cli.cli inspect exports/neko-live-identity-migration/neko-live-0.1.0.neko-plugin
uv run python -m plugin.neko_plugin_cli.cli verify exports/neko-live-identity-migration/neko-live-0.1.0.neko-plugin
```

Expected: package contains payload directory `neko_live`, manifest ID `neko_live`, entry `plugin.plugins.neko_live:NekoLivePlugin`, and verified payload hash.

- [ ] **Step 5: Exercise first install and second safe upgrade in a temporary root**

Use the server install-plan/install API test harness: first install returns `action=install`; the second returns `action=upgrade`; confirmed upgrade keeps the directory named `neko_live`; no `neko_live_1` exists.

- [ ] **Step 6: Run the combined regression gate**

```bash
uv run pytest plugin/tests/unit/test_neko_plugin_cli_public.py plugin/tests/unit/server/test_plugin_cli_install_plan.py plugin/tests/unit/server/test_plugin_upgrade_support.py plugin/tests/unit/server/test_plugin_cli_safe_upgrade.py plugin/tests/unit/server/test_plugin_cli_route.py plugin/plugins/neko_live/tests -q
uv run ruff check plugin/neko_plugin_cli plugin/server/application/plugin_cli plugin/server/application/plugins/upgrade_support.py plugin/server/routes/plugin_cli.py plugin/server/routes/market_bridge.py plugin/plugins/neko_live
git diff --check
```

Expected: all PASS and the worktree contains only intended files.

- [ ] **Step 7: Commit any verification-only assertion updates**

```bash
git add plugin/plugins/neko_live/tests/test_distribution_boundaries.py
git commit -m "test(neko-live): verify migrated distribution"
```
