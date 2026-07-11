"""Small text classifiers owned by the danmaku response path."""

from __future__ import annotations


def is_reaction_only(dense_lowered: str) -> bool:
    reaction_markers = (
        "\u54c8\u54c8",
        "\u7b11\u6b7b",
        "\u7ef7\u4e0d\u4f4f",
        "\u8349\u8349",
        "\u725b\u554a",
        "\u725b\u903c",
        "\u597d\u8036",
        "666",
        "lol",
        "lmao",
    )
    return len(dense_lowered) <= 8 and any(
        marker in dense_lowered for marker in reaction_markers
    )


def is_viewer_to_viewer_mention_text(text: str) -> bool:
    compact = " ".join(str(text or "").strip().replace("\uff20", "@").split())
    if "@" not in compact:
        return False
    aliases = {"neko", "\u732b\u732b", "\u5c0f\u5929", "\u732b\u5a18"}
    lowered_aliases = {alias.lower() for alias in aliases}
    saw_viewer_mention = False
    for part in compact.split("@")[1:]:
        target = []
        for ch in part.strip():
            if ch.isspace() or ch in ":\uff1a,\uff0c\u3001;\uff1b\\|[]()\uff08\uff09<>\u300a\u300b":
                break
            target.append(ch)
        name = "".join(target).strip()
        if not name:
            continue
        if _is_neko_mention_target(name, lowered_aliases):
            return False
        saw_viewer_mention = True
    return saw_viewer_mention


def _is_neko_mention_target(name: str, lowered_aliases: set[str]) -> bool:
    lowered_name = str(name or "").strip().lower()
    if not lowered_name or lowered_name in lowered_aliases:
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
        if not rest or rest.startswith(live_address_prefixes):
            return True
    return False
