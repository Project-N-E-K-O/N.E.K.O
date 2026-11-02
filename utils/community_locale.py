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
        _probe_china_region,
        _read_system_locale_signal_once,
        _system_locale_probe_generation,
    )

    # Share even failed probes for this call, while allowing the next login
    # to retry. Language and region must not mix different OS samples.
    with _system_locale_probe_generation():
        system_locale = ""
        for name, probe in (
            ("windows_locale", _get_windows_locale),
            ("macos_locale", _get_macos_locale),
            ("python_locale", lambda: locale.getlocale()[0]),
            ("lang_env", lambda: os.environ.get("LANG", "")),
        ):
            try:
                candidate = _read_system_locale_signal_once(name, probe)
            except (ValueError, TypeError):
                # The shared reader records only returned values. Publish an
                # unavailable signal before either parser can retry this read.
                candidate = _read_system_locale_signal_once(name, lambda: None)
            if candidate:
                system_locale = candidate
                break
        raw = _get_system_language().replace("_", "-").lower()
        # Bootstrap needs this device snapshot, not the global region cache or
        # its retry cooldown. Reuse the shared probe without changing globals.
        china_region = _probe_china_region().value is True
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
        parts = re.split(r"[-_]", system_locale.split("@", 1)[0].split(".", 1)[0])
        region = None
        for part in parts[1:]:
            # Extension/private-use keys (e.g. u-ca or x-US) are not regions.
            if len(part) == 1:
                break
            if re.fullmatch(r"[A-Za-z]{2}", part):
                region = part.upper()
                break
        # The shared China-source policy can also return False for en-CN.
        # Only an explicit non-China override suppresses a device CN hint.
        non_china_override = os.environ.get("NEKO_IS_CHINA_REGION", "").strip().lower() in {
            "0", "false", "no", "off",
        }
        if region and (region != "CN" or not non_china_override):
            hints["region"] = region
    return hints
