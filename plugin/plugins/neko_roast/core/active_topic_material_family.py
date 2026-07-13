"""Material family classification for active engagement topics."""

from __future__ import annotations

import re


_AB_CHOICE_RE = re.compile(r"(?<![a-z0-9])a\s*[/|]\s*b(?![a-z0-9])", re.I)


def host_material_family(material: dict | None) -> str:
    if not isinstance(material, dict):
        return ""
    explicit_family = str(material.get("family") or "").strip()
    if explicit_family:
        return explicit_family
    combined = " ".join(
        str(material.get(field) or "")
        for field in ("key", "title", "fun_axis", "shape", "preferred_shape")
    )
    dense = _dense_text(combined)
    if not dense:
        return ""
    if any(
        marker in dense
        for marker in (
            "oneword",
            "onechar",
            "password",
            "\u4e00\u4e2a\u5b57",
            "\u4e00\u4e2a\u8bcd",
            "\u4e09\u5b57",
            "\u6697\u53f7",
        )
    ):
        return "short_callback"
    if any(
        marker in dense
        for marker in (
            "choice",
            "eitheror",
            "\u4e8c\u9009\u4e00",
            "\u8fd8\u662f",
            "\u9009\u4e00",
        )
    ) or _AB_CHOICE_RE.search(combined):
        return "choice_vote"
    if any(
        marker in dense
        for marker in (
            "snack",
            "drink",
            "dessert",
            "\u5c0f\u751c\u98df",
            "\u751c\u98df",
            "\u70ed\u996e",
            "\u996e\u6599",
            "\u5c0f\u9c7c\u5e72",
        )
    ):
        return "food_drink"
    if any(
        marker in dense
        for marker in (
            "serious",
            "hosting",
            "hostscore",
            "\u4e3b\u64ad\u529b",
            "\u6b63\u7ecf\u4e3b\u64ad",
            "\u50cf\u4e0d\u50cf\u4e3b\u64ad",
        )
    ):
        return "host_self_test"
    if any(
        marker in dense
        for marker in (
            "keyboard",
            "screen",
            "desk",
            "\u952e\u76d8",
            "\u5c4f\u5e55",
            "\u684c\u9762",
            "\u6c34\u676f",
            "\u96f6\u98df",
        )
    ):
        return "object_scene"
    if any(
        marker in dense
        for marker in (
            "radio",
            "blanket",
            "weather",
            "temperature",
            "mood",
            "stamp",
            "\u7535\u53f0",
            "\u6bdb\u6bef",
            "\u6e29\u5ea6",
            "\u6674\u5929",
            "\u5c0f\u96e8",
            "\u6c14\u6c1b",
            "\u72b6\u6001",
            "\u5fc3\u60c5",
            "\u7ae0",
        )
    ):
        return "room_mood"
    if any(
        marker in dense
        for marker in (
            "tease",
            "\u5410\u69fd",
            "\u88ab\u81ea\u5df1",
            "\u5148\u522b\u7b11",
        )
    ):
        return "tease"
    if any(
        marker in dense
        for marker in (
            "challenge",
            "mission",
            "\u6311\u6218",
            "\u4efb\u52a1",
            "\u59ff\u52bf",
        )
    ):
        return "micro_challenge"
    axis = str(material.get("fun_axis") or "").strip()
    shape = str(material.get("shape") or material.get("preferred_shape") or "").strip()
    return axis or shape


def _dense_text(value: str) -> str:
    lowered = value.lower()
    return "".join(ch for ch in lowered if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
