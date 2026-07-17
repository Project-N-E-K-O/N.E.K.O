# Plugin Safe Local Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace unsafe `_1` plugin-directory conflict handling with an explicit, rollback-safe local plugin upgrade flow.

**Architecture:** Package inspection produces an immutable install plan and a confirmation token derived from the package bytes, target manifest, and destination path. A shared lifecycle/rollback support module performs stop, backup, restore, and restart operations; `PluginCliService` orchestrates staging and promotion while the frontend presents the plan and obtains explicit confirmation.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, asyncio, pathlib, Vue 3, TypeScript, Element Plus, Vitest, pytest.

## Global Constraints

- Executable plugin directories must never be renamed to `_1`, `_2`, or another import-incompatible name.
- Existing plugin replacement requires an explicit confirmation token and a server-side preflight recheck.
- Upgrade order is stop, backup, install into the original directory, validate, restart, then asynchronous backup cleanup.
- Any failure restores the old directory and attempts to restore its prior running state.
- Conflicting bundles are blocked; users upgrade each contained plugin through a single-plugin package.
- The low-level `rename` behavior may remain only for non-executable profile directories.
- Web and Electron use the same API and state model.
- New user-visible text must be added to all 8 frontend locales.
- Python commands run through `uv run` from the repository root.
- Do not log tokens, configuration values, package contents, or absolute user paths in user-facing errors.

---

## File Structure

- Create `plugin/server/application/plugin_cli/install_plan.py`: package/target identity classification and confirmation-token generation.
- Create `plugin/server/application/plugins/upgrade_support.py`: reusable lifecycle and directory rollback primitives.
- Modify `plugin/neko_plugin_cli/core/install.py`: make executable-directory conflicts fail instead of producing renamed plugin folders.
- Modify `plugin/neko_plugin_cli/public/unpack.py`: apply the same fail-closed rule to the legacy unpack API.
- Modify `plugin/neko_plugin_cli/core/models.py`: default conflict strategy is `fail`; retain profile-level rename representation only where required.
- Modify `plugin/neko_plugin_cli/commands/install_cmd.py`: CLI defaults to `fail` and no longer offers plugin-copy renaming.
- Modify `plugin/server/market_protocol_handler.py`: protocol installs default to `fail`.
- Modify `plugin/server/application/plugin_cli/service.py`: expose plan and confirmed safe-upgrade orchestration.
- Modify `plugin/server/routes/plugin_cli.py`: add plan DTO/route and confirmed-upgrade request fields.
- Modify `plugin/server/routes/market_bridge.py`: consume shared lifecycle/rollback primitives without changing market behavior.
- Modify `frontend/plugin-manager/src/api/pluginCli.ts`: add plan types and confirmed-upgrade request fields.
- Modify `frontend/plugin-manager/src/composables/usePackageManager.ts`: plan first, confirm upgrade, then install.
- Modify `frontend/plugin-manager/src/components/plugin/PackageManagerPanel.vue`: remove raw rename/fail controls and show install/upgrade state.
- Modify `frontend/plugin-manager/src/i18n/locales/{en,ja,ko,zh-CN,zh-TW,ru,pt,es}.ts`: add the exact `package.install` key set and preserve `{plugin}`, `{current}`, and `{target}` placeholders in every translation.
- Test `plugin/tests/unit/test_neko_plugin_cli_public.py`, `plugin/tests/unit/server/test_plugin_cli_route.py`, new focused backend test files, and `frontend/plugin-manager/src/composables/usePackageManager.test.ts`.

### Task 1: Make Executable Plugin Conflicts Fail Closed

**Files:**
- Modify: `plugin/neko_plugin_cli/core/install.py`
- Modify: `plugin/neko_plugin_cli/public/unpack.py`
- Modify: `plugin/neko_plugin_cli/commands/install_cmd.py`
- Modify: `plugin/server/market_protocol_handler.py`
- Modify: `plugin/server/routes/market_bridge.py`
- Modify: `plugin/server/routes/plugin_cli.py`
- Test: `plugin/tests/unit/test_neko_plugin_cli_public.py`
- Test: `plugin/tests/integration/test_neko_plugin_cli_workflow.py`
- Test: `plugin/tests/integration/test_market_bridge_e2e.py`

