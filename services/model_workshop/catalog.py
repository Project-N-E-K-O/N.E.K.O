"""Adapt the existing Live2D discovery chain without reversing utility dependencies."""

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
