# -*- coding: utf-8 -*-
"""
配置文件管理模块
负责管理配置文件的存储位置和迁移
"""
import sys
import os
import json
import shutil
import logging
import threading
from datetime import date
from copy import deepcopy
from pathlib import Path

from config import (
    APP_NAME,
    CONFIG_FILES,
    DEFAULT_CONFIG_DATA,
)
from config.prompts_chara import lanlan_prompt
from utils.api_config_loader import (
    get_core_api_profiles,
    get_assist_api_profiles,
    get_assist_api_key_fields,
)

# Workshop配置相关常量 - 将在ConfigManager实例化时使用self.workshop_dir


logger = logging.getLogger(__name__)


class ConfigManager:
    """配置文件管理器"""
    _agent_quota_lock = threading.Lock()
    _free_agent_daily_limit = 300 # 免费配额并非只在本地实施，本地计算是为了减少无效请求、节约网络带宽。
    
    def __init__(self, app_name=None):
        """
        初始化配置管理器
        
        Args:
            app_name: 应用名称，默认使用配置中的 APP_NAME
        """
        self.app_name = app_name if app_name is not None else APP_NAME
        # 检测是否在子进程中，子进程静默初始化（通过 main_server.py 设置的环境变量）
        self._verbose = '_NEKO_MAIN_SERVER_INITIALIZED' not in os.environ
        self.docs_dir = self._get_documents_directory()
        self.app_docs_dir = self.docs_dir / self.app_name
        self.config_dir = self.app_docs_dir / "config"
        self.memory_dir = self.app_docs_dir / "memory"
        self.live2d_dir = self.app_docs_dir / "live2d"
        # VRM模型存储在用户文档目录下（与Live2D保持一致）
        self.vrm_dir = self.app_docs_dir / "vrm"
        self.vrm_animation_dir = self.vrm_dir / "animation"  # VRMA动画文件目录
        self.workshop_dir = self.app_docs_dir / "workshop"
        self.chara_dir = self.app_docs_dir / "character_cards"

        self.project_config_dir = self._get_project_config_directory()
        self.project_memory_dir = self._get_project_memory_directory()
    
    def _log(self, msg):
        """仅在主进程中打印调试信息"""
        if self._verbose:
            print(msg, file=sys.stderr)
    
    def _get_documents_directory(self):
        """获取用户文档目录（使用系统API）"""
        candidates = []  # 候选路径列表
        
        if sys.platform == "win32":
            # Windows: 使用系统API获取真正的"我的文档"路径
            try:
                import ctypes
                from ctypes import windll, wintypes
                
                # 使用SHGetFolderPath获取我的文档路径
                CSIDL_PERSONAL = 5  # My Documents
                SHGFP_TYPE_CURRENT = 0
                
                buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
                windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf)
                api_path = Path(buf.value)
                self._log(f"[ConfigManager] API returned path: {api_path}")
                candidates.append(api_path)
                
                # 如果API返回的路径看起来不对（包含特殊字符但不存在），尝试查找同盘符下可能的替代路径
                if not api_path.exists() and api_path.drive:
                    # 获取盘符
                    drive = api_path.drive
                    # 尝试在同一盘符下查找常见的文档文件夹名
                    possible_names = ["文档", "Documents", "My Documents"]
                    for name in possible_names:
                        alt_path = Path(drive) / name
                        if alt_path.exists():
                            self._log(f"[ConfigManager] Found alternative path on same drive: {alt_path}")
                            candidates.append(alt_path)
            except Exception as e:
                print(f"Warning: Failed to get Documents path via API: {e}", file=sys.stderr)
            
            # 降级：尝试从注册表读取
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
                )
                reg_path_str = winreg.QueryValueEx(key, "Personal")[0]
                winreg.CloseKey(key)
                
                # 展开环境变量
                reg_path = Path(os.path.expandvars(reg_path_str))
                self._log(f"[ConfigManager] Registry returned path: {reg_path}")
                
                # 如果注册表路径不存在，尝试在同一盘符下查找
                if not reg_path.exists() and reg_path.drive:
                    drive = reg_path.drive
                    # 列出盘符下的所有文件夹，查找可能的文档文件夹
                    try:
                        drive_path = Path(drive + "\\")
                        if drive_path.exists():
                            for item in drive_path.iterdir():
                                if item.is_dir() and item.name.lower() in ["documents", "文档", "my documents"]:
                                    self._log(f"[ConfigManager] Found documents folder on drive: {item}")
                                    candidates.append(item)
                    except Exception:
                        pass
                
                candidates.append(reg_path)
            except Exception as e:
                print(f"Warning: Failed to get Documents path from registry: {e}", file=sys.stderr)
            
            # 添加默认路径候选
            candidates.append(Path.home() / "Documents")
            candidates.append(Path.home() / "文档")
            
            # 如果都不行，使用exe所在目录（打包后）或当前目录（开发时）
            if getattr(sys, 'frozen', False):
                candidates.append(Path(sys.executable).parent)
            else:
                candidates.append(Path.cwd())
        
        elif sys.platform == "darwin":
            # macOS: 使用标准路径
            candidates.append(Path.home() / "Documents")
            candidates.append(Path.cwd())
        else:
            # Linux: 尝试使用XDG
            xdg_docs = os.getenv('XDG_DOCUMENTS_DIR')
            if xdg_docs:
                candidates.append(Path(xdg_docs))
            candidates.append(Path.home() / "Documents")
            candidates.append(Path.cwd())
        
        # 遍历候选路径，找到第一个真正可访问且可写的路径
        for docs_dir in candidates:
            try:
                # 检查路径是否存在且可访问
                if docs_dir.exists() and os.access(str(docs_dir), os.R_OK | os.W_OK):
                    # 尝试在该目录创建测试文件，确保真的可写
                    test_path = docs_dir / ".test_neko_write"
                    try:
                        test_path.touch()
                        test_path.unlink()
                        self._log(f"[ConfigManager] ✓ Using documents directory: {docs_dir}")
                        return docs_dir
                    except Exception as e:
                        self._log(f"[ConfigManager] Path exists but not writable: {docs_dir} - {e}")
                        continue
                
                # 如果路径不存在，尝试创建（测试是否可写）
                if not docs_dir.exists():
                    # 分步创建父目录
                    dirs_to_create = []
                    current = docs_dir
                    while current and not current.exists():
                        dirs_to_create.append(current)
                        current = current.parent
                        if current == current.parent:  # 到达根目录
                            break
                    
                    # 从最顶层开始创建
                    for dir_path in reversed(dirs_to_create):
                        if not dir_path.exists():
                            dir_path.mkdir(exist_ok=True)
                    
                    # 测试可写性
                    test_path = docs_dir / ".test_neko_write"
                    test_path.touch()
                    test_path.unlink()
                    self._log(f"[ConfigManager] ✓ Using documents directory (created): {docs_dir}")
                    return docs_dir
            except Exception as e:
                self._log(f"[ConfigManager] Failed to use path {docs_dir}: {e}")
                continue
        
        # 如果所有候选都失败，返回当前目录
        fallback = Path.cwd()
        self._log(f"[ConfigManager] ⚠ All document directories failed, using fallback: {fallback}")
        return fallback
    
    def _get_project_root(self):
        """获取项目根目录（私有方法）"""
        if getattr(sys, 'frozen', False):
            # 如果是打包后的exe（PyInstaller）
            if hasattr(sys, '_MEIPASS'):
                # 单文件模式：使用临时解压目录
                return Path(sys._MEIPASS)
            else:
                # 多文件模式：使用 exe 同目录
                return Path(sys.executable).parent
        else:
            # 开发模式：使用当前工作目录
            return Path.cwd()
    
    @property
    def project_root(self):
        """获取项目根目录（公共属性）"""
        return self._get_project_root()
    
    def _get_project_config_directory(self):
        """获取项目的config目录"""
        return self._get_project_root() / "config"
    
    def _get_project_memory_directory(self):
        """获取项目的memory/store目录"""
        if getattr(sys, 'frozen', False):
            # 如果是打包后的exe（PyInstaller）
            # 单文件模式：数据文件在 _MEIPASS 临时目录
            # 多文件模式：数据文件在 exe 同目录
            if hasattr(sys, '_MEIPASS'):
                # 单文件模式：使用临时解压目录
                app_dir = Path(sys._MEIPASS)
            else:
                # 多文件模式：使用 exe 同目录
                app_dir = Path(sys.executable).parent
        else:
            # 如果是脚本运行
            app_dir = Path.cwd()
        
        return app_dir / "memory" / "store"
    
    def _ensure_app_docs_directory(self):
        """确保应用文档目录存在（N.E.K.O目录本身）"""
        try:
            # 先确保父目录（docs_dir）存在
            if not self.docs_dir.exists():
                print(f"Warning: Documents directory does not exist: {self.docs_dir}", file=sys.stderr)
                print("Warning: Attempting to create documents directory...", file=sys.stderr)
                try:
                    # 尝试创建父目录（可能需要创建多级）
                    dirs_to_create = []
                    current = self.docs_dir
                    while current and not current.exists():
                        dirs_to_create.append(current)
                        current = current.parent
                        # 防止无限循环，到达根目录就停止
                        if current == current.parent:
                            break
                    
                    # 从最顶层开始创建目录
                    for dir_path in reversed(dirs_to_create):
                        if not dir_path.exists():
                            print(f"Creating directory: {dir_path}", file=sys.stderr)
                            dir_path.mkdir(exist_ok=True)
                except Exception as e2:
                    print(f"Warning: Failed to create documents directory: {e2}", file=sys.stderr)
                    return False
            
            # 创建应用目录
            if not self.app_docs_dir.exists():
                print(f"Creating app directory: {self.app_docs_dir}", file=sys.stderr)
                self.app_docs_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create app directory {self.app_docs_dir}: {e}", file=sys.stderr)
            return False
    
    def ensure_config_directory(self):
        """确保我的文档下的config目录存在"""
        try:
            # 先确保app_docs_dir存在
            if not self._ensure_app_docs_directory():
                return False
            
            self.config_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create config directory: {e}", file=sys.stderr)
            return False
    
    def ensure_memory_directory(self):
        """确保我的文档下的memory目录存在"""
        try:
            # 先确保app_docs_dir存在
            if not self._ensure_app_docs_directory():
                return False
            
            self.memory_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create memory directory: {e}", file=sys.stderr)
            return False
    
    def ensure_live2d_directory(self):
        """确保我的文档下的live2d目录存在"""
        try:
            # 先确保app_docs_dir存在
            if not self._ensure_app_docs_directory():
                return False
            
            self.live2d_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create live2d directory: {e}", file=sys.stderr)
            return False
        
    def ensure_vrm_directory(self):
        """确保用户文档目录下的vrm目录和animation子目录存在"""
        try:
            # 先确保app_docs_dir存在
            if not self._ensure_app_docs_directory():
                return False
            # 创建vrm目录
            self.vrm_dir.mkdir(parents=True, exist_ok=True)
            # 创建animation子目录
            self.vrm_animation_dir.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create vrm directory: {e}", file=sys.stderr)
            return False
        
    def ensure_chara_directory(self):
        """确保我的文档下的character_cards目录存在"""
        try:
            # 先确保app_docs_dir存在
            if not self._ensure_app_docs_directory():
                return False
            
            self.chara_dir.mkdir(exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create character_cards directory: {e}", file=sys.stderr)
            return False
    
    def get_config_path(self, filename):
        """
        获取配置文件路径
        
        优先级：
        1. 我的文档/{APP_NAME}/config/
        2. 项目目录/config/
        
        Args:
            filename: 配置文件名
            
        Returns:
            Path: 配置文件路径
        """
        # 首选：我的文档下的配置
        docs_config_path = self.config_dir / filename
        if docs_config_path.exists():
            return docs_config_path
        
        # 备选：项目目录下的配置
        project_config_path = self.project_config_dir / filename
        if project_config_path.exists():
            return project_config_path
        
        # 都不存在，返回我的文档路径（用于创建新文件）
        return docs_config_path
    
    def _get_localized_characters_source(self):
        """根据用户语言获取本地化的 characters.json 源文件路径。
        
        Returns:
            Path | None: 本地化文件路径，如果无法检测语言或文件不存在则返回 None（回退到默认）
        """
        try:
            from utils.language_utils import _get_steam_language, _get_system_language, normalize_language_code
            
            # 优先使用 Steam 语言，其次系统语言
            raw_lang = _get_steam_language()
            if not raw_lang:
                raw_lang = _get_system_language()
            if not raw_lang:
                return None
            
            lang = normalize_language_code(raw_lang, format='full')
        except Exception as e:
            self._log(f"[ConfigManager] Failed to detect language for characters config: {e}")
            return None
        
        if not lang:
            return None
        
        # 映射语言代码到文件后缀
        lang_lower = lang.lower()
        if lang_lower in ('zh-cn', 'zh'):
            suffix = 'zh-CN'
        elif 'tw' in lang_lower or 'hk' in lang_lower:
            suffix = 'zh-TW'
        elif lang_lower.startswith('ja'):
            suffix = 'ja'
        elif lang_lower.startswith('en'):
            suffix = 'en'
        elif lang_lower.startswith('ko'):
            suffix = 'ko'
        else:
            # 未知语言，回退
            return None
        
        localized_path = self.project_config_dir / f"characters.{suffix}.json"
        return localized_path if localized_path.exists() else None
    
    def migrate_config_files(self):
        """
        迁移配置文件到我的文档
        
        策略：
        1. 检查我的文档下的config文件夹，没有就创建
        2. 对于每个配置文件：
           - 如果我的文档下有，跳过
           - 如果我的文档下没有：
             - characters.json: 根据语言选择本地化版本，回退到默认
             - 其他文件: 从项目config复制
           - 如果都没有，不做处理（后续会创建默认值）
        """
        # 确保目录存在
        if not self.ensure_config_directory():
            print("Warning: Cannot create config directory, using project config", file=sys.stderr)
            return
        
        # 显示项目配置目录位置（调试用）
        self._log(f"[ConfigManager] Project config directory: {self.project_config_dir}")
        self._log(f"[ConfigManager] User config directory: {self.config_dir}")
        
        # 迁移每个配置文件
        for filename in CONFIG_FILES:
            docs_config_path = self.config_dir / filename
            project_config_path = self.project_config_dir / filename
            
            # 如果我的文档下已有，跳过
            if docs_config_path.exists():
                self._log(f"[ConfigManager] Config already exists: {filename}")
                continue
            
            # 对 characters.json 特殊处理：根据语言选择本地化版本
            if filename == 'characters.json':
                lang_source = self._get_localized_characters_source()
                if lang_source:
                    try:
                        shutil.copy2(lang_source, docs_config_path)
                        self._log(f"[ConfigManager] ✓ Migrated localized config: {lang_source.name} -> {docs_config_path}")
                        continue
                    except Exception as e:
                        self._log(f"Warning: Failed to migrate localized {lang_source.name}: {e}")
                        # 继续走默认拷贝逻辑
            
            # 如果项目config下有，复制过去
            if project_config_path.exists():
                try:
                    shutil.copy2(project_config_path, docs_config_path)
                    self._log(f"[ConfigManager] ✓ Migrated config: {filename} -> {docs_config_path}")
                except Exception as e:
                    self._log(f"Warning: Failed to migrate {filename}: {e}")
            else:
                if filename in DEFAULT_CONFIG_DATA:
                    self._log(f"[ConfigManager] ~ Using in-memory default for {filename}")
                else:
                    self._log(f"[ConfigManager] ✗ Source config not found: {project_config_path}")
    
    def migrate_memory_files(self):
        """
        迁移记忆文件到我的文档
        
        策略：
        1. 检查我的文档下的memory文件夹，没有就创建
        2. 迁移所有记忆文件和目录
        """
        # 确保目录存在
        if not self.ensure_memory_directory():
            self._log("Warning: Cannot create memory directory, using project memory")
            return
        
        # 如果项目memory/store目录不存在，跳过
        if not self.project_memory_dir.exists():
            return
        
        # 迁移所有记忆文件
        try:
            for item in self.project_memory_dir.iterdir():
                dest_path = self.memory_dir / item.name
                
                # 如果目标已存在，跳过
                if dest_path.exists():
                    continue
                
                # 复制文件或目录
                if item.is_file():
                    shutil.copy2(item, dest_path)
                    print(f"Migrated memory file: {item.name}")
                elif item.is_dir():
                    shutil.copytree(item, dest_path)
                    print(f"Migrated memory directory: {item.name}")
        except Exception as e:
            print(f"Warning: Failed to migrate memory files: {e}", file=sys.stderr)
    
    # --- Character configuration helpers ---

    def get_default_characters(self):
        """获取默认角色配置数据（根据Steam语言本地化内容值）"""
        from config import get_localized_default_characters
        return get_localized_default_characters()

    def load_characters(self, character_json_path=None):
        """加载角色配置"""
        if character_json_path is None:
            character_json_path = str(self.get_config_path('characters.json'))

        try:
            with open(character_json_path, 'r', encoding='utf-8') as f:
                character_data = json.load(f)
        except FileNotFoundError:
            logger.info("未找到猫娘配置文件 %s，使用默认配置。", character_json_path)
            character_data = self.get_default_characters()
        except Exception as e:
            logger.error("读取猫娘配置文件出错: %s，使用默认人设。", e)
            character_data = self.get_default_characters()
        return character_data

    def save_characters(self, data, character_json_path=None):
        """保存角色配置"""
        if character_json_path is None:
            character_json_path = str(self.get_config_path('characters.json'))

        # 确保config目录存在
        self.ensure_config_directory()

        with open(character_json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # --- Voice storage helpers ---

    def load_voice_storage(self):
        """加载音色配置存储"""
        try:
            return self.load_json_config('voice_storage.json', default_value=deepcopy(DEFAULT_CONFIG_DATA['voice_storage.json']))
        except Exception as e:
            logger.error("加载音色配置失败: %s", e)
            return {}

    def save_voice_storage(self, data):
        """保存音色配置存储"""
        try:
            self.save_json_config('voice_storage.json', data)
        except Exception as e:
            logger.error("保存音色配置失败: %s", e)
            raise

    def get_voices_for_current_api(self):
        """获取当前 AUDIO_API_KEY 对应的所有音色"""
        core_config = self.get_core_config()
        audio_api_key = core_config.get('AUDIO_API_KEY', '')

        if not audio_api_key:
            logger.warning("未配置 AUDIO_API_KEY")
            return {}

        voice_storage = self.load_voice_storage()
        all_voices = voice_storage.get(audio_api_key, {})
        # 过滤掉以 "cosyvoice-v2" 开头的旧版音色ID
        return {k: v for k, v in all_voices.items() if not k.startswith("cosyvoice-v2")}

    def save_voice_for_current_api(self, voice_id, voice_data):
        """为当前 AUDIO_API_KEY 保存音色"""
        core_config = self.get_core_config()
        audio_api_key = core_config.get('AUDIO_API_KEY', '')

        if not audio_api_key:
            raise ValueError("未配置 AUDIO_API_KEY")

        voice_storage = self.load_voice_storage()
        if audio_api_key not in voice_storage:
            voice_storage[audio_api_key] = {}

        voice_storage[audio_api_key][voice_id] = voice_data
        self.save_voice_storage(voice_storage)

    def delete_voice_for_current_api(self, voice_id):
        """删除当前 AUDIO_API_KEY 下的指定音色"""
        core_config = self.get_core_config()
        audio_api_key = core_config.get('AUDIO_API_KEY', '')

        if not audio_api_key:
            raise ValueError("未配置 AUDIO_API_KEY")

        voice_storage = self.load_voice_storage()
        if audio_api_key not in voice_storage:
            return False

        if voice_id in voice_storage[audio_api_key]:
            del voice_storage[audio_api_key][voice_id]
            self.save_voice_storage(voice_storage)
            return True
        return False

    def validate_voice_id(self, voice_id):
        """校验 voice_id 是否在当前 AUDIO_API_KEY 下有效"""
        if not voice_id:
            return True

        # 自动忽略以 "cosyvoice-v2" 开头的旧版音色ID
        if voice_id.startswith("cosyvoice-v2"):
            return False

        voices = self.get_voices_for_current_api()
        if voice_id in voices:
            return True

        # 免费预设音色允许豁免保存校验，运行时再由 core.py 按当前线路动态判断可用性
        from utils.api_config_loader import get_free_voices
        free_voices = get_free_voices()
        if voice_id in free_voices.values():
            return True

        return False

    def cleanup_invalid_voice_ids(self):
        """清理 characters.json 中无效的 voice_id"""
        character_data = self.load_characters()
        voices = self.get_voices_for_current_api()
        
        # 免费预设音色也是有效的，不应被清理
        from utils.api_config_loader import get_free_voices
        free_voice_ids = set(get_free_voices().values())
        
        cleaned_count = 0

        catgirls = character_data.get('猫娘', {})
        for name, config in catgirls.items():
            voice_id = config.get('voice_id', '')
            if voice_id and voice_id not in voices and voice_id not in free_voice_ids:
                logger.warning(
                    "猫娘 '%s' 的 voice_id '%s' 在当前 API 的 voice_storage 中不存在，已清除",
                    name,
                    voice_id,
                )
                config['voice_id'] = ''
                cleaned_count += 1

        if cleaned_count > 0:
            self.save_characters(character_data)
            logger.info("已清理 %d 个无效的 voice_id 引用", cleaned_count)

        return cleaned_count

    # --- Character metadata helpers ---

    def get_character_data(self):
        """获取角色基础数据及相关路径"""
        character_data = self.load_characters()
        defaults = self.get_default_characters()

        character_data.setdefault('主人', deepcopy(defaults['主人']))
        character_data.setdefault('猫娘', deepcopy(defaults['猫娘']))

        master_basic_config = character_data.get('主人', {})
        master_name = master_basic_config.get('档案名', defaults['主人']['档案名'])

        catgirl_data = character_data.get('猫娘') or deepcopy(defaults['猫娘'])
        catgirl_names = list(catgirl_data.keys())

        current_catgirl = character_data.get('当前猫娘', '')
        if current_catgirl and current_catgirl in catgirl_names:
            her_name = current_catgirl
        else:
            her_name = catgirl_names[0] if catgirl_names else ''
            if her_name and current_catgirl != her_name:
                logger.info(
                    "当前猫娘配置无效 ('%s')，已自动切换到 '%s'",
                    current_catgirl,
                    her_name,
                )
                character_data['当前猫娘'] = her_name
                self.save_characters(character_data)

        name_mapping = {'human': master_name, 'system': "SYSTEM_MESSAGE"}
        lanlan_prompt_map = {}
        for name in catgirl_names:
            prompt_value = catgirl_data.get(name, {}).get('system_prompt', lanlan_prompt)
            lanlan_prompt_map[name] = prompt_value

        memory_base = str(self.memory_dir)
        semantic_store = {name: f'{memory_base}/semantic_memory_{name}' for name in catgirl_names}
        time_store = {name: f'{memory_base}/time_indexed_{name}' for name in catgirl_names}
        setting_store = {name: f'{memory_base}/settings_{name}.json' for name in catgirl_names}
        recent_log = {name: f'{memory_base}/recent_{name}.json' for name in catgirl_names}

        return (
            master_name,
            her_name,
            master_basic_config,
            catgirl_data,
            name_mapping,
            lanlan_prompt_map,
            semantic_store,
            time_store,
            setting_store,
            recent_log,
        )

    # --- Core config helpers ---

    # Cache for region check to avoid repeated calls (None = not checked, True/False = result)
    _region_cache = None
    
    def _check_non_mainland(self) -> bool:
        """Check if user is non-mainland China (cached, lazy evaluation)."""
        if ConfigManager._region_cache is not None:
            return ConfigManager._region_cache
        
        try:
            from main_routers.shared_state import get_steamworks
            steamworks = get_steamworks()
            
            if steamworks is None:
                return False  # Don't cache, retry next time
            
            ip_country = steamworks.Utils.GetIPCountry()
            if isinstance(ip_country, bytes):
                ip_country = ip_country.decode('utf-8')
            
            result = (ip_country.upper() != 'CN') if ip_country else False
            
            print(f"[GeoIP] _check_non_mainland: country={ip_country}, non_mainland={result}", file=sys.stderr)
            
            ConfigManager._region_cache = result
            return result
            
        except ImportError:
            return False  # Don't cache, module not available yet
        except Exception as e:
            print(f"[GeoIP] _check_non_mainland exception: {e}", file=sys.stderr)
            return False
    
    def _adjust_free_api_url(self, url: str, is_free: bool) -> str:
        """Internal URL adjustment for free API users based on region."""
        if not url or 'lanlan.tech' not in url:
            return url
        
        try:
            if self._check_non_mainland():
                return url.replace('lanlan.tech', 'lanlan.app')
        except Exception:
            pass
        
        return url

    def get_core_config(self):
        """动态读取核心配置"""
        # 从 config 模块导入所有默认配置值
        from config import (
            DEFAULT_CORE_API_KEY,
            DEFAULT_AUDIO_API_KEY,
            DEFAULT_OPENROUTER_API_KEY,
            DEFAULT_MCP_ROUTER_API_KEY,
            DEFAULT_CORE_URL,
            DEFAULT_CORE_MODEL,
            DEFAULT_OPENROUTER_URL,
            DEFAULT_CONVERSATION_MODEL,
            DEFAULT_SUMMARY_MODEL,
            DEFAULT_CORRECTION_MODEL,
            DEFAULT_EMOTION_MODEL,
            DEFAULT_VISION_MODEL,
            DEFAULT_REALTIME_MODEL,
            DEFAULT_TTS_MODEL,
            DEFAULT_AGENT_MODEL,
            DEFAULT_CONVERSATION_MODEL_URL,
            DEFAULT_CONVERSATION_MODEL_API_KEY,
            DEFAULT_SUMMARY_MODEL_URL,
            DEFAULT_SUMMARY_MODEL_API_KEY,
            DEFAULT_CORRECTION_MODEL_URL,
            DEFAULT_CORRECTION_MODEL_API_KEY,
            DEFAULT_EMOTION_MODEL_URL,
            DEFAULT_EMOTION_MODEL_API_KEY,
            DEFAULT_VISION_MODEL_URL,
            DEFAULT_VISION_MODEL_API_KEY,
            DEFAULT_AGENT_MODEL_URL,
            DEFAULT_AGENT_MODEL_API_KEY,
            DEFAULT_REALTIME_MODEL_URL,
            DEFAULT_REALTIME_MODEL_API_KEY,
            DEFAULT_TTS_MODEL_URL,
            DEFAULT_TTS_MODEL_API_KEY,
        )

        config = {
            'CORE_API_KEY': DEFAULT_CORE_API_KEY,
            'AUDIO_API_KEY': DEFAULT_AUDIO_API_KEY,
            'OPENROUTER_API_KEY': DEFAULT_OPENROUTER_API_KEY,
            'MCP_ROUTER_API_KEY': DEFAULT_MCP_ROUTER_API_KEY,
            'CORE_URL': DEFAULT_CORE_URL,
            'CORE_MODEL': DEFAULT_CORE_MODEL,
            'CORE_API_TYPE': 'qwen',
            'OPENROUTER_URL': DEFAULT_OPENROUTER_URL,
            'CONVERSATION_MODEL': DEFAULT_CONVERSATION_MODEL,
            'SUMMARY_MODEL': DEFAULT_SUMMARY_MODEL,
            'CORRECTION_MODEL': DEFAULT_CORRECTION_MODEL,
            'EMOTION_MODEL': DEFAULT_EMOTION_MODEL,
            'ASSIST_API_KEY_QWEN': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_OPENAI': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_GLM': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_STEP': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_SILICON': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_GEMINI': DEFAULT_CORE_API_KEY,
            'IS_FREE_VERSION': False,
            'VISION_MODEL': DEFAULT_VISION_MODEL,
            'AGENT_MODEL': DEFAULT_AGENT_MODEL,
            'REALTIME_MODEL': DEFAULT_REALTIME_MODEL,
            'TTS_MODEL': DEFAULT_TTS_MODEL,
            'CONVERSATION_MODEL_URL': DEFAULT_CONVERSATION_MODEL_URL,
            'CONVERSATION_MODEL_API_KEY': DEFAULT_CONVERSATION_MODEL_API_KEY,
            'SUMMARY_MODEL_URL': DEFAULT_SUMMARY_MODEL_URL,
            'SUMMARY_MODEL_API_KEY': DEFAULT_SUMMARY_MODEL_API_KEY,
            'CORRECTION_MODEL_URL': DEFAULT_CORRECTION_MODEL_URL,
            'CORRECTION_MODEL_API_KEY': DEFAULT_CORRECTION_MODEL_API_KEY,
            'EMOTION_MODEL_URL': DEFAULT_EMOTION_MODEL_URL,
            'EMOTION_MODEL_API_KEY': DEFAULT_EMOTION_MODEL_API_KEY,
            'VISION_MODEL_URL': DEFAULT_VISION_MODEL_URL,
            'VISION_MODEL_API_KEY': DEFAULT_VISION_MODEL_API_KEY,
            'AGENT_MODEL_URL': DEFAULT_AGENT_MODEL_URL,
            'AGENT_MODEL_API_KEY': DEFAULT_AGENT_MODEL_API_KEY,
            'REALTIME_MODEL_URL': DEFAULT_REALTIME_MODEL_URL,
            'REALTIME_MODEL_API_KEY': DEFAULT_REALTIME_MODEL_API_KEY,
            'TTS_MODEL_URL': DEFAULT_TTS_MODEL_URL,
            'TTS_MODEL_API_KEY': DEFAULT_TTS_MODEL_API_KEY,
        }

        core_cfg = deepcopy(DEFAULT_CONFIG_DATA['core_config.json'])

        try:
            with open(str(self.get_config_path('core_config.json')), 'r', encoding='utf-8') as f:
                file_data = json.load(f)
            if isinstance(file_data, dict):
                core_cfg.update(file_data)
            else:
                logger.warning("core_config.json 格式异常，使用默认配置。")

        except FileNotFoundError:
            logger.info("未找到 core_config.json，使用默认配置。")
        except Exception as e:
            logger.error("Error parsing Core API Key: %s", e)
        finally:
            if not isinstance(core_cfg, dict):
                core_cfg = deepcopy(DEFAULT_CONFIG_DATA['core_config.json'])

        # API Keys
        if core_cfg.get('coreApiKey'):
            config['CORE_API_KEY'] = core_cfg['coreApiKey']

        config['ASSIST_API_KEY_QWEN'] = core_cfg.get('assistApiKeyQwen', '') or config['CORE_API_KEY']
        config['ASSIST_API_KEY_OPENAI'] = core_cfg.get('assistApiKeyOpenai', '') or config['CORE_API_KEY']
        config['ASSIST_API_KEY_GLM'] = core_cfg.get('assistApiKeyGlm', '') or config['CORE_API_KEY']
        config['ASSIST_API_KEY_STEP'] = core_cfg.get('assistApiKeyStep', '') or config['CORE_API_KEY']
        config['ASSIST_API_KEY_SILICON'] = core_cfg.get('assistApiKeySilicon', '') or config['CORE_API_KEY']
        config['ASSIST_API_KEY_GEMINI'] = core_cfg.get('assistApiKeyGemini', '') or config['CORE_API_KEY']
        config['ASSIST_API_KEY_KIMI'] = core_cfg.get('assistApiKeyKimi', '') or config['CORE_API_KEY']

        if core_cfg.get('mcpToken'):
            config['MCP_ROUTER_API_KEY'] = core_cfg['mcpToken']

        core_api_profiles = get_core_api_profiles()
        assist_api_profiles = get_assist_api_profiles()
        assist_api_key_fields = get_assist_api_key_fields()

        # Core API profile
        core_api_value = core_cfg.get('coreApi') or config['CORE_API_TYPE']
        config['CORE_API_TYPE'] = core_api_value
        core_profile = core_api_profiles.get(core_api_value)
        if core_profile:
            config.update(core_profile)

        # Assist API profile
        assist_api_value = core_cfg.get('assistApi')
        if core_api_value == 'free':
            assist_api_value = 'free'
        if not assist_api_value:
            assist_api_value = 'qwen'

        config['assistApi'] = assist_api_value

        assist_profile = assist_api_profiles.get(assist_api_value)
        if not assist_profile and assist_api_value != 'qwen':
            logger.warning("未知的 assistApi '%s'，回退到 qwen。", assist_api_value)
            assist_api_value = 'qwen'
            config['assistApi'] = assist_api_value
            assist_profile = assist_api_profiles.get(assist_api_value)

        if assist_profile:
            config.update(assist_profile)
        # agent api 默认跟随辅助 API 的 agent_model，缺失时回退到 VISION_MODEL
        config['AGENT_MODEL'] = config.get('AGENT_MODEL') or config.get('VISION_MODEL', '')
        config['AGENT_MODEL_URL'] = config.get('AGENT_MODEL_URL') or config.get('VISION_MODEL_URL', '') or config.get('OPENROUTER_URL', '')
        config['AGENT_MODEL_URL'] = config['AGENT_MODEL_URL'].replace('lanlan.tech', 'lanlan.app') # TODO: 先放这里

        key_field = assist_api_key_fields.get(assist_api_value)
        derived_key = ''
        if key_field:
            derived_key = config.get(key_field, '')
            if derived_key:
                config['AUDIO_API_KEY'] = derived_key
                config['OPENROUTER_API_KEY'] = derived_key

        if not config['AUDIO_API_KEY']:
            config['AUDIO_API_KEY'] = config['CORE_API_KEY']
        if not config['OPENROUTER_API_KEY']:
            config['OPENROUTER_API_KEY'] = config['CORE_API_KEY']

        # Agent API Key 回退：未显式配置时跟随辅助 API Key
        if not config.get('AGENT_MODEL_API_KEY'):
            config['AGENT_MODEL_API_KEY'] = derived_key if derived_key else config.get('CORE_API_KEY', '')

        # 自定义API配置映射（使用大写下划线形式的内部键，且在未提供时保留已有默认值）
        enable_custom_api = core_cfg.get('enableCustomApi', False)
        config['ENABLE_CUSTOM_API'] = enable_custom_api
        
        # 只有在启用自定义API时才允许覆盖各模型相关字段
        if enable_custom_api:
            # 文本对话模型 模型自定义配置映射
            if core_cfg.get('conversationModelApiKey') is not None:
                config['CONVERSATION_MODEL_API_KEY'] = core_cfg.get('conversationModelApiKey', '') or config.get('CONVERSATION_MODEL_API_KEY', '')
            if core_cfg.get('conversationModelUrl') is not None:
                config['CONVERSATION_MODEL_URL'] = core_cfg.get('conversationModelUrl', '') or config.get('CONVERSATION_MODEL_URL', '')
            if core_cfg.get('conversationModelId') is not None:
                config['CONVERSATION_MODEL'] = core_cfg.get('conversationModelId', '') or config.get('CONVERSATION_MODEL', '')
            
            # Summary（摘要）模型自定义配置映射
            if core_cfg.get('summaryModelApiKey') is not None:
                config['SUMMARY_MODEL_API_KEY'] = core_cfg.get('summaryModelApiKey', '') or config.get('SUMMARY_MODEL_API_KEY', '')
            if core_cfg.get('summaryModelUrl') is not None:
                config['SUMMARY_MODEL_URL'] = core_cfg.get('summaryModelUrl', '') or config.get('SUMMARY_MODEL_URL', '')
            if core_cfg.get('summaryModelId') is not None:
                config['SUMMARY_MODEL'] = core_cfg.get('summaryModelId', '') or config.get('SUMMARY_MODEL', '')
            
            # Correction（纠错）模型自定义配置映射
            if core_cfg.get('correctionModelApiKey') is not None:
                config['CORRECTION_MODEL_API_KEY'] = core_cfg.get('correctionModelApiKey', '') or config.get('CORRECTION_MODEL_API_KEY', '')
            if core_cfg.get('correctionModelUrl') is not None:
                config['CORRECTION_MODEL_URL'] = core_cfg.get('correctionModelUrl', '') or config.get('CORRECTION_MODEL_URL', '')
            if core_cfg.get('correctionModelId') is not None:
                config['CORRECTION_MODEL'] = core_cfg.get('correctionModelId', '') or config.get('CORRECTION_MODEL', '')
            
            # Emotion（情感分析）模型自定义配置映射
            if core_cfg.get('emotionModelApiKey') is not None:
                config['EMOTION_MODEL_API_KEY'] = core_cfg.get('emotionModelApiKey', '') or config.get('EMOTION_MODEL_API_KEY', '')
            if core_cfg.get('emotionModelUrl') is not None:
                config['EMOTION_MODEL_URL'] = core_cfg.get('emotionModelUrl', '') or config.get('EMOTION_MODEL_URL', '')
            if core_cfg.get('emotionModelId') is not None:
                config['EMOTION_MODEL'] = core_cfg.get('emotionModelId', '') or config.get('EMOTION_MODEL', '')
            
            # Vision（视觉）模型自定义配置映射
            if core_cfg.get('visionModelApiKey') is not None:
                config['VISION_MODEL_API_KEY'] = core_cfg.get('visionModelApiKey', '') or config.get('VISION_MODEL_API_KEY', '')
            if core_cfg.get('visionModelUrl') is not None:
                config['VISION_MODEL_URL'] = core_cfg.get('visionModelUrl', '') or config.get('VISION_MODEL_URL', '')
            if core_cfg.get('visionModelId') is not None:
                config['VISION_MODEL'] = core_cfg.get('visionModelId', '') or config.get('VISION_MODEL', '')
            
            # Agent（智能体）模型自定义配置映射
            if core_cfg.get('agentModelApiKey') is not None:
                config['AGENT_MODEL_API_KEY'] = core_cfg.get('agentModelApiKey', '') or config.get('AGENT_MODEL_API_KEY', '')
            if core_cfg.get('agentModelUrl') is not None:
                config['AGENT_MODEL_URL'] = core_cfg.get('agentModelUrl', '') or config.get('AGENT_MODEL_URL', '')
            if core_cfg.get('agentModelId') is not None:
                config['AGENT_MODEL'] = core_cfg.get('agentModelId', '') or config.get('AGENT_MODEL', '')
            
            # Omni/Realtime（全模态/实时）模型自定义配置映射
            if core_cfg.get('omniModelApiKey') is not None:
                config['REALTIME_MODEL_API_KEY'] = core_cfg.get('omniModelApiKey', '') or config.get('REALTIME_MODEL_API_KEY', '')
            if core_cfg.get('omniModelUrl') is not None:
                config['REALTIME_MODEL_URL'] = core_cfg.get('omniModelUrl', '') or config.get('REALTIME_MODEL_URL', '')
            if core_cfg.get('omniModelId') is not None:
                config['REALTIME_MODEL'] = core_cfg.get('omniModelId', '') or config.get('REALTIME_MODEL', '')
            
            # TTS 自定义配置映射
            if core_cfg.get('ttsModelApiKey') is not None:
                config['TTS_MODEL_API_KEY'] = core_cfg.get('ttsModelApiKey', '') or config.get('TTS_MODEL_API_KEY', '')
            if core_cfg.get('ttsModelUrl') is not None:
                config['TTS_MODEL_URL'] = core_cfg.get('ttsModelUrl', '') or config.get('TTS_MODEL_URL', '')
            if core_cfg.get('ttsModelId') is not None:
                config['TTS_MODEL'] = core_cfg.get('ttsModelId', '') or config.get('TTS_MODEL', '')
            
            # TTS Voice ID 作为角色 voice_id 的回退
            if core_cfg.get('ttsVoiceId') is not None:
                config['TTS_VOICE_ID'] = core_cfg.get('ttsVoiceId', '')

        for key, value in config.items():
            if key.endswith('_URL') and isinstance(value, str):
                config[key] = self._adjust_free_api_url(value, True)

        return config

    def get_model_api_config(self, model_type: str) -> dict:
        """
        获取指定模型类型的 API 配置（自动处理自定义 API 优先级）
        
        Args:
            model_type: 模型类型，可选值：
                - 'summary': 摘要模型（回退到辅助API）
                - 'correction': 纠错模型（回退到辅助API）
                - 'emotion': 情感分析模型（回退到辅助API）
                - 'vision': 视觉模型（回退到辅助API）
                - 'realtime': 实时语音模型（回退到核心API）
                - 'tts_default': 默认TTS（回退到核心API，用于OmniOfflineClient）
                - 'tts_custom': 自定义TTS（回退到辅助API，用于voice_id场景）
                
        Returns:
            dict: 包含以下字段的配置：
                - 'model': 模型名称
                - 'api_key': API密钥
                - 'base_url': API端点URL
                - 'is_custom': 是否使用自定义API配置
        """
        core_config = self.get_core_config()
        enable_custom_api = core_config.get('ENABLE_CUSTOM_API', False)
        
        # 模型类型到配置字段的映射
        # fallback_type: 'assist' = 辅助API, 'core' = 核心API
        model_type_mapping = {
            'conversation': {
                'custom_model': 'CONVERSATION_MODEL',
                'custom_url': 'CONVERSATION_MODEL_URL',
                'custom_key': 'CONVERSATION_MODEL_API_KEY',
                'default_model': 'CONVERSATION_MODEL',
                'fallback_type': 'assist',
            },
            'summary': {
                'custom_model': 'SUMMARY_MODEL',
                'custom_url': 'SUMMARY_MODEL_URL',
                'custom_key': 'SUMMARY_MODEL_API_KEY',
                'default_model': 'SUMMARY_MODEL',
                'fallback_type': 'assist',
            },
            'correction': {
                'custom_model': 'CORRECTION_MODEL',
                'custom_url': 'CORRECTION_MODEL_URL',
                'custom_key': 'CORRECTION_MODEL_API_KEY',
                'default_model': 'CORRECTION_MODEL',
                'fallback_type': 'assist',
            },
            'emotion': {
                'custom_model': 'EMOTION_MODEL',
                'custom_url': 'EMOTION_MODEL_URL',
                'custom_key': 'EMOTION_MODEL_API_KEY',
                'default_model': 'EMOTION_MODEL',
                'fallback_type': 'assist',
            },
            'vision': {
                'custom_model': 'VISION_MODEL',
                'custom_url': 'VISION_MODEL_URL',
                'custom_key': 'VISION_MODEL_API_KEY',
                'default_model': 'VISION_MODEL',
                'fallback_type': 'assist',
            },
            'agent': {
                'custom_model': 'AGENT_MODEL',
                'custom_url': 'AGENT_MODEL_URL',
                'custom_key': 'AGENT_MODEL_API_KEY',
                'default_model': 'AGENT_MODEL',
                'fallback_type': 'assist',
            },
            'realtime': {
                'custom_model': 'REALTIME_MODEL',
                'custom_url': 'REALTIME_MODEL_URL',
                'custom_key': 'REALTIME_MODEL_API_KEY',
                'default_model': 'CORE_MODEL',
                'fallback_type': 'core',  # 实时模型回退到核心API
            },
            'tts_default': {
                'custom_model': 'TTS_MODEL',
                'custom_url': 'TTS_MODEL_URL',
                'custom_key': 'TTS_MODEL_API_KEY',
                'default_model': 'CORE_MODEL',
                'fallback_type': 'core',  # 默认TTS回退到核心API
            },
            'tts_custom': {
                'custom_model': 'TTS_MODEL',
                'custom_url': 'TTS_MODEL_URL',
                'custom_key': 'TTS_MODEL_API_KEY',
                'default_model': 'CORE_MODEL',
                'fallback_type': 'assist',  # 自定义TTS回退到辅助API
            },
        }
        
        if model_type not in model_type_mapping:
            raise ValueError(f"Unknown model_type: {model_type}. Valid types: {list(model_type_mapping.keys())}")
        
        mapping = model_type_mapping[model_type]
        
        # agent 不依赖 enable_custom_api 开关；其余模型遵循原逻辑
        if enable_custom_api or model_type == 'agent':
            custom_model = core_config.get(mapping['custom_model'], '')
            custom_url = core_config.get(mapping['custom_url'], '')
            custom_key = core_config.get(mapping['custom_key'], '')
            
            # 自定义配置完整时使用自定义配置
            if custom_model and custom_url:
                return {
                    'model': custom_model,
                    'api_key': custom_key,
                    'base_url': custom_url,
                    'is_custom': True,
                    # 对于 realtime 模型，自定义配置时 api_type 设为 'local'
                    # TODO: 后续完善 'local' 类型的具体实现（如本地推理服务等）
                    'api_type': 'local' if model_type == 'realtime' else None,
                }
        
        # 自定义音色(CosyVoice)的特殊回退逻辑：优先尝试用户保存的 Qwen Cosyvoice API，
        # 只有在缺少 Qwen Cosyvoice API 时才再回退到辅助 API（CosyVoice 目前是唯一支持 voice clone 的）
        if model_type == 'tts_custom':
            qwen_api_key = (core_config.get('ASSIST_API_KEY_QWEN') or '').strip()
            if qwen_api_key:
                qwen_profile = get_assist_api_profiles().get('qwen', {})
                return {
                    'model': core_config.get(mapping['default_model'], ''), # Placeholder only, will be overridden by the actual model
                    'api_key': qwen_api_key,
                    'base_url': qwen_profile.get('OPENROUTER_URL', core_config.get('OPENROUTER_URL', '')), # Placeholder only, will be overridden by the actual url
                    'is_custom': False,
                }

        # 根据 fallback_type 回退到不同的 API
        if mapping['fallback_type'] == 'core':
            # 回退到核心 API 配置
            return {
                'model': core_config.get(mapping['default_model'], ''),
                'api_key': core_config.get('CORE_API_KEY', ''),
                'base_url': core_config.get('CORE_URL', ''),
                'is_custom': False,
                # 对于 realtime 模型，回退到核心API时使用配置的 CORE_API_TYPE
                'api_type': core_config.get('CORE_API_TYPE', '') if model_type == 'realtime' else None,
            }
        else:
            # 回退到辅助 API 配置
            return {
                'model': core_config.get(mapping['default_model'], ''),
                'api_key': core_config.get('OPENROUTER_API_KEY', ''),
                'base_url': core_config.get('OPENROUTER_URL', ''),
                'is_custom': False,
            }

    def is_agent_api_ready(self) -> tuple[bool, list[str]]:
        """
        Agent 模式门槛检查：
        - 必须具备可用的 AGENT_MODEL(model/url/api_key)
        - free 版本允许使用但由前端提示风险
        """
        reasons = []
        core_config = self.get_core_config()
        is_free = bool(core_config.get('IS_FREE_VERSION'))
        agent_api = self.get_model_api_config('agent')
        if not (agent_api.get('model') or '').strip():
            reasons.append("Agent 模型未配置")
        if not (agent_api.get('base_url') or '').strip():
            reasons.append("Agent API URL 未配置")
        api_key = (agent_api.get('api_key') or '').strip()
        if not api_key:
            reasons.append("Agent API Key 未配置或不可用")
        elif api_key == 'free-access' and not is_free:
            reasons.append("Agent API Key 未配置或不可用")
        return len(reasons) == 0, reasons

    def is_free_version(self) -> bool:
        return bool(self.get_core_config().get('IS_FREE_VERSION'))

    def _get_agent_quota_path(self) -> Path:
        """本地 Agent 试用配额计数文件路径。"""
        return self.config_dir / "agent_quota.json"

    def consume_agent_daily_quota(self, source: str = "", units: int = 1) -> tuple[bool, dict]:
        """消费 Agent 模型每日配额（仅免费版生效）。配额并非只在本地实施，本地计算是为了减少无效请求、节约网络带宽。

        Returns:
            (ok, info)
            info:
              - limited: bool
              - date: YYYY-MM-DD
              - used: int
              - limit: int | None
              - remaining: int | None
              - source: str
        """
        if units <= 0:
            units = 1

        is_free = self.is_free_version()
        today = date.today().isoformat()
        limit = int(self._free_agent_daily_limit)

        if not is_free:
            return True, {
                "limited": False,
                "date": today,
                "used": 0,
                "limit": None,
                "remaining": None,
                "source": source or "",
            }

        self.ensure_config_directory()
        quota_path = self._get_agent_quota_path()

        with ConfigManager._agent_quota_lock:
            data = {"date": today, "used": 0}
            try:
                if quota_path.exists():
                    with open(quota_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        loaded_date = str(loaded.get("date") or today)
                        loaded_used = int(loaded.get("used", 0) or 0)
                        if loaded_date == today:
                            data = {"date": today, "used": max(0, loaded_used)}
            except Exception:
                data = {"date": today, "used": 0}

            used = int(data.get("used", 0))
            if used + units > limit:
                return False, {
                    "limited": True,
                    "date": today,
                    "used": used,
                    "limit": limit,
                    "remaining": max(0, limit - used),
                    "source": source or "",
                }

            used += units
            data = {"date": today, "used": used}
            try:
                with open(quota_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning("保存 Agent 配额计数失败: %s", e)

            return True, {
                "limited": True,
                "date": today,
                "used": used,
                "limit": limit,
                "remaining": max(0, limit - used),
                "source": source or "",
            }

    def load_json_config(self, filename, default_value=None):
        """
        加载JSON配置文件
        
        Args:
            filename: 配置文件名
            default_value: 默认值（如果文件不存在）
            
        Returns:
            dict: 配置内容
        """
        config_path = self.get_config_path(filename)
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            if default_value is not None:
                return deepcopy(default_value)
            raise
        except Exception as e:
            print(f"Error loading {filename}: {e}", file=sys.stderr)
            if default_value is not None:
                return deepcopy(default_value)
            raise
    
    def save_json_config(self, filename, data):
        """
        保存JSON配置文件
        
        Args:
            filename: 配置文件名
            data: 要保存的数据
        """
        # 确保目录存在
        self.ensure_config_directory()
        
        config_path = self.config_dir / filename
        
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()  # 强制刷新缓冲区
        except Exception as e:
            print(f"Error saving {filename}: {e}", file=sys.stderr)
            raise
    
    def get_memory_path(self, filename):
        """
        获取记忆文件路径
        
        优先级：
        1. 我的文档/{APP_NAME}/memory/
        2. 项目目录/memory/store/
        
        Args:
            filename: 记忆文件名
            
        Returns:
            Path: 记忆文件路径
        """
        # 首选：我的文档下的记忆
        docs_memory_path = self.memory_dir / filename
        if docs_memory_path.exists():
            return docs_memory_path
        
        # 备选：项目目录下的记忆
        project_memory_path = self.project_memory_dir / filename
        if project_memory_path.exists():
            return project_memory_path
        
        # 都不存在，返回我的文档路径（用于创建新文件）
        return docs_memory_path
    
    def get_config_info(self):
        """获取配置目录信息"""
        return {
            "documents_dir": str(self.docs_dir),
            "app_dir": str(self.app_docs_dir),
            "config_dir": str(self.config_dir),
            "memory_dir": str(self.memory_dir),
            "live2d_dir": str(self.live2d_dir),
            "workshop_dir": str(self.workshop_dir),
            "chara_dir": str(self.chara_dir),
            "project_config_dir": str(self.project_config_dir),
            "project_memory_dir": str(self.project_memory_dir),
            "config_files": {
                filename: str(self.get_config_path(filename))
                for filename in CONFIG_FILES
            }
        }
    
    def get_workshop_config_path(self):
        """
        获取workshop配置文件路径
        
        Returns:
            str: workshop配置文件的绝对路径
        """
        return str(self.get_config_path('workshop_config.json'))

    def _normalize_workshop_folder_path(self, folder_path):
        """标准化 workshop 目录路径，失败时返回 None。"""
        if not isinstance(folder_path, str):
            return None

        path_str = folder_path.strip()
        if not path_str:
            return None

        try:
            # 与 workshop_utils 保持一致：相对路径按用户目录解析
            if not os.path.isabs(path_str):
                path_str = os.path.join(os.path.expanduser('~'), path_str)
            return os.path.normpath(path_str)
        except Exception:
            return None

    def _cleanup_invalid_workshop_config_file(self, config_path):
        """
        检查并清理无效的 workshop 配置文件。

        判定规则：如果配置中任一路径字段存在但不是有效目录，则删除整个配置文件。
        """
        if not config_path.exists():
            return False

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
        except Exception as e:
            logger.warning(f"workshop配置文件损坏，准备删除: {config_path}, error={e}")
            try:
                config_path.unlink()
                return True
            except Exception as delete_error:
                logger.error(f"删除损坏workshop配置文件失败: {config_path}, error={delete_error}")
                return False

        if not isinstance(config_data, dict):
            logger.warning(f"workshop配置格式非法（非对象），准备删除: {config_path}")
            try:
                config_path.unlink()
                return True
            except Exception as delete_error:
                logger.error(f"删除非法workshop配置文件失败: {config_path}, error={delete_error}")
                return False

        path_keys = ("user_mod_folder", "WORKSHOP_PATH", "steam_workshop_path", "default_workshop_folder")
        for key in path_keys:
            if key not in config_data:
                continue

            normalized_path = self._normalize_workshop_folder_path(config_data.get(key))
            if not normalized_path or not os.path.isdir(normalized_path):
                logger.warning(
                    f"发现无效workshop路径，准备删除配置文件: {config_path}, "
                    f"field={key}, value={config_data.get(key)!r}"
                )
                try:
                    config_path.unlink()
                    return True
                except Exception as delete_error:
                    logger.error(f"删除无效workshop配置文件失败: {config_path}, error={delete_error}")
                    return False

        return False

    def _cleanup_invalid_workshop_configs(self):
        """同时检查文档目录和项目目录中的 workshop 配置并清理无效文件。"""
        candidates = (
            self.config_dir / "workshop_config.json",
            self.project_config_dir / "workshop_config.json",
        )
        for candidate in candidates:
            self._cleanup_invalid_workshop_config_file(candidate)
    
    def load_workshop_config(self):
        """
        加载workshop配置
        
        Returns:
            dict: workshop配置数据
        """
        # 兼容历史错误配置：无论配置位于文档目录还是软件目录，先做一次自愈清理
        self._cleanup_invalid_workshop_configs()

        config_path = self.get_workshop_config_path()
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    logger.info(f"成功加载workshop配置: {config}")
                    return config
            else:
                # 如果配置文件不存在，返回默认配置
                default_config = {
                    "default_workshop_folder": str(self.workshop_dir),
                    "auto_create_folder": True
                }
                logger.info(f"创建默认workshop配置: {default_config}")
                return default_config
        except Exception as e:
            error_msg = f"加载workshop配置失败: {e}"
            logger.error(error_msg)
            print(error_msg)
            # 使用默认配置
            return {
                "default_workshop_folder": str(self.workshop_dir),
                "auto_create_folder": True
            }
    
    def save_workshop_config(self, config_data):
        """
        保存workshop配置
        
        Args:
            config_data: 要保存的配置数据
        """
        config_path = self.get_workshop_config_path()
        try:
            # 确保配置目录存在
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            
            # 保存配置
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4, ensure_ascii=False)
            
            logger.info(f"成功保存workshop配置: {config_data}")
        except Exception as e:
            error_msg = f"保存workshop配置失败: {e}"
            logger.error(error_msg)
            print(error_msg)
            raise
    
    def save_workshop_path(self, workshop_path):
        """
        保存workshop根目录路径到配置文件
        
        Args:
            workshop_path: workshop根目录路径
        """
        config = self.load_workshop_config()
        config["WORKSHOP_PATH"] = workshop_path
        self.save_workshop_config(config)
        logger.info(f"已将workshop路径保存到配置文件: {workshop_path}")
    
    def get_workshop_path(self):
        """
        获取保存的workshop根目录路径
        
        Returns:
            str: workshop根目录路径
        """
        config = self.load_workshop_config()
        # 优先使用user_mod_folder，然后是WORKSHOP_PATH，然后是default_workshop_folder，最后使用self.workshop_dir
        return config.get("user_mod_folder", config.get("WORKSHOP_PATH", config.get("default_workshop_folder", str(self.workshop_dir))))


# 全局配置管理器实例
_config_manager = None


def get_config_manager(app_name=None):
    """获取配置管理器单例，默认使用配置中的 APP_NAME"""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager(app_name)
        # 初始化时自动迁移配置文件和记忆文件
        _config_manager.migrate_config_files()
        _config_manager.migrate_memory_files()
    return _config_manager


# 便捷函数
def get_config_path(filename):
    """获取配置文件路径"""
    return get_config_manager().get_config_path(filename)


def load_json_config(filename, default_value=None):
    """加载JSON配置"""
    return get_config_manager().load_json_config(filename, default_value)


def save_json_config(filename, data):
    """保存JSON配置"""
    return get_config_manager().save_json_config(filename, data)

# Workshop配置便捷函数
def load_workshop_config():
    """加载workshop配置"""
    return get_config_manager().load_workshop_config()

def save_workshop_config(config_data):
    """保存workshop配置"""
    return get_config_manager().save_workshop_config(config_data)

def save_workshop_path(workshop_path):
    """保存workshop根目录路径"""
    return get_config_manager().save_workshop_path(workshop_path)

def get_workshop_path():
    """获取workshop根目录路径"""
    return get_config_manager().get_workshop_path()


if __name__ == "__main__":
    # 测试代码
    manager = get_config_manager()
    print("配置管理器信息:")
    info = manager.get_config_info()
    for key, value in info.items():
        if isinstance(value, dict):
            print(f"{key}:")
            for k, v in value.items():
                print(f"  {k}: {v}")
        else:
            print(f"{key}: {value}")

