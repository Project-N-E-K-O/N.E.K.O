"""Application-owned, pinned TSE download and streaming import transactions.

Only this application's release manifest is trusted. HTTP callers cannot choose
URLs, versions, archive members or filesystem paths. Large I/O, ZIP processing,
hashing and ORT validation are owned off-loop operations, joined before cleanup.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import threading
from typing import Any
from urllib.parse import urlsplit
import uuid
import zipfile

from main_logic.voice_identity.contracts import SpeakerModelIdentity


# Small application metadata, loaded once with the module; no model weights are
# read at import time. Packaging includes this file, never the optional weights.
RELEASE_MANIFEST = json.loads(Path(__file__).with_name("release_manifest.json").read_text(encoding="utf-8"))
RESOURCE_REVISION = RELEASE_MANIFEST["resource_revision"]
MODEL_IDENTITY = SpeakerModelIdentity(
    RELEASE_MANIFEST["model_id"], RELEASE_MANIFEST["model_revision"],
    RELEASE_MANIFEST["embedding_dimension"],
)
PREPROCESSING_REVISION = RELEASE_MANIFEST["preprocessing_revision"]
REFERENCE_METHOD = RELEASE_MANIFEST["reference_method"]
MAX_CHUNK_BYTES = 1024 * 1024
TOTAL_TIMEOUT_SECONDS = 30 * 60
IDLE_TIMEOUT_SECONDS = 30


class TseAssetError(ValueError):
    """Stable machine-readable error; raw network/path errors stay off the UI."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TseModelSnapshot:
    directory: Path
    model_identity: SpeakerModelIdentity = MODEL_IDENTITY
    resource_revision: str = RESOURCE_REVISION
    preprocessing_revision: str = PREPROCESSING_REVISION
    reference_method: str = REFERENCE_METHOD


def _check_live(stop: threading.Event) -> None:
    if stop.is_set():
        raise TseAssetError("tse_cancelled")


def _source_url(manifest: dict) -> str | None:
    source = manifest.get("source")
    if source is None:
        return None
    # This is release configuration, not an HTTP request parameter. Keep the
    # original revision URL so every request can resolve current CDN redirects.
    revision = source.get("revision", "")
    url = source.get("url", "")
    parsed = urlsplit(url)
    if (
        source.get("provider") != "modelscope"
        or not revision or revision.lower() in {"main", "master", "latest"}
        or revision not in url
        or parsed.scheme != "https"
        or parsed.hostname not in {"modelscope.cn", "www.modelscope.cn"}
        or parsed.username or parsed.password
    ):
        raise TseAssetError("tse_source_invalid")
    return url


def _hash_file(path: Path, expected: dict, stop: threading.Event) -> None:
    _check_live(stop)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != expected["bytes"]:
        raise TseAssetError("tse_integrity_error")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(MAX_CHUNK_BYTES):
            _check_live(stop)
            digest.update(block)
    if digest.hexdigest() != expected["sha256"]:
        raise TseAssetError("tse_integrity_error")


def _assert_shape(node: Any, expected: list) -> None:
    actual = node.shape
    if node.type != "tensor(float)" or len(actual) != len(expected):
        raise TseAssetError("tse_model_contract_error")
    for value, wanted in zip(actual, expected):
        if isinstance(wanted, int) and value != wanted:
            raise TseAssetError("tse_model_contract_error")
        if isinstance(wanted, str) and isinstance(value, int):
            raise TseAssetError("tse_model_contract_error")


def _validate_onnx(directory: Path, manifest: dict, stop: threading.Event) -> None:
    import numpy as np
    import onnxruntime as ort

    for filename, contract in manifest["onnx"].items():
        _check_live(stop)
        if contract.get("external_data") is not False:
            raise TseAssetError("tse_model_contract_error")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_cpu_mem_arena = False
        # _verify_directory already checked the exact application-owned hashes.
        # These two reviewed graphs were validated from serialized bytes with no
        # model directory and are self-contained (external_data: false). Loading
        # the same pinned bytes by path avoids a second 75.8 MB serialized copy;
        # do not generalize this contract to arbitrary ONNX files or manifests.
        session = ort.InferenceSession(str(directory / filename), sess_options=options, providers=["CPUExecutionProvider"])
        try:
            for actual, expected in ((session.get_inputs(), contract["inputs"]),
                                     (session.get_outputs(), contract["outputs"])):
                if {node.name for node in actual} != set(expected):
                    raise TseAssetError("tse_model_contract_error")
                for node in actual:
                    _assert_shape(node, expected[node.name])
            _check_live(stop)
            inputs = {
                name: np.zeros(tuple(98 if isinstance(dim, str) and name == "fbank" else
                                     1 if isinstance(dim, str) else dim for dim in shape), dtype=np.float32)
                for name, shape in contract["inputs"].items()
            }
            values = session.run(None, inputs)
            for node, value in zip(session.get_outputs(), values):
                if (value.dtype != np.float32
                        or list(value.shape) != contract["probe_outputs"][node.name]
                        or not np.isfinite(value).all()):
                    raise TseAssetError("tse_model_contract_error")
            del values, inputs
        finally:
            # Load the two encoders sequentially, never keep both ORT sessions.
            del session
    _check_live(stop)


