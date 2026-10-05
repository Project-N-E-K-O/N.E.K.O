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

"""Catgirl visit HTTP / WebSocket routers (docs/design/visit-infrastructure.md §4.6, §5 PR-07 / PR-08 / PR-09a).

Sub-modules declare ``APIRouter()`` without a prefix and decorate RELATIVE
paths; the package router (``prefix='/api/visit'``) that includes them is
added by PR-09a. Until then nothing here is mounted on the app.
"""
