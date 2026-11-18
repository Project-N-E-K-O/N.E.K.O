"""Main-owned share jobs; the account process supplies credentialed transport only."""

from __future__ import annotations

import asyncio
import hashlib
import json
from uuid import UUID

from fastapi import APIRouter, Request
import httpx

from main_routers.knowledge_library_router import LibraryRoute, _json, get_host
from services.knowledge_library.jobs import run_sync
from services.knowledge_library.models import LibraryError
from services.knowledge_library.package_install import get_artifact, install_package
from services.knowledge_library.package_validate import DEFAULT_LIMITS, PACKAGE_SUFFIX, validate_package


router = APIRouter(prefix="/api/knowledge-library/market", tags=["knowledge-library-market"], route_class=LibraryRoute)
_MAX_JSON = 256 * 1024
_PUBLISH_FIELDS = {"artifact_id", "title", "description", "client_request_id", "share_id", "revision"}


async def _credentials(client: httpx.AsyncClient) -> tuple[str, dict]:
    """Ask the running bridge for its local token; never import account process state."""
    from config.network import resolve_user_plugin_base

    base = resolve_user_plugin_base().rstrip("/")
    response = await client.get(base + "/market/bridge-token", headers={"Origin": base})
    if not response.is_success or len(response.content) > 4096:
        raise LibraryError("market_unavailable")
    try:
        token = response.json()["bridge_token"]
    except (ValueError, KeyError, TypeError) as exc:
        raise LibraryError("market_invalid_response") from exc
    if not isinstance(token, str) or not token:
        raise LibraryError("market_invalid_response")
    return base, {"Origin": base, "Authorization": "Bearer " + token}


async def _read_json(response: httpx.Response) -> dict:
    """Bound all transport replies and expose only stable failure reasons."""
    raw = bytearray()
    async for chunk in response.aiter_bytes():
        raw.extend(chunk)
        if len(raw) > _MAX_JSON:
            raise LibraryError("market_invalid_response")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise LibraryError("market_invalid_response") from exc
    if not isinstance(value, dict):
        raise LibraryError("market_invalid_response")
    if not response.is_success:
        detail = value.get("detail")
        code = detail.get("code") if isinstance(detail, dict) else value.get("reason")
        raise LibraryError(code if isinstance(code, str) and len(code) < 100 else "market_request_failed")
    return value


async def _bridge_json(method: str, path: str, **kwargs) -> dict:
    """Use fixed local bridge paths; remote URLs and account tokens never come from clients."""
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False, trust_env=False) as client:
            base, headers = await _credentials(client)
            async with client.stream(method, base + "/market/library/" + path, headers=headers, **kwargs) as response:
                return await _read_json(response)
    except httpx.HTTPError as exc:
        raise LibraryError("market_unavailable") from exc


async def _publisher() -> dict:
    """Resolve the actual Market author without moving OAuth credentials to Main."""
    value = await _bridge_json("GET", "publisher")
    if (type(value.get("publisher_id")) is not int or value["publisher_id"] < 1
            or not isinstance(value.get("market_api_url"), str) or not value["market_api_url"]):
        raise LibraryError("market_invalid_response")
    return {"publisher_id": value["publisher_id"], "market_api_url": value["market_api_url"]}


def _selected(share: dict, frozen: dict) -> dict:
    """Bind published state, file identity and package identity before any install."""
    preview = share.get("preview")
    if (share.get("kind") != "knowledge" or share.get("status") != "published"
            or share.get("id") != frozen["share_id"] or share.get("revision") != frozen["revision"]
            or share.get("file_sha256") != frozen["file_sha256"]):
        raise LibraryError("market_revision_changed")
    if (not isinstance(preview, dict) or preview.get("format") != "neko.personal-knowledge-package"
            or preview.get("format_version") != 1 or not isinstance(preview.get("package_id"), str)
            or not isinstance(preview.get("package_version"), str)):
        raise LibraryError("market_not_knowledge_package")
    size = share.get("byte_size")
    if type(size) is not int or not 0 < size <= DEFAULT_LIMITS.max_archive_bytes:
        raise LibraryError("package_too_large")
    return preview


