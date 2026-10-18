# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Jukebox Router

Handles jukebox-related endpoints including:
- Song management (upload, list, delete, visibility)
- Action/VMD management (upload, list, delete)
- Song-Action binding management
- Configuration import/export

URL convention: routes declared WITHOUT trailing slash (no ``@router.get('/')``).
See ``main_routers/characters_router.py`` docstring or
``.agent/rules/neko-guide.md`` (section "API URL 末尾不带斜杠", no trailing API URL slash) for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""  # noqa: DOCSTRING_CJK

import asyncio
import copy
import json
import hashlib
import re
import shutil
import zipfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

from fastapi import APIRouter, File, Form, UploadFile, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from .shared_state import get_config_manager
from utils.file_utils import atomic_write_json, read_json_async
from utils.logger_config import get_module_logger

router = APIRouter(prefix="/api/jukebox", tags=["jukebox"])
logger = get_module_logger(__name__, "Main")
BUILTIN_JUKEBOX_DIR = Path(__file__).parent.parent / "static" / "jukebox"

# Upload constants.
MAX_FILE_SIZE = 1 * 1024 * 1024 * 1024  # 1 GB per song/animation file.
MAX_ZIP_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB archive import/export.
CHUNK_SIZE = 1024 * 1024  # 1MB chunks

# Allowed extensions.
ALLOWED_AUDIO_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.flac'}
ALLOWED_ACTION_EXTENSIONS = {'.vmd', '.bvh', '.fbx', '.vrma'}
JUKEBOX_MEDIA_TYPES = {
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.ogg': 'audio/ogg',
    '.flac': 'audio/flac',
    '.vmd': 'application/octet-stream',
    '.bvh': 'application/octet-stream',
    '.fbx': 'application/octet-stream',
    '.vrma': 'application/octet-stream'
}
BUILTIN_BINDING_OFFSET_MIGRATIONS = {
    ("song_001", "action_003"): {0},
}

def check_file_size(file: UploadFile, max_size: int) -> int:
    """Check file size; returns the size in bytes, raising an exception if the limit is exceeded."""
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    if file_size > max_size:
        raise HTTPException(400, f"文件过大: {file_size / 1024 / 1024 / 1024:.1f}GB > {max_size / 1024 / 1024 / 1024}GB")
    return file_size


def file_iterator(file_path: Path, chunk_size: int = CHUNK_SIZE):
    """File streaming iterator for large file transfers."""
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            yield chunk


def cleanup_temp_path(path: str):
    """Clean up a temporary file or directory."""
    try:
        p = Path(path)
        if p.is_file():
            p.unlink(missing_ok=True)
        elif p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    except Exception as e:
        logger.warning(f"清理临时路径失败 {path}: {e}")


def _resolve_child_path(root: Path, relative_path: str) -> Path:
    target_path = (root / relative_path).resolve()
    root_path = root.resolve()
    try:
        target_path.relative_to(root_path)
    except ValueError:
        raise HTTPException(403, "访问被拒绝")
    return target_path


def _get_flat_builtin_jukebox_path(file_path: str) -> Optional[Path]:
    path = Path(file_path)
    if len(path.parts) != 2 or path.parts[0] not in {"songs", "actions"}:
        return None
    return _resolve_child_path(BUILTIN_JUKEBOX_DIR, path.name)


def resolve_jukebox_file_path(file_path: str) -> Path:
    """Resolve a jukebox file path from user storage or bundled resources."""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)

    # Strip leading slashes to avoid path-resolution problems.
    file_path = file_path.lstrip('/')

    # Handle /static/jukebox/ prefixes for bundled assets.
    if file_path.startswith('static/jukebox/'):
        file_path = file_path.replace('static/jukebox/', '', 1)

    full_path = _resolve_child_path(jukebox_config.jukebox_dir, file_path)

    # Prefer files under user Documents.
    if full_path.exists() and full_path.is_file():
        return full_path

    # Without user files, try bundled storage; historical Steam assets use flat layouts.
    builtin_candidates = [_resolve_child_path(BUILTIN_JUKEBOX_DIR, file_path)]
    flat_builtin_path = _get_flat_builtin_jukebox_path(file_path)
    if flat_builtin_path is not None:
        builtin_candidates.append(flat_builtin_path)

    for builtin_path in builtin_candidates:
        if builtin_path.exists() and builtin_path.is_file():
            return builtin_path

    raise HTTPException(404, "文件不存在")


def get_jukebox_media_type(target_path: Path) -> str:
    return JUKEBOX_MEDIA_TYPES.get(target_path.suffix.lower(), 'application/octet-stream')


def validate_extract_path(file_path: str, extract_dir: Path) -> Path:
    """Validate the extraction path for safety, preventing path traversal attacks."""
    # Normalize paths, removing ../ etc.
    target_path = (extract_dir / file_path).resolve()
    extract_dir_resolved = extract_dir.resolve()
    
    # Require targets under extract_dir.
    try:
        target_path.relative_to(extract_dir_resolved)
    except ValueError:
        raise HTTPException(400, f"非法路径: {file_path}")
    
    # Require expected songs/ or actions/ subdirectories.
    # Compare path parts instead of strings across Windows/Linux separators.
    relative_path = target_path.relative_to(extract_dir_resolved)
    if not relative_path.parts or relative_path.parts[0] not in {"songs", "actions"}:
        raise HTTPException(400, f"路径必须在 songs/ 或 actions/ 目录下: {file_path}")
    
    return target_path

def sanitize_filename(name: str) -> str:
    """Sanitize a filename by removing illegal characters, used for generating IDs."""
    # Remove extensions.
    name = Path(name).stem
    # Replace invalid characters with underscores.
    name = re.sub(r'[<>:"/\\|?*\s]+', '_', name)
    # Collapse repeated underscores.
    name = re.sub(r'_+', '_', name)
    # Strip leading/trailing underscores.
    name = name.strip('_')
    # Limit length.
    if len(name) > 50:
        name = name[:50]
    return name or 'unnamed'

def get_unique_filename(directory: Path, filename: str) -> str:
    """Get a unique filename, appending a numeric suffix (underscore format) on conflict."""
    target_path = directory / filename
    if not target_path.exists():
        return filename

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1

    while True:
        new_filename = f"{stem}_{counter}{suffix}"
        if not (directory / new_filename).exists():
            return new_filename
        counter += 1
        # Prevent infinite loops.
        if counter > 9999:
            raise RuntimeError(f"无法为 {filename} 生成唯一文件名")


@dataclass
class Song:
    id: str
    name: str
    artist: str
    audio: str
    audioMd5: str
    audioFormat: str
    visible: bool
    uploadDate: str
    defaultAction: str = ""  # Default animation ID.


@dataclass
class Action:
    id: str
    name: str
    file: str  # Animation path, e.g. actions/action_001.vmd.
    fileMd5: str  # File MD5.
    format: str  # Animation format: vmd / vrma / fbx / bvh.
    uploadDate: str
    visible: bool = True
    missing: bool = False


class BatchDeleteSongsRequest(BaseModel):
    songIds: List[str] = Field(default_factory=list)


class BatchDeleteActionsRequest(BaseModel):
    actionIds: List[str] = Field(default_factory=list)


