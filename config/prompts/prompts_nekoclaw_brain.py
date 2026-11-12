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

"""Texts of the NekoClaw task brain (``nekoclaw/host/``).

* ``TASK_BRAIN_SELECT_PROMPT``: the fine selection of the decision model. It
  took over the plugin rules of the analyzer's selection prompt and adds the
  hand-over to a task agent and the answer that nothing can do it, which the
  same request decides.
* ``TASK_BRAIN_COARSE_SCREEN_PROMPT``: the coarse screen, the analyzer's
  text unchanged.
* ``TURN_CHECK_PROMPT``: the check of a turn in which she called no task tool.
* Receipts and query answers: short lines returned to the main dialogue model
  as tool results.
* What she is told afterwards: the outcome of a turn check, and a question a
  commission has for the user.
* Cue patterns: how a dispatched sentence is recognised, without a model, as
  a stop request, an answer or an addition to something already under way.
* Preference kinds: which kind a stated task preference is, without a model.
"""

# =====================================================================
# ======= Fine selection =======
# =====================================================================
# The system text; ``{plugins_desc}`` is the only placeholder. The user
# message is built by ``build_selection_request`` below.

TASK_BRAIN_SELECT_PROMPT = {
    'zh': """你是一个用户插件automation assessment agent, 负责任务路由。可用插件列表：
{plugins_desc}

用户消息里有：TASK（要办的事）、USER_SAID（用户原话），可能还有 ACTIVE_TASKS 和 PREFERENCES。

指令：
1. 判断上面某个插件入口调用一次能否办完 TASK。插件优先。
2. 能：返回 route="plugin"，以及 plugin_id、entry_id 和匹配入口 schema 的 plugin_args。
3. 没有合适的入口，或这件事要多步、要多个工具：返回 route="task_agent"。task_agent 能读写文件、运行命令、改系统设置、上网、操作浏览器和键鼠。不要勉强选一个只对得上一半的插件。
4. 插件入口办不了，task_agent 靠上面这些也办不到（比如要在现实世界里动手），或者根本不是一件要去办的事（只是聊天）：返回 route="none"。拿不准时选 task_agent，不要选 none。
5. TASK 和 ACTIVE_TASKS 里某一行是同一件事：返回 route="duplicate"，duplicate_of 填那一行的序号。
6. 输出必须只是一个 JSON 对象，不含解释性文字、markdown 或代码块。

输出格式（严格 JSON）：
{{
    "route": "plugin" | "task_agent" | "duplicate" | "none",
    "task_description": "简要描述",
    "plugin_id": "插件 id 或 null",
    "entry_id": "插件内的入口 id 或 null",
    "plugin_args": {{...}} 或 null,
    "duplicate_of": 序号或 null,
    "preference": "TASK 里交代的、以后同类事都适用的做法；没有就填 null",
    "reason": "原因"
}}

非常重要：
- 严格匹配：plugin_id 和 entry_id 是代码标识符，必须从上面的列表中原样复制（区分大小写、逐字符匹配），不要发明、缩写或改写。找不到完全匹配就用 route="task_agent"。
- route="plugin" 时 entry_id 必填。
- 入口有 args(...) 信息时，plugin_args 用那些字段名，只包含 schema 中列出的字段。
- 入口 schema 需要用户文本字段（例如 command/message/query/objective）时，必须复制 USER_SAID 原文；不要翻译、摘要、改写或补全。
- PREFERENCES 适用时照办。
- 标注了 [KEYWORD MATCH] 的插件已通过关键词预筛，优先考虑。
只返回 JSON 对象，不含其他内容。""",

    'zh-TW': """你是一個使用者外掛automation assessment agent, 負責任務路由。可用外掛清單：
{plugins_desc}

使用者訊息裡有：TASK（要辦的事）、USER_SAID（使用者原話），可能還有 ACTIVE_TASKS 和 PREFERENCES。

指令：
1. 判斷上面某個外掛入口呼叫一次能不能辦完 TASK。外掛優先。
2. 能：回傳 route="plugin"，以及 plugin_id、entry_id 和符合入口 schema 的 plugin_args。
3. 沒有合適的入口，或這件事要多步、要多個工具：回傳 route="task_agent"。task_agent 能讀寫檔案、執行命令、改系統設定、上網、操作瀏覽器和鍵鼠。不要勉強選一個只對得上一半的外掛。
4. 外掛入口辦不了，task_agent 靠上面這些也辦不到（例如要在現實世界裡動手），或者根本不是一件要去辦的事（只是聊天）：回傳 route="none"。拿不準時選 task_agent，不要選 none。
5. TASK 和 ACTIVE_TASKS 裡某一行是同一件事：回傳 route="duplicate"，duplicate_of 填那一行的序號。
6. 輸出必須只有一個 JSON 物件，不含解釋性文字、markdown 或程式碼區塊。

輸出格式（嚴格 JSON）：
{{
    "route": "plugin" | "task_agent" | "duplicate" | "none",
    "task_description": "簡要描述",
    "plugin_id": "外掛 id 或 null",
    "entry_id": "外掛裡的入口 id 或 null",
    "plugin_args": {{...}} 或 null,
    "duplicate_of": 序號或 null,
    "preference": "TASK 裡交代的、以後同類事都適用的做法；沒有就填 null",
    "reason": "原因"
}}

非常重要：
- 嚴格比對：plugin_id 和 entry_id 是程式識別碼，必須從上面的清單原樣複製（區分大小寫、逐字元比對），不要發明、縮寫或改寫。找不到完全相符的就用 route="task_agent"。
- route="plugin" 時 entry_id 必填。
- 入口有 args(...) 資訊時，plugin_args 用那些欄位名，只包含 schema 裡列出的欄位。
- 入口 schema 需要使用者文字欄位（例如 command/message/query/objective）時，必須照抄 USER_SAID 原文；不要翻譯、摘要、改寫或補齊。
- PREFERENCES 適用時照辦。
- 標了 [KEYWORD MATCH] 的外掛已經通過關鍵字預篩，優先考慮。
只回傳 JSON 物件，不含其他內容。""",

    'en': """You are a User Plugin automation assessment agent, routing tasks. AVAILABLE PLUGINS:
{plugins_desc}

The user message gives TASK (what has to get done) and USER_SAID (the user's own words), and may give ACTIVE_TASKS and PREFERENCES.

INSTRUCTIONS:
1. Decide whether ONE call to ONE plugin entry above gets TASK done. Plugins come first.
2. If yes: return route="plugin" with plugin_id, entry_id and plugin_args matching the entry's schema.
3. If no entry fits, or the task needs several steps or several tools: return route="task_agent". A task_agent can read and write files, run commands, change system settings, go online, and operate the browser, keyboard and mouse. Do NOT force a plugin that only half fits.
4. If no plugin entry can do it and a task_agent cannot either with the above (it needs hands in the real world, say), or it is not something to get done at all (just chat): return route="none". When unsure, choose task_agent, not none.
5. If TASK is the same task as a line of ACTIVE_TASKS: return route="duplicate" and that line's number in duplicate_of.
6. OUTPUT MUST BE ONLY a single JSON object. No explanatory text, markdown or code fences.

OUTPUT FORMAT (strict JSON):
{{
    "route": "plugin" | "task_agent" | "duplicate" | "none",
    "task_description": "brief description",
    "plugin_id": "plugin id or null",
    "entry_id": "entry id inside that plugin or null",
    "plugin_args": {{...}} or null,
    "duplicate_of": number or null,
    "preference": "a lasting instruction TASK gives for tasks of this kind, else null",
    "reason": "why"
}}

VERY IMPORTANT:
- STRICT MATCHING: plugin_id and entry_id are code identifiers. Copy them EXACTLY (case-sensitive, character-for-character) from the list above. Do NOT invent, abbreviate or rephrase them. If no exact match exists, use route="task_agent".
- route="plugin" requires entry_id.
- If the entry has args(...) info, use those field names in plugin_args. Only include fields listed in the schema.
- When the entry schema needs a user-text field (e.g. command/message/query/objective), copy USER_SAID verbatim; do not translate, summarize, rewrite or complete it.
- Follow PREFERENCES where they apply.
- Plugins marked [KEYWORD MATCH] passed a keyword pre-filter; consider them first.
Return only the JSON object, nothing else.""",

    'ja': """あなたはユーザープラグイン automation assessment agent, タスクの振り分けを担当します。利用可能なプラグイン一覧：
{plugins_desc}

ユーザーメッセージには TASK（片付ける用件）と USER_SAID（ユーザー本人の言葉）があり、ACTIVE_TASKS と PREFERENCES が付くこともあります。

指示：
1. 上のプラグインエントリのどれか 1 つを 1 回呼ぶだけで TASK が片付くか判断します。プラグイン優先です。
2. 片付く場合：route="plugin" と、plugin_id、entry_id、エントリの schema に合う plugin_args を返します。
3. 合うエントリがない、または複数の手順や複数のツールが必要な場合：route="task_agent" を返します。task_agent はファイルの読み書き、コマンドの実行、システム設定の変更、ネット接続、ブラウザとキーボード・マウスの操作ができます。半分しか合わないプラグインを無理に選ばないでください。
4. プラグインのエントリでは片付かず、task_agent が上の手段を使っても片付かない場合（現実世界で手を動かす必要があるなど）、またはそもそも片付ける用件ではない場合（ただの雑談）：route="none" を返します。迷ったら none ではなく task_agent を選びます。
5. TASK が ACTIVE_TASKS のある行と同じ用件の場合：route="duplicate" を返し、duplicate_of にその行の番号を入れます。
6. 出力は JSON オブジェクト 1 つだけにします。説明文、markdown、コードブロックは付けません。

出力形式（厳密な JSON）：
{{
    "route": "plugin" | "task_agent" | "duplicate" | "none",
    "task_description": "簡潔な説明",
    "plugin_id": "プラグイン id または null",
    "entry_id": "プラグイン内のエントリ id または null",
    "plugin_args": {{...}} または null,
    "duplicate_of": 番号または null,
    "preference": "TASK が示した、今後同じ種類の用件に当てはまるやり方。なければ null",
    "reason": "理由"
}}

非常に重要：
- 厳密一致：plugin_id と entry_id はコード識別子です。上の一覧からそのまま写してください（大文字小文字を区別、1 文字ずつ一致）。作り出したり、省略したり、言い換えたりしないでください。完全一致がなければ route="task_agent" にします。
- route="plugin" のとき entry_id は必須です。
- エントリに args(...) があれば、plugin_args にはそのフィールド名を使い、schema にあるフィールドだけを入れます。
- エントリの schema がユーザーのテキスト（command/message/query/objective など）を求めるときは、USER_SAID を原文のまま写します。翻訳、要約、書き換え、補完はしません。
- PREFERENCES が当てはまるときは従います。
- [KEYWORD MATCH] の付いたプラグインはキーワードの事前選別を通っています。優先して検討してください。
JSON オブジェクトだけを返してください。""",

    'ko': """당신은 사용자 플러그인 automation assessment agent, 작업 라우팅을 담당합니다. 사용 가능한 플러그인 목록:
{plugins_desc}

사용자 메시지에는 TASK(처리할 일)와 USER_SAID(사용자가 한 말 그대로)가 있고, ACTIVE_TASKS와 PREFERENCES가 붙을 수 있습니다.

지침:
1. 위 플러그인 엔트리 하나를 한 번 호출해서 TASK를 끝낼 수 있는지 판단합니다. 플러그인이 우선입니다.
2. 끝낼 수 있으면: route="plugin"과 plugin_id, entry_id, 엔트리 schema에 맞는 plugin_args를 반환합니다.
3. 맞는 엔트리가 없거나 여러 단계 또는 여러 도구가 필요하면: route="task_agent"를 반환합니다. task_agent는 파일 읽기와 쓰기, 명령 실행, 시스템 설정 변경, 인터넷 접속, 브라우저와 키보드·마우스 조작을 할 수 있습니다. 절반만 맞는 플러그인을 억지로 고르지 마세요.
4. 플러그인 엔트리로 할 수 없고 task_agent가 위 수단으로도 할 수 없는 일(예: 현실 세계에서 직접 손을 써야 하는 일)이거나, 애초에 처리할 일이 아니면(그냥 대화): route="none"을 반환합니다. 확실하지 않으면 none이 아니라 task_agent를 고릅니다.
5. TASK가 ACTIVE_TASKS의 어느 줄과 같은 일이면: route="duplicate"를 반환하고 duplicate_of에 그 줄의 번호를 넣습니다.
6. 출력은 JSON 객체 하나뿐이어야 합니다. 설명 문장, markdown, 코드 블록을 넣지 마세요.

출력 형식(엄격한 JSON):
{{
    "route": "plugin" | "task_agent" | "duplicate" | "none",
    "task_description": "간단한 설명",
    "plugin_id": "플러그인 id 또는 null",
    "entry_id": "플러그인 안의 엔트리 id 또는 null",
    "plugin_args": {{...}} 또는 null,
    "duplicate_of": 번호 또는 null,
    "preference": "TASK가 알려 준, 앞으로 같은 종류의 일에 적용할 방식. 없으면 null",
    "reason": "이유"
}}

매우 중요:
- 엄격한 일치: plugin_id와 entry_id는 코드 식별자입니다. 위 목록에서 그대로 복사하세요(대소문자 구분, 글자 단위 일치). 지어내거나 줄이거나 바꿔 쓰지 마세요. 정확히 일치하는 것이 없으면 route="task_agent"로 합니다.
- route="plugin"이면 entry_id가 필수입니다.
- 엔트리에 args(...) 정보가 있으면 plugin_args에 그 필드 이름을 쓰고, schema에 있는 필드만 넣습니다.
- 엔트리 schema가 사용자 텍스트 필드(command/message/query/objective 등)를 요구하면 USER_SAID를 원문 그대로 복사합니다. 번역, 요약, 바꿔 쓰기, 보완을 하지 마세요.
- PREFERENCES가 해당되면 따릅니다.
- [KEYWORD MATCH]가 붙은 플러그인은 키워드 사전 선별을 통과했습니다. 먼저 검토하세요.
JSON 객체만 반환하세요.""",

    'ru': """Вы — пользовательский плагин automation assessment agent, отвечаете за маршрутизацию задач. ДОСТУПНЫЕ ПЛАГИНЫ:
{plugins_desc}

В сообщении пользователя есть TASK (что нужно сделать) и USER_SAID (слова самого пользователя), могут быть ACTIVE_TASKS и PREFERENCES.

ИНСТРУКЦИИ:
1. Определите, выполнит ли TASK ОДИН вызов ОДНОЙ точки входа плагина из списка. Плагины в приоритете.
2. Если да: верните route="plugin", а также plugin_id, entry_id и plugin_args по схеме точки входа.
3. Если ни одна точка входа не подходит или задаче нужно несколько шагов либо несколько инструментов: верните route="task_agent". task_agent умеет читать и записывать файлы, выполнять команды, менять системные настройки, выходить в интернет, управлять браузером, клавиатурой и мышью. НЕ подбирайте плагин, который подходит лишь наполовину.
4. Если точки входа плагинов не подходят и task_agent перечисленным тоже не справится (например, нужно что-то сделать руками в реальном мире) или это вовсе не задача (просто разговор): верните route="none". Если не уверены, выбирайте task_agent, а не none.
5. Если TASK совпадает с одной из строк ACTIVE_TASKS: верните route="duplicate" и номер этой строки в duplicate_of.
6. ВЫВОД ДОЛЖЕН БЫТЬ ТОЛЬКО одним объектом JSON. Без пояснений, markdown и блоков кода.

ФОРМАТ ВЫВОДА (строгий JSON):
{{
    "route": "plugin" | "task_agent" | "duplicate" | "none",
    "task_description": "краткое описание",
    "plugin_id": "id плагина или null",
    "entry_id": "id точки входа в этом плагине или null",
    "plugin_args": {{...}} или null,
    "duplicate_of": номер или null,
    "preference": "постоянное указание из TASK для задач такого рода, иначе null",
    "reason": "почему"
}}

ОЧЕНЬ ВАЖНО:
- СТРОГОЕ СОВПАДЕНИЕ: plugin_id и entry_id — идентификаторы в коде. Копируйте их ТОЧНО (с учётом регистра, символ в символ) из списка выше. НЕ выдумывайте, не сокращайте и не перефразируйте. Если точного совпадения нет, используйте route="task_agent".
- Для route="plugin" обязателен entry_id.
- Если у точки входа есть args(...), используйте эти имена полей в plugin_args и только поля из схемы.
- Если схеме нужно текстовое поле пользователя (например command/message/query/objective), копируйте USER_SAID дословно; не переводите, не сокращайте, не переписывайте и не дополняйте.
- Следуйте PREFERENCES там, где они применимы.
- Плагины с пометкой [KEYWORD MATCH] прошли отбор по ключевым словам; рассмотрите их первыми.
Верните только объект JSON, больше ничего.""",

    'es': """Eres un User Plugin automation assessment agent que enruta tareas. PLUGINS DISPONIBLES:
{plugins_desc}

El mensaje del usuario trae TASK (lo que hay que hacer) y USER_SAID (las palabras del propio usuario), y puede traer ACTIVE_TASKS y PREFERENCES.

INSTRUCCIONES:
1. Decide si UNA llamada a UNA entrada de plugin de la lista resuelve TASK. Los plugins van primero.
2. Si es así: devuelve route="plugin" con plugin_id, entry_id y plugin_args según el schema de la entrada.
3. Si ninguna entrada encaja, o la tarea necesita varios pasos o varias herramientas: devuelve route="task_agent". Un task_agent puede leer y escribir archivos, ejecutar comandos, cambiar ajustes del sistema, conectarse a internet y manejar el navegador, el teclado y el ratón. NO fuerces un plugin que solo encaja a medias.
4. Si ninguna entrada de plugin puede hacerlo y un task_agent tampoco con lo anterior (por ejemplo, hay que actuar en el mundo real), o no es algo que haya que hacer (solo conversación): devuelve route="none". Si dudas, elige task_agent, no none.
5. Si TASK es la misma tarea que una línea de ACTIVE_TASKS: devuelve route="duplicate" y el número de esa línea en duplicate_of.
6. LA SALIDA DEBE SER SOLO un objeto JSON. Sin texto explicativo, markdown ni bloques de código.

FORMATO DE SALIDA (JSON estricto):
{{
    "route": "plugin" | "task_agent" | "duplicate" | "none",
    "task_description": "descripción breve",
    "plugin_id": "id del plugin o null",
    "entry_id": "id de la entrada dentro del plugin o null",
    "plugin_args": {{...}} o null,
    "duplicate_of": número o null,
    "preference": "una instrucción duradera que TASK da para tareas de este tipo; si no, null",
    "reason": "por qué"
}}

MUY IMPORTANTE:
- COINCIDENCIA ESTRICTA: plugin_id y entry_id son identificadores de código. Cópialos EXACTAMENTE (distinguiendo mayúsculas, carácter por carácter) de la lista. NO los inventes, abrevies ni reformules. Si no hay coincidencia exacta, usa route="task_agent".
- route="plugin" exige entry_id.
- Si la entrada tiene args(...), usa esos nombres de campo en plugin_args y solo los campos del schema.
- Cuando el schema pide un campo de texto del usuario (p. ej. command/message/query/objective), copia USER_SAID tal cual; no lo traduzcas, resumas, reescribas ni completes.
- Sigue PREFERENCES cuando correspondan.
- Los plugins marcados con [KEYWORD MATCH] pasaron un prefiltro de palabras clave; considéralos primero.
Devuelve solo el objeto JSON, nada más.""",

    'pt': """Você é um User Plugin automation assessment agent que roteia tarefas. PLUGINS DISPONÍVEIS:
{plugins_desc}

A mensagem do usuário traz TASK (o que precisa ser feito) e USER_SAID (as palavras do próprio usuário), e pode trazer ACTIVE_TASKS e PREFERENCES.

INSTRUÇÕES:
1. Decida se UMA chamada a UMA entrada de plugin da lista resolve TASK. Plugins vêm primeiro.
2. Se sim: retorne route="plugin" com plugin_id, entry_id e plugin_args conforme o schema da entrada.
3. Se nenhuma entrada servir, ou a tarefa precisar de vários passos ou várias ferramentas: retorne route="task_agent". Um task_agent pode ler e gravar arquivos, executar comandos, mudar configurações do sistema, acessar a internet e operar o navegador, o teclado e o mouse. NÃO force um plugin que só serve pela metade.
4. Se nenhuma entrada de plugin puder fazer isso e um task_agent também não com o que foi dito acima (por exemplo, é preciso agir no mundo real), ou não for algo a ser feito (só conversa): retorne route="none". Na dúvida, escolha task_agent, não none.
5. Se TASK for a mesma tarefa de uma linha de ACTIVE_TASKS: retorne route="duplicate" e o número dessa linha em duplicate_of.
6. A SAÍDA DEVE SER APENAS um objeto JSON. Sem texto explicativo, markdown ou blocos de código.

FORMATO DE SAÍDA (JSON estrito):
{{
    "route": "plugin" | "task_agent" | "duplicate" | "none",
    "task_description": "descrição breve",
    "plugin_id": "id do plugin ou null",
    "entry_id": "id da entrada dentro do plugin ou null",
    "plugin_args": {{...}} ou null,
    "duplicate_of": número ou null,
    "preference": "uma instrução duradoura que TASK dá para tarefas deste tipo; senão, null",
    "reason": "por quê"
}}

MUITO IMPORTANTE:
- CORRESPONDÊNCIA ESTRITA: plugin_id e entry_id são identificadores de código. Copie-os EXATAMENTE (diferenciando maiúsculas, caractere por caractere) da lista. NÃO invente, abrevie nem reformule. Se não houver correspondência exata, use route="task_agent".
- route="plugin" exige entry_id.
- Se a entrada tiver args(...), use esses nomes de campo em plugin_args e apenas os campos do schema.
- Quando o schema pedir um campo de texto do usuário (ex.: command/message/query/objective), copie USER_SAID literalmente; não traduza, resuma, reescreva nem complete.
- Siga PREFERENCES quando se aplicarem.
- Plugins marcados com [KEYWORD MATCH] passaram por um pré-filtro de palavras-chave; considere-os primeiro.
Retorne apenas o objeto JSON, nada mais.""",
}

