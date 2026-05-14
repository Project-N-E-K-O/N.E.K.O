# -*- coding: utf-8 -*-
"""
Direct bridge into ``libsteam_api`` for ``ISteamUGC::DownloadItem``.

The bundled ``SteamworksPy`` wrapper library does not export
``Workshop_DownloadItem``; without it, items the user has *subscribed* to
will never have their files downloaded by Steam — subscription alone does
not initiate a download. ``ISteamUGC::DownloadItem`` is the API that asks
the Steam client to actually fetch (or queue) the content.

We bind to ``libsteam_api`` directly via ``ctypes``. That library is already
present in-process because the SteamworksPy wrapper depends on it, so we
share the same Steam runtime state — no separate ``SteamAPI_Init`` is
required here.
"""

import os
import sys
import logging
from ctypes import CDLL, c_bool, c_uint64, c_void_p

logger = logging.getLogger(__name__)

# Steam ships a new ``SteamUGC`` interface version every few SDK releases.
# Bundled NEKO libraries currently expose v020 (Windows) and v021 (macOS),
# but we also probe a small window of nearby versions so an SDK refresh
# does not silently break downloads.
_UGC_ACCESSOR_NAMES = (
    "SteamAPI_SteamUGC_v021",
    "SteamAPI_SteamUGC_v020",
    "SteamAPI_SteamUGC_v019",
    "SteamAPI_SteamUGC_v018",
    "SteamAPI_SteamUGC_v017",
    "SteamAPI_SteamUGC_v016",
    "SteamAPI_SteamUGC_v022",
)

_lib = None
_ugc_handle = None


def _candidate_library_paths():
    """Yield plausible paths for the underlying Steam API library."""
    here = os.path.dirname(os.path.abspath(__file__))
    if sys.platform == "win32":
        names = ("steam_api64.dll", "steam_api.dll")
    elif sys.platform == "darwin":
        names = ("libsteam_api.dylib",)
    else:
        names = ("libsteam_api.so",)

    roots = [here]
    parent = os.path.dirname(here)
    if parent and parent != here:
        roots.append(parent)
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(meipass)
        roots.append(os.path.dirname(sys.executable))

    seen = set()
    for root in roots:
        for name in names:
            path = os.path.join(root, name)
            if path in seen:
                continue
            seen.add(path)
            if os.path.isfile(path):
                yield path


def _load_library():
    """Locate and load the underlying Steam API library."""
    global _lib
    if _lib is not None:
        return _lib

    last_error: Exception | None = None
    for path in _candidate_library_paths():
        try:
            _lib = CDLL(path)
            logger.debug("Native UGC bridge: loaded %s", path)
            return _lib
        except OSError as exc:
            last_error = exc
            logger.debug("Native UGC bridge: failed to load %s: %s", path, exc)

    # Final fallback: rely on the OS loader (the library is already in-process
    # via the SteamworksPy wrapper, so a name-only lookup usually succeeds).
    fallback_names = (
        ("steam_api64", "steam_api") if sys.platform == "win32"
        else ("libsteam_api.dylib",) if sys.platform == "darwin"
        else ("libsteam_api.so",)
    )
    for name in fallback_names:
        try:
            _lib = CDLL(name)
            logger.debug("Native UGC bridge: loaded by name %s", name)
            return _lib
        except OSError as exc:
            last_error = exc

    raise OSError(f"could not load libsteam_api for UGC bridge: {last_error}")


def _get_ugc_handle():
    """Resolve and cache the ``ISteamUGC*`` pointer."""
    global _ugc_handle
    if _ugc_handle:
        return _ugc_handle

    lib = _load_library()
    for accessor in _UGC_ACCESSOR_NAMES:
        fn = getattr(lib, accessor, None)
        if fn is None:
            continue
        fn.restype = c_void_p
        fn.argtypes = []
        try:
            handle = fn()
        except Exception as exc:
            logger.debug("Native UGC bridge: %s raised %s", accessor, exc)
            continue
        if handle:
            _ugc_handle = handle
            logger.info(
                "Native UGC bridge: resolved ISteamUGC handle via %s", accessor
            )
            return _ugc_handle

    raise RuntimeError(
        "ISteamUGC accessor not found in libsteam_api (tried: "
        + ", ".join(_UGC_ACCESSOR_NAMES)
        + ")"
    )


def download_item(published_file_id: int, high_priority: bool = False) -> bool:
    """Ask Steam to download a Workshop item.

    Returns ``True`` if Steam accepted the request — this does **not** mean
    the file is already on disk; the caller should poll ``GetItemState`` /
    ``GetItemInstallInfo`` for completion. Returns ``False`` if Steam
    refused (commonly: not subscribed, app shutting down, invalid id).
    """
    file_id = int(published_file_id)
    if file_id <= 0:
        return False
    try:
        lib = _load_library()
        handle = _get_ugc_handle()
        fn = lib.SteamAPI_ISteamUGC_DownloadItem
        fn.restype = c_bool
        fn.argtypes = [c_void_p, c_uint64, c_bool]
        return bool(fn(handle, c_uint64(file_id), c_bool(bool(high_priority))))
    except Exception as exc:
        logger.warning(
            "Native UGC bridge: DownloadItem(%s, high_priority=%s) failed: %s",
            file_id, high_priority, exc,
        )
        return False