class JukeboxConfig:
    """Jukebox configuration manager."""
    
    def __init__(self, config_mgr):
        self.config_mgr = config_mgr
        self.jukebox_dir = config_mgr.app_docs_dir / "jukebox"
        self.songs_dir = self.jukebox_dir / "songs"
        self.actions_dir = self.jukebox_dir / "actions"
        self.config_file = self.jukebox_dir / "config.json"
        
        # Ensure directories exist.
        self._ensure_directories()
        
        # Load configuration.
        self.data = self._load_config()
    
    def _ensure_directories(self):
        """Ensure the directories exist."""
        self.jukebox_dir.mkdir(parents=True, exist_ok=True)
        self.songs_dir.mkdir(parents=True, exist_ok=True)
        self.actions_dir.mkdir(parents=True, exist_ok=True)
    
    def _load_config(self) -> dict:
        """Load the config file, merging user config with the bundled config."""
        # Default configuration.
        default_config = {
            "version": "1.0",
            "songs": {},
            "actions": {},
            "bindings": {},
            "md5Index": {"songs": {}, "actions": {}}
        }

        # Load user configuration.
        user_config = {}
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
            except Exception as e:
                logger.error(f"加载点歌台用户配置失败: {e}")

        # Load bundled configuration.
        builtin_songs = {}
        builtin_actions = {}
        builtin_bindings = {}
        builtin_md5_index = {"songs": {}, "actions": {}}
        
        builtin_songs_path = Path(__file__).parent.parent / "static" / "jukebox" / "songs.json"
        if builtin_songs_path.exists():
            try:
                with open(builtin_songs_path, 'r', encoding='utf-8') as f:
                    builtin_data = json.load(f)
                    
                    # Load bundled songs in dict form.
                    for song_id, song in builtin_data.get("songs", {}).items():
                        song["isBuiltin"] = True  # Mark bundled resources as nonexportable.
                        builtin_songs[song_id] = song
                    
                    # Load bundled animations in dict form.
                    for action_id, action in builtin_data.get("actions", {}).items():
                        action["isBuiltin"] = True
                        builtin_actions[action_id] = action
                    
                    # Load bundled bindings.
                    builtin_bindings = builtin_data.get("bindings", {})
                    
                    # Load bundled MD5 indexes.
                    builtin_md5_index = builtin_data.get("md5Index", {"songs": {}, "actions": {}})
            except Exception as e:
                logger.error(f"加载软件自带配置失败: {e}")

        # Merge configuration, preferring users while retaining bundled settings.
        merged_songs = {**builtin_songs, **user_config.get("songs", {})}
        merged_actions = {**builtin_actions, **user_config.get("actions", {})}

        # Merge bundled/user bindings, preferring user bindings.
        # Load application-built-in bindings first from user Documents or bundled config.
        user_builtin_bindings = self._migrate_builtin_bindings(
            user_config.get("builtinBindings", {}),
            builtin_bindings,
        )
        merged_bindings = {**builtin_bindings, **user_builtin_bindings}
        # Merge user bindings afterward, overriding built-ins.
        merged_bindings = {**merged_bindings, **user_config.get("bindings", {})}
        
        # Merge MD5 indexes.
        user_md5_index = user_config.get("md5Index", {"songs": {}, "actions": {}})
        merged_md5_index = {
            "songs": {**builtin_md5_index.get("songs", {}), **user_md5_index.get("songs", {})},
            "actions": {**builtin_md5_index.get("actions", {}), **user_md5_index.get("actions", {})}
        }

        # Apply built-in asset overrides.
        builtin_overrides = user_config.get("builtinOverrides", {"songs": {}, "actions": {}})
        for song_id, overrides in builtin_overrides.get("songs", {}).items():
            if song_id in merged_songs:
                merged_songs[song_id].update(overrides)
        for action_id, overrides in builtin_overrides.get("actions", {}).items():
            if action_id in merged_actions:
                merged_actions[action_id].update(overrides)

        return {
            "version": user_config.get("version", "1.0"),
            "songs": merged_songs,
            "actions": merged_actions,
            "bindings": merged_bindings,
            "md5Index": merged_md5_index
        }

    @staticmethod
    def _migrate_builtin_bindings(user_builtin_bindings: dict, builtin_bindings: dict) -> dict:
        migrated_bindings = copy.deepcopy(user_builtin_bindings)
        for (song_id, action_id), old_offsets in BUILTIN_BINDING_OFFSET_MIGRATIONS.items():
            song_bindings = migrated_bindings.get(song_id)
            if not isinstance(song_bindings, dict):
                continue
            binding = song_bindings.get(action_id)
            bundled_binding = builtin_bindings.get(song_id, {}).get(action_id)
            if not isinstance(binding, dict) or not isinstance(bundled_binding, dict):
                continue
            current_offset = binding.get("offset")
            bundled_offset = bundled_binding.get("offset")
            if current_offset in old_offsets and bundled_offset not in old_offsets:
                binding["offset"] = bundled_offset
        return migrated_bindings

    def _load_builtin_resource_defaults(self) -> tuple[dict, dict]:
        """Load bundled song/action defaults used to detect real user overrides."""
        builtin_songs_path = Path(__file__).parent.parent / "static" / "jukebox" / "songs.json"
        if not builtin_songs_path.exists():
            return {}, {}

        try:
            with open(builtin_songs_path, 'r', encoding='utf-8') as f:
                builtin_data = json.load(f)
        except Exception as e:
            logger.error(f"加载软件自带配置失败: {e}")
            return {}, {}

        return builtin_data.get("songs", {}), builtin_data.get("actions", {})

    @staticmethod
    def _collect_builtin_overrides(resources: dict, resource_ids: set, defaults: dict, fields: list[str]) -> dict:
        fallback_values = {
            "visible": True,
            "defaultAction": "",
            "name": "",
            "artist": "",
        }
        overrides_by_id = {}

        for resource_id in resource_ids:
            resource = resources[resource_id]
            default_resource = defaults.get(resource_id, {})
            overrides = {}
            for field in fields:
                if field not in resource:
                    continue
                default_value = default_resource.get(field, fallback_values.get(field))
                if resource[field] != default_value:
                    overrides[field] = resource[field]
            if overrides:
                overrides_by_id[resource_id] = overrides

        return overrides_by_id

    def save(self):
        """Save the config (excluding bundled resources, but keeping cross-type bindings and built-in resource override settings)."""
        # Get every asset's ID/type.
        all_songs = self.data.get("songs", {})
        all_actions = self.data.get("actions", {})

        # Distinguish bundled from user assets.
        user_songs = {k: v for k, v in all_songs.items() if not v.get("isBuiltin", False)}
        user_actions = {k: v for k, v in all_actions.items() if not v.get("isBuiltin", False)}
        builtin_song_ids = {k for k, v in all_songs.items() if v.get("isBuiltin", False)}
        builtin_action_ids = {k for k, v in all_actions.items() if v.get("isBuiltin", False)}

        # Collect built-in visibility/default-animation/name/artist overrides.
        builtin_song_defaults, builtin_action_defaults = self._load_builtin_resource_defaults()
        builtin_overrides = {
            "songs": self._collect_builtin_overrides(
                all_songs,
                builtin_song_ids,
                builtin_song_defaults,
                ["visible", "defaultAction", "name", "artist"],
            ),
            "actions": self._collect_builtin_overrides(
                all_actions,
                builtin_action_ids,
                builtin_action_defaults,
                ["visible", "name"],
            ),
        }

        user_data = {
            "version": self.data.get("version", "1.0"),
            "songs": user_songs,
            "actions": user_actions,
            "bindings": {},  # Save all bindings involving user assets.
            "builtinBindings": {},  # Save application-built-in bindings.
            "md5Index": {
                "songs": {},
                "actions": {}
            },
            "builtinOverrides": builtin_overrides  # Built-in asset overrides.
        }

        # Save bindings.
        for song_id, actions in self.data.get("bindings", {}).items():
            is_user_song = song_id in user_songs

            for action_id, bind_data in actions.items():
                is_user_action = action_id in user_actions

                if is_user_song or is_user_action:
                    # Store user-related bindings in bindings.
                    if song_id not in user_data["bindings"]:
                        user_data["bindings"][song_id] = {}
                    user_data["bindings"][song_id][action_id] = bind_data
                else:
                    # Store built-in bindings in builtinBindings.
                    if song_id not in user_data["builtinBindings"]:
                        user_data["builtinBindings"][song_id] = {}
                    user_data["builtinBindings"][song_id][action_id] = bind_data

        # Filter MD5 indexes to user assets only.
        # Song MD5 index.
        for md5_key, song_id in self.data.get("md5Index", {}).get("songs", {}).items():
            if song_id in all_songs and not all_songs[song_id].get("isBuiltin", False):
                user_data["md5Index"]["songs"][md5_key] = song_id

        # Animation MD5 index.
        for md5_key, action_id in self.data.get("md5Index", {}).get("actions", {}).items():
            if action_id in all_actions and not all_actions[action_id].get("isBuiltin", False):
                user_data["md5Index"]["actions"][md5_key] = action_id

        atomic_write_json(self.config_file, user_data)

    async def asave(self):
        """Async wrapper for save: save() must not be called directly on the event loop (it blocks)."""
        await asyncio.to_thread(self.save)

    def get_next_id(self, prefix: str) -> str:
        """Get the next ID."""
        existing = self.data.get(f"{prefix}s", {})
        max_num = 0
        for key in existing.keys():
            if key.startswith(f"{prefix}_"):
                try:
                    num = int(key.split("_")[1])
                    max_num = max(max_num, num)
                except ValueError:
                    pass
        return f"{prefix}_{max_num + 1:03d}"


