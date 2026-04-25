"""
Music Pusher

能力概览：
1) 本地上传音频并可选自动推送。
2) 在线音频链接入库与即时推送。
3) 定时任务队列，支持立即执行、编辑、删除、运行态查询。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

try:
    import av  # type: ignore
except Exception:  # pragma: no cover
    av = None

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    Ok,
    Err,
    SdkError,
)

_ALLOWED_AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac",
})
_ALLOWED_LYRIC_EXTENSIONS = frozenset({".irc", ".lrc", ".txt"})
_DEFAULT_MAX_UPLOAD_TB = 3
_DEFAULT_MAX_AUDIO_SIZE_BYTES = _DEFAULT_MAX_UPLOAD_TB * 1024 * 1024 * 1024 * 1024
_UPLOAD_SUBDIR = "uploads"
_DEFAULT_PLUGIN_SERVER_PORT = 48916
_STATE_FILE_NAME = "scheduler_state.json"
_UPLOAD_NAME_MAP_FILE_NAME = "upload_name_map.json"
_LYRIC_MAP_FILE_NAME = "lyrics_map.json"
_SCHEDULER_TICK_SECONDS = 1.0
_PUSH_TIMEOUT_SECONDS = 6.0
_DEFAULT_TRACK_DURATION_SECONDS = 28
_MIN_DURATION_SECONDS = 1
_DEFAULT_MAX_DURATION_HOURS = 20000
_DEFAULT_MAX_DURATION_SECONDS = _DEFAULT_MAX_DURATION_HOURS * 3600
_AV_TIME_BASE = 1000000.0
_LYRIC_PUSH_MAX_CHARS = 18000
_LYRIC_PUSH_TARGET_LINES = 5
_DEFAULT_ATTACH_PROMPT_ON_PUSH = True
_PROACTIVE_PROMPT_EXPIRE_SECONDS = 20

_CTRL_STOP = "stop"
_CTRL_NEXT = "next"
_CTRL_PREV = "prev"


def _env_positive_int(name: str) -> int | None:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return None
    try:
        val = int(float(raw))
    except Exception:
        return None
    return val if val > 0 else None


def _read_max_audio_size_bytes() -> int:
    by_bytes = _env_positive_int("MUSIC_PUSHER_MAX_UPLOAD_BYTES")
    if by_bytes is not None:
        return by_bytes

    raw_tb = str(os.getenv("MUSIC_PUSHER_MAX_UPLOAD_TB", "") or "").strip()
    if raw_tb:
        try:
            tb = float(raw_tb)
            if tb > 0:
                return int(tb * 1024 * 1024 * 1024 * 1024)
        except Exception:
            pass

    return _DEFAULT_MAX_AUDIO_SIZE_BYTES


def _read_max_duration_seconds() -> int:
    by_seconds = _env_positive_int("MUSIC_PUSHER_MAX_DURATION_SECONDS")
    if by_seconds is not None:
        return by_seconds

    raw_hours = str(os.getenv("MUSIC_PUSHER_MAX_DURATION_HOURS", "") or "").strip()
    if raw_hours:
        try:
            hours = float(raw_hours)
            if hours > 0:
                return int(hours * 3600)
        except Exception:
            pass

    return _DEFAULT_MAX_DURATION_SECONDS


_MAX_AUDIO_SIZE_BYTES = _read_max_audio_size_bytes()
_MAX_DURATION_SECONDS = _read_max_duration_seconds()


def _format_bytes(size_bytes: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    val = float(max(0, size_bytes))
    idx = 0
    while val >= 1024 and idx < len(units) - 1:
        val /= 1024.0
        idx += 1
    return f"{val:.2f} {units[idx]}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    val = dt or _utc_now()
    return val.astimezone(timezone.utc).isoformat()


def _parse_datetime_to_utc(raw: str) -> datetime:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("时间不能为空")

    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        dt = dt.replace(tzinfo=local_tz)
    return dt.astimezone(timezone.utc)


def _normalize_duration(raw: object, default: int = _DEFAULT_TRACK_DURATION_SECONDS) -> int:
    if isinstance(raw, bool):
        return default
    if isinstance(raw, (int, float)):
        sec = int(raw)
    elif isinstance(raw, str):
        try:
            sec = int(float(raw.strip()))
        except Exception:
            sec = default
    else:
        sec = default

    if sec < _MIN_DURATION_SECONDS:
        return _MIN_DURATION_SECONDS
    if sec > _MAX_DURATION_SECONDS:
        return _MAX_DURATION_SECONDS
    return sec


def _strip_data_uri(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("data:") and "," in raw:
        return raw.split(",", 1)[1]
    return raw


def _decode_audio(audio_base64: str, filename: str) -> tuple[bytes | None, str, str]:
    if not isinstance(audio_base64, str) or not audio_base64.strip():
        return None, "", "audio_base64 不能为空"

    normalized_name = (filename or "track.mp3").strip()
    ext = Path(normalized_name).suffix.lower()
    if ext not in _ALLOWED_AUDIO_EXTENSIONS:
        return None, "", (
            f"不支持的音频格式 '{ext}'。"
            f"支持的格式: {', '.join(sorted(_ALLOWED_AUDIO_EXTENSIONS))}"
        )

    try:
        binary = base64.b64decode(_strip_data_uri(audio_base64), validate=True)
    except Exception as exc:
        return None, "", f"Base64 解码失败: {exc}"

    if len(binary) > _MAX_AUDIO_SIZE_BYTES:
        return None, "", (
            f"音频大小 {_format_bytes(len(binary))} 超过限制 {_format_bytes(_MAX_AUDIO_SIZE_BYTES)}"
        )

    return binary, ext, ""


def _decode_optional_lyric(lyric_base64: str | None, filename: str | None) -> tuple[str, str, str]:
    raw_data = str(lyric_base64 or "").strip()
    if not raw_data:
        return "", "", ""

    normalized_name = (filename or "lyrics.lrc").strip()
    ext = Path(normalized_name).suffix.lower()
    if ext not in _ALLOWED_LYRIC_EXTENSIONS:
        return "", "", f"不支持的歌词格式 '{ext}'，支持: {', '.join(sorted(_ALLOWED_LYRIC_EXTENSIONS))}"

    try:
        binary = base64.b64decode(_strip_data_uri(raw_data), validate=True)
    except Exception as exc:
        return "", "", f"歌词 Base64 解码失败: {exc}"

    if not binary:
        return "", "", "歌词文件为空"

    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            text = binary.decode(enc)
            break
        except Exception:
            text = ""
    if not text:
        return "", "", "歌词文件编码无法识别"

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(normalized) > _LYRIC_PUSH_MAX_CHARS:
        normalized = normalized[:_LYRIC_PUSH_MAX_CHARS]
    return normalized, Path(normalized_name).name, ""


def _clean_lyric_line(raw_line: str) -> str:
    line = str(raw_line or "").strip()
    if not line:
        return ""
    if re.match(r"^\[(ti|ar|al|by|offset|re|ve)\s*:", line, flags=re.IGNORECASE):
        return ""
    line = re.sub(r"\[[0-9]{1,2}:[0-9]{1,2}(?:\.[0-9]{1,3})?\]", "", line)
    line = re.sub(r"<[0-9]{1,2}:[0-9]{1,2}(?:\.[0-9]{1,3})?>", "", line)
    line = re.sub(r"\s+", " ", line).strip()
    return line


def _pick_lyric_excerpt(lyric_text: str) -> tuple[str, int, int]:
    cleaned_lines = []
    for raw in str(lyric_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _clean_lyric_line(raw)
        if line:
            cleaned_lines.append(line)

    if not cleaned_lines:
        return "", 0, 0

    total = len(cleaned_lines)
    want = max(1, int(_LYRIC_PUSH_TARGET_LINES))

    if total <= want:
        return "\n".join(cleaned_lines), total, 1

    max_start = max(0, total - want)
    start_idx = random.randint(0, max_start) if max_start > 0 else 0
    excerpt = cleaned_lines[start_idx:start_idx + want]
    return "\n".join(excerpt), len(excerpt), start_idx + 1


def _normalize_duration_mode(raw: object) -> str:
    text = str(raw or "").strip().lower()
    if text in {"manual", "manual_override"}:
        return "manual"
    return "auto_sum"


def _safe_text(raw: object, fallback: str) -> str:
    text = str(raw or "").strip()
    return text or fallback


def _normalize_song_lookup_text(raw: object) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"[·•・]", "", text)
    text = re.sub(r"[\s\-_()（）\[\]【】《》<>\"'`~!@#$%^&*+=|\\/:;,.?，。！？、]+", "", text)
    return text


def _coerce_bool(raw: object, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _calc_total_duration(queue: list[dict[str, Any]]) -> int:
    return sum(_normalize_duration(it.get("duration_sec"), 0) for it in queue)


def _extract_duration_from_av_container(container: Any) -> int | None:
    container_duration = getattr(container, "duration", None)
    if container_duration:
        sec = float(container_duration) / _AV_TIME_BASE
        if sec > 0:
            return _normalize_duration(round(sec), default=_DEFAULT_TRACK_DURATION_SECONDS)

    for stream in container.streams.audio:
        if stream.duration is not None and stream.time_base is not None:
            sec = float(stream.duration * stream.time_base)
            if sec > 0:
                return _normalize_duration(round(sec), default=_DEFAULT_TRACK_DURATION_SECONDS)
    return None


@neko_plugin
class MusicPusherPlugin(NekoPluginBase):
    """音乐推送 + 定时队列插件。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self._state_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        self._scheduler_stop = asyncio.Event()
        self._scheduler_task: asyncio.Task | None = None
        self._task_timers: dict[str, asyncio.Task] = {}

        self._music_items: list[dict[str, Any]] = []
        self._tasks: dict[str, dict[str, Any]] = {}
        self._upload_name_map: dict[str, dict[str, Any]] = {}
        self._lyrics_map: dict[str, dict[str, Any]] = {}

        self._active_task_id: str | None = None
        self._active_track: dict[str, Any] | None = None
        self._active_track_started_at: float | None = None
        self._active_track_duration: int = 0
        self._active_control: str | None = None
        self._active_execution_task: asyncio.Task | None = None
        self._original_push_message = None
        self._push_mapper_installed = False
        self._attach_prompt_on_push = _DEFAULT_ATTACH_PROMPT_ON_PUSH

        self._playback_state: dict[str, Any] = {
            "status": "idle",
            "position_sec": 0.0,
            "duration_sec": 0.0,
            "updated_at": _iso(),
            "track_url": "",
            "track_title": "",
        }

    @property
    def _state_file(self) -> Path:
        return self.data_path(_STATE_FILE_NAME)

    @property
    def _upload_dir(self) -> Path:
        return self.config_dir / "static" / _UPLOAD_SUBDIR

    @property
    def _upload_name_map_file(self) -> Path:
        return self.data_path(_UPLOAD_NAME_MAP_FILE_NAME)

    @property
    def _lyrics_map_file(self) -> Path:
        return self.data_path(_LYRIC_MAP_FILE_NAME)

    def _build_ui_file_url(self, stored_filename: str) -> str:
        return f"/plugin/{self.plugin_id}/ui/{_UPLOAD_SUBDIR}/{stored_filename}"

    def _resolve_public_origin(self) -> str:
        candidates = (
            os.getenv("NEKO_PLUGIN_SERVER_ORIGIN", ""),
            os.getenv("NEKO_USER_PLUGIN_SERVER_ORIGIN", ""),
            os.getenv("NEKO_SERVER_ORIGIN", ""),
        )
        for raw in candidates:
            val = str(raw or "").strip().rstrip("/")
            if val.startswith("http://") or val.startswith("https://"):
                return val

        try:
            from config import USER_PLUGIN_SERVER_PORT

            port = int(USER_PLUGIN_SERVER_PORT)
            if 1 <= port <= 65535:
                return f"http://127.0.0.1:{port}"
        except Exception:
            pass

        try:
            env_port = int(os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", "").strip())
            if 1 <= env_port <= 65535:
                return f"http://127.0.0.1:{env_port}"
        except Exception:
            pass

        return f"http://127.0.0.1:{_DEFAULT_PLUGIN_SERVER_PORT}"

    def _to_absolute_ui_url(self, maybe_relative_url: str) -> str:
        url = str(maybe_relative_url or "").strip()
        if not url:
            return ""
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if not url.startswith("/"):
            url = f"/{url}"
        return f"{self._resolve_public_origin()}{url}"

    def _normalize_legacy_url(self, url: str) -> str:
        text = str(url or "").strip()
        if not text:
            return ""
        text = text.replace("/plugin/emoji_pusher/ui/", "/plugin/music_pusher/ui/")
        return self._to_absolute_ui_url(text) if text.startswith("/") else text

    def _save_upload_file(self, binary: bytes, ext: str) -> tuple[str, str]:
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        stored_filename = f"{uuid4().hex[:16]}{ext}"
        full_path = self._upload_dir / stored_filename
        full_path.write_bytes(binary)
        return stored_filename, self._build_ui_file_url(stored_filename)

    def _detect_audio_duration_seconds(self, binary: bytes) -> int | None:
        if av is None:
            return None
        try:
            with av.open(io.BytesIO(binary)) as container:
                return _extract_duration_from_av_container(container)
        except Exception:
            return None
        return None

    def _detect_audio_duration_from_url(self, url: str) -> int | None:
        if av is None:
            return None
        try:
            with av.open(url, mode="r", options={"rw_timeout": "5000000"}) as container:
                return _extract_duration_from_av_container(container)
        except Exception:
            return None

    def _build_music_item(
        self,
        *,
        url: str,
        title: str,
        artist: str,
        source: str,
        duration_sec: int,
        stored_filename: str = "",
        original_filename: str = "",
    ) -> dict[str, Any]:
        now_iso = _iso()
        return {
            "item_id": f"itm_{uuid4().hex[:10]}",
            "url": self._normalize_legacy_url(url),
            "title": title,
            "artist": artist,
            "source": source,
            "stored_filename": stored_filename,
            "original_filename": original_filename,
            "duration_sec": _normalize_duration(duration_sec),
            "created_at": now_iso,
            "updated_at": now_iso,
        }

    def _load_upload_name_map_locked(self) -> None:
        path = self._upload_name_map_file
        self._upload_name_map = {}
        if not path.exists():
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.logger.warning(f"读取上传映射失败，将重建: {exc}")
            return

        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            return

        cleaned: dict[str, dict[str, Any]] = {}
        for key, raw in entries.items():
            stored_filename = str(key or "").strip()
            if not stored_filename or stored_filename != Path(stored_filename).name:
                continue
            if not isinstance(raw, dict):
                continue
            title = _safe_text(raw.get("title"), Path(stored_filename).stem)
            artist = _safe_text(raw.get("artist"), "未知")
            original_filename = _safe_text(raw.get("original_filename"), stored_filename)
            cleaned[stored_filename] = {
                "stored_filename": stored_filename,
                "original_filename": original_filename,
                "title": title,
                "artist": artist,
                "duration_sec": _normalize_duration(raw.get("duration_sec")),
                "created_at": _safe_text(raw.get("created_at"), _iso()),
                "updated_at": _safe_text(raw.get("updated_at"), _iso()),
            }
        self._upload_name_map = cleaned

    def _save_upload_name_map_locked(self) -> None:
        self._upload_name_map_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "entries": self._upload_name_map,
            "updated_at": _iso(),
        }
        self._upload_name_map_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _upsert_upload_name_map_locked(
        self,
        *,
        stored_filename: str,
        original_filename: str,
        title: str,
        artist: str,
        duration_sec: object,
    ) -> None:
        key = str(stored_filename or "").strip()
        if not key:
            return
        prev = self._upload_name_map.get(key) or {}
        created_at = _safe_text(prev.get("created_at"), _iso())
        self._upload_name_map[key] = {
            "stored_filename": key,
            "original_filename": _safe_text(original_filename, key),
            "title": _safe_text(title, Path(key).stem),
            "artist": _safe_text(artist, "未知"),
            "duration_sec": _normalize_duration(duration_sec),
            "created_at": created_at,
            "updated_at": _iso(),
        }

    def _load_lyrics_map_locked(self) -> None:
        self._lyrics_map = {}
        path = self._lyrics_map_file
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.logger.warning(f"读取歌词映射失败，将重建: {exc}")
            return

        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            return

        cleaned: dict[str, dict[str, Any]] = {}
        for item_id, raw in entries.items():
            key = str(item_id or "").strip()
            if not key or not isinstance(raw, dict):
                continue
            lyric_text = str(raw.get("lyric_text") or "").replace("\r\n", "\n").strip()
            if not lyric_text:
                continue
            if len(lyric_text) > _LYRIC_PUSH_MAX_CHARS:
                lyric_text = lyric_text[:_LYRIC_PUSH_MAX_CHARS]
            cleaned[key] = {
                "item_id": key,
                "lyric_text": lyric_text,
                "lyric_filename": _safe_text(raw.get("lyric_filename"), "lyrics.lrc"),
                "created_at": _safe_text(raw.get("created_at"), _iso()),
                "updated_at": _safe_text(raw.get("updated_at"), _iso()),
            }
        self._lyrics_map = cleaned

    def _save_lyrics_map_locked(self) -> None:
        self._lyrics_map_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "entries": self._lyrics_map,
            "updated_at": _iso(),
        }
        self._lyrics_map_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _bind_lyrics_to_item_locked(self, *, item_id: str, lyric_text: str, lyric_filename: str) -> None:
        clean_id = str(item_id or "").strip()
        clean_text = str(lyric_text or "").replace("\r\n", "\n").strip()
        if not clean_id:
            return
        if not clean_text:
            self._lyrics_map.pop(clean_id, None)
            return
        prev = self._lyrics_map.get(clean_id) or {}
        self._lyrics_map[clean_id] = {
            "item_id": clean_id,
            "lyric_text": clean_text[:_LYRIC_PUSH_MAX_CHARS],
            "lyric_filename": _safe_text(lyric_filename, "lyrics.lrc"),
            "created_at": _safe_text(prev.get("created_at"), _iso()),
            "updated_at": _iso(),
        }

    def _extract_lyric_for_item_locked(self, item_id: str | None) -> str:
        key = str(item_id or "").strip()
        if not key:
            return ""
        meta = self._lyrics_map.get(key) or {}
        return str(meta.get("lyric_text") or "")

    def _find_music_item_locked(
        self,
        *,
        item_id: str | None = None,
        url: str | None = None,
        title: str | None = None,
        artist: str | None = None,
    ) -> dict[str, Any] | None:
        key = str(item_id or "").strip()
        if key:
            for it in self._music_items:
                if str(it.get("item_id") or "").strip() == key:
                    return it

        norm_url = self._normalize_legacy_url(str(url or "").strip())
        if norm_url:
            for it in self._music_items:
                if self._normalize_legacy_url(str(it.get("url") or "").strip()) == norm_url:
                    return it

        t = str(title or "").strip().lower()
        a = str(artist or "").strip().lower()
        if t or a:
            for it in self._music_items:
                it_t = str(it.get("title") or "").strip().lower()
                it_a = str(it.get("artist") or "").strip().lower()
                if t and a and it_t == t and it_a == a:
                    return it
                if t and not a and it_t == t:
                    return it

        return None

    def _match_music_item_by_name_locked(
        self,
        *,
        title: str | None = None,
        artist: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        query_title = str(title or "").strip()
        query_artist = str(artist or "").strip()
        title_key = _normalize_song_lookup_text(query_title)
        artist_key = _normalize_song_lookup_text(query_artist)
        if not title_key:
            return None, "empty_title"

        exact: list[dict[str, Any]] = []
        partial: list[dict[str, Any]] = []
        for it in self._music_items:
            it_title_key = _normalize_song_lookup_text(it.get("title"))
            it_artist_key = _normalize_song_lookup_text(it.get("artist"))
            if not it_title_key:
                continue
            if it_title_key == title_key:
                if artist_key and it_artist_key != artist_key:
                    continue
                exact.append(it)
                continue
            if artist_key and it_artist_key != artist_key:
                continue
            if title_key in it_title_key or it_title_key in title_key:
                partial.append(it)

        if len(exact) == 1:
            return exact[0], "exact"
        if len(exact) > 1:
            return None, "ambiguous_exact"
        if len(partial) == 1:
            return partial[0], "partial_unique"
        if len(partial) > 1:
            return None, "ambiguous_partial"
        return None, "not_found"

    def _rebuild_music_items_from_uploads_locked(self) -> None:
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        upload_files = {
            p.name: p
            for p in self._upload_dir.iterdir()
            if p.is_file() and p.suffix.lower() in _ALLOWED_AUDIO_EXTENSIONS
        }

        # 仅保留仍存在文件的映射，避免长期积累脏数据。
        self._upload_name_map = {
            name: meta
            for name, meta in self._upload_name_map.items()
            if name in upload_files
        }

        by_stored: dict[str, dict[str, Any]] = {}
        for item in self._music_items:
            stored = str(item.get("stored_filename") or "").strip()
            if stored:
                by_stored[stored] = item

        for stored_name, item in list(by_stored.items()):
            if stored_name not in upload_files:
                continue
            mapped = self._upload_name_map.get(stored_name)
            if mapped is None:
                self._upsert_upload_name_map_locked(
                    stored_filename=stored_name,
                    original_filename=_safe_text(item.get("original_filename"), stored_name),
                    title=_safe_text(item.get("title"), Path(stored_name).stem),
                    artist=_safe_text(item.get("artist"), "未知"),
                    duration_sec=item.get("duration_sec"),
                )
                mapped = self._upload_name_map.get(stored_name)

            if mapped:
                item["title"] = _safe_text(mapped.get("title"), _safe_text(item.get("title"), Path(stored_name).stem))
                item["artist"] = _safe_text(mapped.get("artist"), _safe_text(item.get("artist"), "未知"))
                item["original_filename"] = _safe_text(mapped.get("original_filename"), stored_name)
                item["duration_sec"] = _normalize_duration(item.get("duration_sec"))
                item["updated_at"] = _iso()

        for stored_name in upload_files:
            if stored_name in by_stored:
                continue

            mapped = self._upload_name_map.get(stored_name)
            if mapped is None:
                guessed_title = Path(stored_name).stem
                self._upsert_upload_name_map_locked(
                    stored_filename=stored_name,
                    original_filename=stored_name,
                    title=guessed_title,
                    artist="未知",
                    duration_sec=_DEFAULT_TRACK_DURATION_SECONDS,
                )
                mapped = self._upload_name_map.get(stored_name, {})

            item = self._build_music_item(
                url=self._to_absolute_ui_url(self._build_ui_file_url(stored_name)),
                title=_safe_text(mapped.get("title"), Path(stored_name).stem),
                artist=_safe_text(mapped.get("artist"), "未知"),
                source="upload",
                duration_sec=_normalize_duration(mapped.get("duration_sec")),
                stored_filename=stored_name,
                original_filename=_safe_text(mapped.get("original_filename"), stored_name),
            )
            created_at = str(mapped.get("created_at") or "").strip()
            updated_at = str(mapped.get("updated_at") or "").strip()
            if created_at:
                item["created_at"] = created_at
            if updated_at:
                item["updated_at"] = updated_at
            self._music_items.append(item)

        valid_item_ids = {
            str(it.get("item_id") or "").strip()
            for it in self._music_items
            if isinstance(it, dict)
        }
        self._lyrics_map = {
            item_id: meta
            for item_id, meta in self._lyrics_map.items()
            if item_id in valid_item_ids
        }

    def _make_queue_item_from_music_item(
        self,
        music_item: dict[str, Any],
        *,
        duration_sec: object | None = None,
    ) -> dict[str, Any]:
        lyric_text = str(music_item.get("lyric_text") or "").strip()
        if not lyric_text:
            lyric_text = self._extract_lyric_for_item_locked(str(music_item.get("item_id") or ""))
        return {
            "queue_id": f"q_{uuid4().hex[:10]}",
            "item_id": music_item.get("item_id"),
            "url": self._normalize_legacy_url(str(music_item.get("url") or "").strip()),
            "title": _safe_text(music_item.get("title"), "未命名音乐"),
            "artist": _safe_text(music_item.get("artist"), "未知"),
            "duration_sec": _normalize_duration(
                duration_sec if duration_sec is not None else music_item.get("duration_sec")
            ),
            "lyric_text": lyric_text,
        }

    def _make_queue_item_from_raw(self, raw: dict[str, Any]) -> dict[str, Any]:
        url = self._normalize_legacy_url(str(raw.get("url") or "").strip())
        if not url:
            raise ValueError("队列中的 url 不能为空")
        return {
            "queue_id": f"q_{uuid4().hex[:10]}",
            "item_id": str(raw.get("item_id") or "").strip() or None,
            "url": url,
            "title": _safe_text(raw.get("title"), "未命名音乐"),
            "artist": _safe_text(raw.get("artist"), "未知"),
            "duration_sec": _normalize_duration(raw.get("duration_sec")),
            "lyric_text": str(raw.get("lyric_text") or "").replace("\r\n", "\n").strip()[:_LYRIC_PUSH_MAX_CHARS],
        }

    async def _load_state(self) -> None:
        path = self._state_file
        if not path.exists():
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.logger.warning(f"读取调度状态失败，将重置为空状态: {exc}")
            return

        raw_items = data.get("music_items")
        if isinstance(raw_items, list):
            items: list[dict[str, Any]] = []
            for it in raw_items:
                if not isinstance(it, dict):
                    continue
                if not str(it.get("item_id") or "").strip():
                    continue
                if not str(it.get("url") or "").strip():
                    continue
                normalized = dict(it)
                normalized["url"] = self._normalize_legacy_url(str(it.get("url") or ""))
                normalized["title"] = _safe_text(it.get("title"), "未命名音乐")
                normalized["artist"] = _safe_text(it.get("artist"), "未知")
                normalized["duration_sec"] = _normalize_duration(it.get("duration_sec"))
                normalized["original_filename"] = str(it.get("original_filename") or "").strip()
                normalized["lyric_text"] = str(it.get("lyric_text") or "").replace("\r\n", "\n").strip()[:_LYRIC_PUSH_MAX_CHARS]
                normalized.setdefault("created_at", _iso())
                normalized.setdefault("updated_at", _iso())
                items.append(normalized)
            self._music_items = items

        if isinstance(data, dict):
            settings = data.get("settings")
            if isinstance(settings, dict):
                self._attach_prompt_on_push = _coerce_bool(
                    settings.get("attach_prompt_on_push"),
                    _DEFAULT_ATTACH_PROMPT_ON_PUSH,
                )

        raw_tasks = data.get("tasks")
        if isinstance(raw_tasks, list):
            tasks: dict[str, dict[str, Any]] = {}
            for task in raw_tasks:
                if not isinstance(task, dict):
                    continue
                task_id = str(task.get("task_id") or "").strip()
                if not task_id:
                    continue
                queue = task.get("queue")
                if not isinstance(queue, list):
                    continue

                normalized_queue: list[dict[str, Any]] = []
                for raw_q in queue:
                    if not isinstance(raw_q, dict):
                        continue
                    try:
                        normalized_queue.append(self._make_queue_item_from_raw(raw_q))
                    except Exception:
                        continue
                if not normalized_queue:
                    continue

                normalized = dict(task)
                normalized["task_id"] = task_id
                normalized["queue"] = normalized_queue
                raw_status = str(task.get("status") or "pending")
                normalized["status"] = "stopped" if raw_status == "running" else raw_status
                normalized["name"] = _safe_text(task.get("name"), f"定时任务 {task_id[-4:]}")
                normalized.setdefault("created_at", _iso())
                normalized.setdefault("updated_at", _iso())
                normalized.setdefault("trigger_at", _iso())
                normalized.setdefault("current_index", 0)
                normalized.setdefault("last_error", "")
                if normalized["status"] == "stopped" and not str(normalized.get("last_error") or "").strip():
                    normalized["last_error"] = "插件重载后任务自动停止，请手动继续"
                normalized["duration_mode"] = _normalize_duration_mode(task.get("duration_mode"))
                normalized["total_duration_sec"] = _calc_total_duration(normalized_queue)
                normalized["target_lanlan"] = str(task.get("target_lanlan") or "").strip() or None
                tasks[task_id] = normalized

            self._tasks = tasks

    def _save_state_locked(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "music_items": self._music_items,
            "tasks": list(self._tasks.values()),
            "settings": {
                "attach_prompt_on_push": bool(self._attach_prompt_on_push),
            },
            "updated_at": _iso(),
        }
        self._state_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _serialize_task(self, task: dict[str, Any]) -> dict[str, Any]:
        raw_queue = task.get("queue")
        queue: list[dict[str, Any]] = raw_queue if isinstance(raw_queue, list) else []
        return {
            "task_id": task.get("task_id"),
            "name": task.get("name") or "定时任务",
            "status": task.get("status") or "pending",
            "can_stop": str(task.get("status") or "") == "running",
            "can_run": str(task.get("status") or "") != "running",
            "trigger_at": task.get("trigger_at"),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "started_at": task.get("started_at"),
            "finished_at": task.get("finished_at"),
            "current_index": int(task.get("current_index") or 0),
            "total": len(queue),
            "last_error": task.get("last_error") or "",
            "duration_mode": _normalize_duration_mode(task.get("duration_mode")),
            "total_duration_sec": _normalize_duration(task.get("total_duration_sec"), 0),
            "target_lanlan": task.get("target_lanlan"),
            "queue": queue,
        }

    def _build_runtime_snapshot(self) -> dict[str, Any]:
        active = None
        if self._active_task_id and self._active_track and self._active_track_started_at:
            elapsed = max(0.0, time.time() - self._active_track_started_at)
            duration = max(1, int(self._active_track_duration or _DEFAULT_TRACK_DURATION_SECONDS))
            ratio = min(1.0, elapsed / duration)
            active = {
                "task_id": self._active_task_id,
                "track": self._active_track,
                "elapsed_sec": round(elapsed, 1),
                "duration_sec": duration,
                "progress_ratio": round(ratio, 4),
                "progress_percent": round(ratio * 100, 1),
                "controls": {
                    "can_stop": True,
                    "can_next": True,
                    "can_prev": True,
                },
            }

        next_task = None
        pending_tasks = [
            t for t in self._tasks.values()
            if str(t.get("status") or "pending") == "pending"
        ]
        if pending_tasks:
            pending_tasks.sort(key=lambda t: str(t.get("trigger_at") or ""))
            nxt = pending_tasks[0]
            next_task = {
                "task_id": nxt.get("task_id"),
                "name": nxt.get("name"),
                "trigger_at": nxt.get("trigger_at"),
            }

        return {
            "active": active,
            "next_task": next_task,
            "playback": dict(self._playback_state),
            "now": _iso(),
        }

    def _build_queue_for_task(
        self,
        *,
        queue_item_ids: list[str] | None,
        queue_items: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        final_queue: list[dict[str, Any]] = []
        music_index = {
            str(item.get("item_id") or ""): item
            for item in self._music_items
            if isinstance(item, dict)
        }

        if isinstance(queue_item_ids, list):
            for raw_id in queue_item_ids:
                item_id = str(raw_id or "").strip()
                if not item_id:
                    continue
                src = music_index.get(item_id)
                if src:
                    final_queue.append(self._make_queue_item_from_music_item(src))

        if isinstance(queue_items, list):
            for raw in queue_items:
                if not isinstance(raw, dict):
                    continue
                ref_id = str(raw.get("item_id") or "").strip()
                if ref_id and ref_id in music_index:
                    final_queue.append(
                        self._make_queue_item_from_music_item(
                            music_index[ref_id],
                            duration_sec=raw.get("duration_sec"),
                        )
                    )
                    continue
                final_queue.append(self._make_queue_item_from_raw(raw))

        return final_queue

    def _resolve_target_lanlan(self, kwargs: dict[str, Any]) -> str | None:
        explicit = kwargs.get("target_lanlan")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()

        ctx_obj = kwargs.get("_ctx")
        if isinstance(ctx_obj, dict):
            lanlan_name = ctx_obj.get("lanlan_name")
            if isinstance(lanlan_name, str) and lanlan_name.strip():
                return lanlan_name.strip()

        current_lanlan = getattr(self.ctx, "_current_lanlan", None)
        if isinstance(current_lanlan, str) and current_lanlan.strip():
            return current_lanlan.strip()

        env_candidates = (
            os.getenv("NEKO_TARGET_LANLAN", ""),
            os.getenv("NEKO_LANLAN_NAME", ""),
            os.getenv("NEKO_HER_NAME", ""),
        )
        for raw in env_candidates:
            val = str(raw or "").strip()
            if val:
                return val

        try:
            from utils.config_manager import get_config_manager

            cfg = get_config_manager()
            info = cfg.get_character_data()
            if isinstance(info, tuple) and len(info) >= 2:
                her_name = str(info[1] or "").strip()
                if her_name:
                    return her_name
        except Exception:
            pass

        return None

    def _install_push_mapper(self) -> None:
        """插件内动态映射：统一给 music_pusher 推送打标签，便于跨模块识别。"""
        if self._push_mapper_installed:
            return
        original = getattr(self.ctx, "push_message", None)
        if not callable(original):
            return

        self._original_push_message = original

        def _mapped_push_message(*args, **kwargs):
            source = str(kwargs.get("source") or "").strip()
            if source == "music_pusher":
                metadata = kwargs.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata.setdefault("plugin_marker", "music_pusher")
                metadata.setdefault("dynamic_mapped", True)
                kwargs["metadata"] = metadata
            return self._original_push_message(*args, **kwargs)

        setattr(self.ctx, "push_message", _mapped_push_message)
        self._push_mapper_installed = True

    def _uninstall_push_mapper(self) -> None:
        if not self._push_mapper_installed:
            return
        if callable(self._original_push_message):
            setattr(self.ctx, "push_message", self._original_push_message)
        self._original_push_message = None
        self._push_mapper_installed = False

    def _push_proactive_text(
        self,
        *,
        content: str,
        description: str,
        target_lanlan: str | None,
        metadata: dict[str, Any] | None = None,
        priority: int = 8,
    ) -> None:
        merged_meta = dict(metadata or {})
        merged_meta.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        merged_meta.setdefault("push_marker", "music_pusher_proactive")
        merged_meta.setdefault("plugin_marker", "music_pusher")
        self.ctx.push_message(
            source="music_pusher",
            message_type="proactive_notification",
            description=description,
            priority=priority,
            content=content,
            metadata=merged_meta,
            target_lanlan=target_lanlan,
        )

    def _push_music_link(
        self,
        *,
        url: str,
        title: str,
        artist: str,
        target_lanlan: str | None,
        lyric_text: str = "",
        attach_prompt_on_push: bool = True,
    ) -> None:
        event_id = f"music_push_{uuid4().hex[:12]}"
        expire_at = int(time.time()) + int(_PROACTIVE_PROMPT_EXPIRE_SECONDS)
        parsed = urlparse(url)
        host = str(parsed.hostname or "").strip().lower()
        if host:
            domains = [host]
            if host in {"127.0.0.1", "localhost"}:
                domains = ["127.0.0.1", "localhost"]
            self.ctx.push_message(
                source="music_pusher",
                message_type="music_allowlist_add",
                description=f"Allow music host: {host}",
                priority=7,
                metadata={"domains": domains, "event_id": event_id},
                target_lanlan=target_lanlan,
            )

        self.ctx.push_message(
            source="music_pusher",
            message_type="music_play_url",
            description=f"🎵 用户分享链接 [{title or 'External Link'}]",
            priority=9,
            metadata={
                "url": url,
                "name": title or "用户分享的音乐",
                "artist": artist or "用户",
                "event_id": event_id,
            },
            target_lanlan=target_lanlan,
        )

        if not bool(attach_prompt_on_push):
            return

        lyric_clean = str(lyric_text or "").replace("\r\n", "\n").strip()[:_LYRIC_PUSH_MAX_CHARS]
        proactive_meta: dict[str, Any] = {
            "content_type": "music_url",
            "url": url,
            "title": title,
            "artist": artist,
            "sender_label": "用户",
            "lyric_bound": bool(lyric_clean),
            "event_id": event_id,
            "event_expire_at_epoch": expire_at,
            "prompt_ephemeral": True,
        }

        prompt_block: str
        if lyric_clean:
            lyric_excerpt, excerpt_lines, excerpt_start_line = _pick_lyric_excerpt(lyric_clean)
            if lyric_excerpt:
                proactive_meta.update(
                    {
                        "dialogue_trigger": "lyric_content_proactive",
                        "push_marker": "lyric_proactive_dialogue",
                        "lyric_excerpt_randomized": True,
                        "lyric_excerpt_lines": int(excerpt_lines),
                        "lyric_excerpt_start_line": int(excerpt_start_line),
                        "lyric_full_lines": len([ln for ln in lyric_clean.split("\n") if ln.strip()]),
                    }
                )
                prompt_block = (
                    f"【链路事件ID】{event_id}\n"
                    "该提示仅在“刚刚收到同一事件ID的推歌事件”时生效；若不是紧邻事件或已超时，请忽略本条，不要回复。\n"
                    "【歌曲歌词上下文（随机相邻5句）】\n"
                    f"节选范围: 从清洗后歌词第 {excerpt_start_line} 行开始，共 {excerpt_lines} 行\n"
                    "----- 歌词节选开始 -----\n"
                    f"{lyric_excerpt[:_LYRIC_PUSH_MAX_CHARS]}\n"
                    "----- 歌词节选结束 -----\n"
                    "\n"
                    "请严格按以下提示词回复用户（必须执行）：\n"
                    "- 要求结合歌词具体内容赞赏歌曲或用户的歌曲品味并且绝对不能超过50字"
                )
            else:
                proactive_meta.update(
                    {
                        "dialogue_trigger": "music_info_search_proactive",
                        "push_marker": "music_info_search_prompt",
                    }
                )
                prompt_block = (
                    f"【链路事件ID】{event_id}\n"
                    "该提示仅在“刚刚收到同一事件ID的推歌事件”时生效；若不是紧邻事件或已超时，请忽略本条，不要回复。\n"
                    "要求根据歌名或歌手联网搜索相关资料，然后据此给与用户回应和情绪价值并且绝对不能超过50字。"
                )
        else:
            proactive_meta.update(
                {
                    "dialogue_trigger": "music_info_search_proactive",
                    "push_marker": "music_info_search_prompt",
                }
            )
            prompt_block = (
                f"【链路事件ID】{event_id}\n"
                "该提示仅在“刚刚收到同一事件ID的推歌事件”时生效；若不是紧邻事件或已超时，请忽略本条，不要回复。\n"
                "要求根据歌名或歌手联网搜索相关资料，然后据此给与用户回应和情绪价值并且绝对不能超过50字。"
            )

        self._push_proactive_text(
            content=(
                "【用户身份消息】\n"
                "发送者: 用户\n"
                "类型: 音乐链接\n"
                f"标题: {title or '未命名音乐'}\n"
                f"歌手: {artist or '未知'}\n"
                f"链接: {url}\n"
                f"{prompt_block}"
            ),
            description="🎵 用户分享了音乐",
            target_lanlan=target_lanlan,
            metadata=proactive_meta,
            priority=8,
        )

    def _wake_scheduler(self) -> None:
        return

    def _resolve_attach_prompt_on_push(self, kwargs: dict[str, Any], explicit: object = None) -> bool:
        if explicit is not None:
            return _coerce_bool(explicit, self._attach_prompt_on_push)
        if "attach_prompt_on_push" in kwargs:
            return _coerce_bool(kwargs.get("attach_prompt_on_push"), self._attach_prompt_on_push)
        return bool(self._attach_prompt_on_push)

    def _ensure_scheduler_task(self) -> None:
        task = self._scheduler_task
        if task is None or task.done():
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    def _cancel_task_timer(self, task_id: str) -> None:
        timer = self._task_timers.pop(task_id, None)
        current = asyncio.current_task()
        if timer is current:
            return
        if timer is not None and not timer.done():
            timer.cancel()

    def _schedule_task_timer(self, task_id: str) -> None:
        self._cancel_task_timer(task_id)

        async def _runner() -> None:
            try:
                while not self._scheduler_stop.is_set():
                    async with self._state_lock:
                        task = self._tasks.get(task_id)
                        if task is None:
                            return
                        if str(task.get("status") or "") != "pending":
                            return
                        try:
                            trigger_dt = _parse_datetime_to_utc(str(task.get("trigger_at") or ""))
                        except Exception:
                            trigger_dt = _utc_now()

                    delay = (trigger_dt - _utc_now()).total_seconds()
                    if delay > 0:
                        await asyncio.sleep(min(delay, 1.0))
                        continue

                    self._spawn_execute_task(task_id)
                    return
            except asyncio.CancelledError:
                return

        self._task_timers[task_id] = asyncio.create_task(_runner())

    def _spawn_execute_task(self, task_id: str) -> None:
        live = self._active_execution_task
        if live is not None and not live.done():
            return

        async def _runner() -> None:
            try:
                await self._execute_task(task_id)
            finally:
                current = asyncio.current_task()
                if self._active_execution_task is current:
                    self._active_execution_task = None

        self._active_execution_task = asyncio.create_task(_runner())

    async def _request_active_control(self, command: str, task_id: str | None = None) -> bool:
        cmd = str(command or "").strip().lower()
        if cmd not in {_CTRL_STOP, _CTRL_NEXT, _CTRL_PREV}:
            return False

        async with self._state_lock:
            if self._active_task_id is None:
                return False
            if task_id and task_id != self._active_task_id:
                return False
            self._active_control = cmd
            return True

    @lifecycle(id="startup")
    async def on_start(self, **_):
        # 关键修复：插件 reload 后会切换事件循环，这里重建异步原语，避免绑定旧 loop。
        self._state_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        self._scheduler_stop = asyncio.Event()
        self._task_timers = {}
        self._active_control = None
        self._active_execution_task = None
        self._active_task_id = None
        self._active_track = None
        self._active_track_started_at = None
        self._active_track_duration = 0
        self._playback_state = {
            "status": "idle",
            "position_sec": 0.0,
            "duration_sec": 0.0,
            "updated_at": _iso(),
            "track_url": "",
            "track_title": "",
        }

        self._upload_dir.mkdir(parents=True, exist_ok=True)
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        async with self._state_lock:
            await self._load_state()
            self._load_upload_name_map_locked()
            self._load_lyrics_map_locked()
            self._rebuild_music_items_from_uploads_locked()
            self._save_upload_name_map_locked()
            self._save_lyrics_map_locked()
            self._save_state_locked()
        self._install_push_mapper()
        self.register_static_ui("static")
        self._scheduler_stop.clear()
        self._ensure_scheduler_task()
        async with self._state_lock:
            pending_ids = [
                str(t.get("task_id") or "")
                for t in self._tasks.values()
                if str(t.get("status") or "") == "pending"
            ]
        for task_id in pending_ids:
            if task_id:
                self._schedule_task_timer(task_id)
        return Ok("音乐推送插件已启动（含定时队列）")

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self._scheduler_stop.set()
        for task_id in list(self._task_timers.keys()):
            self._cancel_task_timer(task_id)

        running_task = self._active_execution_task
        self._active_execution_task = None
        if running_task is not None and not running_task.done():
            running_task.cancel()
            try:
                await running_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        task = self._scheduler_task
        self._scheduler_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.logger.warning(f"关闭调度器时出现异常: {exc}")

        async with self._state_lock:
            for live_task in self._tasks.values():
                if str(live_task.get("status") or "") == "running":
                    live_task["status"] = "stopped"
                    live_task["last_error"] = "插件关闭后任务已停止，请手动继续"
                    live_task["finished_at"] = _iso()
                    live_task["updated_at"] = _iso()
            self._save_state_locked()

        self._uninstall_push_mapper()

    @plugin_entry(
        id="upload_music_file",
        name="上传音乐并自动推送",
        description="上传本地音乐文件(Base64)，生成可播放链接并自动推送。",
        input_schema={
            "type": "object",
            "properties": {
                "audio_base64": {"type": "string"},
                "filename": {"type": "string", "default": "track.mp3"},
                "lyric_base64": {"type": "string", "default": ""},
                "lyric_filename": {"type": "string", "default": ""},
                "title": {"type": "string", "default": ""},
                "artist": {"type": "string", "default": ""},
                "auto_push": {"type": "boolean", "default": True},
                "duration_sec": {"type": "number"},
                "attach_prompt_on_push": {"type": "boolean"},
            },
            "required": ["audio_base64", "filename"],
        },
        llm_result_fields=["saved", "pushed", "music_url_absolute", "duration_sec"],
    )
    async def upload_music_file(
        self,
        audio_base64: str,
        filename: str,
        lyric_base64: str = "",
        lyric_filename: str = "",
        title: str = "",
        artist: str = "",
        auto_push: bool = True,
        duration_sec: int | float | str | None = None,
        attach_prompt_on_push: bool | None = None,
        **kwargs,
    ):
        binary, ext, err = _decode_audio(audio_base64, filename)
        if binary is None:
            return Err(SdkError(err))

        lyric_text, lyric_name, lyric_err = _decode_optional_lyric(lyric_base64, lyric_filename)
        if lyric_err:
            return Err(SdkError(lyric_err))

        stored_filename, music_url = self._save_upload_file(binary, ext)
        absolute_url = self._to_absolute_ui_url(music_url)

        detected_duration = self._detect_audio_duration_seconds(binary)
        final_duration = _normalize_duration(duration_sec) if duration_sec is not None else (
            detected_duration if detected_duration is not None else _DEFAULT_TRACK_DURATION_SECONDS
        )

        final_title = title.strip() or Path(filename).stem or "未命名音乐"
        final_artist = artist.strip() or "未知"

        created_item = self._build_music_item(
            url=absolute_url,
            title=final_title,
            artist=final_artist,
            source="upload",
            duration_sec=final_duration,
            stored_filename=stored_filename,
            original_filename=str(Path(filename).name),
        )
        created_item["lyric_text"] = lyric_text
        async with self._state_lock:
            self._music_items.append(created_item)
            self._upsert_upload_name_map_locked(
                stored_filename=stored_filename,
                original_filename=str(Path(filename).name),
                title=final_title,
                artist=final_artist,
                duration_sec=final_duration,
            )
            if lyric_text:
                self._bind_lyrics_to_item_locked(
                    item_id=str(created_item.get("item_id") or ""),
                    lyric_text=lyric_text,
                    lyric_filename=lyric_name,
                )
            self._save_upload_name_map_locked()
            self._save_lyrics_map_locked()
            self._save_state_locked()

        target_lanlan = self._resolve_target_lanlan(kwargs)
        attach_prompt = self._resolve_attach_prompt_on_push(kwargs, attach_prompt_on_push)
        pushed = False
        if auto_push:
            self._push_music_link(
                url=absolute_url,
                title=final_title,
                artist=final_artist,
                target_lanlan=target_lanlan,
                lyric_text=lyric_text,
                attach_prompt_on_push=attach_prompt,
            )
            pushed = True

        return Ok(
            {
                "saved": True,
                "pushed": pushed,
                "title": final_title,
                "artist": final_artist,
                "filename": filename,
                "stored_filename": stored_filename,
                "music_url": music_url,
                "music_url_absolute": absolute_url,
                "size_bytes": len(binary),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "target_lanlan": target_lanlan,
                "item_id": created_item["item_id"],
                "duration_sec": final_duration,
                "detected_duration_sec": detected_duration,
                "lyric_bound": bool(lyric_text),
                "lyric_filename": lyric_name,
                "attach_prompt_on_push": attach_prompt,
                "message": "获取成功",
            }
        )

    @plugin_entry(
        id="push_music_url",
        name="推送音乐链接",
        description="推送可播放音乐链接到主对话模型。",
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "item_id": {"type": "string", "default": ""},
                "title": {"type": "string", "default": ""},
                "artist": {"type": "string", "default": ""},
                "duration_sec": {"type": "number"},
                "lyric_text": {"type": "string", "default": ""},
                "attach_prompt_on_push": {"type": "boolean"},
            },
            "required": ["url"],
        },
        llm_result_fields=["pushed", "chain", "url"],
    )
    async def push_music_url(
        self,
        url: str,
        item_id: str = "",
        title: str = "",
        artist: str = "",
        auto_push: bool = True,
        add_to_library: bool = True,
        duration_sec: int | float | str | None = None,
        lyric_text: str = "",
        attach_prompt_on_push: bool | None = None,
        **kwargs,
    ):
        link = self._normalize_legacy_url(str(url or "").strip())
        if not link:
            return Err(SdkError("url 不能为空"))

        target_lanlan = self._resolve_target_lanlan(kwargs)
        attach_prompt = self._resolve_attach_prompt_on_push(kwargs, attach_prompt_on_push)
        final_title = title.strip() or "主人分享的音乐"
        final_artist = artist.strip() or "主人"
        detected_duration: int | None = None
        if duration_sec is None:
            try:
                detected_duration = await asyncio.wait_for(
                    asyncio.to_thread(self._detect_audio_duration_from_url, link),
                    timeout=_PUSH_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                detected_duration = None
            except Exception:
                detected_duration = None

        final_duration = _normalize_duration(duration_sec) if duration_sec is not None else (
            detected_duration if detected_duration is not None else _DEFAULT_TRACK_DURATION_SECONDS
        )
        lyric_clean = str(lyric_text or "").replace("\r\n", "\n").strip()[:_LYRIC_PUSH_MAX_CHARS]
        if not lyric_clean:
            async with self._state_lock:
                source_item = self._find_music_item_locked(
                    item_id=item_id,
                    url=link,
                    title=final_title,
                    artist=final_artist,
                )
                if source_item:
                    lyric_clean = self._extract_lyric_for_item_locked(str(source_item.get("item_id") or ""))

        item_id = ""
        if add_to_library:
            item = self._build_music_item(
                url=link,
                title=final_title,
                artist=final_artist,
                source="link",
                duration_sec=final_duration,
            )
            if lyric_clean:
                item["lyric_text"] = lyric_clean
            async with self._state_lock:
                self._music_items.append(item)
                if lyric_clean:
                    self._bind_lyrics_to_item_locked(
                        item_id=str(item.get("item_id") or ""),
                        lyric_text=lyric_clean,
                        lyric_filename="manual_lyric.txt",
                    )
                    self._save_lyrics_map_locked()
                self._save_state_locked()
            item_id = str(item.get("item_id") or "")

        if auto_push:
            self._push_music_link(
                url=link,
                title=final_title,
                artist=final_artist,
                target_lanlan=target_lanlan,
                lyric_text=lyric_clean,
                attach_prompt_on_push=attach_prompt,
            )

        return Ok(
            {
                "pushed": bool(auto_push),
                "saved": bool(add_to_library),
                "chain": "music_play_url + proactive_notification",
                "url": link,
                "title": final_title,
                "artist": final_artist,
                "target_lanlan": target_lanlan,
                "item_id": item_id,
                "duration_sec": final_duration,
                "detected_duration_sec": detected_duration,
                "duration_mode": "manual_override" if duration_sec is not None else "auto_detect",
                "lyric_bound": bool(lyric_clean),
                "attach_prompt_on_push": attach_prompt,
                "message": "获取成功",
            }
        )

    @plugin_entry(
        id="run",
        name="通用推歌入口",
        description="通用入口：优先按 url 推歌；未提供 url 时从素材池选择音乐推送。",
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "default": ""},
                "item_id": {"type": "string", "default": ""},
                "title": {"type": "string", "default": ""},
                "artist": {"type": "string", "default": ""},
                "duration_sec": {"type": "number"},
                "lyric_text": {"type": "string", "default": ""},
                "attach_prompt_on_push": {"type": "boolean"},
            },
        },
        llm_result_fields=["pushed", "url", "title", "artist"],
    )
    async def run(
        self,
        url: str = "",
        item_id: str = "",
        title: str = "",
        artist: str = "",
        duration_sec: int | float | str | None = None,
        lyric_text: str = "",
        attach_prompt_on_push: bool | None = None,
        **kwargs,
    ):
        direct_url = self._normalize_legacy_url(str(url or "").strip())
        if direct_url:
            return await self.push_music_url(
                url=direct_url,
                item_id=item_id,
                title=title,
                artist=artist,
                auto_push=True,
                add_to_library=True,
                duration_sec=duration_sec,
                lyric_text=lyric_text,
                attach_prompt_on_push=attach_prompt_on_push,
                **kwargs,
            )

        chosen: dict[str, Any] | None = None
        async with self._state_lock:
            if str(item_id or "").strip():
                chosen = self._find_music_item_locked(item_id=item_id)
            elif str(title or "").strip():
                chosen, reason = self._match_music_item_by_name_locked(title=title, artist=artist)
                if chosen is None:
                    if reason == "ambiguous_exact":
                        return Err(SdkError("按歌曲名精确匹配到多首歌曲，请补充歌手或使用 item_id"))
                    if reason == "ambiguous_partial":
                        return Err(SdkError("按歌曲名模糊匹配到多首歌曲，请补充更完整歌名或歌手"))
                    return Err(SdkError("未找到与给定歌曲名一致的素材，请先上传或校正歌名"))
            else:
                chosen = random.choice(self._music_items) if self._music_items else None

        if not chosen:
            return Err(SdkError("未提供可推送 url，且素材池为空"))

        chosen_lyric = self._extract_lyric_for_item_locked(str(chosen.get("item_id") or ""))
        return await self.push_music_url(
            url=str(chosen.get("url") or ""),
            item_id=str(chosen.get("item_id") or ""),
            title=str(chosen.get("title") or ""),
            artist=str(chosen.get("artist") or ""),
            auto_push=True,
            add_to_library=False,
            duration_sec=chosen.get("duration_sec"),
            lyric_text=chosen_lyric,
            attach_prompt_on_push=attach_prompt_on_push,
            **kwargs,
        )

    @plugin_entry(
        id="get_push_settings",
        name="获取推送设置",
        description="获取当前推歌时是否附带提示词的开关状态。",
        llm_result_fields=["attach_prompt_on_push"],
    )
    async def get_push_settings(self, **_):
        async with self._state_lock:
            enabled = bool(self._attach_prompt_on_push)
        return Ok({"attach_prompt_on_push": enabled, "message": "获取成功"})

    @plugin_entry(
        id="set_push_settings",
        name="设置推送开关",
        description="设置推歌时是否附带提示词与歌词提示内容。",
        input_schema={
            "type": "object",
            "properties": {
                "attach_prompt_on_push": {"type": "boolean"},
            },
            "required": ["attach_prompt_on_push"],
        },
        llm_result_fields=["attach_prompt_on_push"],
    )
    async def set_push_settings(self, attach_prompt_on_push: bool, **_):
        async with self._state_lock:
            self._attach_prompt_on_push = bool(attach_prompt_on_push)
            self._save_state_locked()
        return Ok({"attach_prompt_on_push": bool(self._attach_prompt_on_push), "message": "获取成功"})

    @plugin_entry(
        id="list_music_items",
        name="列出音乐素材",
        description="获取插件音乐素材池。",
        llm_result_fields=["total"],
    )
    async def list_music_items(self, **_):
        async with self._state_lock:
            items = []
            for it in self._music_items:
                item = dict(it)
                lyric_meta = self._lyrics_map.get(str(item.get("item_id") or "").strip()) or {}
                item["lyric_bound"] = bool(lyric_meta)
                item["lyric_filename"] = str(lyric_meta.get("lyric_filename") or "")
                item.pop("lyric_text", None)
                items.append(item)
        items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        return Ok({"items": items, "total": len(items)})

    @plugin_entry(
        id="delete_music_item",
        name="删除音乐素材",
        description="从素材池删除一个音乐项，不影响已创建任务中的快照。",
        input_schema={
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
            },
            "required": ["item_id"],
        },
        llm_result_fields=["deleted"],
    )
    async def delete_music_item(self, item_id: str, **_):
        target = str(item_id or "").strip()
        if not target:
            return Err(SdkError("item_id 不能为空"))

        async with self._state_lock:
            before = len(self._music_items)
            self._music_items = [it for it in self._music_items if str(it.get("item_id") or "") != target]
            deleted = len(self._music_items) != before
            if deleted:
                self._lyrics_map.pop(target, None)
                self._save_lyrics_map_locked()
                self._save_state_locked()

        return Ok({"deleted": deleted, "item_id": target})

    @plugin_entry(
        id="create_schedule_task",
        name="创建定时任务",
        description="创建一个定时音频队列任务。",
        input_schema={
            "type": "object",
            "properties": {
                "trigger_at": {"type": "string", "description": "ISO 时间字符串"},
                "name": {"type": "string", "default": ""},
                "duration_mode": {"type": "string", "default": "auto_sum"},
                "target_lanlan": {"type": "string", "default": ""},
                "queue_item_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
                "queue_items": {
                    "type": "array",
                    "items": {"type": "object"},
                    "default": [],
                },
            },
            "required": ["trigger_at"],
        },
        llm_result_fields=["task_id", "status"],
    )
    async def create_schedule_task(
        self,
        trigger_at: str,
        name: str = "",
        duration_mode: str = "auto_sum",
        target_lanlan: str = "",
        queue_item_ids: list[str] | None = None,
        queue_items: list[dict[str, Any]] | None = None,
        **kwargs,
    ):
        try:
            trigger_dt = _parse_datetime_to_utc(trigger_at)
        except Exception as exc:
            return Err(SdkError(f"时间格式无效: {exc}"))

        mode = _normalize_duration_mode(duration_mode)
        final_target = target_lanlan.strip() or self._resolve_target_lanlan(kwargs)

        async with self._state_lock:
            queue = self._build_queue_for_task(
                queue_item_ids=queue_item_ids,
                queue_items=queue_items,
            )
            if not queue:
                return Err(SdkError("队列不能为空，请先添加音频"))

            task_id = f"tsk_{uuid4().hex[:10]}"
            task = {
                "task_id": task_id,
                "name": _safe_text(name, f"浪漫轮播 {task_id[-4:]}"),
                "status": "pending",
                "trigger_at": _iso(trigger_dt),
                "queue": queue,
                "duration_mode": mode,
                "total_duration_sec": _calc_total_duration(queue),
                "target_lanlan": final_target,
                "current_index": 0,
                "created_at": _iso(),
                "updated_at": _iso(),
                "started_at": None,
                "finished_at": None,
                "last_error": "",
            }
            self._tasks[task_id] = task
            self._save_state_locked()

        self._wake_scheduler()
        self._ensure_scheduler_task()
        self._schedule_task_timer(task_id)
        return Ok({"task_id": task_id, "status": "pending", "message": "获取成功"})

    @plugin_entry(
        id="update_schedule_task",
        name="更新定时任务",
        description="修改时间与队列，支持重排。",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "trigger_at": {"type": "string"},
                "name": {"type": "string", "default": ""},
                "duration_mode": {"type": "string"},
                "target_lanlan": {"type": "string", "default": ""},
                "queue_item_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
                "queue_items": {
                    "type": "array",
                    "items": {"type": "object"},
                    "default": [],
                },
            },
            "required": ["task_id"],
        },
        llm_result_fields=["updated", "task_id"],
    )
    async def update_schedule_task(
        self,
        task_id: str,
        trigger_at: str | None = None,
        name: str | None = None,
        duration_mode: str | None = None,
        target_lanlan: str | None = None,
        queue_item_ids: list[str] | None = None,
        queue_items: list[dict[str, Any]] | None = None,
        **kwargs,
    ):
        target_id = str(task_id or "").strip()
        if not target_id:
            return Err(SdkError("task_id 不能为空"))

        parsed_trigger: datetime | None = None
        if trigger_at is not None:
            try:
                parsed_trigger = _parse_datetime_to_utc(trigger_at)
            except Exception as exc:
                return Err(SdkError(f"时间格式无效: {exc}"))

        async with self._state_lock:
            task = self._tasks.get(target_id)
            if task is None:
                return Err(SdkError("任务不存在"))
            if str(task.get("status") or "") == "running":
                return Err(SdkError("任务执行中，暂不允许编辑"))

            if parsed_trigger is not None:
                task["trigger_at"] = _iso(parsed_trigger)

            if name is not None:
                task["name"] = _safe_text(name, task.get("name") or f"浪漫轮播 {target_id[-4:]}")

            if duration_mode is not None:
                task["duration_mode"] = _normalize_duration_mode(duration_mode)

            if target_lanlan is not None:
                task["target_lanlan"] = target_lanlan.strip() or self._resolve_target_lanlan(kwargs)

            if queue_item_ids is not None or queue_items is not None:
                queue = self._build_queue_for_task(
                    queue_item_ids=queue_item_ids,
                    queue_items=queue_items,
                )
                if not queue:
                    return Err(SdkError("队列不能为空"))
                task["queue"] = queue
                task["total_duration_sec"] = _calc_total_duration(queue)

            task["status"] = "pending"
            task["finished_at"] = None
            task["last_error"] = ""
            task["updated_at"] = _iso()
            task["current_index"] = 0
            self._save_state_locked()

        self._wake_scheduler()
        self._ensure_scheduler_task()
        self._schedule_task_timer(target_id)
        return Ok({"updated": True, "task_id": target_id, "message": "获取成功"})

    @plugin_entry(
        id="delete_schedule_task",
        name="删除定时任务",
        description="删除待执行或已完成任务。",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
        llm_result_fields=["deleted", "task_id"],
    )
    async def delete_schedule_task(self, task_id: str, **_):
        target_id = str(task_id or "").strip()
        if not target_id:
            return Err(SdkError("task_id 不能为空"))

        async with self._state_lock:
            if self._active_task_id == target_id:
                return Err(SdkError("任务执行中，暂不允许删除"))
            deleted = self._tasks.pop(target_id, None) is not None
            if deleted:
                self._save_state_locked()
        if deleted:
            self._cancel_task_timer(target_id)
        return Ok({"deleted": deleted, "task_id": target_id})

    @plugin_entry(
        id="run_schedule_task_now",
        name="立即执行任务",
        description="将任务触发时间改为当前并立即尝试执行。",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "target_lanlan": {"type": "string", "default": ""},
            },
            "required": ["task_id"],
        },
        llm_result_fields=["queued", "task_id", "started_immediately"],
    )
    async def run_schedule_task_now(self, task_id: str, target_lanlan: str = "", **kwargs):
        target_id = str(task_id or "").strip()
        if not target_id:
            return Err(SdkError("task_id 不能为空"))

        should_start_now = False
        async with self._state_lock:
            task = self._tasks.get(target_id)
            if task is None:
                return Err(SdkError("任务不存在"))
            if str(task.get("status") or "") == "running":
                return Err(SdkError("任务已在执行中"))

            task["trigger_at"] = _iso(_utc_now())
            task["status"] = "pending"
            task["finished_at"] = None
            task["last_error"] = ""
            task["updated_at"] = _iso()
            task["current_index"] = 0
            if target_lanlan.strip():
                task["target_lanlan"] = target_lanlan.strip()
            elif not str(task.get("target_lanlan") or "").strip():
                task["target_lanlan"] = self._resolve_target_lanlan(kwargs)
            should_start_now = self._active_task_id is None
            self._save_state_locked()

        self._wake_scheduler()
        self._ensure_scheduler_task()
        self._schedule_task_timer(target_id)
        if should_start_now:
            self._spawn_execute_task(target_id)

        return Ok({
            "queued": True,
            "task_id": target_id,
            "started_immediately": should_start_now,
            "message": "获取成功",
        })

    @plugin_entry(
        id="stop_schedule_task",
        name="停止执行任务",
        description="停止当前运行中的任务，支持指定 task_id。",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["stopped", "task_id"],
    )
    async def stop_schedule_task(self, task_id: str = "", **_):
        target = str(task_id or "").strip() or None
        ok = await self._request_active_control(_CTRL_STOP, target)
        return Ok({
            "stopped": ok,
            "task_id": target or self._active_task_id,
            "message": "获取成功" if ok else "当前无可停止任务",
        })

    @plugin_entry(
        id="toggle_schedule_task",
        name="切换运行或停止",
        description="任务运行/停止切换：运行中则停止，未运行则立即执行。",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
        llm_result_fields=["action", "task_id", "ok"],
    )
    async def toggle_schedule_task(self, task_id: str, **kwargs):
        target = str(task_id or "").strip()
        if not target:
            return Err(SdkError("task_id 不能为空"))

        async with self._state_lock:
            task = self._tasks.get(target)
            if task is None:
                return Err(SdkError("任务不存在"))
            running = str(task.get("status") or "") == "running"

        if running:
            ok = await self._request_active_control(_CTRL_STOP, target)
            return Ok({"action": "stop", "task_id": target, "ok": ok, "message": "获取成功"})

        run_result = await self.run_schedule_task_now(task_id=target, **kwargs)
        if isinstance(run_result, Err):
            return run_result
        return Ok({"action": "run", "task_id": target, "ok": True, "message": "获取成功"})

    @plugin_entry(
        id="skip_current_track",
        name="切换当前曲目",
        description="在运行任务中切换上一首或下一首。",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "default": ""},
                "direction": {"type": "string", "enum": ["prev", "next"], "default": "next"},
            },
        },
        llm_result_fields=["accepted", "direction", "task_id"],
    )
    async def skip_current_track(self, task_id: str = "", direction: str = "next", **_):
        direct = _CTRL_PREV if str(direction or "").lower() == "prev" else _CTRL_NEXT
        target = str(task_id or "").strip() or None
        accepted = await self._request_active_control(direct, target)
        return Ok({
            "accepted": accepted,
            "direction": "prev" if direct == _CTRL_PREV else "next",
            "task_id": target or self._active_task_id,
            "message": "获取成功" if accepted else "当前无可切换任务",
        })

    @plugin_entry(
        id="report_playback_state",
        name="上报播放状态",
        description="前端播放器上报实时进度，以便可视化与任务状态联动。",
        input_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "default": "idle"},
                "position_sec": {"type": "number", "default": 0},
                "duration_sec": {"type": "number", "default": 0},
                "track_url": {"type": "string", "default": ""},
                "track_title": {"type": "string", "default": ""},
            },
        },
        llm_result_fields=["updated"],
    )
    async def report_playback_state(
        self,
        status: str = "idle",
        position_sec: float | int = 0,
        duration_sec: float | int = 0,
        track_url: str = "",
        track_title: str = "",
        **_,
    ):
        self._playback_state = {
            "status": str(status or "idle").strip().lower() or "idle",
            "position_sec": max(0.0, float(position_sec or 0.0)),
            "duration_sec": max(0.0, float(duration_sec or 0.0)),
            "updated_at": _iso(),
            "track_url": str(track_url or "").strip(),
            "track_title": str(track_title or "").strip(),
        }
        return Ok({"updated": True, "message": "获取成功"})

    @plugin_entry(
        id="list_schedule_tasks",
        name="列出定时任务",
        description="获取任务列表和当前执行进度。",
        llm_result_fields=["total"],
    )
    async def list_schedule_tasks(self, **_):
        async with self._state_lock:
            tasks = [self._serialize_task(t) for t in self._tasks.values()]
            runtime = self._build_runtime_snapshot()

        tasks.sort(key=lambda t: (str(t.get("trigger_at") or ""), str(t.get("task_id") or "")))
        return Ok(
            {
                "tasks": tasks,
                "runtime": runtime,
                "total": len(tasks),
            }
        )

    async def _scheduler_loop(self) -> None:
        while not self._scheduler_stop.is_set():
            try:
                await asyncio.sleep(_SCHEDULER_TICK_SECONDS)
                await self._execute_due_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.error(f"调度循环异常，1秒后继续: {exc}")
                await asyncio.sleep(1)

    async def _execute_due_once(self) -> None:
        due_task_id: str | None = None
        async with self._state_lock:
            if self._active_task_id is not None:
                return
            now = _utc_now()
            pending = [
                t for t in self._tasks.values()
                if str(t.get("status") or "") == "pending"
            ]
            pending.sort(key=lambda t: str(t.get("trigger_at") or ""))

            for task in pending:
                try:
                    trigger_dt = _parse_datetime_to_utc(str(task.get("trigger_at") or ""))
                except Exception:
                    trigger_dt = now
                if trigger_dt <= now:
                    due_task_id = str(task.get("task_id") or "").strip()
                    break

        if due_task_id:
            self._spawn_execute_task(due_task_id)

    async def _execute_task(self, task_id: str) -> None:
        async with self._run_lock:
            self._cancel_task_timer(task_id)
            current_execution_task = asyncio.current_task()
            if current_execution_task is not None:
                self._active_execution_task = current_execution_task

            async with self._state_lock:
                task = self._tasks.get(task_id)
                if task is None:
                    return
                if str(task.get("status") or "") != "pending":
                    return

                queue = task.get("queue")
                if not isinstance(queue, list) or not queue:
                    task["status"] = "failed"
                    task["last_error"] = "任务队列为空"
                    task["updated_at"] = _iso()
                    task["finished_at"] = _iso()
                    self._save_state_locked()
                    return

                task["status"] = "running"
                task["started_at"] = _iso()
                task["finished_at"] = None
                task["last_error"] = ""
                task["current_index"] = 0
                task["updated_at"] = _iso()
                self._active_task_id = task_id
                self._active_control = None
                self._active_track = None
                self._active_track_started_at = None
                self._active_track_duration = 0
                target_lanlan = str(task.get("target_lanlan") or "").strip() or self._resolve_target_lanlan({})
                self._save_state_locked()

            success_count = 0
            last_error = ""
            stopped_by_user = False
            queue_size = len(queue)
            index = 0

            async def _consume_control() -> str | None:
                async with self._state_lock:
                    cmd = self._active_control
                    self._active_control = None
                    return cmd

            try:
                while index < queue_size:
                    if self._scheduler_stop.is_set():
                        last_error = "插件关闭或重载，中断本次执行"
                        break

                    cmd_before = await _consume_control()
                    if cmd_before == _CTRL_STOP:
                        stopped_by_user = True
                        last_error = "用户手动停止任务"
                        break
                    if cmd_before == _CTRL_PREV:
                        index = max(0, index - 1)
                        continue
                    if cmd_before == _CTRL_NEXT:
                        index = min(queue_size - 1, index + 1)

                    track = queue[index]

                    if not isinstance(track, dict):
                        last_error = f"第 {index + 1} 首队列项格式无效"
                        index += 1
                        continue

                    url = self._normalize_legacy_url(str(track.get("url") or "").strip())
                    title = _safe_text(track.get("title"), "未命名音乐")
                    artist = _safe_text(track.get("artist"), "未知")
                    duration = _normalize_duration(track.get("duration_sec"))
                    lyric_text = str(track.get("lyric_text") or "").replace("\r\n", "\n").strip()
                    if not lyric_text:
                        async with self._state_lock:
                            lyric_text = self._extract_lyric_for_item_locked(str(track.get("item_id") or ""))

                    if not url:
                        last_error = f"第 {index + 1} 首缺少 URL"
                        index += 1
                        continue

                    async with self._state_lock:
                        task_live = self._tasks.get(task_id)
                        if task_live is None:
                            break
                        task_live["current_index"] = index + 1
                        task_live["updated_at"] = _iso()
                        self._active_track = {
                            "index": index + 1,
                            "total": queue_size,
                            "title": title,
                            "artist": artist,
                            "url": url,
                        }
                        self._active_track_started_at = time.time()
                        self._active_track_duration = duration
                        self._playback_state = {
                            "status": "running",
                            "position_sec": 0.0,
                            "duration_sec": float(duration),
                            "updated_at": _iso(),
                            "track_url": url,
                            "track_title": title,
                        }
                        self._save_state_locked()

                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(
                                self._push_music_link,
                                url=url,
                                title=title,
                                artist=artist,
                                target_lanlan=target_lanlan,
                                lyric_text=lyric_text,
                            ),
                            timeout=_PUSH_TIMEOUT_SECONDS,
                        )
                        success_count += 1
                    except asyncio.TimeoutError:
                        last_error = f"推送超时[{title}]"
                        self.logger.warning(last_error)
                    except Exception as exc:
                        last_error = f"推送失败[{title}]: {exc}"
                        self.logger.warning(last_error)

                    switch_to_prev = False
                    switch_to_next = False
                    end_at = time.time() + duration
                    while time.time() < end_at and not self._scheduler_stop.is_set():
                        await asyncio.sleep(0.25)
                        now_pos = max(0.0, min(float(duration), time.time() - float(self._active_track_started_at or time.time())))
                        self._playback_state = {
                            "status": "running",
                            "position_sec": round(now_pos, 2),
                            "duration_sec": float(duration),
                            "updated_at": _iso(),
                            "track_url": url,
                            "track_title": title,
                        }

                        cmd = await _consume_control()
                        if cmd == _CTRL_STOP:
                            stopped_by_user = True
                            last_error = "用户手动停止任务"
                            break
                        if cmd == _CTRL_PREV:
                            switch_to_prev = True
                            break
                        if cmd == _CTRL_NEXT:
                            switch_to_next = True
                            break

                    if stopped_by_user:
                        break
                    if self._scheduler_stop.is_set():
                        last_error = "插件关闭或重载，中断本次执行"
                        break

                    if switch_to_prev:
                        index = max(0, index - 1)
                        continue
                    if switch_to_next:
                        index = min(queue_size, index + 1)
                        continue

                    index += 1

                async with self._state_lock:
                    task_done = self._tasks.get(task_id)
                    if task_done is not None:
                        total = len(task_done.get("queue") or [])
                        if stopped_by_user:
                            final_status = "stopped"
                        elif self._scheduler_stop.is_set():
                            final_status = "stopped"
                        else:
                            final_status = "completed" if success_count > 0 else "failed"
                        task_done["status"] = final_status
                        task_done["finished_at"] = _iso()
                        task_done["updated_at"] = _iso()
                        task_done["current_index"] = min(total, max(0, int(index)))
                        task_done["last_error"] = last_error
                        self._save_state_locked()
            finally:
                async with self._state_lock:
                    self._active_task_id = None
                    self._active_control = None
                    self._active_track = None
                    self._active_track_started_at = None
                    self._active_track_duration = 0
                    self._playback_state = {
                        "status": "idle",
                        "position_sec": 0.0,
                        "duration_sec": 0.0,
                        "updated_at": _iso(),
                        "track_url": "",
                        "track_title": "",
                    }
                    self._save_state_locked()
                self._wake_scheduler()
