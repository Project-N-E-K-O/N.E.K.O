# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Telemetry Server — security module

1. HMAC-SHA256 signature verification (anti-tampering) — hard-coded secret (same as vLLM)
2. Timestamp window verification (anti-replay)
3. Per-device rate limiting (anti-abuse)
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections import defaultdict
from threading import Lock

# ---------------------------------------------------------------------------
# ★ Keep consistent with _TELEMETRY_HMAC_SECRET in client token_tracker.py.
# This soft signature deters casual spoofing, not determined attackers: the key is necessarily embedded in distributed clients
# and cannot resist reverse engineering. It only blocks easy forgery and dirty data, so retain the matching hardcoded
# default for immediate usability, allowing operators to rotate via NEKO_TELEMETRY_HMAC_SECRET.
# ---------------------------------------------------------------------------
DEFAULT_HMAC_SECRET = os.environ.get("NEKO_TELEMETRY_HMAC_SECRET") or "neko-v1-a3f8b2c1d4e5f6789012345678abcdef"


def compute_signature(payload_json: str, timestamp: float, secret: str = DEFAULT_HMAC_SECRET) -> str:
    """Compute HMAC-SHA256(secret, f"{timestamp}|{sha256(payload_json)}")."""
    if not secret:
        raise ValueError("HMAC secret is not configured (set NEKO_TELEMETRY_HMAC_SECRET)")
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
    """Verify the signature (constant-time comparison, guards against timing attacks)."""
    # Refuse verification when no secret is configured — fail closed so a
    # misconfigured deployment rejects unsigned traffic rather than accepting it.
    if not secret:
        return False
    # hmac.compare_digest raises TypeError on str containing non-ASCII; a forged
    # request with a non-ASCII signature would crash verify into a 500 instead of
    # a clean 403. The signature is always a hex digest (pure ASCII), so any
    # non-ASCII value is necessarily invalid — reject it early.
    if not isinstance(signature, str) or not signature.isascii():
        return False
    expected = compute_signature(payload_json, timestamp, secret)
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Timestamp window.
# ---------------------------------------------------------------------------

TIMESTAMP_TOLERANCE = 300  # ±5 minutes.


def verify_timestamp(timestamp: float, tolerance: float = TIMESTAMP_TOLERANCE) -> bool:
    """Reject requests older/newer than ±tolerance seconds (anti-replay)."""
    return abs(time.time() - timestamp) <= tolerance


# ---------------------------------------------------------------------------
# Rate limiting (sliding window, per device, in-memory storage).
# ---------------------------------------------------------------------------

class RateLimiter:
    """At most max_requests per device_id within a window of `window` seconds."""

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
        """Clean up long-inactive device records (prevents memory bloat)."""
        cutoff = time.time() - max_age
        with self._lock:
            stale = [k for k, v in self._records.items() if not v or v[-1] < cutoff]
            for k in stale:
                del self._records[k]
