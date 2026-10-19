from __future__ import annotations

import json
import re
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_MODELS = ("yui-lolita", "yui-origin")

_EMOTIONS = ("happy", "sad", "angry", "neutral", "surprised")

_GROUPS = (*_EMOTIONS, "Idle")

_FACE_PARAMETER = re.compile(r"^Param(6[6-9]|7[0-9]|8[0-9])$")


def _read_member(name: str, member: str):
    archive = REPO_ROOT / "assets" / f"{name}.tar.gz"
    with tarfile.open(archive, "r:gz") as handle:
        return json.loads(handle.extractfile(member).read().decode("utf-8"))


def _read_model_config(name: str) -> dict:
    return _read_member(name, f"{name}/{name}.model3.json")


def _animated_face_parameters(name: str, motion_file: str) -> set:
    motion = _read_member(name, f"{name}/{motion_file}")
    found = set()
    for curve in motion.get("Curves") or []:
        if curve.get("Target") != "Parameter":
            continue
        identifier = curve.get("Id") or ""
        if _FACE_PARAMETER.match(identifier):
            found.add(identifier)
    return found


@pytest.mark.unit
@pytest.mark.parametrize("name", _MODELS)
def test_emotion_expression_mapping_stays_empty(name: str) -> None:
    config = _read_model_config(name)
    expressions = (config.get("EmotionMapping") or {}).get("expressions") or {}

    overlapping = sorted(emotion for emotion in _EMOTIONS if expressions.get(emotion))
    assert overlapping == [], (
        f"{name}: {overlapping} 又被映射了表情，会和 motion 自带的表情叠加"
    )


@pytest.mark.unit
@pytest.mark.parametrize("name", _MODELS)
def test_every_emotion_group_has_motions(name: str) -> None:
    config = _read_model_config(name)
    motions = (config.get("EmotionMapping") or {}).get("motions") or {}

    missing = [group for group in _GROUPS if not motions.get(group)]
    assert missing == [], f"{name}: 动作组 {missing} 是空的"


@pytest.mark.unit
@pytest.mark.parametrize("name", _MODELS)
def test_motions_are_what_carry_the_emotion_faces(name: str) -> None:
    config = _read_model_config(name)
    motions = (config.get("EmotionMapping") or {}).get("motions") or {}

    for group in _GROUPS:
        animated = set()
        for motion_file in motions.get(group) or []:
            animated |= _animated_face_parameters(name, motion_file)
        assert animated, (
            f"{name}: 动作组 {group} 的 motion 一个表情参数都没驱动，"
            f"情绪表情就没人负责了"
        )