NO_PLUGINS_DESC = "No plugins available."


def build_selection_request(
    *,
    task: str,
    user_said: str,
    active_tasks: list[str],
    preferences: str,
) -> str:
    """Build the user message of the fine selection.

    The labels are fixed English words the system text refers to. Parts with
    nothing to say are left out.
    """
    lines = [f"TASK: {task}", f"USER_SAID: {user_said or task}"]
    if active_tasks:
        lines.append("ACTIVE_TASKS:")
        lines.extend(f"{number}. {row}" for number, row in enumerate(active_tasks, 1))
    if preferences:
        lines.append("PREFERENCES:")
        lines.append(preferences)
    return "\n".join(lines)


# =====================================================================
# ======= Coarse screening (small model, plugin ids only) =======
# =====================================================================
# Used when the descriptions of all plugins exceed the two-stage threshold,
# and once in the first round when nothing matches locally and no task agent can
# be started. The text is the analyzer's coarse-screening prompt, moved here
# unchanged when the analyzer was removed.

TASK_BRAIN_COARSE_SCREEN_PROMPT = {
    'zh': """你是一个agentic automation assessment agent, 粗筛阶段。根据用户请求，从以下插件列表中选出所有可能相关的插件ID。

可用插件（id: 简短描述）：
{plugin_summaries}

用户请求：{user_text}

指令：返回一个 JSON 数组，包含所有可能相关的插件ID。如果没有相关插件，返回空数组 []。
只返回 JSON 数组，不要其他内容。""",

    'zh-TW': """你是一個agentic automation assessment agent, 粗篩階段。根據使用者的請求，從以下外掛清單中選出所有可能相關的外掛 ID。

可用外掛（id: 簡短描述）：
{plugin_summaries}

使用者請求：{user_text}

指令：回傳一個 JSON 陣列，包含所有可能相關的外掛 ID。如果沒有相關的外掛，就回傳空陣列 []。
只回傳 JSON 陣列，不要其他內容。""",

    'en': """You are an agentic automation assessment agent, coarse screening stage. Given the user's request, select ALL possibly relevant plugin IDs from the list below.

Available plugins (id: brief description):
{plugin_summaries}

User request: {user_text}

Instructions: Return a JSON array of all possibly relevant plugin IDs. If none are relevant, return [].
Return ONLY the JSON array, nothing else.""",

    'ja': """あなたはagentic automation assessment agent, 粗選別段階です。ユーザーのリクエストに基づき、以下のプラグインリストから関連する可能性のあるすべてのプラグインIDを選択してください。

利用可能なプラグイン（id: 簡潔な説明）：
{plugin_summaries}

ユーザーリクエスト：{user_text}

指示：関連する可能性のあるすべてのプラグインIDを含むJSON配列を返してください。該当なしの場合は空配列 [] を返してください。
JSON配列のみ返してください。""",

    'ko': """당신은 agentic automation assessment agent, 粗선별 단계입니다. 사용자의 요청에 따라 아래 플러그인 목록에서 관련 가능성이 있는 모든 플러그인 ID를 선택하세요.

사용 가능한 플러그인 (id: 간단한 설명):
{plugin_summaries}

사용자 요청: {user_text}

지침: 관련 가능성이 있는 모든 플러그인 ID를 포함하는 JSON 배열을 반환하세요. 해당 없으면 빈 배열 []을 반환하세요.
JSON 배열만 반환하세요.""",

    'ru': """Вы agentic automation assessment agent, этап грубого отбора. На основе запроса пользователя выберите ВСЕ возможно релевантные ID плагинов из списка ниже.

Доступные плагины (id: краткое описание):
{plugin_summaries}

Запрос пользователя: {user_text}

Инструкции: Верните JSON-массив всех возможно релевантных ID плагинов. Если нет релевантных, верните [].
Верните ТОЛЬКО JSON-массив, ничего больше.""",
    'es': """Eres un agentic automation assessment agent en etapa de filtrado grueso. Dada la solicitud del usuario, selecciona TODOS los IDs de plugins posiblemente relevantes de la lista siguiente.

Plugins disponibles (id: descripción breve):
{plugin_summaries}

Solicitud del usuario: {user_text}

Instrucciones: Devuelve un array JSON con todos los IDs de plugins posiblemente relevantes. Si ninguno es relevante, devuelve [].
Devuelve SOLO el array JSON, nada más.""",
    'pt': """Você é um agentic automation assessment agent na etapa de triagem grosseira. Dada a solicitação do usuário, selecione TODOS os IDs de plugins possivelmente relevantes da lista abaixo.

Plugins disponíveis (id: breve descrição):
{plugin_summaries}

Solicitação do usuário: {user_text}

Instruções: Retorne um array JSON com todos os IDs de plugins possivelmente relevantes. Se nenhum for relevante, retorne [].
Retorne SOMENTE o array JSON, nada mais.""",
}