def build_config_revision(data: dict) -> str:
    """Build a small stable token for polling whether jukebox data changed."""
    payload = {
        "version": data.get("version", "1.0"),
        "songs": data.get("songs", {}),
        "actions": data.get("actions", {}),
        "bindings": data.get("bindings", {}),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.blake2s(serialized.encode("utf-8"), digest_size=12).hexdigest()


def build_config_summary(data: dict) -> dict:
    songs = data.get("songs", {})
    actions = data.get("actions", {})
    return {
        "configRevision": build_config_revision(data),
        "songCount": len(songs),
        "visibleSongCount": sum(1 for song in songs.values() if song.get("visible") is not False),
        "actionCount": len(actions),
    }


def calculate_md5(file_path: Path) -> str:
    """Calculate the MD5 hash for a file."""
    md5_hash = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()


def _clean_audio_metadata_value(value) -> str:
    """Normalize one audio metadata value for jukebox display."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = next((item for item in value if item), "")
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    value = str(value).replace("\x00", "").strip()
    value = re.sub(r"\s+", " ", value)
    if not value:
        return ""
    if value.lower() in {
        "unknown",
        "unknown artist",
        "unknown title",
        "unknown singer",
        "none",
        "null",
        "未知",
        "未知艺术家",
        "未知歌手",
    }:
        return ""
    return value[:200]


def _pick_audio_metadata_value(metadata: dict, *keys: str) -> str:
    normalized = {
        str(key).strip().lower().replace("-", "_").replace(" ", "_"): value
        for key, value in metadata.items()
    }
    for key in keys:
        value = _clean_audio_metadata_value(
            normalized.get(key.strip().lower().replace("-", "_").replace(" ", "_"))
        )
        if value:
            return value
    return ""


def extract_audio_metadata(file_path: Path) -> dict:
    """Best-effort audio tag extraction. Failures must not block upload."""
    try:
        import av
    except Exception:
        return {}

    try:
        with av.open(str(file_path)) as container:
            merged_metadata = dict(getattr(container, "metadata", {}) or {})
            for stream in getattr(container, "streams", []) or []:
                if getattr(stream, "type", "") == "audio":
                    for key, value in (getattr(stream, "metadata", {}) or {}).items():
                        merged_metadata.setdefault(key, value)

        return {
            "name": _pick_audio_metadata_value(
                merged_metadata,
                "title",
                "song",
                "tracktitle",
                "track_title",
                "标题",
            ),
            "artist": _pick_audio_metadata_value(
                merged_metadata,
                "artist",
                "album_artist",
                "albumartist",
                "performer",
                "author",
                "composer",
                "singer",
                "歌手",
                "艺术家",
            ),
        }
    except Exception:
        return {}


# ============== API routes ==============

@router.get("/config")
async def get_config():
    """Get the full config (local bindings are already ID-level; returned as-is)."""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    return {**jukebox_config.data, **build_config_summary(jukebox_config.data)}


@router.get("/config/summary")
async def get_config_summary():
    """Return a lightweight config summary for polling full playlist refreshes."""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    return build_config_summary(jukebox_config.data)


@router.post("/songs")
async def upload_songs(
    files: List[UploadFile] = File(...),
    metadata: str = Form("[]")
):
    """
    Upload songs.
    files: a single file or a list of files
    metadata: JSON string with per-song metadata [{name, artist}, ...]
    """
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    try:
        meta_list = json.loads(metadata) if metadata else []
    except json.JSONDecodeError:
        raise HTTPException(400, "metadata 格式错误")
    
    # Match meta_list length to files, padding empty objects as needed.
    while len(meta_list) < len(files):
        meta_list.append({})
    
    results = []
    for i, (file, meta) in enumerate(zip(files, meta_list)):
        try:
            if not isinstance(meta, dict):
                meta = {}

            # Check file size.
            check_file_size(file, MAX_FILE_SIZE)

            # Validate extensions.
            file_ext = Path(file.filename).suffix.lower()
            if file_ext not in ALLOWED_AUDIO_EXTENSIONS:
                results.append({"success": False, "error": f"不支持的格式: {file_ext}"})
                continue
            
            # Get original basenames without paths.
            original_filename = Path(file.filename).name
            file_stem = Path(file.filename).stem

            # Generate safe filename-based IDs.
            song_id = sanitize_filename(file.filename)

            # Ensure unique IDs.
            base_id = song_id
            counter = 1
            while song_id in jukebox_config.data["songs"]:
                song_id = f"{base_id}_{counter}"
                counter += 1

            # Get unique filenames.
            target_filename = get_unique_filename(jukebox_config.songs_dir, original_filename)
            target_path = jukebox_config.songs_dir / target_filename

            with open(target_path, "wb") as buffer:
                await asyncio.to_thread(shutil.copyfileobj, file.file, buffer)

            # Compute MD5.
            # Hashing reads entire large files; offload to avoid blocking the event loop.
            file_md5 = await asyncio.to_thread(calculate_md5, target_path)

            # Check MD5 duplicates.
            existing_song_id = jukebox_config.data["md5Index"]["songs"].get(file_md5)
            if existing_song_id:
                target_path.unlink(missing_ok=True)
                results.append({"success": False, "error": f"歌曲已存在: {existing_song_id}"})
                continue

            try:
                audio_metadata = await asyncio.to_thread(extract_audio_metadata, target_path)
            except Exception:
                audio_metadata = {}

            # Default display names use supplied values, audio metadata, or filenames.
            # Preserve numeric filename suffixes such as song(1).mp3 in default display names.
            song_name = (
                _clean_audio_metadata_value(meta.get("name"))
                or audio_metadata.get("name")
                or Path(target_filename).stem
            )
            song_artist = (
                _clean_audio_metadata_value(meta.get("artist"))
                or audio_metadata.get("artist")
                or "未知"
            )

            # Create song records.
            song = Song(
                id=song_id,
                name=song_name,
                artist=song_artist,
                audio=f"songs/{target_filename}",
                audioMd5=file_md5,
                audioFormat=file_ext.lstrip("."),
                visible=True,
                uploadDate=datetime.now().isoformat()
            )
            
            # Save configuration.
            jukebox_config.data["songs"][song_id] = asdict(song)
            jukebox_config.data["md5Index"]["songs"][file_md5] = song_id
            
            results.append({"success": True, "song": asdict(song)})
            
        except Exception as e:
            logger.error(f"上传第 {i+1} 首歌曲失败: {e}")
            results.append({"success": False, "error": str(e)})
        finally:
            file.file.close()
    
    await jukebox_config.asave()
    
    # Return one result directly for single-song uploads, otherwise a list.
    if len(files) == 1:
        return results[0] if results else {"success": False, "error": "无文件上传"}
    return {"success": True, "results": results}


@router.post("/songs/batch-delete")
async def batch_delete_songs(request: BatchDeleteSongsRequest):
    """Delete uploaded songs and hide built-in songs in one validated batch."""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)

    song_ids = []
    seen = set()
    for song_id in request.songIds:
        if song_id and song_id not in seen:
            song_ids.append(song_id)
            seen.add(song_id)

    if not song_ids:
        raise HTTPException(400, "未选择歌曲")

    missing_ids = [song_id for song_id in song_ids if song_id not in jukebox_config.data["songs"]]
    if missing_ids:
        raise HTTPException(404, f"歌曲不存在: {', '.join(missing_ids)}")

    deleted = []
    hidden = []
    failed = []

    for song_id in song_ids:
        song = jukebox_config.data["songs"][song_id]
        song_name = song.get("name") or song_id

        try:
            if song.get("isBuiltin", False):
                song["visible"] = False
                hidden.append({"songId": song_id, "name": song_name})
                continue

            audio_path = jukebox_config.jukebox_dir / song["audio"]
            if audio_path.exists():
                audio_path.unlink()

            if song_id in jukebox_config.data["bindings"]:
                del jukebox_config.data["bindings"][song_id]

            song_md5 = song.get("audioMd5", "")
            if song_md5 and song_md5 in jukebox_config.data["md5Index"]["songs"]:
                del jukebox_config.data["md5Index"]["songs"][song_md5]

            del jukebox_config.data["songs"][song_id]
            deleted.append({"songId": song_id, "name": song_name})
        except Exception as exc:
            logger.error(f"批量删除歌曲失败: {song_id}, error={exc}")
            failed.append({"songId": song_id, "name": song_name, "error": str(exc)})

    if deleted or hidden:
        await jukebox_config.asave()

    failed_count = len(failed)
    return {
        "success": failed_count == 0,
        "partial": failed_count > 0 and (len(deleted) > 0 or len(hidden) > 0),
        "requestedCount": len(song_ids),
        "deletedCount": len(deleted),
        "hiddenCount": len(hidden),
        "failedCount": failed_count,
        "deleted": deleted,
        "hidden": hidden,
        "failed": failed,
    }


@router.delete("/songs/{song_id}")
async def delete_song(song_id: str):
    """Delete an uploaded song or hide a built-in song."""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)

    if song_id not in jukebox_config.data["songs"]:
        raise HTTPException(404, "歌曲不存在")

    song = jukebox_config.data["songs"][song_id]

    # Built-ins hide without deleting assets or bindings.
    if song.get("isBuiltin", False):
        song["visible"] = False
        await jukebox_config.asave()
        logger.info(f"隐藏内置歌曲: {song_id}")
        return {"success": True, "message": "内置歌曲已隐藏", "hidden": True}

    # Completely delete user assets.
    # Delete files.
    audio_path = jukebox_config.jukebox_dir / song["audio"]
    if audio_path.exists():
        audio_path.unlink()

    # Delete ID-based bindings.
    if song_id in jukebox_config.data["bindings"]:
        del jukebox_config.data["bindings"][song_id]

    # Remove from MD5 indexes.
    song_md5 = song.get("audioMd5", "")
    if song_md5 and song_md5 in jukebox_config.data["md5Index"]["songs"]:
        del jukebox_config.data["md5Index"]["songs"][song_md5]

    # Delete song records.
    del jukebox_config.data["songs"][song_id]
    await jukebox_config.asave()

    logger.info(f"删除歌曲: {song_id}")
    return {"success": True}


@router.put("/songs/{song_id}/visibility")
async def update_song_visibility(song_id: str, visible: bool = Form(...)):
    """Update song visibility."""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    if song_id not in jukebox_config.data["songs"]:
        raise HTTPException(404, "歌曲不存在")
    
    jukebox_config.data["songs"][song_id]["visible"] = visible
    await jukebox_config.asave()
    
    return {"success": True}


@router.put("/actions/{action_id}/visibility")
async def update_action_visibility(action_id: str, visible: bool = Form(...)):
    """Update an action visibility flag."""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)

    if action_id not in jukebox_config.data["actions"]:
        raise HTTPException(404, "动画不存在")

    jukebox_config.data["actions"][action_id]["visible"] = visible
    await jukebox_config.asave()

    return {"success": True}


@router.put("/songs/{song_id}/metadata")
async def update_song_metadata(
    song_id: str,
    name: str = Form(None),
    artist: str = Form(None)
):
    """Update song metadata (name, artist)."""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    if song_id not in jukebox_config.data["songs"]:
        raise HTTPException(404, "歌曲不存在")
    
    if name is not None:
        jukebox_config.data["songs"][song_id]["name"] = name
    if artist is not None:
        jukebox_config.data["songs"][song_id]["artist"] = artist
    
    await jukebox_config.asave()
    
    logger.info(f"更新歌曲元数据: {song_id}, name={name}, artist={artist}")
    return {"success": True}


@router.put("/actions/{action_id}/metadata")
async def update_action_metadata(
    action_id: str,
    name: str = Form(...)
):
    """Update action metadata (name)."""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    if action_id not in jukebox_config.data["actions"]:
        raise HTTPException(404, "动画不存在")
    
    jukebox_config.data["actions"][action_id]["name"] = name
    await jukebox_config.asave()
    
    logger.info(f"更新动画元数据: {action_id}, name={name}")
    return {"success": True}


@router.put("/songs/{song_id}/default-action")
async def set_song_default_action(
    song_id: str,
    action_id: str = Form(...)  # Empty strings clear default animations.
):
    """Set a song's default action."""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    if song_id not in jukebox_config.data["songs"]:
        raise HTTPException(404, "歌曲不存在")
    
    # If action_id is supplied, check animation existence.
    if action_id and action_id not in jukebox_config.data["actions"]:
        raise HTTPException(404, "动画不存在")
    
    # Check animation/song binding.
    # Binding format: bindings[songId][actionId] = {"offset": 0}.
    if action_id:
        song_bindings = jukebox_config.data["bindings"].get(song_id, {})
        if action_id not in song_bindings:
            raise HTTPException(400, "该动画未绑定到此歌曲")
    
    jukebox_config.data["songs"][song_id]["defaultAction"] = action_id
    await jukebox_config.asave()
    
    logger.info(f"设置歌曲默认动画: {song_id} -> {action_id}")
    return {"success": True, "defaultAction": action_id}


@router.post("/actions")
async def upload_actions(
    files: List[UploadFile] = File(...),
    metadata: str = Form("[]")
):
    """
    Upload actions.
    files: a single file or a list of files
    metadata: JSON string with per-action metadata [{name}, ...]
    """
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    try:
        meta_list = json.loads(metadata) if metadata else []
    except json.JSONDecodeError:
        raise HTTPException(400, "metadata 格式错误")
    
    # Match meta_list length to files, padding empty objects as needed.
    while len(meta_list) < len(files):
        meta_list.append({})
    
    results = []
    for i, (file, meta) in enumerate(zip(files, meta_list)):
        try:
            # Check file size.
            check_file_size(file, MAX_FILE_SIZE)

            # Validate extensions.
            file_ext = Path(file.filename).suffix.lower()
            if file_ext not in ALLOWED_ACTION_EXTENSIONS:
                results.append({"success": False, "error": f"不支持的格式: {file_ext}"})
                continue

            # Get original basenames without paths.
            original_filename = Path(file.filename).name

            # Generate safe filename-based IDs.
            action_id = sanitize_filename(file.filename)

            # Ensure unique IDs.
            base_id = action_id
            counter = 1
            while action_id in jukebox_config.data["actions"]:
                action_id = f"{base_id}_{counter}"
                counter += 1

            # Get unique filenames.
            target_filename = get_unique_filename(jukebox_config.actions_dir, original_filename)
            target_path = jukebox_config.actions_dir / target_filename

            with open(target_path, "wb") as buffer:
                await asyncio.to_thread(shutil.copyfileobj, file.file, buffer)

            # Compute MD5.
            # Hashing reads entire large files; offload to avoid blocking the event loop.
            file_md5 = await asyncio.to_thread(calculate_md5, target_path)

            # Check MD5 duplicates.
            existing_action_id = jukebox_config.data["md5Index"]["actions"].get(file_md5)
            if existing_action_id:
                target_path.unlink(missing_ok=True)
                results.append({"success": False, "error": f"动画已存在: {existing_action_id}"})
                continue

            # Default display names use supplied names or filenames.
            # Preserve numeric filename suffixes such as animation(1).vmd in default display names.
            if not isinstance(meta, dict):
                meta = {}
            action_name = meta.get("name") or Path(target_filename).stem

            # Create animation records.
            action = Action(
                id=action_id,
                name=action_name,
                file=f"actions/{target_filename}",
                fileMd5=file_md5,
                format=file_ext.lstrip("."),
                uploadDate=datetime.now().isoformat(),
                missing=False
            )
            
            # Save configuration.
            jukebox_config.data["actions"][action_id] = asdict(action)
            jukebox_config.data["md5Index"]["actions"][file_md5] = action_id
            
            results.append({"success": True, "action": asdict(action)})
            
        except Exception as e:
            logger.error(f"上传第 {i+1} 个动画失败: {e}")
            results.append({"success": False, "error": str(e)})
        finally:
            file.file.close()
    
    await jukebox_config.asave()
    
    # Return single-animation upload results directly, otherwise a list.
    if len(files) == 1:
        return results[0] if results else {"success": False, "error": "无文件上传"}
    return {"success": True, "results": results}


def _remove_action_bindings(jukebox_config: JukeboxConfig, action_id: str) -> None:
    """Remove action references from bindings and defaultAction fields."""
    for song_id, bindings in list(jukebox_config.data["bindings"].items()):
        if action_id in bindings:
            del bindings[action_id]
            song = jukebox_config.data["songs"].get(song_id)
            if song and song.get("defaultAction") == action_id:
                song["defaultAction"] = ""
                logger.info(f"清除默认动画: {song_id} (删除了动画 {action_id})")
        if not bindings:
            del jukebox_config.data["bindings"][song_id]


@router.post("/actions/batch-delete")
async def batch_delete_actions(request: BatchDeleteActionsRequest):
    """Delete uploaded actions and hide built-in actions in one validated batch."""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)

    action_ids = []
    seen = set()
    for action_id in request.actionIds:
        if action_id and action_id not in seen:
            action_ids.append(action_id)
            seen.add(action_id)

    if not action_ids:
        raise HTTPException(400, "未选择动画")

    missing_ids = [action_id for action_id in action_ids if action_id not in jukebox_config.data["actions"]]
    if missing_ids:
        raise HTTPException(404, f"动画不存在: {', '.join(missing_ids)}")

    deleted = []
    hidden = []
    failed = []

    for action_id in action_ids:
        action = jukebox_config.data["actions"][action_id]
        action_name = action.get("name") or action_id

        try:
            if action.get("isBuiltin", False):
                action["visible"] = False
                hidden.append({"actionId": action_id, "name": action_name})
                continue

            file_path = jukebox_config.jukebox_dir / action["file"]
            if file_path.exists():
                file_path.unlink()

            _remove_action_bindings(jukebox_config, action_id)

            action_md5 = action.get("fileMd5", "")
            if action_md5 and action_md5 in jukebox_config.data["md5Index"]["actions"]:
                del jukebox_config.data["md5Index"]["actions"][action_md5]

            del jukebox_config.data["actions"][action_id]
            deleted.append({"actionId": action_id, "name": action_name})
        except Exception as exc:
            logger.error(f"批量删除动画失败: {action_id}, error={exc}")
            failed.append({"actionId": action_id, "name": action_name, "error": str(exc)})

    if deleted or hidden:
        await jukebox_config.asave()

    failed_count = len(failed)
    return {
        "success": failed_count == 0,
        "partial": failed_count > 0 and (len(deleted) > 0 or len(hidden) > 0),
        "requestedCount": len(action_ids),
        "deletedCount": len(deleted),
        "hiddenCount": len(hidden),
        "failedCount": failed_count,
        "deleted": deleted,
        "hidden": hidden,
        "failed": failed,
    }


