import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry, lifecycle, timer_interval,
    Ok, Err, SdkError, get_plugin_logger
)

# 导入内嵌的 mijia_api
from .mijia_api import create_async_api_client
from .mijia_api.api_client import AsyncMijiaAPI
from .mijia_api.services.auth_service import AuthService
from .mijia_api.infrastructure.credential_provider import CredentialProvider
from .mijia_api.infrastructure.credential_store import FileCredentialStore
from .mijia_api.domain.models import Credential
from .mijia_api.domain.exceptions import TokenExpiredError, DeviceNotFoundError, DeviceOfflineError

@neko_plugin
class MijiaPlugin(NekoPluginBase):
    """米家智能家居插件"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = get_plugin_logger(__name__)
        self.api: Optional[AsyncMijiaAPI] = None
        self.auth_service: Optional[AuthService] = None
        self.credential_path: Optional[Path] = None
        self._lock = asyncio.Lock()

    # ========== 生命周期 ==========
    @lifecycle(id="startup")
    async def on_startup(self, **_):
        """插件启动：加载凭据并初始化API客户端"""
        self.logger.info("米家插件启动中...")

        # 读取配置
        self.credential_path = self.data_path("credential.json")
        self.logger.debug(f"凭据路径: {self.credential_path}")

        store = FileCredentialStore(default_path=self.credential_path)
        # 创建临时 ConfigManager（后续可从插件配置读取）
        from .mijia_api.core.config import ConfigManager
        config = ConfigManager()
        provider = CredentialProvider(config)
        self.auth_service = AuthService(provider, store)

        # 尝试加载已有凭据
        credential = await self._load_credential()
        if credential:
            await self._init_api(credential)
            self.logger.info("米家插件启动成功，已加载已有凭据")
        else:
            self.logger.warning("未找到有效凭据，请在Web UI中登录")

        # 注册静态UI
        static_dir = self.config_dir / "static"
        if static_dir.exists():
            self.register_static_ui(
                directory=static_dir,
                index_file="config.html",
                cache_control="no-cache"
            )
            self.logger.info("已注册米家配置页面")

        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        """插件关闭：清理资源"""
        self.logger.info("米家插件关闭")
        if self.api:
            # 异步客户端无需显式关闭，但可释放资源
            self.api = None
        return Ok({"status": "stopped"})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        """配置变化（如用户在UI修改了凭据路径）时重新加载"""
        self.logger.info("配置变化，重新加载凭据")
        await self._reload_credential()
        return Ok({"reloaded": True})

    # ========== 凭据管理 ==========
    async def _load_credential(self) -> Optional[Credential]:
        """从文件加载凭据"""
        if not self.credential_path or not self.credential_path.exists():
            return None
        try:
            data = json.loads(self.credential_path.read_text())
            credential = Credential.model_validate(data)
            if credential.is_expired():
                self.logger.warning("凭据已过期，需要刷新")
                # 尝试刷新
                return await self._refresh_credential(credential)
            return credential
        except Exception as e:
            self.logger.error(f"加载凭据失败: {e}")
            return None

    async def _save_credential(self, credential: Credential):
        """保存凭据到文件,权限600"""
        if not self.credential_path:
            self.credential_path = self.data_path("credential.json")
        self.credential_path.parent.mkdir(parents=True, exist_ok=True)
        self.credential_path.write_text(credential.model_dump_json())
        # 设置文件权限（仅所有者可读写）
        self.credential_path.chmod(0o600)
        self.logger.info("凭据已保存")

    async def _refresh_credential(self, credential: Credential) -> Optional[Credential]:
        if not self.auth_service:
            return None
    @plugin_entry(
        id="start_qrcode_login",
        name="开始二维码登录",
        description="获取二维码图片并开始登录流程",
        kind="action"
    )
    async def start_qrcode_login(self, **_):
        if not self.auth_service:
            return Err(SdkError("认证服务未初始化"))
        try:
            qr_url, login_url = await self.auth_service.async_get_qrcode()
            return Ok({"qr_url": qr_url, "login_url": login_url})
        except Exception as e:
            return Err(SdkError(f"生成二维码失败: {e}"))

    @plugin_entry(
        id="check_login_status",
        name="检查登录状态",
        description="轮询检查二维码登录是否成功",
        kind="action"
    )
    async def check_login_status(self, login_url: str, **_):
        if not self.auth_service:
            return Err(SdkError("认证服务未初始化"))
        try:
            credential = await self.auth_service.async_poll_login(login_url, timeout=120)
            if credential:
                await self._save_credential(credential)
                await self._init_api(credential)
                return Ok({"success": True, "user_id": credential.user_id})
            else:
                return Ok({"success": False, "message": "登录超时或未扫码"})
        except Exception as e:
            return Err(SdkError(f"检查登录状态失败: {e}"))

    async def _init_api(self, credential: Credential):
        """使用凭据初始化API客户端"""
        try:
            self.api = await create_async_api_client(credential)
            # 可选：测试连接
            await self.api.get_homes()
            self.logger.info("API客户端初始化成功")
        except Exception as e:
            self.logger.error(f"API初始化失败: {e}")
            self.api = None
            raise

    async def _reload_credential(self):
        """重新加载凭据（如配置变化）"""
        async with self._lock:
            credential = await self._load_credential()
            if credential:
                await self._init_api(credential)
            else:
                self.api = None

    # ========== 定时刷新凭据 ==========
    @timer_interval(id="refresh_credential", seconds=86400, auto_start=True)  # 每天一次
    async def _auto_refresh_credential(self, **_):
        """自动刷新凭据，避免过期"""
        if not self.api:
            return Ok({"skipped": "no_api"})
        credential = self.api.credential
        if credential and not credential.is_expired():
            # 如果将在7天内过期，尝试刷新
            if credential.expires_in() < 7 * 86400:
                self.logger.info("凭据即将过期，尝试刷新")
                new_cred = await self._refresh_credential(credential)
                if new_cred:
                    await self._init_api(new_cred)
                    self.logger.info("凭据刷新成功")
                else:
                    self.logger.warning("凭据刷新失败，请手动登录")
        return Ok({"refreshed": new_cred is not None if 'new_cred' in locals() else False})

    # ========== Web UI 端点（供前端调用） ==========
    
    @plugin_entry(
        id="logout",
        name="登出",
        description="清除保存的凭据",
        kind="action"
    )
    async def logout(self, **_):
        """清除本地凭据"""
        if self.credential_path and self.credential_path.exists():
            self.credential_path.unlink()
        self.api = None
        self.logger.info("已登出，凭据已删除")
        return Ok({"success": True})

    # ========== 核心功能入口 ==========
    @plugin_entry(
        id="list_homes",
        name="获取家庭列表",
        description="获取用户的所有家庭",
        llm_result_fields=["homes"]
    )
    async def list_homes(self, **_):
        """获取家庭列表"""
        if not self.api:
            return Err(SdkError("未登录或凭据无效，请先登录"))
        try:
            homes = await self.api.get_homes()
            # 转换为简单字典供AI使用
            result = [{"id": h.id, "name": h.name} for h in homes]
            return Ok({"homes": result})
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except Exception as e:
            self.logger.exception("获取家庭列表失败")
            return Err(SdkError(f"获取家庭列表失败: {e}"))

    @plugin_entry(
        id="list_devices",
        name="获取设备列表",
        description="获取指定家庭下的设备列表",
        input_schema={
            "type": "object",
            "properties": {
                "home_id": {"type": "string", "description": "家庭ID"}
            },
            "required": ["home_id"]
        },
        llm_result_fields=["devices"]
    )
    async def list_devices(self, home_id: str, **_):
        """获取设备列表"""
        if not self.api:
            return Err(SdkError("未登录"))
        try:
            devices = await self.api.get_devices(home_id)
            result = []
            for d in devices:
                result.append({
                    "did": d.did,
                    "name": d.name,
                    "model": d.model,
                    "is_online": d.is_online(),
                    "room_id": d.room_id
                })
            return Ok({"devices": result})
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except Exception as e:
            self.logger.exception("获取设备列表失败")
            return Err(SdkError(f"获取设备列表失败: {e}"))

    @plugin_entry(
        id="control_device",
        name="控制设备属性",
        description="设置设备的属性值（如开关、亮度等）",
        input_schema={
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "设备ID"},
                "siid": {"type": "integer", "description": "服务ID"},
                "piid": {"type": "integer", "description": "属性ID"},
                "value": {"description": "属性值"}
            },
            "required": ["device_id", "siid", "piid", "value"]
        },
        llm_result_fields=["success"]
    )
    async def control_device(self, device_id: str, siid: int, piid: int, value: Any, **_):
        """控制设备"""
        if not self.api:
            return Err(SdkError("未登录"))
        try:
            success = await self.api.control_device(device_id, siid, piid, value)
            return Ok({"success": success})
        except DeviceNotFoundError:
            return Err(SdkError("设备不存在"))
        except DeviceOfflineError:
            return Err(SdkError("设备离线"))
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except Exception as e:
            self.logger.exception("控制设备失败")
            return Err(SdkError(f"控制设备失败: {e}"))

    @plugin_entry(
        id="call_device_action",
        name="调用设备操作",
        description="调用设备的操作（如开始清扫、暂停等）",
        input_schema={
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "设备ID"},
                "siid": {"type": "integer", "description": "服务ID"},
                "aiid": {"type": "integer", "description": "操作ID"},
                "params": {"type": "object", "description": "操作参数（可选）"}
            },
            "required": ["device_id", "siid", "aiid"]
        },
        llm_result_fields=["result"]
    )
    async def call_device_action(self, device_id: str, siid: int, aiid: int, params: Optional[dict] = None, **_):
        if not self.api:
            return Err(SdkError("未登录"))
        try:
            result = await self.api.call_device_action(device_id, siid, aiid, params)
            return Ok({"result": result})
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except Exception as e:
            self.logger.exception("调用设备操作失败")
            return Err(SdkError(f"调用设备操作失败: {e}"))

    @plugin_entry(
        id="execute_scene",
        name="执行智能场景",
        description="执行指定的智能场景",
        input_schema={
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "场景ID"}
            },
            "required": ["scene_id"]
        },
        llm_result_fields=["success"]
    )
    async def execute_scene(self, scene_id: str, **_):
        if not self.api:
            return Err(SdkError("未登录"))
        try:
            success = await self.api.execute_scene(scene_id)
            return Ok({"success": success})
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except Exception as e:
            self.logger.exception("执行场景失败")
            return Err(SdkError(f"执行场景失败: {e}"))

    @plugin_entry(
        id="get_device_status",
        name="获取设备属性值",
        description="获取设备的某个属性值",
        input_schema={
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "设备ID"},
                "siid": {"type": "integer", "description": "服务ID"},
                "piid": {"type": "integer", "description": "属性ID"}
            },
            "required": ["device_id", "siid", "piid"]
        },
        llm_result_fields=["value"]
    )
    async def get_device_status(self, device_id: str, siid: int, piid: int, **_):
        if not self.api:
            return Err(SdkError("未登录"))
        try:
            # 注意：mijiaAPI_V2 可能没有直接的get_property方法，但有批量获取
            # 使用 get_device_properties 单个请求
            requests = [{"did": device_id, "siid": siid, "piid": piid}]
            results = await self.api.get_device_properties(requests)
            if results and len(results) > 0:
                return Ok({"value": results[0].get("value")})
            else:
                return Err(SdkError("未获取到属性值"))
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except Exception as e:
            self.logger.exception("获取设备状态失败")
            return Err(SdkError(f"获取设备状态失败: {e}"))

    # ========== 辅助功能：获取设备规格（可选） ==========
    @plugin_entry(
        id="get_device_spec",
        name="获取设备规格",
        description="获取设备的详细规格（属性和操作列表），用于发现可控制的功能",
        input_schema={
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "设备型号"}
            },
            "required": ["model"]
        },
        llm_result_fields=["services"]
    )
    async def get_device_spec(self, model: str, **_):
        if not self.api:
            return Err(SdkError("未登录"))
        try:
            spec = await self.api.get_device_spec(model)
            if spec:
                # 简化返回，只提取服务、属性、操作的关键信息
                services = []
                for s in spec.services:
                    svc = {
                        "siid": s.siid,
                        "type": s.type,
                        "description": s.description,
                        "properties": [],
                        "actions": []
                    }
                    for p in s.properties:
                        svc["properties"].append({
                            "piid": p.piid,
                            "name": p.name,
                            "type": p.type,
                            "access": p.access
                        })
                    for a in s.actions:
                        svc["actions"].append({
                            "aiid": a.aiid,
                            "name": a.name,
                            "description": a.description
                        })
                    services.append(svc)
                return Ok({"services": services})
            else:
                return Err(SdkError("未找到规格"))
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except Exception as e:
            self.logger.exception("获取设备规格失败")
            return Err(SdkError(f"获取设备规格失败: {e}"))
