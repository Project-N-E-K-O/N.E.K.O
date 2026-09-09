"""Bundled pVAD and explicitly downloaded ECAPA assets."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from main_logic.voice_identity.contracts import SpeakerModelIdentity

ECAPA_REVISION = "a9cb9321b07b4ee5b0ea47fdd25242d9cacd824a"
ECAPA_IDENTITY = SpeakerModelIdentity("speechbrain/spkrec-ecapa-voxceleb", ECAPA_REVISION, 192)
ECAPA_PREPROCESSING_REVISION = "speechbrain-fbank-zero-pad-fft400-hop160-cmn-v1"
ECAPA_REFERENCE_METHOD = "camplus-validated-holdout-up-to-5s-v1"
ECAPA_RESOURCE_REVISION = "ecapa-a9cb9321-f46380bb-fbank024e5073-v1"
_ECAPA_ROOT = (
    "https://huggingface.co/vedk00/ecapa-voxceleb-speaker-embedding-onnx/resolve/"
    + ECAPA_REVISION + "/model/"
)


@dataclass(frozen=True, slots=True)
class Asset:
    filename: str
    size: int
    sha256: str
    url: str


PVAD = Asset(
    "pvad.onnx", 3940567,
    "2114fd3c3fa87b560eaf4cad6a6e1a0a73aefba08da05521a27bfe2382ef4bdd",
    "https://huggingface.co/FireRedTeam/FireRedChat-pvad/resolve/main/pvad.onnx",
)
ECAPA_ASSETS = (
    Asset("ecapa-speaker-v1.onnx", 83476039,
          "f46380bbaeddb929fb3a10ab63a4b1877a50e3d1e5fdd55a1b618d5651d3f64e",
          _ECAPA_ROOT + "ecapa-speaker-v1.onnx"),
    Asset("fbank-80x201-f32.bin", 64320,
          "024e5073b7cfedee84408dc68dd6bafa02808fc786e67f1314e9c918297f5a63",
          _ECAPA_ROOT + "fbank-80x201-f32.bin"),
)


@dataclass(frozen=True, slots=True)
class EcapaModelSnapshot:
    """One fully verified immutable bundle captured before enrollment starts."""

    directory: Path
    model_identity: SpeakerModelIdentity = ECAPA_IDENTITY
    resource_revision: str = ECAPA_RESOURCE_REVISION
    preprocessing_revision: str = ECAPA_PREPROCESSING_REVISION
    reference_method: str = ECAPA_REFERENCE_METHOD


def _bundle_manifest() -> dict:
    return {
        "resource_revision": ECAPA_RESOURCE_REVISION,
        "preprocessing_revision": ECAPA_PREPROCESSING_REVISION,
        "files": {asset.filename: asset.sha256 for asset in ECAPA_ASSETS},
    }


def _verify_bundle(directory: Path) -> EcapaModelSnapshot:
    if (directory / "bundle.json").stat().st_size > 4096:
        raise ValueError("invalid_ecapa_bundle")
    if json.loads((directory / "bundle.json").read_text(encoding="utf-8")) != _bundle_manifest():
        raise ValueError("invalid_ecapa_bundle")
    for asset in ECAPA_ASSETS:
        verify_asset(directory, asset)
    # These hashes identify the reviewed self-contained ONNX and bank in the
    # compatibility report. Do not load the 83MB enrollment model at startup or
    # download completion; its session contract is rechecked in the extractor.
    return EcapaModelSnapshot(directory.resolve())


def verify_asset(directory: Path, asset: Asset) -> Path:
    path = directory / asset.filename
    if path.stat().st_size != asset.size:
        raise ValueError("asset_size_mismatch")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != asset.sha256:
        raise ValueError("asset_checksum_mismatch")
    return path


def bundled_pvad() -> Path:
    return verify_asset(Path(__file__).resolve().parent / "models", PVAD)


def ecapa_available(directory: Path) -> bool:
    try:
        _verify_bundle(Path(directory) / ECAPA_RESOURCE_REVISION)
    except Exception:
        return False
    return True


class EcapaDownload:
    """Own one cancellable download; status reads never touch disk or network."""

    def __init__(self, directory: Path, *, client_factory=None) -> None:
        self.directory = Path(directory)
        self._client_factory = client_factory
        self._state = "missing"
        self._received = 0
        self._error: str | None = None
        self._task: asyncio.Task | None = None
        self._closed = False
        self._snapshot: EcapaModelSnapshot | None = None

    async def initialize(self) -> None:
        try:
            snapshot = await asyncio.to_thread(
                _verify_bundle, self.directory / ECAPA_RESOURCE_REVISION,
            )
        except Exception:
            snapshot = None
        if not self._closed and self._task is None:
            self._snapshot = snapshot
            self._state = "ready" if snapshot is not None else "missing"

    @property
    def ready(self) -> bool:
        return not self._closed and self._state == "ready" and self._snapshot is not None

    def snapshot(self) -> EcapaModelSnapshot | None:
        return self._snapshot if self.ready else None

    def status(self) -> dict[str, object]:
        return {"state": self._state, "downloaded_bytes": self._received,
                "total_bytes": sum(asset.size for asset in ECAPA_ASSETS),
                "model_revision": ECAPA_REVISION,
                "resource_revision": ECAPA_RESOURCE_REVISION,
                "error_code": self._error}

    def start(self) -> dict[str, object]:
        if self._closed:
            raise RuntimeError("download_closed")
        if self.ready or (self._task is not None and not self._task.done()):
            return self.status()
        self._state, self._received, self._error = "downloading", 0, None
        self._task = asyncio.create_task(self._download(), name="voice-identity-ecapa-download")
        return self.status()

    async def _download(self) -> None:
        import httpx

        staging = self.directory / (".ecapa-" + uuid.uuid4().hex + ".part")
        try:
            await _finish_io(staging.mkdir, parents=True, exist_ok=False)
            factory = self._client_factory or (
                lambda: httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30, connect=15))
            )
            async with asyncio.timeout(600), factory() as client:
                for asset in ECAPA_ASSETS:
                    path = staging / asset.filename
                    handle = await _finish_io(path.open, "xb")
                    digest, received = hashlib.sha256(), 0
                    try:
                        async with _asset_response(client, asset.url) as response:
                            response.raise_for_status()
                            async for block in response.aiter_bytes(1024 * 1024):
                                received += len(block)
                                if received > asset.size:
                                    raise ValueError("download_integrity_error")
                                # Wait for each write before cancellation can close its handle.
                                await _finish_io(handle.write, block)
                                digest.update(block)
                                self._received += len(block)
                        if received != asset.size or digest.hexdigest() != asset.sha256:
                            raise ValueError("download_integrity_error")
                    finally:
                        await _finish_io(handle.close)
                self._state = "verifying"
                await _finish_io(
                    (staging / "bundle.json").write_text,
                    json.dumps(_bundle_manifest(), sort_keys=True), encoding="utf-8",
                )
                await _finish_io(_verify_bundle, staging)
                destination = self.directory / ECAPA_RESOURCE_REVISION
                await _finish_io(_publish_bundle, staging, destination)
                if not self._closed:
                    self._snapshot = EcapaModelSnapshot(destination.resolve())
                    self._state = "ready"
        except asyncio.CancelledError:
            self._state = "missing"
            raise
        except Exception as exc:
            self._state = "failed"
            self._error = "download_integrity_error" if isinstance(exc, ValueError) else "download_failed"
        finally:
            await _finish_io(_discard_staging, self.directory, staging)

    async def close(self) -> None:
        self._closed = True
        self._snapshot = None
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


async def _finish_io(call, *args, **kwargs):
    """Join owned filesystem work before its handle/path may be cleaned up."""
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
            # Repeated cancellation must not abandon the same native write/open.
            continue
    if cancelled:
        if hasattr(result, "close"):
            await _finish_io(result.close)
        raise asyncio.CancelledError
    return result


@asynccontextmanager
async def _asset_response(client, url: str):
    """Check every redirect before making a request, including CDN hops."""
    import httpx

    target = httpx.URL(url)
    for _ in range(6):
        if target.scheme != "https":
            raise ValueError("download_insecure_redirect")
        async with client.stream("GET", target, follow_redirects=False) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("download_invalid_redirect")
                target = response.url.join(location)
                continue
            yield response
            return
    raise ValueError("download_redirect_limit")


def _publish_bundle(staging: Path, destination: Path) -> None:
    if destination.exists():
        # A concurrent publisher may have won. Never replace its immutable files.
        try:
            _verify_bundle(destination)
            return
        except (OSError, ValueError):
            # A damaged cache must be repairable by an explicit retry. Only a
            # bundle which fails integrity is retired; verified snapshots remain
            # untouched. Both moves stay within this task's model-cache root.
            if destination.resolve().parent != staging.resolve().parent:
                raise ValueError("invalid_ecapa_publication_path")
            retired = destination.parent / (".ecapa-" + uuid.uuid4().hex + ".part")
            os.replace(destination, retired)
            try:
                os.replace(staging, destination)
            except BaseException:
                if not destination.exists():
                    os.replace(retired, destination)
                raise
            _discard_staging(destination.parent, retired)
            return
    try:
        os.replace(staging, destination)
    except OSError:
        if not destination.exists():
            raise
        _verify_bundle(destination)


def _discard_staging(root: Path, staging: Path) -> None:
    resolved = staging.resolve()
    if resolved.parent != root.resolve() or not resolved.name.startswith(".ecapa-"):
        raise ValueError("invalid_ecapa_staging_path")
    if resolved.exists():
        shutil.rmtree(resolved)