def _verify_directory(directory: Path, manifest: dict, stop: threading.Event,
                      validate_models: Callable = _validate_onnx) -> TseModelSnapshot:
    _check_live(stop)
    if directory.is_symlink() or not directory.is_dir():
        raise TseAssetError("tse_integrity_error")
    if {path.name for path in directory.iterdir()} != set(manifest["files"]):
        raise TseAssetError("tse_integrity_error")
    for filename, expected in manifest["files"].items():
        _hash_file(directory / filename, expected, stop)
    bundle = json.loads((directory / "bundle.json").read_text(encoding="utf-8"))
    if any(bundle.get(key) != manifest[key] for key in (
        "resource_revision", "model_revision", "preprocessing_revision", "reference_method",
    )):
        raise TseAssetError("tse_model_contract_error")
    validate_models(directory, manifest, stop)
    _check_live(stop)
    return TseModelSnapshot(
        directory.resolve(),
        SpeakerModelIdentity(manifest["model_id"], manifest["model_revision"], manifest["embedding_dimension"]),
        manifest["resource_revision"], manifest["preprocessing_revision"], manifest["reference_method"],
    )


def _extract_archive(archive_path: Path, directory: Path, manifest: dict, stop: threading.Event) -> None:
    _hash_file(archive_path, manifest["archive"], stop)
    directory.mkdir(exist_ok=False)
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        expected = manifest["files"]
        # Names are a flat exact allowlist; do not call extract/extractall even
        # after a successful archive hash. Reject aliases and duplicates too.
        if len(entries) != len(expected) or {entry.filename for entry in entries} != set(expected):
            raise TseAssetError("tse_invalid_archive")
        expanded = 0
        for entry in entries:
            _check_live(stop)
            name = entry.filename
            mode = entry.external_attr >> 16
            if (
                PurePosixPath(name).name != name or "\\" in name or ":" in name
                or name in {".", ".."} or entry.is_dir()
                or (stat.S_IFMT(mode) not in {0, stat.S_IFREG})
                or entry.flag_bits & 1
                or entry.file_size != expected[name]["bytes"]
            ):
                raise TseAssetError("tse_invalid_archive")
            expanded += entry.file_size
            if expanded > sum(item["bytes"] for item in expected.values()):
                raise TseAssetError("tse_invalid_archive")
            digest, received = hashlib.sha256(), 0
            with archive.open(entry) as origin, (directory / name).open("xb") as target:
                while block := origin.read(MAX_CHUNK_BYTES):
                    _check_live(stop)
                    received += len(block)
                    if received > expected[name]["bytes"]:
                        raise TseAssetError("tse_invalid_archive")
                    target.write(block)
                    digest.update(block)
            if received != expected[name]["bytes"] or digest.hexdigest() != expected[name]["sha256"]:
                raise TseAssetError("tse_integrity_error")


def _discard_staging(root: Path, staging: Path) -> None:
    # Only this task's UUID directory is removable; never follow cache symlinks.
    if staging.is_symlink() or staging.resolve().parent != root.resolve() or not staging.name.startswith(".tse-"):
        raise TseAssetError("tse_invalid_staging_path")
    if staging.exists():
        shutil.rmtree(staging)


def _publish(directory: Path, root: Path, manifest: dict, stop: threading.Event,
             validate_models: Callable) -> TseModelSnapshot:
    destination = root / manifest["resource_revision"]
    if destination.resolve().parent != root.resolve() or destination.is_symlink():
        raise TseAssetError("tse_invalid_staging_path")
    _check_live(stop)
    if destination.exists():
        try:
            return _verify_directory(destination, manifest, stop, validate_models)
        except TseAssetError as exc:
            if exc.code == "tse_cancelled":
                raise
        # Explicit retry can repair a damaged cache. An intact immutable version
        # is never overwritten, and retirement belongs to this staging directory.
        retired = directory.parent / "retired"
        _check_live(stop)
        os.replace(destination, retired)
        try:
            _check_live(stop)
            os.replace(directory, destination)
        except BaseException:
            if not destination.exists():
                os.replace(retired, destination)
            raise
    else:
        _check_live(stop)
        try:
            os.replace(directory, destination)
        except OSError:
            if not destination.exists():
                raise
            # Another instance can win publication of the same immutable version.
            return _verify_directory(destination, manifest, stop, validate_models)
    return TseModelSnapshot(
        destination.resolve(),
        SpeakerModelIdentity(manifest["model_id"], manifest["model_revision"], manifest["embedding_dimension"]),
        manifest["resource_revision"], manifest["preprocessing_revision"], manifest["reference_method"],
    )


