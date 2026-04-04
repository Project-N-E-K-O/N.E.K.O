"""
Bilibili 弹幕监听核心模块（纯 WebSocket 实现）

不依赖 bilibili_api，直接使用 websockets 库实现 B站弹幕协议，
规避 NEKO 内置 bilibili_api 版本不兼容问题。

协议参考：
  - 连接地址：wss://broadcastlv.chat.bilibili.com/sub
  - 数据包格式：header(16字节) + body
  - 心跳包：30秒一次，维持连接
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import struct
import time
import zlib
import random
from datetime import datetime
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlencode

# ── 取消检查辅助 ──────────────────────────────────────────────────
async def _check_cancelled(stop_event: asyncio.Event):
    """await 此函数可在协程中任意 await 点检查是否应停止"""
    await asyncio.sleep(0)  # 让出控制权，确保事件状态可被读取

# ── WBI 签名常量 ──────────────────────────────────────────────────
# 重排映射表（固定不变）
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

def _get_mixin_key(img_key: str, sub_key: str) -> str:
    """将 img_key + sub_key 按 MIXIN_KEY_ENC_TAB 重排后取前32位"""
    # 两个 key 必须都非空且长度为 32（MD5 hex），否则无法正确重排
    if not img_key or not sub_key or len(img_key) != 32 or len(sub_key) != 32:
        return ""
    raw = img_key + sub_key  # 总长 64
    return "".join(raw[i] for i in _MIXIN_KEY_ENC_TAB if i < len(raw))[:32]


def _wbi_sign(params: dict, mixin_key: str) -> dict:
    """
    对参数字典添加 wts，按键名升序 URL 编码后拼 mixin_key 求 MD5，
    返回追加了 w_rid 和 wts 的新参数字典。
    """
    wts = int(time.time())
    params = dict(params)
    params["wts"] = wts
    # 键名升序，过滤掉值中的 !'()*
    filtered = {
        k: "".join(c for c in str(v) if c not in "!'()*")
        for k, v in sorted(params.items())
    }
    query = urlencode(filtered)
    w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
    params["w_rid"] = w_rid
    return params


# ── WebSocket 弹幕服务器 ──────────────────────────────────────────
WS_URL = "wss://broadcastlv.chat.bilibili.com/sub"
# 备用地址
WS_FALLBACK_URLS = [
    "wss://tx-gz-live-comet-01.chat.bilibili.com/sub",
    "wss://broadcastlv.chat.bilibili.com/sub",
]

# ── 数据包协议常量 ────────────────────────────────────────────────
HEADER_LEN = 16
PROTOCOL_VERSION_JSON       = 0   # JSON
PROTOCOL_VERSION_HEARTBEAT  = 1   # 心跳/认证
PROTOCOL_VERSION_ZLIB       = 2   # zlib 压缩
PROTOCOL_VERSION_BROTLI     = 3   # brotli 压缩

OPERATION_HEARTBEAT         = 2   # 心跳
OPERATION_HEARTBEAT_REPLY   = 3   # 心跳回包（人气值）
OPERATION_SEND_MSG          = 5   # 普通消息
OPERATION_AUTH              = 7   # 认证
OPERATION_AUTH_REPLY        = 8   # 认证回包


def _pack(operation: int, body: bytes, proto_ver: int = PROTOCOL_VERSION_HEARTBEAT) -> bytes:
    """打包数据包"""
    total = HEADER_LEN + len(body)
    return struct.pack(">IHHII", total, HEADER_LEN, proto_ver, operation, 1) + body


def _unpack_header(data: bytes):
    """解包头部，返回 (total_len, header_len, proto_ver, operation, seq)"""
    return struct.unpack(">IHHII", data[:HEADER_LEN])


# 模块级日志回调（由 DanmakuListener 实例设置，供 _decompress 使用）
_module_logger = None

def _decompress(data: bytes, proto_ver: int) -> bytes:
    """解压数据"""
    if proto_ver == PROTOCOL_VERSION_ZLIB:
        return zlib.decompress(data)
    if proto_ver == PROTOCOL_VERSION_BROTLI:
        try:
            import brotli
            return brotli.decompress(data)
        except ImportError:
            if _module_logger:
                _module_logger("brotli 库未安装，无法解压 brotli 数据包，跳过", "warning")
            return b""  # 返回空字节，上层 _split_packets 会返回空列表
    return data


def _split_packets(data: bytes) -> list[bytes]:
    """拆分多个数据包（zlib/brotli 解压后可能包含多个包）"""
    packets = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < HEADER_LEN:
            break
        total_len = struct.unpack(">I", data[offset:offset + 4])[0]
        if total_len < HEADER_LEN or offset + total_len > len(data):
            break
        packets.append(data[offset:offset + total_len])
        offset += total_len
    return packets


class DanmakuListener:
    """
    B站直播弹幕异步监听器（纯 WebSocket 实现，无 bilibili_api 依赖）

    事件回调：
    - on_danmaku(data): 普通弹幕
    - on_gift(data): 礼物
    - on_sc(data): 超级留言
    - on_entry(user_name): 进入直播间
    - on_follow(user_name): 关注主播
    - on_live(): 开播
    - on_preparing(): 下播
    - on_error(e): 连接错误
    """

    def __init__(
        self,
        room_id: int,
        credential=None,
        logger=None,
        callbacks: Dict[str, Callable] = None,
        danmaku_max_length: int = 20,  # 弹幕最大长度限制（B站限制 20 字符）
    ):
        self.room_id = room_id
        self.real_room_id: int = room_id  # 连接后更新为真实房间号（处理短号）
        self.credential = credential
        self.logger = logger
        self.callbacks = callbacks or {}
        self.running = False
        self._stop_event = asyncio.Event()  # 用于在 await 点可靠取消连接
        self._ws = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._buvid3_temp: str = ""  # 临时 buvid3，无凭据时从 B站首页获取
        self._danmaku_max_length = max(1, min(100, danmaku_max_length))  # 限制范围 1-100

        # 设置模块级日志回调（供 _decompress 使用）
        global _module_logger
        if logger:
            _module_logger = lambda msg, level="info": getattr(logger, level, logger.info)(msg)

        # WBI key 缓存（每日更替，缓存12小时足够）
        self._wbi_mixin_key: str = ""
        self._wbi_key_ts: float = 0.0   # 上次获取时间（unix 秒）
        self._wbi_key_ttl: float = 43200  # 12小时

    def _log(self, msg: str, level: str = "info"):
        if self.logger:
            getattr(self.logger, level, self.logger.info)(msg)

    def _emit(self, event: str, *args, **kwargs):
        cb = self.callbacks.get(event)
        if cb:
            try:
                cb(*args, **kwargs)
            except Exception as e:
                self._log(f"回调 {event} 异常: {e}", "warning")

    async def _get_wbi_mixin_key(self, cookies: dict) -> str:
        """
        获取 WBI mixin_key（带12小时缓存）。
        从 https://api.bilibili.com/x/web-interface/nav 接口的
        wbi_img.img_url / sub_url 中提取 img_key / sub_key。
        """
        now = time.time()
        if self._wbi_mixin_key and (now - self._wbi_key_ts) < self._wbi_key_ttl:
            return self._wbi_mixin_key

        try:
            import aiohttp
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.bilibili.com/",
            }
            async with aiohttp.ClientSession(cookies=cookies) as session:
                async with session.get(
                    "https://api.bilibili.com/x/web-interface/nav",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    data = await resp.json()
                    wbi_img = data.get("data", {}).get("wbi_img", {})
                    img_url = wbi_img.get("img_url", "")
                    sub_url = wbi_img.get("sub_url", "")
                    # 从 URL 中取文件名（去掉扩展名）
                    img_key = img_url.rsplit("/", 1)[-1].split(".")[0] if img_url else ""
                    sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0] if sub_url else ""
                    if img_key and sub_key:
                        mixin_key = _get_mixin_key(img_key, sub_key)
                        if mixin_key:
                            self._wbi_mixin_key = mixin_key
                            self._wbi_key_ts = now
                            self._log(f"WBI key 已更新 (img={img_key[:8]}...)")
                            return mixin_key
                        else:
                            self._log(f"WBI key 重排失败: img_key 长度={len(img_key)}, sub_key 长度={len(sub_key)}", "warning")
                    else:
                        self._log(f"WBI key 缺失: img_key={'有' if img_key else '无'}, sub_key={'有' if sub_key else '无'}", "warning")
        except Exception as e:
            self._log(f"获取 WBI key 失败: {e}", "warning")
        return ""

    async def _get_real_room_id(self, room_id: int) -> int:
        """获取真实房间号（处理短号）"""
        try:
            import aiohttp
            url = f"https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom?room_id={room_id}"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    data = await resp.json()
                    if data.get("code") == 0:
                        real_id = data["data"]["room_info"]["room_id"]
                        self._log(f"房间号解析: {room_id} -> {real_id}")
                        return real_id
        except Exception as e:
            self._log(f"获取真实房间号失败: {e}，使用原始号", "warning")
        return room_id

    async def _fetch_buvid3(self) -> str:
        """访问 B站首页获取临时 buvid3（用于绕过 -352 风控）"""
        try:
            import aiohttp
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://www.bilibili.com/",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=8),
                    allow_redirects=True,
                ) as resp:
                    # 从 Set-Cookie 中提取 buvid3
                    buvid3 = resp.cookies.get("buvid3")
                    if buvid3:
                        val = buvid3.value if hasattr(buvid3, "value") else str(buvid3)
                        self._log(f"已获取临时 buvid3 (长度={len(val)})")
                        return val
                    # 备用：从响应头 raw Set-Cookie 里找
                    for raw in resp.headers.getall("Set-Cookie", []):
                        if "buvid3=" in raw:
                            for part in raw.split(";"):
                                part = part.strip()
                                if part.startswith("buvid3="):
                                    val = part[len("buvid3="):]
                                    self._log(f"已获取临时 buvid3 (备用, 长度={len(val)})")
                                    return val
        except Exception as e:
            self._log(f"获取临时 buvid3 失败: {e}", "warning")
        return ""

    async def _get_danmaku_server_info(self, real_room_id: int) -> tuple[str, str]:
        """获取弹幕服务器地址和 token（带 WBI 签名）"""
        try:
            import aiohttp

            # 从凭据中取 buvid3
            buvid3 = ""
            if self.credential:
                try:
                    buvid3 = getattr(self.credential, "buvid3", "") or ""
                except Exception:
                    pass

            # buvid3 为空时自动获取临时值，避免 -352 风控
            if not buvid3:
                self._log("buvid3 为空，尝试获取临时 buvid3...")
                buvid3 = await self._fetch_buvid3()
                # 把获取到的 buvid3 回写到 credential，供认证包使用
                if buvid3 and self.credential:
                    try:
                        self.credential.buvid3 = buvid3
                    except Exception:
                        pass
                # 即使没有 credential 也记下来
                self._buvid3_temp = buvid3

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": f"https://live.bilibili.com/{real_room_id}",
            }

            # 构建 Cookie
            cookies = {"buvid3": buvid3} if buvid3 else {}
            if self.credential:
                try:
                    cookies.update({
                        "SESSDATA": getattr(self.credential, "sessdata", "") or "",
                        "bili_jct": getattr(self.credential, "bili_jct", "") or "",
                        "DedeUserID": getattr(self.credential, "dedeuserid", "") or "",
                    })
                    # 过滤空值
                    cookies = {k: v for k, v in cookies.items() if v}
                except Exception:
                    pass

            # ── WBI 签名 ────────────────────────────────────────────
            params = {"id": real_room_id, "type": 0}
            mixin_key = await self._get_wbi_mixin_key(cookies)
            if mixin_key:
                params = _wbi_sign(params, mixin_key)
                self._log(f"WBI 签名已添加 (w_rid={params.get('w_rid', '')[:8]}...)")
            else:
                self._log("WBI key 获取失败，尝试不带签名请求", "warning")

            url = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo"

            async with aiohttp.ClientSession(cookies=cookies) as session:
                async with session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    data = await resp.json()
                    api_code = data.get("code", -1)
                    self._log(f"getDanmuInfo API: code={api_code}, msg={data.get('message', '')}")
                    if api_code == 0:
                        token = data["data"].get("token", "")
                        hosts = data["data"].get("host_list", [])
                        self._log(f"token长度={len(token)}, 可用服务器数={len(hosts)}")
                        if hosts:
                            host = hosts[0]
                            ws_url = f"wss://{host['host']}:{host['wss_port']}/sub"
                            self._log(f"弹幕服务器: {ws_url}")
                            return ws_url, token
                    else:
                        self._log(f"getDanmuInfo 返回错误: {data}", "warning")
        except Exception as e:
            self._log(f"获取弹幕服务器信息失败: {e}，使用默认地址", "warning")
        return WS_URL, ""

    def _build_auth_body(self, real_room_id: int, token: str) -> bytes:
        """构建认证包 body"""
        uid = 0
        buvid3 = ""
        if self.credential:
            try:
                uid = int(getattr(self.credential, "dedeuserid", 0) or 0)
            except Exception:
                uid = 0
            try:
                buvid3 = getattr(self.credential, "buvid3", "") or ""
            except Exception:
                buvid3 = ""

        # credential 里没有 buvid3 时，用临时获取的
        if not buvid3:
            buvid3 = self._buvid3_temp

        body = {
            "uid": uid,
            "roomid": real_room_id,
            "protover": 2,  # zlib 压缩，兼容性最好
            "platform": "web",
            "type": 2,
            "key": token,
        }
        # buvid3 有值时加入认证包（B站新版要求）
        if buvid3:
            body["buvid"] = buvid3

        self._log(
            f"认证包信息: uid={uid}, room={real_room_id}, "
            f"buvid={'有' if buvid3 else '⚠️无'}, "
            f"token={'有(' + str(len(token)) + '字节)' if token else '⚠️无(空token)'}"
        )
        return json.dumps(body, separators=(",", ":")).encode("utf-8")

    async def _heartbeat_loop(self):
        """每 30 秒发一次心跳，可被 stop() 中断"""
        try:
            while self.running and self._ws:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=30)
                    # _stop_event 被 set，停止心跳
                    break
                except asyncio.TimeoutError:
                    # 30秒超时正常，发送心跳
                    pass
                if self.running and self._ws:
                    try:
                        await self._ws.send(_pack(OPERATION_HEARTBEAT, b"[object Object]"))
                    except Exception:
                        break
        except asyncio.CancelledError:
            pass

    def _dispatch_message(self, cmd: str, data: dict):
        """根据 cmd 分发事件"""
        try:
            if cmd == "DANMU_MSG":
                info = data.get("info", [])
                if not isinstance(info, list) or len(info) < 2:
                    return
                content = str(info[1]) if info[1] is not None else ""

                user_info = info[2] if len(info) > 2 else []
                if isinstance(user_info, list):
                    user_id = user_info[0] if len(user_info) > 0 else 0
                    user_name = str(user_info[1]) if len(user_info) > 1 else "未知"
                else:
                    user_id, user_name = 0, "未知"

                user_level = 0
                if len(info) > 4 and isinstance(info[4], list) and len(info[4]) > 0:
                    try:
                        user_level = int(info[4][0])
                    except (ValueError, TypeError):
                        user_level = 0

                try:
                    ts = info[0][4] / 1000 if isinstance(info[0], list) and len(info[0]) > 4 else None
                    time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else datetime.now().strftime("%H:%M:%S")
                except Exception:
                    time_str = datetime.now().strftime("%H:%M:%S")

                medal_text = ""
                if len(info) > 3 and isinstance(info[3], list) and len(info[3]) >= 2:
                    try:
                        medal_level = int(info[3][0])
                        medal_name = str(info[3][1])
                        medal_text = f"[{medal_name}{medal_level}]"
                    except Exception:
                        pass

                self._emit("on_danmaku", {
                    "time": time_str,
                    "content": content,
                    "user_id": user_id,
                    "user_name": user_name,
                    "user_level": user_level,
                    "medal_text": medal_text,
                    "medal_level": 0,
                    "medal_name": "",
                })

            elif cmd == "SEND_GIFT":
                inner = data.get("data", {})
                self._emit("on_gift", {
                    "user_name": inner.get("uname", "未知"),
                    "user_id": inner.get("uid", 0),
                    "gift_name": inner.get("giftName", "未知礼物"),
                    "num": inner.get("num", 1),
                    "coin_type": inner.get("coin_type", "silver"),
                    "total_coin": inner.get("total_coin", 0),  # 总金瓜子数（所有礼物的价值总和）
                    "price": inner.get("price", 0),            # 单价（金瓜子）
                })

            elif cmd == "SUPER_CHAT_MESSAGE":
                inner = data.get("data", {})
                user_info = inner.get("user_info", {})
                self._emit("on_sc", {
                    "user_name": user_info.get("uname", "未知"),
                    "user_id": inner.get("uid", 0),
                    "message": inner.get("message", ""),
                    "price": inner.get("price", 0),
                    "start_time": inner.get("start_time", 0),
                })

            elif cmd == "INTERACT_WORD":
                inner = data.get("data", {})
                user_name = inner.get("uname", "未知")
                msg_type = inner.get("msg_type", 0)
                if msg_type == 1:
                    self._emit("on_entry", user_name)
                elif msg_type == 2:
                    self._emit("on_follow", user_name)

            elif cmd == "LIVE":
                self._emit("on_live")

            elif cmd == "PREPARING":
                self._emit("on_preparing")

        except Exception as e:
            self._log(f"分发消息 {cmd} 异常: {e}", "debug")

    def _process_packet(self, raw: bytes):
        """处理单个数据包"""
        if len(raw) < HEADER_LEN:
            return
        total_len, header_len, proto_ver, operation, seq = _unpack_header(raw)
        body = raw[header_len:total_len]

        if operation == OPERATION_HEARTBEAT_REPLY:
            # 人气值，忽略
            pass

        elif operation == OPERATION_AUTH_REPLY:
            try:
                result = json.loads(body.decode("utf-8"))
                code = result.get("code", -1)
                if code == 0:
                    self._log("✅ 认证成功，开始接收弹幕")
                else:
                    self._log(f"❌ 认证失败: code={code} msg={result}", "warning")
                    # 认证失败，停止监听
                    self.running = False
                    self._stop_event.set()
            except Exception as ex:
                self._log(f"解析认证回包异常: {ex}", "debug")

        elif operation == OPERATION_SEND_MSG:
            if proto_ver in (PROTOCOL_VERSION_ZLIB, PROTOCOL_VERSION_BROTLI):
                # 解压后递归处理
                try:
                    decompressed = _decompress(body, proto_ver)
                    for pkt in _split_packets(decompressed):
                        self._process_packet(pkt)
                except Exception as e:
                    self._log(f"解压失败: {e}", "warning")
            else:
                # 直接解析 JSON
                try:
                    msg = json.loads(body.decode("utf-8"))
                    cmd = msg.get("cmd", "")
                    # 有些 cmd 带 : 后缀，取前部分
                    cmd = cmd.split(":")[0]
                    if cmd == "DANMU_MSG":
                        self._log(f"📨 收到弹幕包 cmd=DANMU_MSG")
                    self._dispatch_message(cmd, msg)
                except Exception as e:
                    self._log(f"解析消息失败: {e}", "warning")

    async def start(self):
        """启动监听（带自动重连，直到 stop() 被调用）"""
        import websockets

        # 重置停止事件
        self._stop_event.clear()
        self.running = True

        retry_count = 0
        max_retries = 10
        retry_delay = 5  # 初始重试间隔（秒）

        while True:
            if self._stop_event.is_set():
                break
            try:
                await self._connect_once()
            except Exception as e:
                self._log(f"连接过程异常: {e}", "error")
                self._emit("on_error", e)

            if self._stop_event.is_set():
                break

            # 自动重连
            retry_count += 1
            if retry_count > max_retries:
                self._log(f"重连次数超过 {max_retries} 次，停止重连", "error")
                break

            wait = min(retry_delay * retry_count, 60)
            # 前3次打印重连日志，之后静默
            if retry_count <= 3:
                self._log(f"🔄 {wait}s 后自动重连 (第{retry_count}次)...")
            elif retry_count == 4:
                self._log(f"🔄 持续重连中，后续重连不再打印日志（共最多{max_retries}次）")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=wait)
                # _stop_event 被 set，退出重连循环
                break
            except asyncio.TimeoutError:
                # 超时正常，继续重连
                pass

        self.running = False
        self._log("弹幕监听已停止")

    async def _connect_once(self):
        """单次 WebSocket 连接（内部）"""
        import websockets

        # 连接前检查是否已被 stop
        if self._stop_event.is_set():
            return

        # 1. 获取真实房间号
        real_room_id = await self._get_real_room_id(self.room_id)
        if self._stop_event.is_set():
            return
        # 保存真实房间号供 send_danmaku 等接口使用
        self.real_room_id = real_room_id

        # 2. 获取服务器和 token
        ws_url, token = await self._get_danmaku_server_info(real_room_id)
        if self._stop_event.is_set():
            return

        # 3. 构建认证包
        auth_body = self._build_auth_body(real_room_id, token)

        self.running = True
        self._log(f"正在连接弹幕服务器: {ws_url}")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Origin": "https://live.bilibili.com",
        }

        # websockets 新版用 additional_headers，旧版用 extra_headers，做兼容
        try:
            import inspect
            _ws_ver = getattr(websockets, "__version__", "unknown")
            _ws_connect_sig = inspect.signature(websockets.connect)
            if "additional_headers" in _ws_connect_sig.parameters:
                _ws_kwargs = {"additional_headers": headers}
            else:
                _ws_kwargs = {"extra_headers": headers}
            self._log(f"websockets 版本={_ws_ver}, 使用参数={list(_ws_kwargs.keys())[0]}")
        except Exception:
            _ws_kwargs = {"extra_headers": headers}

        try:
            async with websockets.connect(ws_url, ping_interval=None, **_ws_kwargs) as ws:
                self._ws = ws

                # 发送认证包
                await ws.send(_pack(OPERATION_AUTH, auth_body))
                self._log("认证包已发送，等待服务器回复...")

                # 启动心跳
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                self._log("弹幕连接已建立，开始接收消息")

                async for message in ws:
                    if self._stop_event.is_set():
                        break
                    try:
                        if isinstance(message, bytes):
                            self._process_packet(message)
                        # str 消息忽略
                    except Exception as e:
                        self._log(f"处理消息异常: {e}", "debug")

        except Exception as e:
            err_str = str(e)
            # "no close frame" 是服务器直接断 TCP 的正常情况，降级为 warning
            if "no close frame" in err_str or "connection closed" in err_str.lower():
                self._log(f"连接已断开: {err_str}", "warning")
            else:
                self._log(f"连接异常: {err_str}", "error")
                raise
        finally:
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
            self._ws = None
            self._log("弹幕连接已断开")

    async def send_danmaku(
        self,
        message: str,
        room_id: int,
        credential=None,
        danmaku_max_length: int = 20,
    ) -> dict:
        """
        发送弹幕到 B站直播间。

        Args:
            message: 弹幕文本
            room_id: 直播间真实房间号
            credential: _BiliCredential 实例（需要 bili_jct + SESSDATA）
            danmaku_max_length: 弹幕最大长度限制（默认 20，B站限制 20 字符/秒）

        Returns:
            dict: {"success": bool, "message": str}
        """
        if not credential:
            return {"success": False, "message": "未登录，无法发送弹幕"}

        bili_jct = getattr(credential, "bili_jct", "")
        sessdata = getattr(credential, "sessdata", "")
        dedeuserid = getattr(credential, "dedeuserid", "")

        if not bili_jct:
            return {"success": False, "message": "缺少 bili_jct，无法发送弹幕"}

        message = str(message).strip()
        if not message:
            return {"success": False, "message": "弹幕内容不能为空"}
        max_len = danmaku_max_length or self._danmaku_max_length or 20
        if len(message) > max_len:
            message = message[:max_len]

        try:
            import aiohttp

            url = "https://api.live.bilibili.com/msg/send"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": f"https://live.bilibili.com/{room_id}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            cookies = {
                "SESSDATA": sessdata,
                "bili_jct": bili_jct,
                "DedeUserID": dedeuserid,
            }
            # 过滤空值
            cookies = {k: v for k, v in cookies.items() if v}

            payload = {
                "bubble": "0",
                "msg": message,
                "color": "16777215",
                "mode": "1",
                "fontsize": "25",
                "rnd": int(time.time() * 1000000),
                "roomid": room_id,
                "csrf": bili_jct,
                "csrf_token": bili_jct,
            }

            async with aiohttp.ClientSession(cookies=cookies) as session:
                async with session.post(
                    url,
                    data=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    data = await resp.json()
                    code = data.get("code", -1)
                    if code == 0:
                        self._log(f"✅ 弹幕发送成功: {message[:30]}...")
                        return {"success": True, "message": f"弹幕发送成功: {message}"}
                    else:
                        msg = data.get("message", "未知错误")
                        self._log(f"❌ 弹幕发送失败: code={code} msg={msg}", "warning")
                        return {"success": False, "message": f"发送失败: {msg} (code={code})"}
        except Exception as e:
            self._log(f"❌ 弹幕发送异常: {e}", "error")
            return {"success": False, "message": f"发送异常: {e}"}

    async def stop(self):
        """断开连接（可在任意时刻安全调用，包括连接建立过程中）"""
        self.running = False
        self._stop_event.set()  # 唤醒所有等待此事件的协程
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None

    def is_running(self) -> bool:
        return self.running