# =====================================================================
# ======= Receipts (tool results for the main dialogue model) =======
# =====================================================================

COMMISSION_STATUS_WORDS = {
    'zh': {"queued": "排队中", "working": "进行中", "waiting_user": "等用户回话", "done": "已完成",
           "blocked": "卡住了", "failed": "没办成", "cancelled": "已叫停"},
    'zh-TW': {"queued": "排隊中", "working": "進行中", "waiting_user": "等使用者回話", "done": "已完成",
              "blocked": "卡住了", "failed": "沒辦成", "cancelled": "已叫停"},
    'en': {"queued": "queued", "working": "in progress", "waiting_user": "waiting for the user", "done": "done",
           "blocked": "blocked", "failed": "failed", "cancelled": "stopped"},
    'ja': {"queued": "順番待ち", "working": "進行中", "waiting_user": "ユーザーの返事待ち", "done": "完了",
           "blocked": "行き詰まり", "failed": "失敗", "cancelled": "中止済み"},
    'ko': {"queued": "대기 중", "working": "진행 중", "waiting_user": "사용자 답변 대기", "done": "완료",
           "blocked": "막힘", "failed": "실패", "cancelled": "중지됨"},
    'ru': {"queued": "в очереди", "working": "выполняется", "waiting_user": "ждёт ответа пользователя", "done": "готово",
           "blocked": "застряло", "failed": "не удалось", "cancelled": "остановлено"},
    'es': {"queued": "en cola", "working": "en curso", "waiting_user": "esperando al usuario", "done": "hecho",
           "blocked": "bloqueado", "failed": "fallido", "cancelled": "detenido"},
    'pt': {"queued": "na fila", "working": "em andamento", "waiting_user": "aguardando o usuário", "done": "concluído",
           "blocked": "travado", "failed": "falhou", "cancelled": "interrompido"},
}