@router.get("/status")
async def status():
    """Read the existing desktop account state without exposing credentials."""
    return await _bridge_json("GET", "status")


@router.post("/browse")
async def browse(request: Request):
    """Create a market pairing only after the Main mutation guard has passed."""
    await _json(request, set())
    return await _bridge_json("POST", "browse")


@router.post("/login")
async def login(request: Request):
    """Start the existing desktop OAuth flow rather than a second credentials store."""
    await _json(request, set())
    return await _bridge_json("POST", "login")


@router.get("/shares/{share_id}")
async def detail(share_id: int):
    """Supply the share revision and raw artifact hash the user selects for install."""
    if share_id < 1:
        raise LibraryError("invalid_share")
    return await _bridge_json("GET", f"shares/{share_id}")


def _publish_runner(host, frozen: dict):
    async def run(context):
        # Market's creation UUID is unique per author. A queued job, an uncertain
        # response and its explicit retry must never move into another account's
        # idempotency namespace, including an identically numbered foreign Market.
        publisher = await _publisher()
        if any(frozen.get(key) != value for key, value in publisher.items()):
            raise LibraryError("market_publisher_changed")
        path = await context.run_sync(get_artifact, host.root, frozen["artifact_id"])

        def inspect():
            validated = validate_package(path, expected_sha256=frozen["artifact_id"])
            return path.stat().st_size, validated.manifest["package_id"], validated.manifest["version"]

        size, package_id, package_version = await context.run_sync(inspect)
        metadata = {**frozen, "byte_size": size}
        metadata_line = json.dumps(metadata, ensure_ascii=False).encode() + b"\n"
        if frozen.get("share_id"):
            # A replacement response may have been lost after Market committed.
            # Observe exact bytes first, instead of replaying a stale revision or
            # assigning a fresh identity to the same author operation.
            current = (await _bridge_json("GET", f"shares/{frozen['share_id']}"))["share"]
            if current.get("file_sha256") == frozen["artifact_id"] and current.get("byte_size") == size:
                preview = current.get("preview", {})
                if preview.get("package_id") != package_id or preview.get("package_version") != package_version:
                    raise LibraryError("market_artifact_mismatch")
                context.commit_gate()
                return {"share": current, "artifact_id": frozen["artifact_id"]}
        def publication_gate():
            # Once the upload request starts, a lost response cannot prove that
            # Market did not commit. Credentials-only failures remain cancellable.
            with host.write_guard():
                context.commit_gate()
        for attempt in range(2):
            handle = await run_sync(path.open, "rb")
            try:
                async def body():
                    yield metadata_line
                    while chunk := await run_sync(handle.read, 64 * 1024):
                        yield chunk
                async with httpx.AsyncClient(timeout=180.0, follow_redirects=False, trust_env=False) as client:
                    base, headers = await _credentials(client)
                    headers.update({"Content-Type": "application/x-neko-library-upload",
                                    "Content-Length": str(size + len(metadata_line))})
                    await context.run_sync(publication_gate)
                    async with client.stream("POST", base + "/market/library/publish", headers=headers, content=body()) as response:
                        try:
                            result = await _read_json(response)
                        except LibraryError as exc:
                            if exc.code == "market_auth_retry" and attempt == 0:
                                continue
                            raise
                share = result.get("share", {})
                preview = share.get("preview", {})
                if (share.get("file_sha256") != frozen["artifact_id"] or share.get("byte_size") != size
                        or preview.get("package_id") != package_id or preview.get("package_version") != package_version):
                    raise LibraryError("market_artifact_mismatch")
                return {"share": share, "artifact_id": frozen["artifact_id"]}
            except httpx.HTTPError as exc:
                raise LibraryError("market_unavailable") from exc
            finally:
                await run_sync(handle.close)
        raise LibraryError("market_auth_required")
    return run


def _publication_payload(value: dict) -> dict:
    """Compare effective browser inputs independently of optional-field spelling."""
    return {"artifact_id": value["artifact_id"], "title": value["title"],
            "description": value.get("description", ""), "client_request_id": str(UUID(value["client_request_id"])),
            "share_id": value.get("share_id"), "revision": value.get("revision")}


