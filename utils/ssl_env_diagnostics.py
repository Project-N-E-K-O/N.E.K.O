# -*- coding: utf-8 -*-
"""
SSL 环境预检与诊断文件输出工具。

目标：
1) 启动阶段快速识别 Windows 证书库异常（如 ASN1 nested asn1 error）
2) 生成结构化证据文件，便于用户上报与问题归因
"""

from __future__ import annotations

import json
import os
import platform
import ssl
import traceback
from datetime import datetime


def probe_ssl_environment() -> dict:
    """执行 SSL 预检，返回结构化结果。"""
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
    """将 SSL 相关诊断写入 JSON 文件，返回文件路径。"""
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
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path
    except Exception:
        return None
