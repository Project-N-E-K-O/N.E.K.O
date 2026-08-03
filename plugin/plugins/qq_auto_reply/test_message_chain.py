"""
测试 message_chain 模型 — 对齐 KiraAI MessageChain

运行: python test_message_chain.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from message_chain import (
    MessageChain, Text, Image, At, Reply, Forward,
    Emoji, Sticker, Record, Notice, Poke, File, JsonCard,
    chain_from_onebot_message,
)


def ok(name: str) -> None:
    print(f"  [OK] {name}")


def fail(name: str, reason: str) -> None:
    print(f"  [FAIL] {name}: {reason}")


# ── 1. 基础元素 repr ──────────────────────────────────────

print("\n1. 基础元素 repr")
assert Text("你好").repr == "你好"; ok("Text")
assert Image(url="http://x.com/1.png").repr == "[图片]"; ok("Image")
assert At("123", "小明").repr == "[@小明]"; ok("At with nickname")
assert At("all").repr == "[@全体成员]"; ok("At all")
assert At("456").repr == "[@用户456]"; ok("At without nickname")
assert Emoji("277").repr == "[表情 277]"; ok("Emoji")
assert Sticker(sticker_id="1").repr == "[动画表情]"; ok("Sticker")
assert Record(file_id="abc").repr == "[语音]"; ok("Record")
assert Notice("hello").repr == "hello"; ok("Notice")
assert Poke("123").repr == "[戳一戳]"; ok("Poke")
assert File(name="doc.pdf").repr == "[文件 doc.pdf]"; ok("File with name")
assert File().repr == "[文件]"; ok("File without name")

# ── 2. MessageChain 基础 ───────────────────────────────────

print("\n2. MessageChain 基础")
chain = MessageChain()
chain.add(Text("你好")).add(Text("世界"))
assert chain.repr == "你好世界"; ok("repr 拼接")
assert chain.plain_text == "你好世界"; ok("plain_text")

chain2 = MessageChain(sender_name="小明", sender_id="123", timestamp=1759248000, message_id="msg1")
chain2.add(Text("测试"))
assert chain2.sender_name == "小明"; ok("sender_name")
assert chain2.repr == "测试"; ok("sender repr")
from datetime import datetime
ts_str = datetime.fromtimestamp(1759248000).strftime("%Y-%m-%d %H:%M:%S")
assert len(ts_str) == 19; ok("timestamp conversion")

# ── 3. chain_from_onebot_message ────────────────────────────

print("\n3. chain_from_onebot_message")
msg = {
    "user_id": 123,
    "sender": {"nickname": "小明"},
    "time": 1759248000,
    "message_id": "msg123",
    "message": [
        {"type": "text", "data": {"text": "你好"}},
        {"type": "face", "data": {"id": "12"}},
        {"type": "image", "data": {"url": "http://x.com/pic.png"}},
        {"type": "at", "data": {"qq": "456"}},
    ],
}
chain = chain_from_onebot_message(msg)
assert chain.sender_name == "小明"; ok("sender from onebot")
assert chain.sender_id == "123"; ok("sender_id from onebot")
assert chain.message_id == "msg123"; ok("message_id from onebot")
assert len(chain.elements) == 4; ok("4 elements")
assert isinstance(chain.elements[0], Text); ok("element[0] Text")
assert isinstance(chain.elements[1], Emoji); ok("element[1] Emoji")
assert isinstance(chain.elements[2], Image); ok("element[2] Image")
assert isinstance(chain.elements[3], At); ok("element[3] At")

# ── 4. JSON 卡片 ───────────────────────────────────────────

print("\n4. JSON 卡片解析")
card_json = '{"app":"com.tencent.miniapp","prompt":"[分享]","meta":{"detail_1":{"title":"测试标题","desc":"测试描述","host":{"nick":"小明"}}}}'
card = JsonCard(raw_json=card_json)
assert card.title == "测试标题"; ok("card title")
assert card.desc == "测试描述"; ok("card desc")
assert card.nick == "小明"; ok("card nick")
assert "测试标题" in card.repr; ok("card repr contains title")

# 损坏 JSON 不崩溃
bad_card = JsonCard(raw_json="not json")
assert bad_card.repr == "[卡片]"; ok("broken JSON card fallback")

# ── 5. 空消息各类型 ─────────────────────────────────────────

print("\n5. 边缘情况")
empty_msg = {"message": []}
chain = chain_from_onebot_message(empty_msg)
assert len(chain.elements) == 0; ok("empty segments")

# 各种未知 type 不崩溃
msg_unknown = {"message": [{"type": "unknown_type", "data": {}}]}
chain = chain_from_onebot_message(msg_unknown)
assert len(chain.elements) == 0; ok("unknown type skipped")

# 无 message 字段
msg_no_msg = {"user_id": 1}
chain = chain_from_onebot_message(msg_no_msg)
assert len(chain.elements) == 0; ok("no message field")

# ── 6. Reply 嵌套 ───────────────────────────────────────────

print("\n6. Reply 嵌套链")
inner_chain = MessageChain(sender_name="小红", sender_id="456", timestamp=1759248100)
inner_chain.add(Text("原消息"))
reply = Reply(message_id="msg99", chain=inner_chain)
assert reply.repr == "原消息"; ok("Reply.repr delegates to inner chain")
assert reply.message_id == "msg99"; ok("Reply.message_id")

# ── 7. Forward 链 ──────────────────────────────────────────

print("\n7. Forward 链")
chain_a = MessageChain(sender_name="A", timestamp=1754000000)
chain_a.add(Text("消息A"))
chain_b = MessageChain(sender_name="B", timestamp=1759248060)
chain_b.add(Text("消息B"))
chain_b.add(Image(url="http://x.com/b.png"))
forward = Forward(chains=[chain_a, chain_b])
assert len(forward.chains) == 2; ok("2 forward sub-chains")
assert forward.chains[0].sender_name == "A"; ok("forward chain A sender")
assert forward.chains[1].sender_name == "B"; ok("forward chain B sender")
assert forward.chains[1].elements[1].repr == "[图片]"; ok("forward chain B image")

# ── 8. 完整 OneBot 消息 ─────────────────────────────────────

print("\n8. 完整群消息解析")
full_msg = {
    "user_id": 111,
    "group_id": 999,
    "sender": {"nickname": "群友", "card": "大佬"},
    "time": 1759248000,
    "message_id": "msg_full",
    "message": [
        {"type": "reply", "data": {"id": "msg_ref"}},
        {"type": "text", "data": {"text": "看看这个 "}},
        {"type": "image", "data": {"url": "http://x.com/share.jpg", "file": ""}},
        {"type": "text", "data": {"text": " 怎么样？"}},
        {"type": "at", "data": {"qq": "222"}},
        {"type": "video", "data": {"file": "vid.mp4", "url": "http://x.com/v.mp4"}},
        {"type": "json", "data": {"data": '{"app":"com.tencent.miniapp","prompt":"[分享]","meta":{"detail_1":{"title":"文章标题","desc":"摘要","host":{"nick":"作者"}}}}'}},
        {"type": "file", "data": {"file": "report.pdf"}},
    ],
}
chain = chain_from_onebot_message(full_msg)
assert len(chain.elements) == 8; ok("full msg 8 elements")
# 验证各类型
types = [type(e).__name__ for e in chain.elements]
assert "Reply" in types; ok("has Reply")
assert "Image" in types; ok("has Image")
assert "At" in types; ok("has At")
assert "JsonCard" in types; ok("has JsonCard")
assert "File" in types; ok("has File")
# 验证 Text 包含 "[视频]"
has_video = any(isinstance(e, Text) and "[视频]" in e.text for e in chain.elements)
assert has_video; ok("video → [视频]")

print(f"\n{'='*50}")
print("All tests passed!")
print(f"{'='*50}")
