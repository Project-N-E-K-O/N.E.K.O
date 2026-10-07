from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.unit
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("月台尽头的猫灯", "小剧场《月台尽头的猫灯》"),
        ("《月台尽头的猫灯》", "小剧场《月台尽头的猫灯》"),
        ("『月台尽头的猫灯』", "小剧场『月台尽头的猫灯』"),
    ],
)
def test_theater_memory_title_marks_are_idempotent(title, expected):
    """无书名号时自动补齐，已有成对标题符号时不能再套一层。"""  # noqa: DOCSTRING_CJK
    from config.prompts.prompts_memory import get_theater_memory_context

    rendered = get_theater_memory_context(
        "zh-CN",
        name="小葵",
        master="哥哥",
        title=title,
        status="paused",
    )

    assert expected in rendered
    assert "《《" not in rendered
    assert "》》" not in rendered


@pytest.mark.unit
def test_completed_theater_memory_without_ending_title_stays_completed():
    from config.prompts.prompts_memory import get_theater_memory_context

    rendered = get_theater_memory_context(
        "zh-CN", name="小葵", master="哥哥", title="雨夜合租",
        status="completed", summary="两人保住了共同的住处。",
    )

    assert "这次演绎已经完成" in rendered
    assert "两人保住了共同的住处" in rendered
    assert "暂停" not in rendered


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_recent_history_accepts_string_content():
    from app import memory_server

    fake_config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"test_char": {}}}),
        aget_character_data=AsyncMock(return_value=(
            "master",
            None,
            None,
            None,
            {"human": "Master", "ai": "Catgirl", "system": "System"},
            None,
            None,
            None,
            None,
        )),
    )
    fake_recent = SimpleNamespace(
        aget_recent_history=AsyncMock(return_value=[
            SimpleNamespace(type="system", content="session note"),
            SimpleNamespace(type="human", content="plain user history"),
            SimpleNamespace(type="ai", content="plain ai history"),
        ])
    )

    with patch.object(memory_server.runtime, "_config_manager", fake_config), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent):
        result = await memory_server.get_recent_history("test_char")

    assert "session note" in result
    assert "Master | plain user history" in result
    assert "test_char | plain ai history" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_recent_history_keeps_text_part_content():
    from app import memory_server

    fake_config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"test_char": {}}}),
        aget_character_data=AsyncMock(return_value=(
            "master",
            None,
            None,
            None,
            {"human": "Master", "ai": "Catgirl", "system": "System"},
            None,
            None,
            None,
            None,
        )),
    )
    fake_recent = SimpleNamespace(
        aget_recent_history=AsyncMock(return_value=[
            SimpleNamespace(
                type="human",
                content=[
                    {"type": "text", "text": "part one"},
                    {"type": "image_url", "image_url": "ignored"},
                    {"type": "text", "text": "part two"},
                ],
            ),
        ])
    )

    with patch.object(memory_server.runtime, "_config_manager", fake_config), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent):
        result = await memory_server.get_recent_history("test_char")

    assert "Master | part one\npart two" in result
    assert "ignored" not in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_recent_history_uses_type_as_unknown_speaker():
    from app import memory_server

    fake_config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"test_char": {}}}),
        aget_character_data=AsyncMock(return_value=(
            "master",
            None,
            None,
            None,
            {"human": "Master", "ai": "Catgirl", "system": "System"},
            None,
            None,
            None,
            None,
        )),
    )
    fake_recent = SimpleNamespace(
        aget_recent_history=AsyncMock(return_value=[
            SimpleNamespace(type="tool", content="tool result"),
        ])
    )

    with patch.object(memory_server.runtime, "_config_manager", fake_config), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent):
        result = await memory_server.get_recent_history("test_char")

    assert "tool | tool result" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_recent_history_renders_one_theater_episode_context():
    """日常上下文只渲染单集摘要，不展开完整剧场正文。"""  # noqa: DOCSTRING_CJK
    from app import memory_server
    from utils.llm_client import AIMessage, HumanMessage

    episode = {
        "source": "theater_numeric_v2",
        "session_id": "theater_session",
        "archive_from_revision": 1,
        "archive_through_revision": 1,
        "story_title": "雨夜合租",
        "episode_status": "paused",
    }
    fake_config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"test_char": {}}}),
        aget_character_data=AsyncMock(return_value=(
            "哥哥",
            None,
            None,
            None,
            {"human": "哥哥", "ai": "Catgirl", "system": "System"},
            None,
            None,
            None,
            None,
        )),
    )
    fake_recent = SimpleNamespace(
        aget_recent_history=AsyncMock(return_value=[
            AIMessage(
                content="雨点敲在窗沿。\n\n（抬起头）你来了。",
                metadata={
                    **episode,
                    "parts": [
                        {"kind": "scene_narration", "phase": "opening", "text": "雨点敲在窗沿。"},
                        {"kind": "action", "phase": "opening", "text": "（抬起头）"},
                        {"kind": "dialogue", "phase": "opening", "text": "你来了。"},
                    ],
                },
            ),
            HumanMessage(content="把合同递过去。", metadata=episode),
        ])
    )

    with patch.object(memory_server.runtime, "_config_manager", fake_config), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent):
        result = await memory_server.get_recent_history("test_char", "zh")

    assert "共同演绎小剧场《雨夜合租》" in result
    assert "属于虚构剧情，不代表现实经历" in result
    assert "雨点敲在窗沿。" not in result
    assert "test_char | （抬起头）你来了。" not in result
    assert "哥哥 | 把合同递过去。" not in result
    assert "【旁白】" not in result
    assert "【转场】" not in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_recent_history_merges_incremental_theater_archives_by_session():
    """同 Session 的暂停与完成批次只显示一次，并采用最新完成状态。"""  # noqa: DOCSTRING_CJK

    from app import memory_server
    from utils.llm_client import AIMessage, SystemMessage

    shared = {
        "source": "theater_numeric_v2",
        "story_id": "story_rain",
        "session_id": "theater_session",
        "story_title": "《雨夜合租》",
    }
    fake_config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"test_char": {}}}),
        aget_character_data=AsyncMock(return_value=(
            "哥哥", None, None, None,
            {"human": "哥哥", "ai": "Catgirl", "system": "System"},
            None, None, None, None,
        )),
    )
    fake_recent = SimpleNamespace(
        aget_recent_history=AsyncMock(return_value=[
            AIMessage(content="旧开场正文", metadata={
                **shared,
                "episode_status": "paused",
                "archive_from_revision": 1,
                "archive_through_revision": 3,
            }),
            SystemMessage(content="两人保住了共同的住处。", metadata={
                **shared,
                "memory_tier": "episode_summary",
                "message_kind": "episode_summary",
                "episode_status": "completed",
                "ending_title": "雨停之后",
                "episode_summary": "两人保住了共同的住处。",
                "run_index": 2,
                "story_run_count": 2,
                "ending_titles_seen": ["雨中相守", "雨停之后"],
                "archive_from_revision": 4,
                "archive_through_revision": 8,
            }),
        ]),
    )

    with patch.object(memory_server.runtime, "_config_manager", fake_config), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent):
        result = await memory_server.get_recent_history("test_char", "zh")

    assert result.count("共同演绎小剧场《雨夜合租》") == 1
    assert "第 2 次演绎" in result
    assert "共演绎这个剧本 2 次" in result
    assert "雨中相守、雨停之后" in result
    assert "达成结局《雨停之后》" in result
    assert "两人保住了共同的住处。" in result
    assert "剧情尚未结束时暂停" not in result
    assert "旧开场正文" not in result


