"""Stage the versioned Python tooling resources in the desktop backend."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]


def stage(destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(f"Plugin tools already exist: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".plugin-tools-", dir=destination.parent) as staging:
        resources = Path(staging) / destination.name
        resources.mkdir()
        _build_resources(resources)
        resources.rename(destination)


def _build_resources(destination: Path) -> None:
    subprocess.run(
        ["uv", "build", "--wheel", "--python", sys.executable, "--out-dir", str(destination)],
        cwd=ROOT,
        check=True,
    )
    (wheel,) = destination.glob("*.whl")
    requirements = destination / "requirements.txt"
    subprocess.run(
        ["uv", "export", "--locked", "--only-group", "plugin-tools", "--no-emit-project",
         "--no-header", "--output-file", str(requirements)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    test_config = destination / "pytest.ini"
    shutil.copyfile(ROOT / "pytest.ini", test_config)
    hashes = {}
    for path in (wheel, requirements, test_config):
        with path.open("rb") as stream:
            hashes[path.name] = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = {
        "schema": 1,
        "platform": sys.platform,
        "architecture": platform.machine().lower(),
        "python": platform.python_version(),
        "wheel": wheel.name,
        "sha256": hashes,
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("directory", nargs="?", type=Path, help="Tool resource directory")
    destination.add_argument("--backend", type=Path, help="Path to the packaged backend executable")
    args = parser.parse_args()
    if args.backend:
        if not args.backend.is_file():
            parser.error(f"Backend executable not found: {args.backend}")
        sys.path.insert(0, str(ROOT))
        from plugin.neko_plugin_cli.tool_resources import plugin_tool_resources_dir

        stage(plugin_tool_resources_dir(args.backend))
    else:
        stage(args.directory.resolve())