@router.delete("/actions/{action_id}")
async def delete_action(action_id: str):
    """Delete an action."""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)

    if action_id not in jukebox_config.data["actions"]:
        raise HTTPException(404, "动画不存在")

    action = jukebox_config.data["actions"][action_id]

    # Hide built-ins without deleting assets.
    if action.get("isBuiltin", False):
        action["visible"] = False
        await jukebox_config.asave()
        logger.info(f"隐藏内置动画: {action_id}")
        return {"success": True, "message": "内置动画已隐藏", "hidden": True}

    # Completely delete user assets.
    # Delete files.
    file_path = jukebox_config.jukebox_dir / action["file"]
    if file_path.exists():
        file_path.unlink()

    _remove_action_bindings(jukebox_config, action_id)

    # Remove from MD5 indexes.
    action_md5 = action.get("fileMd5", "")
    if action_md5 and action_md5 in jukebox_config.data["md5Index"]["actions"]:
        del jukebox_config.data["md5Index"]["actions"][action_md5]

    # Delete animation records.
    del jukebox_config.data["actions"][action_id]
    await jukebox_config.asave()

    logger.info(f"删除动画: {action_id}")
    return {"success": True}


@router.post("/bind")
async def bind_song_action(
    songId: str = Form(...),
    actionId: str = Form(...),
    offset: int = Form(0)
):
    """Create a binding between a song and an action (ID-based)."""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)

    # Validate existence.
    if songId not in jukebox_config.data["songs"]:
        raise HTTPException(404, "歌曲不存在")
    if actionId not in jukebox_config.data["actions"]:
        raise HTTPException(404, "动画不存在")
    
    # Create ID-keyed bindings.
    # Binding structure: bindings[songId][actionId] = {"offset": 0}.
    if songId not in jukebox_config.data["bindings"]:
        jukebox_config.data["bindings"][songId] = {}
    
    jukebox_config.data["bindings"][songId][actionId] = {"offset": offset}
    
    # Automatically default the first bound animation of each type.
    song = jukebox_config.data["songs"][songId]
    action = jukebox_config.data["actions"][actionId]
    action_format = action.get("format", "vmd").lower()

    # Check existing defaults of this type.
    current_default = song.get("defaultAction", "")
    if current_default:
        default_action = jukebox_config.data["actions"].get(current_default)
        if default_action:
            default_format = default_action.get("format", "vmd").lower()
            # Preserve existing same-type defaults.
            if default_format == action_format:
                logger.info(f"歌曲 {songId} 已有 {action_format} 类型的默认动画，保持原有设置")
            else:
                # Different type: set as default.
                song["defaultAction"] = actionId
                logger.info(f"设置默认动画: {songId} -> {actionId} (类型: {action_format})")
        else:
            # Missing default animation: replace it.
            song["defaultAction"] = actionId
            logger.info(f"设置默认动画: {songId} -> {actionId} (原默认动画不存在)")
    else:
        # No default animation: set one.
        song["defaultAction"] = actionId
        logger.info(f"设置默认动画: {songId} -> {actionId} (首次绑定)")
    
    await jukebox_config.asave()
    
    logger.info(f"建立绑定: {songId} <-> {actionId}, offset={offset}")
    return {"success": True, "defaultAction": song.get("defaultAction", "")}


