# -*- coding: utf-8 -*-
"""
Jukebox Router

Handles jukebox-related endpoints including:
- Song management (upload, list, delete, visibility)
- Action/VMD management (upload, list, delete)
- Song-Action binding management
- Configuration import/export
"""

import io
import json
import hashlib
import shutil
import zipfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .shared_state import get_config_manager
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger

router = APIRouter(prefix="/api/jukebox", tags=["jukebox"])
logger = get_module_logger(__name__, "Main")

# 文件上传常量
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
CHUNK_SIZE = 1024 * 1024  # 1MB chunks

# 允许的文件扩展名
ALLOWED_AUDIO_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.flac'}
ALLOWED_ACTION_EXTENSIONS = {'.vmd', '.bvh', '.fbx', '.vrma'}

import re

def sanitize_filename(name: str) -> str:
    """清理文件名，移除非法字符，用于生成ID"""
    # 移除扩展名
    name = Path(name).stem
    # 替换非法字符为下划线
    name = re.sub(r'[<>:"/\\|?*\s]+', '_', name)
    # 移除连续的下划线
    name = re.sub(r'_+', '_', name)
    # 移除首尾下划线
    name = name.strip('_')
    # 限制长度
    if len(name) > 50:
        name = name[:50]
    return name or 'unnamed'

def get_unique_filename(directory: Path, filename: str) -> str:
    """获取唯一的文件名，如果冲突则添加数字后缀（使用下划线格式）"""
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
        # 防止无限循环
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
    defaultAction: str = ""  # 默认动画ID


@dataclass
class Action:
    id: str
    name: str
    file: str  # 动画文件路径（如 actions/action_001.vmd）
    fileMd5: str  # 文件MD5
    format: str  # 动画格式（vmd, vrma, fbx, bvh）
    uploadDate: str
    missing: bool = False


