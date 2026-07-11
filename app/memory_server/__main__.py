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

"""Standalone entry point: ``python -m app.memory_server``.

Body of the former ``if __name__ == "__main__":`` block of the monolithic
``app/memory_server.py`` (the launcher's merged mode imports the package and
mounts ``memory_server.app`` itself; this path is for standalone dev runs).
"""

import argparse
import os
import signal
import threading
import time

import uvicorn

from app import memory_server
from app.memory_server._shared import logger
from config import MEMORY_SERVER_PORT

if __name__ == "__main__":
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='Memory Server')
    parser.add_argument('--enable-shutdown', action='store_true',
                       help='启用响应退出请求功能（仅在终端用户环境使用）')
    args = parser.parse_args()

    # 设置全局变量（/shutdown 端点经 `global enable_shutdown` 读同一模块属性）
    memory_server.enable_shutdown = args.enable_shutdown

    # 创建一个后台线程来监控关闭信号
    def monitor_shutdown():
        while not memory_server.shutdown_event.is_set():
            time.sleep(0.1)
        logger.info("检测到关闭信号，正在关闭memory_server...")
        # 发送SIGTERM信号给当前进程
        os.kill(os.getpid(), signal.SIGTERM)

    # 只有在启用关闭功能时才启动监控线程
    if memory_server.enable_shutdown:
        shutdown_monitor = threading.Thread(target=monitor_shutdown, daemon=True)
        shutdown_monitor.start()

    # 启动服务器
    uvicorn.run(memory_server.app, host="127.0.0.1", port=MEMORY_SERVER_PORT)
