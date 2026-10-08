"""Local chat display resources, independent of model previews and cloud assets.

Callers serialize commits with the character and cloud-save mutation fences.
All functions here are synchronous and belong in a worker thread.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import re
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from utils.config_manager.reserved_schema import is_valid_character_uid
from utils.file_utils import atomic_write_text

SOURCE_MAX_BYTES = 10 * 1024 * 1024
SOURCE_MAX_PIXELS = 20_000_000
NORMALIZED_SIZE = 320
NORMALIZED_MAX_BYTES = 1024 * 1024
STORAGE_QUOTA_BYTES = 64 * 1024 * 1024
INITIAL_REVISION = "0"
MAX_RECORD_BYTES = ((NORMALIZED_MAX_BYTES + 2) // 3) * 4 + 4096
PNG_DATA_PREFIX = "data:image/png;base64,"
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


class ChatAvatarError(Exception):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code = code
        self.status = status


def limits() -> dict:
    return {
        "source_max_bytes": SOURCE_MAX_BYTES,
        "source_max_pixels": SOURCE_MAX_PIXELS,
        "normalized_size": NORMALIZED_SIZE,
        "normalized_max_bytes": NORMALIZED_MAX_BYTES,
        "storage_quota_bytes": STORAGE_QUOTA_BYTES,
    }


def validate_uid(uid: str) -> str:
    if not is_valid_character_uid(uid):
        raise ChatAvatarError("chat_avatar_invalid_request", 400)
    return uid


def validate_operation(base_revision, operation_id) -> tuple[str, str]:
    if not all(isinstance(v, str) and _TOKEN_RE.fullmatch(v) for v in (base_revision, operation_id)):
        raise ChatAvatarError("chat_avatar_invalid_request", 400)
    return base_revision, operation_id


def normalize_png(payload: bytes) -> str:
    """Fully decode the submitted static 320px PNG and strip all metadata."""
    if len(payload) > NORMALIZED_MAX_BYTES:
        raise ChatAvatarError("chat_avatar_too_large", 413)
    try:
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != "PNG" or image.size != (NORMALIZED_SIZE, NORMALIZED_SIZE) or image.n_frames != 1:
                raise ChatAvatarError("chat_avatar_invalid_image", 422)
            image.verify()
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            rgba = image.convert("RGBA")
            output = io.BytesIO()
            rgba.save(output, format="PNG")
            normalized = output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ChatAvatarError("chat_avatar_invalid_image", 422) from exc
    if len(normalized) > NORMALIZED_MAX_BYTES:
        raise ChatAvatarError("chat_avatar_too_large", 413)
    return PNG_DATA_PREFIX + base64.b64encode(normalized).decode("ascii")


def record_path(directory: Path, uid: str) -> Path:
    return Path(directory) / f"{validate_uid(uid)}.json"


def empty_record(uid: str) -> dict:
    return {"schema_version": 1, "character_uid": validate_uid(uid), "revision": INITIAL_REVISION,
            "data_url": None, "last_operation_id": None}


def read_record(directory: Path, uid: str) -> dict:
    path = record_path(directory, uid)
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_RECORD_BYTES + 1)
    except FileNotFoundError:
        # A broken symlink is a damaged record, rather than an absent avatar.
        if path.is_symlink():
            raise ChatAvatarError("chat_avatar_record_corrupt", 500)
        return empty_record(uid)
    except OSError as exc:
        raise ChatAvatarError("chat_avatar_read_failed", 503) from exc
    try:
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError("oversized record")
        record = json.loads(raw)
        if (not isinstance(record, dict) or record.get("schema_version") != 1
                or record.get("character_uid") != uid
                or not isinstance(record.get("revision"), str)
                or not _TOKEN_RE.fullmatch(record["revision"])
                or record["revision"] == INITIAL_REVISION
                or not isinstance(record.get("last_operation_id"), str)
                or not _TOKEN_RE.fullmatch(record["last_operation_id"])):
            raise ValueError("invalid record schema")
        data_url = record.get("data_url")
        if data_url is not None:
            if not isinstance(data_url, str) or not data_url.startswith(PNG_DATA_PREFIX):
                raise ValueError("invalid image encoding")
            payload = base64.b64decode(data_url[len(PNG_DATA_PREFIX):], validate=True)
            # Decode again to distinguish a valid JSON wrapper from corrupt image bytes.
            normalize_png(payload)
        if "data_url" not in record:
            raise ValueError("missing image")
        return {key: record[key] for key in empty_record(uid)}
    except (ValueError, TypeError, KeyError, RecursionError, binascii.Error, ChatAvatarError) as exc:
        raise ChatAvatarError("chat_avatar_record_corrupt", 500) from exc


def write_record(directory: Path, uid: str, data_url: str | None,
                 base_revision: str, operation_id: str) -> dict:
    validate_operation(base_revision, operation_id)
    current = read_record(directory, uid)
    if current["last_operation_id"] == operation_id:
        if current["data_url"] == data_url:
            return current
        raise ChatAvatarError("chat_avatar_conflict", 409)
    if current["revision"] != base_revision:
        raise ChatAvatarError("chat_avatar_conflict", 409)
    record = {"schema_version": 1, "character_uid": uid, "revision": uuid.uuid4().hex,
              "data_url": data_url, "last_operation_id": operation_id}
    encoded = json.dumps(record, ensure_ascii=True, separators=(",", ":"))
    directory = Path(directory)
    target = record_path(directory, uid)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        total = sum(entry.stat().st_size for entry in directory.iterdir()
                    if entry.name != target.name and entry.suffix == ".json")
        if total + len(encoded.encode("utf-8")) > STORAGE_QUOTA_BYTES:
            raise ChatAvatarError("chat_avatar_quota_exceeded", 507)
        atomic_write_text(target, encoded, encoding="utf-8")
    except OSError as exc:
        raise ChatAvatarError("chat_avatar_write_failed", 503) from exc
    return record


def remove_record(directory: Path, uid: str) -> None:
    """Only used after the character deletion transaction has committed."""
    try:
        record_path(directory, uid).unlink(missing_ok=True)
    except OSError as exc:
        raise ChatAvatarError("chat_avatar_write_failed", 503) from exc