def _legacy_theater_render(history, *, lang, name, master):
    """Verbatim theater-capsule loop that get_recent_history and _new_dialog each inlined."""
    from config.prompts.prompts_memory import get_theater_memory_context
    from memory.message_sources import is_theater_memory_message, theater_memory_episode_key
    from utils.llm_client import (
        message_metadata,
    )

    latest_theater_metadata = {
        theater_memory_episode_key(message): message_metadata(message)
        for message in history
        if is_theater_memory_message(message)
    }
    latest_episode_by_story = {}
    latest_rank_by_story = {}
    for position, (episode_key, metadata) in enumerate(latest_theater_metadata.items()):
        story_id = episode_key[0]
        raw_run_index = metadata.get("run_index")
        run_index = (
            raw_run_index
            if isinstance(raw_run_index, int)
            and not isinstance(raw_run_index, bool)
            and raw_run_index > 0
            else 0
        )
        rank = (run_index, position)
        if rank >= latest_rank_by_story.get(story_id, (-1, -1)):
            latest_rank_by_story[story_id] = rank
            latest_episode_by_story[story_id] = episode_key

    rendered = []
    rendered_theater_episodes = set()
    for i in history:
        if is_theater_memory_message(i):
            episode_key = theater_memory_episode_key(i)
            if episode_key in rendered_theater_episodes:
                continue
            metadata = latest_theater_metadata[episode_key]
            is_latest_story_run = (
                latest_episode_by_story.get(episode_key[0]) == episode_key
            )
            rendered.append((i, get_theater_memory_context(
                lang,
                name=name,
                master=master,
                title=str(metadata.get("story_title") or ""),
                status=str(metadata.get("episode_status") or "paused"),
                ending=str(metadata.get("ending_title") or ""),
                summary=str(
                    metadata.get("episode_summary")
                    or metadata.get("ending_summary")
                    or ""
                ),
                run_index=(
                    metadata.get("run_index")
                    if isinstance(metadata.get("run_index"), int)
                    and not isinstance(metadata.get("run_index"), bool)
                    else 0
                ),
                story_run_count=(
                    metadata.get("story_run_count")
                    if is_latest_story_run
                    and isinstance(metadata.get("story_run_count"), int)
                    and not isinstance(metadata.get("story_run_count"), bool)
                    else 0
                ),
                ending_titles=(
                    metadata.get("ending_titles_seen")
                    if is_latest_story_run
                    and isinstance(metadata.get("ending_titles_seen"), list)
                    else []
                ),
            )))
            rendered_theater_episodes.add(episode_key)
            continue
        rendered.append((i, None))
    return rendered


