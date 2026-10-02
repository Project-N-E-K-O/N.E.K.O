"""Request-side rewrite: a chain is cut back to its first comment, unlabelled.

The properties asserted here are the ones that can regress silently: a label
or a second comment left in the view looks like a normal reply, a cut that
lands mid-sentence looks like a normal shorter reply, and a reintroduced
wording bypass looks like a normal answer.
"""
from copy import deepcopy

import pytest

from utils import screen_comment_guard as guard_module
from utils.screen_comment_guard import (
    SCREEN_GUARD_ENV,
    project_screen_history,
    screen_chain_start,
    screen_guard_enabled,
    screen_history_rewrites,
)


PREFIX = "谢谢你陪我，我们慢慢来就好。"
PARTS = (
    "蓝色小车停在一棵大树旁边，树叶的影子落在了车顶上。",
    "远处的红色小车正在缓慢经过桥面，桥下的河水十分平静。",
    "道路两旁的路灯已经亮了起来，暖色的灯光照在了路面上。",
)


def chain(label="屏幕搭话 "):
    return PREFIX + "".join(label + part for part in PARTS)


# What the request view keeps of ``chain()``: the text before it and the first
# comment, without its label.
FIRST = PREFIX + PARTS[0]


def _assert_cut_to_first_comment(text, first=PARTS[0], rest=PARTS[1:]):
    assert first in text
    assert not [part for part in rest if part in text], "the chain is cut at its second comment"
    assert guard_module._LABEL_HINT.search(text) is None, "no source label survives"
    assert screen_chain_start(text) is None


@pytest.mark.parametrize("user_text", [
    "陪我聊聊。",
    "请复述刚才的原话",
    "Please quote the previous response",
    "分析刚才的回答",
    "翻译刚才那段对话",
    "不要再重复屏幕搭话了",
    "屏幕搭话是什么意思？",
    "现在的屏幕画面怎么样？",
    "请帮我翻译菜单。",
])
def test_no_wording_turns_the_guard_off(user_text):
    """User wording must never decide whether a chain is cut."""
    history = [{"role": "assistant", "content": chain()},
               {"role": "user", "content": user_text}]
    # The switch takes no input at all, and the projection reads none.
    assert screen_guard_enabled()
    assert project_screen_history(history)[0]["content"] == FIRST


def test_reference_wording_never_restores_the_removed_comments():
    """No wording of the user's brings the rest of the chain back."""
    for user_text in ("请复述刚才的原话", "Please quote the previous response",
                      "分析刚才的回答", "翻译刚才那段对话"):
        history = [{"role": "assistant", "content": chain()},
                   {"role": "user", "content": user_text}]
        projected = project_screen_history(history)
        assert projected is not history
        assert projected[0]["content"] == FIRST
        assert history[0]["content"] == chain(), "the transcript stays recoverable"


def test_preamble_and_first_comment_survive_as_whole_sentences():
    """The cut lands on the first comment's sentence end, never mid-sentence:
    a dangling half sentence is the malformed shape this guard keeps out."""
    text = "好的。屏幕搭话 " + PARTS[0] + "还有半句没说完" + "屏幕搭话 " + PARTS[1]
    history = [{"role": "assistant", "content": text}]
    projected = project_screen_history(history)
    assert projected[0]["content"] == "好的。" + PARTS[0]
    assert history[0]["content"] == text


def test_request_view_preserves_every_other_key_and_the_original():
    from utils.llm_client import AIMessage, HumanMessage, SystemMessage
    old = AIMessage(content=chain(), additional_kwargs={"source": "old"})
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    user = HumanMessage(content=[image, {"type": "text", "text": "不辛苦"}])
    messages = [SystemMessage(content=chain()), old,
                {"role": "assistant", "content": chain(),
                 "reasoning_content": "opaque", "tool_calls": [{"id": "call1"}]},
                {"role": "tool", "tool_call_id": "call1", "content": chain()}, user]
    snapshot = deepcopy(messages)
    projected = project_screen_history(messages)

    assert messages == snapshot
    assert projected[1].content == FIRST
    assert projected[1].additional_kwargs == old.additional_kwargs
    assert projected[2]["content"] == FIRST
    # Tool-result pairing and provider contract survive the rewrite.
    assert projected[2]["tool_calls"] == messages[2]["tool_calls"]
    assert projected[2]["reasoning_content"] == "opaque"
    # Non-assistant roles keep their own text even when it looks like a chain.
    assert all(projected[i] is messages[i] for i in (0, 3, 4))
    assert project_screen_history(projected) is projected, "projection is idempotent"


def test_rewrite_is_idempotent():
    """A second projection of a rewritten view must change nothing."""
    once = project_screen_history([{"role": "assistant", "content": chain()}])
    assert screen_chain_start(once[0]["content"]) is None
    assert project_screen_history(once) is once
    run = project_screen_history(
        [_user("开始"), _assistant(_chain()), _assistant(_COMMENT_A), _assistant(_COMMENT_B),
         _user("继续")],
    )
    assert project_screen_history(run) is run


def test_unlabelled_history_passes_through_unchanged():
    """Unlabelled history is left byte-for-byte alone."""
    messages = [{"role": "assistant", "content": PREFIX},
                {"role": "user", "content": "陪我聊聊。"}]
    assert project_screen_history(messages) is messages


@pytest.mark.parametrize("text, expected", [
    (PREFIX + "屏幕搭话 " + PARTS[0], PREFIX + PARTS[0]),
    ("屏幕搭话 A。屏幕搭话 B。", "A。B。"),
    ("屏幕搭话 短。屏幕搭话 也短。", "短。也短。"),
])
def test_labels_go_even_without_a_chain(text, expected):
    """A single comment, or comments below ``MIN_PROSE``, are no chain and are
    not cut, but their labels are still a sample of the format and go."""
    messages = [{"role": "assistant", "content": text},
                {"role": "user", "content": "陪我聊聊。"}]
    assert screen_chain_start(text) is None
    assert project_screen_history(messages)[0]["content"] == expected
    assert messages[0]["content"] == text


