from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Union, Coroutine, cast

import ormsgpack

try:
    import zmq
except Exception:  # pragma: no cover
    zmq = None

try:
    import zmq.asyncio
except Exception:  # pragma: no cover
    pass


class MessagePlaneRpcClient:
    def __init__(self, *, plugin_id: str, endpoint: str) -> None:
        if zmq is None:
            raise RuntimeError("pyzmq is not available")
        self._plugin_id = str(plugin_id)
        self._endpoint = str(endpoint)
        try:
            import threading

            self._tls = threading.local()
            self._lock = threading.Lock()  # Protect socket creation
        except Exception:
            self._tls = None
            self._lock = None
        
        # Async socket cache (task-local for asyncio)
        self._async_sock_cache: Optional[Any] = None
        self._async_ctx_cache: Optional[Any] = None

    def _get_sock(self):
        if self._tls is not None:
            sock = getattr(self._tls, "sock", None)
            if sock is not None:
                return sock
        if zmq is None:
            return None
        
        # Protect socket creation with lock to avoid ZMQ assertion failures
        if self._lock is not None:
            with self._lock:
                # Double-check after acquiring lock
                if self._tls is not None:
                    sock = getattr(self._tls, "sock", None)
                    if sock is not None:
                        return sock
                
                # Use thread-local context to avoid context lock contention
                if self._tls is not None:
                    ctx = getattr(self._tls, "ctx", None)
                    if ctx is None:
                        ctx = zmq.Context()
                        self._tls.ctx = ctx
                else:
                    ctx = zmq.Context.instance()
                
                sock = ctx.socket(zmq.DEALER)
                ident = f"mp:{self._plugin_id}:{int(time.time() * 1000)}".encode("utf-8")
                try:
                    sock.setsockopt(zmq.IDENTITY, ident)
                except Exception:
                    pass
                try:
                    sock.setsockopt(zmq.LINGER, 0)
                except Exception:
                    pass
                try:
                    # TCP_NODELAY for lower latency
                    sock.setsockopt(getattr(zmq, 'TCP_NODELAY', 1), 1)
                except Exception:
                    pass
                try:
                    # Increase buffer sizes for better throughput
                    sock.setsockopt(zmq.RCVBUF, 2*1024*1024)  # 2MB receive buffer
                except Exception:
                    pass
                try:
                    sock.setsockopt(zmq.SNDBUF, 2*1024*1024)  # 2MB send buffer
                except Exception:
                    pass
                try:
                    # Increase high water mark for better burst performance
                    sock.setsockopt(zmq.RCVHWM, 10000)
                except Exception:
                    pass
                try:
                    sock.setsockopt(zmq.SNDHWM, 10000)
                except Exception:
                    pass
                sock.connect(self._endpoint)
                if self._tls is not None:
                    try:
                        self._tls.sock = sock
                    except Exception:
                        pass
                return sock
        else:
            # No threading support, use global context
            ctx = zmq.Context.instance()
            sock = ctx.socket(zmq.DEALER)
            ident = f"mp:{self._plugin_id}:{int(time.time() * 1000)}".encode("utf-8")
            try:
                sock.setsockopt(zmq.IDENTITY, ident)
            except Exception:
                pass
            try:
                sock.setsockopt(zmq.LINGER, 0)
            except Exception:
                pass
            sock.connect(self._endpoint)
            return sock

    def _next_req_id(self) -> str:
        if self._tls is not None:
            try:
                n = int(getattr(self._tls, "req_seq", 0) or 0) + 1
                self._tls.req_seq = n
                return f"{self._plugin_id}:{n}"
            except Exception:
                pass
        return str(uuid.uuid4())
    
    def _is_in_event_loop(self) -> bool:
        """检测当前是否在事件循环中运行"""
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    async def _get_async_sock(self):
        """获取异步 ZMQ socket (复用连接)"""
        if self._async_sock_cache is not None:
            return self._async_sock_cache
        
        try:
            import zmq.asyncio
        except Exception:
            return None
        
        if self._async_ctx_cache is None:
            self._async_ctx_cache = zmq.asyncio.Context()
        
        ctx = self._async_ctx_cache
        sock = ctx.socket(zmq.DEALER)
        ident = f"mp:{self._plugin_id}:{int(time.time() * 1000)}".encode("utf-8")
        
        try:
            sock.setsockopt(zmq.IDENTITY, ident)
        except Exception:
            pass
        try:
            sock.setsockopt(zmq.LINGER, 0)
        except Exception:
            pass
        try:
            sock.setsockopt(getattr(zmq, 'TCP_NODELAY', 1), 1)
        except Exception:
            pass
        try:
            sock.setsockopt(zmq.RCVBUF, 2*1024*1024)
        except Exception:
            pass
        try:
            sock.setsockopt(zmq.SNDBUF, 2*1024*1024)
        except Exception:
            pass
        try:
            sock.setsockopt(zmq.RCVHWM, 10000)
        except Exception:
            pass
        try:
            sock.setsockopt(zmq.SNDHWM, 10000)
        except Exception:
            pass
        
        sock.connect(self._endpoint)
        self._async_sock_cache = sock
        return sock
    
    async def request_async(self, *, op: str, args: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
        """异步版本的 RPC 请求"""
        try:
            import zmq.asyncio
        except Exception:
            return None
        
        sock = await self._get_async_sock()
        if sock is None:
            return None
        
        req_id = self._next_req_id()
        req = {
            "v": 1,
            "op": op,
            "req_id": req_id,
            "args": args,
            "from_plugin": self._plugin_id
        }
        
        try:
            # 零拷贝优化:直接序列化到 bytes,避免中间拷贝
            raw = ormsgpack.packb(req)
        except Exception:
            return None
        
        try:
            # 零拷贝发送:copy=False 避免 ZMQ 内部拷贝,track=False 避免跟踪开销
            await sock.send(raw, flags=0, copy=False, track=False)
        except Exception:
            return None
        
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return None
            
            try:
                # 使用 asyncio.wait_for 实现超时
                events = await asyncio.wait_for(
                    sock.poll(timeout=int(remaining * 1000), flags=zmq.POLLIN),
                    timeout=remaining
                )
                if events == 0:
                    continue
            except asyncio.TimeoutError:
                return None
            except Exception:
                return None
            
            try:
                # 零拷贝接收:copy=False 返回 Frame,需要转换为 bytes
                resp_frame = await sock.recv(flags=0, copy=False)
                resp_raw = bytes(resp_frame)  # Frame to bytes
            except Exception:
                return None
            
            try:
                # 直接反序列化
                resp = ormsgpack.unpackb(resp_raw)
            except Exception:
                continue
            
            if isinstance(resp, dict):
                _req_id = resp.get("req_id")
                _v = resp.get("v")
                _ok = resp.get("ok")
                if _req_id == req_id and _v == 1 and isinstance(_ok, bool):
                    return resp
    
    def request_sync(self, *, op: str, args: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
        """同步版本的 RPC 请求 (原 request 方法)"""
        if zmq is None:
            return None
        sock = self._get_sock()
        if sock is None:
            return None
        req_id = self._next_req_id()
        # Fast path: pre-allocate dict with exact size to avoid rehashing
        req = {
            "v": 1,
            "op": op,
            "req_id": req_id,
            "args": args,
            "from_plugin": self._plugin_id
        }
        try:
            # 零拷贝优化:直接序列化
            raw = ormsgpack.packb(req)
        except Exception:
            return None
        try:
            # 零拷贝发送:copy=False 避免 ZMQ 内部拷贝,track=False 避免跟踪开销
            sock.send(raw, flags=0, copy=False, track=False)
        except Exception:
            return None
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            try:
                events = sock.poll(timeout=int(remaining * 1000), flags=zmq.POLLIN)
                if events == 0:
                    continue
            except Exception:
                return None
            try:
                # 零拷贝接收:copy=False 返回 Frame,需要转换为 bytes
                resp_frame = sock.recv(flags=0, copy=False)
                resp_raw = bytes(resp_frame)  # Frame to bytes
            except Exception:
                return None
            try:
                # 直接反序列化
                resp = ormsgpack.unpackb(resp_raw)
            except Exception:
                continue
            if isinstance(resp, dict):
                _req_id = resp.get("req_id")
                _v = resp.get("v")
                _ok = resp.get("ok")
                if _req_id == req_id and _v == 1 and isinstance(_ok, bool):
                    return resp
    
    def request(self, *, op: str, args: Dict[str, Any], timeout: float) -> Union[Optional[Dict[str, Any]], Coroutine[Any, Any, Optional[Dict[str, Any]]]]:
        """智能 RPC 请求:自动检测执行环境,选择同步或异步执行方式
        
        Returns:
            在事件循环中返回协程,否则返回结果字典
        """
        if self._is_in_event_loop():
            return self.request_async(op=op, args=args, timeout=timeout)
        return self.request_sync(op=op, args=args, timeout=timeout)
    
    async def batch_request_async(self, requests: List[Dict[str, Any]], *, timeout: float = 5.0) -> List[Optional[Dict[str, Any]]]:
        """异步批量请求
        
        Args:
            requests: List of {"op": str, "args": dict} requests
            timeout: Timeout for all requests
            
        Returns:
            List of responses (None for failed requests)
        """
        try:
            import zmq.asyncio
        except Exception:
            return [None] * len(requests)
        
        if not requests:
            return []
        
        sock = await self._get_async_sock()
        if sock is None:
            return [None] * len(requests)
        
        # Prepare all requests
        req_ids = []
        for i, req_data in enumerate(requests):
            req_id = self._next_req_id()
            req_ids.append(req_id)
            req = {
                "v": 1,
                "op": req_data.get("op", ""),
                "req_id": req_id,
                "args": req_data.get("args", {}),
                "from_plugin": self._plugin_id
            }
            
            try:
                # 零拷贝序列化
                raw = ormsgpack.packb(req)
            except Exception:
                continue
            
            try:
                # 零拷贝批量发送
                flags = zmq.SNDMORE if i < len(requests) - 1 else 0
                await sock.send(raw, flags=flags, copy=False, track=False)
            except Exception:
                pass
        
        # Collect responses
        responses: List[Optional[Dict[str, Any]]] = cast(List[Optional[Dict[str, Any]]], [None] * len(requests))
        deadline = asyncio.get_event_loop().time() + timeout
        received = set()
        
        while len(received) < len(req_ids):
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            
            try:
                events = await asyncio.wait_for(
                    sock.poll(timeout=int(remaining * 1000), flags=zmq.POLLIN),
                    timeout=remaining
                )
                if events == 0:
                    continue
            except asyncio.TimeoutError:
                break
            except Exception:
                break
            
            try:
                # 零拷贝接收:copy=False 返回 Frame,需要转换为 bytes
                resp_frame = await sock.recv(flags=0, copy=False)
                resp_raw = bytes(resp_frame)  # Frame to bytes
            except Exception:
                break
            
            try:
                # 直接反序列化
                resp = ormsgpack.unpackb(resp_raw)
            except Exception:
                continue
            
            if not isinstance(resp, dict):
                continue
            
            resp_id = resp.get("req_id")
            if resp_id in req_ids:
                idx = req_ids.index(resp_id)
                responses[idx] = resp
                received.add(resp_id)
        
        return responses


def format_rpc_error(err: Any) -> str:
    if err is None:
        return "message_plane error"
    if isinstance(err, str):
        return err
    if isinstance(err, dict):
        code = err.get("code")
        msg = err.get("message")
        if isinstance(code, str) and isinstance(msg, str):
            return f"{code}: {msg}" if code else msg
        if isinstance(msg, str):
            return msg
    try:
        return str(err)
    except Exception:
        return "message_plane error"
