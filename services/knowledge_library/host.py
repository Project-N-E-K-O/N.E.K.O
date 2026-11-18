"""Adapt application storage and bounded computation to the library domain."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import re
from weakref import WeakSet

from .models import LibraryError
from .paths import library_root

_hosts: WeakSet = WeakSet()


class HttpEmbeddingAdapter:
    """Borrow the owning encoder over HTTP in split and merged runtimes."""

    def __init__(self) -> None:
        self._spec: dict | None = None

    @staticmethod
    def _url(suffix: str) -> str:
        # The launcher resolves the current Memory endpoint; no browser origin
        # or fixed deployment port is stored in the frontend or package.
        from config import MEMORY_SERVER_PORT

        return f"http://127.0.0.1:{MEMORY_SERVER_PORT}/internal/knowledge-library/embeddings/{suffix}"

    async def _call(self, method: str, suffix: str, payload: dict | None = None) -> dict:
        """Map transport failures to domain reasons, without logging raw text."""
        import httpx
        from utils.internal_http_client import get_internal_http_client

        try:
            response = await get_internal_http_client().request(
                method, self._url(suffix), json=payload, timeout=120.0,
            )
            result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LibraryError("embedding_unavailable") from exc
        if not isinstance(result, dict) or not result.get("ok"):
            reason = result.get("reason") if isinstance(result, dict) else None
            # Only the known small error vocabulary crosses the host boundary.
            raise LibraryError(reason if reason in {
                "embedding_unavailable", "embedding_incompatible", "embedding_failed", "embedding_busy", "input_token_limit",
            } else "embedding_unavailable")
        return result

    async def specification(self) -> dict:
        """Freeze actual encoder weights, tokenizer and processing identity."""
        result = await self._call("GET", "spec")
        spec = result.get("specification")
        if not isinstance(spec, dict):
            raise LibraryError("embedding_unavailable")
        self._spec = spec
        return dict(spec)

    async def embed_batch(self, texts: list[str], *, expected_spec: dict | None = None) -> list[list[float]]:
        """Use foreground admission for an interactive query."""
        return await self._embed_batch(texts, "foreground", expected_spec)

    async def embed_background(self, texts: list[str], *, expected_spec: dict | None = None) -> list[list[float]]:
        """Let indexing/build batches yield to queued memory and query work."""
        return await self._embed_batch(texts, "background", expected_spec)

    async def _embed_batch(self, texts: list[str], priority: str, expected_spec: dict | None) -> list[list[float]]:
        """Keep each RPC small; the server rejects identity drift per batch."""
        # A concurrent query may refresh the adapter cache. A task's explicit
        # identity always wins and cannot silently switch to that newer cache.
        spec = expected_spec if expected_spec is not None else self._spec or await self.specification()
        vectors = []
        for offset in range(0, len(texts), 16):
            batch = texts[offset:offset + 16]
            result = await self._call("POST", "batch", {"texts": batch, "specification": spec, "priority": priority})
            rows = result.get("vectors")
            if result.get("specification") != spec or not isinstance(rows, list) or len(rows) != len(batch):
                raise LibraryError("embedding_incompatible")
            vectors.extend(rows)
        return vectors

    async def plan_inputs(self, inputs: list[dict], *, expected_spec: dict | None = None) -> list[list[dict]]:
        """Plan complete character spans using the encoder owner's real tokenizer."""
        spec = expected_spec if expected_spec is not None else self._spec or await self.specification()
        planned = []
        for offset in range(0, len(inputs), 16):
            batch = inputs[offset:offset + 16]
            result = await self._call("POST", "plan", {"inputs": batch, "specification": spec})
            parts = result.get("parts")
            if result.get("specification") != spec or not isinstance(parts, list) or len(parts) != len(batch):
                raise LibraryError("embedding_incompatible")
            # Validate coverage at the process boundary, before any caller can
            # mistake missing source characters for a complete embedding input.
            for source, spans in zip(batch, parts):
                position = 0
                if not isinstance(spans, list) or not spans:
                    raise LibraryError("embedding_incompatible")
                for span in spans:
                    if (not isinstance(span, dict) or set(span) != {"start", "end"}
                            or type(span["start"]) is not int or type(span["end"]) is not int
                            or span["start"] != position or not position < span["end"] <= len(source["body"])):
                        raise LibraryError("embedding_incompatible")
                    position = span["end"]
                if position != len(source["body"]):
                    raise LibraryError("embedding_incompatible")
            planned.extend(parts)
        return planned


