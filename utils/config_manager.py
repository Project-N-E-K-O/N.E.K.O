# -*- coding: utf-8 -*-
"""
配置文件管理模块
负责管理配置文件的存储位置和迁移
"""
import sys
import os
import json
import shutil
from pathlib import Path


class ConfigManager:
    """配置文件管理器"""
    
    # 配置文件名
    CONFIG_FILES = [
        'characters.json',
        'core_config.json',
        'user_preferences.json',
        'voice_storage.json'  # 添加这一行
    ]
    
    def __init__(self, app_name="Xiao8"):
        """
        初始化配置管理器
        
        Args:
            app_name: 应用名称
        """
        self.app_name = app_name
        self.docs_dir = self._get_documents_directory()
        self.app_docs_dir = self.docs_dir / app_name
        self.config_dir = self.app_docs_dir / "config"
        self.memory_dir = self.app_docs_dir / "memory"
        self.live2d_dir = self.app_docs_dir / "live2d"
        self.project_config_dir = self._get_project_config_directory()
        self.project_memory_dir = self._get_project_memory_directory()
    
    def _get_documents_directory(self):
        """获取用户文档目录（使用系统API）"""
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
                docs_dir = Path(buf.value)
                
                if docs_dir.exists():
                    return docs_dir
            except Exception as e:
                print(f"Warning: Failed to get Documents path via API: {e}", file=sys.stderr)
            
            # 降级：尝试从注册表读取
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
                )
                docs_dir = Path(winreg.QueryValueEx(key, "Personal")[0])
                winreg.CloseKey(key)
                
                # 展开环境变量
                docs_dir = Path(os.path.expandvars(str(docs_dir)))
                if docs_dir.exists():
                    return docs_dir
            except Exception as e:
                print(f"Warning: Failed to get Documents path from registry: {e}", file=sys.stderr)
            
            # 最后的降级：使用默认路径
            docs_dir = Path.home() / "Documents"
            if not docs_dir.exists():
                docs_dir = Path.home() / "文档"
        
        elif sys.platform == "darwin":
            # macOS: 使用标准路径
            docs_dir = Path.home() / "Documents"
        else:
            # Linux: 尝试使用XDG
            xdg_docs = os.getenv('XDG_DOCUMENTS_DIR')
            if xdg_docs:
                docs_dir = Path(xdg_docs)
            else:
                docs_dir = Path.home() / "Documents"
        
        return docs_dir
    
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
    
    def ensure_config_directory(self):
        """确保我的文档下的config目录存在"""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create config directory: {e}", file=sys.stderr)
            return False
    
    def ensure_memory_directory(self):
        """确保我的文档下的memory目录存在"""
        try:
            self.memory_dir.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create memory directory: {e}", file=sys.stderr)
            return False
    
    def ensure_live2d_directory(self):
        """确保我的文档下的live2d目录存在"""
        try:
            self.live2d_dir.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            print(f"Warning: Failed to create live2d directory: {e}", file=sys.stderr)
            return False
    
    def get_config_path(self, filename):
        """
        获取配置文件路径
        
        优先级：
        1. 我的文档/Xiao8/config/
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
        for filename in self.CONFIG_FILES:
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
                # 创建默认配置文件
                self.save_json_config(filename, default_value)
                return default_value
            raise
        except Exception as e:
            print(f"Error loading {filename}: {e}", file=sys.stderr)
            if default_value is not None:
                return default_value
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
        except Exception as e:
            print(f"Error saving {filename}: {e}", file=sys.stderr)
            raise
    
    def get_memory_path(self, filename):
        """
        获取记忆文件路径
        
        优先级：
        1. 我的文档/Xiao8/memory/
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
                for filename in self.CONFIG_FILES
            }
        }


# 全局配置管理器实例
_config_manager = None


def get_config_manager(app_name="Xiao8"):
    """获取配置管理器单例"""
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

