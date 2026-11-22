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
