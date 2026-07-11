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

"""Preview image discovery / scoring and card-face normalization,
plus the /upload-preview-image endpoint.

Split out of the former monolithic ``main_routers/workshop_router.py``.
"""

from ._shared import logger, router

import os
import json
import tempfile
from datetime import datetime
from pathlib import Path
from fastapi import Request
from fastapi.responses import JSONResponse
from utils.file_utils import atomic_write_json
from utils.config_manager import get_reserved


WORKSHOP_CARD_FACE_SIZE = (768, 1024)


WORKSHOP_CARD_FACE_PADDING = 48


WORKSHOP_CARD_FACE_RATIO_TOLERANCE = 0.02


WORKSHOP_CARD_FACE_MARKER_KEY = 'neko_workshop_card_face'


WORKSHOP_CARD_FACE_MARKER_VALUE = 'steam_preview_v1'


WORKSHOP_STANDARD_PREVIEW_STEMS = ('preview', 'thumbnail', 'icon', 'header')


WORKSHOP_STANDARD_PREVIEW_EXTENSIONS = ('.jpg', '.png', '.jpeg', '.webp')


WORKSHOP_PREVIEW_IMAGE_NAMES = tuple(
    f'{stem}{ext}'
    for stem in WORKSHOP_STANDARD_PREVIEW_STEMS
    for ext in WORKSHOP_STANDARD_PREVIEW_EXTENSIONS
)


WORKSHOP_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}


WORKSHOP_MODEL_TEXTURE_DIR_NAMES = {'texture', 'textures'}


def _collect_workshop_character_name_hints(folder_path: str) -> set[str]:
    hints: set[str] = set()
    try:
        for root, _dirs, filenames in os.walk(folder_path):
            for filename in filenames:
                if not filename.endswith('.chara.json'):
                    continue
                stem = filename[:-11].strip()
                if stem:
                    hints.add(stem)
                chara_path = os.path.join(root, filename)
                try:
                    with open(chara_path, 'r', encoding='utf-8') as f:
                        chara_data = json.load(f)
                    if isinstance(chara_data, dict):
                        chara_name = str(chara_data.get('档案名') or chara_data.get('name') or '').strip()
                        if chara_name:
                            hints.add(chara_name)
                except Exception:
                    continue
    except Exception:
        return hints
    return hints


def _collect_workshop_model_image_references(folder_path: str) -> set[str]:
    references: set[str] = set()

    def _walk_json_values(value, base_dir: str) -> None:
        if isinstance(value, dict):
            for item in value.values():
                _walk_json_values(item, base_dir)
            return
        if isinstance(value, list):
            for item in value:
                _walk_json_values(item, base_dir)
            return
        if not isinstance(value, str):
            return

        normalized = value.replace('\\', '/').strip()
        if not normalized:
            return
        ext = os.path.splitext(normalized)[1].lower()
        if ext not in WORKSHOP_IMAGE_EXTENSIONS:
            return
        references.add(os.path.realpath(os.path.join(base_dir, normalized)))

    try:
        for root, _dirs, filenames in os.walk(folder_path):
            for filename in filenames:
                lower_name = filename.lower()
                if not (
                    lower_name.endswith('.model3.json')
                    or lower_name == 'model.json'
                    or lower_name.endswith('.model.json')
                ):
                    continue
                model_path = os.path.join(root, filename)
                try:
                    with open(model_path, 'r', encoding='utf-8') as f:
                        model_data = json.load(f)
                    _walk_json_values(model_data, root)
                except Exception:
                    continue
    except Exception:
        return references
    return references


def _score_workshop_preview_candidate(
    image_path: str,
    folder_path: str,
    character_name_hints: set[str],
    model_image_references: set[str],
) -> int:
    rel_path = os.path.relpath(image_path, folder_path)
    path_parts = Path(rel_path).parts
    lower_name = os.path.basename(image_path).lower()
    stem = os.path.splitext(os.path.basename(image_path))[0].strip()
    depth = max(0, len(path_parts) - 1)
    score = 0

    if lower_name in WORKSHOP_PREVIEW_IMAGE_NAMES:
        score += 120
    if depth == 0:
        score += 80
    else:
        score -= min(depth * 12, 48)

    if any(part.startswith('.') for part in path_parts):
        score -= 80
    if any(part.lower() in WORKSHOP_MODEL_TEXTURE_DIR_NAMES for part in path_parts[:-1]):
        score -= 120
    if os.path.realpath(image_path) in model_image_references:
        score -= 120

    if stem:
        for hint in character_name_hints:
            if stem == hint:
                score += 100
                break
            if stem in hint or hint in stem:
                score += 40
                break

    try:
        file_size = os.path.getsize(image_path)
        if file_size <= 0:
            score -= 200
        elif file_size >= 8 * 1024:
            score += 8
    except OSError:
        score -= 200

    try:
        from PIL import Image as PILImage
        with PILImage.open(image_path) as img:
            width, height = img.size
        if width < 128 or height < 128:
            score -= 80
        else:
            score += 12
    except Exception:
        score -= 160

    return score


