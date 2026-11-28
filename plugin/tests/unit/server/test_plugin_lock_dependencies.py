"""Installing an installed plugin's uv.lock dependencies into vendor/ (no real uv or network)."""

from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import sys

import pytest

from plugin.neko_plugin_cli.core import uv_lock_install as lock_install
from plugin.server.application import plugin_lock_dependencies as deps
from plugin.server.application.plugins import operation_lock
from plugin.server.infrastructure.plugin_tool_runtime import ToolRuntime

pytestmark = pytest.mark.plugin_unit
# The fixture replaces it; one test needs the real best-effort refresh.
_REAL_REFRESH = deps._refresh_registry

_LOCK = """version = 1

[[package]]
name = "orjson"
version = "3.13.0"
source = { registry = "https://pypi.org/simple" }
wheels = [
    { url = "https://files.example/orjson-3.13.0-py3-none-any.whl", size = 130518 },
]
"""


def _write_export(cmd: list[str], text: str) -> None:
    Path(cmd[cmd.index("--output-file") + 1]).write_text(text, encoding="utf-8")


class FakeUv:
    """Stands in for uv: records commands, installs dist-info into --target."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.install_error: str | None = None
        self.installed = [("orjson", "3.13.0")]

    def __call__(self, cmd, *, label, env, cancel, deadline):
        self.commands.append(cmd)
        self.envs.append(env)
        if cmd[1] == "export":
            _write_export(cmd, "orjson==3.13.0 \\\n    --hash=sha256:" + "0" * 64 + "\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if "--dry-run" in cmd:
            lines = "".join(f" + {name}=={version}\n" for name, version in self.installed)
            return subprocess.CompletedProcess(cmd, 0, "", f"Would install 1 package\n{lines}")
        if self.install_error is not None:
            return subprocess.CompletedProcess(cmd, 1, "", self.install_error)
        target = Path(cmd[cmd.index("--target") + 1])
        for name, version in self.installed:
            dist = target / f"{name}-{version}.dist-info"
            dist.mkdir(parents=True)
            (dist / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        return subprocess.CompletedProcess(cmd, 0, "", " + orjson==3.13.0\n")


@pytest.fixture
def plugin_dir(tmp_path):
    directory = tmp_path / "plugins" / "demo"
    directory.mkdir(parents=True)
    (directory / "plugin.toml").write_text('[plugin]\nid = "demo"\n', encoding="utf-8")
    (directory / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1"\ndependencies = ["orjson>=3"]\n', encoding="utf-8",
    )
    (directory / "uv.lock").write_text(_LOCK, encoding="utf-8")
    return directory


@pytest.fixture
def uv(monkeypatch, plugin_dir):
    fake = FakeUv()
    refreshed: list[str] = []

    async def refresh(plugin_id):
        refreshed.append(plugin_id)

    def passthrough(function):
        return function

    monkeypatch.setattr(deps, "_tasks", {})
    monkeypatch.setattr(deps, "run_tool_command", fake)
    monkeypatch.setattr(
        deps, "prepare_tool_runtime",
        lambda **_kwargs: ToolRuntime(sys.executable, "uv", {"UV_INDEX_URL": "https://dev.invalid"}, None, False),
    )
    monkeypatch.setattr(deps, "_plugin_dir", lambda _plugin_id: plugin_dir)
    monkeypatch.setattr(deps, "_refresh_registry", refresh)
    monkeypatch.setattr(operation_lock, "serialized_plugin_operation", passthrough)
    fake.refreshed = refreshed
    return fake


async def _run(confirm: str | None = None) -> dict[str, object]:
    await deps.request_dependency_install("demo", confirm_lock_sha256=confirm)
    await deps._tasks["demo"].runner
    return await deps.get_dependency_status("demo")


def _leftovers(plugin_dir: Path) -> list[str]:
    return sorted(path.name for path in plugin_dir.iterdir() if path.name.startswith(".vendor."))


def test_plan_lists_packages_with_sizes_and_waits_for_confirmation(uv, plugin_dir):
    status = asyncio.run(_run())

    assert status["state"] == "awaiting_confirmation"
    assert status["pending"] is True
    assert status["packages"] == [{"name": "orjson", "version": "3.13.0", "size": 130518}]
    assert status["total_size"] == 130518 and status["size_complete"] is True
    assert status["lock_sha256"] == lock_install.lock_sha256(plugin_dir)
    # Planning never installs.
    assert [cmd[1] for cmd in uv.commands] == ["export", "pip"]
    assert "--dry-run" in uv.commands[1]
    assert not (plugin_dir / "vendor").exists()


def test_uv_gets_one_index_from_the_hook(uv, monkeypatch):
    monkeypatch.setattr(deps, "plugin_dependency_index_url", lambda: "https://mirror.example/simple")
    asyncio.run(_run())

    for env in uv.envs:
        assert env["UV_DEFAULT_INDEX"] == "https://mirror.example/simple"
        assert "UV_INDEX_URL" not in env


def test_default_index_is_official_pypi():
    assert deps.plugin_dependency_index_url() == "https://pypi.org/simple"


def test_confirmed_install_swaps_vendor_in_and_records_the_lock(uv, plugin_dir):
    planned = asyncio.run(_run())
    status = asyncio.run(_run(planned["lock_sha256"]))

    assert status["state"] == "completed"
    assert status["pending"] is False
    vendor = plugin_dir / "vendor"
    assert (vendor / "orjson-3.13.0.dist-info" / "METADATA").is_file()
    assert lock_install.read_lock_marker(vendor) == planned["lock_sha256"]
    install = uv.commands[-1]
    assert "--dry-run" not in install and "--require-hashes" in install
    assert Path(install[install.index("--target") + 1]).name.startswith(".vendor.staging-")
    assert _leftovers(plugin_dir) == []
    assert uv.refreshed == ["demo"]


def test_failed_install_keeps_the_previous_state(uv, plugin_dir):
    planned = asyncio.run(_run())
    uv.install_error = "error: Failed to fetch: `https://user:secret@pypi.example/simple/orjson/`"

    status = asyncio.run(_run(planned["lock_sha256"]))

    assert status["state"] == "failed"
    assert status["error_code"] == "network"
    assert "secret" not in status["error_detail"]
    assert status["pending"] is True
    assert not (plugin_dir / "vendor").exists()
    assert _leftovers(plugin_dir) == []


def test_failed_reinstall_leaves_the_existing_vendor_untouched(uv, plugin_dir):
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    lock_install.write_lock_marker(vendor, "f" * 64)
    (vendor / "keep.txt").write_text("old", encoding="utf-8")
    planned = asyncio.run(_run())
    uv.install_error = "Because orjson==3.13.0 has no usable wheels ..."

    status = asyncio.run(_run(planned["lock_sha256"]))

    assert status["error_code"] == "no_wheel"
    assert (vendor / "keep.txt").read_text(encoding="utf-8") == "old"
    assert lock_install.read_lock_marker(vendor) == "f" * 64
    assert _leftovers(plugin_dir) == []


def test_retry_after_failure_installs_without_asking_again(uv, plugin_dir):
    planned = asyncio.run(_run())
    uv.install_error = "error: Failed to fetch"
    assert asyncio.run(_run(planned["lock_sha256"]))["state"] == "failed"
    uv.install_error = None

    status = asyncio.run(_run(planned["lock_sha256"]))

    assert status["state"] == "completed"
    assert status["error_code"] is None


def test_changed_lock_is_shown_again_instead_of_installed(uv, plugin_dir):
    planned = asyncio.run(_run())
    (plugin_dir / "uv.lock").write_text(_LOCK + "\n# new release\n", encoding="utf-8")

    status = asyncio.run(_run(planned["lock_sha256"]))

    assert status["state"] == "awaiting_confirmation"
    assert status["lock_sha256"] != planned["lock_sha256"]
    assert not (plugin_dir / "vendor").exists()


def test_lock_changed_during_install_is_not_swapped_in(uv, plugin_dir):
    planned = asyncio.run(_run())
    original = uv.__call__

    def change_lock_while_installing(cmd, **kwargs):
        result = original(cmd, **kwargs)
        if "--target" in cmd and "--dry-run" not in cmd:
            (plugin_dir / "uv.lock").write_text(_LOCK + "\n# upgraded meanwhile\n", encoding="utf-8")
        return result

    deps.run_tool_command = change_lock_while_installing  # restored by monkeypatch
    status = asyncio.run(_run(planned["lock_sha256"]))

    assert status["error_code"] == "lock_changed"
    assert not (plugin_dir / "vendor").exists()
    assert _leftovers(plugin_dir) == []


def test_lock_that_does_not_cover_pyproject_never_replaces_vendor(uv, plugin_dir):
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    lock_install.write_lock_marker(vendor, "f" * 64)
    (vendor / "keep.txt").write_text("old", encoding="utf-8")
    uv.installed = [("anyio", "4.16.0")]
    planned = asyncio.run(_run())

    status = asyncio.run(_run(planned["lock_sha256"]))

    assert status["state"] == "failed"
    assert status["error_code"] == "lock_mismatch"
    # Checked in staging, before the swap: the previous vendor/ is intact.
    assert (vendor / "keep.txt").read_text(encoding="utf-8") == "old"
    assert lock_install.read_lock_marker(vendor) == "f" * 64
    assert _leftovers(plugin_dir) == []


def test_confirming_a_ready_plan_reports_installing_right_away(uv, plugin_dir):
    planned = asyncio.run(_run())

    async def confirm():
        returned = await deps.request_dependency_install("demo", confirm_lock_sha256=planned["lock_sha256"])
        await deps._tasks["demo"].runner
        return returned

    # The dialog polls only while planning or installing.
    assert asyncio.run(confirm())["state"] == "installing"


def test_direct_url_sources_are_rejected_before_uv_fetches_anything(uv, plugin_dir):
    original = uv.__call__

    def export_with_direct_url(cmd, **kwargs):
        if cmd[1] == "export":
            uv.commands.append(cmd)
            _write_export(cmd, "evil @ http://127.0.0.1:8080/admin/x.whl \\\n    --hash=sha256:" + "0" * 64 + "\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return original(cmd, **kwargs)

    deps.run_tool_command = export_with_direct_url  # restored by monkeypatch
    status = asyncio.run(_run())

    assert status["state"] == "failed"
    assert status["error_code"] == "unpinned_source"
    assert [cmd[1] for cmd in uv.commands] == ["export"]  # no dry-run ever ran


def test_vendor_installed_for_another_python_is_pending_again(uv, plugin_dir):
    vendor = plugin_dir / "vendor"
    dist = vendor / "orjson-3.13.0.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: orjson\nVersion: 3.13.0\n", encoding="utf-8")
    lock_install.write_lock_marker(vendor, lock_install.lock_sha256(plugin_dir))
    assert deps.dependencies_pending(plugin_dir) is False

    lock_install.write_lock_marker(vendor, lock_install.lock_sha256(plugin_dir), python="cp27-plan9")

    assert deps.dependencies_pending(plugin_dir) is True
    planned = asyncio.run(_run())
    assert asyncio.run(_run(planned["lock_sha256"]))["state"] == "completed"
    assert deps.dependencies_pending(plugin_dir) is False


def test_empty_vendor_dir_from_the_archive_still_installs(uv, plugin_dir):
    (plugin_dir / "vendor").mkdir()

    assert deps.dependencies_pending(plugin_dir) is True
    planned = asyncio.run(_run())
    assert asyncio.run(_run(planned["lock_sha256"]))["state"] == "completed"


def test_legacy_requirements_txt_keeps_the_old_path(uv, plugin_dir):
    (plugin_dir / "requirements.txt").write_text("orjson\n", encoding="utf-8")

    assert deps.dependencies_pending(plugin_dir) is False


def test_plugin_shipping_its_own_vendor_is_not_touched(uv, plugin_dir):
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    (vendor / "shipped.txt").write_text("mine", encoding="utf-8")

    status = asyncio.run(deps.request_dependency_install("demo"))

    assert status == {"plugin_id": "demo", "state": None, "pending": False}
    assert "demo" not in deps._tasks
    assert uv.commands == []
    assert sorted(path.name for path in vendor.iterdir()) == ["shipped.txt"]


def test_plugin_without_lock_is_not_pending(uv, plugin_dir):
    (plugin_dir / "uv.lock").unlink()
    assert deps.dependencies_pending(plugin_dir) is False


def test_swap_restores_the_old_vendor_when_the_rename_fails(plugin_dir, monkeypatch):
    vendor = plugin_dir / "vendor"
    vendor.mkdir()
    (vendor / "old.txt").write_text("old", encoding="utf-8")
    staging = plugin_dir / ".vendor.staging-0123abcd"
    staging.mkdir()
    real_rename = Path.rename

    def failing_rename(self, target):
        if self == staging:
            raise OSError("disk full")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", failing_rename)
    with pytest.raises(OSError):
        deps._swap_in(plugin_dir, staging)

    assert (vendor / "old.txt").read_text(encoding="utf-8") == "old"
    assert not any(path.name.startswith(".vendor.backup-") for path in plugin_dir.iterdir())


@pytest.mark.parametrize(
    ("output", "code"),
    [
        ("error: In `--require-hashes` mode, all requirements must have a hash, but none were provided for: file:///x",
         "unpinned_source"),
        ("Hash mismatch for `idna==3.20`", "hash_mismatch"),
        ("Because docopt==0.6.2 has no usable wheels and you require docopt==0.6.2", "no_wheel"),
        ("Because foo was not found in the package registry and you require foo", "not_on_index"),
        ("Failed to fetch: https://pypi.org/simple/httpx/ dns error", "network"),
        ("error sending request for url (https://pypi.org/simple/x/): connection refused", "network"),
        # Package names must not read as network failures.
        ("Because networkx==3.4 has no usable wheels and you require networkx==3.4", "no_wheel"),
        ("error: Failed to install: tlslite_ng-0.8.0 (something else)", "install_failed"),
        ("something else", "install_failed"),
    ],
)
def test_uv_errors_map_to_user_messages(output, code):
    assert deps._classify(output) == code


def test_builtin_plugin_reads_as_not_pending_without_an_error(monkeypatch):
    monkeypatch.setattr(deps, "_plugin_dir", lambda _plugin_id: None)

    assert asyncio.run(deps.get_dependency_status("builtin")) == {
        "plugin_id": "builtin", "state": None, "pending": False,
    }
    with pytest.raises(deps.ServerDomainError) as caught:
        asyncio.run(deps.request_dependency_install("builtin"))
    assert caught.value.code == "PLUGIN_DEPENDENCIES_NOT_APPLICABLE"


def test_uninstall_during_install_leaves_no_recreated_plugin_dir(tmp_path):
    plugin_dir = tmp_path / "plugins" / "gone"
    staging = plugin_dir / ".vendor.staging-0123abcd"
    staging.mkdir(parents=True)  # what uv --target does after the plugin was removed

    deps._discard_staging(plugin_dir, staging)

    assert not plugin_dir.exists()


def test_discarding_staging_never_removes_an_installed_plugin(plugin_dir):
    staging = plugin_dir / ".vendor.staging-0123abcd"
    staging.mkdir()

    deps._discard_staging(plugin_dir, staging)

    assert (plugin_dir / "plugin.toml").is_file() and not staging.exists()



def test_plugin_dir_replaced_during_install_is_not_swapped_in(uv, plugin_dir):
    """An upgrade with the same uv.lock renames the plugin away while uv runs."""
    planned = asyncio.run(_run())
    original = uv.__call__
    moved = plugin_dir.parent / "demo.bak"

    def upgrade_while_installing(cmd, **kwargs):
        if "--target" in cmd and "--dry-run" not in cmd:
            plugin_dir.rename(moved)  # what the replace transaction does
            plugin_dir.mkdir()
            for name in ("plugin.toml", "pyproject.toml", "uv.lock"):
                (plugin_dir / name).write_bytes((moved / name).read_bytes())
        return original(cmd, **kwargs)  # uv keeps writing at the same path

    deps.run_tool_command = upgrade_while_installing  # restored by monkeypatch
    status = asyncio.run(_run(planned["lock_sha256"]))

    assert status["state"] == "failed"
    assert status["error_code"] == "lock_changed"
    assert not (plugin_dir / "vendor").exists()
    assert _leftovers(plugin_dir) == []


def test_lock_with_a_url_source_is_refused_before_any_uv_command(uv, plugin_dir, monkeypatch):
    prepared: list[object] = []
    monkeypatch.setattr(deps, "prepare_tool_runtime", lambda **kwargs: prepared.append(kwargs))
    (plugin_dir / "uv.lock").write_text(
        _LOCK + '\n[[package]]\nname = "evil"\nversion = "1"\nsource = { url = "http://127.0.0.1/x.whl" }\n',
        encoding="utf-8",
    )

    status = asyncio.run(_run())

    assert status["state"] == "failed"
    assert status["error_code"] == "unpinned_source"
    assert "127.0.0.1" in status["error_detail"]
    assert uv.commands == [] and prepared == []


def test_running_plugin_is_never_given_a_new_vendor(uv, plugin_dir, monkeypatch):
    monkeypatch.setattr(deps, "_plugin_running", lambda _plugin_id: True)

    assert asyncio.run(deps.request_dependency_install("demo")) == {
        "plugin_id": "demo", "state": None, "pending": False,
    }
    assert uv.commands == []


def test_lock_wait_timeout_is_a_retryable_busy(uv, plugin_dir, monkeypatch):
    from plugin.server.application.plugins.operation_lock import PluginOperationBusy

    def busy(function):
        async def wrapped(*args, **kwargs):
            raise PluginOperationBusy("held")
        return wrapped

    planned = asyncio.run(_run())
    monkeypatch.setattr(operation_lock, "serialized_plugin_operation", busy)

    status = asyncio.run(_run(planned["lock_sha256"]))

    assert status["error_code"] == "busy"
    assert not (plugin_dir / "vendor").exists() and _leftovers(plugin_dir) == []



def test_lock_edited_in_place_makes_the_installed_vendor_pending(uv, plugin_dir):
    planned = asyncio.run(_run())
    assert asyncio.run(_run(planned["lock_sha256"]))["state"] == "completed"
    assert deps.dependencies_pending(plugin_dir) is False

    (plugin_dir / "uv.lock").write_text(_LOCK + "\n# bumped in a source checkout\n", encoding="utf-8")

    assert deps.dependencies_pending(plugin_dir) is True


def test_registry_refresh_failure_after_the_swap_still_completes(uv, plugin_dir, monkeypatch):
    from plugin.server.application.plugins import registry_service
    from plugin.server.application.plugins.operation_lock import PluginOperationBusy

    async def busy_refresh(self, plugin_id):
        raise PluginOperationBusy("held")

    monkeypatch.setattr(deps, "_refresh_registry", _REAL_REFRESH)
    monkeypatch.setattr(registry_service.PluginRegistryService, "refresh_plugin", busy_refresh)
    planned = asyncio.run(_run())

    status = asyncio.run(_run(planned["lock_sha256"]))

    assert status["state"] == "completed"
    assert (plugin_dir / "vendor" / "orjson-3.13.0.dist-info").is_dir()


def test_status_reads_the_package_list_once():
    task = deps._Task("demo", Path("."))
    task.packages = [lock_install.LockedPackage("a", "1", 10), lock_install.LockedPackage("b", "1", None)]

    status = task.as_dict()

    assert status["total_size"] == 10 and status["size_complete"] is False


def test_status_never_pairs_awaiting_confirmation_with_an_empty_list():
    """The planner publishes packages, then the state, while a status read is under way."""

    class RacingTask(deps._Task):
        published = False

        def __getattribute__(self, name):
            value = object.__getattribute__(self, name)
            if name == "packages" and not object.__getattribute__(self, "published"):
                # The worker publishes right after this read returned the old list.
                object.__setattr__(self, "published", True)
                object.__setattr__(self, "packages", [lock_install.LockedPackage("a", "1", 10)])
                object.__setattr__(self, "state", "awaiting_confirmation")
            return value

    status = RacingTask("demo", Path(".")).as_dict()

    assert not (status["state"] == "awaiting_confirmation" and status["packages"] == [])