@pytest.mark.parametrize("text, expected", [
    ("她说“屏幕搭话：你好呀”然后走了。", "她说“你好呀”然后走了。"),
    ("```\n屏幕搭话: 代码块里的标签\n```", "```\n代码块里的标签\n```"),
    ("<think>屏幕搭话：想一想</think>好的。", "<think>想一想</think>好的。"),
    ("【屏幕搭话】今天天气不错。", "今天天气不错。"),
    ("[屏幕画面] 今天天气不错。", "今天天气不错。"),
    ("螢幕搭話：今天天氣不錯。", "今天天氣不錯。"),
    ("/螢幕畫面/ 今天天氣不錯。", "今天天氣不錯。"),
    ("“/屏幕画面/今天天气不错。”", "“今天天气不错。”"),
    ("（/屏幕画面/ 今天天气不错。）", "（今天天气不错。）"),
])
def test_labels_go_from_quotes_code_brackets_and_traditional_forms(text, expected):
    """Removal is stateless: the lexer's quote and code exemptions guard chain
    detection, but only the request copy of the model's own reply is touched."""
    messages = [_assistant(text), _user("继续")]
    assert project_screen_history(messages)[0]["content"] == expected


@pytest.mark.parametrize("text", [
    "The screenshot comment: it looks fine to me.",
    "Scroll down to the screen comment section of the page.",
    "Check the keyboard/screen display first.",
    "打开设置/屏幕画面选项看看。",
    # Either-or wording with a slash is not a label, chained or not.
    ("你可以发屏幕截图/照片给我，我帮你看看哪里出了问题呀。"
     "如果不方便的话，也可以描述一下屏幕显示/报错的具体内容哦，我会尽量帮你分析。"),
    ("The screen display / layout looks off on my side, sorry about that. "
     "Try the screen content / settings panel and tell me what you see there."),
    "照片/屏幕截图 都可以发给我，我帮你看看哪里出了问题呀。照片/屏幕截图/视频也行，我会尽量帮你分析。",
    "Could you share the screen content/layout you see?",
    # Label-like path segments and reference-style links.
    "截图放在 /tmp/[屏幕截图]/file.png 和 C:\\Users\\me\\[屏幕画面]\\a.png 里了。",
    "看这张[屏幕截图][1]，再看那张[屏幕画面][2]，都在下面的链接里。",
    # A label-like path segment inside a URL or a Markdown link target.
    "链接 https://host/屏幕搭话 打开，还有[图](https://host/【屏幕画面】/a.png)和[图2](/屏幕画面/b.png)。",
    "截图在这里：[capture](https://host/屏幕截图/file.png)，还有 https://host/屏幕画面/a.png 也可以看。",
    "屏幕搭话就是我会定时看看你的屏幕。",
    "“屏幕搭话”功能开启之后我会主动和你聊几句哦。",
    # Markdown link text is not a label, neither for removal nor for chains.
    "看这张[屏幕截图](https://example.com/a.png)就知道了。",
    "![screen image](a.png)",
    "看这张【屏幕截图】(https://example.com/a.png)就知道了。",
    ("这是[屏幕画面](https://example.com/a.png)，这个视频画面好漂亮，色调很温柔呢。"
     "再看[屏幕画面](https://example.com/b.png)，右下角那只猫好可爱，毛茸茸的呢。"),
])
def test_words_and_prose_that_only_contain_a_label_stay(text):
    messages = [_assistant(text), _user("继续")]
    assert project_screen_history(messages) is messages


def test_a_label_named_like_a_reference_definition_is_still_removed():
    """A Markdown shortcut reference whose name is a label is not told apart
    from the label (design doc 7.1.3, item 7): guarding wins over the link."""
    text = "屏幕截图 这个视频画面好漂亮，色调很温柔呢。\n\n[屏幕截图]: https://host/a.png"
    messages = [_assistant("[" + text.replace(" ", "] ", 1)), _user("继续")]
    assert "[屏幕截图]" not in project_screen_history(messages)[0]["content"]


def test_a_label_inside_a_url_is_neither_removed_nor_a_chain_marker():
    text = ("屏幕搭话：这个视频画面好漂亮，色调很温柔呢。"
            "看 https://host/屏幕搭话：右下角那只猫好可爱 毛茸茸的呢。")
    messages = [_user("聊"), _assistant(text), _user("继续")]
    assert project_screen_history(messages)[1]["content"] == text[len("屏幕搭话："):]


def test_whitespace_around_a_removed_label_stays():
    """Indentation and trailing spaces can be Markdown structure."""
    text = "看这段：\n\n    echo screen comment: demo\n    ls  "
    messages = [_assistant("    " + text.lstrip()), _user("继续")]
    assert project_screen_history(messages)[0]["content"] == "    看这段：\n\n    echo demo\n    ls  "


def test_a_bare_label_does_not_join_paragraphs():
    messages = [_assistant("我最喜欢的功能是屏幕搭话\n\n还有语音聊天哦。"), _user("继续")]
    assert project_screen_history(messages)[0]["content"] == "我最喜欢的功能是\n\n还有语音聊天哦。"


def test_a_message_that_was_only_a_label_leaves_the_view():
    for messages in (
        [_user("聊"), _assistant("好呀。"), _assistant("屏幕搭话："), _user("继续")],
        [_user("聊"), _assistant("屏幕搭话："), _assistant("好呀。"), _user("继续")],
    ):
        assert [m["content"] for m in project_screen_history(messages)] == ["聊", "好呀。", "继续"]


def test_a_lone_label_between_user_turns_stays_so_turns_alternate():
    """Without a neighbouring assistant turn the label-only message stays,
    as a neutral ellipsis: no label, and no empty text part either."""
    for first in (_user("聊"), None):
        messages = [m for m in (first, _assistant("屏幕搭话："), _user("继续")) if m]
        projected = project_screen_history(messages)
        assert [m["role"] for m in projected] == [m["role"] for m in messages]
        assert [m["content"] for m in projected if m["role"] == "assistant"] == ["…"]


def test_non_text_parts_of_a_rewritten_message_stay():
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}
    messages = [
        _user("聊"),
        {"role": "assistant", "content": [{"type": "text", "text": chain("屏幕搭话：")}, image]},
        {"role": "assistant", "content": [{"type": "text", "text": "屏幕搭话："}, image]},
        _user("继续"),
    ]
    first, label_only = project_screen_history(messages)[1:3]
    assert first["content"][1:] == [image]
    _assert_cut_to_first_comment(first["content"][0]["text"])
    assert label_only["content"] == [image]


