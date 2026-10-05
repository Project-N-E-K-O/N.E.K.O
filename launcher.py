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

"""Thin compatibility entry point for the N.E.K.O launcher."""

from __future__ import annotations

import os
import sys

from launcher_core.bootstrap import (
    _ensure_utf8_filesystem_encoding,
    _pin_project_root_first,
    _warn_if_windows7,
)


if __name__ == "__main__":
    _ensure_utf8_filesystem_encoding()
    _warn_if_windows7()
    if os.environ.get("NEKO_WAKE_WORD_RELEASE_SMOKE") == "1":
        from multiprocessing import freeze_support as _wake_freeze_support
        _wake_freeze_support()
        from main_logic.voice_identity_service.wake_word_release_smoke import main as _wake_release_smoke
        sys.exit(_wake_release_smoke())
    if os.environ.get("NEKO_MEDIA_RELEASE_SMOKE") == "1":
        from multiprocessing import freeze_support as _media_freeze_support
        _media_freeze_support()
        from main_logic.watch_together.media_smoke import main as _media_smoke
        sys.exit(_media_smoke())
    if sys.argv[1:] == ["--neko-plugin-metadata-worker"]:
        from plugin.server.application.plugins.metadata_scanner import _worker_main

        _worker_main()
        raise SystemExit(0)
    if os.environ.get("NEKO_VOICE_IDENTITY_RELEASE_SMOKE") == "1":
        # Frozen multiprocessing children re-enter this file.  Let Python
        # consume its private child-process arguments before dispatching the
        # release smoke, otherwise the CAM++ host would recursively run it.
        from multiprocessing import freeze_support as _release_smoke_freeze_support

        _release_smoke_freeze_support()
        from main_logic.voice_identity_service.release_smoke import (
            main as _run_voice_identity_release_smoke,
        )

        sys.exit(_run_voice_identity_release_smoke())

# multiprocessing spawn children re-enter this file (as ``__mp_main__`` from
# source) before unpickling their target, with the parent's sys.path copied
# verbatim. In a plugin host that list can carry plugin ``vendor/`` dirs ahead
# of the repo root (plugin/core/registry.py inserts them at index 0), so pin
# the repo root first again here, before the target's ``config`` / ``utils`` /
# ``plugin`` / ``main_logic`` imports resolve. Frozen children are unaffected
# either way: spawn.prepare() replaces sys.path after this runs, and Nuitka's
# loader precedes the path finder.
_pin_project_root_first()

if __name__ == "__main__":
    # Only the real entry path needs the runtime chain; spawn children import
    # what their target needs when unpickling it.
    from launcher_core.runtime import start_launcher

    sys.exit(start_launcher())
