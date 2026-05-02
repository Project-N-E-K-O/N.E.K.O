"""异步统计服务

提供异步的设备统计功能。
"""

from typing import Any, Dict, List

from ..domain.models import Credential, DeviceStatus
from ..repositories.interfaces import IAsyncDeviceRepository


class AsyncStatisticsService:
    """异步统计服务

    使用异步仓储提供非阻塞的设备统计。
    """

    def __init__(self, device_repo: IAsyncDeviceRepository) -> None:
        self._device_repo = device_repo

    async def get_device_statistics(self, home_id: str, credential: Credential) -> Dict[str, Any]:
        devices = await self._device_repo.get_all(home_id, credential)

        online_count = sum(1 for d in devices if d.is_online())
        offline_count = sum(1 for d in devices if d.status == DeviceStatus.OFFLINE)

        counts: Dict[str, int] = {}
        for device in devices:
            counts[device.model] = counts.get(device.model, 0) + 1

        return {
            "total": len(devices),
            "online": online_count,
            "offline": offline_count,
            "by_model": counts,
        }
