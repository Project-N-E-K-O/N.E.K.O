from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import shutil
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from plugin.server.routes import market_bridge
from tests.fake_clock import patch_module_clock


pytestmark = pytest.mark.plugin_unit


def _market_install_request(
    plugin_id: str = "demo",
    *,
    require_confirm: bool = True,
) -> market_bridge.MarketInstallRequest:
    return market_bridge.MarketInstallRequest(
        package_url=f"https://downloads.example/{plugin_id}.neko-plugin",
        package_sha256="a" * 64,
        plugin_id="42",
        version="1.0.0",
        expected_plugin_toml_id=plugin_id,
        require_confirm=require_confirm,
    )


def _bridge_request(*, origin: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/market/tasks/task-id/confirm",
            "raw_path": b"/market/tasks/task-id/confirm",
            "query_string": b"",
            "headers": [
                (b"host", b"127.0.0.1:48911"),
                (b"origin", origin.encode("ascii")),
            ],
            "client": ("127.0.0.1", 54321),
            "server": ("127.0.0.1", 48911),
        }
    )


@pytest.mark.asyncio
async def test_market_install_rejects_capacity_instead_of_evicting_active_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_worker = asyncio.create_task(asyncio.Event().wait())
    monkeypatch.setattr(market_bridge, "_TASK_MAX_ENTRIES", 1)
    monkeypatch.setattr(
        market_bridge,
        "_tasks",
        {
            "active": {
                "task_id": "active",
                "status": "installing",
                "created_at": 1.0,
                "completed_at": None,
                "logical_plugin_key": "another-plugin",
            }
        },
    )
    monkeypatch.setattr(market_bridge, "_task_workers", {"active": active_worker})

    try:
        with pytest.raises(HTTPException) as exc_info:
            await market_bridge.market_install(
                _market_install_request("capacity-demo"),
                market_bridge.get_bridge_token(),
            )
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["code"] == "market_task_capacity_reached"
        assert market_bridge._tasks["active"]["status"] == "installing"
        assert market_bridge._task_workers["active"] is active_worker
        assert not active_worker.done()
    finally:
        active_worker.cancel()
        await asyncio.gather(active_worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_external_handoffs_cannot_consume_local_plugin_center_reserve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market_bridge, "_tasks", {})
    monkeypatch.setattr(market_bridge, "_task_workers", {})
    monkeypatch.setattr(market_bridge, "_TASK_MAX_ENTRIES", 3)
    monkeypatch.setattr(market_bridge, "_LOCAL_TASK_RESERVED_ENTRIES", 1)
    monkeypatch.setattr(market_bridge, "_main_server_port", lambda: 48911)

    for plugin_id in ("external-one", "external-two"):
        response = await market_bridge.market_install(
            _market_install_request(plugin_id),
            market_bridge.get_bridge_token(),
            origin="https://market.project-neko.cn",
        )
        assert response.status == "awaiting_confirmation"

    with pytest.raises(HTTPException) as external_error:
        await market_bridge.market_install(
            _market_install_request("external-three"),
            market_bridge.get_bridge_token(),
            origin="https://market.project-neko.cn",
        )
    assert external_error.value.status_code == 429
    assert external_error.value.detail["code"] == "external_handoff_capacity_reached"

    local_response = await market_bridge.market_install(
        _market_install_request("local-plugin-center"),
        market_bridge.get_bridge_token(),
        origin="http://127.0.0.1:48911",
    )
    assert local_response.status == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_external_handoffs_are_limited_per_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market_bridge, "_tasks", {})
    monkeypatch.setattr(market_bridge, "_task_workers", {})
    monkeypatch.setattr(market_bridge, "_TASK_MAX_ENTRIES", 200)
    monkeypatch.setattr(market_bridge, "_EXTERNAL_SOURCE_PENDING_LIMIT", 2, raising=False)
    monkeypatch.setattr(market_bridge, "_EXTERNAL_SOURCE_RATE_LIMIT", 20, raising=False)
    monkeypatch.setattr(market_bridge, "_external_handoff_attempts", {}, raising=False)

    for plugin_id, origin in (
        ("source-one", "https://market.project-neko.cn"),
        ("source-two", "https://spoofed-origin.example"),
    ):
        response = await market_bridge.market_install(
            _market_install_request(plugin_id),
            market_bridge.get_bridge_token(),
            origin=origin,
        )
        assert response.status == "awaiting_confirmation"

    with pytest.raises(HTTPException) as exc_info:
        await market_bridge.market_install(
            _market_install_request("source-three"),
            market_bridge.get_bridge_token(),
            origin="https://market.project-neko.cn",
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["code"] == "external_handoff_source_limit_reached"


@pytest.mark.asyncio
async def test_external_handoff_creation_is_rate_limited_per_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market_bridge, "_tasks", {})
    monkeypatch.setattr(market_bridge, "_task_workers", {})
    monkeypatch.setattr(market_bridge, "_TASK_MAX_ENTRIES", 200)
    monkeypatch.setattr(market_bridge, "_EXTERNAL_SOURCE_PENDING_LIMIT", 20, raising=False)
    monkeypatch.setattr(market_bridge, "_EXTERNAL_SOURCE_RATE_LIMIT", 2, raising=False)
    monkeypatch.setattr(market_bridge, "_external_handoff_attempts", {}, raising=False)

    for plugin_id in ("rate-one", "rate-two"):
        response = await market_bridge.market_install(
            _market_install_request(plugin_id),
            market_bridge.get_bridge_token(),
            origin="https://market.project-neko.cn",
        )
        assert response.status == "awaiting_confirmation"

    with pytest.raises(HTTPException) as exc_info:
        await market_bridge.market_install(
            _market_install_request("rate-three"),
            market_bridge.get_bridge_token(),
            origin="https://market.project-neko.cn",
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["code"] == "external_handoff_rate_limited"


def test_external_handoff_rate_state_expires_without_leaking_source_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    patch_module_clock(monkeypatch, market_bridge, time=lambda: now[0])
    attempts = {"old.example": market_bridge.deque([100.0])}
    monkeypatch.setattr(market_bridge, "_external_handoff_attempts", attempts)
    monkeypatch.setattr(market_bridge, "_tasks", {})

    now[0] += market_bridge._EXTERNAL_SOURCE_RATE_WINDOW_SECONDS + 1
    market_bridge._cleanup_tasks()

    assert market_bridge._external_handoff_attempts == {}


@pytest.mark.asyncio
async def test_market_worker_shutdown_cancels_and_awaits_every_active_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    finished = asyncio.Event()

    async def worker() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    active_worker = asyncio.create_task(worker())
    await started.wait()
    monkeypatch.setattr(market_bridge, "_task_workers", {"active": active_worker})

    await market_bridge.shutdown_market_task_workers()

    assert finished.is_set()
    assert active_worker.done()
    assert market_bridge._task_workers == {}


def _payload(plugin_id: str = "demo") -> SimpleNamespace:
    return SimpleNamespace(
        plugin_id=plugin_id,
        version="2.0.0",
        expected_plugin_toml_id=plugin_id,
        package_url="https://example.invalid/demo.neko-plugin",
        package_sha256="a" * 64,
        payload_hash="",
        channel="stable",
        published_at="",
    )


def test_market_download_url_rejects_plain_http_and_loopback() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        market_bridge._validate_market_download_url(
            "http://downloads.example/plugin.neko-plugin"
        )
    with pytest.raises(ValueError, match="public Internet"):
        market_bridge._validate_market_download_url(
            "https://127.0.0.1/plugin.neko-plugin"
        )


def test_market_download_url_rejects_hostname_resolving_to_private_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        market_bridge.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (market_bridge.socket.AF_INET, 0, 0, "", ("10.10.0.5", 443))
        ],
    )

    with pytest.raises(ValueError, match="public Internet"):
        market_bridge._validate_market_download_url(
            "https://github.com/example/plugin.neko-plugin"
        )


