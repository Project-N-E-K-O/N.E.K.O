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

from urllib.parse import quote, unquote


def encode_url_path(path: str) -> str:
    """
    Safely encode URL path segments, avoiding spaces/special characters breaking static resource loading.
    Encodes only the path segments themselves, preserving the '/' separator structure.
    """
    if not path:
        return path

    parts = path.split('/')
    encoded_parts = [quote(unquote(part), safe='') for part in parts]
    return '/'.join(encoded_parts)
