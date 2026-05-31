# -*- coding: utf-8 -*-
"""
用户**负面意图 / 回避指令** prompt + 模板集中地。包括两类相关但用途不同
的工具：

(1) **Ban-topic 抽取（带 term）**：``DIRECTIVE_PATTERNS`` 7 locale 正则模板 +
    ``extract_directives()``。匹配祈使句结构"动词 + 对象"，capture group 直接
    拿到话题。命中后由 ``memory.user_directives`` 持久化 3 天（TTL 见
    ``USER_DIRECTIVE_TTL_SECONDS``），下次 ``_build_initial_prompt`` 启动时把
    活跃 term 注入 system prompt 让模型避开。

(2) **Negative-intent 关键词扫描（boolean）**：``NEGATIVE_KEYWORDS_I18N`` +
    ``scan_negative_keywords()``。frozenset 子串扫描，命中即"用户希望结束当前
    话题"（含 *显式回避* 与 *嫌烦* 两族）。下游由 evidence 系统
    （``app/memory_server._amaybe_trigger_negative_keyword_hook``）异步派一次
    LLM target check（``NEGATIVE_TARGET_CHECK_PROMPT``）决定打哪条 fact 的
    disputation signal。

设计动机
--------
用户偶尔会显式说"别再提 X / 不要叫我 X / stop saying X / その話はもう"——
这些都是显式的 ban-topic 指令。本轮 LLM 看得到原话不需要处理；但等到**下一轮
会话重启**（archive / cold start / 重连），那句话早就被 compress 掉了，模型
会再次踩雷。

落点：在 user_utterance 入口跑正则抽取 → 命中 → 写进
``memory/{name}/user_directives.json``（3 天 TTL，``memory/user_directives.py``
负责存储）。下次 ``_build_initial_prompt`` 把活跃条目拼成一段注入到 system
prompt 末尾。

约定：宁可错杀
--------------
- 所有 locale 模板**并行**跑，不依赖检测语言（用户中英混说很常见）
- 抓到的 term 只做轻量 trim（剥两端标点 + 语气词），不做语义校验
- term 长度 ∈ [2, 40] 才入库；越界丢弃
- 正则只覆盖**带具体对象**的指令（ban_topic）。无对象的"闭嘴/换话题/shut up"
  本身在 context 已经被 LLM 看到，又不适合持久化，**不**抽取
- 错杀代价 = 用户下次再说一遍；模型代价 = system prompt 多一行；
  漏抽代价 = 用户被再次冒犯。所以倾向于宽松。

ban-topic regex vs. negative-keyword scan 的差异
------------------------------------------------
- regex 能直接 capture term（祈使句结构清晰），用于 user_directives 的写盘
- substring scan 只判定"有没有负面意图"，捕不到 term；用于 evidence 的
  fast pre-filter（命中后 LLM 复核 target），还能涵盖"嫌烦型"（"烦死"、
  "annoying"——这些无 term，不归 directive，但仍是 negative signal）
"""
from __future__ import annotations

import re
from typing import List, Tuple

from config.prompts.prompts_sys import _loc


# 抓到 term 后剥两端的字符：标点 + 各语言语气助词 / 修饰小尾巴。
# 全在尾部 strip，不影响中间内容。
_TRIM_TRAIL = (
    # ASCII / CJK 标点 / 空白
    " \t\n\r"
    ".,!?;:\"'`()[]{}<>"
    "。！？，；：、…—·"
    "“”‘’（）【】《》「」『』"
)
# zh / ja 助词、句末 particle（出现在 term 尾部时一并剥掉）
_TRIM_TRAIL_TOKENS = (
    # zh
    "了", "啊", "呀", "吧", "嘛", "哦", "呗", "啦", "呢", "嘞", "诶",
    # ja
    "ね", "よ", "わ", "の", "って", "なんて", "という",
    # ko
    "요", "은", "는", "이", "가", "을", "를", "에", "에서",
    # ru (鲜见词尾 particle)
    # es
    "porfa", "porfavor",
    # pt
    # en
    "please",
)


def _norm_lang(lang: str) -> str:
    """归一化 lang code（``zh-CN`` → ``zh``、``pt-BR`` → ``pt`` 等）。

    本模块的 render 函数都靠 dict 精确 key 取模板；如果上游把
    ``user_language`` 直接传过来（带 region 后缀），会全部走英文兜底——这是
    用户可见的回归。在边界归一化一次，比要求所有调用方都先 normalize 更稳。

    策略：优先走 ``config._runtime.normalize_language_code``（app 启动注册了
    ``utils.language_utils.normalize_language_code``，能识别 Steam literal
    如 ``schinese`` → ``zh``，未知语言归 ``en``——render 函数用英文兜底）；
    resolver 未绑定时退化为本地 split 兜底。

    ⚠️ 该 helper 服务于 i18n **template rendering** 路径（未知 → en）。如果
    需要"未知 → 中文"的兜底（比如 ``scan_negative_keywords`` 的契约），不要
    复用此 helper，自己写本地 strip——见该函数实现。
    """
    if not lang:
        return 'en'
    try:
        from config._runtime import normalize_language_code as _nlc
        out = _nlc(lang, format='short') or lang
    except Exception:
        out = lang
    # Defensive split: resolver 未绑定（partial entrypoint / 测试直跑）时
    # ``_nlc`` 会**原样**返回输入；这里手动剥 region 后缀，保 zh-CN → zh
    # 这种基础归一化在测试环境也能工作。已是短码则 split 是 no-op。
    if '-' in out or '_' in out:
        out = out.split('-', 1)[0].split('_', 1)[0]
    return out or 'en'


def _trim_term(term: str) -> str:
    """裁剪 term：先剥尾部 particle / 修饰词，再剥两端标点 + 空白。"""
    if not term:
        return ""
    s = term.strip()
    changed = True
    # 反复剥尾词，直到稳定（"了啊吧" 这种连续助词）
    while changed:
        changed = False
        for tok in _TRIM_TRAIL_TOKENS:
            if s.endswith(tok) and len(s) > len(tok):
                s = s[: -len(tok)].rstrip()
                changed = True
        # 同时剥两端标点
        new_s = s.strip(_TRIM_TRAIL)
        if new_s != s:
            s = new_s
            changed = True
    return s.strip()


