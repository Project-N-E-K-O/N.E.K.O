# -*- coding: utf-8 -*-
"""
MMD Router

Handles MMD model-related endpoints including:
- MMD model listing (PMX/PMD)
- MMD model upload
- VMD animation listing and upload
- MMD emotion mapping configuration
"""

import asyncio
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import charset_normalizer
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse

from .shared_state import get_config_manager
from .workshop_router import get_subscribed_workshop_items
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger

router = APIRouter(prefix="/api/model/mmd", tags=["mmd"])
logger = get_module_logger(__name__, "Main")

# MMD 模型路径常量
MMD_USER_PATH = "/user_mmd"
MMD_STATIC_PATH = "/static/mmd"

# 文件上传常量
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB (MMD 模型含纹理可能较大)
MAX_ZIP_UNCOMPRESSED = 2 * 1024 * 1024 * 1024  # 2GB 解压上限，防止 zip bomb
MAX_ZIP_ENTRIES = 10000  # ZIP 内最大文件数
CHUNK_SIZE = 1024 * 1024  # 1MB chunks

# 允许的文件扩展名
ALLOWED_MODEL_EXTENSIONS = {'.pmx', '.pmd'}
ALLOWED_ANIMATION_EXTENSIONS = {'.vmd'}
RESERVED_DIRS = {'animation', 'emotion_config'}


def safe_mmd_path(mmd_dir: Path, filename: str, subdir: str | None = None) -> tuple[Path | None, str]:
    """安全地构造和验证 MMD 目录内的路径，防止路径穿越攻击。"""
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
    """确保 MMD 用户目录存在，返回目录路径。"""
    try:
        mmd_dir = config_mgr.mmd_dir
        mmd_dir.mkdir(parents=True, exist_ok=True)
        animation_dir = config_mgr.mmd_animation_dir
        animation_dir.mkdir(parents=True, exist_ok=True)
        return mmd_dir
    except Exception as e:
        logger.error(f"创建 MMD 目录失败: {e}")
        return None


# ═══════════════════ ZIP 编码兼容处理 ═══════════════════
#
# MMD 模型多源自日本，ZIP 文件名常使用 Shift-JIS (CP932) 编码。
# Python 的 zipfile 模块对未设置 UTF-8 标志位的条目默认用 CP437 解码，
# 导致日文/中文文件名变为乱码。
#
# 处理策略：
#   1. 尝试拼接文件名字节流，使用 charset_normalizer 全局推测编码
#   2. 如果推测成功，优先使用全局编码；若失败，降级进行单文件推断
#   3. 对解码后的文件名进行清理（保留中文/日文），避免操作系统非法字符

def _detect_zip_encoding(zf: zipfile.ZipFile) -> str | None:
    """检测 ZIP 压缩包中非 UTF-8 条目的实际文件名编码（严格限制在中日韩范围）。"""
    non_utf8_infos = [info for info in zf.infolist() if not (info.flag_bits & 0x800)]
    if not non_utf8_infos:
        return None

    # 1. 尝试拼接前 100 个非 ASCII 文件名进行全局探测
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

    # 2. 优先防线：强推 UTF-8
    try:
        raw_bytes_blob.decode('utf-8')
        return 'utf-8'
    except UnicodeDecodeError:
        pass

    # 3. 核心修改：给探测器戴上“紧箍咒”，只允许中日韩常见编码
    ALLOWED_ENCODINGS = {'cp932', 'shift_jis', 'gbk', 'gb18030', 'gb2312', 'big5', 'euc-kr'}
    
    results = charset_normalizer.from_bytes(raw_bytes_blob)
    for result in results:
        if result.encoding and result.encoding.lower() in ALLOWED_ENCODINGS:
            return result.encoding.lower()

    # 4. 降级安全网：调整顺序为日文优先，并加入严格的“往返校验”
    for fallback_enc in ('cp932', 'gbk', 'big5', 'euc-kr'):
        try:
            test_decoded = raw_bytes_blob.decode(fallback_enc)
            # 往返校验：解码后再编码回去，看是否与原始字节一致，防止强行解码成乱码
            if test_decoded.encode(fallback_enc) == raw_bytes_blob:
                return fallback_enc
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue

    return None


