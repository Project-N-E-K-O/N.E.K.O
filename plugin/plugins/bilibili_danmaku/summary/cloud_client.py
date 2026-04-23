"""
云端API客户端（占位符实现）

在实际部署中，这里应该实现真实的云端LLM API调用。
当前为占位符实现，使用本地引擎模拟云端API。
"""

from __future__ import annotations

import asyncio
import json
from typing import Dict, List, Optional, Any
import aiohttp

from ..aggregator import AggregatedEvent
from .local_engine import SummaryResult


class CloudClient:
    """云端API客户端"""
    
    def __init__(
        self,
        base_url: str = "https://api.company.internal/v1",
        api_key: str = "",
        timeout: float = 10.0,
        max_retries: int = 2
    ):
        """
        初始化云端客户端
        
        Args:
            base_url: API基础URL
            api_key: API密钥
            timeout: 请求超时时间（秒）
            max_retries: 最大重试次数
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        
        # 会话管理
        self._session: Optional[aiohttp.ClientSession] = None
        
        # 端点
        self.summary_endpoint = f"{self.base_url}/summary"
        self.health_endpoint = f"{self.base_url}/health"
    
    async def _ensure_session(self) -> None:
        """确保会话存在"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "BilibiliDanmakuPlugin/1.0"
                }
            )
    
    async def summarize(
        self,
        events: List[AggregatedEvent],
        room_id: int,
        period_sec: int = 30
    ) -> SummaryResult:
        """
        调用云端API生成摘要
        
        注意：这是占位符实现，实际应该调用真实的云端API。
        当前使用本地引擎模拟，便于开发和测试。
        """
        # TODO: 实现真实的云端API调用
        # 当前使用本地引擎模拟
        
        from .local_engine import LocalEngine
        local_engine = LocalEngine()
        
        # 模拟API调用延迟
        await asyncio.sleep(0.3)
        
        # 使用本地引擎生成摘要
        result = await local_engine.summarize(events, room_id, period_sec)
        
        # 标记为云端生成（模拟）
        result.metadata.update({
            "engine": "cloud",
            "api_version": "v1",
            "cloud_provider": "company_llm",
            "is_mock": True  # 标记为模拟数据
        })
        
        return result
    
    async def summarize_real(
        self,
        events: List[AggregatedEvent],
        room_id: int,
        period_sec: int = 30
    ) -> SummaryResult:
        """
        真实的云端API调用（示例代码）
        
        在实际部署中取消注释并实现此方法
        """
        """
        await self._ensure_session()
        
        # 准备请求数据
        request_data = {
            "room_id": room_id,
            "period_sec": period_sec,
            "events": self._serialize_events(events),
            "timestamp": time.time(),
            "metadata": {
                "version": "1.0",
                "source": "bilibili_danmaku_plugin"
            }
        }
        
        # 重试逻辑
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                async with self._session.post(
                    self.summary_endpoint,
                    json=request_data,
                    headers={"X-Request-ID": f"req_{int(time.time())}_{attempt}"}
                ) as response:
                    
                    if response.status == 200:
                        data = await response.json()
                        return self._parse_response(data, room_id, period_sec)
                    
                    elif response.status == 429:
                        # 限流，等待后重试
                        retry_after = int(response.headers.get('Retry-After', 1))
                        await asyncio.sleep(retry_after)
                        continue
                    
                    else:
                        last_error = f"HTTP {response.status}: {await response.text()}"
                        
            except asyncio.TimeoutError:
                last_error = f"请求超时 ({self.timeout}s)"
            except aiohttp.ClientError as e:
                last_error = f"网络错误: {e}"
            except Exception as e:
                last_error = f"未知错误: {e}"
            
            # 重试前等待
            if attempt < self.max_retries:
                await asyncio.sleep(1 * (attempt + 1))
        
        raise Exception(f"云端API调用失败: {last_error}")
        """
        
        # 当前返回模拟数据
        return await self.summarize(events, room_id, period_sec)
    
    def _serialize_events(self, events: List[AggregatedEvent]) -> List[Dict[str, Any]]:
        """序列化事件列表"""
        serialized = []
        for event in events:
            data = {
                "type": event.type,
                "uid": event.uid,
                "name": event.name,
                "content": event.content,
                "gift_name": event.gift_name,
                "gift_num": event.gift_num,
                "battery": event.battery,
                "price": event.price,
                "user_level": event.user_level,
                "medal_level": event.medal_level,
                "medal_name": event.medal_name,
                "guard_level": event.guard_level,
                "is_vip": event.is_vip,
                "priority": event.priority,
                "timestamp": event.timestamp,
                "rmb_value": event.rmb_value,
            }
            serialized.append(data)
        return serialized
    
    def _parse_response(
        self,
        data: Dict[str, Any],
        room_id: int,
        period_sec: int
    ) -> SummaryResult:
        """解析API响应"""
        from .local_engine import SummaryResult
        
        return SummaryResult(
            summary_text=data.get("summary_text", ""),
            highlights=data.get("highlights", []),
            topics=data.get("topics", []),
            suggestions=data.get("suggestions", []),
            priority=data.get("priority", 5),
            metadata={
                "room_id": room_id,
                "period_sec": period_sec,
                "engine": "cloud",
                "api_version": data.get("api_version", "v1"),
                "response_id": data.get("response_id"),
                "processing_time_ms": data.get("processing_time_ms", 0),
            }
        )
    
    async def test_connection(self) -> bool:
        """测试连接"""
        try:
            await self._ensure_session()
            
            # 简单的健康检查
            async with self._session.get(
                self.health_endpoint,
                timeout=5.0
            ) as response:
                return response.status == 200
                
        except Exception:
            return False
    
    async def get_api_info(self) -> Dict[str, Any]:
        """获取API信息"""
        try:
            await self._ensure_session()
            
            async with self._session.get(
                f"{self.base_url}/info",
                timeout=5.0
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    return {"error": f"HTTP {response.status}"}
                    
        except Exception as e:
            return {"error": str(e)}
    
    async def close(self) -> None:
        """关闭客户端"""
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def __aenter__(self):
        await self._ensure_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


# 全局云端客户端实例
_global_cloud_client: Optional[CloudClient] = None

def get_global_cloud_client(
    base_url: str = "",
    api_key: str = ""
) -> CloudClient:
    """获取全局云端客户端实例"""
    global _global_cloud_client
    
    if _global_cloud_client is None:
        # 从环境变量或配置文件中读取
        import os
        actual_base_url = base_url or os.getenv("LLM_API_BASE_URL", "https://api.company.internal/v1")
        actual_api_key = api_key or os.getenv("LLM_API_KEY", "")
        
        _global_cloud_client = CloudClient(
            base_url=actual_base_url,
            api_key=actual_api_key
        )
    
    return _global_cloud_client