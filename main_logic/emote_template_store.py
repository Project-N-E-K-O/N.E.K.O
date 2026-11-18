# -*- coding: utf-8 -*-
"""导入即拷副本：每个模板一份落盘的拷贝，重开时不再要求用户重新定位。

设计上只有三条硬规则（和前端模板库的约定一一对应）：

1. **副本即唯一事实**：原件更新/删除/挪走都与这里无关；想更新就再导一次，
   同键原地覆盖（``save`` 永远是替换语义，没有合并）。
2. 新包使用资源内容生成的 ``spine-<sha256>`` 作为库键和目录名；原文件名只作显示。
   旧库键仍通过哈希定位原有副本，以便用户查看和删除，不批量重命名用户文件。
3. **没有配额、没有过期清理**：占多少由调用方如实报告，删不删由用户决定；
   ``delete`` 是唯一释放入口。

改动这三条之前先看 tests/unit/test_emote_template_store.py——每条规则都有对应的红灯。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path, PurePosixPath

# 与 main_logic.emote_template_adapter / 客户端 ClientZipAssetStore 的上限保持一致：
# 拷贝不会导入比原件更大的东西。
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_TEXTURE_BYTES = 64 * 1024 * 1024
MAX_TEXTURE_TOTAL_BYTES = 256 * 1024 * 1024
MAX_TEXTURE_FILES = 2000

ARCHIVE_NAME = "template.zip"
TEXTURES_DIR = "textures"
META_NAME = "meta.json"
_KEY_HASH_ALGORITHM = "sha256"

class EmoteTemplateStoreError(Exception):
    """副本存取失败。message 面向用户，可直接透传到接口响应。"""


def dir_key_for(key: str) -> str:
    """使用规范 Spine 内容标识作为目录名，旧格式库键保留原有哈希定位方式。"""
    if re.fullmatch(r"spine-[0-9a-f]{64}", str(key)):
        return str(key)
    digest = hashlib.new(_KEY_HASH_ALGORITHM, str(key or "").encode("utf-8"))
    return digest.hexdigest()


def normalize_texture_path(value: str) -> str:
    """限于副本内的 PNG，以及 StageImages 下的 GIF。"""
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
        """这份副本附带的补全贴图（相对 textures/ 的路径，按序稳定）。"""
        textures_dir = self._entry_dir(key) / TEXTURES_DIR
        if not textures_dir.is_dir():
            return []
        return [
            "/".join(path.relative_to(textures_dir).parts)
            for path in sorted(textures_dir.rglob("*"))
            if path.is_file()
        ]

    def list(self) -> list[dict]:
        """全部副本 + 各自真实占用的字节数（stat 出来的，不是存的）。"""
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
        """当初那个 zip 的文件名；恢复时必须原样归还，库身份才不会分裂。"""
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
        seen: set[str] = set()
        cleaned: list[tuple[str, bytes]] = []
        total_texture_bytes = 0
        for relative, blob in textures or []:
            path = normalize_texture_path(relative)
            lowered = path.lower()
            if lowered in seen:
                continue
            blob_bytes = bytes(blob or b"")
            if len(blob_bytes) > MAX_TEXTURE_BYTES:
                raise EmoteTemplateStoreError(f"贴图过大：{path}")
            total_texture_bytes += len(blob_bytes)
            if total_texture_bytes > MAX_TEXTURE_TOTAL_BYTES:
                raise EmoteTemplateStoreError("附带贴图总大小超过上限")
            seen.add(lowered)
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
    ) -> dict:
        """落一份副本；同键已存在就整体替换（原件语义：再导一次 = 更新）。

        ``filename`` 是当初那个 zip 的名字。库键的第一段就是它（adapter 用上传文件名
        当 packageName），恢复时不原样归还，同一个模板就会分裂成两个库身份。
        """
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
        staging = self.root / f".uploading-{int(time.time() * 1000)}-{os.urandom(4).hex()}"
        final = self._entry_dir(key)
        retired = self.root / f".retired-{int(time.time() * 1000)}-{os.urandom(4).hex()}"
        try:
            staging.mkdir(parents=True)
            (staging / ARCHIVE_NAME).write_bytes(archive)
            if cleaned:
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
            # Windows 上 os.replace 不能盖非空目录：先把旧的挪去待删，再换新的进来。
            # 换新失败时把旧目录原样换回去，宁可留一个 .retired- 孤儿也不丢用户副本。
            swapped_old = False
            try:
                if final.exists():
                    os.replace(final, retired)
                    swapped_old = True
                os.replace(staging, final)
            except OSError:
                if swapped_old and not final.exists() and retired.exists():
                    os.replace(retired, final)
                raise
        except OSError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise EmoteTemplateStoreError(f"副本写入失败：{exc}") from exc
        finally:
            # 只有 final 活着才清旧目录：restore-back 也失败时留着孤儿，比删掉强
            if final.is_dir():
                shutil.rmtree(retired, ignore_errors=True)
        return {
            "key": str(key),
            "bytes": _dir_size(final),
            "textures": [relative for relative, _ in cleaned],
        }

    def save_textures(self, key: str, textures: list[tuple[str, bytes]]) -> dict:
        """往已有副本里合并贴图（导入之后才补的那批）。同路径覆盖，其余保留。

        这是"补全缺失贴图"的持久化出口：没有它，导入后补的贴图只活在本次会话，
        重开一次就丢 —— 而那正是副本要消掉的痛点。
        """
        if not self.has(key):
            raise EmoteTemplateStoreError("这个模板还没有本地副本")
        cleaned = self._clean_textures(textures)
        textures_dir = self._entry_dir(key) / TEXTURES_DIR
        for relative, blob in cleaned:
            target = self._safe_child(textures_dir, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(blob)
                os.replace(temporary, target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return {"key": str(key), "textures": self.texture_paths(key)}

    def remove_texture(self, key: str, relative: str) -> None:
        """从副本里删掉单张贴图文件。"""
        textures_dir = self._entry_dir(key) / TEXTURES_DIR
        target = self._safe_child(textures_dir, relative)
        if target.is_file():
            target.unlink()

    def delete(self, key: str) -> dict:
        entry_dir = self._entry_dir(key)
        if not entry_dir.is_dir():
            return {"key": str(key), "freedBytes": 0}
        freed = _dir_size(entry_dir)
        shutil.rmtree(entry_dir, ignore_errors=True)
        if entry_dir.exists():
            raise EmoteTemplateStoreError("副本删除未完成，请重试")
        return {"key": str(key), "freedBytes": freed}
