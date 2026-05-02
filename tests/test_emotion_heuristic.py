"""启发式情感分类的回归测试。

锁定 PR #1079 修复过的所有 case，包括：
- 否定回看（句首带空格 token、子句标点截断、转折连词截断、tight lookback、blocklist）
- 反向情绪嵌入（unhappy/unsurprised 等英文 \\b 边界 + 中文非否定词组）
- 跨语种关键词命中（zh/en/ja/ko/ru）
- Mixed-script ASCII keyword（好happy啊 / 超annoyed欸）
- Filler 防双重计分（haha haha haha 不应过度刷分触发 override）

后续若改动 config/prompts_emotion.py 里的 token 表 / blocklist / contrast
conjunctions，或 main_routers/system_router.py 里的 _has_heuristic_negation_before、
_count_keyword_hits、_is_ascii_word_keyword，应跑此测试确认无 silent regression。
"""
import pytest

from main_routers.system_router import _infer_emotion_from_text


# (text, expected_emotion) — None 表示启发式不应得出非 neutral 情绪
CASES = [
    # 句首带空格 token（` no `/` But `）
    ('no angry feelings', None),
    ('No furious, just calm', None),
    ('but annoyed today', 'angry'),

    # 子句标点截断
    ('我不是难过，我是生气', 'angry'),
    ('我不开心，但是好生气', 'angry'),
    ('not sad, just angry', 'angry'),
    ('not happy. angry actually', 'angry'),

    # 转折连词截断（zh 但/而是, en but/yet, ko -지만/-는데, ru а）
    ('I am not sad but angry', 'angry'),
    ('我不难过但我生气', 'angry'),
    ('不是难过而是生气', 'angry'),
    ('not happy yet angry', 'angry'),
    ('슬프지 않지만 행복해', 'happy'),
    ('화나진 않지만 기뻐', 'happy'),
    ('я не грущу, а злюсь', 'angry'),

    # 英文 \b 词边界（reject reverse-emotion embedding）
    ('unhappy', None),
    ('unhappy days', None),
    ('unsurprised by this', None),
    ('unhappy and unsurprised', None),

    # 英文真情绪 keyword 命中（fallback 路径不再 regress to neutral）
    ('I am happy', 'happy'),
    ('I feel sad', 'sad'),
    ('I am surprised', 'surprised'),
    ('happy days', 'happy'),
    ('that was sad', 'sad'),
    ('haha awesome lovely', 'happy'),
    ('feeling depressed and heartbroken', 'sad'),
    ('wow whoa omg', 'surprised'),

    # Mixed-script ASCII keyword（CJK 不算 \w 边界）
    ('好happy啊', 'happy'),
    ('超annoyed欸', 'angry'),
    ('真的好angry啊', 'angry'),
    ('我sad了', 'sad'),

    # zh 词中假阳（无/不思议/不错/不具合/不愧/不仅/不可思议）
    ('无语真生气', 'angry'),
    ('无聊死了真烦死', 'angry'),
    ('不思议地开心', 'happy'),
    ('这个功能不错真开心', 'happy'),
    ('不具合で悲しい', 'sad'),
    ('不可思议好开心', 'happy'),
    ('莫名开心', 'happy'),
    ('莫名生气', 'angry'),
    ('莫名其妙好难过', 'sad'),

    # zh 多字否定（不太/不是很/不那么/不怎么/没那么）
    ('不是很生气', None),
    ('不怎么开心', None),
    ('不那么烦死', None),
    ('没那么开心', None),
    ('不太开心', None),
    ('不算很生气', None),

    # zh 紧凑单字否定（紧邻情绪词才算）
    ('我不开心', None),
    ('我不生气了', None),
    ('一点也不烦死', None),
    ('我不喜欢', None),
    ('并不开心', None),

    # zh 非否定固定搭配 blocklist（不仅/不只/不但/不光）
    ('不仅开心还很爽', 'happy'),
    ('不只生气还失望', 'angry'),
    ('不但难过还委屈', 'sad'),

    # en 非否定固定搭配 blocklist（not only / no doubt / no wonder）
    ('not only happy', 'happy'),
    ('not only angry', 'angry'),
    ('I am not only happy but excited', 'happy'),

    # ko 紧凑单字否定（안/못 in 안좋아 / 못좋아）
    ('안좋아', None),
    ('안 좋아', None),
    ('못좋아', None),
    ('오늘 안좋아 진짜', None),
    ('안녕하세요 정말 좋아', 'happy'),  # `안녕` 不应触发否定
    ('안내해줘 좋아', 'happy'),

    # 真否定仍要识别（en/zh/ru）
    ('not angry at all', None),
    ('я не злюсь', None),

    # 高假阳清理（mad/hate/damn/Madison/класс/ого/最悪/최악 等已删/已防护）
    ('damn good food!', None),
    ('Madison is coming', None),
    ('classical music is nice', None),
    ('классический фильм', None),
    ('классика мирового кино', None),
    ('у меня много дел', None),
    ('какого черта', None),
    ('今日最悪じゃないよ', None),

    # 跨语种真情绪
    ('好开心啊', 'happy'),
    ('好难过', 'sad'),
    ('气死我了！', 'angry'),
    ('shut up and go away', 'angry'),
    ('うるさい、黙れ', 'angry'),
    ('닥쳐, 꺼져', 'angry'),
    ('заткнись, отвали', 'angry'),
    ('я счастлив', 'happy'),
    ('очень рада', 'happy'),
    ('ничего себе!', 'surprised'),

    # 跨语种混合命中（用户混着语言聊）
    ('气死了，furious！', 'angry'),
    ('うるさい，闭嘴！', 'angry'),
    ('닥쳐, fuck off', 'angry'),

    # Filler 不应过度刷分越过 strong override 阈值（>=4 & conf<0.8）
    # `哈哈` keyword 命中 N 次 + playful 命中 +1（不是 ×2，避免双重计分）
    ('哈哈哈哈哈', 'happy'),
    ('haha haha haha', 'happy'),
]


@pytest.mark.unit
@pytest.mark.parametrize('text,expected', CASES)
def test_infer_emotion_from_text(text, expected):
    emotion, _score = _infer_emotion_from_text(text)
    assert emotion == expected, (
        f"text={text!r} expected={expected!r} got={emotion!r}"
    )


@pytest.mark.unit
def test_filler_score_below_strong_override():
    """`haha haha haha` 等 filler 不应被 playful 双倍加权推到 override 阈值之上。

    强 override 条件是 `score >= 4 & confidence < 0.8`，filler 分数应保持
    可控（keyword 命中 + playful +1，不是 playful * 2 双重计分）。
    """
    _emotion, score = _infer_emotion_from_text('haha haha haha')
    # keyword 命中 3 次 + playful patterns 命中 +1 = 4 分（边界值，不应更高）
    assert score <= 4, f'filler score 过度放大: {score}'
