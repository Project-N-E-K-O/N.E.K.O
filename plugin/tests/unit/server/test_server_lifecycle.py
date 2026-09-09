from __future__ import annotations

import asyncio
import copy

import pytest

from plugin.server import lifecycle as module
from plugin.server.application.plugins.operation_lock import plugin_operation_lock


pytestmark = pytest.mark.plugin_unit


@pytest.mark.asyncio
async def test_ensure_plugin_messaging_started_initializes_response_map_and_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _State:
        @property
        def plugin_response_map(self) -> dict[str, object]:
            calls.append("response_map")
            return {}

    async def _start_router() -> None:
        calls.append("router_start")

    async def _start_delivery_path() -> bool:
        calls.append("delivery_path")
        return True

    monkeypatch.setattr(module, "state", _State())
    monkeypatch.setattr(module.plugin_router, "start", _start_router)
    monkeypatch.setattr(module._service, "ensure_delivery_path_started", _start_delivery_path)

    ensure = getattr(module, "ensure_plugin_messaging_started", None)
    assert callable(ensure)

    # The result is the contract: the caller starts the plugin either way but has
    # to be able to say it is doing so over a dead path.
    assert await ensure() is True

    # The delivery path is not optional here. This entry point is what
    # ``POST /plugin/{id}/start`` calls, and a plugin started through it pushes
    # messages immediately -- the router carries entry triggers and @llm_tool
    # calls, NOT push_message. Starting only the router produced a plugin whose
    # tool calls worked while every alert, including the character's own death,
    # went nowhere with nothing logged above DEBUG (2026-09-10).
    assert calls == ["response_map", "router_start", "delivery_path"]


@pytest.mark.asyncio
async def test_ensure_plugin_messaging_started_starts_router_when_response_map_init_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _State:
        @property
        def plugin_response_map(self) -> dict[str, object]:
            calls.append("response_map")
            raise RuntimeError("response map unavailable")

    async def _start_router() -> None:
        calls.append("router_start")

    warnings: list[tuple[str, str, str]] = []

    class _Logger:
        def warning(self, message: str, err_type: str, err: str) -> None:
            warnings.append((message, err_type, err))

        def debug(self, *_args: object, **_kwargs: object) -> None:
            return None

    async def _start_delivery_path() -> bool:
        calls.append("delivery_path")
        return False

    monkeypatch.setattr(module, "state", _State())
    monkeypatch.setattr(module.plugin_router, "start", _start_router)
    monkeypatch.setattr(module._service, "ensure_delivery_path_started", _start_delivery_path)
    monkeypatch.setattr(module, "logger", _Logger())

    # A dead delivery path is reported, not swallowed -- the caller decides what
    # to do about it and has to be able to name the plugin in its warning.
    assert await module.ensure_plugin_messaging_started() is False

    # A response-map failure must not cost the delivery path either.
    assert calls == ["response_map", "router_start", "delivery_path"]
    assert warnings == [
        (
            "failed to initialize plugin response map early: err_type={}, err={}",
            "RuntimeError",
            "response map unavailable",
        )
    ]