def test_market_download_url_rejects_untrusted_public_https_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        market_bridge.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (market_bridge.socket.AF_INET, 0, 0, "", ("93.184.216.34", 443))
        ],
    )

    with pytest.raises(ValueError, match="trusted release host"):
        market_bridge._validate_market_download_url(
            "https://downloads.example/plugin.neko-plugin"
        )


@pytest.mark.parametrize(
    "url,fake_ip",
    [
        (
            "https://github.com/example/plugin/releases/download/v1/plugin.neko-plugin",
            "198.18.0.154",
        ),
        (
            "https://release-assets.githubusercontent.com/github-production-release-asset/1/plugin",
            "198.18.0.155",
        ),
    ],
)
def test_market_download_url_accepts_proxy_fake_ip_for_trusted_github_release_hosts(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    fake_ip: str,
) -> None:
    monkeypatch.setattr(
        market_bridge.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (market_bridge.socket.AF_INET, 0, 0, "", (fake_ip, 443))
        ],
    )

    assert market_bridge._validate_market_download_url(url) == url


def test_market_download_url_rejects_proxy_fake_ip_for_untrusted_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        market_bridge.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (market_bridge.socket.AF_INET, 0, 0, "", ("198.18.0.99", 443))
        ],
    )

    with pytest.raises(ValueError, match="trusted release host"):
        market_bridge._validate_market_download_url(
            "https://attacker.example/plugin.neko-plugin"
        )


@pytest.mark.parametrize("private_ip", ["127.0.0.1", "10.0.0.8", "169.254.1.2"])
def test_market_download_url_still_rejects_private_ips_for_trusted_github_host(
    monkeypatch: pytest.MonkeyPatch,
    private_ip: str,
) -> None:
    monkeypatch.setattr(
        market_bridge.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (market_bridge.socket.AF_INET, 0, 0, "", (private_ip, 443))
        ],
    )

    with pytest.raises(ValueError, match="public Internet"):
        market_bridge._validate_market_download_url(
            "https://github.com/example/plugin.neko-plugin"
        )


def test_market_download_url_rejects_mixed_public_and_private_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        market_bridge.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (market_bridge.socket.AF_INET, 0, 0, "", ("93.184.216.34", 443)),
            (market_bridge.socket.AF_INET, 0, 0, "", ("10.0.0.8", 443)),
        ],
    )

    with pytest.raises(ValueError, match="public Internet"):
        market_bridge._validate_market_download_url(
            "https://github.com/example/plugin.neko-plugin"
        )


def test_market_install_request_requires_logical_manifest_identity() -> None:
    with pytest.raises(ValidationError):
        market_bridge.MarketInstallRequest(
            package_url="https://downloads.example/plugin.neko-plugin",
            package_sha256="a" * 64,
            plugin_id="123",
        )


def test_market_install_request_never_honors_browser_rename_policy() -> None:
    payload = market_bridge.MarketInstallRequest(
        package_url="https://downloads.example/plugin.neko-plugin",
        package_sha256="a" * 64,
        plugin_id="123",
        expected_plugin_toml_id="demo",
        on_conflict="rename",
    )

    assert payload.on_conflict == "fail"


