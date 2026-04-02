# -*- coding: utf-8 -*-
"""
Agent 路由 / 评估相关 prompt — 多语言 i18n 格式。

所有 prompt 均为 dict[lang_code, str]，使用 _loc() 取值。
lang_code: zh / en / ja / ko / ru
"""

# =====================================================================
# ======= 统一渠道评估 (Unified Channel Assessment) =======
# =====================================================================

UNIFIED_CHANNEL_SYSTEM_PROMPT = {
    'zh': """你是一个agentic automation assessment agent, 根据用户的最新请求，判断哪些Agent可以处理它。

可用渠道：
{channels_block}

指令：
1. 分析对话，找出用户最新的可执行请求。
2. 对每个可用渠道，判断它是否能执行该任务。
3. 你应该选出最佳的单一渠道，只为它设置 can_execute=true。
   如果两个渠道同样适合，你可以为两者都设置 can_execute=true — 系统会用优先级规则选择一个。
4. 如果没有渠道能处理请求（如纯聊天、事实性问答），对所有渠道设置 can_execute=false。
5. 如果存在 `LATEST_USER_REQUEST`，以它为准，而非助手声称"已完成"。

输出格式（严格 JSON，不含其他内容）：
{{
{json_fields}
}}

只包含此列表中的渠道：{keys_json}。不要发明渠道。
只返回 JSON 对象，不要 markdown 代码块，不要额外文字。""",

    'en': """You are an agentic automation assessment agent, given the user's latest request, decide which Agent(s) can handle it.

Available Agents:
{channels_block}

INSTRUCTIONS:
1. Analyze the conversation and identify the user's latest actionable request.
2. For EACH available Agent, decide whether it can execute the task.
3. You should pick the SINGLE BEST Agent and set can_execute=true for it only.
   If two Agents are equally suitable, you MAY set can_execute=true for both — the system will use priority rules to pick one.
4. If NO Agent can handle the request (e.g., pure conversation, factual Q&A), set can_execute=false for all.
5. If `LATEST_USER_REQUEST` exists, prioritize it over assistant claims like "already done".

OUTPUT FORMAT (strict JSON, nothing else):
{{
{json_fields}
}}

Only include Agents from this list: {keys_json}. Do NOT invent Agents.
Return ONLY the JSON object, no markdown fences, no extra text.""",

    'ja': """あなたはagentic automation assessment agent, ユーザーの最新リクエストに基づき、どのAgentが処理できるか判断してください。

利用可能なAgent：
{channels_block}

指示：
1. 会話を分析し、ユーザーの最新の実行可能なリクエストを特定してください。
2. 各利用可能Agentについて、タスクを実行できるかどうか判断してください。
3. 最適な単一Agentを選び、そのAgentのみ can_execute=true に設定してください。
   2つのAgentが同等に適している場合、両方を can_execute=true にしても構いません — システムが優先度ルールで1つを選びます。
4. どのAgentもリクエストを処理できない場合（純粋な会話、事実確認など）、すべて can_execute=false にしてください。
5. `LATEST_USER_REQUEST` がある場合、アシスタントの「完了済み」という主張よりもそちらを優先してください。

出力形式（厳密な JSON、他の内容なし）：
{{
{json_fields}
}}

このリストのAgentのみ含めてください：{keys_json}。Agentを作り出さないでください。
JSONオブジェクトのみ返してください。マークダウンフェンスや余分なテキストは不要です。""",

    'ko': """당신은 agentic automation assessment agent, 사용자의 최신 요청에 따라 어떤 Agent가 처리할 수 있는지 판단하세요.

사용 가능한 Agent:
{channels_block}

지침:
1. 대화를 분석하고 사용자의 최신 실행 가능한 요청을 식별하세요.
2. 각 사용 가능한 Agent에 대해 해당 작업을 실행할 수 있는지 결정하세요.
3. 가장 적합한 단일 Agent를 선택하고 해당 Agent만 can_execute=true로 설정하세요.
   두 Agent가 동등하게 적합한 경우 둘 다 can_execute=true로 설정할 수 있습니다 — 시스템이 우선순위 규칙으로 하나를 선택합니다.
4. 어떤 Agent도 요청을 처리할 수 없는 경우(순수 대화, 사실 Q&A 등), 모두 can_execute=false로 설정하세요.
5. `LATEST_USER_REQUEST`가 있으면 어시스턴트의 "이미 완료" 주장보다 이를 우선시하세요.

출력 형식(엄격한 JSON, 다른 내용 없음):
{{
{json_fields}
}}

이 목록의 Agent만 포함하세요: {keys_json}. Agent를 만들어내지 마세요.
JSON 객체만 반환하세요. 마크다운 펜스나 추가 텍스트는 불필요합니다.""",

    'ru': """Вы agentic automation assessment agent, на основе последнего запроса пользователя определите, какие Agent могут его обработать.

Доступные Agent:
{channels_block}

ИНСТРУКЦИИ:
1. Проанализируйте разговор и определите последний выполнимый запрос пользователя.
2. Для КАЖДОГО доступного Agent определите, может ли он выполнить задачу.
3. Выберите ЕДИНСТВЕННЫЙ ЛУЧШИЙ Agent и установите can_execute=true только для него.
   Если два Agent одинаково подходят, вы МОЖЕТЕ установить can_execute=true для обоих — система использует приоритетные правила для выбора.
4. Если НИ ОДИН Agent не может обработать запрос (чистый разговор, фактические Q&A), установите can_execute=false для всех.
5. Если существует `LATEST_USER_REQUEST`, отдайте ему приоритет перед утверждениями ассистента вроде «уже сделано».

ФОРМАТ ВЫВОДА (строгий JSON, больше ничего):
{{
{json_fields}
}}

Включайте только Agent из этого списка: {keys_json}. НЕ выдумывайте Agent.
Верните ТОЛЬКО объект JSON, без markdown-блоков, без дополнительного текста.""",
}


