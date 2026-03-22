"""认证数据管理器

提供统一的认证数据保存、加载、清理和验证功能。
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from .logger import get_logger

_LOGGER = get_logger(__name__)


class AuthDataManager:
    """认证数据管理器"""
    
    def __init__(self, config_dir: Optional[Path] = None):
        """初始化认证数据管理器
        
        Args:
            config_dir: 配置目录，默认为 ~/.miot-mcp
        """
        self._config_dir = config_dir or (Path.home() / '.miot-mcp')
        self._auth_file = self._config_dir / 'auth_data.json'
        self._backup_file = self._config_dir / 'auth_data.backup.json'
        
    def save(self, auth_data: Dict[str, Any], create_backup: bool = True) -> bool:
        """保存认证数据
        
        Args:
            auth_data: 认证数据
            create_backup: 是否创建备份文件
            
        Returns:
            bool: 是否保存成功
        """
        try:
            # 确保配置目录存在
            self._config_dir.mkdir(exist_ok=True)
            
            # 创建备份（如果原文件存在且需要备份）
            if create_backup and self._auth_file.exists():
                self._create_backup()
            
            # 添加保存时间戳
            auth_data_with_meta = {
                **auth_data,
                '_metadata': {
                    'saved_at': datetime.now().isoformat(),
                    'version': '1.0'
                }
            }
            
            # 保存到临时文件，然后原子性移动
            temp_file = self._auth_file.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(auth_data_with_meta, f, ensure_ascii=False, indent=2)
            
            # 原子性移动
            temp_file.replace(self._auth_file)
            
            _LOGGER.info(f"认证数据已保存到: {self._auth_file}")
            return True
            
        except Exception as e:
            _LOGGER.error(f"保存认证数据失败: {e}")
            return False
    
    def load(self) -> Optional[Dict[str, Any]]:
        """加载认证数据
        
        Returns:
            Optional[Dict[str, Any]]: 认证数据，如果不存在或加载失败则返回None
        """
        try:
            if not self._auth_file.exists():
                _LOGGER.debug(f"认证数据文件不存在: {self._auth_file}")
                return None
            
            with open(self._auth_file, 'r', encoding='utf-8') as f:
                auth_data = json.load(f)
            
            # 移除元数据
            if '_metadata' in auth_data:
                metadata = auth_data.pop('_metadata')
                _LOGGER.debug(f"认证数据保存时间: {metadata.get('saved_at')}")
            
            _LOGGER.info(f"成功加载认证数据: {self._auth_file}")
            return auth_data
            
        except json.JSONDecodeError as e:
            _LOGGER.error(f"认证数据文件格式错误: {e}")
            # 尝试从备份恢复
            return self._restore_from_backup()
        except Exception as e:
            _LOGGER.error(f"加载认证数据失败: {e}")
            return None
    
    def clear(self) -> bool:
        """清除认证数据
        
        Returns:
            bool: 是否清除成功
        """
        try:
            files_removed = []
            
            # 删除主文件
            if self._auth_file.exists():
                self._auth_file.unlink()
                files_removed.append(str(self._auth_file))
            
            # 删除备份文件
            if self._backup_file.exists():
                self._backup_file.unlink()
                files_removed.append(str(self._backup_file))
            
            # 删除临时文件
            temp_file = self._auth_file.with_suffix('.tmp')
            if temp_file.exists():
                temp_file.unlink()
                files_removed.append(str(temp_file))
            
            if files_removed:
                _LOGGER.info(f"已清除认证数据文件: {files_removed}")
            else:
                _LOGGER.debug("没有找到需要清除的认证数据文件")
            
            return True
            
        except Exception as e:
            _LOGGER.error(f"清除认证数据失败: {e}")
            return False
    
    def exists(self) -> bool:
        """检查认证数据是否存在
        
        Returns:
            bool: 认证数据文件是否存在
        """
        return self._auth_file.exists()
    
    def validate(self, auth_data: Optional[Dict[str, Any]] = None) -> bool:
        """验证认证数据的有效性
        
        Args:
            auth_data: 要验证的认证数据，如果为None则加载文件中的数据
            
        Returns:
            bool: 认证数据是否有效
        """
        if auth_data is None:
            auth_data = self.load()
        
        if not auth_data:
            return False
        
        # 检查必要字段
        required_fields = ['userId', 'serviceToken', 'ssecurity']
        for field in required_fields:
            if field not in auth_data:
                _LOGGER.warning(f"认证数据缺少必要字段: {field}")
                return False
        
        # 检查token是否为空
        if not auth_data.get('serviceToken'):
            _LOGGER.warning("serviceToken为空")
            return False
        
        _LOGGER.debug("认证数据验证通过")
        return True
    
    def get_file_path(self) -> Path:
        """获取认证数据文件路径
        
        Returns:
            Path: 认证数据文件路径
        """
        return self._auth_file
    
    def _create_backup(self) -> bool:
        """创建备份文件
        
        Returns:
            bool: 是否创建成功
        """
        try:
            if self._auth_file.exists():
                # 复制到备份文件
                import shutil
                shutil.copy2(self._auth_file, self._backup_file)
                _LOGGER.debug(f"已创建认证数据备份: {self._backup_file}")
                return True
        except Exception as e:
            _LOGGER.warning(f"创建认证数据备份失败: {e}")
        return False
    
    def _restore_from_backup(self) -> Optional[Dict[str, Any]]:
        """从备份文件恢复认证数据
        
        Returns:
            Optional[Dict[str, Any]]: 恢复的认证数据
        """
        try:
            if not self._backup_file.exists():
                _LOGGER.debug("备份文件不存在")
                return None
            
            with open(self._backup_file, 'r', encoding='utf-8') as f:
                auth_data = json.load(f)
            
            # 移除元数据
            if '_metadata' in auth_data:
                auth_data.pop('_metadata')
            
            _LOGGER.info(f"从备份文件恢复认证数据: {self._backup_file}")
            
            # 恢复到主文件
            self.save(auth_data, create_backup=False)
            
            return auth_data
            
        except Exception as e:
            _LOGGER.error(f"从备份恢复认证数据失败: {e}")
            return None
