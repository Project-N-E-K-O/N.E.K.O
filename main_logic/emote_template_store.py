# -*- coding: utf-8 -*-
"""Persist one independent copy of each imported template.

The saved copy remains authoritative if the original is changed, moved or deleted.
Importing an existing key replaces its copy rather than merging archives. New
archives use a spine-<sha256> content identifier; original names are display
metadata. Legacy keys retain their hashed locations so existing files remain
accessible without bulk renaming. Storage has no quota or expiry: report actual
bytes and release them only when the user deletes a copy. The corresponding
storage tests cover these rules.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path, PurePosixPath

# 与 main_logic.emote_template_adapter / 客户端 ClientZipAssetStore 的上限保持一致：
# 拷贝不会导入比原件更大的东西。
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_TEXTURE_BYTES = 64 * 1024 * 1024
MAX_TEXTURE_TOTAL_BYTES = 256 * 1024 * 1024
MAX_TEXTURE_FILES = 2000
MAX_STAGING_AGE_SECONDS = 60 * 60

ARCHIVE_NAME = "template.zip"
TEXTURES_DIR = "textures"
META_NAME = "meta.json"
_KEY_HASH_ALGORITHM = "sha256"
_replacement_lock = threading.RLock()
_active_staging: set[Path] = set()


def _link_or_copy(source, destination):
    """Share unchanged files on supported filesystems; preserve copy fallback."""
    try:
        os.link(source, destination)
    except OSError:
        return shutil.copy2(source, destination)
    return str(destination)

class EmoteTemplateStoreError(Exception):
    """Report a storage failure with a message suitable for the API response."""


def dir_key_for(key: str) -> str:
    """Use canonical Spine content IDs and preserve hashed locations for legacy keys."""
    if re.fullmatch(r"spine-[0-9a-f]{64}", str(key)):
        return str(key)
    digest = hashlib.new(_KEY_HASH_ALGORITHM, str(key or "").encode("utf-8"))
    return digest.hexdigest()


def normalize_texture_path(value: str) -> str:
    """Allow PNG textures within a copy and GIF images within StageImages."""
    raw = str(value or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or "\x00" in raw:
        raise EmoteTemplateStoreError("贴图路径不合法")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise EmoteTemplateStoreError("贴图路径不合法")
    path = "/".join(parts)
    is_stage_gif = path.startswith("StageImages/") and PurePosixPath(path).suffix.lower() == ".gif"
    if not path.lower().endswith(".png") and not is_stage_gif:
        raise EmoteTemplateStoreError("贴图只能是 PNG")
    return path


def _dir_size(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


class EmoteTemplateArchiveStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._recovery_blocked_keys: set[str] = set()
        self._recover_retired()

    def _recover_retired(self) -> None:
        """Recover a complete old copy if directory publication was interrupted."""
        with _replacement_lock:
            blocked = self._recovery_blocked_keys
            # Generated names include the retirement time. Prefer the newest
            # candidate before removing older copies of the same key.
            for retired in sorted(self.root.glob('.retired-*'), reverse=True):
                if retired.is_symlink() or not retired.is_dir():
                    continue
                meta = self._read_meta(retired)
                if not meta or not (retired / ARCHIVE_NAME).is_file():
                    continue
                key = str(meta['key'])
                if key in blocked:
                    continue
                final = self._entry_dir(key)
                if not final.exists():
                    try:
                        os.replace(retired, final)
                    except OSError:
                        blocked.add(key)
                        print('[emote-template] Skipped an inaccessible recovery candidate')
                elif ((final / ARCHIVE_NAME).is_file()
                      and (self._read_meta(final) or {}).get('key') == meta['key']):
                    try:
                        shutil.rmtree(retired)
                    except OSError:
                        blocked.add(key)
                        print('[emote-template] Skipped an inaccessible retired copy')
            self._cleanup_staging()

    def _cleanup_staging(self) -> None:
        cutoff = time.time() - MAX_STAGING_AGE_SECONDS
        root = self.root.resolve()
        for staging in self.root.glob('.uploading-*'):
            try:
                resolved = staging.resolve()
                if (staging.is_symlink() or not staging.is_dir() or resolved.parent != root
                        or resolved in _active_staging):
                    continue
                modified = staging.stat().st_mtime
                match = re.fullmatch(r'\.uploading-(\d+)-[0-9a-f]+', staging.name)
                created = int(match[1]) / 1000 if match else modified
                if max(created, modified) <= cutoff:
                    shutil.rmtree(staging)
            except OSError:
                print('[emote-template] Skipped an inaccessible staging copy')

    def _new_staging(self) -> Path:
        with _replacement_lock:
            staging = self.root / f".uploading-{int(time.time() * 1000)}-{os.urandom(4).hex()}"
            _active_staging.add(staging.resolve())
            return staging

    def _discard_staging(self, staging: Path) -> None:
        # Keep it registered while deleting, so recovery skips it without
        # making unrelated stores wait for a potentially large directory tree.
        try:
            shutil.rmtree(staging, ignore_errors=True)
        finally:
            with _replacement_lock:
                _active_staging.discard(staging.resolve())

    def _require_writable(self, key: str) -> None:
        with _replacement_lock:
            blocked = str(key) in self._recovery_blocked_keys
            if not blocked and not self._entry_dir(key).exists():
                # Another store may have failed publication after this instance
                # was created. Reject rather than restore here: callers may
                # already have collected an empty image list for reimport.
                blocked = any(
                    not retired.is_symlink() and retired.is_dir()
                    and (retired / ARCHIVE_NAME).is_file()
                    and (self._read_meta(retired) or {}).get('key') == str(key)
                    for retired in self.root.glob('.retired-*')
                )
            if blocked:
                raise EmoteTemplateStoreError("这个模板的副本尚未恢复，请稍后重试")

    def _publish_copy(self, key: str, staging: Path) -> None:
        """Publish a complete archive/image generation with recoverable rollback."""
        final = self._entry_dir(key)
        retired = self.root / f".retired-{int(time.time() * 1000)}-{os.urandom(4).hex()}"
        with _replacement_lock:
            self._require_writable(key)
            swapped_old = False
            try:
                # Windows cannot replace a populated directory directly.
                if final.exists():
                    os.replace(final, retired)
                    swapped_old = True
                os.replace(staging, final)
            except OSError:
                if swapped_old and not final.exists() and retired.exists():
                    try:
                        os.replace(retired, final)
                    except OSError:
                        self._recovery_blocked_keys.add(str(key))
                        raise
                raise
            finally:
                # Retain the recovery copy if publication and rollback both fail.
                if final.is_dir():
                    shutil.rmtree(retired, ignore_errors=True)

    # ── 路径解析 ────────────────────────────────────────────────────────────
    def _entry_dir(self, key: str) -> Path:
        return self.root / dir_key_for(key)

    def _safe_child(self, base: Path, relative: str) -> Path:
        normalized = normalize_texture_path(relative)
        candidate = (base / Path(*PurePosixPath(normalized).parts)).resolve()
        base_resolved = base.resolve()
        if base_resolved != candidate and base_resolved not in candidate.parents:
            raise EmoteTemplateStoreError("贴图路径越界")
        return candidate

    # ── 读 ─────────────────────────────────────────────────────────────────
    def _read_meta(self, entry_dir: Path) -> dict | None:
        try:
            meta = json.loads((entry_dir / META_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return meta if isinstance(meta, dict) and meta.get("key") else None

    def texture_paths(self, key: str) -> list[str]:
        """Return stable, ordered supplemental texture paths relative to textures/."""
        textures_dir = self._entry_dir(key) / TEXTURES_DIR
        if not textures_dir.is_dir():
            return []
        paths = []
        for path in sorted(textures_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = "/".join(path.relative_to(textures_dir).parts)
            try:
                normalize_texture_path(relative)
            except EmoteTemplateStoreError:
                continue  # OS metadata and interrupted writes are not textures.
            paths.append(relative)
        return paths

    def list(self) -> list[dict]:
        """List saved copies and their actual byte sizes measured from the filesystem."""
        if not self.root.is_dir():
            return []
        entries = []
        for entry_dir in sorted(self.root.iterdir()):
            if not entry_dir.is_dir() or entry_dir.name.startswith("."):
                continue
            meta = self._read_meta(entry_dir)
            if not meta:
                continue
            entries.append({
                "key": str(meta["key"]),
                "savedAt": int(meta.get("savedAt") or 0),
                "bytes": _dir_size(entry_dir),
            })
        return entries


    def has(self, key: str) -> bool:
        entry_dir = self._entry_dir(key)
        return (entry_dir / ARCHIVE_NAME).is_file()

    def archive_path(self, key: str) -> Path:
        entry_dir = self._entry_dir(key)
        archive = entry_dir / ARCHIVE_NAME
        if not archive.is_file():
            raise EmoteTemplateStoreError("这个模板还没有本地副本")
        return archive

    def archive_name(self, key: str) -> str:
        """Return the original archive filename for restoration and display metadata."""
        if re.fullmatch(r"spine-[0-9a-f]{64}", str(key)):
            return str(key) + ".zip"
        meta = self._read_meta(self._entry_dir(key))
        return str((meta or {}).get("archiveName") or "").strip() or "template.zip"

    def texture_path(self, key: str, relative: str) -> Path:
        entry_dir = self._entry_dir(key)
        textures_dir = entry_dir / TEXTURES_DIR
        if not textures_dir.is_dir():
            raise EmoteTemplateStoreError("这个模板没有附带贴图")
        candidate = self._safe_child(textures_dir, relative)
        if not candidate.is_file():
            raise EmoteTemplateStoreError("贴图不存在")
        return candidate

    # ── 写 ─────────────────────────────────────────────────────────────────
    def _clean_textures(self, textures: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
        seen: dict[str, str] = {}
        cleaned: list[tuple[str, bytes]] = []
        total_texture_bytes = 0
        for relative, blob in textures or []:
            path = normalize_texture_path(relative)
            lowered = path.lower()
            if lowered in seen:
                if seen[lowered] != path:
                    raise EmoteTemplateStoreError(f"贴图路径大小写冲突：{path}")
                continue
            blob_bytes = bytes(blob or b"")
            if len(blob_bytes) > MAX_TEXTURE_BYTES:
                raise EmoteTemplateStoreError(f"贴图过大：{path}")
            total_texture_bytes += len(blob_bytes)
            if total_texture_bytes > MAX_TEXTURE_TOTAL_BYTES:
                raise EmoteTemplateStoreError("附带贴图总大小超过上限")
            seen[lowered] = path
            cleaned.append((path, blob_bytes))
        if len(cleaned) > MAX_TEXTURE_FILES:
            raise EmoteTemplateStoreError(f"贴图数量超过上限（最多 {MAX_TEXTURE_FILES} 张）")
        return cleaned

    def save(
        self,
        key: str,
        archive: bytes,
        textures: list[tuple[str, bytes]] | None = None,
        filename: str = "",
        *,
        preserve_textures: bool = False,
    ) -> dict:
        """Persist an archive, replacing the entire saved copy when its key already exists.

        Preserve the supplied original filename as metadata for archive restoration.
        Reimports can retain supplemental textures under the publication lock.
        """
        self._require_writable(key)
        if not str(key or "").strip():
            raise EmoteTemplateStoreError("缺少模板标识")
        if not archive:
            raise EmoteTemplateStoreError("模板包是空的")
        if len(archive) > MAX_ARCHIVE_BYTES:
            raise EmoteTemplateStoreError(
                f"模板包过大，副本上限为 {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB"
            )
        cleaned = self._clean_textures(textures)

        self.root.mkdir(parents=True, exist_ok=True)
        staging = self._new_staging()
        final = self._entry_dir(key)
        try:
            staging.mkdir(parents=True)
            (staging / ARCHIVE_NAME).write_bytes(archive)
            with _replacement_lock:
                self._require_writable(key)
                if preserve_textures:
                    replacements = {path for path, _ in cleaned}
                    cleaned = self._clean_textures(cleaned + [
                        (path, self.texture_path(key, path).read_bytes())
                        for path in self.texture_paths(key) if path not in replacements
                    ])
                for relative, blob in cleaned:
                    target = self._safe_child(staging / TEXTURES_DIR, relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(blob)
                meta = {
                    "key": str(key),
                    "savedAt": int(time.time() * 1000),
                    "archiveBytes": len(archive),
                    "textureCount": len(cleaned),
                    "archiveName": (str(key) + ".zip") if re.fullmatch(r"spine-[0-9a-f]{64}", str(key)) else str(filename or "").strip()[:200],
                    "sourceName": str(filename or "").strip()[:200],
                }
                (staging / META_NAME).write_text(
                    json.dumps(meta, ensure_ascii=False), encoding="utf-8"
                )
                self._publish_copy(key, staging)
        except OSError as exc:
            raise EmoteTemplateStoreError(f"副本写入失败：{exc}") from exc
        finally:
            self._discard_staging(staging)
        return {
            "key": str(key),
            "bytes": _dir_size(final),
            "textures": [relative for relative, _ in cleaned],
        }

    def save_textures(self, key: str, textures: list[tuple[str, bytes]]) -> dict:
        """Merge supplemental textures into an existing saved copy.

        Replace matching paths while retaining other textures. Persisting textures
        supplied after import makes them available when the editor reopens.
        """
        self._require_writable(key)
        if not self.has(key):
            raise EmoteTemplateStoreError("这个模板还没有本地副本")
        cleaned = self._clean_textures(textures)
        if not cleaned:
            return {"key": str(key), "textures": self.texture_paths(key)}
        textures_dir = self._entry_dir(key) / TEXTURES_DIR
        existing = self.texture_paths(key)
        spellings = {}
        for relative in existing + [path for path, _ in cleaned]:
            lowered = relative.lower()
            if lowered in spellings and spellings[lowered] != relative:
                raise EmoteTemplateStoreError(f"贴图路径大小写冲突：{relative}")
            spellings[lowered] = relative
        replacements = dict(cleaned)
        total_bytes = sum(len(blob) for blob in replacements.values()) + sum(
            self._safe_child(textures_dir, path).stat().st_size for path in existing if path not in replacements
        )
        if len(set(existing) | replacements.keys()) > MAX_TEXTURE_FILES:
            raise EmoteTemplateStoreError(f"贴图数量超过上限（最多 {MAX_TEXTURE_FILES} 张）")
        if total_bytes > MAX_TEXTURE_TOTAL_BYTES:
            raise EmoteTemplateStoreError("附带贴图总大小超过上限")
        staging = self._new_staging()
        try:
            # Share unchanged files; detach replacements before writing so a
            # failed batch cannot modify the currently published generation.
            with _replacement_lock:
                shutil.copytree(self._entry_dir(key), staging, copy_function=_link_or_copy)
                for relative, blob in cleaned:
                    target = self._safe_child(staging / TEXTURES_DIR, relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.unlink(missing_ok=True)
                    target.write_bytes(blob)
                self._publish_copy(key, staging)
        except OSError as exc:
            raise EmoteTemplateStoreError(f"贴图写入失败：{exc}") from exc
        finally:
            self._discard_staging(staging)
        return {"key": str(key), "textures": self.texture_paths(key)}

    def remove_texture(self, key: str, relative: str) -> None:
        """Remove one texture file from a saved copy."""
        self._require_writable(key)
        textures_dir = self._entry_dir(key) / TEXTURES_DIR
        target = self._safe_child(textures_dir, relative)
        if target.is_file():
            target.unlink()

    def delete(self, key: str) -> dict:
        freed = 0
        with _replacement_lock:
            # Remove recovery candidates first, so a later constructor cannot
            # resurrect this key even if deletion of the published copy stops.
            targets = [retired for retired in self.root.glob('.retired-*')
                       if retired.is_dir() and not retired.is_symlink()
                       and (self._read_meta(retired) or {}).get('key') == str(key)]
            targets.append(self._entry_dir(key))
            for entry_dir in targets:
                if not entry_dir.is_dir():
                    continue
                freed += _dir_size(entry_dir)
                try:
                    shutil.rmtree(entry_dir)
                except OSError as exc:
                    raise EmoteTemplateStoreError("副本删除未完成，请重试") from exc
        return {"key": str(key), "freedBytes": freed}
