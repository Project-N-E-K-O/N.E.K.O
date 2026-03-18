# -*- coding: utf-8 -*-
"""
Telemetry Server — 安全模块

1. HMAC-SHA256 签名验证（防篡改）—— 秘钥硬编码（与 vLLM 一致）
2. 时间戳窗口验证（防重放）
3. 基于设备的速率限制（防滥用）
"""
from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict
from threading import Lock

# ---------------------------------------------------------------------------
# ★ 与客户端 token_tracker.py 中的 _TELEMETRY_HMAC_SECRET 保持一致
# ---------------------------------------------------------------------------
DEFAULT_HMAC_SECRET = "neko-v1-a3f8b2c1d4e5f6789012345678abcdef"


def compute_signature(payload_json: str, timestamp: float, secret: str = DEFAULT_HMAC_SECRET) -> str:
    """计算 HMAC-SHA256(secret, f"{timestamp}|{sha256(payload_json)}")。"""
    body_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    message = f"{timestamp}|{body_hash}"
    return hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(
    payload_json: str,
    timestamp: float,
    signature: str,
    secret: str = DEFAULT_HMAC_SECRET,
) -> bool:
    """验证签名（常量时间比较，防时序攻击）。"""
    expected = compute_signature(payload_json, timestamp, secret)
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# 时间戳窗口
# ---------------------------------------------------------------------------

TIMESTAMP_TOLERANCE = 300  # ±5 分钟


def verify_timestamp(timestamp: float, tolerance: float = TIMESTAMP_TOLERANCE) -> bool:
    """拒绝超过 ±tolerance 秒的请求（防重放）。"""
    return abs(time.time() - timestamp) <= tolerance


# ---------------------------------------------------------------------------
# 速率限制（滑动窗口，per-device，内存存储）
# ---------------------------------------------------------------------------

class RateLimiter:
    """每个 device_id 在 window 秒内最多 max_requests 次请求。"""

    def __init__(self, max_requests: int = 60, window: float = 3600.0):
        self.max_requests = max_requests
        self.window = window
        self._records: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def is_allowed(self, device_id: str) -> bool:
        now = time.time()
        cutoff = now - self.window
        with self._lock:
            ts_list = self._records[device_id]
            self._records[device_id] = [t for t in ts_list if t > cutoff]
            if len(self._records[device_id]) >= self.max_requests:
                return False
            self._records[device_id].append(now)
            return True

    def cleanup_stale(self, max_age: float = 86400.0):
        """清理长期不活跃的设备记录（防内存膨胀）。"""
        cutoff = time.time() - max_age
        with self._lock:
            stale = [k for k, v in self._records.items() if not v or v[-1] < cutoff]
            for k in stale:
                del self._records[k]
