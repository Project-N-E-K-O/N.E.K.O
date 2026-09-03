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

"""Behaviour tests for the dist architecture gate that guards issue #2898."""
from __future__ import annotations

import importlib.util
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "check_dist_arch", ROOT / "scripts" / "check_dist_arch.py"
)
assert _SPEC is not None and _SPEC.loader is not None
check_dist_arch = importlib.util.module_from_spec(_SPEC)
sys.modules["check_dist_arch"] = check_dist_arch
_SPEC.loader.exec_module(check_dist_arch)


_MACHO_CPU_BY_ARCH = {"x64": 0x01000007, "arm64": 0x0100000C}
_ELF_MACHINE_BY_ARCH = {"x64": 62, "arm64": 183}
_PE_MACHINE_BY_ARCH = {"x64": 0x8664, "arm64": 0xAA64}


def macho(arch: str) -> bytes:
    return b"\xcf\xfa\xed\xfe" + struct.pack("<I", _MACHO_CPU_BY_ARCH[arch]) + b"\x00" * 56


def macho_fat(*arches: str) -> bytes:
    blob = b"\xca\xfe\xba\xbe" + struct.pack(">I", len(arches))
    for arch in arches:
        blob += struct.pack(">I", _MACHO_CPU_BY_ARCH[arch]) + b"\x00" * 16
    return blob + b"\x00" * 64


def elf(arch: str) -> bytes:
    head = bytearray(b"\x7fELF" + b"\x00" * 60)
    head[4] = 2  # ELFCLASS64
    head[5] = 1  # ELFDATA2LSB
    head[18:20] = struct.pack("<H", _ELF_MACHINE_BY_ARCH[arch])
    return bytes(head)


def pe(arch: str) -> bytes:
    pe_offset = 0x80
    head = bytearray(b"\x00" * (pe_offset + 8))
    head[0:2] = b"MZ"
    head[0x3C:0x40] = struct.pack("<I", pe_offset)
    head[pe_offset : pe_offset + 4] = b"PE\x00\x00"
    head[pe_offset + 4 : pe_offset + 6] = struct.pack("<H", _PE_MACHINE_BY_ARCH[arch])
    return bytes(head)