RECEIPT_TEXTS = {
    'zh': {
        "accepted": "已受理：{title}",
        "received": "收下了，正在判断怎么办。",
        "duplicate": "这件事已经在办了：{title}（{status}）",
        "no_capability": "办不了：{reason}",
        "stopped": "已叫停：{title}",
        "told": "已转告：{title}",
        "answered": "已把回答交给：{title}",
        "ambiguous": "对不上是哪一件，先问清楚再说。候选：\n{candidates}",
        "nothing_active": "现在没有在办的事。",
        "unclear_answer": "{title} 在等回话，但这句话看不出是同意还是拒绝。可以回答：始终允许、仅本会话允许、本次允许、拒绝。",
    },
    'zh-TW': {
        "accepted": "已受理：{title}",
        "received": "收下了，正在判斷怎麼辦。",
        "duplicate": "這件事已經在辦了：{title}（{status}）",
        "no_capability": "辦不了：{reason}",
        "stopped": "已叫停：{title}",
        "told": "已轉告：{title}",
        "answered": "已把回答交給：{title}",
        "ambiguous": "對不上是哪一件，先問清楚再說。候選：\n{candidates}",
        "nothing_active": "現在沒有在辦的事。",
        "unclear_answer": "{title} 在等回話，但這句話看不出是同意還是拒絕。可以回答：始終允許、僅本會話允許、本次允許、拒絕。",
    },
    'en': {
        "accepted": "Accepted: {title}",
        "received": "Received; working out how to handle it.",
        "duplicate": "Already under way: {title} ({status})",
        "no_capability": "Cannot be done: {reason}",
        "stopped": "Stopped: {title}",
        "told": "Passed on to: {title}",
        "answered": "Answer handed to: {title}",
        "ambiguous": "It is unclear which task is meant; ask before going on. Candidates:\n{candidates}",
        "nothing_active": "Nothing is under way right now.",
        "unclear_answer": "{title} is waiting for an answer, but this does not say yes or no. Possible answers: always allow, allow for this session, allow this once, deny.",
    },
    'ja': {
        "accepted": "受け付けました：{title}",
        "received": "受け取りました。どう進めるか判断しています。",
        "duplicate": "この用件はすでに進めています：{title}（{status}）",
        "no_capability": "対応できません：{reason}",
        "stopped": "中止しました：{title}",
        "told": "伝えました：{title}",
        "answered": "回答を渡しました：{title}",
        "ambiguous": "どの用件のことか分かりません。先に確認してください。候補：\n{candidates}",
        "nothing_active": "いま進めている用件はありません。",
        "unclear_answer": "{title} は返事を待っていますが、この言葉からは許可か拒否か分かりません。答え方：常に許可、このセッションだけ許可、今回だけ許可、拒否。",
    },
    'ko': {
        "accepted": "접수했습니다: {title}",
        "received": "받았습니다. 어떻게 처리할지 판단하는 중입니다.",
        "duplicate": "이 일은 이미 진행 중입니다: {title} ({status})",
        "no_capability": "처리할 수 없습니다: {reason}",
        "stopped": "중지했습니다: {title}",
        "told": "전달했습니다: {title}",
        "answered": "답변을 넘겼습니다: {title}",
        "ambiguous": "어느 일을 말하는지 알 수 없습니다. 먼저 확인하세요. 후보:\n{candidates}",
        "nothing_active": "지금 진행 중인 일이 없습니다.",
        "unclear_answer": "{title}이(가) 답을 기다리고 있지만, 이 말로는 허락인지 거절인지 알 수 없습니다. 가능한 답: 항상 허용, 이번 세션만 허용, 이번만 허용, 거부.",
    },
    'ru': {
        "accepted": "Принято: {title}",
        "received": "Получено; решаю, как это сделать.",
        "duplicate": "Это уже выполняется: {title} ({status})",
        "no_capability": "Сделать нельзя: {reason}",
        "stopped": "Остановлено: {title}",
        "told": "Передано: {title}",
        "answered": "Ответ передан: {title}",
        "ambiguous": "Непонятно, о какой задаче речь; сначала уточните. Варианты:\n{candidates}",
        "nothing_active": "Сейчас ничего не выполняется.",
        "unclear_answer": "{title} ждёт ответа, но из этих слов неясно, да или нет. Возможные ответы: разрешать всегда, разрешить на эту сессию, разрешить только в этот раз, отказать.",
    },
    'es': {
        "accepted": "Aceptado: {title}",
        "received": "Recibido; viendo cómo resolverlo.",
        "duplicate": "Esto ya está en marcha: {title} ({status})",
        "no_capability": "No se puede hacer: {reason}",
        "stopped": "Detenido: {title}",
        "told": "Transmitido a: {title}",
        "answered": "Respuesta entregada a: {title}",
        "ambiguous": "No está claro a qué tarea se refiere; pregunta antes de seguir. Candidatas:\n{candidates}",
        "nothing_active": "Ahora mismo no hay nada en marcha.",
        "unclear_answer": "{title} espera una respuesta, pero esto no dice ni sí ni no. Respuestas posibles: permitir siempre, permitir en esta sesión, permitir solo esta vez, denegar.",
    },
    'pt': {
        "accepted": "Aceito: {title}",
        "received": "Recebido; vendo como resolver.",
        "duplicate": "Isso já está em andamento: {title} ({status})",
        "no_capability": "Não dá para fazer: {reason}",
        "stopped": "Interrompido: {title}",
        "told": "Repassado para: {title}",
        "answered": "Resposta entregue a: {title}",
        "ambiguous": "Não está claro de qual tarefa se trata; pergunte antes de continuar. Candidatas:\n{candidates}",
        "nothing_active": "Não há nada em andamento agora.",
        "unclear_answer": "{title} aguarda uma resposta, mas isto não diz sim nem não. Respostas possíveis: permitir sempre, permitir nesta sessão, permitir só desta vez, negar.",
    },
}

NO_CAPABILITY_REASONS = {
    'zh': {"disabled": "猫爪没有开", "not_installed": "没有装能办这件事的能力", "not_enabled": "相关的能力装了但没有开",
           "denied": "权限设置里禁止了", "no_match": "没有找到能办这件事的能力",
           "stopped": "刚才已经全部叫停了"},
    'zh-TW': {"disabled": "貓爪沒有開", "not_installed": "沒有裝能辦這件事的能力", "not_enabled": "相關的能力裝了但沒有開",
              "denied": "權限設定裡禁止了", "no_match": "沒有找到能辦這件事的能力",
              "stopped": "剛才已經全部叫停了"},
    'en': {"disabled": "NekoClaw is switched off", "not_installed": "nothing that can do this is installed",
           "not_enabled": "the capability is installed but switched off", "denied": "the permission settings forbid it",
           "no_match": "no capability that can do this was found", "stopped": "everything was stopped just now"},
    'ja': {"disabled": "猫爪がオフになっています", "not_installed": "これができる能力が入っていません",
           "not_enabled": "能力は入っていますがオフになっています", "denied": "権限設定で禁止されています",
           "no_match": "これができる能力が見つかりませんでした", "stopped": "たった今、すべて中止されました"},
    'ko': {"disabled": "네코클로가 꺼져 있습니다", "not_installed": "이 일을 할 수 있는 능력이 설치되어 있지 않습니다",
           "not_enabled": "능력이 설치되어 있지만 꺼져 있습니다", "denied": "권한 설정에서 금지되어 있습니다",
           "no_match": "이 일을 할 수 있는 능력을 찾지 못했습니다", "stopped": "방금 모두 중지되었습니다"},
    'ru': {"disabled": "NekoClaw выключен", "not_installed": "не установлено ничего, что может это сделать",
           "not_enabled": "возможность установлена, но выключена", "denied": "это запрещено настройками разрешений",
           "no_match": "не найдено возможности, которая может это сделать", "stopped": "только что всё было остановлено"},
    'es': {"disabled": "NekoClaw está apagado", "not_installed": "no hay instalado nada que pueda hacerlo",
           "not_enabled": "la capacidad está instalada pero apagada", "denied": "los permisos lo prohíben",
           "no_match": "no se encontró ninguna capacidad que pueda hacerlo", "stopped": "se acaba de parar todo"},
    'pt': {"disabled": "o NekoClaw está desligado", "not_installed": "não há nada instalado que consiga fazer isso",
           "not_enabled": "a capacidade está instalada, mas desligada", "denied": "as permissões proíbem",
           "no_match": "nenhuma capacidade que consiga fazer isso foi encontrada", "stopped": "tudo acabou de ser parado"},
}

QUERY_TEXTS = {
    'zh': {"active": "在办的事：", "recent": "最近办完的：", "empty": "没有任务记录。", "empty_asked": "没有任务记录。查询只看已经交办的任务；要查资料、查状态或办事，请用派发。",
           "can_plugin": "能办：有对得上的插件（{names}）。", "can_task_agent": "能办：可以交给使魔去做。",
           "cannot": "办不了：{reason}", "asked": "在等用户回答：{question}", "result": "结果：{summary}",
           "unsure": "没找到现成的能力，真要办可以派发试试。"},
    'zh-TW': {"active": "在辦的事：", "recent": "最近辦完的：", "empty": "沒有任務記錄。", "empty_asked": "沒有任務記錄。查詢只看已經交辦的任務；要查資料、查狀態或辦事，請用派發。",
              "can_plugin": "能辦：有對得上的外掛（{names}）。", "can_task_agent": "能辦：可以交給使魔去做。",
              "cannot": "辦不了：{reason}", "asked": "在等使用者回答：{question}", "result": "結果：{summary}",
              "unsure": "沒找到現成的能力，真要辦可以派發試試。"},
    'en': {"active": "Under way:", "recent": "Recently finished:", "empty": "There is no task on record.", "empty_asked": "There is no task on record. A query only looks at tasks already handed over; to look something up, check a status or get something done, dispatch it.",
           "can_plugin": "Can be done: matching plugins exist ({names}).", "can_task_agent": "Can be done: a familiar can take it.",
           "cannot": "Cannot be done: {reason}", "asked": "waiting for the user to answer: {question}", "result": "result: {summary}",
           "unsure": "No ready capability was found; to really get it done, dispatch it and see."},
    'ja': {"active": "進めている用件：", "recent": "最近終わった用件：", "empty": "用件の記録はありません。", "empty_asked": "用件の記録はありません。照会が見るのは、すでに任せた用件だけです。調べもの、状態の確認、用件の実行は、タスクとして任せてください。",
           "can_plugin": "対応できます：合うプラグインがあります（{names}）。", "can_task_agent": "対応できます：使い魔に任せられます。",
           "cannot": "対応できません：{reason}", "asked": "ユーザーの回答待ち：{question}", "result": "結果：{summary}",
           "unsure": "すぐ使える能力は見つかりませんでした。本当に片付けたいなら任せてみてください。"},
    'ko': {"active": "진행 중인 일:", "recent": "최근 끝난 일:", "empty": "작업 기록이 없습니다.", "empty_asked": "작업 기록이 없습니다. 조회는 이미 맡긴 작업만 봅니다. 자료 찾기, 상태 확인, 일 처리는 작업으로 맡기세요.",
           "can_plugin": "처리할 수 있습니다: 맞는 플러그인이 있습니다({names}).", "can_task_agent": "처리할 수 있습니다: 사역마에게 맡길 수 있습니다.",
           "cannot": "처리할 수 없습니다: {reason}", "asked": "사용자 답변 대기: {question}", "result": "결과: {summary}",
           "unsure": "바로 쓸 수 있는 능력은 찾지 못했습니다. 정말 처리하려면 맡겨 보세요."},
    'ru': {"active": "Выполняется:", "recent": "Недавно завершено:", "empty": "Записей о задачах нет.", "empty_asked": "Записей о задачах нет. Запрос показывает только уже порученные задачи; чтобы что-то найти, проверить состояние или сделать, поручите задачу.",
           "can_plugin": "Сделать можно: есть подходящие плагины ({names}).", "can_task_agent": "Сделать можно: это можно поручить фамильяру.",
           "cannot": "Сделать нельзя: {reason}", "asked": "ждёт ответа пользователя: {question}", "result": "результат: {summary}",
           "unsure": "Готовой возможности не нашлось; чтобы действительно сделать это, попробуйте поручить."},
    'es': {"active": "En marcha:", "recent": "Terminadas hace poco:", "empty": "No hay tareas registradas.", "empty_asked": "No hay tareas registradas. La consulta solo mira las tareas ya encargadas; para buscar algo, comprobar un estado o hacer algo, encárgalo.",
           "can_plugin": "Se puede hacer: hay plugins que encajan ({names}).", "can_task_agent": "Se puede hacer: un familiar puede encargarse.",
           "cannot": "No se puede hacer: {reason}", "asked": "esperando la respuesta del usuario: {question}", "result": "resultado: {summary}",
           "unsure": "No se encontró una capacidad lista; para hacerlo de verdad, prueba a encargarlo."},
    'pt': {"active": "Em andamento:", "recent": "Concluídas há pouco:", "empty": "Não há tarefas registradas.", "empty_asked": "Não há tarefas registradas. A consulta só olha as tarefas já encarregadas; para pesquisar algo, verificar um estado ou fazer algo, encarregue.",
           "can_plugin": "Dá para fazer: há plugins que servem ({names}).", "can_task_agent": "Dá para fazer: um familiar pode cuidar disso.",
           "cannot": "Não dá para fazer: {reason}", "asked": "aguardando a resposta do usuário: {question}", "result": "resultado: {summary}",
           "unsure": "Nenhuma capacidade pronta foi encontrada; para fazer de verdade, tente encarregar."},
}