@router.delete("/bind")
async def unbind_song_action(
    songId: str = Form(...),
    actionId: str = Form(...)
):
    """Remove a binding between a song and an action (ID-based)."""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    # Validate existence.
    if songId not in jukebox_config.data["songs"]:
        raise HTTPException(404, "歌曲不存在")
    if actionId not in jukebox_config.data["actions"]:
        raise HTTPException(404, "动画不存在")
    
    # Remove bindings.
    if songId in jukebox_config.data["bindings"]:
        if actionId in jukebox_config.data["bindings"][songId]:
            del jukebox_config.data["bindings"][songId][actionId]
            
            # Remove empty dicts after the last binding disappears.
            if not jukebox_config.data["bindings"][songId]:
                del jukebox_config.data["bindings"][songId]
            
            # Clear default settings when unbinding the default animation.
            song = jukebox_config.data["songs"][songId]
            if song.get("defaultAction") == actionId:
                song["defaultAction"] = ""
                logger.info(f"清除默认动画: {songId} (解绑了默认动画 {actionId})")
            
            await jukebox_config.asave()
            logger.info(f"解除绑定: {songId} <-> {actionId}")
            return {"success": True, "defaultAction": song.get("defaultAction", "")}
    
    raise HTTPException(404, "绑定关系不存在")


