"""Resolve the vendored RVC root under the N.E.K.O repo (never D:\\RVC)."""

from __future__ import annotations

from pathlib import Path

# plugin/plugins/rvc_cover/paths.py → repo root is parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_VENDOR_RVC = _REPO_ROOT / "vendor" / "rvc"


def repo_root() -> Path:
    return _REPO_ROOT


def default_rvc_root() -> Path:
    return _DEFAULT_VENDOR_RVC


def default_python_path(rvc_root: Path | None = None) -> Path:
    root = Path(rvc_root) if rvc_root is not None else default_rvc_root()
    return root / "runtime" / "python.exe"


def _is_user_original_rvc_install(path: Path) -> bool:
    """True for a top-level RVC install like D:\\RVC (not our vendor copy)."""
    try:
        resolved = path.resolve()
    except Exception:
        return False
    # Never allow the user's original pack as the working tree.
    if resolved == Path("D:/RVC").resolve() or resolved == Path("D:/rvc").resolve():
        return True
    # Also block any path that is outside the N.E.K.O repo and named exactly "RVC"
    # with a runtime/ next to it — keep writes inside vendor/rvc only.
    try:
        resolved.relative_to(_REPO_ROOT.resolve())
        return False
    except ValueError:
        return resolved.name.lower() == "rvc"


def resolve_rvc_root(raw: str | Path | None = None) -> Path:
    """Resolve configured rvc_root under the N.E.K.O repo.

    Relative paths resolve from the repo root. Absolute paths that point at the
    user's original ``D:\\RVC`` (or similar outside-repo RVC installs) are
    redirected to ``vendor/rvc`` so inference never writes there.
    """
    text = str(raw or "").strip()
    if not text:
        return default_rvc_root()
    path = Path(text)
    if not path.is_absolute():
        path = (_REPO_ROOT / path).resolve()
    else:
        path = path.resolve()
    if _is_user_original_rvc_install(path):
        return default_rvc_root()
    return path
