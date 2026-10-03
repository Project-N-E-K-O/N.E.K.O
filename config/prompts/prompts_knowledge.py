# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Localized public-knowledge tool descriptions."""

PUBLIC_KNOWLEDGE_TOOL_DESCRIPTION = {
    "zh": (
        "查询本地公共知识库，或从允许的素材分类中抽取条目。可用于网络梗、ACG、"
        "神话、塔罗、电影、颜色、动物、食物、职业和情绪素材。当用户明确要求抽取、"
        "随机选择或提供这类素材时，必须先以 mode=sample 调用本工具，不要自行编造结果。"
        "返回内容可能是事实、梗、对话样例、参考回答或写作素材；应根据用户意图按需引用、"
        "改写或模仿，不要因其是样例就拒绝使用。若上下文已给出相关参考资料，直接使用即可，"
        "不必重复调用。本工具不会联网或读取用户记忆。"
    ),
    "en": (
        "Query local public knowledge or draw entries from an allowed material category. "
        "Covers memes, ACG, mythology, tarot, films, colors, animals, foods, occupations, "
        "and moods. When the user explicitly asks to draw, randomly choose, or provide such "
        "material, you must call this tool with mode=sample before answering instead of "
        "inventing a result. Results may be facts or corpus examples; quote, rewrite, or "
        "imitate examples when that matches the request. If relevant reference material is "
        "already present in the context, use it directly instead of querying again. It never "
        "accesses the network or user memory."
    ),
    "ja": "ローカル公開知識を検索します。素材の抽選やランダム選択を明示された場合は、回答前に必ず mode=sample で呼び出してください。文脈に関連する参考資料が既にある場合は、再検索せずそのまま使ってください。ネットワークやユーザー記憶にはアクセスしません。",
    "ko": "로컬 공개 지식을 검색합니다. 소재 추첨이나 무작위 선택을 명시적으로 요청받으면 답변 전에 반드시 mode=sample로 호출해야 합니다. 맥락에 관련 참고 자료가 이미 있으면 다시 조회하지 말고 그대로 사용하세요. 네트워크나 사용자 기억에는 접근하지 않습니다.",
    "es": "Consulta conocimiento público local. Si el usuario pide extraer o elegir material al azar, debes llamar primero a esta herramienta con mode=sample. Si el contexto ya incluye material de referencia relevante, úsalo directamente en lugar de consultar de nuevo. No accede a la red ni a la memoria del usuario.",
    "pt": "Consulta conhecimento público local. Se o usuário pedir material aleatório, chame primeiro esta ferramenta com mode=sample. Se o contexto já trouxer material de referência relevante, use-o diretamente em vez de consultar de novo. Não acessa a rede nem a memória do usuário.",
    "ru": "Ищет в локальной базе знаний. Если пользователь просит выбрать случайный материал, сначала обязательно вызовите инструмент с mode=sample. Если в контексте уже есть подходящий справочный материал, используйте его, а не запрашивайте повторно. Сеть и память пользователя не используются.",
    "zh-TW": "查詢本機公共知識。使用者明確要求抽取或隨機選擇素材時，必須先用 mode=sample 呼叫本工具，不可自行編造；若上下文已有相關參考資料，直接使用即可，不必重複呼叫；不會連網或讀取使用者記憶。",
}

PUBLIC_KNOWLEDGE_SAMPLE_TOOL_DESCRIPTION = {
    "zh": "从本地公共素材中抽取用户明确要求的随机项目，例如塔罗牌、动物、颜色或职业。不用于普通知识查询。",
    "en": "Draw a user-requested random item from local public material, such as a tarot card, animal, color, or occupation. Do not use it for ordinary knowledge lookup.",
    "ja": "タロット、動物、色、職業など、利用者が求めたランダム素材をローカル公開素材から抽出します。通常の知識検索には使用しません。",
    "ko": "타로, 동물, 색상, 직업처럼 사용자가 요청한 무작위 항목을 로컬 공개 자료에서 뽑습니다. 일반 지식 검색에는 사용하지 않습니다.",
    "es": "Extrae de material público local un elemento aleatorio solicitado, como una carta del tarot, un animal, un color o una profesión. No se usa para consultas normales.",
    "pt": "Seleciona do material público local um item aleatório solicitado, como uma carta de tarô, animal, cor ou profissão. Não serve para consultas comuns.",
    "ru": "Выбирает из локальных общедоступных материалов случайный запрошенный объект: карту Таро, животное, цвет или профессию. Не используется для обычного поиска знаний.",
    "zh-TW": "從本機公共素材中抽取使用者明確要求的隨機項目，例如塔羅牌、動物、顏色或職業。不用於一般知識查詢。",
}

PUBLIC_KNOWLEDGE_QUERY_DESCRIPTION = {
    "zh": (
        "查询时填写词条或问题；抽取时填写允许的标签，例如 "
        "dataset:tarot-interpretations 或 dataset:occupations。"
    ),
    "en": (
        "For lookup, the term or question. For sampling, an allowed tag such as "
        "dataset:tarot-interpretations or dataset:occupations."
    ),
    "ja": "検索する語句、または抽出用の許可タグ（例: dataset:tarot-interpretations）。",
    "ko": "검색할 문구 또는 추출용 허용 태그(예: dataset:tarot-interpretations).",
    "es": "El término a consultar o una etiqueta permitida para extraer material.",
    "pt": "O termo a consultar ou uma etiqueta permitida para selecionar material.",
    "ru": "Термин для поиска или разрешённый тег для выбора материала.",
    "zh-TW": "要查詢的詞句，或抽取素材用的允許標籤。",
}

PUBLIC_KNOWLEDGE_MATERIAL_TYPE_DESCRIPTION = {
    "zh": "事实、解释选 knowledge；回复、对话或风格参考选 corpus；auto 根据问题判断。",
    "en": "Use knowledge for facts and explanations, corpus for reply or style examples, and auto to infer from the request.",
    "ja": "事実や説明は knowledge、返信・会話・文体の例は corpus、判定を任せる場合は auto。",
    "ko": "사실·설명은 knowledge, 답변·대화·스타일 예시는 corpus, 자동 판단은 auto를 사용합니다.",
    "es": "Usa knowledge para hechos y explicaciones, corpus para ejemplos de respuesta o estilo y auto para inferirlo.",
    "pt": "Use knowledge para fatos e explicações, corpus para exemplos de resposta ou estilo e auto para inferir.",
    "ru": "knowledge — факты и объяснения, corpus — примеры ответов и стиля, auto — автоматический выбор.",
    "zh-TW": "事實、解釋選 knowledge；回覆、對話或風格參考選 corpus；auto 依問題判斷。",
}
