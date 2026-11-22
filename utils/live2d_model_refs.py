"""Checks for file references stored in a Live2D model config (``.model3.json``).

Shared by the main server's Live2D routes and the monitor server, so both read and
accept the same set of references.
"""

from __future__ import annotations

import os
import pathlib
from urllib.parse import unquote

from utils.frontend_utils import _is_within


def is_unsafe_model_ref(file_path: str, config_dir: str, model_root_real: str) -> bool:
    """
    Whether a file reference, relative to the folder holding the model config
    (*config_dir*), would point outside the model folder: carrying a colon (URL
    scheme or drive), absolute, climbing out with ``..``, or resolving outside
    *model_root_real* through a link. The frontend resolves references as URLs,
    so the percent-decoded form is checked as well. Links resolving inside the
    model folder are fine. URL parsing drops tabs / newlines and strips surrounding
    spaces, so control characters are refused and the space-stripped form is checked too;
    ``?`` / ``#`` would end the path part of the URL, so they are refused as well.
    """
    decoded = unquote(file_path)
    for candidate in {file_path, decoded, file_path.strip(' '), decoded.strip(' ')}:
        if any(ch in ':?#' or ord(ch) < 0x20 or ord(ch) == 0x7f for ch in candidate):
            return True
        normalized = candidate.replace('\\', '/')
        posix = pathlib.PurePosixPath(normalized)
        if posix.is_absolute() or ".." in posix.parts:
            return True
        # Same rule as the router's directory-listing check, without its "skipped entry"
        # log: this checks client-submitted values.
        try:
            target = os.path.realpath(os.path.join(config_dir, *posix.parts))
            if not _is_within(model_root_real, target):
                return True
        except (OSError, ValueError):
            return True
    return False


def sanitize_emotion_mapping(mapping, config_dir: str, model_root_real: str) -> dict:
    """
    Drop from a stored or derived emotion mapping everything the POST would refuse, so
    that what GET returns can always be posted back unchanged (the model manager edits
    a mapping by GET, modify, POST). A legacy bare-string group value counts as a
    one-path list here; unsafe paths and non-string entries are left out.
    """
    if not isinstance(mapping, dict):
        return {"motions": {}, "expressions": {}}
    result = dict(mapping)
    for section in ('motions', 'expressions'):
        if section not in mapping:
            continue
        groups = mapping[section]
        cleaned = {}
        if isinstance(groups, dict):
            for group, files in groups.items():
                if isinstance(files, str):
                    files = [files]
                if not isinstance(files, list):
                    files = []
                cleaned[group] = [
                    f for f in files
                    if isinstance(f, str) and not is_unsafe_model_ref(f, config_dir, model_root_real)
                ]
        result[section] = cleaned
    return result
