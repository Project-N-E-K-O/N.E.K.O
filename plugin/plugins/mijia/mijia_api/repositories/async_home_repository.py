"""异步家庭仓储实现

基于AsyncHttpClient的异步家庭仓储实现，支持缓存。
"""

from typing import List, Optional

from ..domain.models import Credential, Home
from ..infrastructure.cache_manager import CacheManager
from ..infrastructure.http_client import AsyncHttpClient
from .interfaces import IHomeRepository


class AsyncHomeRepositoryImpl(IHomeRepository):
    """异步家庭仓储实现

    使用 AsyncHttpClient 提供异步的家庭数据访问。
    """

    def __init__(self, http_client: AsyncHttpClient, cache_manager: CacheManager):
        self._http = http_client
        self._cache = cache_manager

    async def get_all(self, credential: Credential) -> List[Home]:
        """异步获取所有家庭"""
        cache_key = "homes"
        cached = self._cache.get(cache_key, namespace=credential.user_id)
        if cached is not None:
            return [Home.model_validate(h) for h in cached]

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
        response = await self._http.post(uri, data, credential)

        home_list = response.get("result", {}).get("homelist", [])
        homes = []
        for home_data in home_list:
            home = Home(
                id=str(home_data.get("id", "")),
                name=home_data.get("name", ""),
                uid=str(home_data.get("uid", "")),
                rooms=home_data.get("roomlist", []),
            )
            homes.append(home)

        self._cache.set(
            cache_key, [h.model_dump() for h in homes], ttl=300, namespace=credential.user_id
        )

        return homes

    async def get_by_id(self, home_id: str, credential: Credential) -> Optional[Home]:
        """异步根据ID获取家庭"""
        cache_key = f"home:{home_id}"
        cached = self._cache.get(cache_key, namespace=credential.user_id)
        if cached:
            return Home.model_validate(cached)

        homes = await self.get_all(credential)
        for home in homes:
            if home.id == home_id:
                self._cache.set(cache_key, home.model_dump(), ttl=300, namespace=credential.user_id)
                return home

        return None