**Interfaces:**
- Consumes: existing `install_package(package, *, plugins_root, profiles_root, on_conflict="fail")` API.
- Produces: default `on_conflict="fail"`; an existing executable target always raises `FileExistsError` rather than returning a renamed `InstalledPlugin`.

- [ ] **Step 1: Replace the rename expectation with a failing executable-directory test**

```python
def test_install_package_never_renames_existing_plugin_directory(tmp_path: Path) -> None:
    plugin_dir = _make_plugin_dir(tmp_path)
    package_path = tmp_path / "demo_plugin.neko-plugin"
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    build_plugin(plugin_dir, package_path)
    install_package(package_path, plugins_root=plugins_root, profiles_root=profiles_root)

    with pytest.raises(FileExistsError, match="demo_plugin"):
        install_package(
            package_path,
            plugins_root=plugins_root,
            profiles_root=profiles_root,
            on_conflict="rename",
        )

    assert not (plugins_root / "demo_plugin_1").exists()
```

- [ ] **Step 2: Run the focused test and verify the old behavior fails it**

Run: `uv run pytest plugin/tests/unit/test_neko_plugin_cli_public.py::test_install_package_never_renames_existing_plugin_directory -q`

Expected: FAIL because `demo_plugin_1` is currently created.

- [ ] **Step 3: Separate executable-target resolution from profile conflict resolution**

Implement an executable-target helper in `PackageInstaller` and call it for every plugin payload:

```python
@staticmethod
def resolve_plugin_target_dir(target_dir: Path) -> Path:
    if target_dir.exists():
        raise FileExistsError(f"plugin target already exists: {target_dir.name}")
    return target_dir
```

Keep `resolve_target_dir(target_dir, *, on_conflict)` only for package profile directories. Apply the executable fail-closed helper in both the core installer and legacy public unpacker. Change public, server, market protocol, and CLI defaults to `fail`; remove `rename` from the CLI and plugin-manager FastAPI request choices used for executable installation.

- [ ] **Step 4: Update bundle tests to reject any executable conflict atomically**

```python
def test_install_bundle_rejects_existing_plugin_without_partial_promotion(tmp_path: Path) -> None:
    first = _make_plugin_dir(tmp_path / "src", plugin_id="foo")
    second = _make_plugin_dir(tmp_path / "src", plugin_id="bar")
    package_path = tmp_path / "bundle.neko-bundle"
    build_bundle([first, second], package_path, bundle_id="bundle", package_name="Bundle", version="1.0.0")
    plugins_root = tmp_path / "plugins"
    (plugins_root / "foo").mkdir(parents=True)

    with pytest.raises(FileExistsError):
        install_package(package_path, plugins_root=plugins_root, profiles_root=tmp_path / "profiles")

    assert not (plugins_root / "bar").exists()
```

- [ ] **Step 5: Run packaging regressions**

Run: `uv run pytest plugin/tests/unit/test_neko_plugin_cli_public.py plugin/tests/integration/test_neko_plugin_cli_workflow.py -q`

Expected: PASS; no test expects an executable plugin `_1` directory.

Replace `test_upgrade_honors_recorded_directory_for_renamed_install` with a market test that pre-creates the target plugin directory, submits an initial install, and asserts a failed task with no `<plugin_id>_1` directory or install-source lock entry.

- [ ] **Step 6: Commit**

```bash
git add plugin/neko_plugin_cli/core/install.py plugin/neko_plugin_cli/public/unpack.py plugin/neko_plugin_cli/commands/install_cmd.py plugin/server/market_protocol_handler.py plugin/server/routes/market_bridge.py plugin/server/routes/plugin_cli.py plugin/tests/unit/test_neko_plugin_cli_public.py plugin/tests/integration/test_neko_plugin_cli_workflow.py plugin/tests/integration/test_market_bridge_e2e.py
git commit -m "fix(plugin): reject renamed executable installs"
```

### Task 2: Add Install Planning and Concurrency Tokens

**Files:**
- Create: `plugin/server/application/plugin_cli/install_plan.py`
- Create: `plugin/tests/unit/server/test_plugin_cli_install_plan.py`
- Modify: `plugin/server/application/plugin_cli/service.py`
- Modify: `plugin/server/routes/plugin_cli.py`
- Test: `plugin/tests/unit/server/test_plugin_cli_route.py`