@pytest.mark.asyncio
async def test_startup_reconciles_existing_install_source_after_migration_before_registry_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_backup = copy.deepcopy(module.state.plugins)
    hosts_backup = dict(module.state.plugin_hosts)
    handlers_backup = dict(module.state.event_handlers)
    cache_backup = copy.deepcopy(module.state._snapshot_cache)
    calls: list[tuple[str, str]] = []

    async def _noop_async(*args, **kwargs):
        return None

    try:
        service = module.ServerLifecycleService()

        monkeypatch.setattr(module.ServerLifecycleService, "_clear_runtime_state", staticmethod(lambda: None))
        monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)
        async def _plane_ok(*args, **kwargs) -> bool:
            # ``_start_message_plane`` reports usability now; a ``None`` stub
            # would read as "the plane failed" and quietly change what this
            # test's startup() exercises.
            return True

        monkeypatch.setattr(module.plugin_router, "start", _noop_async)
        monkeypatch.setattr(service, "_start_message_plane", _plane_ok)
        monkeypatch.setattr(module.bus_subscription_manager, "start", _noop_async)
        monkeypatch.setattr(module.status_manager, "start_status_consumer", _noop_async)
        monkeypatch.setattr(module.metrics_collector, "start", _noop_async)
        monkeypatch.setattr(module, "start_bridge", lambda: None)
        monkeypatch.setattr(module, "start_proactive_bridge", lambda: None)

        async def _migrate_layout():
            calls.append(("layout", "migrate"))
            return type(
                "MigrationResult",
                (),
                {"migrated": (), "blocked": ()},
            )()

        monkeypatch.setattr(module, "migrate_legacy_plugin_layout", _migrate_layout)

        install_source_manager = object()

        class _StartupReconciler:
            def __init__(self, manager: object) -> None:
                assert manager is install_source_manager

            async def run(self) -> None:
                calls.append(("install_source", "reconcile"))

        monkeypatch.setattr(module, "get_install_source_manager", lambda: install_source_manager)
        monkeypatch.setattr(module, "StartupReconciler", _StartupReconciler)

        async def _retry_deferred_profile_cleanup() -> int:
            calls.append(("profile_cleanup", "retry"))
            return 0

        monkeypatch.setattr(
            service._plugin_lifecycle_service,
            "retry_deferred_profile_cleanup",
            _retry_deferred_profile_cleanup,
        )

        async def _refresh_registry() -> dict[str, object]:
            calls.append(("registry", "refresh"))
            with module.state.acquire_plugins_write_lock():
                module.state.plugins.clear()
                module.state.plugins.update(
                    {
                        "auto_plugin": {
                            "id": "auto_plugin",
                            "type": "plugin",
                            "runtime_enabled": True,
                            "runtime_auto_start": True,
                        },
                        "manual_plugin": {
                            "id": "manual_plugin",
                            "type": "plugin",
                            "runtime_enabled": True,
                            "runtime_auto_start": False,
                        },
                        "failed_plugin": {
                            "id": "failed_plugin",
                            "type": "plugin",
                            "runtime_enabled": True,
                            "runtime_auto_start": True,
                            "runtime_load_state": "failed",
                        },
                    }
                )
            return {"success": True, "added": ["auto_plugin"], "updated": [], "removed": [], "failed": []}

        async def _start_plugin(plugin_id: str, restore_state: bool = False, *, refresh_registry: bool = True) -> dict[str, object]:
            _ = restore_state
            calls.append(("start", f"{plugin_id}:{refresh_registry}"))
            return {"success": True, "plugin_id": plugin_id}

        monkeypatch.setattr(service._plugin_registry_service, "refresh_registry", _refresh_registry)
        monkeypatch.setattr(service._plugin_lifecycle_service, "start_plugin", _start_plugin)

        await service.startup()

        assert calls == [
            ("layout", "migrate"),
            ("install_source", "reconcile"),
            ("profile_cleanup", "retry"),
            ("registry", "refresh"),
            ("start", "auto_plugin:False"),
        ]
    finally:
        with module.state.acquire_plugins_write_lock():
            module.state.plugins.clear()
            module.state.plugins.update(plugins_backup)
        with module.state.acquire_plugin_hosts_write_lock():
            module.state.plugin_hosts.clear()
            module.state.plugin_hosts.update(hosts_backup)
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()
            module.state.event_handlers.update(handlers_backup)
        with module.state._snapshot_cache_lock:
            module.state._snapshot_cache = cache_backup


@pytest.mark.asyncio
async def test_layout_migration_and_reconcile_share_plugin_operation_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = module.ServerLifecycleService()
    migration_started = asyncio.Event()

    async def migrate_layout():
        migration_started.set()
        return type("MigrationResult", (), {"migrated": (), "blocked": ()})()

    monkeypatch.setattr(module, "migrate_legacy_plugin_layout", migrate_layout)
    monkeypatch.setattr(module, "get_install_source_manager", lambda: None)

    async with plugin_operation_lock.hold():
        task = asyncio.create_task(
            service._migrate_layout_and_reconcile_install_sources()
        )
        await asyncio.sleep(0)
        assert not migration_started.is_set()

    await task
    assert migration_started.is_set()


