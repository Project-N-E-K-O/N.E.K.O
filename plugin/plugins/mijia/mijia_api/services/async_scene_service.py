"""异步智能服务

封装智能相关的异步业务逻辑。
"""

from typing import List

from ..domain.models import Credential, Scene
from ..repositories.interfaces import ISceneRepository


class AsyncSceneService:
    """异步智能服务

    使用异步仓储提供非阻塞的智能操作。
    """

    def __init__(self, scene_repo: ISceneRepository):
        self._scene_repo = scene_repo

    async def get_scenes(self, home_id: str, credential: Credential) -> List[Scene]:
        return await self._scene_repo.get_all(home_id, credential)

    async def execute_scene(self, scene_id: str, home_id: str, credential: Credential) -> bool:
        return await self._scene_repo.execute(scene_id, home_id, credential)