class LibraryHost:
    """Own one isolated service per app and protect every persisted mutation."""

    def __init__(self, config_manager) -> None:
        self.config_manager = config_manager
        self.root = library_root(config_manager).resolve()
        self.embedder = HttpEmbeddingAdapter()
        self._service = None
        self._ownership = None
        self._opening: asyncio.Task | None = None
        self._closing = False
        self._lock = asyncio.Lock()
        # Multipart parsers spool to disk, but concurrent uploads still consume
        # handles and buffers. Admission rejects excess requests before reading.
        self.upload_slots = asyncio.Semaphore(2)
        _hosts.add(self)

    @contextmanager
    def write_guard(self):
        """Recheck write authority and root immediately around disk commits."""
        from utils.cloudsave_runtime import MaintenanceModeError, cloudsave_writable_transaction

        try:
            with cloudsave_writable_transaction(
                self.config_manager, operation="save", target="knowledge_library",
            ):
                if library_root(self.config_manager).resolve() != self.root:
                    raise LibraryError("storage_root_changed")
                yield
        except MaintenanceModeError as error:
            # Reuse the existing maintenance gate while exposing only this
            # domain's stable retryable reason to the independent page.
            raise LibraryError("storage_busy") from error

    async def service(self):
        """Keep initialization owned even if its first HTTP caller disconnects."""
        if self._closing:
            raise LibraryError("service_stopping")
        if self._opening is None:
            # The host retains this task. Shielding its whole lifecycle avoids
            # losing a FileLock returned by a still-running acquisition thread
            # when the requesting coroutine is cancelled before assignment.
            self._opening = asyncio.create_task(self._open(), name="personal-library-start")
        task = self._opening
        try:
            return await asyncio.shield(task)
        except Exception:
            if self._opening is task:
                self._opening = None
            raise

    async def _open(self):
        """Start lazily on first use; ordinary application startup stays cheap."""
        async with self._lock:
            if self._service is None:
                from .service import LibraryService
                from filelock import FileLock, Timeout

                def acquire_owner():
                    # A process owns a domain until its workers drain. A second
                    # Main process must not concurrently recover the same jobs
                    # or replace manifests using only an in-process RLock.
                    with self.write_guard():
                        self.root.mkdir(parents=True, exist_ok=True)
                        owner = FileLock(str(self.root / "writer.lock"), thread_local=False)
                        try:
                            owner.acquire(timeout=0)
                        except Timeout as exc:
                            raise LibraryError("storage_busy") from exc
                        return owner

                self._ownership = await asyncio.to_thread(acquire_owner)
                candidate = LibraryService(self.root, embedder=self.embedder, write_guard=self.write_guard)
                try:
                    await candidate.start()
                    await asyncio.to_thread(self._reclaim_staging, candidate)
                except BaseException:
                    try:
                        await candidate.close()
                    finally:
                        await asyncio.to_thread(self._ownership.release)
                        self._ownership = None
                    raise
                self._service = candidate
            return self._service

    def _reclaim_staging(self, service) -> None:
        """Reclaim abandoned transfers after exclusive ownership and job recovery.

        Remote retries always download their frozen revision again. Offline
        retries keep the complete uploaded bytes while their task is unfinished.
        This never touches originals, snapshots, installed versions or artifacts.
        """
        from .package_validate import PACKAGE_SUFFIX

        retained = {
            record["frozen_input"].get("upload_id")
            for record in service.jobs.records.values()
            if record["kind"] == "install" and record["state"] != "completed"
        }
        with self.write_guard():
            staging = self.root / "staging"
            if staging.is_symlink():
                return
            for name in ("market", "imports"):
                directory = staging / name
                if not directory.is_dir() or directory.is_symlink():
                    continue
                for path in directory.glob("*" + PACKAGE_SUFFIX):
                    identifier = path.name[:-len(PACKAGE_SUFFIX)]
                    # Only this feature's generated 32-hex transfer names are
                    # cleanup candidates; unfamiliar files remain untouched.
                    if re.fullmatch(r"[0-9a-f]{32}", identifier) and (name == "market" or identifier not in retained):
                        if path.is_file() or path.is_symlink():
                            path.unlink()

    async def close(self) -> None:
        """Drain owned jobs before releasing this runtime's service."""
        self._closing = True
        admissions = tuple(getattr(self, "_market_publication_tasks", ()))
        if admissions:
            # A disconnected browser can leave a retained publication admission
            # verifying its author or persisting its first job. Keep root
            # ownership until these bounded tasks really stop submitting work.
            await asyncio.gather(*(asyncio.shield(task) for task in admissions), return_exceptions=True)
        if self._opening is not None:
            try:
                await asyncio.shield(self._opening)
            except Exception:
                pass  # Failed startup already released its acquired ownership.
        async with self._lock:
            if self._service is not None:
                await self._service.close()
                self._service = None
            if self._ownership is not None:
                await asyncio.to_thread(self._ownership.release)
                self._ownership = None
            self._opening = None


async def close_library_hosts() -> None:
    """Main shutdown hook, limited to hosts created by this feature."""
    await asyncio.gather(*(host.close() for host in tuple(_hosts)))
