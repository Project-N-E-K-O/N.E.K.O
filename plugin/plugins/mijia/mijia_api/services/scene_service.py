"""智能服务

封装智能相关的业务逻辑。
"""

from typing import List

from ..domain.models import Credential, Scene
from ..repositories.interfaces import ISceneRepository


class SceneService:
    """智能服务

    封装智能管理和执行的业务逻辑。
    """

    def __init__(self, scene_repo: ISceneRepository):
        """初始化智能服务

        Args:
            scene_repo: 智能仓储接口实现
        """
        self._scene_repo = scene_repo

    def get_scenes(self, home_id: str, credential: Credential) -> List[Scene]:
        """获取智能列表

        Args:
            home_id: 家庭ID
            credential: 用户凭据

        Returns:
            智能列表
        """
        return self._scene_repo.get_all(home_id, credential)

    def execute_scene(self, scene_id: str, home_id: str, credential: Credential) -> bool:
        """执行智能

        Args:
            scene_id: 智能ID
            home_id: 家庭ID
            credential: 用户凭据

        Returns:
            执行是否成功
        """
        return self._scene_repo.execute(scene_id, home_id, credential)
