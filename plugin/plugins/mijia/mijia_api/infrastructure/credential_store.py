"""凭据存储

提供凭据的存储和加载功能。
"""

import json
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from ..core.logging import get_logger
from ..domain.models import Credential

logger = get_logger(__name__)


class ICredentialStore(ABC):
    """凭据存储接口"""

    @abstractmethod
    def save(self, credential: Credential, path: Optional[str] = None) -> None:
        """保存凭据"""
        pass

    @abstractmethod
    def load(self, path: Optional[str] = None) -> Optional[Credential]:
        """加载凭据"""
        pass

    @abstractmethod
    def delete(self, path: Optional[str] = None) -> None:
        """删除凭据"""
        pass


class FileCredentialStore(ICredentialStore):
    """文件凭据存储

    将凭据以JSON格式存储到文件系统。
    支持从配置文件读取存储路径，默认为项目根目录下的 .mijia/credential.json。
    文件权限设置为0o600（仅所有者可读写）以保护敏感信息。
    """

    def __init__(self, default_path: Optional[Path] = None):
        """初始化文件凭据存储

        Args:
            default_path: 默认存储路径，如果未指定则使用 .mijia/credential.json
        """
        if default_path:
            self._default_path = default_path
        else:
            # 默认使用项目根目录下的 .mijia/credential.json
            # 项目根目录定义为包含 pyproject.toml 的目录
            project_root = self._find_project_root()
            self._default_path = project_root / ".mijia" / "credential.json"
        
        # 确保目录存在
        self._default_path.parent.mkdir(parents=True, exist_ok=True)
    
    def _find_project_root(self) -> Path:
        """查找项目根目录
        
        从当前文件向上查找，直到找到包含 pyproject.toml 的目录。
        如果找不到，则使用当前工作目录。
        
        Returns:
            项目根目录路径
        """
        current = Path(__file__).resolve()
        
        # 向上查找，最多查找10层
        for _ in range(10):
            current = current.parent
            if (current / "pyproject.toml").exists():
                return current
            # 到达文件系统根目录
            if current.parent == current:
                break
        
        # 如果找不到，使用当前工作目录
        return Path.cwd()

    def save(self, credential: Credential, path: Optional[str] = None) -> None:
        """保存凭据到文件

        Args:
            credential: 要保存的凭据对象
            path: 可选的文件路径，如果未指定则使用默认路径

        Raises:
            Exception: 当文件写入失败时抛出异常
        """
        file_path = self._get_path(path)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(credential.to_dict(), f, ensure_ascii=False, indent=2, default=str)

            # 设置文件权限为仅所有者可读写
            if sys.platform == "win32":
                try:
                    import subprocess
                    username = subprocess.check_output(
                        ["cmd", "/c", "echo", "%USERNAME%"], text=True
                    ).strip()
                    subprocess.run(
                        ["icacls", str(file_path), "/inheritance:r", "/grant:r", f"{username}:F"],
                        check=False, capture_output=True
                    )
                except Exception as e:
                    logger.warning(f"设置凭据文件权限失败(Windows): {e}")
            else:
                file_path.chmod(0o600)

            logger.info(f"凭据已保存到: {file_path}")
        except Exception as e:
            logger.error(f"保存凭据失败: {e}")
            raise

    def load(self, path: Optional[str] = None) -> Optional[Credential]:
        """从文件加载凭据

        Args:
            path: 可选的文件路径，如果未指定则使用默认路径

        Returns:
            加载的凭据对象，如果文件不存在或加载失败则返回None
        """
        file_path = self._get_path(path)

        if not file_path.exists():
            logger.warning(f"凭据文件不存在: {file_path}")
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            credential = Credential.from_dict(data)
            # 脱敏：只显示用户ID前4位
            masked_id = credential.user_id[:4] + "****" if len(credential.user_id) > 4 else "****"
            logger.info(f"凭据已加载，用户ID: {masked_id}")
            return credential
        except Exception as e:
            logger.error(f"加载凭据失败: {e}")
            return None

    def delete(self, path: Optional[str] = None) -> None:
        """删除凭据文件

        Args:
            path: 可选的文件路径，如果未指定则使用默认路径
        """
        file_path = self._get_path(path)

        if file_path.exists():
            file_path.unlink()
            logger.info(f"凭据已删除: {file_path}")

    def _get_path(self, path: Optional[str]) -> Path:
        """获取文件路径

        Args:
            path: 可选的文件路径

        Returns:
            Path对象，如果path为None则返回默认路径
        """
        if path:
            file_path = Path(path)
            # 展开用户目录符号 ~
            if str(file_path).startswith("~"):
                file_path = file_path.expanduser()
            return file_path
        return self._default_path