def _sanitize_filename(filename: str) -> str:
    """清理文件名，将反斜杠转为正斜杠保留目录结构，并移除非法字符。"""
    # 接受 CodeRabbit 建议：先将 Windows 风格的反斜杠统一转为 Web/Linux 标准的正斜杠
    normalized = filename.replace('\\', '/')
    # 按目录层级拆分，清理每一层，再拼装回去
    return '/'.join(
        re.sub(r'[<>:"|?*]', '_', part)
        for part in normalized.split('/')
    )


def _build_zip_name_map(zf: zipfile.ZipFile) -> dict[str, str]:
    """为 ZIP 中所有条目构建「原始名 → 正确解码并清理后名称」的映射表。"""
    global_encoding = _detect_zip_encoding(zf)
    name_map = {}

    # MMD 常用的兜底编码列表，日文优先
    FALLBACK_ENCODINGS = ['cp932', 'gbk', 'big5', 'euc-kr']

    for info in zf.infolist():
        if not (info.flag_bits & 0x800):  # 非标记为 UTF-8 的条目
            try:
                raw_bytes = info.filename.encode('cp437')
                decoded = None
                
                # 尝试应用全局编码
                if global_encoding:
                    try:
                        test_decoded = raw_bytes.decode(global_encoding)
                        if test_decoded.encode(global_encoding) == raw_bytes:
                            decoded = test_decoded
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        pass
                
                # 如果全局编码在这个具体文件上失败，暴力尝试备选列表，加入往返校验
                if not decoded:
                    for enc in FALLBACK_ENCODINGS:
                        try:
                            test_decoded = raw_bytes.decode(enc)
                            if test_decoded.encode(enc) == raw_bytes:
                                decoded = test_decoded
                                break
                        except (UnicodeDecodeError, UnicodeEncodeError):
                            continue
                            
                # 终极兜底：强行 utf-8 替换错误字符，至少保证程序不崩溃
                if not decoded:
                    decoded = raw_bytes.decode('utf-8', errors='replace')
                
                name_map[info.filename] = _sanitize_filename(decoded)
                
            except (UnicodeEncodeError, UnicodeDecodeError):
                name_map[info.filename] = _sanitize_filename(info.filename)
        else:
            # 已明确标记为 UTF-8 的条目，仅做清理
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
    """使用修正后的文件名解压 ZIP 内容。

    逐条目提取，将文件写入 target_dir 下以 name_map 修正后的路径，
    同时做路径越界安全检查。
    """
    resolved_target = target_dir.resolve()

    for info in zf.infolist():
        decoded_name = name_map.get(info.filename, info.filename)
        target_path = (target_dir / decoded_name).resolve()

        # 安全：确保路径不会逃逸到目标目录之外
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
    """处理 MMD 文件上传的通用流式逻辑。"""
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

        # 确保父目录存在
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


# ═══════════════════ 路由端点 ═══════════════════


@router.post('/upload')
async def upload_mmd_model(file: UploadFile = File(...)):
    """上传 MMD 模型文件（PMX/PMD）"""
    config_mgr = get_config_manager()
    mmd_dir = _ensure_mmd_directory(config_mgr)
    if not mmd_dir:
        return JSONResponse(status_code=500, content={"success": False, "error": "MMD 目录创建失败"})

    return await _handle_mmd_file_upload(file, mmd_dir, ALLOWED_MODEL_EXTENSIONS, 'MMD 模型')


@router.post('/upload_animation')
async def upload_mmd_animation(file: UploadFile = File(...)):
    """上传 VMD 动画文件"""
    config_mgr = get_config_manager()
    mmd_dir = _ensure_mmd_directory(config_mgr)
    if not mmd_dir:
        return JSONResponse(status_code=500, content={"success": False, "error": "MMD 目录创建失败"})

    return await _handle_mmd_file_upload(file, mmd_dir, ALLOWED_ANIMATION_EXTENSIONS, 'VMD 动画', 'animation')