# ── 渠道特点描述 ────────────────────────────────────────────────

CHANNEL_DESC_COPAW = {
    'zh': ("- **copaw**: 远程 Agent 系统 + 云端虚拟机。"
           "最适合需要完全自主的复杂、长时间任务（如多步研究、复杂网页工作流）。"
           "最慢最贵，但最强大。"),
    'en': ("- **copaw**: Remote agent system running on a cloud VM. "
           "Best for complex, long-running tasks that need full autonomy (e.g., multi-step research, "
           "complex web workflows). Slowest and most expensive, but the most powerful."),
    'ja': ("- **copaw**: クラウドVM上で動作するリモートエージェントシステム。"
           "完全な自律性が必要な複雑・長時間タスク（多段階の調査、複雑なWebワークフローなど）に最適。"
           "最も遅く高価だが、最も強力。"),
    'ko': ("- **copaw**: 클라우드 VM에서 실행되는 원격 에이전트 시스템. "
           "완전한 자율성이 필요한 복잡하고 장시간 작업(다단계 연구, 복잡한 웹 워크플로우 등)에 최적. "
           "가장 느리고 비싸지만 가장 강력."),
    'ru': ("- **copaw**: Удалённая агентская система на облачной ВМ. "
           "Лучше всего подходит для сложных долгосрочных задач с полной автономией "
           "(многоэтапные исследования, сложные веб-процессы). Самый медленный и дорогой, но самый мощный."),
}

