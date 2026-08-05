"""Localized prompts for the built-in public-knowledge tool."""

from __future__ import annotations

from config.prompts._locale import normalize_prompt_locale
from config.prompts.prompts_sys import _loc


PUBLIC_KNOWLEDGE_TOOL_DESCRIPTION = {
    "zh": "查询本地公共知识，或从允许的素材分类中抽取条目。用户明确要求随机选择素材时，必须先以 mode=sample 调用；不会联网或读取用户记忆。",
    "zh-TW": "查詢本機公共知識，或從允許的素材分類中抽取條目。使用者明確要求隨機選擇素材時，必須先以 mode=sample 呼叫；不會連網或讀取使用者記憶。",
    "en": "Query local public knowledge or draw entries from an allowed material category. When the user explicitly requests a random choice, call this tool with mode=sample first. It never accesses the network or user memory.",
    "ja": "ローカル公開知識を検索します。素材のランダム選択を求められた場合は、先に mode=sample で呼び出してください。ネットワークやユーザー記憶にはアクセスしません。",
    "ko": "로컬 공개 지식을 검색합니다. 무작위 소재 선택을 요청받으면 먼저 mode=sample로 호출하세요. 네트워크나 사용자 기억에는 접근하지 않습니다.",
    "ru": "Ищет в локальной базе публичных знаний. При запросе случайного материала сначала вызовите инструмент с mode=sample. Сеть и память пользователя не используются.",
    "es": "Consulta conocimiento público local. Si se pide material aleatorio, llama primero a la herramienta con mode=sample. No accede a la red ni a la memoria del usuario.",
    "pt": "Consulta conhecimento público local. Se for solicitado material aleatório, chame primeiro a ferramenta com mode=sample. Não acessa a rede nem a memória do usuário.",
}

PUBLIC_KNOWLEDGE_QUERY_DESCRIPTION = {
    "zh": "查询时填写词条或问题；抽取时填写允许的标签，例如 dataset:tarot-interpretations。",
    "zh-TW": "查詢時填寫詞條或問題；抽取時填寫允許的標籤，例如 dataset:tarot-interpretations。",
    "en": "For lookup, provide a term or question. For sampling, provide an allowed tag such as dataset:tarot-interpretations.",
    "ja": "検索する語句、または抽出用の許可タグ（例: dataset:tarot-interpretations）。",
    "ko": "검색할 문구 또는 추출용 허용 태그(예: dataset:tarot-interpretations).",
    "ru": "Термин для поиска или разрешённый тег для выбора материала.",
    "es": "El término que se consultará o una etiqueta permitida para extraer material.",
    "pt": "O termo a consultar ou uma etiqueta permitida para selecionar material.",
}


def knowledge_prompt_language(language: str | None) -> str:
    """Normalize runtime language codes for the eight prompt locales."""
    return normalize_prompt_locale(
        language,
        default="en",
        simplified="zh",
        keep_traditional=True,
    )


def localized_knowledge_prompt(templates: dict[str, str], language: str | None) -> str:
    return _loc(templates, knowledge_prompt_language(language))


__all__ = [
    "PUBLIC_KNOWLEDGE_QUERY_DESCRIPTION",
    "PUBLIC_KNOWLEDGE_TOOL_DESCRIPTION",
    "knowledge_prompt_language",
    "localized_knowledge_prompt",
]