@pytest.mark.asyncio
async def test_market_request_waits_for_local_confirmation_even_when_browser_opts_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks: dict[str, dict[str, Any]] = {}
    workers: dict[str, asyncio.Task[None]] = {}
    execution_started = asyncio.Event()

    async def fake_execute(_task_id: str, _payload: object) -> None:
        execution_started.set()

    monkeypatch.setattr(market_bridge, "_tasks", tasks)
    monkeypatch.setattr(market_bridge, "_task_workers", workers)
    monkeypatch.setattr(market_bridge, "_execute_install", fake_execute)

    response = await market_bridge.market_install(
        _market_install_request(require_confirm=False),
        market_bridge._BRIDGE_TOKEN,
    )
    repeated = await market_bridge.market_install(
        _market_install_request(require_confirm=True),
        market_bridge._BRIDGE_TOKEN,
    )

    try:
        assert response.status == "awaiting_confirmation"
        assert repeated.task_id == response.task_id
        assert tasks[response.task_id]["status"] == "awaiting_confirmation"
        assert tasks[response.task_id]["available_actions"] == [
            "open_plugin_center",
            "cancel",
        ]
        assert workers == {}
        assert not execution_started.is_set()
    finally:
        for worker in workers.values():
            worker.cancel()
        if workers:
            await asyncio.gather(*workers.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_only_local_plugin_manager_can_confirm_market_task_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks: dict[str, dict[str, Any]] = {}
    workers: dict[str, asyncio.Task[None]] = {}
    execution_started = asyncio.Event()
    release_execution = asyncio.Event()
    execution_calls: list[str] = []

    async def fake_execute(task_id: str, _payload: object) -> None:
        execution_calls.append(task_id)
        execution_started.set()
        await release_execution.wait()

    monkeypatch.setattr(market_bridge, "_tasks", tasks)
    monkeypatch.setattr(market_bridge, "_task_workers", workers)
    monkeypatch.setattr(market_bridge, "_execute_install", fake_execute)
    monkeypatch.setattr(market_bridge, "_main_server_port", lambda: 48911)

    created = await market_bridge.market_install(
        _market_install_request(),
        market_bridge._BRIDGE_TOKEN,
    )

    with pytest.raises(HTTPException) as remote_error:
        await market_bridge.confirm_market_install_task(
            created.task_id,
            _bridge_request(origin="https://market.project-neko.cn"),
            market_bridge._BRIDGE_TOKEN,
        )
    assert remote_error.value.status_code == 403
    assert workers == {}

    local_request = _bridge_request(origin="http://127.0.0.1:48911")
    first = await market_bridge.confirm_market_install_task(
        created.task_id,
        local_request,
        market_bridge._BRIDGE_TOKEN,
    )
    second = await market_bridge.confirm_market_install_task(
        created.task_id,
        local_request,
        market_bridge._BRIDGE_TOKEN,
    )

    assert first.status == "pending"
    assert second.task_id == first.task_id
    await execution_started.wait()
    assert execution_calls == [created.task_id]

    release_execution.set()
    await asyncio.gather(*workers.values())


@pytest.mark.asyncio
async def test_canceling_unconfirmed_market_task_never_starts_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks: dict[str, dict[str, Any]] = {}
    workers: dict[str, asyncio.Task[None]] = {}
    execution_started = asyncio.Event()

    async def fake_execute(_task_id: str, _payload: object) -> None:
        execution_started.set()

    monkeypatch.setattr(market_bridge, "_tasks", tasks)
    monkeypatch.setattr(market_bridge, "_task_workers", workers)
    monkeypatch.setattr(market_bridge, "_execute_install", fake_execute)

    created = await market_bridge.market_install(
        _market_install_request(),
        market_bridge._BRIDGE_TOKEN,
    )
    canceled = await market_bridge.cancel_market_install_task(
        created.task_id,
        market_bridge._BRIDGE_TOKEN,
    )

    assert canceled.status == "canceled"
    assert canceled.completed_at is not None
    assert canceled.available_actions == []
    assert workers == {}
    assert not execution_started.is_set()
    assert "pending_payload" not in tasks[created.task_id]


@pytest.mark.asyncio
async def test_unconfirmed_market_handoff_expires_without_starting_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks: dict[str, dict[str, Any]] = {}
    workers: dict[str, asyncio.Task[None]] = {}
    now = [1_000.0]

    monkeypatch.setattr(market_bridge, "_tasks", tasks)
    monkeypatch.setattr(market_bridge, "_task_workers", workers)
    patch_module_clock(monkeypatch, market_bridge, time=lambda: now[0])

    created = await market_bridge.market_install(
        _market_install_request(),
        market_bridge._BRIDGE_TOKEN,
    )
    now[0] += market_bridge._INSTALL_CONFIRMATION_TTL_SECONDS + 1

    expired = await market_bridge.market_task_status(
        created.task_id,
        market_bridge._BRIDGE_TOKEN,
    )

    assert expired.status == "canceled"
    assert expired.error_code == "install_confirmation_expired"
    assert expired.available_actions == ["retry"]
    assert workers == {}
    assert "pending_payload" not in tasks[created.task_id]


@pytest.mark.asyncio
async def test_pending_market_confirmations_are_visible_only_to_local_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(market_bridge, "_tasks", tasks)
    monkeypatch.setattr(market_bridge, "_task_workers", {})
    monkeypatch.setattr(market_bridge, "_main_server_port", lambda: 48911)

    created = await market_bridge.market_install(
        _market_install_request("external_demo"),
        market_bridge._BRIDGE_TOKEN,
    )

    with pytest.raises(HTTPException) as remote_error:
        await market_bridge.market_pending_confirmations(
            _bridge_request(origin="https://market.project-neko.cn"),
            market_bridge._BRIDGE_TOKEN,
        )
    assert remote_error.value.status_code == 403

    pending = await market_bridge.market_pending_confirmations(
        _bridge_request(origin="http://127.0.0.1:48911"),
        market_bridge._BRIDGE_TOKEN,
    )

    assert [item.task_id for item in pending] == [created.task_id]
    assert pending[0].plugin_id == "external_demo"
    assert pending[0].version == "1.0.0"
    assert pending[0].package_sha256 == "a" * 64
    assert pending[0].package_host == "downloads.example"
    assert not hasattr(pending[0], "package_url")


def test_market_domain_error_keeps_reason_details_actions_and_correlation_id() -> None:
    domain_error = market_bridge.ServerDomainError(
        code="PLUGIN_PACKAGE_IDENTITY_MISMATCH",
        message="market identity does not match package manifest",
        status_code=422,
        details={"expected_plugin_id": "demo", "actual_plugin_id": "other"},
    )
    task_error = market_bridge._task_error_from_domain_error(domain_error)
    task: dict[str, object] = {"stage": "verify", "progress": 0.5}

    market_bridge._finalize_task_failure(
        task,
        task_error,
        time.monotonic(),
        {"task_id": "correlation-123", "plugin_id": "demo"},
    )

    assert task["error_code"] == "PLUGIN_PACKAGE_IDENTITY_MISMATCH"
    assert task["error_details"] == {
        "expected_plugin_id": "demo",
        "actual_plugin_id": "other",
    }
    assert task["available_actions"] == ["choose_matching_package"]
    assert task["correlation_id"] == "correlation-123"


def _entry(plugin_id: str = "demo", package_id: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        plugin_id=plugin_id,
        directory_name=plugin_id,
        source_detail=None,
        package_id=package_id,
    )


def _configure_paths(
    monkeypatch: pytest.MonkeyPatch,
    *,
    plugins_root: Path,
    profiles_root: Path,
) -> None:
    policy = SimpleNamespace(
        user_plugins_root=plugins_root,
        install_plugins_root=plugins_root,
        package_profiles_root=profiles_root,
        package_artifacts_root=plugins_root.parent / "packages",
    )
    monkeypatch.setattr(
        market_bridge.PluginCliPathPolicy,
        "from_settings",
        classmethod(lambda cls: policy),
    )
    monkeypatch.setattr(
        market_bridge,
        "get_install_source_manager",
        lambda: SimpleNamespace(find_active_market_entry=lambda plugin_id: _entry(plugin_id)),
    )
    monkeypatch.setattr(
        market_bridge,
        "inspect_package",
        lambda path: SimpleNamespace(package_id="demo"),
    )
    monkeypatch.setattr(
        market_bridge,
        "collect_package_state_files",
        lambda _path: {"demo": {}},
    )
    monkeypatch.setattr(
        market_bridge,
        "get_user_installation_package_state_files",
        lambda _plugin_id, **_kwargs: None,
    )


def test_market_install_status_promotes_pending_restart_activation() -> None:
    result = market_bridge._with_market_operation_status(
        {
            "unpack": {
                "activation": {
                    "status": "pending_restart",
                    "plugin_ids": ["demo"],
                    "reason": "currently_running_version_remains_active_until_restart",
                }
            },
            "install": {},
        },
        operation="install",
        restarted=False,
        rollback_status="not_needed",
    )

    assert result["activation"] == {
        "status": "pending_restart",
        "plugin_ids": ["demo"],
        "reason": "currently_running_version_remains_active_until_restart",
    }
    assert result["install"]["activation"] == result["activation"]
    assert market_bridge._market_install_success_message(result) == (
        "安装成功，重启 N.E.K.O 后切换到新版本"
    )


@pytest.mark.asyncio
async def test_market_upgrade_delegates_file_replacement_to_shared_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    monkeypatch.setattr(market_bridge, "_download_package", lambda _url, _task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)

    calls: list[dict[str, Any]] = []

    async def shared_replace(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            install_result={"operation": "upgrade"},
            restarted=False,
            rollback_status="not_needed",
            backup_dir=tmp_path / "backup",
        )

    monkeypatch.setattr(market_bridge, "replace_plugin", shared_replace, raising=False)

    task: dict[str, Any] = {}
    await market_bridge._do_upgrade(task, _payload(), {})

    assert len(calls) == 1
    assert calls[0]["layout"].installed_dir == plugin_dir.resolve()
    assert calls[0]["additional_targets"] == (profiles_root / "demo",)
    assert calls[0]["preserve_targets"] == (profiles_root / "demo",)
    assert task["result"] == {
        "operation": "upgrade",
        "restarted": False,
        "rollback_status": "not_needed",
    }


@pytest.mark.asyncio
async def test_market_upgrade_replaces_owned_data_and_preserves_user_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    package_asset = plugin_dir / "data" / "defaults.json"
    user_data = plugin_dir / "data" / "user.db"
    package_asset.parent.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    package_asset.write_bytes(b"old-package-data")
    user_data.write_bytes(b"user-created-data\x00\xff")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda _plugin_id: _async_false())
    monkeypatch.setattr(
        market_bridge,
        "_download_package",
        lambda _url, _task: _async_value(package_path),
    )
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *_args, **_kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)
    monkeypatch.setattr(
        market_bridge,
        "get_user_installation_package_state_files",
        lambda _plugin_id, **_kwargs: {
            "data/defaults.json": hashlib.sha256(b"old-package-data").hexdigest()
        },
    )
    monkeypatch.setattr(
        market_bridge,
        "collect_package_state_files",
        lambda _path: {
            "demo": {
                "data/defaults.json": hashlib.sha256(b"new-package-data").hexdigest()
            }
        },
    )

    async def install_new(**_kwargs: Any) -> dict[str, object]:
        package_asset.parent.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text("version = '2.0.0'\n", encoding="utf-8")
        package_asset.write_bytes(b"new-package-data")
        return {"unpack": {}, "install": {}}

    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(upload_and_install=install_new),
    )

    await market_bridge._do_upgrade({}, _payload(), {})

    assert package_asset.read_bytes() == b"new-package-data"
    assert user_data.read_bytes() == b"user-created-data\x00\xff"


