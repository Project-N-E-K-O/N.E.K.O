"""Small text guards for live-only conversation boundaries."""

from __future__ import annotations

import re


_SUPPORT_CLAIM_ACTION_TOKENS = (
    "\u6295\u5582",
    "\u6253\u8d4f",
    "\u8d5e\u52a9",
    "\u4e0a\u8230",
    "\u5145\u7535",
    "\u7834\u8d39",
    "donated",
    "donate",
    "tipped",
    "tip",
    "gifted",
)

_SUPPORT_CLAIM_SEND_ACTION_TOKENS = (
    "\u8d60\u9001",
    "\u9001\u4e86",
    "\u9001\u4f60",
    "\u9001\u51fa",
    "\u53d1\u9001",
    "\u53d1\u4e86",
    "sent",
)

_SUPPORT_CLAIM_OBJECT_TOKENS = (
    "\u793c\u7269",
    "\u5c0f\u82b1\u82b1",
    "\u5c0f\u5fc3\u5fc3",
    "\u5c0f\u9c7c\u5e72",
    "\u9c7c\u5e72",
    "\u4eba\u6c14\u7968",
    "\u8db3\u8ff9",
    "\u7c89\u4e1d\u56e2\u706f\u724c",
    "\u706f\u724c",
    "\u8d85\u7ea7\u5927\u706b\u7bad",
    "\u5927\u706b\u7bad",
    "\u706b\u7bad",
    "\u98de\u8239",
    "b\u7ad9\u58f9\u53f7",
    "\u9192\u76ee\u7559\u8a00",
    "\u8230\u957f",
    "\u63d0\u7763",
    "\u603b\u7763",
    "\u5927\u822a\u6d77",
    "superchat",
    "super chat",
    "sc",
    "gift",
)

_SUPPORT_CLAIM_ACTOR_TOKENS = (
    "\u6211",
    "\u4ffa",
    "\u54b1",
    "\u7ed9\u4f60",
    "\u7ed9\u732b\u732b",
    "\u7ed9\u672c\u55b5",
    "\u9001\u4f60",
)

_SUPPORT_CLAIM_DONE_TOKENS = (
    "\u4e86",
    "\u5566",
    "\u521a",
    "\u5df2\u7ecf",
    "\u5df2",
    "\u6765\u4e86",
    "\u5b89\u6392",
)

_SUPPORT_CLAIM_THANK_TOKENS = (
    "\u8c22\u8c22",
    "\u611f\u8c22",
    "\u591a\u8c22",
)

_SUPPORT_CLAIM_DISCUSSION_TOKENS = (
    "\u529f\u80fd",
    "\u673a\u5236",
    "\u8bbe\u7f6e",
    "\u6309\u94ae",
    "\u5165\u53e3",
    "\u600e\u4e48",
    "\u5982\u4f55",
    "\u4e3a\u4ec0\u4e48",
    "\u591a\u5c11\u94b1",
    "\u4ef7\u683c",
    "\u89c4\u5219",
    "\u699c",
    "\u68c0\u6d4b",
    "\u8bcd\u8bed",
    "\u4e4b\u7c7b",
    "\u5982\u679c",
    "\u5047\u5982",
    "\u6bd4\u5982",
    "\u4f1a\u4e0d\u4f1a",
    "\u80fd\u4e0d\u80fd",
)

_OWNER_MEMORY_TOKENS = (
    "\u78b3\u57fa\u751f\u7269",
    "\u4e3b\u4eba",
    "\u4e3b\u5b50",
    "\u634f\u6211",
    "\u634f\u8033\u6735",
    "\u63c9\u6211",
    "rua\u6211",
    "\u540e\u53f0\u64cd\u4f5c\u8005",
    "\u540e\u53f0\u4eba\u7c7b",
    "\u4eba\u7c7b\u4e3b\u64ad",
    "\u4f60\u76f4\u64ad\u95f4",
)

_STAGE_ACTION_TOKENS = (
    "\u5bf9\u7740",
    "\u626c\u4e86\u626c",
    "\u626c\u4e0b\u5df4",
    "\u62ac\u722a",
    "\u4f38\u722a",
    "\u7529\u5c3e",
    "\u5c3e\u5df4",
    "\u8033\u6735",
    "\u7728\u773c",
    "\u6b6a\u5934",
    "\u53c9\u8170",
    "\u62cd\u684c",
    "\u51d1\u8fd1",
    "\u770b\u5411",
)

_BRACKETED_RE = re.compile(r"[\(\uff08\[]([^()\uff08\uff09\[\]]{1,32})[\)\uff09\]]")


def dense_text(value: str) -> str:
    return "".join(
        ch.casefold()
        for ch in str(value or "")
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )


def looks_like_support_claim_text(text: str) -> bool:
    lowered = str(text or "").casefold()
    dense = dense_text(lowered)
    if not dense:
        return False
    has_action = _has_dense_token(dense, _SUPPORT_CLAIM_ACTION_TOKENS)
    has_send_action = _has_dense_token(dense, _SUPPORT_CLAIM_SEND_ACTION_TOKENS)
    has_object = _has_dense_token(dense, _SUPPORT_CLAIM_OBJECT_TOKENS)
    has_actor = _has_dense_token(dense, _SUPPORT_CLAIM_ACTOR_TOKENS)
    has_done = _has_dense_token(dense, _SUPPORT_CLAIM_DONE_TOKENS)
    has_thanks = _has_dense_token(dense, _SUPPORT_CLAIM_THANK_TOKENS)
    has_discussion = _has_dense_token(dense, _SUPPORT_CLAIM_DISCUSSION_TOKENS)
    english_claim = any(
        phrase in lowered
        for phrase in (
            "i sent a gift",
            "i gifted",
            "sent you a gift",
            "donated to you",
            "tipped you",
        )
    )
    if english_claim:
        return True
    if not (has_action or has_send_action or has_object):
        return False
    if has_discussion and not (has_actor and (has_action or has_thanks) and (has_object or has_done)):
        return False
    if has_action and (has_actor or has_done or has_object):
        return True
    if has_send_action and has_object:
        return True
    if has_thanks and has_object and not has_discussion:
        return True
    return has_object and has_actor and has_done


def _has_dense_token(dense: str, tokens: tuple[str, ...]) -> bool:
    return any(token.casefold().replace(" ", "") in dense for token in tokens)


def looks_like_idiom_chain_start(text: str) -> bool:
    dense = dense_text(text)
    return "\u6210\u8bed\u63a5\u9f99" in dense or dense.endswith("\u63a5\u9f99")


def looks_like_idiom_chain_turn(text: str) -> bool:
    dense = dense_text(text)
    if len(dense) != 4:
        return False
    return all("\u4e00" <= ch <= "\u9fff" for ch in dense)


def context_mentions_idiom_chain(*blocks: str) -> bool:
    return any(looks_like_idiom_chain_start(block) for block in blocks if block)


def looks_like_stage_direction_output(text: str) -> bool:
    raw = str(text or "")
    for match in _BRACKETED_RE.finditer(raw):
        inner = match.group(1)
        if any(token in inner for token in _STAGE_ACTION_TOKENS):
            return True
    stripped = raw.strip()
    return stripped.startswith("*") and stripped.endswith("*") and any(
        token in stripped for token in _STAGE_ACTION_TOKENS
    )


def looks_like_owner_memory_leak(text: str) -> bool:
    dense = dense_text(text)
    return any(token.casefold().replace(" ", "") in dense for token in _OWNER_MEMORY_TOKENS)
