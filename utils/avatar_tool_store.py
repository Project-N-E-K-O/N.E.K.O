# -*- coding: utf-8 -*-
"""Authoritative local store for user-created avatar tools.

The browser only receives the public runtime projection returned by ``list_items``.
Interaction meanings stay in ``record.json`` and are read by Python when handling a
validated local interaction.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import threading
import unicodedata
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from PIL import Image, UnidentifiedImageError

from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.file_utils import atomic_write_json


LOCAL_AVATAR_TOOL_ID_PATTERN = re.compile(
    r"^local-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
LOCAL_AVATAR_TOOL_UPLOAD_PATTERN = re.compile(
    r"^\.local-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.uploading$"
)
LOCAL_AVATAR_TOOL_UPDATE_PATTERN = re.compile(
    r"^\.(local-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.updating$"
)
LOCAL_AVATAR_TOOL_BACKUP_PATTERN = re.compile(
    r"^\.(local-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.backup$"
)
LOCAL_AVATAR_TOOL_DELETING_PATTERN = re.compile(
    r"^\.(local-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\.deleting$"
)
PUBLIC_AVATAR_TOOL_FIXED_RESOURCE_NAMES = frozenset(
    {"default.png", "normal.mp3", "special.png", "special.mp3"}
)
PUBLIC_AVATAR_TOOL_CHANGE_RESOURCE_PATTERN = re.compile(r"^change-[0-9]{3}\.png$")
LOCAL_AVATAR_TOOL_CHANGE_MODES = frozenset({"press-swap", "click-advance"})

AVATAR_TOOL_LIMITS: dict[str, int] = {
    "maxTools": 64,
    "maxNameChars": 20,
    "maxMeaningChars": 100,
    "maxChangeImages": 16,
    "maxImageBytes": 8 * 1024 * 1024,
    "maxImagePixels": 16_000_000,
    "maxAudioBytes": 5 * 1024 * 1024,
    "maxAudioDurationMs": 10_000,
    "maxTotalBytes": 256 * 1024 * 1024,
}

# A create/update request can contain the default and surprise images in
# addition to every change image, plus normal/surprise audio. Leave bounded
# room for multipart headers and short text fields without weakening per-file
# validation in the router/store.
AVATAR_TOOL_MAX_MULTIPART_BODY_BYTES = (
    (AVATAR_TOOL_LIMITS["maxChangeImages"] + 2)
    * AVATAR_TOOL_LIMITS["maxImageBytes"]
    + 2 * AVATAR_TOOL_LIMITS["maxAudioBytes"]
    + 1024 * 1024
)

# record.json 最坏情况：16 条 change item（meaning 各 100 字符）、special、
# 20 条资源摘要，按 indent=2 落盘也就十几 KB。给到 64 KiB 是四倍余量，同时
# 让同步盘冲突或磁盘损坏产生的畸形大文件在读进内存之前就被拦下 —— list_items
# 会对每个道具读一遍 record，而前端每次窗口聚焦都会拉列表。
AVATAR_TOOL_MAX_RECORD_BYTES = 64 * 1024

_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MEANING_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x09\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_NAME_SPACES_PATTERN = re.compile(r" +")
_REVISION_PATTERN = re.compile(r"^[0-9]+-[0-9]+$")
_RESOURCE_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_STORE_LOCK = threading.RLock()
_RECOVERY_PENDING_ROOTS: set[str] = set()
# 消费点逐字节校验时发现内容与 record 摘要不符的道具，按 store root 分组。
# 由 quarantine() 写入，成功的 create/update/delete 解除单个道具的隔离；
# 进程内状态，重启即重新评估。
_QUARANTINED_TOOL_IDS: dict[str, set[str]] = {}
logger = logging.getLogger(__name__)


class AvatarToolStoreError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        field: str | None = None,
        index: int | None = None,
        integrity_mismatch: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.field = field
        self.index = index
        # 只有「读到了字节、但和 record 里的摘要对不上」才置位。OSError 这类
        # 瞬时失败不算，否则一次文件占用就会把好道具永久隔离。
        self.integrity_mismatch = integrity_mismatch


def is_local_avatar_tool_id(value: object) -> bool:
    return isinstance(value, str) and LOCAL_AVATAR_TOOL_ID_PATTERN.fullmatch(value) is not None


def is_public_avatar_tool_resource_path(root: Path | str, path: object) -> bool:
    """Return whether an HTTP path names one published, non-symlink resource."""
    pure_path = PurePosixPath(str(path or ""))
    if pure_path.is_absolute() or len(pure_path.parts) != 2:
        return False
    tool_id, filename = pure_path.parts
    if (
        not is_local_avatar_tool_id(tool_id)
        or (
            filename not in PUBLIC_AVATAR_TOOL_FIXED_RESOURCE_NAMES
            and PUBLIC_AVATAR_TOOL_CHANGE_RESOURCE_PATTERN.fullmatch(filename) is None
        )
    ):
        return False
    root_path = Path(root)
    directory = root_path / tool_id
    candidate = directory / filename
    if root_path.is_symlink() or directory.is_symlink() or candidate.is_symlink():
        return False
    try:
        candidate.resolve().relative_to(root_path.resolve())
    except (OSError, ValueError):
        return False
    return candidate.is_file()


def _validate_name(value: object, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise AvatarToolStoreError("name_required", "name is required", field="name")
    if _CONTROL_CHARACTER_PATTERN.search(value):
        raise AvatarToolStoreError("name_invalid", "name contains unsupported characters", field="name")
    normalized = _NAME_SPACES_PATTERN.sub(" ", unicodedata.normalize("NFC", value).strip())
    if not normalized:
        raise AvatarToolStoreError("name_required", "name is required", field="name")
    if len(normalized) > maximum:
        raise AvatarToolStoreError("name_too_long", "name is too long", field="name")
    for character in normalized:
        category = unicodedata.category(character)
        if character in {" ", "-", "_"} or category[0] in {"L", "M", "N"}:
            continue
        raise AvatarToolStoreError("name_invalid", "name contains unsupported characters", field="name")
    return normalized


def _validate_meaning(
    value: object,
    *,
    field: str,
    maximum: int,
    index: int | None = None,
) -> str:
    if not isinstance(value, str):
        raise AvatarToolStoreError(f"{field}_required", f"{field} is required", field=field, index=index)
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise AvatarToolStoreError(f"{field}_required", f"{field} is required", field=field, index=index)
    if len(normalized) > maximum:
        raise AvatarToolStoreError(f"{field}_too_long", f"{field} is too long", field=field, index=index)
    if _MEANING_CONTROL_CHARACTER_PATTERN.search(normalized):
        raise AvatarToolStoreError(f"{field}_invalid", f"{field} contains control characters", field=field, index=index)
    return normalized


def _validate_resource(
    validator,
    data: bytes,
    *,
    limits: dict[str, int],
    field: str,
    index: int | None = None,
) -> bytes:
    try:
        return validator(data, limits=limits)
    except AvatarToolStoreError as exc:
        raise AvatarToolStoreError(
            exc.code,
            str(exc),
            status_code=exc.status_code,
            field=field,
            index=index,
        ) from exc


def _validate_probability(value: object, *, field: str | None = None) -> float:
    if isinstance(value, bool):
        raise AvatarToolStoreError("special_probability_invalid", "Special probability is invalid", field=field)
    try:
        probability = float(value)
    except (TypeError, ValueError) as exc:
        raise AvatarToolStoreError(
            "special_probability_invalid",
            "Special probability is invalid",
            field=field,
        ) from exc
    if not math.isfinite(probability) or probability <= 0 or probability > 1:
        raise AvatarToolStoreError("special_probability_invalid", "Special probability is invalid", field=field)
    return probability


def _decode_static_png(data: bytes, *, limits: dict[str, int]) -> bytes:
    if not data:
        raise AvatarToolStoreError("image_required", "PNG image is required")
    if len(data) > limits["maxImageBytes"]:
        raise AvatarToolStoreError("image_too_large", "PNG image is too large", status_code=413)

    try:
        with Image.open(io.BytesIO(data)) as verify_image:
            if verify_image.format != "PNG":
                raise AvatarToolStoreError("image_not_png", "Image must be a real PNG")
            frame_count = int(getattr(verify_image, "n_frames", 1) or 1)
            if frame_count != 1 or bool(getattr(verify_image, "is_animated", False)):
                raise AvatarToolStoreError("image_animated", "Animated PNG is not supported")
            width, height = verify_image.size
            if width <= 0 or height <= 0 or width * height > limits["maxImagePixels"]:
                raise AvatarToolStoreError("image_pixels_exceeded", "PNG dimensions are too large")
            verify_image.verify()

        with Image.open(io.BytesIO(data)) as decoded:
            if decoded.format != "PNG":
                raise AvatarToolStoreError("image_not_png", "Image must be a real PNG")
            decoded.load()
            rgba = decoded.convert("RGBA")
            alpha = rgba.getchannel("A")
            if alpha.getbbox() is None:
                raise AvatarToolStoreError("image_fully_transparent", "PNG cannot be fully transparent")
            output = io.BytesIO()
            rgba.save(output, format="PNG", optimize=True)
            canonical = output.getvalue()
            if len(canonical) > limits["maxImageBytes"]:
                raise AvatarToolStoreError(
                    "image_too_large",
                    "PNG image is too large",
                    status_code=413,
                )
            return canonical
    except AvatarToolStoreError:
        raise
    except Image.DecompressionBombError as exc:
        raise AvatarToolStoreError("image_pixels_exceeded", "PNG dimensions are too large") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AvatarToolStoreError("image_decode_failed", "PNG could not be decoded") from exc


def _validate_mp3(data: bytes, *, limits: dict[str, int]) -> bytes:
    if not data:
        raise AvatarToolStoreError("audio_required", "MP3 audio is required")
    if len(data) > limits["maxAudioBytes"]:
        raise AvatarToolStoreError("audio_too_large", "MP3 audio is too large", status_code=413)

    try:
        import av
    except ImportError as exc:
        raise AvatarToolStoreError(
            "audio_validation_unavailable",
            "MP3 validation is unavailable",
            status_code=503,
        ) from exc

    try:
        with av.open(io.BytesIO(data), mode="r") as container:
            format_names = set(str(container.format.name or "").lower().split(","))
            if "mp3" not in format_names:
                raise AvatarToolStoreError("audio_not_mp3", "Audio must be a real MP3")
            audio_streams = [stream for stream in container.streams if stream.type == "audio"]
            if not audio_streams:
                raise AvatarToolStoreError("audio_stream_missing", "MP3 must contain an audio stream")

            decoded_frames = 0
            duration_ms = 0.0
            for frame in container.decode(audio_streams[0]):
                decoded_frames += 1
                sample_rate = int(frame.sample_rate or 0)
                samples = int(frame.samples or 0)
                if sample_rate > 0 and samples > 0:
                    duration_ms += samples * 1000 / sample_rate
                elif frame.duration is not None and frame.time_base is not None:
                    duration_ms += float(frame.duration * frame.time_base) * 1000
                if duration_ms > limits["maxAudioDurationMs"]:
                    raise AvatarToolStoreError("audio_too_long", "MP3 audio is too long", status_code=413)
            if decoded_frames == 0 or duration_ms <= 0:
                raise AvatarToolStoreError("audio_decode_failed", "MP3 could not be decoded")
    except AvatarToolStoreError:
        raise
    except Exception as exc:
        raise AvatarToolStoreError("audio_decode_failed", "MP3 could not be decoded") from exc
    return data


def _validate_special_mp3(data: bytes, *, limits: dict[str, int]) -> bytes:
    try:
        return _validate_mp3(data, limits=limits)
    except AvatarToolStoreError as exc:
        raise AvatarToolStoreError(
            f"special_{exc.code}",
            str(exc),
            status_code=exc.status_code,
        ) from exc


class AvatarToolStore:
    def __init__(self, config_manager: Any):
        self.config_manager = config_manager
        self.root = Path(config_manager.avatar_tools_dir)
        self.limits = dict(AVATAR_TOOL_LIMITS)

    def _root_key(self) -> str:
        return os.path.normcase(os.path.abspath(self.root))

    def _ensure_directory(self) -> None:
        if not self.config_manager.ensure_avatar_tools_directory():
            raise AvatarToolStoreError(
                "avatar_tools_directory_unavailable",
                "Avatar tool storage is unavailable",
                status_code=503,
            )

    def ensure(self) -> None:
        with _STORE_LOCK:
            root_key = self._root_key()
            recovery_pending = root_key in _RECOVERY_PENDING_ROOTS
            if recovery_pending:
                assert_cloudsave_writable(
                    self.config_manager,
                    operation="recover",
                    target="avatar_tools",
                )
            self._ensure_directory()
            if not recovery_pending:
                return
            try:
                self._recover_interrupted_mutations()
            except OSError as exc:
                raise AvatarToolStoreError(
                    "avatar_tools_directory_unavailable",
                    "Avatar tool storage is unavailable",
                    status_code=503,
                ) from exc
            _RECOVERY_PENDING_ROOTS.discard(root_key)

    def initialize(self) -> None:
        """Prepare the store once and recover interrupted mutations."""
        with _STORE_LOCK:
            root_key = self._root_key()
            try:
                assert_cloudsave_writable(
                    self.config_manager,
                    operation="recover",
                    target="avatar_tools",
                )
            except MaintenanceModeError:
                _RECOVERY_PENDING_ROOTS.add(root_key)
                return
            try:
                self._ensure_directory()
                self._recover_interrupted_mutations()
            except AvatarToolStoreError:
                _RECOVERY_PENDING_ROOTS.add(root_key)
                raise
            except OSError as exc:
                _RECOVERY_PENDING_ROOTS.add(root_key)
                raise AvatarToolStoreError(
                    "avatar_tools_directory_unavailable",
                    "Avatar tool storage is unavailable",
                    status_code=503,
                ) from exc
            _RECOVERY_PENDING_ROOTS.discard(root_key)

    def quarantine(self, tool_id: str) -> None:
        # 消费点（详情页、静态资源、互动）逐字节校验时发现内容和 record 里的
        # 摘要对不上，就地把这个道具从公开目录摘掉，不必等重启。启动不做全量
        # 复核：那会给每次冷启动加上 O(总字节数) 的开销，而作者原来的启动路径
        # 一个文件都不 hash。
        if is_local_avatar_tool_id(tool_id):
            _QUARANTINED_TOOL_IDS.setdefault(self._root_key(), set()).add(tool_id)

    def _release_quarantine(self, tool_id: str) -> None:
        quarantined = _QUARANTINED_TOOL_IDS.get(self._root_key())
        if quarantined is not None:
            quarantined.discard(tool_id)

    def _recover_interrupted_mutations(self) -> None:
        def remove_owned_directory(directory: Path) -> None:
            if directory.is_symlink() or not directory.is_dir():
                return
            try:
                shutil.rmtree(directory)
            except FileNotFoundError:
                return

        candidates = list(self.root.iterdir())
        tool_ids = {
            match.group(1)
            for candidate in candidates
            for pattern in (LOCAL_AVATAR_TOOL_UPDATE_PATTERN, LOCAL_AVATAR_TOOL_BACKUP_PATTERN)
            if (match := pattern.fullmatch(candidate.name)) is not None
        }
        for tool_id in tool_ids:
            final = self.root / tool_id
            updating = self.root / f".{tool_id}.updating"
            backup = self.root / f".{tool_id}.backup"
            if final.is_dir() and not final.is_symlink():
                try:
                    self._read_record_from_directory(
                        tool_id,
                        final,
                        verify_resources=True,
                    )
                except AvatarToolStoreError:
                    pass
                else:
                    remove_owned_directory(updating)
                    remove_owned_directory(backup)
                    continue
            if backup.is_dir() and not backup.is_symlink():
                try:
                    self._read_record_from_directory(
                        tool_id,
                        backup,
                        verify_resources=True,
                    )
                except AvatarToolStoreError:
                    pass
                else:
                    if final.is_symlink() or final.is_file():
                        final.unlink()
                    elif final.is_dir():
                        shutil.rmtree(final)
                    os.replace(backup, final)
                    remove_owned_directory(updating)
                    continue
            remove_owned_directory(updating)

        for candidate in self.root.iterdir():
            if not (
                LOCAL_AVATAR_TOOL_UPLOAD_PATTERN.fullmatch(candidate.name)
                or LOCAL_AVATAR_TOOL_DELETING_PATTERN.fullmatch(candidate.name)
            ):
                continue
            if candidate.is_symlink() or not candidate.is_dir():
                continue
            remove_owned_directory(candidate)

    def read_record(
        self,
        tool_id: str,
        *,
        verify_resources: bool = False,
    ) -> dict[str, Any]:
        with _STORE_LOCK:
            if self._root_key() in _RECOVERY_PENDING_ROOTS:
                self.ensure()
            try:
                return self._read_record_from_directory(
                    tool_id,
                    self.root / tool_id,
                    verify_resources=verify_resources,
                )
            except AvatarToolStoreError as exc:
                if exc.integrity_mismatch:
                    logger.warning(
                        "Quarantining local avatar tool %s: %s", tool_id, exc
                    )
                    self.quarantine(tool_id)
                raise

    def _read_record_from_directory(
        self,
        tool_id: str,
        directory: Path,
        *,
        verify_resources: bool = False,
    ) -> dict[str, Any]:
        if not is_local_avatar_tool_id(tool_id):
            raise AvatarToolStoreError("invalid_tool_id", "Invalid local avatar tool ID")
        path = directory / "record.json"
        if path.is_symlink() or not path.is_file():
            raise AvatarToolStoreError("tool_not_found", "Avatar tool does not exist", status_code=404)
        try:
            # 有界读取：畸形的多 GB record 只会被读走 64 KiB + 1 字节就出局，
            # 不会在列表刷新／详情／启动恢复时把内存吃光。
            with path.open("rb") as stream:
                raw = stream.read(AVATAR_TOOL_MAX_RECORD_BYTES + 1)
            if len(raw) > AVATAR_TOOL_MAX_RECORD_BYTES:
                raise AvatarToolStoreError(
                    "record_invalid",
                    "Avatar tool record is invalid",
                    status_code=404,
                )
            payload = json.loads(raw.decode("utf-8"))
        except AvatarToolStoreError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404) from exc
        return self._validate_record(
            payload,
            expected_id=tool_id,
            directory=directory,
            verify_resources=verify_resources,
        )

    def _validate_record(
        self,
        payload: object,
        *,
        expected_id: str,
        directory: Path | None = None,
        verify_resources: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {
            "recordVersion", "id", "name", "defaultImage", "imageChange",
            "interaction", "resourceDigests",
        }:
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
        if payload.get("recordVersion") != 2 or payload.get("id") != expected_id:
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
        name = _validate_name(payload.get("name"), maximum=self.limits["maxNameChars"])
        default_image = payload.get("defaultImage")
        image_change = payload.get("imageChange")
        interaction = payload.get("interaction")
        resource_digests = payload.get("resourceDigests")
        if default_image != "default.png":
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
        if not isinstance(image_change, dict) or set(image_change) != {"mode", "items"}:
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
        mode = image_change.get("mode")
        items = image_change.get("items")
        if mode not in LOCAL_AVATAR_TOOL_CHANGE_MODES or not isinstance(items, list):
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
        if not 1 <= len(items) <= self.limits["maxChangeImages"]:
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
        if mode == "press-swap" and len(items) != 1:
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
        clean_items: list[dict[str, str]] = []
        for index, item in enumerate(items):
            expected_image = f"change-{index:03d}.png"
            if not isinstance(item, dict) or set(item) != {"image", "meaning"}:
                raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
            if item.get("image") != expected_image:
                raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
            clean_items.append({
                "image": expected_image,
                "meaning": _validate_meaning(
                    item.get("meaning"),
                    field="meaning",
                    maximum=self.limits["maxMeaningChars"],
                ),
            })
        if not isinstance(interaction, dict) or not set(interaction).issubset({"normalSound", "special"}):
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
        normal_sound = interaction.get("normalSound")
        if "normalSound" in interaction and normal_sound is None:
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
        if normal_sound is not None and normal_sound != "normal.mp3":
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
        special = interaction.get("special")
        if "special" in interaction and special is None:
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
        clean_special = None
        if special is not None:
            if not isinstance(special, dict) or set(special) not in (
                {"probability", "image", "meaning"},
                {"probability", "image", "meaning", "sound"},
            ):
                raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
            if special.get("image") != "special.png":
                raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
            special_sound = special.get("sound")
            if "sound" in special and special_sound != "special.mp3":
                raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
            special_probability = special.get("probability")
            if isinstance(special_probability, bool) or not isinstance(special_probability, (int, float)):
                raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
            clean_special = {
                "probability": _validate_probability(special_probability),
                "image": "special.png",
                "meaning": _validate_meaning(
                    special.get("meaning"),
                    field="special_meaning",
                    maximum=self.limits["maxMeaningChars"],
                ),
                **({"sound": "special.mp3"} if special_sound else {}),
            }
        directory = directory or self.root / expected_id
        if directory.is_symlink() or not directory.is_dir():
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
        resource_names = ["default.png", *(item["image"] for item in clean_items)]
        if normal_sound:
            resource_names.append(normal_sound)
        if clean_special:
            resource_names.append(clean_special["image"])
            if clean_special.get("sound"):
                resource_names.append(clean_special["sound"])
        if (
            not isinstance(resource_digests, dict)
            or set(resource_digests) != set(resource_names)
            or any(
                not isinstance(digest, str)
                or _RESOURCE_DIGEST_PATTERN.fullmatch(digest) is None
                for digest in resource_digests.values()
            )
        ):
            raise AvatarToolStoreError(
                "record_invalid",
                "Avatar tool resource integrity is invalid",
                status_code=404,
            )
        for filename in resource_names:
            resource = directory / filename
            if resource.is_symlink() or not resource.is_file():
                raise AvatarToolStoreError("record_invalid", "Avatar tool resource is invalid", status_code=404)
            if verify_resources:
                try:
                    actual_digest = self._file_digest(
                        resource,
                        self.limits["maxAudioBytes"]
                        if filename.endswith(".mp3")
                        else self.limits["maxImageBytes"],
                    )
                except AvatarToolStoreError:
                    raise
                except OSError as exc:
                    raise AvatarToolStoreError(
                        "record_invalid",
                        "Avatar tool resource integrity is invalid",
                        status_code=404,
                    ) from exc
                if actual_digest != resource_digests[filename]:
                    raise AvatarToolStoreError(
                        "record_invalid",
                        "Avatar tool resource integrity is invalid",
                        status_code=404,
                        integrity_mismatch=True,
                    )
        expected_entries = {"record.json", *resource_names}
        try:
            actual_entries = set()
            for entry in directory.iterdir():
                # 之前只把普通文件计入集合，于是同步盘或手工改动塞进来的子目录、
                # 符号链接会被无声忽略，闭包照样判过。
                if entry.is_symlink() or not entry.is_file():
                    raise AvatarToolStoreError(
                        "record_invalid",
                        "Avatar tool resource closure is invalid",
                        status_code=404,
                    )
                actual_entries.add(entry.name)
        except AvatarToolStoreError:
            raise
        except OSError as exc:
            raise AvatarToolStoreError("record_invalid", "Avatar tool resource is invalid", status_code=404) from exc
        if actual_entries != expected_entries:
            raise AvatarToolStoreError("record_invalid", "Avatar tool resource closure is invalid", status_code=404)
        return {
            "recordVersion": 2,
            "id": expected_id,
            "name": name,
            "defaultImage": "default.png",
            "imageChange": {"mode": mode, "items": clean_items},
            "interaction": {
                **({"normalSound": normal_sound} if normal_sound else {}),
                **({"special": clean_special} if clean_special else {}),
            },
            "resourceDigests": {
                filename: resource_digests[filename]
                for filename in resource_names
            },
        }

    @staticmethod
    def _file_digest(path: Path, maximum: int) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            # 在已打开的 fd 上预检，没有额外 syscall 也没有 TOCTOU。资源被外部
            # 换成多 GB 文件时，这里会一路读到 EOF —— 而且全程持有 _STORE_LOCK，
            # 会把其它 store 操作一起卡住。
            if os.fstat(stream.fileno()).st_size > maximum:
                raise AvatarToolStoreError(
                    "record_invalid",
                    "Avatar tool resource integrity is invalid",
                    status_code=404,
                    integrity_mismatch=True,
                )
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def record_revision(record: dict[str, Any]) -> str:
        encoded = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).digest()
        return f"2-{int.from_bytes(digest, 'big')}"

    @staticmethod
    def _asset_url(record: dict[str, Any], filename: str) -> str:
        digest = record["resourceDigests"][filename]
        return f"/user_avatar_tools/{record['id']}/{filename}?v={digest}"

    def _public_item(self, record: dict[str, Any]) -> dict[str, Any]:
        tool_id = record["id"]
        item = {
            "id": tool_id,
            "revision": self.record_revision(record),
            "name": record["name"],
            "changeMode": record["imageChange"]["mode"],
            "defaultUrl": self._asset_url(record, record["defaultImage"]),
            "changeUrls": [
                self._asset_url(record, item["image"])
                for item in record["imageChange"]["items"]
            ],
        }
        normal_sound = record["interaction"].get("normalSound")
        if normal_sound:
            item["normalSoundUrl"] = self._asset_url(record, normal_sound)
        special = record["interaction"].get("special")
        if special:
            item["special"] = {
                "probability": special["probability"],
                "imageUrl": self._asset_url(record, special["image"]),
                **(
                    {
                        "soundUrl": self._asset_url(record, special["sound"])
                    }
                    if special.get("sound")
                    else {}
                ),
            }
        return item

    def get_detail(self, tool_id: str) -> dict[str, Any]:
        with _STORE_LOCK:
            record = self.read_record(tool_id, verify_resources=True)
            detail = {
                "id": tool_id,
                "revision": self.record_revision(record),
                "name": record["name"],
                "changeMode": record["imageChange"]["mode"],
                "defaultImage": {
                    "resource": record["defaultImage"],
                    "url": self._asset_url(record, record["defaultImage"]),
                },
                "changeItems": [
                    {
                        "resource": item["image"],
                        "url": self._asset_url(record, item["image"]),
                        "meaning": item["meaning"],
                    }
                    for item in record["imageChange"]["items"]
                ],
            }
            normal_sound = record["interaction"].get("normalSound")
            if normal_sound:
                detail["normalSound"] = {
                    "resource": normal_sound,
                    "url": self._asset_url(record, normal_sound),
                }
            special = record["interaction"].get("special")
            if special:
                detail["special"] = {
                    "probability": special["probability"],
                    "image": {
                        "resource": special["image"],
                        "url": self._asset_url(record, special["image"]),
                    },
                    "meaning": special["meaning"],
                    **(
                        {
                            "sound": {
                                "resource": special["sound"],
                                "url": self._asset_url(record, special["sound"]),
                            }
                        }
                        if special.get("sound")
                        else {}
                    ),
                }
            return detail

    def list_items(self) -> list[dict[str, Any]]:
        with _STORE_LOCK:
            self.ensure()
            items: list[dict[str, Any]] = []
            try:
                candidates = sorted(self.root.iterdir(), key=lambda item: item.name)
            except OSError as exc:
                raise AvatarToolStoreError(
                    "avatar_tools_directory_unavailable",
                    "Avatar tool storage is unavailable",
                    status_code=503,
                ) from exc
            quarantined = _QUARANTINED_TOOL_IDS.get(self._root_key(), frozenset())
            for candidate in candidates:
                if candidate.is_symlink() or not candidate.is_dir() or not is_local_avatar_tool_id(candidate.name):
                    continue
                if candidate.name in quarantined:
                    logger.warning(
                        "Skipping quarantined local avatar tool %s", candidate.name
                    )
                    continue
                try:
                    # 这里只做轻量校验（记录形状、资源存在、闭包一致），不重算
                    # digest —— 前端每次 window focus 都会拉列表，逐字节核验放在
                    # 真正消费资源的地方，发现不符再由 quarantine() 摘掉。
                    record = self.read_record(
                        candidate.name,
                        verify_resources=False,
                    )
                    items.append(self._public_item(record))
                except (AvatarToolStoreError, OSError) as exc:
                    logger.warning("Skipping invalid local avatar tool %s: %s", candidate.name, exc)
                    continue
            return items

    def _current_storage_bytes(self) -> int:
        total = 0
        for directory in self.root.iterdir():
            is_published = is_local_avatar_tool_id(directory.name)
            is_pending_delete = LOCAL_AVATAR_TOOL_DELETING_PATTERN.fullmatch(directory.name) is not None
            is_update_backup = LOCAL_AVATAR_TOOL_BACKUP_PATTERN.fullmatch(directory.name) is not None
            if (
                directory.is_symlink()
                or not directory.is_dir()
                or not (is_published or is_pending_delete or is_update_backup)
            ):
                continue
            for entry in directory.iterdir():
                if entry.is_file() and not entry.is_symlink():
                    total += entry.stat().st_size
        return total

    def delete_tool(self, tool_id: str) -> str:
        if not is_local_avatar_tool_id(tool_id):
            raise AvatarToolStoreError("invalid_tool_id", "Invalid local avatar tool ID")

        with _STORE_LOCK:
            recovery_pending = self._root_key() in _RECOVERY_PENDING_ROOTS
            if recovery_pending:
                assert_cloudsave_writable(
                    self.config_manager,
                    operation="delete",
                    target=f"avatar_tools/{tool_id}",
                )
                self.ensure()
            directory = self.root / tool_id
            if directory.is_symlink() or not directory.is_dir():
                raise AvatarToolStoreError(
                    "tool_not_found",
                    "Avatar tool does not exist",
                    status_code=404,
                )
            try:
                root = self.root.resolve(strict=True)
                target = directory.resolve(strict=True)
            except OSError as exc:
                raise AvatarToolStoreError(
                    "tool_not_found",
                    "Avatar tool does not exist",
                    status_code=404,
                ) from exc
            if target.parent != root:
                raise AvatarToolStoreError("invalid_tool_path", "Invalid local avatar tool path")

            if not recovery_pending:
                assert_cloudsave_writable(
                    self.config_manager,
                    operation="delete",
                    target=f"avatar_tools/{tool_id}",
                )
            deleting = self.root / f".{tool_id}.deleting"
            if deleting.is_symlink() or deleting.exists():
                raise AvatarToolStoreError(
                    "tool_delete_failed",
                    "Avatar tool could not be deleted",
                    status_code=500,
                )
            backup = self.root / f".{tool_id}.backup"
            if backup.is_symlink() or (backup.exists() and not backup.is_dir()):
                raise AvatarToolStoreError(
                    "tool_delete_failed",
                    "Avatar tool could not be deleted",
                    status_code=500,
                )
            if backup.is_dir():
                try:
                    shutil.rmtree(backup)
                except OSError as exc:
                    raise AvatarToolStoreError(
                        "tool_delete_failed",
                        "Avatar tool could not be deleted",
                        status_code=500,
                    ) from exc
            try:
                os.replace(target, deleting)
            except FileNotFoundError as exc:
                raise AvatarToolStoreError(
                    "tool_not_found",
                    "Avatar tool does not exist",
                    status_code=404,
                ) from exc
            except OSError as exc:
                raise AvatarToolStoreError(
                    "tool_delete_failed",
                    "Avatar tool could not be deleted",
                    status_code=500,
                ) from exc
            try:
                shutil.rmtree(deleting)
            except OSError:
                # 与 _cleanup_failed_staging 对齐：残留的 .deleting 目录仍被
                # _current_storage_bytes 计入，不登记恢复的话这份字节数在本
                # 进程生命周期内再也要不回来，用户只会看到 storage_limit_reached。
                _RECOVERY_PENDING_ROOTS.add(self._root_key())
                logger.warning("Could not clean deleted avatar tool %s", deleting)
            self._release_quarantine(tool_id)
            return tool_id

    def _prepare_tool_contents(
        self,
        *,
        tool_id: str,
        name: str,
        change_mode: str,
        change_meanings: list[str],
        default_image: bytes,
        change_images: list[bytes],
        normal_sound: bytes | None,
        special_probability: object | None,
        special_image: bytes | None,
        special_meaning: str | None,
        special_sound: bytes | None,
    ) -> tuple[dict[str, Any], dict[str, bytes]]:
        clean_name = _validate_name(name, maximum=self.limits["maxNameChars"])
        if change_mode not in LOCAL_AVATAR_TOOL_CHANGE_MODES:
            raise AvatarToolStoreError("change_mode_invalid", "Image change mode is invalid")
        if len(change_images) != len(change_meanings):
            raise AvatarToolStoreError("change_items_mismatch", "Images and meanings must match")
        if not 1 <= len(change_images) <= self.limits["maxChangeImages"]:
            raise AvatarToolStoreError("change_items_invalid", "Image change item count is invalid")
        if change_mode == "press-swap" and len(change_images) != 1:
            raise AvatarToolStoreError("change_items_invalid", "Press-swap requires one change image")
        clean_meanings = [
            _validate_meaning(
                meaning,
                field="change_meaning",
                maximum=self.limits["maxMeaningChars"],
                index=index,
            )
            for index, meaning in enumerate(change_meanings)
        ]
        resources = {
            "default.png": _validate_resource(
                _decode_static_png,
                default_image,
                limits=self.limits,
                field="default_image",
            ),
            **{
                f"change-{index:03d}.png": _validate_resource(
                    _decode_static_png,
                    image,
                    limits=self.limits,
                    field="change_image",
                    index=index,
                )
                for index, image in enumerate(change_images)
            },
        }
        if normal_sound is not None:
            resources["normal.mp3"] = _validate_resource(
                _validate_mp3,
                normal_sound,
                limits=self.limits,
                field="normal_sound",
            )

        special_values = (special_probability, special_image, special_meaning, special_sound)
        special_enabled = any(value is not None for value in special_values)
        clean_special = None
        if special_enabled:
            if special_probability is None:
                raise AvatarToolStoreError(
                    "special_probability_required",
                    "Special probability is required",
                    field="special_probability",
                )
            if special_image is None:
                raise AvatarToolStoreError(
                    "special_image_required",
                    "Special image is required",
                    field="special_image",
                )
            if special_meaning is None:
                raise AvatarToolStoreError(
                    "special_meaning_required",
                    "Special meaning is required",
                    field="special_meaning",
                )
            resources["special.png"] = _validate_resource(
                _decode_static_png,
                special_image,
                limits=self.limits,
                field="special_image",
            )
            clean_special = {
                "probability": _validate_probability(special_probability, field="special_probability"),
                "image": "special.png",
                "meaning": _validate_meaning(
                    special_meaning,
                    field="special_meaning",
                    maximum=self.limits["maxMeaningChars"],
                ),
                **({"sound": "special.mp3"} if special_sound is not None else {}),
            }
            if special_sound is not None:
                resources["special.mp3"] = _validate_resource(
                    _validate_special_mp3,
                    special_sound,
                    limits=self.limits,
                    field="special_sound",
                )

        record = {
            "recordVersion": 2,
            "id": tool_id,
            "name": clean_name,
            "defaultImage": "default.png",
            "imageChange": {
                "mode": change_mode,
                "items": [
                    {"image": f"change-{index:03d}.png", "meaning": meaning}
                    for index, meaning in enumerate(clean_meanings)
                ],
            },
            "interaction": {
                **({"normalSound": "normal.mp3"} if normal_sound is not None else {}),
                **({"special": clean_special} if clean_special else {}),
            },
            "resourceDigests": {
                filename: hashlib.sha256(data).hexdigest()
                for filename, data in resources.items()
            },
        }
        return record, resources

    @staticmethod
    def _directory_bytes(directory: Path) -> int:
        return sum(
            entry.stat().st_size
            for entry in directory.iterdir()
            if entry.is_file() and not entry.is_symlink()
        )

    def _write_staged_tool(
        self,
        directory: Path,
        record: dict[str, Any],
        resources: dict[str, bytes],
    ) -> None:
        directory.mkdir(mode=0o700)
        for filename, data in resources.items():
            (directory / filename).write_bytes(data)
        atomic_write_json(directory / "record.json", record, ensure_ascii=False, indent=2)
        self._read_record_from_directory(
            record["id"],
            directory,
            verify_resources=True,
        )

    def _cleanup_failed_staging(self, directory: Path) -> None:
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            return
        except OSError:
            _RECOVERY_PENDING_ROOTS.add(self._root_key())
            logger.warning("Could not clean failed avatar tool staging directory %s", directory)

    def create_tool(
        self,
        *,
        tool_id: str,
        name: str,
        change_mode: str,
        change_meanings: list[str],
        default_image: bytes,
        change_images: list[bytes],
        normal_sound: bytes | None = None,
        special_probability: object | None = None,
        special_image: bytes | None = None,
        special_meaning: str | None = None,
        special_sound: bytes | None = None,
    ) -> dict[str, Any]:
        if not is_local_avatar_tool_id(tool_id):
            raise AvatarToolStoreError("invalid_tool_id", "Invalid local avatar tool ID")
        record, resources = self._prepare_tool_contents(
            tool_id=tool_id,
            name=name,
            change_mode=change_mode,
            change_meanings=change_meanings,
            default_image=default_image,
            change_images=change_images,
            normal_sound=normal_sound,
            special_probability=special_probability,
            special_image=special_image,
            special_meaning=special_meaning,
            special_sound=special_sound,
        )
        with _STORE_LOCK:
            assert_cloudsave_writable(
                self.config_manager,
                operation="create",
                target="avatar_tools",
            )
            self.ensure()
            final = self.root / tool_id
            if final.exists():
                current = self.read_record(tool_id, verify_resources=True)
                if current != record:
                    raise AvatarToolStoreError(
                        "tool_id_conflict",
                        "Local avatar tool ID already belongs to a different creation",
                        status_code=409,
                    )
                self._release_quarantine(tool_id)
                return self._public_item(current)
            if len(self.list_items()) >= self.limits["maxTools"]:
                raise AvatarToolStoreError("tool_limit_reached", "Avatar tool limit reached", status_code=409)
            temporary = self.root / f".{tool_id}.uploading"
            try:
                self._write_staged_tool(temporary, record, resources)
                created_size = self._directory_bytes(temporary)
                if self._current_storage_bytes() + created_size > self.limits["maxTotalBytes"]:
                    raise AvatarToolStoreError(
                        "storage_limit_reached",
                        "Avatar tool storage limit reached",
                        status_code=413,
                    )
                os.replace(temporary, final)
            except BaseException:
                self._cleanup_failed_staging(temporary)
                raise
            self._release_quarantine(tool_id)
            return self._public_item(record)

    def update_tool(
        self,
        tool_id: str,
        *,
        base_revision: str,
        name: str,
        change_mode: str,
        change_meanings: list[str],
        default_resource: str | None,
        default_image: bytes | None,
        change_resources: list[str],
        change_images: list[bytes],
        normal_sound_resource: str | None = None,
        normal_sound: bytes | None = None,
        special_probability: object | None = None,
        special_image_resource: str | None = None,
        special_image: bytes | None = None,
        special_meaning: str | None = None,
        special_sound_resource: str | None = None,
        special_sound: bytes | None = None,
    ) -> dict[str, Any]:
        if not is_local_avatar_tool_id(tool_id):
            raise AvatarToolStoreError("invalid_tool_id", "Invalid local avatar tool ID")

        with _STORE_LOCK:
            assert_cloudsave_writable(
                self.config_manager,
                operation="update",
                target=f"avatar_tools/{tool_id}",
            )
            self.ensure()
            current = self.read_record(tool_id, verify_resources=True)
            final = self.root / tool_id
            current_revision = self.record_revision(current)
            if not _REVISION_PATTERN.fullmatch(base_revision) or base_revision != current_revision:
                raise AvatarToolStoreError(
                    "tool_revision_conflict",
                    "Avatar tool changed after the edit page was opened",
                    status_code=409,
                )

            def retained_bytes(resource: str | None, allowed: set[str], *, field: str) -> bytes:
                if not resource or resource not in allowed:
                    raise AvatarToolStoreError(
                        "resource_reference_invalid",
                        "Retained resource is invalid",
                        field=field,
                    )
                candidate = final / resource
                if candidate.is_symlink() or not candidate.is_file():
                    raise AvatarToolStoreError(
                        "resource_reference_invalid",
                        "Retained resource is invalid",
                        field=field,
                    )
                # 这次 read_bytes 和上面那次 read_record(verify_resources=True)
                # 是两次独立的打开：同步盘 / 网络盘上的外部写者可能在中间把文件
                # 换掉，于是「保留原图」的 PUT 会静默发布用户没提交过的内容。
                # 读完立刻对齐 record 里的摘要，把这个窗口关掉。
                # 摘要比对本身也能挡住被换掉的文件，但那是在读进内存之后。
                # 先用 fstat 预检大小，超限的直接出局，一个字节都不读 —— 外部
                # 把资源换成多 GB 文件时不至于把内存吃光。用实际大小而不是上限
                # 去 read，避免为一张 120 KB 的图预分配 8 MiB 缓冲区。
                maximum = (
                    self.limits["maxAudioBytes"]
                    if resource.endswith(".mp3")
                    else self.limits["maxImageBytes"]
                )
                try:
                    with candidate.open("rb") as stream:
                        if os.fstat(stream.fileno()).st_size > maximum:
                            raise AvatarToolStoreError(
                                "resource_reference_invalid",
                                "Retained resource is invalid",
                                field=field,
                            )
                        data = stream.read(maximum + 1)
                    if len(data) > maximum:
                        # fstat 之后又被换大了。
                        raise AvatarToolStoreError(
                            "resource_reference_invalid",
                            "Retained resource is invalid",
                            field=field,
                        )
                except AvatarToolStoreError:
                    raise
                except OSError as exc:
                    # 文件锁、同步盘出错、外部删除都会走到这里。update_avatar_tool
                    # 只接 AvatarToolStoreError / MaintenanceModeError，裸 OSError
                    # 会绕过受控错误响应变成 500。这是瞬时失败不是损坏，用 503。
                    raise AvatarToolStoreError(
                        "resource_read_failed",
                        "Retained resource could not be read",
                        status_code=503,
                        field=field,
                    ) from exc
                expected_digest = current["resourceDigests"].get(resource)
                if (
                    not expected_digest
                    or hashlib.sha256(data).hexdigest() != expected_digest
                ):
                    raise AvatarToolStoreError(
                        "resource_reference_invalid",
                        "Retained resource is invalid",
                        field=field,
                    )
                return data

            current_change_resources = {
                item["image"] for item in current["imageChange"]["items"]
            }
            if (default_resource is None) == (default_image is None):
                raise AvatarToolStoreError(
                    "default_image_source_invalid",
                    "Choose either the current or a replacement default image",
                    field="default_image",
                )
            next_default = default_image if default_image is not None else retained_bytes(
                default_resource,
                {current["defaultImage"]},
                field="default_image",
            )

            if len(change_resources) != len(change_meanings):
                raise AvatarToolStoreError("change_items_mismatch", "Images and meanings must match")
            replacement_index = 0
            next_change_images: list[bytes] = []
            for index, resource in enumerate(change_resources):
                if resource:
                    next_change_images.append(retained_bytes(
                        resource,
                        current_change_resources,
                        field="change_image",
                    ))
                    continue
                if replacement_index >= len(change_images):
                    raise AvatarToolStoreError(
                        "change_image_required",
                        "Change image is required",
                        field="change_image",
                        index=index,
                    )
                next_change_images.append(change_images[replacement_index])
                replacement_index += 1
            if replacement_index != len(change_images):
                raise AvatarToolStoreError("change_items_mismatch", "Images and meanings must match")

            current_normal_sound = current["interaction"].get("normalSound")
            if normal_sound is not None and normal_sound_resource is not None:
                raise AvatarToolStoreError(
                    "normal_sound_source_invalid",
                    "Choose either the current or a replacement sound",
                    field="normal_sound",
                )
            next_normal_sound = normal_sound
            if normal_sound_resource is not None:
                next_normal_sound = retained_bytes(
                    normal_sound_resource,
                    {current_normal_sound} if current_normal_sound else set(),
                    field="normal_sound",
                )

            special_enabled = any(value is not None for value in (
                special_probability,
                special_image_resource,
                special_image,
                special_meaning,
                special_sound_resource,
                special_sound,
            ))
            next_special_image = None
            next_special_sound = None
            if special_enabled:
                if special_image is not None and special_image_resource is not None:
                    raise AvatarToolStoreError(
                        "special_image_source_invalid",
                        "Choose either the current or a replacement special image",
                        field="special_image",
                    )
                current_special = current["interaction"].get("special")
                next_special_image = special_image
                if special_image_resource is not None:
                    next_special_image = retained_bytes(
                        special_image_resource,
                        {current_special["image"]} if current_special else set(),
                        field="special_image",
                    )
                if special_sound is not None and special_sound_resource is not None:
                    raise AvatarToolStoreError(
                        "special_sound_source_invalid",
                        "Choose either the current or a replacement special sound",
                        field="special_sound",
                    )
                next_special_sound = special_sound
                if special_sound_resource is not None:
                    next_special_sound = retained_bytes(
                        special_sound_resource,
                        {current_special.get("sound")} if current_special and current_special.get("sound") else set(),
                        field="special_sound",
                    )

            record, resources = self._prepare_tool_contents(
                tool_id=tool_id,
                name=name,
                change_mode=change_mode,
                change_meanings=change_meanings,
                default_image=next_default,
                change_images=next_change_images,
                normal_sound=next_normal_sound,
                special_probability=special_probability,
                special_image=next_special_image,
                special_meaning=special_meaning,
                special_sound=next_special_sound,
            )

            updating = self.root / f".{tool_id}.updating"
            backup = self.root / f".{tool_id}.backup"
            for transient in (updating, backup):
                if transient.is_symlink():
                    raise AvatarToolStoreError("invalid_tool_path", "Invalid local avatar tool path")
                if transient.exists():
                    shutil.rmtree(transient)
            published_backup = False
            try:
                self._write_staged_tool(updating, record, resources)
                current_size = self._directory_bytes(final)
                updated_size = self._directory_bytes(updating)
                if self._current_storage_bytes() - current_size + updated_size > self.limits["maxTotalBytes"]:
                    raise AvatarToolStoreError(
                        "storage_limit_reached",
                        "Avatar tool storage limit reached",
                        status_code=413,
                    )
                os.replace(final, backup)
                published_backup = True
                os.replace(updating, final)
            except BaseException:
                self._cleanup_failed_staging(updating)
                if published_backup and not final.exists() and backup.exists():
                    try:
                        os.replace(backup, final)
                    except OSError:
                        _RECOVERY_PENDING_ROOTS.add(self._root_key())
                        logger.warning(
                            "Could not restore avatar tool update backup %s",
                            backup,
                            exc_info=True,
                        )
                        raise
                raise
            try:
                shutil.rmtree(backup)
            except OSError:
                logger.warning("Could not remove avatar tool update backup %s", backup)
            # 整个目录已被 _write_staged_tool 逐字节校验过的新内容替换，
            # 所以上一次硬重载的隔离判定对它已经失效。
            self._release_quarantine(tool_id)
            return self._public_item(record)


def get_avatar_tool_store(config_manager: Any) -> AvatarToolStore:
    return AvatarToolStore(config_manager)