@pytest.mark.asyncio
async def test_market_upgrade_rolls_back_plugin_profile_with_plugin_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    profile_dir = profiles_root / "demo"
    plugin_dir.mkdir(parents=True)
    profile_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    (profile_dir / "default.toml").write_text("version = 1\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(
        monkeypatch,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda plugin_id: _async_false())
    monkeypatch.setattr(market_bridge, "_download_package", lambda url, task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda path: None)

    async def install_then_fail(**kwargs: Any) -> dict[str, object]:
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir)
        if profile_dir.exists():
            shutil.rmtree(profile_dir)
        plugin_dir.mkdir(parents=True)
        profile_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text("version = '2.0.0'\n", encoding="utf-8")
        (profile_dir / "default.toml").write_text("version = 2\n", encoding="utf-8")
        raise RuntimeError("install failed after promotion")

    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(upload_and_install=install_then_fail),
    )

    with pytest.raises(market_bridge._TaskError, match="install failed after promotion"):
        await market_bridge._do_upgrade({}, _payload(), {})

    assert (plugin_dir / "plugin.toml").read_text(encoding="utf-8") == "version = '1.0.0'\n"
    assert (profile_dir / "default.toml").read_text(encoding="utf-8") == "version = 1\n"


@pytest.mark.asyncio
async def test_market_upgrade_exposes_rollback_while_files_are_being_restored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.application.plugins import upgrade_support

    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda _plugin_id: _async_false())
    monkeypatch.setattr(market_bridge, "_download_package", lambda _url, _task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)
    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(
            upload_and_install=lambda **_kwargs: _async_raise(RuntimeError("install failed")),
        ),
    )

    rollback_started = asyncio.Event()
    allow_rollback = asyncio.Event()
    remove_directory = upgrade_support.remove_directory

    async def pause_during_rollback(path: Path) -> None:
        rollback_started.set()
        await allow_rollback.wait()
        await remove_directory(path)

    monkeypatch.setattr(upgrade_support, "remove_directory", pause_during_rollback)

    task: dict[str, Any] = {}
    operation = asyncio.create_task(market_bridge._do_upgrade(task, _payload(), {}))
    await asyncio.wait_for(rollback_started.wait(), timeout=1)

    assert task["stage"] == "rollback"
    assert task["rollback"]["running"] is True
    assert task["rollback"]["restored"] is False

    allow_rollback.set()
    with pytest.raises(market_bridge._TaskError) as exc_info:
        await operation

    assert exc_info.value.code == "upgrade_rollback_completed"
    assert task["rollback"]["running"] is False
    assert task["rollback"]["restored"] is True