def find_preview_image_in_folder(
    folder_path,
    character_name: str | None = None,
    character_file_stem: str | None = None,
):
    """Find the image best suited as the preview/card-face in a Workshop content directory."""
    for image_name in WORKSHOP_PREVIEW_IMAGE_NAMES:
        image_path = os.path.join(folder_path, image_name)
        if os.path.exists(image_path) and os.path.isfile(image_path):
            return image_path

    if character_name or character_file_stem:
        character_name_hints = {
            hint
            for hint in (str(character_name or '').strip(), str(character_file_stem or '').strip())
            if hint
        }
    else:
        character_name_hints = _collect_workshop_character_name_hints(folder_path)
    model_image_references = _collect_workshop_model_image_references(folder_path)
    candidates: list[tuple[int, int, str]] = []

    try:
        for root, dirs, filenames in os.walk(folder_path):
            dirs[:] = [dirname for dirname in dirs if not dirname.startswith('.')]
            for filename in filenames:
                if filename.startswith('.'):
                    continue
                ext = os.path.splitext(filename)[1].lower()
                if ext not in WORKSHOP_IMAGE_EXTENSIONS:
                    continue
                image_path = os.path.join(root, filename)
                if not os.path.isfile(image_path):
                    continue
                score = _score_workshop_preview_candidate(
                    image_path,
                    folder_path,
                    character_name_hints,
                    model_image_references,
                )
                depth = max(0, len(Path(os.path.relpath(image_path, folder_path)).parts) - 1)
                candidates.append((score, -depth, image_path))
    except Exception:
        return None

    if not candidates:
        return None

    best_score, _depth_score, best_path = max(candidates, key=lambda item: (item[0], item[1], item[2]))
    if best_score <= 0:
        return None
    return best_path


def _build_workshop_card_face_meta(item: dict) -> dict:
    workshop_author = ''
    try:
        workshop_author = str(item.get('authorName') or item.get('author') or item.get('creatorName') or '').strip()[:64]
    except Exception:
        workshop_author = ''

    now_iso = datetime.utcnow().isoformat() + 'Z'
    return {
        'author': workshop_author,
        'origin': 'steam',
        'created_at': now_iso,
        'updated_at': now_iso,
    }


