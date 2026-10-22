"""Market replacements preserve preferences after their outer backup moved code.

Exercise real package building, install planning, staged extraction, and directory
replacement. Host lifecycle and configuration persistence stay isolated from the
developer's running application.
"""

from __future__ import annotations

from copy import deepcopy
import functools
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tomllib

import pytest

from plugin import settings
from plugin.neko_plugin_cli.public import build_plugin
from plugin.server.application.plugin_cli import service as cli_service
from plugin.server.application.install_source.models import LockEntry
from plugin.server.application.plugins import development
from plugin.server.application.plugins.installation_transactions import replace
from plugin.server.infrastructure import autostart_approvals, runtime_overrides
from plugin.server.routes import market_bridge
from plugin.server.domain.errors import ServerDomainError

pytestmark = pytest.mark.plugin_unit

PLUGIN_ID = "market_autostart_demo"


def _write_plugin(parent: Path, *, version: str, payload: str) -> Path:
    directory = parent / PLUGIN_ID
    directory.mkdir(parents=True)
    (directory / "plugin.toml").write_text(
        f'[plugin]\nid="{PLUGIN_ID}"\nname="Market auto-start demo"\n'
        f'version="{version}"\ntype="plugin"\n'
        f'entry="plugin.plugins.{PLUGIN_ID}:Plugin"\n'
        "[plugin_runtime]\nauto_start=true\n",
        encoding="utf-8",
    )
    (directory / "__init__.py").write_text("class Plugin: pass\n", encoding="utf-8")
    (directory / "payload.txt").write_text(payload, encoding="utf-8")
    return directory


@pytest.fixture
def market_install_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    paths = {
        "USER_PLUGIN_CONFIG_ROOT": tmp_path / "plugins",
        "USER_PLUGIN_PACKAGES_ROOT": tmp_path / "packages",
        "USER_PACKAGE_PROFILES_ROOT": tmp_path / "profiles",
        "PLUGIN_STATE_ROOT": tmp_path / "state",
        "BUILTIN_PLUGIN_CONFIG_ROOT": tmp_path / "builtin",
    }
    for name, path in paths.items():
        path.mkdir()
        monkeypatch.setattr(settings, name, path)

    # Distinct durable stores let the assertions detect cache-only restoration,
    # unrelated-entry loss, and accidental materialization of an absent override.
    preference_store = {"unrelated": {"enabled": False, "auto_start": True}}
    pending_store = {"unrelated_pending"}

    def save_preferences(value):
        preference_store.clear()
        preference_store.update(deepcopy(value))

    def save_pending(value):
        pending_store.clear()
        pending_store.update(value)
        return True

    monkeypatch.setattr(runtime_overrides, "_cache", None)
    monkeypatch.setattr(runtime_overrides, "_cache_write_blocked_by_invalid_content", False)
    monkeypatch.setattr(runtime_overrides, "_load_from_disk", lambda: deepcopy(preference_store))
    monkeypatch.setattr(runtime_overrides, "_save_to_disk", save_preferences)
    monkeypatch.setattr(autostart_approvals, "_cache", set(pending_store))
    monkeypatch.setattr(autostart_approvals, "_load_failed", False)
    monkeypatch.setattr(autostart_approvals, "_save_locked", save_pending)
    monkeypatch.setattr(development, "list_registration_records_sync", lambda: [])

    entry = LockEntry(
        root_id="user", channel="market", directory_name=PLUGIN_ID, plugin_id=PLUGIN_ID,
        package_id=PLUGIN_ID, profile_dir="",
        reason="user_requested", installed_at="2026-10-15T00:00:00Z",
        updated_at="2026-10-15T00:00:00Z", last_seen_at="2026-10-15T00:00:00Z",
    )
    manager = SimpleNamespace(
        is_degraded=False,
        builtin_root=paths["BUILTIN_PLUGIN_CONFIG_ROOT"],
        user_root=paths["USER_PLUGIN_CONFIG_ROOT"],
        entry_for_directory=lambda *args, **kwargs: None,
        record_market_upgrade=lambda **kwargs: (entry, []),
        record_market_install=lambda **kwargs: (entry, []),
        find_active_user_entry=lambda _plugin_id: entry,
    )
    monkeypatch.setattr(cli_service, "get_install_source_manager", lambda: manager)
    monkeypatch.setattr(market_bridge, "get_install_source_manager", lambda: manager)
    service = cli_service.PluginCliService()
    monkeypatch.setattr(service, "_require_install_source_manager", lambda: manager)
    # Lock behavior has separate coverage; bypass only the decorator so this
    # transaction never creates a lock file under the user's application root.
    monkeypatch.setattr(
        service, "install",
        functools.partial(cli_service.PluginCliService.install.__wrapped__, service),
    )
    monkeypatch.setattr(
        service, "upload_and_install",
        functools.partial(cli_service.PluginCliService.upload_and_install.__wrapped__, service),
    )
    monkeypatch.setattr(
        market_bridge, "_replace_market_plugin_transaction",
        market_bridge._replace_market_plugin_transaction.__wrapped__,
    )

    async def get_service():
        return service

    monkeypatch.setattr(market_bridge, "get_plugin_cli_service", get_service)

    async def not_running(_plugin_id):
        return False

    async def no_refresh(_plugin_id):
        return None

    monkeypatch.setattr(replace, "_plugin_is_running", not_running)
    monkeypatch.setattr(replace, "_evict_replaced_plugin_modules", lambda _plugin_id: None)
    monkeypatch.setattr(replace, "_revoke_hot_reload_recovery", lambda _plugin_id: None)
    monkeypatch.setattr(cli_service, "_refresh_committed_market_install", no_refresh)

    return SimpleNamespace(
        root=tmp_path,
        user_root=paths["USER_PLUGIN_CONFIG_ROOT"],
        state_root=paths["PLUGIN_STATE_ROOT"],
        service=service,
        preference_store=preference_store,
        pending_store=pending_store,
    )


