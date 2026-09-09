from __future__ import annotations

import asyncio
import copy
import threading

import pytest

from plugin.server import lifecycle as module
from plugin.server.application.plugins.operation_lock import plugin_operation_lock


pytestmark = pytest.mark.plugin_unit

# Stands in for the runner the real ``_start_message_plane`` assigns. Stubs
# must set it: ``_start_delivery_path_locked`` decides whether to bind the
# bridges by asking whether a runner exists, so a stub that reports success
# while leaving none describes a state production never reaches -- and would
# quietly exercise the retirement path instead of the one under test.
_PLANE_STUB = object()


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
        service._message_plane_runner = _PLANE_STUB
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
        service._message_plane_runner = _PLANE_STUB
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
        service._message_plane_runner = _PLANE_STUB
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

    bridges: list[str] = []

    monkeypatch.setattr(module, "build_message_plane_runner", _build)
    monkeypatch.setattr(module, "ingest_auth_token", lambda: "token")
    monkeypatch.setattr(module, "refresh_ingest_endpoint", lambda: None)
    monkeypatch.setattr(module, "start_bridge", lambda: bridges.append("plane_bridge"))
    monkeypatch.setattr(module, "start_proactive_bridge", lambda: bridges.append("proactive"))
    monkeypatch.setattr(module, "wait_for_proactive_subscriber", lambda _t: True)

    # Fresh start with a false probe: the runner is kept and the bridges still
    # come up, but the path is NOT latched. Latching here would be permanent --
    # nothing re-probes a path already marked started, so a plane that never
    # arrived would answer submitted=True for the life of the process.
    assert await service.ensure_delivery_path_started() is False

    # Both bridges started anyway. This is the factual basis for the wording of
    # the "not verified" warning: their sockets reattach on their own once the
    # plane binds, so a merely-slow plane heals with no further entry. An early
    # return on a failed probe would make that warning a lie.
    assert bridges == ["plane_bridge", "proactive"]
    assert service._delivery_path_started is False, "an unverified plane was latched"
    assert service._message_plane_runner is not None, "the runner was discarded"
    assert len(built) == 1

    # Still unhealthy on the next entry: reuse branch re-probes, still refuses,
    # and does not rebuild (rebuilding would hand its ports to a second runner).
    assert await service.ensure_delivery_path_started() is False
    assert service._delivery_path_started is False
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
async def test_startup_starts_the_delivery_path_on_itself_not_the_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``startup()`` must not reach the module singleton's delivery path.

    It used to call ``ensure_plugin_messaging_started()``, which starts the path
    on ``_service``, and then start it again on ``self``. In production those are
    the same object and the second call latches out. On any other instance it
    builds TWO message planes: the first holds the configured ports, the second
    falls back to different ones, and the caller's ``shutdown()`` owns neither.
    """
    service = module.ServerLifecycleService()
    assert service is not module._service

    touched: list[str] = []

    async def _singleton_path() -> bool:
        touched.append("singleton")
        return True

    async def _own_path() -> bool:
        touched.append("self")
        return True

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(module._service, "ensure_delivery_path_started", _singleton_path)
    monkeypatch.setattr(service, "ensure_delivery_path_started", _own_path)
    monkeypatch.setattr(module.ServerLifecycleService, "_clear_runtime_state", staticmethod(lambda: None))
    monkeypatch.setattr(module, "emit_lifecycle_event", lambda event: None)
    monkeypatch.setattr(module.plugin_router, "start", _noop)
    monkeypatch.setattr(module, "migrate_legacy_plugin_layout", _noop)
    monkeypatch.setattr(module.bus_subscription_manager, "start", _noop)
    monkeypatch.setattr(module.status_manager, "start_status_consumer", _noop)
    monkeypatch.setattr(module.metrics_collector, "start", _noop)
    monkeypatch.setattr(service, "_migrate_layout_and_reconcile_install_sources", _noop)
    monkeypatch.setattr(service, "_refresh_registry_and_start_autostart_plugins", _noop)
    monkeypatch.setattr(
        service._plugin_lifecycle_service, "retry_deferred_profile_cleanup", _noop
    )

    await service.startup()

    assert touched == ["self"], "startup() started the delivery path on the singleton"


@pytest.mark.asyncio
async def test_failure_report_names_the_stage_that_did_not_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two failure kinds have opposite prognoses, so they cannot share wording.

    A plane that failed its probe self-heals: both bridges are up either way and
    their sockets reattach once it binds. A bridge that raised is simply not
    running and nothing reattaches it. Collapsing both into one bool forced a
    single sentence that was false about whichever case it was not -- first
    calling a slow plane a delivery outage, then calling a dead bridge a slow
    plane. The stage list is what lets the caller say the true one.
    """
    service = module.ServerLifecycleService()

    async def _plane_ok() -> bool:
        service._message_plane_runner = _PLANE_STUB
        return True

    async def _plane_unhealthy() -> bool:
        service._message_plane_runner = _PLANE_STUB
        return False

    monkeypatch.setattr(module, "refresh_ingest_endpoint", lambda: None)
    monkeypatch.setattr(module, "start_bridge", lambda: None)
    monkeypatch.setattr(module, "start_proactive_bridge", lambda: None)
    monkeypatch.setattr(module, "wait_for_proactive_subscriber", lambda _t: True)

    # Everything up.
    monkeypatch.setattr(service, "_start_message_plane", _plane_ok)
    assert await service._start_delivery_path_locked() == []

    # Plane only -- the self-healing case.
    monkeypatch.setattr(service, "_start_message_plane", _plane_unhealthy)
    assert await service._start_delivery_path_locked() == ["message_plane"]

    # A bridge that raised is named, and is NOT reported as the plane case.
    monkeypatch.setattr(service, "_start_message_plane", _plane_ok)

    def _boom() -> None:
        raise OSError("no socket")

    monkeypatch.setattr(module, "start_proactive_bridge", _boom)
    failed = await service._start_delivery_path_locked()
    assert failed == ["proactive_bridge"]
    assert failed != ["message_plane"], "a dead bridge would be described as a slow plane"

    # Both, in the order they are attempted.
    monkeypatch.setattr(service, "_start_message_plane", _plane_unhealthy)
    assert await service._start_delivery_path_locked() == [
        "message_plane",
        "proactive_bridge",
    ]


