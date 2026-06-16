"""Slow global signal collection for topic hooks.

The signal layer deliberately does not decide what the user cares about.
It only keeps compact evidence across a longer window so the LLM can judge
stable, high-readiness topic opportunities instead of overfitting the last
few chat turns.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from main_logic.topic.common import ZH_TOPIC_STOP_CHARS, clean_text, topic_units
from utils.tokenize import truncate_to_tokens


_MAX_SIGNAL_TEXT_CHARS = 500
# Per-turn evidence cap in tokens, unified with the recent-conversation
# per-turn budget (llm_enrichment._MAX_CONV_TOKENS_PER_TURN) so both inputs
# to the emotion-tier topic call share one budget unit.
_MAX_SIGNAL_TOKENS_PER_TURN = 300
_MAX_GLOBAL_TURNS = 80
_READY_SCORE = 80
_FILLER_TEXTS = {
    "你好",
    "啊",
    "嗯",
    "哦",
    "好",
    "可以",
    "对",
    "對",
    "行",
    "行吧",
    "哈哈",
    "没事",
    "沒事",
    "不知道",
}


_GLOBAL_SIGNAL_LABELS = {
    "zh": {
        "progress": "收集进度",
        "user_count": "用户证据数",
        "meaningful_user_count": "有效用户证据数",
        "ai_count": "AI回应数",
        "density": "信息密度",
        "stability": "稳定度",
        "note": "说明: 这是跨最近窗口的慢收集证据。收集进度只是本地信息量估算；请由模型最终判断哪些点稳定、强相关、适合低频开口；不要按关键词硬凑。",
        "evidence": "全局证据",
        "user": "用户",
        "ai": "AI",
        "seconds_ago": "{value}s前",
        "minutes_ago": "{value}min前",
        "hours_ago": "{value}h前",
    },
    "zh-TW": {
        "progress": "收集進度",
        "user_count": "使用者證據數",
        "meaningful_user_count": "有效使用者證據數",
        "ai_count": "AI 回應數",
        "density": "資訊密度",
        "stability": "穩定度",
        "note": "說明: 這是跨最近視窗的慢收集證據。收集進度只是本地資訊量估算；請由模型最終判斷哪些點穩定、強相關、適合低頻開口；不要按關鍵字硬湊。",
        "evidence": "全域證據",
        "user": "使用者",
        "ai": "AI",
        "seconds_ago": "{value}s前",
        "minutes_ago": "{value}min前",
        "hours_ago": "{value}h前",
    },
    "en": {
        "progress": "Collection progress",
        "user_count": "User evidence count",
        "meaningful_user_count": "Useful user evidence count",
        "ai_count": "AI response count",
        "density": "Information density",
        "stability": "Stability",
        "note": "Note: This is slow evidence collected across the recent window. The progress score is only a local information estimate; the model must decide which points are stable, relevant, and suitable for a low-frequency opening. Do not force topics by keyword.",
        "evidence": "Global evidence",
        "user": "User",
        "ai": "AI",
        "seconds_ago": "{value}s ago",
        "minutes_ago": "{value}min ago",
        "hours_ago": "{value}h ago",
    },
    "ja": {
        "progress": "収集進捗",
        "user_count": "ユーザー証拠数",
        "meaningful_user_count": "有効なユーザー証拠数",
        "ai_count": "AI応答数",
        "density": "情報密度",
        "stability": "安定度",
        "note": "説明: これは直近ウィンドウ全体から集めた遅い証拠です。収集進捗はローカルな情報量の推定にすぎません。安定していて関連が強く、低頻度で自然に切り出せる点をモデルが最終判断してください。キーワードだけで無理に作らないでください。",
        "evidence": "全体証拠",
        "user": "ユーザー",
        "ai": "AI",
        "seconds_ago": "{value}秒前",
        "minutes_ago": "{value}分前",
        "hours_ago": "{value}時間前",
    },
    "ko": {
        "progress": "수집 진행도",
        "user_count": "사용자 증거 수",
        "meaningful_user_count": "유효한 사용자 증거 수",
        "ai_count": "AI 응답 수",
        "density": "정보 밀도",
        "stability": "안정도",
        "note": "설명: 최근 구간 전체에서 천천히 모은 증거입니다. 진행도는 로컬 정보량 추정일 뿐입니다. 어떤 지점이 안정적이고 관련성이 높으며 낮은 빈도로 자연스럽게 꺼내기 좋은지는 모델이 최종 판단하세요. 키워드만 보고 억지로 만들지 마세요.",
        "evidence": "전역 증거",
        "user": "사용자",
        "ai": "AI",
        "seconds_ago": "{value}초 전",
        "minutes_ago": "{value}분 전",
        "hours_ago": "{value}시간 전",
    },
    "es": {
        "progress": "Progreso de recopilación",
        "user_count": "Evidencias del usuario",
        "meaningful_user_count": "Evidencias útiles del usuario",
        "ai_count": "Respuestas de IA",
        "density": "Densidad de información",
        "stability": "Estabilidad",
        "note": "Nota: Esta es evidencia lenta recopilada en la ventana reciente. El progreso es solo una estimación local de información; el modelo debe decidir qué puntos son estables, relevantes y adecuados para abrir un tema de baja frecuencia. No fuerces temas por palabras clave.",
        "evidence": "Evidencia global",
        "user": "Usuario",
        "ai": "IA",
        "seconds_ago": "hace {value}s",
        "minutes_ago": "hace {value}min",
        "hours_ago": "hace {value}h",
    },
    "pt": {
        "progress": "Progresso da coleta",
        "user_count": "Evidências do usuário",
        "meaningful_user_count": "Evidências úteis do usuário",
        "ai_count": "Respostas da IA",
        "density": "Densidade de informação",
        "stability": "Estabilidade",
        "note": "Nota: esta é uma evidência lenta coletada na janela recente. O progresso é apenas uma estimativa local de informação; o modelo deve decidir quais pontos são estáveis, relevantes e adequados para uma abertura de baixa frequência. Não force temas por palavras-chave.",
        "evidence": "Evidência global",
        "user": "Usuário",
        "ai": "IA",
        "seconds_ago": "há {value}s",
        "minutes_ago": "há {value}min",
        "hours_ago": "há {value}h",
    },
    "ru": {
        "progress": "Прогресс сбора",
        "user_count": "Число пользовательских сигналов",
        "meaningful_user_count": "Число полезных пользовательских сигналов",
        "ai_count": "Ответы AI",
        "density": "Информационная плотность",
        "stability": "Стабильность",
        "note": "Примечание: это медленно собранные сигналы за недавнее окно. Прогресс является только локальной оценкой информативности; модель должна сама решить, какие точки стабильны, релевантны и подходят для редкого естественного начала. Не подбирай темы только по ключевым словам.",
        "evidence": "Глобальные сигналы",
        "user": "Пользователь",
        "ai": "AI",
        "seconds_ago": "{value}с назад",
        "minutes_ago": "{value}мин назад",
        "hours_ago": "{value}ч назад",
    },
}


def _clean_text(value: Any, *, limit: int = _MAX_SIGNAL_TEXT_CHARS) -> str:
    return clean_text(value, limit=limit)


def _label_key_for_lang(lang: str | None) -> str:
    raw = str(lang or "").strip().replace("_", "-")
    if not raw:
        return "zh"
    if raw in _GLOBAL_SIGNAL_LABELS:
        return raw
    lower = raw.lower()
    if lower.startswith(("zh-tw", "zh-hant", "zh-hk")):
        return "zh-TW"
    if lower.startswith("zh"):
        return "zh"
    short = lower.split("-", 1)[0]
    return short if short in _GLOBAL_SIGNAL_LABELS else "en"


def _format_age(age_s: float, labels: Mapping[str, str]) -> str:
    if age_s < 90:
        return labels["seconds_ago"].format(value=int(age_s))
    if age_s < 3600:
        return labels["minutes_ago"].format(value=int(age_s / 60))
    return labels["hours_ago"].format(value=int(age_s / 3600))


@dataclass(frozen=True)
class TopicTurnSignal:
    actor: str
    text: str
    timestamp: float
    lang: str


class TopicSignalStore:
    """In-memory slow evidence store, scoped per character."""

    def __init__(
        self,
        *,
        min_user_turns_for_topic: int = 4,
        max_turns: int = _MAX_GLOBAL_TURNS,
    ) -> None:
        self._min_user_turns_for_topic = max(1, int(min_user_turns_for_topic))
        self._turns: dict[str, deque[TopicTurnSignal]] = defaultdict(
            lambda: deque(maxlen=max(1, int(max_turns)))
        )

    def note_turn(
        self,
        lanlan_name: str,
        *,
        actor: str,
        text: Any,
        lang: str = "zh",
        now: float | None = None,
    ) -> None:
        cleaned = truncate_to_tokens(_clean_text(text), _MAX_SIGNAL_TOKENS_PER_TURN)
        if not cleaned:
            return
        name = str(lanlan_name or "default")
        safe_actor = "ai" if actor == "ai" else "user"
        self._turns[name].append(
            TopicTurnSignal(
                actor=safe_actor,
                text=cleaned,
                timestamp=float(now if now is not None else time.time()),
                lang=lang or "zh",
            )
        )

    def clear(self, lanlan_name: str) -> None:
        self._turns.pop(str(lanlan_name or "default"), None)

    def readiness_percent(self, lanlan_name: str) -> int:
        user_turns = self._user_turns(lanlan_name)
        if not user_turns:
            return 0
        meaningful = [turn for turn in user_turns if _turn_information_score(turn.text) >= 20]
        if not meaningful:
            return 0

        sample_score = min(
            40,
            int(len(meaningful) * 40 / self._min_user_turns_for_topic),
        )
        density_score = int(
            sum(_turn_information_score(turn.text) for turn in meaningful)
            / len(meaningful)
            * 0.5
        )
        stability_score = _stability_score(meaningful)
        return max(0, min(100, sample_score + density_score + stability_score))

    def is_ready(self, lanlan_name: str) -> bool:
        return self.readiness_percent(lanlan_name) >= _READY_SCORE

    def format_global_signals(self, lanlan_name: str, *, max_lines: int = 40, lang: str | None = None) -> str:
        """Render the slow-evidence turns as prompt context.

        Only the raw evidence list is emitted. The local heuristic stats
        (readiness / density / stability / counts) are deliberately NOT
        injected — they have no grounding or scale the LLM can use, and the
        model can read stability straight off the evidence. Those scores
        stay backend-only, feeding the ``is_ready`` gate (see
        ``readiness_percent``), not the prompt.
        """
        name = str(lanlan_name or "default")
        labels = _GLOBAL_SIGNAL_LABELS[_label_key_for_lang(lang)]
        turns = list(self._turns.get(name, ()))
        if not turns:
            return ""

        selected = _select_turns_for_prompt(turns, max_lines=max_lines)
        base_ts = turns[-1].timestamp
        lines = [f"{labels['evidence']}:"]
        for turn in selected:
            age_s = max(0.0, base_ts - turn.timestamp)
            age = _format_age(age_s, labels)
            label = labels["user"] if turn.actor == "user" else labels["ai"]
            lines.append(f"- [{age}] {label}: {turn.text}")
        return "\n".join(lines)

    def _user_turns(self, lanlan_name: str) -> list[TopicTurnSignal]:
        name = str(lanlan_name or "default")
        return [turn for turn in self._turns.get(name, ()) if turn.actor == "user"]


def _select_turns_for_prompt(
    turns: Iterable[TopicTurnSignal],
    *,
    max_lines: int,
) -> list[TopicTurnSignal]:
    try:
        max_lines = int(max_lines)
    except (TypeError, ValueError):
        max_lines = 0
    if max_lines <= 0:
        return []
    all_turns = list(turns)
    if len(all_turns) <= max_lines:
        return all_turns
    head_count = min(12, max_lines // 2)
    tail_count = max_lines - head_count
    return all_turns[:head_count] + all_turns[-tail_count:]


def _turn_information_score(text: str) -> int:
    cleaned = _clean_text(text, limit=120)
    if not cleaned:
        return 0
    normalized = cleaned.lower()
    if normalized in _FILLER_TEXTS:
        return 0
    cjk_count = sum(1 for char in cleaned if "\u4e00" <= char <= "\u9fff")
    ascii_count = sum(1 for char in cleaned if char.isalnum() and not ("\u4e00" <= char <= "\u9fff"))
    signal_len = cjk_count + ascii_count
    if signal_len <= 2:
        return 0
    if signal_len <= 4:
        return 12

    score = min(60, int(signal_len * 2.5))
    unique_units = _topic_units(cleaned)
    score += min(20, int(len(unique_units) * 2.5))
    if any(mark in cleaned for mark in "，。！？,.!?"):
        score += 5
    if len(cleaned) >= 18:
        score += 8
    if len(cleaned) >= 30:
        score += 7
    return max(0, min(100, score))


def _topic_units(text: str) -> set[str]:
    return topic_units(text, limit=120, stop_chars=ZH_TOPIC_STOP_CHARS)


def _stability_score(turns: Iterable[TopicTurnSignal]) -> int:
    unit_counts: dict[str, int] = defaultdict(int)
    valid_turns = 0
    for turn in turns:
        units = _topic_units(turn.text)
        if not units:
            continue
        valid_turns += 1
        for unit in units:
            unit_counts[unit] += 1
    if valid_turns < 2:
        return 0
    repeated_units = [
        unit for unit, count in unit_counts.items()
        if count >= 2 and (len(unit) >= 2 or "\u4e00" <= unit <= "\u9fff")
    ]
    return min(30, len(repeated_units) * 5)
