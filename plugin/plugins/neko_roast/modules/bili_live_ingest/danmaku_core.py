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
from datetime import datetime
from typing import Callable, Dict, Optional
from urllib.parse import urlencode

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


# ── 连接状态枚举 ─────────────────────────────────────────────────
class ConnectionState:
    DISCONNECTED = "disconnected"      # 未连接
    CONNECTING = "connecting"          # 连接中
    AUTHENTICATING = "authenticating"   # 认证中
    RECEIVING = "receiving"             # 接收中（认证成功后进入）
    RECONNECTING = "reconnecting"       # 重连中


# ── WebSocket 弹幕服务器 ──────────────────────────────────────────
# 最可靠的服务器（始终作为最终保底）
WS_MAIN_URL = "wss://broadcastlv.chat.bilibili.com/sub"
# 备用地址（按可靠性排序）
WS_FALLBACK_URLS = [
    "wss://tx-gz-live-comet-01.chat.bilibili.com/sub",
    "wss://live-comet-01.chat.bilibili.com/sub",
    "wss://live-comet-02.chat.bilibili.com/sub",
    "wss://broadcastlv.chat.bilibili.com/sub",  # 最终保底
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


def _decompress(data: bytes, proto_ver: int, log: Callable[[str, str], None] | None = None) -> bytes:
    """解压数据"""
    if proto_ver == PROTOCOL_VERSION_ZLIB:
        return zlib.decompress(data)
    if proto_ver == PROTOCOL_VERSION_BROTLI:
        try:
            import brotli
            return brotli.decompress(data)
        except ImportError:
            if log:
                log("brotli 库未安装，无法解压 brotli 数据包，跳过", "warning")
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
        self._ready_event = asyncio.Event()
        self._authenticated_in_attempt = False
        self._buvid3_temp: str = ""  # 临时 buvid3，无凭据时从 B站首页获取

        # 连接状态
        self._connection_state = ConnectionState.DISCONNECTED

        # 直播结束标记：收到 PREPARING 后置位，阻止重连循环
        self._live_ended: bool = False
        self._current_server: str = ""  # 当前连接的服务器地址
        self._viewer_count: int = 0  # 当前观看人数（人气值）

        # WBI key 缓存（每日更替，缓存12小时足够）
        self._wbi_mixin_key: str = ""
        self._wbi_key_ts: float = 0.0   # 上次获取时间（unix 秒）
        self._wbi_key_ttl: float = 43200  # 12小时
        self._real_room_id_cache: dict[int, tuple[int, float]] = {}
        self._real_room_id_ttl: float = 300
        self._http_timeout = 8

    def _log(self, msg: str, level: str = "info"):
        if self.logger:
            getattr(self.logger, level, self.logger.info)(msg)

    async def _emit(self, event: str, *args, **kwargs):
        cb = self.callbacks.get(event)
        self._log(f"_emit: event={event}, cb={'有' if cb else '无'}, callbacks_keys={list(self.callbacks.keys())}", "debug")
        if cb:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(*args, **kwargs)
                else:
                    cb(*args, **kwargs)
            except Exception as e:
                self._log(f"回调 {event} 异常: {e}", "warning")

    def get_connection_state(self) -> dict:
        """
        获取当前连接状态信息。

        Returns:
            dict: 包含连接状态的字典
                - state: 连接状态字符串
                - server: 当前服务器地址
                - viewer_count: 当前观看人数
                - room_id: 房间号
        """
        return {
            "state": self._connection_state,
            "server": self._current_server,
            "viewer_count": self._viewer_count,
            "room_id": self.real_room_id if self._connection_state != ConnectionState.DISCONNECTED else self.room_id,
        }

    async def _request_json(
        self,
        url: str,
        *,
        headers: Optional[dict] = None,
        cookies: Optional[dict] = None,
        params: Optional[dict] = None,
        allow_redirects: bool = True,
    ) -> dict:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=self._http_timeout)
        async with aiohttp.ClientSession(cookies=cookies, timeout=timeout) as session:
            async with session.get(
                url,
                headers=headers,
                params=params,
                allow_redirects=allow_redirects,
            ) as resp:
                return await resp.json()

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
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.bilibili.com/",
            }
            data = await self._request_json(
                "https://api.bilibili.com/x/web-interface/nav",
                headers=headers,
                cookies=cookies,
            )
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
        now = time.time()
        cached = self._real_room_id_cache.get(room_id)
        if cached and now - cached[1] < self._real_room_id_ttl:
            return cached[0]
        try:
            url = f"https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom?room_id={room_id}"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            data = await self._request_json(url, headers=headers)
            if data.get("code") == 0:
                real_id = data["data"]["room_info"]["room_id"]
                self._real_room_id_cache[room_id] = (real_id, now)
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
            timeout = aiohttp.ClientTimeout(total=self._http_timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    "https://www.bilibili.com/",
                    headers=headers,
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

    async def _get_danmaku_server_info(self, real_room_id: int) -> tuple[list, str]:
        """
        获取所有弹幕服务器地址列表和 token（带 WBI 签名）。

        Returns:
            tuple: ([(ws_url, host, wss_port), ...], token)
                - ws_url: 完整的 WebSocket URL
                - host: 服务器域名
                - wss_port: WSS 端口
                - token: 认证 token
        """
        servers = []
        token = ""

        try:
            # 从凭据中取 buvid3
            buvid3 = ""
            if self.credential:
                try:
                    buvid3 = getattr(self.credential, "buvid3", "") or ""
                except Exception as e:
                    self._log(f"credential cookie extraction failed: {e}", "debug")

            # buvid3 为空时自动获取临时值，避免 -352 风控
            if not buvid3:
                self._log("buvid3 为空，尝试获取临时 buvid3...")
                buvid3 = await self._fetch_buvid3()
                # 把获取到的 buvid3 回写到 credential，供认证包使用
                if buvid3 and self.credential:
                    try:
                        self.credential.buvid3 = buvid3
                    except Exception as e:
                        self._log(f"credential buvid3 writeback failed: {e}", "debug")
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
                except Exception as e:
                    self._log(f"credential cookie extraction failed: {e}", "debug")

            # ── WBI 签名 ────────────────────────────────────────────
            params = {"id": real_room_id, "type": 0}
            mixin_key = await self._get_wbi_mixin_key(cookies)
            if mixin_key:
                params = _wbi_sign(params, mixin_key)
                self._log(f"WBI 签名已添加 (w_rid={params.get('w_rid', '')[:8]}...)")
            else:
                self._log("WBI key 获取失败，尝试不带签名请求", "warning")

            url = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo"
            data = await self._request_json(url, params=params, headers=headers, cookies=cookies)
            api_code = data.get("code", -1)
            self._log(f"getDanmuInfo API: code={api_code}, msg={data.get('message', '')}")
            if api_code == 0:
                token = data["data"].get("token", "")
                hosts = data["data"].get("host_list", [])
                self._log(f"token长度={len(token)}, 可用服务器数={len(hosts)}")
                if hosts:
                    # 构建所有服务器的 URL 列表
                    for host in hosts:
                        # B站 API 可能返回 wss_port=0, 此时降级尝试 port 字段或默认 443
                        wss_port = host.get("wss_port", 0)
                        if not wss_port:
                            wss_port = host.get("port", 443)
                        if not wss_port:
                            wss_port = 443
                        ws_url = f"wss://{host['host']}:{wss_port}/sub"
                        servers.append((ws_url, host['host'], wss_port))
                    # 始终加入最可靠的 broadcastlv 作为保底（去重）
                    main_host = "broadcastlv.chat.bilibili.com"
                    if not any(s[1] == main_host for s in servers):
                        servers.append((f"wss://{main_host}/sub", main_host, 443))
                    self._log(f"弹幕服务器列表: {[s[1] + ':' + str(s[2]) for s in servers]}")
                    return servers, token
            else:
                self._log(f"getDanmuInfo 返回错误: {data}", "warning")
        except Exception as e:
            self._log(f"获取弹幕服务器信息失败: {e}，使用默认地址", "warning")

        # 回退到所有备用服务器（而非单一地址）
        fallback_servers = []
        for url in WS_FALLBACK_URLS:
            # 解析 wss://host:port/sub 格式
            try:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                host = parsed.hostname or ""
                port = parsed.port or 443
                fallback_servers.append((url, host, port))
            except Exception:
                fallback_servers.append((url, url.split("//")[1].split(":")[0] if "//" in url else "", 443))
        return fallback_servers, token

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
        # buvid3 有值时加入认证包（同时兼容新旧字段名）
        if buvid3:
            body["buvid"] = buvid3
            body["buvid3"] = buvid3  # 新版 B站 协议需要

        self._log(
            f"认证包信息: uid={uid}, room={real_room_id}, "
            f"buvid3={'有' if buvid3 else '⚠️无'}, "
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
                    except Exception as e:
                        self._log(f"heartbeat send failed: {e}", "debug")
                        break
        except asyncio.CancelledError:
            self._log("heartbeat loop cancelled", "debug")

    async def _dispatch_message(self, cmd: str, data: dict):
        """根据 cmd 分发事件"""
        self._log(f"_dispatch_message: cmd={cmd}", "debug")
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
                medal_level = 0
                medal_name = ""
                if len(info) > 3 and isinstance(info[3], list) and len(info[3]) >= 2:
                    try:
                        medal_level = int(info[3][0])
                        medal_name = str(info[3][1])
                        medal_text = f"[{medal_name}{medal_level}]"
                    except Exception as e:
                        self._log(f"fans medal parse failed: {e}", "debug")

                await self._emit("on_danmaku", {
                    "time": time_str,
                    "content": content,
                    "user_id": user_id,
                    "user_name": user_name,
                    "user_level": user_level,
                    "medal_text": medal_text,
                    "medal_level": medal_level,
                    "medal_name": medal_name,
                })

                # LiveDanmaku 事件（增强协议）
                try:
                    from .livedanmaku import LiveDanmaku as _LD
                    ld = _LD.from_danmaku(data)
                    if ld.text:
                        await self._emit("on_event", "DANMU_MSG", ld)
                except Exception as e:
                    self._log(f"LiveDanmaku DANMU_MSG parse failed: {e}", "debug")

            elif cmd == "SEND_GIFT":
                inner = data.get("data", {})
                try:
                    from .livedanmaku import LiveDanmaku as _LD
                    ld = _LD.from_gift(data)
                    await self._emit("on_event", "SEND_GIFT", ld)
                except Exception as e:
                    self._log(f"LiveDanmaku SEND_GIFT parse failed: {e}", "debug")
                    await self._emit("on_event", "SEND_GIFT", {
                        "uid": inner.get("uid", 0),
                        "nickname": inner.get("uname", ""),
                        "danmaku_text": f"gift {inner.get('giftName', 'gift')}",
                        "gift_name": inner.get("giftName", "gift"),
                        "gift_count": inner.get("num", 1),
                        "gift_value": inner.get("total_coin", 0),
                        "gift_coin_type": inner.get("coin_type", ""),
                        "room_id": inner.get("room_id") or inner.get("ruid", 0),
                    })
                await self._emit("on_gift", {
                    "user_name": inner.get("uname", "未知"),
                    "user_id": inner.get("uid", 0),
                    "gift_name": inner.get("giftName", "未知礼物"),
                    "num": inner.get("num", 1),
                    "coin_type": inner.get("coin_type", "silver"),
                    "total_coin": inner.get("total_coin", 0),
                    "price": inner.get("price", 0),
                })

            elif cmd == "SUPER_CHAT_MESSAGE":
                inner = data.get("data", {})
                user_info = inner.get("user_info", {})
                try:
                    from .livedanmaku import LiveDanmaku as _LD
                    ld = _LD.from_sc(data)
                    await self._emit("on_event", "SUPER_CHAT_MESSAGE", ld)
                except Exception as e:
                    self._log(f"LiveDanmaku SUPER_CHAT_MESSAGE parse failed: {e}", "debug")
                    await self._emit("on_event", "SUPER_CHAT_MESSAGE", {
                        "uid": inner.get("uid", 0),
                        "nickname": user_info.get("uname", ""),
                        "danmaku_text": inner.get("message", "") or "Super Chat",
                        "gift_name": "Super Chat",
                        "gift_value": (int(inner.get("price") or 0) * 1000) if str(inner.get("price") or "0").isdigit() else 0,
                        "room_id": inner.get("room_id", 0),
                    })
                await self._emit("on_sc", {
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
                    await self._emit("on_entry", user_name)
                elif msg_type == 2:
                    await self._emit("on_follow", user_name)

                try:
                    from .livedanmaku import LiveDanmaku as _LD
                    ld = _LD.from_interact(data)
                    await self._emit("on_event", "INTERACT_WORD", ld)
                except Exception as e:
                    self._log(f"LiveDanmaku INTERACT_WORD parse failed: {e}", "debug")

            elif cmd == "LIVE":
                await self._emit("on_live")

            elif cmd == "PREPARING":
                self._live_ended = True
                await self._emit("on_preparing")

            # ── 新增协议指令（MagicalDanmaku 增强） ────────────────────────
            elif cmd in self._CMD_HANDLERS:
                handler = self._CMD_HANDLERS[cmd]
                try:
                    ld = handler(data)
                    if ld:
                        await self._emit("on_event", cmd, ld)
                except Exception as e:
                    self._log(f"增强协议处理 {cmd} 异常: {e}", "debug")
            else:
                gift_payload = self._fallback_support_gift_payload(cmd, data)
                if gift_payload:
                    await self._emit("on_event", "SEND_GIFT", gift_payload)

        except Exception as e:
            self._log(f"分发消息 {cmd} 异常: {e}", "debug")

    @staticmethod
    def _fallback_support_gift_payload(cmd: str, data: dict):
        """Recover trusted Bilibili support packets that use a newer/unknown cmd."""
        if not isinstance(data, dict) or str(cmd or "").split(":", 1)[0] == "DANMU_MSG":
            return None
        inner = data.get("data")
        if not isinstance(inner, dict):
            return None
        gift_info = inner.get("gift_info") or inner.get("giftInfo") or inner.get("gift") or {}
        if not isinstance(gift_info, dict):
            gift_info = {}
        command = str(cmd or "").upper()
        explicit_gift_command = "GIFT" in command
        official_support_command = command in {"USER_TOAST_MSG"}
        explicit_gift_fields = any(
            key in inner
            for key in ("gift_id", "giftId", "giftName", "gift_name", "gift_name_str", "giftNameStr")
        )
        nested_gift_fields = any(
            key in gift_info
            for key in ("gift_id", "giftId", "giftName", "gift_name", "gift_name_str", "giftNameStr")
        )
        nested_gift_name_fields = any(
            key in gift_info
            for key in ("giftName", "gift_name", "gift_name_str", "giftNameStr")
        )
        text_hint = DanmakuListener._first_text(
            inner,
            gift_info,
            keys=("toast_msg", "toastMsg", "message", "msg", "copy_writing", "copyWriting", "text", "content"),
        )
        if official_support_command and DanmakuListener._looks_like_fans_medal_activation_text(text_hint):
            return None
        support_hint = (
            explicit_gift_command
            or explicit_gift_fields
            or (
                official_support_command
                and nested_gift_name_fields
                and DanmakuListener._looks_like_gift_transfer_text(text_hint)
            )
        )
        gift_name = DanmakuListener._first_text(
            inner,
            gift_info,
            keys=("giftName", "gift_name", "gift_name_str", "giftNameStr"),
        )
        if not gift_name and explicit_gift_command:
            gift_name = DanmakuListener._first_text(inner, gift_info, keys=("name",))
        elif not gift_name and nested_gift_fields:
            gift_name = DanmakuListener._first_text(gift_info, keys=("name",))
        if not gift_name and support_hint and DanmakuListener._looks_like_fans_medal_text(text_hint):
            gift_name = "fans medal"
        if not gift_name or not support_hint:
            return None
        uid = DanmakuListener._first_int(inner, gift_info, keys=("uid", "user_id", "userId", "sender_uid", "senderUid"))
        nickname = DanmakuListener._first_text(
            inner,
            gift_info,
            keys=("uname", "user_name", "userName", "nickname", "name"),
        )
        count = DanmakuListener._first_int(inner, gift_info, keys=("num", "gift_num", "giftNum", "combo_num", "comboNum")) or 1
        value = DanmakuListener._first_int(inner, gift_info, keys=("total_coin", "totalCoin", "price", "coin", "gold")) or 0
        room_id = DanmakuListener._first_int(inner, data, keys=("room_id", "roomId", "roomid")) or 0
        return {
            "uid": uid,
            "nickname": nickname,
            "danmaku_text": f"gift {gift_name}",
            "gift_name": gift_name,
            "gift_count": count,
            "gift_value": value,
            "room_id": room_id,
            "raw_cmd": str(cmd or ""),
        }

    @staticmethod
    def _looks_like_fans_medal_text(text: str) -> bool:
        cleaned = str(text or "")
        if not cleaned:
            return False
        return (
            ("粉丝团灯牌" in cleaned or "粉丝牌" in cleaned or "灯牌" in cleaned)
            and ("赠送" in cleaned or "点亮" in cleaned or "投喂" in cleaned or "加入" in cleaned)
        )

    @staticmethod
    def _looks_like_fans_medal_activation_text(text: str) -> bool:
        cleaned = str(text or "")
        return DanmakuListener._looks_like_fans_medal_text(cleaned) and (
            "成功" in cleaned or "点亮" in cleaned or "加入粉丝团" in cleaned
        )

    @staticmethod
    def _looks_like_gift_transfer_text(text: str) -> bool:
        cleaned = str(text or "")
        return bool(cleaned) and any(marker in cleaned for marker in ("赠送", "投喂"))

    @staticmethod
    def _first_text(*sources: dict, keys: tuple[str, ...]) -> str:
        for source in sources:
            if not isinstance(source, dict):
                continue
            for key in keys:
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _first_int(*sources: dict, keys: tuple[str, ...]) -> int:
        for source in sources:
            if not isinstance(source, dict):
                continue
            for key in keys:
                value = source.get(key)
                if isinstance(value, bool):
                    continue
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    continue
                if parsed >= 0:
                    return parsed
        return 0

    # ── 增强协议指令处理器 ─────────────────────────────────────

    @staticmethod
    def _handle_guard_buy(data: dict):
        """GUARD_BUY — 上舰（大航海）"""
        from .livedanmaku import LiveDanmaku as _LD
        return _LD.from_guard_buy(data)

    @staticmethod
    def _handle_entry_effect(data: dict):
        """ENTRY_EFFECT — 高能用户进场特效"""
        from .livedanmaku import LiveDanmaku as _LD
        return _LD.from_entry_effect(data)

    @staticmethod
    def _handle_combo_send(data: dict):
        """COMBO_SEND — 礼物连击"""
        from .livedanmaku import LiveDanmaku as _LD, GiftInfo, MessageType, MedalInfo
        d = data.get("data", {})
        return _LD(
            msg_type=MessageType.MSG_GIFT,
            uid=int(d.get("uid", 0)),
            nickname=str(d.get("uname", "")),
            text=f"连击 {d.get('combo_num', 1)} 个 {d.get('gift_name', '礼物')}",
            room_id=int(data.get("room_id", 0)),
            guard_level=int(d.get("guard_level", 0)),
            user_level=int(d.get("level", 0)),
            gift=GiftInfo(
                gift_id=int(d.get("gift_id", 0)),
                gift_name=str(d.get("gift_name", "礼物")),
                num=int(d.get("combo_num", 1)),
                total_coin=int(d.get("total_coin", 0)),
            ),
            medal=MedalInfo(
                name=str(d.get("medal_info", {}).get("medal_name", "")),
                level=int(d.get("medal_info", {}).get("medal_level", 0)),
            ) if d.get("medal_info") else None,
        )

    @staticmethod
    def _handle_like(data: dict):
        """LIKE_INFO_V3_CLICK — 点赞"""
        from .livedanmaku import LiveDanmaku as _LD
        return _LD.from_like(data)

    @staticmethod
    def _handle_online_rank(data: dict):
        """ONLINE_RANK_V2 / ONLINE_RANK_TOP3 — 高能榜"""
        from .livedanmaku import LiveDanmaku as _LD
        return _LD.from_online_rank(data)

    @staticmethod
    def _handle_notice(data: dict):
        """NOTICE_MSG — 公告"""
        from .livedanmaku import LiveDanmaku as _LD
        return _LD.from_notice(data)

    @staticmethod
    def _handle_anchor_lot(data: dict):
        """ANCHOR_LOT_START / ANCHOR_LOT_END — 天选抽奖"""
        from .livedanmaku import LiveDanmaku as _LD
        return _LD.from_anchor_lot(data)

    @staticmethod
    def _handle_block(data: dict):
        """ROOM_BLOCK_MSG — 禁言"""
        from .livedanmaku import LiveDanmaku as _LD
        return _LD.from_block(data)

    @staticmethod
    def _handle_watched_change(data: dict):
        """WATCHED_CHANGE — 看过人数变化"""
        from .livedanmaku import LiveDanmaku as _LD
        return _LD.from_watched_change(data)

    @staticmethod
    def _handle_room_update(data: dict):
        """ROOM_REAL_TIME_MESSAGE_UPDATE — 直播间实时数据更新"""
        from .livedanmaku import LiveDanmaku as _LD, MessageType
        d = data.get("data", {})
        return _LD(
            msg_type=MessageType.MSG_EXTRA,
            uid=0,
            nickname="",
            text=f"直播间实时更新: {d.get('fans', 0)}粉丝, {d.get('room_id', '?')}",
            room_id=int(data.get("room_id", 0)),
            extra_json=__import__('json').dumps(data, ensure_ascii=False),
        )

    @staticmethod
    def _handle_room_change(data: dict):
        """ROOM_CHANGE — 直播间信息变更"""
        from .livedanmaku import LiveDanmaku as _LD, MessageType
        d = data.get("data", {})
        changes = []
        if "title" in d:
            changes.append(f"标题: {d['title']}")
        if "area_name" in d:
            changes.append(f"分区: {d['area_name']}")
        text = "直播间变更: " + "; ".join(changes) if changes else "直播间信息更新"
        return _LD(
            msg_type=MessageType.MSG_EXTRA,
            uid=0,
            nickname="",
            text=text,
            room_id=int(data.get("room_id", 0)),
            extra_json=__import__('json').dumps(data, ensure_ascii=False),
        )

    @staticmethod
    def _handle_sc_jpn(data: dict):
        """SUPER_CHAT_MESSAGE_JPN — 日文 SC（复用 SC handler）"""
        from .livedanmaku import LiveDanmaku as _LD
        return _LD.from_sc(data)

    # ── CMD 分发字典 ──────────────────────────────────────────
    _CMD_HANDLERS = {
        "GUARD_BUY": _handle_guard_buy,
        "ENTRY_EFFECT": _handle_entry_effect,
        "COMBO_SEND": _handle_combo_send,
        "LIKE_INFO_V3_CLICK": _handle_like,
        "ONLINE_RANK_V2": _handle_online_rank,
        "ONLINE_RANK_TOP3": _handle_online_rank,
        "NOTICE_MSG": _handle_notice,
        "ANCHOR_LOT_START": _handle_anchor_lot,
        "ANCHOR_LOT_END": _handle_anchor_lot,
        "ROOM_BLOCK_MSG": _handle_block,
        "WATCHED_CHANGE": _handle_watched_change,
        "ROOM_REAL_TIME_MESSAGE_UPDATE": _handle_room_update,
        "ROOM_CHANGE": _handle_room_change,
        "SUPER_CHAT_MESSAGE_JPN": _handle_sc_jpn,
    }

    async def _process_packet(self, raw: bytes):
        """处理单个数据包"""
        if len(raw) < HEADER_LEN:
            return
        total_len, header_len, proto_ver, operation, seq = _unpack_header(raw)
        body = raw[header_len:total_len]

        if operation == OPERATION_HEARTBEAT_REPLY:
            # 解析人气值（心跳回复前4字节是大端序int）
            try:
                if len(body) >= 4:
                    viewer_count = struct.unpack(">I", body[:4])[0]
                    if viewer_count != self._viewer_count:
                        self._viewer_count = viewer_count
                        self._log(f"📊 人气值: {viewer_count:,}")
                    # 可选：触发人气值变化回调
                    await self._emit("on_viewer_count", viewer_count)
            except Exception as e:
                self._log(f"viewer count callback failed: {e}", "debug")

        elif operation == OPERATION_AUTH_REPLY:
            try:
                result = json.loads(body.decode("utf-8"))
                code = result.get("code", -1)
                if code == 0:
                    self._connection_state = ConnectionState.RECEIVING
                    self._authenticated_in_attempt = True
                    self._ready_event.set()
                    self._log(f"✅ 认证成功，开始接收弹幕 [{self._current_server}]")
                else:
                    self._connection_state = ConnectionState.DISCONNECTED
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
                    decompressed = _decompress(body, proto_ver, self._log)
                    for pkt in _split_packets(decompressed):
                        await self._process_packet(pkt)
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
                    await self._dispatch_message(cmd, msg)
                except Exception as e:
                    self._log(f"解析消息失败: {e}", "warning")

    async def start(self):
        """启动监听（带自动重连，直到 stop() 被调用）"""
        import websockets

        # 重置停止事件和直播结束标记
        self._stop_event.clear()
        self._ready_event.clear()
        self._live_ended = False
        self.running = True
        self._connection_state = ConnectionState.CONNECTING

        retry_count = 0
        max_retries = 10
        retry_delay = 5  # 初始重试间隔（秒）

        try:
            while True:
                if self._stop_event.is_set():
                    break
                self._authenticated_in_attempt = False
                try:
                    await self._connect_once()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._log(f"连接过程异常: {e}", "error")
                    await self._emit("on_error", e)

                if self._stop_event.is_set():
                    break
                if self._live_ended:
                    self._connection_state = ConnectionState.DISCONNECTED
                    break

                # A connection that reached AUTH ready is a successful recovery.
                # A later disconnect starts a fresh retry budget instead of consuming
                # the failures accumulated before authentication.
                if self._authenticated_in_attempt:
                    retry_count = 0
                retry_count += 1
                if retry_count > max_retries:
                    self._log(f"重连次数超过 {max_retries} 次，停止重连", "error")
                    self._connection_state = ConnectionState.DISCONNECTED
                    break

                self._connection_state = ConnectionState.RECONNECTING
                wait = min(retry_delay * retry_count, 60)
            # 前3次打印重连日志，之后静默
                if retry_count <= 3:
                    self._log(f"🔄 {wait}s 后自动重连 (第{retry_count}次)...")
                elif retry_count == 4:
                    self._log(f"🔄 持续重连中，后续重连不再打印日志（共最多{max_retries}次）")

                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=wait)
                    break
                except asyncio.TimeoutError:
                    pass
        finally:
            self.running = False
            self._connection_state = ConnectionState.DISCONNECTED
            self._current_server = ""
            self._log("弹幕监听已停止")

    async def wait_until_ready(self) -> None:
        """Wait until Bilibili acknowledges AUTH successfully."""
        await self._ready_event.wait()

    async def _connect_once(self):
        """单次 WebSocket 连接（尝试所有服务器，内部）"""
        import websockets

        # 连接前检查是否已被 stop
        if self._stop_event.is_set():
            return

        # 1. 获取真实房间号
        real_room_id = await self._get_real_room_id(self.room_id)
        if self._stop_event.is_set():
            return
        # 保存真实房间号供状态投影和后续协议请求使用
        self.real_room_id = real_room_id

        # 2. 获取所有服务器和 token
        servers, token = await self._get_danmaku_server_info(real_room_id)
        if self._stop_event.is_set():
            return

        if not servers:
            self._log("没有可用的弹幕服务器", "error")
            return

        # 3. 构建认证包
        auth_body = self._build_auth_body(real_room_id, token)

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

        # 遍历所有服务器尝试连接
        last_error = None
        for ws_url, host, port in servers:
            if self._stop_event.is_set():
                return

            self._connection_state = ConnectionState.CONNECTING
            self._current_server = f"{host}:{port}"
            self._log(f"正在连接弹幕服务器 [{host}:{port}]...")

            # 标记是否曾成功建立连接（用于区分"从未连上"和"连上后正常断开"）
            had_authenticated = False

            try:
                async with websockets.connect(ws_url, ping_interval=None, **_ws_kwargs) as ws:
                    self._ws = ws

                    # 发送认证包
                    self._connection_state = ConnectionState.AUTHENTICATING
                    await ws.send(_pack(OPERATION_AUTH, auth_body))
                    self._log("认证包已发送，等待服务器回复...")

                    # 启动心跳（心跳会在收到 AUTH_REPLY 成功后自动开始计时）
                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                    async for message in ws:
                        if self._stop_event.is_set():
                            break
                        try:
                            if isinstance(message, bytes):
                                # 认证成功会在这里设置 RECEIVING 状态
                                await self._process_packet(message)
                            # str 消息忽略
                        except Exception as e:
                            self._log(f"处理消息异常: {e}", "debug")

                    # 正常退出循环（可能是 stop() 调用或服务器断开）
                    had_authenticated = self._connection_state == ConnectionState.RECEIVING

                    # 如果是被 stop() 打断，跳出不再尝试其他服务器
                    if self._stop_event.is_set():
                        break

                    # 直播结束：收到 PREPARING 后不再重连
                    if self._live_ended:
                        self._log(f"直播已结束，停止重连", "info")
                        break

                    # 否则是服务器正常断开，继续尝试下一个服务器
                    if had_authenticated:
                        self._log(f"服务器 [{host}:{port}] 连接已正常关闭，尝试下一个...", "info")
                    continue

            except Exception as e:
                err_str = str(e)
                last_error = e
                # "no close frame" 是服务器直接断 TCP 的正常情况，降级为 warning
                if "no close frame" in err_str or "connection closed" in err_str.lower():
                    self._log(f"服务器 [{host}:{port}] 连接已断开，尝试下一个...", "warning")
                else:
                    self._log(f"服务器 [{host}:{port}] 连接异常: {err_str}，尝试下一个...", "warning")
                continue

            finally:
                if self._heartbeat_task and not self._heartbeat_task.done():
                    self._heartbeat_task.cancel()
                self._ws = None
                self._log(f"弹幕连接 [{host}:{port}] 已断开")

        # 只有在从未成功认证过的情况下才报"所有服务器失败"
        # 如果有任意服务器曾成功连接并断开，说明是直播结束，不是故障
        if not self._stop_event.is_set() and self._connection_state != ConnectionState.RECEIVING:
            self._connection_state = ConnectionState.DISCONNECTED
            self._current_server = ""
            if last_error:
                self._log(f"所有 {len(servers)} 个服务器连接失败: {last_error}", "error")
                raise last_error

    async def stop(self):
        """断开连接（可在任意时刻安全调用，包括连接建立过程中）"""
        self.running = False
        self._connection_state = ConnectionState.DISCONNECTED
        self._current_server = ""
        self._viewer_count = 0  # 清空人气值
        self._stop_event.set()  # 唤醒所有等待此事件的协程
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception as e:
                self._log(f"WebSocket close failed: {e}", "debug")
        self._ws = None

    def is_running(self) -> bool:
        return self.running
