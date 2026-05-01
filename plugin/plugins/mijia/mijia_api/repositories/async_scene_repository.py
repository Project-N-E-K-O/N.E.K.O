"""异步智能仓储实现

基于AsyncHttpClient的异步智能仓储实现。
"""

from typing import List

from ..domain.models import Credential, Scene
from ..infrastructure.http_client import AsyncHttpClient
from .interfaces import ISceneRepository


class AsyncSceneRepositoryImpl(ISceneRepository):
    """异步智能仓储实现

    使用 AsyncHttpClient 提供异步的智能数据访问。
    """

    def __init__(self, http_client: AsyncHttpClient):
        self._http = http_client

    async def get_all(self, home_id: str, credential: Credential) -> List[Scene]:
        """异步获取家庭下所有智能"""
        response = await self._http.post(
            "/appgateway/miot/appsceneservice/AppSceneService/GetSimpleSceneList",
            {
                "app_version": 12,
                "get_type": 2,
                "home_id": str(home_id),
                "owner_uid": credential.user_id,
            },
            credential,
        )

        scene_list = response.get("result", {}).get("manual_scene_info_list", [])
        scenes = []
        for scene_data in scene_list:
            scene_id_raw = scene_data.get("scene_id")
            if scene_id_raw is None or scene_id_raw == "":
                continue
            scene = Scene(
                scene_id=str(scene_id_raw),
                name=scene_data.get("name", ""),
                home_id=home_id,
                icon=scene_data.get("icon"),
            )
            scenes.append(scene)

        return scenes

    async def execute(self, scene_id: str, home_id: str, credential: Credential) -> bool:
        """异步执行智能"""
        response = await self._http.post(
            "/appgateway/miot/appsceneservice/AppSceneService/NewRunScene",
            {
                "scene_id": scene_id,
                "scene_type": 2,
                "phone_id": "null",
                "home_id": str(home_id),
                "owner_uid": credential.user_id,
            },
            credential,
        )

        return response.get("code") == 0
