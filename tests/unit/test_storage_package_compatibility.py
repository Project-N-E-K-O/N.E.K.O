import importlib

import pytest


@pytest.mark.parametrize(
    ("legacy_name", "package_name"),
    (
        ("utils.storage_layout", "utils.storage.layout"),
        ("utils.storage_policy", "utils.storage.policy"),
        ("utils.storage_migration", "utils.storage.migration"),
        ("utils.storage_path_rewrite", "utils.storage.path_rewrite"),
        ("utils.storage_location_bootstrap", "utils.storage.location_bootstrap"),
    ),
)
def test_legacy_storage_modules_alias_package_implementations(legacy_name, package_name):
    legacy_module = importlib.import_module(legacy_name)
    package_module = importlib.import_module(package_name)

    assert legacy_module is package_module