def _read_card_face_origin(meta_path: Path) -> str | None:
    """Read the persisted card-face origin marker from the sidecar file."""
    try:
        if not meta_path.exists():
            return None
        with open(meta_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        origin = str(data.get('origin', '') or '').strip()
        return origin or None
    except Exception:
        return None


def _is_workshop_card_face_normalized(face_path: Path) -> bool:
    """Return True when the existing face already matches the workshop 3:4 derivative shape."""
    if not face_path.exists():
        return False

    from PIL import Image as PILImage

    try:
        with PILImage.open(face_path) as img:
            width, height = img.size
    except Exception:
        return False

    if width <= 0 or height <= 0:
        return False

    target_ratio = WORKSHOP_CARD_FACE_SIZE[0] / WORKSHOP_CARD_FACE_SIZE[1]
    current_ratio = width / height
    return abs(current_ratio - target_ratio) <= WORKSHOP_CARD_FACE_RATIO_TOLERANCE


def _should_refresh_workshop_card_face(face_path: Path, meta_path: Path) -> bool:
    """Decide whether a workshop preview is allowed to replace the current card face."""
    if not face_path.exists():
        return True

    origin = _read_card_face_origin(meta_path)
    if origin is None:
        # sidecar 缺失时默认保护现有自定义 PNG；但如果卡面带有本地生成的
        # Workshop marker，说明它是渲染中断后留下的孤儿文件，允许后续重试。
        return _has_workshop_card_face_marker(face_path)

    if origin in {'self', 'imported'}:
        return False

    return not _is_workshop_card_face_normalized(face_path)


def _render_workshop_card_face_image(img):
    """Render a workshop preview into the normalized 3:4 in-app card-face layout."""
    from PIL import Image as PILImage, ImageFilter, ImageOps

    resampling = getattr(PILImage, 'Resampling', PILImage)
    lanczos = getattr(resampling, 'LANCZOS', PILImage.BICUBIC)

    working = ImageOps.exif_transpose(img).convert('RGBA')

    canvas = PILImage.new('RGBA', WORKSHOP_CARD_FACE_SIZE, (231, 245, 255, 255))
    background = ImageOps.fit(
        working,
        WORKSHOP_CARD_FACE_SIZE,
        method=lanczos,
        centering=(0.5, 0.5),
    )
    background = background.filter(ImageFilter.GaussianBlur(radius=28))
    canvas = PILImage.blend(canvas, background, 0.82)
    canvas = PILImage.alpha_composite(
        canvas,
        PILImage.new('RGBA', WORKSHOP_CARD_FACE_SIZE, (255, 255, 255, 30)),
    )

    foreground = working.copy()
    foreground.thumbnail(
        (
            max(64, WORKSHOP_CARD_FACE_SIZE[0] - WORKSHOP_CARD_FACE_PADDING * 2),
            max(64, WORKSHOP_CARD_FACE_SIZE[1] - WORKSHOP_CARD_FACE_PADDING * 2),
        ),
        resample=lanczos,
    )
    foreground = ImageOps.expand(foreground, border=8, fill=(255, 255, 255, 28))

    offset_x = (WORKSHOP_CARD_FACE_SIZE[0] - foreground.width) // 2
    offset_y = (WORKSHOP_CARD_FACE_SIZE[1] - foreground.height) // 2
    canvas.alpha_composite(foreground, (offset_x, offset_y))
    return canvas


def _has_workshop_card_face_marker(face_path: Path) -> bool:
    """Detect workshop-generated preview PNGs even if the sidecar is missing."""
    try:
        from PIL import Image as PILImage

        with PILImage.open(face_path) as img:
            return str(img.info.get(WORKSHOP_CARD_FACE_MARKER_KEY, '') or '') == WORKSHOP_CARD_FACE_MARKER_VALUE
    except Exception:
        return False


def _is_matching_workshop_character(catgirl_data: dict, item_id) -> bool:
    if not isinstance(catgirl_data, dict):
        return False

    try:
        current_item_id = str(item_id or '').strip()
        if not current_item_id:
            return False

        # 归属判定与退订确认路径（_is_confirmed_workshop_character）保持一致：
        #   - character_origin.source_id 表示角色最初来自哪个 Workshop 物品
        #   - avatar.asset_source_id 表示当前实际绑定的模型来源
        # 旧数据 / 半迁移数据可能只有 avatar 绑定（例如 live2d_item_id 迁移只写
        # avatar.asset_source_id，或用户在模型设置里手动绑定 Workshop 模型时只写
        # avatar.*）。若这里只看 character_origin，这类角色会被退订路径按 avatar
        # 命中删除并打上 tombstone，却无法被恢复路径识别，导致 tombstone 永远清不掉、
        # /sync-character/{item_id} 一直回 409。两边判定必须对偶。
        origin_source = str(
            get_reserved(catgirl_data, 'character_origin', 'source', default='') or ''
        ).strip()
        origin_source_id = str(
            get_reserved(catgirl_data, 'character_origin', 'source_id', default='') or ''
        ).strip()
        avatar_source = str(
            get_reserved(catgirl_data, 'avatar', 'asset_source', default='') or ''
        ).strip()
        avatar_source_id = str(
            get_reserved(catgirl_data, 'avatar', 'asset_source_id', default='') or ''
        ).strip()

        return (
            origin_source == 'steam_workshop' and origin_source_id == current_item_id
        ) or (
            avatar_source == 'steam_workshop' and avatar_source_id == current_item_id
        )
    except Exception:
        return False


def _ensure_workshop_card_face_from_preview(
    config_mgr,
    chara_name: str,
    preview_image_path: str | None,
    item: dict | None = None,
) -> bool:
    """Create or refresh a workshop-derived card face from the Steam preview image."""
    if not preview_image_path or not os.path.isfile(preview_image_path):
        return False
    if not config_mgr.ensure_card_faces_directory():
        return False

    face_path = config_mgr.card_faces_dir / f"{chara_name}.png"
    meta_path = config_mgr.card_face_meta_path(chara_name)
    if not _should_refresh_workshop_card_face(face_path, meta_path):
        return False

    from PIL import Image as PILImage
    from PIL import PngImagePlugin

    fd, temp_path = tempfile.mkstemp(
        prefix=f".{face_path.name}.",
        suffix=".tmp",
        dir=str(face_path.parent),
    )

    try:
        with os.fdopen(fd, 'w+b') as temp_file:
            with PILImage.open(preview_image_path) as img:
                normalized = _render_workshop_card_face_image(img)
                pnginfo = PngImagePlugin.PngInfo()
                pnginfo.add_text(WORKSHOP_CARD_FACE_MARKER_KEY, WORKSHOP_CARD_FACE_MARKER_VALUE)
                normalized.save(temp_file, format='PNG', optimize=True, pnginfo=pnginfo)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, face_path)
        if item and not meta_path.exists():
            atomic_write_json(meta_path, _build_workshop_card_face_meta(item), ensure_ascii=False, indent=2)
    except Exception:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass
        raise

    return True


