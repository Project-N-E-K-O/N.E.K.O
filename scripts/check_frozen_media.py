"""Run the bundled media smoke without starting servers or using network APIs."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(sys.argv[1]).resolve()
    binary = root / ("projectneko_server.exe" if sys.platform == "win32" else "projectneko_server")
    env = {**os.environ, "NEKO_MEDIA_RELEASE_SMOKE": "1"}
    result = subprocess.run([str(binary)], env=env, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=120,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print(result.stdout)
    print(result.stderr, file=sys.stderr)
    if result.returncode or "NEKO_MEDIA_RELEASE_SMOKE_OK" not in result.stdout:
        raise SystemExit("Frozen media smoke failed: PyAV/codecs/worker unavailable")


if __name__ == "__main__":
    main()