# ---------------------------------------------------------------------------
# 正则模板：(locale, kind, compiled_pattern, capture_group_index)
#
# 每条 pattern 必须有一个 capture group 给 term。
# kind 目前只有 ``ban_topic``（带 term）；将来若加 ``rename_request`` 等
# 在此扩展。
# ---------------------------------------------------------------------------

# 各 locale 内的"动词块"（说/提/talk about/言う/...）由各 locale 自己列。
# pattern 全部 re.compile 以 IGNORECASE / UNICODE 跑。

_PATTERNS_RAW: List[Tuple[str, str, str]] = [
    # ---------- zh ----------
    # 别/不要/不许/不准 + （再）+ 动词 + 对象
    # terminator 不放 ``\s``：zh 句子里中英混说时（"别叫我 John Smith"）lazy
    # ``(.{1,40}?)`` 会在第一个空格切断成 "John"。让终结符必须是标点 / EOL /
    # 句末助词，多词 NP 才能被完整捕获（codex P2）。
    ("zh", "ban_topic",
     r"(?:别|不要|不许|不准|莫|休|甭)\s*(?:再)?\s*"
     r"(?:说|提|聊|讲|谈|讨论|扯|提起|提及|讲到|聊到|谈起|谈到|说起|说到|喊我|叫我|管我叫|称呼我为?)\s*"
     r"(.{1,40}?)(?:\s*(?:了|啊|呀|嘛|哦|呗|吧|啦|呢))?(?:[，。！？；,.!?;]|\s*$)"),
    # X + 这个? + 别(再)+ 提
    ("zh", "ban_topic",
     r"(.{1,30}?)\s*(?:这个|这事|这话题|这件事)?\s*别\s*(?:再)?\s*"
     r"(?:说|提|聊|讲|提了|提起|提及)\s*(?:了)?(?:[，。！？；,.!?;\s]|$)"),
    # 不想/不愿 + 聊/讨论 + X — 同上：terminator 不要 \s，否则多词 NP 被切
    ("zh", "ban_topic",
     r"(?:我)?\s*(?:不想|不愿意|不愿|懒得|没心情)\s*(?:再)?\s*"
     r"(?:说|提|聊|讲|谈|讨论)\s*(.{1,40}?)(?:\s*(?:了|的事))?(?:[，。！？；,.!?;]|\s*$)"),
    # 关于 X + 别(再)+ 说
    ("zh", "ban_topic",
     r"关于\s*(.{1,30}?)\s*(?:的事)?\s*(?:就)?\s*别\s*(?:再)?\s*"
     r"(?:说|提|聊|讲)\s*(?:了)?(?:[，。！？；,.!?;\s]|$)"),

    # ---------- en ----------
    # stop/don't/quit + verb + (about|saying) + X
    # ``X`` 是英文 NP，常带空格（"my ex"、"the weather"）。terminator 用
    # filler-word / 标点 / 句尾，避免 lazy ``.{1,40}?`` 在 X 内的第一个空格就
    # 切断成 "my"。
    ("en", "ban_topic",
     r"(?:please\s+)?(?:stop|quit|don'?t|do\s+not|no\s+more)\s+"
     r"(?:talking\s+about|talk\s+about|saying|say|mentioning|mention|"
     r"bringing\s+up|bring\s+up|going\s+on\s+about|"
     r"calling\s+me\s+a|calling\s+me|call\s+me\s+a|call\s+me)\s+"
     r"(.{1,40}?)"
     r"(?:\s+(?:again|anymore|any\s+more|please|ever|already|now|"
     r"forever|today|tonight|right\s+now|in\s+(?:front|public))"
     r"|[,.!?;]|$)"),
    # X + is off limits / off the table / not a topic
    ("en", "ban_topic",
     r"(.{1,30}?)\s+is\s+(?:off[\s\-]?limits|off\s+the\s+table|a\s+(?:no[\s\-]?go|forbidden)\s+topic)"
     r"(?:[\s,.!?;]|$)"),
    # I don't want to talk/hear about X
    # X 是 NP 可能含空格（"my ex girlfriend"）。terminator 用 filler-word /
    # 标点 / 句尾，否则 lazy ``.{1,40}?`` 在第一个空格就切断成 "my"（codex P1）。
    ("en", "ban_topic",
     r"i\s+(?:don'?t|do\s+not|really\s+don'?t)\s+(?:want\s+to|wanna)\s+"
     r"(?:talk|hear|discuss|think)\s+(?:about|of)\s+(.{1,40}?)"
     r"(?:\s+(?:anymore|any\s+more|again|ever|already|right\s+now|today|tonight|please)"
     r"|[,.!?;]|$)"),
    # drop the X / leave X alone (subject)
    ("en", "ban_topic",
     r"(?:drop|leave\s+alone)\s+(?:the\s+|that\s+)?(.{1,30}?)\s+"
     r"(?:topic|subject|thing|stuff|already)(?:[\s,.!?;]|$)"),

    # ---------- ja ----------
    # X + のこと/について + は + もう + 言わないで/やめて/しないで
    ("ja", "ban_topic",
     r"(.{1,40}?)\s*(?:のこと|の話|について|に関して|っていう話)\s*"
     r"(?:は)?\s*(?:もう|二度と|これ以上)?\s*"
     r"(?:言わないで|話さないで|しないで|やめて|止めて|よして|聞きたくない|触れないで)"),
    # もう + X + (の話) + (は) + 嫌だ/聞きたくない
    ("ja", "ban_topic",
     r"もう\s*(.{1,40}?)\s*(?:のこと|の話)?\s*(?:は)?\s*"
     r"(?:嫌|いや|聞きたくない|話したくない|やめて)"),
    # X + って + 呼ばないで / 言わないで
    ("ja", "ban_topic",
     r"(.{1,30}?)\s*(?:って|とは|なんて)\s*"
     r"(?:呼ばないで|言わないで|呼ぶな|言うな)"),

    # ---------- ko ----------
    # X + (에 대해|얘기|이야기) + (는)? + 그만 / 하지 마 / 꺼내지 마
    ("ko", "ban_topic",
     r"(.{1,40}?)\s*(?:에\s*대해서?|얘기|이야기|소리|말)\s*(?:는|은)?\s*"
     r"(?:그만|하지\s*마(?:세요|십시오)?|꺼내지\s*마(?:세요)?|관두|치워)"),
    # 다시는 + X + 말하지 마 / 꺼내지 마
    ("ko", "ban_topic",
     r"(?:다시는|두\s*번\s*다시|이제)\s*(.{1,40}?)\s*"
     r"(?:말하지|꺼내지|언급하지)\s*마(?:세요|십시오)?"),
    # X + (이|가)? + 듣기 싫다 / 짜증나
    ("ko", "ban_topic",
     r"(.{1,30}?)\s*(?:이|가)?\s*(?:듣기\s*싫|말하기\s*싫|짜증나|지긋지긋)"),

    # ---------- ru ----------
    # не говори / хватит про / прекрати + (preposition)? + X
    # 介词 "про / о / об / обо" 出现在动词后 + term 前，必须先 consume 才能
    # 让 (.{1,40}?) 捕获到实际话题；否则贪心地把介词当 term。
    # term 用 en 同款 filler-word terminator，支持 "моей бывшей" 这类多词短语。
    ("ru", "ban_topic",
     r"(?:не\s+(?:говори|упоминай|повторяй|произноси|обсуждай|называй\s+меня)|"
     r"хватит\s+(?:говорить|обсуждать|упоминать)|"
     r"перестань\s+(?:говорить|обсуждать|упоминать|называть\s+меня)|"
     r"прекрати\s+(?:говорить|обсуждать|упоминать|называть\s+меня))\s+"
     r"(?:про\s+|обо?\s+|о\s+)?"  # 可选介词
     r"(.{1,40}?)"
     r"(?:\s+(?:больше|никогда|пожалуйста|снова|опять|вообще|сегодня)"
     r"|[,.!?;]|$)"),
    # о X + больше + не говори
    ("ru", "ban_topic",
     r"(?:обо|об|о)\s+(.{1,30}?)\s+больше\s+не\s+(?:говори|упоминай)"),
    # я не хочу + (говорить|слышать) + о X — 同 en 的 filler-word terminator，
    # 支持 "моей бывшей" 这种多词短语。
    ("ru", "ban_topic",
     r"я\s+не\s+хочу\s+(?:говорить|слышать|обсуждать)\s+(?:обо|об|о)\s+(.{1,40}?)"
     r"(?:\s+(?:больше|никогда|пожалуйста|снова|опять|вообще|сегодня)"
     r"|[,.!?;]|$)"),

    # ---------- es ----------
    # no hables / no menciones / deja de hablar + (de|sobre) + X
    ("es", "ban_topic",
     r"(?:no\s+(?:hables|menciones|digas|sigas\s+hablando|me\s+llames)|"
     r"deja\s+de\s+(?:hablar|mencionar|llamarme)|"
     r"para\s+de\s+(?:hablar|mencionar))\s+"
     r"(?:de|sobre|acerca\s+de)?\s*(.{1,40}?)"
     r"(?:\s+(?:más|nunca|jamás|otra\s+vez|de\s+nuevo|por\s+favor|porfa|hoy|ahora)"
     r"|[,.!?;]|$)"),
    # no quiero + (oír|hablar|saber) + (de|nada de) + X — 同 en/ru
    ("es", "ban_topic",
     r"no\s+quiero\s+(?:oír|hablar|saber|escuchar)\s+(?:nada\s+)?(?:de|sobre)\s+"
     r"(.{1,40}?)"
     r"(?:\s+(?:más|nunca|jamás|otra\s+vez|de\s+nuevo|por\s+favor|porfa|hoy|ahora)"
     r"|[,.!?;]|$)"),

    # ---------- pt ----------
    # não fale / não mencione / pare de falar + (de|sobre) + X
    ("pt", "ban_topic",
     r"(?:não\s+(?:fale|mencione|diga|continue\s+falando|me\s+chame)|"
     r"pare\s+de\s+(?:falar|mencionar|me\s+chamar)|"
     r"deix[ea]\s+de\s+(?:falar|mencionar))\s+"  # deixe de / deixa de（codex P2）
     r"(?:de|sobre|a\s+respeito\s+de)?\s*(.{1,40}?)"
     r"(?:\s+(?:mais|nunca|jamais|de\s+novo|outra\s+vez|por\s+favor|hoje|agora)"
     r"|[,.!?;]|$)"),
    # não quero + (ouvir|falar|saber) + (de|sobre|nada de) + X — 同 en/ru
    ("pt", "ban_topic",
     r"não\s+quero\s+(?:ouvir|falar|saber|escutar)\s+(?:nada\s+)?(?:de|sobre)\s+"
     r"(.{1,40}?)"
     r"(?:\s+(?:mais|nunca|jamais|de\s+novo|outra\s+vez|por\s+favor|hoje|agora)"
     r"|[,.!?;]|$)"),
]


