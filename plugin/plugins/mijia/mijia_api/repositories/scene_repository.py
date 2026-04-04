"""智能仓储实现

基于HTTP的智能仓储实现。
"""

from typing import List

from ..domain.models import Credential, Scene
from ..infrastructure.http_client import HttpClient
from .interfaces import ISceneRepository


class SceneRepositoryImpl(ISceneRepository):
    """智能仓储实现

    基于HTTP API的智能仓储实现。
    """

    def __init__(self, http_client: HttpClient):
        """初始化智能仓储

        Args:
            http_client: HTTP客户端
        """
        self._http = http_client

    def get_all(self, home_id: str, credential: Credential) -> List[Scene]:
        """获取家庭下所有智能

        Args:
            home_id: 家庭ID
            credential: 用户凭据

        Returns:
            智能列表
        """
        # 从API获取智能列表
        response = self._http.post(
            "/appgateway/miot/appsceneservice/AppSceneService/GetSimpleSceneList",
            json={
                "app_version": 12,
                "get_type": 2,
                "home_id": str(home_id),
                "owner_uid": credential.user_id
            },
            credential=credential
        )

        # 解析智能列表
        scene_list = response.get("result", {}).get("manual_scene_info_list", [])
        scenes = []
        for scene_data in scene_list:
            # 映射API字段到领域模型
            scene_id_raw = scene_data.get("scene_id")
            # 跳过无效的场景ID（None 和空字符串都要过滤）
            if scene_id_raw is None or scene_id_raw == "":
                continue
            scene_id = str(scene_id_raw)
            scene = Scene(
                scene_id=scene_id,
                name=scene_data.get("name", ""),
                home_id=home_id,
                icon=scene_data.get("icon"),
            )
            scenes.append(scene)

        return scenes

    def execute(self, scene_id: str, home_id: str, credential: Credential) -> bool:
        """执行智能

        Args:
            scene_id: 智能ID
            home_id: 家庭ID
            credential: 用户凭据

        Returns:
            执行成功返回True，失败返回False
        """
        # 调用API执行智能
        response = self._http.post(
            "/appgateway/miot/appsceneservice/AppSceneService/NewRunScene",
            json={
                "scene_id": scene_id,
                "scene_type": 2,
                "phone_id": "null",
                "home_id": str(home_id),
                "owner_uid": credential.user_id
            },
            credential=credential
        )

        # 检查结果
        return response.get("code") == 0
