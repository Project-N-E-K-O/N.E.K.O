# -*- coding: utf-8 -*-
"""Vendor mc-agent + portable Node.js into data/mc-agent/ for distribution.

Produces a self-contained tree:

    data/mc-agent/
      ├── node/                      # portable Node.js for Windows
      │     └── node-vX.Y.Z-win-x64/
      │            ├── node.exe
      │            └── npm.cmd ...
      ├── src/                       # mc-agent source (no .git, no tests)
      │     ├── main.js
      │     ├── settings.js
      │     ├── andy.json
      │     ├── package.json
      │     ├── node_modules/        # pre-installed via portable npm
      │     └── ...
      └── 启动mc-agent.bat           # standalone launch entry for end users
                                      # (also lets the N.E.K.O. launcher.py
                                      #  spawn the same node.exe + main.js)

After this script runs successfully, two distribution paths work:

1. **Standalone zip**: zip up data/mc-agent/ and ship to users who want to
   run mc-agent without the N.E.K.O. launcher. They double-click the .bat.

2. **Bundled with N.E.K.O.**: build_nuitka.bat picks data/mc-agent/ up as
   --include-data-dir, the host launcher detects node.exe + main.js and
   spawns the subprocess automatically.

Either way, the same on-disk layout works.

Usage
-----

::

    uv run python scripts/vendor_mc_agent.py --source C:/Users/wehos/Project/mc-agent

    # or pull from upstream fork (slower, network-bound)
    uv run python scripts/vendor_mc_agent.py --git https://github.com/Project-N-E-K-O/mc-agent.git

    uv run python scripts/vendor_mc_agent.py --clean   # nuke data/mc-agent/ first

Windows-only by design; mineflayer / canvas native modules in node_modules
are platform-specific and we only ship a Windows desktop build today.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEST_ROOT = REPO_ROOT / "data" / "mc-agent"

NODE_VERSION = "v20.11.1"
NODE_DIST = f"node-{NODE_VERSION}-win-x64"
NODE_URL = f"https://nodejs.org/dist/{NODE_VERSION}/{NODE_DIST}.zip"

# Files / dirs we never want to ship — keep the vendored src/ small.
SRC_EXCLUDE = {
    ".git",
    ".github",
    "node_modules",  # we reinstall via portable npm; user's local copy may
                     # be wrong arch (WSL / mingw) and we want a clean run
    "tests",
    "tasks",         # ~20MB of evaluation harness data, unused at runtime
    "experiments",
    "wandb",
    "code_records",
    "logs",
    ".claude",
    ".codex",
    "__pycache__",
}


def _info(msg: str) -> None:
    print(f"[vendor-mc-agent] {msg}", flush=True)


def _ensure_windows() -> None:
    # 该脚本拉的是 win-x64 Node zip + 假定 node.exe / npm.cmd 这套打包布局，
    # 在非 Windows 上跑出来的 bundle 不能用。早 fail 比让用户跑到 npm ci
    # 阶段才报错要清晰得多。
    if sys.platform != "win32":
        raise SystemExit(
            "[vendor-mc-agent] Windows-only: this script bundles a portable "
            "win-x64 Node and the node.exe / npm.cmd launch scripts; running "
            "it on other platforms produces a broken bundle."
        )


def _download_portable_node(dest_node_root: Path, *, force: bool) -> Path:
    """Fetch and unzip Node.js for Windows. Returns the path to node.exe."""
    node_subdir = dest_node_root / NODE_DIST
    node_exe = node_subdir / "node.exe"
    if node_exe.exists() and not force:
        _info(f"keep existing portable Node at {node_subdir}")
        return node_exe

    dest_node_root.mkdir(parents=True, exist_ok=True)
    zip_path = dest_node_root / f"{NODE_DIST}.zip"
    _info(f"downloading {NODE_URL} (~30MB)…")
    urllib.request.urlretrieve(NODE_URL, zip_path)
    _info("extracting…")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_node_root)
    zip_path.unlink()
    if not node_exe.exists():
        raise RuntimeError(f"node.exe not found after extract: expected {node_exe}")
    _info(f"portable Node ready at {node_subdir}")
    return node_exe


def _copy_source(src: Path, dest: Path) -> None:
    """Copy mc-agent source tree, skipping junk we never want to ship."""
    if dest.exists():
        shutil.rmtree(dest)
    _info(f"copying source: {src} → {dest}")

    def _ignore(_dir: str, names: list[str]) -> list[str]:
        return [n for n in names if n in SRC_EXCLUDE]

    shutil.copytree(src, dest, ignore=_ignore)


def _clone_source(git_url: str, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    _info(f"cloning {git_url} → {dest}")
    subprocess.run(
        ["git", "clone", "--depth", "1", git_url, str(dest)],
        check=True,
    )
    # Strip metadata after clone — the vendored tree doesn't need .git.
    git_dir = dest / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir)


def _run_npm_ci(src_dir: Path, node_exe: Path) -> None:
    """Run `npm ci --omit=dev` inside the vendored src using portable npm."""
    npm_cmd = node_exe.parent / "npm.cmd"
    if not npm_cmd.exists():
        raise RuntimeError(f"npm.cmd missing alongside node.exe: {npm_cmd}")
    _info(f"running npm ci --omit=dev in {src_dir} (this can take 5-10 min on first run)")
    # Put the portable Node on PATH so any postinstall scripts pick the same
    # node we just downloaded, not whatever happens to be on the user's PATH.
    env = os.environ.copy()
    env["PATH"] = f"{node_exe.parent};{env.get('PATH', '')}"
    subprocess.run(
        [str(npm_cmd), "ci", "--omit=dev"],
        cwd=src_dir,
        env=env,
        check=True,
        shell=True,  # npm.cmd is a batch shim; needs shell=True on Windows
    )
    _info("npm ci complete")


def _write_standalone_launcher(dest_root: Path, node_exe_rel: str) -> None:
    """Write 启动mc-agent.bat — for users who want to run without N.E.K.O."""
    bat = dest_root / "启动mc-agent.bat"
    content = (
        "@echo off\r\n"
        "chcp 65001 >nul 2>&1\r\n"
        "echo Starting mc-agent...\r\n"
        "echo (close this window or Ctrl+C to stop)\r\n"
        "echo.\r\n"
        f'"%~dp0{node_exe_rel}" "%~dp0src\\main.js"\r\n'
        "pause\r\n"
    )
    bat.write_bytes(content.encode("utf-8"))
    _info(f"wrote {bat}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    src_group = parser.add_mutually_exclusive_group()
    src_group.add_argument(
        "--source",
        type=Path,
        help="Local mc-agent checkout to copy from (recommended for dev builds).",
    )
    src_group.add_argument(
        "--git",
        type=str,
        help="Git URL to clone mc-agent from (slower, network-bound).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete data/mc-agent/ entirely before vendoring.",
    )
    parser.add_argument(
        "--force-node",
        action="store_true",
        help="Re-download portable Node even if it already exists.",
    )
    parser.add_argument(
        "--skip-npm",
        action="store_true",
        help="Skip npm ci (useful when iterating on the script itself).",
    )
    args = parser.parse_args()

    _ensure_windows()

    if args.clean and DEST_ROOT.exists():
        _info(f"--clean: removing {DEST_ROOT}")
        shutil.rmtree(DEST_ROOT)

    DEST_ROOT.mkdir(parents=True, exist_ok=True)

    # 1. portable Node
    node_exe = _download_portable_node(DEST_ROOT / "node", force=args.force_node)

    # 2. mc-agent source
    src_dest = DEST_ROOT / "src"
    if args.source:
        if not args.source.exists():
            raise SystemExit(f"--source path does not exist: {args.source}")
        _copy_source(args.source, src_dest)
    elif args.git:
        _clone_source(args.git, src_dest)
    elif src_dest.exists():
        _info(f"using existing {src_dest} (pass --source/--git to refresh)")
    else:
        raise SystemExit(
            "no mc-agent source available. pass --source <local path> or --git <url>."
        )

    # 3. node_modules via portable npm
    if not args.skip_npm:
        _run_npm_ci(src_dest, node_exe)

    # 4. standalone launcher
    node_exe_rel = node_exe.relative_to(DEST_ROOT).as_posix().replace("/", "\\")
    _write_standalone_launcher(DEST_ROOT, node_exe_rel)

    _info("done.")
    _info(f"  Bundle root: {DEST_ROOT}")
    _info(f"  Standalone launcher: {DEST_ROOT / '启动mc-agent.bat'}")
    _info("  To distribute standalone: zip up the data/mc-agent/ tree.")
    _info("  To bundle with N.E.K.O.: run build_nuitka.bat — it'll pick this up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