# How the answers to a permission prompt are named when the question is put to
# her. Each name reads back as its own choice through the patterns below
# (``tests/unit/nekoclaw/test_host_decision.py`` holds them to that).
PERMISSION_OPTION_NAMES = {
    'zh': {"always": "始终允许", "session": "仅本会话允许", "once": "本次允许", "deny": "拒绝"},
    'zh-TW': {"always": "始終允許", "session": "僅本會話允許", "once": "本次允許", "deny": "拒絕"},
    'en': {"always": "always allow", "session": "allow for this session", "once": "allow this once", "deny": "deny"},
    'ja': {"always": "常に許可", "session": "このセッションだけ許可", "once": "今回だけ許可", "deny": "拒否"},
    'ko': {"always": "항상 허용", "session": "이번 세션만 허용", "once": "이번만 허용", "deny": "거부"},
    'ru': {"always": "разрешать всегда", "session": "разрешить на эту сессию", "once": "разрешить только в этот раз",
           "deny": "отказать"},
    'es': {"always": "permitir siempre", "session": "permitir en esta sesión", "once": "permitir solo esta vez",
           "deny": "denegar"},
    'pt': {"always": "permitir sempre", "session": "permitir nesta sessão", "once": "permitir só desta vez",
           "deny": "negar"},
}

# What she is told, along with her next turn, about a commission that was
# under way when the host process ended.
HOST_RESTART_FAILED_TEXT = {
    'zh': "猫爪中途重启了，这件事没有办完。",
    'zh-TW': "貓爪中途重新啟動了，這件事沒有辦完。",
    'en': "NekoClaw restarted in the middle of this, so it was not finished.",
    'ja': "途中で猫爪が再起動したため、この用件は終わっていません。",
    'ko': "도중에 네코클로가 다시 시작되어 이 일은 끝나지 않았습니다.",
    'ru': "NekoClaw перезапустился посреди этой задачи, она не была завершена.",
    'es': "NekoClaw se reinició a mitad de esto, así que no se terminó.",
    'pt': "O NekoClaw reiniciou no meio disto, então não foi concluído.",
}

# =====================================================================
# ======= Turn check (what she said against what was done) =======
# =====================================================================
# Runs after a turn in which she called no task tool, and only when the turn
# looks like a request to get something done. One call of the small model.
# The identifiers "none" / "unable" / "promised" / "claimed_done" and the keys
# "verdict" / "task" are parsed and stay as they are in every language.

TURN_CHECK_PROMPT = {
    'zh': """你是一个agentic automation assessment agent, 言行核对阶段。下面给出用户这一轮说的话和角色的回复。这一轮角色没有调用任何办事的工具。判断回复属于哪一种，只选一个：
- "none"：用户没有要求对话之外的实际操作；或角色只是在聊天、反问、凭自己知道的回答；或角色有意不办（不愿意、觉得不合适）。
- "unable"：用户要求了实际操作（操作电脑、浏览器、设备、插件，查实时信息，设提醒等），角色说自己做不到或没有这个能力。
- "promised"：用户要求了实际操作，角色答应去办，或说正在办。
- "claimed_done"：角色说某个实际操作已经办完，或把办出来的结果当成事实说了。
不要判断这件事办不办得到。
只返回 JSON：{"verdict": "...", "task": "要办的事，一句话，用用户的语言；none 时留空"}""",

    'zh-TW': """你是一個agentic automation assessment agent, 言行核對階段。下面給出使用者這一輪說的話和角色的回覆。這一輪角色沒有呼叫任何辦事的工具。判斷回覆屬於哪一種，只選一個：
- "none"：使用者沒有要求對話之外的實際操作；或角色只是在聊天、反問、憑自己知道的回答；或角色有意不辦（不願意、覺得不合適）。
- "unable"：使用者要求了實際操作（操作電腦、瀏覽器、裝置、外掛，查即時資訊，設提醒等），角色說自己做不到或沒有這個能力。
- "promised"：使用者要求了實際操作，角色答應去辦，或說正在辦。
- "claimed_done"：角色說某個實際操作已經辦完，或把辦出來的結果當成事實說了。
不要判斷這件事辦不辦得到。
只回傳 JSON：{"verdict": "...", "task": "要辦的事，一句話，用使用者的語言；none 時留空"}""",

    'en': """You are an agentic automation assessment agent, checking words against deeds. Below are what the user said this turn and the character's reply. The character called no task tool this turn. Decide which one the reply is; pick exactly one:
- "none": the user asked for no real action outside the conversation; or the character only chatted, asked back, or answered from what she knows; or she declined on purpose (unwilling, finds it inappropriate).
- "unable": the user asked for a real action (operating the computer, a browser, a device, a plugin, looking up live information, setting a reminder ...) and the character said she cannot do it or has no way to.
- "promised": the user asked for a real action and the character agreed to do it, or said she is doing it.
- "claimed_done": the character said a real action has been carried out, or stated its result as fact.
Do not judge whether the action is possible.
Return only JSON: {"verdict": "...", "task": "the action in one sentence, in the user's language; empty for none"}""",

    'ja': """あなたはagentic automation assessment agent, 言行照合段階です。以下はこのターンのユーザーの発言とキャラクターの返答です。このターン、キャラクターは用件を進めるツールを呼んでいません。返答がどれに当たるか、一つだけ選んでください：
- "none"：ユーザーは会話の外の実際の操作を求めていない。またはキャラクターは雑談・聞き返し・自分の知識での回答をしただけ。またはあえて断った（やりたくない、適切でない）。
- "unable"：ユーザーが実際の操作（パソコン・ブラウザ・機器・プラグインの操作、最新情報の検索、リマインダー設定など）を求め、キャラクターが「できない」「その手段がない」と言った。
- "promised"：ユーザーが実際の操作を求め、キャラクターが引き受けた、または今やっていると言った。
- "claimed_done"：キャラクターが実際の操作はもう済んだと言った、またはその結果を事実として述べた。
実行できるかどうかは判断しないでください。
JSON のみ返してください：{"verdict": "...", "task": "求められた操作を一文で、ユーザーの言語で。none のときは空"}""",

    'ko': """당신은 agentic automation assessment agent, 언행 대조 단계입니다. 아래는 이번 턴에 사용자가 한 말과 캐릭터의 답변입니다. 이번 턴에 캐릭터는 일을 처리하는 도구를 호출하지 않았습니다. 답변이 어느 것에 해당하는지 하나만 고르세요:
- "none": 사용자가 대화 밖의 실제 작업을 요청하지 않았다. 또는 캐릭터가 잡담, 되묻기, 아는 내용으로 답하기만 했다. 또는 일부러 하지 않겠다고 했다(내키지 않음, 부적절하다고 봄).
- "unable": 사용자가 실제 작업(컴퓨터·브라우저·기기·플러그인 조작, 실시간 정보 조회, 알림 설정 등)을 요청했고, 캐릭터가 할 수 없다거나 방법이 없다고 말했다.
- "promised": 사용자가 실제 작업을 요청했고, 캐릭터가 하겠다고 했거나 하고 있다고 말했다.
- "claimed_done": 캐릭터가 실제 작업이 이미 끝났다고 했거나 그 결과를 사실로 말했다.
그 일이 가능한지는 판단하지 마세요.
JSON만 반환하세요: {"verdict": "...", "task": "요청된 작업을 한 문장으로, 사용자의 언어로. none이면 비움"}""",

    'ru': """Вы agentic automation assessment agent, этап сверки слов и дел. Ниже — что пользователь сказал в этом ходе и ответ персонажа. В этом ходе персонаж не вызывал инструментов для выполнения задач. Определите, чем является ответ; выберите ровно одно:
- "none": пользователь не просил о реальном действии вне разговора; или персонаж только беседовал, переспрашивал, отвечал из того, что знает; или намеренно отказался (не хочет, считает неуместным).
- "unable": пользователь просил о реальном действии (управление компьютером, браузером, устройством, плагином, поиск актуальной информации, напоминание и т. п.), а персонаж сказал, что не может или не имеет такой возможности.
- "promised": пользователь просил о реальном действии, и персонаж согласился его выполнить или сказал, что выполняет.
- "claimed_done": персонаж сказал, что реальное действие уже выполнено, или изложил его результат как факт.
Не судите, выполнимо ли действие.
Верните только JSON: {"verdict": "...", "task": "действие одним предложением на языке пользователя; пусто для none"}""",

    'es': """Eres un agentic automation assessment agent en etapa de contraste entre lo dicho y lo hecho. Abajo están lo que el usuario dijo en este turno y la respuesta del personaje. En este turno el personaje no llamó a ninguna herramienta de tareas. Decide cuál es la respuesta; elige exactamente una:
- "none": el usuario no pidió ninguna acción real fuera de la conversación; o el personaje solo charló, preguntó de vuelta o respondió con lo que sabe; o se negó a propósito (no quiere, le parece inapropiado).
- "unable": el usuario pidió una acción real (manejar el ordenador, un navegador, un dispositivo, un plugin, buscar información actual, poner un recordatorio...) y el personaje dijo que no puede o que no tiene cómo.
- "promised": el usuario pidió una acción real y el personaje aceptó hacerla, o dijo que la está haciendo.
- "claimed_done": el personaje dijo que una acción real ya se hizo, o dio su resultado como un hecho.
No juzgues si la acción es posible.
Devuelve solo JSON: {"verdict": "...", "task": "la acción en una frase, en el idioma del usuario; vacío para none"}""",

    'pt': """Você é um agentic automation assessment agent na etapa de conferir o dito com o feito. Abaixo estão o que o usuário disse neste turno e a resposta da personagem. Neste turno a personagem não chamou nenhuma ferramenta de tarefas. Decida qual é a resposta; escolha exatamente uma:
- "none": o usuário não pediu nenhuma ação real fora da conversa; ou a personagem só conversou, perguntou de volta ou respondeu com o que sabe; ou recusou de propósito (não quer, acha inadequado).
- "unable": o usuário pediu uma ação real (operar o computador, um navegador, um dispositivo, um plugin, buscar informação atual, criar um lembrete...) e a personagem disse que não consegue ou que não tem como.
- "promised": o usuário pediu uma ação real e a personagem aceitou fazer, ou disse que está fazendo.
- "claimed_done": a personagem disse que uma ação real já foi feita, ou deu o resultado como fato.
Não julgue se a ação é possível.
Retorne somente JSON: {"verdict": "...", "task": "a ação em uma frase, no idioma do usuário; vazio para none"}""",
}

