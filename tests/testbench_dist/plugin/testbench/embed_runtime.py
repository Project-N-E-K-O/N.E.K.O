"""Load Testbench uvicorn stack after path_bootstrap (dynamic imports for ruff CI)."""
from __future__ import annotations

from typing import Any


def load_testbench_app_stack() -> tuple[Any, Any, Any]:
    uvicorn = __import__("uvicorn")
    live_runtime_log = __import__(
        "tests.testbench.pipeline.live_runtime_log",
        fromlist=["live_runtime_log"],
    ).live_runtime_log
    app = __import__("tests.testbench.server", fromlist=["app"]).app
    return uvicorn, live_runtime_log, app
