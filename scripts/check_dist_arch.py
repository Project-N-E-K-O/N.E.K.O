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
still emits a native arm64 binary and Playwright still downloads the host's
browser build. Nothing in the pipeline looked at the produced Mach-O, so a
completely unusable Intel build shipped for months.

The runner label is fixed separately; this gate is what keeps the class of bug
from coming back silently on any platform, including the Linux arm64 leg where
a mis-resolved electron-builder/Nuitka target would fail the same way.

## What it checks

- The main entry binary's declared CPU type.
- Every native library outside the vendored browser tree (``.so``/``.dylib``/
  ``.pyd``/``.dll``).
- Inside ``playwright_browsers/``, that no path component carries the *opposite*
  architecture token. The vendored browser has its own layout and its own
  third-party binaries, so it is matched by directory naming rather than by
  header scanning (#2898's bad build shipped ``chromium-1208/chrome-mac-arm64``
  inside an x64 bundle).

Headers are parsed directly, so this runs identically on all three CI hosts and
needs neither ``lipo`` nor ``file``.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

# Mach-O
_MACHO_MAGICS = {
    b"\xcf\xfa\xed\xfe": "<",  # MH_MAGIC_64, little-endian host order
    b"\xfe\xed\xfa\xcf": ">",  # MH_CIGAM_64
    b"\xce\xfa\xed\xfe": "<",  # MH_MAGIC (32-bit)
    b"\xfe\xed\xfa\xce": ">",
}
_MACHO_FAT_MAGICS = {b"\xca\xfe\xba\xbe": ">", b"\xbe\xba\xfe\xca": "<"}
_MACHO_CPU = {0x01000007: "x64", 0x0100000C: "arm64", 0x00000007: "x86", 0x0000000C: "arm"}

# ELF
_ELF_MACHINE = {62: "x64", 183: "arm64", 3: "x86", 40: "arm"}

# PE
_PE_MACHINE = {0x8664: "x64", 0xAA64: "arm64", 0x014C: "x86", 0x01C0: "arm"}

_NATIVE_SUFFIXES = (".so", ".dylib", ".pyd", ".dll")
_ARCH_TOKENS = ("x64", "arm64")
# 供应商目录里出现的架构写法不止一种（chrome-mac-x64 / chrome-linux-arm64 / mac-arm64 ...），
# 用「对立架构的词元」做黑名单，比要求某个确切目录名更耐得住 Playwright 改布局。
_OPPOSITE_TOKENS = {
    "x64": ("-arm64", "_arm64", "aarch64"),
    "arm64": ("-x64", "_x64", "-x86_64", "_x86_64", "amd64"),
}
_VENDORED_BROWSER_DIR = "playwright_browsers"


def _read_head(path: Path, size: int = 64) -> bytes:
    with path.open("rb") as handle:
        return handle.read(size)


def read_arches(path: Path) -> list[str] | None:
    """Return the architectures a binary declares, or None if it is not one.

    A Mach-O fat binary reports every slice it carries; every other format
    reports a single entry.
    """
    head = _read_head(path)
    if len(head) < 20:
        return None

    magic = head[:4]
    if magic in _MACHO_FAT_MAGICS:
        endian = _MACHO_FAT_MAGICS[magic]
        (count,) = struct.unpack(endian + "I", head[4:8])
        # 每个 fat_arch 是 20 字节，cputype 在其起始处；限个上界，别让畸形头把内存吃穿。
        if count > 64:
            return None
        blob = _read_head(path, 8 + 20 * count)
        arches: list[str] = []
        for index in range(count):
            offset = 8 + 20 * index
            if offset + 4 > len(blob):
                break
            (cpu,) = struct.unpack(endian + "I", blob[offset : offset + 4])
            arches.append(_MACHO_CPU.get(cpu, f"unknown(0x{cpu:08x})"))
        return arches or None

    if magic in _MACHO_MAGICS:
        endian = _MACHO_MAGICS[magic]
        (cpu,) = struct.unpack(endian + "I", head[4:8])
        return [_MACHO_CPU.get(cpu, f"unknown(0x{cpu:08x})")]

    if magic == b"\x7fELF":
        endian = "<" if head[5] == 1 else ">"
        (machine,) = struct.unpack(endian + "H", head[18:20])
        return [_ELF_MACHINE.get(machine, f"unknown({machine})")]

    if magic[:2] == b"MZ":
        (pe_offset,) = struct.unpack("<I", head[0x3C:0x40])
        blob = _read_head(path, pe_offset + 8)
        if len(blob) < pe_offset + 8 or blob[pe_offset : pe_offset + 4] != b"PE\x00\x00":
            return None
        (machine,) = struct.unpack("<H", blob[pe_offset + 4 : pe_offset + 6])
        return [_PE_MACHINE.get(machine, f"unknown(0x{machine:04x})")]

    return None


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
        # macOS 的 dist 根上还有个同名 shell wrapper（exec 进 .app），它不是 Mach-O，
        # read_arches 返回 None —— 跳过继续找真的那个，别把 wrapper 当主程序放行。
        if candidate.is_file() and read_arches(candidate) is not None:
            return candidate
    return None


def _iter_native_libs(dist_root: Path):
    for path in dist_root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if _VENDORED_BROWSER_DIR in path.parts:
            continue
        if path.suffix.lower() in _NATIVE_SUFFIXES:
            yield path


def check_dist_arch(dist_root: Path, expect_arch: str, platform: str) -> list[str]:
    """Return a list of human-readable problems; empty means the dist is clean."""
    issues: list[str] = []

    entry = _entry_binary(dist_root, platform)
    if entry is None:
        issues.append(
            "main server binary not found (or not a recognisable executable) under "
            f"{dist_root}"
        )
    else:
        arches = read_arches(entry) or []
        if expect_arch not in arches:
            issues.append(
                f"main binary is {'/'.join(arches) or 'unreadable'}, expected {expect_arch}: "
                f"{entry.relative_to(dist_root)}"
            )

    mismatched: list[str] = []
    for lib in _iter_native_libs(dist_root):
        arches = read_arches(lib)
        if arches is None:
            # 后缀像原生库但根本不是可执行格式（占位文件、文本 stub）——不是架构问题。
            continue
        if expect_arch not in arches:
            mismatched.append(f"{lib.relative_to(dist_root)} is {'/'.join(arches)}")
    if mismatched:
        shown = ", ".join(sorted(mismatched)[:10])
        issues.append(
            f"{len(mismatched)} native library/libraries are not {expect_arch}: {shown}"
            + (" ..." if len(mismatched) > 10 else "")
        )

    browser_root = dist_root / _VENDORED_BROWSER_DIR
    if browser_root.is_dir():
        tokens = _OPPOSITE_TOKENS.get(expect_arch, ())
        bad_paths = sorted(
            str(path.relative_to(dist_root))
            for path in browser_root.rglob("*")
            if path.is_dir()
            and any(token in part.lower() for part in path.parts for token in tokens)
        )
        if bad_paths:
            issues.append(
                f"vendored browser tree carries non-{expect_arch} directories: "
                + ", ".join(bad_paths[:5])
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
        choices=("win", "mac", "linux"),
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
