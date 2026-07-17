"""Request, reaction, and runtime-feedback filters for active topics."""

from __future__ import annotations


def is_direct_neko_request_or_ack(dense_lowered: str) -> bool:
    if not any(target in dense_lowered for target in ("\u732b\u732b", "neko")):
        return False
    chinese_markers = (
        "\u8bb2\u8bb2",
        "\u8bf4\u8bf4",
        "\u804a\u804a",
        "\u8bc4\u4ef7\u4e00\u4e0b",
        "\u9510\u8bc4\u4e00\u4e0b",
        "\u70b9\u8bc4\u4e00\u4e0b",
        "\u5e2e\u6211",
        "\u7ed9\u6211",
        "\u80fd\u4e0d\u80fd",
        "\u53ef\u4e0d\u53ef\u4ee5",
        "\u53ef\u4ee5\u4e0d\u53ef\u4ee5",
        "\u8981\u4e0d\u8981",
        "\u8c22\u8c22",
        "\u611f\u8c22",
        "\u8f9b\u82e6\u4e86",
    )
    english_markers = (
        "tellus",
        "helpme",
        "giveme",
        "ratemy",
        "tellme",
        "canyou",
        "couldyou",
        "willyou",
        "wouldyou",
        "please",
        "pls",
        "thankyou",
        "thanks",
        "thx",
    )
    return any(marker in dense_lowered for marker in chinese_markers + english_markers)


def is_untargeted_request_or_reaction(dense_lowered: str) -> bool:
    return is_untargeted_request(dense_lowered) or is_reaction_only(dense_lowered)


def is_untargeted_request(dense_lowered: str) -> bool:
    request_markers = (
        "\u8bb2\u8bb2",
        "\u8bf4\u8bf4",
        "\u804a\u804a",
        "\u8bc4\u4ef7\u4e00\u4e0b",
        "\u9510\u8bc4\u4e00\u4e0b",
        "\u70b9\u8bc4\u4e00\u4e0b",
        "\u63a8\u8350\u4e00\u4e0b",
        "\u9009\u4e00\u4e0b",
        "\u8d77\u4e2a\u5916\u53f7",
        "\u5e2e\u6211",
        "\u7ed9\u6211",
        "tellme",
        "recommendme",
        "giveme",
        "ratemy",
        "helpme",
        "canyou",
        "couldyou",
        "please",
        "pls",
    )
    return any(marker in dense_lowered for marker in request_markers)


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
    return len(dense_lowered) <= 8 and any(marker in dense_lowered for marker in reaction_markers)


def is_live_test_or_runtime_feedback(dense_lowered: str) -> bool:
    control_markers = (
        "\u4e0b\u4e00\u6b65",
        "\u770b\u72b6\u6001",
        "\u68c0\u6d4b\u72b6\u6001",
        "\u91cd\u542f",
        "\u5173\u95ed",
        "\u5f00\u542f",
        "\u63d0\u4ea4",
        "\u63a8\u9001",
        "\u6d4b\u8bd5\u7ed3\u675f",
        "nextstep",
        "checkstatus",
        "restart",
        "reload",
        "shutdown",
    )
    if any(marker in dense_lowered for marker in control_markers):
        return True
    feedback_markers = (
        "\u5ef6\u8fdf",
        "\u5361\u4e86",
        "\u5361\u4f4f",
        "\u6709\u70b9\u957f",
        "\u592a\u957f",
        "\u56de\u590d\u957f",
        "\u8f93\u51fa\u957f",
        "\u6ca1\u8f93\u51fa",
        "\u6ca1\u6709\u8f93\u51fa",
        "\u6ca1\u89e6\u53d1",
        "\u89e6\u53d1\u4e86",
        "latency",
        "toolong",
        "nooutput",
        "notriggered",
    )
    return any(marker in dense_lowered for marker in feedback_markers)
