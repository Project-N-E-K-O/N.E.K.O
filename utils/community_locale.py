"""Device locale hints for community login and anonymous recommendations."""

from __future__ import annotations

import locale
import os
import re


def community_locale_hints() -> dict[str, str]:
    # Keep heavyweight language probing off the import/startup path.
    from utils.language_utils import (
        _get_macos_locale, _get_windows_locale, get_global_language_full, is_china_region,
    )

    raw = get_global_language_full().replace("_", "-").lower()
    if raw.startswith(("zh-tw", "zh-hk", "zh-mo", "zh-hant")):
        language = "zh-TW"
    elif raw.startswith("zh"):
        language = "zh-CN"
    else:
        base = raw.split("-", 1)[0]
        language = base if base in {"en", "es", "ja", "ko", "pt", "ru"} else "en"
    hints = {"locale": language}
    # The existing region detector incorporates OS regional settings. For
    # other regions use the OS locale's territory when available.
    if is_china_region():
        hints["region"] = "CN"
    else:
        try:
            system_locale = (
                _get_windows_locale() or _get_macos_locale()
                or locale.getlocale()[0] or os.environ.get("LANG", "")
            )
        except (ValueError, TypeError):
            system_locale = ""
        parts = re.split(r"[-_]", system_locale.split(".", 1)[0])
        region = next((part.upper() for part in parts[1:] if re.fullmatch(r"[A-Za-z]{2}", part)), None)
        if region:
            hints["region"] = region
    return hints
