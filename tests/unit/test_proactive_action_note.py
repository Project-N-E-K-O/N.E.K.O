"""主动搭话 action_note 元数据回写测试。

覆盖两层契约：
1. ``build_proactive_action_note``：根据 primary_channel + source_links 构造的
   一行 [...] 注解，必须包含本轮实际投递的素材信息（歌名/艺人/来源），且在
   素材缺失时返回空串而不是凭空编出"未知 - 未知"骚扰 LLM 上下文。模板里对人
   的称呼一律用 master_name 实名展开，不写"主人"这类物化称呼。
2. ``finish_proactive_delivery(action_note=...)``：注解只进
   ``_conversation_history``（→ memory_context），不进 send_lanlan_response、
   不进 TTS。
"""
import asyncio
import os
import sys
from queue import Queue

import pytest
from unittest.mock import AsyncMock, MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from config.prompts_proactive import build_proactive_action_note
from main_logic.core import LLMSessionManager
from main_logic.session_state import SessionStateMachine

# 测试公用 master_name —— 任何字符串都行，关键是验证它会被原样展开进 note，
# 而不是被替换成"主人/master/ご主人さま"等物化称呼。
MASTER = "小明"


# ─────────────────────────────────────────────────────────────────────────────
# build_proactive_action_note —— 纯函数行为
# ─────────────────────────────────────────────────────────────────────────────

def test_action_note_empty_when_no_source_links():
    """source_links 为空（chat / vision / 没找到素材）→ 空串，不污染历史。"""
    assert build_proactive_action_note('music', [], 'zh', master_name=MASTER) == ''
    assert build_proactive_action_note('music', None, 'zh', master_name=MASTER) == ''


def test_action_note_vision_channel_always_empty():
    """vision 通道：屏幕本身是用户那侧已有的画面，不是 AI 分享出去的素材，
    哪怕 source_links 不空也不写 note。"""
    links = [{'title': '某曲', 'artist': 'X', 'source': '音乐推荐', 'type': 'music'}]
    assert build_proactive_action_note('vision', links, 'zh', master_name=MASTER) == ''


def test_action_note_chat_unknown_empty_when_no_source_links():
    """chat / unknown / 空通道 + source_links 空 → 空串。
    （AI 自己说的话已经在 full_text 里，无外部素材时不需要追加元数据。）"""
    assert build_proactive_action_note('chat', [], 'zh', master_name=MASTER) == ''
    assert build_proactive_action_note('unknown', [], 'zh', master_name=MASTER) == ''
    assert build_proactive_action_note('', [], 'zh', master_name=MASTER) == ''


def test_action_note_chat_channel_with_music_fallback_uses_music_note():
    """关键回归（Codex review #1041 找出的 case）：

    主路径里 ``should_try_music_fallback`` 允许 LLM Phase 2 输出 [CHAT]
    （→ primary_channel='chat'）时仍然把 music tracks 追加进 source_links
    并设 is_music_used=True，用户那侧**实际听到了歌**。此时 action_note
    必须按 music 模板出，否则 AI 下一轮反问"刚才放的什么"还是答不上来——
    PR 要解决的核心痛点丢失。

    旧实现按 primary_channel 严格分支返回空串；新实现在 chat/unknown 通道
    回退探测 source_links 实际素材。
    """
    links = [{
        'title': '稻香',
        'artist': '周杰伦',
        'url': 'https://example.com/track',
        'source': '音乐推荐',
        'type': 'music',
    }]
    note = build_proactive_action_note('chat', links, 'zh', master_name=MASTER)
    assert '稻香' in note
    assert '周杰伦' in note
    assert MASTER in note