def test_delivery_path_lock_hands_off_across_event_loops() -> None:
    """The two callers really do run on different loops in different threads.

    ``startup()`` is awaited from the agent's loop; ``POST /plugin/{id}/start``
    runs on the embedded plugin server's own loop in the ``plugin-server``
    thread. An ``asyncio.Lock`` binds its waiter future to whichever loop first
    contends, and a release from the other loop does not wake it -- so the very
    race the lock exists for is the one that would break it.

    Deliberately NOT an asyncio test: a single-loop test cannot tell the two lock
    types apart, which is why nothing caught this before review did.
    """
    service = module.ServerLifecycleService()
    lock = service._delivery_path_lock

    b_may_start = threading.Event()
    b_acquired = threading.Event()
    b_error: list[BaseException] = []

    def _run_b() -> None:
        async def _main() -> None:
            b_may_start.wait(5)
            async with module._held(lock):
                b_acquired.set()

        try:
            asyncio.run(_main())
        except BaseException as exc:  # noqa: BLE001 - surfaced via the list below
            b_error.append(exc)

    thread_b = threading.Thread(target=_run_b, name="lock-loop-b", daemon=True)

    async def _run_a() -> None:
        async with module._held(lock):
            thread_b.start()
            b_may_start.set()
            await asyncio.sleep(0.15)
            assert not b_acquired.is_set(), "the other loop got in while it was held"
        # Released from loop A. Loop B must be woken on ITS own loop.
        await asyncio.to_thread(b_acquired.wait, 5)

    asyncio.run(_run_a())
    thread_b.join(timeout=5)

    assert not b_error, f"cross-loop acquire raised: {b_error!r}"
    assert b_acquired.is_set(), "the waiter on the other loop was never woken"


