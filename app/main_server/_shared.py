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

"""Shared runtime references for main-server sibling modules."""

from typing import Any, Callable


class RuntimeReferences:
    """References initialized by the ordered package facade."""

    logger: Any = None
    config_manager: Any = None
    app: Any = None
    is_main_process: bool = False
    server_loop: Any = None
    get_app_root: Callable[[], str] | None = None
    resolve_user_plugin_base: Callable[[], str] | None = None
    get_start_config: Callable[[], dict] | None = None
    shutdown_server_async: Callable[[], Any] | None = None
    request_application_shutdown_async: Callable[..., Any] | None = None


runtime = RuntimeReferences()
