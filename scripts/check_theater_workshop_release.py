"""Run the frozen workshop smoke in a fresh cwd, without source import paths."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary-dir", type=Path, required=True)
    args = parser.parse_args()
    directory = args.binary_dir.resolve()
    executable = directory / ("projectneko_server.exe" if sys.platform == "win32" else "projectneko_server")
    if not executable.is_file():
        parser.error("compiled backend executable is missing")
    # Generate only non-private fixture data on the build side. The launched
    # binary never imports tests or invokes this development interpreter.
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    from tests.unit.theater_workshop.test_numeric_v2_names import NAMES, named_outline
    from tests.unit.theater_workshop.test_numeric_v2_generation import _generation_setup

    fixture = {"title": "旧信", "names": NAMES, "outline": named_outline(), "setup": _generation_setup()}
    with tempfile.TemporaryDirectory(prefix="neko-workshop-release-") as temporary:
        root = Path(temporary)
        source = root / "fixture.json"
        source.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
        env = {key: value for key, value in os.environ.items()
               if key not in {"PYTHONPATH", "PYTHONHOME", "NEKO_VOICE_IDENTITY_RELEASE_SMOKE"}
               and not key.startswith(("INKAI_", "NEKO_NUMERIC_DRAMA_"))}
        env["NEKO_THEATER_WORKSHOP_REQUIRE_FROZEN"] = "1"
        result = subprocess.run([str(executable), "--theater-workshop-smoke", str(source)],
                                cwd=root, env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=120)
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        if result.returncode or "NEKO_THEATER_WORKSHOP_SMOKE_OK" not in result.stdout:
            raise SystemExit("frozen workshop smoke failed")
        # The macOS top-level file is a shell launcher; hash the actual image.
        image = directory / "projectneko_server.app/Contents/MacOS/projectneko_server"
        if not image.is_file():
            image = executable
        with image.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        print(json.dumps({"platform": sys.platform, "binary_sha256": digest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