@pytest.mark.asyncio
async def test_a_half_dead_plane_is_not_healthy_even_when_its_probe_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A passing RPC probe is not proof the plane can take records.

    ``PythonMessagePlaneRunner.health_check`` reaches the RPC endpoint only. If
    the ingest thread exits while RPC keeps serving, the probe answers healthy,
    the delivery path latches as ready, and nothing re-probes it -- the plane
    accepts nothing and ``push_message`` keeps reporting success. Liveness has to
    be part of the answer, not a fallback consulted only after a failed probe.
    """
    service = module.ServerLifecycleService()
    ingest_alive = True

    class _Runner:
        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def is_alive(self) -> bool:
            # Stands in for "RPC thread up, ingest thread gone".
            return ingest_alive

        async def health_check_async(self, *, timeout_s: float = 1.0) -> bool:
            return True  # the RPC endpoint answers either way

    monkeypatch.setattr(module, "build_message_plane_runner", lambda *, auth_token: _Runner())
    monkeypatch.setattr(module, "ingest_auth_token", lambda: "token")
    monkeypatch.setattr(module, "refresh_ingest_endpoint", lambda: None)
    monkeypatch.setattr(module, "start_bridge", lambda: None)
    monkeypatch.setattr(module, "start_proactive_bridge", lambda: None)
    monkeypatch.setattr(module, "wait_for_proactive_subscriber", lambda _t: True)
    monkeypatch.setattr(module, "proactive_bridge_is_alive", lambda: True)

    assert await service.ensure_delivery_path_started() is True

    service._delivery_path_started = False
    ingest_alive = False
    assert await service.ensure_delivery_path_started() is False, (
        "a plane whose ingest thread is gone was reported healthy"
    )


def test_runner_liveness_requires_both_serving_threads() -> None:
    """``is_alive`` is ALL, not ANY -- the two threads do different jobs.

    An ``any`` answer would agree with the RPC-only health probe about a plane
    that can no longer ingest, which is precisely the combination that latches a
    dead delivery path.
    """
    from plugin.message_plane.runner import PythonMessagePlaneRunner

    class _Thread:
        def __init__(self, alive: bool) -> None:
            self._alive = alive

        def is_alive(self) -> bool:
            return self._alive

    runner = PythonMessagePlaneRunner.__new__(PythonMessagePlaneRunner)

    for rpc, ingest, expected in (
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
    ):
        runner._thread = _Thread(rpc)
        runner._ingest_thread = _Thread(ingest)
        assert runner.is_alive() is expected, f"rpc={rpc} ingest={ingest}"

    runner._thread = None
    runner._ingest_thread = _Thread(True)
    assert runner.is_alive() is False


@pytest.mark.asyncio
async def test_an_unhealthy_plane_is_eventually_retired_and_rebuilt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keeping an unhealthy runner must not mean keeping it forever.

    Reuse-without-rebuild exists so a retry does not hand the runner's ports to a
    second one. Applied unconditionally it strands the path: nothing else stops
    the runner, so one whose threads died -- or one that simply never answers --
    fails every future probe and no manual plugin start can recover delivery
    short of a full lifecycle shutdown.
    """
    service = module.ServerLifecycleService()
    built: list[object] = []
    stopped: list[object] = []
    healthy = False
    alive = True

    class _Runner:
        def start(self) -> None:
            return None

        def stop(self) -> None:
            stopped.append(self)

        def is_alive(self) -> bool:
            return alive

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
    monkeypatch.setattr(module, "proactive_bridge_is_alive", lambda: True)

    # Alive but unhealthy: kept for a bounded number of probes...
    for expected in range(1, module._MAX_PLANE_PROBE_FAILURES):
        assert await service._start_delivery_path_locked() == ["message_plane"]
        assert service._plane_probe_failures == expected
        assert stopped == [], "retired before its bounded chances were used"
        assert len(built) == 1

    # ...then retired, so the next entry rebuilds instead of probing a wedge.
    assert await service._start_delivery_path_locked() == ["message_plane"]
    assert stopped == [built[0]]
    assert service._message_plane_runner is None
    healthy = True
    assert await service._start_delivery_path_locked() == []
    assert len(built) == 2

    # Threads gone: retired on the first probe, no need to burn the budget.
    healthy = False
    alive = False
    assert await service._start_delivery_path_locked() == ["message_plane"]
    assert stopped == [built[0], built[1]]
    assert service._message_plane_runner is None


