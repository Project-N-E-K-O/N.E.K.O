"""米家智能设备插件 - 主类"""

import json
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry, lifecycle, timer_interval,
    Ok, Err, SdkError,
)

from .adapter.mijia_adapter import MijiaAdapter


@neko_plugin
class MijiaPlugin(NekoPluginBase):
    """米家智能设备插件"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self._config: Dict[str, Any] = {}
        self._adapter: Optional[MijiaAdapter] = None
        self._connected = False
        self._device_cache: Dict[str, Dict[str, Any]] = {}
        self._last_discovery_time: Optional[datetime] = None

        # 数据文件路径
        self._devices_file = self.data_path("devices.json")
        self._config_file = self.config_dir / "config.json"

        # 加载数据
        self._load_device_cache()
        self._load_config()

        self.logger.info("米家插件初始化完成")

    # ==================== 私有方法 ====================

    def _load_device_cache(self) -> None:
        """从文件加载设备缓存"""
        try:
            if self._devices_file.exists():
                with open(self._devices_file, 'r', encoding='utf-8') as f:
                    self._device_cache = json.load(f)
                self.logger.info(f"已加载 {len(self._device_cache)} 个设备缓存")
        except Exception as e:
            self.logger.error(f"加载设备缓存失败: {e}")
            self._device_cache = {}

    def _save_device_cache(self) -> None:
        """保存设备缓存到文件"""
        try:
            self._devices_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._devices_file, 'w', encoding='utf-8') as f:
                json.dump(self._device_cache, f, ensure_ascii=False, indent=2)
            self.logger.info(f"已保存 {len(self._device_cache)} 个设备缓存")
        except Exception as e:
            self.logger.error(f"保存设备缓存失败: {e}")

    def _load_config(self) -> None:
        """加载配置"""
        try:
            if self._config_file.exists():
                with open(self._config_file, 'r', encoding='utf-8') as f:
                    self._config = json.load(f)
                self.logger.info(f"已加载配置文件: {self._config_file}")
            else:
                self._config = self._get_default_config()
                self._save_config()
                self.logger.info(f"已创建默认配置文件: {self._config_file}")
        except Exception as e:
            self.logger.error(f"加载配置失败: {e}")
            self._config = self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {
            "username": "",
            "password": "",
            "enableQR": True,
            "auto_connect": False,
        }

    def _save_config(self) -> None:
        """保存配置"""
        try:
            self._config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._config_file, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
            self.logger.info(f"配置已保存: {self._config_file}")
        except Exception as e:
            self.logger.error(f"保存配置失败: {e}")

    def _update_device_properties_cache(self, device_id: str, properties) -> None:
        """更新设备属性缓存"""
        if device_id not in self._device_cache:
            return

        device_info = self._device_cache[device_id]
        properties_info = {}

        for prop in properties:
            prop_info = {
                "name": prop.name,
                "description": prop.desc,
                "type": prop.type,
                "rw": prop.rw,
                "unit": prop.unit,
                "siid": getattr(prop, 'siid', None),
                "piid": getattr(prop, 'piid', None),
                "range": getattr(prop, 'range', None),
                "value_list": getattr(prop, 'value_list', None)
            }
            properties_info[prop.name] = prop_info

        device_info["properties"] = properties_info
        device_info["last_update"] = datetime.now().isoformat()
        self._device_cache[device_id] = device_info
        self._save_device_cache()
        self.logger.info(f"已更新设备 {device_id} 的属性缓存")

    def _update_device_actions_cache(self, device_id: str, actions) -> None:
        """更新设备动作缓存"""
        if device_id not in self._device_cache:
            return

        device_info = self._device_cache[device_id]
        actions_info = {}

        for action in actions:
            action_info = {
                "name": action.name,
                "description": action.desc,
                "siid": getattr(action, 'siid', None),
                "aiid": getattr(action, 'aiid', None)
            }
            actions_info[action.name] = action_info

        device_info["actions"] = actions_info
        device_info["last_update"] = datetime.now().isoformat()
        self._device_cache[device_id] = device_info
        self._save_device_cache()
        self.logger.info(f"已更新设备 {device_id} 的动作缓存")

    def _check_connected(self) -> bool:
        """检查是否已连接"""
        return self._connected and self._adapter is not None

    async def _auto_connect(self) -> None:
        """自动连接"""
        result = await self._connect()
        if isinstance(result, Err):
            self.logger.error(f"自动连接失败: {result.error}")

    async def _fetch_device_details(self, device_id: str) -> None:
        """后台获取设备详细信息"""
        if not self._check_connected():
            return

        self.logger.info(f"正在获取设备 {device_id} 的详细信息...")

        try:
            properties = await self._adapter.get_device_properties(device_id)
            self._update_device_properties_cache(device_id, properties)
            self.logger.info(f"已获取设备 {device_id} 的 {len(properties)} 个属性")
        except Exception as e:
            self.logger.warning(f"获取设备 {device_id} 属性失败: {e}")

        try:
            actions = await self._adapter.get_device_actions(device_id)
            self._update_device_actions_cache(device_id, actions)
            self.logger.info(f"已获取设备 {device_id} 的 {len(actions)} 个动作")
        except Exception as e:
            self.logger.warning(f"获取设备 {device_id} 动作失败: {e}")

        self.push_message(
            source="mijia",
            message_type="text",
            description="设备信息获取完成",
            priority=3,
            content=f"设备 {device_id} 的详细信息已获取",
            metadata={"device_id": device_id}
        )

    # ==================== 生命周期 ====================

    @lifecycle(id="startup")
    async def on_startup(self, **_): 
        """插件启动"""
        self.logger.info("米家插件启动中...")

        self._adapter = MijiaAdapter(self._config, config_dir=self.data_path())

        self.report_status({
            "status": "initialized",
            "connected": False,
            "device_count": 0
        })

        if self._config.get("auto_connect", False):
            await self._adapter.disconnect()

        self.push_message(
            source="mijia",
            message_type="text",
            description="插件启动",
            priority=3,
            content="米家插件已启动"
        )

        return Ok({"status": "started"})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        """插件关闭"""
        self.logger.info("米家插件关闭中...")

        if self._adapter and self._connected:
            await self._adapter.disconnect() 

        self.push_message(
            source="mijia",
            message_type="text",
            description="插件关闭",
            priority=3,
            content="米家插件已关闭"
        )

        return Ok({"status": "stopped"})

    # ==================== 公开入口点 ====================

    @plugin_entry(
        id="connect",
        name="连接米家",
        description="连接到米家云服务"
    )
    async def connect(self, **_):
        """连接到米家"""
        if not self._adapter:
            return Err(SdkError("适配器未初始化"))

        try:
            self.logger.info("正在连接米家云服务...")

            self.report_status({
                "status": "connecting",
                "message": "正在连接..."
            })

            result = await self._adapter.connect()

            if result:
                self._connected = True
                self.logger.info("米家连接成功")

                self.push_message(
                    source="mijia",
                    message_type="text",
                    description="连接成功",
                    priority=5,
                    content="已连接到米家云服务"
                )

                self.report_status({
                    "status": "connected",
                    "connected": True,
                    "device_count": self._adapter.device_count
                })

                return Ok({
                    "success": True,
                    "message": "连接成功"
                })
            else:
                self.logger.error("米家连接失败")
                return Err(SdkError("连接失败，请检查配置"))

        except Exception as e:
            self.logger.exception(f"连接异常: {e}")
            return Err(SdkError(str(e)))

    @plugin_entry(
        id="disconnect",
        name="断开连接",
        description="断开米家云服务连接"
    )
    async def disconnect(self, **_):
        """断开连接"""
        if not self._adapter or not self._connected:
            return Ok({
                "success": True,
                "message": "已断开"
            })

        try:
            await self._adapter.disconnect()
            self._connected = False

            self.push_message(
                source="mijia",
                message_type="text",
                description="断开连接",
                priority=3,
                content="已断开米家云服务连接"
            )

            self.report_status({
                "status": "disconnected",
                "connected": False
            })

            return Ok({
                "success": True,
                "message": "断开成功"
            })

        except Exception as e:
            self.logger.exception(f"断开连接异常: {e}")
            return Err(SdkError(str(e)))

    @plugin_entry(
        id="discover_devices",
        name="发现设备",
        description="发现米家设备",
        input_schema={
            "type": "object",
            "properties": {
                "online_only": {
                    "type": "boolean",
                    "description": "是否只返回在线设备",
                    "default": False
                },
                "force_refresh": {
                    "type": "boolean",
                    "description": "是否强制刷新（忽略缓存）",
                    "default": False
                }
            }
        }
    )
    async def discover_devices(self, online_only: bool = False, force_refresh: bool = False, **_):
        """发现设备"""
        if not force_refresh and self._device_cache:
            self.logger.info(f"使用缓存设备列表 ({len(self._device_cache)} 个设备)")
            device_list = list(self._device_cache.values())
            return Ok({
                "success": True,
                "devices": device_list,
                "count": len(device_list),
                "cached": True,
                "last_discovery": self._last_discovery_time.isoformat() if self._last_discovery_time else None
            })

        if not self._check_connected():
            return Err(SdkError("未连接到米家云服务，请先调用 connect"))

        try:
            self.logger.info("开始发现设备...")

            self.report_status({
                "status": "discovering",
                "message": "正在发现设备..."
            })

            devices = await self._adapter.discover_devices(online_only=online_only)

            device_list = []
            for device in devices:
                device_info = {
                    "did": device.did,
                    "name": device.name,
                    "model": device.model,
                    "online": getattr(device, 'online', True),
                    "room_id": getattr(device, 'room_id', None),
                    "spec_type": getattr(device, 'spec_type', None),
                    "properties": {},
                    "actions": {},
                    "last_update": datetime.now().isoformat()
                }
                self._device_cache[device.did] = device_info
                device_list.append(device_info)

                asyncio.create_task(self._fetch_device_details(device.did))

            self._last_discovery_time = datetime.now()
            self._save_device_cache()

            self.logger.info(f"发现 {len(device_list)} 个设备")

            self.push_message(
                source="mijia",
                message_type="text",
                description="设备发现完成",
                priority=4,
                content=f"发现 {len(device_list)} 个米家设备",
                metadata={"count": len(device_list)}
            )

            self.report_status({
                "status": "connected",
                "connected": True,
                "device_count": len(device_list)
            })

            return Ok({
                "success": True,
                "devices": device_list,
                "count": len(device_list),
                "cached": False,
                "last_discovery": self._last_discovery_time.isoformat()
            })

        except Exception as e:
            self.logger.exception(f"发现设备失败: {e}")
            return Err(SdkError(str(e)))

    @plugin_entry(
        id="get_devices",
        name="获取设备列表",
        description="获取已发现的设备列表"
    )
    async def get_devices(self, **_):
        """获取设备列表"""
        if not self._device_cache:
            return await self.discover_devices(force_refresh=False, **_)

        return Ok({
            "success": True,
            "devices": list(self._device_cache.values()),
            "count": len(self._device_cache),
            "cached": True,
            "last_discovery": self._last_discovery_time.isoformat() if self._last_discovery_time else None
        })

    @plugin_entry(
        id="get_device_by_name",
        name="按名称查找设备",
        description="根据名称查找设备标识",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "设备名称（支持模糊匹配）"
                }
            },
            "required": ["name"]
        }
    )
    async def get_device_by_name(self, name: str, **_):
        """按名称查找设备"""
        name_lower = name.lower()
        results = []

        for did, device in self._device_cache.items():
            if name_lower in device.get("name", "").lower():
                results.append({
                    "did": did,
                    "name": device.get("name"),
                    "model": device.get("model"),
                    "online": device.get("online", True),
                    "properties": device.get("properties", {}),
                    "actions": device.get("actions", {})
                })

        return Ok({
            "success": True,
            "devices": results,
            "count": len(results),
            "total": len(self._device_cache)
        })

    @plugin_entry(
        id="get_device_by_did",
        name="按ID查找设备",
        description="根据设备ID查找设备标识",
        input_schema={
            "type": "object",
            "properties": {
                "did": {
                    "type": "string",
                    "description": "设备ID"
                }
            },
            "required": ["did"]
        }
    )
    async def get_device_by_did(self, did: str, **_):
        """按设备ID查找设备"""
        device = self._device_cache.get(did)
        if not device:
            return Err(SdkError(f"未找到设备: {did}"))

        return Ok({
            "success": True,
            "device": device
        })

    @plugin_entry(
        id="get_device_properties",
        name="获取设备属性",
        description="获取设备支持的属性列表",
        input_schema={
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "设备ID"
                }
            },
            "required": ["device_id"]
        }
    )
    async def get_device_properties(self, device_id: str, **_):
        """获取设备属性"""
        if not self._check_connected():
            return Err(SdkError("未连接到米家云服务，请先调用 connect"))

        try:
            properties = await self._adapter.get_device_properties(device_id)
            self._update_device_properties_cache(device_id, properties)

            prop_list = []
            for prop in properties:
                prop_list.append({
                    "name": prop.name,
                    "description": prop.desc,
                    "type": prop.type,
                    "rw": prop.rw,
                    "unit": prop.unit,
                    "siid": getattr(prop, 'siid', None),
                    "piid": getattr(prop, 'piid', None),
                    "range": getattr(prop, 'range', None),
                    "value_list": getattr(prop, 'value_list', None)
                })

            return Ok({
                "success": True,
                "device_id": device_id,
                "properties": prop_list,
                "count": len(prop_list)
            })

        except Exception as e:
            self.logger.exception(f"获取设备属性失败: {e}")
            return Err(SdkError(str(e)))

    @plugin_entry(
        id="get_property_value",
        name="获取属性值",
        description="获取设备属性的当前值",
        input_schema={
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "siid": {"type": "integer"},
                "piid": {"type": "integer"}
            },
            "required": ["device_id", "siid", "piid"]
        }
    )
    async def get_property_value(self, device_id: str, siid: int, piid: int, **_):
        """获取属性值"""
        if not self._check_connected():
            return Err(SdkError("未连接到米家云服务，请先调用 connect"))

        try:
            value = await self._adapter.get_property_value(device_id, siid, piid)

            return Ok({
                "success": True,
                "device_id": device_id,
                "siid": siid,
                "piid": piid,
                "value": value
            })

        except Exception as e:
            self.logger.exception(f"获取属性值失败: {e}")
            return Err(SdkError(str(e)))

    @plugin_entry(
        id="set_property_value",
        name="设置属性值",
        description="设置设备属性的值",
        input_schema={
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "siid": {"type": "integer"},
                "piid": {"type": "integer"},
                "value": {"description": "要设置的值"}
            },
            "required": ["device_id", "siid", "piid", "value"]
        }
    )
    async def set_property_value(self, device_id: str, siid: int, piid: int, value: Any, **_):
        """设置属性值"""
        if not self._check_connected():
            return Err(SdkError("未连接到米家云服务，请先调用 connect"))

        try:
            result = await self._adapter.set_property_value(device_id, siid, piid, value)

            if result:
                self.push_message(
                    source="mijia",
                    message_type="text",
                    description="属性设置成功",
                    priority=5,
                    content=f"设备 {device_id} 属性 {siid}:{piid} 已设置为 {value}"
                )

            return Ok({
                "success": result,
                "device_id": device_id,
                "siid": siid,
                "piid": piid,
                "value": value
            })

        except Exception as e:
            self.logger.exception(f"设置属性值失败: {e}")
            return Err(SdkError(str(e)))

    @plugin_entry(
        id="call_action",
        name="调用动作",
        description="调用设备动作",
        input_schema={
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "siid": {"type": "integer"},
                "aiid": {"type": "integer"},
                "params": {
                    "type": "array",
                    "description": "动作参数",
                    "default": []
                }
            },
            "required": ["device_id", "siid", "aiid"]
        }
    )
    async def call_action(self, device_id: str, siid: int, aiid: int, params: Optional[List] = None, **_):
        """调用动作"""
        if not self._check_connected():
            return Err(SdkError("未连接到米家云服务，请先调用 connect"))

        if params is None:
            params = []

        try:
            result = await self._adapter.call_action(device_id, siid, aiid, params)

            self.push_message(
                source="mijia",
                message_type="text",
                description="动作调用成功",
                priority=5,
                content=f"设备 {device_id} 动作 {siid}:{aiid} 已执行"
            )

            return Ok({
                "success": True,
                "device_id": device_id,
                "siid": siid,
                "aiid": aiid,
                "result": result
            })

        except Exception as e:
            self.logger.exception(f"调用动作失败: {e}")
            return Err(SdkError(str(e)))

    @plugin_entry(
        id="search_devices",
        name="搜索设备",
        description="搜索和过滤设备",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词",
                    "default": ""
                },
                "online_only": {
                    "type": "boolean",
                    "description": "是否只显示在线设备",
                    "default": False
                }
            }
        }
    )
    async def search_devices(self, query: str = "", online_only: bool = False, **_):
        """搜索设备"""
        if not self._device_cache:
            return await self.discover_devices(force_refresh=False, **_)

        results = []
        query_lower = query.lower() if query else ""

        for device in self._device_cache.values():
            if query and query_lower not in device.get("name", "").lower() and query_lower not in device.get("model", "").lower():
                continue

            if online_only and not device.get("online", True):
                continue

            results.append(device)

        return Ok({
            "success": True,
            "query": query,
            "online_only": online_only,
            "devices": results,
            "count": len(results),
            "total": len(self._device_cache)
        })

    @plugin_entry(
        id="get_status",
        name="获取状态",
        description="获取插件状态"
    )
    async def get_status(self, **_):
        """获取插件状态"""
        return Ok({
            "success": True,
            "connected": self._connected,
            "device_count": len(self._device_cache),
            "last_discovery": self._last_discovery_time.isoformat() if self._last_discovery_time else None,
            "config": {
                "username": self._config.get("username", "")[:3] + "***" if self._config.get("username") else "",
                "enableQR": self._config.get("enableQR", True),
                "auto_connect": self._config.get("auto_connect", False)
            }
        })

    @plugin_entry(
        id="update_config",
        name="更新配置",
        description="更新插件配置",
        input_schema={
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "password": {"type": "string"},
                "enableQR": {"type": "boolean"},
                "auto_connect": {"type": "boolean"}
            }
        }
    )
    async def update_config(self, username: Optional[str] = None, password: Optional[str] = None,
                            enableQR: Optional[bool] = None, auto_connect: Optional[bool] = None, **_):
        """更新配置"""
        updated = False

        if username is not None:
            self._config["username"] = username
            updated = True
        if password is not None:
            self._config["password"] = password
            updated = True
        if enableQR is not None:
            self._config["enableQR"] = enableQR
            updated = True
        if auto_connect is not None:
            self._config["auto_connect"] = auto_connect
            updated = True

        if updated:
            self._save_config()

            self.push_message(
                source="mijia",
                message_type="text",
                description="配置已更新",
                priority=4,
                content="米家插件配置已更新"
            )

        return Ok({
            "success": True,
            "updated": updated,
            "config": {k: v for k, v in self._config.items() if k != "password"}
        })

    @timer_interval(
        id="heartbeat",
        seconds=60,
        name="心跳",
        description="定期检查连接状态",
        auto_start=True
    )
    async def heartbeat(self, **_):
        """心跳任务"""
        if self._connected and self._adapter:
            try:
                if hasattr(self._adapter, 'connected') and not self._adapter.connected:
                    self._connected = False
                    self.logger.warning("检测到连接已断开")

                    self.push_message(
                        source="mijia",
                        message_type="text",
                        description="连接断开",
                        priority=7,
                        content="米家连接已断开"
                    )

                    self.report_status({
                        "status": "disconnected",
                        "connected": False
                    })
            except Exception as e:
                self.logger.error(f"心跳检查失败: {e}")

        return Ok({"checked": True})
