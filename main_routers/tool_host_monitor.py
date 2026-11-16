"""Monitor plugin hosts that own remote tool registrations."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import psutil

from utils.internal_http_client import get_internal_http_client

_PROBE_INTERVAL = 5.0
_PROBE_TIMEOUT = 1.0
_FAILURE_THRESHOLD = 3
# How long a host that has never answered /health keeps its tools while its
# process is alive. This covers a listener that has not bound yet; past it, a
# host that never confirmed itself is treated like one whose listener stopped.
_STARTUP_GRACE = 60.0


@dataclass(frozen=True)
class ToolHost:
    instance_id: str
    origin: str
    pid: int
    started_at: float


@dataclass
class _ProbeState:
    failures: int = 0
    seen_healthy: bool = False
    first_probed_at: float = field(default_factory=time.monotonic)


def _process_exited(pid: int, started_at: float) -> bool:
    try:
        process = psutil.Process(pid)
        return process.create_time() != started_at or process.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True
    except psutil.AccessDenied:
        return False


def _exited_processes(processes: set[tuple[int, float]]) -> set[tuple[int, float]]:
    return {(pid, started_at) for pid, started_at in processes if _process_exited(pid, started_at)}


async def _probe_origin(client: httpx.AsyncClient, origin: str) -> tuple[str | None, bool]:
    try:
        response = await asyncio.wait_for(
            client.get(f"{origin}/health", timeout=_PROBE_TIMEOUT, follow_redirects=False),
            timeout=_PROBE_TIMEOUT,
        )
        if response.is_success:
            body = response.json()
            if isinstance(body, dict) and isinstance(body.get("host_instance_id"), str):
                return body["host_instance_id"], False
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return None, True
    except (httpx.HTTPError, httpx.InvalidURL, TimeoutError, ValueError):
        pass
    return None, False


class ToolHostMonitor:
    def __init__(
        self,
        registered_hosts: Callable[[], set[ToolHost]],
        evict_host: Callable[[str, str], int],
    ) -> None:
        self._registered_hosts = registered_hosts
        self._evict_host = evict_host
        self._probes: dict[ToolHost, _ProbeState] = {}
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="plugin-tool-host-monitor")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._probes.clear()

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(_PROBE_INTERVAL)
            await self.probe_once()

    async def probe_once(self) -> None:
        hosts = self._registered_hosts()
        for host in self._probes.keys() - hosts:
            del self._probes[host]
        if not hosts:
            return

        client = get_internal_http_client()
        origins = dict.fromkeys(host.origin for host in hosts)
        responses = await asyncio.gather(*(_probe_origin(client, origin) for origin in origins))
        observations = dict(zip(origins, responses))

        # Before the first successful probe, a live process may still be
        # starting, so it gets a bounded grace period. For an embedded host
        # that has answered before (or never answered within the grace
        # period), the HTTP listener can stop while its process continues
        # running, so process liveness no longer protects it.
        now = time.monotonic()
        failed_probes = []
        processes_to_check = set()
        for host in hosts:
            state = self._probes.setdefault(host, _ProbeState())
            observed_id, connection_failed = observations[host.origin]
            if observed_id == host.instance_id:
                state.seen_healthy = True
                state.failures = 0
                continue
            confirmed_or_overdue = (
                state.seen_healthy or now - state.first_probed_at >= _STARTUP_GRACE
            )
            listener_stopped = confirmed_or_overdue and bool(observed_id or connection_failed)
            failed_probes.append((host, state, observed_id, listener_stopped))
            if not listener_stopped:
                processes_to_check.add((host.pid, host.started_at))

        exited = (
            await asyncio.to_thread(_exited_processes, processes_to_check)
            if processes_to_check else set()
        )
        for host, state, observed_id, listener_stopped in failed_probes:
            if not listener_stopped and (host.pid, host.started_at) not in exited:
                state.failures = 0
                continue
            if observed_id:
                self._evict_host(
                    host.instance_id, f"callback {host.origin} now belongs to instance {observed_id}",
                )
                continue
            state.failures += 1
            if state.failures >= _FAILURE_THRESHOLD:
                self._evict_host(host.instance_id, f"health probe for {host.origin} failed repeatedly")
