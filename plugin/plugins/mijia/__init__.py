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

_EMBEDDED_BY_AGENT = os.getenv("NEKO_PLUGIN_HOSTED_BY_AGENT", "").strip().lower() == "true"

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
                if cache_path.exists():
                    try:
                        cached = await read_json_async(cache_path)
                        cache_user_id = cached.get('user_id')
                        current_user_id = self.api.credential.user_id if self.api.credential else None
                        if not current_user_id or cache_user_id == current_user_id:
                            devices = cached.get("devices", [])
                        else:
                            self.logger.debug(f"设备缓存归属不匹配(u={cache_user_id}→{current_user_id})，跳过")
                    except Exception:
                        pass

                # 获取场景列表（从缓存，需归属校验防止跨用户泄露）
                scenes_cache_path = self.data_path("scenes_cache.json")
                if scenes_cache_path.exists():
                    try:
                        cached = await read_json_async(scenes_cache_path)
                        cache_user_id = cached.get('user_id')
                        current_user_id = self.api.credential.user_id if self.api.credential else None
                        if not current_user_id or cache_user_id == current_user_id:
                            scenes = cached.get("scenes", [])
                        else:
                            self.logger.debug(f"场景缓存归属不匹配(u={cache_user_id}→{current_user_id})，跳过")
                    except Exception:
                        pass
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

    async def _build_room_map(self) -> dict[str, str]:
        """构建 room_id → room_name 映射，用于区域+设备名匹配"""
        if not self.api:
            return {}
        try:
            homes = await self.api.get_homes()
            room_map: dict[str, str] = {}
            for home in homes:
                if not home.rooms:
                    continue
                for room in home.rooms:
                    rid = str(room.get("id", ""))
                    rname = room.get("name", "")
                    if rid and rname:
                        room_map[rid] = rname
            return room_map
        except Exception as e:
            self.logger.debug(f"构建房间映射失败: {e}")
            return {}

    # ========== 设备匹配与命令解析 ==========

    async def _load_devices_cache(self) -> list[dict]:
        """加载设备缓存，不存在时自动拉取"""
        cache_path = self.data_path("devices_cache.json")
        if cache_path.exists() and self.api:
            try:
                cached = await read_json_async(cache_path)
                cache_user_id = cached.get('user_id')
                current_user_id = self.api.credential.user_id if self.api.credential else None
                if not current_user_id or cache_user_id == current_user_id:
                    devices = cached.get('devices', [])
                    # 新逻辑依赖 room_name 字段，旧缓存缺少时自动刷新
                    if devices and 'room_name' in devices[0]:
                        return devices
                    self.logger.info("设备缓存缺少 room_name 字段，自动刷新")
            except Exception:
                pass
        result = await self.list_devices()
        if result.is_ok():
            return result.value.get('devices', [])
        return []

    async def _match_devices(self, name: str) -> dict:
        """统一设备匹配，返回 {"devices": [...], "status": "ok"|"ambiguous"|"not_found", "message": "..."}

        匹配优先级：精确别名 > 精确设备名 > 区域+设备名拆分 > 模糊匹配
        多设备时按房间分组展示，要求用户指定房间。
        """
        devices = await self._load_devices_cache()
        if not devices:
            return {"devices": [], "status": "not_found", "message": "设备列表为空，请先获取设备列表"}

        name_lower = name.lower().strip().replace("的", "")

        # === 精确匹配 ===
        exact = []
        for d in devices:
            alias = d.get('alias', '')
            if alias:
                alias_list = [a.strip().lower() for a in alias.split(',') if a.strip()]
                if name_lower in alias_list:
                    exact.append(d)
                    continue
            if d.get('name', '').lower() == name_lower:
                exact.append(d)

        if len(exact) == 1:
            return {"devices": exact, "status": "ok"}
        if len(exact) > 1:
            return {
                "devices": exact,
                "status": "ambiguous",
                "message": self._format_ambiguous_message(name, exact),
            }

        # === 区域+设备名拆分 ===
        room_map: dict[str, str] = {}
        for d in devices:
            rn = d.get('room_name', '')
            if rn:
                room_map[rn.lower()] = rn

        room_matched = []
        for rn_lower, rn_original in room_map.items():
            if name_lower.startswith(rn_lower):
                device_part = name_lower[len(rn_lower):].strip()
                if device_part:
                    for d in devices:
                        if d.get('room_name', '').lower() != rn_lower:
                            continue
                        dname = d.get('name', '').lower()
                        dalias = d.get('alias', '').lower()
                        if device_part in dname or device_part in dalias:
                            room_matched.append(d)
                    break
            elif name_lower.endswith(rn_lower):
                device_part = name_lower[:-len(rn_lower)].strip()
                if device_part:
                    for d in devices:
                        if d.get('room_name', '').lower() != rn_lower:
                            continue
                        dname = d.get('name', '').lower()
                        dalias = d.get('alias', '').lower()
                        if device_part in dname or device_part in dalias:
                            room_matched.append(d)
                    break

        if len(room_matched) == 1:
            return {"devices": room_matched, "status": "ok"}
        if len(room_matched) > 1:
            return {
                "devices": room_matched,
                "status": "ambiguous",
                "message": self._format_ambiguous_message(name, room_matched),
            }

        # === 模糊匹配（子串） ===
        fuzzy = []
        for d in devices:
            dname = d.get('name', '').lower()
            dalias = d.get('alias', '')
            if name_lower in dname:
                fuzzy.append(d)
                continue
            if dalias:
                alias_list = [a.strip().lower() for a in dalias.split(',') if a.strip()]
                if any(name_lower in a for a in alias_list):
                    fuzzy.append(d)

        if len(fuzzy) == 1:
            return {"devices": fuzzy, "status": "ok"}
        if len(fuzzy) > 1:
            return {
                "devices": fuzzy,
                "status": "ambiguous",
                "message": self._format_ambiguous_message(name, fuzzy),
            }

        # === 完全无匹配，列出所有设备 ===
        all_names = []
        for d in devices:
            rn = d.get('room_name', '')
            dn = d.get('name', '未知')
            alias = d.get('alias', '')
            label = f"{rn} {dn}" if rn else dn
            if alias:
                label += f" (别名: {alias})"
            all_names.append(f"  • {label}")
        return {
            "devices": [],
            "status": "not_found",
            "message": f"未找到匹配 '{name}' 的设备。当前设备列表：\n" + "\n".join(all_names),
        }

    def _format_ambiguous_message(self, query: str, devices: list[dict]) -> str:
        """格式化多设备歧义提示，按房间分组"""
        lines = [f"找到 {len(devices)} 个匹配 '{query}' 的设备："]
        for i, d in enumerate(devices, 1):
            rn = d.get('room_name', '')
            dn = d.get('name', '未知')
            alias = d.get('alias', '')
            status = "🟢" if d.get("is_online") else "🔴"
            label = f"{rn} {dn}" if rn else dn
            if alias:
                label += f" (别名: {alias})"
            lines.append(f"  {i}. {status} {label}")
        lines.append("请用房间名+设备名精确指定，如 '卧室灯'")
        return "\n".join(lines)

    @plugin_entry(
        id="open_ui",
        name="打开配置页面",
        description="在浏览器中打开米家插件的 Web UI 配置页面",
        kind="action"
    )
    async def open_ui(self, **_):
        """在浏览器中打开米家配置页面"""
        url = "http://localhost:48916/plugin/mijia/ui/"
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
        name="重新加载凭据",
        description="重新从文件加载米家凭据并初始化API，防止插件重载后凭据未及时加载导致显示未登录",
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
        name="开始二维码登录",
        description="获取二维码图片并开始登录流程",
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
        name="检查登录状态",
        description="轮询检查二维码登录是否成功",
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
        name="登出",
        description="清除保存的凭据并清空本地数据",
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

    @ui.action(label=tr("actions.refreshDevices.label", default="刷新设备"), tone="secondary", group="device", order=10, refresh_context=True)
    @plugin_entry(
        id="list_devices",
        name="获取设备列表",
        description="获取设备列表，home_id留空自动使用第一个家庭，支持缓存",
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

        # 如果不强制刷新，尝试从缓存读取（必须已登录，防止跨用户缓存泄露）
        if not refresh and cache_path.exists() and self.api:
            try:
                cached = await read_json_async(cache_path)
                # 跨用户/家庭校验，防止缓存泄漏
                cache_home_id = cached.get('home_id')
                cache_user_id = cached.get('user_id')
                current_user_id = self.api.credential.user_id if self.api.credential else None
                # 归属匹配才返回缓存；不匹配时跳过缓存，继续走网络请求
                if cache_home_id == home_id and (not current_user_id or cache_user_id == current_user_id):
                    devices = cached.get('devices', [])
                    self.logger.info(f"从缓存读取设备列表: {len(devices)} 个设备")
                    lines = [f"📱 共有 {len(devices)} 个设备（缓存）:"]
                    for d in devices:
                        status = "🟢" if d.get("is_online") else "🔴"
                        lines.append(f"  {status} {d.get('name')} (型号: {d.get('model')})")
                    message = "\n".join(lines)
                    return Ok({"success": True, "message": message, "devices": devices, "from_cache": True, "count": len(devices)})
                else:
                    self.logger.warning(
                        f"缓存归属不匹配(user_id: {cache_user_id}→{current_user_id}, "
                        f"home_id: {cache_home_id}→{home_id})，跳过缓存"
                    )
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
            # 构建房间映射，注入 room_name 到每个设备
            room_map = await self._build_room_map()
            result = []
            for d in devices:
                room_name = room_map.get(str(d.room_id), "") if d.room_id else ""
                device_info = {
                    "did": d.did,
                    "name": d.name,
                    "model": d.model,
                    "is_online": d.is_online(),
                    "room_id": d.room_id,
                    "room_name": room_name,
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
            
            # 保存到缓存（使用异步写入避免阻塞）
            try:
                user_id = self.api.credential.user_id if self.api and self.api.credential else None
                await atomic_write_json_async(
                    cache_path,
                    {"devices": result, "home_id": home_id, "user_id": user_id},
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
        name="获取缓存的设备列表",
        description="读取本地缓存的设备列表，缓存不存在时自动拉取",
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
        if not refresh and cache_path.exists() and self.api:
            try:
                cached = await read_json_async(cache_path)
                # 跨用户校验，防止缓存泄漏
                cache_user_id = cached.get('user_id')
                current_user_id = self.api.credential.user_id if self.api.credential else None
                # 归属匹配才返回缓存；不匹配时跳过，继续走网络请求
                if not current_user_id or cache_user_id == current_user_id:
                    devices = cached.get('devices', [])
                    self.logger.info(f"AI 从缓存读取设备列表: {len(devices)} 个设备")
                    lines = [f"📱 共有 {len(devices)} 个设备:"]
                    for d in devices:
                        status = "🟢" if d.get("is_online") else "🔴"
                        lines.append(f"  {status} {d.get('name')} (型号: {d.get('model')})")
                    message = "\n".join(lines)
                    return Ok({"success": True, "message": message, "devices": devices, "from_cache": True, "count": len(devices)})
                else:
                    self.logger.warning(
                        f"缓存归属不匹配(user_id: {cache_user_id}→{current_user_id})，跳过缓存"
                    )
            except Exception as e:
                self.logger.warning(f"读取缓存失败: {e}")

        # 缓存不存在或刷新，调用 list_devices
        return await self.list_devices(refresh=refresh)

    @plugin_entry(
        id="list_scenes",
        name="获取智能场景列表",
        description="列出当前账号下所有米家智能场景，支持缓存",
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
        if not refresh and cache_path.exists() and self.api:
            try:
                cached = await read_json_async(cache_path)
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
                else:
                    self.logger.warning(
                        f"场景缓存归属不匹配(user_id: {cache_user_id}→{current_user_id}, "
                        f"home_id: {cache_home_id}→{home_id})，跳过缓存"
                    )
            except Exception as e:
                self.logger.warning(f"读取场景缓存失败: {e}")

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
        name="设置设备别名",
        description="为指定设备设置自定义别名，方便用别名控制设备",
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
        if not cache_path.exists():
            return Err(SdkError("设备缓存不存在，请先获取设备列表"))

        try:
            data = await read_json_async(cache_path)
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
        name="获取设备别名列表",
        description="返回所有设备的别名映射（did -> alias）",
        llm_result_fields=["message"]
    )
    async def get_device_aliases(self, **_):
        """获取所有设备别名"""
        cache_path = self.data_path("devices_cache.json")
        if not cache_path.exists():
            return Ok({"success": True, "aliases": {}, "message": "无缓存数据"})

        try:
            data = await read_json_async(cache_path)
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
        name="智能控制",
        description=(
            "统一设备控制入口，用一句话控制设备或执行场景。"
            "支持：开关（'打开卧室灯'）、亮度（'灯调到50%'）、温度（'空调调26度'）、"
            "模式（'空调调制冷'）、场景（'执行回家场景'）。"
            "支持设备名、别名、房间名+设备名（如'卧室灯'）。"
        ),
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

        self.logger.info(f"智能控制命令: {command}")

        # === 场景执行 ===
        scene_match = re.match(r'(?:执行|运行|触发)\s*(.+)', command.strip())
        if scene_match:
            return await self._execute_scene_by_name(scene_match.group(1).strip())

        # === 开关指令最高优先级（防止被动作分支抢先匹配） ===
        # "打开/关闭/开/关" 开头的是二元开关指令，直接走控制解析，不进动作分支
        _is_switch_cmd = re.match(r'(?:打开|开启|关闭|关掉|开|关)\s*\S', command.strip())

        # === 查询意图识别 ===
        _QUERY_PAT = re.search(
            r'(?:是多少|怎么样|什么状态|几度|多亮|多暗|多热|多冷|'
            r'查询|看看|看一下|剩余|还剩|还有多久)',
            command,
        )
        if _QUERY_PAT:
            # 去掉查询关键词，提取设备名
            device_hint = command[:_QUERY_PAT.start()].strip()
            # 去掉属性名后缀（温度/湿度/亮度/电量等），保留设备名
            device_hint = re.sub(
                r'(?:温度|湿度|亮度|色温|音量|风速|电量|浓度|'
                r'空气质量|PM2\.5|甲醛|水温|滤芯|剩余量|剩余时间)$',
                '', device_hint,
            ).strip()
            if device_hint:
                return await self.query_device_state(device_hint)

        # === 设备操作（开始/暂停/停止/回充等） ===
        # 开关指令已优先处理，此处跳过防止 "关灯" 被误匹配为动作
        _ACTION_VERBS = (
            r'开始|启动|继续|暂停|停止|回充|回去充电|'
            r'出舱|集尘|洗拖布|烘干|建图|召唤清洁'
        )
        act_m = None
        if not _is_switch_cmd:
            act_m = re.match(
                r'(.+?)(?:的|把|让)?\s*(' + _ACTION_VERBS + r')(?:.+)?$',
                command.strip(),
            )
            if not act_m:
                # 动词在前："开始扫地" / "暂停洗衣机"
                act_m2 = re.match(
                    r'(' + _ACTION_VERBS + r')\s*(.+)',
                    command.strip(),
                )
                if act_m2:
                    act_m = type('M', (), {'group': lambda self, n: act_m2.group(3 - n) if n in (1, 2) else None})()

        if act_m:
            device_hint = (act_m.group(1) or "").strip()
            verb = (act_m.group(2) or "").strip()
            if device_hint and verb:
                match_result = await self._match_devices(device_hint)
                if match_result["status"] == "ok" and len(match_result["devices"]) == 1:
                    device = match_result["devices"][0]
                    did = device.get("did")
                    actions = device.get("actions", [])
                    display_name = device.get("alias") or device.get("name", device_hint)

                    # 从动词推断 action name
                    _VERB_TO_ACTION = {
                        "开始": ["start", "start_sweep", "start_wash", "start_cook", "start-work", "start-drying"],
                        "启动": ["start", "start_sweep", "start_wash", "start_cook", "start-work"],
                        "继续": ["start", "resume", "continue"],
                        "暂停": ["pause", "pause-sweeping", "stop-sweeping"],
                        "停止": ["stop", "stop-sweeping", "stop-wash", "stop-working", "cancel_cooking"],
                        "关闭": ["stop", "stop-working", "cancel_cooking"],
                        "回充": ["start-charge", "start_charge"],
                        "回去充电": ["start-charge", "start_charge"],
                        "出舱": ["start-eject"],
                        "集尘": ["start-dust-arrest"],
                        "洗拖布": ["start-mop-wash"],
                        "烘干": ["start-dry"],
                        "建图": ["start-build-map"],
                        "召唤清洁": ["start-call-clean"],
                    }
                    candidates = _VERB_TO_ACTION.get(verb, [verb])
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
                            result = await self.api.call_device_action(
                                did, matched_action["siid"], matched_action["aiid"]
                            )
                            return Ok({"success": True, "message": f"✅ 已对'{display_name}'执行'{verb}'操作", "device": display_name, "action": verb, "result": result})
                        except Exception as e:
                            return Err(SdkError(f"对'{display_name}'执行'{verb}'操作失败: {e}"))
                    else:
                        action_names = [a.get("name") for a in actions]
                        return Err(SdkError(
                            f"'{display_name}'没有'{verb}'操作。可用操作：{', '.join(action_names) if action_names else '无'}"
                        ))

        # === 解析控制命令 ===
        parsed = self._parse_control_command(command)
        if not parsed:
            return Err(SdkError(
                "无法理解命令。支持的格式：\n"
                "  开关：'打开卧室灯' / '关掉插座'\n"
                "  亮度：'灯调到50%' / '灯亮度50'\n"
                "  温度：'空调调26度'\n"
                "  模式：'空调调制冷'\n"
                "  场景：'执行回家场景'"
            ))

        device_name = parsed["device"]
        action = parsed["action"]
        prop_name = parsed.get("prop")
        value = parsed.get("value")

        self.logger.info(f"解析结果: device=\"{device_name}\", action={action}, prop={prop_name}, value={value}")

        # === 匹配设备 ===
        match_result = await self._match_devices(device_name)
        if match_result["status"] == "not_found":
            return Err(SdkError(match_result["message"]))
        if match_result["status"] == "ambiguous":
            return Err(SdkError(match_result["message"]))

        device = match_result["devices"][0]
        did = device.get("did")
        display_name = device.get("alias") or device.get("name", device_name)
        props = device.get("properties", [])

        # === 开关控制 ===
        if action == "switch":
            # 收集所有可写的开关属性
            switch_props = []
            for p in props:
                pname = p.get("name", "").lower()
                if any(k in pname for k in ["开关", "电源", "power", "switch", "on"]):
                    if p.get("access") in ["write", "read_write", "notify_read_write"]:
                        switch_props.append(p)
            # fallback: 找第一个可写 bool 属性
            if not switch_props:
                for p in props:
                    if p.get("access") in ["write", "read_write", "notify_read_write"] and p.get("type") == "bool":
                        switch_props.append(p)
                        break

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
                cmd_lower = command.lower()
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
                if success:
                    return Ok({"success": True, "message": f"✅ 已{action_text}'{display_name}'", "device": display_name, "action": action_text})
                else:
                    return Ok({"success": False, "message": f"❌ {action_text}'{display_name}'失败"})
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

        # === 相对值调整（调亮一点/温度高一点） ===
        if action == "adjust_prop":
            direction = parsed.get("direction", 1)
            delta = parsed.get("delta")
            prop = self._find_property_for_control(props, prop_name, None)
            if not prop:
                available = [p.get("name") for p in props if p.get("access") in ["write", "read_write", "notify_read_write"]]
                return Err(SdkError(f"'{display_name}'没有可控制的'{prop_name}'属性。可控制属性：{', '.join(available) if available else '无'}"))

            siid = prop.get("siid")
            piid = prop.get("piid")
            vr = prop.get("value_range", [])
            v_min = vr[0] if len(vr) >= 1 else 0
            v_max = vr[1] if len(vr) >= 2 else 100
            step = vr[2] if len(vr) >= 3 else 1

            # 读取当前值
            try:
                cur_results = await self.api.get_device_properties([{"did": did, "siid": siid, "piid": piid}])
                cur_value = cur_results[0].get("value") if cur_results else None
            except Exception:
                cur_value = None

            if cur_value is None:
                return Err(SdkError(f"无法读取'{display_name}'的{prop_name}当前值"))

            # 计算目标值
            if delta is not None:
                target = cur_value + direction * delta
            else:
                # 默认调整步长：范围的 10%
                range_size = v_max - v_min
                default_step = max(step, range_size * 0.1)
                target = cur_value + direction * default_step

            target = max(v_min, min(v_max, target))
            # 对齐步长
            if step > 1:
                target = round((target - v_min) / step) * step + v_min

            try:
                success = await self.api.control_device(did, siid, piid, target)
                if success:
                    return Ok({"success": True, "message": f"✅ 已将'{display_name}'的{prop_name}从{cur_value}调整为{target}", "device": display_name, "property": prop_name, "value": target})
                else:
                    return Ok({"success": False, "message": f"❌ 调整'{display_name}'的{prop_name}失败"})
            except TokenExpiredError:
                return Err(SdkError("凭据已过期，请重新登录"))
            except MijiaAPIException as e:
                return Err(SdkError(f"调整'{display_name}'的{prop_name}失败: {e}"))
            except Exception as e:
                self.logger.exception("调整失败")
                return Err(SdkError(f"调整失败: {e}"))

        # === 属性控制（亮度/温度/模式等） ===
        if not prop_name:
            return Err(SdkError("请指定要调整的属性，如'灯亮度50%'"))

        prop = self._find_property_for_control(props, prop_name, value)
        if not prop:
            available = [p.get("name") for p in props if p.get("access") in ["write", "read_write", "notify_read_write"]]
            return Err(SdkError(f"'{display_name}'没有可控制的'{prop_name}'属性。可控制属性：{', '.join(available) if available else '无'}"))

        # 极值处理："最高"/"最低" → 从 value_range 取边界
        if value in ("max", "min"):
            vr = prop.get("value_range", [])
            if len(vr) >= 2:
                value = vr[1] if value == "max" else vr[0]
            elif prop.get("value_list"):
                vals = [item.get("value", 0) for item in prop.get("value_list", [])]
                value = max(vals) if value == "max" else min(vals)
            else:
                return Err(SdkError(f"'{display_name}'的{prop_name}没有值范围信息，无法设置极值"))

        # 模式/档位枚举转换：中文 → 设备 spec 数字值
        if isinstance(value, str) and prop.get("value_list"):
            resolved = self._resolve_enum_value(prop, value)
            if resolved is not None:
                self.logger.info(f"枚举转换: '{value}' → {resolved}")
                value = resolved
            else:
                available_modes = [f"{item.get('description')}(={item.get('value')})" for item in prop.get("value_list", [])]
                return Err(SdkError(
                    f"'{display_name}'的{prop_name}不支持'{value}'。"
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
                return Ok({"success": True, "message": f"✅ 已将'{display_name}'的{prop_name}设为{value_display}", "device": display_name, "property": prop_name, "value": value})
            else:
                return Ok({"success": False, "message": f"❌ 设置'{display_name}'的{prop_name}失败"})
        except TokenExpiredError:
            return Err(SdkError("凭据已过期，请重新登录"))
        except MijiaAPIException as e:
            self.logger.warning(f"API控制失败: device={display_name}, did={did}, siid={siid}, piid={piid}, value={value}, error={e}")
            if e.code == -6:
                return Err(SdkError(f"设置'{display_name}'的{prop_name}失败：设备不支持该操作或参数有误"))
            return Err(SdkError(f"设置'{display_name}'的{prop_name}失败: {e}"))
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

    def _parse_control_command(self, command: str) -> Optional[dict]:
        """解析控制命令，返回 {device, action, prop?, value?}

        action: "switch" | "set_prop"
        """
        cmd = command.strip()

        # 场景命令不在此处理
        if re.match(r'(?:执行|运行|触发)', cmd):
            return None

        # === 开关命令：动词在最前面，直接切 ===
        for kw in ["打开", "开启", "开"]:
            if cmd.startswith(kw):
                device = cmd[len(kw):].strip()
                return {"device": device, "action": "switch", "value": True} if device else None
        for kw in ["关闭", "关掉", "关"]:
            if cmd.startswith(kw):
                device = cmd[len(kw):].strip()
                return {"device": device, "action": "switch", "value": False} if device else None

        # === 属性/模式命令：找分界线 ===
        # 左边 = 设备引用（原样传给 _match_devices），右边 = 意图
        # 分界线三种方式，按优先级尝试：
        _VERB = r'调到|调成|调为|设为|设置为|调至|切换到|切换至'
        _PROP = r'亮度|色温|温度|音量|风速|浓度|湿度|位置|吸力|档位|水温|水量|角度|转速'
        _MODE = (
            r'制冷|制热|自动|送风|除湿|睡眠|节能|静音|强力|舒适|标准|日光|月光|彩光|温馨|'
            r'电视|阅读|电脑|娱乐|休闲|办公|儿童|夜灯|自然风|直吹风|冷风|烘干|风干|'
            r'换气|干燥|吹风|待机|恒温|热风|暖风|清洁|快洗|轻柔|大件|羊毛|棉麻|'
            r'化纤|衬衣|桶自洁|婴童|冲锋衣|智能洗|内衣|丝绸|牛仔|蒸汽|护色|防过敏|'
            r'顽渍|节能洗|标准洗|玻璃洗|预洗|少量洗|消毒|奶瓶|分层|随心|及时|'
            r'速冷|速冻|假日|手动|最爱|智能|夜光'
        )

        device_ref = None
        intent = None

        # 1) 动词分界："空调调到26度" → "空调" | "调到26度"
        verb_re = re.compile(_VERB)
        verb_m = verb_re.search(cmd)
        if verb_m:
            device_ref = cmd[:verb_m.start()].strip()
            intent = cmd[verb_m.start():]

        # 1.5) 单字"调"分界（仅后跟数字或模式词时）：
        #      "空调调26度" → "空调" | "调26度"，"空调调制冷" → "空调" | "调制冷"
        if not device_ref:
            tiao_m = re.search(r'调(?=\d|(?:' + _MODE + r'))', cmd)
            if tiao_m:
                device_ref = cmd[:tiao_m.start()].strip()
                intent = cmd[tiao_m.start():]

        # 2) 属性/模式词分界："灯亮度50%" → "灯" | "亮度50%"
        if not device_ref:
            prop_mode_re = re.compile(r'(?:' + _PROP + r'|' + _MODE + r')')
            pm_m = prop_mode_re.search(cmd)
            if pm_m:
                device_ref = cmd[:pm_m.start()].strip()
                intent = cmd[pm_m.start():]

        # 3) 纯数字分界（仅当前一位是中文时，避免误切含数字的设备名）：
        #    "卧室灯50%" → "卧室灯" | "50%"
        if not device_ref:
            num_m = re.search(r'(?<=[一-鿿])(\d)', cmd)
            if num_m:
                device_ref = cmd[:num_m.start()].strip()
                intent = cmd[num_m.start():]

        if not device_ref or not intent:
            return None

        # === 解析意图 ===

        # 模式命令："制冷" / "自动模式" / "调制冷"
        mode_m = re.match(r'(?:(?:' + _VERB + r'))?\s*(' + _MODE + r')(?:模式)?$', intent)
        if mode_m:
            return {"device": device_ref, "action": "set_prop", "prop": "模式", "value": mode_m.group(1)}

        # 属性 + 数值："亮度50%" / "调到50%" / "调到26度" / "温度26" / "50%"
        val_m = re.search(
            r'(?:' + _VERB + r')?\s*'
            r'(' + _PROP + r')?'
            r'(\d+(?:\.\d+)?)'
            r'\s*(%|度|℃|°)?',
            intent,
        )
        if val_m:
            prop_name = val_m.group(1)
            num_str = val_m.group(2)
            unit = val_m.group(3) or ""
            value = float(num_str) if '.' in num_str else int(num_str)

            # 无属性名时靠单位推断
            if not prop_name:
                if unit in ("度", "℃", "°"):
                    prop_name = "温度"
                elif unit == "%" or (isinstance(value, int) and 0 <= value <= 100):
                    prop_name = "亮度"
                else:
                    return None

            return {"device": device_ref, "action": "set_prop", "prop": prop_name, "value": value}

        # 相对值调整："调亮一点" / "温度高一点" / "风速调大一点"
        _ADJUST_UP = r'高|大|亮|暖|多|强|快|升'
        _ADJUST_DOWN = r'低|小|暗|冷|少|弱|慢|降'
        adj_m = re.search(
            r'(' + _PROP + r')?\s*(?:调|设|切)?\s*'
            r'(' + _ADJUST_UP + r'|' + _ADJUST_DOWN + r')'
            r'(?:一?点|一些|一?些|一?些|少许)?',
            intent,
        )
        if adj_m:
            prop_name = adj_m.group(1)
            direction_word = adj_m.group(2)
            direction = 1 if re.match(_ADJUST_UP, direction_word) else -1
            if not prop_name:
                # 从意图中推断属性名
                if re.search(r'度|热|冷', intent):
                    prop_name = "温度"
                elif re.search(r'亮|暗|光', intent):
                    prop_name = "亮度"
                elif re.search(r'风|速|档', intent):
                    prop_name = "风速"
                elif re.search(r'音|声', intent):
                    prop_name = "音量"
                elif re.search(r'湿', intent):
                    prop_name = "湿度"
                else:
                    prop_name = "亮度"
            # 检查是否有具体 delta 值
            delta_m = re.search(r'(\d+(?:\.\d+)?)\s*(%|度|℃)?', intent)
            delta = float(delta_m.group(1)) if delta_m else None
            return {
                "device": device_ref, "action": "adjust_prop",
                "prop": prop_name, "direction": direction, "delta": delta,
            }

        # 极值："调到最高" / "调到最低" / "最亮" / "最暗"
        _EXTREME = r'最高|最低|最亮|最暗|最大|最小|最强|最弱|最快|最慢|最暖|最冷|最多|最少'
        ext_m = re.search(
            r'(' + _PROP + r')?\s*(?:调到|调成|调为|设为)?\s*(' + _EXTREME + r')',
            intent,
        )
        if ext_m:
            prop_name = ext_m.group(1)
            extreme_word = ext_m.group(2)
            extreme = "max" if extreme_word in ["最高", "最亮", "最大", "最强", "最快", "最暖", "最多"] else "min"
            if not prop_name:
                if re.search(r'度|热|冷', intent):
                    prop_name = "温度"
                elif re.search(r'亮|暗', intent):
                    prop_name = "亮度"
                elif re.search(r'风|速|档', intent):
                    prop_name = "风速"
                elif re.search(r'音|声', intent):
                    prop_name = "音量"
                elif re.search(r'湿', intent):
                    prop_name = "湿度"
                else:
                    prop_name = "亮度"
            return {"device": device_ref, "action": "set_prop", "prop": prop_name, "value": extreme}

        # 颜色控制："灯调到红色" / "灯设成蓝色"
        _COLOR_MAP = {
            "红": 0xFF0000, "红色": 0xFF0000,
            "绿": 0x00FF00, "绿色": 0x00FF00,
            "蓝": 0x0000FF, "蓝色": 0x0000FF,
            "黄": 0xFFFF00, "黄色": 0xFFFF00,
            "紫": 0xFF00FF, "紫色": 0xFF00FF,
            "橙": 0xFFA500, "橙色": 0xFFA500,
            "粉": 0xFFC0CB, "粉色": 0xFFC0CB, "粉红": 0xFFC0CB,
            "白": 0xFFFFFF, "白色": 0xFFFFFF,
            "青": 0x00FFFF, "青色": 0x00FFFF,
            "黑": 0x000000, "黑色": 0x000000,
            "暖白": 0xFFF4E0, "冷白": 0xF0F8FF,
        }
        color_m = re.search(
            r'(?:调到|调成|设为|设成|切换到|切换至)\s*(.+?)(?:模式)?$',
            intent,
        )
        if color_m:
            color_word = color_m.group(1).strip()
            rgb = _COLOR_MAP.get(color_word)
            if rgb is not None:
                return {"device": device_ref, "action": "set_prop", "prop": "颜色", "value": rgb}

        return None

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
            desc = str(item.get("description", ""))
            desc_lower = desc.lower()
            # 英文枚举名精确匹配
            if any(desc_lower == kw.lower() for kw in en_keywords):
                return item["value"]
            # 英文枚举名包含匹配
            if any(kw.lower() in desc_lower for kw in en_keywords):
                return item["value"]
            # 中文直接匹配（value_list 描述可能是中文）
            if chinese_lower in desc_lower:
                return item["value"]

        return None

    async def _execute_scene_by_name(self, scene_name: str) -> Any:
        """按场景名称查找并执行场景"""
        cache_path = self.data_path("scenes_cache.json")
        scenes = []
        cached_home_id = None
        if cache_path.exists():
            try:
                cached = await read_json_async(cache_path)
                scenes = cached.get('scenes', [])
                cached_home_id = cached.get('home_id')
            except Exception:
                pass

        if not scenes:
            return Err(SdkError("场景列表为空，请先获取场景列表"))

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
        name="查询设备状态",
        description="按名称查询设备所有可读属性的当前值，支持设备名、别名、房间名+设备名（如'卧室灯'）",
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
        match_result = await self._match_devices(name)
        if match_result["status"] == "not_found":
            return Err(SdkError(match_result["message"]))
        if match_result["status"] == "ambiguous":
            return Err(SdkError(match_result["message"]))

        devices = match_result["devices"]
        
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
                "vertical_swing": "上下摆风",
                "vertical_swing": "上下扫风",
                "Vertical Swing": "上下摆风",
                "horizontal_swing": "左右摆风",
                "Horizontal Swing": "左右摆风",
                "Sleep Mode": "睡眠模式",
                "sleep-mode": "睡眠模式",
                "Eco Mode": "节能模式",
                "eco": "节能模式",
                "Dry Mode": "除湿模式",
                "dryer": "干燥模式",
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
                "dryer": "烘干/风干",

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