@pytest.mark.asyncio
async def test_market_upgrade_preserves_install_source_error_after_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda _plugin_id: _async_false())
    monkeypatch.setattr(market_bridge, "_download_package", lambda _url, _task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)
    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(
            upload_and_install=lambda **_kwargs: _async_raise(
                market_bridge.InstallSourceError("lock_write_failed", "lock is read-only")
            ),
        ),
    )

    task: dict[str, Any] = {}
    with pytest.raises(market_bridge._TaskError) as exc_info:
        await market_bridge._do_upgrade(task, _payload(), {})

    assert exc_info.value.code == "lock_write_failed"
    assert task["rollback"]["running"] is False
    assert task["rollback"]["restored"] is True
    assert (plugin_dir / "plugin.toml").read_text(encoding="utf-8") == "version = '1.0.0'\n"


@pytest.mark.asyncio
async def test_market_upgrade_preserves_package_state_conflict_after_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    plugin_dir.mkdir(parents=True)
    old_manifest = "version = '1.0.0'\n"
    (plugin_dir / "plugin.toml").write_text(old_manifest, encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda _plugin_id: _async_false())
    monkeypatch.setattr(market_bridge, "_download_package", lambda _url, _task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *_args, **_kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)
    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(
            upload_and_install=lambda **_kwargs: _async_raise(
                market_bridge.PackageStateConflictError("data/defaults.json")
            ),
        ),
    )

    task: dict[str, Any] = {}
    with pytest.raises(market_bridge._TaskError) as exc_info:
        await market_bridge._do_upgrade(task, _payload(), {})

    assert exc_info.value.code == "PLUGIN_PACKAGE_STATE_CONFLICT"
    assert task["rollback"]["restored"] is True
    assert (plugin_dir / "plugin.toml").read_text(encoding="utf-8") == old_manifest


@pytest.mark.asyncio
async def test_market_upgrade_preserves_existing_profile_files_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    profile_dir = profiles_root / "demo"
    plugin_dir.mkdir(parents=True)
    profile_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    (profile_dir / "default.toml").write_text("user_value = true\n", encoding="utf-8")
    (profile_dir / "custom.toml").write_text("custom = true\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(
        monkeypatch,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda plugin_id: _async_false())
    monkeypatch.setattr(market_bridge, "_download_package", lambda url, task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda path: None)

    async def install_new(**kwargs: Any) -> dict[str, object]:
        plugin_dir.mkdir(parents=True)
        profile_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text("version = '2.0.0'\n", encoding="utf-8")
        (profile_dir / "default.toml").write_text("package_value = true\n", encoding="utf-8")
        (profile_dir / "new.toml").write_text("new = true\n", encoding="utf-8")
        return {"operation": "upgrade"}

    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(upload_and_install=install_new),
    )

    await market_bridge._do_upgrade({}, _payload(), {})

    assert (plugin_dir / "plugin.toml").read_text(encoding="utf-8") == "version = '2.0.0'\n"
    assert (profile_dir / "default.toml").read_text(encoding="utf-8") == "user_value = true\n"
    assert (profile_dir / "custom.toml").read_text(encoding="utf-8") == "custom = true\n"
    assert (profile_dir / "new.toml").read_text(encoding="utf-8") == "new = true\n"