# 编译期一次性 compile，运行时直接复用。
DIRECTIVE_PATTERNS: List[Tuple[str, str, "re.Pattern[str]"]] = [
    (locale, kind, re.compile(raw, re.IGNORECASE | re.UNICODE))
    for locale, kind, raw in _PATTERNS_RAW
]


def extract_directives(text: str) -> List[Tuple[str, str, str]]:
    """对一段 user 文本跑所有 locale × kind 模板，返回 ``[(locale, kind, term)]``。

    - 所有模板**并行**尝试，不预先检测语言
    - 命中后 term 经 ``_trim_term`` 清洗，长度必须 ∈ [2, 40]
    - 同一 ``(kind, term_lower)`` 在结果列表里只保留一次（保留首个匹配的 locale，
      因为重复入库由 ``UserDirectivesManager.record`` 再去重一遍）

    重复模式是有意为之：upstream 多语言混说时一句话可能命中多个 locale 的
    pattern；这里先去重避免一句话灌出 5 条记录，但同一句话**不同**的 term
    （"别提小明和小红"）仍会各自被记录——前提是模板能拆出两次匹配。
    """
    if not text:
        return []
    seen: set[tuple[str, str]] = set()
    out: List[Tuple[str, str, str]] = []
    for locale, kind, pat in DIRECTIVE_PATTERNS:
        for m in pat.finditer(text):
            try:
                term_raw = m.group(1)
            except IndexError:
                continue
            term = _trim_term(term_raw)
            if not (2 <= len(term) <= 40):
                continue
            key = (kind, term.casefold())
            if key in seen:
                continue
            seen.add(key)
            out.append((locale, kind, term))
    return out


