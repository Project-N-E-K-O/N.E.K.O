import asyncio
import json
import re
import webbrowser
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
        self._background_tasks: set = set()  # 持有后台 Task 引用，防止被 GC 提前回收

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
            try:
                await self._init_api(credential)
                self.logger.info("米家插件启动成功，已加载已有凭据")
            except Exception as e:
                self.logger.error(f"API初始化失败，插件将在未登录状态下运行: {e}")
                task = asyncio.create_task(self._auto_open_config_page())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
        else:
            self.logger.warning("未找到有效凭据，请在Web UI中登录")
            task = asyncio.create_task(self._auto_open_config_page())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        # 注册静态UI
        # register_static_ui 接受相对目录名，内部会拼接 self.config_dir / directory
        # static/ 目录下的入口文件为 config.html
        if (self.config_dir / "static").exists():
            ok = self.register_static_ui(
                "static",
                index_file="index.html",
                cache_control="no-cache, no-store, must-revalidate"
            )
            if ok:
                self.logger.info("已注册米家配置页面，访问路径: /plugin/mijia/ui/")
            else:
                self.logger.warning("注册静态UI失败，请检查 static/config.html 是否存在")

        return Ok({"status": "ready"})
    
    async def _auto_open_config_page(self):
        """延迟打开浏览器配置页面"""
        await asyncio.sleep(2)  # 等待主服务器完全启动
        # 插件 UI 服务器运行在 agent_server 内，默认端口 48916（USER_PLUGIN_SERVER_PORT）
        url = "http://localhost:48916/plugin/mijia/ui/"
        webbrowser.open(url)
        self.logger.info(f"已自动打开配置页面: {url}")

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        """插件关闭：清理资源"""
        self.logger.info("米家插件关闭")
        if self.api:
            try:
                await self.api.close()
            except Exception as e:
                self.logger.warning(f"关闭API客户端时出错: {e}")
            finally:
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
            text = self.credential_path.read_text().strip()
            if not text:
                # 文件存在但内容为空，视同未登录
                return None
            data = json.loads(text)
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
        import sys
        if sys.platform == "win32":
            try:
                import subprocess
                username = subprocess.check_output(
                    ["cmd", "/c", "echo", "%USERNAME%"], text=True
                ).strip()
                path_str = str(self.credential_path)
                # 先移除所有继承权限，再授权当前用户完全控制
                subprocess.run(
                    ["icacls", path_str, "/inheritance:r", "/grant:r", f"{username}:F"],
                    check=False, capture_output=True
                )
            except Exception as e:
                self.logger.warning(f"设置凭据文件权限失败(Windows): {e}")
        else:
            self.credential_path.chmod(0o600)
        self.logger.info("凭据已保存")

    async def _refresh_credential(self, credential: Credential) -> Optional[Credential]:
        if not self.auth_service:
            return None
        try:
            new_cred = await self.auth_service.async_refresh_credential(credential)
            if new_cred:
                await self._save_credential(new_cred)
                self.logger.info("凭据刷新成功并已保存")
            return new_cred
        except Exception as e:
            self.logger.error(f"刷新凭据失败: {e}")
            return None

    def _parse_xiaomi_response(self, text: str) -> dict:
        """解析小米登录返回的 &&&START&&&{...} 格式"""
        marker = "&&&START&&&"
        idx = text.find(marker)
        if idx == -1:
            # 尝试直接解析 JSON
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {}
        json_str = text[idx + len(marker):]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return {}

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
            raw_qr_data, login_url = await self.auth_service.async_get_qrcode()
            # 解析小米原始响应格式 &&&START&&&{...}
            qr_data = self._parse_xiaomi_response(raw_qr_data)
            qr_url = qr_data.get("qr", raw_qr_data)  # 如果解析失败，返回原始数据
            # login_url 也可能是原始格式，尝试解析
            if login_url.startswith("&&&START&&&"):
                login_data = self._parse_xiaomi_response(login_url)
                login_url = login_data.get("loginUrl", login_url)
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
        # 先构建新实例，探活成功后再替换，避免旧连接在验证期间被提前丢弃
        new_api = create_async_api_client(credential)
        try:
            await new_api.get_homes()
        except Exception as e:
            self.logger.error(f"API初始化失败: {e}")
            try:
                await new_api.close()
            except Exception:
                pass
            raise
        
        # 验证通过，关闭旧客户端后原子替换
        old_api = self.api
        self.api = new_api
        if old_api is not None:
            try:
                await old_api.close()
            except Exception as close_err:
                self.logger.warning(f"关闭旧API客户端时出错: {close_err}")
        
        self.logger.info("API客户端初始化成功")

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
        new_cred = None
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
        return Ok({"refreshed": new_cred is not None})

    # ========== Web UI 端点（供前端调用） ==========
    
    @plugin_entry(
        id="logout",
        name="登出",
        description="清除保存的凭据并清空本地数据",
        kind="action"
    )
    async def logout(self, **_):
        """清除本地凭据和数据"""
        # 删除凭据文件
        if self.credential_path and self.credential_path.exists():
            self.credential_path.unlink()
        
        # 清空 data 文件夹
        data_dir = self.data_path()
        if data_dir and data_dir.exists():
            import shutil
            deleted = 0
            for item in data_dir.iterdir():
                try:
                    if item.is_file():
                        item.unlink()
                        deleted += 1
                    elif item.is_dir():
                        shutil.rmtree(item)
                        deleted += 1
                except Exception as e:
                    self.logger.warning(f"删除数据文件失败 {item}: {e}")
        
        self.api = None
        self.logger.info("已登出，凭据和数据已删除")
        return Ok({"success": True, "message": "✅ 已登出，所有本地数据已清除"})

    # ========== 核心功能入口 ==========
    @plugin_entry(
        id="list_homes",
        name="获取家庭列表",
        description="列出当前账号下所有米家家庭及其 ID",
        llm_result_fields=["message"]
    )
    async def list_homes(self, **_):
        """获取家庭列表"""
        if not self.api:
            return Err(SdkError("未登录或凭据无效，请先登录"))
        try:
            homes = await self.api.get_homes()
            # 转换为简单字典供AI使用，过滤掉没有id的家庭
            result = [{"id": h.id, "name": h.name} for h in homes if h.id]
            if not result:
                self.logger.warning(f"获取到 {len(homes)} 个家庭，但都没有有效ID")
            
            # 构建友好消息
            lines = [f"🏠 共有 {len(result)} 个家庭:"]
            for h in result:
                lines.append(f"  • {h.get('name')} (ID: {h.get('id')})")
            message = "\n".join(lines)
            
            return Ok({"success": True, "message": message, "homes": result, "count": len(result)})
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except Exception as e:
            self.logger.exception("获取家庭列表失败")
            return Err(SdkError(f"获取家庭列表失败: {e}"))

    @plugin_entry(
        id="list_devices",
        name="获取设备列表",
        description="从服务器拉取设备列表并写入本地缓存，返回设备名称和在线状态概览",
        input_schema={
            "type": "object",
            "properties": {
                "home_id": {"type": "string", "description": "家庭ID，留空则自动使用第一个家庭"},
                "refresh": {"type": "boolean", "description": "是否强制刷新缓存，默认 false"}
            },
            "required": []
        },
        llm_result_fields=["message"]
    )
    async def list_devices(self, home_id: str = None, refresh: bool = False, **_):
        """获取设备列表并缓存"""
        cache_path = self.data_path("devices_cache.json")
        
        # 如果不强制刷新，尝试从缓存读取
        if not refresh and cache_path.exists():
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                devices = cached.get('devices', [])
                self.logger.info(f"从缓存读取设备列表: {len(devices)} 个设备")
                # 构建友好消息（与网络请求分支保持一致）
                lines = [f"📱 共有 {len(devices)} 个设备（缓存）:"]
                for d in devices:
                    status = "🟢" if d.get("is_online") else "🔴"
                    lines.append(f"  {status} {d.get('name')} (型号: {d.get('model')})")
                message = "\n".join(lines)
                return Ok({"success": True, "message": message, "devices": devices, "from_cache": True, "count": len(devices)})
            except Exception as e:
                self.logger.warning(f"读取缓存失败: {e}")
        
        if not self.api:
            return Err(SdkError("未登录"))
        
        # 如果 home_id 为空，尝试获取第一个家庭
        if not home_id:
            try:
                homes = await self.api.get_homes()
                valid_homes = [h for h in homes if h.id]
                if not valid_homes:
                    return Err(SdkError("没有可用的家庭，请先创建家庭或检查登录状态"))
                home_id = valid_homes[0].id
            except Exception as e:
                return Err(SdkError(f"无法获取默认家庭: {e}"))
        
        try:
            devices = await self.api.get_devices(home_id)
            result = []
            for d in devices:
                device_info = {
                    "did": d.did,
                    "name": d.name,
                    "model": d.model,
                    "is_online": d.is_online(),
                    "room_id": d.room_id
                }
                
                # 获取设备规格并缓存关键信息（siid, piid, aiid）
                if d.model:
                    try:
                        spec = await self.api.get_device_spec(d.model)
                        if spec:
                            # 缓存属性信息（包含 siid, piid）
                            properties = []
                            for p in spec.properties:
                                prop = {
                                    "siid": p.siid,
                                    "piid": p.piid,
                                    "name": p.name,
                                    "type": p.type.value if hasattr(p.type, 'value') else str(p.type),
                                    "access": p.access.value if hasattr(p.access, 'value') else str(p.access)
                                }
                                if p.value_range:
                                    prop["value_range"] = p.value_range
                                if p.value_list:
                                    prop["value_list"] = p.value_list
                                properties.append(prop)
                            
                            # 缓存操作信息（包含 siid, aiid）
                            actions = []
                            for a in spec.actions:
                                action = {
                                    "siid": a.siid,
                                    "aiid": a.aiid,
                                    "name": a.name
                                }
                                actions.append(action)
                            
                            device_info["properties"] = properties
                            device_info["actions"] = actions
                    except Exception as e:
                        self.logger.debug(f"获取设备 {d.name}({d.model}) 规格失败: {e}")
                
                result.append(device_info)
            
            # 保存到缓存
            try:
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump({"devices": result, "home_id": home_id}, f, ensure_ascii=False, indent=2)
                self.logger.info(f"设备列表已缓存: {len(result)} 个设备")
            except Exception as e:
                self.logger.warning(f"保存缓存失败: {e}")
            
            # 构建友好消息
            lines = [f"📱 共有 {len(result)} 个设备:"]
            for d in result:
                status = "🟢" if d.get("is_online") else "🔴"
                lines.append(f"  {status} {d.get('name')} (型号: {d.get('model')})")
            message = "\n".join(lines)
            
            return Ok({"success": True, "message": message, "devices": result, "from_cache": False, "count": len(result)})
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except Exception as e:
            self.logger.exception("获取设备列表失败")
            return Err(SdkError(f"获取设备列表失败: {e}"))

    @plugin_entry(
        id="get_cached_devices",
        name="获取缓存的设备列表",
        description="读取本地缓存的设备列表，无网络请求，速度快。缓存不存在时自动调用 list_devices 获取",
        input_schema={
            "type": "object",
            "properties": {
                "refresh": {"type": "boolean", "description": "是否强制刷新缓存，默认 false"}
            },
            "required": []
        },
        llm_result_fields=["message"]
    )
    async def get_cached_devices(self, refresh: bool = False, **_):
        """获取缓存的设备列表"""
        cache_path = self.data_path("devices_cache.json")
        
        if not refresh and cache_path.exists():
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                devices = cached.get('devices', [])
                self.logger.info(f"AI 从缓存读取设备列表: {len(devices)} 个设备")
                
                # 构建友好消息
                lines = [f"📱 共有 {len(devices)} 个设备:"]
                for d in devices:
                    status = "🟢" if d.get("is_online") else "🔴"
                    lines.append(f"  {status} {d.get('name')} (型号: {d.get('model')})")
                message = "\n".join(lines)
                
                return Ok({"success": True, "message": message, "devices": devices, "from_cache": True, "count": len(devices)})
            except Exception as e:
                self.logger.warning(f"读取缓存失败: {e}")
        
        # 缓存不存在或刷新，调用 list_devices
        return await self.list_devices(refresh=refresh)

    @plugin_entry(
        id="find_device_by_name",
        name="根据名称查找设备",
        description="按名称模糊搜索设备，返回匹配设备的完整信息（did、properties、actions 等）",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "设备名称或部分名称，如 '插座'、'灯'"}
            },
            "required": ["name"]
        },
        llm_result_fields=["message"]
    )
    async def find_device_by_name(self, name: str, **_):
        """根据名称查找设备"""
        cache_path = self.data_path("devices_cache.json")
        
        if not cache_path.exists():
            # 缓存不存在，先获取设备列表
            result = await self.list_devices()
            if result.is_err():
                return result
            devices = result.value.get('devices', [])
        else:
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                devices = cached.get('devices', [])
            except Exception as e:
                return Err(SdkError(f"读取缓存失败: {e}"))
        
        # 模糊匹配设备名称
        name_lower = name.lower()
        matched = []
        for d in devices:
            if name_lower in d.get('name', '').lower():
                matched.append(d)
        
        if not matched:
            return Err(SdkError(f"未找到名称包含 '{name}' 的设备"))
        
        # 构建友好消息
        lines = [f"🔍 找到 {len(matched)} 个匹配 '{name}' 的设备:"]
        for d in matched:
            status = "🟢 在线" if d.get("is_online") else "🔴 离线"
            lines.append(f"  • {d.get('name')} ({status})")
            lines.append(f"    型号: {d.get('model')}")
            lines.append(f"    DID: {d.get('did')}")
            if d.get("properties"):
                lines.append(f"    属性数: {len(d.get('properties', []))}")
        message = "\n".join(lines)
        
        return Ok({"success": True, "message": message, "devices": matched, "count": len(matched)})

    @plugin_entry(
        id="smart_control",
        name="智能控制设备",
        description="用自然语言控制设备开关，如'打开插座'、'关闭灯'，自动匹配设备并执行",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "控制命令，如'打开插座'、'关闭灯'"}
            },
            "required": ["command"]
        },
        llm_result_fields=["message"]
    )
    async def smart_control(self, command: str, **_):
        """智能控制：用户说'打开插座'，自动完成搜索+控制"""
        if not self.api:
            return Err(SdkError("未登录"))
        
        self.logger.info(f"智能控制命令: {command}")
        
        cmd = command.lower().strip()
        
        # 判断开关并提取设备名
        # 优先匹配长词，且只移除开头的控制词（避免误删设备名中的字）
        turn_on = None
        device_name = cmd
        
        # 按长度降序排列，优先匹配长词
        open_keywords = ["打开", "开启", "开"]
        close_keywords = ["关闭", "关掉", "关"]
        
        for keyword in open_keywords:
            if device_name.startswith(keyword):
                turn_on = True
                device_name = device_name[len(keyword):].strip()
                break
        
        if turn_on is None:
            for keyword in close_keywords:
                if device_name.startswith(keyword):
                    turn_on = False
                    device_name = device_name[len(keyword):].strip()
                    break
        
        if turn_on is None:
            return Err(SdkError("请说'打开'或'关闭'"))
        
        if not device_name:
            return Err(SdkError("请指定设备名，如'打开插座'"))
        
        self.logger.info(f"解析结果: 设备='{device_name}', 操作={'开' if turn_on else '关'}")
        
        # 查找设备
        result = await self.find_device_by_name(name=device_name)
        if result.is_err():
            self.logger.error(f"查找设备失败: {result.error}")
            return result
        
        devices = result.value.get("devices", [])
        count = result.value.get("count", len(devices))
        self.logger.info(f"找到 {count} 个设备, devices列表长度={len(devices)}")
        
        if not devices:
            return Err(SdkError(f"未找到'{device_name}'"))
        
        # 多设备匹配时返回歧义错误，避免误操作
        if len(devices) > 1:
            device_names = [d.get("name", "未知") for d in devices]
            return Err(SdkError(
                f"找到多个匹配 '{device_name}' 的设备: {', '.join(device_names)}。"
                f"请使用更精确的设备名称，或通过 find_device_by_name 查看完整列表后使用 control_device 精确控制。"
            ))
        
        device = devices[0]
        self.logger.info(f"设备数据: {device}")
        
        did = device.get("did")
        name = device.get("name", device_name)
        props = device.get("properties", [])
        
        self.logger.info(f"使用设备: name={name}, did={did}, 属性数={len(props)}")
        
        # 找开关属性
        switch = None
        for p in props:
            pname = p.get("name", "").lower()
            if any(k in pname for k in ["开关", "电源", "power", "switch"]):
                if p.get("access") in ["write", "read_write"]:
                    switch = p
                    self.logger.info(f"找到开关属性: {p}")
                    break
        
        if not switch:
            # 找第一个可写的bool
            for p in props:
                if p.get("access") in ["write", "read_write"] and p.get("type") == "bool":
                    switch = p
                    self.logger.info(f"找到bool属性: {p}")
                    break
        
        if not switch:
            self.logger.error(f"设备 '{name}' 没有可控制的开关属性")
            return Err(SdkError(f"'{name}'没有可控制的开关"))
        
        siid = switch.get("siid")
        piid = switch.get("piid")
        self.logger.info(f"准备控制: did={did}, siid={siid}, piid={piid}, value={turn_on}")
        
        # 执行控制
        try:
            success = await self.api.control_device(did, siid, piid, turn_on)
            action = "打开" if turn_on else "关闭"
            self.logger.info(f"控制结果: success={success}")
            if success:
                message = f"✅ 已{action}'{name}'"
                return Ok({"success": True, "message": message, "device": name, "action": action})
            else:
                message = f"❌ {action}'{name}'失败"
                return Ok({"success": False, "message": message})
        except Exception as e:
            self.logger.exception("控制失败")
            return Err(SdkError(f"控制失败: {e}"))

    @plugin_entry(
        id="control_device",
        name="控制设备属性",
        description="向设备写入属性值，用于精确控制开关、亮度、温度等属性",
        input_schema={
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "设备 ID（did）"},
                "siid": {"type": "integer", "description": "服务 ID"},
                "piid": {"type": "integer", "description": "属性 ID"},
                "value": {"description": "目标属性值"}
            },
            "required": ["device_id", "siid", "piid", "value"]
        },
        llm_result_fields=["message"]
    )
    async def control_device(self, device_id: str, siid: int, piid: int, value: Any, **_):
        """控制设备"""
        if not self.api:
            return Err(SdkError("未登录"))
        try:
            success = await self.api.control_device(device_id, siid, piid, value)
            if success:
                message = f"✅ 设备控制成功 (did={device_id}, siid={siid}, piid={piid}, value={value})"
                return Ok({"success": True, "message": message, "device_id": device_id, "value": value})
            else:
                message = f"❌ 设备控制失败 (did={device_id})"
                return Ok({"success": False, "message": message})
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
        description="触发设备的预定义操作，如扫地机开始清扫、播放音乐等",
        input_schema={
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "设备 ID（did）"},
                "siid": {"type": "integer", "description": "服务 ID"},
                "aiid": {"type": "integer", "description": "操作 ID"},
                "params": {"type": "array", "description": "操作参数列表，可选"}
            },
            "required": ["device_id", "siid", "aiid"]
        },
        llm_result_fields=["message"]
    )
    async def call_device_action(self, device_id: str, siid: int, aiid: int, params: Optional[list] = None, **_):
        if not self.api:
            return Err(SdkError("未登录"))
        # 无参 action 需要空列表，不能透传 None（下层协议期望 list 而非 null）
        normalized_params: list = params if params is not None else []
        try:
            result = await self.api.call_device_action(device_id, siid, aiid, normalized_params)
            message = f"✅ 操作执行成功 (siid={siid}, aiid={aiid})"
            return Ok({"success": True, "message": message, "result": result})
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except Exception as e:
            self.logger.exception("调用设备操作失败")
            return Err(SdkError(f"调用设备操作失败: {e}"))

    @plugin_entry(
        id="execute_scene",
        name="执行智能场景",
        description="触发米家 App 中预设的智能场景",
        input_schema={
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "场景 ID"},
                "home_id": {"type": "string", "description": "家庭 ID"}
            },
            "required": ["scene_id", "home_id"]
        },
        llm_result_fields=["message"]
    )
    async def execute_scene(self, scene_id: str, home_id: str, **_):
        if not self.api:
            return Err(SdkError("未登录"))
        try:
            success = await self.api.execute_scene(scene_id, home_id)
            if success:
                message = f"✅ 场景执行成功 (ID: {scene_id})"
                return Ok({"success": True, "message": message})
            else:
                message = f"❌ 场景执行失败 (ID: {scene_id})"
                return Ok({"success": False, "message": message})
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except Exception as e:
            self.logger.exception("执行场景失败")
            return Err(SdkError(f"执行场景失败: {e}"))

    @plugin_entry(
        id="get_device_status",
        name="获取设备属性值",
        description="读取设备的单个属性当前值",
        input_schema={
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "设备 ID（did）"},
                "siid": {"type": "integer", "description": "服务 ID"},
                "piid": {"type": "integer", "description": "属性 ID"}
            },
            "required": ["device_id", "siid", "piid"]
        },
        llm_result_fields=["message"]
    )
    async def get_device_status(self, device_id: str, siid: int, piid: int, **_):
        """获取设备单个属性值"""
        if not self.api:
            return Err(SdkError("未登录"))
        try:
            requests = [{"did": device_id, "siid": siid, "piid": piid}]
            results = await self.api.get_device_properties(requests)
            if results and len(results) > 0:
                value = results[0].get("value")
                code = results[0].get("code", 0)
                if code == 0:
                    message = f"📊 属性值: {value} (siid={siid}, piid={piid})"
                    return Ok({"success": True, "value": value, "message": message, "device_id": device_id})
                else:
                    return Err(SdkError(f"查询失败，错误码: {code}"))
            else:
                return Err(SdkError("未获取到属性值"))
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except Exception as e:
            self.logger.exception("获取设备状态失败")
            return Err(SdkError(f"获取设备状态失败: {e}"))

    # ========== 辅助功能：获取设备规格（可选） ==========
    @plugin_entry(
        id="query_device_state",
        name="查询设备状态",
        description="按设备名称查询所有可读属性的当前值，返回格式化的状态汇总",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "设备名称或部分名称，如 '插座'、'灯'"}
            },
            "required": ["name"]
        },
        llm_result_fields=["message"]
    )
    async def query_device_state(self, name: str, **_):
        """根据设备名称查询设备状态"""
        if not self.api:
            return Err(SdkError("未登录"))
        
        # 查找设备
        result = await self.find_device_by_name(name=name)
        if result.is_err():
            return result
        
        devices = result.value.get("devices", [])
        if not devices:
            return Err(SdkError(f"未找到'{name}'"))
        
        # 多设备匹配时返回歧义错误，避免查询到错误设备
        if len(devices) > 1:
            device_names = [d.get("name", "未知") for d in devices]
            return Err(SdkError(
                f"找到多个匹配 '{name}' 的设备: {', '.join(device_names)}。"
                f"请使用更精确的设备名称。"
            ))
        
        device = devices[0]
        did = device.get("did")
        device_name = device.get("name", name)
        props = device.get("properties", [])
        
        if not props:
            return Ok({
                "success": True,
                "message": f"📱 设备 '{device_name}' 没有可查询的属性",
                "device": device_name,
                "states": []
            })
        
        # 构建查询请求（所有可读属性）
        requests = []
        readable_props = []
        for p in props:
            access = p.get("access", "")
            if access in ["read", "read_write", "notify_read", "notify_read_write"]:
                requests.append({
                    "did": did,
                    "siid": p.get("siid"),
                    "piid": p.get("piid")
                })
                readable_props.append(p)
        
        if not requests:
            return Ok({
                "success": True,
                "message": f"📱 设备 '{device_name}' 没有可读属性",
                "device": device_name,
                "states": []
            })
        
        try:
            results = await self.api.get_device_properties(requests)
            
            # 用 (siid, piid) 建立索引，不依赖返回顺序
            result_map = {}
            for res in results:
                key = (res.get("siid"), res.get("piid"))
                result_map[key] = res
            
            # 整理状态信息
            states = []
            lines = [f"📱 设备 '{device_name}' 当前状态："]
            lines.append("")

            # 属性名本地化映射（英文 -> 中文）
            NAME_MAP = {
                # 设备信息类
                "Device Manufacturer": "设备制造商",
                "Device Model": "设备型号",
                "Device ID": "设备ID",
                "Current Firmware Version": "当前固件版本",
                "Serial Number": "序列号",
                "Device Name": "设备名称",
                "Device Location": "设备位置",
                "Model": "型号",
                "Manufacturer": "制造商",
                "Firmware Version": "固件版本",
                "Hardware Version": "硬件版本",
                "MAC Address": "MAC地址",
                "IP Address": "IP地址",
                "RSSI": "信号强度",
                "Battery Level": "电池电量",
                "Battery Voltage": "电池电压",
                "Charging State": "充电状态",
                "Low Battery": "低电量",
                
                # 开关控制类
                "Switch Status": "开关状态",
                "Power": "电源",
                "On": "开启",
                "Off": "关闭",
                "Toggle": "切换",
                "Default Power On State": "默认通电状态",
                "Power Off Memory": "断电记忆",
                "Physical Control Locked": "物理控制锁定",
                "Child Lock": "童锁",
                
                # 功率电量类
                "Electric Power": "实时功率",
                "Power Consumption": "累计用电量",
                "Voltage": "电压",
                "Current": "电流",
                "Load Power": "负载功率",
                "Total Consumption": "总用电量",
                "Today Consumption": "今日用电量",
                "Month Consumption": "本月用电量",
                "Power Factor": "功率因数",
                "Leakage Current": "漏电流",
                "Surge Power": "浪涌功率",
                "over-ele-day": "日用电超限阈值",
                "over-ele-month": "月用电超限阈值",
                "on-off-count": "开关次数",
                
                # 照明类
                "Brightness": "亮度",
                "Color Temperature": "色温",
                "Color": "颜色",
                "Hue": "色相",
                "Saturation": "饱和度",
                "Light Mode": "灯光模式",
                "Scene": "场景",
                "Night Light": "夜灯",
                "Ambient Light": "氛围灯",
                "Illuminance": "照度",
                "Colorful": "彩光模式",
                "Flow": "流光模式",
                
                # 环境传感器类
                "temperature": "温度",
                "Temperature": "温度",
                "humidity": "湿度",
                "Humidity": "湿度",
                "PM2.5": "PM2.5",
                "PM10": "PM10",
                "CO2": "二氧化碳",
                "TVOC": "总挥发性有机物",
                "Formaldehyde": "甲醛",
                "AQI": "空气质量指数",
                "Air Quality": "空气质量",
                "Air Quality Level": "空气质量等级",
                "Pressure": "气压",
                "Noise": "噪音",
                "Light Intensity": "光照强度",
                "UV Index": "紫外线指数",
                "Water Leak": "水浸检测",
                "Smoke Alarm": "烟雾报警",
                "Gas Alarm": "燃气报警",
                "Door Status": "门状态",
                "Window Status": "窗状态",
                "Motion Detection": "移动检测",
                "Occupancy": "有人/无人",
                
                # 空调/温控类
                "Target Temperature": "目标温度",
                "Current Temperature": "当前温度",
                "Mode": "模式",
                "Fan Speed": "风速",
                "Fan Level": "风量档位",
                "Swing Mode": "摆风模式",
                "Vertical Swing": "上下摆风",
                "Horizontal Swing": "左右摆风",
                "Sleep Mode": "睡眠模式",
                "Eco Mode": "节能模式",
                "Dry Mode": "除湿模式",
                "Heat Mode": "制热模式",
                "Cool Mode": "制冷模式",
                "Auto Mode": "自动模式",
                "Heating": "加热中",
                "Cooling": "制冷中",
                "Defrosting": "除霜中",
                
                # 窗帘/电机类
                "Motor Control": "电机控制",
                "Motor Reverse": "电机反转",
                "Position": "位置",
                "Current Position": "当前位置",
                "Target Position": "目标位置",
                "Run Time": "运行时间",
                
                # 安防类
                "Alarm": "警报",
                "Alarm Volume": "警报音量",
                "Alarm Duration": "警报时长",
                "Guard Mode": "守护模式",
                "Away Mode": "离家模式",
                "Home Mode": "在家模式",
                "Sleep Mode Guard": "睡眠守护",
                
                # 定时/倒计时类
                "start-time": "开始时间",
                "end-time": "结束时间",
                "duration": "持续时长",
                "left-time": "剩余时间",
                "countdown": "倒计时",
                "Timer": "定时器",
                "Schedule": "定时任务",
                
                # 状态/故障类
                "status": "状态",
                "mode": "模式",
                "on": "开启状态",
                "power": "功率设定",
                "data-value": "数据值",
                "Device Fault": "设备故障",
                "Fault": "故障",
                "Error": "错误",
                "Error Code": "错误代码",
                "Working Time": "工作时间",
                "Remaining Time": "剩余时间",
                "Filter Life": "滤芯寿命",
                "Filter Used Time": "滤芯已用时间",
                "protect-time": "保护时间",
            }

            # 硬编码单位映射（属性名 -> 单位）
            UNIT_MAP = {
                "Electric Power": "W",
                "Power Consumption": "kWh",
                "Voltage": "V",
                "Current": "A",
                "temperature": "°C",
                "Temperature": "°C",
                "humidity": "%",
                "Humidity": "%",
            }

            for prop in readable_props:
                key = (prop.get("siid"), prop.get("piid"))
                res = result_map.get(key)
                if res is None:
                    continue

                siid = prop.get("siid")
                piid = prop.get("piid")
                original_name = prop.get("name", f"属性{piid}")

                # 属性名本地化（保留原始名用于调试）
                display_name = NAME_MAP.get(original_name, original_name)

                value = res.get("value")
                code = res.get("code", -1)
                # 优先使用 spec 中的 unit，否则使用硬编码映射
                unit = prop.get("unit") or UNIT_MAP.get(original_name)

                if code == 0:
                    # 格式化值
                    if isinstance(value, bool):
                        value_str = "✅ 开启" if value else "❌ 关闭"
                    else:
                        value_str = str(value)
                        # 添加单位
                        if unit:
                            value_str = f"{value_str} {unit}"

                    states.append({
                        "name": display_name,
                        "original_name": original_name,
                        "value": value,
                        "siid": siid,
                        "piid": piid,
                        "unit": unit
                    })
                    lines.append(f"  • {display_name}: {value_str}")
            
            if not states:
                lines.append("  （暂无可用状态数据）")
            
            message = "\n".join(lines)
            return Ok({
                "success": True,
                "message": message,
                "device": device_name,
                "states": states
            })
            
        except Exception as e:
            self.logger.exception("查询设备状态失败")
            return Err(SdkError(f"查询设备状态失败: {e}"))

    @plugin_entry(
        id="get_device_spec",
        name="获取设备规格",
        description="查询设备型号的完整规格，列出所有可控属性（含 siid/piid）和可调用操作（含 siid/aiid）",
        input_schema={
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "设备型号，如 cuco.plug.v3"}
            },
            "required": ["model"]
        },
        llm_result_fields=["message"]
    )
    async def get_device_spec(self, model: str, **_):
        if not self.api:
            return Err(SdkError("未登录"))
        if not model:
            return Err(SdkError("设备型号(model)不能为空"))
        try:
            spec = await self.api.get_device_spec(model)
            if spec:
                # 简化返回，只提取属性、操作的关键信息
                properties = []
                actions = []
                
                for p in spec.properties:
                    prop = {
                        "siid": p.siid,
                        "piid": p.piid,
                        "name": p.name,
                        "type": p.type.value if hasattr(p.type, 'value') else str(p.type),
                        "access": p.access.value if hasattr(p.access, 'value') else str(p.access)
                    }
                    if p.value_range:
                        prop["value_range"] = p.value_range
                    if p.value_list:
                        prop["value_list"] = p.value_list
                    properties.append(prop)
                
                for a in spec.actions:
                    action = {
                        "siid": a.siid,
                        "aiid": a.aiid,
                        "name": a.name,
                        "parameters": [
                            {
                                "name": param.name,
                                "type": param.type.value if hasattr(param.type, 'value') else str(param.type)
                            }
                            for param in a.parameters
                        ]
                    }
                    actions.append(action)
                
                # 构建友好的消息
                lines = [f"📋 设备规格: {spec.name} ({model})", ""]
                
                lines.append(f"【属性】共 {len(properties)} 个:")
                for p in properties:
                    access_icon = "🔘" if "write" in p.get("access", "") else "👁"
                    lines.append(f"  {access_icon} {p.get('name')} (siid={p.get('siid')}, piid={p.get('piid')}, type={p.get('type')})")
                
                lines.append("")
                lines.append(f"【操作】共 {len(actions)} 个:")
                for a in actions:
                    lines.append(f"  ▶ {a.get('name')} (siid={a.get('siid')}, aiid={a.get('aiid')})")
                
                message = "\n".join(lines)
                
                return Ok({
                    "success": True,
                    "message": message,
                    "model": spec.model,
                    "name": spec.name,
                    "properties": properties,
                    "actions": actions
                })
            else:
                return Err(SdkError("未找到规格"))
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except Exception as e:
            self.logger.exception("获取设备规格失败")
            return Err(SdkError(f"获取设备规格失败: {e}"))
