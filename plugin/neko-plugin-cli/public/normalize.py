"""Cross-platform path and content normalization for neko package operations.

Every path that participates in hash computation or archive entry naming MUST
pass through the helpers in this module so that packages built on Windows,
macOS, and Linux produce identical results.

Design decisions
----------------
* Unicode NFC normalization — macOS HFS+/APFS silently converts filenames to
  NFD.  We normalize to NFC (the form used by most other systems and by Python
  string literals) so that the same logical filename always hashes identically.
* Posix-style forward-slash separators — Windows ``Path`` objects use
  backslashes; we always convert to ``/`` before hashing or storing in ZIP.
* Case-sensitive byte-level sorting — ``sorted(key=normalize_archive_key)``
  gives a deterministic order regardless of the OS default collation.
* Dangerous-character rejection — control characters, Windows reserved device
  names, and excessively long components are rejected early so that archives
  remain extractable on all platforms.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path, PurePosixPath


# ---------------------------------------------------------------------------
# Unicode helpers
# ---------------------------------------------------------------------------

def normalize_unicode(value: str) -> str:
    """Return *value* in Unicode NFC form."""
    return unicodedata.normalize("NFC", value)


# ---------------------------------------------------------------------------
# Dangerous-path detection
# ---------------------------------------------------------------------------

# Characters that must never appear in archive entry names.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

# Windows reserved device names (case-insensitive, with or without extension).
_WINDOWS_RESERVED_RE = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\..+)?$",
    re.IGNORECASE,
)

# Maximum byte length for a single path component (conservative cross-platform
# limit — NTFS allows 255 UTF-16 code units, ext4 allows 255 bytes).
_MAX_COMPONENT_BYTES = 255


def validate_archive_entry_name(name: str) -> PurePosixPath:
    """Validate and return a safe ``PurePosixPath`` for an archive entry.

    Raises ``ValueError`` for any name that would be unsafe to extract on
    common operating systems.
    """
    if not name:
        raise ValueError("archive entry name must not be empty")

    # Reject backslashes and Windows drive letters.
    if "\\" in name or (len(name) >= 2 and name[1] == ":"):
        raise ValueError(
            f"archive entry must use forward-slash posix paths, "
            f"but got Windows-style path: '{name}'. "
            f"This usually means the package was created on Windows with a tool "
            f"that did not normalize path separators."
        )

    # Reject control characters (NUL, tabs, newlines, etc.).
    match = _CONTROL_CHAR_RE.search(name)
    if match:
        char_hex = f"0x{ord(match.group()):02x}"
        raise ValueError(
            f"archive entry '{name!r}' contains control character {char_hex} "
            f"at position {match.start()}. Control characters (0x00-0x1f, 0x7f) "
            f"are not allowed in archive entry names because they cause extraction "
            f"failures on most operating systems."
        )

    path = PurePosixPath(name)

    if path.is_absolute():
        raise ValueError(
            f"archive entry must be a relative path, but got absolute path: '{name}'. "
            f"Absolute paths are rejected to prevent writing outside the extraction directory."
        )
    if ".." in path.parts:
        raise ValueError(
            f"archive entry must not contain '..' parent traversal: '{name}'. "
            f"Parent traversal components are rejected to prevent zip-slip attacks "
            f"that could write files outside the extraction directory."
        )

    # Per-component checks.
    for part in path.parts:
        # Trailing dots/spaces are silently stripped on Windows, causing
        # mismatches between the name stored in the ZIP and the name on disk.
        if part != part.rstrip(". "):
            stripped = part.rstrip(". ")
            raise ValueError(
                f"archive entry component '{part}' ends with dots or spaces. "
                f"Windows silently strips these, so the file would be extracted as "
                f"'{stripped}' instead of '{part}', causing hash mismatches. "
                f"Rename the file to remove trailing dots/spaces before packaging."
            )
        if _WINDOWS_RESERVED_RE.fullmatch(part):
            raise ValueError(
                f"archive entry component '{part}' is a Windows reserved device name "
                f"(CON, PRN, AUX, NUL, COM0-9, LPT0-9). Files with these names "
                f"cannot be created on Windows. Rename the file before packaging."
            )
        byte_len = len(part.encode("utf-8"))
        if byte_len > _MAX_COMPONENT_BYTES:
            raise ValueError(
                f"archive entry component '{part[:50]}...' is {byte_len} bytes in UTF-8, "
                f"exceeding the {_MAX_COMPONENT_BYTES}-byte cross-platform limit. "
                f"Shorten the filename to ensure it can be extracted on all platforms."
            )

    return path


# ---------------------------------------------------------------------------
# Canonical archive key (for sorting and hashing)
# ---------------------------------------------------------------------------

def normalize_archive_key(posix_path_str: str) -> str:
    """Return a canonical sort/hash key for an archive-relative path.

    The returned string is NFC-normalized and uses forward slashes.  It is
    suitable as a ``sorted()`` key so that file ordering is identical on every
    platform.
    """
    return normalize_unicode(posix_path_str)


def normalize_relative_posix(path: Path, root: Path) -> str:
    """Return the NFC-normalized posix-style relative path of *path* under *root*."""
    return normalize_unicode(path.relative_to(root).as_posix())