@pytest.mark.asyncio
async def test_market_upgrade_uses_package_id_for_profile_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "demo"
    package_id = "demo-package"
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / plugin_id
    profile_dir = profiles_root / package_id
    plugin_dir.mkdir(parents=True)
    profile_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    (profile_dir / "default.toml").write_text("user_value = true\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(
        monkeypatch,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setattr(
        market_bridge,
        "get_install_source_manager",
        lambda: SimpleNamespace(
            find_active_market_entry=lambda _plugin_id: _entry(plugin_id, package_id)
        ),
    )
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda plugin_id: _async_false())
    monkeypatch.setattr(market_bridge, "_download_package", lambda url, task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda path: None)
    monkeypatch.setattr(
        market_bridge,
        "inspect_package",
        lambda path: SimpleNamespace(package_id=package_id),
        raising=False,
    )

    async def install_new(**kwargs: Any) -> dict[str, object]:
        if profile_dir.exists():
            raise FileExistsError(profile_dir)
        plugin_dir.mkdir(parents=True)
        profile_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text("version = '2.0.0'\n", encoding="utf-8")
        (profile_dir / "new.toml").write_text("new = true\n", encoding="utf-8")
        return {"operation": "upgrade"}

    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(upload_and_install=install_new),
    )

    await market_bridge._do_upgrade({}, _payload(plugin_id), {})

    assert (profile_dir / "default.toml").read_text(encoding="utf-8") == "user_value = true\n"
    assert (profile_dir / "new.toml").read_text(encoding="utf-8") == "new = true\n"


@pytest.mark.asyncio
async def test_market_upgrade_rejects_legacy_rename_despite_stale_incoming_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    old_profile = profiles_root / "old-package"
    stale_profile = profiles_root / "new-package"
    plugin_dir.mkdir(parents=True)
    old_profile.mkdir(parents=True)
    stale_profile.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    (old_profile / "default.toml").write_text("user_value = true\n", encoding="utf-8")
    (stale_profile / "default.toml").write_text("stale = true\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    monkeypatch.setattr(
        market_bridge,
        "get_install_source_manager",
        lambda: SimpleNamespace(find_active_market_entry=lambda _plugin_id: _entry("demo", "")),
    )
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda _plugin_id: _async_false())
    monkeypatch.setattr(market_bridge, "_download_package", lambda _url, _task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)
    monkeypatch.setattr(
        market_bridge,
        "inspect_package",
        lambda _path: SimpleNamespace(package_id="new-package"),
    )

    install_called = False

    async def unexpected_install(**_kwargs: Any) -> dict[str, object]:
        nonlocal install_called
        install_called = True
        return {}

    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(upload_and_install=unexpected_install),
    )

    with pytest.raises(market_bridge._TaskError) as exc_info:
        await market_bridge._do_upgrade({}, _payload(), {})

    assert exc_info.value.code == "package_id_change"
    assert "package id changes are not supported" in str(exc_info.value)
    assert install_called is False
    assert (plugin_dir / "plugin.toml").read_text(encoding="utf-8") == "version = '1.0.0'\n"
    assert (old_profile / "default.toml").read_text(encoding="utf-8") == "user_value = true\n"
    assert (stale_profile / "default.toml").read_text(encoding="utf-8") == "stale = true\n"


@pytest.mark.asyncio
async def test_market_upgrade_blocks_package_id_change_and_preserves_old_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    old_profile = profiles_root / "old-package"
    plugin_dir.mkdir(parents=True)
    old_profile.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    (old_profile / "default.toml").write_text("user_value = true\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    monkeypatch.setattr(
        market_bridge,
        "get_install_source_manager",
        lambda: SimpleNamespace(
            find_active_market_entry=lambda _plugin_id: _entry("demo", "old-package")
        ),
    )
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda _plugin_id: _async_false())
    monkeypatch.setattr(market_bridge, "_download_package", lambda _url, _task: _async_value(package_path))
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)
    monkeypatch.setattr(
        market_bridge,
        "inspect_package",
        lambda _path: SimpleNamespace(package_id="new-package"),
    )

    install_called = False

    async def unexpected_install(**_kwargs: Any) -> dict[str, object]:
        nonlocal install_called
        install_called = True
        return {}

    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(upload_and_install=unexpected_install),
    )

    with pytest.raises(market_bridge._TaskError, match="package id changes are not supported"):
        await market_bridge._do_upgrade({}, _payload(), {})

    assert install_called is False
    assert (plugin_dir / "plugin.toml").read_text(encoding="utf-8") == "version = '1.0.0'\n"
    assert (old_profile / "default.toml").read_text(encoding="utf-8") == "user_value = true\n"