class JukeboxConfig:
    """点歌台配置管理器"""
    
    def __init__(self, config_mgr):
        self.config_mgr = config_mgr
        self.jukebox_dir = config_mgr.app_docs_dir / "jukebox"
        self.songs_dir = self.jukebox_dir / "songs"
        self.actions_dir = self.jukebox_dir / "actions"
        self.config_file = self.jukebox_dir / "config.json"
        
        # 确保目录存在
        self._ensure_directories()
        
        # 加载配置
        self.data = self._load_config()
    
    def _ensure_directories(self):
        """确保目录存在"""
        self.jukebox_dir.mkdir(parents=True, exist_ok=True)
        self.songs_dir.mkdir(parents=True, exist_ok=True)
        self.actions_dir.mkdir(parents=True, exist_ok=True)
    
    def _load_config(self) -> dict:
        """加载配置文件，融合用户配置和软件自带配置"""
        # 默认配置
        default_config = {
            "version": "1.0",
            "songs": {},
            "actions": {},
            "bindings": {},
            "md5Index": {"songs": {}, "actions": {}}
        }

        # 加载用户配置
        user_config = {}
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
            except Exception as e:
                logger.error(f"加载点歌台用户配置失败: {e}")

        # 加载软件自带配置
        builtin_songs = {}
        builtin_actions = {}
        builtin_bindings = {}
        builtin_md5_index = {"songs": {}, "actions": {}}
        
        builtin_songs_path = Path(__file__).parent.parent / "static" / "jukebox" / "songs.json"
        if builtin_songs_path.exists():
            try:
                with open(builtin_songs_path, 'r', encoding='utf-8') as f:
                    builtin_data = json.load(f)
                    
                    # 加载自带歌曲（字典格式）
                    for song_id, song in builtin_data.get("songs", {}).items():
                        song["isBuiltin"] = True  # 标记为自带资源，不可导出
                        builtin_songs[song_id] = song
                    
                    # 加载自带动画（字典格式）
                    for action_id, action in builtin_data.get("actions", {}).items():
                        action["isBuiltin"] = True
                        builtin_actions[action_id] = action
                    
                    # 加载自带绑定关系
                    builtin_bindings = builtin_data.get("bindings", {})
                    
                    # 加载自带MD5索引
                    builtin_md5_index = builtin_data.get("md5Index", {"songs": {}, "actions": {}})
            except Exception as e:
                logger.error(f"加载软件自带配置失败: {e}")

        # 融合配置：用户配置优先，但保留软件自带配置
        merged_songs = {**builtin_songs, **user_config.get("songs", {})}
        merged_actions = {**builtin_actions, **user_config.get("actions", {})}
        merged_bindings = {**builtin_bindings, **user_config.get("bindings", {})}
        
        # 融合MD5索引
        user_md5_index = user_config.get("md5Index", {"songs": {}, "actions": {}})
        merged_md5_index = {
            "songs": {**builtin_md5_index.get("songs", {}), **user_md5_index.get("songs", {})},
            "actions": {**builtin_md5_index.get("actions", {}), **user_md5_index.get("actions", {})}
        }

        return {
            "version": user_config.get("version", "1.0"),
            "songs": merged_songs,
            "actions": merged_actions,
            "bindings": merged_bindings,
            "md5Index": merged_md5_index
        }

    def save(self):
        """保存配置（排除自带资源，但保留跨类型绑定）"""
        # 获取所有资源ID及其类型
        all_songs = self.data.get("songs", {})
        all_actions = self.data.get("actions", {})
        
        # 区分自带资源和用户资源
        user_songs = {k: v for k, v in all_songs.items() if not v.get("isBuiltin", False)}
        user_actions = {k: v for k, v in all_actions.items() if not v.get("isBuiltin", False)}
        builtin_song_ids = {k for k, v in all_songs.items() if v.get("isBuiltin", False)}
        builtin_action_ids = {k for k, v in all_actions.items() if v.get("isBuiltin", False)}
        
        user_data = {
            "version": self.data.get("version", "1.0"),
            "songs": user_songs,
            "actions": user_actions,
            "bindings": {},  # 保存所有涉及用户资源的绑定
            "md5Index": {
                "songs": {},
                "actions": {}
            }
        }
        
        # 保存绑定关系：只要绑定涉及用户资源就保存
        # 包括：用户歌曲↔用户动画、自带歌曲↔用户动画、用户歌曲↔自带动画
        for song_id, actions in self.data.get("bindings", {}).items():
            is_user_song = song_id in user_songs
            
            for action_id, bind_data in actions.items():
                is_user_action = action_id in user_actions
                
                # 只保存至少一方是用户资源的绑定
                if is_user_song or is_user_action:
                    if song_id not in user_data["bindings"]:
                        user_data["bindings"][song_id] = {}
                    user_data["bindings"][song_id][action_id] = bind_data
        
        # 过滤MD5索引：只保留用户资源的MD5
        for md5_key, md5_map in self.data.get("md5Index", {}).items():
            user_data["md5Index"][md5_key] = {
                k: v for k, v in md5_map.items() 
                if v not in all_songs or not all_songs.get(v, {}).get("isBuiltin", False)
                if v not in all_actions or not all_actions.get(v, {}).get("isBuiltin", False)
            }
        
        atomic_write_json(self.config_file, user_data)
    
    def get_next_id(self, prefix: str) -> str:
        """获取下一个 ID"""
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


def calculate_md5(file_path: Path) -> str:
    """计算文件 MD5"""
    md5_hash = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()





# ═══════════════════ API 路由 ═══════════════════

@router.get("/config")
async def get_config():
    """获取完整配置（本地绑定已经是ID级别，直接返回）"""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    return jukebox_config.data


