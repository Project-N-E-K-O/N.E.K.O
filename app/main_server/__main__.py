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

"""Run the main FastAPI server as ``python -m app.main_server``."""

import asyncio
import logging
import os

from config import MAIN_SERVER_PORT

from . import (
    _get_port_owners,
    _is_port_available,
    app,
    cleanup,
    get_start_config,
    logger,
    set_start_config,
)

if __name__ == "__main__":
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--open-browser", action="store_true", help="启动后是否打开浏览器并监控它"
    )
    parser.add_argument(
        "--page",
        type=str,
        default="",
        choices=["index", "character_card_manager", "api_key", ""],
        help="要打开的页面路由（不含域名和端口）",
    )
    args = parser.parse_args()

    logger.info("--- Starting FastAPI Server ---")
    # 使用 os.path.abspath 输出更清晰的完整路径
    logger.info(f"Serving static files from: {os.path.abspath('static')}")
    logger.info(f"Serving index.html from: {os.path.abspath('templates/index.html')}")
    logger.info(
        f"Access UI at: http://127.0.0.1:{MAIN_SERVER_PORT} (or your network IP:{MAIN_SERVER_PORT})"
    )
    logger.info("-----------------------------")

    # ── 前端构建产物检测 ──────────────────────────────────────
    _frontend_missing = []
    if not os.path.isfile("frontend/plugin-manager/dist/index.html"):
        _frontend_missing.append(
            "plugin-manager  (frontend/plugin-manager/dist/index.html)"
        )
    if not os.path.isfile("static/react/neko-chat/neko-chat-window.iife.js"):
        _frontend_missing.append(
            "react-neko-chat  (static/react/neko-chat/neko-chat-window.iife.js)"
        )
    if _frontend_missing:
        _bar = "!" * 60
        _msg = f"\n{_bar}\n{_bar}\n!!  WARNING: 前端资源未构建，以下模块缺失:\n"
        for _m in _frontend_missing:
            _msg += f"!!    - {_m}\n"
        _msg += (
            f"!!\n"
            f"!!  请先运行构建脚本:\n"
            f"!!    Windows:  .\\build_frontend.bat\n"
            f"!!    Linux:    ./build_frontend.sh\n"
            f"!!\n"
            f"!!  否则部分页面将无法正常显示！\n"
            f"{_bar}\n{_bar}\n"
        )
        print(_msg, flush=True)
        logger.warning(
            "前端资源未构建，部分页面将无法正常显示！请运行 build_frontend.sh / build_frontend.bat"
        )

    # 使用统一的速率限制日志过滤器
    from utils.logger_config import create_main_server_filter, create_httpx_filter

    # 为 uvicorn access 日志添加过滤器
    logging.getLogger("uvicorn.access").addFilter(create_main_server_filter())

    # 为 httpx 日志添加可用性检查过滤器
    logging.getLogger("httpx").addFilter(create_httpx_filter())

    # 启动前预检端口，避免 uvicorn 启动后立刻退出且日志不明显
    if not _is_port_available(MAIN_SERVER_PORT):
        owner_pids = _get_port_owners(MAIN_SERVER_PORT)
        owner_hint = f"，占用PID: {owner_pids}" if owner_pids else ""
        logger.error(f"启动失败：端口 {MAIN_SERVER_PORT} 已被占用{owner_hint}")
        raise SystemExit(1)

    # 1) 配置 UVicorn
    _behind_proxy = os.environ.get("NEKO_BEHIND_PROXY", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=MAIN_SERVER_PORT,
        log_level="info",
        loop="asyncio",
        reload=False,
        proxy_headers=_behind_proxy,
        forwarded_allow_ips="*" if _behind_proxy else None,
        # WebSocket keep-alive: send server-initiated pings every 20s, close if no pong within 60s
        ws_ping_interval=20.0,
        ws_ping_timeout=60.0,
    )
    server = uvicorn.Server(config)

    # Set browser mode flag if --open-browser is used
    if args.open_browser:
        # 使用 FastAPI 的 app.state 来管理配置
        start_config = {
            "browser_mode_enabled": True,
            "browser_page": args.page if args.page != "index" else "",
            "shutdown_memory_server_on_exit": False,
            "server": server,
        }
        set_start_config(start_config)
    else:
        # 设置默认配置
        start_config = {
            "browser_mode_enabled": False,
            "browser_page": "",
            "shutdown_memory_server_on_exit": False,
            "server": server,
        }
        set_start_config(start_config)

    print(f"启动配置: {get_start_config()}")

    # 2) 信号处理：Ctrl+C 时快速关闭
    #    uvicorn 的 install_signal_handlers() 会用 signal.signal(sig, self.handle_exit)
    #    覆盖我们直接注册的信号处理器。所以这里 monkey-patch server.handle_exit，
    #    这样无论 uvicorn 何时安装信号处理器，最终调用的都是我们的逻辑。
    _shutdown_state = {"signal_count": 0}
    _original_handle_exit = server.handle_exit

    def _custom_handle_exit(sig, frame):
        _shutdown_state["signal_count"] += 1
        if _shutdown_state["signal_count"] > 1:
            logger.warning("收到第二次关闭信号, 立即强制退出.")
            cleanup()
            os._exit(130)
        logger.info("正在关闭服务器...")
        cleanup()
        _original_handle_exit(sig, frame)

    server.handle_exit = _custom_handle_exit

    # 4) 启动服务器（阻塞，直到 server.should_exit=True）
    logger.info("--- Starting FastAPI Server ---")
    logger.info(f"Access UI at: http://127.0.0.1:{MAIN_SERVER_PORT}/{args.page}")

    try:
        server.run()
    except KeyboardInterrupt:
        # Ctrl+C 正常关闭，不显示 traceback
        logger.info("收到关闭信号（Ctrl+C），正在关闭服务器...")
    except (asyncio.CancelledError, SystemExit):
        # 正常的关闭信号
        pass
    except Exception as e:
        # 真正的错误，显示完整 traceback
        logger.error(f"服务器运行时发生错误: {e}", exc_info=True)
        raise
    finally:
        logger.info("服务器已关闭")
