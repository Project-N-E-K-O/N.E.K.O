"""Optional PNGTuber downloads from a shipped, pinned release catalog.

No client-supplied URLs or archives are accepted. Installs reuse the validated,
transactional PNGTuber unpacker and never change a character's active model.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from scripts.unpack_builtin_pngtuber import _archive_parts, unpack_model

_ACTIVE = {"downloading", "verifying", "installing", "cancelling"}
_DOWNLOAD_HOSTS = {
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
}


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in _DOWNLOAD_HOSTS:
            raise ValueError("Untrusted resource download redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class DownloadCancelled(Exception):
    pass


class PNGTuberDownloads:
    def __init__(self, catalog: Path, output_root: Path, *, open_url=None):
        self.catalog = catalog
        self.output_root = output_root
        self._open_url = open_url or build_opener(_SafeRedirect()).open
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._cancel: dict[str, threading.Event] = {}

    def entries(self):
        if not self.catalog.is_file():
            return []
        data = json.loads(self.catalog.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("models"), list):
            raise ValueError("Invalid optional model catalog")
        return data["models"]

    def entry(self, model_id):
        for row in self.entries():
            if row["id"] == model_id:
                return row
        raise ValueError("Unknown optional PNGTuber model")

    @staticmethod
    def installation_folder(row):
        package = row["package"]
        folder, checksum = package.get("folder"), package.get("archive_sha256")
        if not isinstance(folder, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", folder):
            raise ValueError("Invalid optional model folder")
        if not isinstance(checksum, str) or not re.fullmatch(
            r"[A-Fa-f0-9]{64}", checksum
        ):
            raise ValueError("Invalid optional model version checksum")
        # New versions never replace paths still being read by the current renderer.
        return folder + "-" + checksum.lower()[:16]

    def status(self, model_id):
        row = self.entry(model_id)
        with self._lock:
            job = dict(self._jobs.get(model_id, {}))
        installed_folder = self.installation_folder(row)
        installed = self.output_root / installed_folder / ".builtin-pack-sha256"
        if job.get("state") == "complete" and not installed.is_file():
            job = {}
        return {
            "id": model_id,
            "name": row["name"],
            "version": row["version"],
            "installed_folder": installed_folder,
            "published": row.get("published") is True,
            "download_size": sum(
                p["archive_size"] for p in _archive_parts(row["package"])
            ),
            "installed": installed.is_file(),
            "update_available": installed.is_file()
            and installed.read_text(encoding="utf-8").strip().lower()
            != row["package"]["archive_sha256"].lower(),
            "state": "idle",
            "received": 0,
            **job,
        }

    def start(self, model_id):
        row = self.entry(model_id)
        if row.get("published") is not True:
            raise ValueError("Optional model release has not been published yet")
        # Validate before starting the thread and before touching the network.
        self._release_url(row)
        parts = _archive_parts(row["package"])
        total = sum(p["archive_size"] for p in parts)
        with self._lock:
            if self._jobs.get(model_id, {}).get("state") in _ACTIVE:
                return False
            event = threading.Event()
            self._cancel[model_id] = event
            self._jobs[model_id] = {
                "state": "downloading",
                "received": 0,
                "total": total,
                "error": None,
            }
        threading.Thread(target=self._run, args=(row, event), daemon=True).start()
        return True

    def cancel(self, model_id):
        self.entry(model_id)
        with self._lock:
            job = self._jobs.get(model_id, {})
            if job.get("state") not in {"downloading", "verifying", "cancelling"}:
                return False
            job["state"] = "cancelling"
            self._cancel[model_id].set()
            return True

    @staticmethod
    def _release_url(row):
        base = row["release_url"].rstrip("/")
        parsed = urlsplit(base)
        pieces = parsed.path.strip("/").split("/")
        if (
            parsed.scheme != "https"
            or parsed.netloc != "github.com"
            or parsed.query
            or parsed.fragment
            or len(pieces) != 5
            or pieces[2:4] != ["releases", "download"]
            or pieces[4] == "latest"
            or any(p in {"", ".", ".."} for p in pieces)
        ):
            raise ValueError("Expected a pinned GitHub Release URL")
        return base

    def _update(self, model_id, **values):
        with self._lock:
            self._jobs[model_id].update(values)

    def _run(self, row, event):
        model_id = row["id"]
        package = dict(row["package"], folder=self.installation_folder(row))
        temporary = None
        outcome = {"state": "complete"}
        try:
            parts = _archive_parts(package)
            base = self._release_url(row)
            self.output_root.mkdir(parents=True, exist_ok=True)
            required = (
                sum(p["archive_size"] for p in parts)
                + package["unpacked_size"]
                + 32 * 1024 * 1024
            )
            if shutil.disk_usage(self.output_root).free < required:
                raise ValueError(
                    "Not enough disk space to download and safely install this model"
                )
            temporary = Path(
                tempfile.mkdtemp(
                    prefix=".pngtuber-download-", dir=self.output_root.parent
                )
            )
            received = 0
            for part in parts:
                if event.is_set():
                    raise DownloadCancelled()
                digest, count = hashlib.sha256(), 0
                request = Request(
                    base + "/" + part["archive"],
                    headers={
                        "User-Agent": "NEKO-PNGTuber",
                        "Accept-Encoding": "identity",
                    },
                )
                with (
                    self._open_url(request, timeout=20) as response,
                    (temporary / part["archive"]).open("wb") as target,
                ):
                    while True:
                        if event.is_set():
                            raise DownloadCancelled()
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > part["archive_size"]:
                            raise ValueError(
                                "Resource archive exceeds its declared size"
                            )
                        target.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        self._update(model_id, received=received)
                if (
                    count != part["archive_size"]
                    or digest.hexdigest() != part["archive_sha256"]
                ):
                    raise ValueError("Resource archive checksum or size mismatch")
            self._update(model_id, state="verifying")
            # Cancellation and entering the atomic install stage share one lock.
            with self._lock:
                if event.is_set():
                    raise DownloadCancelled()
                self._jobs[model_id]["state"] = "installing"
            unpack_model(package, temporary, self.output_root)
        except DownloadCancelled:
            outcome = {"state": "cancelled", "error": None}
        except Exception as error:
            outcome = {"state": "failed", "error": str(error)}
        finally:
            if temporary is not None:
                shutil.rmtree(temporary, ignore_errors=True)
            self._update(model_id, **outcome)
