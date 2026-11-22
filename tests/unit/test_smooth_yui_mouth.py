# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Unit tests for ``scripts/smooth_yui_mouth.py``."""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "smooth_yui_mouth.py"

pytestmark = pytest.mark.unit


def _load_script_module():
    scripts_str = str(SCRIPTS_DIR)
    if scripts_str not in sys.path:
        sys.path.insert(0, scripts_str)
    spec = importlib.util.spec_from_file_location("smooth_yui_mouth", SCRIPT_PATH)
    assert spec and spec.loader, f"failed to load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _load_script_module()


def _raw_motion():
    return {
        "Version": 3,
        "Meta": {
            "Duration": 6.0,
            "Fps": 60.0,
            "Loop": True,
            "AreBeziersRestricted": True,
            "CurveCount": 3,
            "TotalSegmentCount": 4,
            "TotalPointCount": 18,
            "UserDataCount": 0,
            "TotalUserDataSize": 0,
        },
        "Curves": [
            {
                "Target": "Parameter",
                "Id": "ParamMouthOpenY",
                "Segments": [
                    0.0, 0.5,
                    1, 1.0, 1.0, 2.0, 0.1, 3.0, 0.9,
                    1, 4.0, 0.0, 5.0, 1.0, 6.0, 0.6,
                ],
            },
            {
                "Target": "Parameter",
                "Id": "ParamMouthForm",
                "Segments": [
                    0.0, -0.4,
                    0, 2.0, 0.8,
                    0, 4.0, -0.9,
                    0, 6.0, 0.4,
                ],
            },
            {
                "Target": "Parameter",
                "Id": "ParamAngleX",
                "Segments": [0.0, 0.0, 0, 6.0, 5.0],
            },
        ],
    }


def test_first_pass_smooths_and_second_pass_is_exact_noop(tmp_path):
    """Smoothing a raw motion must be idempotent on subsequent runs."""
    motion_file = tmp_path / "idle.motion3.json"
    motion_file.write_text(json.dumps(_raw_motion()), encoding="utf-8")

    MOD.process(motion_file)
    first_bytes = motion_file.read_bytes()
    first_data = json.loads(first_bytes.decode("utf-8"))

    for curve in first_data["Curves"]:
        if curve["Id"] in MOD.TARGET_IDS:
            keys = MOD.extract_keys(curve["Segments"])
            assert keys[0][1] == 0
            assert keys[-1][1] == 0
            assert MOD.is_already_smoothed(curve["Segments"], MOD.NEUTRAL[curve["Id"]])

    MOD.process(motion_file)
    assert motion_file.read_bytes() == first_bytes


def test_repo_smoothed_assets_are_byte_for_byte_unchanged(tmp_path):
    """Running smooth_yui_mouth on checked-in smoothed assets must not alter them."""
    origin_dir = PROJECT_ROOT / "static" / "yui-origin"
    samples = sorted(origin_dir.glob("*.motion3.json"))[:5]
    assert samples, "expected checked-in yui-origin motion files"

    for sample in samples:
        copied = tmp_path / sample.name
        shutil.copyfile(sample, copied)
        before = copied.read_bytes()
        MOD.process(copied)
        assert copied.read_bytes() == before, f"{sample.name} was modified on re-smooth"


def test_atomic_write_failure_preserves_original_and_cleans_temp(tmp_path, monkeypatch):
    """A write failure during os.replace must leave original bytes intact and no .tmp files."""
    motion_file = tmp_path / "idle.motion3.json"
    original_bytes = json.dumps(_raw_motion()).encode("utf-8")
    motion_file.write_bytes(original_bytes)

    def _boom(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(MOD.os, "replace", _boom)

    with pytest.raises(OSError, match="disk full"):
        MOD.process(motion_file)

    assert motion_file.read_bytes() == original_bytes
    assert list(tmp_path.glob("*.tmp")) == []
