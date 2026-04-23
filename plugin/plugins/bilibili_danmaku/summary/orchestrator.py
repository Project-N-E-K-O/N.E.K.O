"""
摘要编排器

功能：
- 协调云端和本地摘要生成
- 处理降级和熔断
- 管理摘要生成流程
- 提供统一接口
"""

from __future__ import annotations

import time
import asyncio
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass

from ..aggregator import AggregatedEvent, create_event_id
from .local_engine import LocalEngine, SummaryResult, get_global_local_engine
from .circuit_breaker import CircuitBreaker, CircuitBreakerError, get_global_breaker_manager


@dataclass
class OrchestratorConfig:
    """编排器配置"""
    cloud_enabled: bool = True
    cloud_timeout: float = 10.0
    cloud_retry_times: int = 2
    cloud_retry_delay: float = 1.0
    
    local_enabled: bool = True
    local_fallback: bool = True
    
    circuit_breaker_enabled: bool = True
    circuit_breaker_name: str = "summary_cloud"
    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_reset_timeout: float = 300.0
    
    monitoring_enabled: bool = True


class CloudClient:
    """云端API客户端（模拟实现）"""
    
    def __init__(self, base_url: str = "", api_key: str = ""):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = 10.0
    
    async def summarize(
        self,
        events: List[AggregatedEvent],
        room_id: int,
        period_sec: int = 30
    ) -> SummaryResult:
        """
        调用云端API生成摘要（模拟实现）
        
        在实际项目中，这里应该调用真实的云端LLM API
        """
        # 模拟API调用延迟
        await asyncio.sleep(0.5)
        
        # 模拟API响应
        from .local_engine import LocalEngine
        local_engine = LocalEngine()
        
        # 使用本地引擎生成摘要（模拟云端API）
        result = await local_engine.summarize(events, room_id, period_sec)
        
        # 标记为云端生成
        result.metadata["engine"] = "cloud"
        result.metadata["api_version"] = "v1"
        
        return result
    
    async def test_connection(self) -> bool:
        """测试连接"""
        try:
            # 模拟连接测试
            await asyncio.sleep(0.1)
            return True
        except Exception:
            return False


