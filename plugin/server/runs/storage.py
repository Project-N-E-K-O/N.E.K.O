from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from plugin.settings import BLOB_STORE_DIR, BLOB_UPLOAD_MAX_BYTES


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
            except Exception:
                pass

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

    def finalize_upload(self, upload_id: str) -> Optional[UploadSession]:
        with self._lock:
            sess = self._uploads.get(str(upload_id))
            if sess is None:
                return None
            self._uploads.pop(str(upload_id), None)
        try:
            if sess.final_path.exists():
                return sess
            if sess.tmp_path.exists():
                os.replace(str(sess.tmp_path), str(sess.final_path))
        except Exception:
            return sess
        return sess

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
