"""地点管理 router — 保存/删除/列出常用地点。"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

from plugin.sdk.plugin import plugin_entry, ui, tr, Ok, Err, SdkError
from plugin.sdk.shared.core.router import PluginRouter

from .._api import geocode_city

_STORE_KEY = "saved_locations"


class LocationsRouter(PluginRouter):
    """地点管理 entry：增删查 + 设默认。"""

    def __init__(self):
        super().__init__(name="locations")

    # ── helpers ──

    async def _load(self) -> List[Dict[str, Any]]:
        plugin = self.main_plugin
        if not plugin.store.enabled:
            return []
        result = await plugin.store.get(_STORE_KEY, [])
        # Result 类型：Ok(value) 或 Err(error)
        if hasattr(result, "is_ok") and callable(result.is_ok):
            if result.is_ok():
                data = result.value
            else:
                plugin.logger.warning("store.get failed: {}", result.error)
                return []
        elif hasattr(result, "value"):
            data = result.value
        else:
            data = result
        return data if isinstance(data, list) else []

    async def _save(self, locations: List[Dict[str, Any]]) -> bool:
        """保存地点列表。返回是否成功。"""
        plugin = self.main_plugin
        if not plugin.store.enabled:
            plugin.logger.error("PluginStore is disabled, cannot save locations")
            return False
        result = await plugin.store.set(_STORE_KEY, locations)
        if hasattr(result, "is_ok") and callable(result.is_ok):
            if not result.is_ok():
                plugin.logger.error("store.set failed: {}", result.error)
                return False
        return True

    # ── entries ──

    @plugin_entry(
        id="list_locations",
        name="查看常用地点",
        description="列出所有保存的常用地点。",
        llm_result_fields=["count", "locations"],
    )
    async def list_locations(self, **_):
        locations = await self._load()
        return Ok({"count": len(locations), "locations": locations})

    @ui.action(
        label=tr("actions.addLocation.label", default="Add location"),
        icon="➕",
        tone="success",
        group="locations",
        order=10,
        refresh_context=True,
    )
    @plugin_entry(
        id="add_location",
        name=tr("entries.addLocation.name", default="添加常用地点"),
        description=tr("entries.addLocation.description", default="添加一个常用地点。提供标签和城市名，自动获取坐标。"),
        llm_result_fields=["message"],
        input_schema={
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": tr("fields.locationLabel.description", default="地点标签（如：家、公司）")},
                "city": {"type": "string", "description": tr("fields.city.description", default="城市名")},
                "address": {"type": "string", "description": tr("fields.address.description", default="具体地址（可选）"), "default": ""},
                "set_default": {"type": "boolean", "description": tr("fields.setDefault.description", default="是否设为默认地点"), "default": False},
            },
            "required": ["label", "city"],
        },
    )
    async def add_location(self, label: str, city: str, address: str = "", set_default: bool = False, **_):
        if not label.strip():
            return Err(SdkError("标签不能为空"))
        if not city.strip():
            return Err(SdkError("城市不能为空"))

        plugin = self.main_plugin
        locale = plugin._i18n.locale

        # geocode
        try:
            geo = await geocode_city(city.strip(), locale=locale)
        except Exception:
            geo = None
        if not geo:
            return Err(SdkError(f"无法定位城市: {city}"))

        locations = await self._load()

        # 检查标签重复
        for loc in locations:
            if loc.get("label") == label.strip():
                return Err(SdkError(f"标签 '{label}' 已存在"))

        new_loc: Dict[str, Any] = {
            "id": uuid.uuid4().hex[:8],
            "label": label.strip(),
            "city": geo["city"],
            "address": address.strip(),
            "lat": geo["lat"],
            "lon": geo["lon"],
            "country": geo.get("country", ""),
            "is_default": False,
        }

        if set_default or not locations:
            for loc in locations:
                loc["is_default"] = False
            new_loc["is_default"] = True

        locations.append(new_loc)
        if not await self._save(locations):
            return Err(SdkError("保存失败，请检查插件存储是否启用"))

        return Ok({"message": f"已添加地点: {new_loc['label']} ({new_loc['city']})", "location": new_loc})

    @ui.action(
        label=tr("actions.removeLocation.label", default="Remove location"),
        icon="🗑️",
        tone="danger",
        group="locations",
        order=30,
        confirm=tr("actions.removeLocation.confirm", default="Remove this saved location?"),
        refresh_context=True,
    )
    @plugin_entry(
        id="remove_location",
        name=tr("entries.removeLocation.name", default="删除常用地点"),
        description=tr("entries.removeLocation.description", default="按 ID 或标签删除一个常用地点。"),
        llm_result_fields=["message"],
        input_schema={
            "type": "object",
            "properties": {
                "location_id": {"type": "string", "description": tr("fields.locationId.description", default="地点 ID 或标签")},
            },
            "required": ["location_id"],
        },
    )
    async def remove_location(self, location_id: str, **_):
        locations = await self._load()
        key = location_id.strip()
        before = len(locations)
        locations = [loc for loc in locations if loc.get("id") != key and loc.get("label") != key]
        if len(locations) == before:
            return Err(SdkError(f"未找到地点: {key}"))

        # 如果删掉了默认地点，把第一个设为默认
        if locations and not any(loc.get("is_default") for loc in locations):
            locations[0]["is_default"] = True

        if not await self._save(locations):
            return Err(SdkError("保存失败"))
        return Ok({"message": f"已删除地点: {key}", "remaining": len(locations)})

    @ui.action(
        label=tr("actions.setDefaultLocation.label", default="Set default"),
        icon="⭐",
        tone="primary",
        group="locations",
        order=20,
        refresh_context=True,
    )
    @plugin_entry(
        id="set_default_location",
        name=tr("entries.setDefaultLocation.name", default="设置默认地点"),
        description=tr("entries.setDefaultLocation.description", default="将指定地点设为默认（查天气时优先使用）。"),
        llm_result_fields=["message"],
        input_schema={
            "type": "object",
            "properties": {
                "location_id": {"type": "string", "description": tr("fields.locationId.description", default="地点 ID 或标签")},
            },
            "required": ["location_id"],
        },
    )
    async def set_default_location(self, location_id: str, **_):
        locations = await self._load()
        key = location_id.strip()
        found = False
        for loc in locations:
            if loc.get("id") == key or loc.get("label") == key:
                loc["is_default"] = True
                found = True
            else:
                loc["is_default"] = False
        if not found:
            return Err(SdkError(f"未找到地点: {key}"))
        if not await self._save(locations):
            return Err(SdkError("保存失败"))
        return Ok({"message": f"已将 '{key}' 设为默认地点"})