**Interfaces:**
- Consumes: `inspect_package(path) -> PackageInspectResult`, user plugin root, and installed `plugin.toml` files.
- Produces: `build_install_plan(package_path: Path, plugins_root: Path) -> PluginInstallPlan` and `PluginCliService.plan_install(*, package: str, plugins_root: str | None = None) -> dict[str, object]`.

- [ ] **Step 1: Write classifier tests**

```python
def _write_plugin(
    root: Path,
    plugin_id: str,
    version: str,
    previous_ids: tuple[str, ...] = (),
) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True)
    previous_line = f"previous_ids = {json.dumps(list(previous_ids))}\n" if previous_ids else ""
    (plugin_dir / "plugin.toml").write_text(
        f'[plugin]\nid = "{plugin_id}"\nname = "{plugin_id}"\nversion = "{version}"\n{previous_line}',
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    return plugin_dir


def _single_package(tmp_path: Path, plugin_id: str, version: str = "2.0.0") -> Path:
    package_path = tmp_path / f"{plugin_id}-{version}.neko-plugin"
    pack_plugin(_write_plugin(tmp_path / "source", plugin_id, version), package_path)
    return package_path


def test_plan_marks_new_single_plugin_as_install(tmp_path: Path) -> None:
    plan = build_install_plan(package_path=_single_package(tmp_path, "demo"), plugins_root=tmp_path / "plugins")
    assert plan.action == "install"
    assert plan.plugin_id == "demo"
    assert plan.confirmation_token == ""


def test_plan_marks_matching_existing_plugin_as_upgrade(tmp_path: Path) -> None:
    package = _single_package(tmp_path, "demo", version="2.0.0")
    _write_plugin(tmp_path / "plugins", plugin_id="demo", version="1.0.0")
    plan = build_install_plan(package_path=package, plugins_root=tmp_path / "plugins")
    assert plan.action == "upgrade"
    assert plan.current_version == "1.0.0"
    assert plan.target_version == "2.0.0"
    assert len(plan.confirmation_token) == 64


def test_plan_blocks_bundle_with_any_existing_plugin(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    package_path = tmp_path / "demo-bundle.neko-bundle"
    build_bundle(
        [_write_plugin(source_root, "demo", "2.0.0"), _write_plugin(source_root, "other", "2.0.0")],
        package_path,
        bundle_id="demo_bundle",
        package_name="Demo Bundle",
        version="2.0.0",
    )
    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root, "demo", "1.0.0")
    plan = build_install_plan(package_path=package_path, plugins_root=plugins_root)
    assert plan.action == "blocked"
    assert plan.reason == "bundle_conflict"


def test_plan_blocks_an_installed_declared_previous_id(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path / "source", "neko_live", "1.0.0", previous_ids=("neko_roast",))
    package_path = tmp_path / "neko-live.neko-plugin"
    pack_plugin(source, package_path)
    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root, "neko_roast", "0.1.0")

    plan = build_install_plan(package_path=package_path, plugins_root=plugins_root)

    assert plan.action == "blocked"
    assert plan.reason == "legacy_plugin_present"
    assert plan.legacy_plugin_ids == ("neko_roast",)
```

- [ ] **Step 2: Run classifier tests and verify import failure**

Run: `uv run pytest plugin/tests/unit/server/test_plugin_cli_install_plan.py -q`

Expected: FAIL because `install_plan.py` does not exist.

- [ ] **Step 3: Implement immutable plan DTO and fingerprint**

```python
@dataclass(frozen=True, slots=True)
class PluginInstallPlan:
    action: Literal["install", "upgrade", "blocked"]
    package_type: Literal["plugin", "bundle"]
    plugin_id: str
    directory_name: str
    current_version: str
    target_version: str
    confirmation_token: str
    reason: str
    legacy_plugin_ids: tuple[str, ...]


def confirmation_token(*, package_path: Path, target_dir: Path) -> str:
    digest = hashlib.sha256()
    digest.update(package_path.read_bytes())
    digest.update(b"\0")
    digest.update(str(target_dir.resolve()).encode("utf-8"))
    digest.update(b"\0")
    digest.update((target_dir / "plugin.toml").read_bytes())
    return digest.hexdigest()
```

The classifier must require one package plugin for upgrade, verify target directory name and manifest ID agree, and return `blocked` for same-directory/different-ID, multiple matching installations, bundle conflicts, or any installed ID listed by the package manifest's optional `plugin.previous_ids`. `previous_ids` is an install-time collision guard only; it must not register aliases or migrate data.