@router.post("/songs")
async def upload_songs(
    files: List[UploadFile] = File(...),
    metadata: str = Form("[]")
):
    """
    上传歌曲
    files: 单个文件或文件列表
    metadata: JSON 字符串，包含每首歌的元数据 [{name, artist}, ...]
    """
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    try:
        meta_list = json.loads(metadata) if metadata else []
    except json.JSONDecodeError:
        raise HTTPException(400, "metadata 格式错误")
    
    # 确保 meta_list 长度与 files 一致，不足时补充空对象
    while len(meta_list) < len(files):
        meta_list.append({})
    
    results = []
    for i, (file, meta) in enumerate(zip(files, meta_list)):
        try:
            # 验证文件扩展名
            file_ext = Path(file.filename).suffix.lower()
            if file_ext not in ALLOWED_AUDIO_EXTENSIONS:
                results.append({"success": False, "error": f"不支持的格式: {file_ext}"})
                continue
            
            # 获取原始文件名（不含路径）
            original_filename = Path(file.filename).name
            file_stem = Path(file.filename).stem

            # 生成安全的ID（基于文件名）
            song_id = sanitize_filename(file.filename)

            # 确保ID唯一
            base_id = song_id
            counter = 1
            while song_id in jukebox_config.data["songs"]:
                song_id = f"{base_id}_{counter}"
                counter += 1

            # 获取唯一的文件名
            target_filename = get_unique_filename(jukebox_config.songs_dir, original_filename)
            target_path = jukebox_config.songs_dir / target_filename

            with open(target_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            # 计算 MD5
            file_md5 = calculate_md5(target_path)

            # 检查重复（基于MD5）
            existing_song_id = jukebox_config.data["md5Index"]["songs"].get(file_md5)
            if existing_song_id:
                target_path.unlink(missing_ok=True)
                results.append({"success": False, "error": f"歌曲已存在: {existing_song_id}"})
                continue

            # 使用提供的值或文件名作为默认显示名称
            # 如果文件名有数字后缀（如"歌曲(1).mp3"），默认显示名称也保留这个后缀
            song_name = meta.get("name") or Path(target_filename).stem
            song_artist = meta.get("artist") or "未知"

            # 创建歌曲记录
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
            
            # 保存到配置
            jukebox_config.data["songs"][song_id] = asdict(song)
            jukebox_config.data["md5Index"]["songs"][file_md5] = song_id
            
            results.append({"success": True, "song": asdict(song)})
            
        except Exception as e:
            logger.error(f"上传第 {i+1} 首歌曲失败: {e}")
            results.append({"success": False, "error": str(e)})
        finally:
            file.file.close()
    
    jukebox_config.save()
    
    # 单首歌曲上传时直接返回结果，批量时返回结果列表
    if len(files) == 1:
        return results[0] if results else {"success": False, "error": "无文件上传"}
    return {"success": True, "results": results}


@router.delete("/songs/{song_id}")
async def delete_song(song_id: str):
    """删除歌曲"""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    if song_id not in jukebox_config.data["songs"]:
        raise HTTPException(404, "歌曲不存在")
    
    song = jukebox_config.data["songs"][song_id]
    
    # 删除文件
    audio_path = jukebox_config.jukebox_dir / song["audio"]
    if audio_path.exists():
        audio_path.unlink()
    
    # 删除相关绑定（使用ID）
    if song_id in jukebox_config.data["bindings"]:
        del jukebox_config.data["bindings"][song_id]
    
    # 从 MD5 索引中移除
    song_md5 = song.get("audioMd5", "")
    if song_md5 and song_md5 in jukebox_config.data["md5Index"]["songs"]:
        del jukebox_config.data["md5Index"]["songs"][song_md5]
    
    # 删除歌曲记录
    del jukebox_config.data["songs"][song_id]
    jukebox_config.save()
    
    logger.info(f"删除歌曲: {song_id}")
    return {"success": True}


@router.put("/songs/{song_id}/visibility")
async def update_song_visibility(song_id: str, visible: bool = Form(...)):
    """更新歌曲可见性"""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    if song_id not in jukebox_config.data["songs"]:
        raise HTTPException(404, "歌曲不存在")
    
    jukebox_config.data["songs"][song_id]["visible"] = visible
    jukebox_config.save()
    
    return {"success": True}


@router.put("/songs/{song_id}/metadata")
async def update_song_metadata(
    song_id: str,
    name: str = Form(None),
    artist: str = Form(None)
):
    """更新歌曲元数据（名称、歌手）"""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    if song_id not in jukebox_config.data["songs"]:
        raise HTTPException(404, "歌曲不存在")
    
    if name is not None:
        jukebox_config.data["songs"][song_id]["name"] = name
    if artist is not None:
        jukebox_config.data["songs"][song_id]["artist"] = artist
    
    jukebox_config.save()
    
    logger.info(f"更新歌曲元数据: {song_id}, name={name}, artist={artist}")
    return {"success": True}


@router.put("/actions/{action_id}/metadata")
async def update_action_metadata(
    action_id: str,
    name: str = Form(...)
):
    """更新动画元数据（名称）"""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    if action_id not in jukebox_config.data["actions"]:
        raise HTTPException(404, "动画不存在")
    
    jukebox_config.data["actions"][action_id]["name"] = name
    jukebox_config.save()
    
    logger.info(f"更新动画元数据: {action_id}, name={name}")
    return {"success": True}


@router.put("/songs/{song_id}/default-action")
async def set_song_default_action(
    song_id: str,
    action_id: str = Form(...)  # 空字符串表示取消默认动画
):
    """设置歌曲的默认动画"""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    if song_id not in jukebox_config.data["songs"]:
        raise HTTPException(404, "歌曲不存在")
    
    # 如果提供了action_id，检查动画是否存在
    if action_id and action_id not in jukebox_config.data["actions"]:
        raise HTTPException(404, "动画不存在")
    
    # 检查动画是否绑定到该歌曲
    # 绑定数据格式: bindings[songId][actionId] = {"offset": 0}
    if action_id:
        song_bindings = jukebox_config.data["bindings"].get(song_id, {})
        if action_id not in song_bindings:
            raise HTTPException(400, "该动画未绑定到此歌曲")
    
    jukebox_config.data["songs"][song_id]["defaultAction"] = action_id
    jukebox_config.save()
    
    logger.info(f"设置歌曲默认动画: {song_id} -> {action_id}")
    return {"success": True, "defaultAction": action_id}


@router.post("/actions")
async def upload_actions(
    files: List[UploadFile] = File(...),
    metadata: str = Form("[]")
):
    """
    上传动画
    files: 单个文件或文件列表
    metadata: JSON 字符串，包含每个动画的元数据 [{name}, ...]
    """
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    try:
        meta_list = json.loads(metadata) if metadata else []
    except json.JSONDecodeError:
        raise HTTPException(400, "metadata 格式错误")
    
    # 确保 meta_list 长度与 files 一致，不足时补充空对象
    while len(meta_list) < len(files):
        meta_list.append({})
    
    results = []
    for i, (file, meta) in enumerate(zip(files, meta_list)):
        try:
            # 验证文件扩展名
            file_ext = Path(file.filename).suffix.lower()
            if file_ext not in ALLOWED_ACTION_EXTENSIONS:
                results.append({"success": False, "error": f"不支持的格式: {file_ext}"})
                continue

            # 获取原始文件名（不含路径）
            original_filename = Path(file.filename).name

            # 生成安全的ID（基于文件名）
            action_id = sanitize_filename(file.filename)

            # 确保ID唯一
            base_id = action_id
            counter = 1
            while action_id in jukebox_config.data["actions"]:
                action_id = f"{base_id}_{counter}"
                counter += 1

            # 获取唯一的文件名
            target_filename = get_unique_filename(jukebox_config.actions_dir, original_filename)
            target_path = jukebox_config.actions_dir / target_filename

            with open(target_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            # 计算 MD5
            file_md5 = calculate_md5(target_path)

            # 检查重复（基于MD5）
            existing_action_id = jukebox_config.data["md5Index"]["actions"].get(file_md5)
            if existing_action_id:
                target_path.unlink(missing_ok=True)
                results.append({"success": False, "error": f"动画已存在: {existing_action_id}"})
                continue

            # 使用提供的名称或文件名作为默认显示名称
            # 如果文件名有数字后缀（如"动画(1).vmd"），默认显示名称也保留这个后缀
            action_name = meta.get("name") or Path(target_filename).stem

            # 创建动画记录
            action = Action(
                id=action_id,
                name=action_name,
                file=f"actions/{target_filename}",
                fileMd5=file_md5,
                format=file_ext.lstrip("."),
                uploadDate=datetime.now().isoformat(),
                missing=False
            )
            
            # 保存到配置
            jukebox_config.data["actions"][action_id] = asdict(action)
            jukebox_config.data["md5Index"]["actions"][file_md5] = action_id
            
            results.append({"success": True, "action": asdict(action)})
            
        except Exception as e:
            logger.error(f"上传第 {i+1} 个动画失败: {e}")
            results.append({"success": False, "error": str(e)})
        finally:
            file.file.close()
    
    jukebox_config.save()
    
    # 单个动画上传时直接返回结果，批量时返回结果列表
    if len(files) == 1:
        return results[0] if results else {"success": False, "error": "无文件上传"}
    return {"success": True, "results": results}


@router.delete("/actions/{action_id}")
async def delete_action(action_id: str):
    """删除动画"""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)

    if action_id not in jukebox_config.data["actions"]:
        raise HTTPException(404, "动画不存在")

    action = jukebox_config.data["actions"][action_id]

    # 删除文件
    file_path = jukebox_config.jukebox_dir / action["file"]
    if file_path.exists():
        file_path.unlink()

    # 从所有绑定中移除（使用ID）
    for song_id, bindings in jukebox_config.data["bindings"].items():
        if action_id in bindings:
            del bindings[action_id]

    # 从 MD5 索引中移除
    action_md5 = action.get("fileMd5", "")
    if action_md5 and action_md5 in jukebox_config.data["md5Index"]["actions"]:
        del jukebox_config.data["md5Index"]["actions"][action_md5]

    # 删除动画记录
    del jukebox_config.data["actions"][action_id]
    jukebox_config.save()

    logger.info(f"删除动画: {action_id}")
    return {"success": True}


@router.post("/bind")
async def bind_song_action(
    songId: str = Form(...),
    actionId: str = Form(...),
    offset: int = Form(0)
):
    """建立歌曲与动画的绑定（基于ID）"""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)

    # 验证存在性
    if songId not in jukebox_config.data["songs"]:
        raise HTTPException(404, "歌曲不存在")
    if actionId not in jukebox_config.data["actions"]:
        raise HTTPException(404, "动画不存在")
    
    # 建立绑定（使用ID作为键）
    # 绑定结构: bindings[songId][actionId] = {"offset": 0}
    if songId not in jukebox_config.data["bindings"]:
        jukebox_config.data["bindings"][songId] = {}
    
    jukebox_config.data["bindings"][songId][actionId] = {"offset": offset}
    
    # 自动设置默认动画：如果这是该类型第一个绑定的动画，设为默认
    song = jukebox_config.data["songs"][songId]
    action = jukebox_config.data["actions"][actionId]
    action_format = action.get("format", "vmd").lower()

    # 检查是否已有该类型的默认动画
    current_default = song.get("defaultAction", "")
    if current_default:
        default_action = jukebox_config.data["actions"].get(current_default)
        if default_action:
            default_format = default_action.get("format", "vmd").lower()
            # 如果已有同类型的默认动画，不覆盖
            if default_format == action_format:
                logger.info(f"歌曲 {songId} 已有 {action_format} 类型的默认动画，保持原有设置")
            else:
                # 不同类型，设为默认
                song["defaultAction"] = actionId
                logger.info(f"设置默认动画: {songId} -> {actionId} (类型: {action_format})")
        else:
            # 默认动画不存在了，设为新的
            song["defaultAction"] = actionId
            logger.info(f"设置默认动画: {songId} -> {actionId} (原默认动画不存在)")
    else:
        # 没有默认动画，设为默认
        song["defaultAction"] = actionId
        logger.info(f"设置默认动画: {songId} -> {actionId} (首次绑定)")
    
    jukebox_config.save()
    
    logger.info(f"建立绑定: {songId} <-> {actionId}, offset={offset}")
    return {"success": True, "defaultAction": song.get("defaultAction", "")}