CHANNEL_DESC_OPENFANG = {
    'zh': ("- **openfang**: 本地 WASM 沙箱多 Agent 系统。"
           "适合需要工具编排的复合任务（数据处理、代码执行、多步思考、多维检索）。"
           "比浏览器慢但功能强大。不适合需要屏幕/GUI 交互的任务。"),
    'en': ("- **openfang**: Local WASM-sandboxed multi-agent system. "
           "Good for compound tasks requiring tool orchestration (data processing, "
           "code execution, multi-step reasoning, multi-dimensional retrieval). "
           "Slower than browser but very capable. NOT suitable for tasks requiring screen/GUI interaction."),
    'ja': ("- **openfang**: ローカルWASMサンドボックス型マルチエージェントシステム。"
           "ツール連携が必要な複合タスク（データ処理、コード実行、多段階推論、多次元検索）に適。"
           "ブラウザより遅いが非常に高機能。画面/GUI操作が必要なタスクには不向き。"),
    'ko': ("- **openfang**: 로컬 WASM 샌드박스 멀티 에이전트 시스템. "
           "도구 오케스트레이션이 필요한 복합 작업(데이터 처리, 코드 실행, 다단계 추론, 다차원 검색)에 적합. "
           "브라우저보다 느리지만 매우 강력. 화면/GUI 상호작용이 필요한 작업에는 부적합."),
    'ru': ("- **openfang**: Локальная мультиагентная WASM-песочница. "
           "Подходит для составных задач с оркестрацией инструментов (обработка данных, "
           "выполнение кода, многоэтапное рассуждение, многомерный поиск). "
           "Медленнее браузера, но очень функционален. НЕ подходит для задач с экраном/GUI."),
}

CHANNEL_DESC_BROWSER_USE = {
    'zh': ("- **browser_use**: 本地浏览器自动化。"
           "快速且经济，适合简单网页交互：打开 URL、填写网页表单、网页搜索、从网络下载。"
           "仅限本地浏览器任务 — 无法与操作系统应用交互。"),
    'en': ("- **browser_use**: Local browser automation. "
           "Fast and cheap for simple web interactions: opening URLs, filling web forms, "
           "web search, downloading from the internet. "
           "Limited to local browser tasks — cannot interact with OS applications."),
    'ja': ("- **browser_use**: ローカルブラウザ自動化。"
           "単純なWeb操作に高速かつ低コスト：URL を開く、Webフォーム入力、Web検索、ダウンロード。"
           "ローカルブラウザタスクに限定 — OSアプリとの連携不可。"),
    'ko': ("- **browser_use**: 로컬 브라우저 자동화. "
           "간단한 웹 상호작용에 빠르고 저렴: URL 열기, 웹 폼 작성, 웹 검색, 인터넷 다운로드. "
           "로컬 브라우저 작업에 한정 — OS 앱과 상호작용 불가."),
    'ru': ("- **browser_use**: Локальная автоматизация браузера. "
           "Быстро и дёшево для простых веб-действий: открытие URL, заполнение форм, "
           "веб-поиск, скачивание. "
           "Только задачи локального браузера — не может взаимодействовать с приложениями ОС."),
}

CHANNEL_DESC_COMPUTER_USE = {
    'zh': ("- **computer_use**: 直接控制本地键盘和鼠标。"
           "唯一可以与本地操作系统交互的渠道（打开桌面应用、点击原生 UI 元素、控制鼠标键盘）。"
           "较慢、较贵，且会占用用户的鼠标键盘。"
           "在任务明确需要本地操作系统 GUI 交互时使用。"),
    'en': ("- **computer_use**: Direct local keyboard & mouse control. "
           "The ONLY channel that can interact with the local operating system "
           "(open desktop apps, click native UI elements, control the mouse/keyboard). "
           "Slower, more expensive, and takes over the user's mouse/keyboard. "
           "Use when the task clearly requires local OS GUI interaction."),
    'ja': ("- **computer_use**: ローカルキーボード＆マウスの直接操作。"
           "ローカルOSと対話できる唯一のチャネル"
           "（デスクトップアプリ起動、ネイティブUI要素クリック、マウス/キーボード操作）。"
           "より遅く高価で、ユーザーのマウス/キーボードを占有。"
           "タスクが明確にローカルOS GUI操作を必要とする場合に使用。"),
    'ko': ("- **computer_use**: 로컬 키보드 및 마우스 직접 제어. "
           "로컬 운영체제와 상호작용할 수 있는 유일한 채널 "
           "(데스크톱 앱 열기, 네이티브 UI 요소 클릭, 마우스/키보드 제어). "
           "더 느리고 비싸며 사용자의 마우스/키보드를 점유. "
           "작업이 명확히 로컬 OS GUI 상호작용을 요구할 때 사용."),
    'ru': ("- **computer_use**: Прямое управление клавиатурой и мышью. "
           "ЕДИНСТВЕННЫЙ канал для взаимодействия с локальной ОС "
           "(запуск приложений, клики по нативным элементам UI, управление мышью/клавиатурой). "
           "Медленнее, дороже и занимает мышь/клавиатуру пользователя. "
           "Используется когда задача явно требует GUI-взаимодействия с локальной ОС."),
}


