"""The monitor server's GET /api/live2d/emotion_mapping keeps to the model folder, like the main server's."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app import monitor


def _link(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as e:  # e.g. Windows without symlink privilege
        pytest.skip(f"symlinks unavailable: {e}")


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def model_dir(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / "user_live2d" / "m"
    path.mkdir(parents=True)
    monkeypatch.setattr(monitor, "find_model_directory", lambda _name: (str(path), "/user_live2d"))
    return path


def test_config_link_leaving_model_dir_is_refused(model_dir, tmp_path):
    _write(tmp_path / "outside" / "o.model3.json", {"EmotionMapping": {"motions": {"happy": ["outside"]}}})
    _link(tmp_path / "outside" / "o.model3.json", model_dir / "m.model3.json")
    result = monitor.get_emotion_mapping("m")
    assert result.status_code == 403
    assert b"outside" not in result.body


def test_inside_config_is_used_next_to_an_outside_link(model_dir, tmp_path):
    _write(tmp_path / "outside" / "o.model3.json", {"EmotionMapping": {"motions": {"happy": ["outside"]}}})
    _link(tmp_path / "outside" / "o.model3.json", model_dir / "z.model3.json")
    _write(model_dir / "m.model3.json", {"EmotionMapping": {"motions": {"happy": ["inside"]}}})
    assert monitor.get_emotion_mapping("m") == {"success": True, "config": {"motions": {"happy": ["inside"]}}}


def test_regular_and_inside_linked_configs_still_work(model_dir):
    _write(model_dir / "real" / "r.model3.json", {"EmotionMapping": {"motions": {"happy": ["inside"]}}})
    _link(model_dir / "real" / "r.model3.json", model_dir / "m.model3.json")
    assert monitor.get_emotion_mapping("m")["config"] == {"motions": {"happy": ["inside"]}}


def test_refused_references_are_left_out_like_the_main_server(model_dir):
    _write(model_dir / "m.model3.json", {"EmotionMapping": {
        "motions": {"happy": ["../x.motion3.json", "motions/ok.motion3.json"]},
        "expressions": {"sad": "e.exp3.json"},
    }})
    assert monitor.get_emotion_mapping("m")["config"] == {
        "motions": {"happy": ["motions/ok.motion3.json"]},
        "expressions": {"sad": ["e.exp3.json"]},
    }


def test_derived_mapping_skips_non_string_expression_files(model_dir):
    _write(model_dir / "m.model3.json", {"FileReferences": {"Expressions": [
        {"Name": "happy_a", "File": 3}, {"Name": "happy_b", "File": "b.exp3.json"}]}})
    assert monitor.get_emotion_mapping("m")["config"]["expressions"] == {"happy": ["b.exp3.json"]}


@pytest.mark.parametrize("config, expected", [
    ([], {"motions": {}, "expressions": {}}),
    ({"FileReferences": "x"}, {"motions": {}, "expressions": {}}),
    ({"FileReferences": {"Motions": {"Idle": 5}, "Expressions": {"a": 1}}}, {"motions": {"Idle": []}, "expressions": {}}),
    ({"FileReferences": {"Expressions": [{"Name": 3, "File": "e.exp3.json"}]}},
     {"motions": {}, "expressions": {"neutral": ["e.exp3.json"]}}),
])
def test_unexpected_shapes_on_disk_do_not_500(model_dir, config, expected):
    _write(model_dir / "m.model3.json", config)
    result = monitor.get_emotion_mapping("m")
    assert getattr(result, "status_code", 200) == 200
    assert result["config"] == expected


def test_non_object_stored_mapping_falls_back_to_derived(model_dir):
    _write(model_dir / "m.model3.json", {"EmotionMapping": [1], "FileReferences": {
        "Motions": {"Idle": [{"File": "motions/idle.motion3.json"}]}}})
    assert monitor.get_emotion_mapping("m")["config"] == {
        "motions": {"Idle": ["motions/idle.motion3.json"]}, "expressions": {}}
