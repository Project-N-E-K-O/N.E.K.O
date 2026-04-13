"""家庭仓储实现

基于HTTP的家庭仓储实现，支持缓存。
"""

from typing import List, Optional

from ..domain.models import Credential, Home
from ..infrastructure.cache_manager import CacheManager
from ..infrastructure.http_client import HttpClient
from .interfaces import IHomeRepository


class HomeRepositoryImpl(IHomeRepository):
    """家庭仓储实现

    基于HTTP API的家庭仓储实现，集成缓存管理。
    """

    def __init__(self, http_client: HttpClient, cache_manager: CacheManager):
        """初始化家庭仓储

        Args:
            http_client: HTTP客户端
            cache_manager: 缓存管理器
        """
        self._http = http_client
        self._cache = cache_manager

    def get_all(self, credential: Credential) -> List[Home]:
        """获取所有家庭

        Args:
            credential: 用户凭据

        Returns:
            家庭列表
        """
        # 检查缓存（使用 is not None 区分空列表和缓存未命中）
        cache_key = "homes"
        cached = self._cache.get(cache_key, namespace=credential.user_id)
        if cached is not None:
            return [Home.model_validate(h) for h in cached]

        # 从API获取（使用旧项目的URI和参数）
        uri = "/v2/homeroom/gethome_merged"
        data = {
            "fg": True,
            "fetch_share": True,
            "fetch_share_dev": True,
            "fetch_cariot": True,
            "limit": 300,
            "app_ver": 7,
            "plat_form": 0,
        }
        response = self._http.post(uri, json=data, credential=credential)

        # 解析家庭列表
        home_list = response.get("result", {}).get("homelist", [])
        homes = []
        for home_data in home_list:
            # 映射API字段到领域模型
            home = Home(
                id=str(home_data.get("id", "")),
                name=home_data.get("name", ""),
                uid=str(home_data.get("uid", "")),
                rooms=home_data.get("roomlist", []),
            )
            homes.append(home)

        # 缓存结果（TTL=300秒）
        self._cache.set(
            cache_key, [h.model_dump() for h in homes], ttl=300, namespace=credential.user_id
        )

        return homes

    def get_by_id(self, home_id: str, credential: Credential) -> Optional[Home]:
        """根据ID获取家庭

        Args:
            home_id: 家庭ID
            credential: 用户凭据

        Returns:
            家庭对象，不存在返回None
        """
        # 检查缓存
        cache_key = f"home:{home_id}"
        cached = self._cache.get(cache_key, namespace=credential.user_id)
        if cached:
            return Home.model_validate(cached)

        # 从所有家庭中查找
        homes = self.get_all(credential)
        for home in homes:
            if home.id == home_id:
                # 缓存单个家庭
                self._cache.set(cache_key, home.model_dump(), ttl=300, namespace=credential.user_id)
                return home

        return None
