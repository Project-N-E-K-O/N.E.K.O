"""
对话桥接器 - 将 cross_server 的对话转发到 message_plane

本模块与 cross_server.py 中的其他通道（monitor、bullet）平级，
负责将对话数据转发到 message_plane，供插件订阅和分析。

设计原则:
1. 与 plane_bridge.py 使用相同的消息格式和端点
2. 懒加载 + 非阻塞 + 自动重连
3. 进程安全 (cross_server 是独立进程)
4. 失败静默，不影响主流程
"""

import os
import queue
import threading
import time
import uuid
import json
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

# 默认端点 (与 plugin/settings.py 中的 MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT 一致)
# 注意：cross_server 是独立进程，不能直接导入 plugin/settings.py（避免循环依赖）
DEFAULT_INGEST_ENDPOINT = "tcp://127.0.0.1:38867"

# 消息配置
MESSAGE_STORE = "conversations"     # 独立的 conversations store（与 messages 分离）
MESSAGE_TOPIC = "all"               # 默认话题
MESSAGE_SOURCE = "main_server"      # 消息来源标识
MESSAGE_PLUGIN_ID = "neko_core"     # 系统级 plugin_id
MESSAGE_PRIORITY = 10               # 对话消息优先级


class ConversationIdGenerator:
    """
    对话 ID 生成器 - 生成有序、可追踪的 conversation_id
    
    格式: {timestamp_ms}_{sequence:05d}_{random_suffix:04x}
    例如: 1738406400000_00001_a1b2
    
    优点:
    - 时间戳部分可排序，判断先后
    - 序列号可检测同一毫秒内的漏掉
    - 随机后缀防止冲突（多进程场景）
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._last_ts: int = 0
        self._seq: int = 0
    
    def next_id(self) -> str:
        """生成下一个 conversation_id"""
        import random
        with self._lock:
            ts = int(time.time() * 1000)  # 毫秒时间戳
            if ts == self._last_ts:
                self._seq += 1
            else:
                self._last_ts = ts
                self._seq = 0
            # 格式: 时间戳_序列号_随机后缀
            suffix = format(random.randint(0, 0xFFFF), '04x')
            return f"{ts}_{self._seq:05d}_{suffix}"


# 全局 conversation_id 生成器（进程内单例）
_conversation_id_generator: Optional[ConversationIdGenerator] = None
_generator_lock = threading.Lock()


def get_conversation_id_generator() -> ConversationIdGenerator:
    """获取全局 ConversationIdGenerator 实例"""
    global _conversation_id_generator
    if _conversation_id_generator is None:
        with _generator_lock:
            if _conversation_id_generator is None:
                _conversation_id_generator = ConversationIdGenerator()
    return _conversation_id_generator


def generate_conversation_id() -> str:
    """便捷函数: 生成下一个 conversation_id
    
    格式: {timestamp_ms}_{sequence:05d}_{random_suffix:04x}
    例如: 1738406400000_00001_a1b2
    
    特点:
    - 有序: 可通过时间戳和序列号判断先后
    - 可追踪: 可检测漏掉的消息
    - 唯一: 随机后缀防止多进程冲突
    """
    return get_conversation_id_generator().next_id()


def _get_ingest_endpoint() -> str:
    """获取 ingest 端点，优先从环境变量读取
    
    支持的环境变量（按优先级）：
    1. NEKO_MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT
    2. NEKO_MESSAGE_PLANE_INGEST
    3. 默认值 tcp://127.0.0.1:38867
    """
    return os.getenv(
        "NEKO_MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT",
        os.getenv("NEKO_MESSAGE_PLANE_INGEST", DEFAULT_INGEST_ENDPOINT)
    )


def _get_message_plane_enabled() -> bool:
    """检查消息面桥接是否启用"""
    val = os.getenv("NEKO_MESSAGE_PLANE_BRIDGE_ENABLED", "true").lower()
    return val in ("true", "1", "yes", "on")


class ConversationBridge:
    """
    对话桥接器 - 将对话转发到 message_plane
    
    使用与 plane_bridge.py 相同的协议:
    - ZMQ PUSH 模式
    - msgpack 序列化
    - delta_batch 消息格式
    
    与 cross_server 中的其他通道（monitor、bullet）平级，
    但使用 ZMQ 而非 WebSocket，因为 message_plane 使用 ZMQ 作为 ingest 接口。
    """
    
    def __init__(self, endpoint: Optional[str] = None, enabled: Optional[bool] = None):
        self._endpoint = endpoint or _get_ingest_endpoint()
        self._enabled = enabled if enabled is not None else _get_message_plane_enabled()
        self._q: "queue.Queue[bytes]" = queue.Queue(maxsize=2048)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._zmq_available: Optional[bool] = None
        self._init_lock = threading.Lock()
        self._connected = False
        self._last_error_time: float = 0
        self._error_log_interval: float = 5.0  # 错误日志间隔，避免刷屏
        
    def _check_zmq(self) -> bool:
        """检查 ZMQ 和 ormsgpack 是否可用"""
        if self._zmq_available is not None:
            return self._zmq_available
        with self._init_lock:
            if self._zmq_available is not None:
                return self._zmq_available
            try:
                import zmq  # noqa: F401
                import ormsgpack  # noqa: F401
                self._zmq_available = True
            except ImportError as e:
                self._zmq_available = False
                logger.debug(f"ZMQ or ormsgpack not available: {e}")
            return self._zmq_available
    
    def start(self) -> bool:
        """启动后台发送线程
        
        Returns:
            是否成功启动
        """
        if not self._enabled:
            return False
        if not self._check_zmq():
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        
        with self._init_lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stop.clear()
            t = threading.Thread(
                target=self._run,
                daemon=True,
                name="ConversationBridge"
            )
            self._thread = t
            t.start()
            logger.debug(f"ConversationBridge started, endpoint: {self._endpoint}")
            return True
    
    def stop(self) -> None:
        """停止后台线程"""
        self._stop.set()
        if self._thread and self._thread.is_alive():
            try:
                self._thread.join(timeout=2.0)
            except Exception:
                pass
        self._connected = False
    
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._connected and self._thread is not None and self._thread.is_alive()
    
    def publish(
        self,
        messages: List[Dict[str, str]],
        lanlan_name: str,
        turn_type: str = "turn_end",
        conversation_id: Optional[str] = None,
    ) -> bool:
        """
        发布对话到 message_plane (非阻塞)
        
        Args:
            messages: 对话消息列表 [{role, text}, ...]
            lanlan_name: 角色名
            turn_type: 事件类型 ("turn_end" | "session_end" | "renew_session")
            conversation_id: 对话ID，用于关联触发事件和对话上下文
            
        Returns:
            是否成功加入发送队列
        """
        if not self._enabled:
            return False
        if not self._check_zmq():
            return False
        
        # 确保后台线程运行
        if not self.start():
            return False
        
        # 如果没有提供 conversation_id，使用有序生成器
        if conversation_id is None:
            conversation_id = generate_conversation_id()
        
        # 构造与 plane_bridge.py 相同格式的消息
        # 参考: plugin/server/messaging/plane_bridge.py enqueue_delta()
        msg = {
            "v": 1,
            "kind": "delta_batch",
            "from": "cross_server",
            "ts": time.time(),
            "batch_id": str(uuid.uuid4()),
            "items": [{
                "store": MESSAGE_STORE,
                "topic": MESSAGE_TOPIC,
                "payload": {
                    "message_id": str(uuid.uuid4()),
                    "message_type": MESSAGE_TOPIC,
                    "source": MESSAGE_SOURCE,
                    "plugin_id": MESSAGE_PLUGIN_ID,
                    "priority": MESSAGE_PRIORITY,
                    "timestamp": time.time(),
                    "content": json.dumps(messages, ensure_ascii=False),
                    "metadata": {
                        "lanlan_name": lanlan_name,
                        "turn_type": turn_type,
                        "message_count": len(messages),
                        "conversation_id": conversation_id,
                    },
                },
            }],
        }
        
        try:
            import ormsgpack
            data = ormsgpack.packb(msg)
            self._q.put_nowait(data)
            return True
        except queue.Full:
            # 队列满时静默丢弃，避免阻塞主流程
            now = time.time()
            if now - self._last_error_time > self._error_log_interval:
                logger.debug(f"[{lanlan_name}] Conversation bridge queue full, dropping message")
                self._last_error_time = now
            return False
        except Exception as e:
            now = time.time()
            if now - self._last_error_time > self._error_log_interval:
                logger.debug(f"[{lanlan_name}] Conversation bridge serialize error: {e}")
                self._last_error_time = now
            return False
    
    def _run(self) -> None:
        """后台发送线程"""
        try:
            import zmq
        except ImportError:
            logger.warning("ZMQ not available, conversation bridge thread exiting")
            return
        
        ctx = zmq.Context.instance()
        sock: Optional["zmq.Socket"] = None
        reconnect_delay = 0.5
        max_reconnect_delay = 10.0
        
        while not self._stop.is_set():
            # 连接管理
            if sock is None:
                try:
                    sock = ctx.socket(zmq.PUSH)
                    sock.setsockopt(zmq.LINGER, 0)
                    sock.setsockopt(zmq.SNDHWM, 100)  # 发送高水位
                    sock.setsockopt(zmq.SNDTIMEO, 1000)  # 发送超时 1s
                    sock.connect(self._endpoint)
                    self._connected = True
                    logger.debug(f"ConversationBridge connected to {self._endpoint}")
                    reconnect_delay = 0.5  # 重置重连延迟
                except Exception as e:
                    self._connected = False
                    now = time.time()
                    if now - self._last_error_time > self._error_log_interval:
                        logger.debug(f"ConversationBridge connect failed: {e}")
                        self._last_error_time = now
                    if sock:
                        try:
                            sock.close(0)
                        except Exception:
                            pass
                        sock = None
                    # 指数退避重连
                    self._stop.wait(min(reconnect_delay, max_reconnect_delay))
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                    continue
            
            # 从队列获取消息
            try:
                data = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            
            # 发送消息
            try:
                sock.send(data, flags=zmq.NOBLOCK)
            except zmq.Again:
                # 发送缓冲区满，丢弃消息
                now = time.time()
                if now - self._last_error_time > self._error_log_interval:
                    logger.debug("ConversationBridge send buffer full, dropping message")
                    self._last_error_time = now
            except Exception as e:
                self._connected = False
                now = time.time()
                if now - self._last_error_time > self._error_log_interval:
                    logger.debug(f"ConversationBridge send error: {e}")
                    self._last_error_time = now
                # 连接可能断开，重置
                try:
                    sock.close(0)
                except Exception:
                    pass
                sock = None
        
        # 清理
        self._connected = False
        if sock:
            try:
                sock.close(0)
            except Exception:
                pass
        logger.debug("ConversationBridge thread stopped")


# 全局单例 (进程内)
_bridge: Optional[ConversationBridge] = None
_bridge_lock = threading.Lock()


def get_conversation_bridge() -> ConversationBridge:
    """获取全局 ConversationBridge 实例
    
    每个进程有独立的实例（cross_server 是独立进程）
    """
    global _bridge
    if _bridge is None:
        with _bridge_lock:
            if _bridge is None:
                _bridge = ConversationBridge()
    return _bridge


def publish_conversation(
    messages: List[Dict[str, str]],
    lanlan_name: str,
    turn_type: str = "turn_end",
    conversation_id: Optional[str] = None,
) -> bool:
    """便捷函数: 发布对话到 message_plane
    
    Args:
        messages: 对话消息列表 [{role, text}, ...]
        lanlan_name: 角色名
        turn_type: 事件类型 ("turn_end" | "session_end" | "renew_session")
        conversation_id: 对话ID，用于关联触发事件和对话上下文
        
    Returns:
        是否成功加入发送队列
        
    Note:
        此函数是非阻塞的，失败时静默返回 False，不影响调用方
    """
    try:
        return get_conversation_bridge().publish(messages, lanlan_name, turn_type, conversation_id)
    except Exception:
        return False


def stop_conversation_bridge() -> None:
    """停止全局 ConversationBridge（用于进程退出时清理）"""
    global _bridge
    if _bridge is not None:
        try:
            _bridge.stop()
        except Exception:
            pass