@pytest.mark.asyncio
async def test_market_restart_failure_restores_previous_install_source_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    profile_dir = profiles_root / "demo"
    plugin_dir.mkdir(parents=True)
    profile_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    (profile_dir / "default.toml").write_text("user_value = true\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")

    old_entry = _entry("demo", "demo")

    class FakeManager:
        def __init__(self) -> None:
            self.current = old_entry
            self.restore_calls: list[SimpleNamespace] = []

        def find_active_market_entry(self, _plugin_id: str) -> SimpleNamespace:
            return self.current

        def restore_entry_for_rollback(self, entry: SimpleNamespace) -> None:
            self.restore_calls.append(entry)
            self.current = entry

    manager = FakeManager()
    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    monkeypatch.setattr(market_bridge, "get_install_source_manager", lambda: manager)
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda _plugin_id: _async_true())
    monkeypatch.setattr(
        market_bridge,
        "stop_plugin_for_upgrade",
        lambda _plugin_id: _async_none(),
    )
    monkeypatch.setattr(
        market_bridge,
        "_download_package",
        lambda _url, _task: _async_value(package_path),
    )
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)

    async def install_new(**_kwargs: Any) -> dict[str, object]:
        plugin_dir.mkdir(parents=True)
        profile_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text("version = '2.0.0'\n", encoding="utf-8")
        (profile_dir / "generated.toml").write_text("replacement = true\n", encoding="utf-8")
        manager.current = _entry("demo", "demo")
        manager.current.source_detail = SimpleNamespace(version="2.0.0")
        return {"operation": "upgrade"}

    start_calls = 0

    async def fail_new_start(_plugin_id: str, *, strict: bool) -> bool:
        nonlocal start_calls
        start_calls += 1
        if start_calls == 1:
            raise RuntimeError("replacement start failed")
        return True

    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(upload_and_install=install_new),
    )
    monkeypatch.setattr(market_bridge, "start_plugin_after_upgrade", fail_new_start)

    with pytest.raises(market_bridge._TaskError) as exc_info:
        await market_bridge._do_upgrade({}, _payload(), {})

    assert exc_info.value.code == "upgrade_rollback_completed"
    assert manager.restore_calls == [old_entry]
    assert manager.current is old_entry
    assert (plugin_dir / "plugin.toml").read_text(encoding="utf-8") == "version = '1.0.0'\n"
    assert (profile_dir / "default.toml").read_text(encoding="utf-8") == "user_value = true\n"
    assert not (profile_dir / "generated.toml").exists()


@pytest.mark.asyncio
async def test_market_committed_finish_failure_keeps_new_files_and_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.application.plugins import upgrade_support

    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")
    old_entry = _entry("demo", "demo")

    class FakeManager:
        def __init__(self) -> None:
            self.current = old_entry
            self.restore_calls: list[SimpleNamespace] = []

        def find_active_market_entry(self, _plugin_id: str) -> SimpleNamespace:
            return self.current

        def restore_entry_for_rollback(self, entry: SimpleNamespace) -> None:
            self.restore_calls.append(entry)
            self.current = entry

    manager = FakeManager()
    _configure_paths(monkeypatch, plugins_root=plugins_root, profiles_root=profiles_root)
    monkeypatch.setattr(market_bridge, "get_install_source_manager", lambda: manager)
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda _plugin_id: _async_false())
    monkeypatch.setattr(
        market_bridge,
        "_download_package",
        lambda _url, _task: _async_value(package_path),
    )
    monkeypatch.setattr(
        market_bridge,
        "_verify_sha256_file",
        lambda *_args, **_kwargs: "passed",
    )
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)

    async def install_new(**_kwargs: Any) -> dict[str, object]:
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text(
            "version = '2.0.0'\n",
            encoding="utf-8",
        )
        manager.current = _entry("demo", "demo")
        manager.current.source_detail = SimpleNamespace(version="2.0.0")
        return {"operation": "upgrade"}

    def fail_finish(_journal: object) -> None:
        raise OSError("simulated committed journal finish failure")

    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(upload_and_install=install_new),
    )
    monkeypatch.setattr(upgrade_support._ReplacementJournal, "finish", fail_finish)

    task: dict[str, Any] = {}
    with pytest.raises(market_bridge._TaskError) as exc_info:
        await market_bridge._do_upgrade(task, _payload(), {})

    assert exc_info.value.code == (
        "PLUGIN_REPLACEMENT_COMMITTED_CLEANUP_INCOMPLETE"
    )
    assert task["rollback"] == {
        "prepared": True,
        "restored": False,
        "committed": True,
    }
    assert manager.restore_calls == []
    assert manager.current.source_detail.version == "2.0.0"
    assert (plugin_dir / "plugin.toml").read_text(encoding="utf-8") == (
        "version = '2.0.0'\n"
    )


@pytest.mark.asyncio
async def test_market_upgrade_direct_cancellation_restores_files_and_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.application.plugins.mutation_guard import plugin_mutation_guard
    from plugin.server.application.plugins import upgrade_support

    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    plugin_dir.mkdir(parents=True)
    old_manifest = "version = '1.0.0'\n"
    (plugin_dir / "plugin.toml").write_text(old_manifest, encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")
    old_entry = _entry("demo", "demo")

    class FakeManager:
        def __init__(self) -> None:
            self.restore_calls = 0

        def find_active_market_entry(self, _plugin_id: str) -> SimpleNamespace:
            return old_entry

        def restore_entry_for_rollback(self, entry: SimpleNamespace) -> None:
            assert entry is old_entry
            self.restore_calls += 1

    manager = FakeManager()
    _configure_paths(
        monkeypatch,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setattr(market_bridge, "get_install_source_manager", lambda: manager)
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda _plugin_id: _async_false())
    monkeypatch.setattr(
        market_bridge,
        "_download_package",
        lambda _url, _task: _async_value(package_path),
    )
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)

    install_started = asyncio.Event()
    hold_install = asyncio.Event()

    async def blocked_install(**_kwargs: Any) -> dict[str, object]:
        install_started.set()
        await hold_install.wait()
        raise AssertionError("canceled install must not resume")

    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(upload_and_install=blocked_install),
    )

    rollback_started = asyncio.Event()
    allow_rollback = asyncio.Event()
    original_rollback_targets = upgrade_support._rollback_targets

    async def blocked_rollback_targets(**kwargs: Any) -> bool:
        rollback_started.set()
        await allow_rollback.wait()
        return await original_rollback_targets(**kwargs)

    monkeypatch.setattr(
        upgrade_support,
        "_rollback_targets",
        blocked_rollback_targets,
    )

    operation = asyncio.create_task(market_bridge._do_upgrade({}, _payload(), {}))
    await install_started.wait()
    backup_root = plugins_root / ".upgrade-backups"
    backup_dirs = list(backup_root.glob("demo.bak.*"))
    assert not plugin_dir.exists()
    assert len(backup_dirs) == 1

    operation.cancel()
    await rollback_started.wait()
    operation.cancel()
    allow_rollback.set()
    with pytest.raises(asyncio.CancelledError):
        await operation

    assert (plugin_dir / "plugin.toml").read_text(encoding="utf-8") == old_manifest
    assert not list(backup_root.glob("demo.bak.*"))
    assert manager.restore_calls == 1
    async with plugin_mutation_guard():
        pass


