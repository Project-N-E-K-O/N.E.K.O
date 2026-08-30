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

"""Direct-run entry point: ``python -m app.agent_server``.

Replaces the former ``python app/agent_server.py`` invocation of the
monolithic module.
"""

import os

from config import TOOL_SERVER_PORT

from app.agent_server import app

if __name__ == "__main__":
    import uvicorn
    import logging  # 仍需要用于uvicorn的过滤器
    
    # 使用统一的速率限制日志过滤器
    from utils.logger_config import create_agent_server_filter
    
    # Add filter to uvicorn access logger (uvicorn仍使用标准logging)
    logging.getLogger("uvicorn.access").addFilter(create_agent_server_filter())
    
    _behind_proxy = os.environ.get("NEKO_BEHIND_PROXY", "").strip().lower() in ("1", "true", "yes")
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=TOOL_SERVER_PORT,
        proxy_headers=_behind_proxy,
        forwarded_allow_ips="*" if _behind_proxy else None,
    )
