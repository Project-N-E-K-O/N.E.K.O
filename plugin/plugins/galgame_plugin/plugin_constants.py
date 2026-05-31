"""Mixin-shared OCR backend selection constants.

Only the two ``_OCR_*_SELECTIONS`` sets are kept here because they are the
only module-level constants referenced from ``plugin_entries/`` mixin bodies
(``galgame_set_ocr_backend`` uses them for argparse choices and validation).
All other timing / latency constants live in ``plugin_core`` alongside the
``GalgamePlugin`` class body that consumes them — keeping this module narrow
avoids the false impression that everything here is "shared" with mixins.
"""
from __future__ import annotations


_OCR_BACKEND_SELECTIONS = {"auto", "rapidocr"}
_OCR_CAPTURE_BACKEND_SELECTIONS = {"auto", "smart", "dxcam", "mss", "printwindow"}