@pytest.mark.asyncio
async def test_retiring_the_plane_also_retires_both_bridges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rebuilt plane can land on different ports; live bridges cannot follow.

    Each bridge connects its socket once, inside its own thread, and neither ever
    reconnects -- the plane bridge PUSHes to the ingest endpoint it read at
    connect time (and swallows send failures), and ``ProactiveBridge`` reads the
    PUB endpoint once at thread start. Both ``start()`` calls return early on a
    live thread, so leaving them up across a rebuild strands them on an endpoint
    nobody serves while the next probe latches the path as ready.
    """
    service = module.ServerLifecycleService()
    stops: list[str] = []

    class _Runner:
        def start(self) -> None:
            return None

        def stop(self) -> None:
            stops.append("runner")

        def is_alive(self) -> bool:
            return False  # threads gone -> retire on the first probe

        async def health_check_async(self, *, timeout_s: float = 1.0) -> bool:
            return False

    monkeypatch.setattr(module, "build_message_plane_runner", lambda *, auth_token: _Runner())
    monkeypatch.setattr(module, "ingest_auth_token", lambda: "token")
    monkeypatch.setattr(module, "refresh_ingest_endpoint", lambda: None)
    monkeypatch.setattr(module, "start_bridge", lambda: None)
    monkeypatch.setattr(module, "start_proactive_bridge", lambda: None)
    monkeypatch.setattr(module, "stop_bridge", lambda: stops.append("plane_bridge"))
    monkeypatch.setattr(module, "stop_proactive_bridge", lambda: stops.append("proactive"))
    monkeypatch.setattr(module, "wait_for_proactive_subscriber", lambda _t: True)
    monkeypatch.setattr(module, "proactive_bridge_is_alive", lambda: True)

    # First entry takes the fresh-build branch: the probe fails, but a runner
    # that was just created keeps its cycle (it may still be coming up), so
    # nothing is retired yet.
    assert await service._start_delivery_path_locked() == ["message_plane"]
    assert stops == []
    assert service._message_plane_runner is not None

    # Second entry takes the reuse branch, re-probes, sees the threads are gone,
    # and retires the plane -- taking both bridges with it.
    assert await service._start_delivery_path_locked() == ["message_plane"]
    assert stops == ["runner", "plane_bridge", "proactive"]
    assert service._message_plane_runner is None


def test_plane_bridge_stop_lets_a_following_start_take_effect() -> None:
    """``stop()`` must clear the thread, or the retirement start-over is a no-op.

    ``start()`` returns early while a thread is alive. If ``stop()`` only set the
    flag, the ``start()`` right after would see the still-draining thread, do
    nothing, and leave the bridge stopped for good -- the retirement path would
    turn a repointing into an outage.
    """
    from plugin.server.messaging import plane_bridge

    bridge = plane_bridge._Bridge()
    if not bridge._enabled:
        pytest.skip("message plane bridge disabled by configuration")

    bridge.start()
    first = bridge._thread
    assert first is not None and first.is_alive()

    bridge.stop()
    assert bridge._thread is None, "stop() left the thread in place"
    assert not first.is_alive(), "stop() returned before the thread exited"

    bridge.start()
    try:
        second = bridge._thread
        assert second is not None and second.is_alive()
        assert second is not first, "start() reused the stopped thread"
    finally:
        bridge.stop()


@pytest.mark.asyncio
async def test_a_raising_plane_start_does_not_start_the_bridges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plane that raised leaves nothing for the bridges to attach to.

    ``ProactiveBridge._run`` reads the PUB endpoint once at thread start and
    ``start()`` reuses a live thread, so a bridge started during a failed attempt
    is pinned to that attempt's endpoint. If the retry's
    ``build_message_plane_runner`` falls back to a different port, the bridge
    stays subscribed to a plane that is not the one running -- and it looks
    perfectly healthy while every proactive message goes nowhere.

    A probe that merely returned False is different: a real runner is assigned at
    the endpoint the bridges are about to read, so that path still starts them.
    """
    service = module.ServerLifecycleService()
    started: list[str] = []

    monkeypatch.setattr(module, "refresh_ingest_endpoint", lambda: started.append("ingest_ep"))
    monkeypatch.setattr(module, "start_bridge", lambda: started.append("plane_bridge"))
    monkeypatch.setattr(module, "start_proactive_bridge", lambda: started.append("proactive"))
    monkeypatch.setattr(module, "wait_for_proactive_subscriber", lambda _t: True)
    monkeypatch.setattr(module, "proactive_bridge_is_alive", lambda: True)

    async def _raises() -> bool:
        raise OSError("port busy")

    monkeypatch.setattr(service, "_start_message_plane", _raises)
    assert await service._start_delivery_path_locked() == ["message_plane"]
    assert started == [], "bridges were pinned to a failed attempt's endpoint"

    # A ZMQ bind failure must take the same degraded path. ZMQError derives from
    # ZMQBaseError(Exception) and is NOT an OSError, so the enumerated tuple this
    # block used to carry let it escape -- and through the newly shared
    # manual-start entry that surfaced as a 500 from the route instead of a
    # degraded start where the router's tools stay usable.
    import zmq

    async def _zmq_raises() -> bool:
        raise zmq.ZMQError(98, "Address already in use")

    monkeypatch.setattr(service, "_start_message_plane", _zmq_raises)
    assert await service._start_delivery_path_locked() == ["message_plane"]
    assert started == []

    # The probe-false path keeps its existing behaviour: it leaves a real runner
    # assigned at the endpoint the bridges are about to read, so they still start.
    # The stub has to assign one the way the real method does -- the guard asks
    # "is there a runner", and a stub that skips that would fake a retirement.
    sentinel = object()

    async def _unhealthy() -> bool:
        service._message_plane_runner = sentinel  # type: ignore[assignment]
        return False

    monkeypatch.setattr(service, "_start_message_plane", _unhealthy)
    assert await service._start_delivery_path_locked() == ["message_plane"]
    assert started == ["ingest_ep", "plane_bridge", "proactive"]

    # And a retirement (runner gone, no throw) must take the same early exit as
    # the throw: this is the case the first version of the guard missed.
    started.clear()
    service._message_plane_runner = None

    async def _retired() -> bool:
        service._message_plane_runner = None
        return False

    monkeypatch.setattr(service, "_start_message_plane", _retired)
    assert await service._start_delivery_path_locked() == ["message_plane"]
    assert started == [], "bridges were bound to a retired runner's endpoint"