- [ ] **Step 4: Add `POST /plugin-cli/install-plan`**

Add route DTOs:

```python
class PluginCliInstallPlanRequest(BaseModel):
    package: str
    plugins_root: str | None = None


class PluginCliInstallPlanResponse(BaseModel):
    action: Literal["install", "upgrade", "blocked"]
    package_type: Literal["plugin", "bundle"]
    plugin_id: str
    directory_name: str
    current_version: str = ""
    target_version: str = ""
    confirmation_token: str = ""
    reason: str = ""
    legacy_plugin_ids: list[str] = Field(default_factory=list)
```

Route through `PluginCliService.plan_install`; apply the same root containment policy as `install`.

- [ ] **Step 5: Add route tests**

```python
response = await client.post("/plugin-cli/install-plan", json={"package": str(package_path)})
assert response.status_code == 200
assert response.json()["action"] == "upgrade"
assert response.json()["plugin_id"] == "simple_plugin"
```

- [ ] **Step 6: Run plan and route tests**

Run: `uv run pytest plugin/tests/unit/server/test_plugin_cli_install_plan.py plugin/tests/unit/server/test_plugin_cli_route.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add plugin/server/application/plugin_cli/install_plan.py plugin/server/application/plugin_cli/service.py plugin/server/routes/plugin_cli.py plugin/tests/unit/server/test_plugin_cli_install_plan.py plugin/tests/unit/server/test_plugin_cli_route.py
git commit -m "feat(plugin): plan local package upgrades"
```

### Task 3: Implement Stop, Backup, Install, Validate, Restart, and Rollback

**Files:**
- Create: `plugin/server/application/plugins/upgrade_support.py`
- Create: `plugin/tests/unit/server/test_plugin_upgrade_support.py`
- Modify: `plugin/server/routes/market_bridge.py`
- Modify: `plugin/server/application/plugin_cli/service.py`
- Create: `plugin/tests/unit/server/test_plugin_cli_safe_upgrade.py`

**Interfaces:**
- Consumes: `PluginInstallPlan`, `PluginLifecycleService`, and an injected asynchronous staging installer callback.
- Produces: shared `plugin_is_running(plugin_id: str) -> Awaitable[bool]`, `stop_plugin_for_upgrade(plugin_id: str) -> Awaitable[None]`, `start_plugin_after_upgrade(plugin_id: str, *, strict: bool) -> Awaitable[bool]`, `backup_path_for(target_dir: Path) -> Path`, `restore_directory(backup_dir: Path, target_dir: Path) -> Awaitable[None]`, `remove_directory(target_dir: Path) -> Awaitable[None]`, and `run_rollback(*, plugin_id: str, target_dir: Path, backup_dir: Path, restart: bool, start: Callable[[str], Awaitable[None]]) -> Awaitable[bool]`; confirmed local upgrade response fields `operation`, `restarted`, and `rollback_status`.

- [ ] **Step 1: Write rollback primitive tests**

```python
@pytest.mark.asyncio
async def test_run_rollback_removes_new_directory_restores_backup_and_restarts(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    backup = tmp_path / "demo.bak"
    target.mkdir()
    (target / "new.txt").write_text("new")
    backup.mkdir()
    (backup / "old.txt").write_text("old")
    restarted: list[str] = []

    async def start(plugin_id: str) -> None:
        restarted.append(plugin_id)

    restored = await run_rollback(
        plugin_id="demo",
        target_dir=target,
        backup_dir=backup,
        restart=True,
        start=start,
    )

    assert restored is True
    assert (target / "old.txt").read_text() == "old"
    assert restarted == ["demo"]
```

- [ ] **Step 2: Run the primitive test and verify import failure**

Run: `uv run pytest plugin/tests/unit/server/test_plugin_upgrade_support.py -q`

Expected: FAIL because the shared support module does not exist.

- [ ] **Step 3: Extract lifecycle and directory primitives**

Move the generic behavior currently private to `market_bridge.py` into `upgrade_support.py`. Keep market progress/task formatting in the route, but replace route-private lifecycle, restore, remove, and timestamp helpers with shared calls. `start_plugin_after_upgrade(plugin_id, strict=True)` must raise during forward upgrade; rollback calls `start_plugin_after_upgrade(plugin_id, strict=False)` so restart failure does not hide the original error.

