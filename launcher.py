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

from launcher_core.bootstrap import _ensure_utf8_filesystem_encoding
from launcher_core.runtime import start_launcher


if __name__ == "__main__":
    _ensure_utf8_filesystem_encoding()
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
    sys.exit(start_launcher())