@pytest.mark.asyncio
async def test_market_upgrade_cancellation_during_inflight_backup_rename_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.application.plugins import upgrade_support
    from plugin.server.application.plugins.mutation_guard import plugin_mutation_guard

    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    plugin_dir.mkdir(parents=True)
    old_manifest = "version = '1.0.0'\n"
    (plugin_dir / "plugin.toml").write_text(old_manifest, encoding="utf-8")
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")
    old_entry = _entry("demo", "demo")

    class FakeManager:
        def __init__(self) -> None:
            self.restore_calls = 0

        def find_active_market_entry(self, _plugin_id: str) -> SimpleNamespace:
            return old_entry

        def restore_entry_for_rollback(self, entry: SimpleNamespace) -> None:
            assert entry is old_entry
            self.restore_calls += 1

    manager = FakeManager()
    _configure_paths(
        monkeypatch,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setattr(market_bridge, "get_install_source_manager", lambda: manager)
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda _plugin_id: _async_false())
    monkeypatch.setattr(
        market_bridge,
        "_download_package",
        lambda _url, _task: _async_value(package_path),
    )
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)

    install_called = False

    async def unexpected_install(**_kwargs: Any) -> dict[str, object]:
        nonlocal install_called
        install_called = True
        raise AssertionError("install must not begin after backup cancellation")

    monkeypatch.setattr(
        market_bridge,
        "_cli_service",
        SimpleNamespace(upload_and_install=unexpected_install),
    )

    loop = asyncio.get_running_loop()
    rename_started = asyncio.Event()
    allow_rename = threading.Event()
    original_rename = Path.rename

    def blocked_rename(path: Path, target: Path) -> Path:
        if path == plugin_dir:
            loop.call_soon_threadsafe(rename_started.set)
            assert allow_rename.wait(timeout=10)
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", blocked_rename)

    cancellation_wait_started = asyncio.Event()
    cancellation_safe_calls = 0
    original_cancellation_safe = upgrade_support.await_cancellation_safe

    async def observed_cancellation_safe(operation: Any) -> Any:
        nonlocal cancellation_safe_calls
        cancellation_safe_calls += 1
        if cancellation_safe_calls == 1:
            cancellation_wait_started.set()
        return await original_cancellation_safe(operation)

    monkeypatch.setattr(
        upgrade_support,
        "await_cancellation_safe",
        observed_cancellation_safe,
    )

    operation = asyncio.create_task(market_bridge._do_upgrade({}, _payload(), {}))
    await rename_started.wait()
    operation.cancel()
    assert operation.cancelling() == 1
    await cancellation_wait_started.wait()
    operation.cancel()
    allow_rename.set()

    with pytest.raises(asyncio.CancelledError):
        await operation

    backup_root = plugins_root / ".upgrade-backups"
    assert (plugin_dir / "plugin.toml").read_text(encoding="utf-8") == old_manifest
    assert not list(backup_root.glob("demo.bak.*"))
    assert not install_called
    assert manager.restore_calls == 1
    async with plugin_mutation_guard():
        pass


@pytest.mark.asyncio
async def test_market_backup_failure_reports_incomplete_when_old_plugin_cannot_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_root = tmp_path / "plugins"
    profiles_root = tmp_path / "profiles"
    plugin_dir = plugins_root / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text("version = '1.0.0'\n", encoding="utf-8")
    _configure_paths(
        monkeypatch,
        plugins_root=plugins_root,
        profiles_root=profiles_root,
    )
    monkeypatch.setattr(market_bridge, "plugin_is_running", lambda plugin_id: _async_true())
    monkeypatch.setattr(market_bridge, "stop_plugin_for_upgrade", lambda plugin_id: _async_none())
    monkeypatch.setattr(
        market_bridge,
        "start_plugin_after_upgrade",
        lambda plugin_id, strict: _async_raise(RuntimeError("old plugin restart failed")),
    )
    package_path = tmp_path / "demo.neko-plugin"
    package_path.write_bytes(b"package")
    monkeypatch.setattr(
        market_bridge,
        "_download_package",
        lambda _url, _task: _async_value(package_path),
    )
    monkeypatch.setattr(market_bridge, "_verify_sha256_file", lambda *args, **kwargs: "passed")
    monkeypatch.setattr(market_bridge, "_cleanup_download_file", lambda _path: None)
    monkeypatch.setattr(market_bridge.os, "rename", lambda source, target: _raise_permission_error())

    with pytest.raises(market_bridge._TaskError) as exc_info:
        await market_bridge._do_upgrade({}, _payload(), {})

    assert exc_info.value.code == "upgrade_rollback_incomplete"


async def _async_none() -> None:
    return None


async def _async_true() -> bool:
    return True


async def _async_false() -> bool:
    return False


async def _async_value(value: Any) -> Any:
    return value


async def _async_raise(error: Exception) -> None:
    raise error


def _raise_permission_error() -> None:
    raise PermissionError("backup denied")