@router.post("/export")
async def export_config(
    songIds: Optional[str] = Form(None),
    actionIds: Optional[str] = Form(None),
    includeHidden: bool = Form(True)
):
    """
    Export the config.
    songIds: JSON string array of song IDs to export. If None, exports all / non-hidden songs depending on includeHidden
    actionIds: JSON string array of action IDs to export (only for selective export). If None, exports all actions
    includeHidden: whether to include hidden songs. If False, only bindings of non-hidden songs are exported
    """
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)

    # Parse ID lists.
    try:
        selected_songs = json.loads(songIds) if songIds else None
        selected_actions = json.loads(actionIds) if actionIds else None
    except json.JSONDecodeError as err:
        raise HTTPException(400, f"导出参数格式错误: {err.msg}") from err

    if selected_songs is not None and not isinstance(selected_songs, list):
        raise HTTPException(400, "songIds 必须是 JSON 数组")
    if selected_actions is not None and not isinstance(selected_actions, list):
        raise HTTPException(400, "actionIds 必须是 JSON 数组")

    # Create manually managed temporary directories; TemporaryDirectory would clean up on return.
    temp_dir = tempfile.mkdtemp()
    temp_path = Path(temp_dir)
    export_dir = temp_path / "jukebox_export"
    export_dir.mkdir()

    try:
        # Prepare export data.
        export_data = {
            "version": jukebox_config.data["version"],
            "songs": {},
            "actions": {},
            "bindings": {}
        }

        # Export songs; empty selections [] are valid and mean export none.
        songs_to_export = selected_songs if selected_songs is not None else list(jukebox_config.data["songs"].keys())

        # Collect song IDs to export.
        song_ids_to_export = set()
        for song_id in songs_to_export:
            if song_id not in jukebox_config.data["songs"]:
                continue

            song = jukebox_config.data["songs"][song_id]

            # Skip nonexportable bundled assets.
            if song.get("isBuiltin", False):
                continue

            # Skip hidden songs unless included.
            if not includeHidden and not song.get("visible", True):
                continue

            song_ids_to_export.add(song_id)

        # Collect animation IDs for export:
        # - Export all: every animation, used or unused.
        # - Export selected: selected animations plus those used by selected songs.
        if selected_actions is not None:
            # Selected export combines selected animations with selected songs' animations.
            action_ids_to_export = set(selected_actions)
            for song_id in song_ids_to_export:
                if song_id in jukebox_config.data.get("bindings", {}):
                    for action_id in jukebox_config.data["bindings"][song_id]:
                        action_ids_to_export.add(action_id)
        else:
            # Export-all includes every animation.
            action_ids_to_export = set(jukebox_config.data["actions"].keys())

        # Export songs.
        for song_id in song_ids_to_export:
            song = jukebox_config.data["songs"][song_id]
            export_data["songs"][song_id] = song.copy()

            # Copy files.
            src_path = jukebox_config.jukebox_dir / song["audio"]
            if src_path.exists():
                dst_path = export_dir / song["audio"]
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(shutil.copy2, src_path, dst_path)

        # Export animations.
        for action_id in action_ids_to_export:
            if action_id not in jukebox_config.data["actions"]:
                continue

            action = jukebox_config.data["actions"][action_id]

            if not includeHidden and not action.get("visible", True):
                continue

            # For built-ins, export IDs/MD5 without packaging files.
            if action.get("isBuiltin", False):
                # Export necessary IDs/MD5/names only, excluding paths.
                export_data["actions"][action_id] = {
                    "id": action_id,
                    "name": action.get("name", ""),
                    "fileMd5": action.get("fileMd5", ""),
                    "isBuiltin": True  # Mark as built-in.
                }
                continue

            export_data["actions"][action_id] = action

            # Copy files.
            src_path = jukebox_config.jukebox_dir / action["file"]
            if src_path.exists():
                dst_path = export_dir / action["file"]
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(shutil.copy2, src_path, dst_path)

        if not includeHidden:
            for song in export_data["songs"].values():
                default_action = song.get("defaultAction", "")
                if default_action and default_action not in export_data["actions"]:
                    song["defaultAction"] = ""

        # Convert exported bindings from IDs to MD5 for cross-system imports.
        # Local format: bindings[songId][actionId] = {"offset": 0}.
        # Export format: bindings[songMd5][actionMd5] = {"offset": 0}.
        md5_bindings = {}

        # Build ID-to-MD5 mappings.
        song_id_to_md5 = {sid: s.get("audioMd5", "") for sid, s in jukebox_config.data["songs"].items()}
        action_id_to_md5 = {aid: a.get("fileMd5", "") for aid, a in jukebox_config.data["actions"].items()}

        # Export bindings:
        # - All excluding hidden: bindings used by visible songs only.
        # - All including hidden or selected: bindings used by exported songs.
        for song_id in jukebox_config.data.get("bindings", {}):
            # Selected exports include only selected songs' bindings.
            if selected_songs is not None and song_id not in song_ids_to_export:
                continue

            # Check whether songs belong in binding exports.
            song = jukebox_config.data["songs"].get(song_id)
            if not song:
                continue

            # Skip hidden-song bindings unless included.
            if not includeHidden and not song.get("visible", True):
                continue

            # Skip bundled-song bindings because bundled songs are nonexportable.
            if song.get("isBuiltin", False):
                continue

            song_md5 = song_id_to_md5.get(song_id, "")
            if not song_md5:
                continue

            # Export all this song's bindings, including built-in animation bindings.
            for action_id, binding_data in jukebox_config.data["bindings"][song_id].items():
                action = jukebox_config.data["actions"].get(action_id)
                if not action:
                    continue
                if not includeHidden and not action.get("visible", True):
                    continue

                action_md5 = action_id_to_md5.get(action_id, "")
                if action_md5:
                    if song_md5 not in md5_bindings:
                        md5_bindings[song_md5] = {}
                    md5_bindings[song_md5][action_md5] = {
                        "offset": binding_data.get("offset", 0)
                    }
        
        export_data["bindings"] = md5_bindings

        # Write configuration files.
        with open(export_dir / "config.json", "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)

        # Package ZIP archives.
        zip_path = temp_path / "jukebox_export.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in export_dir.rglob("*"):
                if file_path.is_file():
                    zf.write(file_path, file_path.relative_to(export_dir))

        # Stream responses rather than loading large files entirely into memory.
        # BackgroundTask cleans temporary directories after responses finish.
        return StreamingResponse(
            file_iterator(zip_path),
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=jukebox_export.zip"},
            background=BackgroundTask(cleanup_temp_path, str(temp_dir))
        )
    except Exception:
        # Clean temporary directories on exceptions.
        await asyncio.to_thread(cleanup_temp_path, str(temp_dir))
        raise


@router.get("/file/{file_path:path}")
async def get_file(file_path: str):
    """Get a song or action file.
    file_path: relative path, e.g. songs/song_001.mp3 or actions/action_001.vmd
    Prefers the user documents directory; falls back to the bundled directory if absent.
    """
    target_path = resolve_jukebox_file_path(file_path)
    return FileResponse(target_path, media_type=get_jukebox_media_type(target_path))


@router.head("/file/{file_path:path}")
async def head_file(file_path: str):
    """Check whether a song or action file exists without streaming it."""
    target_path = resolve_jukebox_file_path(file_path)
    return Response(
        status_code=200,
        media_type=get_jukebox_media_type(target_path),
        headers={"Content-Length": str(target_path.stat().st_size)}
    )


@router.post("/import")
async def import_config(file: UploadFile = File(...)):
    """Import a config (MD5-level bindings)."""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)

    # Check archive sizes against 10 GB limits.
    check_file_size(file, MAX_ZIP_SIZE)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        zip_path = temp_path / "import.zip"

        with open(zip_path, "wb") as buffer:
            await asyncio.to_thread(shutil.copyfileobj, file.file, buffer)
        
        extract_dir = temp_path / "extracted"
        extract_dir.mkdir()

        # Extract ZIP safely against Zip Slip.
        try:
            zf = zipfile.ZipFile(zip_path, "r")
        except zipfile.BadZipFile as err:
            raise HTTPException(400, "导入文件不是有效的 ZIP 压缩包") from err

        with zf:
            # Bound total extracted size against ZIP bombs.
            total_uncompressed = sum(info.file_size for info in zf.infolist())
            max_uncompressed = MAX_ZIP_SIZE
            if total_uncompressed > max_uncompressed:
                raise HTTPException(400, f"解压后总大小超出限制 ({total_uncompressed} > {max_uncompressed})")

            for member in zf.namelist():
                # Validate path safety.
                member_path = (extract_dir / member).resolve()
                try:
                    member_path.relative_to(extract_dir.resolve())
                except ValueError as err:
                    raise HTTPException(400, f"导入包包含非法路径: {member}") from err

                # Extract safely.
                if member.endswith('/'):
                    member_path.mkdir(parents=True, exist_ok=True)
                else:
                    member_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(member_path, 'wb') as dst:
                        await asyncio.to_thread(shutil.copyfileobj, src, dst)
        
        config_path = extract_dir / "config.json"
        if not config_path.exists():
            raise HTTPException(400, "导入包中缺少 config.json")
        
        try:
            import_data = await read_json_async(config_path)
        except json.JSONDecodeError as err:
            raise HTTPException(400, "config.json 不是有效的 JSON") from err
        
        stats = {
            "songsAdded": 0,
            "songsMerged": 0,
            "actionsAdded": 0,
            "actionsMerged": 0,
            "bindingsAdded": 0
        }

        # Map imported IDs to local IDs for defaultAction handling.
        id_mapping = {
            "songs": {},  # old_song_id -> new_song_id
            "actions": {}  # old_action_id -> new_action_id
        }

        # Step 1: import songs.
        for song_id, song in import_data.get("songs", {}).items():
            # Validate path safety.
            try:
                src_audio = validate_extract_path(song["audio"], extract_dir)
            except HTTPException as e:
                logger.warning(f"跳过非法路径的歌曲: {song_id}, 路径: {song['audio']}, 错误: {e.detail}")
                continue

            # Compute actual-file MD5; never trust configured digests.
            if src_audio.exists():
                file_md5 = await asyncio.to_thread(calculate_md5, src_audio)
                song["audioMd5"] = file_md5
            else:
                file_md5 = song.get("audioMd5", "")

            existing_id = jukebox_config.data["md5Index"]["songs"].get(file_md5) if file_md5 else None

            if existing_id:
                stats["songsMerged"] += 1
                # Record ID mappings for defaultAction handling.
                id_mapping["songs"][song_id] = existing_id
            else:
                if src_audio.exists():
                    original_filename = Path(song["audio"]).name
                    target_filename = get_unique_filename(jukebox_config.songs_dir, original_filename)
                    dst_audio = jukebox_config.songs_dir / target_filename
                    dst_audio.parent.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(shutil.copy2, src_audio, dst_audio)

                    new_id = sanitize_filename(target_filename)
                    base_id = new_id
                    counter = 1
                    while new_id in jukebox_config.data["songs"]:
                        new_id = f"{base_id}_{counter}"
                        counter += 1

                    song["id"] = new_id
                    song["audio"] = f"songs/{target_filename}"
                    original_stem = Path(original_filename).stem
                    if song.get("name") == original_stem:
                        song["name"] = Path(target_filename).stem

                    # Clear imported defaultAction IDs, invalid locally.
                    # Steps 3/4 restore defaults through bindings.
                    song.pop("defaultAction", None)
                    jukebox_config.data["songs"][new_id] = song
                    jukebox_config.data["md5Index"]["songs"][file_md5] = new_id
                    stats["songsAdded"] += 1

                    # Record ID mappings for defaultAction handling.
                    id_mapping["songs"][song_id] = new_id
        
        # Step 2: import animations.
        for action_id, action in import_data.get("actions", {}).items():
            # Resolve built-in actions to their local equivalents.
            if action.get("isBuiltin", False):
                file_md5 = action.get("fileMd5", "")
                # Find local built-ins by MD5.
                local_action_id = jukebox_config.data["md5Index"]["actions"].get(file_md5)
                if local_action_id:
                    local_action = jukebox_config.data["actions"].get(local_action_id)
                    if local_action and local_action.get("isBuiltin", False):
                        # Record mappings for matching local built-ins.
                        id_mapping["actions"][action_id] = local_action_id
                        stats["actionsMerged"] += 1
                        logger.info(f"导入映射内置动作: {action_id} -> {local_action_id}")
                continue

            # Validate path safety.
            try:
                src_file = validate_extract_path(action["file"], extract_dir)
            except HTTPException as e:
                logger.warning(f"跳过非法路径的动画: {action_id}, 路径: {action['file']}, 错误: {e.detail}")
                continue

            # Compute actual-file MD5; never trust configured digests.
            if src_file.exists():
                file_md5 = await asyncio.to_thread(calculate_md5, src_file)
                action["fileMd5"] = file_md5
            else:
                file_md5 = action.get("fileMd5", "")

            existing_id = jukebox_config.data["md5Index"]["actions"].get(file_md5) if file_md5 else None

            if existing_id:
                stats["actionsMerged"] += 1
                # Record ID mappings for defaultAction handling.
                id_mapping["actions"][action_id] = existing_id
            else:
                if src_file.exists():
                    original_filename = Path(action["file"]).name
                    target_filename = get_unique_filename(jukebox_config.actions_dir, original_filename)
                    dst_file = jukebox_config.actions_dir / target_filename
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(shutil.copy2, src_file, dst_file)

                    new_id = sanitize_filename(target_filename)
                    base_id = new_id
                    counter = 1
                    while new_id in jukebox_config.data["actions"]:
                        new_id = f"{base_id}_{counter}"
                        counter += 1

                    action["id"] = new_id
                    action["file"] = f"actions/{target_filename}"
                    original_stem = Path(original_filename).stem
                    if action.get("name") == original_stem:
                        action["name"] = Path(target_filename).stem

                    jukebox_config.data["actions"][new_id] = action
                    jukebox_config.data["md5Index"]["actions"][file_md5] = new_id
                    stats["actionsAdded"] += 1

                    # Record ID mappings for defaultAction handling.
                    id_mapping["actions"][action_id] = new_id
        
        # Step 3: import MD5 bindings, converting to ID-based storage.
        # Import format: bindings[songMd5][actionMd5] = {"offset": 0}.
        # Storage format: bindings[songId][actionId] = {"offset": 0}.
        for song_md5, action_bindings in import_data.get("bindings", {}).items():
            # Find local song IDs by MD5.
            song_id = jukebox_config.data["md5Index"]["songs"].get(song_md5)
            if not song_id:
                continue  # Missing local song: skip binding.
            
            # Ensure songs exist in binding indexes.
            if song_id not in jukebox_config.data["bindings"]:
                jukebox_config.data["bindings"][song_id] = {}
            
            for action_md5, binding_data in action_bindings.items():
                # Find local animation IDs by MD5.
                action_id = jukebox_config.data["md5Index"]["actions"].get(action_md5)
                if not action_id:
                    continue  # Missing local animation: skip binding.
                
                # Add absent ID-based bindings.
                if action_id not in jukebox_config.data["bindings"][song_id]:
                    jukebox_config.data["bindings"][song_id][action_id] = {
                        "offset": binding_data.get("offset", 0)
                    }
                    stats["bindingsAdded"] += 1
                    
                    # Set default animations automatically.
                    song = jukebox_config.data["songs"][song_id]
                    current_default = song.get("defaultAction", "")
                    
                    if not current_default:
                        song["defaultAction"] = action_id
                        logger.info(f"导入设置默认动画: {song_id} -> {action_id} (首次绑定)")
                    else:
                        # Check existing default-animation existence.
                        if current_default not in jukebox_config.data["actions"]:
                            song["defaultAction"] = action_id
                            logger.info(f"导入设置默认动画: {song_id} -> {action_id} (原默认动画不存在)")

        # Step 4: map defaultAction.
        # Use imported ID mappings to convert old defaultAction to new IDs.
        for old_song_id, song in import_data.get("songs", {}).items():
            old_default_action = song.get("defaultAction", "")
            if not old_default_action:
                continue

            # Get local song IDs.
            local_song_id = id_mapping["songs"].get(old_song_id)
            if not local_song_id:
                continue

            # Get local animation IDs.
            local_action_id = id_mapping["actions"].get(old_default_action)
            if not local_action_id:
                continue

            # Check binding existence to ensure valid defaultAction.
            local_song = jukebox_config.data["songs"].get(local_song_id)
            if not local_song:
                continue

            # Check animation/song binding.
            bindings = jukebox_config.data["bindings"].get(local_song_id, {})
            if local_action_id in bindings:
                local_song["defaultAction"] = local_action_id
                logger.info(f"导入映射默认动画: {local_song_id} -> {local_action_id}")

        await jukebox_config.asave()
        
        logger.info(f"导入完成: {stats}")
        return {"success": True, "stats": stats}


