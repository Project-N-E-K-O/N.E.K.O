"""Main-owned connected downloads, reusing N2's one spool and durable install.

Only the configured running Plugin Server is contacted here. It owns desktop
OAuth and fetches a fixed Market endpoint; no browser URL or local path enters
this downloader. Importing the module starts no worker and creates no files.
"""

from __future__ import annotations

import asyncio
import json
import time
from urllib.parse import urlencode

import anyio
import httpx

from config.network import resolve_user_plugin_base
from utils.character_memory import character_config_mutation_lock
from utils.cloudsave_runtime import assert_cloudsave_writable, MaintenanceModeError
from utils.http.internal_client import internal_service_event_hooks
from utils.market_config import get_market_web_url
from utils.storage_location_bootstrap import get_storage_startup_blocking_reason_readonly

from .catalog import public_result
from .market import Launches, PackageStream
from .paths import WorkshopError
from .runtime import get_service
from .tasks import _drain_sync


def _client(base):
    return httpx.Client(base_url=base, timeout=httpx.Timeout(20, connect=3),
                        follow_redirects=False, trust_env=False, proxy=None,
                        event_hooks=internal_service_event_hooks(sync=True))


def _metadata_client(base):
    return httpx.AsyncClient(base_url=base, timeout=httpx.Timeout(20, connect=3),
                             follow_redirects=False, trust_env=False, proxy=None,
                             event_hooks=internal_service_event_hooks())


def _pair_client():
    return httpx.AsyncClient(timeout=8, trust_env=False, proxy=None, follow_redirects=False,
                             event_hooks=internal_service_event_hooks())


def _json(response, maximum=4096):
    """Bound loopback JSON too; never buffer a large error or compressed body."""
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise WorkshopError("model_market_unavailable")
    raw = bytearray()
    deadline = time.monotonic() + 25
    for chunk in response.iter_raw():
        if time.monotonic() >= deadline:
            raise WorkshopError("model_market_timeout")
        if len(raw) + len(chunk) > maximum:
            raise WorkshopError("model_market_unavailable")
        raw.extend(chunk)
    return _decode_json(raw, response)


def _decode_json(raw, response):
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        raise WorkshopError("model_market_unavailable") from None
    if not isinstance(value, dict):
        raise WorkshopError("model_market_unavailable")
    if response.status_code != 200:
        detail = value.get("detail")
        code = detail.get("code") if isinstance(detail, dict) else None
        # Only existing stable auth/domain codes can become a public error.
        if code not in {"market_auth_required", "market_auth_pending", "model_market_busy",
                        "model_market_package_changed"}:
            code = "model_market_unavailable"
        raise WorkshopError(code)
    return value


async def _metadata_json(response, maximum=4096):
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise WorkshopError("model_market_unavailable")
    raw = bytearray()
    try:
        async with asyncio.timeout(25):
            async for chunk in response.aiter_raw():
                if len(raw) + len(chunk) > maximum:
                    raise WorkshopError("model_market_unavailable")
                raw.extend(chunk)
    except TimeoutError:
        raise WorkshopError("model_market_timeout") from None
    return _decode_json(raw, response)


def _ready(config):
    if get_storage_startup_blocking_reason_readonly(config):
        raise WorkshopError("model_workshop_storage_blocked")
    try:
        assert_cloudsave_writable(config, operation="save", target="model_workshop")
    except MaintenanceModeError:
        raise WorkshopError("model_workshop_storage_blocked") from None