@router.delete("/bind")
async def unbind_song_action(
    songId: str = Form(...),
    actionId: str = Form(...)
):
    """解除歌曲与动画的绑定（基于ID）"""
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    # 验证存在性
    if songId not in jukebox_config.data["songs"]:
        raise HTTPException(404, "歌曲不存在")
    if actionId not in jukebox_config.data["actions"]:
        raise HTTPException(404, "动画不存在")
    
    # 解除绑定
    if songId in jukebox_config.data["bindings"]:
        if actionId in jukebox_config.data["bindings"][songId]:
            del jukebox_config.data["bindings"][songId][actionId]
            
            # 如果没有绑定了，删除空字典
            if not jukebox_config.data["bindings"][songId]:
                del jukebox_config.data["bindings"][songId]
            
            # 如果解绑的是默认动画，清除默认动画设置
            song = jukebox_config.data["songs"][songId]
            if song.get("defaultAction") == actionId:
                song["defaultAction"] = ""
                logger.info(f"清除默认动画: {songId} (解绑了默认动画 {actionId})")
            
            jukebox_config.save()
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
    导出配置
    songIds: JSON 字符串数组，为空时导出所有
    actionIds: JSON 字符串数组，为空时导出所有
    """
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    # 解析 ID 列表
    selected_songs = json.loads(songIds) if songIds else None
    selected_actions = json.loads(actionIds) if actionIds else None
    
    # 创建临时目录
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        export_dir = temp_path / "jukebox_export"
        export_dir.mkdir()
        
        # 准备导出数据
        export_data = {
            "version": jukebox_config.data["version"],
            "songs": {},
            "actions": {},
            "bindings": {}
        }
        
        # 导出歌曲（注意：空列表 [] 也是有效的选择，表示不导出任何歌曲）
        songs_to_export = selected_songs if selected_songs is not None else list(jukebox_config.data["songs"].keys())
        
        # 收集需要导出的歌曲ID
        song_ids_to_export = set()
        for song_id in songs_to_export:
            if song_id not in jukebox_config.data["songs"]:
                continue
            
            song = jukebox_config.data["songs"][song_id]
            
            # 跳过自带资源（不可导出）
            if song.get("isBuiltin", False):
                continue
            
            # 跳过隐藏歌曲（如果不包含隐藏）
            if not includeHidden and not song.get("visible", True):
                continue
            
            song_ids_to_export.add(song_id)
        
        # 收集需要导出的动画ID
        action_ids_to_export = set(selected_actions if selected_actions is not None else [])
        
        # 导出歌曲
        for song_id in song_ids_to_export:
            song = jukebox_config.data["songs"][song_id]
            export_data["songs"][song_id] = song
            
            # 复制文件
            src_path = jukebox_config.jukebox_dir / song["audio"]
            if src_path.exists():
                dst_path = export_dir / song["audio"]
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)
        
        # 导出动画
        for action_id in action_ids_to_export:
            if action_id not in jukebox_config.data["actions"]:
                continue

            action = jukebox_config.data["actions"][action_id]
            
            # 跳过自带资源（不可导出）
            if action.get("isBuiltin", False):
                continue
            
            export_data["actions"][action_id] = action

            # 复制文件
            src_path = jukebox_config.jukebox_dir / action["file"]
            if src_path.exists():
                dst_path = export_dir / action["file"]
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)

        # 导出绑定关系（将ID绑定转换为MD5绑定，便于跨系统导入）
        # 本地存储格式: bindings[songId][actionId] = {"offset": 0}
        # 导出格式: bindings[songMd5][actionMd5] = {"offset": 0}
        md5_bindings = {}
        
        # 构建ID到MD5的映射
        song_id_to_md5 = {sid: s.get("audioMd5", "") for sid, s in jukebox_config.data["songs"].items()}
        action_id_to_md5 = {aid: a.get("fileMd5", "") for aid, a in jukebox_config.data["actions"].items()}
        
        # 1. 收集选中歌曲的绑定（ID -> MD5）
        for song_id in song_ids_to_export:
            song_md5 = song_id_to_md5.get(song_id, "")
            if not song_md5:
                continue
            
            # 查找该歌曲ID的所有绑定
            if song_id in jukebox_config.data.get("bindings", {}):
                md5_bindings[song_md5] = {}
                for action_id, binding_data in jukebox_config.data["bindings"][song_id].items():
                    action_md5 = action_id_to_md5.get(action_id, "")
                    if action_md5:
                        md5_bindings[song_md5][action_md5] = {
                            "offset": binding_data.get("offset", 0)
                        }
        
        # 2. 收集选中动画相关的绑定（反向查找）
        for action_id in action_ids_to_export:
            action_md5 = action_id_to_md5.get(action_id, "")
            if not action_md5:
                continue
            
            # 遍历所有绑定，查找包含该动画的绑定
            for song_id, action_bindings in jukebox_config.data.get("bindings", {}).items():
                if action_id in action_bindings:
                    song_md5 = song_id_to_md5.get(song_id, "")
                    if song_md5:
                        if song_md5 not in md5_bindings:
                            md5_bindings[song_md5] = {}
                        md5_bindings[song_md5][action_md5] = {
                            "offset": action_bindings[action_id].get("offset", 0)
                        }
        
        export_data["bindings"] = md5_bindings
        
        # 写入配置文件
        with open(export_dir / "config.json", "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        
        # 打包为 zip
        zip_path = temp_path / "jukebox_export.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in export_dir.rglob("*"):
                if file_path.is_file():
                    zf.write(file_path, file_path.relative_to(export_dir))
        
        # 读取 zip 文件到内存（避免临时目录被清理后文件丢失）
        with open(zip_path, "rb") as f:
            zip_content = f.read()
        
        return StreamingResponse(
            io.BytesIO(zip_content),
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=jukebox_export.zip"}
        )


@router.get("/file/{file_path:path}")
async def get_file(file_path: str):
    """获取歌曲或动画文件
    file_path: 相对路径，如 songs/song_001.mp3 或 actions/action_001.vmd
    优先从用户文档目录获取，如果不存在则从软件自带目录获取
    """
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    # 去除前导斜杠，防止路径解析问题
    file_path = file_path.lstrip('/')
    
    # 处理 /static/jukebox/ 前缀（自带资源的特殊路径）
    if file_path.startswith('static/jukebox/'):
        file_path = file_path.replace('static/jukebox/', '', 1)
    
    # 安全检查：确保路径在 jukebox 目录内
    full_path = (jukebox_config.jukebox_dir / file_path).resolve()
    jukebox_root = jukebox_config.jukebox_dir.resolve()
    
    # 防止目录遍历攻击
    if not str(full_path).startswith(str(jukebox_root)):
        raise HTTPException(403, "访问被拒绝")
    
    # 优先使用用户文档目录的文件
    if full_path.exists() and full_path.is_file():
        target_path = full_path
    else:
        # 如果用户目录不存在，尝试从软件自带目录获取
        builtin_path = Path(__file__).parent.parent / "static" / "jukebox" / file_path
        builtin_path = builtin_path.resolve()
        builtin_root = (Path(__file__).parent.parent / "static" / "jukebox").resolve()
        
        # 安全检查
        if not str(builtin_path).startswith(str(builtin_root)):
            raise HTTPException(403, "访问被拒绝")
        
        if not builtin_path.exists() or not builtin_path.is_file():
            raise HTTPException(404, "文件不存在")
        
        target_path = builtin_path
    
    # 根据扩展名确定媒体类型
    ext = target_path.suffix.lower()
    media_types = {
        '.mp3': 'audio/mpeg',
        '.wav': 'audio/wav',
        '.ogg': 'audio/ogg',
        '.vmd': 'application/octet-stream',
        '.bvh': 'application/octet-stream',
        '.fbx': 'application/octet-stream'
    }
    media_type = media_types.get(ext, 'application/octet-stream')
    
    return FileResponse(target_path, media_type=media_type)


@router.post("/import")
async def import_config(file: UploadFile = File(...)):
    """导入配置（MD5级别绑定）"""
    # 注意：使用配置中已有的 MD5 值，不再重新计算
    # 如果有不一样的地方 虽然不知道为什么导入的文件和本身 MD5 不一样,但是既然存在修改那一定是有他的道理的.jpeg
    config_mgr = get_config_manager()
    jukebox_config = JukeboxConfig(config_mgr)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        zip_path = temp_path / "import.zip"
        
        with open(zip_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        extract_dir = temp_path / "extracted"
        extract_dir.mkdir()
        
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        
        config_path = extract_dir / "config.json"
        if not config_path.exists():
            raise HTTPException(400, "导入包中缺少 config.json")
        
        with open(config_path, "r", encoding="utf-8") as f:
            import_data = json.load(f)
        
        stats = {
            "songsAdded": 0,
            "songsMerged": 0,
            "actionsAdded": 0,
            "actionsMerged": 0,
            "bindingsAdded": 0
        }
        
        # 第一步：导入歌曲
        for song_id, song in import_data.get("songs", {}).items():
            file_md5 = song.get("audioMd5", "")
            if not file_md5:
                src_audio = extract_dir / song["audio"]
                if src_audio.exists():
                    file_md5 = calculate_md5(src_audio)
                    song["audioMd5"] = file_md5

            existing_id = jukebox_config.data["md5Index"]["songs"].get(file_md5) if file_md5 else None
            
            if existing_id:
                stats["songsMerged"] += 1
            else:
                src_audio = extract_dir / song["audio"]
                if src_audio.exists():
                    original_filename = Path(song["audio"]).name
                    target_filename = get_unique_filename(jukebox_config.songs_dir, original_filename)
                    dst_audio = jukebox_config.songs_dir / target_filename
                    dst_audio.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_audio, dst_audio)

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

                    jukebox_config.data["songs"][new_id] = song
                    jukebox_config.data["md5Index"]["songs"][file_md5] = new_id
                    stats["songsAdded"] += 1
        
        # 第二步：导入动画
        for action_id, action in import_data.get("actions", {}).items():
            file_md5 = action.get("fileMd5", "")
            if not file_md5:
                src_file = extract_dir / action["file"]
                if src_file.exists():
                    file_md5 = calculate_md5(src_file)
                    action["fileMd5"] = file_md5

            existing_id = jukebox_config.data["md5Index"]["actions"].get(file_md5) if file_md5 else None

            if existing_id:
                stats["actionsMerged"] += 1
            else:
                src_file = extract_dir / action["file"]
                if src_file.exists():
                    original_filename = Path(action["file"]).name
                    target_filename = get_unique_filename(jukebox_config.actions_dir, original_filename)
                    dst_file = jukebox_config.actions_dir / target_filename
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dst_file)

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
        
        # 第三步：导入MD5级别的绑定，转换为ID级别存储
        # 导入格式: bindings[songMd5][actionMd5] = {"offset": 0}
        # 存储格式: bindings[songId][actionId] = {"offset": 0}
        for song_md5, action_bindings in import_data.get("bindings", {}).items():
            # 通过MD5查找本地歌曲ID
            song_id = jukebox_config.data["md5Index"]["songs"].get(song_md5)
            if not song_id:
                continue  # 本地没有这首歌曲，跳过绑定
            
            # 确保歌曲在绑定索引中
            if song_id not in jukebox_config.data["bindings"]:
                jukebox_config.data["bindings"][song_id] = {}
            
            for action_md5, binding_data in action_bindings.items():
                # 通过MD5查找本地动画ID
                action_id = jukebox_config.data["md5Index"]["actions"].get(action_md5)
                if not action_id:
                    continue  # 本地没有这个动画，跳过绑定
                
                # 如果绑定不存在，则添加（ID级别）
                if action_id not in jukebox_config.data["bindings"][song_id]:
                    jukebox_config.data["bindings"][song_id][action_id] = {
                        "offset": binding_data.get("offset", 0)
                    }
                    stats["bindingsAdded"] += 1
                    
                    # 自动设置默认动画
                    song = jukebox_config.data["songs"][song_id]
                    current_default = song.get("defaultAction", "")
                    
                    if not current_default:
                        song["defaultAction"] = action_id
                        logger.info(f"导入设置默认动画: {song_id} -> {action_id} (首次绑定)")
                    else:
                        # 检查当前默认动画是否存在
                        if current_default not in jukebox_config.data["actions"]:
                            song["defaultAction"] = action_id
                            logger.info(f"导入设置默认动画: {song_id} -> {action_id} (原默认动画不存在)")
        
        jukebox_config.save()
        
        logger.info(f"导入完成: {stats}")
        return {"success": True, "stats": stats}


@router.post("/pack-folder")
async def pack_folder(files: List[UploadFile] = File(...)):
    """将文件夹中的文件打包成 ZIP"""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # 保存所有文件到临时目录
        for file in files:
            file_path = temp_path / file.filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "wb") as f:
                shutil.copyfileobj(file.file, f)

        # 创建 ZIP 文件
        zip_path = temp_path / "packed.zip"
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_path in temp_path.rglob('*'):
                if file_path.is_file() and file_path.name != 'packed.zip':
                    arcname = file_path.relative_to(temp_path)
                    zf.write(file_path, arcname)

        return FileResponse(zip_path, media_type='application/zip', filename='packed.zip')
