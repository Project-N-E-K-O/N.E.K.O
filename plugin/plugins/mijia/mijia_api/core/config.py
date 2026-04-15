"""配置管理器

提供集中式配置管理，支持从TOML文件和环境变量加载配置。
"""

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

# Python 3.11+ 使用内置的tomllib，Python 3.9-3.10 使用tomli
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        if not TYPE_CHECKING:
            tomllib = None  # type: ignore[assignment]

from .logging import get_logger

logger = get_logger(__name__)


class ConfigManager:
    """配置管理器

    支持从TOML文件和环境变量加载配置，提供集中式配置管理接口。
    环境变量格式：MIJIA_<KEY>，例如 MIJIA_API_BASE_URL

    配置加载优先级：
    1. 默认配置（最低优先级）
    2. TOML文件配置
    3. 环境变量（最高优先级）

    示例:
        >>> config = ConfigManager()
        >>> api_url = config.get("API_BASE_URL")
        >>> config.set("DEFAULT_TIMEOUT", 60)
        >>> all_config = config.get_all()
    """

    def __init__(self, config_path: Optional[Path] = None):
        """初始化配置管理器

        Args:
            config_path: TOML配置文件路径，如果为None或文件不存在则只使用默认配置
        """
        self._config: Dict[str, Any] = {}
        self._load_defaults()

        if config_path and config_path.exists():
            self._load_from_file(config_path)

        self._load_from_env()

    def _load_defaults(self) -> None:
        """加载默认配置"""
        self._config = {
            # API相关配置
            "API_BASE_URL": "https://api.mijia.tech/app",
            "LOGIN_URL": "https://account.xiaomi.com",
            "SERVICE_LOGIN_URL": "https://account.xiaomi.com/pass/serviceLogin",
            "DEVICE_SPEC_URL": "https://miot-spec.org/miot-spec-v2/instance",
            # 网络相关配置
            "DEFAULT_TIMEOUT": 30,
            "MAX_RETRIES": 3,
            # 缓存相关配置
            "CACHE_TTL": 300,
            # 日志相关配置
            "LOG_LEVEL": "INFO",
            # 安全相关配置
            "CREDENTIAL_PATH": ".mijia/credential.json",
            # Redis配置（可选）
            "REDIS_ENABLED": False,
            "REDIS_HOST": "localhost",
            "REDIS_PORT": 6379,
            "REDIS_DB": 0,
            "REDIS_PASSWORD": None,
            "REDIS_TIMEOUT": 5,
            "REDIS_CONNECT_TIMEOUT": 5,
        }

    def _load_from_file(self, path: Path) -> None:
        """从TOML文件加载配置

        Args:
            path: TOML配置文件路径
        """
        if tomllib is None:
            logger.warning("TOML支持不可用，请安装tomli库: pip install tomli")
            return

        try:
            with open(path, "rb") as f:
                file_config = tomllib.load(f)
            
            # 展平嵌套的配置结构
            # 例如：{"security": {"credential_path": "..."}} -> {"CREDENTIAL_PATH": "..."}
            flattened = self._flatten_config(file_config)
            self._config.update(flattened)
        except Exception as e:
            # 配置文件加载失败不应该导致程序崩溃，只记录错误
            logger.warning(f"加载配置文件失败: {e}", extra={"path": str(path)})
    
    def _flatten_config(self, config: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
        """展平嵌套的配置字典
        
        将TOML文件中的嵌套结构转换为扁平的键值对。
        例如：{"api": {"base_url": "..."}} -> {"API_BASE_URL": "..."}
        
        Args:
            config: 配置字典
            prefix: 键名前缀
            
        Returns:
            展平后的配置字典
        """
        result = {}
        
        # 展平嵌套的节（如 [security].credential_path -> SECURITY_CREDENTIAL_PATH）
        for key, value in config.items():
            if isinstance(value, dict):
                # 递归处理嵌套字典，累积前缀
                result.update(self._flatten_config(value, f"{prefix}{key}_"))
            else:
                # 转换为大写并添加前缀
                full_key = f"{prefix}{key}".upper()
                result[full_key] = value
        
        # 别名归一化：展平后的节前缀在默认值里没有对应节
        # 例如：SECURITY_CREDENTIAL_PATH -> CREDENTIAL_PATH（匹配默认值键名）
        aliases = {
            "SECURITY_CREDENTIAL_PATH": "CREDENTIAL_PATH",
            "LOGGING_LEVEL": "LOG_LEVEL",
            "NETWORK_DEFAULT_TIMEOUT": "DEFAULT_TIMEOUT",
            "NETWORK_MAX_RETRIES": "MAX_RETRIES",
        }
        for alias_key, canonical_key in aliases.items():
            if alias_key in result and canonical_key not in result:
                result[canonical_key] = result.pop(alias_key)
        
        return result

    def _load_from_env(self) -> None:
        """从环境变量加载配置

        环境变量格式：MIJIA_<KEY>
        例如：MIJIA_API_BASE_URL 会覆盖 API_BASE_URL 配置项
        """
        for key in self._config.keys():
            env_key = f"MIJIA_{key}"
            if env_key in os.environ:
                env_value = os.environ[env_key]
                # 尝试转换类型
                self._config[key] = self._convert_env_value(env_value, self._config[key])

    def _convert_env_value(self, env_value: str, default_value: Any) -> Any:
        """转换环境变量值为合适的类型

        Args:
            env_value: 环境变量字符串值
            default_value: 默认值，用于推断目标类型

        Returns:
            转换后的值
        """
        # 如果默认值是布尔类型，转换为布尔值
        if isinstance(default_value, bool):
            return env_value.lower() in ("true", "1", "yes", "on")

        # 如果默认值是整数类型，转换为整数
        if isinstance(default_value, int):
            try:
                return int(env_value)
            except ValueError:
                return default_value

        # 如果默认值是浮点数类型，转换为浮点数
        if isinstance(default_value, float):
            try:
                return float(env_value)
            except ValueError:
                return default_value

        # 如果值是"None"字符串，返回None
        if env_value.lower() == "none":
            return None

        # 其他情况返回字符串
        return env_value

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项

        Args:
            key: 配置项键名
            default: 默认值，当配置项不存在时返回

        Returns:
            配置项的值，如果不存在则返回default

        示例:
            >>> config = ConfigManager()
            >>> timeout = config.get("DEFAULT_TIMEOUT")
            >>> custom = config.get("CUSTOM_KEY", "default_value")
        """
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """设置配置项

        Args:
            key: 配置项键名
            value: 配置项的值

        示例:
            >>> config = ConfigManager()
            >>> config.set("DEFAULT_TIMEOUT", 60)
            >>> config.set("CUSTOM_KEY", "custom_value")
        """
        self._config[key] = value

    def get_all(self) -> Dict[str, Any]:
        """获取所有配置

        Returns:
            包含所有配置项的字典副本

        示例:
            >>> config = ConfigManager()
            >>> all_config = config.get_all()
            >>> print(all_config["API_BASE_URL"])
        """
        return self._config.copy()