def _golden_theater_histories():
    from utils.llm_client import AIMessage, HumanMessage, SystemMessage

    def capsule(story, session, **metadata):
        return SystemMessage(content=f"{story}/{session}", metadata={
            "source": "theater_numeric_v2",
            "memory_tier": "episode_summary",
            "message_kind": "episode_summary",
            "story_id": story,
            "session_id": session,
            **metadata,
        })

    legacy_body = AIMessage(content="旧开场正文", metadata={
        "source": "theater_numeric_v2",
        "story_id": "rain",
        "session_id": "rain_1",
        "story_title": "雨夜合租",
        "episode_status": "paused",
    })
    return {
        "multi_story_multi_run": [
            HumanMessage(content="早上好"),
            capsule("rain", "rain_1", story_title="雨夜合租", episode_status="completed",
                    ending_title="雨中相守", episode_summary="守住了住处。",
                    run_index=1, story_run_count=1, ending_titles_seen=["雨中相守"]),
            AIMessage(content="早呀"),
            capsule("rain", "rain_2", story_title="《雨夜合租》", episode_status="completed",
                    ending_title="雨停之后", episode_summary="一起等到了天晴。",
                    run_index=2, story_run_count=2,
                    ending_titles_seen=["雨中相守", "雨停之后", "雨停之后", " "]),
            capsule("lamp", "lamp_1", story_title="月台尽头的猫灯", episode_status="paused",
                    episode_summary="灯还亮着。", run_index=1, story_run_count=3),
            HumanMessage(content="继续吧"),
        ],
        "out_of_order_runs_and_session_updates": [
            capsule("rain", "rain_3", story_title="雨夜合租", run_index=3, story_run_count=3,
                    ending_titles_seen=["甲", "乙"]),
            capsule("rain", "rain_1", story_title="雨夜合租", run_index=1, story_run_count=1),
            legacy_body,
            capsule("rain", "rain_1", story_title="雨夜合租", episode_status="completed",
                    ending_summary="最后补上的结局。", run_index=1, story_run_count=1),
            capsule("rain", "rain_3", story_title="雨夜合租", episode_status="completed",
                    ending_title="", episode_summary="未命名结局。", run_index=3,
                    story_run_count=3, ending_titles_seen=["甲", "乙", "丙"]),
        ],
        "missing_and_invalid_metadata": [
            capsule("odd", "odd_1"),
            capsule("odd", "odd_2", story_title=None, episode_status=None, run_index=True,
                    story_run_count=True, ending_titles_seen="甲"),
            capsule("odd", "odd_3", run_index=-4, story_run_count=-2, ending_titles_seen=None),
            capsule("odd", "odd_4", run_index="5", story_run_count=0),
            capsule("neg", "neg_1", run_index=0, story_run_count=-7, ending_titles_seen=["x"]),
            capsule("", "", story_title="无剧本", run_index=2, story_run_count=2),
        ],
        "ordinary_only": [
            HumanMessage(content="你好"),
            AIMessage(content=[{"type": "text", "text": "嗯"}]),
        ],
    }


@pytest.mark.unit
@pytest.mark.parametrize("lang", ["zh", "zh-TW", "en", "ja", "ko", "ru"])
@pytest.mark.parametrize("case", sorted(_golden_theater_histories()))
def test_shared_theater_capsule_render_matches_legacy_inline_loops(lang, case):
    """Golden: the shared helper reproduces the two former inline loops exactly."""
    from app.memory_server.routes import _iter_theater_rendered_history

    history = _golden_theater_histories()[case]
    expected = _legacy_theater_render(history, lang=lang, name="小葵", master="哥哥")
    actual = list(_iter_theater_rendered_history(
        history, lang=lang, name="小葵", master="哥哥",
    ))

    assert [(id(message), text) for message, text in actual] == [
        (id(message), text) for message, text in expected
    ]
    if case != "ordinary_only":
        assert any(text for _message, text in actual)