def test_action_note_unknown_channel_falls_back_to_source_links():
    """unknown / 空通道也走探测路径，按 music > meme > web 优先级匹配。"""
    music_link = {'title': 'T', 'artist': 'A', 'source': '音乐推荐', 'type': 'music'}
    meme_link = {'title': 'M', 'source': '微博', 'type': 'meme'}

    note_music = build_proactive_action_note('unknown', [music_link], 'zh', master_name=MASTER)
    assert 'T' in note_music and 'A' in note_music

    note_meme = build_proactive_action_note('', [meme_link], 'zh', master_name=MASTER)
    assert 'M' in note_meme and '微博' in note_meme

    # music + meme 共存 → 优先 music
    note_both = build_proactive_action_note(
        'unknown', [meme_link, music_link], 'zh', master_name=MASTER,
    )
    assert 'T' in note_both
    assert 'M' not in note_both


def test_action_note_music_includes_title_and_artist():
    links = [{
        'title': '稻香',
        'artist': '周杰伦',
        'url': 'https://example.com/track',
        'source': '音乐推荐',
        'type': 'music',
    }]
    note = build_proactive_action_note('music', links, 'zh', master_name=MASTER)
    assert '稻香' in note
    assert '周杰伦' in note
    assert MASTER in note
    assert note.startswith('[') and note.endswith(']')


def test_action_note_does_not_use_dehumanizing_terms():
    """禁止字面量"主人 / master / ご主人さま / 주인 / хозяин"出现在注解里——这是
    物化称呼，所有语言都必须用 master_name 实名展开。"""
    links = [{'title': 'T', 'artist': 'A', 'source': '音乐推荐', 'type': 'music'}]
    forbidden = ['主人', 'master', 'Master', 'ご主人', '주인', 'хозяин', 'Хозяин']
    for lang in ('zh', 'en', 'ja', 'ko', 'ru'):
        note = build_proactive_action_note('music', links, lang, master_name=MASTER)
        for word in forbidden:
            assert word not in note, f"lang={lang} note='{note}' 含物化称呼 '{word}'"


def test_action_note_master_name_is_expanded_literally():
    """master_name 应该原样展开进 note，不被替换或加前缀。"""
    links = [{'title': 'T', 'artist': 'A', 'source': '音乐推荐', 'type': 'music'}]
    for name in ('小明', 'Alice', 'mochi', '猫猫'):
        note = build_proactive_action_note(
            'music', links, 'zh', master_name=name,
        )
        assert name in note


def test_action_note_music_skips_unrelated_links():
    """primary_channel=music 但 source_links 里只有 web 链接（异常路径）→ 空串。
    避免拿一个 web 标题当歌名编出"《某条新闻》— 未知艺术家"。"""
    links = [{
        'title': '某条新闻',
        'url': 'https://news.example.com/x',
        'source': 'B站',
    }]
    assert build_proactive_action_note('music', links, 'zh', master_name=MASTER) == ''


def test_action_note_music_picks_music_link_among_others():
    """source_links 里 web link 在前、music link 在后（fallback 路径常见），
    music 通道仍要挑出 music 那条。"""
    links = [
        {'title': '一条新闻', 'source': '微博', 'type': 'web'},
        {'title': '夜曲', 'artist': '周杰伦', 'source': '音乐推荐', 'type': 'music'},
    ]
    note = build_proactive_action_note('music', links, 'zh', master_name=MASTER)
    assert '夜曲' in note
    assert '周杰伦' in note
    assert '新闻' not in note


def test_action_note_meme_uses_title_and_source():
    links = [{
        'title': '猫猫表情包',
        'url': 'https://example.com/m.gif',
        'source': '微博',
        'type': 'meme',
    }]
    note = build_proactive_action_note('meme', links, 'zh', master_name=MASTER)
    assert '猫猫表情包' in note
    assert '微博' in note
    assert MASTER in note


def test_action_note_meme_falls_back_when_type_missing():
    """meme 通道但素材没填 type=meme（早期 fallback 链路），按非音乐链接兜底。"""
    links = [{
        'title': '一只柴犬',
        'url': 'https://example.com/d.png',
        'source': '微博',
    }]
    note = build_proactive_action_note('meme', links, 'zh', master_name=MASTER)
    assert '一只柴犬' in note
    assert '微博' in note