# ---------------------------------------------------------------------------
# 下一轮会话注入用的 system prompt 片段
# ---------------------------------------------------------------------------
# 历史的"用户最近表示不想聊"列表会被拼成 ``- {term1}\n- {term2}\n``，再用
# 各 locale 的模板包一层 header / footer。两个槽位：
#   {items}     —— bullet list
#   {n}         —— 条数（少数语言语法需要单复数）
#
# 渲染层：UserDirectivesManager.render_prompt_block(lanlan_name, lang)。

USER_DIRECTIVES_PROMPT_BLOCK = {
    'zh': (
        "\n\n[用户最近明确表示过不想聊或不喜欢被提到以下内容（共{n}项）]\n"
        "{items}\n"
        "请在本次会话里主动避开这些话题或称呼，除非用户自己重新提起。"
    ),
    'en': (
        "\n\n[The user recently asked not to discuss or be referred to as the "
        "following ({n} item(s))]\n"
        "{items}\n"
        "Please actively steer clear of these topics or labels in this session, "
        "unless the user brings them up again."
    ),
    'ja': (
        "\n\n[最近、ユーザーが話したくない・呼ばれたくないと明示した内容（{n}件）]\n"
        "{items}\n"
        "今回のセッションでは、ユーザー自身が再び話題にしない限り、"
        "これらの話題や呼び方を能動的に避けてください。"
    ),
    'ko': (
        "\n\n[사용자가 최근에 언급하지 말거나 그렇게 부르지 말라고 명확히 요청한 항목 ({n}개)]\n"
        "{items}\n"
        "이번 세션에서는 사용자가 직접 다시 꺼내지 않는 한, "
        "이러한 화제나 호칭을 적극적으로 피해 주세요."
    ),
    'ru': (
        "\n\n[Пользователь недавно явно просил не обсуждать или не называть "
        "следующее ({n} шт.)]\n"
        "{items}\n"
        "В этой сессии активно избегайте этих тем и обращений, "
        "если пользователь сам к ним не вернётся."
    ),
    'es': (
        "\n\n[El usuario pidió explícitamente no hablar de o no ser llamado/a "
        "con lo siguiente ({n} elemento(s))]\n"
        "{items}\n"
        "Evita activamente estos temas o etiquetas en esta sesión, "
        "salvo que el propio usuario los vuelva a sacar."
    ),
    'pt': (
        "\n\n[O usuário pediu explicitamente para não falar sobre ou ser "
        "chamado(a) pelo seguinte ({n} item(ns))]\n"
        "{items}\n"
        "Evite ativamente esses tópicos ou rótulos nesta sessão, "
        "a menos que o próprio usuário volte a mencioná-los."
    ),
}


def render_directives_block(terms: List[str], lang: str) -> str:
    """把 active term 列表渲染成一段 system-prompt 文本（含 leading newlines）。

    空列表 → 返回 ""（调用方直接 concat，不需要判空）。
    ``lang`` 接受完整 locale（``zh-CN`` 等），内部归一化为 short code。
    """
    if not terms:
        return ""
    short = _norm_lang(lang)
    template = USER_DIRECTIVES_PROMPT_BLOCK.get(short) or USER_DIRECTIVES_PROMPT_BLOCK['en']
    items = "\n".join(f"- {t}" for t in terms)
    return template.format(items=items, n=len(terms))


# ---------------------------------------------------------------------------
# 防复读（anti-repeat）— 注入"最近高频 topic 词"提示
# ---------------------------------------------------------------------------
# 来源：``memory.anti_repeat.AntiRepeatCorpus.top_recent_topics``。注入位置同
# ``USER_DIRECTIVES_PROMPT_BLOCK`` —— ``_build_initial_prompt`` 末尾、ban list
# 之后。proactive 与 regular reply 共用：proactive 还会被 BM25 总分阈值
# 拦截（regen / drop），regular 只靠这段 prompt 软约束。
#
# 这段的语气和 ban list 不一样：ban list 是"用户明确说过别提"，必须强约束；
# 这里只是"你最近聊过这些，换些角度更好"，建议性的，不要太重，否则把 LLM
# 引导成话题切换疯子。