class SummaryOrchestrator:
    """摘要编排器"""
    
    def __init__(
        self,
        config: Optional[OrchestratorConfig] = None,
        cloud_client: Optional[CloudClient] = None,
        local_engine: Optional[LocalEngine] = None,
        callback: Optional[Callable[[SummaryResult], Any]] = None
    ):
        """
        初始化编排器
        
        Args:
            config: 编排器配置
            cloud_client: 云端客户端
            local_engine: 本地引擎
            callback: 摘要生成完成后的回调函数
        """
        self.config = config or OrchestratorConfig()
        self.cloud_client = cloud_client
        self.local_engine = local_engine or get_global_local_engine()
        self.callback = callback
        
        # 熔断器
        self.circuit_breaker: Optional[CircuitBreaker] = None
        if self.config.circuit_breaker_enabled:
            breaker_manager = get_global_breaker_manager()
            self.circuit_breaker = asyncio.run(breaker_manager.get_breaker(
                name=self.config.circuit_breaker_name,
                failure_threshold=self.config.circuit_breaker_failure_threshold,
                reset_timeout=self.config.circuit_breaker_reset_timeout
            ))
        
        # 统计信息
        self.stats = {
            "total_requests": 0,
            "cloud_success": 0,
            "cloud_failure": 0,
            "local_fallback": 0,
            "circuit_breaker_trips": 0,
            "avg_processing_time": 0.0,
        }
        
        # 性能监控
        self.processing_times: List[float] = []
        
        # 异步锁
        self._lock = asyncio.Lock()
    
    async def generate_summary(
        self,
        events: List[AggregatedEvent],
        room_id: int,
        period_sec: int = 30
    ) -> Optional[SummaryResult]:
        """
        生成摘要
        
        Args:
            events: 聚合事件列表
            room_id: 直播间ID
            period_sec: 聚合周期（秒）
        
        Returns:
            SummaryResult: 摘要结果，失败时返回None
        """
        start_time = time.time()
        self.stats["total_requests"] += 1
        
        try:
            result = None
            
            # 1. 尝试云端生成
            if self.config.cloud_enabled and self.cloud_client:
                result = await self._try_cloud_generation(events, room_id, period_sec)
            
            # 2. 降级到本地生成
            if result is None and self.config.local_enabled:
                result = await self._fallback_to_local(events, room_id, period_sec)
            
            # 3. 处理结果
            if result:
                await self._handle_result(result)
            
            # 4. 更新统计
            processing_time = time.time() - start_time
            await self._update_stats(processing_time, result)
            
            return result
            
        except Exception as e:
            print(f"摘要生成失败: {e}")
            return None
    
    async def _try_cloud_generation(
        self,
        events: List[AggregatedEvent],
        room_id: int,
        period_sec: int
    ) -> Optional[SummaryResult]:
        """尝试云端生成"""
        if not self.cloud_client:
            return None
        
        # 检查熔断器
        if self.circuit_breaker and self.circuit_breaker.is_open():
            print(f"[Orchestrator] 熔断器打开，跳过云端生成")
            return None
        
        try:
            # 使用熔断器执行
            if self.circuit_breaker:
                result = await self.circuit_breaker.execute(
                    self._call_cloud_api,
                    events, room_id, period_sec
                )
            else:
                result = await self._call_cloud_api(events, room_id, period_sec)
            
            self.stats["cloud_success"] += 1
            return result
            
        except CircuitBreakerError as e:
            print(f"[Orchestrator] 熔断器阻止请求: {e}")
            self.stats["circuit_breaker_trips"] += 1
            return None
            
        except Exception as e:
            print(f"[Orchestrator] 云端生成失败: {e}")
            self.stats["cloud_failure"] += 1
            return None
    
    async def _call_cloud_api(
        self,
        events: List[AggregatedEvent],
        room_id: int,
        period_sec: int
    ) -> SummaryResult:
        """调用云端API"""
        # 设置超时
        try:
            result = await asyncio.wait_for(
                self.cloud_client.summarize(events, room_id, period_sec),
                timeout=self.config.cloud_timeout
            )
            return result
        except asyncio.TimeoutError:
            raise Exception(f"云端API超时 ({self.config.cloud_timeout}s)")
    
    async def _fallback_to_local(
        self,
        events: List[AggregatedEvent],
        room_id: int,
        period_sec: int
    ) -> SummaryResult:
        """降级到本地生成"""
        print(f"[Orchestrator] 降级到本地规则引擎")
        
        try:
            result = await self.local_engine.summarize(events, room_id, period_sec)
            result.metadata["is_fallback"] = True
            
            self.stats["local_fallback"] += 1
            return result
            
        except Exception as e:
            print(f"[Orchestrator] 本地生成失败: {e}")
            raise
    
    async def _handle_result(self, result: SummaryResult) -> None:
        """处理生成结果"""
        # 调用回调函数
        if self.callback:
            try:
                await self.callback(result)
            except Exception as e:
                print(f"[Orchestrator] 回调函数失败: {e}")
        
        # 记录日志
        if self.config.monitoring_enabled:
            self._log_result(result)
    
    def _log_result(self, result: SummaryResult) -> None:
        """记录结果日志"""
        metadata = result.metadata
        engine = metadata.get("engine", "unknown")
        is_fallback = metadata.get("is_fallback", False)
        
        log_msg = f"[Summary] 生成成功: engine={engine}"
        if is_fallback:
            log_msg += " (fallback)"
        
        log_msg += f", events={metadata.get('event_count', 0)}"
        log_msg += f", priority={result.priority}"
        
        print(log_msg)
    
    async def _update_stats(self, processing_time: float, result: Optional[SummaryResult]) -> None:
        """更新统计信息"""
        async with self._lock:
            # 记录处理时间
            self.processing_times.append(processing_time)
            if len(self.processing_times) > 100:
                self.processing_times.pop(0)
            
            # 计算平均处理时间
            if self.processing_times:
                self.stats["avg_processing_time"] = sum(self.processing_times) / len(self.processing_times)
    
    async def get_status(self) -> Dict[str, Any]:
        """获取编排器状态"""
        breaker_status = None
        if self.circuit_breaker:
            breaker_status = self.circuit_breaker.get_status()
        
        cloud_available = False
        if self.cloud_client:
            cloud_available = await self.cloud_client.test_connection()
        
        return {
            "config": {
                "cloud_enabled": self.config.cloud_enabled,
                "local_enabled": self.config.local_enabled,
                "circuit_breaker_enabled": self.config.circuit_breaker_enabled,
            },
            "cloud_available": cloud_available,
            "circuit_breaker": breaker_status,
            "stats": self.stats.copy(),
            "local_engine_ready": self.local_engine is not None,
        }
    
    async def force_local_mode(self) -> None:
        """强制使用本地模式"""
        self.config.cloud_enabled = False
        print("[Orchestrator] 已切换到纯本地模式")
    
    async def force_cloud_mode(self) -> None:
        """强制使用云端模式"""
        self.config.cloud_enabled = True
        self.config.local_fallback = False
        print("[Orchestrator] 已切换到纯云端模式")
    
    async def reset_circuit_breaker(self) -> None:
        """重置熔断器"""
        if self.circuit_breaker:
            self.circuit_breaker.force_reset()
            print("[Orchestrator] 熔断器已重置")
    
    async def shutdown(self) -> None:
        """关闭编排器"""
        # 保存统计信息等清理工作
        print(f"[Orchestrator] 关闭，共处理 {self.stats['total_requests']} 次请求")


# 全局编排器实例
_global_orchestrator: Optional[SummaryOrchestrator] = None

def get_global_orchestrator(
    config: Optional[OrchestratorConfig] = None,
    cloud_client: Optional[CloudClient] = None,
    local_engine: Optional[LocalEngine] = None,
    callback: Optional[Callable[[SummaryResult], Any]] = None
) -> SummaryOrchestrator:
    """获取全局编排器实例"""
    global _global_orchestrator
    
    if _global_orchestrator is None:
        _global_orchestrator = SummaryOrchestrator(
            config=config,
            cloud_client=cloud_client,
            local_engine=local_engine,
            callback=callback
        )
    
    return _global_orchestrator


# 简单的回调函数示例
async def default_summary_callback(result: SummaryResult) -> None:
    """默认摘要回调函数"""
    # 这里可以将摘要推送到AI
    # 在实际集成中，这里会调用 push_message 等接口
    print(f"[Callback] 收到摘要: {result.summary_text[:100]}...")