def test_action_note_web_skips_music_recommendations_appended_at_tail():
    """real-world：primary_channel=web 时，build_proactive_response 先放 web link，
    随后 _append_music_recommendations 可能把音乐 rec 追加到 source_links 末尾
    （music fallback 路径）。web 注解不能错挑成那条音乐项。"""
    links = [
        {'title': 'AI 大新闻', 'source': '36kr', 'mode': 'news'},
        {'title': '《起风了》', 'artist': '吴青峰', 'source': '音乐推荐', 'type': 'music'},
    ]
    note = build_proactive_action_note('news', links, 'zh', master_name=MASTER)
    assert 'AI 大新闻' in note
    assert '36kr' in note
    assert '起风了' not in note


def test_action_note_web_subchannels_all_route_to_web_template():
    """web/news/video/home/personal/window 这几个细粒度子通道共享 web 模板。

    集合必须与 ``main_routers/system_router.py:build_proactive_response`` 里
    ``web_link.get('mode', 'web')`` 产出的 mode 同步（参见
    PROACTIVE_SOURCE_LABELS keys）。漏 'window' 是 CodeRabbit review #1041
    找出的回归 bug：window 通道会落到 chat fallback、被 music-first 优先级
    误识别成"放歌"。
    """
    link = {'title': 'foo', 'source': 'bar'}
    for ch in ('web', 'news', 'video', 'home', 'personal', 'window'):
        note = build_proactive_action_note(ch, [link], 'zh', master_name=MASTER)
        assert note != '', f'channel={ch} 漏到 fallback'
        assert 'foo' in note and 'bar' in note


def test_action_note_window_channel_with_music_recs_does_not_pick_music():
    """关键回归（CodeRabbit review #1041 #2）：window 通道下，即便 source_links
    里同时混进 music recs（_append_music_recommendations 仍可能把音乐 track
    追加进 web/window 路径的 source_links），window note 也必须按 web 模板
    出，不能被 music-first 优先级误识别成"放歌"。"""
    links = [
        {'title': '搜索结果', 'source': 'B站', 'mode': 'window'},
        {'title': '稻香', 'artist': '周杰伦', 'source': '音乐推荐', 'type': 'music'},
    ]
    note = build_proactive_action_note('window', links, 'zh', master_name=MASTER)
    assert '搜索结果' in note
    assert 'B站' in note
    assert '稻香' not in note  # music 不能抢走 window note
    assert '周杰伦' not in note


def test_action_note_uses_placeholder_for_missing_fields():
    """title/artist/source 缺失时按本地化占位符兜底，不出现 'None'。"""
    note = build_proactive_action_note(
        'music',
        [{'type': 'music', 'source': '音乐推荐'}],  # 缺 title + artist
        'zh',
        master_name=MASTER,
    )
    assert note != ''
    assert 'None' not in note
    assert '未命名' in note or '未知' in note


def test_action_note_empty_master_falls_back_to_neutral_placeholder():
    """master_name 传空时按本地化中性占位符兜底（"对方/them/相手/상대/собеседника"），
    保证不出现"主人"等物化称呼。"""
    links = [{'title': 'T', 'artist': 'A', 'source': '音乐推荐', 'type': 'music'}]
    note_zh = build_proactive_action_note('music', links, 'zh', master_name='')
    note_en = build_proactive_action_note('music', links, 'en', master_name='')
    assert '主人' not in note_zh
    assert 'master' not in note_en.lower()
    assert '对方' in note_zh
    assert 'them' in note_en


def test_action_note_localizes_template_per_language():
    """同一组数据按 language 走不同模板。"""
    links = [{'title': 'Hello', 'artist': 'Adele', 'source': '音乐推荐', 'type': 'music'}]
    zh = build_proactive_action_note('music', links, 'zh', master_name=MASTER)
    en = build_proactive_action_note('music', links, 'en', master_name=MASTER)
    ja = build_proactive_action_note('music', links, 'ja', master_name=MASTER)
    # 各语言都包含 master_name + 标题 + 艺人
    for note in (zh, en, ja):
        assert MASTER in note
        assert 'Hello' in note
        assert 'Adele' in note
    # 各语言模板应当不同（最起码不会三种语言输出相同字符串）
    assert len({zh, en, ja}) == 3