@pytest.mark.unit
def test_theater_render_passes_only_positive_run_counters():
    """run_index and story_run_count share the positive-int rule."""
    from app.memory_server.routes import _iter_theater_rendered_history
    from utils.llm_client import SystemMessage

    history = [SystemMessage(content="x", metadata={
        "source": "theater_numeric_v2",
        "memory_tier": "episode_summary",
        "story_id": "s",
        "session_id": "s1",
        "story_title": "雨夜合租",
        "run_index": -1,
        "story_run_count": -3,
    })]
    with patch("app.memory_server.routes.get_theater_memory_context") as render:
        render.return_value = "rendered capsule"
        list(_iter_theater_rendered_history(history, lang="zh", name="n", master="m"))

    assert render.call_args.kwargs["story_run_count"] == 0
    assert render.call_args.kwargs["run_index"] == 0


_COMMENT_A = "屏幕搭话 蓝色小车停在一棵大树旁边，树叶的影子落在了车顶上。"
_COMMENT_B = "屏幕搭话 远处的红色小车正在缓慢经过桥面，桥下的河水十分平静。"
_BODY_A = _COMMENT_A[len("屏幕搭话 "):]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_recent_history_cuts_screen_chains_before_rendering():
    """Renewal and restarts bring this history back as system-prompt text,
    past the offline client's request-view projection: the chain must be cut
    here, on the structured messages."""
    from app import memory_server

    fake_config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"test_char": {}}}),
        aget_character_data=AsyncMock(return_value=(
            "master", None, None, None,
            {"human": "Master", "ai": "Catgirl", "system": "System"},
            None, None, None, None,
        )),
    )
    fake_recent = SimpleNamespace(aget_recent_history=AsyncMock(return_value=[
        SimpleNamespace(type="human", content="陪我聊聊"),
        SimpleNamespace(type="ai", content=_COMMENT_A + _COMMENT_B),
        SimpleNamespace(type="ai", content="普通的一句回复。"),
        SimpleNamespace(type="human", content="继续"),
        # A chain split over the replies that end the history: the new
        # session's user turn follows them, so they are one run.
        SimpleNamespace(type="ai", content=_COMMENT_A),
        # cross_server stores assistant turns as text-part lists.
        SimpleNamespace(type="ai", content=[{"type": "text", "text": _COMMENT_B}]),
    ]))

    with patch.object(memory_server.runtime, "_config_manager", fake_config), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent):
        result = await memory_server.get_recent_history("test_char", "zh")

    assert "屏幕搭话" not in result and "红色小车" not in result
    assert result.count(f"test_char | {_BODY_A}") == 2
    assert "test_char | 普通的一句回复。" in result
    assert "Master | 陪我聊聊" in result


def test_new_dialog_renders_through_the_screen_guard():
    """_new_dialog needs the whole runtime to run; pin that it renders the
    recent history through the guarded helper."""
    import inspect
    from app.memory_server import routes

    source = inspect.getsource(routes._new_dialog)
    assert "_screen_guarded_recent_history(" in source
    assert "for i in await runtime.recent_history_manager.aget_recent_history" not in source


@pytest.mark.unit
@pytest.mark.asyncio
async def test_screen_guard_leaves_theater_capsules_alone():
    """Theater capsules are system messages; the screen-chain cut only rewrites
    assistant texts, so a capsule whose summary reads like a comment chain is
    still rendered from its metadata."""
    from app import memory_server
    from utils.llm_client import HumanMessage, SystemMessage

    summary = _COMMENT_A + _COMMENT_B
    fake_config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"test_char": {}}}),
        aget_character_data=AsyncMock(return_value=(
            "master", None, None, None,
            {"human": "Master", "ai": "Catgirl", "system": "System"},
            None, None, None, None,
        )),
    )
    fake_recent = SimpleNamespace(aget_recent_history=AsyncMock(return_value=[
        HumanMessage(content="陪我聊聊"),
        SystemMessage(content=[{"type": "text", "text": summary}], metadata={
            "source": "theater_numeric_v2",
            "story_id": "story_rain",
            "session_id": "theater_session",
            "story_title": "雨夜合租",
            "memory_tier": "episode_summary",
            "message_kind": "episode_summary",
            "episode_status": "completed",
            "ending_title": "雨停之后",
            "episode_summary": summary,
        }),
    ]))

    with patch.object(memory_server.runtime, "_config_manager", fake_config), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent):
        result = await memory_server.get_recent_history("test_char", "zh")

    assert "共同演绎小剧场《雨夜合租》" in result
    assert "红色小车" in result