@router.post('/upload_zip')
async def upload_mmd_zip(file: UploadFile = File(...)):
    """上传 MMD 模型 ZIP 包（含 PMX/PMD + 纹理），自动解压到子目录。"""
    config_mgr = get_config_manager()
    mmd_dir = _ensure_mmd_directory(config_mgr)
    if not mmd_dir:
        return JSONResponse(status_code=500, content={"success": False, "error": "MMD 目录创建失败"})

    if not file or not file.filename:
        return JSONResponse(status_code=400, content={"success": False, "error": "没有上传文件"})

    if not file.filename.lower().endswith('.zip'):
        return JSONResponse(status_code=400, content={"success": False, "error": "请上传 .zip 文件"})

    # 先将上传内容写到临时文件，再解压（避免内存爆炸）
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

        # 验证 ZIP 完整性并查找 PMX/PMD
        if not zipfile.is_zipfile(str(tmp_path)):
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的 ZIP 文件"})

        with zipfile.ZipFile(str(tmp_path), 'r') as zf:
            # 构建编码修正映射（处理日文 Shift-JIS / 中文 GBK 等非 UTF-8 编码）
            name_map = _build_zip_name_map(zf)
            decoded_names = list(name_map.values())

            # 安全检查：zip bomb 防护
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

            # 安全检查：不能有绝对路径或 ..
            for name in decoded_names:
                if name.startswith('/') or '..' in name:
                    return JSONResponse(status_code=400, content={
                        "success": False, "error": "ZIP 包含不安全的路径"
                    })

            # 查找 PMX/PMD（使用解码后的文件名）
            model_entries = [
                n for n in decoded_names
                if Path(n).suffix.lower() in ALLOWED_MODEL_EXTENSIONS and not n.endswith('/')
            ]
            if not model_entries:
                return JSONResponse(status_code=400, content={
                    "success": False, "error": "ZIP 中未找到 .pmx 或 .pmd 模型文件"
                })

            # 选第一个模型文件，用其文件名做子目录名
            model_entry = model_entries[0]
            model_stem = Path(model_entry).stem

            # 检测 ZIP 最外层是否已有统一目录（使用解码后的名称）
            all_decoded_files = [n for n in decoded_names if not n.endswith('/')]
            top_level_items = {n.split('/')[0] for n in all_decoded_files}
            if len(top_level_items) == 1:
                zip_root_dir = top_level_items.pop()
                # 确认它确实是目录而非单个文件：
                # 存在目录条目（以 '/' 结尾）或存在以它为前缀的子路径
                is_dir = any(
                    n == f"{zip_root_dir}/" or n.startswith(f"{zip_root_dir}/")
                    for n in decoded_names
                )
                if is_dir:
                    # ZIP 本身已经是 "model_name/..." 结构
                    extract_dir_name = zip_root_dir
                else:
                    # 单文件 ZIP，用模型名创建子目录
                    extract_dir_name = model_stem
            else:
                # ZIP 是扁平结构，用模型名创建子目录
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
                # 检查是否包含有效模型文件，若无则为残留空目录，自动清理
                has_valid_model = any(
                    any(target_dir.rglob(f'*{ext}'))
                    for ext in ALLOWED_MODEL_EXTENSIONS
                )
                if has_valid_model:
                    return JSONResponse(status_code=400, content={
                        "success": False,
                        "error": f"目录 {extract_dir_name} 已存在，请先删除旧模型"
                    })
                else:
                    logger.info(f"清理残留的无效模型目录: {target_dir}")
                    await asyncio.to_thread(shutil.rmtree, target_dir, ignore_errors=True)

            # 使用编码修正后的文件名解压
            if all(
                n.startswith(extract_dir_name + '/') or n == extract_dir_name
                for n in decoded_names
            ):
                # ZIP 已含同名目录结构，直接解压到 mmd_dir
                _extract_zip_with_encoding(zf, mmd_dir, name_map)
            else:
                # 解压到 target_dir
                target_dir.mkdir(parents=True, exist_ok=True)
                _extract_zip_with_encoding(zf, target_dir, name_map)

        # 找到解压后的 PMX 路径
        pmx_candidates = []
        for ext in ALLOWED_MODEL_EXTENSIONS:
            pmx_candidates.extend(target_dir.rglob(f'*{ext}'))
        if not pmx_candidates:
            await asyncio.to_thread(shutil.rmtree, target_dir, ignore_errors=True)
            return JSONResponse(status_code=500, content={
                "success": False, "error": "解压后未找到模型文件"
            })

        pmx_file = pmx_candidates[0]
        rel_path = pmx_file.relative_to(mmd_dir)
        model_url = f"{MMD_USER_PATH}/{rel_path.as_posix()}"
        file_count = sum(1 for _ in target_dir.rglob('*') if _.is_file())

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
    """获取 MMD 模型列表（PMX/PMD），包括子目录"""
    try:
        config_mgr = get_config_manager()
        models = []
        seen_urls = set()

        # 1. 项目目录下的 static/mmd/（递归搜索）
        project_root = config_mgr.project_root
        static_mmd_dir = project_root / "static" / "mmd"
        if static_mmd_dir.exists():
            for ext in ALLOWED_MODEL_EXTENSIONS:
                for model_file in static_mmd_dir.rglob(f'*{ext}'):
                    rel_path = model_file.relative_to(static_mmd_dir)
                    url = f"/static/mmd/{rel_path.as_posix()}"
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    models.append({
                        "name": model_file.stem,
                        "filename": model_file.name,
                        "url": url,
                        "rel_path": rel_path.as_posix(),
                        "type": model_file.suffix.lstrip('.'),
                        "size": model_file.stat().st_size,
                        "location": "project"
                    })

        # 2. 用户目录下的 mmd/（递归搜索，跳过保留目录）
        mmd_dir = _ensure_mmd_directory(config_mgr)
        if mmd_dir and mmd_dir.exists():
            found_top_dirs = set()  # 记录包含有效模型的顶层子目录
            for ext in ALLOWED_MODEL_EXTENSIONS:
                for model_file in mmd_dir.rglob(f'*{ext}'):
                    try:
                        rel_path = model_file.relative_to(mmd_dir)
                        if rel_path.parts and rel_path.parts[0] in RESERVED_DIRS:
                            continue
                    except (ValueError, IndexError):
                        continue
                    url = f"{MMD_USER_PATH}/{rel_path.as_posix()}"
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    # 记录包含模型的顶层子目录
                    if rel_path.parts:
                        found_top_dirs.add(rel_path.parts[0])
                    models.append({
                        "name": model_file.stem,
                        "filename": model_file.name,
                        "url": url,
                        "rel_path": rel_path.as_posix(),
                        "type": model_file.suffix.lstrip('.'),
                        "size": model_file.stat().st_size,
                        "location": "user"
                    })

            # 查找残缺模型目录（有目录但无 PMX/PMD 的顶层子目录）
            # 这些通常是导入失败后留下的残留，需要显示在列表中以便用户删除
            for item in mmd_dir.iterdir():
                if item.is_dir() and item.name not in RESERVED_DIRS and item.name not in found_top_dirs:
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

        # 3. 搜索Steam创意工坊中的MMD文件
        try:
            workshop_items_result = await get_subscribed_workshop_items()
            if isinstance(workshop_items_result, dict) and workshop_items_result.get('success', False):
                items = workshop_items_result.get('items', [])
                for item in items:
                    installed_folder = item.get('installedFolder')
                    item_id = item.get('publishedFileId')
                    if installed_folder and os.path.exists(installed_folder) and os.path.isdir(installed_folder) and item_id:
                        # 递归搜索安装目录下的PMX/PMD文件
                        installed_path = Path(installed_folder)
                        for ext in ALLOWED_MODEL_EXTENSIONS:
                            for model_file in installed_path.rglob(f'*{ext}'):
                                try:
                                    rel_path = model_file.relative_to(installed_path)
                                except ValueError:
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
                                    "type": model_file.suffix.lstrip('.'),
                                    "size": model_file.stat().st_size,
                                    "location": "steam_workshop",
                                    "source": "steam_workshop",
                                    "item_id": str(item_id)
                                })
        except Exception as e:
            logger.error(f"获取创意工坊MMD模型时出错: {e}")

        return JSONResponse(content={"success": True, "models": models})
    except Exception as e:
        logger.error(f"获取 MMD 模型列表失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/animations')
def get_mmd_animations():
    """获取 VMD 动画文件列表"""
    try:
        config_mgr = get_config_manager()
        animations = []
        seen_urls = set()

        # 1. 项目目录下的 static/mmd/animation/
        project_root = config_mgr.project_root
        static_anim_dir = project_root / "static" / "mmd" / "animation"
        if static_anim_dir.exists():
            for anim_file in static_anim_dir.glob('*.vmd'):
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

        # 2. 用户目录下的 mmd/animation/
        mmd_dir = _ensure_mmd_directory(config_mgr)
        if mmd_dir:
            user_anim_dir = mmd_dir / "animation"
            if user_anim_dir.exists():
                for anim_file in user_anim_dir.glob('*.vmd'):
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

        return JSONResponse(content={"success": True, "animations": animations})
    except Exception as e:
        logger.error(f"获取 VMD 动画列表失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/config')
def get_mmd_config():
    """获取 MMD 路径配置"""
    return JSONResponse(content={
        "success": True,
        "paths": {
            "user_mmd": MMD_USER_PATH,
            "static_mmd": MMD_STATIC_PATH
        }
    })


@router.get('/emotion_mapping')
def get_emotion_mapping(model: str = ""):
    """获取 MMD 模型的情感映射配置"""
    try:
        config_mgr = get_config_manager()
        mmd_dir = _ensure_mmd_directory(config_mgr)
        if not mmd_dir:
            return JSONResponse(content={"success": True, "mapping": {}})

        config_path = mmd_dir / "emotion_config"
        config_path.mkdir(parents=True, exist_ok=True)

        if model:
            # 路径安全检查：拒绝含路径分隔符的输入以防止目录穿越
            if '/' in model or '\\' in model:
                return JSONResponse(status_code=400, content={"success": False, "error": "无效的模型名称"})
            safe_name = model.replace('..', '_')
            config_file = config_path / f"{safe_name}.json"

            # 验证路径不会穿越
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
    """更新 MMD 模型的情感映射配置"""
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

        # 路径安全检查：拒绝含路径分隔符的输入以防止目录穿越
        if '/' in model_name or '\\' in model_name:
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的模型名称"})
        safe_name = model_name.replace('..', '_')
        config_file = config_path / f"{safe_name}.json"

        if not config_file.resolve().is_relative_to(config_path.resolve()):
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的模型名称"})

        atomic_write_json(config_file, mapping)

        logger.info(f"更新 MMD 情感映射: {safe_name}")
        return JSONResponse(content={"success": True, "message": "情感映射已更新"})
    except Exception as e:
        logger.error(f"更新 MMD 情感映射失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.delete('/model')
async def delete_mmd_model(request: Request):
    """删除 MMD 模型文件（及其所在目录中的关联资源）"""
    try:
        data = await request.json()
        model_url = data.get('url', '').strip()

        if not model_url:
            return JSONResponse(status_code=400, content={"success": False, "error": "缺少模型 URL"})

        config_mgr = get_config_manager()
        mmd_dir = _ensure_mmd_directory(config_mgr)
        if not mmd_dir:
            return JSONResponse(status_code=500, content={"success": False, "error": "MMD 目录不可用"})

        # 从 URL 提取相对路径
        if model_url.startswith(MMD_USER_PATH + '/'):
            rel_path = model_url[len(MMD_USER_PATH) + 1:]
        elif model_url.startswith(MMD_STATIC_PATH + '/'):
            return JSONResponse(status_code=400, content={"success": False, "error": "不能删除项目内置模型"})
        else:
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的模型路径"})

        # 安全路径验证
        safe_path, error = safe_mmd_path(mmd_dir, rel_path)
        if not safe_path:
            # safe_mmd_path 拒绝目录路径，但对于残缺模型目录需要允许删除
            # 检查是否是 mmd_dir 的直接子目录且包含模型文件
            candidate = (mmd_dir / rel_path).resolve()
            if candidate.is_dir() and candidate.parent.resolve() == mmd_dir.resolve() and candidate.is_relative_to(mmd_dir.resolve()) and candidate.name.lower() not in RESERVED_DIRS:
                has_model = any(
                    any(candidate.rglob(f'*{ext}'))
                    for ext in ALLOWED_MODEL_EXTENSIONS
                )
                if has_model:
                    # 目录含有效模型文件，应通过模型 URL 而非目录路径删除
                    return JSONResponse(status_code=400, content={
                        "success": False, "error": "该目录包含模型文件，请通过模型 URL 删除"
                    })
                # 残缺目录（无模型文件）：允许删除
                deleted_files = sum(1 for f in candidate.rglob('*') if f.is_file())
                await asyncio.to_thread(shutil.rmtree, candidate)
                logger.info(f"删除残缺 MMD 模型目录: {candidate}")
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
            # 模型在子目录中：删除整个子目录（包含纹理等关联资源）
            # 先验证请求的路径确实指向模型文件
            if safe_path.suffix.lower() not in ALLOWED_MODEL_EXTENSIONS:
                return JSONResponse(status_code=400, content={"success": False, "error": "只能删除模型文件"})
            # 找到 mmd_dir 的直接子目录
            rel_to_mmd = model_parent.resolve().relative_to(mmd_dir.resolve())
            top_subdir = mmd_dir / rel_to_mmd.parts[0]
            if not top_subdir.resolve().is_relative_to(mmd_dir.resolve()):
                return JSONResponse(status_code=400, content={"success": False, "error": "路径越界"})
            if top_subdir.name.lower() in RESERVED_DIRS:
                return JSONResponse(status_code=400, content={"success": False, "error": "不能删除保留目录"})
            for f in top_subdir.rglob('*'):
                if f.is_file():
                    deleted_files += 1
            await asyncio.to_thread(shutil.rmtree, top_subdir)
            logger.info(f"删除 MMD 模型目录: {top_subdir} ({deleted_files} 个文件)")
        else:
            # 模型在顶层：只删除模型文件本身
            if safe_path.suffix.lower() not in ALLOWED_MODEL_EXTENSIONS:
                return JSONResponse(status_code=400, content={"success": False, "error": "只能删除模型文件"})
            safe_path.unlink()
            deleted_files = 1
            logger.info(f"删除 MMD 模型文件: {safe_path}")

        # 同时删除对应的情感映射配置（与 GET/POST 保持一致的规范化）
        safe_model_name = model_name.replace('..', '_')
        emotion_config = mmd_dir / "emotion_config" / f"{safe_model_name}.json"
        if emotion_config.exists() and emotion_config.resolve().is_relative_to((mmd_dir / "emotion_config").resolve()):
            emotion_config.unlink()
            logger.info(f"删除 MMD 情感映射配置: {emotion_config}")

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
    """获取可删除的 VMD 动画列表"""
    try:
        config_mgr = get_config_manager()
        mmd_dir = _ensure_mmd_directory(config_mgr)
        if not mmd_dir:
            return JSONResponse(content={"success": True, "animations": []})

        user_anim_dir = mmd_dir / "animation"
        animations = []

        if user_anim_dir.exists():
            for vmd_file in user_anim_dir.iterdir():
                if vmd_file.is_file() and vmd_file.suffix.lower() == '.vmd':
                    rel_path = vmd_file.relative_to(mmd_dir)
                    animations.append({
                        "name": vmd_file.stem,
                        "filename": vmd_file.name,
                        "url": f"{MMD_USER_PATH}/{rel_path.as_posix()}",
                        "path": rel_path.as_posix()
                    })

        return JSONResponse(content={"success": True, "animations": animations})
    except Exception as e:
        logger.error(f"获取 VMD 动画列表失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.delete('/animation')
async def delete_mmd_animation(request: Request):
    """删除 VMD 动画文件"""
    try:
        data = await request.json()
        anim_url = data.get('url', '').strip()

        if not anim_url:
            return JSONResponse(status_code=400, content={"success": False, "error": "缺少动画 URL"})

        config_mgr = get_config_manager()
        mmd_dir = _ensure_mmd_directory(config_mgr)
        if not mmd_dir:
            return JSONResponse(status_code=500, content={"success": False, "error": "MMD 目录不可用"})

        # 从 URL 提取相对路径
        if anim_url.startswith(MMD_USER_PATH + '/'):
            rel_path = anim_url[len(MMD_USER_PATH) + 1:]
        else:
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的动画 URL"})

        # 安全路径验证
        safe_path, error = safe_mmd_path(mmd_dir, rel_path)
        if not safe_path:
            return JSONResponse(status_code=400, content={"success": False, "error": error})

        # 验证是 VMD 文件
        if safe_path.suffix.lower() != '.vmd':
            return JSONResponse(status_code=400, content={"success": False, "error": "只能删除 VMD 动画文件"})

        # 验证文件在 animation 目录下
        animation_dir = mmd_dir / "animation"
        if not safe_path.resolve().is_relative_to(animation_dir.resolve()):
            return JSONResponse(status_code=400, content={"success": False, "error": "动画文件不在有效目录中"})

        if not safe_path.exists():
            return JSONResponse(status_code=404, content={"success": False, "error": "动画文件不存在"})

        # 删除文件
        safe_path.unlink()
        logger.info(f"删除 VMD 动画文件: {safe_path}")

        return JSONResponse(content={
            "success": True,
            "message": f"已删除动画 {safe_path.stem}"
        })
    except Exception as e:
        logger.error(f"删除 VMD 动画失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})