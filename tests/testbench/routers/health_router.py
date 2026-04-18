"""Health check and system info endpoints.

Later phases (P20) will extend this router with ``/system/paths`` and
``/system/open_path``. For now it only exposes boot-time diagnostics.
"""
from __future__ import annotations

from fastapi import APIRouter

from tests.testbench import config as tb_config

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict:
    """Basic liveness probe. Returns ``{"status": "ok"}``."""
    return {"status": "ok"}


@router.get("/version")
async def version() -> dict:
    """Static version metadata for the testbench UI."""
    return {
        "name": "N.E.K.O. Testbench",
        "version": "0.1.0",
        "phase": "P09",
        "host": tb_config.DEFAULT_HOST,
        "port": tb_config.DEFAULT_PORT,
    }