def test_action_note_unknown_language_falls_back_to_english():
    """_loc 静默回退：未翻译语言（如 'es'）走英文模板。"""
    links = [{'title': 'X', 'artist': 'Y', 'source': '音乐推荐', 'type': 'music'}]
    note = build_proactive_action_note('music', links, 'es', master_name=MASTER)
    # 走英文模板，但 master_name 仍按传入展开
    assert MASTER in note
    assert 'X' in note and 'Y' in note


def test_action_note_normalizes_region_language_codes():
    """回归（CodeRabbit review #1041）：caller 传区域标签（zh-CN / ja-JP / en-US 等）
    时，placeholders 和 _loc 都要走对应短码模板，不能双双落到英文兜底导致丢失
    本地化。生产 caller 通常已经传短码，但这是防御性护栏。"""
    links = [{'title': 'X', 'artist': 'Y', 'source': '音乐推荐', 'type': 'music'}]
    note_full = build_proactive_action_note('music', links, 'zh-CN', master_name=MASTER)
    note_short = build_proactive_action_note('music', links, 'zh', master_name=MASTER)
    # 区域标签和短码应得到等价的中文模板输出
    assert note_full == note_short
    # 兜底：归一化失败也不应回落英文 placeholder（"Unknown Artist"）
    note_ja_full = build_proactive_action_note('music', links, 'ja-JP', master_name=MASTER)
    assert 'Unknown Artist' not in note_ja_full


def test_action_note_russian_master_fallback_stays_grammatically_consistent():
    """回归（CodeRabbit review #1041 #1）：ru placeholders.master 是 'собеседника'
    （genitive 形式），三条 ru 模板都用 'для + genitive' 介词结构（避免之前
    'с {master}' instrumental 与 fallback genitive 冲突的 case mismatch）。
    空名兜底必须给出语法自洽的句子，不能出现 'с собеседника' 这种 instrumental
    介词配 genitive 名词的拼接错误。
    """
    music_links = [{'title': 'X', 'artist': 'Y', 'source': '音乐推荐', 'type': 'music'}]
    meme_links = [{'title': 'M', 'source': 'src', 'type': 'meme'}]
    web_links = [{'title': 'W', 'source': 'src'}]

    for primary, links in (('music', music_links), ('meme', meme_links), ('web', web_links)):
        note = build_proactive_action_note(primary, links, 'ru', master_name='')
        # fallback 'собеседника' 出现在 для 之后是合法 genitive
        assert 'собеседника' in note
        # 不能出现 'с собеседника'（instrumental 介词 + genitive 名词的混搭）
        assert ' с собеседника' not in note
        assert ' с собеседника' not in note  # 防御 NBSP


def test_action_note_collapses_multiline_title_into_single_line():
    """回归（CodeRabbit review #1041）：action_note 是单行元数据，title/artist/source/
    master_name 任一含 \\n / \\r / \\t 时必须强制压成一行；否则 AIMessage.content
    被破坏成多行，下游 LLM context 渲染会把 note 误当对话内容。"""
    links = [{
        'title': '稻香\n副标题',
        'artist': '周杰伦\t',
        'source': '音乐推荐',
        'type': 'music',
    }]
    note = build_proactive_action_note('music', links, 'zh', master_name=f'小明\r\n')
    assert '\n' not in note
    assert '\r' not in note
    assert '\t' not in note
    # 关键内容仍保留（空白被折叠成单空格）
    assert '稻香' in note and '副标题' in note
    assert '周杰伦' in note
    assert '小明' in note


# ─────────────────────────────────────────────────────────────────────────────
# finish_proactive_delivery(action_note=...) —— action_note 只进历史
# ─────────────────────────────────────────────────────────────────────────────


