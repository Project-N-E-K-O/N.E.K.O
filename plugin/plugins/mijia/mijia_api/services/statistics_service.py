"""统计服务

提供设备统计功能。
"""

from typing import Any, Dict, List

from ..domain.models import Credential, Device, DeviceStatus
from ..repositories.interfaces import IDeviceRepository


class StatisticsService:
    """统计服务

    提供设备统计数据查询功能，包括设备总数、在线/离线统计、按型号分类统计等。
    """

    def __init__(self, device_repo: IDeviceRepository) -> None:
        """初始化统计服务

        Args:
            device_repo: 设备仓储接口
        """
        self._device_repo = device_repo

    def get_device_statistics(self, home_id: str, credential: Credential) -> Dict[str, Any]:
        """获取设备统计信息

        统计指定家庭下的设备总数、在线数、离线数，以及按型号分类的设备数量。

        Args:
            home_id: 家庭ID
            credential: 用户凭据

        Returns:
            统计信息字典，包含以下字段：
            - total: 设备总数
            - online: 在线设备数
            - offline: 离线设备数
            - by_model: 按型号统计的设备数量字典
        """
        # 获取所有设备
        devices = self._device_repo.get_all(home_id, credential)

        # 统计在线和离线设备数量
        online_count = sum(1 for d in devices if d.is_online())
        # UNKNOWN 状态不属于离线，只有显式 OFFLINE 才计入离线
        offline_count = sum(1 for d in devices if d.status == DeviceStatus.OFFLINE)

        return {
            "total": len(devices),
            "online": online_count,
            "offline": offline_count,
            "by_model": self._count_by_model(devices),
        }

    def _count_by_model(self, devices: List[Device]) -> Dict[str, int]:
        """按型号统计设备数量

        Args:
            devices: 设备列表

        Returns:
            型号到设备数量的映射字典
        """
        counts: Dict[str, int] = {}
        for device in devices:
            counts[device.model] = counts.get(device.model, 0) + 1
        return counts
