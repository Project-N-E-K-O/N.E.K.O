"""Regression tests for heuristic emotion classification.

Covers all cases fixed by PR #1079, including:
- Negation lookback (leading-space token, clause punctuation truncation, contrast conjunction truncation, tight lookback, blocklist)
- Reverse emotion embedding (English \\b boundaries in unhappy/unsurprised, plus Chinese phrases that are not negation)
- Cross-language keyword matches (zh/en/ja/ko/ru)
- Mixed-script ASCII keywords (好happy啊 / 超annoyed欸)
- Filler double-count prevention (haha haha haha must not over-score and trigger the override)

If token tables / blocklists / contrast
conjunctions in config/prompts/prompts_emotion.py or _has_heuristic_negation_before,
_count_keyword_hits, _is_ascii_word_keyword in main_routers/system_router.py change later, run this test to catch silent regressions.
"""  # noqa: DOCSTRING_CJK
import pytest

from main_routers.system_router import _infer_emotion_from_text


# (text, expected_emotion) — None means the heuristic should not infer a non-neutral emotion.
CASES = [
    # Token with a leading space (` no `/` But `).
    ('no angry feelings', None),
    ('No furious, just calm', None),
    ('but annoyed today', 'angry'),

    # Truncate at clause punctuation.
    ('我不是难过，我是生气', 'angry'),
    ('我不开心，但是好生气', 'angry'),
    ('not sad, just angry', 'angry'),
    ('not happy. angry actually', 'angry'),

    # Truncate at contrast conjunctions (zh 但/而是, en but/yet, ko -지만/-는데, ru а).
    ('I am not sad but angry', 'angry'),
    ('我不难过但我生气', 'angry'),
    ('不是难过而是生气', 'angry'),
    ('not happy yet angry', 'angry'),
    ('슬프지 않지만 행복해', 'happy'),
    ('화나진 않지만 기뻐', 'happy'),
    ('я не грущу, а злюсь', 'angry'),

    # English \b word boundary (reject reverse-emotion embedding).
    ('unhappy', None),
    ('unhappy days', None),
    ('unsurprised by this', None),
    ('unhappy and unsurprised', None),

    # Match a genuine English emotion keyword (the fallback path must not regress to neutral).
    ('I am happy', 'happy'),
    ('I feel sad', 'sad'),
    ('I am surprised', 'surprised'),
    ('happy days', 'happy'),
    ('that was sad', 'sad'),
    ('haha awesome lovely', 'happy'),
    ('feeling depressed and heartbroken', 'sad'),
    ('wow whoa omg', 'surprised'),

    # Mixed-script ASCII keyword (CJK characters do not count as a \w boundary).
    ('好happy啊', 'happy'),
    ('超annoyed欸', 'angry'),
    ('真的好angry啊', 'angry'),
    ('我sad了', 'sad'),

    # Chinese false positives inside words (无/不思议/不错/不具合/不愧/不仅/不可思议).
    ('无语真生气', 'angry'),
    ('无聊死了真烦死', 'angry'),
    ('不思议地开心', 'happy'),
    ('这个功能不错真开心', 'happy'),
    ('不具合で悲しい', 'sad'),
    ('不可思议好开心', 'happy'),
    ('莫名开心', 'happy'),
    ('莫名生气', 'angry'),
    ('莫名其妙好难过', 'sad'),

    # Chinese multi-character negation (不太/不是很/不那么/不怎么/没那么).
    ('不是很生气', None),
    ('不怎么开心', None),
    ('不那么烦死', None),
    ('没那么开心', None),
    ('不太开心', None),
    ('不算很生气', None),

    # Chinese compact single-character negation (counts only next to an emotion word).
    ('我不开心', None),
    ('我不生气了', None),
    ('一点也不烦死', None),
    ('我不喜欢', None),
    ('并不开心', None),

    # Chinese fixed phrases that are not negation (blocklist: 不仅/不只/不但/不光).
    ('不仅开心还很爽', 'happy'),
    ('不只生气还失望', 'angry'),
    ('不但难过还委屈', 'sad'),

    # English fixed phrases that are not negation (blocklist: not only / no doubt / no wonder).
    ('not only happy', 'happy'),
    ('not only angry', 'angry'),
    ('I am not only happy but excited', 'happy'),

    # Korean compact single-character negation (안/못 in 안좋아 / 못좋아).
    ('안좋아', None),
    ('안 좋아', None),
    ('못좋아', None),
    ('오늘 안좋아 진짜', None),
    ('안녕하세요 정말 좋아', 'happy'),  # `안녕` should not trigger negation.
    ('안내해줘 좋아', 'happy'),

    # Genuine negation must still be recognized (en/zh/ru).
    ('not angry at all', None),
    ('я не злюсь', None),

    # Clean up high false positives (mad/hate/damn/Madison/класс/ого/最悪/최악, etc. were removed or guarded).
    ('damn good food!', None),
    ('Madison is coming', None),
    ('classical music is nice', None),
    ('классический фильм', None),
    ('классика мирового кино', None),
    ('у меня много дел', None),
    ('какого черта', None),
    ('今日最悪じゃないよ', None),

    # Genuine cross-language emotion.
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

    # Mixed cross-language matches (the user chats in multiple languages).
    ('气死了，furious！', 'angry'),
    ('うるさい，闭嘴！', 'angry'),
    ('닥쳐, fuck off', 'angry'),

    # Filler must not over-score past the strong override threshold (>=4 & conf<0.8).
    # `哈哈` keyword matches N times + playful match +1 (not ×2, to avoid double counting).
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
    """Filler such as `haha haha haha` must not be double-weighted by playful scoring and pushed above the override threshold.

    The strong override condition is `score >= 4 & confidence < 0.8`; the filler score must remain
    controlled (keyword match + playful +1, not playful * 2 double-counting).
    """
    _emotion, score = _infer_emotion_from_text('haha haha haha')
    # Keyword matches 3 times + playful patterns match +1 = 4 points (boundary value; must not be higher).
    assert score <= 4, f'filler score 过度放大: {score}'