RECENT_TOPIC_HINT_PROMPT_BLOCK = {
    'zh': (
        "\n\n[最近几轮你已经聊过的话题（{n}项）]\n"
        "{items}\n"
        "如果还没必要，尽量换个角度或换个话题，避免连续围绕同一主题打转。"
    ),
    'en': (
        "\n\n[Topics you've already touched on in the last few turns ({n})]\n"
        "{items}\n"
        "Unless still relevant, try a fresh angle or a new topic rather than "
        "circling back to the same one."
    ),
    'ja': (
        "\n\n[最近のターンで既に触れた話題（{n}件）]\n"
        "{items}\n"
        "まだ必要でなければ、同じ話題を繰り返さず、別の切り口や新しい話題に"
        "切り替えてみてください。"
    ),
    'ko': (
        "\n\n[최근 몇 턴 동안 이미 다룬 화제 ({n}개)]\n"
        "{items}\n"
        "꼭 필요하지 않다면 같은 주제를 맴돌지 말고 다른 각도나 새로운 화제로"
        "전환해 보세요."
    ),
    'ru': (
        "\n\n[Темы, которые вы уже затронули за последние ходы ({n} шт.)]\n"
        "{items}\n"
        "Если в этом нет необходимости, попробуйте новый ракурс или другую "
        "тему, не кружите вокруг одной и той же."
    ),
    'es': (
        "\n\n[Temas que ya tocaste en los últimos turnos ({n} elemento(s))]\n"
        "{items}\n"
        "Salvo que sea necesario, prueba un ángulo distinto o un tema nuevo "
        "en lugar de volver al mismo."
    ),
    'pt': (
        "\n\n[Tópicos que você já abordou nos últimos turnos ({n} item(ns))]\n"
        "{items}\n"
        "A menos que ainda seja relevante, tente um ângulo novo ou outro "
        "tópico em vez de voltar ao mesmo."
    ),
}


def render_recent_topics_block(terms: List[str], lang: str) -> str:
    """把"最近 topic 词"列表渲染成 system-prompt 片段；空列表 → ""。"""
    if not terms:
        return ""
    short = _norm_lang(lang)
    template = RECENT_TOPIC_HINT_PROMPT_BLOCK.get(short) or RECENT_TOPIC_HINT_PROMPT_BLOCK['en']
    items = "\n".join(f"- {t}" for t in terms)
    return template.format(items=items, n=len(terms))


# ---------------------------------------------------------------------------
# Proactive regen 指令 — 给重 sample 用
# ---------------------------------------------------------------------------
# 当 BM25 总分超 REGEN_THRESHOLD 时，``main_routers/system_router`` 在第二次
# Phase 2 LLM 调用前注入这段，告诉 LLM 哪些 term 必须避开。
#
# ⚠️ 措辞刻意做成"结构化指令 + 显式反复述约束"：早期版本是一句散文式祈使
# （"换一个完全不同的角度或主题"），弱模型在超长上下文末尾收到后，容易把指令
# 原文/规划脚手架当成正文吐出来（线上见过 "完全不同的角度或主题"、"括号、Emoji"
# 这类泄漏）。现在每条都：(1) 用方括号小标题标明这是改写要求而非对话；(2) 末尾
# 明确"不要复述/解释本要求、不要输出标签化回复以外的任何东西"。注入侧还会把
# BEGIN 触发句放在最后（见 system_router），使指令本身不是模型看到的最后一句。
# 占位符：{terms} 要避开的词；{master_name} 搭话对象。

PROACTIVE_REGEN_AVOID_INSTRUCTION = {
    'zh': (
        "【改写要求】这些词和话题最近已经聊得太多，本次必须避开：{terms}。"
        "换个角度或换个话题，直接写一句全新的搭话。"
        "输出严格遵守上面的格式：第一行写来源标签，第二行起只写要对{master_name}说的原话；"
        "如果想不出新角度，就只输出 [PASS]。"
        "不要复述或解释本要求，不要输出任何思考过程、清单或标签化回复以外的内容。"
    ),
    'en': (
        "[Rewrite] These words and topics have been used too much recently and MUST be "
        "avoided: {terms}. Pick a different angle or topic and write one brand-new line. "
        "Keep strictly to the format above: the first line is the source tag, then write "
        "only the actual words you'd say to {master_name}; if you have no fresh angle, output "
        "only [PASS]. Do NOT restate or explain this instruction, and do NOT output any "
        "reasoning, lists, or anything other than the tagged reply."
    ),
    'ja': (
        "【書き直し】次の語と話題は最近使いすぎているので必ず避けてください：{terms}。"
        "切り口か話題を変えて、新しい一言を書いてください。"
        "出力は上の形式を厳守：1行目に来源タグ、その後は{master_name}に実際に言う言葉だけ。"
        "新しい切り口が思いつかなければ [PASS] だけを出力。"
        "この指示を復唱・説明せず、思考過程やリスト、タグ付き発言以外のものを出力しないこと。"
    ),
    'ko': (
        "【다시 쓰기】다음 단어와 화제는 최근에 너무 많이 다뤘으니 반드시 피하세요: {terms}. "
        "관점이나 화제를 바꿔 완전히 새로운 한마디를 쓰세요. "
        "출력은 위 형식을 엄격히 따르세요: 첫 줄은 출처 태그, 이후에는 {master_name}에게 실제로 "
        "할 말만 쓰세요; 새 관점이 없으면 [PASS]만 출력하세요. 이 지시를 되풀이하거나 설명하지 "
        "말고, 사고 과정·목록·태그 외의 어떤 것도 출력하지 마세요."
    ),
    'ru': (
        "[Перепиши] Эти слова и темы в последнее время используются слишком часто, их "
        "обязательно нужно избегать: {terms}. Выбери другой угол или тему и напиши одну "
        "совершенно новую реплику. Строго соблюдай формат выше: первая строка — тег источника, "
        "далее — только сами слова, которые ты скажешь {master_name}; если нового угла нет, "
        "выведи только [PASS]. Не пересказывай и не объясняй эту инструкцию, не выводи "
        "рассуждения, списки или что-либо кроме реплики с тегом."
    ),
    'es': (
        "[Reescribe] Estas palabras y temas se han usado demasiado últimamente y DEBES "
        "evitarlos: {terms}. Elige otro ángulo o tema y escribe una frase totalmente nueva. "
        "Respeta estrictamente el formato de arriba: la primera línea es la etiqueta de "
        "fuente, luego escribe solo lo que le dirías a {master_name}; si no tienes un ángulo "
        "nuevo, responde solo [PASS]. No repitas ni expliques esta instrucción, y no muestres "
        "razonamientos, listas ni nada que no sea la respuesta con etiqueta."
    ),
    'pt': (
        "[Reescreva] Estas palavras e temas foram usados demais recentemente e você DEVE "
        "evitá-los: {terms}. Escolha outro ângulo ou tema e escreva uma fala totalmente nova. "
        "Siga estritamente o formato acima: a primeira linha é a etiqueta de fonte, depois "
        "escreva apenas o que você diria a {master_name}; se não tiver um ângulo novo, "
        "responda apenas [PASS]. Não repita nem explique esta instrução, e não exiba "
        "raciocínio, listas ou qualquer coisa além da resposta com etiqueta."
    ),
}