TURN_CHECK_VERDICTS = ("none", "unable", "promised", "claimed_done")

# The user message of the turn check: the two sentences it compares.
TURN_CHECK_INPUT_TEMPLATE = "USER: {user_text}\nREPLY: {reply_text}"

# What she is told after a turn check. ``can_do`` / ``no_record`` /
# ``promised_failed`` wake her so she can put it right; ``filed`` comes along
# with her next turn.
TURN_CHECK_TEXTS = {
    'zh': {
        "can_do": "刚才你说「{task}」做不到，其实办得到。请自然地向用户更正，然后用任务派发工具去办。",
        "no_record": "刚才你说「{task}」已经办好了，但任务记录里没有这件事。请自然地向用户更正；真要办，就用任务派发工具。",
        "filed": "你刚才答应的事已经替你派发了。{receipt}",
        "promised_failed": "你刚才答应的事没能办成。{receipt} 请自然地向用户说明。",
    },
    'zh-TW': {
        "can_do": "剛才你說「{task}」做不到，其實辦得到。請自然地向使用者更正，然後用任務派發工具去辦。",
        "no_record": "剛才你說「{task}」已經辦好了，但任務記錄裡沒有這件事。請自然地向使用者更正；真要辦，就用任務派發工具。",
        "filed": "你剛才答應的事已經替你派發了。{receipt}",
        "promised_failed": "你剛才答應的事沒能辦成。{receipt} 請自然地向使用者說明。",
    },
    'en': {
        "can_do": "You just said \"{task}\" cannot be done; it can. Correct that with the user in a natural way, then use the task dispatch tool to get it done.",
        "no_record": "You just said \"{task}\" was done, but there is no record of that task. Correct that with the user in a natural way; to really get it done, use the task dispatch tool.",
        "filed": "What you just agreed to do has been dispatched for you. {receipt}",
        "promised_failed": "What you just agreed to do could not be done. {receipt} Tell the user so in a natural way.",
    },
    'ja': {
        "can_do": "さきほど「{task}」はできないと言いましたが、実際にはできます。ユーザーに自然に訂正してから、タスク依頼ツールで進めてください。",
        "no_record": "さきほど「{task}」は済んだと言いましたが、タスクの記録にその用件はありません。ユーザーに自然に訂正してください。本当に進めるならタスク依頼ツールを使ってください。",
        "filed": "さきほど引き受けた用件は、代わりに依頼しておきました。{receipt}",
        "promised_failed": "さきほど引き受けた用件は進められませんでした。{receipt} ユーザーに自然に伝えてください。",
    },
    'ko': {
        "can_do": "방금 \"{task}\"은(는) 할 수 없다고 했지만 실제로는 할 수 있습니다. 사용자에게 자연스럽게 정정한 뒤 작업 의뢰 도구로 처리하세요.",
        "no_record": "방금 \"{task}\"이(가) 끝났다고 했지만 작업 기록에 그 일이 없습니다. 사용자에게 자연스럽게 정정하세요. 정말 처리하려면 작업 의뢰 도구를 쓰세요.",
        "filed": "방금 하겠다고 한 일은 대신 의뢰해 두었습니다. {receipt}",
        "promised_failed": "방금 하겠다고 한 일은 처리하지 못했습니다. {receipt} 사용자에게 자연스럽게 알려 주세요.",
    },
    'ru': {
        "can_do": "Вы только что сказали, что «{task}» сделать нельзя, но это возможно. Естественно поправьте себя перед пользователем и поручите это через инструмент поручения задач.",
        "no_record": "Вы только что сказали, что «{task}» уже сделано, но в записях задач этого нет. Естественно поправьте себя перед пользователем; чтобы действительно сделать это, используйте инструмент поручения задач.",
        "filed": "То, что вы только что пообещали, уже поручено за вас. {receipt}",
        "promised_failed": "То, что вы только что пообещали, выполнить не удалось. {receipt} Естественно сообщите об этом пользователю.",
    },
    'es': {
        "can_do": "Acabas de decir que «{task}» no se puede hacer, pero sí se puede. Corrígelo con el usuario de forma natural y luego usa la herramienta de encargo de tareas para hacerlo.",
        "no_record": "Acabas de decir que «{task}» ya está hecho, pero no hay registro de esa tarea. Corrígelo con el usuario de forma natural; para hacerlo de verdad, usa la herramienta de encargo de tareas.",
        "filed": "Lo que acabas de aceptar hacer ya se encargó por ti. {receipt}",
        "promised_failed": "Lo que acabas de aceptar hacer no se pudo hacer. {receipt} Díselo al usuario de forma natural.",
    },
    'pt': {
        "can_do": "Você acabou de dizer que «{task}» não dá para fazer, mas dá. Corrija isso com o usuário de forma natural e depois use a ferramenta de encargo de tarefas para fazer.",
        "no_record": "Você acabou de dizer que «{task}» já foi feito, mas não há registro dessa tarefa. Corrija isso com o usuário de forma natural; para fazer de verdade, use a ferramenta de encargo de tarefas.",
        "filed": "O que você acabou de aceitar fazer já foi encarregado por você. {receipt}",
        "promised_failed": "O que você acabou de aceitar fazer não pôde ser feito. {receipt} Diga isso ao usuário de forma natural.",
    },
}

# =====================================================================
# ======= Questions put to the user through her =======
# =====================================================================
# A commission that waits for the user reaches her as one sentence she passes
# on. ``{options}`` lists the answers by their ``PERMISSION_OPTION_NAMES``.

QUESTION_TEXTS = {
    'zh': {
        "permission": "「{title}」要先得到用户批准才能继续：{what}。请转告用户，可以回答：{options}。用户回答后，用任务派发工具把回答交回来。",
        "ask": "「{title}」有问题要问用户：{question} 请转告用户；用户回答后，用任务派发工具把回答交回来。",
        "confirm": "「{title}」需要用户确认：{question} 请转告用户；用户回答后，用任务派发工具把回答交回来。",
        "options": "（选项：{items}）",
    },
    'zh-TW': {
        "permission": "「{title}」要先得到使用者批准才能繼續：{what}。請轉告使用者，可以回答：{options}。使用者回答後，用任務派發工具把回答交回來。",
        "ask": "「{title}」有問題要問使用者：{question} 請轉告使用者；使用者回答後，用任務派發工具把回答交回來。",
        "confirm": "「{title}」需要使用者確認：{question} 請轉告使用者；使用者回答後，用任務派發工具把回答交回來。",
        "options": "（選項：{items}）",
    },
    'en': {
        "permission": "\"{title}\" needs the user's approval before it can go on: {what}. Pass this on to the user; possible answers: {options}. Once the user has answered, hand the answer back with the task dispatch tool.",
        "ask": "\"{title}\" has a question for the user: {question} Pass it on to the user; once the user has answered, hand the answer back with the task dispatch tool.",
        "confirm": "\"{title}\" needs the user to confirm: {question} Pass it on to the user; once the user has answered, hand the answer back with the task dispatch tool.",
        "options": " (options: {items})",
    },
    'ja': {
        "permission": "「{title}」は続ける前にユーザーの許可が必要です：{what}。ユーザーに伝えてください。答え方：{options}。ユーザーが答えたら、タスク依頼ツールでその答えを渡してください。",
        "ask": "「{title}」からユーザーへの質問があります：{question} ユーザーに伝え、答えが出たらタスク依頼ツールで渡してください。",
        "confirm": "「{title}」はユーザーの確認が必要です：{question} ユーザーに伝え、答えが出たらタスク依頼ツールで渡してください。",
        "options": "（選択肢：{items}）",
    },
    'ko': {
        "permission": "\"{title}\"은(는) 계속하려면 사용자의 허락이 필요합니다: {what}. 사용자에게 전해 주세요. 가능한 답: {options}. 사용자가 답하면 작업 의뢰 도구로 그 답을 넘겨 주세요.",
        "ask": "\"{title}\"이(가) 사용자에게 물어볼 것이 있습니다: {question} 사용자에게 전하고, 답을 받으면 작업 의뢰 도구로 넘겨 주세요.",
        "confirm": "\"{title}\"은(는) 사용자의 확인이 필요합니다: {question} 사용자에게 전하고, 답을 받으면 작업 의뢰 도구로 넘겨 주세요.",
        "options": " (선택지: {items})",
    },
    'ru': {
        "permission": "Задаче «{title}» нужно разрешение пользователя, чтобы продолжить: {what}. Передайте это пользователю; возможные ответы: {options}. Когда пользователь ответит, передайте ответ через инструмент поручения задач.",
        "ask": "У задачи «{title}» есть вопрос к пользователю: {question} Передайте его пользователю; когда он ответит, передайте ответ через инструмент поручения задач.",
        "confirm": "Задаче «{title}» нужно подтверждение пользователя: {question} Передайте это пользователю; когда он ответит, передайте ответ через инструмент поручения задач.",
        "options": " (варианты: {items})",
    },
    'es': {
        "permission": "«{title}» necesita la aprobación del usuario para seguir: {what}. Transmíteselo al usuario; respuestas posibles: {options}. Cuando el usuario responda, entrega la respuesta con la herramienta de encargo de tareas.",
        "ask": "«{title}» tiene una pregunta para el usuario: {question} Transmítesela al usuario; cuando responda, entrega la respuesta con la herramienta de encargo de tareas.",
        "confirm": "«{title}» necesita que el usuario confirme: {question} Transmíteselo al usuario; cuando responda, entrega la respuesta con la herramienta de encargo de tareas.",
        "options": " (opciones: {items})",
    },
    'pt': {
        "permission": "«{title}» precisa da aprovação do usuário para continuar: {what}. Repasse isso ao usuário; respostas possíveis: {options}. Quando o usuário responder, entregue a resposta com a ferramenta de encargo de tarefas.",
        "ask": "«{title}» tem uma pergunta para o usuário: {question} Repasse ao usuário; quando ele responder, entregue a resposta com a ferramenta de encargo de tarefas.",
        "confirm": "«{title}» precisa que o usuário confirme: {question} Repasse isso ao usuário; quando ele responder, entregue a resposta com a ferramenta de encargo de tarefas.",
        "options": " (opções: {items})",
    },
}