def test_a_rewritten_text_part_keeps_its_place_among_images():
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}
    later = {"type": "image_url", "image_url": {"url": "data:image/png;base64,BB=="}}
    messages = [
        {"role": "assistant", "content": [
            image, {"type": "text", "text": "屏幕搭话：" + PARTS[0]}, later,
        ]},
        _user("继续"),
    ]
    content = project_screen_history(messages)[0]["content"]
    assert content == [image, {"type": "text", "text": PARTS[0]}, later]


def test_text_parts_split_by_an_image_each_keep_their_slot():
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}

    def text(value):
        return {"type": "text", "text": value}

    labelled = [text("屏幕搭话：看这张。"), image, text("屏幕搭话：再看这张，挺好的。")]
    assert project_screen_history([_assistant_parts(labelled), _user("继续")])[0]["content"] == [
        text("看这张。"), image, text("再看这张，挺好的。"),
    ]
    indented = [text("    屏幕搭话：看这张。"), image, text("屏幕搭话：再看这张，挺好的。")]
    assert project_screen_history([_assistant_parts(indented), _user("继续")])[0]["content"] == [
        text("看这张。"), image, text("再看这张，挺好的。"),
    ]
    spaced = [text("屏幕搭话：看这张。 "), image, text(" 屏幕搭话：再看这张，挺好的。")]
    assert project_screen_history([_assistant_parts(spaced), _user("继续")])[0]["content"] == [
        text("看这张。"), image, text("再看这张，挺好的。"),
    ]
    intro = [text("先说一句 "), image, text(chain("屏幕搭话："))]
    content = project_screen_history([_assistant_parts(intro), _user("继续")])[0]["content"]
    assert content[:2] == [text("先说一句"), image]
    _assert_cut_to_first_comment(content[2]["text"])
    chained = [text("屏幕搭话：" + PARTS[0]), image, text("屏幕搭话：" + PARTS[1])]
    content = project_screen_history([_assistant_parts(chained), _user("继续")])[0]["content"]
    assert content[1:] == [image]
    _assert_cut_to_first_comment(content[0]["text"])


def _assistant_parts(parts):
    return {"role": "assistant", "content": parts}


def test_a_cache_with_equal_display_names_is_rendered_unchanged(monkeypatch):
    """The cache keys lines by display name (its writers even merge
    neighbouring lines by it). When the user's equals the character's,
    every line reads as the user's, so nothing is cut: the guard fails open
    there, as with the operator switch off."""
    from types import SimpleNamespace

    from main_logic.core.notify import NotifyMixin

    monkeypatch.delenv(SCREEN_GUARD_ENV, raising=False)
    owner = SimpleNamespace(lanlan_name="YUI", master_name="YUI", user_language="zh")
    cache = [{"role": "YUI", "text": "陪我聊聊"}, {"role": "YUI", "text": _COMMENT_A + _COMMENT_B}]
    assert NotifyMixin._convert_cache_to_str(owner, cache).splitlines() == [
        "YUI | 陪我聊聊", f"YUI | {_COMMENT_A + _COMMENT_B}",
    ]


def test_label_removal_is_idempotent_and_reported():
    hits = {}
    once = project_screen_history(
        [_assistant("屏幕搭话 ：" + PARTS[0]), _user("继续")], hits=hits,
    )
    assert once[0]["content"] == PARTS[0]
    assert hits == {"label": 1}
    assert project_screen_history(once) is once


@pytest.mark.parametrize("label", [
    "屏幕搭话 ", "屏幕搭话：", "/屏幕画面/", "／屏幕内容／ ", "/ 螢幕畫面 /：",
    "screen comment: ", "Screen comment：", "/screen observation/", "/ screen comment / ",
])
def test_every_supported_label_form_is_cut_and_removed(label):
    messages = [{"role": "assistant", "content": chain(label)}]
    text = project_screen_history(messages)[0]["content"]
    assert text.startswith(PREFIX)
    _assert_cut_to_first_comment(text)


def test_reference_wording_helper_is_gone_from_the_module():
    """The word-list heuristic must not be reachable as an authorization input."""
    assert not hasattr(guard_module, "requests_history_reference")
    assert not hasattr(guard_module, "_CHAT_REFERENCE")


# ── Wire-level positives ────────────────────────────────────────────────────
# The projection is only worth anything if the rewrite survives the real
# client path and lands in the provider payload. These mirror the offline
# probe that produced the incident evidence, but run through the production
# client instead of a hand-rolled projection.
#
# The negative control lives in ``test_screen_chat_history_payload.py``: seven
# independent single-marker messages must reach the payload untouched, because
# one marker per message is not a chain.

# Synthetic: same shape as the incident (label, length, "哇…呀！…喵～"
# cadence), none of its wording.
WIRE_COMMENTS = (
    "哇，这台小推车上挂着的那串纸风车转得好欢呀！配上背后整排的木头货架，"
    "简直像是本喵偷偷溜进了集市里陪你一起逛呢喵～",
    "哇，左上角地图里三个补给点的图标排得好整齐呀！你盯着这些路线的样子"
    "超认真，本喵就在旁边乖乖守着，等你把这一关都走完喵～",
)


def _chain(label="屏幕搭话 "):
    return "".join(label + text for text in WIRE_COMMENTS)


def _bare_client(language="zh"):
    from main_logic.omni_offline_client import OmniOfflineClient
    from tests.unit.test_tool_calling import _init_bare

    client = _init_bare(OmniOfflineClient.__new__(OmniOfflineClient))
    client._use_genai_sdk = False
    client._user_language_provider = lambda: language
    return client