@pytest.mark.asyncio
async def test_ensure_delivery_path_started_is_idempotent_under_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both entry points call this; it must bring the plane up exactly once.

    The startup lifecycle and ``POST /plugin/{id}/start`` can race -- that race
    is the normal case, not an edge one, because the manual start is what a user
    clicks while the server is still coming up.
    """
    service = module.ServerLifecycleService()
    started: list[str] = []

    async def _start_plane() -> bool:
        started.append("plane")
        await asyncio.sleep(0)  # a real await, so a second caller can interleave
        return True

    monkeypatch.setattr(service, "_start_message_plane", _start_plane)
    monkeypatch.setattr(module, "refresh_ingest_endpoint", lambda: started.append("ingest_ep"))
    monkeypatch.setattr(module, "start_bridge", lambda: started.append("bridge"))
    monkeypatch.setattr(module, "start_proactive_bridge", lambda: started.append("proactive"))
    monkeypatch.setattr(module, "wait_for_proactive_subscriber", lambda _timeout: True)

    await asyncio.gather(
        service.ensure_delivery_path_started(),
        service.ensure_delivery_path_started(),
    )
    await service.ensure_delivery_path_started()

    assert started == ["plane", "ingest_ep", "bridge", "proactive"]

    # A shutdown re-arms it: a restart in the same process must get a live plane
    # back, so the latch cannot survive teardown.
    service._delivery_path_started = False
    await service.ensure_delivery_path_started()
    assert started.count("plane") == 2


@pytest.mark.asyncio
async def test_a_failed_delivery_path_is_not_latched_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed attempt must stay retryable, or the recovery entry point is dead.

    Every step swallows its own failure so one broken component cannot abort
    server startup. Latching that outcome would mean a later manual plugin start
    skips the retry and the plugin stays mute until the process restarts --
    which is precisely the failure this whole mechanism exists to end.
    """
    service = module.ServerLifecycleService()
    attempts: list[str] = []
    fail = True

    async def _start_plane() -> bool:
        attempts.append("plane")
        if fail:
            raise RuntimeError("port busy")
        return True

    monkeypatch.setattr(service, "_start_message_plane", _start_plane)
    monkeypatch.setattr(module, "refresh_ingest_endpoint", lambda: None)
    monkeypatch.setattr(module, "start_bridge", lambda: None)
    monkeypatch.setattr(module, "start_proactive_bridge", lambda: None)
    monkeypatch.setattr(module, "wait_for_proactive_subscriber", lambda _t: True)

    await service.ensure_delivery_path_started()
    assert service._delivery_path_started is False
    assert attempts == ["plane"]

    # Second caller retries rather than short-circuiting on a failed latch.
    await service.ensure_delivery_path_started()
    assert attempts == ["plane", "plane"]

    # Once it succeeds it latches and stops retrying.
    fail = False
    await service.ensure_delivery_path_started()
    assert service._delivery_path_started is True
    assert attempts == ["plane", "plane", "plane"]
    await service.ensure_delivery_path_started()
    assert attempts == ["plane", "plane", "plane"]


@pytest.mark.asyncio
async def test_a_failed_bridge_also_leaves_the_path_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not just the plane: a bridge that failed to start is equally undelivered."""
    service = module.ServerLifecycleService()

    async def _noop() -> bool:
        return True

    monkeypatch.setattr(service, "_start_message_plane", _noop)
    monkeypatch.setattr(module, "refresh_ingest_endpoint", lambda: None)
    monkeypatch.setattr(module, "start_bridge", lambda: None)
    monkeypatch.setattr(module, "wait_for_proactive_subscriber", lambda _t: True)

    def _boom() -> None:
        raise OSError("no socket")

    monkeypatch.setattr(module, "start_proactive_bridge", _boom)
    await service.ensure_delivery_path_started()
    assert service._delivery_path_started is False

    monkeypatch.setattr(module, "start_proactive_bridge", lambda: None)
    await service.ensure_delivery_path_started()
    assert service._delivery_path_started is True


@pytest.mark.asyncio
async def test_partial_retry_reuses_the_running_plane_instead_of_building_a_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry after a partial failure must not stand up a second plane.

    When the plane started but a later step failed, the path stays unlatched and
    a later start retries the whole sequence. Rebuilding the runner there would
    strand the first one's threads and sockets -- and the first still holds the
    configured ports, so the replacement's fallback picks different ones and the
    bridge gets refreshed onto a plane that is not the one running.
    """
    service = module.ServerLifecycleService()
    built: list[object] = []

    class _Runner:
        def start(self) -> None:
            return None

        async def health_check_async(self, *, timeout_s: float = 1.0) -> bool:
            return True

    def _build(*, auth_token: str) -> object:
        runner = _Runner()
        built.append(runner)
        return runner

    monkeypatch.setattr(module, "build_message_plane_runner", _build)
    monkeypatch.setattr(module, "ingest_auth_token", lambda: "token")
    monkeypatch.setattr(module, "refresh_ingest_endpoint", lambda: None)
    monkeypatch.setattr(module, "start_bridge", lambda: None)
    monkeypatch.setattr(module, "wait_for_proactive_subscriber", lambda _t: True)

    def _bridge_boom() -> None:
        raise OSError("no socket")

    monkeypatch.setattr(module, "start_proactive_bridge", _bridge_boom)
    await service.ensure_delivery_path_started()
    assert service._delivery_path_started is False
    assert len(built) == 1
    first = service._message_plane_runner
    assert first is built[0]

    # Retry: the failed bridge re-runs, the healthy plane is reused as-is.
    monkeypatch.setattr(module, "start_proactive_bridge", lambda: None)
    await service.ensure_delivery_path_started()
    assert service._delivery_path_started is True
    assert len(built) == 1, "a second MessagePlaneRunner was built on retry"
    assert service._message_plane_runner is first