def _ensure_workshop_card_face_meta(config_mgr, chara_name: str, item: dict) -> bool:
    """Persist sidecar metadata for workshop-generated card faces when missing."""
    if not config_mgr.ensure_card_faces_directory():
        return False

    face_path = config_mgr.card_faces_dir / f"{chara_name}.png"
    if not face_path.exists() or not _has_workshop_card_face_marker(face_path):
        return False

    meta_path = config_mgr.card_face_meta_path(chara_name)
    if meta_path.exists():
        return False

    atomic_write_json(meta_path, _build_workshop_card_face_meta(item), ensure_ascii=False, indent=2)
    return True


@router.post('/upload-preview-image')
async def upload_preview_image(request: Request):
    """
    Upload a preview image, renamed uniformly to preview.* and saved into the given content folder (if provided).
    """
    try:  
        # 接收上传的文件和表单数据
        form = await request.form()
        file = form.get('file')
        content_folder = form.get('content_folder')
        
        if not file:
            return JSONResponse({
                "success": False,
                "error": "没有选择文件",
                "message": "请选择要上传的图片文件"
            }, status_code=400)
        
        # 验证文件类型
        allowed_types = ['image/jpeg', 'image/png', 'image/jpg']
        if file.content_type not in allowed_types:
            return JSONResponse({
                "success": False,
                "error": "文件类型不允许",
                "message": "只允许上传JPEG和PNG格式的图片"
            }, status_code=400)
        
        # 获取文件扩展名
        # 扩展名按 content-type 固定映射，别信 filename
        content_type_to_ext = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png"}
        file_extension = content_type_to_ext.get(file.content_type)
        if not file_extension:
            return JSONResponse({"success": False, "error": "文件类型不允许"}, status_code=400)
                    
        # 处理内容文件夹路径
        if content_folder:
            # 规范化路径
            import urllib.parse
            content_folder = urllib.parse.unquote(content_folder)
            if os.name == 'nt':
                content_folder = content_folder.replace('/', '\\')
                if content_folder.startswith('\\\\'):
                    content_folder = content_folder[2:]
                else:
                    content_folder = content_folder.replace('\\', '/')
            
            # 验证内容文件夹存在
            if not os.path.exists(content_folder) or not os.path.isdir(content_folder):
                # 如果文件夹不存在，回退到临时目录
                logger.warning(f"指定的内容文件夹不存在: {content_folder}，使用临时目录")
                content_folder = None
        
        # 创建统一命名的预览图路径
        if content_folder:
            # 直接保存到内容文件夹
            preview_image_path = os.path.join(content_folder, f'preview{file_extension}')
        else:
            # 使用临时目录
            import tempfile
            temp_folder = tempfile.gettempdir()
            preview_image_path = os.path.join(temp_folder, f'preview{file_extension}')
        
        # 保存文件到指定路径
        with open(preview_image_path, 'wb') as f:
            f.write(await file.read())
        
        return JSONResponse({
            "success": True,
            "file_path": preview_image_path,
            "message": "文件上传成功"
        })
    except Exception as e:
        logger.error(f"上传预览图片时出错: {e}")
        return JSONResponse({
            "success": False,
            "error": "内部错误",
            "message": "文件上传失败"
        }, status_code=500)
