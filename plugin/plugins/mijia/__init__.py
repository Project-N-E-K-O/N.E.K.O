import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from utils.file_utils import atomic_write_json_async, read_json_async

from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry, lifecycle, timer_interval,
    ui, tr, Ok, Err, SdkError, get_plugin_logger
)

from config import USER_PLUGIN_BASE


# ── 同步 helper（已禁用自动跳转，仅作备用）──────────────────────────────
def _open_url_in_browser(url: str) -> None:
    """在系统默认浏览器中打开 URL（同步，通过 to_thread 调用）"""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
    except Exception:
        raise


# 导入内嵌的 mijia_api
from .mijia_api import create_async_api_client
from .mijia_api.api_client import AsyncMijiaAPI
from .mijia_api.services.auth_service import AuthService
from .mijia_api.infrastructure.credential_provider import CredentialProvider
from .mijia_api.infrastructure.credential_store import FileCredentialStore
from .mijia_api.domain.models import Credential
from .mijia_api.domain.exceptions import TokenExpiredError, DeviceNotFoundError, DeviceOfflineError, MijiaAPIException

# 导入 NLP 规则引擎（纯函数，见 nlp/ 子包）
from .nlp import MatchResult, RouteResult, match_devices, route
from .nlp.action_verbs import VERB_TO_ACTION
from .nlp.intent_terms import SCENE_RE
from .nlp.value_resolver import resolve_adjust_target

_EMBEDDED_BY_AGENT = os.getenv("NEKO_PLUGIN_HOSTED_BY_AGENT", "").strip().lower() == "true"