def _make_mgr() -> LLMSessionManager:
    """复用 test_proactive_sid_guard.py 的最小 manager 装配。"""
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.use_tts = True
    mgr.tts_cache_lock = asyncio.Lock()
    mgr.lock = asyncio.Lock()
    mgr._proactive_write_lock = asyncio.Lock()
    mgr.tts_pending_chunks = []
    mgr.tts_request_queue = Queue()
    mgr.tts_response_queue = Queue()
    mgr.tts_thread = MagicMock()
    mgr.tts_thread.is_alive.return_value = True
    mgr.tts_ready = True
    mgr.current_speech_id = None
    mgr._tts_done_queued_for_turn = False
    mgr.lanlan_name = "Test"
    mgr.session = None
    mgr.websocket = None
    mgr.sync_message_queue = Queue()
    mgr._enqueue_tts_text_chunk = MagicMock()
    mgr._respawn_tts_worker = MagicMock()
    mgr._tts_markdown_stripper = MagicMock()
    mgr._tts_markdown_stripper.flush.return_value = ""
    mgr._tts_bracket_stripper = MagicMock()
    mgr._tts_bracket_stripper.feed.side_effect = lambda text: text
    mgr._tts_bracket_stripper.flush.return_value = ""
    mgr._tts_norm_speech_id = None
    mgr.send_lanlan_response = AsyncMock()
    mgr.state = SessionStateMachine(lanlan_name="Test")
    mgr._activity_tracker = MagicMock()
    mgr._current_ai_turn_text = ''
    return mgr


@pytest.mark.asyncio
async def test_finish_proactive_delivery_appends_action_note_to_history():
    """action_note 非空 → AIMessage 内容尾部追加 \\n + note；send_lanlan_response
    收到的仍是裸 full_text（前端不会展示这条元数据）。"""
    mgr = _make_mgr()
    mgr.current_speech_id = "s"
    mgr.session = MagicMock()
    mgr.session._conversation_history = []
    note = f"[给{MASTER}放了《稻香》— 周杰伦]"

    result = await LLMSessionManager.finish_proactive_delivery(
        mgr, "给你放首歌～", expected_speech_id="s", action_note=note,
    )

    assert result is True
    assert len(mgr.session._conversation_history) == 1
    history_text = mgr.session._conversation_history[0].content
    assert "给你放首歌～" in history_text
    assert note in history_text
    # 前端不能看到 note：send_lanlan_response 只收 full_text
    sent_text = mgr.send_lanlan_response.call_args.args[0]
    assert sent_text == "给你放首歌～"
    assert note not in sent_text


@pytest.mark.asyncio
async def test_finish_proactive_delivery_empty_action_note_unchanged():
    """action_note 为空串/None → 历史与原行为完全一致（不引入多余换行）。"""
    mgr = _make_mgr()
    mgr.current_speech_id = "s"
    mgr.session = MagicMock()
    mgr.session._conversation_history = []

    await LLMSessionManager.finish_proactive_delivery(
        mgr, "纯聊天内容", expected_speech_id="s", action_note="",
    )
    assert mgr.session._conversation_history[0].content == "纯聊天内容"

    mgr2 = _make_mgr()
    mgr2.current_speech_id = "s"
    mgr2.session = MagicMock()
    mgr2.session._conversation_history = []
    await LLMSessionManager.finish_proactive_delivery(
        mgr2, "纯聊天内容", expected_speech_id="s", action_note=None,
    )
    assert mgr2.session._conversation_history[0].content == "纯聊天内容"


@pytest.mark.asyncio
async def test_finish_proactive_delivery_action_note_skipped_on_sid_mismatch():
    """sid 不匹配（用户已接管）时整轮 finish 短路，action_note 也不能漏写进历史。"""
    mgr = _make_mgr()
    mgr.current_speech_id = "s_user"
    mgr.session = MagicMock()
    mgr.session._conversation_history = []

    result = await LLMSessionManager.finish_proactive_delivery(
        mgr, "孤儿 proactive", expected_speech_id="s_proactive",
        action_note=f"[给{MASTER}放了《X》— Y]",
    )
    assert result is False
    assert mgr.session._conversation_history == []
