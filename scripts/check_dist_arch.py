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

"""Assert that a built dist actually contains the CPU architecture it claims.

Run right after the Nuitka build, before the artifact is uploaded.

## Why this exists

Issue #2898: the nightly ``mac-x64`` backend was arm64. The ``mac-x64`` matrix
leg ran on the ``macos-15`` runner, which is Apple Silicon -- ``actions/setup-python``
with ``architecture: x64`` only makes *Python* run under Rosetta, while Nuitka
still emits a native arm64 binary. Nothing in the pipeline looked at the
produced Mach-O, so a completely unusable Intel build shipped for months.

The runner label is fixed separately; this gate is what keeps the class of bug
from coming back silently on any platform, including the Linux arm64 leg where
a mis-resolved electron-builder/Nuitka target would fail the same way.

## What it checks

- The main entry binary's CPU type and container format.
- Every packaged native binary. Three filename shapes, because a plain suffix
  allowlist misses two real ones: versioned ELF sonames such as
  ``libpython3.11.so.1.0`` (whose ``Path.suffix`` is ``.0``) and executables
  shipped without any suffix, as helper programs are on macOS and Linux.
- That the container format matches the target platform. A Windows PE that
  survived inside a macOS bundle has a perfectly matching *architecture*, so
  only the format check catches it -- and this repo does keep all three
  platforms' Steam natives in the source tree, stripped by ``rm -f`` at build
  time.

Headers are parsed directly, so this runs identically on all three CI hosts and
needs neither ``lipo`` nor ``file``.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path
from typing import NamedTuple

MACHO = "mach-o"
ELF = "elf"
PE = "pe"

# Mach-O
_MACHO_MAGICS = {
    b"\xcf\xfa\xed\xfe": "<",  # MH_MAGIC_64, little-endian host order
    b"\xfe\xed\xfa\xcf": ">",  # MH_CIGAM_64
    b"\xce\xfa\xed\xfe": "<",  # MH_MAGIC (32-bit)
    b"\xfe\xed\xfa\xce": ">",
}
# FAT_MAGIC/FAT_CIGAM carry 20-byte fat_arch records; FAT_MAGIC_64/FAT_CIGAM_64
# (trailing byte 0xbf, not 0xbe) carry 32-byte fat_arch_64 records. Both are
# valid universal binaries -- reading only the 32-bit form reports a 64-bit fat
# main binary as "not an executable" and skips a 64-bit fat dylib entirely.
_MACHO_FAT_MAGICS = {
    b"\xca\xfe\xba\xbe": (">", 20),
    b"\xbe\xba\xfe\xca": ("<", 20),
    b"\xca\xfe\xba\xbf": (">", 32),
    b"\xbf\xba\xfe\xca": ("<", 32),
}
_MACHO_CPU = {0x01000007: "x64", 0x0100000C: "arm64", 0x00000007: "x86", 0x0000000C: "arm"}

# ELF
_ELF_MACHINE = {62: "x64", 183: "arm64", 3: "x86", 40: "arm"}

# PE
_PE_MACHINE = {0x8664: "x64", 0xAA64: "arm64", 0x014C: "x86", 0x01C0: "arm"}

_FORMAT_BY_PLATFORM = {"mac": MACHO, "linux": ELF, "win": PE}

_NATIVE_SUFFIXES = (".so", ".dylib", ".pyd", ".dll", ".exe")
_ARCH_TOKENS = ("x64", "arm64")
# PE e_lfanew points to the executable header, usually a few hundred bytes in real files. Use a generous upper limit
# so forged offsets cannot cause whole-file reads.
_MAX_PE_HEADER_OFFSET = 4 * 1024 * 1024


class BinaryInfo(NamedTuple):
    """The container format and the CPU architectures a binary declares."""

    format: str
    arches: list[str]


def _read_head(path: Path, size: int = 64) -> bytes:
    with path.open("rb") as handle:
        return handle.read(size)


def read_binary(path: Path) -> BinaryInfo | None:
    """Inspect a file's header, or return None when it is not an executable.

    A Mach-O fat binary reports every slice it carries; every other format
    reports a single entry.
    """
    try:
        head = _read_head(path)
    except OSError:
        return None
    if len(head) < 20:
        return None

    magic = head[:4]
    if magic in _MACHO_FAT_MAGICS:
        endian, record_size = _MACHO_FAT_MAGICS[magic]
        (count,) = struct.unpack(endian + "I", head[4:8])
        # Bound malformed headers to avoid exhausting memory.
        if count > 64:
            return None
        blob = _read_head(path, 8 + record_size * count)
        arches: list[str] = []
        for index in range(count):
            offset = 8 + record_size * index
            if offset + 4 > len(blob):
                break
            (cpu,) = struct.unpack(endian + "I", blob[offset : offset + 4])
            arches.append(_MACHO_CPU.get(cpu, f"unknown(0x{cpu:08x})"))
        return BinaryInfo(MACHO, arches) if arches else None

    if magic in _MACHO_MAGICS:
        endian = _MACHO_MAGICS[magic]
        (cpu,) = struct.unpack(endian + "I", head[4:8])
        return BinaryInfo(MACHO, [_MACHO_CPU.get(cpu, f"unknown(0x{cpu:08x})")])

    if magic == b"\x7fELF":
        endian = "<" if head[5] == 1 else ">"
        (machine,) = struct.unpack(endian + "H", head[18:20])
        return BinaryInfo(ELF, [_ELF_MACHINE.get(machine, f"unknown({machine})")])

    if magic[:2] == b"MZ":
        # Every suffixless file in the dist reaches this reader, including data files coincidentally starting with MZ.
        # Headers shorter than 0x40 yield empty slices; struct.unpack would raise struct.error and crash the gate.
        # Forged huge pe_offset values would also make _read_head request unbounded reads. Guard both.
        if len(head) < 0x40:
            return None
        (pe_offset,) = struct.unpack("<I", head[0x3C:0x40])
        if pe_offset > _MAX_PE_HEADER_OFFSET:
            return None
        blob = _read_head(path, pe_offset + 8)
        if len(blob) < pe_offset + 8 or blob[pe_offset : pe_offset + 4] != b"PE\x00\x00":
            return None
        (machine,) = struct.unpack("<H", blob[pe_offset + 4 : pe_offset + 6])
        return BinaryInfo(PE, [_PE_MACHINE.get(machine, f"unknown(0x{machine:04x})")])

    return None


def read_arches(path: Path) -> list[str] | None:
    """Convenience wrapper returning just the architectures."""
    info = read_binary(path)
    return None if info is None else info.arches


def _entry_binary(dist_root: Path, platform: str) -> Path | None:
    """Locate the main server binary for a platform, or None when absent.

    macOS wraps the Nuitka output in ``projectneko_server.app``; the runtime dir
    handed to this script may be either the bundle root or its ``MacOS`` dir.
    """
    candidates = [
        dist_root / "projectneko_server.exe",
        dist_root / "projectneko_server",
        dist_root / "projectneko_server.app" / "Contents" / "MacOS" / "projectneko_server",
    ]
    if platform == "win":
        candidates = [dist_root / "projectneko_server.exe"]
    for candidate in candidates:
        # macOS dist roots also contain same-named shell wrappers executing into .app, not Mach-O files;
        # read_binary returns None. Continue to the actual executable instead of accepting a wrapper as the main program.
        if candidate.is_file() and read_binary(candidate) is not None:
            return candidate
    return None


def _looks_native(path: Path) -> bool:
    """Is this filename shaped like something that could be a native binary?"""
    return path.suffix.lower() in _NATIVE_SUFFIXES or ".so." in path.name.lower() or not path.suffix


def _iter_native_binaries(dist_root: Path):
    for path in dist_root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if _looks_native(path):
            yield path


def check_dist_arch(dist_root: Path, expect_arch: str, platform: str) -> list[str]:
    """Return a list of human-readable problems; empty means the dist is clean."""
    issues: list[str] = []
    expect_format = _FORMAT_BY_PLATFORM[platform]

    entry = _entry_binary(dist_root, platform)
    if entry is None:
        issues.append(
            "main server binary not found (or not a recognisable executable) under "
            f"{dist_root}"
        )
    else:
        info = read_binary(entry)
        assert info is not None  # _entry_binary only returns readable binaries
        if expect_arch not in info.arches:
            issues.append(
                f"main binary is {'/'.join(info.arches) or 'unreadable'}, expected "
                f"{expect_arch}: {entry.relative_to(dist_root)}"
            )
        if info.format != expect_format:
            issues.append(
                f"main binary is a {info.format} file but {platform} builds must be "
                f"{expect_format}: {entry.relative_to(dist_root)}"
            )

    mismatched: list[str] = []
    foreign_format: list[str] = []
    for lib in _iter_native_binaries(dist_root):
        info = read_binary(lib)
        if info is None:
            # Native-library-like names with non-executable contents (placeholders/text stubs) are not architecture issues.
            continue
        relative = lib.relative_to(dist_root)
        if info.format != expect_format:
            # Wrong-platform libraries may have the correct architecture (Windows DLLs are also PE x64);
            # only binary format detects them, so report a separate category.
            foreign_format.append(f"{relative} is {info.format}")
        elif expect_arch not in info.arches:
            mismatched.append(f"{relative} is {'/'.join(info.arches)}")
    if mismatched:
        shown = ", ".join(sorted(mismatched)[:10])
        issues.append(
            f"{len(mismatched)} native binary/binaries are not {expect_arch}: {shown}"
            + (" ..." if len(mismatched) > 10 else "")
        )
    if foreign_format:
        shown = ", ".join(sorted(foreign_format)[:10])
        issues.append(
            f"{len(foreign_format)} native binary/binaries are not {expect_format} "
            f"(wrong platform): {shown}" + (" ..." if len(foreign_format) > 10 else "")
        )

    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "dist_root",
        nargs="?",
        default="dist/Xiao8",
        help="Path to the built dist root (default: dist/Xiao8)",
    )
    parser.add_argument(
        "--expect-arch",
        required=True,
        choices=_ARCH_TOKENS,
        help="CPU architecture this build is supposed to be",
    )
    parser.add_argument(
        "--expect-platform",
        required=True,
        choices=tuple(_FORMAT_BY_PLATFORM),
        help="Host platform this build targets",
    )
    args = parser.parse_args(argv)

    dist_root = Path(args.dist_root).resolve()
    if not dist_root.is_dir():
        print(f"[FAIL] dist root does not exist or is not a directory: {dist_root}", file=sys.stderr)
        return 1

    issues = check_dist_arch(dist_root, args.expect_arch, args.expect_platform)
    if issues:
        print(
            f"[FAIL] {dist_root} does not match {args.expect_platform}/{args.expect_arch}:",
            file=sys.stderr,
        )
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
        print(
            "\nHint: this almost always means the matrix leg ran on a runner whose "
            "native architecture differs from the one it claims to build (see #2898, "
            "where mac-x64 ran on the Apple Silicon `macos-15` image). Check the "
            "`runs-on` label for this leg before touching anything else.",
            file=sys.stderr,
        )
        return 1

    print(f"[OK] dist matches {args.expect_platform}/{args.expect_arch}: {dist_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
