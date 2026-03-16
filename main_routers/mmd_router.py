# -*- coding: utf-8 -*-
"""
MMD Router

Handles MMD model-related endpoints including:
- MMD model listing (PMX/PMD)
- MMD model upload
- VMD animation listing and upload
- MMD emotion mapping configuration
"""

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse

from .shared_state import get_config_manager
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger

router = APIRouter(prefix="/api/model/mmd", tags=["mmd"])
logger = get_module_logger(__name__, "Main")

# MMD 模型路径常量
MMD_USER_PATH = "/user_mmd"
MMD_STATIC_PATH = "/static/mmd"

# 文件上传常量
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB (MMD 模型含纹理可能较大)
CHUNK_SIZE = 1024 * 1024  # 1MB chunks

# 允许的文件扩展名
ALLOWED_MODEL_EXTENSIONS = {'.pmx', '.pmd'}
ALLOWED_ANIMATION_EXTENSIONS = {'.vmd'}


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
            # 安全检查：不能有绝对路径或 ..
            for name in zf.namelist():
                if name.startswith('/') or '..' in name:
                    return JSONResponse(status_code=400, content={
                        "success": False, "error": "ZIP 包含不安全的路径"
                    })

            # 查找 PMX/PMD
            model_entries = [
                n for n in zf.namelist()
                if Path(n).suffix.lower() in ALLOWED_MODEL_EXTENSIONS and not n.endswith('/')
            ]
            if not model_entries:
                return JSONResponse(status_code=400, content={
                    "success": False, "error": "ZIP 中未找到 .pmx 或 .pmd 模型文件"
                })

            # 选第一个模型文件，用其文件名做子目录名
            model_entry = model_entries[0]
            model_stem = Path(model_entry).stem

            # 检测 ZIP 最外层是否已有统一目录
            all_names = [n for n in zf.namelist() if not n.endswith('/')]
            top_level_items = {n.split('/')[0] for n in all_names}
            if len(top_level_items) == 1:
                # ZIP 本身已经是 "model_name/..." 结构
                zip_root_dir = top_level_items.pop()
                extract_dir_name = zip_root_dir
            else:
                # ZIP 是扁平结构，用模型名创建子目录
                extract_dir_name = model_stem

            target_dir = (mmd_dir / extract_dir_name).resolve()
            if not target_dir.is_relative_to(mmd_dir.resolve()):
                return JSONResponse(status_code=400, content={
                    "success": False, "error": "路径越界"
                })

            if target_dir.exists():
                return JSONResponse(status_code=400, content={
                    "success": False,
                    "error": f"目录 {extract_dir_name} 已存在，请先删除旧模型"
                })

            # 解压
            if len(top_level_items | {extract_dir_name}) and all(
                n.startswith(extract_dir_name + '/') or n == extract_dir_name
                for n in zf.namelist()
            ):
                # ZIP 已含同名目录结构，直接解压到 mmd_dir
                zf.extractall(str(mmd_dir))
            else:
                # 解压到 target_dir
                target_dir.mkdir(parents=True, exist_ok=True)
                zf.extractall(str(target_dir))

        # 找到解压后的 PMX 路径
        pmx_candidates = []
        for ext in ALLOWED_MODEL_EXTENSIONS:
            pmx_candidates.extend(target_dir.rglob(f'*{ext}'))
        if not pmx_candidates:
            shutil.rmtree(target_dir, ignore_errors=True)
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
        return JSONResponse(status_code=400, content={"success": False, "error": "ZIP 文件损坏"})
    except Exception as e:
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
def get_mmd_models():
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

        # 2. 用户目录下的 mmd/（递归搜索，跳过 animation 和 emotion_config）
        mmd_dir = _ensure_mmd_directory(config_mgr)
        if mmd_dir and mmd_dir.exists():
            skip_dirs = {'animation', 'emotion_config'}
            for ext in ALLOWED_MODEL_EXTENSIONS:
                for model_file in mmd_dir.rglob(f'*{ext}'):
                    try:
                        rel_path = model_file.relative_to(mmd_dir)
                        if rel_path.parts and rel_path.parts[0] in skip_dirs:
                            continue
                    except (ValueError, IndexError):
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
                        "type": model_file.suffix.lstrip('.'),
                        "size": model_file.stat().st_size,
                        "location": "user"
                    })

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
            # 路径安全检查
            safe_name = Path(model).stem  # 只取文件名主体
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

        safe_name = Path(model_name).stem
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
            return JSONResponse(status_code=400, content={"success": False, "error": error})

        if not safe_path.exists():
            return JSONResponse(status_code=404, content={"success": False, "error": "模型文件不存在"})

        model_parent = safe_path.parent
        model_name = safe_path.stem
        deleted_files = 0

        if model_parent.resolve() != mmd_dir.resolve():
            # 模型在子目录中：删除整个子目录（包含纹理等关联资源）
            # 找到 mmd_dir 的直接子目录
            rel_to_mmd = model_parent.resolve().relative_to(mmd_dir.resolve())
            top_subdir = mmd_dir / rel_to_mmd.parts[0]
            if not top_subdir.resolve().is_relative_to(mmd_dir.resolve()):
                return JSONResponse(status_code=400, content={"success": False, "error": "路径越界"})
            for f in top_subdir.rglob('*'):
                if f.is_file():
                    deleted_files += 1
            shutil.rmtree(top_subdir)
            logger.info(f"删除 MMD 模型目录: {top_subdir} ({deleted_files} 个文件)")
        else:
            # 模型在顶层：只删除 PMX 文件本身
            safe_path.unlink()
            deleted_files = 1
            logger.info(f"删除 MMD 模型文件: {safe_path}")

        # 同时删除对应的情感映射配置
        emotion_config = mmd_dir / "emotion_config" / f"{model_name}.json"
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