@pytest.mark.asyncio
async def test_a_dead_proactive_bridge_is_a_failure_but_a_slow_one_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``wait_for_proactive_subscriber`` returns False for both; they differ.

    A live thread whose SUB has not attached yet heals on its own, so treating it
    as a failure would tear down a working plane. A thread that exited during
    socket setup returns False just as fast and never recovers -- and reporting
    THAT as success latched the path, so nothing ever restarted the bridge and
    every proactive message stayed undeliverable until a full shutdown.
    """
    service = module.ServerLifecycleService()

    async def _plane_ok() -> bool:
        service._message_plane_runner = _PLANE_STUB
        return True

    monkeypatch.setattr(service, "_start_message_plane", _plane_ok)
    monkeypatch.setattr(module, "refresh_ingest_endpoint", lambda: None)
    monkeypatch.setattr(module, "start_bridge", lambda: None)
    monkeypatch.setattr(module, "start_proactive_bridge", lambda: None)
    monkeypatch.setattr(module, "wait_for_proactive_subscriber", lambda _t: False)

    # Alive but slow: not a failure.
    monkeypatch.setattr(module, "proactive_bridge_is_alive", lambda: True)
    assert await service._start_delivery_path_locked() == []

    # Dead thread: a failure, and named so the warning tells the truth.
    monkeypatch.setattr(module, "proactive_bridge_is_alive", lambda: False)
    assert await service._start_delivery_path_locked() == ["proactive_bridge"]

    # Not double-counted when start_proactive_bridge already raised.
    def _boom() -> None:
        raise OSError("no socket")

    monkeypatch.setattr(module, "start_proactive_bridge", _boom)
    assert await service._start_delivery_path_locked() == ["proactive_bridge"]


@pytest.mark.asyncio
async def test_shutdown_itself_closes_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The producer side of the gate contract.

    The consumer-side test below sets ``_delivery_path_shutting_down`` by hand,
    so on its own it would stay green if someone deleted the two lines in
    ``shutdown()`` that close the gate -- and the race this PR closes would be
    back with a passing test attached. This pins the lines themselves.
    """
    service = module.ServerLifecycleService()
    service._delivery_path_started = True

    async def _noop_internal() -> object:
        return module._ShutdownResult(had_errors=False)

    monkeypatch.setattr(service, "_shutdown_internal", _noop_internal)

    await service.shutdown()

    assert service._delivery_path_shutting_down is True
    assert service._delivery_path_started is False


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
        service._message_plane_runner = _PLANE_STUB
        return True

    monkeypatch.setattr(service, "_start_message_plane", _start_plane)
    monkeypatch.setattr(module, "refresh_ingest_endpoint", lambda: None)
    monkeypatch.setattr(module, "start_bridge", lambda: None)
    monkeypatch.setattr(module, "start_proactive_bridge", lambda: None)
    monkeypatch.setattr(module, "wait_for_proactive_subscriber", lambda _t: True)

    # Simulate what shutdown() does to the gate, without driving real teardown.
    async with module._held(service._delivery_path_lock):
        service._delivery_path_shutting_down = True
        service._delivery_path_started = False

    await service.ensure_delivery_path_started()
    assert starts == []
    assert service._delivery_path_started is False

    # startup() reopens it: the same service instance is reused across a restart
    # in the same process, and a gate latched closed would mute the new run.
    async with module._held(service._delivery_path_lock):
        service._delivery_path_shutting_down = False
    await service.ensure_delivery_path_started()
    assert starts == ["plane"]
    assert service._delivery_path_started is True