async def _upload_market_package(environment, *, mode: str, version: str):
    source = _write_plugin(environment.root / "source", version=version, payload="new code")
    package = environment.root / f"{PLUGIN_ID}.neko-plugin"
    build_plugin(source, package)
    return await cli_service.PluginCliService.upload_and_install.__wrapped__(
        environment.service,
        filename=package.name,
        package_path=str(package),
        install_source_override={
            "channel": "market",
            "mode": mode,
            "directory_name": PLUGIN_ID,
            "market_detail": {
                "plugin_market_id": PLUGIN_ID,
                "version": version,
                "package_url": f"https://example.invalid/{PLUGIN_ID}.neko-plugin",
                "expected_plugin_toml_id": PLUGIN_ID,
            },
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["upgrade", "reinstall"])
@pytest.mark.parametrize("auto_start", [True, False, None], ids=["on", "off", "manifest-default"])
@pytest.mark.parametrize("pending", [False, True], ids=["approved", "pending"])
@pytest.mark.parametrize("validation_fails", [False, True], ids=["success", "rollback"])
async def test_market_replacement_preserves_autostart_state(
    market_install_environment, monkeypatch, mode, auto_start, pending, validation_fails
) -> None:
    env = market_install_environment
    if auto_start is not None:
        env.preference_store[PLUGIN_ID] = {"enabled": False, "auto_start": auto_start}
    if pending:
        env.pending_store.add(PLUGIN_ID)
        autostart_approvals._cache.add(PLUGIN_ID)
    before_preferences = deepcopy(env.preference_store)
    before_pending = frozenset(env.pending_store)
    target = _write_plugin(env.user_root, version="1.0.0", payload="old code")
    next_version = "2.0.0" if mode == "upgrade" else "1.0.0"
    validation_observations = []

    source = _write_plugin(env.root / "source", version=next_version, payload="new code")
    package = env.root / f"{PLUGIN_ID}.neko-plugin"
    build_plugin(source, package)

    async def download(_url, _task):
        return package

    monkeypatch.setattr(market_bridge, "_download_package", download)

    original_plan = env.service.plan_install
    plans = []

    async def trace_plan(**kwargs):
        assert not target.exists(), "Market must back up the old code before planning"
        result = await original_plan(**kwargs)
        plans.append(result["action"])
        return result

    monkeypatch.setattr(env.service, "plan_install", trace_plan)
    original_validate = replace._validate_installed_identity

    def validate_new(layout):
        original_validate(layout)
        assert (target / "payload.txt").read_text(encoding="utf-8") == "new code"
        validation_observations.append(True)
        if validation_fails:
            raise ValueError("simulated Market post-install validation failure")

    monkeypatch.setattr(replace, "_validate_installed_identity", validate_new)
    task = {}
    replacement = market_bridge._do_upgrade(
        task,
        market_bridge.MarketInstallRequest(
            plugin_id=PLUGIN_ID,
            expected_plugin_toml_id=PLUGIN_ID,
            package_url=f"https://example.invalid/{PLUGIN_ID}.neko-plugin",
            package_sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
            version=next_version,
            mode=mode,
        ),
        {},
        record_as_reinstall=mode == "reinstall",
    )
    if validation_fails:
        with pytest.raises(market_bridge._TaskError) as raised:
            await replacement
        assert raised.value.code == "upgrade_rollback_completed"
        assert task["rollback"]["restored"] is True
        assert "simulated Market post-install validation failure" in raised.value.message
    else:
        await replacement
        assert task["result"]["rollback_status"] == "not_needed"
    assert validation_observations == [True]
    assert plans == ["install", "install"]

    expected_payload = "old code" if validation_fails else "new code"
    expected_version = "1.0.0" if validation_fails else next_version
    assert (target / "payload.txt").read_text(encoding="utf-8") == expected_payload
    manifest = tomllib.loads((target / "plugin.toml").read_text(encoding="utf-8"))
    assert manifest["plugin"]["version"] == expected_version
    assert manifest["plugin_runtime"]["auto_start"] is True
    # Drop the preference cache to verify durable state, not just this process.
    runtime_overrides.reset_cache_for_testing()
    assert runtime_overrides.load_runtime_overrides() == before_preferences
    assert env.preference_store == before_preferences
    assert frozenset(env.pending_store) == before_pending
    assert autostart_approvals.get_autostart_pending_snapshot() == before_pending


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_auto_start", [None, True], ids=["no-preference", "stale-on"])
async def test_market_first_install_still_disables_and_gates_autostart(
    market_install_environment, previous_auto_start
) -> None:
    env = market_install_environment
    if previous_auto_start is not None:
        env.preference_store[PLUGIN_ID] = {"enabled": True, "auto_start": previous_auto_start}

    await _upload_market_package(env, mode="install", version="1.0.0")

    target = env.user_root / PLUGIN_ID
    assert (target / "payload.txt").read_text(encoding="utf-8") == "new code"
    runtime_overrides.reset_cache_for_testing()
    override = runtime_overrides.get_runtime_override_entry(PLUGIN_ID)
    assert override["auto_start"] is False
    assert env.preference_store["unrelated"] == {"enabled": False, "auto_start": True}
    assert env.pending_store == {"unrelated_pending", PLUGIN_ID}
    assert autostart_approvals.get_autostart_pending_snapshot() == env.pending_store


@pytest.mark.asyncio
@pytest.mark.parametrize("first_write_fails", [False, True], ids=["post-install-idempotent", "preflight-fails"])
async def test_first_install_persists_off_before_promotion_without_a_second_write(
    market_install_environment, monkeypatch, first_write_fails
) -> None:
    """A read-only preference store must not permit an unguarded first install.

    The post-install pass is idempotent for the manifest ID already protected by
    the real install plan; it does not depend on another successful disk write.
    """
    env = market_install_environment
    before = deepcopy(env.preference_store)
    save_preferences = runtime_overrides._save_to_disk
    writes = []

    def fail_if_not_preflight(value):
        writes.append(deepcopy(value))
        assert not (env.user_root / PLUGIN_ID).exists(), "preferences must precede code promotion"
        if first_write_fails or len(writes) > 1:
            raise runtime_overrides.RuntimeOverrideWriteError("preference store is read-only")
        save_preferences(value)

    monkeypatch.setattr(runtime_overrides, "_save_to_disk", fail_if_not_preflight)
    if first_write_fails:
        with pytest.raises(ServerDomainError) as raised:
            await _upload_market_package(env, mode="install", version="1.0.0")
        assert raised.value.code == "PLUGIN_RUNTIME_PREFERENCE_PERSIST_FAILED"
        assert not (env.user_root / PLUGIN_ID).exists()
        expected = before
        assert env.pending_store == {"unrelated_pending"}
    else:
        result = await _upload_market_package(env, mode="install", version="1.0.0")
        assert (env.user_root / PLUGIN_ID / "plugin.toml").is_file()
        assert not result.get("autostart_gate_warning")
        expected = {**before, PLUGIN_ID: {"auto_start": False}}
        assert env.pending_store == {"unrelated_pending", PLUGIN_ID}
    assert len(writes) == 1
    runtime_overrides.reset_cache_for_testing()
    assert runtime_overrides.load_runtime_overrides() == expected
    assert env.preference_store == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plan",
    [
        {"action": "install", "package_type": "plugin", "plugin_id": "different_plugin"},
        {"action": "install", "package_type": "bundle", "plugin_id": PLUGIN_ID},
    ],
    ids=["different-plugin", "bundle"],
)
async def test_replacement_context_rejects_wrong_identity_before_writing(
    market_install_environment, monkeypatch, plan
) -> None:
    env = market_install_environment

    async def planned(**_kwargs):
        return plan

    def unexpected_write(*_args, **_kwargs):
        pytest.fail("invalid replacement must be rejected before promotion or preference writes")

    monkeypatch.setattr(env.service, "plan_install", planned)
    monkeypatch.setattr(env.service, "_install_sync", unexpected_write)
    monkeypatch.setattr(cli_service, "mark_autostart_pending", unexpected_write)
    monkeypatch.setattr(cli_service, "_persist_first_install_autostart_off", unexpected_write)
    with pytest.raises(ServerDomainError) as raised:
        await env.service.install(
            package="unused.neko-plugin", _preserve_autostart_plugin_id=PLUGIN_ID
        )
    assert raised.value.code == "PLUGIN_PACKAGE_IDENTITY_MISMATCH"
    assert raised.value.status_code == 409
