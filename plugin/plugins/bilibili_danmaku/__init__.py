"""
Bilibili 弹幕插件 (Bilibili-Danmaku)

功能：
- 监听 B站直播间弹幕，过滤后按配置间隔推送给 AI，AI 自动以 TTS 语音回复观众（虚拟主播模式）
- SC、高价值礼物即时推送通知给 AI，触发语音感谢
- AI 可通过 send_danmaku 发送弹幕到直播间（需登录）
- 自动读取 NEKO 项目已保存的 B站 Cookie（无需重复登录）
- 游客模式：仅基础敏感词过滤
- 登录模式：支持等级过滤、礼物价值过滤

入口：
- set_room_id      更改监听的直播间
- set_interval     更改推送给 AI 的弹幕间隔（5s ~ 180s）
- send_danmaku     发送弹幕到直播间（需登录）
- get_danmaku      获取最新弹幕
- get_status       获取插件状态
- save_credential  保存 B站登录凭据
- clear_credential 清除 B站登录凭据
- reload_credential 重新加载凭据
- connect / disconnect 开始/停止监听
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    timer_interval,
    Ok,
    Err,
    SdkError,
    get_plugin_logger,
)

from .danmaku_core import DanmakuListener
from .filter import DanmakuFilter, get_level_tier, get_level_weekly_bonus


# ==========================================
# 本地凭据类（替代 bilibili_api.Credential，无外部依赖）
# ==========================================
class _BiliCredential:
    """轻量 B站凭据容器，仅存储 Cookie 字段供 DanmakuListener 使用"""

    def __init__(
        self,
        sessdata: str = "",
        bili_jct: str = "",
        buvid3: str = "",
        dedeuserid: str = "",
    ):
        self.sessdata = sessdata
        self.bili_jct = bili_jct
        self.buvid3 = buvid3
        self.dedeuserid = dedeuserid


# ==========================================
# 常量
# ==========================================
MIN_INTERVAL = 5        # 最小推送间隔（秒）
MAX_INTERVAL = 180      # 最大推送间隔（秒）
DEFAULT_INTERVAL = 10   # 默认推送间隔（秒）
DEFAULT_ROOM_ID = 0     # 0 表示未配置

# ==========================================
# 插件级加密 Cookie 工具（Fernet，独立密钥）
# ==========================================
_PLUGIN_CRED_FILE = "bili_credential.enc"
_PLUGIN_KEY_FILE  = "bili_credential.key"


def _get_fernet(data_dir: Path):
    """获取或生成插件本地 Fernet 实例，密钥存 data_dir/<_PLUGIN_KEY_FILE>"""
    from cryptography.fernet import Fernet
    key_path = data_dir / _PLUGIN_KEY_FILE
    if key_path.exists():
        key = key_path.read_bytes()
    else:
        key = Fernet.generate_key()
        data_dir.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(key)
        if sys.platform != "win32":
            os.chmod(str(key_path), 0o600)
    return Fernet(key)


def _save_credential_encrypted(data_dir: Path, cred: dict) -> bool:
    """加密保存凭据字典到 data_dir/<_PLUGIN_CRED_FILE>"""
    try:
        f = _get_fernet(data_dir)
        enc = f.encrypt(json.dumps(cred, ensure_ascii=False).encode("utf-8"))
        cred_path = data_dir / _PLUGIN_CRED_FILE
        cred_path.write_bytes(enc)
        if sys.platform != "win32":
            os.chmod(str(cred_path), 0o600)
        return True
    except Exception:
        return False


def _load_credential_encrypted(data_dir: Path) -> Optional[Dict[str, str]]:
    """从 data_dir/<_PLUGIN_CRED_FILE> 解密读取凭据字典，失败返回 None"""
    try:
        cred_path = data_dir / _PLUGIN_CRED_FILE
        if not cred_path.exists():
            return None
        key_path = data_dir / _PLUGIN_KEY_FILE
        if not key_path.exists():
            return None
        from cryptography.fernet import Fernet
        key = key_path.read_bytes()
        fernet = Fernet(key)
        dec = fernet.decrypt(cred_path.read_bytes()).decode("utf-8")
        return json.loads(dec)
    except Exception:
        return None


def _delete_credential_files(data_dir: Path):
    """删除插件本地凭据文件"""
    for fname in (_PLUGIN_CRED_FILE, _PLUGIN_KEY_FILE):
        p = data_dir / fname
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass


@neko_plugin
class BiliDanmakuPlugin(NekoPluginBase):
    """B站直播弹幕插件"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = get_plugin_logger(__name__)

        # 监听器
        self._listener: Optional[DanmakuListener] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._connecting: bool = False  # 正在建立连接（task已创建但WebSocket未就绪）

        # 过滤器
        self._filter: Optional[DanmakuFilter] = None

        # 弹幕队列（缓冲未推送的弹幕）
        # _danmaku_queue：供 AI 定时推送消费（push_danmaku entry）
        # _ui_queue：供 UI 实时展示消费（get_danmaku entry）
        self._danmaku_queue: deque = deque(maxlen=200)
        self._gift_queue: deque = deque(maxlen=50)
        self._sc_queue: deque = deque(maxlen=20)

        self._ui_danmaku_queue: deque = deque(maxlen=500)
        self._ui_gift_queue: deque = deque(maxlen=100)
        self._ui_sc_queue: deque = deque(maxlen=50)

        # 统计
        self._total_received = 0
        self._total_filtered = 0
        self._total_pushed = 0

        # 配置（从 config.json 加载）
        self._room_id: int = DEFAULT_ROOM_ID
        self._interval: int = DEFAULT_INTERVAL  # 秒
        self._target_lanlan: str = ""  # 弹幕推送的目标 AI 名称（留空不指定）
        self._danmaku_max_length: int = 20  # 弹幕最大长度限制（B站限制 20 字符）
        self._bilibili_credential = None
        self._is_logged_in: bool = False
        self._last_push_time: float = 0.0  # 上次推送时间戳（内存变量）

        # 推送限流（防止弹幕频繁时刷屏）
        self._last_push_ts: float = 0.0       # 上次 push_message 时间戳
        self._push_cooldown: float = 5.0       # 两次推送最小间隔（秒）
        self._push_sc_threshold: int = 1       # SC 价格阈值（元），≥此值自动推送
        self._push_gift_threshold: float = 1.0 # 礼物价值阈值（RMB），≥此值自动推送

    # ==========================================
    # 生命周期
    # ==========================================

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        """插件启动：加载配置，尝试读取 B站凭据，启动监听"""
        self.logger.info("Bilibili弹幕插件启动中...")

        # 加载插件配置
        self._load_plugin_config()

        # 尝试从 NEKO 读取 B 站凭据
        await self._load_bilibili_credential()

        # 初始化过滤器
        self._init_filter()

        # 注册静态 UI
        if (self.config_dir / "static").exists():
            ok = self.register_static_ui(
                "static",
                index_file="index.html",
                cache_control="no-cache, no-store, must-revalidate",
            )
            if ok:
                self.logger.info("✅ 弹幕控制台已注册，访问: http://localhost:48916/plugin/bilibili-danmaku/ui/")
            else:
                self.logger.warning("注册静态UI失败")

        # 如果配置了房间号，自动启动监听
        if self._room_id > 0:
            asyncio.create_task(self._start_listening())
            self.logger.info(f"自动启动监听直播间 {self._room_id}")
        else:
            self.logger.warning("未配置直播间ID，请在控制台或使用 set_room_id 配置")

        return Ok({
            "status": "ready",
            "room_id": self._room_id,
            "interval": self._interval,
            "logged_in": self._is_logged_in,
            "message": f"✅ 弹幕插件已启动\n{'🔐 已登录模式' if self._is_logged_in else '👤 游客模式'}\n直播间: {self._room_id or '未配置'}\n推送间隔: {self._interval}s"
        })

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        """插件关闭"""
        self.logger.info("Bilibili弹幕插件关闭")
        await self._stop_listening()
        return Ok({"status": "stopped"})

    # ==========================================
    # 内部方法：配置加载
    # ==========================================

    def _load_plugin_config(self):
        """从插件 data/config.json 加载配置"""
        config_path = self.data_path("config.json")
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                self._room_id = int(cfg.get("room_id", DEFAULT_ROOM_ID))
                raw_interval = int(cfg.get("interval_seconds", DEFAULT_INTERVAL))
                self._interval = max(MIN_INTERVAL, min(MAX_INTERVAL, raw_interval))
                self._target_lanlan = str(cfg.get("target_lanlan", "")).strip()
                self._danmaku_max_length = int(cfg.get("danmaku_max_length", 20))
                # 弹幕长度限制：B站实际限制为 20 字符/秒
                self._danmaku_max_length = max(1, min(100, self._danmaku_max_length))
                self.logger.info(f"已加载配置: room_id={self._room_id}, interval={self._interval}s, target_lanlan='{self._target_lanlan}', danmaku_max_length={self._danmaku_max_length}")
            except Exception as e:
                self.logger.warning(f"加载配置失败，使用默认值: {e}")
        else:
            # 写入默认配置
            self._save_plugin_config()

        # 清理旧版推送时间记录文件（已改用内存变量）
        legacy_file = self.data_path("last_push.txt")
        if legacy_file.exists():
            try:
                legacy_file.unlink()
            except Exception:
                pass

    def _save_plugin_config(self):
        """保存配置到 data/config.json"""
        config_path = self.data_path("config.json")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        cfg = {
            "room_id": self._room_id,
            "interval_seconds": self._interval,
            "_comment_interval": f"推送间隔范围 {MIN_INTERVAL}~{MAX_INTERVAL} 秒",
            "target_lanlan": self._target_lanlan,
            "_comment_target_lanlan": "弹幕推送的目标 AI 名称（应与 lanlan_name 一致，留空则不指定）",
            "danmaku_max_length": self._danmaku_max_length,
            "_comment_danmaku_max_length": "发送弹幕的最大长度限制（B站限制 20 字符，建议 20）",
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    async def _load_bilibili_credential(self):
        """从插件本地加密存储或 NEKO 全局凭据文件读取 B 站 Cookie"""
        # ── 1. 优先读插件自己保存的加密 Cookie ────────────────────
        try:
            local_cred = _load_credential_encrypted(self.data_path())
            if local_cred and local_cred.get("SESSDATA"):
                self._bilibili_credential = _BiliCredential(
                    sessdata=local_cred.get("SESSDATA", ""),
                    bili_jct=local_cred.get("bili_jct", ""),
                    buvid3=local_cred.get("buvid3", ""),
                    dedeuserid=local_cred.get("DedeUserID", ""),
                )
                self._is_logged_in = True
                self.logger.info("✅ 已读取插件本地加密凭据，使用登录模式")
                return
        except Exception as e:
            self.logger.warning(f"读取插件本地凭据失败: {e}")

        # ── 2. Fallback：读取 NEKO 全局保存的 B 站 Cookie ─────────
        try:
            from utils.cookies_login import load_cookies_from_file
            cookies = load_cookies_from_file("bilibili")
            if cookies and cookies.get("SESSDATA"):
                self._bilibili_credential = _BiliCredential(
                    sessdata=cookies.get("SESSDATA", ""),
                    bili_jct=cookies.get("bili_jct", ""),
                    buvid3=cookies.get("buvid3", ""),
                    dedeuserid=cookies.get("DedeUserID", ""),
                )
                self._is_logged_in = True
                self.logger.info("✅ 已读取 NEKO 全局 B站凭据，使用登录模式")
            else:
                self._is_logged_in = False
                self.logger.info("👤 未找到 B站凭据，使用游客模式")
        except Exception as e:
            self._is_logged_in = False
            self.logger.warning(f"读取 B站凭据失败: {e}，使用游客模式")

    async def _push_to_ai(self, content: str, summary: str, priority: int = 5):
        """
        将弹幕内容注入到 AI 对话，触发 TTS 语音回复。
        优先通过 /api/internal/inject_text 直接注入（不依赖 WebSocket）；
        失败时回落到 push_message（proactive_notification）。
        内置限流，避免短时间内频繁推送。
        """
        now = datetime.now().timestamp()
        if now - self._last_push_ts < self._push_cooldown:
            self.logger.debug(f"push 限流中，跳过（距上次 {now - self._last_push_ts:.1f}s）")
            return
        self._last_push_ts = now
        await self._do_push_to_ai(content, summary, priority)

    async def _do_push_to_ai(self, content: str, summary: str, priority: int):
        """
        将弹幕内容通过 push_message 推送给 AI，触发语音回复。
        参考 memo_reminder 插件的通道方式，直接用 push_message 即可。
        """
        # 包装成猫娘视角的弹幕提示，让她知道是直播间消息
        danmaku_notice = (
            f"【B站直播间弹幕】{content}\n"
            "（这是你在直播时收到的实时弹幕，可以自然地回应一下~）"
        )
        try:
            self.push_message(
                source="bilibili_danmaku",
                message_type="proactive_notification",
                description=f"📺 {summary[:60]}",
                priority=priority,
                content=danmaku_notice,
                metadata={
                    "room_id": self._room_id,
                    "plugin_id": "bilibili-danmaku",
                },
                target_lanlan=self._target_lanlan if self._target_lanlan else None,  # 可配置的目标 AI
            )
            self.logger.info(f"📤 push_message 成功: {summary[:50]}")
        except Exception as e:
            self.logger.warning(f"push_message 失败: {e}")

    def _init_filter(self):
        """初始化过滤器"""
        # 加载过滤器配置
        filter_cfg_path = self.data_path("filter_config.json")
        filter_cfg = {}
        if filter_cfg_path.exists():
            try:
                with open(filter_cfg_path, "r", encoding="utf-8") as f:
                    filter_cfg = json.load(f)
            except Exception:
                pass

        config = {
            "is_logged_in": self._is_logged_in,
            "filter": filter_cfg,
        }
        self._filter = DanmakuFilter(config)
        self.logger.info(f"过滤器: {self._filter.describe_mode()}")

    # ==========================================
    # 内部方法：监听控制
    # ==========================================

    async def _start_listening(self):
        """启动弹幕监听"""
        if self._room_id <= 0:
            self.logger.error("未设置直播间ID")
            return

        # 先停止已有的监听
        await self._stop_listening()

        # 清空队列
        self._danmaku_queue.clear()
        self._gift_queue.clear()
        self._sc_queue.clear()
        self._ui_danmaku_queue.clear()
        self._ui_gift_queue.clear()
        self._ui_sc_queue.clear()

        callbacks = {
            "on_danmaku": self._on_danmaku,
            "on_gift": self._on_gift,
            "on_sc": self._on_sc,
            "on_entry": self._on_entry,
            "on_follow": self._on_follow,
            "on_live": self._on_live,
            "on_preparing": self._on_preparing,
            "on_error": self._on_error,
        }

        self._listener = DanmakuListener(
            room_id=self._room_id,
            credential=self._bilibili_credential,
            logger=self.logger,
            callbacks=callbacks,
            danmaku_max_length=self._danmaku_max_length,  # 从配置读取
        )

        self._listen_task = asyncio.create_task(self._run_listener())
        self._connecting = True
        self.logger.info(f"🎬 开始监听直播间 {self._room_id}（{'登录' if self._is_logged_in else '游客'}模式）")

    async def _stop_listening(self):
        """停止弹幕监听"""
        self._connecting = False
        if self._listener and self._listener.is_running():
            await self._listener.stop()
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        self._listener = None
        self._listen_task = None

        # 清空所有缓冲队列，防止停止后继续推送旧弹幕
        self._danmaku_queue.clear()
        self._sc_queue.clear()
        self._gift_queue.clear()
        self._ui_danmaku_queue.clear()
        self._ui_gift_queue.clear()
        self._ui_sc_queue.clear()
        self.logger.info("已清空弹幕缓冲队列")

    async def _run_listener(self):
        """包装 DanmakuListener.start()，连接成功后清除 _connecting 标记"""
        try:
            # listener.start() 内部在建立 WebSocket 后才进入消息循环
            # 为了让 _connecting 尽快变 False，监听 running 标志
            if self._listener:
                # 启动监听（阻塞直到断开）
                await self._listener.start()
        finally:
            self._connecting = False

    # ==========================================
    # 弹幕事件回调
    # ==========================================

    def _on_danmaku(self, data: dict):
        """收到弹幕"""
        self._total_received += 1

        # 过滤
        passed, reason = self._filter.check_danmaku(data)
        if not passed:
            self._total_filtered += 1
            self.logger.debug(f"弹幕过滤: {reason} | {data.get('user_name')}: {data.get('content')}")
            return

        self.logger.info(f"💬 弹幕入队: [{data.get('medal_text','')}]{data.get('user_name')}: {data.get('content')}")

        item = {
            "type": "danmaku",
            "time": data.get("time", ""),
            "user_name": data.get("user_name", ""),
            "user_level": data.get("user_level", 0),
            "medal": data.get("medal_text", ""),
            "content": data.get("content", ""),
            "received_at": datetime.now().isoformat(),
        }
        self._danmaku_queue.append(item)      # AI 推送队列
        self._ui_danmaku_queue.append(item)   # UI 展示队列

    def _on_gift(self, data: dict):
        """收到礼物"""
        passed, reason = self._filter.check_gift(data)
        if not passed:
            self.logger.debug(f"礼物过滤: {reason} | {data.get('user_name')}: {data.get('gift_name')}")
            return

        item = {
            "type": "gift",
            "user_name": data.get("user_name", ""),
            "gift_name": data.get("gift_name", ""),
            "num": data.get("num", 1),
            "price_rmb": round(data.get("total_coin", 0) / 1000, 2),  # 总金瓜子换算 RMB
            "received_at": datetime.now().isoformat(),
        }
        self._gift_queue.append(item)       # AI 推送队列
        self._ui_gift_queue.append(item)    # UI 展示队列

        # 高价值礼物自动推送给 AI（在 B站监听事件循环里，可用 create_task）
        if item["price_rmb"] >= self._push_gift_threshold:
            price_str = f"≈¥{item['price_rmb']:.1f}" if item["price_rmb"] > 0 else ""
            content = (
                f"======[直播间礼物] 你正在直播，有观众送了礼物，请用语音感谢TA！======\n"
                f"🎁 用户: {item['user_name']}\n"
                f"   礼物: {item['num']}个 {item['gift_name']} {price_str}"
            )
            summary = f"礼物 {item['num']}×{item['gift_name']} {price_str} | {item['user_name']}"
            asyncio.create_task(self._push_to_ai(content, summary, priority=7))

    def _on_sc(self, data: dict):
        """收到 SuperChat"""
        passed, reason = self._filter.check_sc(data)
        if not passed:
            self.logger.debug(f"SC过滤: {reason} | {data.get('user_name')}: {data.get('message')}")
            return

        item = {
            "type": "superchat",
            "user_name": data.get("user_name", ""),
            "message": data.get("message", ""),
            "price": data.get("price", 0),
            "received_at": datetime.now().isoformat(),
        }
        self._sc_queue.append(item)       # AI 推送队列
        self._ui_sc_queue.append(item)    # UI 展示队列

        # 自动推送 SC 给 AI（在 B站监听事件循环里，可用 create_task）
        price = item.get("price", 0)
        if price >= self._push_sc_threshold:
            content = (
                f"======[直播间SuperChat] 你正在直播，有观众发了SC，请用语音感谢并回应TA！======\n"
                f"💰 用户: {item['user_name']}\n"
                f"   金额: ¥{price}\n"
                f"   内容: {item['message']}"
            )
            summary = f"SC ¥{price} | {item['user_name']}: {item['message'][:20]}"
            asyncio.create_task(self._push_to_ai(content, summary, priority=8))

    def _on_entry(self, user_name: str):
        pass  # 进场提示不推送给 AI

    def _on_follow(self, user_name: str):
        pass  # 关注提示不推送给 AI

    def _on_live(self):
        self.logger.info(f"🎬 直播间 {self._room_id} 开播了！")

    def _on_preparing(self):
        self.logger.info(f"📴 直播间 {self._room_id} 已下播")

    def _on_error(self, e: Exception):
        self.logger.error(f"弹幕连接错误: {e}")

    # ==========================================
    # 定时器：按设定间隔推送弹幕给 AI
    # ==========================================

    @timer_interval(id="push_danmaku", seconds=5, auto_start=True)
    async def push_danmaku_tick(self, **_):
        """
        每5秒检查一次，实际推送频率由 _interval 控制（默认10s）。
        收集缓冲区中的弹幕/SC/礼物，通过 push_message 推送给 AI。
        AI 回复会自动走 TTS 语音播放给直播间观众。
        """
        # 未监听时不推送（防止停止后继续推送旧弹幕）
        is_listening = self._listener is not None and self._listener.is_running()
        if not is_listening and not self._connecting:
            return Ok({"skipped": True, "reason": "not_listening"})

        now = datetime.now().timestamp()

        if now - self._last_push_time < self._interval:
            return Ok({"skipped": True})

        # 收集待推送内容
        # 弹幕较多时，优先推送最新的弹幕，避免旧弹幕积压
        # 保留最新的弹幕（至少保留 5 条），其余的推送给 AI
        keep_count = min(5, len(self._danmaku_queue))
        total_count = len(self._danmaku_queue)
        to_pop = total_count - keep_count  # 要推送的数量

        danmaku_batch = []
        if to_pop > 0:
            # 取最旧的 to_pop 条弹幕推送（从队首取），保留最新的弹幕在队列中
            for _ in range(to_pop):
                danmaku_batch.append(self._danmaku_queue.popleft())
            # 反转顺序（按时间正序，最早的在前）
            danmaku_batch.reverse()
        # 最多保留 10 条
        if len(danmaku_batch) > 10:
            danmaku_batch = danmaku_batch[-10:]

        sc_batch = []
        while self._sc_queue:
            sc_batch.append(self._sc_queue.popleft())

        gift_batch = []
        while self._gift_queue and len(gift_batch) < 5:
            gift_batch.append(self._gift_queue.popleft())

        if not danmaku_batch and not sc_batch and not gift_batch:
            return Ok({"pushed": False, "reason": "no_data"})

        # 更新推送时间
        self._last_push_time = now
        self._total_pushed += len(danmaku_batch) + len(sc_batch) + len(gift_batch)

        # 构建 AI 消息内容 - 使用明确的指令格式，让 AI 知道需要回复
        lines = [
            "======[直播间互动] 你现在正在直播，观众发来了弹幕/礼物/SC，请用语音自然回复他们！======",
            "",
            f"📺 直播间ID: {self._room_id}",
            ""
        ]

        if sc_batch:
            lines.append(f"💰 Super Chat（{len(sc_batch)} 条）- 请感谢并回应：")
            for sc in sc_batch:
                lines.append(f"  ¥{sc['price']} | {sc['user_name']}: {sc['message']}")
            lines.append("")

        if gift_batch:
            lines.append(f"🎁 礼物（{len(gift_batch)} 条）- 请感谢送礼物的人：")
            for g in gift_batch:
                price_str = f"≈¥{g['price_rmb']}" if g['price_rmb'] > 0 else ""
                lines.append(f"  {g['user_name']} 送了 {g['num']}个 {g['gift_name']} {price_str}")
            lines.append("")

        if danmaku_batch:
            lines.append(f"💬 弹幕（{len(danmaku_batch)} 条）- 请回复观众的弹幕内容：")
            for d in danmaku_batch:
                medal = d.get("medal", "")
                level_info = f"LV{d['user_level']}" if d.get("user_level") else ""
                prefix = " ".join(x for x in [medal, level_info] if x)
                prefix_str = f"[{prefix}]" if prefix else ""
                lines.append(f"  {prefix_str}{d['user_name']}: {d['content']}")

        content = "\n".join(lines)
        summary_parts = []
        if sc_batch:
            summary_parts.append(f"SC {len(sc_batch)}条")
        if gift_batch:
            summary_parts.append(f"礼物 {len(gift_batch)}条")
        if danmaku_batch:
            summary_parts.append(f"弹幕 {len(danmaku_batch)}条")
        summary = f"直播间 {self._room_id}: " + ", ".join(summary_parts)

        # 通过 push_message 推送给 AI（不依赖 timer 返回值）
        await self._push_to_ai(content, summary, priority=5)

        return Ok({
            "pushed": True,
            "danmaku": danmaku_batch,
            "superchat": sc_batch,
            "gifts": gift_batch,
        })

    # ==========================================
    # AI 可调用入口
    # ==========================================

    @plugin_entry(
        id="get_danmaku",
        name="获取直播间弹幕",
        description="获取当前直播间最新的弹幕、SC、礼物，返回格式化内容供 AI 理解和回复",
        input_schema={
            "type": "object",
            "properties": {
                "max_count": {
                    "type": "integer",
                    "description": "最多返回的弹幕条数（默认10，最大30）"
                },
                "include_gifts": {
                    "type": "boolean",
                    "description": "是否包含礼物信息（默认true）"
                }
            },
            "required": []
        },
        llm_result_fields=["message"]
    )
    async def get_danmaku(self, max_count: int = 10, include_gifts: bool = True, **_):
        """获取缓冲区中的弹幕，格式化返回给 AI"""
        is_listening = self._listener is not None and self._listener.is_running()

        if not is_listening:
            if self._connecting:
                # 任务已创建，WebSocket 正在握手中
                return Ok({
                    "success": False,
                    "message": f"⏳ 正在连接直播间 {self._room_id}，请稍候几秒后再试...",
                    "room_id": self._room_id,
                    "listening": False,
                    "connecting": True,
                    "logged_in": self._is_logged_in,
                    "interval": self._interval,
                    "queue_size": len(self._ui_danmaku_queue),
                    "stats": {
                        "received": self._total_received,
                        "filtered": self._total_filtered,
                        "pushed": self._total_pushed,
                    },
                })
            else:
                status = "未配置直播间，请先调用 set_room_id" if self._room_id <= 0 else "未在监听"
                return Ok({
                    "success": False,
                    "message": f"⚠️ 直播间 {self._room_id} {status}",
                    "room_id": self._room_id,
                    "listening": False,
                    "connecting": False,
                    "logged_in": self._is_logged_in,
                    "interval": self._interval,
                    "queue_size": len(self._ui_danmaku_queue),
                    "stats": {
                        "received": self._total_received,
                        "filtered": self._total_filtered,
                        "pushed": self._total_pushed,
                    },
                })

        max_count = max(1, min(30, max_count))

        # 取弹幕（消费 UI 专属队列，避免影响 AI 推送队列）
        danmaku_list = []
        while self._ui_danmaku_queue and len(danmaku_list) < max_count:
            danmaku_list.append(self._ui_danmaku_queue.popleft())

        sc_list = []
        while self._ui_sc_queue:
            sc_list.append(self._ui_sc_queue.popleft())

        gift_list = []
        if include_gifts:
            while self._ui_gift_queue and len(gift_list) < 5:
                gift_list.append(self._ui_gift_queue.popleft())

        if not danmaku_list and not sc_list and not gift_list:
            return Ok({
                "success": True,
                "message": f"📭 直播间 {self._room_id} 暂无新弹幕\n（已过滤 {self._total_filtered} 条，已收到 {self._total_received} 条）",
                "room_id": self._room_id,
                "listening": True,
                "logged_in": self._is_logged_in,
                "interval": self._interval,
                "queue_size": len(self._ui_danmaku_queue),
                "stats": {
                    "received": self._total_received,
                    "filtered": self._total_filtered,
                    "pushed": self._total_pushed,
                },
            })

        # 格式化消息
        lines = [f"📺 直播间 {self._room_id} 最新动态", ""]

        if sc_list:
            lines.append(f"💰 Super Chat（{len(sc_list)} 条）：")
            for sc in sc_list:
                lines.append(f"  ¥{sc['price']} | {sc['user_name']}: {sc['message']}")
            lines.append("")

        if gift_list:
            lines.append(f"🎁 礼物（{len(gift_list)} 条）：")
            for g in gift_list:
                price_str = f"≈¥{g['price_rmb']}" if g['price_rmb'] > 0 else ""
                lines.append(f"  {g['user_name']} 送了 {g['num']}个 {g['gift_name']} {price_str}")
            lines.append("")

        if danmaku_list:
            lines.append(f"💬 弹幕（{len(danmaku_list)} 条）：")
            for d in danmaku_list:
                level_info = f"LV{d['user_level']}" if d.get("user_level") else ""
                medal = d.get("medal", "")
                prefix = " ".join(x for x in [medal, level_info] if x)
                prefix_str = f"[{prefix}]" if prefix else ""
                lines.append(f"  {prefix_str}{d['user_name']}: {d['content']}")

        lines.append("")
        lines.append(
            f"📊 统计：共收到 {self._total_received} 条，"
            f"过滤 {self._total_filtered} 条，"
            f"{'已登录' if self._is_logged_in else '游客'}模式"
        )

        message = "\n".join(lines)

        return Ok({
            "success": True,
            "message": message,
            "room_id": self._room_id,
            "listening": True,
            "logged_in": self._is_logged_in,
            "interval": self._interval,
            "queue_size": len(self._ui_danmaku_queue),
            "danmaku_count": len(danmaku_list),
            "sc_count": len(sc_list),
            "gift_count": len(gift_list),
            "danmaku": danmaku_list,
            "superchat": sc_list,
            "gifts": gift_list,
            "stats": {
                "received": self._total_received,
                "filtered": self._total_filtered,
                "pushed": self._total_pushed,
            },
        })

    @plugin_entry(
        id="set_room_id",
        name="更改监听直播间",
        description="切换要监听的 B站直播间，传入直播间号码（数字ID）",
        input_schema={
            "type": "object",
            "properties": {
                "room_id": {
                    "type": "integer",
                    "description": "B站直播间ID（数字），如 1234567"
                }
            },
            "required": ["room_id"]
        },
        llm_result_fields=["message"]
    )
    async def set_room_id(self, room_id: int, **_):
        """更改直播间并重新连接"""
        if not isinstance(room_id, int) or room_id <= 0:
            return Err(SdkError("直播间ID必须是正整数"))

        old_room = self._room_id
        self._room_id = room_id
        self._save_plugin_config()

        # 重新启动监听
        asyncio.create_task(self._start_listening())

        if old_room > 0:
            msg = f"✅ 直播间已从 {old_room} 切换到 {room_id}，正在重新连接..."
        else:
            msg = f"✅ 已设置直播间 {room_id}，正在连接..."

        return Ok({
            "success": True,
            "message": msg,
            "room_id": room_id,
            "old_room_id": old_room,
        })

    @plugin_entry(
        id="set_interval",
        name="更改弹幕推送间隔",
        description=f"设置每次推送弹幕给AI的时间间隔（最小{MIN_INTERVAL}秒，最大{MAX_INTERVAL}秒）",
        input_schema={
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "description": f"间隔秒数，范围 {MIN_INTERVAL}~{MAX_INTERVAL}"
                }
            },
            "required": ["seconds"]
        },
        llm_result_fields=["message"]
    )
    async def set_interval(self, seconds: int, **_):
        """更改推送间隔"""
        if not isinstance(seconds, int):
            return Err(SdkError("间隔必须是整数"))

        if seconds < MIN_INTERVAL or seconds > MAX_INTERVAL:
            return Err(SdkError(
                f"间隔超出范围：请设置 {MIN_INTERVAL}~{MAX_INTERVAL} 秒之间"
            ))

        old_interval = self._interval
        self._interval = seconds
        self._save_plugin_config()

        return Ok({
            "success": True,
            "message": (
                f"✅ 推送间隔已从 {old_interval}s 更改为 {seconds}s\n"
                f"（范围：{MIN_INTERVAL}s ~ {MAX_INTERVAL}s）"
            ),
            "interval": seconds,
            "old_interval": old_interval,
        })

    @plugin_entry(
        id="set_target_lanlan",
        name="设置目标 AI",
        description="设置弹幕推送的目标 AI 名称",
        input_schema={
            "type": "object",
            "properties": {
                "target_lanlan": {
                    "type": "string",
                    "description": "目标 AI 名称（应与 lanlan_name 一致，留空则不指定）",
                },
            },
        },
        llm_result_fields=["message"],
    )
    async def set_target_lanlan(self, target_lanlan: str = "", **_):
        """设置弹幕推送的目标 AI 名称"""
        old_value = self._target_lanlan
        self._target_lanlan = str(target_lanlan).strip()
        self._save_plugin_config()
        return Ok({
            "success": True,
            "message": f"✅ 目标 AI 已从 '{old_value or '(未指定)'}' 更改为 '{self._target_lanlan or '(未指定)'}'",
            "target_lanlan": self._target_lanlan,
            "old_value": old_value,
        })

    @plugin_entry(
        id="set_danmaku_max_length",
        name="设置弹幕最大长度",
        description="设置发送弹幕的最大长度限制",
        input_schema={
            "type": "object",
            "properties": {
                "max_length": {
                    "type": "integer",
                    "description": "弹幕最大长度（范围 1-100，建议 20，B站限制 20 字符/秒）",
                },
            },
        },
        llm_result_fields=["message"],
    )
    async def set_danmaku_max_length(self, max_length: int = 20, **_):
        """设置弹幕最大长度限制"""
        try:
            max_length = int(max_length)
        except (TypeError, ValueError):
            return Err(SdkError("max_length 必须是整数"))

        if max_length < 1 or max_length > 100:
            return Err(SdkError("max_length 超出范围：请设置 1~100 之间"))

        old_value = self._danmaku_max_length
        self._danmaku_max_length = max_length
        self._save_plugin_config()

        # 更新监听器的弹幕长度限制（如果监听器已创建）
        if self._listener:
            self._listener._danmaku_max_length = max_length

        return Ok({
            "success": True,
            "message": f"✅ 弹幕最大长度已从 {old_value} 更改为 {max_length}",
            "max_length": max_length,
            "old_value": old_value,
        })

    @plugin_entry(
        id="connect",
        name="开始监听",
        description="立即开始（或重启）弹幕监听，可选传入直播间ID",
        input_schema={
            "type": "object",
            "properties": {
                "room_id": {
                    "type": "integer",
                    "description": "直播间ID（可选，不传则使用当前配置）"
                }
            },
            "required": []
        },
        llm_result_fields=["message"]
    )
    async def connect(self, room_id: int = 0, **_):
        """开始或重启弹幕监听"""
        if room_id and room_id > 0:
            self._room_id = room_id
            self._save_plugin_config()

        if self._room_id <= 0:
            return Err(SdkError("未配置直播间ID，请先传入 room_id"))

        asyncio.create_task(self._start_listening())
        return Ok({
            "success": True,
            "message": f"✅ 正在连接直播间 {self._room_id}，稍后弹幕将开始接收",
            "room_id": self._room_id,
        })

    @plugin_entry(
        id="disconnect",
        name="停止监听",
        description="停止当前弹幕监听连接",
        llm_result_fields=["message"]
    )
    async def disconnect(self, **_):
        """停止弹幕监听"""
        was_listening = self._listener is not None and (
            self._listener.is_running() or self._connecting
        )
        await self._stop_listening()
        return Ok({
            "success": True,
            "message": f"✅ 已停止监听直播间 {self._room_id}" if was_listening else "ℹ️ 当前未在监听",
            "room_id": self._room_id,
        })

    @plugin_entry(
        id="get_status",
        name="获取插件状态",
        description="获取弹幕插件当前状态，包括直播间、监听状态、过滤设置等",
        llm_result_fields=["message"]
    )
    async def get_status(self, **_):
        """获取插件运行状态"""
        is_listening = self._listener is not None and self._listener.is_running()
        if self._connecting and not is_listening:
            listen_status = "🟡 连接中..."
        elif is_listening:
            listen_status = "🟢 监听中"
        else:
            listen_status = "🔴 未监听"

        lines = [
            "📡 B站弹幕插件状态",
            "",
            f"直播间: {self._room_id if self._room_id > 0 else '未配置'}",
            f"监听状态: {listen_status}",
            f"账号状态: {'🔐 已登录' if self._is_logged_in else '👤 游客模式'}",
            f"过滤模式: {self._filter.describe_mode() if self._filter else '未初始化'}",
            f"推送间隔: {self._interval}s",
            f"目标AI: {self._target_lanlan or '(未指定)'}",
            f"弹幕最大长度: {self._danmaku_max_length} 字符",
            "",
            f"弹幕缓冲: {len(self._danmaku_queue)} 条",
            f"SC缓冲: {len(self._sc_queue)} 条",
            f"礼物缓冲: {len(self._gift_queue)} 条",
            "",
            f"总收到: {self._total_received} 条",
            f"已过滤: {self._total_filtered} 条",
            f"已推送: {self._total_pushed} 条",
        ]

        return Ok({
            "success": True,
            "message": "\n".join(lines),
            "room_id": self._room_id,
            "listening": is_listening,
            "logged_in": self._is_logged_in,
            "interval": self._interval,
            "target_lanlan": self._target_lanlan,
            "danmaku_max_length": self._danmaku_max_length,
            "queue_size": len(self._danmaku_queue),
            "stats": {
                "received": self._total_received,
                "filtered": self._total_filtered,
                "pushed": self._total_pushed,
            }
        })

    @plugin_entry(
        id="save_credential",
        name="保存B站登录凭据",
        description="将用户提供的 B站 Cookie 字段加密保存到插件本地，重启后生效",
        input_schema={
            "type": "object",
            "properties": {
                "sessdata":    {"type": "string", "description": "SESSDATA Cookie 值"},
                "bili_jct":    {"type": "string", "description": "bili_jct Cookie 值"},
                "dedeuserid":  {"type": "string", "description": "DedeUserID Cookie 值"},
                "buvid3":      {"type": "string", "description": "buvid3 Cookie 值（可选但强烈建议填写）"},
            },
            "required": ["sessdata", "bili_jct", "dedeuserid"]
        },
        llm_result_fields=["message"]
    )
    async def save_credential(
        self,
        sessdata: str = "",
        bili_jct: str = "",
        dedeuserid: str = "",
        buvid3: str = "",
        **_
    ):
        """加密保存 B站凭据并立即生效（无需重启）"""
        sessdata   = str(sessdata or "").strip()
        bili_jct   = str(bili_jct or "").strip()
        dedeuserid = str(dedeuserid or "").strip()
        buvid3     = str(buvid3 or "").strip()

        if not sessdata:
            return Err(SdkError("SESSDATA 不能为空"))
        if not bili_jct:
            return Err(SdkError("bili_jct 不能为空"))
        if not dedeuserid:
            return Err(SdkError("DedeUserID 不能为空"))

        cred_dict = {
            "SESSDATA":   sessdata,
            "bili_jct":   bili_jct,
            "DedeUserID": dedeuserid,
            "buvid3":     buvid3,
        }

        # data_path() 即 data 目录（config_dir/data/）
        data_dir = self.data_path()
        ok = _save_credential_encrypted(data_dir, cred_dict)
        if not ok:
            return Err(SdkError("加密保存失败，请检查 cryptography 库是否可用"))

        # 立即热更新内存中的凭据
        self._bilibili_credential = _BiliCredential(
            sessdata=sessdata,
            bili_jct=bili_jct,
            buvid3=buvid3,
            dedeuserid=dedeuserid,
        )
        self._is_logged_in = True
        self.logger.info(f"✅ B站凭据已加密保存 (UID={dedeuserid})")

        # 如果当前在监听，重启以使新凭据生效
        if self._room_id > 0:
            asyncio.create_task(self._start_listening())
            restart_msg = "，已重启弹幕监听以应用新凭据"
        else:
            restart_msg = ""

        return Ok({
            "success": True,
            "message": f"✅ B站凭据已加密保存{restart_msg}\nUID: {dedeuserid}\n{'已包含 buvid3' if buvid3 else '⚠️ 未填写 buvid3，可能影响连接稳定性'}",
            "uid": dedeuserid,
            "has_buvid3": bool(buvid3),
        })

    @plugin_entry(
        id="clear_credential",
        name="清除B站登录凭据",
        description="删除插件本地保存的 B站 Cookie，切换回游客模式",
        llm_result_fields=["message"]
    )
    async def clear_credential(self, **_):
        """清除插件本地加密凭据，切换回游客模式"""
        data_dir = self.data_path()
        _delete_credential_files(data_dir)

        self._bilibili_credential = None
        self._is_logged_in = False
        self.logger.info("🗑️ 已清除插件本地 B站凭据，切换为游客模式")

        return Ok({
            "success": True,
            "message": "✅ 已清除 B站凭据，切换为游客模式\n如需重新登录，请在控制台输入 Cookie 字段并保存。",
        })

    @plugin_entry(
        id="reload_credential",
        name="重新加载凭据",
        description="重新从本地文件/NEKO全局读取 B站凭据，无需重启插件",
        llm_result_fields=["message"]
    )
    async def reload_credential(self, **_):
        """热重载凭据（不重启监听）"""
        await self._load_bilibili_credential()
        self._init_filter()
        status = "🔐 已登录" if self._is_logged_in else "👤 游客模式"
        return Ok({
            "success": True,
            "message": f"✅ 凭据已重新加载\n当前状态: {status}",
            "logged_in": self._is_logged_in,
        })

    @plugin_entry(
        id="send_danmaku",
        name="发送弹幕到直播间",
        description="向当前监听的 B站直播间发送弹幕消息，用于回复弹幕、感谢礼物等互动。需要已登录 B站账号。",
        input_schema={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "要发送的弹幕内容（建议 20 字符以内，B站限制 20 字符/秒）"
                }
            },
            "required": ["message"]
        },
        llm_result_fields=["message"]
    )
    async def send_danmaku(self, message: str, **_):
        """
        发送弹幕到当前监听的 B站直播间。
        需要已登录（有 bili_jct 凭据）。
        """
        if not self._is_logged_in or not self._bilibili_credential:
            return Err(SdkError("未登录 B站 账号，无法发送弹幕。请先使用 save_credential 保存凭据。"))

        if not self._listener or not self._listener.is_running():
            return Err(SdkError("当前未在监听直播间，无法发送弹幕。请先连接直播间。"))

        result = await self._listener.send_danmaku(
            message=message,
            room_id=self._room_id,
            credential=self._bilibili_credential,
            danmaku_max_length=self._danmaku_max_length,
        )

        if result.get("success"):
            return Ok({
                "success": True,
                "message": result.get("message", "✅ 弹幕已发送"),
            })
        else:
            return Err(SdkError(result.get("message", "弹幕发送失败")))
