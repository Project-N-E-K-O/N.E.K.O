from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.plugin_unit

_PROBE = """
import sys
import plugin._types as types_pkg
import plugin.sdk as sdk_pkg

models = {"RunStatus", "PluginMeta", "PluginPushMessage", "HealthCheckResponse"}
missing_types = sorted(models - set(dir(types_pkg)))
missing_sdk = sorted({"plugin", "adapter"} - set(dir(sdk_pkg)))
listing = dir(types_pkg)
assert listing == sorted(listing)
# Listing names must not import them.
loaded = sorted(
    name for name in ("plugin._types.models", "plugin.sdk.plugin", "plugin.sdk.adapter")
    if name in sys.modules
)
print(repr((missing_types, missing_sdk, loaded)))
"""


def test_dir_lists_lazy_exports_without_importing_them():
    # A fresh interpreter: other tests may already have imported the lazy modules.
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=120, check=True,
    ).stdout.strip().splitlines()[-1]
    assert out == repr(([], [], []))