@router.post("/pack-folder")
async def pack_folder(files: List[UploadFile] = File(...)):
    """Pack the files in a folder into a ZIP."""
    # Create manually managed temporary directories; TemporaryDirectory would clean up on return.
    temp_dir = tempfile.mkdtemp()
    temp_path = Path(temp_dir)

    try:
        # Save every file to temporary directories.
        for file in files:
            # Check file size.
            check_file_size(file, MAX_FILE_SIZE)

            # Allow relative paths while preventing traversal.
            relative_path = Path(file.filename)
            if relative_path.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.parts):
                raise HTTPException(400, f"文件名包含非法路径: {file.filename}")

            file_path = (temp_path / relative_path).resolve()
            try:
                file_path.relative_to(temp_path.resolve())
            except ValueError as err:
                raise HTTPException(400, f"文件名包含非法路径: {file.filename}") from err
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "wb") as f:
                await asyncio.to_thread(shutil.copyfileobj, file.file, f)

        # Create ZIP files.
        zip_path = temp_path / "packed.zip"
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_path in temp_path.rglob('*'):
                if file_path.is_file() and file_path.name != 'packed.zip':
                    arcname = file_path.relative_to(temp_path)
                    zf.write(file_path, arcname)

        # Stream responses rather than loading large files entirely into memory.
        # BackgroundTask cleans temporary directories after responses finish.
        return StreamingResponse(
            file_iterator(zip_path),
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=packed.zip"},
            background=BackgroundTask(cleanup_temp_path, str(temp_dir))
        )
    except Exception:
        # Clean temporary directories on exceptions.
        await asyncio.to_thread(cleanup_temp_path, str(temp_dir))
        raise
