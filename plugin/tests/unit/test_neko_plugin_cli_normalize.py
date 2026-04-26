"""Tests for neko_plugin_cli.core.normalize — cross-platform path safety."""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path, PurePosixPath

import pytest

CLI_ROOT = Path(__file__).resolve().parents[2] / "neko-plugin-cli"
_SRC_DIR = str(CLI_ROOT / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from neko_plugin_cli.core.normalize import (
    normalize_archive_key,
    normalize_relative_posix,
    normalize_unicode,
    validate_archive_entry_name,
)

pytestmark = pytest.mark.plugin_unit


# ---------------------------------------------------------------------------
# validate_archive_entry_name — valid paths
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    [
        "manifest.toml",
        "payload/plugins/demo/plugin.toml",
        "payload/profiles/default.toml",
        "payload/plugins/my-plugin/__init__.py",
        "payload/plugins/test_123/data/file.bin",
    ],
)
def test_validate_accepts_valid_posix_paths(name: str) -> None:
    result = validate_archive_entry_name(name)
    assert isinstance(result, PurePosixPath)
    assert str(result) == name


# ---------------------------------------------------------------------------
# validate_archive_entry_name — rejection cases
# ---------------------------------------------------------------------------

def test_validate_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        validate_archive_entry_name("")


@pytest.mark.parametrize(
    "name",
    [
        "payload\\plugins\\test",
        "path\\to\\file.py",
    ],
)
def test_validate_rejects_backslash_paths(name: str) -> None:
    with pytest.raises(ValueError, match="forward-slash posix paths"):
        validate_archive_entry_name(name)


def test_validate_rejects_drive_letter() -> None:
    with pytest.raises(ValueError, match="forward-slash posix paths"):
        validate_archive_entry_name("C:test")


@pytest.mark.parametrize(
    "name",
    [
        "payload/test\x00file",
        "payload/\x01init.py",
        "payload/tab\there",
        "payload/newline\nhere",
        "payload/del\x7fhere",
    ],
)
def test_validate_rejects_control_characters(name: str) -> None:
    with pytest.raises(ValueError, match="control character"):
        validate_archive_entry_name(name)


def test_validate_rejects_absolute_path() -> None:
    with pytest.raises(ValueError, match="relative path"):
        validate_archive_entry_name("/etc/passwd")


@pytest.mark.parametrize(
    "name",
    [
        "payload/../etc/passwd",
        "../secret",
        "a/b/../../c",
    ],
)
def test_validate_rejects_parent_traversal(name: str) -> None:
    with pytest.raises(ValueError, match="parent traversal"):
        validate_archive_entry_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "payload/file.",
        "payload/file...",
        "payload/file ",
        "payload/file. ",
    ],
)
def test_validate_rejects_trailing_dots_or_spaces(name: str) -> None:
    with pytest.raises(ValueError, match="ends with dots or spaces"):
        validate_archive_entry_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "payload/CON",
        "payload/PRN",
        "payload/AUX",
        "payload/NUL",
        "payload/COM0",
        "payload/COM9",
        "payload/LPT0",
        "payload/LPT9",
        "payload/CON.txt",
        "payload/con",
        "payload/Con.log",
    ],
)
def test_validate_rejects_windows_reserved_names(name: str) -> None:
    with pytest.raises(ValueError, match="Windows reserved device name"):
        validate_archive_entry_name(name)


def test_validate_rejects_overlong_component() -> None:
    long_name = "a" * 256
    with pytest.raises(ValueError, match="exceeding the 255-byte"):
        validate_archive_entry_name(f"payload/{long_name}")


def test_validate_accepts_max_length_component() -> None:
    name = "a" * 255
    result = validate_archive_entry_name(f"payload/{name}")
    assert result.parts[-1] == name


# ---------------------------------------------------------------------------
# NFC normalization
# ---------------------------------------------------------------------------

def test_normalize_unicode_converts_nfd_to_nfc() -> None:
    nfd = unicodedata.normalize("NFD", "café")
    nfc = unicodedata.normalize("NFC", "café")
    assert nfd != nfc  # sanity: they differ at byte level
    assert normalize_unicode(nfd) == nfc
    assert normalize_unicode(nfc) == nfc


def test_normalize_archive_key_is_nfc() -> None:
    nfd_path = unicodedata.normalize("NFD", "plugins/café/file.py")
    nfc_path = unicodedata.normalize("NFC", "plugins/café/file.py")
    assert normalize_archive_key(nfd_path) == normalize_archive_key(nfc_path)


def test_normalize_relative_posix_uses_forward_slashes(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c.txt"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.touch()
    result = normalize_relative_posix(nested, tmp_path)
    assert result == "a/b/c.txt"
    assert "\\" not in result