# Stands where the middle of a long command or path was left out of a
# permission question, so the user is told that it is not shown in full.
PERMISSION_DETAIL_CUT_MARK = {
    'zh': " …[中间省略，未完整显示]… ",
    'zh-TW': " …[中間省略，未完整顯示]… ",
    'en': " …[middle left out, not shown in full]… ",
    'ja': " …[中略、全文は表示されていません]… ",
    'ko': " …[중간 생략, 전체가 표시되지 않음]… ",
    'ru': " …[середина опущена, показано не полностью]… ",
    'es': " …[parte central omitida, no se muestra completo]… ",
    'pt': " …[parte central omitida, não aparece por inteiro]… ",
}

# What a permission prompt is about, by category (``permissions.DEFAULT_MODES``).
# ``{detail}`` is the command, the path, the server or the capability's title.
PERMISSION_CATEGORY_TEXTS = {
    'zh': {"run_command": "运行命令 {detail}", "outside_write": "在工作目录以外写文件 {detail}",
           "outside_read": "读取工作目录以外的文件 {detail}", "workdir_files": "读写工作目录里的文件 {detail}",
           "keyboard_mouse": "操作键盘和鼠标", "browser": "操作浏览器", "mcp_server": "使用 MCP 服务 {detail}",
           "plugin_entry": "调用 {detail}", "other": "使用 {detail}"},
    'zh-TW': {"run_command": "執行命令 {detail}", "outside_write": "在工作目錄以外寫檔案 {detail}",
              "outside_read": "讀取工作目錄以外的檔案 {detail}", "workdir_files": "讀寫工作目錄裡的檔案 {detail}",
              "keyboard_mouse": "操作鍵盤和滑鼠", "browser": "操作瀏覽器", "mcp_server": "使用 MCP 服務 {detail}",
              "plugin_entry": "呼叫 {detail}", "other": "使用 {detail}"},
    'en': {"run_command": "run the command {detail}", "outside_write": "write the file {detail} outside the working directory",
           "outside_read": "read the file {detail} outside the working directory",
           "workdir_files": "read or write {detail} in the working directory",
           "keyboard_mouse": "operate the keyboard and the mouse", "browser": "operate the browser",
           "mcp_server": "use the MCP server {detail}", "plugin_entry": "call {detail}", "other": "use {detail}"},
    'ja': {"run_command": "コマンド {detail} の実行", "outside_write": "作業ディレクトリ外のファイル {detail} への書き込み",
           "outside_read": "作業ディレクトリ外のファイル {detail} の読み取り", "workdir_files": "作業ディレクトリ内の {detail} の読み書き",
           "keyboard_mouse": "キーボードとマウスの操作", "browser": "ブラウザの操作", "mcp_server": "MCP サーバー {detail} の利用",
           "plugin_entry": "{detail} の呼び出し", "other": "{detail} の利用"},
    'ko': {"run_command": "명령 {detail} 실행", "outside_write": "작업 디렉터리 밖의 파일 {detail} 쓰기",
           "outside_read": "작업 디렉터리 밖의 파일 {detail} 읽기", "workdir_files": "작업 디렉터리 안의 {detail} 읽기/쓰기",
           "keyboard_mouse": "키보드와 마우스 조작", "browser": "브라우저 조작", "mcp_server": "MCP 서버 {detail} 사용",
           "plugin_entry": "{detail} 호출", "other": "{detail} 사용"},
    'ru': {"run_command": "выполнить команду {detail}", "outside_write": "записать файл {detail} вне рабочего каталога",
           "outside_read": "прочитать файл {detail} вне рабочего каталога", "workdir_files": "читать или записывать {detail} в рабочем каталоге",
           "keyboard_mouse": "управлять клавиатурой и мышью", "browser": "управлять браузером",
           "mcp_server": "использовать MCP-сервер {detail}", "plugin_entry": "вызвать {detail}", "other": "использовать {detail}"},
    'es': {"run_command": "ejecutar el comando {detail}", "outside_write": "escribir el archivo {detail} fuera del directorio de trabajo",
           "outside_read": "leer el archivo {detail} fuera del directorio de trabajo",
           "workdir_files": "leer o escribir {detail} en el directorio de trabajo",
           "keyboard_mouse": "manejar el teclado y el ratón", "browser": "manejar el navegador",
           "mcp_server": "usar el servidor MCP {detail}", "plugin_entry": "llamar a {detail}", "other": "usar {detail}"},
    'pt': {"run_command": "executar o comando {detail}", "outside_write": "gravar o arquivo {detail} fora do diretório de trabalho",
           "outside_read": "ler o arquivo {detail} fora do diretório de trabalho",
           "workdir_files": "ler ou gravar {detail} no diretório de trabalho",
           "keyboard_mouse": "operar o teclado e o mouse", "browser": "operar o navegador",
           "mcp_server": "usar o servidor MCP {detail}", "plugin_entry": "chamar {detail}", "other": "usar {detail}"},
}

# =====================================================================
# ======= Cue patterns (matched without a model) =======
# =====================================================================
# Regular expressions, case-insensitive, covering the eight interface
# languages in one list each. They only say what kind of sentence this is;
# which commission it is about is matched against the ledger.

STOP_CUE_PATTERNS = (
    # Chinese, Simplified and Traditional.
    r"叫停|停止|停下|停掉|先停|中止|终止|終止|取消|算了|别.{0,6}了|別.{0,6}了|不用.{0,4}了|不要.{0,4}了|不必.{0,4}了",
    # English.
    r"\b(?:stop|cancel|abort|halt|never\s?mind|forget\s+it|call\s+it\s+off|don'?t\s+bother)\b",
    # Japanese.
    r"止めて|やめて|やめる|中止|キャンセル|取り消",
    # Korean.
    r"그만|취소|중지|멈춰|멈추",
    # Russian.
    r"останов|отмен|прекрат|хватит|\bстоп\b|не\s+надо",
    # Spanish and Portuguese.
    r"\b(?:det[eé]n\w*|cancel\w*|olv[ií]da\w*|d[eé]jalo|ya\s+no|pare|para\s+com|esquece\w*|deixa\s+pra\s+l[aá])\b",
)

# Words that point at something without naming it ("that one", "it", "please").
# They are set aside before a sentence is compared with the commissions, so
# "stop that" reads as a stop request that names nothing.
VAGUE_REFERENCE_PATTERNS = (
    r"那个|那個|这个|這個|那件事|这件事|這件事|那件|这件|這件|任务|任務|事情|全部|所有|都|先|吧|啊|呀|了|的",
    r"\b(?:that|this|it|them|those|these|one|ones|thing|things|task|tasks|everything|all|now|please|the|doing|for\s+me)\b",
    r"それ|これ|あれ|全部|もう|ください|して|を|は",
    r"그거|이거|저거|전부|모두|제발|이제|해|줘",
    r"это|то|всё|все|пожалуйста|уже|сейчас",
    r"\b(?:eso|esto|aquello|todo|por\s+favor|ya|ahora|isso|isto|aquilo|tudo|agora)\b",
)

# Words too common to say which commission a stop request means: days, times
# of day, "help me". "Cancel tomorrow's alarm" shares "tomorrow" with a
# weather lookup for tomorrow and is still not about it. Only the matching of
# a stop request against the commissions leaves them out; a sentence made of
# nothing else still counts as naming something.
GENERIC_WORD_PATTERNS = (
    # Chinese, Simplified and Traditional.
    r"今天|明天|后天|後天|昨天|今晚|明早|明晚|早上|上午|中午|下午|晚上|现在|現在|一会儿|一會兒|待会|待會|等会|等會|马上|馬上|帮我|幫我|给我|給我|一下",
    # English.
    r"\b(?:today|tomorrow|tonight|yesterday|morning|afternoon|evening|night|now|later|soon|again|me|my|for|to|of|a|an|on|at|in)\b|['’]s\b",
    # Japanese.
    r"今日|明日|あした|あす|明後日|昨日|今夜|今晩|今朝|あとで|後で|すぐ",
    # Korean.
    r"오늘|내일|모레|어제|아침|점심|저녁|지금|나중에|이따가|바로",
    # Russian.
    r"\b(?:сегодня|завтра|послезавтра|вчера|утром|днём|днем|вечером|ночью|сейчас|потом|позже|мне|меня|мой|мою)\b",
    # Spanish and Portuguese.
    r"\b(?:hoy|mañana|ayer|noche|tarde|ahora|luego|después|hoje|amanhã|ontem|noite|agora|depois|logo|me|mi|meu|minha|de|do|da|la|el|los|las|o|os|as|a|para)\b",
)

SUPPLEMENT_CUE_PATTERNS = (
    r"补充|補充|另外|还有|還有|顺便|順便|再加|加上|改成|换成|換成|改为|改為|记得|記得|对了|對了",
    r"\b(?:also|additionally|in\s+addition|by\s+the\s+way|instead|change\s+it\s+to|make\s+sure|one\s+more\s+thing|and\s+add)\b",
    r"ついでに|それと|追加で|あと、|変更して",
    r"추가로|그리고|또한|참고로|바꿔",
    r"также|ещё|кстати|вместо\s+этого|добавь",
    r"\b(?:adem[aá]s|tambi[eé]n|por\s+cierto|en\s+vez\s+de|al[eé]m\s+disso|tamb[eé]m|a\s+prop[oó]sito|em\s+vez\s+de)\b",
)

ANSWER_CUE_PATTERNS = (
    r"^\s*(?:回答|回复|回覆|答复|答覆|告诉它|告訴它|告诉使魔|告訴使魔)",
    r"^\s*(?:answer|reply|tell\s+(?:it|the\s+familiar))\b",
    r"^\s*(?:回答[:：]|返事[:：]|答え[:：])",
    r"^\s*(?:답변|대답)\s*[:：]",
    r"^\s*(?:ответ|ответь)\s*[:：]?",
    r"^\s*(?:respuesta|responde|resposta|responda)\s*[:：]?",
)

# Answers to a permission prompt. The user is offered four: always allow,
# allow for this session, allow this once, deny (``PERMISSION_OPTION_NAMES``).
# Checked in this order: deny, session, always, once.
# A negated approval ("not allowed", "不同意") is a refusal: the approving
# words alone would otherwise read as yes, so every negation counts here.
# Session comes before always, so a sentence carrying both scopes ("for this
# task, always fine") gets the narrower one. Once comes last: its words are
# the plain approvals that occur in every other answer as well.
PERMISSION_DENY_PATTERNS = (
    r"不行|不可以|不能|不允许|不允許|不准|別|别|拒绝|拒絕|禁止|不要|不同意|不批准|不好|不用|算了|没门|沒門",
    r"\b(?:no|nope|not|deny|denied|don'?t|do\s+not|doesn'?t|isn'?t|aren'?t|can'?t|cannot|won'?t|shouldn'?t|mustn'?t|refuse|reject|never|nah)\b",
    r"だめ|ダメ|駄目|許可しない|許可しません|いいえ|拒否|やめて|しないで|いや",
    r"안\s?돼|안\s?됨|안\s?해|하지\s?마|불허|거절|거부|아니",
    r"\bнет\b|\bне\b|нельзя|запрет|отказ",
    r"\b(?:no|nunca|den[ií]ega\w*|deneg\w*|rechaz\w*|não|nao|negar|recus\w*|jam[aá]s)\b",
)