async def _submit_publication(service, host, payload: dict, *, retry: bool = False) -> dict:
    """Reserve one durable author operation even when its HTTP response is lost."""
    if getattr(host, "_closing", False):
        # Shutdown drains retained admissions before releasing the root lock;
        # a request resuming after file validation may not create another one.
        raise LibraryError("service_stopping")
    # Host attributes are initialized without an await, so all requests in this
    # event loop share the same admission lock and retain their owned tasks.
    if not hasattr(host, "_market_publication_lock"):
        host._market_publication_lock = asyncio.Lock()
        host._market_publication_tasks = set()
    # Waiting for account IO/this lock still owns a task and request input. Use
    # the domain's existing bound rather than creating an unlimited second queue.
    if len(host._market_publication_tasks) >= service.limits.max_pending_jobs:
        raise LibraryError("jobs_busy")

    async def reserve():
        async with host._market_publication_lock:
            wanted = _publication_payload(payload)
            # Startup loads the durable job files into these records. The same
            # map also contains reservations awaiting their first atomic save.
            # Do not scope lookup to the current author: that would let a login
            # switch silently create a second author's work with the same UUID.
            records = [record for record in service.jobs.records.values()
                       if record["kind"] == "market_publish"
                       and str(UUID(record["frozen_input"]["client_request_id"])) == wanted["client_request_id"]]
            if any(_publication_payload(record["frozen_input"]) != wanted for record in records):
                raise LibraryError("market_request_conflict")
            publisher = await _publisher()
            known = [record["frozen_input"] for record in records]
            if "publisher_id" in payload:
                known.append(payload)  # Explicit retry preserves its original author too.
            if any(any(previous.get(key) != value for key, value in publisher.items()) for previous in known):
                raise LibraryError("market_publisher_changed")
            if records:
                # A repeat POST recovers its original job. An explicit retry
                # may enqueue only when no matching active/completed job exists.
                record = max(records, key=lambda item: (
                    2 if item["state"] in {"queued", "running"} else 1 if item["state"] == "completed" else 0,
                    item["created_at"],
                ))
                if not retry or record["state"] in {"queued", "running", "completed"}:
                    return service.jobs.public(record)
                frozen = record["frozen_input"]
            else:
                frozen = {**wanted, **publisher}
            await run_sync(get_artifact, host.root, frozen["artifact_id"])
            return await service.submit_job("market_publish", None, _publish_runner(host, frozen), frozen_input=frozen)

    # Retain the whole locked transaction, not just jobs.submit: otherwise a
    # disconnected caller could release the lock before its background submit
    # registered the UUID, letting another request pass the same empty lookup.
    task = asyncio.create_task(reserve(), name="personal-library-publication-admission")
    host._market_publication_tasks.add(task)
    def finished(completed):
        host._market_publication_tasks.discard(completed)
        if not completed.cancelled():
            completed.exception()  # Observe failures even if the HTTP caller left.
    task.add_done_callback(finished)
    return await asyncio.shield(task)


@router.post("/publish")
async def publish(request: Request):
    """Publish a selected completed artifact through the domain's existing queue."""
    payload = await _json(request, _PUBLISH_FIELDS)
    try:
        UUID(payload["client_request_id"])
        artifact = payload["artifact_id"]
        if len(artifact) != 64 or any(char not in "0123456789abcdef" for char in artifact):
            raise ValueError
        if not isinstance(payload["title"], str) or not 0 < len(payload["title"].strip()) <= 120:
            raise ValueError
        if not isinstance(payload.get("description", ""), str) or len(payload.get("description", "")) > 5000:
            raise ValueError
        if payload.get("share_id") is not None and (type(payload["share_id"]) is not int or payload["share_id"] < 1
                or type(payload.get("revision")) is not int or payload["revision"] < 1):
            raise ValueError
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise LibraryError("invalid_publish_request") from exc
    host = await get_host(request)
    service = await host.service()
    return {"ok": True, "job": await _submit_publication(service, host, payload)}


