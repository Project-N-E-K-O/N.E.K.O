"""Health check and system info endpoints.

Later phases (P20) will extend this router with ``/system/paths`` and
``/system/open_path``. For now it only exposes boot-time diagnostics.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter

from tests.testbench import config as tb_config

router = APIRouter(tags=["health"])

# 进程启动时生成一次. 前端用它判断"服务是否新启动了" — 例如 welcome 横幅
# 想每次重启都再提醒一次: 比较上次看过时存的 boot_id 和现在返回的, 不一致
# 就当作"新一轮启动"重置 LS 里的 seen flag. 格式用 UUID 避免任何"按时间
# 推测前后" 类错觉 (比如同秒内连重启不会误判为同一次).
BOOT_ID = uuid.uuid4().hex


@router.get("/healthz")
async def healthz() -> dict:
    """Basic liveness probe. Returns ``{"status": "ok", "boot_id": ...}``.

    ``boot_id`` 每次进程启动生成一次, 前端用来检测服务重启.
    """
    return {"status": "ok", "boot_id": BOOT_ID}


@router.get("/version")
async def version() -> dict:
    """Static version metadata for the testbench UI."""
    return {
        "name": "N.E.K.O. Testbench",
        "version": "0.1.0",
        "phase": "P14",
        "host": tb_config.DEFAULT_HOST,
        "port": tb_config.DEFAULT_PORT,
        "boot_id": BOOT_ID,
    }
