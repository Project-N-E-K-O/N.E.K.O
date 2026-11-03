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
"""Unit tests for ``scripts/slim_live2d_motions.py``.

The writer is shared with ``smooth_yui_mouth.py``, so these pin the parts
that would silently damage motion files if changed: six-place rounding,
untouched structure and metadata, and a stable second pass.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "slim_live2d_motions.py"

pytestmark = pytest.mark.unit


def _load_script_module():
    spec = importlib.util.spec_from_file_location("slim_live2d_motions", SCRIPT_PATH)
    assert spec and spec.loader, f"failed to load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _load_script_module()


def _motion():
    return {
        "Version": 3,
        "Meta": {
            "Duration": 5.986212730407715,
            "Fps": 60.0,
            "Loop": True,
            "AreBeziersRestricted": True,
            "CurveCount": 2,
            "TotalSegmentCount": 2,
            "TotalPointCount": 6,
            "UserDataCount": 0,
            "TotalUserDataSize": 0,
        },
        "Curves": [
            {
                "Target": "Parameter",
                "Id": "ParamAngleX",
                "Segments": [
                    0.0, 0.4147433042526245,
                    1, 0.19029812514781952, 4.7826690673828125,
                    0.3805962502956390, -4.324324131011963,
                    0.5708943605422974, -0.0000000001,
                ],
            },
            {
                "Target": "PartOpacity",
                "Id": "PartArm",
                "Segments": [0.0, 1.0, 0, 5.986212730407715, 1.0],
            },
        ],
    }


def test_rounds_floats_to_six_places_and_collapses_integers():
    out = json.loads(MOD.dump_motion(_motion()))
    assert out["Meta"]["Duration"] == 5.986213
    assert out["Curves"][0]["Segments"] == [
        0, 0.414743, 1, 0.190298, 4.782669, 0.380596, -4.324324, 0.570894, 0,
    ]
    # 60.0 / 1.0 / -1e-10 become plain integers; "-0" must not appear.
    text = MOD.dump_motion(_motion())
    assert '"Fps":60,' in text
    assert "-0," not in text and "-0]" not in text
    assert out["Curves"][1]["Segments"] == [0, 1, 0, 5.986213, 1]


def test_keeps_structure_order_and_metadata():
    src = _motion()
    out = json.loads(MOD.dump_motion(src))
    assert list(out) == list(src)
    assert list(out["Meta"]) == list(src["Meta"])
    for key in ("Loop", "AreBeziersRestricted", "CurveCount", "TotalSegmentCount", "TotalPointCount"):
        assert out["Meta"][key] == src["Meta"][key]
    assert [(c["Target"], c["Id"], len(c["Segments"])) for c in out["Curves"]] == [
        (c["Target"], c["Id"], len(c["Segments"])) for c in src["Curves"]
    ]


def test_output_is_compact_and_stable():
    text = MOD.dump_motion(_motion())
    assert "\n" not in text and ", " not in text and ": " not in text
    assert MOD.dump_motion(json.loads(text)) == text


def test_rounding_never_reorders_close_times():
    times = [1.0, 1.0000004, 1.0000006, 1.0000014, 1.0000016]
    rounded = json.loads(MOD.dump_motion(times))
    assert rounded == sorted(rounded)


def test_main_rewrites_files_and_second_pass_is_noop(tmp_path, monkeypatch):
    path = tmp_path / "idle1.motion3.json"
    path.write_text(json.dumps(_motion(), indent=4), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["slim_live2d_motions.py", "--root", str(tmp_path)])
    MOD.main()
    first = path.read_bytes()
    assert first == MOD.dump_motion(_motion()).encode("utf-8")
    MOD.main()
    assert path.read_bytes() == first