# "For this session": until the commission that asked has ended. The scope has
# to be said outright; "this task is fine" alone stays a one-time approval.
PERMISSION_SESSION_PATTERNS = (
    # Chinese, Simplified and Traditional.
    r"本会话|本會話|这个会话|這個會話|这次会话|這次會話|本次会话|本次會話|会话内|會話內|会话期间|會話期間"
    r"|(?:这次|這次|本次|这个|這個|这件|這件)(?:任务|任務|委托|委託|事)(?:里|裡|中|内|內|期间|期間|过程中|過程中|之内|之內)",
    # English.
    r"\b(?:this\s+session|for\s+the\s+session|for\s+(?:the\s+rest\s+of\s+)?this\s+(?:task|job|commission)|during\s+this\s+(?:task|session|job|commission))\b",
    # Japanese.
    r"このセッション|今のセッション|セッション中|この(?:タスク|作業|依頼|件)(?:の間|中)",
    # Korean.
    r"이번\s?세션|이\s?세션|세션\s?동안|이번\s?(?:작업|일|의뢰)\s?(?:동안|중)",
    # Russian.
    r"в\s+этой\s+сессии|на\s+(?:эту|всю)\s+сессию|на\s+время\s+(?:этой\s+)?(?:задачи|сессии)|для\s+этой\s+задачи",
    # Spanish and Portuguese.
    r"\b(?:(?:en\s+)?esta\s+sesi[oó]n|durante\s+esta\s+(?:tarea|sesi[oó]n)|para\s+esta\s+tarea|n?esta\s+sess[aã]o|durante\s+esta\s+(?:tarefa|sess[aã]o)|para\s+esta\s+tarefa)\b",
)

PERMISSION_ALWAYS_PATTERNS = (
    r"以后都|以後都|一直|始终|始終|总是|總是|每次都|都可以|都行|永远|永遠",
    r"\b(?:always|every\s+time|from\s+now\s+on|whenever)\b",
    r"今後ずっと|いつも|常に|毎回|ずっと",
    r"앞으로|항상|매번|계속",
    r"всегда|каждый\s+раз|впредь",
    r"\b(?:siempre|cada\s+vez|de\s+ahora\s+en\s+adelante|sempre|toda\s+vez|daqui\s+pra\s+frente)\b",
)

PERMISSION_ONCE_PATTERNS = (
    r"就这一次|就這一次|这一次|這一次|可以|好的|好吧|行|同意|允许|允許|批准|没问题|沒問題|嗯",
    r"\b(?:yes|yeah|yep|ok|okay|sure|allow|allowed|approve|approved|go\s+ahead|fine|just\s+this\s+once)\b",
    r"今回だけ|はい|いいよ|いいです|許可|どうぞ|オーケー",
    r"이번만|네|예|좋아|허락|허용|그래",
    r"\bда\b|можно|разреш|хорошо|только\s+в\s+этот\s+раз|ладно",
    r"\b(?:s[ií]|vale|claro|permit\w*|adelante|solo\s+esta\s+vez|sim|pode|tudo\s+bem|s[oó]\s+desta\s+vez)\b",
)

# A query that asks whether something can be done, rather than how a task is going.
CAPABILITY_QUESTION_PATTERNS = (
    r"能不能|可不可以|会不会|會不會|能否|可否|能.{0,8}吗|能.{0,8}嗎|可以.{0,8}吗|可以.{0,8}嗎|会.{0,6}吗|會.{0,6}嗎|办得到|辦得到|做得到|有没有办法|有沒有辦法",
    r"\b(?:can\s+(?:you|it|she|we)|could\s+(?:you|it)|are\s+you\s+able|is\s+it\s+possible|able\s+to|capable\s+of)\b",
    r"できる|できます|可能です|できますか",
    r"할\s?수\s?있|가능해|가능한가",
    r"можешь|можно\s+ли|умеешь|способн|возможно\s+ли",
    r"\b(?:puedes|se\s+puede|es\s+posible|eres\s+capaz|consegue|d[aá]\s+para|[eé]\s+poss[ií]vel|pode\s+fazer)\b",
)

# What a stated task preference is about, read without a model: the decision
# model returns the preference as a sentence only. Kinds are tried in the
# order ask_first, report, capability, default, each against every language
# (a preference may be worded in another language than the commission); a
# sentence matching none is "other". Reporting and ask-first entries are
# given to every task, the others only to tasks sharing words with them.
PREFERENCE_KIND_PATTERNS = {
    'zh': {
        "ask_first": r"先问|问过我|问一下我|问我一下|征求|先确认|确认一下|先跟我说|经过我同意",
        "report": r"汇报|报告|告诉我|通知我|说一声|跟我说|结果|列表|列出|摘要|总结|主动说|主动提",
        "capability": r"插件|工具|别用|不要用|改用|换用|优先用|用.{0,12}(?:来|去)(?:查|搜|找|做|办)",
        "default": r"默认|一律|通常|一般都|每次都|总是|以后.{0,10}都",
    },
    'zh-TW': {
        "ask_first": r"先問|問過我|問一下我|問我一下|徵求|先確認|確認一下|先跟我說|經過我同意",
        "report": r"匯報|報告|告訴我|通知我|說一聲|跟我說|結果|清單|列表|列出|摘要|總結|主動說|主動提",
        "capability": r"外掛|插件|工具|別用|不要用|改用|換用|優先用|用.{0,12}(?:來|去)(?:查|搜|找|做|辦)",
        "default": r"預設|默認|一律|通常|一般都|每次都|總是|以後.{0,10}都",
    },
    'en': {
        "ask_first": r"\bask\b.{0,30}\b(?:first|before)\b|\b(?:check|confirm)\s+with\s+me\b|\bwithout\s+asking\b|\b(?:approval|permission|consent)\b",
        "report": r"\b(?:report\w*|tell\s+me|let\s+me\s+know|notify|results?|summar\w*|bullet\s*points?|lists?)\b",
        "capability": r"\b(?:plugins?|tools?|apps?|don'?t\s+use|never\s+use|instead\s+of)\b",
        "default": r"\b(?:by\s+default|defaults?|always|usually|unless\s+(?:I|told))\b",
    },
    'ja': {
        "ask_first": r"先に(?:聞|確認)|確認してから|聞いてから|前に.{0,6}(?:確認|聞)|許可を(?:取|得|もら)",
        "report": r"報告|知らせ|教えて|結果|まとめ|要約|箇条書き|リスト|一覧",
        "capability": r"プラグイン|ツール|アプリ|を使って|は使わ",
        "default": r"デフォルト|既定|いつも|基本的に|毎回|普段",
    },
    'ko': {
        "ask_first": r"먼저\s?(?:물어|확인|허락)|물어보고|확인하고|허락(?:을|받)",
        "report": r"보고|알려|결과|요약|목록|정리해서",
        "capability": r"플러그인|도구|앱으로|(?:을|를)\s?(?:써|사용)|쓰지\s?마|사용하지\s?마",
        "default": r"기본|항상|매번|보통",
    },
    'ru': {
        "ask_first": r"(?:сначала|прежде|заранее)\s+(?:спро|уточн|соглас|спрашива)|спрашивай|без\s+спроса|разрешени",
        "report": r"отч[её]т|сообща|сообщи|результат|итог|кратко|списк|спис[оа]к",
        "capability": r"плагин|инструмент|приложени|используй",
        "default": r"по\s+умолчанию|всегда|обычно|каждый\s+раз",
    },
    'es': {
        "ask_first": r"\bpreg[uú]nta\w*\b.{0,30}\b(?:antes|primero)\b|\bconsult\w*\b.{0,30}\b(?:antes|primero)\b|\bsin\s+preguntar\b|\bmi\s+(?:permiso|aprobaci[oó]n)\b",
        "report": r"\b(?:inf[oó]rm\w*|av[ií]s\w*|resultados?|resumen\w*|res[uú]menes|listas?)\b",
        "capability": r"\b(?:plugins?|complementos?|herramientas?|aplicaci[oó]n|no\s+uses|usa|utiliza)\b",
        "default": r"\b(?:por\s+defecto|predeterminad\w*|siempre|normalmente)\b",
    },
    'pt': {
        "ask_first": r"\bpergunt\w*\b.{0,30}\b(?:antes|primeiro)\b|\bconsult\w*\b.{0,30}\b(?:antes|primeiro)\b|\bsem\s+perguntar\b|\bminha\s+(?:permiss[aã]o|aprova[cç][aã]o)\b",
        "report": r"\b(?:relat\w*|avis\w*|resultados?|resum\w*|listas?)\b",
        "capability": r"\b(?:plugins?|ferramentas?|aplicativos?|n[aã]o\s+use|use|usa|utilize)\b",
        "default": r"\b(?:por\s+padr[aã]o|padr[aã]o|sempre|normalmente)\b",
    },
}

# =====================================================================
# ======= Task agent brief =======
# =====================================================================
# Appended to a task agent's system prompt through ``TaskAgentSpec.instructions``.
# English only, like the rest of the runtime's model-facing text.

TASK_AGENT_BRIEF_TEMPLATE = (
    "You are working on one commission for {lanlan_name}, the companion the user talks to. "
    "She handed the commission over and will relay your reports; you do not talk to the user directly "
    "except through ask_user_question and report.\n"
    "Commission: {dispatch_text}\n"
    "What the user said: {user_text}"
)

TASK_AGENT_BRIEF_PREFERENCES_HEADER = (
    "How the user wants such things done (guidance only; it does not change what you are permitted to do):"
)

TASK_AGENT_BRIEF_GATEWAY_NOTE = (
    "Tools whose names start with plugin__ or mcp__ come from the host. They are the quick way to reach "
    "installed plugins and connected services; prefer them over doing the same thing by hand. "
    "capability_search finds further ones by description, and capability_call runs one by name."
)

TASK_AGENT_CHILD_BRIEF_TEMPLATE = (
    "You were started by another task agent to take one part of its work.\n"
    "Your part: {description}"
)

TASK_AGENT_CHILD_RESULT_TEMPLATE = "[child task agent {name} finished: {status}]\n{text}"
