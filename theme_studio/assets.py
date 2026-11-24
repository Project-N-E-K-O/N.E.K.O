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

"""Disk fingerprints of the Theme Studio loader assets.

Used for two things: the ``?v=`` cache versions in templates and the
``css_sha8`` / ``loader_version`` fields the service attaches to theme.json.
Templates reference the loader as two plain static files:

.. code-block:: html

    <link rel="stylesheet" href="/static/theme-studio/loader/neko-theme-studio.css?v=VERSION-CSS8">
    <script src="/static/theme-studio/loader/neko-theme-studio.js?v=VERSION-LOADERVERSION" defer></script>

The fingerprint is functional: every 15 s the browser-side loader checks that
the stylesheet active in the page is the one on disk, using the ``-CSS8``
suffix of that URL. Without it the loader has to download the whole 158 KB
stylesheet to compare rule signatures, per window, every 15 s.

Only ``stat`` plus reading two small files happens here; no service is built
and no config directory is touched, so page rendering can call it freely.
Missing files yield ``{}``: templates then skip the loader, and the service
reports empty fingerprints. Results are cached by ``(mtime_ns, size)``.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

LOADER_DIR_NAME = "loader"
CSS_FILE_NAME = "neko-theme-studio.css"
JS_FILE_NAME = "neko-theme-studio.js"

# ``SCRIPT_VERSION = '1.6.40'`` in the loader script; dotted digits only.
_RE_SCRIPT_VERSION = re.compile(r"SCRIPT_VERSION\s*=\s*'([0-9]+(?:\.[0-9]+)*)'")

_CACHE: tuple[tuple, dict] = ((), {})


def loader_asset_meta(loader_dir: str | Path) -> dict:
    """Return ``{"css_sha8": ..., "loader_version": ...}``, or ``{}`` if assets are missing."""
    global _CACHE

    base = Path(loader_dir)
    css_path = base / CSS_FILE_NAME
    js_path = base / JS_FILE_NAME
    try:
        css_stat = css_path.stat()
        js_stat = js_path.stat()
    except OSError:
        return {}

    key = (
        str(base),
        css_stat.st_mtime_ns,
        css_stat.st_size,
        js_stat.st_mtime_ns,
        js_stat.st_size,
    )
    cached_key, cached_meta = _CACHE
    if cached_key == key and cached_meta:
        return dict(cached_meta)

    try:
        css_sha8 = hashlib.sha256(css_path.read_bytes()).hexdigest()[:8]
    except OSError:
        return {}

    loader_version = ""
    try:
        match = _RE_SCRIPT_VERSION.search(js_path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        match = None
    if match:
        loader_version = match.group(1)

    meta = {"css_sha8": css_sha8, "loader_version": loader_version}
    _CACHE = (key, meta)
    return dict(meta)


def loader_versions(loader_dir: str | Path, static_asset_version: str) -> dict:
    """Build the two template context version strings.

    ``theme_loader_js_version`` prefers the version declared by the script and
    falls back to the CSS fingerprint; both are only part of the URL, the real
    freshness check happens inside the loader.
    """
    meta = loader_asset_meta(loader_dir)
    if not meta:
        return {}
    fallback = meta["css_sha8"]
    return {
        "theme_loader_css_version": f"{static_asset_version}-{meta['css_sha8']}",
        "theme_loader_js_version": f"{static_asset_version}-{meta['loader_version'] or fallback}",
    }
