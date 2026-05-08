from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from plugin.logging_config import logger

from plugin.settings import (
    BLOB_STORE_DIR,
    BLOB_UPLOAD_MAX_BYTES,
    BLOB_UPLOAD_SESSION_TTL_SECONDS,
)


class UploadNotFoundError(RuntimeError):
    """Raised when a finalize targets a missing or expired upload session."""

    def __init__(self, upload_id: str, *, reason: str = "") -> None:
        self.upload_id = upload_id
        super().__init__(f"upload {upload_id} not found: {reason}" if reason else f"upload {upload_id} not found")


@dataclass(frozen=True)
class UploadSession:
    upload_id: str
    run_id: str
    blob_id: str
    filename: Optional[str]
    mime: Optional[str]
    created_at: float
    max_bytes: int
    tmp_path: Path
    final_path: Path


class BlobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._uploads: Dict[str, UploadSession] = {}
        self._blob_to_run: Dict[str, str] = {}
        self._upload_ttl_seconds = max(1.0, float(BLOB_UPLOAD_SESSION_TTL_SECONDS))
        self._janitor_stop = threading.Event()
        self._janitor_interval_seconds = min(60.0, max(5.0, self._upload_ttl_seconds / 4.0))
        self._janitor_thread = threading.Thread(
            target=self._janitor_loop,
            daemon=True,
            name="blob-upload-janitor",
        )
        self._janitor_thread.start()

    def _janitor_loop(self) -> None:
        while not self._janitor_stop.wait(self._janitor_interval_seconds):
            try:
                self.cleanup_expired_uploads()
            except Exception:
                logger.debug("blob upload janitor error", exc_info=True)

    def cleanup_expired_uploads(self) -> int:
        deadline = float(time.time()) - self._upload_ttl_seconds
        expired: list[UploadSession] = []
        with self._lock:
            for upload_id, sess in list(self._uploads.items()):
                if float(sess.created_at) > deadline:
                    continue
                # Skip sessions whose tmp file was recently written to (still active).
                try:
                    if sess.tmp_path.exists() and sess.tmp_path.stat().st_mtime > deadline:
                        continue
                except OSError:
                    pass
                expired.append(sess)
                self._uploads.pop(upload_id, None)
                if self._blob_to_run.get(sess.blob_id) == sess.run_id:
                    self._blob_to_run.pop(sess.blob_id, None)

        for sess in expired:
            for path in (sess.tmp_path, sess.final_path):
                try:
                    if path.exists():
                        path.unlink()
                except FileNotFoundError:
                    pass
                except Exception:
                    logger.warning("Failed to remove expired blob {}: {}", path, sess.upload_id, exc_info=True)
        return len(expired)

    def _ensure_dirs(self) -> Path:
        p = Path(str(BLOB_STORE_DIR)).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def create_upload(self, *, run_id: str, filename: Optional[str], mime: Optional[str], max_bytes: Optional[int]) -> UploadSession:
        base = self._ensure_dirs()
        upload_id = str(uuid.uuid4())
        blob_id = upload_id
        created_at = float(time.time())
        limit = int(BLOB_UPLOAD_MAX_BYTES)
        if max_bytes is not None:
            try:
                mb = int(max_bytes)
                if mb > 0:
                    limit = min(limit, mb)
            except (ValueError, TypeError) as e:
                raise ValueError(f"invalid max_bytes: {max_bytes}") from e

        tmp_path = base / f"{blob_id}.upload"
        final_path = base / f"{blob_id}.blob"

        sess = UploadSession(
            upload_id=upload_id,
            run_id=str(run_id),
            blob_id=blob_id,
            filename=str(filename) if isinstance(filename, str) and filename else None,
            mime=str(mime) if isinstance(mime, str) and mime else None,
            created_at=created_at,
            max_bytes=limit,
            tmp_path=tmp_path,
            final_path=final_path,
        )
        with self._lock:
            self._uploads[upload_id] = sess
            self._blob_to_run[blob_id] = str(run_id)
        return sess

    def get_upload(self, upload_id: str) -> Optional[UploadSession]:
        with self._lock:
            return self._uploads.get(str(upload_id))

    def finalize_upload(self, upload_id: str) -> UploadSession:
        upload_id = str(upload_id)
        with self._lock:
            sess = self._uploads.get(upload_id)
            if sess is None:
                raise UploadNotFoundError(upload_id, reason="session not found (expired or never created)")
            if sess.final_path.exists():
                if self._uploads.get(upload_id) == sess:
                    self._uploads.pop(upload_id, None)
                return sess
            if sess.tmp_path.exists():
                os.replace(str(sess.tmp_path), str(sess.final_path))
                if self._uploads.get(upload_id) == sess:
                    self._uploads.pop(upload_id, None)
                return sess
            logger.warning("finalize_upload: both paths missing for upload_id={}", upload_id)
            if self._uploads.get(upload_id) == sess:
                self._uploads.pop(upload_id, None)
            if self._blob_to_run.get(sess.blob_id) == sess.run_id:
                self._blob_to_run.pop(sess.blob_id, None)
            raise UploadNotFoundError(upload_id, reason="both tmp and final paths missing")

    def get_blob_path(self, *, run_id: str, blob_id: str) -> Optional[Path]:
        rid = str(run_id)
        bid = str(blob_id)
        with self._lock:
            owner = self._blob_to_run.get(bid)
        if owner != rid:
            return None
        base = self._ensure_dirs()
        p = base / f"{bid}.blob"
        if not p.exists():
            return None
        return p


blob_store = BlobStore()
