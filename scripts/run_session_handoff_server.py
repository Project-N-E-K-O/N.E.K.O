"""Run the real backend with content-free lifecycle/resource sampling.

Use ``uv run python scripts/run_session_handoff_server.py --help``. This is an
opt-in acceptance entry point, never imported by the production server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import threading
import time


def process_resources():
    import psutil
    process = psutil.Process()
    try:
        connections = process.net_connections(kind="inet")
        established = sum(connection.status == "ESTABLISHED" for connection in connections)
    except (psutil.AccessDenied, psutil.Error):
        established = None
    return {
        "threads": process.num_threads(),
        "python_threads": threading.active_count(),
        "established_network_connections": established,
        "rss_bytes": process.memory_info().rss,
    }


async def sample_resources(server, path, interval):
    from app.main_server.character_runtime import _iter_session_managers
    def append(row):
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    while not server.should_exit:
        managers = []
        for ordinal, (_, manager) in enumerate(_iter_session_managers()):
            operation = getattr(manager, "_start_operation", None)
            connections = getattr(manager, "_connection_records", ())
            retirements = getattr(manager, "_session_retirements", ())
            runtimes = getattr(manager, "_tts_runtimes", ())
            managers.append({
                "manager": ordinal,
                "request_id": getattr(operation, "request_id", None),
                "generation": getattr(operation, "generation", None),
                "operation_valid": getattr(operation, "valid", None),
                "starting": bool(manager.is_starting),
                "active": bool(manager.is_active),
                "session_ready": bool(manager.session_ready),
                "tts_ready": bool(manager.tts_ready),
                "llm_registered_live": sum(not item.closed for item in connections),
                "llm_connecting": sum(item.connect_task is not None and not item.connect_task.done()
                                      for item in connections),
                "llm_retired_live": sum(item.retired and not item.closed for item in connections),
                "tts_live_workers": sum(bool(item.thread and item.thread.is_alive()) for item in runtimes),
                "tts_retired_live": sum(bool(item.retired and item.thread and item.thread.is_alive())
                                        for item in runtimes),
                "handoff_pending": sum(not item.handoff_safe.is_set() for item in retirements),
                "cleanup_pending": sum(not item.cleanup_complete.is_set() for item in retirements),
            })
        row = {"unix_ms": int(time.time() * 1000), "monotonic": time.monotonic(),
               "pid": os.getpid(), "managers": managers,
               "process": await asyncio.to_thread(process_resources)}
        await asyncio.to_thread(append, row)
        await asyncio.sleep(interval)


async def serve(args):
    import uvicorn
    from app.main_server import app, set_start_config
    from config import MAIN_SERVER_PORT
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=MAIN_SERVER_PORT,
                                          ws_ping_interval=20, ws_ping_timeout=60))
    set_start_config({"browser_mode_enabled": False, "browser_page": "",
                      "shutdown_memory_server_on_exit": False, "server": server})
    sampler = asyncio.create_task(sample_resources(server, args.output, args.interval))
    try:
        await server.serve()
    finally:
        sampler.cancel()
        await asyncio.gather(sampler, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/session-handoff-lifecycle/resources.jsonl"))
    parser.add_argument("--data-root", type=Path, help="Optional isolated storage root, configured before imports.")
    parser.add_argument("--interval", type=float, default=0.1, help="Sampling resolution in seconds (default 0.1).")
    args = parser.parse_args()
    if args.interval < 0.05:
        parser.error("--interval must be at least 0.05 seconds")
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    if args.data_root:
        data_root = str(args.data_root.resolve())
        os.environ["NEKO_STORAGE_SELECTED_ROOT"] = data_root
        os.environ["NEKO_STORAGE_ANCHOR_ROOT"] = data_root
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(serve(args))


if __name__ == "__main__":
    main()
