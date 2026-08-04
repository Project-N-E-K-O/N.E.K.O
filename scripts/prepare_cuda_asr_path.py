"""Prefer GPU for local faster-whisper by prepending CUDA/cuBLAS DLL dirs.

Many Windows boxes have a GPU + ctranslate2 CUDA build, but miss system-wide
CUDA Toolkit DLLs. Torch/RVC installs often ship ``cublas64_12.dll``; putting
that directory on PATH (and via os.add_dll_directory) lets Whisper use the GPU.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _candidate_dirs() -> list[Path]:
    roots: list[Path] = []
    explicit = (os.environ.get("NEKO_CUDA_DLL_DIR") or "").strip()
    if explicit:
        roots.append(Path(explicit))

    # Common local packs that already shipped CUDA 12 runtime DLLs.
    for raw in (
        r"D:\RVC\runtime\Lib\site-packages\torch\lib",
        r"D:\RVC1\MXGF.CC_RVC v9.0\runtime\Lib\site-packages\torch\lib",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python*\Lib\site-packages\torch\lib"),
    ):
        if "*" in raw:
            parent = Path(raw.split("*", 1)[0])
            if parent.exists():
                roots.extend(parent.glob("Python*/Lib/site-packages/torch/lib"))
        else:
            roots.append(Path(raw))

    cuda_root = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA")
    if cuda_root.exists():
        roots.extend(sorted(cuda_root.glob("v*/bin"), reverse=True))

    # Also allow a venv torch if the user installed CUDA-enabled torch later.
    try:
        import torch  # type: ignore

        torch_lib = Path(torch.__file__).resolve().parent / "lib"
        roots.append(torch_lib)
    except Exception:
        pass

    return roots


def find_cublas_dir() -> Path | None:
    for directory in _candidate_dirs():
        try:
            if directory.is_dir() and any(directory.glob("cublas64_*.dll")):
                return directory.resolve()
        except OSError:
            continue
    return None


def prepare_cuda_asr_path() -> str | None:
    """Put a cuBLAS directory on PATH for this process. Returns the dir or None."""

    found = find_cublas_dir()
    if found is None:
        return None
    path = str(found)
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    if path not in parts:
        os.environ["PATH"] = path + (os.pathsep + current if current else "")
    if sys.platform == "win32":
        add = getattr(os, "add_dll_directory", None)
        if callable(add):
            try:
                add(path)
            except (FileNotFoundError, OSError):
                pass
    return path


def main() -> int:
    path = prepare_cuda_asr_path()
    if path:
        print(path)
        return 0
    print("", end="")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
