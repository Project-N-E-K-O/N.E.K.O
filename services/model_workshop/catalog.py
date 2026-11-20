"""Adapt the existing Live2D discovery chain without reversing utility dependencies."""

from pathlib import Path
from urllib.parse import quote

from utils.config_manager import get_config_manager
from utils.frontend_utils import find_model_directory as _find_directory
from utils.frontend_utils import find_models as _find_models

from .runtime import filter_catalog, snapshot


def find_models():
    """Preserve existing built-in/Steam discovery and local model identities."""
    models = _find_models()
    return filter_catalog(get_config_manager(), models)


def find_model_directory(model_name):
    """Check the actual selected directory after legacy CFA/source precedence."""
    directory, prefix = _find_directory(model_name)
    if directory:
        snapshot(get_config_manager()).require(directory)
    return directory, prefix


def public_result(config, row):
    """Return existing resource URLs and raw attribution text, never filesystem roots or Steam IDs."""
    kind = row["model_format"]
    relative = Path(row["local_entrypoint"]).relative_to(kind).as_posix()
    prefix = {"live2d": "/user_live2d", "vrm": "/user_vrm", "mmd": "/user_mmd"}[kind]
    if kind == "live2d":
        readable = getattr(config, "readable_live2d_dir", None)
        if readable and Path(readable).resolve() != Path(config.live2d_dir).resolve():
            prefix = "/user_live2d_local"
    return {
        **{
            key: row[key]
            for key in (
                "package_sha256",
                "install_id",
                "model_format",
                "display_name",
                "attribution",
                "license",
            )
        },
        "url": prefix + "/" + quote(relative, safe="/"),
        "name": Path(row["target"]).name
        if kind == "live2d"
        else Path(row["local_entrypoint"]).stem,
        "status": row.get("status", "installed"),
        "already_installed": row.get("already_installed", False),
    }