@pytest.mark.asyncio
async def test_reuse_reprobes_health_and_refuses_an_unhealthy_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-null runner only means ``start()`` did not raise.

    The fresh-start path deliberately treats a false probe as non-fatal ("it may
    still be starting") and keeps the runner assigned. Reusing that unverified
    runner would let the bridges come up against a plane that never arrived and
    latch the path as ready -- push_message answering submitted=True with
    nothing behind it, the exact failure this PR exists to end.
    """
    service = module.ServerLifecycleService()
    built: list[object] = []
    healthy = False

    class _Runner:
        def start(self) -> None:
            return None

        async def health_check_async(self, *, timeout_s: float = 1.0) -> bool:
            return healthy

    def _build(*, auth_token: str) -> object:
        runner = _Runner()
        built.append(runner)
        return runner

    monkeypatch.setattr(module, "build_message_plane_runner", _build)
    monkeypatch.setattr(module, "ingest_auth_token", lambda: "token")
    monkeypatch.setattr(module, "refresh_ingest_endpoint", lambda: None)
    monkeypatch.setattr(module, "start_bridge", lambda: None)
    monkeypatch.setattr(module, "start_proactive_bridge", lambda: None)
    monkeypatch.setattr(module, "wait_for_proactive_subscriber", lambda _t: True)

    # Fresh start with a false probe: non-fatal, runner kept, path latches as
    # before -- that behaviour predates this PR and is deliberate.
    await service.ensure_delivery_path_started()
    assert service._delivery_path_started is True
    assert len(built) == 1

    # Now force the reuse branch with the plane still unhealthy.
    service._delivery_path_started = False
    await service.ensure_delivery_path_started()
    assert service._delivery_path_started is False, "an unverified plane was latched"
    assert len(built) == 1, "the unhealthy plane was rebuilt, splitting its ports"

    # It comes up late: the next retry re-probes, sees it, and latches.
    healthy = True
    await service.ensure_delivery_path_started()
    assert service._delivery_path_started is True
    assert len(built) == 1


@pytest.mark.asyncio
async def test_a_plane_that_failed_to_start_is_rebuilt_on_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reuse above must not mask a plane that never came up."""
    service = module.ServerLifecycleService()
    built: list[object] = []
    fail = True

    class _Runner:
        def start(self) -> None:
            if fail:
                raise OSError("port busy")

        async def health_check_async(self, *, timeout_s: float = 1.0) -> bool:
            return True

    def _build(*, auth_token: str) -> object:
        runner = _Runner()
        built.append(runner)
        return runner

    monkeypatch.setattr(module, "build_message_plane_runner", _build)
    monkeypatch.setattr(module, "ingest_auth_token", lambda: "token")
    monkeypatch.setattr(module, "refresh_ingest_endpoint", lambda: None)
    monkeypatch.setattr(module, "start_bridge", lambda: None)
    monkeypatch.setattr(module, "start_proactive_bridge", lambda: None)
    monkeypatch.setattr(module, "wait_for_proactive_subscriber", lambda _t: True)

    await service.ensure_delivery_path_started()
    assert service._delivery_path_started is False
    assert service._message_plane_runner is None

    fail = False
    await service.ensure_delivery_path_started()
    assert service._delivery_path_started is True
    assert len(built) == 2


@pytest.mark.asyncio
async def test_shutdown_closes_the_gate_so_a_late_start_cannot_orphan_a_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manual plugin start racing teardown must not stand a new plane up.

    ``shutdown`` clears the latch, so without a gate the very next
    ``ensure_delivery_path_started`` would take the lock, see a cleared flag, and
    build a plane that ``_shutdown_internal`` has already walked past -- orphan
    threads and sockets, and a ``True`` flag describing a plane nobody owns.
    """
    service = module.ServerLifecycleService()
    starts: list[str] = []

    async def _start_plane() -> bool:
        starts.append("plane")
        return True

    monkeypatch.setattr(service, "_start_message_plane", _start_plane)
    monkeypatch.setattr(module, "refresh_ingest_endpoint", lambda: None)
    monkeypatch.setattr(module, "start_bridge", lambda: None)
    monkeypatch.setattr(module, "start_proactive_bridge", lambda: None)
    monkeypatch.setattr(module, "wait_for_proactive_subscriber", lambda _t: True)

    # Simulate what shutdown() does to the gate, without driving real teardown.
    async with service._delivery_path_lock:
        service._delivery_path_shutting_down = True
        service._delivery_path_started = False

    await service.ensure_delivery_path_started()
    assert starts == []
    assert service._delivery_path_started is False

    # startup() reopens it: the same service instance is reused across a restart
    # in the same process, and a gate latched closed would mute the new run.
    async with service._delivery_path_lock:
        service._delivery_path_shutting_down = False
    await service.ensure_delivery_path_started()
    assert starts == ["plane"]
    assert service._delivery_path_started is True