def write(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


@pytest.fixture()
def mac_bundle(tmp_path: Path):
    """A macOS-shaped dist: shell wrapper at the root, real Mach-O inside the .app."""

    def build(entry_arch: str, lib_arch: str | None = None) -> Path:
        root = tmp_path / "Xiao8"
        write(
            root / "projectneko_server",
            b'#!/bin/bash\nexec "$(dirname "$0")/projectneko_server.app/Contents/MacOS/projectneko_server" "$@"\n',
        )
        write(
            root / "projectneko_server.app" / "Contents" / "MacOS" / "projectneko_server",
            macho(entry_arch),
        )
        write(
            root / "projectneko_server.app" / "Contents" / "MacOS" / "unicodedata.so",
            macho(lib_arch or entry_arch),
        )
        return root

    return build


def test_clean_x64_mac_dist_passes(mac_bundle) -> None:
    root = mac_bundle("x64")
    assert check_dist_arch.check_dist_arch(root, "x64", "mac") == []


def test_issue_2898_shape_is_rejected(mac_bundle) -> None:
    """The exact #2898 build: mac-x64 artifact whose Mach-O is arm64."""
    root = mac_bundle("arm64")
    issues = check_dist_arch.check_dist_arch(root, "x64", "mac")
    assert issues, "an arm64 binary in an x64 bundle must not pass"
    assert any("main binary is arm64" in issue for issue in issues)


def test_shell_wrapper_at_root_does_not_shadow_the_real_binary(mac_bundle) -> None:
    """The root `projectneko_server` is a text wrapper, not the Mach-O to inspect.

    Without the "skip candidates that are not binaries" guard, the wrapper is
    found first, `read_arches` returns None, and the arm64 payload sails through.
    """
    root = mac_bundle("arm64")
    entry = check_dist_arch._entry_binary(root, "mac")
    assert entry is not None
    assert entry.name == "projectneko_server"
    assert entry.parent.name == "MacOS"
    assert check_dist_arch.read_arches(entry) == ["arm64"]


def test_mismatched_native_library_is_reported(mac_bundle) -> None:
    root = mac_bundle("x64", lib_arch="arm64")
    issues = check_dist_arch.check_dist_arch(root, "x64", "mac")
    assert any("native library" in issue and "unicodedata.so" in issue for issue in issues)


def test_universal_binary_containing_the_expected_arch_passes(tmp_path: Path) -> None:
    root = tmp_path / "Xiao8"
    write(root / "projectneko_server", macho_fat("x64", "arm64"))
    assert check_dist_arch.check_dist_arch(root, "x64", "mac") == []
    assert check_dist_arch.check_dist_arch(root, "arm64", "mac") == []


def test_vendored_browser_directory_with_opposite_arch_token_is_rejected(tmp_path: Path) -> None:
    """#2898 also shipped chromium-1208/chrome-mac-arm64 inside the x64 bundle."""
    root = tmp_path / "Xiao8"
    write(root / "projectneko_server", macho("x64"))
    (root / "playwright_browsers" / "chromium-1208" / "chrome-mac-arm64").mkdir(parents=True)
    issues = check_dist_arch.check_dist_arch(root, "x64", "mac")
    assert any("vendored browser tree" in issue for issue in issues)


def test_vendored_browser_directory_matching_the_build_passes(tmp_path: Path) -> None:
    root = tmp_path / "Xiao8"
    write(root / "projectneko_server", macho("x64"))
    (root / "playwright_browsers" / "chromium-1208" / "chrome-mac-x64").mkdir(parents=True)
    assert check_dist_arch.check_dist_arch(root, "x64", "mac") == []


def test_tokenless_browser_directory_with_wrong_arch_binary_is_rejected(tmp_path: Path) -> None:
    """Playwright also uses tokenless names like `chrome-mac`.

    Directory-name matching alone would let a wrong-arch Chromium through, so
    the browser tree is header-scanned as well.
    """
    root = tmp_path / "Xiao8"
    write(root / "projectneko_server", macho("x64"))
    browser = root / "playwright_browsers" / "chromium-1208" / "chrome-mac"
    write(browser / "libEGL.dylib", macho("arm64"))
    issues = check_dist_arch.check_dist_arch(root, "x64", "mac")
    assert any("vendored browser tree contains" in issue for issue in issues)


def test_suffixless_browser_executable_is_scanned(tmp_path: Path) -> None:
    """Chromium's own executable has no filename suffix; dispatch on magic."""
    root = tmp_path / "Xiao8"
    write(root / "projectneko_server", macho("arm64"))
    browser = (
        root / "playwright_browsers" / "chromium-1208" / "chrome-mac"
        / "Chromium.app" / "Contents" / "MacOS"
    )
    write(browser / "Chromium", macho("x64"))
    issues = check_dist_arch.check_dist_arch(root, "arm64", "mac")
    assert any("vendored browser tree contains" in issue for issue in issues)


def test_matching_browser_tree_passes(tmp_path: Path) -> None:
    root = tmp_path / "Xiao8"
    write(root / "projectneko_server", macho("x64"))
    browser = root / "playwright_browsers" / "chromium-1208" / "chrome-mac"
    write(browser / "libEGL.dylib", macho("x64"))
    write(browser / "Chromium.app" / "Contents" / "MacOS" / "Chromium", macho("x64"))
    write(browser / "icudtl.dat", b"binary blob, not an executable\n")
    assert check_dist_arch.check_dist_arch(root, "x64", "mac") == []


@pytest.mark.parametrize(
    ("payload", "platform", "suffix"),
    [(elf, "linux", ".so"), (pe, "win", ".dll")],
)
def test_elf_and_pe_dists_are_checked_too(tmp_path: Path, payload, platform, suffix) -> None:
    root = tmp_path / "Xiao8"
    entry = "projectneko_server.exe" if platform == "win" else "projectneko_server"
    write(root / entry, payload("x64"))
    write(root / f"native{suffix}", payload("arm64"))
    issues = check_dist_arch.check_dist_arch(root, "x64", platform)
    assert any("native library" in issue for issue in issues)

    write(root / f"native{suffix}", payload("x64"))
    assert check_dist_arch.check_dist_arch(root, "x64", platform) == []


def test_linux_arm64_dist_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "Xiao8"
    write(root / "projectneko_server", elf("arm64"))
    write(root / "native.so", elf("arm64"))
    assert check_dist_arch.check_dist_arch(root, "arm64", "linux") == []
    assert check_dist_arch.check_dist_arch(root, "x64", "linux") != []


def test_missing_entry_binary_is_an_error(tmp_path: Path) -> None:
    root = tmp_path / "Xiao8"
    root.mkdir(parents=True)
    issues = check_dist_arch.check_dist_arch(root, "x64", "linux")
    assert any("main server binary not found" in issue for issue in issues)


def test_non_binary_files_with_native_suffixes_are_ignored(tmp_path: Path) -> None:
    root = tmp_path / "Xiao8"
    write(root / "projectneko_server", elf("x64"))
    write(root / "placeholder.so", b"not a binary at all\n")
    assert check_dist_arch.check_dist_arch(root, "x64", "linux") == []


def test_cli_exit_codes(tmp_path: Path, capsys) -> None:
    root = tmp_path / "Xiao8"
    write(root / "projectneko_server", elf("arm64"))
    assert (
        check_dist_arch.main([str(root), "--expect-arch", "arm64", "--expect-platform", "linux"])
        == 0
    )
    assert (
        check_dist_arch.main([str(root), "--expect-arch", "x64", "--expect-platform", "linux"]) == 1
    )
    assert "#2898" in capsys.readouterr().err