# render_regen_avoid_instruction 缺省称呼（master_name 未传时的中性占位）。
# 不用"主人/master"等物化称呼（见项目约定）。
_DEFAULT_ADDRESSEE = {
    "zh": "对方",
    "en": "them",
    "ja": "相手",
    "ko": "상대",
    "ru": "собеседника",
    "es": "la otra persona",
    "pt": "a outra pessoa",
}


def render_regen_avoid_instruction(terms: List[str], lang: str, master_name: str = "") -> str:
    """把 regen 用的"避开 X / Y"指令渲染成文本。空列表 → ""。

    ``master_name`` 用于把"对谁说"写进指令；缺省退化为中性占位符，避免 KeyError。
    """
    if not terms:
        return ""
    short = _norm_lang(lang)
    template = PROACTIVE_REGEN_AVOID_INSTRUCTION.get(short) or PROACTIVE_REGEN_AVOID_INSTRUCTION['en']
    # 每个词单独括起来，让模型清楚哪些是要避开的离散词（CJK 用「」，其余用双引号），
    # 再用各 locale 的列表分隔符拼接。
    lq, rq = ("「", "」") if short in ("zh", "ja") else ('"', '"')
    sep = "、" if short in ("zh", "ja") else ", "
    quoted_terms = sep.join(f"{lq}{t}{rq}" for t in terms)
    return template.format(
        terms=quoted_terms,
        master_name=master_name or _DEFAULT_ADDRESSEE.get(short, "them"),
    )


# ---------------------------------------------------------------------------
# Proactive 格式纠正指令 — 初稿没按格式输出时自救用
# ---------------------------------------------------------------------------
# 初稿没解析到合法来源标签时（弱化模型常把人设 Format/约束块当正文吐出来，
# 如 "No Markdown: Yes."），system_router 注入这段再生成一次，把模型拽回
# "第一行写来源标签、其后正文" 的格式；与 BEGIN 触发句一起放进 Human turn
# （末尾仍是中性触发句）。占位符：{master_name} 搭话对象。

PROACTIVE_FORMAT_FIX_INSTRUCTION = {
    'zh': (
        "【格式纠正】上一次的输出没有按规定格式，把格式要求当成正文吐了出来。"
        "请重写：第一行只写一个来源标签（按上面输出格式段列出的来源标签选，"
        "如 [CHAT]、[WEB]、[MUSIC]、[MEME]），第二行起只写要对{master_name}说的话本身；"
        "没什么新鲜的可说就只输出 [PASS]。"
        "不要复述或解释任何规则，不要输出清单或思考过程，标签和正文以外的内容一律不要输出。"
    ),
    'en': (
        "[Format fix] Your last output didn't follow the required format — it spat out the "
        "rules as if they were the message. Rewrite it: the first line is a single source tag "
        "(choose from the source tags listed in the output-format section above, e.g. [CHAT], "
        "[WEB], [MUSIC], [MEME]), then from the next line write only the actual words you'd say "
        "to {master_name}; if you have nothing fresh to say, output only [PASS]. Do NOT restate "
        "or explain any rule, do NOT output lists or reasoning, and output nothing other than "
        "the tag and the message."
    ),
    'ja': (
        "【書式修正】前回の出力は指定の書式に従わず、ルールをそのまま本文として出してしまいました。"
        "書き直してください：1行目に来源タグを1つだけ（上の出力形式に挙げられたタグから選ぶ。"
        "例：[CHAT]・[WEB]・[MUSIC]・[MEME]）、2行目以降は{master_name}に実際に言う言葉だけ。"
        "新しく言うことがなければ [PASS] だけを出力。"
        "ルールを復唱・説明せず、リストや思考過程を出さず、タグと本文以外は何も出力しないこと。"
    ),
    'ko': (
        "【형식 교정】지난 출력이 규정된 형식을 따르지 않고 규칙을 본문처럼 뱉어냈습니다. "
        "다시 쓰세요: 첫 줄에는 출처 태그 하나만(위 출력 형식에 나열된 태그 중 선택, 예: [CHAT]·"
        "[WEB]·[MUSIC]·[MEME]), 이후 줄부터는 {master_name}에게 실제로 할 말만. 새로 할 말이 "
        "없으면 [PASS]만 출력. 규칙을 되풀이하거나 설명하지 말고, 목록·사고 과정을 출력하지 "
        "말며, 태그와 본문 외에는 아무것도 출력하지 마세요."
    ),
    'ru': (
        "[Исправь формат] Прошлый вывод не соответствовал формату — ты выдал правила, как "
        "будто это сообщение. Перепиши: первая строка — один тег источника (выбери из тегов, "
        "перечисленных в разделе формата вывода выше, напр. [CHAT], [WEB], [MUSIC], [MEME]), "
        "далее со следующей строки — только сами слова, которые ты скажешь {master_name}; если "
        "нового сказать нечего, выведи только [PASS]. Не пересказывай и не объясняй правила, не "
        "выводи списки или рассуждения и не выводи ничего, кроме тега и сообщения."
    ),
    'es': (
        "[Corrige el formato] Tu última salida no siguió el formato requerido: soltó las reglas "
        "como si fueran el mensaje. Reescríbela: la primera línea es una sola etiqueta de fuente "
        "(elige entre las etiquetas listadas en la sección de formato de salida de arriba, p. ej. "
        "[CHAT], [WEB], [MUSIC], [MEME]), luego desde la línea siguiente escribe solo lo que le "
        "dirías a {master_name}; si no tienes nada nuevo que decir, responde solo [PASS]. No "
        "repitas ni expliques ninguna regla, no muestres listas ni razonamientos, y no muestres "
        "nada más que la etiqueta y el mensaje."
    ),
    'pt': (
        "[Corrija o formato] Sua última saída não seguiu o formato exigido — cuspiu as regras "
        "como se fossem a mensagem. Reescreva: a primeira linha é uma única etiqueta de fonte "
        "(escolha entre as etiquetas listadas na seção de formato de saída acima, p. ex. [CHAT], "
        "[WEB], [MUSIC], [MEME]), depois, a partir da linha seguinte, escreva apenas o que você "
        "diria a {master_name}; se não tiver nada novo a dizer, responda apenas [PASS]. Não "
        "repita nem explique nenhuma regra, não exiba listas ou raciocínio, e não exiba nada "
        "além da etiqueta e da mensagem."
    ),
}