@pytest.mark.parametrize("prefix", ["", "呼噜……", "我们慢慢来就好。"])
@pytest.mark.parametrize("with_tool_round", [False, True])
def test_rewrite_survives_the_client_request_path(prefix, with_tool_round):
    """Only the first comment reaches the payload; every other key and message intact."""
    client = _bare_client()

    poisoned = prefix + _chain()
    if with_tool_round:
        messages = [
            {"role": "assistant", "content": poisoned, "reasoning_content": "opaque",
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "lookup", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
            {"role": "user", "content": "查询之后陪我聊聊。"},
        ]
    else:
        messages = [{"role": "assistant", "content": poisoned},
                    {"role": "user", "content": "陪我聊聊。"}]
    snapshot = deepcopy(messages)
    payload = client._dialog_messages_for_provider(messages)

    assert messages == snapshot, "the saved transcript must not be rewritten"
    assert payload is not messages
    assert payload[0]["content"] == prefix + WIRE_COMMENTS[0]
    if with_tool_round:
        # Tool-result pairing must survive or the provider rejects the request.
        assert payload[0]["tool_calls"] == messages[0]["tool_calls"]
        assert payload[0]["reasoning_content"] == "opaque"
        assert payload[1] is messages[1]
    assert payload[-1] is messages[-1]
    import json as _json
    dumped = _json.dumps(payload, ensure_ascii=False)
    assert WIRE_COMMENTS[1] not in dumped
    assert "屏幕搭话" not in dumped


@pytest.mark.parametrize("language", ["zh", "en", "ja"])
def test_rewrite_does_not_depend_on_the_session_locale(language):
    """The view is cut from the reply's own text; nothing locale-specific is
    inserted, so every session gets the same rewrite."""
    messages = [{"role": "assistant", "content": _chain()},
                {"role": "user", "content": "hi"}]
    payload = _bare_client(language)._dialog_messages_for_provider(messages)
    assert payload[0]["content"] == WIRE_COMMENTS[0]


def test_untouched_messages_and_roles_pass_the_client_path_unchanged():
    """System, plain assistant, user text and image parts are not candidates."""
    client = _bare_client()
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    messages = [
        {"role": "system", "content": "保持角色。"},
        {"role": "assistant", "content": "正常回复。"},
        {"role": "user", "content": [image, {"type": "text", "text": "看看图"}]},
    ]
    assert client._dialog_messages_for_provider(messages) is messages


# ── Cross-message chains ────────────────────────────────────────────────────
# A chain can be spread over several messages, and whether it propagates is
# positional. Both halves of the rule were measured separately against a real
# model, so each is pinned here:
#
#   [u, a, a, ask]   chains 5/5        a run answering a user turn propagates
#   [u, a×7, ask]    chains 2/2
#   [a×7, u]         chains 0/3        no user turn ahead of the run
#   [a×7, u1, ask]   chains 0/2        an intervening user turn ends it
#   [u, a, tool, a]  chains 0/3        a tool result ends it
#
# The first two are why the run is merged at all; the last three are why the
# merge is bounded rather than global.

_COMMENT_A = "屏幕搭话 蓝色小车停在一棵大树旁边，树叶的影子落在了车顶上。"
_COMMENT_B = "屏幕搭话 远处的红色小车正在缓慢经过桥面，桥下的河水十分平静。"


def _assistant(text):
    return {"role": "assistant", "content": text}


def _user(text):
    return {"role": "user", "content": text}


_BODY_A = _COMMENT_A[len("屏幕搭话 "):]
_BODY_B = _COMMENT_B[len("屏幕搭话 "):]


def _rewritten(messages):
    """Indices of the messages the request view rewrites or leaves out."""
    return sorted(guard_module._chain_rewrites(messages))


def test_chain_spread_over_the_run_answering_a_user_turn_is_cut():
    messages = [{"role": "system", "content": "sys"},
                _user("陪我聊聊。"), _assistant(_COMMENT_A), _assistant(_COMMENT_B),
                _user("那你继续说说。")]
    assert screen_chain_start(_COMMENT_A) is None, "one comment alone is not a chain"
    assert screen_chain_start(_COMMENT_B) is None
    assert _rewritten(messages) == [2, 3]
    projected = project_screen_history(messages)
    # One unlabelled comment answers the user turn; the rest of the run goes.
    assert [m["content"] for m in projected] == [
        "sys", "陪我聊聊。", _BODY_A, "那你继续说说。",
    ]


def test_run_keeps_normal_replies_before_the_chain():
    messages = [_user("陪我聊聊。"), _assistant("好呀，我看看。"),
                _assistant(_COMMENT_A), _assistant(_COMMENT_B), _assistant(_COMMENT_A),
                _user("继续")]
    projected = project_screen_history(messages)
    assert projected[1] is messages[1]
    assert [m["content"] for m in projected] == ["陪我聊聊。", "好呀，我看看。", _BODY_A, "继续"]


def test_a_fragment_before_the_cut_is_dropped_not_kept():
    """Text before the second label that never reaches a sentence end would be
    a dangling fragment; that message leaves the view instead."""
    messages = [_user("聊"), _assistant(_COMMENT_A), _assistant("嗯 " + _COMMENT_B), _user("继续")]
    assert [m["content"] for m in project_screen_history(messages)] == ["聊", _BODY_A, "继续"]


def test_a_tool_call_message_past_the_cut_stays_with_empty_content():
    """Dropping an assistant turn that carries ``tool_calls`` would orphan its
    results, so it stays, emptied, with every other key intact."""
    calls = [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    messages = [_user("聊"), _assistant(_COMMENT_A),
                {"role": "assistant", "content": _COMMENT_B, "tool_calls": calls,
                 "reasoning_content": "opaque"},
                _user("继续")]
    projected = project_screen_history(messages)
    assert projected[2] == {"role": "assistant", "content": "", "tool_calls": calls,
                            "reasoning_content": "opaque"}
    assert messages[2]["content"] == _COMMENT_B


@pytest.mark.parametrize("with_system", [False, True])
def test_deliveries_with_no_user_turn_ahead_are_left_alone(with_system):
    """The app's own proactive deliveries: 7 single-comment messages, then the
    user replies. Measured 0/3 propagation, and the shape the payload test
    pins as must-pass."""
    messages = ([{"role": "system", "content": "sys"}] if with_system else []) + [
        _assistant(f"屏幕搭话 第{index}条观察，画面里有些东西值得说。") for index in range(7)
    ] + [_user("陪我聊聊。")]
    assert _rewritten(messages) == []


def test_intervening_user_turn_ends_the_run():
    messages = [{"role": "system", "content": "sys"},
                _assistant(_COMMENT_A), _assistant(_COMMENT_B),
                _user("陪我聊聊。"), _user("那你继续说说。")]
    assert _rewritten(messages) == []


def test_tool_result_ends_the_run():
    """Measured 0/3: the tool boundary breaks the pattern rather than
    continuing it, so the two halves are not merged across it."""
    messages = [{"role": "system", "content": "sys"}, _user("陪我聊聊。"),
                _assistant(_COMMENT_A),
                {"role": "tool", "tool_call_id": "c1", "content": "ok"},
                _assistant(_COMMENT_B), _user("那你继续说说。")]
    assert _rewritten(messages) == []


def test_a_single_trailing_comment_is_not_a_chain():
    """One comment in the run is not a chain, so a normal single delivery
    survives even when it answers a user turn."""
    messages = [{"role": "system", "content": "sys"}, _user("陪我聊聊。"),
                _assistant(_COMMENT_A), _user("那你继续说说。")]
    assert _rewritten(messages) == []


@pytest.mark.parametrize("as_dict", [False, True])
def test_proactive_source_splits_runs_without_exempting_single_message_chains(as_dict):
    from utils.llm_client import AIMessage

    def proactive(text):
        metadata = {"dialog_source": "proactive"}
        return ({"role": "assistant", "content": text, "additional_kwargs": metadata}
                if as_dict else AIMessage(content=text, additional_kwargs=metadata))

    messages = [_user("开始"), _assistant(_COMMENT_A), proactive(_COMMENT_B),
                _assistant(_COMMENT_A), _assistant(_COMMENT_B), proactive(chain()),
                _assistant(_COMMENT_A), _assistant(_COMMENT_B), _user("继续")]
    snapshot = deepcopy(messages)
    assert _rewritten(messages) == [3, 4, 5, 6, 7]
    assert messages == snapshot


@pytest.mark.parametrize("metadata", [{}, {"dialog_source": "unknown"},
                                     {"anti_repeat_response_id": "delivery-1"}])
def test_unknown_source_keeps_cross_message_detection(metadata):
    messages = [_user("开始"),
                {**_assistant(_COMMENT_A), "additional_kwargs": metadata},
                _assistant(_COMMENT_B), _user("继续")]
    assert _rewritten(messages) == [1, 2]


def test_proactive_source_survives_file_and_sql_history_roundtrip(tmp_path):
    import json
    from sqlalchemy import select
    from utils.llm_client import AIMessage, HumanMessage
    from utils.llm_client.messages import messages_from_dict, messages_to_dict
    from utils.llm_client.history import SQLChatMessageHistory

    messages = [HumanMessage(content="开始"), *[
        AIMessage(content=text, additional_kwargs={
            "dialog_source": "proactive", "not_persisted": "private",
        }) for text in (_COMMENT_A, _COMMENT_B)
    ], HumanMessage(content="继续")]
    restored = messages_from_dict(json.loads(json.dumps(messages_to_dict(messages))))
    assert guard_module._chain_rewrites(restored) == {}
    history = SQLChatMessageHistory(f"sqlite:///{tmp_path / 'source.db'}", "source-test")
    try:
        history.add_messages(messages)
        with history._engine.connect() as connection:
            rows = connection.execute(select(history._table.c.message).order_by(history._table.c.id))
            saved = [json.loads(row[0]) for row in rows]
        assert saved[1]["data"]["additional_kwargs"] == {"dialog_source": "proactive"}
        restored = messages_from_dict(saved)
        assert guard_module._chain_rewrites(restored) == {}
        assert "dialog_source" not in restored[1].to_openai()
    finally:
        history._engine.dispose()
        SQLChatMessageHistory._engine_cache.pop(f"sqlite:///{tmp_path / 'source.db'}", None)


# ── Review round: message boundaries, tool images, English prose, switch ─────

_C1 = "屏幕搭话 你这波推进打得很稳，坦克卡位也非常漂亮。陪你冲锋喵"
_C2 = "屏幕搭话 对面残血已经跑不掉了，等你把全场都拿下。继续加油喵"


@pytest.mark.parametrize("first", [
    _C1,                                     # ends in a letter: "喵"
    _C1 + "「没说完的引号",                   # unclosed quote
    _C1 + "<think>",                          # unclosed think tag
    _C1 + "\n```",                            # fence opened, never closed
    _C1 + "\n> 引用块里的一句话",             # quote block paragraph
])
def test_each_message_starts_with_a_fresh_lexer(first):
    """Lexical state ends with the message. Before, the run was joined with
    "" and the second label was glued to the first message's last word."""
    messages = [{"role": "system", "content": "sys"}, _user("聊"),
                _assistant(first), _assistant(_C2), _user("继续")]
    assert _rewritten(messages) == [2, 3]


def test_tool_image_turn_is_not_the_last_user_turn():
    """The tool loop appends {"role": "user"} image turns in place; the next
    iteration's request must still see the run before the real user turn."""
    image_turn = {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        {"type": "text", "text": "tool image"}]}
    base = [{"role": "system", "content": "sys"}, _user("聊"),
            _assistant(_COMMENT_A), _assistant(_COMMENT_B), _user("继续")]
    assert _rewritten(base) == [2, 3]
    after_tool = base + [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "{}"},
        image_turn, dict(image_turn),
    ]
    assert _rewritten(after_tool) == [2, 3]
    released = after_tool[:-2] + [{"role": "user", "content": "[image removed]"}]
    assert _rewritten(released) == [2, 3]


