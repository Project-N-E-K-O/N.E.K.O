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
MMD Router

Handles MMD model-related endpoints including:
- MMD model listing (PMX/PMD)
- MMD model upload
- VMD animation listing and upload
- MMD emotion mapping configuration

URL convention: routes declared WITHOUT trailing slash (no ``@router.get('/')``).
See ``main_routers/characters_router.py`` docstring or
``.agent/rules/neko-guide.md`` (§\"No trailing slash in API URLs\") for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""

import asyncio
import json
import os
import re
import shutil
import tempfile
import traceback
import zipfile
from pathlib import Path

import charset_normalizer
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse

from .shared_state import get_config_manager
from .workshop_router import get_subscribed_workshop_items
from utils.file_utils import atomic_write_json_async
from utils.logger_config import get_module_logger
from utils.url_utils import is_utf8_text

router = APIRouter(prefix="/api/model/mmd", tags=["mmd"])
logger = get_module_logger(__name__, "Main")

# MMD model path constants.
MMD_USER_PATH = "/user_mmd"
MMD_STATIC_PATH = "/static/mmd"

# File-upload constants.
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB (MMD models with textures can be large).
MAX_ZIP_UNCOMPRESSED = 2 * 1024 * 1024 * 1024  # 2GB extraction cap prevents ZIP bombs.
MAX_ZIP_ENTRIES = 10000  # Maximum files inside ZIP.
CHUNK_SIZE = 1024 * 1024  # 1MB chunks

# Allowed file extensions.
ALLOWED_MODEL_EXTENSIONS = {'.pmx', '.pmd'}
ALLOWED_ANIMATION_EXTENSIONS = {'.vmd'}
RESERVED_DIRS = {'animation', 'emotion_config'}


def _file_check(path: Path) -> bool | None:
    """``path.is_file()``, or None when the type cannot be checked.

    On Python 3.11 ``is_file()`` raises for e.g. a link into an unreadable
    folder (EACCES) instead of returning False.
    """
    try:
        return path.is_file()
    except OSError:
        return None


def _iter_model_files(root: Path):
    """Yield model files under ``root``, matching the extension in any letter case.

    The upload checks accept ``Foo.PMX``; ``rglob('*.pmx')`` is case-sensitive on
    Linux, so scanning by pattern would lose such files. Folders named like a
    model (``old.PMX/``) and dangling links are not model files.

    A path whose type cannot be checked (e.g. a link into an unreadable folder
    raises PermissionError) is still yielded, as the name-only scan did: the
    "folder already has a model" checks must then keep the folder, not treat it
    as residue or fail into the import cleanup that deletes it.
    """
    for path in root.rglob('*'):
        if path.suffix.lower() not in ALLOWED_MODEL_EXTENSIONS:
            continue
        # Type unknown (None): keep the path so the folder is not deleted.
        if _file_check(path) is not False:
            yield path


def safe_mmd_path(mmd_dir: Path, filename: str, subdir: str | None = None) -> tuple[Path | None, str]:
    """Safely construct and validate paths inside the MMD directory, preventing path traversal attacks."""
    try:
        if subdir:
            target_path = mmd_dir / subdir / filename
        else:
            target_path = mmd_dir / filename

        resolved_path = target_path.resolve()
        resolved_mmd_dir = mmd_dir.resolve()

        try:
            if not resolved_path.is_relative_to(resolved_mmd_dir):
                return None, "路径越界：目标路径不在允许的目录内"
        except AttributeError:
            try:
                resolved_path.relative_to(resolved_mmd_dir)
            except ValueError:
                return None, "路径越界：目标路径不在允许的目录内"

        if resolved_path.exists() and resolved_path.is_dir():
            return None, "目标路径是目录，不是文件"

        return resolved_path, ""
    except Exception as e:
        return None, f"路径验证失败: {str(e)}"


def _ensure_mmd_directory(config_mgr) -> Path | None:
    """Ensure the MMD user directory exists and return its path."""
    try:
        mmd_dir = config_mgr.mmd_dir
        mmd_dir.mkdir(parents=True, exist_ok=True)
        animation_dir = config_mgr.mmd_animation_dir
        animation_dir.mkdir(parents=True, exist_ok=True)
        return mmd_dir
    except Exception as e:
        logger.error(f"创建 MMD 目录失败: {type(e).__name__}")
        print(f"创建 MMD 目录失败: {type(e).__name__}: {e}")
        return None


# ═══════════════════ ZIP encoding compatibility ═══════════════════
#
# MMD models often originate in Japan, with ZIP filenames encoded Shift-JIS (CP932).
# Python zipfile defaults to CP437 when UTF-8 flag is unset,
# garbling Japanese/Chinese filenames.
#
# Strategy:
#   1. Concatenate filename bytes and infer global encoding via charset_normalizer.
#   2. Prefer successful global detection; otherwise infer per file.
#   3. Sanitize decoded names, retaining Chinese/Japanese but removing OS-invalid characters.

def _detect_zip_encoding(zf: zipfile.ZipFile) -> str | None:
    """Detect the actual filename encoding of non-UTF-8 entries in a ZIP archive (strictly limited to CJK encodings)."""
    non_utf8_infos = [info for info in zf.infolist() if not (info.flag_bits & 0x800)]
    if not non_utf8_infos:
        return None

    # 1. Concatenate first 100 non-ASCII filenames for global detection.
    raw_bytes_blob = b""
    count = 0
    for info in non_utf8_infos:
        try:
            raw_bytes = info.filename.encode('cp437')
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        
        if any(b > 127 for b in raw_bytes):
            raw_bytes_blob += raw_bytes + b" "
            count += 1
            if count >= 100:
                break

    if not raw_bytes_blob:
        return None

    # 2. First defense: prefer UTF-8.
    try:
        raw_bytes_blob.decode('utf-8')
        return 'utf-8'
    except UnicodeDecodeError:
        pass

    # 3. Core change: constrain detector to common CJK encodings only.
    ALLOWED_ENCODINGS = {'cp932', 'shift_jis', 'gbk', 'gb18030', 'gb2312', 'big5', 'euc-kr'}
    
    results = charset_normalizer.from_bytes(raw_bytes_blob)
    for result in results:
        if result.encoding and result.encoding.lower() in ALLOWED_ENCODINGS:
            return result.encoding.lower()

    # 4. Safety fallback: Japanese-first ordering with strict round-trip validation.
    for fallback_enc in ('cp932', 'gbk', 'big5', 'euc-kr'):
        try:
            test_decoded = raw_bytes_blob.decode(fallback_enc)
            # Round trip: decode/reencode and compare original bytes, preventing forced garbling.
            if test_decoded.encode(fallback_enc) == raw_bytes_blob:
                return fallback_enc
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue

    return None


def _sanitize_filename(filename: str) -> str:
    """Sanitize a filename: convert backslashes to forward slashes to preserve directory structure, and strip illegal characters."""
    # CodeRabbit suggestion: normalize Windows backslashes to Web/Linux forward slashes first.
    normalized = filename.replace('\\', '/')
    # Split directory levels, sanitize each, then reassemble.
    return '/'.join(
        re.sub(r'[<>:"|?*]', '_', part)
        for part in normalized.split('/')
    )


def _build_zip_name_map(zf: zipfile.ZipFile) -> dict[str, str]:
    """Build a mapping from original names to correctly decoded and sanitized names for all ZIP entries."""
    global_encoding = _detect_zip_encoding(zf)
    name_map = {}

    # Common MMD fallback encodings, Japanese first.
    FALLBACK_ENCODINGS = ['cp932', 'gbk', 'big5', 'euc-kr']

    for info in zf.infolist():
        if not (info.flag_bits & 0x800):  # Entries not marked UTF-8.
            try:
                raw_bytes = info.filename.encode('cp437')
                decoded = None
                
                # Try global encoding.
                if global_encoding:
                    try:
                        test_decoded = raw_bytes.decode(global_encoding)
                        if test_decoded.encode(global_encoding) == raw_bytes:
                            decoded = test_decoded
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        pass
                
                # If global encoding fails for this file, brute-force fallbacks with round-trip checks.
                if not decoded:
                    for enc in FALLBACK_ENCODINGS:
                        try:
                            test_decoded = raw_bytes.decode(enc)
                            if test_decoded.encode(enc) == raw_bytes:
                                decoded = test_decoded
                                break
                        except (UnicodeDecodeError, UnicodeEncodeError):
                            continue
                            
                # Last resort: UTF-8 with replacement characters, ensuring no program crash.
                if not decoded:
                    decoded = raw_bytes.decode('utf-8', errors='replace')
                
                name_map[info.filename] = _sanitize_filename(decoded)
                
            except (UnicodeEncodeError, UnicodeDecodeError):
                name_map[info.filename] = _sanitize_filename(info.filename)
        else:
            # UTF-8-marked entries need sanitization only.
            name_map[info.filename] = _sanitize_filename(info.filename)

    if global_encoding:
        sample = next(
            ((orig, decoded) for orig, decoded in name_map.items() if orig != decoded),
            None
        )
        if sample:
            logger.info(f"ZIP 文件名编码推断及修正 ({global_encoding}): {sample[0]!r} → {sample[1]!r}")

    return name_map


def _extract_zip_with_encoding(
    zf: zipfile.ZipFile,
    target_dir: Path,
    name_map: dict[str, str]
):
    """Extract ZIP contents using the corrected filenames.

    Extracts entry by entry, writing files under target_dir at the paths
    corrected via name_map, with path-escape safety checks along the way.
    """
    resolved_target = target_dir.resolve()

    for info in zf.infolist():
        decoded_name = name_map.get(info.filename, info.filename)
        target_path = (target_dir / decoded_name).resolve()

        # Safety: ensure paths cannot escape target directory.
        if not target_path.is_relative_to(resolved_target):
            logger.warning(f"跳过路径越界的 ZIP 条目: {decoded_name!r}")
            continue

        if info.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            continue

        target_path.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target_path, 'wb') as dst:
            shutil.copyfileobj(src, dst)