def render_format_fix_instruction(lang: str, master_name: str = "") -> str:
    """渲染"格式纠正"自救指令。``master_name`` 缺省退化为中性占位符。"""
    short = _norm_lang(lang)
    template = PROACTIVE_FORMAT_FIX_INSTRUCTION.get(short) or PROACTIVE_FORMAT_FIX_INSTRUCTION['en']
    return template.format(master_name=master_name or _DEFAULT_ADDRESSEE.get(short, "them"))


# =====================================================================
# ======= Negative-keyword target check (RFC §3.4.5 Layer 2) ==========
# =====================================================================
# 职责：用户说"别提了 / 换个话题"这类话命中本地关键词后，派一次小 LLM 调
# 用决定"用户到底是在说哪条？还是只是泛化情绪？"。水印："======以上为".
#
# 历史位置：从 ``prompts_memory.py`` 迁过来——negative-intent prompt + 关键词
# 与本模块的 ban-topic regex/抽取 是同一类输入（"用户的负面 / 回避指令"），
# 集中在一处便于以后维护词表 / prompt 一致性。
# evidence 系统的接入点保持原样（``app/memory_server._amaybe_trigger_negative_keyword_hook``）。

NEGATIVE_TARGET_CHECK_PROMPT = {
    "zh": """你是一个用户回避意图判定专家。

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

用户消息里，"别提了 / 不想聊 / 换个话题 / 别再说"这类表达到底指上述哪一条？可能多条、也可能一条都没有（用户只是泛化情绪）。

只能从"观察列表"里选 target_id，不要凭空生成。
target_type 必须是字符串 "reflection" 或 "persona" 之一。

返回合法 JSON（如果用户只是泛化情绪，无明确 target，返回 {"targets": []}）：
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "简短理由"}]}""",
    "en": """You are a user pushback target analyst.

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

In the user's messages, when they say things like "don't mention / change the topic / stop talking about", which observation(s) above are they referring to? Could be several, or none at all (just a vague mood).

target_id MUST come from "observations" above — do not invent IDs.
target_type MUST be the literal string "reflection" or "persona".

Return valid JSON. If the user is just venting without a specific target, return an object with an empty `targets` array: {"targets": []}. Otherwise:
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "short rationale"}]}""",
    "ja": """あなたはユーザーの拒否反応が何を指しているかを判定する専門家です。

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

ユーザーが「その話はいい／話題を変えて／やめて」などと言ったのは、上の観察のうちどれを指していますか？複数の場合もあれば、一つも該当しない場合もあります（単なるムード）。

target_id は必ず上の "観察" から選ぶこと。
target_type は文字列 "reflection" または "persona" のいずれかでなければならない。

有効な JSON で返す。該当なしの場合は targets を空配列に: {"targets": []}。
それ以外:
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "短い理由"}]}""",
    "ko": """당신은 사용자의 거부 표현이 무엇을 가리키는지 판정하는 전문가입니다.

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

사용자가 "그 얘기는 그만 / 다른 이야기하자" 같은 표현을 쓸 때, 위 관찰 중 어떤 것을 가리킵니까? 여러 개일 수도, 전혀 없을 수도 있습니다.

target_id는 반드시 위 "관찰"에서 가져오세요.
target_type은 문자열 "reflection" 또는 "persona" 중 하나여야 합니다.

유효한 JSON으로 반환하세요. 해당 없음이면 targets를 빈 배열로: {"targets": []}.
그 외:
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "짧은 이유"}]}""",
    "ru": """Вы эксперт по определению цели пользовательского отказа от темы.

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

Когда пользователь говорит "хватит об этом / сменим тему / не надо об этом", к каким из перечисленных наблюдений это относится? Может быть несколько или ни одного (просто эмоция).

target_id ДОЛЖЕН быть из "наблюдений" выше.
target_type ДОЛЖЕН быть строкой "reflection" или "persona".

Верните валидный JSON. Если конкретной цели нет — объект с пустым массивом `targets`: {"targets": []}. В противном случае:
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "короткое обоснование"}]}""",
    "es": """Eres especialista en determinar el objetivo de una reacción de rechazo del usuario.

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

Cuando el usuario dice cosas como "no lo menciones / cambia de tema / deja de hablar de eso", ¿a cuál(es) de las observaciones de arriba se refiere? Puede ser varias o ninguna (solo un estado de ánimo general).

target_id DEBE venir de la "lista de observaciones" de arriba; no inventes IDs.
target_type DEBE ser literalmente "reflection" o "persona".

Devuelve JSON válido. Si no hay objetivo específico, devuelve {"targets": []}. Si lo hay:
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "razón breve"}]}""",
    "pt": """Você é especialista em determinar o alvo de uma reação de recusa do usuário.

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

Quando o usuário diz coisas como "não mencione / muda de assunto / pare de falar disso", a qual(is) observação(ões) acima ele se refere? Pode ser várias ou nenhuma (apenas um humor geral).

target_id DEVE vir da "lista de observações" acima; não invente IDs.
target_type DEVE ser literalmente "reflection" ou "persona".

Retorne JSON válido. Se não houver alvo específico, retorne {"targets": []}. Caso contrário:
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "motivo breve"}]}""",
}


