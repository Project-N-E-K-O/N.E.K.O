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
SSL environment preflight and diagnostics file output utility.

Goals:
1) quickly identify Windows certificate store anomalies at startup (e.g. ASN1 nested asn1 error)
2) produce a structured evidence file for user reports and root-cause analysis
"""

from __future__ import annotations

import os
import platform
import ssl
import traceback
from datetime import datetime

from utils.file_utils import atomic_write_json


def probe_ssl_environment() -> dict:
    """Run the SSL preflight; returns a structured result."""
    result = {
        "ok": True,
        "error_type": None,
        "error_message": None,
        "is_windows": platform.system().lower() == "windows",
        "is_asn1_nested_error": False,
    }
    try:
        # create_default_context 会触发默认信任链加载
        # 在部分 Windows 环境会于此处抛出 ASN1 相关异常
        ssl.create_default_context()
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        result["ok"] = False
        result["error_type"] = type(e).__name__
        result["error_message"] = msg
        lowered = msg.lower()
        result["is_asn1_nested_error"] = (
            "nested asn1 error" in lowered or "[asn1]" in lowered
        )
    return result


def write_ssl_diagnostic(
    event: str,
    output_dir: str,
    error: Exception | None = None,
    extra: dict | None = None,
) -> str | None:
    """Write SSL-related diagnostics to a JSON file; returns the file path."""
    try:
        os.makedirs(output_dir, exist_ok=True)
        payload = {
            "timestamp": datetime.now().isoformat(),
            "event": event,
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "openssl_version": getattr(ssl, "OPENSSL_VERSION", ""),
            "extra": extra or {},
        }
        if error is not None:
            payload["error"] = {
                "type": type(error).__name__,
                "message": str(error),
                "traceback": "".join(
                    traceback.format_exception(type(error), error, error.__traceback__)
                ),
            }
        filename = f"ssl_diagnostic_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
        path = os.path.join(output_dir, filename)
        atomic_write_json(path, payload, ensure_ascii=False, indent=2)
        return path
    except Exception:
        return None