async def _owned_io(call, *args, **kwargs):
    """A canceled await does not abandon its thread and race cleanup against it.

    close() can return after its bounded wait; this task retains ownership until
    the native call finishes. Its stop event fences publication in the meantime.
    """
    task = asyncio.create_task(asyncio.to_thread(call, *args, **kwargs))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            cancelled = True
            if task.cancelled():
                raise
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        if hasattr(result, "close"):
            await _owned_io(result.close)
        raise asyncio.CancelledError
    return result


@asynccontextmanager
async def _response(client, url: str):
    import httpx

    target = httpx.URL(url)
    for _ in range(6):
        if target.scheme != "https" or target.username or target.password:
            raise TseAssetError("tse_source_invalid")
        async with client.stream("GET", target, follow_redirects=False) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise TseAssetError("tse_download_failed")
                target = response.url.join(location)
                continue
            yield response
            return
    raise TseAssetError("tse_download_failed")


class TseAssets:
    """One app-owned operation and immutable verified snapshot, across UI windows."""

    def __init__(self, directory: Path, *, client_factory=None,
                 _manifest: dict | None = None, _validate_models: Callable = _validate_onnx) -> None:
        self.directory = Path(directory)
        self._manifest = _manifest if _manifest is not None else RELEASE_MANIFEST
        self._validate_models = _validate_models
        self._client_factory = client_factory
        self._url = _source_url(self._manifest)
        self._state = "missing"
        self._received = 0
        self._error: str | None = None
        self._closed = False
        self._generation = 0
        self._snapshot: TseModelSnapshot | None = None
        self._task: asyncio.Task | None = None
        self._stop = threading.Event()

    @property
    def ready(self) -> bool:
        return not self._closed and self._snapshot is not None

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def snapshot(self) -> TseModelSnapshot | None:
        return self._snapshot if self.ready else None

    def status(self) -> dict[str, object]:
        return {
            "state": self._state, "downloaded_bytes": self._received,
            "total_bytes": self._manifest["archive"]["bytes"],
            "resource_revision": self._manifest["resource_revision"],
            "model_revision": self._manifest["model_revision"],
            "source_configured": self._url is not None,
            "installed": self.ready, "busy": self.busy,
            "error_code": self._error,
        }

    async def initialize(self) -> None:
        if self._closed or self.busy:
            return
        self._generation += 1
        generation, stop = self._generation, self._stop
        self._state = "verifying"
        task = asyncio.create_task(self._initialize(generation, stop), name="voice-identity-tse-initialize")
        self._task = task
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            stop.set()
            task.cancel()
            raise

    async def _initialize(self, generation: int, stop: threading.Event) -> None:
        try:
            snapshot = await _owned_io(
                _verify_directory, self.directory / self._manifest["resource_revision"],
                self._manifest, stop, self._validate_models,
            )
        except Exception:
            snapshot = None
        if self._current(generation, stop):
            self._snapshot = snapshot
            self._state = "ready" if snapshot is not None else "missing"

    def _begin(self) -> tuple[int, threading.Event]:
        if self._closed:
            raise TseAssetError("tse_assets_closed")
        self._generation += 1
        self._stop = threading.Event()
        self._state, self._received, self._error = "downloading", 0, None
        return self._generation, self._stop

    def _current(self, generation: int, stop: threading.Event) -> bool:
        return not self._closed and self._generation == generation and self._stop is stop and not stop.is_set()

    def start(self) -> dict[str, object]:
        if self._closed:
            raise TseAssetError("tse_assets_closed")
        if self.busy or self.ready:
            return self.status()
        if self._url is None:
            raise TseAssetError("tse_source_unconfigured")
        generation, stop = self._begin()
        self._task = asyncio.create_task(self._run(generation, stop), name="voice-identity-tse-download")
        return self.status()

    async def import_stream(self, chunks: AsyncIterable[bytes]) -> dict[str, object]:
        if self.busy:
            raise TseAssetError("tse_assets_busy")
        generation, stop = self._begin()
        task = asyncio.create_task(self._run(generation, stop, chunks), name="voice-identity-tse-import")
        self._task = task
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Until import returns, the upload request owns this iterator. It may
            # no longer be read after client disconnect or route cancellation.
            stop.set()
            task.cancel()
            if self._generation == generation and not self._closed:
                self._state, self._error = "failed", "tse_cancelled"
            raise
        return self.status()

    async def _receive(self, chunks: AsyncIterable[bytes], path: Path,
                       generation: int, stop: threading.Event) -> None:
        handle = await _owned_io(path.open, "xb")
        total = self._manifest["archive"]["bytes"]
        iterator = chunks.__aiter__()
        try:
            while True:
                try:
                    block = await asyncio.wait_for(anext(iterator), IDLE_TIMEOUT_SECONDS)
                except StopAsyncIteration:
                    break
                _check_live(stop)
                if not self._current(generation, stop):
                    raise TseAssetError("tse_cancelled")
                if not isinstance(block, bytes):
                    raise TseAssetError("tse_invalid_archive")
                if self._received + len(block) > total:
                    raise TseAssetError("tse_integrity_error")
                # Starlette/transport chunks can exceed the write size. Slice
                # them without making a second whole-upload buffer.
                view = memoryview(block)
                for offset in range(0, len(view), MAX_CHUNK_BYTES):
                    _check_live(stop)
                    part = view[offset:offset + MAX_CHUNK_BYTES]
                    await _owned_io(handle.write, part)
                    if not self._current(generation, stop):
                        raise TseAssetError("tse_cancelled")
                    self._received += len(part)
            if self._received != total:
                raise TseAssetError("tse_integrity_error")
        finally:
            await _owned_io(handle.close)

    async def _download(self, path: Path, generation: int, stop: threading.Event) -> None:
        import httpx

        factory = self._client_factory or (
            lambda: httpx.AsyncClient(timeout=httpx.Timeout(30, connect=15), trust_env=False)
        )
        async with factory() as client:
            async with _response(client, self._url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type in {"text/html", "application/json"}:
                    raise TseAssetError("tse_invalid_archive")
                length = response.headers.get("content-length")
                if length is not None and int(length) != self._manifest["archive"]["bytes"]:
                    raise TseAssetError("tse_integrity_error")
                await self._receive(response.aiter_bytes(MAX_CHUNK_BYTES), path, generation, stop)

    async def _run(self, generation: int, stop: threading.Event,
                   chunks: AsyncIterable[bytes] | None = None) -> None:
        staging = self.directory / (".tse-" + uuid.uuid4().hex)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + TOTAL_TIMEOUT_SECONDS
        # Cancellation alone cannot interrupt _owned_io. Retire publication at
        # the actual deadline, even while its native operation is still running.
        deadline_handle = loop.call_at(deadline, stop.set)
        try:
            async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
                await _owned_io(staging.mkdir, parents=True, exist_ok=False)
                archive, extracted = staging / "download.zip", staging / "bundle"
                if chunks is None:
                    await self._download(archive, generation, stop)
                else:
                    await self._receive(chunks, archive, generation, stop)
                if not self._current(generation, stop):
                    _check_live(stop)
                    return
                self._state = "verifying"
                await _owned_io(_extract_archive, archive, extracted, self._manifest, stop)
                await _owned_io(_verify_directory, extracted, self._manifest, stop, self._validate_models)
                if not self._current(generation, stop):
                    _check_live(stop)
                    return
                self._state = "installing"
                snapshot = await _owned_io(_publish, extracted, self.directory, self._manifest, stop, self._validate_models)
                if self._current(generation, stop):
                    self._snapshot, self._state, self._error = snapshot, "ready", None
        except asyncio.CancelledError:
            stop.set()
            if self._generation == generation and not self._closed:
                self._state, self._error = "failed", "tse_cancelled"
            raise
        except Exception as exc:
            if not self._closed and self._generation == generation and self._stop is stop:
                self._state = "failed"
                self._error = ("tse_timeout" if loop.time() >= deadline else
                               exc.code if isinstance(exc, TseAssetError) else
                               "tse_timeout" if isinstance(exc, TimeoutError) else
                               "tse_download_failed" if chunks is None else "tse_import_failed")
            stop.set()
        finally:
            deadline_handle.cancel()
            # Staging belongs to this operation even if a later operation takes
            # over. No global glob/delete can remove another task's files.
            try:
                await _owned_io(_discard_staging, self.directory, staging)
            except OSError:
                # Disk failure may leave a private .tse-* directory; it cannot be
                # recognized as a usable resource version at startup.
                pass

    async def close(self, timeout: float = 5.0) -> None:
        self._closed = True
        self._generation += 1
        self._snapshot = None
        self._stop.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            # Native filesystem/ORT calls cannot be killed by coroutine cancel.
            # They keep task-owned cleanup; publication is fenced by _stop.
            done, _ = await asyncio.wait({self._task}, timeout=max(0.0, timeout))
            for task in done:
                if not task.cancelled():
                    task.exception()
