"""Device hints for initializing community account recommendation preferences."""

from __future__ import annotations

import locale
import os
import re


def community_bootstrap_locale_hints() -> dict[str, str]:
    """Read device hints independently of the interface or Steam language.

    The community server owns preference initialization and preserves any
    existing account choice. Call this synchronous OS probe off the event loop.
    """
    from utils.language_utils import (
        _get_macos_locale,
        _get_system_language,
        _get_windows_locale,
        _read_system_locale_signal_once,
        _system_locale_probe_generation,
        is_china_region,
    )

    # Share even failed probes for this call, while allowing the next login
    # to retry. Language and region must not mix different OS samples.
    with _system_locale_probe_generation():
        raw = _get_system_language().replace("_", "-").lower()
        try:
            system_locale = (
                _read_system_locale_signal_once("windows_locale", _get_windows_locale)
                or _read_system_locale_signal_once("macos_locale", _get_macos_locale)
                or _read_system_locale_signal_once("python_locale", lambda: locale.getlocale()[0])
                or _read_system_locale_signal_once("lang_env", lambda: os.environ.get("LANG", ""))
            )
        except (ValueError, TypeError):
            system_locale = ""
        china_region = is_china_region()
    traditional_prefixes = ("zh-tw", "zh-hk", "zh-mo", "zh-hant")
    device_locale = system_locale.replace("_", "-").lower()
    # The shared language probe collapses zh-MO to zh. Retain the device's
    # Chinese variant for bootstrap without changing global language behavior.
    if raw.startswith("zh") and device_locale.startswith(traditional_prefixes):
        raw = device_locale
    if raw.startswith(traditional_prefixes):
        language = "zh-TW"
    elif raw.startswith("zh"):
        language = "zh-CN"
    else:
        base = raw.split("-", 1)[0]
        language = base if base in {"en", "es", "ja", "ko", "pt", "ru"} else "en"
    hints = {"locale": language}
    if china_region:
        hints["region"] = "CN"
    else:
        parts = re.split(r"[-_]", system_locale.split(".", 1)[0])
        region = next((part.upper() for part in parts[1:] if re.fullmatch(r"[A-Za-z]{2}", part)), None)
        # A negative authoritative China verdict must not be overridden by
        # extracting CN from the raw locale again.
        if region and region != "CN":
            hints["region"] = region
    return hints