def test_a_real_user_turn_after_a_tool_result_still_counts():
    """Only dict-shaped turns right after tool results are tool images; the
    saved transcript's own user message is an object and stays a user turn."""
    from utils.llm_client import AIMessage, HumanMessage
    messages = [HumanMessage(content="聊"), AIMessage(content=_COMMENT_A),
                AIMessage(content=_COMMENT_B), HumanMessage(content="继续"),
                {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
                {"role": "tool", "tool_call_id": "c1", "content": "{}"},
                HumanMessage(content="再说一句")]
    assert _rewritten(messages) == []


@pytest.mark.parametrize("text", [
    ("The screen comment feature shows a remark next to the game view when it is on. "
    "A screen comment is short and it is written by the character, not by you."),
    ("Screen comment support is optional here, and it can be turned off at any time. "
    "Each screen comment appears only once, then it fades out of the chat window."),
])
def test_english_prose_about_the_feature_is_not_a_chain(text):
    """"screen comment" followed by a space is ordinary English; only the
    colon form (or a slash-delimited label) counts as a marker."""
    assert screen_chain_start(text) is None
    messages = [_assistant(text), _user("ok")]
    assert project_screen_history(messages) is messages


@pytest.mark.parametrize("value", ["0", "false", "OFF", " no "])
def test_operator_switch_turns_the_guard_off(monkeypatch, value):
    monkeypatch.setenv(SCREEN_GUARD_ENV, value)
    messages = [_assistant(chain()), _user("继续")]
    assert not screen_guard_enabled()
    assert project_screen_history(messages) is messages


@pytest.mark.parametrize("value", ["", "1", "on", "yes"])
def test_operator_switch_defaults_on(monkeypatch, value):
    monkeypatch.setenv(SCREEN_GUARD_ENV, value)
    assert screen_guard_enabled()


def test_hits_report_the_category_of_each_rewrite():
    hits = {}
    project_screen_history(
        [_assistant(chain()), _user("聊"), _assistant(_COMMENT_A),
         _assistant(_COMMENT_B), _user("继续")],
        hits=hits,
    )
    assert hits == {"message": 1, "run": 2}


@pytest.mark.parametrize("glue", ["", "喵", "。", "\n", "233", "OK", "QwQ", "LOL", "_"])
def test_a_label_glued_to_the_previous_chinese_sentence_is_a_marker(glue):
    """Within one message, a label glued to the end of the previous sentence
    must still count, whether that sentence ends in a CJK letter or in an
    ASCII word ("233", "OK", "QwQ"). The ASCII-word rule is for English
    labels only."""
    joined = _C1 + glue + _C2
    assert screen_chain_start(joined) == 0
    text = project_screen_history([_assistant(joined), _user("继续")])[0]["content"]
    # The first comment ends at its last sentence end; a glued "。" closes
    # its trailing "陪你冲锋喵" into one.
    tail = "陪你冲锋喵。" if glue == "。" else ""
    assert text == "你这波推进打得很稳，坦克卡位也非常漂亮。" + tail


@pytest.mark.parametrize("label", ["屏幕搭话 ", "屏幕搭话：", "【屏幕画面】"])
def test_a_chinese_label_after_an_ascii_word_is_a_marker(label):
    """A bare or bracketed Chinese label counts right after an ASCII letter
    or digit. (A slash form must open a phrase; see the either-or test.)"""
    text = "好耶233" + label + PARTS[0] + "OK" + label + PARTS[1]
    assert screen_chain_start(text) == len("好耶233")


@pytest.mark.parametrize("text", [
    ("The screenshot comment: it looks fine to me, and the colours are right. "
    "Another screenshot comment: the layout also reads well on a small phone."),
    ("prescreen comment: this is just a word that happens to contain a label. "
    "prescreen comment: and here it is again, still inside a longer word."),
    # A slash glued to an English word joins it to the next one.
    ("Check the keyboard/screen display first, it may just be dimmed. "
    "If the keyboard/screen display still flickers, restart the laptop."),
    ("Open Settings/Screen comment and switch it on for this character. "
    "Later, Settings/Screen comment also lets you pick how often it talks."),
])
def test_labels_inside_english_words_stay_inert(text):
    assert screen_chain_start(text) is None


@pytest.mark.parametrize("text", [
    "屏幕搭话就是我会定时看看你的屏幕，然后主动和你聊几句哦。屏幕搭话需要你先在设置里授权才可以使用呢。",
    "“屏幕搭话”功能开启之后我会定时看看你的屏幕，然后主动和你聊几句哦。“屏幕搭话”需要你先在设置里授权才可以使用呢。",
    "- **屏幕搭话**：开启之后我会定时看看你的屏幕，然后主动和你聊几句哦。\n- **屏幕截图**：你也可以随时手动发截图给我看呢。",
])
def test_feature_prose_that_names_the_label_like_prose_is_not_a_chain(text):
    """Explaining the feature does not trip the guard when the reply names
    it the way prose does: no separator, quoted, or in bold."""
    assert screen_chain_start(text) is None
    messages = [_assistant(text), _user("ok")]
    assert project_screen_history(messages) is messages


def test_repeated_projection_reuses_the_lexer_result(monkeypatch):
    """Each provider call rebuilds the request view over unchanged history;
    both the per-message and the run results are cached instead of re-lexed."""
    made = []
    real = guard_module._ScreenLexer
    monkeypatch.setattr(guard_module, "_ScreenLexer", lambda: made.append(1) or real())
    guard_module._cached_dechain.cache_clear()
    messages = [_user("聊"), _assistant(chain()), _assistant(_COMMENT_A), _assistant(_COMMENT_B),
                _user("继续")]
    first = project_screen_history(messages)
    lexed = len(made)
    assert lexed
    for _ in range(3):
        assert [m["content"] for m in project_screen_history(messages)] == [m["content"] for m in first]
    assert len(made) == lexed


def test_text_that_never_mentions_a_screen_skips_the_lexer(monkeypatch):
    """Ordinary chat must neither pay for the lexer nor fill its cache."""
    made = []
    real = guard_module._ScreenLexer
    monkeypatch.setattr(guard_module, "_ScreenLexer", lambda: made.append(1) or real())
    guard_module._cached_dechain.cache_clear()
    messages = [_user("聊"), *[_assistant(f"第{i}句普通回复，说得够长够长够长了。") for i in range(5)],
                _user("继续")]
    assert project_screen_history(messages) is messages
    assert made == []
    assert guard_module._cached_dechain.cache_info().currsize == 0


# ── Restore paths (third review round) ──────────────────────────────────────

def _cache_owner(language="zh"):
    from types import SimpleNamespace
    return SimpleNamespace(lanlan_name="YUI", user_language=language)


def test_hot_swap_cache_cuts_the_characters_chain(monkeypatch):
    """The cache is primed into the next session's system prompt, past the
    request-view projection. Consecutive character lines are merged into one
    entry, so a chain spread over replies is caught as one text."""
    from main_logic.core.notify import NotifyMixin

    monkeypatch.delenv(SCREEN_GUARD_ENV, raising=False)
    cache = [{"role": "Alice", "text": "屏幕搭话 这句是用户说的，不该被改。屏幕搭话 用户这边也会写很长很长的句子。"},
             {"role": "YUI", "text": _COMMENT_A + _COMMENT_B},
             {"role": "YUI", "text": "普通的一句回复。"}]
    rendered = NotifyMixin._convert_cache_to_str(_cache_owner(), cache)
    assert rendered.splitlines() == [
        f"Alice | {cache[0]['text']}",
        f"YUI | {_BODY_A}",
        "YUI | 普通的一句回复。",
    ]
    monkeypatch.setenv(SCREEN_GUARD_ENV, "0")
    assert _COMMENT_A in NotifyMixin._convert_cache_to_str(_cache_owner(), cache)


def test_a_repeated_rewrite_is_logged_once_at_info(monkeypatch):
    """Every provider call re-projects the history; the same rewrite must
    not add an INFO line to each of them, or a new hit gets lost."""
    from unittest.mock import MagicMock
    import main_logic.omni_offline_client._tools as tooling

    fake = MagicMock()
    monkeypatch.setattr(tooling, "logger", fake)
    client = _bare_client()
    messages = [_user("聊"), _assistant(chain()), _user("继续")]
    levels = []
    for batch in (messages, messages, messages, messages + [_assistant(chain("屏幕搭话："))]):
        fake.reset_mock()
        client._dialog_messages_for_provider(batch)
        levels.append("info" if fake.info.called else "debug" if fake.debug.called else None)
    assert levels == ["info", "debug", "debug", "info"]


def test_restored_history_counts_its_last_run_as_the_current_turn():
    """Restored history is followed by the user speaking in the new session,
    so a chain spread over the run at its end is cut there even though no
    user message follows it in the list."""
    messages = [_user("聊"), _assistant(_COMMENT_A), _assistant(_COMMENT_B)]
    assert _rewritten(messages) == []
    projected = project_screen_history(messages, trailing_turn=True)
    assert [m["content"] for m in projected[1:]] == [_BODY_A]
    # The run still has to follow a user turn.
    lone = [_assistant(_COMMENT_A), _assistant(_COMMENT_B)]
    assert guard_module._chain_rewrites(lone, trailing_turn=True) == {}


def test_hot_swap_cache_catches_a_chain_split_over_entries(monkeypatch):
    from types import SimpleNamespace
    from main_logic.core.notify import NotifyMixin

    monkeypatch.delenv(SCREEN_GUARD_ENV, raising=False)
    owner = SimpleNamespace(lanlan_name="YUI", master_name="Alice", user_language="zh")
    cache = [{"role": "Alice", "text": "陪我聊聊"},
             {"role": "YUI", "text": _COMMENT_A},
             {"role": "YUI", "text": _COMMENT_B}]
    rendered = NotifyMixin._convert_cache_to_str(owner, cache).splitlines()
    assert rendered == ["Alice | 陪我聊聊", f"YUI | {_BODY_A}"]


def test_list_content_is_checked_like_text():
    """Memory restores assistant messages as [{"type": "text", ...}]; the
    guard must read their text, in one message and across the run."""
    from types import SimpleNamespace

    def listed(text):
        return SimpleNamespace(type="ai", content=[{"type": "text", "text": text}])

    single = [SimpleNamespace(type="human", content="聊"), listed(chain())]
    assert project_screen_history(single, trailing_turn=True)[1].content == FIRST
    run = [SimpleNamespace(type="human", content="聊"), listed(_COMMENT_A), listed(_COMMENT_B)]
    projected = project_screen_history(run, trailing_turn=True)
    assert [m.content for m in projected[1:]] == [_BODY_A]
    assert run[1].content == [{"type": "text", "text": _COMMENT_A}], "originals untouched"


def test_a_cache_slice_is_judged_with_what_precedes_it(monkeypatch):
    """A slice primed after an earlier one is judged together with it: a
    chain split across the boundary is caught, and only the slice renders.
    Here the slice is the second comment, so nothing of it is rendered."""
    from types import SimpleNamespace
    from main_logic.core.notify import NotifyMixin

    monkeypatch.delenv(SCREEN_GUARD_ENV, raising=False)
    owner = SimpleNamespace(lanlan_name="YUI", master_name="Alice", user_language="zh")
    earlier = [{"role": "Alice", "text": "陪我聊聊"}, {"role": "YUI", "text": _COMMENT_A}]
    later = [{"role": "YUI", "text": _COMMENT_B}]
    assert NotifyMixin._convert_cache_to_str(owner, later).splitlines() == [f"YUI | {_BODY_B}"]
    assert NotifyMixin._convert_cache_to_str(owner, later, preceding=earlier) == ""


# ── Review round on the split PR ────────────────────────────────────────────

def test_a_chain_that_starts_inside_one_message_and_continues_in_the_next_is_cut_once():
    """The run is judged on the original texts: rewriting the first message
    alone must not hide the label in the message that continues its chain."""
    messages = [_user("聊"), _assistant(chain()), _assistant(_COMMENT_B), _user("继续")]
    projected = project_screen_history(messages)
    assert [m["content"] for m in projected] == ["聊", FIRST, "继续"]
    assert _rewritten(messages) == [1, 2]


@pytest.mark.asyncio
async def test_hot_swap_cache_keeps_each_proactive_delivery_apart(monkeypatch):
    """Consecutive assistant publishes are merged into one cache entry, which
    would read two independent proactive deliveries as one chain. Guarded
    (proactive) publishes get their own entries, marked, and the cache
    rendering splits the run at them."""
    from main_logic.core import LLMSessionManager
    from main_logic.core.notify import NotifyMixin
    from tests.unit.test_core_game_route_memory_contract import _make_manager

    monkeypatch.delenv(SCREEN_GUARD_ENV, raising=False)
    mgr = _make_manager()
    mgr.is_preparing_new_session = True
    mgr.message_cache_for_new_session = [{"role": "Master", "text": "陪我聊聊"}]
    for sid, comment in (("s-1", _COMMENT_A), ("s-2", _COMMENT_B)):
        mgr.current_speech_id = sid
        await LLMSessionManager.send_lanlan_response(
            mgr, comment, is_first_chunk=True, expected_speech_id=sid,
        )
    cache = list(mgr.message_cache_for_new_session)
    assert cache == [
        {"role": "Master", "text": "陪我聊聊"},
        {"role": "Lan", "text": _COMMENT_A, "source": "proactive", "speech_id": "s-1"},
        {"role": "Lan", "text": _COMMENT_B, "source": "proactive", "speech_id": "s-2"},
    ]
    assert NotifyMixin._convert_cache_to_str(mgr, cache).splitlines() == [
        "Master | 陪我聊聊", f"Lan | {_BODY_A}", f"Lan | {_BODY_B}",
    ]
    # An ordinary reply still starts its own entry after a delivery and
    # merges its own chunks, as before.
    await LLMSessionManager.send_lanlan_response(mgr, "普通回复", is_first_chunk=True)
    await LLMSessionManager.send_lanlan_response(mgr, "，接着说。")
    assert mgr.message_cache_for_new_session[-1] == {"role": "Lan", "text": "普通回复，接着说。"}


@pytest.mark.asyncio
async def test_hot_swap_cache_replaces_a_retried_proactive_attempt(monkeypatch):
    """prompt_ephemeral restarts every attempt with is_first_chunk=True. The
    discarded attempt's fragment must not stay behind as its own entry."""
    from main_logic.core import LLMSessionManager
    from tests.unit.test_core_game_route_memory_contract import _make_manager

    mgr = _make_manager()
    mgr.is_preparing_new_session = True
    mgr.current_speech_id = "s-1"
    mgr.message_cache_for_new_session = [{"role": "Master", "text": "陪我聊聊"}]
    send = LLMSessionManager.send_lanlan_response
    await send(mgr, "屏幕搭话：这个", is_first_chunk=True, expected_speech_id="s-1")
    await send(mgr, "视频", expected_speech_id="s-1")
    await send(mgr, _COMMENT_A, is_first_chunk=True, expected_speech_id="s-1")
    assert mgr.message_cache_for_new_session[1:] == [
        {"role": "Lan", "text": _COMMENT_A, "source": "proactive", "speech_id": "s-1"},
    ]


# ── Second review round on the split PR ─────────────────────────────────────

def test_restored_history_that_ends_in_a_user_line_still_judges_its_run():
    """trailing_turn must not hide the run before a history's own last user
    line: that line is the turn the run answers."""
    messages = [_user("你看"), _assistant(_COMMENT_A), _assistant(_COMMENT_B), _user("嗯？")]
    assert _rewritten(messages) == [1, 2]
    assert sorted(screen_history_rewrites(messages, trailing_turn=True)) == [1, 2]


def test_the_cut_lands_in_the_message_holding_the_second_label():
    """The second comment's label may sit in one message and its prose in the
    next; the half sentence before that label is not kept, and every message
    after the label goes."""
    first = _COMMENT_A + "还有呀你看 屏幕搭话：右下角"
    assert guard_module._dechain((first, "那只猫好可爱，毛茸茸的好想摸一摸。")) == (_BODY_A, None)
    messages = [_user("聊"), _assistant(first), _assistant("那只猫好可爱，毛茸茸的好想摸一摸。"),
                _assistant("嗯嗯，真的。"), _user("继续")]
    assert [m["content"] for m in project_screen_history(messages)] == ["聊", _BODY_A, "继续"]


@pytest.mark.parametrize("text", [
    ("屏幕搭话：这个网站的地址是example.com看起来挺好的嘛，你要不要也去看看 "
     "屏幕搭话：右下角那只猫好可爱，毛茸茸的好想摸一摸。"),
    ("屏幕搭话：这个版本号是v2.0和1.5两个都有呢，你装的是哪一个呀 "
     "屏幕搭话：右下角那只猫好可爱，毛茸茸的好想摸一摸。"),
])
def test_ascii_dots_inside_words_and_numbers_do_not_end_a_comment(text):
    """"example.com", "v2.0" and "1.5" are not sentence ends, so a first comment
    that only has those never counts as complete, and nothing is cut there."""
    assert guard_module._dechain((text,)) is None


def test_a_soft_sentence_end_before_the_next_label_still_counts():
    text = "屏幕搭话：这个视频画面好漂亮，色调很温柔呢喵～屏幕搭话：右下角那只猫好可爱，毛茸茸的好想摸一摸。"
    assert guard_module._dechain((text,)) == ("这个视频画面好漂亮，色调很温柔呢喵～",)


def test_a_short_comment_between_two_complete_ones_does_not_break_the_chain():
    text = _COMMENT_A + "屏幕搭话：短。" + _COMMENT_B
    assert guard_module._dechain((text,)) == (_BODY_A,)


@pytest.mark.parametrize("text, expected", [
    ("屏幕搭话 ：" + PARTS[0] + "屏幕搭话 ：" + PARTS[1], PARTS[0]),
    (("I'm here. screen comment: the video looks really lovely and calm today. "
      "screen comment: the cat in the corner is so cute and fluffy."),
     "I'm here. the video looks really lovely and calm today."),
])
def test_a_removed_label_takes_its_separator_with_it(text, expected):
    assert guard_module._dechain((text,)) == (expected,)


@pytest.mark.parametrize("end", ["～", ".", "~"])
def test_a_soft_sentence_end_glued_to_an_english_label_still_counts(end):
    """A letter after "." or "~" usually means a word or number goes on, but
    not when that letter starts the next label."""
    text = ("screen comment: the video looks really lovely and calm today" + end
            + "screen comment: the cat in the corner is so cute and fluffy.")
    assert guard_module._dechain((text,)) == (
        "the video looks really lovely and calm today" + end,
    )


def test_a_chain_completed_by_a_glued_label_is_reported_at_that_label():
    """When the label after the second comment is what completes it, the
    chain is confirmed there, even if nothing complete follows."""
    text = ("screen comment: the video looks really lovely and calm today. "
            "screen comment: the cat in the corner is so cute and fluffy.screen comment: ok")
    assert guard_module._dechain((text,)) == ("the video looks really lovely and calm today.",)