def _install_runner(service, host, frozen: dict):
    async def run(context):
        selected = (await _bridge_json("GET", f"shares/{frozen['share_id']}"))["share"]
        preview = _selected(selected, frozen)
        path = host.root / "staging/market" / (context.record["job_id"] + PACKAGE_SUFFIX)

        handle = None
        def open_output():
            nonlocal handle
            with host.write_guard():
                path.parent.mkdir(parents=True, exist_ok=True)
                # Assign ownership in the real thread, before its await can be
                # cancelled or a job flag can reject the result on return.
                handle = path.open("wb", buffering=0)

        received, hashed = 0, hashlib.sha256()
        try:
            context.check_cancelled()
            await run_sync(open_output)
            context.check_cancelled()
            async with httpx.AsyncClient(timeout=180.0, follow_redirects=False, trust_env=False) as client:
                base, headers = await _credentials(client)
                async with client.stream("GET", base + f"/market/library/shares/{frozen['share_id']}/download",
                                         params={"revision": frozen["revision"], "file_sha256": frozen["file_sha256"]},
                                         headers=headers) as response:
                    if not response.is_success:
                        await _read_json(response)
                    async for chunk in response.aiter_bytes(64 * 1024):
                        context.check_cancelled()
                        received += len(chunk)
                        if received > selected["byte_size"]:
                            raise LibraryError("market_artifact_mismatch")
                        hashed.update(chunk)
                        def save(block):
                            with host.write_guard():
                                handle.write(block)
                        await context.run_sync(save, chunk)
            await run_sync(handle.close)
            if received != selected["byte_size"] or hashed.hexdigest() != frozen["file_sha256"]:
                raise LibraryError("market_artifact_mismatch")
            try:
                spec = await host.embedder.specification()
            except LibraryError:
                spec = None
            prepared = await install_package(path, host.root, expected_spec=spec, expected_sha256=frozen["file_sha256"],
                                             expected_package_id=preview["package_id"], expected_version=preview["package_version"],
                                             check_cancelled=context.check_cancelled, write_guard=host.write_guard)
            if prepared["snapshot"]["embedding_spec"] != preview.get("embedding_spec"):
                raise LibraryError("market_artifact_mismatch")
            library = await service.install_snapshot(prepared["snapshot"], installation=prepared["installation"],
                                                    library_id=frozen.get("library_id"), context=context)
            result = {"library_id": library["library_id"], "artifact_id": frozen["file_sha256"],
                      "share_id": frozen["share_id"], "revision": frozen["revision"],
                      "semantic_available": prepared["semantic_available"]}
            # The market wrapper must not discard the local commit's warning
            # or report a visible installation as a failed download.
            if library.get("warnings"):
                result["warnings"] = library["warnings"]
            return result
        except httpx.HTTPError as exc:
            raise LibraryError("market_unavailable") from exc
        finally:
            def cleanup():
                # Every parser/copier awaited above has really exited. Retained
                # artifacts are the recovery source; retries redownload their
                # frozen revision, so task staging bytes need no second lifetime.
                try:
                    if handle is not None:
                        handle.close()
                finally:
                    with host.write_guard():
                        path.unlink(missing_ok=True)
            await run_sync(cleanup)
    return run


@router.post("/install")
async def install(request: Request):
    """Freeze a user's selected share revision/hash, never install the latest silently."""
    payload = await _json(request, {"share_id", "revision", "file_sha256", "library_id"})
    if (type(payload.get("share_id")) is not int or payload["share_id"] < 1
            or type(payload.get("revision")) is not int or payload["revision"] < 1
            or not isinstance(payload.get("file_sha256"), str) or len(payload["file_sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in payload["file_sha256"])):
        raise LibraryError("invalid_share")
    host = await get_host(request)
    service = await host.service()
    if payload.get("library_id"):
        await service.get_library(payload["library_id"])
    return {"ok": True, "job": await service.submit_job("market_install", payload.get("library_id"),
                                                         _install_runner(service, host, payload), frozen_input=payload)}


async def retry_market_job(service, host, record: dict) -> dict:
    """Let the common retry route reuse the same frozen UUID or selected share identity."""
    frozen = record["frozen_input"]
    if record["kind"] == "market_publish":
        return await _submit_publication(service, host, frozen, retry=True)
    runner = _install_runner(service, host, frozen)
    return await service.submit_job(record["kind"], record["library_id"], runner, frozen_input=frozen)
