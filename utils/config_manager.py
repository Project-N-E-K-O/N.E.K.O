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
from copy import deepcopy
from pathlib import Path

from config import (
    APP_NAME,
    CONFIG_FILES,
    DEFAULT_CHARACTERS_CONFIG,
    DEFAULT_CONFIG_DATA,
)
from config.prompts_chara import lanlan_prompt
from utils.api_config_loader import (
    get_core_api_profiles,
    get_assist_api_profiles,
    get_assist_api_key_fields,
)


logger = logging.getLogger(__name__)


class ConfigManager:
    """配置文件管理器"""
    
    def __init__(self, app_name=None):
        """
        初始化配置管理器
        
        Args:
            app_name: 应用名称，默认使用配置中的 APP_NAME
        """
        self.app_name = app_name if app_name is not None else APP_NAME
        self.docs_dir = self._get_documents_directory()
        self.app_docs_dir = self.docs_dir / self.app_name
        self.config_dir = self.app_docs_dir / "config"
        self.memory_dir = self.app_docs_dir / "memory"
        self.live2d_dir = self.app_docs_dir / "live2d"
        self.project_config_dir = self._get_project_config_directory()
        self.project_memory_dir = self._get_project_memory_directory()
    
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
                print(f"[ConfigManager] API returned path: {api_path}", file=sys.stderr)
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
                            print(f"[ConfigManager] Found alternative path on same drive: {alt_path}", file=sys.stderr)
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
                print(f"[ConfigManager] Registry returned path: {reg_path}", file=sys.stderr)
                
                # 如果注册表路径不存在，尝试在同一盘符下查找
                if not reg_path.exists() and reg_path.drive:
                    drive = reg_path.drive
                    # 列出盘符下的所有文件夹，查找可能的文档文件夹
                    try:
                        drive_path = Path(drive + "\\")
                        if drive_path.exists():
                            for item in drive_path.iterdir():
                                if item.is_dir() and item.name.lower() in ["documents", "文档", "my documents"]:
                                    print(f"[ConfigManager] Found documents folder on drive: {item}", file=sys.stderr)
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
                    test_path = docs_dir / ".test_xiao8_write"
                    try:
                        test_path.touch()
                        test_path.unlink()
                        print(f"[ConfigManager] ✓ Using documents directory: {docs_dir}", file=sys.stderr)
                        return docs_dir
                    except Exception as e:
                        print(f"[ConfigManager] Path exists but not writable: {docs_dir} - {e}", file=sys.stderr)
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
                    test_path = docs_dir / ".test_xiao8_write"
                    test_path.touch()
                    test_path.unlink()
                    print(f"[ConfigManager] ✓ Using documents directory (created): {docs_dir}", file=sys.stderr)
                    return docs_dir
            except Exception as e:
                print(f"[ConfigManager] Failed to use path {docs_dir}: {e}", file=sys.stderr)
                continue
        
        # 如果所有候选都失败，返回当前目录
        fallback = Path.cwd()
        print(f"[ConfigManager] ⚠ All document directories failed, using fallback: {fallback}", file=sys.stderr)
        return fallback
    
    def _get_project_config_directory(self):
        """获取项目的config目录"""
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
        
        return app_dir / "config"
    
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
        """确保应用文档目录存在（Xiao8目录本身）"""
        try:
            # 先确保父目录（docs_dir）存在
            if not self.docs_dir.exists():
                print(f"Warning: Documents directory does not exist: {self.docs_dir}", file=sys.stderr)
                print(f"Warning: Attempting to create documents directory...", file=sys.stderr)
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
    
    def migrate_config_files(self):
        """
        迁移配置文件到我的文档
        
        策略：
        1. 检查我的文档下的config文件夹，没有就创建
        2. 对于每个配置文件：
           - 如果我的文档下有，跳过
           - 如果我的文档下没有，但项目config下有，复制过去
           - 如果都没有，不做处理（后续会创建默认值）
        """
        # 确保目录存在
        if not self.ensure_config_directory():
            print(f"Warning: Cannot create config directory, using project config", file=sys.stderr)
            return
        
        # 显示项目配置目录位置（调试用）
        print(f"[ConfigManager] Project config directory: {self.project_config_dir}")
        print(f"[ConfigManager] User config directory: {self.config_dir}")
        
        # 迁移每个配置文件
        for filename in CONFIG_FILES:
            docs_config_path = self.config_dir / filename
            project_config_path = self.project_config_dir / filename
            
            # 如果我的文档下已有，跳过
            if docs_config_path.exists():
                print(f"[ConfigManager] Config already exists: {filename}")
                continue
            
            # 如果项目config下有，复制过去
            if project_config_path.exists():
                try:
                    shutil.copy2(project_config_path, docs_config_path)
                    print(f"[ConfigManager] ✓ Migrated config: {filename} -> {docs_config_path}")
                except Exception as e:
                    print(f"Warning: Failed to migrate {filename}: {e}", file=sys.stderr)
            else:
                if filename in DEFAULT_CONFIG_DATA:
                    print(f"[ConfigManager] ~ Using in-memory default for {filename}")
                else:
                    print(f"[ConfigManager] ✗ Source config not found: {project_config_path}")
    
    def migrate_memory_files(self):
        """
        迁移记忆文件到我的文档
        
        策略：
        1. 检查我的文档下的memory文件夹，没有就创建
        2. 迁移所有记忆文件和目录
        """
        # 确保目录存在
        if not self.ensure_memory_directory():
            print(f"Warning: Cannot create memory directory, using project memory", file=sys.stderr)
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
        """获取默认角色配置数据"""
        return deepcopy(DEFAULT_CHARACTERS_CONFIG)

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
        return voice_storage.get(audio_api_key, {})

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

    def validate_voice_id(self, voice_id):
        """校验 voice_id 是否在当前 AUDIO_API_KEY 下有效"""
        if not voice_id:
            return True

        voices = self.get_voices_for_current_api()
        return voice_id in voices

    def cleanup_invalid_voice_ids(self):
        """清理 characters.json 中无效的 voice_id"""
        character_data = self.load_characters()
        voices = self.get_voices_for_current_api()
        cleaned_count = 0

        catgirls = character_data.get('猫娘', {})
        for name, config in catgirls.items():
            voice_id = config.get('voice_id', '')
            if voice_id and voice_id not in voices:
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
            DEFAULT_SUMMARY_MODEL,
            DEFAULT_CORRECTION_MODEL,
            DEFAULT_EMOTION_MODEL,
            DEFAULT_VISION_MODEL,
            DEFAULT_OMNI_MODEL,
            DEFAULT_TTS_MODEL,
            DEFAULT_SUMMARY_MODEL_PROVIDER,
            DEFAULT_SUMMARY_MODEL_URL,
            DEFAULT_SUMMARY_MODEL_API_KEY,
            DEFAULT_CORRECTION_MODEL_PROVIDER,
            DEFAULT_CORRECTION_MODEL_URL,
            DEFAULT_CORRECTION_MODEL_API_KEY,
            DEFAULT_EMOTION_MODEL_PROVIDER,
            DEFAULT_EMOTION_MODEL_URL,
            DEFAULT_EMOTION_MODEL_API_KEY,
            DEFAULT_VISION_MODEL_PROVIDER,
            DEFAULT_VISION_MODEL_URL,
            DEFAULT_VISION_MODEL_API_KEY,
            DEFAULT_OMNI_MODEL_PROVIDER,
            DEFAULT_OMNI_MODEL_URL,
            DEFAULT_OMNI_MODEL_API_KEY,
            DEFAULT_TTS_MODEL_PROVIDER,
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
            'SUMMARY_MODEL': DEFAULT_SUMMARY_MODEL,
            'CORRECTION_MODEL': DEFAULT_CORRECTION_MODEL,
            'EMOTION_MODEL': DEFAULT_EMOTION_MODEL,
            'ASSIST_API_KEY_QWEN': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_OPENAI': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_GLM': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_STEP': DEFAULT_CORE_API_KEY,
            'ASSIST_API_KEY_SILICON': DEFAULT_CORE_API_KEY,
            'COMPUTER_USE_MODEL': 'glm-4.5v',
            'COMPUTER_USE_GROUND_MODEL': 'glm-4.5v',
            'COMPUTER_USE_MODEL_URL': 'https://open.bigmodel.cn/api/paas/v4',
            'COMPUTER_USE_GROUND_URL': 'https://open.bigmodel.cn/api/paas/v4',
            'COMPUTER_USE_MODEL_API_KEY': '',
            'COMPUTER_USE_GROUND_API_KEY': '',
            'IS_FREE_VERSION': False,
            'VISION_MODEL': DEFAULT_VISION_MODEL,
            'OMNI_MODEL': DEFAULT_OMNI_MODEL,
            'TTS_MODEL': DEFAULT_TTS_MODEL,
            'SUMMARY_MODEL_PROVIDER': DEFAULT_SUMMARY_MODEL_PROVIDER,
            'SUMMARY_MODEL_URL': DEFAULT_SUMMARY_MODEL_URL,
            'SUMMARY_MODEL_API_KEY': DEFAULT_SUMMARY_MODEL_API_KEY,
            'CORRECTION_MODEL_PROVIDER': DEFAULT_CORRECTION_MODEL_PROVIDER,
            'CORRECTION_MODEL_URL': DEFAULT_CORRECTION_MODEL_URL,
            'CORRECTION_MODEL_API_KEY': DEFAULT_CORRECTION_MODEL_API_KEY,
            'EMOTION_MODEL_PROVIDER': DEFAULT_EMOTION_MODEL_PROVIDER,
            'EMOTION_MODEL_URL': DEFAULT_EMOTION_MODEL_URL,
            'EMOTION_MODEL_API_KEY': DEFAULT_EMOTION_MODEL_API_KEY,
            'VISION_MODEL_PROVIDER': DEFAULT_VISION_MODEL_PROVIDER,
            'VISION_MODEL_URL': DEFAULT_VISION_MODEL_URL,
            'VISION_MODEL_API_KEY': DEFAULT_VISION_MODEL_API_KEY,
            'OMNI_MODEL_PROVIDER': DEFAULT_OMNI_MODEL_PROVIDER,
            'OMNI_MODEL_URL': DEFAULT_OMNI_MODEL_URL,
            'OMNI_MODEL_API_KEY': DEFAULT_OMNI_MODEL_API_KEY,
            'TTS_MODEL_PROVIDER': DEFAULT_TTS_MODEL_PROVIDER,
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

        if core_cfg.get('mcpToken'):
            config['MCP_ROUTER_API_KEY'] = core_cfg['mcpToken']

        config['COMPUTER_USE_MODEL_API_KEY'] = config['COMPUTER_USE_GROUND_API_KEY'] = config['ASSIST_API_KEY_GLM']

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

        key_field = assist_api_key_fields.get(assist_api_value)
        if key_field:
            derived_key = config.get(key_field, '')
            if derived_key:
                config['AUDIO_API_KEY'] = derived_key
                config['OPENROUTER_API_KEY'] = derived_key

        if not config['AUDIO_API_KEY']:
            config['AUDIO_API_KEY'] = config['CORE_API_KEY']
        if not config['OPENROUTER_API_KEY']:
            config['OPENROUTER_API_KEY'] = config['CORE_API_KEY']

        return config

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
            "project_config_dir": str(self.project_config_dir),
            "project_memory_dir": str(self.project_memory_dir),
            "config_files": {
                filename: str(self.get_config_path(filename))
                for filename in CONFIG_FILES
            }
        }


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

