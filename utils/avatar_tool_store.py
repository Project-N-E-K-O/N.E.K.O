# -*- coding: utf-8 -*-
"""Authoritative local store for user-created avatar tools.

The browser only receives the public runtime projection returned by ``list_items``.
Interaction meanings stay in ``record.json`` and are read by Python when handling a
validated local interaction.
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from PIL import Image, UnidentifiedImageError

from utils.cloudsave_runtime import assert_cloudsave_writable
from utils.file_utils import atomic_write_json


LOCAL_AVATAR_TOOL_ID_PATTERN = re.compile(
    r"^local-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
LOCAL_AVATAR_TOOL_UPLOAD_PATTERN = re.compile(
    r"^\.local-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.uploading$"
)
PUBLIC_AVATAR_TOOL_FIXED_RESOURCE_NAMES = frozenset(
    {"default.png", "normal.mp3", "special.png", "special.mp3"}
)
PUBLIC_AVATAR_TOOL_CHANGE_RESOURCE_PATTERN = re.compile(r"^change-[0-9]{3}\.png$")
LOCAL_AVATAR_TOOL_CHANGE_MODES = frozenset({"press-swap", "click-advance"})

AVATAR_TOOL_LIMITS: dict[str, int] = {
    "maxTools": 64,
    "maxNameChars": 80,
    "maxMeaningChars": 1200,
    "maxChangeImages": 16,
    "maxImageBytes": 8 * 1024 * 1024,
    "maxImagePixels": 16_000_000,
    "maxAudioBytes": 5 * 1024 * 1024,
    "maxAudioDurationMs": 10_000,
    "maxTotalBytes": 256 * 1024 * 1024,
}

_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MUTATION_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


class AvatarToolStoreError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


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


def _validate_text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise AvatarToolStoreError(f"{field}_required", f"{field} is required")
    normalized = value.strip()
    if not normalized:
        raise AvatarToolStoreError(f"{field}_required", f"{field} is required")
    if len(normalized) > maximum:
        raise AvatarToolStoreError(f"{field}_too_long", f"{field} is too long")
    if _CONTROL_CHARACTER_PATTERN.search(normalized):
        raise AvatarToolStoreError(f"{field}_invalid", f"{field} contains control characters")
    return normalized


def _validate_probability(value: object) -> float:
    if isinstance(value, bool):
        raise AvatarToolStoreError("special_probability_invalid", "Special probability is invalid")
    try:
        probability = float(value)
    except (TypeError, ValueError) as exc:
        raise AvatarToolStoreError(
            "special_probability_invalid",
            "Special probability is invalid",
        ) from exc
    if not math.isfinite(probability) or probability <= 0 or probability > 1:
        raise AvatarToolStoreError("special_probability_invalid", "Special probability is invalid")
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
            return output.getvalue()
    except AvatarToolStoreError:
        raise
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

    def ensure(self) -> None:
        if not self.config_manager.ensure_avatar_tools_directory():
            raise AvatarToolStoreError(
                "avatar_tools_directory_unavailable",
                "Avatar tool storage is unavailable",
                status_code=503,
            )
        self._cleanup_unpublished_directories()

    def _cleanup_unpublished_directories(self) -> None:
        with _MUTATION_LOCK:
            for candidate in self.root.iterdir():
                if not LOCAL_AVATAR_TOOL_UPLOAD_PATTERN.fullmatch(candidate.name):
                    continue
                if candidate.is_symlink() or not candidate.is_dir():
                    continue
                shutil.rmtree(candidate, ignore_errors=True)

    def _record_path(self, tool_id: str) -> Path:
        if not is_local_avatar_tool_id(tool_id):
            raise AvatarToolStoreError("invalid_tool_id", "Invalid local avatar tool ID")
        return self.root / tool_id / "record.json"

    def read_record(self, tool_id: str) -> dict[str, Any]:
        path = self._record_path(tool_id)
        if path.is_symlink() or not path.is_file():
            raise AvatarToolStoreError("tool_not_found", "Avatar tool does not exist", status_code=404)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404) from exc
        return self._validate_record(payload, expected_id=tool_id)

    def _validate_record(self, payload: object, *, expected_id: str) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {
            "recordVersion", "id", "name", "defaultImage", "imageChange", "interaction"
        }:
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
        if payload.get("recordVersion") != 2 or payload.get("id") != expected_id:
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
        name = _validate_text(payload.get("name"), field="name", maximum=self.limits["maxNameChars"])
        default_image = payload.get("defaultImage")
        image_change = payload.get("imageChange")
        interaction = payload.get("interaction")
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
                "meaning": _validate_text(
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
            if special_sound is not None and special_sound != "special.mp3":
                raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
            special_probability = special.get("probability")
            if isinstance(special_probability, bool) or not isinstance(special_probability, (int, float)):
                raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
            clean_special = {
                "probability": _validate_probability(special_probability),
                "image": "special.png",
                "meaning": _validate_text(
                    special.get("meaning"),
                    field="special_meaning",
                    maximum=self.limits["maxMeaningChars"],
                ),
                **({"sound": "special.mp3"} if special_sound else {}),
            }
        directory = self.root / expected_id
        if directory.is_symlink() or not directory.is_dir():
            raise AvatarToolStoreError("record_invalid", "Avatar tool record is invalid", status_code=404)
        resource_names = ["default.png", *(item["image"] for item in clean_items)]
        if normal_sound:
            resource_names.append(normal_sound)
        if clean_special:
            resource_names.append(clean_special["image"])
            if clean_special.get("sound"):
                resource_names.append(clean_special["sound"])
        for filename in resource_names:
            resource = directory / filename
            if resource.is_symlink() or not resource.is_file():
                raise AvatarToolStoreError("record_invalid", "Avatar tool resource is invalid", status_code=404)
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
        }

    def _asset_url(self, tool_id: str, filename: str) -> str:
        path = self.root / tool_id / filename
        stat = path.stat()
        return f"/user_avatar_tools/{tool_id}/{filename}?v={stat.st_size}-{stat.st_mtime_ns}"

    def _public_item(self, record: dict[str, Any]) -> dict[str, Any]:
        tool_id = record["id"]
        item = {
            "id": tool_id,
            "name": record["name"],
            "changeMode": record["imageChange"]["mode"],
            "defaultUrl": self._asset_url(tool_id, record["defaultImage"]),
            "changeUrls": [
                self._asset_url(tool_id, item["image"])
                for item in record["imageChange"]["items"]
            ],
        }
        normal_sound = record["interaction"].get("normalSound")
        if normal_sound:
            item["normalSoundUrl"] = self._asset_url(tool_id, normal_sound)
        special = record["interaction"].get("special")
        if special:
            item["special"] = {
                "probability": special["probability"],
                "imageUrl": self._asset_url(tool_id, special["image"]),
                **(
                    {"soundUrl": self._asset_url(tool_id, special["sound"])}
                    if special.get("sound")
                    else {}
                ),
            }
        return item

    def list_items(self) -> list[dict[str, Any]]:
        self.ensure()
        items: list[dict[str, Any]] = []
        for candidate in sorted(self.root.iterdir(), key=lambda item: item.name):
            if candidate.is_symlink() or not candidate.is_dir() or not is_local_avatar_tool_id(candidate.name):
                continue
            try:
                items.append(self._public_item(self.read_record(candidate.name)))
            except (AvatarToolStoreError, OSError) as exc:
                logger.warning("Skipping invalid local avatar tool %s: %s", candidate.name, exc)
                continue
        return items

    def _current_storage_bytes(self) -> int:
        total = 0
        for directory in self.root.iterdir():
            if directory.is_symlink() or not directory.is_dir() or not is_local_avatar_tool_id(directory.name):
                continue
            for entry in directory.iterdir():
                if entry.is_file() and not entry.is_symlink():
                    total += entry.stat().st_size
        return total

    def create_tool(
        self,
        *,
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
        clean_name = _validate_text(name, field="name", maximum=self.limits["maxNameChars"])
        if change_mode not in LOCAL_AVATAR_TOOL_CHANGE_MODES:
            raise AvatarToolStoreError("change_mode_invalid", "Image change mode is invalid")
        if len(change_images) != len(change_meanings):
            raise AvatarToolStoreError("change_items_mismatch", "Images and meanings must match")
        if not 1 <= len(change_images) <= self.limits["maxChangeImages"]:
            raise AvatarToolStoreError("change_items_invalid", "Image change item count is invalid")
        if change_mode == "press-swap" and len(change_images) != 1:
            raise AvatarToolStoreError("change_items_invalid", "Press-swap requires one change image")
        clean_meanings = [
            _validate_text(
                meaning,
                field="meaning",
                maximum=self.limits["maxMeaningChars"],
            )
            for meaning in change_meanings
        ]
        default_png = _decode_static_png(default_image, limits=self.limits)
        change_pngs = [
            _decode_static_png(image, limits=self.limits)
            for image in change_images
        ]
        normal_mp3 = _validate_mp3(normal_sound, limits=self.limits) if normal_sound is not None else None
        special_values = (special_probability, special_image, special_meaning, special_sound)
        special_enabled = any(value is not None for value in special_values)
        clean_special = None
        special_png = None
        special_mp3 = None
        if special_enabled:
            if special_probability is None:
                raise AvatarToolStoreError("special_probability_required", "Special probability is required")
            if special_image is None:
                raise AvatarToolStoreError("special_image_required", "Special image is required")
            if special_meaning is None:
                raise AvatarToolStoreError("special_meaning_required", "Special meaning is required")
            probability = _validate_probability(special_probability)
            special_png = _decode_static_png(special_image, limits=self.limits)
            clean_special = {
                "probability": probability,
                "image": "special.png",
                "meaning": _validate_text(
                    special_meaning,
                    field="special_meaning",
                    maximum=self.limits["maxMeaningChars"],
                ),
                **({"sound": "special.mp3"} if special_sound is not None else {}),
            }
            special_mp3 = (
                _validate_special_mp3(special_sound, limits=self.limits)
                if special_sound is not None
                else None
            )

        with _MUTATION_LOCK:
            self.ensure()
            assert_cloudsave_writable(
                self.config_manager,
                operation="create",
                target="avatar_tools",
            )
            published = [
                item for item in self.root.iterdir()
                if item.is_dir() and not item.is_symlink() and is_local_avatar_tool_id(item.name)
            ]
            if len(published) >= self.limits["maxTools"]:
                raise AvatarToolStoreError("tool_limit_reached", "Avatar tool limit reached", status_code=409)
            tool_id = f"local-{uuid.uuid4()}"
            temporary = self.root / f".{tool_id}.uploading"
            final = self.root / tool_id
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
                    **({"normalSound": "normal.mp3"} if normal_mp3 is not None else {}),
                    **({"special": clean_special} if clean_special else {}),
                },
            }
            try:
                temporary.mkdir(mode=0o700)
                (temporary / "default.png").write_bytes(default_png)
                for index, image in enumerate(change_pngs):
                    (temporary / f"change-{index:03d}.png").write_bytes(image)
                if normal_mp3 is not None:
                    (temporary / "normal.mp3").write_bytes(normal_mp3)
                if special_png is not None:
                    (temporary / "special.png").write_bytes(special_png)
                if special_mp3 is not None:
                    (temporary / "special.mp3").write_bytes(special_mp3)
                atomic_write_json(temporary / "record.json", record, ensure_ascii=False, indent=2)
                created_size = sum(
                    entry.stat().st_size
                    for entry in temporary.iterdir()
                    if entry.is_file() and not entry.is_symlink()
                )
                if self._current_storage_bytes() + created_size > self.limits["maxTotalBytes"]:
                    raise AvatarToolStoreError(
                        "storage_limit_reached",
                        "Avatar tool storage limit reached",
                        status_code=413,
                    )
                os.replace(temporary, final)
            except BaseException:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
            return self._public_item(record)


def get_avatar_tool_store(config_manager: Any) -> AvatarToolStore:
    return AvatarToolStore(config_manager)
