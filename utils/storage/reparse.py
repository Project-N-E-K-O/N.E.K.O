"""Pure Windows reparse classification shared by local storage consumers."""
from __future__ import annotations

import os


def is_name_surrogate(path_stat: os.stat_result) -> bool:
    """Reject path-redirection tags, while permitting cloud data placeholders."""
    tag = int(getattr(path_stat, "st_reparse_tag", 0) or 0)
    return bool(tag & 0x20000000)
