"""
摘要生成模块包

包含：
- 本地规则引擎（LocalEngine）
- 云端API客户端（CloudClient）
- 熔断器（CircuitBreaker）
- 摘要编排器（SummaryOrchestrator）
"""

from .local_engine import LocalEngine, SummaryResult
from .cloud_client import CloudClient
from .circuit_breaker import CircuitBreaker, CircuitBreakerError
from .orchestrator import SummaryOrchestrator, OrchestratorConfig

__all__ = [
    "LocalEngine",
    "SummaryResult",
    "CloudClient",
    "CircuitBreaker",
    "CircuitBreakerError", 
    "SummaryOrchestrator",
    "OrchestratorConfig"
]