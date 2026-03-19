from __future__ import annotations

import importlib

from plugin.sdk_v2 import shared
from plugin.sdk_v2.shared import models
from plugin.sdk_v2.shared import constants
from plugin.sdk_v2.shared.models import errors


def test_shared_and_models_exports() -> None:
    shared_mod = importlib.reload(shared)
    models_mod = importlib.reload(models)

    for name in shared_mod.__all__:
        assert hasattr(shared_mod, name)

    for name in models_mod.__all__:
        assert hasattr(models_mod, name)


def test_error_code_values() -> None:
    assert int(errors.ErrorCode.SUCCESS) == 0
    assert int(errors.ErrorCode.VALIDATION_ERROR) == 400
    assert int(errors.ErrorCode.NOT_FOUND) == 404
    assert int(errors.ErrorCode.TIMEOUT) == 408
    assert int(errors.ErrorCode.CONFLICT) == 409
    assert int(errors.ErrorCode.INTERNAL) == 500


def test_sdk_version_constant() -> None:
    assert constants.SDK_VERSION == "0.1.0"


def test_shared_package_submodules_exist() -> None:
    assert hasattr(shared, "core")
    assert hasattr(shared, "bus")
    assert hasattr(shared, "storage")
    assert hasattr(shared, "runtime")