- [ ] **Step 4: Run market upgrade regressions after extraction**

Run: `uv run pytest plugin/tests/integration/test_market_bridge_e2e.py -k "upgrade" -q`

Expected: PASS with unchanged market result/error codes.

- [ ] **Step 5: Write safe-upgrade transaction tests**

Define `perform_safe_upgrade` with injected callbacks so each failure stage is deterministic:

```python
async def perform_safe_upgrade(
    *,
    plan: PluginInstallPlan,
    target_dir: Path,
    install_new: Callable[[], Awaitable[dict[str, object]]],
    validate_new: Callable[[], Awaitable[None]],
    is_running: Callable[[str], Awaitable[bool]],
    stop: Callable[[str], Awaitable[None]],
    start: Callable[[str], Awaitable[None]],
    cleanup_backup: Callable[[Path], Awaitable[None]],
) -> SafeUpgradeResult:
    """Replace one installed plugin directory or restore the old version."""
```

Use this parameterized failure test in `test_plugin_cli_safe_upgrade.py`:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["install", "validate", "restart"])
async def test_safe_upgrade_restores_old_directory_after_each_failure(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    (target / "plugin.toml").write_text('[plugin]\nid = "demo"\nversion = "1.0.0"\n', encoding="utf-8")
    calls: list[str] = []

    async def is_running(plugin_id: str) -> bool:
        assert plugin_id == "demo"
        return True

    async def stop(plugin_id: str) -> None:
        calls.append(f"stop:{plugin_id}")

    async def install_new() -> dict[str, object]:
        if failure_stage == "install":
            raise RuntimeError("install failed")
        target.mkdir()
        (target / "plugin.toml").write_text('[plugin]\nid = "demo"\nversion = "2.0.0"\n', encoding="utf-8")
        return {"installed_plugin_count": 1}

    async def validate_new() -> None:
        if failure_stage == "validate":
            raise RuntimeError("validation failed")

    async def start(plugin_id: str) -> None:
        calls.append(f"start:{plugin_id}")
        if failure_stage == "restart" and calls.count("start:demo") == 1:
            raise RuntimeError("restart failed")

    async def cleanup_backup(_backup_dir: Path) -> None:
        raise AssertionError("failed upgrades must not schedule successful cleanup")

    plan = PluginInstallPlan(
        action="upgrade",
        package_type="plugin",
        plugin_id="demo",
        directory_name="demo",
        current_version="1.0.0",
        target_version="2.0.0",
        confirmation_token="a" * 64,
        reason="",
        legacy_plugin_ids=(),
    )
    with pytest.raises(SafeUpgradeError):
        await perform_safe_upgrade(
            plan=plan,
            target_dir=target,
            install_new=install_new,
            validate_new=validate_new,
            is_running=is_running,
            stop=stop,
            start=start,
            cleanup_backup=cleanup_backup,
        )

    assert 'version = "1.0.0"' in (target / "plugin.toml").read_text(encoding="utf-8")
    assert not (tmp_path / "demo_1").exists()
    assert calls[0] == "stop:demo"
    assert calls[-1] == "start:demo"


@pytest.mark.asyncio
async def test_safe_upgrade_keeps_new_version_and_cleans_backup(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    (target / "plugin.toml").write_text('[plugin]\nid = "demo"\nversion = "1.0.0"\n', encoding="utf-8")
    cleaned: list[Path] = []

    async def install_new() -> dict[str, object]:
        target.mkdir()
        (target / "plugin.toml").write_text('[plugin]\nid = "demo"\nversion = "2.0.0"\n', encoding="utf-8")
        return {"installed_plugin_count": 1}

    async def nothing(_plugin_id: str) -> None:
        return None

    async def validate_new() -> None:
        assert 'version = "2.0.0"' in (target / "plugin.toml").read_text(encoding="utf-8")

    async def running(_plugin_id: str) -> bool:
        return True

    async def cleanup_backup(path: Path) -> None:
        cleaned.append(path)

    plan = PluginInstallPlan(
        action="upgrade",
        package_type="plugin",
        plugin_id="demo",
        directory_name="demo",
        current_version="1.0.0",
        target_version="2.0.0",
        confirmation_token="a" * 64,
        reason="",
        legacy_plugin_ids=(),
    )
    result = await perform_safe_upgrade(
        plan=plan,
        target_dir=target,
        install_new=install_new,
        validate_new=validate_new,
        is_running=running,
        stop=nothing,
        start=nothing,
        cleanup_backup=cleanup_backup,
    )

    assert result.restarted is True
    assert 'version = "2.0.0"' in (target / "plugin.toml").read_text(encoding="utf-8")
    assert cleaned == [result.backup_dir]
```

Add this service-level stale-plan test (the file-local `_write_plugin` helper writes `plugin.toml` and `__init__.py` exactly as in Task 2):

```python
@pytest.mark.asyncio
async def test_service_rejects_changed_target_before_stopping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_plugin(tmp_path / "source", "demo", "2.0.0")
    package_path = tmp_path / "demo-2.0.0.neko-plugin"
    pack_plugin(source, package_path)
    plugins_root = tmp_path / "plugins"
    target = _write_plugin(plugins_root, "demo", "1.0.0")
    _patch_plugin_cli_settings(
        monkeypatch,
        builtin_root=tmp_path / "builtin",
        user_root=plugins_root,
        packages_root=tmp_path / "packages",
        profiles_root=tmp_path / "profiles",
    )
    service = PluginCliService()
    plan = await service.plan_install(package=str(package_path))
    (target / "plugin.toml").write_text(
        '[plugin]\nid = "demo"\nname = "demo"\nversion = "1.0.1"\n',
        encoding="utf-8",
    )
    stop_calls: list[str] = []

    async def unexpected_stop(plugin_id: str) -> None:
        stop_calls.append(plugin_id)

    monkeypatch.setattr(upgrade_support, "stop_plugin_for_upgrade", unexpected_stop)
    with pytest.raises(ServerDomainError) as exc_info:
        await service.install(
            package=str(package_path),
            confirm_upgrade=True,
            confirmation_token=str(plan["confirmation_token"]),
        )

    assert exc_info.value.code == "PLUGIN_UPGRADE_PLAN_CHANGED"
    assert stop_calls == []
```

- [ ] **Step 6: Extend install request and implement confirmed replacement**

```python
class PluginCliInstallRequest(BaseModel):
    package: str
    plugins_root: str | None = None
    profiles_root: str | None = None
    confirm_upgrade: bool = False
    confirmation_token: str | None = None
```

`PluginCliService.install` must recompute the plan immediately before mutation. For `upgrade`, require `confirm_upgrade is True` and exact token equality; otherwise raise `ServerDomainError(code="PLUGIN_UPGRADE_CONFIRMATION_REQUIRED", status_code=409, details=plan_dict)`. Stop the plugin only when running, rename the directory to a unique backup, call staging promotion with `on_conflict="fail"`, validate manifest ID/directory/entry, restart if necessary, and schedule backup cleanup. On failure, call shared rollback and return `PLUGIN_UPGRADE_ROLLED_BACK` with a non-sensitive stage.

- [ ] **Step 7: Run safe-upgrade and route tests**

Run: `uv run pytest plugin/tests/unit/server/test_plugin_upgrade_support.py plugin/tests/unit/server/test_plugin_cli_safe_upgrade.py plugin/tests/unit/server/test_plugin_cli_route.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add plugin/server/application/plugins/upgrade_support.py plugin/server/application/plugin_cli/service.py plugin/server/routes/market_bridge.py plugin/server/routes/plugin_cli.py plugin/tests/unit/server/test_plugin_upgrade_support.py plugin/tests/unit/server/test_plugin_cli_safe_upgrade.py plugin/tests/integration/test_market_bridge_e2e.py plugin/tests/unit/server/test_plugin_cli_route.py
git commit -m "feat(plugin): add rollback-safe local upgrades"
```

### Task 4: Replace Raw Conflict Controls with an Explicit Upgrade Confirmation UI

**Files:**
- Modify: `frontend/plugin-manager/src/api/pluginCli.ts`
- Modify: `frontend/plugin-manager/src/composables/usePackageManager.ts`
- Modify: `frontend/plugin-manager/src/composables/usePackageManager.test.ts`
- Modify: `frontend/plugin-manager/src/components/plugin/PackageManagerPanel.vue`
- Modify: `frontend/plugin-manager/src/i18n/locales/en.ts`
- Modify: `frontend/plugin-manager/src/i18n/locales/ja.ts`
- Modify: `frontend/plugin-manager/src/i18n/locales/ko.ts`
- Modify: `frontend/plugin-manager/src/i18n/locales/zh-CN.ts`
- Modify: `frontend/plugin-manager/src/i18n/locales/zh-TW.ts`
- Modify: `frontend/plugin-manager/src/i18n/locales/ru.ts`
- Modify: `frontend/plugin-manager/src/i18n/locales/pt.ts`
- Modify: `frontend/plugin-manager/src/i18n/locales/es.ts`

**Interfaces:**
- Consumes: `POST /plugin-cli/install-plan` and confirmed `POST /plugin-cli/install`.
- Produces: `planPluginInstall(payload)`, one confirmation dialog, and deterministic install/upgrade/blocked UI states.

- [ ] **Step 1: Write composable tests for install, upgrade, cancel, and blocked plans**

```typescript
it('confirms a matching upgrade and forwards the confirmation token', async () => {
  vi.mocked(planPluginInstall).mockResolvedValue({
    action: 'upgrade', plugin_id: 'demo_plugin', directory_name: 'demo_plugin',
    package_type: 'plugin', current_version: '1.0.0', target_version: '2.0.0',
    confirmation_token: 'a'.repeat(64), reason: '', legacy_plugin_ids: [],
  })
  vi.mocked(ElMessageBox.confirm).mockResolvedValue('confirm')
  await manager.handleInstall()
  expect(installPluginPackage).toHaveBeenCalledWith(expect.objectContaining({
    confirm_upgrade: true,
    confirmation_token: 'a'.repeat(64),
  }))
})
```

Add these three deterministic cases after the upgrade-confirmation test:

```typescript
it('installs a new plugin without upgrade credentials', async () => {
  const manager = usePackageManager()
  manager.installForm.value.package = 'demo.neko-plugin'
  vi.mocked(planPluginInstall).mockResolvedValue({
    action: 'install', plugin_id: 'demo_plugin', directory_name: 'demo_plugin',
    package_type: 'plugin', current_version: '', target_version: '1.0.0',
    confirmation_token: '', reason: '', legacy_plugin_ids: [],
  })
  vi.mocked(installPluginPackage).mockResolvedValue(installResponse)
  await manager.handleInstall()
  expect(installPluginPackage).toHaveBeenCalledWith(expect.not.objectContaining({ confirmation_token: expect.anything() }))
})

it('does not install when the user cancels an upgrade', async () => {
  const manager = usePackageManager()
  manager.installForm.value.package = 'demo.neko-plugin'
  vi.mocked(planPluginInstall).mockResolvedValue(upgradePlan)
  vi.mocked(ElMessageBox.confirm).mockRejectedValue('cancel')
  await manager.handleInstall()
  expect(installPluginPackage).not.toHaveBeenCalled()
})

it('does not install a blocked bundle conflict', async () => {
  const manager = usePackageManager()
  manager.installForm.value.package = 'demo.neko-bundle'
  vi.mocked(planPluginInstall).mockResolvedValue({
    action: 'blocked', plugin_id: '', directory_name: '', package_type: 'bundle',
    current_version: '', target_version: '1.0.0', confirmation_token: '',
    reason: 'bundle_conflict', legacy_plugin_ids: [],
  })
  await manager.handleInstall()
  expect(installPluginPackage).not.toHaveBeenCalled()
  expect(ElMessage.error).toHaveBeenCalledWith(expect.stringContaining('blockedBundleConflict'))
})
```

Define `upgradePlan` and `installResponse` as typed constants at the top of the test file, and add `ElMessageBox.confirm` to the Element Plus mock.

- [ ] **Step 2: Run the frontend test and verify missing API/state failures**

Run from `frontend/plugin-manager`: `npm test -- --run src/composables/usePackageManager.test.ts`

Expected: FAIL because plan API and confirmation handling do not exist.

- [ ] **Step 3: Add frontend API types and plan call**

```typescript
export type PluginCliInstallAction = 'install' | 'upgrade' | 'blocked'

export interface PluginCliInstallPlanResponse {
  action: PluginCliInstallAction
  package_type: 'plugin' | 'bundle'
  plugin_id: string
  directory_name: string
  current_version: string
  target_version: string
  confirmation_token: string
  reason: string
  legacy_plugin_ids: string[]
}
```

Change `PluginCliConflictStrategy` to `'fail'`, add `confirm_upgrade` and `confirmation_token` to `PluginCliInstallRequest`, and add `planPluginInstall`.

- [ ] **Step 4: Implement the plan-confirm-install flow**

`handleInstall` must set `installing` before planning and keep it true through confirmation and mutation. For upgrades, display plugin name, current version, target version, and a short “running plugins will restart” note. Remove the rename/fail radio group and replace it with read-only copy that duplicate imports become safe upgrades. Never expose backup paths or tokens.

- [ ] **Step 5: Add all 8 locale keys**

Add the same key set under `package.install` in every locale. Use these exact English meanings and the same placeholders in every translation:

```typescript
planFailed: 'Could not inspect the installation package.',
upgradeTitle: 'Upgrade {plugin}?',
upgradeBody: 'Version {current} will be replaced by {target}. A running plugin will restart briefly.',
upgradeConfirm: 'Upgrade plugin',
upgradeCancelled: 'Upgrade cancelled.',
upgradeSucceeded: '{plugin} was upgraded successfully.',
blockedBundleConflict: 'This bundle contains an installed plugin. Upgrade its plugins one at a time.',
blockedDirectoryConflict: 'The destination directory belongs to another plugin and was not changed.',
blockedLegacyPlugin: 'An earlier version of this plugin is still installed. Uninstall {plugin} before continuing.',
rollbackCompleted: 'The upgrade failed and the previous version was restored.',
```

Keep `{plugin}`, `{current}`, and `{target}` placeholders identical across all 8 locales.

- [ ] **Step 6: Run frontend verification**

Run from `frontend/plugin-manager`:

```bash
npm test -- --run src/composables/usePackageManager.test.ts
npm run check:i18n
npm run type-check
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/plugin-manager/src/api/pluginCli.ts frontend/plugin-manager/src/composables/usePackageManager.ts frontend/plugin-manager/src/composables/usePackageManager.test.ts frontend/plugin-manager/src/components/plugin/PackageManagerPanel.vue frontend/plugin-manager/src/i18n/locales
git commit -m "feat(plugin-ui): confirm safe local upgrades"
```

### Task 5: End-to-End Upgrade Verification

**Files:**
- Modify: `plugin/tests/unit/server/test_plugin_cli_route.py`
- Modify: `frontend/plugin-manager/src/composables/usePackageManager.test.ts`

**Interfaces:**
- Consumes: completed backend and frontend safe-upgrade flow.
- Produces: verified install, upgrade, rollback, and market compatibility gates.

- [ ] **Step 1: Add one HTTP-level upgrade workflow test**

The test must pack v1 and v2 of the same fixture, install v1, call `/plugin-cli/install-plan` for v2, call `/plugin-cli/install` with the returned token, and assert the original directory now contains v2 without any sibling `_1` directory.

- [ ] **Step 2: Run backend focused and market regression suites**

```bash
uv run pytest plugin/tests/unit/test_neko_plugin_cli_public.py plugin/tests/unit/server/test_plugin_cli_install_plan.py plugin/tests/unit/server/test_plugin_upgrade_support.py plugin/tests/unit/server/test_plugin_cli_safe_upgrade.py plugin/tests/unit/server/test_plugin_cli_route.py -q
uv run pytest plugin/tests/integration/test_market_bridge_e2e.py -k "install or upgrade or rollback" -q
uv run ruff check plugin/neko_plugin_cli plugin/server/application/plugin_cli plugin/server/application/plugins/upgrade_support.py plugin/server/routes/plugin_cli.py plugin/server/routes/market_bridge.py plugin/tests/unit/server
```

Expected: all PASS.

- [ ] **Step 3: Run frontend suite and production build**

From `frontend/plugin-manager`:

```bash
npm test -- --run src/composables/usePackageManager.test.ts
npm run check:i18n
npm run build
```

Expected: tests, locale parity, type-check, and Vite build PASS.

- [ ] **Step 4: Commit final test adjustments**

```bash
git add plugin/tests/unit/server/test_plugin_cli_route.py frontend/plugin-manager/src/composables/usePackageManager.test.ts
git commit -m "test(plugin): cover local upgrade workflow"
```