# 设备缓存 schema 版本：value_list 从纯值列表升级为带 description/comment 的
# dict 列表（v2）。读缓存时校验版本，不匹配则视为失效重新拉取，避免旧缓存缺
# 枚举描述导致中文模式命令失效。
_DEVICES_CACHE_SCHEMA = 2

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

    # ========== Hosted UI ==========
    @ui.context(id="dashboard", title="米家智能家居控制面板")
    async def get_dashboard_context(self):
        """为 Hosted UI 面板提供状态数据"""
        logged_in = self.api is not None
        homes = []
        devices = []
        scenes = []

        if logged_in:
            try:
                # 获取家庭列表
                raw_homes = await self.api.get_homes()
                homes = [{"id": h.id, "name": h.name} for h in raw_homes if h.id]

                # 获取设备列表（从缓存，需归属校验防止跨用户泄露）
                cache_path = self.data_path("devices_cache.json")
                try:
                    cached = await read_json_async(cache_path)
                except Exception:
                    cached = None
                if cached is not None:
                    cache_user_id = cached.get('user_id')
                    current_user_id = self.api.credential.user_id if self.api.credential else None
                    if not current_user_id or cache_user_id == current_user_id:
                        devices = cached.get("devices", [])
                    else:
                        self.logger.debug(f"设备缓存归属不匹配(u={cache_user_id}→{current_user_id})，跳过")

                # 获取场景列表（从缓存，需归属校验防止跨用户泄露）
                scenes_cache_path = self.data_path("scenes_cache.json")
                try:
                    cached = await read_json_async(scenes_cache_path)
                except Exception:
                    cached = None
                if cached is not None:
                    cache_user_id = cached.get('user_id')
                    current_user_id = self.api.credential.user_id if self.api.credential else None
                    if not current_user_id or cache_user_id == current_user_id:
                        scenes = cached.get("scenes", [])
                    else:
                        self.logger.debug(f"场景缓存归属不匹配(u={cache_user_id}→{current_user_id})，跳过")
            except Exception as e:
                self.logger.warning(f"获取UI状态失败: {e}")

        return {
            "logged_in": logged_in,
            "homes": homes,
            "devices": devices,
            "scenes": scenes,
            "device_count": len(devices),
            "scene_count": len(scenes),
            "online_count": sum(1 for d in devices if d.get("is_online")),
        }

    # ========== 生命周期 ==========
    @lifecycle(id="startup")
    async def on_startup(self, **_):
        """插件启动：加载凭据并初始化API客户端"""
        self.logger.info("米家插件启动中...")

        # 读取配置
        self.credential_path = self.data_path("credential.json")
        self.logger.debug(f"凭据路径: {self.credential_path}")

        # 后台静默加载凭据，不阻塞启动
        task = asyncio.create_task(self._background_load_credential())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        # 注册静态UI
        # register_static_ui 接受相对目录名，内部会拼接 self.config_dir / directory
        # static/ 目录下的入口文件为 index.html
        if (self.config_dir / "static").exists():
            ok = self.register_static_ui(
                "static",
                index_file="index.html",
                cache_control="no-cache, no-store, must-revalidate"
            )
            if ok:
                self.logger.info("已注册米家配置页面，访问路径: /plugin/mijia/ui/")
            else:
                self.logger.warning("注册静态UI失败，请检查 static/index.html 是否存在")

        return Ok({"status": "ready"})

    async def _background_load_credential(self):
        """后台静默加载凭据，不阻塞插件启动"""
        try:
            store = FileCredentialStore(default_path=self.credential_path)
            from .mijia_api.core.config import ConfigManager
            config = ConfigManager()
            provider = CredentialProvider(config)
            self.auth_service = AuthService(provider, store)

            credential = await self._load_credential()
            if credential:
                try:
                    await self._init_api(credential)
                    self.logger.info("米家插件启动成功，已加载已有凭据")
                except Exception as e:
                    self.logger.error(f"API初始化失败，插件将在未登录状态下运行: {e}")
            else:
                self.logger.warning("未找到有效凭据，请在Web UI中登录")
        except Exception as e:
            self.logger.error(f"后台加载凭据失败: {e}")



    def _ensure_auth_service(self):
        """懒加载初始化认证服务（供手动入口调用，避免启动时阻塞）"""
        if self.auth_service:
            return
        from .mijia_api.core.config import ConfigManager
        config = ConfigManager()
        store = FileCredentialStore(default_path=self.credential_path)
        provider = CredentialProvider(config)
        self.auth_service = AuthService(provider, store)

    async def _build_room_maps(self) -> tuple[dict[str, str], dict[str, str]]:
        """构建 room_id→room_name 和 device_did→room_name 两种映射

        device_did→room_name 映射从 gethome_merged 的 roomlist 中提取，
        用于设备列表 API 不返回 room_id 时的降级方案。
        """
        if not self.api:
            self.logger.info("构建房间映射跳过：API 未就绪")
            return {}, {}
        try:
            homes = await self.api.get_homes()
            room_map: dict[str, str] = {}
            device_room_map: dict[str, str] = {}
            device_field_candidates = ("dids", "devices", "device_list", "child_devices")
            for home in homes:
                if not home.rooms:
                    self.logger.info(f"家庭 {home.name}({home.id}) 无房间数据")
                    continue
                for room in home.rooms:
                    rid = str(room.get("id", ""))
                    rname = room.get("name", "")
                    if not rid or not rname:
                        continue
                    room_map[rid] = rname

                    # 每个房间独立检测设备列表字段，不同 room API 返回可能使用不同 key
                    room_field = next(
                        (f for f in device_field_candidates if f in room),
                        None,
                    )
                    dids = room.get(room_field) if room_field else None
                    if room_field is None:
                        self.logger.debug(f"房间 '{rname}'({rid}) 的 key 不在候选列表中: {list(room.keys())}")
                    if dids and isinstance(dids, list):
                        for did in dids:
                            did_str = str(did)
                            if did_str and did_str not in device_room_map:
                                device_room_map[did_str] = rname

            total_rooms = len(room_map)
            room_names = list(room_map.values())
            from_device = len(device_room_map)
            if from_device:
                self.logger.info(
                    f"房间映射构建完成: {total_rooms} 个房间 {room_names}, "
                    f"设备→房间映射 {from_device} 条"
                )
            else:
                self.logger.info(f"房间映射构建完成: {total_rooms} 个房间 {room_names}, 无设备→房间映射")
            return room_map, device_room_map
        except Exception as e:
            self.logger.warning(f"构建房间映射失败: {e}")
            return {}, {}

    # ========== 设备匹配与命令解析 ==========

    async def _load_devices_cache(self) -> list[dict]:
        """加载设备缓存，不存在或版本不匹配时自动拉取"""
        cache_path = self.data_path("devices_cache.json")
        if self.api:
            try:
                cached = await read_json_async(cache_path)
            except Exception:
                cached = None
            if cached is not None:
                cache_user_id = cached.get('user_id')
                current_user_id = self.api.credential.user_id if self.api.credential else None
                if cached.get("schema_version") != _DEVICES_CACHE_SCHEMA:
                    self.logger.info("设备缓存 schema 版本不匹配，自动刷新")
                elif not current_user_id or cache_user_id == current_user_id:
                    devices = cached.get('devices', [])
                    if devices:
                        self.logger.info(f"设备缓存有效: {len(devices)} 个设备")
                        return devices
                    self.logger.info("设备缓存为空，自动刷新")
        self.logger.info("从 API 刷新设备缓存")
        result = await self.list_devices(refresh=True)
        if result.is_ok():
            fresh_devices = result.value.get('devices', [])
            self.logger.info(f"API 刷新完成: {len(fresh_devices)} 个设备")
            return fresh_devices
        self.logger.info("API 刷新设备列表失败")
        return []

    @plugin_entry(
        id="open_ui",
        name=tr("entries.open_ui.name", default="打开配置页面"),
        description=tr("entries.open_ui.description", default="在浏览器中打开米家插件的 Web UI 配置页面"),
        kind="action"
    )
    async def open_ui(self, **_):
        """在浏览器中打开米家配置页面"""
        url = f"{USER_PLUGIN_BASE}/plugin/mijia/ui/"
        try:
            await asyncio.to_thread(_open_url_in_browser, url)
            self.logger.info(f"已在浏览器中打开: {url}")
            return Ok({"success": True, "url": url, "message": "已在浏览器打开配置页面"})
        except Exception as e:
            self.logger.exception("打开配置页面失败")
            return Err(SdkError(f"打开配置页面失败: {e}"))

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        """插件关闭：清理资源"""
        self.logger.info("米家插件关闭")

        # 取消所有后台任务
        if self._background_tasks:
            for task in list(self._background_tasks):
                task.cancel()
            if self._background_tasks:
                await asyncio.gather(*self._background_tasks, return_exceptions=True)
                self._background_tasks.clear()

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

    @plugin_entry(
        id="reload_credential",
        name=tr("entries.reload_credential.name", default="重新加载凭据"),
        description=tr("entries.reload_credential.description", default="重新从文件加载米家凭据并初始化API，防止插件重载后凭据未及时加载导致显示未登录"),
        kind="action"
    )
    async def reload_credential(self, **_):
        """重新加载凭据（供前端刷新状态前调用）"""
        try:
            await self._reload_credential()
        except Exception as e:
            self.logger.warning(f"reload_credential 失败: {e}")
        return Ok({
            "success": True,
            "logged_in": self.api is not None,
        })

    # ========== 凭据管理 ==========
    async def _load_credential(self) -> Optional[Credential]:
        """从文件加载凭据"""
        if not self.credential_path or not self.credential_path.exists():
            return None
        try:
            text = await asyncio.to_thread(self.credential_path.read_text)
            text = text.strip()
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

        # 确保目录存在（使用 to_thread 避免阻塞）
        await asyncio.to_thread(self.credential_path.parent.mkdir, parents=True, exist_ok=True)

        # 写入凭据内容
        await asyncio.to_thread(
            self.credential_path.write_text, credential.model_dump_json()
        )

        # 设置文件权限（仅所有者可读写）
        if sys.platform == "win32":
            try:
                def _apply_windows_acl() -> tuple[int, str]:
                    username = subprocess.check_output(
                        ["cmd", "/c", "echo", "%USERNAME%"], text=True
                    ).strip()
                    path_str = str(self.credential_path)
                    # 先移除所有继承权限，再授权当前用户完全控制
                    result = subprocess.run(
                        ["icacls", path_str, "/inheritance:r", "/grant:r", f"{username}:F"],
                        check=False, capture_output=True, text=True
                    )
                    return result.returncode, (result.stderr or "").strip()

                returncode, stderr = await asyncio.to_thread(_apply_windows_acl)
                if returncode != 0:
                    self.logger.warning(
                        f"设置凭据文件权限失败(Windows): icacls 返回码 {returncode}"
                        + (f", stderr: {stderr}" if stderr else "")
                    )
                else:
                    self.logger.debug("凭据文件权限已设置（仅当前用户）")
            except Exception as e:
                self.logger.warning(f"设置凭据文件权限失败(Windows): {e}")
        else:
            await asyncio.to_thread(self.credential_path.chmod, 0o600)
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

    @ui.action(label=tr("actions.login.label", default="扫码登录"), tone="primary", group="auth", order=10, refresh_context=True)
    @plugin_entry(
        id="start_qrcode_login",
        name=tr("entries.start_qrcode_login.name", default="开始二维码登录"),
        description=tr("entries.start_qrcode_login.description", default="获取二维码图片并开始登录流程"),
        kind="action"
    )
    async def start_qrcode_login(self, **_):
        self._ensure_auth_service()
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
        name=tr("entries.check_login_status.name", default="检查登录状态"),
        description=tr("entries.check_login_status.description", default="轮询检查二维码登录是否成功"),
        kind="action"
    )
    async def check_login_status(self, login_url: str, **_):
        self._ensure_auth_service()
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
                # 关闭旧 client 再置 None，防止 HttpClient / CacheManager 资源泄漏
                old_api = self.api
                self.api = None
                if old_api is not None:
                    try:
                        await old_api.close()
                    except Exception as close_err:
                        self.logger.warning(f"关闭旧API客户端时出错: {close_err}")

    # ========== 定时刷新凭据 ==========
    @timer_interval(id="refresh_credential", seconds=86400, auto_start=True)  # 每天一次
    async def _auto_refresh_credential(self, **_):
        """自动刷新凭据，避免过期"""
        if not self.api:
            return Ok({"skipped": "no_api"})
        new_cred = None
        credential = self.api.credential
        if credential:
            # 同时处理"7天内即将过期"和"已经过期但尚未处理"两种情况
            if not credential.is_expired() and credential.expires_in() >= 7 * 86400:
                return Ok({"skipped": "not_near_expiry"})
            # 已过期或在7天内，尝试刷新
            if credential.is_expired():
                self.logger.warning("凭据已过期，尝试刷新")
            else:
                self.logger.info("凭据即将过期，尝试刷新")
            new_cred = await self._refresh_credential(credential)
            if new_cred:
                await self._init_api(new_cred)
                self.logger.info("凭据刷新成功")
            else:
                self.logger.warning("凭据刷新失败，请手动登录")
        return Ok({"refreshed": new_cred is not None})

    # ========== Web UI 端点（供前端调用） ==========
    
    @ui.action(label=tr("actions.logout.label", default="登出"), tone="danger", group="auth", order=20, refresh_context=True)
    @plugin_entry(
        id="logout",
        name=tr("entries.logout.name", default="登出"),
        description=tr("entries.logout.description", default="清除保存的凭据并清空本地数据"),
        kind="action"
    )
    async def logout(self, **_):
        """清除本地凭据和数据"""
        # 删除凭据文件
        if self.credential_path and self.credential_path.exists():
            await asyncio.to_thread(self.credential_path.unlink)

        # 清空 data 文件夹（使用线程避免阻塞）
        data_dir = self.data_path()
        if data_dir and data_dir.exists():

            def _delete_all():
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
                return deleted

            deleted = await asyncio.to_thread(_delete_all)
            self.logger.debug(f"已删除 {deleted} 个数据文件")
        
        # 关闭旧 client 再置 None，防止 HttpClient / CacheManager 资源泄漏
        old_api = self.api
        self.api = None
        self.auth_service = None
        if old_api is not None:
            try:
                await old_api.close()
            except Exception as close_err:
                self.logger.warning(f"关闭旧API客户端时出错: {close_err}")
        self.logger.info("已登出，凭据和数据已删除")
        return Ok({"success": True, "message": "✅ 已登出，所有本地数据已清除"})

    # ========== 核心功能入口 ==========
    @plugin_entry(
        id="list_homes",
        name=tr("entries.list_homes.name", default="获取家庭列表"),
        description=tr("entries.list_homes.description", default="列出当前账号下所有米家家庭及其 ID"),
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

    @ui.action(label=tr("actions.refreshDevices.label", default="刷新设备"), tone="secondary", group="device", order=10, refresh_context=True)
    @plugin_entry(
        id="list_devices",
        name=tr("entries.list_devices.name", default="获取设备列表"),
        description=tr("entries.list_devices.description", default="获取设备列表，home_id留空自动使用第一个家庭，支持缓存"),
        input_schema={
            "type": "object",
            "properties": {
                "home_id": {"type": "string", "description": "家庭ID，留空自动用第一个"},
                "refresh": {"type": "boolean", "description": "是否强制刷新缓存"}
            },
            "required": []
        },
        llm_result_fields=["message"]
    )
    async def list_devices(self, home_id: str = None, refresh: bool = False, **_):
        """获取设备列表并缓存"""
        cache_path = self.data_path("devices_cache.json")

        # 异步读旧缓存（缺失抛 FileNotFoundError，不经同步 exists() 检查）：
        # 用于命中缓存、保留 home_id 与用户自定义别名
        cached = None
        if self.api:
            try:
                cached = await read_json_async(cache_path)
            except Exception:
                cached = None

        current_user_id = self.api.credential.user_id if self.api and self.api.credential else None
        cache_home_id = cached.get('home_id') if cached is not None else None
        cache_user_id = cached.get('user_id') if cached is not None else None

        # 旧缓存里的设备别名（按 DID）：仅在缓存归属严格匹配当前用户时提取，
        # 否则跨账号共享 DID 会把上一账号的自定义别名泄露并持久化到本账号
        old_aliases: dict[str, str] = {}
        if cached is not None and (not current_user_id or cache_user_id == current_user_id):
            for d in cached.get("devices", []) or []:
                did = d.get("did")
                if did and d.get("alias"):
                    old_aliases[did] = d["alias"]

        # schema 不匹配：归属校验通过则保留旧 home_id，刷新仍用同一家庭，避免切错
        if cached is not None and cached.get("schema_version") != _DEVICES_CACHE_SCHEMA:
            self.logger.info("设备缓存 schema 版本不匹配，刷新并保留 home_id")
            if not home_id and (not current_user_id or cache_user_id == current_user_id):
                home_id = cache_home_id

        # 不强制刷新且缓存归属匹配才返回缓存；否则走网络请求
        if not refresh and cached is not None and cached.get("schema_version") == _DEVICES_CACHE_SCHEMA:
            if cache_home_id == home_id and (not current_user_id or cache_user_id == current_user_id):
                devices = cached.get('devices', [])
                self.logger.info(f"从缓存读取设备列表: {len(devices)} 个设备")
                lines = [f"📱 共有 {len(devices)} 个设备（缓存）:"]
                for d in devices:
                    status = "🟢" if d.get("is_online") else "🔴"
                    lines.append(f"  {status} {d.get('name')} (型号: {d.get('model')})")
                message = "\n".join(lines)
                return Ok({"success": True, "message": message, "devices": devices, "from_cache": True, "count": len(devices)})
            self.logger.warning(
                f"缓存归属不匹配(user_id: {cache_user_id}→{current_user_id}, "
                f"home_id: {cache_home_id}→{home_id})，跳过缓存"
            )

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
            # 构建房间映射，注入 room_name 到每个设备
            room_map, device_room_map = await self._build_room_maps()
            result = []
            room_filled = 0
            room_empty = 0
            for d in devices:
                room_name = ""
                if d.room_id:
                    room_name = room_map.get(str(d.room_id), "")
                if not room_name:
                    # 降级：通过设备 DID 在房间数据结构中查找
                    room_name = device_room_map.get(d.did, "")
                if room_name:
                    room_filled += 1
                else:
                    room_empty += 1
                device_info = {
                    "did": d.did,
                    "name": d.name,
                    "model": d.model,
                    "is_online": d.is_online(),
                    "room_id": d.room_id,
                    "room_name": room_name,
                }
                # 回填用户自定义别名（按 DID），避免刷新重建后别名丢失
                if d.did in old_aliases:
                    device_info["alias"] = old_aliases[d.did]
                
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
                                if p.service_description:
                                    prop["service_desc"] = p.service_description
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
                    except TokenExpiredError:
                        raise  # 让外层统一返回"凭据已过期"，不能静默写半残缓存
                    except Exception as e:
                        self.logger.debug(f"获取设备 {d.name}({d.model}) 规格失败: {e}")
                
                result.append(device_info)

            self.logger.info(f"设备房间注入: {room_filled} 个有房间, {room_empty} 个无房间")

            # 保存到缓存（使用异步写入避免阻塞）
            try:
                user_id = self.api.credential.user_id if self.api and self.api.credential else None
                await atomic_write_json_async(
                    cache_path,
                    {"schema_version": _DEVICES_CACHE_SCHEMA, "devices": result, "home_id": home_id, "user_id": user_id},
                    ensure_ascii=False,
                    indent=2
                )
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
        name=tr("entries.get_cached_devices.name", default="获取缓存的设备列表"),
        description=tr("entries.get_cached_devices.description", default="读取本地缓存的设备列表，缓存不存在时自动拉取"),
        input_schema={
            "type": "object",
            "properties": {
                "refresh": {"type": "boolean", "description": "是否强制刷新缓存"}
            },
            "required": []
        },
        llm_result_fields=["message"]
    )
    async def get_cached_devices(self, refresh: bool = False, **_):
        """获取缓存的设备列表"""
        cache_path = self.data_path("devices_cache.json")

        # 必须已登录才能读缓存，防止跨用户缓存泄露
        if not refresh and self.api:
            try:
                cached = await read_json_async(cache_path)
            except Exception:
                cached = None
            if cached is not None:
                # 跨用户校验，防止缓存泄漏
                cache_user_id = cached.get('user_id')
                current_user_id = self.api.credential.user_id if self.api.credential else None
                # 归属匹配才返回缓存；不匹配时跳过，继续走网络请求
                if cached.get("schema_version") != _DEVICES_CACHE_SCHEMA:
                    self.logger.info("设备缓存 schema 版本不匹配，跳过缓存")
                elif not current_user_id or cache_user_id == current_user_id:
                    devices = cached.get('devices', [])
                    self.logger.info(f"AI 从缓存读取设备列表: {len(devices)} 个设备")
                    lines = [f"📱 共有 {len(devices)} 个设备:"]
                    for d in devices:
                        status = "🟢" if d.get("is_online") else "🔴"
                        lines.append(f"  {status} {d.get('name')} (型号: {d.get('model')})")
                    message = "\n".join(lines)
                    return Ok({"success": True, "message": message, "devices": devices, "from_cache": True, "count": len(devices)})
                self.logger.warning(
                    f"缓存归属不匹配(user_id: {cache_user_id}→{current_user_id})，跳过缓存"
                )

        # 缓存不存在或刷新，调用 list_devices
        return await self.list_devices(refresh=refresh)

    @plugin_entry(
        id="list_scenes",
        name=tr("entries.list_scenes.name", default="获取智能场景列表"),
        description=tr("entries.list_scenes.description", default="列出当前账号下所有米家智能场景，支持缓存"),
        input_schema={
            "type": "object",
            "properties": {
                "home_id": {"type": "string", "description": "家庭ID，留空自动使用第一个"},
                "refresh": {"type": "boolean", "description": "是否强制刷新缓存"}
            },
            "required": []
        },
        llm_result_fields=["message"]
    )
    async def list_scenes(self, home_id: str = None, refresh: bool = False, **_):
        """获取智能场景列表并缓存"""
        cache_path = self.data_path("scenes_cache.json")

        # 如果不强制刷新，尝试从缓存读取（必须已登录，防止跨用户缓存泄露）
        if not refresh and self.api:
            try:
                cached = await read_json_async(cache_path)
            except Exception:
                cached = None
            if cached is not None:
                cache_home_id = cached.get('home_id')
                cache_user_id = cached.get('user_id')
                current_user_id = self.api.credential.user_id if self.api.credential else None
                # 归属不匹配：跳过缓存，继续走网络请求
                if cache_home_id == home_id and (not current_user_id or cache_user_id == current_user_id):
                    scenes = cached.get('scenes', [])
                    self.logger.info(f"AI 从缓存读取场景列表: {len(scenes)} 个场景")
                    lines = [f"🎬 共有 {len(scenes)} 个智能场景:"]
                    for s in scenes:
                        lines.append(f"  • {s.get('name')} (ID: {s.get('id')})")
                    message = "\n".join(lines)
                    return Ok({"success": True, "message": message, "scenes": scenes, "from_cache": True, "count": len(scenes)})
                self.logger.warning(
                    f"场景缓存归属不匹配(user_id: {cache_user_id}→{current_user_id}, "
                    f"home_id: {cache_home_id}→{home_id})，跳过缓存"
                )

        if not self.api:
            return Err(SdkError("未登录"))

        # 获取 home_id
        if not home_id:
            try:
                homes = await self.api.get_homes()
                valid_homes = [h for h in homes if h.id]
                if not valid_homes:
                    return Err(SdkError("没有可用的家庭"))
                home_id = valid_homes[0].id
            except Exception as e:
                return Err(SdkError(f"无法获取默认家庭: {e}"))

        try:
            scenes = await self.api.get_scenes(home_id)
            result = [{"id": s.scene_id, "name": s.name} for s in scenes if s.scene_id]

            # 保存缓存（使用异步写入避免阻塞）
            try:
                user_id = self.api.credential.user_id if self.api and self.api.credential else None
                await atomic_write_json_async(
                    cache_path,
                    {"scenes": result, "home_id": home_id, "user_id": user_id},
                    ensure_ascii=False,
                    indent=2
                )
                self.logger.info(f"场景列表已缓存: {len(result)} 个场景")
            except Exception as e:
                self.logger.warning(f"保存场景缓存失败: {e}")

            lines = [f"🎬 共有 {len(result)} 个智能场景:"]
            for s in result:
                lines.append(f"  • {s.get('name')} (ID: {s.get('id')})")
            message = "\n".join(lines)
            return Ok({"success": True, "message": message, "scenes": result, "from_cache": False, "count": len(result)})
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except Exception as e:
            self.logger.exception("获取场景列表失败")
            return Err(SdkError(f"获取场景列表失败: {e}"))

    @plugin_entry(
        id="set_device_alias",
        name=tr("entries.set_device_alias.name", default="设置设备别名"),
        description=tr("entries.set_device_alias.description", default="为指定设备设置自定义别名，方便用别名控制设备"),
        input_schema={
            "type": "object",
            "properties": {
                "did": {"type": "string", "description": "设备 DID"},
                "alias": {"type": "string", "description": "自定义别名，多个别名用逗号分隔，如'卧室插座,床头插座'，留空则清除别名"}
            },
            "required": ["did"]
        },
        llm_result_fields=["message"]
    )
    async def set_device_alias(self, did: str, alias: str = "", **_):
        """设置设备别名到缓存"""
        cache_path = self.data_path("devices_cache.json")
        try:
            data = await read_json_async(cache_path)
        except FileNotFoundError:
            return Err(SdkError("设备缓存不存在，请先获取设备列表"))
        except Exception as e:
            return Err(SdkError(f"读取设备缓存失败: {e}"))

        # 归属校验：别名只能写入当前用户的缓存，防止跨账号泄露
        current_user_id = self.api.credential.user_id if self.api and self.api.credential else None
        cache_user_id = data.get('user_id') if isinstance(data, dict) else None
        if current_user_id and cache_user_id and cache_user_id != current_user_id:
            return Err(SdkError("设备缓存归属不匹配，请先刷新设备列表"))

        try:
            devices = data.get("devices", [])
            found = False
            for d in devices:
                if d.get("did") == did:
                    if alias:
                        d["alias"] = alias.strip()
                        msg = f"已将'{d.get('name')}'的别名设为：{alias.strip()}"
                    else:
                        d.pop("alias", None)
                        msg = f"已清除'{d.get('name')}'的别名"
                    found = True
                    break

            if not found:
                return Err(SdkError(f"未找到 DID 为 {did} 的设备"))

            await atomic_write_json_async(cache_path, data, ensure_ascii=False, indent=2)

            return Ok({"success": True, "message": msg, "did": did, "alias": alias.strip() if alias else ""})
        except Exception as e:
            return Err(SdkError(f"保存别名失败: {e}"))

    @plugin_entry(
        id="get_device_aliases",
        name=tr("entries.get_device_aliases.name", default="获取设备别名列表"),
        description=tr("entries.get_device_aliases.description", default="返回所有设备的别名映射（did -> alias）"),
        llm_result_fields=["message"]
    )
    async def get_device_aliases(self, **_):
        """获取所有设备别名"""
        cache_path = self.data_path("devices_cache.json")
        try:
            data = await read_json_async(cache_path)
        except FileNotFoundError:
            return Ok({"success": True, "aliases": {}, "message": "无缓存数据"})
        except Exception as e:
            return Err(SdkError(f"读取设备缓存失败: {e}"))

        # 归属校验：只读当前用户的别名，防止跨账号泄露
        current_user_id = self.api.credential.user_id if self.api and self.api.credential else None
        cache_user_id = data.get('user_id') if isinstance(data, dict) else None
        if current_user_id and cache_user_id and cache_user_id != current_user_id:
            return Err(SdkError("设备缓存归属不匹配，请先刷新设备列表"))

        try:
            devices = data.get("devices", [])
            aliases = {d.get("did"): d.get("alias", "") for d in devices if d.get("alias")}
            lines = [f"📝 共有 {len(aliases)} 个设备别名:"]
            for did, alias in aliases.items():
                lines.append(f"  • {alias} (DID: {did})")
            message = "\n".join(lines) if aliases else "暂无别名"
            return Ok({"success": True, "aliases": aliases, "message": message})
        except Exception as e:
            return Err(SdkError(f"读取别名失败: {e}"))

    @ui.action(label=tr("actions.smartControl.label", default="智能控制"), tone="success", group="control", order=10, refresh_context=True)
    @plugin_entry(
        id="smart_control",
        name=tr("entries.smart_control.name", default="智能控制"),
        description=tr("entries.smart_control.description", default="用一句话控制设备"),
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "控制命令，如'打开卧室灯'、'灯亮度50%'、'空调26度'、'执行回家场景'"}
            },
            "required": ["command"]
        },
        llm_result_fields=["message"]
    )
    async def smart_control(self, command: str, **_):
        """统一设备控制入口"""
        if not self.api:
            return Err(SdkError("未登录"))

        # 原始指令属会话文本，按仓库规范用 print 而非 logger
        print(f"[smart_control] 指令: {command}", flush=True)

        # 场景命令与设备无关，先短路：避免加载设备缓存（缓存缺失时会触发网络刷新）
        scene_m = SCENE_RE.match(command.strip())
        if scene_m:
            return await self._execute_scene_by_name(scene_m.group(1).strip())

        # 意图路由（nlp/ 纯规则引擎）：开关/查询/动作/属性分支短路
        devices = await self._load_devices_cache()
        result = await route(command, devices)
        # 房间映射惰性获取：仅当匹配确实需要（not_found/ambiguous 且存在缺
        # room_name 的设备——可能是目标设备）时才调 get_homes，避免精确匹配拖慢
        if (
            result.branch in ("switch", "control", "action")
            and result.match is not None
            and result.match.status in ("not_found", "ambiguous")
            and devices
            and any(not d.get("room_name") for d in devices)
        ):
            room_map, device_room_map = await self._build_room_maps()
            result = await route(
                command, devices, api_room_map=room_map, device_room_map=device_room_map
            )
        self.logger.info(f"路由结果: branch={result.branch}")
        # 设备/解析诊断含用户原文片段，按规范用 print
        print(
            f"[smart_control] 路由: branch={result.branch}, "
            f"device={result.device_hint!r}, parsed={result.parsed!r}",
            flush=True,
        )

        if result.branch == "query":
            return await self.query_device_state(result.device_hint)
        if result.branch == "action":
            return await self._execute_action_branch(result)
        if result.branch in ("switch", "control"):
            return await self._execute_control_branch(result, command)

        return Err(SdkError(
            "无法理解命令。支持的格式：\n"
            "  开关：'打开卧室灯' / '关掉插座'\n"
            "  亮度：'灯调到50%' / '灯亮度50'\n"
            "  温度：'空调调26度'\n"
            "  模式：'空调调制冷'\n"
            "  场景：'执行回家场景'"
        ))

    async def _execute_action_branch(self, result: RouteResult) -> Any:
        """执行设备动作分支（开始/暂停/停止/回充等）"""
        match_result = result.match
        if match_result and match_result.status == "ok" and len(match_result.devices) == 1:
            device = match_result.devices[0]
            did = device.get("did")
            actions = device.get("actions", [])
            display_name = device.get("alias") or device.get("name", result.device_hint)

            # 从动词推断 action name
            candidates = VERB_TO_ACTION.get(result.verb, [result.verb])
            matched_action = None
            for a in actions:
                aname = a.get("name", "").lower()
                if any(c.lower() == aname for c in candidates):
                    matched_action = a
                    break
            if not matched_action:
                # 模糊匹配
                for a in actions:
                    aname = a.get("name", "").lower()
                    if any(c.lower() in aname for c in candidates):
                        matched_action = a
                        break

            if matched_action:
                try:
                    act_result = await self.api.call_device_action(
                        did, matched_action["siid"], matched_action["aiid"]
                    )
                    # call_device_action 返回 False 表示动作被设备/网关拒绝，不能报成功
                    if act_result:
                        return Ok({"success": True, "message": f"✅ 已对'{display_name}'执行'{result.verb}'操作", "device": display_name, "action": result.verb, "result": act_result})
                    return Err(SdkError(self.i18n.t(
                        "switch.action_rejected",
                        default="对'{name}'执行'{action}'操作失败（动作被拒绝）",
                        name=display_name, action=result.verb,
                    )))
                except TokenExpiredError:
                    return Err(SdkError("凭据已过期，请重新登录"))
                except Exception as e:
                    return Err(SdkError(f"对'{display_name}'执行'{result.verb}'操作失败: {e}"))
            else:
                action_names = [a.get("name") for a in actions]
                return Err(SdkError(
                    f"'{display_name}'没有'{result.verb}'操作。可用操作：{', '.join(action_names) if action_names else '无'}"
                ))

        # 设备未匹配/歧义：明确报错（原逻辑在此场景会掉落到属性解析，产生无意义结果）
        if match_result and match_result.message:
            return Err(SdkError(match_result.message))
        return Err(SdkError(f"未找到设备'{result.device_hint}'"))

    async def _execute_control_branch(self, result: RouteResult, command: str) -> Any:
        """执行控制分支（switch / set_prop / adjust_prop）"""
        match_result = result.match
        if match_result is None or match_result.status != "ok":
            return Err(SdkError(match_result.message if match_result else "未找到设备"))
        parsed = result.parsed
        if parsed is None:
            return Err(SdkError("无法解析控制命令"))

        device = match_result.devices[0]
        did = device.get("did")
        display_name = device.get("alias") or device.get("name", result.device_hint)
        props = device.get("properties", [])
        actions = device.get("actions", [])

        if parsed.action == "switch":
            return await self._execute_switch(props, actions, did, display_name, parsed.value, command)
        if parsed.action == "adjust_prop":
            return await self._execute_adjust_prop(props, did, display_name, parsed)
        return await self._execute_set_prop(props, did, display_name, parsed)

    @staticmethod
    def _find_power_action(actions: list[dict], value: Any) -> Optional[dict]:
        """在设备动作中查找电源开关动作：True → Turn On，False → Turn Off。

        MIoT 电视/显示器等设备没有可写的 power 属性，电源开关以动作形式暴露
        （如 xiaomi.tv.rmh1 的 "Turn On"(siid 6) / "Turn Off"(siid 2)）。
        不收录 "set power" 类动作——它们通常带 in 参数，无参调用会被云端拒绝。
        """
        keywords = ["turn on", "power on", "switch on", "power-on"]
        if not value:
            keywords = ["turn off", "power off", "switch off", "power-off"]
        for a in actions:
            aname = (a.get("name") or "").lower()
            if any(k in aname for k in keywords):
                return a
        return None

    @staticmethod
    def _find_input_selector_prop(props: list[dict]) -> Optional[dict]:
        """查找输入源选择属性（TV/投影类设备：uint/int + value_list + 名字含 input/source/channel）。

        这类设备云端没有可写的 power 属性，设输入源会唤醒电视（实测 xiaomi.tv.rmh1）。
        """
        for p in props:
            if p.get("access") not in ["write", "read_write", "notify_read_write"]:
                continue
            pname = (p.get("name") or "").lower()
            if not any(k in pname for k in ["input", "source", "channel"]):
                continue
            ptype = (p.get("type") or "").lower()
            if ptype not in ("uint", "uint8", "uint16", "uint32", "int", "int8", "int16", "int32"):
                continue
            if p.get("value_list"):
                return p
        return None

    @staticmethod
    def _has_bool_power_prop(props: list[dict]) -> bool:
        """是否存在真正的 bool 型电源属性（名称含 power/switch/开关/电源）。

        不含裸 "on"（is-on 是 speaker-mode 状态，非电源）；用于判断输入源唤醒
        是否让位于真正的电源属性。
        """
        for p in props:
            if p.get("type") != "bool":
                continue
            if p.get("access") not in ["write", "read_write", "notify_read_write"]:
                continue
            pname = (p.get("name") or "").lower()
            if any(k in pname for k in ["开关", "电源", "power", "switch"]):
                return True
        return False

    async def _execute_switch(self, props: list[dict], actions: list[dict], did: str, display_name: str, value: Any, command: str) -> Any:
        """执行二元开关控制（电源动作 → 输入源唤醒 → 开关属性，含多控开关方位词匹配）"""
        action_text = "打开" if value else "关闭"

        # 1) 电源动作优先；网关不支持动作时（如 /miotspec/action 返回 -6）回落属性通道。
        #    仅当动作真正执行成功（act_result 为 True）才返回成功，否则继续回落。
        power_action = self._find_power_action(actions, value)
        if power_action:
            try:
                act_result = await self.api.call_device_action(
                    did, power_action["siid"], power_action["aiid"]
                )
                if act_result:
                    return Ok({"success": True, "message": f"✅ 已{action_text}'{display_name}'", "device": display_name, "action": action_text, "result": act_result})
                self.logger.info("电源动作返回失败，回落属性控制")
            except TokenExpiredError:
                return Err(SdkError("凭据已过期，请重新登录"))
            except Exception as e:
                self.logger.info(f"电源动作不可用，回落属性控制: {e}")

        # 2) 打开命令：输入源选择属性唤醒（仅当设备没有真正的 bool 电源属性时——
        # 电视等设备无 power 属性，靠写输入源唤醒；若同时有电源属性，优先走属性通道，
        # 避免被输入源路径抢先而跳过真正的电源属性）
        if value is True and not self._has_bool_power_prop(props):
            selector = self._find_input_selector_prop(props)
            if selector:
                # value_list 可能是 dict 列表 [{value,...}] 或纯值列表 [1,2,3]（标准解析器扁平化）
                raw_values = selector.get("value_list") or []
                values = [
                    item.get("value") if isinstance(item, dict) else item
                    for item in raw_values
                    if item is not None
                ]
                cur = None
                try:
                    res = await self.api.get_device_properties(
                        [{"did": did, "siid": selector["siid"], "piid": selector["piid"]}]
                    )
                    cur = res[0].get("value") if res else None
                except TokenExpiredError:
                    return Err(SdkError("凭据已过期，请重新登录"))
                except Exception:
                    cur = None
                # 电视在线时写回当前输入源；离线/读失败时写 HDMI 1（4），
                # 不在列表则用列表第一个值
                target = cur if cur in values else (4 if 4 in values else (values[0] if values else None))
                if target is not None:
                    try:
                        ok = await self.api.control_device(did, selector["siid"], selector["piid"], target)
                        if ok:
                            post = None
                            try:
                                chk = await self.api.get_device_properties(
                                    [{"did": did, "siid": selector["siid"], "piid": selector["piid"]}]
                                )
                                post = chk[0].get("value") if chk else None
                            except Exception:
                                post = None
                            # 只有观察到"值实际从可读的旧值变更到 target"才确认唤醒。
                            # 写前读不到值（cur 为 None）时无法用输入源回读确认真实电源
                            # 状态 → 不报开机成功（返回未确认），避免云端缓存回显造成假成功
                            if post is not None and post == target and cur is not None and cur != target:
                                return Ok({"success": True, "message": f"✅ 已{action_text}'{display_name}'", "device": display_name, "action": action_text, "value": target})
                            return Err(SdkError(self.i18n.t(
                                "switch.wake_unconfirmed",
                                default="已向'{name}'发送开机指令，但无法确认是否已唤醒",
                                name=display_name,
                            )))
                    except TokenExpiredError:
                        return Err(SdkError("凭据已过期，请重新登录"))
                    except Exception:
                        pass  # 写入失败，继续回落

        # 3) 收集所有可写的开关属性
        switch_props = []
        for p in props:
            pname = p.get("name", "").lower()
            if any(k in pname for k in ["开关", "电源", "power", "switch", "on"]):
                if p.get("access") in ["write", "read_write", "notify_read_write"]:
                    switch_props.append(p)
        # 二进制开关指令只应发给 bool 型属性：无条件过滤掉名字含 "on" 的非布尔
        # 属性（"TV Input Control"/position/ventilation/illumination 等），
        # 否则 True/False 会被发给 enum/int/string 属性并触发控制失败。
        # 兜底不再接受"任意可写 bool"——仅保留名称明确标识电源的属性，
        # 避免把 Mute/儿童锁等无关布尔属性误当电源开关。
        switch_props = [p for p in switch_props if p.get("type") == "bool"]

        if not switch_props:
            return Err(SdkError(f"'{display_name}'没有可控制的开关"))

        # 多控开关：根据命令中的方位词匹配
        switch = None
        if len(switch_props) == 1:
            switch = switch_props[0]
        else:
            # 从命令中提取方位关键词
            _POS_MAP = [
                (r"左", ["Left", "left"]),
                (r"右", ["Right", "right"]),
                (r"中", ["Middle", "middle", "Center", "center"]),
                (r"(?:一|1)键", ["First", "first", "1"]),
                (r"(?:二|2)键", ["Second", "second", "2"]),
                (r"(?:三|3)键", ["Third", "third", "3"]),
                (r"(?:四|4)键", ["Fourth", "fourth", "4"]),
                (r"(?:五|5)键", ["Fifth", "fifth", "5"]),
                (r"(?:六|6)键", ["Sixth", "sixth", "6"]),
            ]
            for pattern, en_keywords in _POS_MAP:
                if re.search(pattern, command):
                    for p in switch_props:
                        sdesc = (p.get("service_desc") or "").lower()
                        if any(kw.lower() in sdesc for kw in en_keywords):
                            switch = p
                            break
                    if switch:
                        break

            # 未匹配到方位词 → 按默认主开关优先级自动选择
            if not switch:
                switch = self._pick_default_switch(switch_props, display_name)
                self.logger.info(
                    f"多控开关未指定方位，自动选择默认主开关: "
                    f"device={display_name}, service_desc={switch.get('service_desc')}, "
                    f"siid={switch.get('siid')}, piid={switch.get('piid')}"
                )

        siid = switch.get("siid")
        piid = switch.get("piid")
        try:
            success = await self.api.control_device(did, siid, piid, value)
            action_text = "打开" if value else "关闭"
            if not success:
                return Ok({"success": False, "message": f"❌ {action_text}'{display_name}'失败"})
            # 写后回读校验：防止写入"属性合法但并非电源"的字段（如电视 speaker-mode
            # 的 is-on）被 API 照单全收返回 code 0，却对真机毫无作用。
            # 只写（access="write"）属性读不回来，跳过校验直接按成功返回。
            # 云端属性传播有延迟（实测关闭可慢至数秒），故轮询而非单次回读。
            if switch.get("access") == "write":
                return Ok({"success": True, "message": f"✅ 已{action_text}'{display_name}'", "device": display_name, "action": action_text})
            actual = None
            for wait in (0.5, 1.5, 3.0):
                await asyncio.sleep(wait)
                try:
                    chk = await self.api.get_device_properties(
                        [{"did": did, "siid": siid, "piid": piid}]
                    )
                    actual = chk[0].get("value") if chk else None
                except TokenExpiredError:
                    return Err(SdkError("凭据已过期，请重新登录"))
                except Exception:
                    actual = None
                if actual is not None and bool(actual) == bool(value):
                    actual = value
                    break
            if actual != value:
                self.logger.warning(
                    f"开关回读校验失败: device={display_name}, siid={siid}, piid={piid}, "
                    f"期望={value}, 实际={actual}（写入未生效）"
                )
                return Err(SdkError(self.i18n.t(
                    "switch.no_response",
                    default="{action}'{name}'失败：设备未响应电源控制（{name} 可能不支持云端开关）",
                    action=action_text, name=display_name,
                )))
            return Ok({"success": True, "message": f"✅ 已{action_text}'{display_name}'", "device": display_name, "action": action_text})
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except MijiaAPIException as e:
            self.logger.warning(f"API控制失败: device={display_name}, did={did}, siid={siid}, piid={piid}, value={value}, error={e}")
            if e.code == -6:
                return Err(SdkError(f"控制'{display_name}'失败：设备不支持该操作或参数有误（siid={siid}, piid={piid}），请检查设备是否在线"))
            return Err(SdkError(f"控制'{display_name}'失败: {e}"))
        except Exception as e:
            self.logger.exception("控制失败")
            return Err(SdkError(f"控制失败: {e}"))

    async def _execute_adjust_prop(self, props: list[dict], did: str, display_name: str, parsed: Any) -> Any:
        """执行相对值调整（调亮一点/温度高一点）"""
        direction = parsed.direction
        delta = parsed.delta
        prop = self._find_property_for_control(props, parsed.prop, None)
        if not prop:
            available = [p.get("name") for p in props if p.get("access") in ["write", "read_write", "notify_read_write"]]
            return Err(SdkError(f"'{display_name}'没有可控制的'{parsed.prop}'属性。可控制属性：{', '.join(available) if available else '无'}"))

        siid = prop.get("siid")
        piid = prop.get("piid")
        vr = prop.get("value_range", [])

        # 读取当前值
        try:
            cur_results = await self.api.get_device_properties([{"did": did, "siid": siid, "piid": piid}])
            cur_value = cur_results[0].get("value") if cur_results else None
        except Exception:
            cur_value = None

        if cur_value is None:
            return Err(SdkError(f"无法读取'{display_name}'的{parsed.prop}当前值"))

        # 计算目标值（钳值 + 步长对齐，逻辑见 nlp/value_resolver.py）
        target = resolve_adjust_target(cur_value, direction, delta, vr)

        try:
            success = await self.api.control_device(did, siid, piid, target)
            if success:
                return Ok({"success": True, "message": f"✅ 已将'{display_name}'的{parsed.prop}从{cur_value}调整为{target}", "device": display_name, "property": parsed.prop, "value": target})
            else:
                return Ok({"success": False, "message": f"❌ 调整'{display_name}'的{parsed.prop}失败"})
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except MijiaAPIException as e:
            return Err(SdkError(f"调整'{display_name}'的{parsed.prop}失败: {e}"))
        except Exception as e:
            self.logger.exception("调整失败")
            return Err(SdkError(f"调整失败: {e}"))

    async def _execute_set_prop(self, props: list[dict], did: str, display_name: str, parsed: Any) -> Any:
        """执行属性控制（亮度/温度/模式等）"""
        if not parsed.prop:
            return Err(SdkError("请指定要调整的属性，如'灯亮度50%'"))

        prop = self._find_property_for_control(props, parsed.prop, parsed.value)
        if not prop:
            available = [p.get("name") for p in props if p.get("access") in ["write", "read_write", "notify_read_write"]]
            return Err(SdkError(f"'{display_name}'没有可控制的'{parsed.prop}'属性。可控制属性：{', '.join(available) if available else '无'}"))

        value = parsed.value
        # 极值处理："最高"/"最低" → 从 value_range 取边界
        if value in ("max", "min"):
            vr = prop.get("value_range", [])
            if len(vr) >= 2:
                value = vr[1] if value == "max" else vr[0]
            elif prop.get("value_list"):
                vals = [
                    item.get("value", 0) if isinstance(item, dict) else item
                    for item in prop.get("value_list", [])
                ]
                value = max(vals) if value == "max" else min(vals)
            else:
                return Err(SdkError(f"'{display_name}'的{parsed.prop}没有值范围信息，无法设置极值"))

        # 模式/档位枚举转换：中文 → 设备 spec 数字值
        if isinstance(value, str) and prop.get("value_list"):
            resolved = self._resolve_enum_value(prop, value)
            if resolved is not None:
                self.logger.info(f"枚举转换: '{value}' → {resolved}")
                value = resolved
            else:
                # 标签回退顺序：description → comment → value
                available_modes = [
                    (
                        f"{item.get('description') or item.get('comment') or item.get('value')}"
                        f"(={item.get('value')})"
                        if isinstance(item, dict) else str(item)
                    )
                    for item in prop.get("value_list", [])
                ]
                return Err(SdkError(
                    f"'{display_name}'的{parsed.prop}不支持'{value}'。"
                    f"可用模式：{', '.join(available_modes)}"
                ))

        # 窗帘位置：MIoT spec 定义 0=关闭, 100=全开，直接透传用户设定值
        # 不做反转——用户说"位置到80"即设为 80（80% 开）

        siid = prop.get("siid")
        piid = prop.get("piid")
        self.logger.info(f"控制属性: {prop.get('name')}, siid={siid}, piid={piid}, value={value}")

        try:
            success = await self.api.control_device(did, siid, piid, value)
            if success:
                unit = prop.get("unit", "")
                value_display = f"{value}{unit}" if unit else str(value)
                return Ok({"success": True, "message": f"✅ 已将'{display_name}'的{parsed.prop}设为{value_display}", "device": display_name, "property": parsed.prop, "value": value})
            else:
                return Ok({"success": False, "message": f"❌ 设置'{display_name}'的{parsed.prop}失败"})
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except MijiaAPIException as e:
            self.logger.warning(f"API控制失败: device={display_name}, did={did}, siid={siid}, piid={piid}, value={value}, error={e}")
            if e.code == -6:
                return Err(SdkError(f"设置'{display_name}'的{parsed.prop}失败：设备不支持该操作或参数有误"))
            return Err(SdkError(f"设置'{display_name}'的{parsed.prop}失败: {e}"))
        except Exception as e:
            self.logger.exception("控制失败")
            return Err(SdkError(f"控制失败: {e}"))

    @staticmethod
    def _pick_default_switch(switch_props: list, display_name: str = "") -> dict:
        """当多控开关未指定方位时，按默认主开关优先级自动选择。

        优先级：
        1. 通用 Switch（无方位前缀，主开关 / USB 插座的主插孔）
        2. Left Switch Service（左键）
        3. First Switch Service（第 1 键）
        4. Middle Switch Service（中键）
        5. Right Switch Service（右键）
        6. Second / Third / Fourth / Fifth / Sixth Switch Service
        7. 兜底：第一个可写开关
        """
        import re as _re

        _POSITIONAL_PREFIXES = {"left", "right", "middle", "center",
                                "first", "second", "third", "fourth", "fifth", "sixth"}

        def _svc_desc(p):
            return (p.get("service_desc") or "").lower()

        def _is_generic_switch(sd):
            """通用 Switch：含 'switch' 但不含方位/序号前缀，也非 USB 开关。"""
            if "switch" not in sd:
                return False
            if "usb" in sd:
                return False
            words = sd.split()
            # "switch" 或 "switch service" → 通用主开关
            if words[0] == "switch":
                return True
            # 如果第一个词是方位/序号前缀，则不是通用的
            return words[0] not in _POSITIONAL_PREFIXES

        # 按优先级匹配
        _POSITIONAL_ORDER = [
            (["left"], r"left\s+switch"),
            (["first"], r"first\s+switch"),
            (["middle", "center"], r"(?:middle|center)\s+switch"),
            (["right"], r"right\s+switch"),
            (["second"], r"second\s+switch"),
            (["third"], r"third\s+switch"),
            (["fourth"], r"fourth\s+switch"),
            (["fifth"], r"fifth\s+switch"),
            (["sixth"], r"sixth\s+switch"),
        ]

        # 第一优先：通用 Switch（主开关 / 主插孔）
        for p in switch_props:
            if _is_generic_switch(_svc_desc(p)):
                return p

        # 第二优先：按方位/序号顺序
        for _, pattern in _POSITIONAL_ORDER:
            for p in switch_props:
                if _re.search(pattern, _svc_desc(p)):
                    return p

        # 第二步：过滤掉 USB 子开关，取第一个剩余的
        non_usb = [p for p in switch_props if "usb" not in _svc_desc(p)]
        if non_usb:
            return non_usb[0]

        # 最终兜底：直接取第一个
        return switch_props[0]

    def _find_property_for_control(self, props: list[dict], prop_name: str, value: Any) -> Optional[dict]:
        """根据属性名和目标值，从设备属性列表中找到匹配的可写属性"""
        prop_name_lower = prop_name.lower()

        # 属性名关键词映射（对齐小爱同学标准翻译表）
        PROP_KEYWORDS = {
            "亮度": ["亮度", "brightness"],
            "色温": ["色温", "color temperature"],
            "温度": ["目标温度", "设定温度", "温度", "target temperature", "target-temperature", "temperature"],
            "音量": ["音量", "volume"],
            "风速": ["风速", "风量", "风档", "档位", "fan speed", "fan level", "fan_level", "fan-level", "stepless_fan_level"],
            "模式": ["模式", "mode"],
            "浓度": ["浓度", "density"],
            "湿度": ["湿度", "设定湿度", "target-humidity", "target_humidity", "humidity"],
            "位置": ["位置", "position", "target-position", "target_position"],
            "吸力": ["吸力", "suction", "suction-level", "suction_level"],
            "档位": ["档位", "heat level", "heat-level", "heat_level", "massage-strength", "massage_strength"],
            "水温": ["水温", "设定温度", "target-temperature", "target_temperature"],
            "水量": ["水量", "泵量", "pump-flux", "pump_flux", "mop-water-output-level"],
            "角度": ["角度", "angle", "backrest-angle", "backrest_angle", "leg-rest-angle", "leg-rest_angle"],
            "转速": ["转速", "spin-speed", "spin_speed"],
            "颜色": ["颜色", "color", "Color", "rgb-color", "color-temperature"],
        }

        keywords = PROP_KEYWORDS.get(prop_name, [prop_name_lower])

        # 1. 按关键词匹配可写属性
        for p in props:
            if p.get("access") not in ["write", "read_write", "notify_read_write"]:
                continue
            pname = p.get("name", "").lower()
            if any(kw in pname for kw in keywords):
                return p

        # 2. 模式命令：找第一个可写 string/uint 属性（通常模式属性是 enum）
        if prop_name == "模式":
            for p in props:
                if p.get("access") not in ["write", "read_write", "notify_read_write"]:
                    continue
                ptype = p.get("type", "")
                if ptype in ["string", "uint8", "uint16", "uint32", "int8", "int16", "int32"]:
                    # 检查是否有 value_list（枚举属性）
                    if p.get("value_list"):
                        return p

        return None

    # ── 中文模式名 → 英文枚举关键词映射（对齐小爱同学翻译表） ──
    _MODE_CN_TO_EN: dict[str, list[str]] = {
        # 空调
        "制冷": ["Cool"], "制热": ["Heat"], "送风": ["Fan"],
        # 通用
        "自动": ["Auto"], "睡眠": ["Sleep"], "静音": ["Silent", "Qtet"],
        "强力": ["Strong", "Intensive", "Turbo"], "智能": ["Smart"],
        "节能": ["Energy Saving"], "舒适": ["Comfort"],
        "标准": ["Basic", "Standard"], "手动": ["None", "Manual"],
        "最爱": ["Favorite"], "除湿": ["Dry"],
        # 灯
        "日光": ["Day"], "月光": ["Night"], "彩光": ["Color"],
        "温馨": ["Warmth"], "电视": ["Tv"], "阅读": ["Reading"],
        "电脑": ["Computer"], "娱乐": ["Entertainment"],
        "休闲": ["Leisure"], "办公": ["Office"], "儿童": ["Baby", "Baby Care"],
        "夜灯": ["Night Light", "Nightlight"],
        # 风扇
        "自然风": ["Natural Wind"], "直吹风": ["Straight Wind"],
        "冷风": ["Cold Air"],
        # 净化器/新风机
        "低风": ["Low"], "中风": ["Medium"], "高风": ["High"],
        "超强": ["Turbo"],
        # 浴霸
        "暖风": ["Hot", "Warm", "Heat"], "热风": ["Hot"],
        "换气": ["Ventilate"], "干燥": ["Dry"], "吹风": ["Fan"],
        "待机": ["Idle"], "恒温": ["Constant Temperature"],
        # 洗衣机
        "日常洗": ["Daily Wash"], "快速洗": ["Quick Wash"], "快洗": ["Quick Wash"],
        "轻柔": ["Delicate Wash", "Delicate"], "大件": ["Heavy Wash", "Large wash"],
        "棉麻": ["Cotton"], "化纤": ["Synthetic"], "羊毛": ["Wool"],
        "婴童": ["Baby Care"], "内衣": ["Underwear"], "丝绸": ["Silk"],
        "牛仔": ["Jeans"], "蒸汽": ["Steam Wash"], "护色": ["Color Protection"],
        "防过敏": ["Anti-allergy"], "顽渍": ["Stain Wash"],
        "智能洗": ["Smart"], "混合": ["Mix"], "冲锋衣": ["Jacket"],
        "衬衣": ["Shirt"], "桶自洁": ["Drum Clean"],
        "烘干": ["Dry", "Wash Dry"], "除菌": ["Sterilization"],
        "除螨": ["Mite Removal"], "新衣": ["New-Clothes Wash"],
        "单漂": ["Rinse"], "单脱": ["Spin"], "自定义": ["User Define"],
        "高温": ["Boiling"], "空气洗": ["Dry Air Wash"],
        "快洗烘": ["Quick Wash Dry"], "智能洗烘": ["Wash Dry"],
        "运动": ["Sportswear", "Sport Mode"],
        # 洗碗机
        "玻璃": ["Glass"], "预洗": ["Prewash"], "少量": ["Bit Wash"],
        "自洁": ["Self Clean"], "消毒": ["Disinfecting"],
        "奶瓶": ["Bottle"], "大物": ["Large wash"], "锅具": ["Pot wash"],
        "分层": ["Layered Wash"], "随心": ["Pleased"], "及时": ["Timely"],
        # 冰箱
        "假日": ["Holiday"], "速冷": ["Quick Cooling"], "速冻": ["Quick Frozen"],
        # 扫地机
        "安静": ["Silent"], "全速": ["Full Speed"],
        # 按摩椅
        "全身": ["Full Body"], "肩颈": ["Shoulder and Neck"],
        "腰臀": ["Waist and Hip"], "日常放松": ["Relaxed"],
        "助眠": ["Sleep"], "解压": ["Relieve Stress"],
        "零重力": ["Zero Gravity"],
        # 电饭煲
        "精煮": ["Fine Cook"], "快煮": ["Quick Cook"],
        "煮粥": ["Cook Congee"], "保温": ["Keep Warm"],
        "蒸饭": ["Steam Rice"], "煲仔饭": ["ClaypotRice"],
        "杂粮": ["MultigrainRice"], "炖汤": ["Soup"],
        # 香薰机
        "唤醒": ["Wake Up"],
        # 热水器
        "速热": ["Quick Heat"],
    }

    def _resolve_enum_value(self, prop: dict, chinese_value: str) -> Optional[int]:
        """将中文模式/档位名转换为设备 spec 的数字枚举值。

        Args:
            prop: 设备属性字典（需含 value_list）
            chinese_value: 用户输入的中文值，如 "制冷"

        Returns:
            匹配到的数字值，未匹配返回 None
        """
        value_list = prop.get("value_list")
        if not value_list:
            return None

        # 直接尝试把输入当数字
        if isinstance(chinese_value, (int, float)):
            return int(chinese_value)

        en_keywords = self._MODE_CN_TO_EN.get(chinese_value, [])
        chinese_lower = chinese_value.lower()

        for item in value_list:
            # 兼容旧缓存：value_list 可能是纯值列表 [0,1,2]（无描述，无法映射中文）
            if not isinstance(item, dict):
                continue
            desc = str(item.get("description", ""))
            comment = str(item.get("comment", ""))
            desc_lower = desc.lower()
            comment_lower = comment.lower()
            for field_lower in (desc_lower, comment_lower):
                # 英文枚举名精确匹配
                if any(field_lower == kw.lower() for kw in en_keywords):
                    return item["value"]
                # 英文枚举名包含匹配
                if any(kw.lower() in field_lower for kw in en_keywords):
                    return item["value"]
            # 中文直接匹配（description/comment 可能是中文，如 spec 的 comment 恒湿/睡眠/风干）
            if chinese_lower in desc_lower or chinese_lower in comment_lower:
                return item["value"]

        return None

    async def _execute_scene_by_name(self, scene_name: str) -> Any:
        """按场景名称查找并执行场景"""
        cache_path = self.data_path("scenes_cache.json")
        scenes = []
        cached_home_id = None
        cached_user_id = None
        try:
            cached = await read_json_async(cache_path)
            scenes = cached.get('scenes', [])
            cached_home_id = cached.get('home_id')
            cached_user_id = cached.get('user_id')
        except Exception:
            pass

        if not scenes:
            return Err(SdkError("场景列表为空，请先获取场景列表"))

        # 归属校验：场景缓存必须属于当前账号（user_id 严格匹配）
        current_user_id = self.api.credential.user_id if self.api else None
        if current_user_id and cached_user_id and cached_user_id != current_user_id:
            return Err(SdkError("场景缓存归属不匹配，请先刷新场景列表"))

        # 归属校验：缓存的 home_id 必须属于当前账号
        if cached_home_id and self.api:
            try:
                homes = await self.api.get_homes()
                valid_home_ids = {h.id for h in homes if h.id}
                if cached_home_id not in valid_home_ids:
                    return Err(SdkError("场景缓存归属不匹配，请先刷新场景列表"))
            except Exception:
                pass

        # 模糊匹配场景名
        name_lower = scene_name.lower()
        matched = [s for s in scenes if name_lower in s.get("name", "").lower()]

        if not matched:
            names = [s.get("name") for s in scenes]
            return Err(SdkError(f"未找到场景'{scene_name}'。当前场景：{', '.join(names)}"))
        if len(matched) > 1:
            names = [s.get("name") for s in matched]
            return Err(SdkError(f"找到多个匹配场景：{', '.join(names)}，请更精确指定"))

        scene = matched[0]
        # 优先使用缓存中的 home_id（场景所属家庭），否则回退到第一个家庭
        home_id = cached_home_id
        if not home_id:
            try:
                homes = await self.api.get_homes()
                home_id = homes[0].id if homes else None
            except Exception:
                home_id = None

        if not home_id:
            return Err(SdkError("未找到可用家庭"))

        try:
            success = await self.api.execute_scene(scene["id"], home_id)
            if success:
                return Ok({"success": True, "message": f"✅ 已执行场景'{scene['name']}'"})
            else:
                return Ok({"success": False, "message": f"❌ 执行场景'{scene['name']}'失败"})
        except Exception as e:
            return Err(SdkError(f"执行场景失败: {e}"))


    # ========== 辅助功能：获取设备规格（可选） ==========
    @plugin_entry(
        id="query_device_state",
        name=tr("entries.query_device_state.name", default="查询设备状态"),
        description=tr("entries.query_device_state.description", default="按名称查询设备所有可读属性的当前值，支持设备名、别名、房间名+设备名"),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "设备名称、别名或房间名+设备名，如'灯'、'卧室灯'、'床头插座'"}
            },
            "required": ["name"]
        },
        llm_result_fields=["message"]
    )
    async def query_device_state(self, name: str, **_):
        """根据设备名称查询设备状态"""
        if not self.api:
            return Err(SdkError("未登录"))

        # 统一设备匹配（支持区域+设备名、别名、模糊匹配）
        cached_devices = await self._load_devices_cache()
        match_result = match_devices(name, cached_devices)
        # 房间映射惰性获取：仅当匹配需要（not_found/ambiguous 且存在缺 room_name
        # 的设备）时才调 get_homes，避免精确匹配被拖慢
        if (
            match_result.status in ("not_found", "ambiguous")
            and cached_devices
            and any(not d.get("room_name") for d in cached_devices)
        ):
            room_map, device_room_map = await self._build_room_maps()
            match_result = match_devices(
                name, cached_devices, api_room_map=room_map, device_room_map=device_room_map
            )
        if match_result.status == "not_found":
            return Err(SdkError(match_result.message))
        if match_result.status == "ambiguous":
            return Err(SdkError(match_result.message))

        devices = match_result.devices
        
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

            # 属性名本地化映射（对齐小爱同学标准翻译表）
            NAME_MAP = {
                # ── 设备信息类 ──
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
                "battery-level": "电池电量",
                "Battery Voltage": "电池电压",
                "Charging State": "充电状态",
                "Low Battery": "低电量",

                # ── 开关控制类（灯/插座/开关等共用） ──
                "Switch Status": "开关状态",
                "on": "开关",
                "Power": "电源",
                "On": "开启",
                "Off": "关闭",
                "Toggle": "切换",
                "Default Power On State": "默认通电状态",
                "Power Off Memory": "断电记忆",
                "Physical Control Locked": "儿童锁",
                "physical-controls-locked": "儿童锁",
                "physical_controls_locked": "儿童锁",
                "Child Lock": "童锁",

                # ── 功率电量类（插座/开关） ──
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

                # ── 照明类（灯/风扇灯/浴霸灯） ──
                "Brightness": "亮度",
                "brightness": "亮度",
                "Color Temperature": "色温",
                "color_temperature": "色温",
                "Color": "颜色",
                "color": "颜色",
                "Hue": "色相",
                "Saturation": "饱和度",
                "Light Mode": "灯光模式",
                "Scene": "场景",
                "Night Light": "夜灯",
                "Ambient Light": "氛围灯",
                "ambient-light": "氛围灯",
                "Illuminance": "照度",
                "illumination": "光照度",
                "Colorful": "彩光模式",
                "Flow": "流光模式",

                # ── 环境传感器类（温湿度传感器/空气检测仪等） ──
                "temperature": "温度",
                "Temperature": "温度",
                "relative_humidity": "湿度",
                "relative-humidity": "湿度",
                "humidity": "湿度",
                "Humidity": "湿度",
                "pm25_density": "PM2.5",
                "PM2.5": "PM2.5",
                "PM10": "PM10",
                "co2-density": "二氧化碳浓度",
                "CO2": "二氧化碳",
                "TVOC": "总挥发性有机物",
                "hcho-density": "甲醛浓度",
                "Formaldehyde": "甲醛",
                "AQI": "空气质量指数",
                "air-quality": "空气质量",
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

                # ── 空调/温控类 ──
                "target_temperature": "目标温度",
                "target-temperature": "目标温度",
                "Target Temperature": "目标温度",
                "Current Temperature": "当前温度",
                "Mode": "模式",
                "mode": "模式",
                "Fan Speed": "风速",
                "fan_speed": "风速",
                "Fan Level": "风量档位",
                "fan_level": "风量档位",
                "fan-level": "风量档位",
                "stepless_fan_level": "无级风速",
                "Swing Mode": "摆风模式",
                "vertical_swing": "上下摆风/上下扫风",
                "Vertical Swing": "上下摆风",
                "horizontal_swing": "左右摆风",
                "Horizontal Swing": "左右摆风",
                "Sleep Mode": "睡眠模式",
                "sleep-mode": "睡眠模式",
                "Eco Mode": "节能模式",
                "eco": "节能模式",
                "Dry Mode": "除湿模式",
                "dryer": "干燥/烘干",
                "Heat Mode": "制热模式",
                "heater": "辅热模式",
                "Cool Mode": "制冷模式",
                "Auto Mode": "自动模式",
                "Heating": "加热中",
                "Cooling": "制冷中",
                "Defrosting": "除霜中",
                "soft-wind": "柔风",
                "un-straight-blowing": "防直吹",
                "uv": "杀菌功能",
                "indicator-light": "指示灯",

                # ── 窗帘/电机/晾衣架类 ──
                "motor_control": "电机控制",
                "Motor Control": "电机控制",
                "Motor Reverse": "电机反转",
                "Position": "位置",
                "position": "位置",
                "Current Position": "当前位置",
                "target-position": "目标位置",
                "target_position": "目标位置",
                "Target Position": "目标位置",
                "Run Time": "运行时间",

                # ── 安防/报警类 ──
                "alarm": "提示音",
                "Alarm": "提示音",
                "Alarm Volume": "警报音量",
                "Alarm Duration": "警报时长",
                "Guard Mode": "守护模式",
                "Away Mode": "离家模式",
                "Home Mode": "在家模式",
                "Sleep Mode Guard": "睡眠守护",

                # ── 定时/倒计时类 ──
                "start-time": "开始时间",
                "end-time": "结束时间",
                "duration": "持续时长",
                "left-time": "剩余时间",
                "left_time": "剩余时间",
                "countdown": "倒计时",
                "Timer": "定时器",
                "Schedule": "定时任务",
                "target-time": "目标时间",
                "target_time": "目标时间",
                "cook-time": "烹饪时间",
                "cook_time": "烹饪时间",

                # ── 滤芯/耗材类 ──
                "filter-life-level": "滤芯剩余寿命",
                "filter_life_level": "滤芯剩余寿命",
                "filter-life-time": "滤芯剩余天数",
                "filter-left-time": "滤芯剩余天数",
                "filter_left_time": "滤芯剩余天数",
                "Filter Life": "滤芯寿命",
                "Filter Used Time": "滤芯已用时间",
                "repellent-left-level": "蚊香剩余量",

                # ── 洗衣机/洗碗机类 ──
                "rinse-times": "漂洗次数",
                "drying-time": "烘干时长",
                "door-state": "舱门状态",
                "spin-speed": "脱水转速",
                "detergent-self-delivery": "洗衣液自动投放",
                "detergent-left-level": "洗衣液剩余量",
                "fabric-softener-self-delivery": "柔顺剂自动投放",
                "fabric-softener-left-level": "柔顺剂剩余量",

                # ── 水质/净饮类 ──
                "tds_in": "入水水质",
                "tds_out": "出水水质",
                "tds-out": "出水水质",

                # ── 扫地机类 ──
                "suction-level": "吸力档位",
                "sweep-mop-type": "扫拖模式",
                "mop-water-output-level": "抹布水量",
                "battery": "电池电量",
                "current-step-count": "累计步数",
                "current-calorie-consumption": "消耗热量",
                "working-time": "工作时间",
                "current-distance": "跑步里程",

                # ── 状态/故障类 ──
                "status": "状态",
                "on": "开关状态",
                "power": "功率设定",
                "data-value": "数据值",
                "Device Fault": "设备故障",
                "Fault": "故障",
                "Error": "错误",
                "Error Code": "错误代码",
                "Working Time": "工作时间",
                "Remaining Time": "剩余时间",
                "protect-time": "保护时间",
                "anion": "负离子",
                "identify": "定位",

                # ── 浴霸类 ──
                "heating": "制热",
                "blow": "吹风",
                "ventilation": "换气",
                "stop-working": "停止工作",

                # ── 冰箱类 ──
                "Refrigerating Chamber": "冷藏室",
                "Freezing Chamber": "冷冻室",
                "Change Chamber": "变温室",

                # ── 鱼缸类 ──
                "water-pump": "水泵",
                "pump-flux": "水泵水量",
                "automatic-feeding": "自动喂食",
                "no-disturb": "勿扰模式",
            }

            # 硬编码单位映射（属性名 -> 单位，对齐小爱同学标准翻译表）
            UNIT_MAP = {
                "Electric Power": "W",
                "Power Consumption": "kWh",
                "Voltage": "V",
                "Current": "A",
                "temperature": "°C",
                "Temperature": "°C",
                "target_temperature": "°C",
                "target-temperature": "°C",
                "relative_humidity": "%",
                "relative-humidity": "%",
                "humidity": "%",
                "Humidity": "%",
                "target-humidity": "%",
                "target_humidity": "%",
                "pm25_density": "μg/m³",
                "PM2.5": "μg/m³",
                "co2-density": "ppm",
                "CO2": "ppm",
                "hcho-density": "mg/m³",
                "Formaldehyde": "mg/m³",
                "Brightness": "%",
                "brightness": "%",
                "Battery Level": "%",
                "battery-level": "%",
                "filter-life-level": "%",
                "filter_life_level": "%",
                "filter-life-time": "天",
                "filter-left-time": "天",
                "left-time": "分钟",
                "left_time": "分钟",
                "cook-time": "分钟",
                "cook_time": "分钟",
                "target-time": "分钟",
                "drying-time": "分钟",
                "spin-speed": "转",
                "speed-level": "km/h",
                "Illuminance": "lux",
                "illumination": "lux",
                "tds_in": "ppm",
                "tds_out": "ppm",
                "tds-out": "ppm",
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
                        # 窗帘 current-position 算法修正：
                        # MIoT spec 定义 0=关闭, 100=全开
                        # 临界点 0-2 判定为"关着"，3-100 判定为"开着"
                        sdesc_lower = (prop.get("service_desc") or "").lower()
                        is_curtain = "curtain" in sdesc_lower or "窗帘" in sdesc_lower
                        if is_curtain and original_name == "Current Position" and isinstance(value, (int, float)):
                            state_text = "关闭" if value <= 2 else "开启"
                            value_str = f"{'❌' if value <= 2 else '✅'} {state_text}（{value}%）"
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
            
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except Exception as e:
            self.logger.exception("查询设备状态失败")
            return Err(SdkError(f"查询设备状态失败: {e}"))

