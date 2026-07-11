"""Mention parsing helpers for live-room topic filtering."""

from __future__ import annotations

import unicodedata


def is_viewer_to_viewer_mention_text(text: str) -> bool:
    compact = " ".join(str(text or "").strip().replace("\uff20", "@").split())
    if "@" not in compact:
        return False
    aliases = {"neko", "\u732b\u732b", "\u5c0f\u5929", "\u732b\u5a18"}
    lowered_aliases = {alias.lower() for alias in aliases}
    saw_non_neko_target = False
    for part in compact.split("@")[1:]:
        target = []
        for ch in part.strip():
            if (
                ch.isspace()
                or ch in ":：,，、;；.!?。？！\\|[]()（）<>《》"
                or unicodedata.category(ch).startswith("S")
            ):
                break
            target.append(ch)
        name = "".join(target).strip()
        if not name:
            continue
        if is_neko_mention_target(name, lowered_aliases):
            return False
        saw_non_neko_target = True
    return saw_non_neko_target


def is_neko_mention_target(name: str, lowered_aliases: set[str]) -> bool:
    lowered_name = str(name or "").strip().lower()
    if not lowered_name:
        return False
    if lowered_name in lowered_aliases:
        return True
    live_address_prefixes = (
        "\u4eca",
        "\u4f60",
        "\u5728",
        "\u80fd",
        "\u53ef",
        "\u5e2e",
        "\u6765",
        "\u8bb2",
        "\u8bf4",
        "\u600e",
        "\u4e3a",
        "\u8981",
        "\u6709",
        "\u662f",
        "\u4f1a",
        "\u60f3",
        "\u64ad",
        "\u8bc4",
        "\u9510",
        "\u65e9",
        "\u665a",
        "\u5462",
        "\u5440",
        "\u554a",
        "\u5417",
        "\u561b",
        "\u5427",
        "\u54c8",
        "what",
        "why",
        "how",
        "can",
        "could",
        "please",
        "pls",
        "pick",
        "rate",
        "tell",
        "help",
        "say",
    )
    for alias in lowered_aliases:
        if not lowered_name.startswith(alias):
            continue
        rest = lowered_name[len(alias) :].lstrip("_-")
        if not rest:
            return True
        if "\u4e00" <= rest[0] <= "\u9fff":
            return True
        if rest.startswith(live_address_prefixes):
            return True
    return False