def get_negative_target_check_prompt(lang: str = "zh") -> str:
    return _loc(NEGATIVE_TARGET_CHECK_PROMPT, lang)


# =====================================================================
# ======= Negative-keyword scanning (RFC §3.4.5 Layer 1) ==============
# =====================================================================
# 本地确定性 frozenset 扫描；命中后异步派发 Layer 2 LLM 判定。
# 目标语义：用户希望 AI 闭嘴 / 回避特定话题（包含"嫌烦"族，因为这类词用在
# 话题语境时基本都意味着"想结束这个话题"）。**不收纯情绪词**（焦虑/崩溃/
# 难受/失望/痛苦…）——它们经常单独出现而无回避意图，会触发无用 LLM 调用。
# 单字也避免（"烦"会被"麻烦你"/"麻烦了"误命中），双字以上更稳。
NEGATIVE_KEYWORDS_I18N: dict[str, frozenset[str]] = {
    "zh": frozenset(
        [
            # 显式回避型
            "别说了",
            "别再说",
            "不要再说",
            "不要说",
            "别提了",
            "别提",
            "别再提",
            "不要再提",
            "不想提",
            "不想再提",
            "不想说",
            "不想说了",
            "不想再说",
            "别讲",
            "别再讲",
            "不要讲",
            "不要再讲",
            "别聊",
            "别聊这个",
            "不要聊",
            "不想聊",
            "换个话题",
            "换话题",
            "聊点别的",
            "说点别的",
            "这个不用说了",
            "闭嘴",
            "别问了",
            "不要问了",
            # 嫌烦型（暗含"想结束此话题"）
            "烦死",
            "烦人",
            "好烦",
            "真烦",
            "烦透",
            "心烦",
            "讨厌",
            "真讨厌",
            "受不了",
            "无语",
            "真无语",
        ]
    ),
    "en": frozenset(
        [
            # Explicit avoidance
            "stop talking about",
            "don't mention",
            "do not mention",
            "change the topic",
            "change the subject",
            "let's not discuss",
            "let's not talk about",
            "drop the subject",
            "drop it",
            "not this again",
            "shut up",
            "let it go",
            "move on",
            "enough of this",
            # Annoyance (implies "end this topic")
            # `hate` must stay multi-word — bare "hate" is a substring of common
            # words like "whatever" and would fire false positives every turn.
            "i hate",
            "hate this",
            "hate that",
            "hate it",
            "hate when",
            "annoying",
            "annoyed",
            "frustrating",
            "frustrated",
            "sick of",
        ]
    ),
    "ja": frozenset(
        [
            # 明示的な回避
            "その話は",
            "その話はもう",
            "その話やめ",
            "やめて",
            "話題を変えて",
            "別の話",
            "他の話",
            "言わないで",
            "黙って",
            # うんざり系（話題を終わらせたい含意）
            "もう嫌",
            "イライラ",
            "うざい",
            "しつこい",
        ]
    ),
    "ko": frozenset(
        [
            # 명시적 회피
            "그만하자",
            "그 얘기는 그만",
            "다른 이야기",
            "다른 얘기",
            "다른 얘기 하자",
            "말하지 마",
            "닥쳐",
            # 짜증 계열 (화제 종료 함의)
            "짜증",
            "싫어",
            "지긋지긋",
        ]
    ),
    "ru": frozenset(
        [
            # Явное избегание
            "хватит об этом",
            "сменим тему",
            "не говори об этом",
            "другая тема",
            "не надо об этом",
            "замолчи",
            "отстань",
            "хватит",
            # Раздражение (подразумевает «закроем тему»)
            "раздражает",
            "надоело",
            "достало",
        ]
    ),
    "es": frozenset(
        [
            "no hables",
            "no quiero hablar",
            "no quiero hablar de eso",
            "cambia de tema",
            "hablemos de otra cosa",
            "déjalo",
            "basta",
            "no lo menciones",
            "no sigas",
        ]
    ),
    "pt": frozenset(
        [
            "não fale",
            "não quero falar",
            "não quero falar disso",
            "mude de assunto",
            "vamos falar de outra coisa",
            "deixa pra lá",
            "chega",
            "não mencione isso",
            "não continue",
        ]
    ),
}


def scan_negative_keywords(message: str, lang: str = "zh") -> bool:
    """Fast path: case-insensitive substring scan against NEGATIVE_KEYWORDS_I18N.

    Returns True if the message contains any negation keyword for the given
    language; if lang is unknown, falls back to zh.

    ⚠️ 不走 ``_norm_lang``——那个 helper 服务于 i18n template rendering，未知
    语言归 ``en``（英文是 lingua franca，模板渲染默认英文合理）。本函数的契约
    是"语言识别不出就当中文用户"（codex P2 / scan-only policy），与 render
    路径策略不同。所以这里只做最小归一化：strip region 后缀（``en-US`` →
    ``en`` / ``zh-CN`` → ``zh``），未识别的短码留给 ``.get(..., zh)`` 兜底。
    """
    if not message:
        return False
    # 只剥 region 后缀（zh-CN/zh_CN/en-US/pt-BR ...），保留契约："未知 → zh"。
    # 同时 strip 前后空白 + lower 大小写——上游若传 ``EN-US`` 或 ``" en-US "``，
    # split 后是 ``EN`` / `` en``，dict key 都是小写无空白会 miss → 错落 zh
    # 兜底（CodeRabbit Minor）。
    short = (lang or "").strip().lower().split('-', 1)[0].split('_', 1)[0]
    # `zh` is always non-empty in the dict, so the fallback is guaranteed
    # to yield a frozenset (CodeRabbit PR #929 dead-code cleanup).
    kws = NEGATIVE_KEYWORDS_I18N.get(short, NEGATIVE_KEYWORDS_I18N["zh"])
    lower = message.lower()
    for kw in kws:
        if kw.lower() in lower:
            return True
    return False