# =====================================================================
# ======= User Plugin 评估 =======
# =====================================================================

USER_PLUGIN_SYSTEM_PROMPT = {
    'zh': """你是一个用户插件automation assessment agent, 可用插件列表：
{plugins_desc}

指令：
1. 分析对话，判断是否应该为用户的请求调用某个可用插件。
2. 关注用户的最新消息/意图 — 不要关注 AI 是否已经回复。AI 在对话中的回复不代表插件不需要；评估用户的请求是否能从插件执行中受益。
3. 如果是，你必须返回 plugin id、entry_id（该插件内要调用的具体入口）以及匹配入口 schema 的 plugin_args。
4. 如果无法确定具体的插件入口，返回 has_task=false 或 can_execute=false，并在 'reason' 字段中说明原因。
5. 输出必须只是一个 JSON 对象，不含其他内容。不要包含任何解释性文字、markdown 或代码块。

示例（必须严格遵循此结构）：
{{
    "has_task": true,
    "can_execute": true,
    "task_description": "example: call testPlugin open entry",
    "plugin_id": "testPlugin",
    "entry_id": "open",
    "plugin_args": {{"message": "hello"}},
    "reason": ""
}}

输出格式（严格 JSON）：
{{
    "has_task": boolean,
    "can_execute": boolean,
    "task_description": "简要描述",
    "plugin_id": "插件 id 或 null",
    "entry_id": "插件内的入口 id 或 null",
    "plugin_args": {{...}} 或 null,
    "reason": "原因"
}}

非常重要：
- 如果 has_task 和 can_execute 都为 true，entry_id 是必需的。
- 如果 has_task/can_execute 为 true 时 entry_id 缺失或为 null，响应将被视为不可执行。
- 严格匹配：plugin_id 和 entry_id 是代码标识符。你必须从上面的可用插件列表中原样复制它们（区分大小写、逐字符匹配）。不要发明、缩写或改写它们。如果找不到完全匹配，设置 can_execute=false。
- 如果入口有 args(...) 信息，在 plugin_args 中使用那些字段名。只包含 schema 中列出的字段。
- 如果用户的意图与任何插件的描述功能不明确匹配，设置 has_task=false。
只返回 JSON 对象，不含其他内容。""",

    'en': """You are a User Plugin automation assessment agent, AVAILABLE PLUGINS:
{plugins_desc}

INSTRUCTIONS:
1. Analyze the conversation and determine if any available plugin should be invoked for the user's request.
2. Focus on the USER's latest message/intent — NOT on whether the AI has already replied. An AI reply in the conversation does NOT mean the plugin is unnecessary; assess whether the user's request can benefit from plugin execution.
3. If yes, you MUST return the plugin id, the entry_id (the specific entry inside that plugin to invoke), and plugin_args matching the entry's schema.
4. If you cannot determine a specific plugin entry, return has_task=false or can_execute=false and explain why in the 'reason' field.
5. OUTPUT MUST BE ONLY a single JSON object and NOTHING ELSE. Do NOT include any explanatory text, markdown, or code fences.

EXAMPLE (must follow this structure exactly):
{{
    "has_task": true,
    "can_execute": true,
    "task_description": "example: call testPlugin open entry",
    "plugin_id": "testPlugin",
    "entry_id": "open",
    "plugin_args": {{"message": "hello"}},
    "reason": ""
}}

OUTPUT FORMAT (strict JSON):
{{
    "has_task": boolean,
    "can_execute": boolean,
    "task_description": "brief description",
    "plugin_id": "plugin id or null",
    "entry_id": "entry id inside the plugin or null",
    "plugin_args": {{...}} or null,
    "reason": "why"
}}

VERY IMPORTANT:
- If has_task and can_execute are true, entry_id is REQUIRED.
- If entry_id is missing or null when has_task/can_execute are true, the response will be treated as non-executable.
- STRICT MATCHING: plugin_id and entry_id are code identifiers. You MUST copy them EXACTLY (case-sensitive, character-for-character) from the AVAILABLE PLUGINS list above. Do NOT invent, abbreviate, or paraphrase them. If you cannot find an exact match, set can_execute=false.
- If an entry has args(...) info, use those field names in plugin_args. Only include fields listed in the schema.
- If the user's intent does not clearly match any plugin's described functionality, set has_task=false.
Return only the JSON object, nothing else.""",

    'ja': """あなたはユーザープラグイン automation assessment agent, 利用可能なプラグイン一覧：
{plugins_desc}

指示：
1. 会話を分析し、ユーザーのリクエストに対して利用可能なプラグインを呼び出すべきか判断してください。
2. ユーザーの最新メッセージ/意図に注目してください — AIがすでに返答したかどうかではありません。会話中のAI返答はプラグインが不要であることを意味しません。ユーザーのリクエストがプラグイン実行から利益を得られるか評価してください。
3. はいの場合、plugin id、entry_id（呼び出すプラグイン内の特定エントリ）、entry の schema に一致する plugin_args を返す必要があります。
4. 特定のプラグインエントリを決定できない場合、has_task=false または can_execute=false を返し、'reason' フィールドで理由を説明してください。
5. 出力は単一のJSONオブジェクトのみで、他のものは一切含めないでください。説明テキスト、markdown、コードブロックを含めないでください。

例（この構造に厳密に従ってください）：
{{
    "has_task": true,
    "can_execute": true,
    "task_description": "example: call testPlugin open entry",
    "plugin_id": "testPlugin",
    "entry_id": "open",
    "plugin_args": {{"message": "hello"}},
    "reason": ""
}}

出力形式（厳密なJSON）：
{{
    "has_task": boolean,
    "can_execute": boolean,
    "task_description": "簡潔な説明",
    "plugin_id": "プラグインIDまたはnull",
    "entry_id": "プラグイン内のエントリIDまたはnull",
    "plugin_args": {{...}} または null,
    "reason": "理由"
}}

非常に重要：
- has_task と can_execute が true の場合、entry_id は必須です。
- has_task/can_execute が true なのに entry_id が欠落または null の場合、レスポンスは実行不可として扱われます。
- 厳密マッチング：plugin_id と entry_id はコード識別子です。上記の利用可能プラグインリストからそのまま（大文字小文字区別、文字ごと）コピーしてください。発明、省略、言い換えをしないでください。完全一致が見つからない場合は can_execute=false を設定してください。
- エントリに args(...) 情報がある場合、plugin_args でそのフィールド名を使用してください。schema にリストされたフィールドのみ含めてください。
- ユーザーの意図がどのプラグインの機能とも明確に一致しない場合、has_task=false を設定してください。
JSONオブジェクトのみ返してください。""",

    'ko': """당신은 사용자 플러그인 automation assessment agent, 사용 가능한 플러그인 목록:
{plugins_desc}

지침:
1. 대화를 분석하고 사용자의 요청에 대해 사용 가능한 플러그인을 호출해야 하는지 판단하세요.
2. 사용자의 최신 메시지/의도에 집중하세요 — AI가 이미 응답했는지 여부가 아닙니다. 대화에서 AI 응답은 플러그인이 불필요하다는 의미가 아닙니다. 사용자의 요청이 플러그인 실행으로 이익을 얻을 수 있는지 평가하세요.
3. 예인 경우, plugin id, entry_id(호출할 플러그인 내 특정 엔트리), entry의 schema에 맞는 plugin_args를 반환해야 합니다.
4. 특정 플러그인 엔트리를 결정할 수 없는 경우, has_task=false 또는 can_execute=false를 반환하고 'reason' 필드에 이유를 설명하세요.
5. 출력은 단일 JSON 객체만이어야 하며 다른 것은 포함하지 마세요. 설명 텍스트, 마크다운, 코드 블록을 포함하지 마세요.

예시(이 구조를 정확히 따라야 합니다):
{{
    "has_task": true,
    "can_execute": true,
    "task_description": "example: call testPlugin open entry",
    "plugin_id": "testPlugin",
    "entry_id": "open",
    "plugin_args": {{"message": "hello"}},
    "reason": ""
}}

출력 형식(엄격한 JSON):
{{
    "has_task": boolean,
    "can_execute": boolean,
    "task_description": "간단한 설명",
    "plugin_id": "플러그인 id 또는 null",
    "entry_id": "플러그인 내 엔트리 id 또는 null",
    "plugin_args": {{...}} 또는 null,
    "reason": "이유"
}}

매우 중요:
- has_task와 can_execute가 true이면 entry_id는 필수입니다.
- has_task/can_execute가 true인데 entry_id가 누락되거나 null이면 응답은 실행 불가능으로 처리됩니다.
- 엄격한 매칭: plugin_id와 entry_id는 코드 식별자입니다. 위의 사용 가능한 플러그인 목록에서 정확히(대소문자 구분, 문자 단위) 복사해야 합니다. 만들거나 축약하거나 바꿔 말하지 마세요. 정확한 일치를 찾을 수 없으면 can_execute=false로 설정하세요.
- 엔트리에 args(...) 정보가 있으면 plugin_args에서 해당 필드 이름을 사용하세요. schema에 나열된 필드만 포함하세요.
- 사용자의 의도가 어떤 플러그인의 설명된 기능과 명확히 일치하지 않으면 has_task=false로 설정하세요.
JSON 객체만 반환하세요.""",

    'ru': """Вы — пользовательский плагин automation assessment agent, список доступных плагинов:
{plugins_desc}

ИНСТРУКЦИИ:
1. Проанализируйте разговор и определите, нужно ли вызвать какой-либо доступный плагин для запроса пользователя.
2. Сосредоточьтесь на последнем сообщении/намерении ПОЛЬЗОВАТЕЛЯ — НЕ на том, ответил ли уже ИИ. Ответ ИИ в разговоре НЕ означает, что плагин не нужен; оцените, может ли запрос пользователя выиграть от выполнения плагина.
3. Если да, вы ДОЛЖНЫ вернуть plugin id, entry_id (конкретную точку входа внутри плагина) и plugin_args, соответствующие схеме точки входа.
4. Если вы не можете определить конкретную точку входа плагина, верните has_task=false или can_execute=false и объясните причину в поле 'reason'.
5. ВЫВОД ДОЛЖЕН БЫТЬ ТОЛЬКО одним объектом JSON и НИЧЕМ БОЛЬШЕ. НЕ включайте пояснительный текст, markdown или блоки кода.

ПРИМЕР (следуйте этой структуре точно):
{{
    "has_task": true,
    "can_execute": true,
    "task_description": "example: call testPlugin open entry",
    "plugin_id": "testPlugin",
    "entry_id": "open",
    "plugin_args": {{"message": "hello"}},
    "reason": ""
}}

ФОРМАТ ВЫВОДА (строгий JSON):
{{
    "has_task": boolean,
    "can_execute": boolean,
    "task_description": "краткое описание",
    "plugin_id": "id плагина или null",
    "entry_id": "id точки входа внутри плагина или null",
    "plugin_args": {{...}} или null,
    "reason": "причина"
}}

ОЧЕНЬ ВАЖНО:
- Если has_task и can_execute равны true, entry_id ОБЯЗАТЕЛЕН.
- Если entry_id отсутствует или равен null при has_task/can_execute=true, ответ будет считаться невыполнимым.
- СТРОГОЕ СООТВЕТСТВИЕ: plugin_id и entry_id — это кодовые идентификаторы. Вы ДОЛЖНЫ скопировать их ТОЧНО (с учётом регистра, посимвольно) из списка доступных плагинов выше. НЕ придумывайте, не сокращайте и не перефразируйте. Если точное совпадение не найдено, установите can_execute=false.
- Если у точки входа есть информация args(...), используйте эти имена полей в plugin_args. Включайте только поля, указанные в схеме.
- Если намерение пользователя явно не соответствует описанной функциональности ни одного плагина, установите has_task=false.
Верните только объект JSON, ничего больше.""",
}