class ConnectedDownloads:
    """Keep bounded jobs alive after HTTP disconnect, and drain them at shutdown."""

    def __init__(self, apply_installed):
        self._apply_installed = apply_installed
        self.launches = Launches()
        self.jobs: dict[tuple[str, str], asyncio.Task] = {}
        self.closed = False
        self.metadata_jobs: dict[tuple[str, str], asyncio.Task] = {}

    async def launch(self, config, origin):
        if self.closed:
            raise WorkshopError("model_workshop_closed")
        _ready(config)
        web = get_market_web_url().rstrip("/")
        if not web:
            raise WorkshopError("model_market_unavailable")
        async with character_config_mutation_lock:
            row = await _drain_sync(lambda: self.launches.create(config))
        try:
            if self.closed:
                raise WorkshopError("model_workshop_closed")
            # Pairing state belongs to the live Plugin Server, not an imported
            # copy of its globals. Its ordinary local-origin gate remains intact.
            base = resolve_user_plugin_base()
            async with asyncio.timeout(8), _pair_client() as client:
                async with client.stream("POST", base + "/market/pair-code",
                                         headers={"Origin": base}) as response:
                    raw = bytearray()
                    async for chunk in response.aiter_raw(4096):
                        if len(raw) + len(chunk) > 4096:
                            raise WorkshopError("model_market_unavailable")
                        raw.extend(chunk)
                    pair = json.loads(raw)
                    if response.status_code != 200 or not isinstance(pair, dict):
                        raise WorkshopError("model_market_unavailable")
            code = pair.get("one_time_code")
            if not isinstance(code, str) or not 1 <= len(code) <= 256:
                raise WorkshopError("model_market_unavailable")
            if self.closed:
                raise WorkshopError("model_workshop_closed")
            row.check(config)
            # Distinct keys prevent the existing theater initializer consuming
            # model pairing before the new Market adapter can exchange it.
            query = urlencode({"model_origin": origin, "model_request": row.request_id,
                               "model_code": code})
            return {**row.public(), "url": web + "/#/models?" + query}
        except BaseException as error:
            async with character_config_mutation_lock:
                self.launches.items.pop(row.request_id, None)
            if isinstance(error, (asyncio.CancelledError, WorkshopError)):
                raise
            raise WorkshopError("model_market_unavailable") from None

    async def select(self, config, request_id, model_id, digest, size, operation_id):
        if self.closed:
            raise WorkshopError("model_workshop_closed")
        _ready(config)
        # Coordinate admission with the original role-save lock; the per-role
        # limit also covers the saved callback while its runtime refresh is pending.
        async with character_config_mutation_lock:
            row, fresh = self.launches.select(config, request_id, model_id, digest, size, operation_id)
        if fresh:
            job = asyncio.create_task(self._run(config, row))
            key = (row.request_id, row.operation_id)
            self.jobs[key] = job
            row.connection.running = True
            job.add_done_callback(lambda finished: self._forget(key, finished))
        return row.public()

    def _forget(self, request_id, finished):
        # A queued callback from download must not discard a newer apply owner.
        if self.jobs.get(request_id) is finished:
            self.jobs.pop(request_id, None)
            # A late completion callback must not clear a successor's worker ownership.
            row = self.launches.get(*request_id)
            row.connection.running = any(key[0] == row.request_id and not job.done()
                                         for key, job in self.jobs.items())

    async def _run(self, config, row):
        key = (row.request_id, row.operation_id)
        try:
            if self.closed or row.cancel.is_set():
                raise WorkshopError("model_market_cancelled")
            fetch = asyncio.create_task(self._metadata(row))
            self.metadata_jobs[key] = fetch
            try:
                metadata = await fetch
            finally:
                self.metadata_jobs.pop(key, None)
            await _drain_sync(lambda: self._receive(config, row, metadata))
        except asyncio.CancelledError:
            if not self.closed and not row.cancel.is_set():
                raise
            row.phase, row.error = "cancelled", "model_market_cancelled"
        except Exception as error:
            row.error = str(error) if isinstance(error, WorkshopError) else "model_market_unavailable"
            row.phase = "cancelled" if row.cancel.is_set() else "failed"

    async def _metadata(self, row):
        model_id, digest, size = row.selection
        base = resolve_user_plugin_base()
        # Only these read-only requests are interruptible by shutdown. Accepted
        # package writes retain the synchronous owner and its existing drain.
        async with _metadata_client(base) as client:
            local = {"Origin": base, "Accept-Encoding": "identity"}
            async with client.stream("GET", "/market/bridge-token", headers=local) as response:
                token = (await _metadata_json(response)).get("bridge_token")
            if not isinstance(token, str) or not 1 <= len(token) <= 512:
                raise WorkshopError("model_market_unavailable")
            if self.closed or row.cancel.is_set():
                raise WorkshopError("model_market_cancelled")
            headers = {**local, "Authorization": "Bearer " + token}
            params = {"expected_sha256": digest, "byte_size": size}
            async with client.stream("GET", f"/market/models/descriptor/{model_id}",
                                     headers=headers, params=params) as response:
                metadata = await _metadata_json(response)
        return base, token, metadata

    def _receive(self, config, row, selected):
        if self.closed or row.cancel.is_set():
            raise WorkshopError("model_market_cancelled")
        model_id, digest, size = row.selection
        base, token, metadata = selected
        with _client(base) as client:
            local = {"Origin": base, "Accept-Encoding": "identity"}
            headers = {**local, "Authorization": "Bearer " + token}
            params = {"expected_sha256": digest, "byte_size": size}
            if (metadata.get("id"), metadata.get("file_sha256"), metadata.get("byte_size")) != row.selection:
                raise WorkshopError("model_market_package_changed")
            _ready(config)
            row.check(config)
            service = get_service(config)
            # Fresh authorization is still checked above, but an already
            # installed, locally editable model needs no second network copy.
            registry = service.installer.registry
            with service.activity(), registry.lock:
                # A different model's unsafe/missing resources must not block
                # this choice; retain the selected package's attempt/status gates.
                installed = registry.read()
                candidate = installed["installed"].get(digest)
                existing = (dict(candidate, status="installed")
                            if candidate is not None
                            and installed["attempts"].get(digest) == candidate["install_id"]
                            and registry.status(candidate) == "installed" else None)
            if existing is not None:
                state = {"phase": "completed", "result": {**existing, "already_installed": True}}
            else:
                with client.stream("GET", f"/market/models/package/{model_id}",
                                   headers=headers, params=params) as response:
                    if response.status_code != 200:
                        _json(response)  # Translate only bounded, known error codes.
                    if (response.headers.get("content-type", "").split(";", 1)[0] != "application/zip"
                            or response.headers.get("content-length") != str(size)
                            or response.headers.get("content-encoding", "identity").lower() != "identity"):
                        raise WorkshopError("model_market_package_changed")
                    # Do not aggregate a megabyte before returning to the
                    # deadline/cancel gate: a slow trickle must remain bounded.
                    stream = PackageStream(response.iter_raw(), digest, size, row.cancel)
                    row.task_id = service.receive(stream)
                row.phase = "installing"
                while True:
                    if row.cancel.is_set():
                        state = service.cancel(row.task_id)
                        break
                    try:
                        state = service.wait(row.task_id, timeout=.1)
                        break
                    except TimeoutError:
                        continue
            if state["phase"] != "completed":
                row.phase, row.error = state["phase"], state.get("error")
                return
            row.result = public_result(config, state["result"])
            row.phase = "installed"
            try:
                row.check(config)
            except WorkshopError as error:
                # An installed resource is retained, but stale authorization
                # can never proceed to the separate, guarded role-save stage.
                if str(error) != "market_auth_pending":
                    row.phase, row.error = "blocked", str(error)

    def status(self, config, request_id, operation_id=None):
        row = self.launches.get(request_id, operation_id)
        current = self.launches.get(request_id)
        # Validate the current baseline even between choices, preserving saved receipts.
        if current.phase != "applying":
            # asave_characters yields while writing. Only its locked commit
            # callback may advance the witness; a concurrent read must not
            # revoke that in-flight save by observing its intermediate file.
            try:
                current.check_connection(config)
            except WorkshopError as error:
                if str(error) != "market_auth_pending":
                    current.connection.error = str(error)
        if row.phase in {"browsing", "installed"}:
            try:
                row.check(config)
            except WorkshopError as error:
                if str(error) != "market_auth_pending":
                    row.phase, row.error = "blocked", str(error)
        return row.public()

    async def apply(self, config, request_id, operation_id):
        """Only the local manager may finish its edit check and authorize saving.

        Keep a single background owner across HTTP disconnect/repeated polling;
        the original save handler rechecks role identity inside its mutation lock.
        """
        row = self.launches.operation(request_id, operation_id)
        if row.phase in {"saved", "completed", "blocked", "failed", "cancelled"}:
            return row.public()
        if self.closed:
            raise WorkshopError("model_workshop_closed")
        _ready(config)
        key = (request_id, operation_id)
        job = self.jobs.get(key)
        if row.phase == "installed" and job is not None and not job.done():
            # The download's final callback, or another save caller, still owns
            # this row. Settle it before deciding whether a save is needed.
            await asyncio.shield(job)
            return await self.apply(config, request_id, operation_id)
        if row.phase == "installed" and (job is None or job.done()):
            async def save():
                try:
                    await self._apply_installed(config, row)
                except Exception as error:
                    if isinstance(error, WorkshopError) and str(error) == "market_auth_pending":
                        return  # The next local apply retries its locked authorization.
                    row.error = str(error) if isinstance(error, WorkshopError) else "model_market_apply_failed"
                    # Persistence callbacks are authoritative even on refresh failure.
                    if row.phase != "saved":
                        row.phase = "blocked"
            job = asyncio.create_task(save())
            self.jobs[key] = job
            row.connection.running = True
            job.add_done_callback(lambda finished: self._forget(key, finished))
        elif row.phase not in {"installed", "applying"}:
            raise WorkshopError("model_market_not_installed")
        if job is not None:
            await asyncio.shield(job)
        if row.phase == "installed":
            raise WorkshopError("market_auth_pending")
        return row.public()

    def cancel(self, request_id, operation_id):
        row = self.launches.operation(request_id, operation_id)
        # Never claim cancellation rolled back a role-save commit.
        if row.phase not in {"applying", "saved", "completed"}:
            row.cancel.set()
            if row.phase in {"browsing", "installed", "blocked"}:
                row.phase, row.error = "cancelled", "model_market_cancelled"
        return row.public()

    async def ready(self, config, request_id, operation_id):
        """Only the native manager can acknowledge its clean reload/edit checks."""
        if self.closed:
            raise WorkshopError("model_workshop_closed")
        row = self.launches.operation(request_id, operation_id)
        job = self.jobs.get((request_id, operation_id))
        if row.phase in {"saved", "completed", "failed", "cancelled", "blocked"} and job is not None:
            await asyncio.shield(job)
        _ready(config)
        async with character_config_mutation_lock:
            row.check_connection(config)
            if row.phase not in {"saved", "completed", "failed", "cancelled", "blocked"} or row.error == "model_market_refresh_failed":
                raise WorkshopError("model_market_busy")
            row.connection.ready = True
            # Capacity exhaustion ends this pairing instead of promising another
            # choice or discarding a receipt that a delayed request could replay.
            from .market import MAX_OPERATIONS
            if len(row.connection.operations) >= MAX_OPERATIONS:
                row.connection.error = "model_market_expired"
        return row.public()

    def invalidate(self, request_id):
        """A local edit permanently revokes this pairing, including future choices."""
        row = self.launches.get(request_id)
        row.connection.error = row.connection.error or "model_market_target_changed"
        if row.operation_id is not None:
            return self.cancel(request_id, row.operation_id)
        return row.public()

    async def close(self):
        self.closed = True
        for row in list(self.launches.items.values()):
            if row.phase in {"downloading", "installing", "installed"}:
                row.cancel.set()
        for fetch in list(self.metadata_jobs.values()):
            fetch.cancel()
        pending = asyncio.gather(*list(self.jobs.values()), return_exceptions=True)
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            with anyio.CancelScope(shield=True):
                while not pending.done():
                    try:
                        await asyncio.wait({pending})
                    except asyncio.CancelledError:
                        continue
            pending.result()
            raise
        pending.result()
