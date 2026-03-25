"""米家配置管理模块"""

import os
import json
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict

from ..utils.logger import get_logger

_LOGGER = get_logger(__name__)

@dataclass
class MijiaConfig:
    """米家配置数据类"""
    username: str = ""
    password: str = ""
    enableQR: bool = False
    log_level: str = "INFO"
    auto_connect: bool = False
    device_cache_ttl: int = 300
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MijiaConfig':
        """从字典创建配置实例"""
        return cls(
            username=data.get('username', ''),
            password=data.get('password', ''),
            enableQR=data.get('enableQR', False),
            log_level=data.get('log_level', 'INFO'),
            auto_connect=data.get('auto_connect', False),
            device_cache_ttl=data.get('device_cache_ttl', 300)
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)


def load_mijia_config(config_path: Optional[Path] = None) -> MijiaConfig:
    """加载米家配置
    
    Args:
        config_path: 配置文件路径，默认从环境变量或标准位置加载
        
    Returns:
        MijiaConfig: 配置对象
    """
    config = MijiaConfig()
    
    # 1. 先从环境变量加载
    config.username = os.environ.get('MIJIA_USERNAME', '')
    config.password = os.environ.get('MIJIA_PASSWORD', '')
    config.enableQR = os.environ.get('MIJIA_ENABLE_QR', '').lower() == 'true'
    config.log_level = os.environ.get('MIJIA_LOG_LEVEL', 'INFO')
    config.auto_connect = os.environ.get('MIJIA_AUTO_CONNECT', '').lower() == 'true'
    
    try:
        # 2. 从配置文件加载（覆盖环境变量）
        if config_path is None:
            # 默认配置文件位置
            possible_paths = [
                Path.cwd() / 'config' / 'mijia.json',
                Path(__file__).parent.parent / 'config' / 'mijia.json'
            ]
            
            for path in possible_paths:
                if path.exists():
                    config_path = path
                    _LOGGER.info(f"找到配置文件: {path}")
                    break
        
        if config_path and config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                file_config = json.load(f)
                
            # 更新配置（文件中的值优先）
            if file_config.get('username'):
                config.username = file_config['username']
            if file_config.get('password'):
                config.password = file_config['password']
            if 'enableQR' in file_config:
                config.enableQR = file_config['enableQR']
            if file_config.get('log_level'):
                config.log_level = file_config['log_level']
            if 'auto_connect' in file_config:
                config.auto_connect = file_config['auto_connect']
            if file_config.get('device_cache_ttl'):
                config.device_cache_ttl = file_config['device_cache_ttl']
                
            _LOGGER.info(f"已加载配置文件: {config_path}")
        else:
            _LOGGER.info("未找到配置文件，使用环境变量配置")
            
    except Exception as e:
        _LOGGER.error(f"加载配置文件失败: {e}")
    
    return config


def save_mijia_config(config: MijiaConfig, config_path: Path) -> bool:
    """保存米家配置
    
    Args:
        config: 配置对象
        config_path: 配置文件路径
        
    Returns:
        bool: 是否保存成功
    """
    try:
        # 确保目录存在
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 保存配置
        config_dict = config.to_dict()
        
        # 如果密码为空，尝试保留原有密码
        if not config_dict['password'] and config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    old_config = json.load(f)
                config_dict['password'] = old_config.get('password', '')
            except Exception as e:
                _LOGGER.debug(f"无法读取原配置文件保留密码: {e}")
        
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config_dict, f, ensure_ascii=False, indent=2)
            
        _LOGGER.info(f"配置已保存到: {config_path}")
        return True
        
    except Exception as e:
        _LOGGER.error(f"保存配置失败: {e}")
        return False