async def _handle_mmd_file_upload(
    file: UploadFile,
    target_dir: Path,
    allowed_extensions: set,
    file_type_name: str,
    subdir: str | None = None
) -> JSONResponse:
    """Common streaming logic for handling MMD file uploads."""
    try:
        if not file:
            return JSONResponse(status_code=400, content={"success": False, "error": "没有上传文件"})

        filename = file.filename
        if not filename:
            return JSONResponse(status_code=400, content={"success": False, "error": "文件名为空"})

        ext = Path(filename).suffix.lower()
        if ext not in allowed_extensions:
            return JSONResponse(status_code=400, content={
                "success": False,
                "error": f"文件必须是 {', '.join(allowed_extensions)} 格式"
            })

        filename = Path(filename).name

        target_file_path, path_error = safe_mmd_path(target_dir, filename, subdir)
        if target_file_path is None:
            logger.warning(f"路径穿越尝试被阻止: {filename!r} - {path_error}")
            return JSONResponse(status_code=400, content={"success": False, "error": path_error})

        # Ensure parent directory exists.
        target_file_path.parent.mkdir(parents=True, exist_ok=True)

        total_size = 0
        try:
            with open(target_file_path, 'xb') as f:
                while True:
                    chunk = await file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > MAX_FILE_SIZE:
                        raise ValueError("FILE_TOO_LARGE")
                    f.write(chunk)
        except FileExistsError:
            return JSONResponse(status_code=400, content={
                "success": False,
                "error": f"{file_type_name} {filename} 已存在，请先删除或重命名"
            })
        except ValueError as ve:
            if str(ve) == "FILE_TOO_LARGE":
                try:
                    target_file_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return JSONResponse(status_code=400, content={
                    "success": False,
                    "error": f"文件过大，最大允许 {MAX_FILE_SIZE // (1024 * 1024)}MB"
                })
            raise
        except Exception as e:
            logger.error(f"文件上传写入失败: {e}")
            try:
                target_file_path.unlink(missing_ok=True)
            except Exception:
                pass
            return JSONResponse(status_code=500, content={"success": False, "error": f"保存文件失败: {str(e)}"})
        finally:
            try:
                await file.close()
            except Exception:
                pass

        logger.info(f"成功上传 {file_type_name}: {filename} ({total_size / (1024 * 1024):.2f}MB)")

        if subdir == 'animation':
            return JSONResponse(content={
                "success": True,
                "message": f"{file_type_name} {filename} 上传成功",
                "filename": filename,
                "file_path": f"{MMD_USER_PATH}/animation/{filename}"
            })
        else:
            model_name = Path(filename).stem
            return JSONResponse(content={
                "success": True,
                "message": f"{file_type_name} {filename} 上传成功",
                "model_name": model_name,
                "model_url": f"{MMD_USER_PATH}/{filename}",
                "file_size": total_size
            })

    except Exception as e:
        logger.error(f"上传 {file_type_name} 失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


# ═══════════════════ Route endpoints ═══════════════════


@router.post('/upload')
async def upload_mmd_model(file: UploadFile = File(...)):
    """Upload an MMD model file (PMX/PMD)."""
    config_mgr = get_config_manager()
    mmd_dir = _ensure_mmd_directory(config_mgr)
    if not mmd_dir:
        return JSONResponse(status_code=500, content={"success": False, "error": "MMD 目录创建失败"})

    return await _handle_mmd_file_upload(file, mmd_dir, ALLOWED_MODEL_EXTENSIONS, 'MMD 模型')


@router.post('/upload_animation')
async def upload_mmd_animation(file: UploadFile = File(...)):
    """Upload a VMD animation file."""
    config_mgr = get_config_manager()
    mmd_dir = _ensure_mmd_directory(config_mgr)
    if not mmd_dir:
        return JSONResponse(status_code=500, content={"success": False, "error": "MMD 目录创建失败"})

    return await _handle_mmd_file_upload(file, mmd_dir, ALLOWED_ANIMATION_EXTENSIONS, 'VMD 动画', 'animation')


@router.post('/upload_zip')
async def upload_mmd_zip(file: UploadFile = File(...)):
    """Upload an MMD model ZIP package (PMX/PMD + textures), automatically extracted into a subdirectory."""
    config_mgr = get_config_manager()
    mmd_dir = _ensure_mmd_directory(config_mgr)
    if not mmd_dir:
        return JSONResponse(status_code=500, content={"success": False, "error": "MMD 目录创建失败"})

    if not file or not file.filename:
        return JSONResponse(status_code=400, content={"success": False, "error": "没有上传文件"})

    if not file.filename.lower().endswith('.zip'):
        return JSONResponse(status_code=400, content={"success": False, "error": "请上传 .zip 文件"})

    # Write upload to temporary file before extraction, avoiding memory explosion.
    tmp_path = None
    target_dir = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
            tmp_path = Path(tmp.name)
            total_size = 0
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE:
                    return JSONResponse(status_code=400, content={
                        "success": False,
                        "error": f"文件过大，最大允许 {MAX_FILE_SIZE // (1024 * 1024)}MB"
                    })
                tmp.write(chunk)

        # Validate ZIP integrity and find PMX/PMD.
        if not zipfile.is_zipfile(str(tmp_path)):
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的 ZIP 文件"})

        with zipfile.ZipFile(str(tmp_path), 'r') as zf:
            # Build corrected-encoding map for non-UTF-8 names, e.g. Japanese Shift-JIS / Chinese GBK.
            name_map = _build_zip_name_map(zf)
            decoded_names = list(name_map.values())

            # Security: ZIP-bomb protection.
            info_list = zf.infolist()
            if len(info_list) > MAX_ZIP_ENTRIES:
                return JSONResponse(status_code=400, content={
                    "success": False,
                    "error": f"ZIP 内文件数 {len(info_list)} 超过上限 {MAX_ZIP_ENTRIES}"
                })
            total_uncompressed = sum(i.file_size for i in info_list)
            if total_uncompressed > MAX_ZIP_UNCOMPRESSED:
                return JSONResponse(status_code=400, content={
                    "success": False,
                    "error": f"ZIP 解压后大小 {total_uncompressed // (1024 * 1024)}MB 超过上限 {MAX_ZIP_UNCOMPRESSED // (1024 * 1024)}MB"
                })

            # Security: forbid absolute paths or ..
            for name in decoded_names:
                if name.startswith('/') or '..' in name:
                    return JSONResponse(status_code=400, content={
                        "success": False, "error": "ZIP 包含不安全的路径"
                    })

            # Find PMX/PMD using decoded names.
            model_entries = [
                n for n in decoded_names
                if Path(n).suffix.lower() in ALLOWED_MODEL_EXTENSIONS and not n.endswith('/')
            ]
            if not model_entries:
                return JSONResponse(status_code=400, content={
                    "success": False, "error": "ZIP 中未找到 .pmx 或 .pmd 模型文件"
                })

            # Pick first model, using its filename as subdirectory name.
            model_entry = model_entries[0]
            model_stem = Path(model_entry).stem

            # Detect common ZIP outer directory using decoded names.
            all_decoded_files = [n for n in decoded_names if not n.endswith('/')]
            top_level_items = {n.split('/')[0] for n in all_decoded_files}
            if len(top_level_items) == 1:
                zip_root_dir = top_level_items.pop()
                # Confirm directory rather than single file:
                # directory entry ends '/' or child paths share its prefix.
                is_dir = any(
                    n == f"{zip_root_dir}/" or n.startswith(f"{zip_root_dir}/")
                    for n in decoded_names
                )
                if is_dir:
                    # ZIP already has "model_name/..." layout.
                    extract_dir_name = zip_root_dir
                else:
                    # Single-file ZIP: create model-named subdirectory.
                    extract_dir_name = model_stem
            else:
                # Flat ZIP: create model-named subdirectory.
                extract_dir_name = model_stem

            target_dir = (mmd_dir / extract_dir_name).resolve()
            if not target_dir.is_relative_to(mmd_dir.resolve()):
                return JSONResponse(status_code=400, content={
                    "success": False, "error": "路径越界"
                })

            if extract_dir_name.lower() in RESERVED_DIRS:
                return JSONResponse(status_code=400, content={
                    "success": False, "error": f"目录名 '{extract_dir_name}' 是保留名称，不能用作模型目录"
                })

            if target_dir.exists():
                # Check valid model files; absent means leftover empty directory, automatically clean up.
                has_valid_model = any(_iter_model_files(target_dir))
                if has_valid_model:
                    return JSONResponse(status_code=400, content={
                        "success": False,
                        "error": f"目录 {extract_dir_name} 已存在，请先删除旧模型"
                    })
                else:
                    logger.info(f"清理残留的无效模型目录: {extract_dir_name}")
                    await asyncio.to_thread(shutil.rmtree, target_dir, ignore_errors=True)

            # Extract using corrected filenames.
            if all(
                n.startswith(extract_dir_name + '/') or n == extract_dir_name
                for n in decoded_names
            ):
                # ZIP already contains matching directory: extract directly into mmd_dir.
                await asyncio.to_thread(_extract_zip_with_encoding, zf, mmd_dir, name_map)
            else:
                # Extract into target_dir.
                target_dir.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(_extract_zip_with_encoding, zf, target_dir, name_map)

        # Find extracted PMX path.
        # Only a checked file can be returned as model_url for the frontend to load.
        pmx_candidates = [p for p in _iter_model_files(target_dir) if _file_check(p)]
        if not pmx_candidates:
            await asyncio.to_thread(shutil.rmtree, target_dir, ignore_errors=True)
            return JSONResponse(status_code=500, content={
                "success": False, "error": "解压后未找到模型文件"
            })

        pmx_file = pmx_candidates[0]
        # target_dir is resolved; resolve mmd_dir too, or a link or relative part
        # in mmd_dir makes relative_to raise and the import cleanup delete the model.
        rel_path = pmx_file.relative_to(mmd_dir.resolve())
        model_url = f"{MMD_USER_PATH}/{rel_path.as_posix()}"
        file_count = sum(1 for path in target_dir.rglob('*') if _file_check(path))

        logger.info(f"成功解压 MMD 模型包: {extract_dir_name} ({file_count} 个文件, {total_size / (1024*1024):.1f}MB)")

        return JSONResponse(content={
            "success": True,
            "message": f"MMD模型 {model_stem} 上传成功（含 {file_count} 个文件）",
            "model_name": model_stem,
            "model_url": model_url,
            "file_count": file_count,
            "file_size": total_size
        })

    except zipfile.BadZipFile:
        if target_dir and target_dir.exists():
            await asyncio.to_thread(shutil.rmtree, target_dir, ignore_errors=True)
        return JSONResponse(status_code=400, content={"success": False, "error": "ZIP 文件损坏"})
    except Exception as e:
        if target_dir and target_dir.exists():
            await asyncio.to_thread(shutil.rmtree, target_dir, ignore_errors=True)
        logger.error(f"上传 MMD ZIP 包失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        try:
            await file.close()
        except Exception:
            pass


@router.get('/models')
async def get_mmd_models():
    """List MMD models (PMX/PMD), including subdirectories."""
    try:
        config_mgr = get_config_manager()
        models = []
        seen_urls = set()
        skipped = 0  # Non-UTF-8 names cannot be serialized or requested (#3230).
        unchecked = 0  # Paths whose type cannot be checked (e.g. EACCES on Python 3.11).

        # 1. Project static/mmd/, recursively.
        project_root = config_mgr.project_root
        static_mmd_dir = project_root / "static" / "mmd"
        if static_mmd_dir.exists():
            for model_file in _iter_model_files(static_mmd_dir):
                # Dangling or looping link, or a path whose type cannot be checked:
                # leave it out so the other models still list.
                is_file = _file_check(model_file)
                if not is_file:
                    if is_file is None:
                        unchecked += 1
                    continue
                rel_path = model_file.relative_to(static_mmd_dir)
                if not is_utf8_text(rel_path.as_posix()):
                    skipped += 1
                    continue
                url = f"/static/mmd/{rel_path.as_posix()}"
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                models.append({
                    "name": model_file.stem,
                    "filename": model_file.name,
                    "url": url,
                    "rel_path": rel_path.as_posix(),
                    "type": model_file.suffix.lower().lstrip('.'),
                    "size": model_file.stat().st_size,
                    "location": "project"
                })

        # 2. User mmd/, recursively, skipping reserved directories.
        mmd_dir = _ensure_mmd_directory(config_mgr)
        if mmd_dir and mmd_dir.exists():
            found_top_dirs = set()  # Top-level subdirectories holding a model file, listed or skipped.
            for model_file in _iter_model_files(mmd_dir):
                is_file = _file_check(model_file)
                if is_file is False:  # dangling or looping link
                    continue
                try:
                    rel_path = model_file.relative_to(mmd_dir)
                    if rel_path.parts and rel_path.parts[0].lower() in RESERVED_DIRS:
                        continue
                except (ValueError, IndexError):
                    continue
                # Record the folder before the UTF-8 skip: a folder whose only model
                # has a non-UTF-8 name is not import residue (and is counted once).
                if rel_path.parts:
                    found_top_dirs.add(rel_path.parts[0])
                if is_file is None:
                    # Type cannot be checked: leave it out so the other models still
                    # list. Its folder was recorded above, so it is not offered as
                    # deletable residue (delete-by-folder would refuse it anyway).
                    unchecked += 1
                    continue
                if not is_utf8_text(rel_path.as_posix()):
                    skipped += 1
                    continue
                url = f"{MMD_USER_PATH}/{rel_path.as_posix()}"
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                models.append({
                    "name": model_file.stem,
                    "filename": model_file.name,
                    "url": url,
                    "rel_path": rel_path.as_posix(),
                    "type": model_file.suffix.lower().lstrip('.'),
                    "size": model_file.stat().st_size,
                    "location": "user"
                })

            # Find incomplete top-level model directories lacking PMX/PMD.
            # Usually failed-import residue; list so users can delete.
            for item in mmd_dir.iterdir():
                try:
                    is_dir = item.is_dir()
                except OSError:
                    # Type cannot be checked (e.g. EACCES on Python 3.11): not
                    # offered as residue, and the rest of the list still loads.
                    unchecked += 1
                    continue
                if is_dir and item.name.lower() not in RESERVED_DIRS and item.name not in found_top_dirs:
                    if not is_utf8_text(item.name):
                        skipped += 1
                        continue
                    models.append({
                        "name": item.name,
                        "filename": "",
                        "url": f"{MMD_USER_PATH}/{item.name}",
                        "rel_path": item.name,
                        "type": "",
                        "size": 0,
                        "location": "user",
                        "broken": True
                    })

        # 3. Search Steam Workshop MMD files.
        try:
            workshop_items_result = await get_subscribed_workshop_items()
            if isinstance(workshop_items_result, dict) and workshop_items_result.get('success', False):
                items = workshop_items_result.get('items', [])
                for item in items:
                    installed_folder = item.get('installedFolder')
                    item_id = item.get('publishedFileId')
                    if installed_folder and os.path.exists(installed_folder) and os.path.isdir(installed_folder) and item_id:
                        # Recursively search installation directory for PMX/PMD.
                        installed_path = Path(installed_folder)
                        for model_file in _iter_model_files(installed_path):
                            # Dangling or looping link, or type cannot be checked.
                            is_file = _file_check(model_file)
                            if not is_file:
                                if is_file is None:
                                    unchecked += 1
                                continue
                            try:
                                rel_path = model_file.relative_to(installed_path)
                            except ValueError:
                                continue
                            if not is_utf8_text(rel_path.as_posix()):
                                skipped += 1
                                continue
                            url = f"/workshop/{item_id}/{rel_path.as_posix()}"
                            if url in seen_urls:
                                continue
                            seen_urls.add(url)
                            models.append({
                                "name": model_file.stem,
                                "filename": model_file.name,
                                "url": url,
                                "rel_path": rel_path.as_posix(),
                                "type": model_file.suffix.lower().lstrip('.'),
                                "size": model_file.stat().st_size,
                                "location": "steam_workshop",
                                "source": "steam_workshop",
                                "item_id": str(item_id)
                            })
        except Exception as e:
            logger.error(f"获取创意工坊MMD模型时出错: {e}")

        if skipped:
            logger.warning(f"跳过 {skipped} 个文件名不是合法 UTF-8 的 MMD 模型")
        if unchecked:
            logger.warning(f"跳过 {unchecked} 个无法检查类型的 MMD 模型路径")
        return JSONResponse(content={"success": True, "models": models})
    except Exception as e:
        logger.error(f"获取 MMD 模型列表失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


def _list_animation_dir(directory: Path, suffix: str | None = None) -> list[Path]:
    """Entries of an animation folder, or [] if it cannot be read.

    ``suffix`` keeps only entries with that extension in any letter case, matching
    the upload check (``glob('*.vmd')`` would miss ``Foo.VMD`` on Linux).
    One unreadable folder must not fail the whole list; its path stays out of
    the persistent log.
    """
    try:
        entries = list(directory.iterdir())
        if suffix:
            entries = [entry for entry in entries if entry.suffix.lower() == suffix]
        return entries
    except Exception as dir_error:
        logger.warning(f"处理动画目录失败: {type(dir_error).__name__}")
        print(f"处理动画目录失败 {directory!r}: {type(dir_error).__name__}: {dir_error}")
        return []


def _animation_dir_exists(directory: Path) -> bool:
    """``directory.exists()``, treating a check that raises (e.g. PermissionError) as missing."""
    try:
        return directory.exists()
    except Exception as dir_error:
        logger.warning(f"检查动画目录失败: {type(dir_error).__name__}")
        print(f"检查动画目录失败 {directory!r}: {type(dir_error).__name__}: {dir_error}")
        return False


@router.get('/animations')
def get_mmd_animations():
    """List VMD animation files."""
    try:
        config_mgr = get_config_manager()
        animations = []
        seen_urls = set()
        skipped = 0  # Non-UTF-8 names cannot be serialized or requested.
        failed = 0

        # 1. Project static/mmd/animation/.
        project_root = config_mgr.project_root
        static_anim_dir = project_root / "static" / "mmd" / "animation"
        if _animation_dir_exists(static_anim_dir):
            for anim_file in _list_animation_dir(static_anim_dir, '.vmd'):
                try:
                    if not anim_file.is_file():
                        continue
                    if not is_utf8_text(anim_file.name):
                        skipped += 1
                        continue
                    url = f"/static/mmd/animation/{anim_file.name}"
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    animations.append({
                        "name": anim_file.stem,
                        "filename": anim_file.name,
                        "url": url,
                        "type": "vmd",
                        "size": anim_file.stat().st_size
                    })
                except Exception as file_error:
                    failed += 1
                    print(f"处理动画文件失败 {anim_file!r}: {type(file_error).__name__}: {file_error}")

        # 2. User mmd/animation/.
        mmd_dir = _ensure_mmd_directory(config_mgr)
        if mmd_dir:
            user_anim_dir = mmd_dir / "animation"
            if _animation_dir_exists(user_anim_dir):
                for anim_file in _list_animation_dir(user_anim_dir, '.vmd'):
                    try:
                        if not anim_file.is_file():
                            continue
                        if not is_utf8_text(anim_file.name):
                            skipped += 1
                            continue
                        url = f"{MMD_USER_PATH}/animation/{anim_file.name}"
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)
                        animations.append({
                            "name": anim_file.stem,
                            "filename": anim_file.name,
                            "url": url,
                            "type": "vmd",
                            "size": anim_file.stat().st_size
                        })
                    except Exception as file_error:
                        failed += 1
                        print(f"处理动画文件失败 {anim_file!r}: {type(file_error).__name__}: {file_error}")

        if skipped:
            logger.warning(f"跳过 {skipped} 个文件名不是合法 UTF-8 的 VMD 动画")
        if failed:
            logger.warning(f"跳过 {failed} 个处理失败的 VMD 动画文件")
        return JSONResponse(content={"success": True, "animations": animations})
    except Exception as e:
        # Exception text and tracebacks may carry absolute paths: details go to print only.
        logger.error(f"获取 VMD 动画列表失败: {type(e).__name__}")
        print(f"获取 VMD 动画列表失败: {traceback.format_exc()}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/config')
def get_mmd_config():
    """Get the MMD path configuration."""
    return JSONResponse(content={
        "success": True,
        "paths": {
            "user_mmd": MMD_USER_PATH,
            "static_mmd": MMD_STATIC_PATH
        }
    })


@router.get('/emotion_mapping')
def get_emotion_mapping(model: str = ""):
    """Get the emotion mapping configuration of an MMD model."""
    try:
        config_mgr = get_config_manager()
        mmd_dir = _ensure_mmd_directory(config_mgr)
        if not mmd_dir:
            return JSONResponse(content={"success": True, "mapping": {}})

        config_path = mmd_dir / "emotion_config"
        config_path.mkdir(parents=True, exist_ok=True)

        if model:
            # Path safety: reject separators to prevent traversal.
            if '/' in model or '\\' in model:
                return JSONResponse(status_code=400, content={"success": False, "error": "无效的模型名称"})
            safe_name = model.replace('..', '_')
            config_file = config_path / f"{safe_name}.json"

            # Verify path cannot traverse.
            if not config_file.resolve().is_relative_to(config_path.resolve()):
                return JSONResponse(status_code=400, content={"success": False, "error": "无效的模型名称"})

            if config_file.exists():
                with open(config_file, 'r', encoding='utf-8') as f:
                    mapping = json.load(f)
                return JSONResponse(content={"success": True, "mapping": mapping})

        return JSONResponse(content={"success": True, "mapping": {}})
    except Exception as e:
        logger.error(f"获取 MMD 情感映射失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post('/emotion_mapping')
async def update_emotion_mapping(request: Request):
    """Update the emotion mapping configuration of an MMD model."""
    try:
        data = await request.json()
        model_name = data.get('model', '')
        mapping = data.get('mapping', {})

        if not model_name:
            return JSONResponse(status_code=400, content={"success": False, "error": "缺少模型名称"})
        if not isinstance(mapping, dict):
            return JSONResponse(status_code=400, content={"success": False, "error": "映射配置格式无效"})

        config_mgr = get_config_manager()
        mmd_dir = _ensure_mmd_directory(config_mgr)
        if not mmd_dir:
            return JSONResponse(status_code=500, content={"success": False, "error": "MMD 目录创建失败"})

        config_path = mmd_dir / "emotion_config"
        config_path.mkdir(parents=True, exist_ok=True)

        # Path safety: reject separators to prevent traversal.
        if '/' in model_name or '\\' in model_name:
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的模型名称"})
        safe_name = model_name.replace('..', '_')
        config_file = config_path / f"{safe_name}.json"

        if not config_file.resolve().is_relative_to(config_path.resolve()):
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的模型名称"})

        await atomic_write_json_async(config_file, mapping)

        logger.info(f"更新 MMD 情感映射: {safe_name}")
        return JSONResponse(content={"success": True, "message": "情感映射已更新"})
    except Exception as e:
        logger.error(f"更新 MMD 情感映射失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


def _count_and_rmtree(path: Path) -> int:
    # Counts checkable files only: an entry whose type cannot be checked is not
    # counted (rmtree still removes it) and does not stop the delete.
    count = sum(1 for f in path.rglob('*') if _file_check(f))
    shutil.rmtree(path)
    return count


def _tree_has_model_named(root: Path, key: str, skip_top_dirs: frozenset[str] = frozenset()) -> bool:
    """Whether root holds a model file whose normalised stem matches key.

    Uses os.walk instead of Path.rglob: on Python 3.11 rglob silently skips
    directories it cannot list, which would hide a same-named model. Raises
    the first listing error when the scan was incomplete and no match was found.
    """
    errors: list[OSError] = []
    wanted = key.casefold()
    for dirpath, dirnames, filenames in os.walk(root, onerror=errors.append):
        if skip_top_dirs and Path(dirpath) == root:
            dirnames[:] = [name for name in dirnames if name.lower() not in skip_top_dirs]
        for name in filenames:
            candidate = Path(name)
            if (candidate.suffix.lower() in ALLOWED_MODEL_EXTENSIONS
                    and candidate.stem.replace("..", "_").casefold() == wanted):
                return True
    if errors:
        raise errors[0]
    return False


def _remove_unused_emotion_mapping(config_mgr, mmd_dir: Path, model_name: str) -> None:
    key = model_name.replace("..", "_")
    mapping_dir = mmd_dir / "emotion_config"
    mapping = mapping_dir / f"{key}.json"
    try:
        if not mapping.is_file() or not mapping.resolve().is_relative_to(mapping_dir.resolve()):
            return
        roots = [mmd_dir, config_mgr.project_root / "static" / "mmd"]
        for folder in (config_mgr.get_steam_workshop_path(), config_mgr.get_workshop_path()):
            if folder:
                roots.append(Path(folder))
        for root in dict.fromkeys(roots):
            if not root.is_dir():
                continue
            skip = frozenset(RESERVED_DIRS) if root == mmd_dir else frozenset()
            if _tree_has_model_named(root, key, skip):
                return
        mapping.unlink(missing_ok=True)
        logger.info("删除 MMD 情感映射配置: %s", mapping.name)
    except (OSError, RuntimeError) as exc:
        # Best-effort cleanup: an incomplete scan (or a symlink loop in resolve) keeps the mapping.
        logger.warning("清理 MMD 情感映射配置失败，保留原文件 %s: %s", mapping.name, exc)


def _delete_subdir_model_sync(safe_path: Path, top_subdir: Path) -> int:
    """Delete an MMD model in a subdirectory, removing the directory only if no sibling model remains."""
    resolved_target = safe_path.resolve()
    resolved_top = top_subdir.resolve()
    has_sibling_model = any(
        candidate.is_file()
        and candidate.suffix.lower() in ALLOWED_MODEL_EXTENSIONS
        and candidate.resolve() != resolved_target
        for candidate in top_subdir.rglob("*")
    )
    if not has_sibling_model:
        return _count_and_rmtree(top_subdir)

    safe_path.unlink()
    cur = safe_path.parent.resolve()
    while cur != resolved_top.parent and cur.is_relative_to(resolved_top):
        try:
            if any(cur.iterdir()):
                break
            cur.rmdir()
        except OSError:
            break
        cur = cur.parent
    return 1


@router.delete('/model')
async def delete_mmd_model(request: Request):
    """Delete an MMD model file (and the associated resources in its directory)."""
    try:
        data = await request.json()
        model_url = data.get('url', '').strip()

        if not model_url:
            return JSONResponse(status_code=400, content={"success": False, "error": "缺少模型 URL"})

        config_mgr = get_config_manager()
        mmd_dir = _ensure_mmd_directory(config_mgr)
        if not mmd_dir:
            return JSONResponse(status_code=500, content={"success": False, "error": "MMD 目录不可用"})

        # Extract relative path from URL.
        if model_url.startswith(MMD_USER_PATH + '/'):
            rel_path = model_url[len(MMD_USER_PATH) + 1:]
        elif model_url.startswith(MMD_STATIC_PATH + '/'):
            return JSONResponse(status_code=400, content={"success": False, "error": "不能删除项目内置模型"})
        else:
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的模型路径"})

        # Validate safe path.
        safe_path, error = safe_mmd_path(mmd_dir, rel_path)
        if not safe_path:
            # safe_mmd_path rejects directories, but incomplete model directories must be deletable.
            # Check direct child of mmd_dir and presence of model files.
            candidate = (mmd_dir / rel_path).resolve()
            try:
                candidate_is_dir = candidate.is_dir()
            except OSError:
                # Type cannot be checked: refuse with the path error below instead of 500.
                candidate_is_dir = False
            if candidate_is_dir and candidate.parent.resolve() == mmd_dir.resolve() and candidate.is_relative_to(mmd_dir.resolve()) and candidate.name.lower() not in RESERVED_DIRS:
                has_model = any(_iter_model_files(candidate))
                if has_model:
                    # Valid models present: delete by model URL, never directory path.
                    return JSONResponse(status_code=400, content={
                        "success": False, "error": "该目录包含模型文件，请通过模型 URL 删除"
                    })
                # Incomplete directory without model files: permit deletion.
                deleted_files = await asyncio.to_thread(_count_and_rmtree, candidate)
                logger.info(f"删除残缺 MMD 模型目录: {candidate.name}")
                return JSONResponse(content={
                    "success": True,
                    "message": f"已删除残缺模型目录 {rel_path}",
                    "deleted_files": deleted_files
                })
            return JSONResponse(status_code=400, content={"success": False, "error": error})

        if not safe_path.exists():
            return JSONResponse(status_code=404, content={"success": False, "error": "模型文件不存在"})

        model_parent = safe_path.parent
        model_name = safe_path.stem
        deleted_files = 0

        if model_parent.resolve() != mmd_dir.resolve():
            # Model in subdirectory: delete entire directory, including textures/associated resources.
            # First verify requested path actually identifies model file.
            if safe_path.suffix.lower() not in ALLOWED_MODEL_EXTENSIONS:
                return JSONResponse(status_code=400, content={"success": False, "error": "只能删除模型文件"})
            # Find immediate child directory of mmd_dir.
            rel_to_mmd = model_parent.resolve().relative_to(mmd_dir.resolve())
            top_subdir = mmd_dir / rel_to_mmd.parts[0]
            if not top_subdir.resolve().is_relative_to(mmd_dir.resolve()):
                return JSONResponse(status_code=400, content={"success": False, "error": "路径越界"})
            if top_subdir.name.lower() in RESERVED_DIRS:
                return JSONResponse(status_code=400, content={"success": False, "error": "不能删除保留目录"})
            deleted_files = await asyncio.to_thread(_delete_subdir_model_sync, safe_path, top_subdir)
            logger.info(f"删除 MMD 模型: {safe_path.name} ({deleted_files} 个文件)")
        else:
            # Top-level model: delete model file only.
            if safe_path.suffix.lower() not in ALLOWED_MODEL_EXTENSIONS:
                return JSONResponse(status_code=400, content={"success": False, "error": "只能删除模型文件"})
            safe_path.unlink()
            deleted_files = 1
            logger.info(f"删除 MMD 模型文件: {safe_path.name}")

        await asyncio.to_thread(_remove_unused_emotion_mapping, config_mgr, mmd_dir, model_name)

        return JSONResponse(content={
            "success": True,
            "message": f"已删除模型 {model_name}",
            "deleted_files": deleted_files
        })
    except Exception as e:
        logger.error(f"删除 MMD 模型失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/animations/list')
async def list_mmd_animations_for_delete(request: Request):
    """List VMD animations eligible for deletion."""
    try:
        config_mgr = get_config_manager()
        mmd_dir = _ensure_mmd_directory(config_mgr)
        if not mmd_dir:
            return JSONResponse(content={"success": True, "animations": []})

        user_anim_dir = mmd_dir / "animation"
        animations = []
        skipped = 0  # Non-UTF-8 names cannot be serialized or requested.
        failed = 0

        if _animation_dir_exists(user_anim_dir):
            for vmd_file in _list_animation_dir(user_anim_dir):
                try:
                    if vmd_file.suffix.lower() != '.vmd':
                        continue
                    if not vmd_file.is_file():
                        continue
                    if not is_utf8_text(vmd_file.name):
                        skipped += 1
                        continue
                    rel_path = vmd_file.relative_to(mmd_dir)
                    animations.append({
                        "name": vmd_file.stem,
                        "filename": vmd_file.name,
                        "url": f"{MMD_USER_PATH}/{rel_path.as_posix()}",
                        "path": rel_path.as_posix()
                    })
                except Exception as file_error:
                    failed += 1
                    print(f"处理动画文件失败 {vmd_file!r}: {type(file_error).__name__}: {file_error}")

        if skipped:
            logger.warning(f"跳过 {skipped} 个文件名不是合法 UTF-8 的 VMD 动画")
        if failed:
            logger.warning(f"跳过 {failed} 个处理失败的 VMD 动画文件")
        return JSONResponse(content={"success": True, "animations": animations})
    except Exception as e:
        # Exception text and tracebacks may carry absolute paths: details go to print only.
        logger.error(f"获取 VMD 动画列表失败: {type(e).__name__}")
        print(f"获取 VMD 动画列表失败: {traceback.format_exc()}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.delete('/animation')
async def delete_mmd_animation(request: Request):
    """Delete a VMD animation file."""
    try:
        data = await request.json()
        anim_url = data.get('url', '').strip()

        if not anim_url:
            return JSONResponse(status_code=400, content={"success": False, "error": "缺少动画 URL"})

        config_mgr = get_config_manager()
        mmd_dir = _ensure_mmd_directory(config_mgr)
        if not mmd_dir:
            return JSONResponse(status_code=500, content={"success": False, "error": "MMD 目录不可用"})

        # Extract relative path from URL.
        if anim_url.startswith(MMD_USER_PATH + '/'):
            rel_path = anim_url[len(MMD_USER_PATH) + 1:]
        else:
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的动画 URL"})

        # Validate safe path.
        safe_path, error = safe_mmd_path(mmd_dir, rel_path)
        if not safe_path:
            return JSONResponse(status_code=400, content={"success": False, "error": error})

        # Verify VMD file.
        if safe_path.suffix.lower() != '.vmd':
            return JSONResponse(status_code=400, content={"success": False, "error": "只能删除 VMD 动画文件"})

        # Verify inside animation directory.
        animation_dir = mmd_dir / "animation"
        if not safe_path.resolve().is_relative_to(animation_dir.resolve()):
            return JSONResponse(status_code=400, content={"success": False, "error": "动画文件不在有效目录中"})

        if not safe_path.exists():
            return JSONResponse(status_code=404, content={"success": False, "error": "动画文件不存在"})

        # Delete file.
        safe_path.unlink()
        logger.info(f"删除 VMD 动画文件: {safe_path.name}")

        return JSONResponse(content={
            "success": True,
            "message": f"已删除动画 {safe_path.stem}"
        })
    except Exception as e:
        logger.error(f"删除 VMD 动画失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})