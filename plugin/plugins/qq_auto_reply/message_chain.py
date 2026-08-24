"""消息链模型 — 对齐 KiraAI 的 MessageChain 递归结构。

每个消息元素都有 .repr 属性用于注入 LLM prompt，
MessageChain 可以嵌套（reply.chain, forward.chains）。"""

from __future__ import annotations

import json as _json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, Union


# ── 基础元素 ──────────────────────────────────────────────

class MessageElement(ABC):
    """消息元素基类。"""

    @property
    @abstractmethod
    def repr(self) -> str:
        """LLM prompt 中的文本表示。"""
        ...


class Text(MessageElement):
    def __init__(self, text: str) -> None:
        self.text: str = str(text)

    @property
    def repr(self) -> str:
        return self.text


class Image(MessageElement):
    def __init__(self, url: str = "", file: str = "") -> None:
        self.url: str = str(url or "")
        self.file: str = str(file or "")

    @property
    def repr(self) -> str:
        return "[图片]"


class At(MessageElement):
    def __init__(self, pid: str, nickname: str = "") -> None:
        self.pid: str = str(pid)
        self.nickname: str = str(nickname or "")

    @property
    def repr(self) -> str:
        if self.pid == "all":
            return "[@全体成员]"
        if self.nickname:
            return f"[@{self.nickname}]"
        return f"[@用户{self.pid}]"


class Reply(MessageElement):
    def __init__(self, message_id: str, chain: Optional[MessageChain] = None) -> None:
        self.message_id: str = str(message_id)
        self.chain: Optional[MessageChain] = chain  # 被引用消息的内容链

    @property
    def repr(self) -> str:
        if self.chain:
            return self.chain.repr
        return f"[引用 {self.message_id}]"


class Forward(MessageElement):
    def __init__(self, chains: list[MessageChain]) -> None:
        self.chains: list[MessageChain] = chains

    @property
    def repr(self) -> str:
        return "[转发]"


class Emoji(MessageElement):
    def __init__(self, emoji_id: str) -> None:
        self.emoji_id: str = str(emoji_id)

    @property
    def repr(self) -> str:
        return f"[表情 {self.emoji_id}]"


class Sticker(MessageElement):
    def __init__(self, sticker_bs64: str = "", sticker_id: str = "") -> None:
        self.sticker_bs64: str = str(sticker_bs64 or "")
        self.sticker_id: str = str(sticker_id or "")

    @property
    def repr(self) -> str:
        return "[动画表情]"


class Record(MessageElement):
    def __init__(self, bs64: str = "", file_id: str = "") -> None:
        self.bs64: str = str(bs64 or "")
        self.file_id: str = str(file_id or "")

    @property
    def repr(self) -> str:
        return "[语音]"


class Notice(MessageElement):
    def __init__(self, text: str) -> None:
        self.text: str = str(text)

    @property
    def repr(self) -> str:
        return self.text


class Poke(MessageElement):
    def __init__(self, pid: str) -> None:
        self.pid: str = str(pid)

    @property
    def repr(self) -> str:
        return "[戳一戳]"


class File(MessageElement):
    def __init__(self, name: str = "", bs64: str = "", url: str = "") -> None:
        self.name: str = str(name or "")
        self.bs64: str = str(bs64 or "")
        # 可选直链 URL（Lagrange 类后端 data.url / NapCat get_*_file_url 换取），
        # 纯增量：repr / bs64 行为不变。
        self.url: str = str(url or "")

    @property
    def repr(self) -> str:
        return f"[文件 {self.name}]" if self.name else "[文件]"


class JsonCard(MessageElement):
    """QQ JSON 卡片消息（小程序/分享卡片等）。"""
    def __init__(self, raw_json: str) -> None:
        self.raw: str = str(raw_json or "")
        self.title: str = ""
        self.desc: str = ""
        self.app: str = ""
        self.nick: str = ""
        self.prompt: str = ""
        self._parse()

    def _parse(self) -> None:
        try:
            card = _json.loads(self.raw)
            detail = card.get("meta", {}).get("detail_1", {})
            self.title = str(detail.get("title") or "")
            self.desc = str(detail.get("desc") or "")
            self.app = str(card.get("app") or "")
            self.nick = str(detail.get("host", {}).get("nick") or "")
            self.prompt = str(card.get("prompt") or "")
        except Exception:
            pass

    @property
    def repr(self) -> str:
        parts: list[str] = ["[卡片"]
        if self.title:
            parts.append(f" {self.title}")
        if self.desc:
            parts.append(f": {self.desc}")
        if self.nick:
            parts.append(f" (来自{self.nick})")
        parts.append("]")
        return "".join(parts)


# ── 消息链 ────────────────────────────────────────────────

@dataclass
class MessageChain:
    """有序消息元素列表，支持嵌套（reply/forward）。"""
    elements: list[MessageElement] = field(default_factory=list)
    sender_name: str = ""
    sender_id: str = ""
    timestamp: int = 0
    message_id: str = ""

    def add(self, element: MessageElement) -> "MessageChain":
        self.elements.append(element)
        return self

    @property
    def repr(self) -> str:
        """拼接所有元素的 repr，用于注入 LLM 上下文。"""
        return "".join(e.repr for e in self.elements)

    @property
    def plain_text(self) -> str:
        """提取纯文本（仅 Text 元素）。"""
        return "".join(e.text for e in self.elements if isinstance(e, Text))

    @staticmethod
    def empty() -> "MessageChain":
        return MessageChain()


# ── 构建工具 ──────────────────────────────────────────────

def chain_from_onebot_message(msg: dict[str, Any]) -> MessageChain:
    """从 OneBot 消息 dict 构建消息链（不含嵌套展开）。"""
    chain = MessageChain(
        sender_name=(msg.get("sender") or {}).get("nickname") or str(msg.get("user_id") or ""),
        sender_id=str(msg.get("user_id") or ""),
        timestamp=int(msg.get("time") or 0),
        message_id=str(msg.get("message_id") or ""),
    )
    segments = msg.get("message") or []
    if not isinstance(segments, list):
        return chain
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        t = seg.get("type") or ""
        data = seg.get("data") or {}
        if t == "text":
            chain.add(Text(str(data.get("text") or "")))
        elif t == "image":
            chain.add(Image(
                url=str(data.get("url") or ""),
                file=str(data.get("file") or ""),
            ))
        elif t == "at":
            chain.add(At(pid=str(data.get("qq") or "")))
        elif t == "reply":
            chain.add(Reply(message_id=str(data.get("id") or "")))
        elif t == "face":
            chain.add(Emoji(emoji_id=str(data.get("id") or "")))
        elif t == "record":
            chain.add(Record(file_id=str(data.get("file") or "")))
        elif t == "forward":
            chain.add(Forward(chains=[]))
        elif t == "video":
            chain.add(Text("[视频]"))
        elif t == "json":
            chain.add(JsonCard(raw_json=str(data.get("data") or "")))
        elif t == "file":
            chain.add(File(name=str(data.get("file") or "")))
    return chain
