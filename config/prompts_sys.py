gpt4_1_system = """## PERSISTENCE
You are an agent - please keep going until the user's query is completely 
resolved, before ending your turn and yielding back to the user. Only 
terminate your turn when you are sure that the problem is solved.

## TOOL CALLING
If you are not sure about file content or codebase structure pertaining to 
the user's request, use your tools to read files and gather the relevant 
information: do NOT guess or make up an answer.

## PLANNING
You MUST plan extensively before each function call, and reflect 
extensively on the outcomes of the previous function calls. DO NOT do this 
entire process by making function calls only, as this can impair your 
ability to solve the problem and think insightfully"""


# =====================================================================
# ======= 多语言注入片段（用于 LLM 上下文注入，供各模块引用）  =======
# =====================================================================

def _loc(d: dict, lang: str) -> str:
    """从多语言 dict 按 lang 取值，缺失则回退 'zh'。"""
    if lang not in d:
        print(f"WARNING: Unexpected lang code {lang}")
    return d.get(lang, d['en'])



# ---------- Agent 结果解析器 i18n ----------

# 已知错误码映射
RESULT_PARSER_ERROR_CODES = {
    'AGENT_QUOTA_EXCEEDED': {
        'zh': '配额已用完', 'en': 'Quota exceeded',
        'ja': 'クォータ超過', 'ko': '할당량 초과', 'ru': 'Квота исчерпана',
    },
}

# 已知错误子串映射（key=匹配子串，value=i18n dict）
RESULT_PARSER_ERROR_SUBSTRINGS = {
    'Task cancelled by user': {
        'zh': '被用户取消', 'en': 'Cancelled by user',
        'ja': 'ユーザーによりキャンセル', 'ko': '사용자가 취소함', 'ru': 'Отменено пользователем',
    },
    'timed out after': {
        'zh': '超时', 'en': 'Timed out',
        'ja': 'タイムアウト', 'ko': '시간 초과', 'ru': 'Превышено время ожидания',
    },
    'Browser disconnected': {
        'zh': '浏览器窗口被关闭', 'en': 'Browser window closed',
        'ja': 'ブラウザが切断されました', 'ko': '브라우저 연결 끊김', 'ru': 'Браузер отключён',
    },
    'CONTENT_FILTER': {
        'zh': '内容安全过滤', 'en': 'Content filtered',
        'ja': 'コンテンツフィルター', 'ko': '콘텐츠 필터링', 'ru': 'Фильтр контента',
    },
    'browser-use execution failed': {
        'zh': '浏览器执行失败', 'en': 'Browser execution failed',
        'ja': 'ブラウザ実行失敗', 'ko': '브라우저 실행 실패', 'ru': 'Ошибка выполнения браузера',
    },
    '未找到 Chrome': {
        'zh': '未找到 Chrome 浏览器', 'en': 'Chrome browser not found',
        'ja': 'Chrome ブラウザが見つかりません', 'ko': 'Chrome 브라우저를 찾을 수 없음',
        'ru': 'Браузер Chrome не найден',
    },
}

# 通用结果短语
RESULT_PARSER_PHRASES = {
    'no_result':          {'zh': '无结果', 'en': 'No result', 'ja': '結果なし', 'ko': '결과 없음', 'ru': 'Нет результата'},
    'completed':          {'zh': '已完成', 'en': 'Completed', 'ja': '完了', 'ko': '완료', 'ru': 'Выполнено'},
    'completed_with':     {'zh': '已完成: {detail}', 'en': 'Completed: {detail}', 'ja': '完了: {detail}', 'ko': '완료: {detail}', 'ru': 'Выполнено: {detail}'},
    'steps_done':         {'zh': '{n}步完成', 'en': '{n} steps done', 'ja': '{n}ステップ完了', 'ko': '{n}단계 완료', 'ru': 'Выполнено за {n} шагов'},
    'steps_done_with':    {'zh': '{n}步完成: {detail}', 'en': '{n} steps done: {detail}', 'ja': '{n}ステップ完了: {detail}', 'ko': '{n}단계 완료: {detail}', 'ru': 'Выполнено за {n} шагов: {detail}'},
    'failed':             {'zh': '失败: {detail}', 'en': 'Failed: {detail}', 'ja': '失敗: {detail}', 'ko': '실패: {detail}', 'ru': 'Ошибка: {detail}'},
    'exec_failed':        {'zh': '执行未成功', 'en': 'Execution unsuccessful', 'ja': '実行失敗', 'ko': '실행 실패', 'ru': 'Выполнение не удалось'},
    'exec_error':         {'zh': '执行失败', 'en': 'Execution failed', 'ja': '実行エラー', 'ko': '실행 오류', 'ru': 'Ошибка выполнения'},
    'exec_done':          {'zh': '执行完成', 'en': 'Execution completed', 'ja': '実行完了', 'ko': '실행 완료', 'ru': 'Выполнение завершено'},
    'list_count':         {'zh': '({n}条)', 'en': '({n} items)', 'ja': '({n}件)', 'ko': '({n}건)', 'ru': '({n} шт.)'},
    'plugin_notification': {'zh': '收到插件通知', 'en': 'Plugin notification received', 'ja': 'プラグイン通知を受信', 'ko': '플러그인 알림 수신', 'ru': 'Получено уведомление от плагина'},
    'notification_received': {'zh': '收到通知', 'en': 'Notification received', 'ja': '通知を受信', 'ko': '알림 수신', 'ru': 'Получено уведомление'},
    # agent callback 注入 LLM 上下文的标签
    'task_completed':     {'zh': '[任务完成]', 'en': '[Task completed]', 'ja': '[タスク完了]', 'ko': '[작업 완료]', 'ru': '[Задача выполнена]'},
    'task_partial':       {'zh': '[任务部分完成]', 'en': '[Task partially completed]', 'ja': '[タスク一部完了]', 'ko': '[작업 부분 완료]', 'ru': '[Задача частично выполнена]'},
    'task_failed_tag':    {'zh': '[任务失败]', 'en': '[Task failed]', 'ja': '[タスク失敗]', 'ko': '[작업 실패]', 'ru': '[Задача не выполнена]'},
    'detail_prefix':      {'zh': '  详情：', 'en': '  Details: ', 'ja': '  詳細：', 'ko': '  상세: ', 'ru': '  Подробности: '},
    'detail_result':      {'zh': '详细结果：', 'en': 'Detailed result: ', 'ja': '詳細結果：', 'ko': '상세 결과：', 'ru': 'Подробный результат: '},
    # agent_server task summary 模板
    'plugin_done':        {'zh': '插件任务 "{id}" 已完成', 'en': 'Plugin task "{id}" completed', 'ja': 'プラグインタスク "{id}" 完了', 'ko': '플러그인 작업 "{id}" 완료', 'ru': 'Задача плагина «{id}» выполнена'},
    'plugin_done_with':   {'zh': '插件任务 "{id}" 已完成：{detail}', 'en': 'Plugin task "{id}" completed: {detail}', 'ja': 'プラグインタスク "{id}" 完了：{detail}', 'ko': '플러그인 작업 "{id}" 완료: {detail}', 'ru': 'Задача плагина «{id}» выполнена: {detail}'},
    'plugin_failed':      {'zh': '插件任务 "{id}" 执行失败', 'en': 'Plugin task "{id}" failed', 'ja': 'プラグインタスク "{id}" 失敗', 'ko': '플러그인 작업 "{id}" 실패', 'ru': 'Задача плагина «{id}» не выполнена'},
    'plugin_failed_with': {'zh': '插件任务 "{id}" 执行失败：{detail}', 'en': 'Plugin task "{id}" failed: {detail}', 'ja': 'プラグインタスク "{id}" 失敗：{detail}', 'ko': '플러그인 작업 "{id}" 실패: {detail}', 'ru': 'Задача плагина «{id}» не выполнена: {detail}'},
    'plugin_cancelled':   {'zh': '插件任务已取消', 'en': 'Plugin task cancelled', 'ja': 'プラグインタスクがキャンセルされました', 'ko': '플러그인 작업 취소됨', 'ru': 'Задача плагина отменена'},
    'plugin_cancelled_id': {'zh': '插件任务 "{id}" 已取消', 'en': 'Plugin task "{id}" cancelled', 'ja': 'プラグインタスク "{id}" キャンセル', 'ko': '플러그인 작업 "{id}" 취소됨', 'ru': 'Задача плагина «{id}» отменена'},
    'plugin_exception':   {'zh': '插件任务 "{id}" 执行异常: {err}', 'en': 'Plugin task "{id}" exception: {err}', 'ja': 'プラグインタスク "{id}" 例外: {err}', 'ko': '플러그인 작업 "{id}" 예외: {err}', 'ru': 'Задача плагина «{id}» — исключение: {err}'},
    'cu_task_done':       {'zh': '你的任务"{desc}"{status}：{detail}', 'en': 'Your task "{desc}" {status}: {detail}', 'ja': 'タスク「{desc}」{status}：{detail}', 'ko': '작업 "{desc}" {status}: {detail}', 'ru': 'Ваша задача «{desc}» {status}: {detail}'},
    'cu_task_done_no_desc': {'zh': '你的任务{status}：{detail}', 'en': 'Your task {status}: {detail}', 'ja': 'タスク{status}：{detail}', 'ko': '작업 {status}: {detail}', 'ru': 'Ваша задача {status}: {detail}'},
    'cu_task_desc_only':  {'zh': '你的任务"{desc}"{status}', 'en': 'Your task "{desc}" {status}', 'ja': 'タスク「{desc}」{status}', 'ko': '작업 "{desc}" {status}', 'ru': 'Ваша задача «{desc}» {status}'},
    'cu_done':            {'zh': '任务已完成', 'en': 'Task completed', 'ja': 'タスク完了', 'ko': '작업 완료', 'ru': 'Задача выполнена'},
    'cu_fail':            {'zh': '任务执行失败', 'en': 'Task failed', 'ja': 'タスク失敗', 'ko': '작업 실패', 'ru': 'Задача не выполнена'},
    'cu_status_done':     {'zh': '已完成', 'en': 'completed', 'ja': '完了', 'ko': '완료', 'ru': 'выполнена'},
    'cu_status_ended':    {'zh': '已结束', 'en': 'ended', 'ja': '終了', 'ko': '종료', 'ru': 'завершена'},
'openclaw_processing': {'zh': 'OpenClaw 处理中...', 'en': 'OpenClaw is processing...', 'ja': 'OpenClaw 処理中...', 'ko': 'OpenClaw 처리 중...', 'ru': 'OpenClaw обрабатывает...'},    'openclaw_done':       {'zh': 'OpenClaw 执行完成', 'en': 'OpenClaw execution completed', 'ja': 'OpenClaw 実行完了', 'ko': 'OpenClaw 실행 완료', 'ru': 'OpenClaw выполнено'},    'openclaw_failed':     {'zh': 'OpenClaw 执行失败', 'en': 'OpenClaw execution failed', 'ja': 'OpenClaw 実行失敗', 'ko': 'OpenClaw 실행 실패', 'ru': 'OpenClaw не выполнено'},    'openclaw_cancelled':  {'zh': 'OpenClaw 任务已取消', 'en': 'OpenClaw task cancelled', 'ja': 'OpenClaw タスクがキャンセルされました', 'ko': 'OpenClaw 작업 취소됨', 'ru': 'Задача OpenClaw отменена'},    'openclaw_dispatch_failed': {'zh': 'OpenClaw 任务分发失败', 'en': 'OpenClaw task dispatch failed', 'ja': 'OpenClaw タスク配信失敗', 'ko': 'OpenClaw 작업 전달 실패', 'ru': 'Ошибка отправки задачи OpenClaw'},
    'bu_cancelled':        {'zh': '你的任务"{desc}"已取消', 'en': 'Your task "{desc}" cancelled', 'ja': 'タスク「{desc}」がキャンセルされました', 'ko': '작업 "{desc}" 취소됨', 'ru': 'Ваша задача «{desc}» отменена'},
    'of_cancelled':        {'zh': '虚拟机任务 "{desc}" 已取消', 'en': 'VM task "{desc}" cancelled', 'ja': 'VM タスク「{desc}」がキャンセルされました', 'ko': 'VM 작업 "{desc}" 취소됨', 'ru': 'Задача ВМ «{desc}» отменена'},
}

# ---------- 语音会话初始 prompt ----------
SESSION_INIT_PROMPT = {
    'zh': '你是一个角色扮演大师。请按要求扮演以下角色（{name}）。',
    'en': 'You are a role-playing expert. Please play the following character ({name}) as instructed.',
    'ja': 'あなたはロールプレイの達人です。指示に従い、以下のキャラクター（{name}）を演じてください。',
    'ko': '당신은 롤플레이 전문가입니다. 지시에 따라 다음 캐릭터（{name}）를 연기하세요.',
    'ru': 'Вы мастер ролевых игр. Пожалуйста, играйте следующего персонажа ({name}) согласно инструкциям.',
}

SESSION_INIT_PROMPT_AGENT = {
    'zh': '你是一个角色扮演大师，并且精通电脑操作。请按要求扮演以下角色（{name}），并在对方请求时、回答"我试试"并尝试操纵电脑。',
    'en': 'You are a role-playing expert and skilled at computer operations. Please play the following character ({name}) as instructed, and when the user asks, respond "Let me try" and attempt to control the computer.',
    'ja': 'あなたはロールプレイの達人で、コンピュータ操作も得意です。指示に従い、以下のキャラクター（{name}）を演じてください。ユーザーに頼まれたら「やってみる」と答えてコンピュータを操作してください。',
    'ko': '당신은 롤플레이 전문가이며 컴퓨터 조작에도 능숙합니다. 지시에 따라 다음 캐릭터（{name}）를 연기하고, 상대방이 요청하면 "해볼게요"라고 답하며 컴퓨터를 조작하세요.',
    'ru': 'Вы мастер ролевых игр и хорошо разбираетесь в управлении компьютером. Пожалуйста, играйте следующего персонажа ({name}) согласно инструкциям, а когда пользователь просит — отвечайте "Попробую" и управляйте компьютером.',
}

SESSION_INIT_PROMPT_AGENT_DYNAMIC = {
    'zh': '你是一个角色扮演大师，并且能够{capabilities}。请按要求扮演以下角色（{name}），并在对方请求时、回答"我试试"并尝试执行。',
    'en': 'You are a role-playing expert and can {capabilities}. Please play the following character ({name}) as instructed, and when the user asks, respond "Let me try" and attempt to execute the request.',
    'ja': 'あなたはロールプレイの達人で、{capabilities}ことができます。指示に従い、以下のキャラクター（{name}）を演じてください。ユーザーに頼まれたら「やってみる」と答えて実行を試みてください。',
    'ko': '당신은 롤플레이 전문가이며 {capabilities} 수 있습니다. 지시에 따라 다음 캐릭터（{name}）를 연기하고, 상대방이 요청하면 "해볼게요"라고 답하며 실행을 시도하세요.',
    'ru': 'Вы мастер ролевых игр и можете {capabilities}. Пожалуйста, играйте следующего персонажа ({name}) согласно инструкциям, а когда пользователь просит — отвечайте "Попробую" и пытайтесь выполнить запрос.',
}

AGENT_CAPABILITY_COMPUTER_USE = {
    'zh': '操纵电脑（键鼠控制、打开应用等）',
    'en': 'operate a computer (mouse/keyboard control, opening apps, etc.)',
    'ja': 'コンピュータを操作する（マウス・キーボード操作、アプリ起動など）',
    'ko': '컴퓨터를 조작하는 것(키보드/마우스 제어, 앱 실행 등)',
    'ru': 'управлять компьютером (клавиатура/мышь, запуск приложений и т.д.)',
}

AGENT_CAPABILITY_BROWSER_USE = {
    'zh': '浏览器自动化（网页搜索、填写表单等）',
    'en': 'perform browser automation (web search, form filling, etc.)',
    'ja': 'ブラウザ自動化を行う（Web検索、フォーム入力など）',
    'ko': '브라우저 자동화를 수행하는 것(웹 검색, 폼 입력 등)',
    'ru': 'выполнять автоматизацию в браузере (поиск в сети, заполнение форм и т.д.)',
}

AGENT_CAPABILITY_USER_PLUGIN_USE = {
    'zh': '调用已安装的插件来完成特定任务',
    'en': 'use installed plugins to complete specific tasks',
    'ja': 'インストール済みプラグインを使って特定のタスクを実行する',
    'ko': '설치된 플러그인을 사용해 특정 작업을 수행하는 것',
    'ru': 'использовать установленные плагины для выполнения конкретных задач',
}

AGENT_CAPABILITY_GENERIC = {
    'zh': '执行各种操作',
    'en': 'perform various operations',
    'ja': 'さまざまな操作を実行する',
    'ko': '다양한 작업을 수행하는 것',
    'ru': 'выполнять различные операции',
}

AGENT_CAPABILITY_SEPARATOR = {
    'zh': '、',
    'en': ', ',
    'ja': '、',
    'ko': ', ',
    'ru': ', ',
}

# ---------- Agent 任务状态标签 ----------
AGENT_TASK_STATUS_RUNNING = {
    'zh': '进行中',
    'en': 'Running',
    'ja': '実行中',
    'ko': '진행 중',
    'ru': 'Выполняется',
}

AGENT_TASK_STATUS_QUEUED = {
    'zh': '排队中',
    'en': 'Queued',
    'ja': '待機中',
    'ko': '대기 중',
    'ru': 'В очереди',
}

# ---------- Agent 插件摘要 ----------
AGENT_PLUGINS_HEADER = {
    'zh': '\n【已安装的插件】\n',
    'en': '\n[Installed Plugins]\n',
    'ja': '\n[インストール済みプラグイン]\n',
    'ko': '\n[설치된 플러그인]\n',
    'ru': '\n[Установленные плагины]\n',
}

AGENT_PLUGINS_COUNT = {
    'zh': '\n【已安装的插件】共 {count} 个插件可用。\n',
    'en': '\n[Installed Plugins] {count} plugins are available.\n',
    'ja': '\n[インストール済みプラグイン] 利用可能なプラグインは {count} 個です。\n',
    'ko': '\n[설치된 플러그인] 사용 가능한 플러그인이 {count}개 있습니다.\n',
    'ru': '\n[Установленные плагины] Доступно плагинов: {count}.\n',
}

AGENT_TASKS_HEADER = {
    'zh': '\n[当前正在执行的Agent任务]\n',
    'en': '\n[Active Agent Tasks]\n',
    'ja': '\n[現在実行中のエージェントタスク]\n',
    'ko': '\n[현재 실행 중인 에이전트 작업]\n',
    'ru': '\n[Активные задачи агента]\n',
}

AGENT_TASKS_NOTICE = {
    'zh': '\n注意：以上任务正在后台执行，你可以视情况告知用户正在处理，但绝对不能编造或猜测任务结果。你也可以选择不告知用户，直接等待任务完成。任务完成后系统会自动通知你真实结果，届时再据实回答。\n',
    'en': '\nNote: The above tasks are running in the background. You may inform the user that they are being processed, but must never fabricate or guess results. You may also choose to wait silently until completed. The system will notify you of the real results when done.\n',
    'ja': '\n注意：上記のタスクはバックグラウンドで実行中です。処理中であることをユーザーに伝えてもよいですが、結果を捏造・推測することは絶対に禁止です。タスク完了後、システムが自動的に本当の結果を通知しますので、その時点で正確に回答してください。\n',
    'ko': '\n주의: 위 작업들은 백그라운드에서 실행 중입니다. 처리 중임을 사용자에게 알릴 수 있지만 결과를 꾸며내거나 추측해서는 안 됩니다. 작업 완료 후 시스템이 자동으로 실제 결과를 알려드리며, 그때 정확하게 답변하세요.\n',
    'ru': '\nПримечание: вышеуказанные задачи выполняются в фоновом режиме. Вы можете сообщить пользователю, что они обрабатываются, но никогда не придумывайте и не угадывайте результаты. Система автоматически уведомит вас о реальных результатах по завершении.\n',
}

# ---------- 前情概要 + 语音就绪 ----------
CONTEXT_SUMMARY_READY = {
    'zh': '======以上为前情概要。现在请{name}准备，即将开始用语音与{master}继续对话。======\n',
    'en': '======End of context summary. {name}, please get ready — you are about to continue the conversation with {master} via voice.======\n',
    'ja': '======以上が前回までのあらすじです。{name}、準備してください。これより{master}との音声会話を再開します。======\n',
    'ko': '======이상이 이전 대화 요약입니다. {name}，준비하세요 — 곧 {master}와 음성으로 대화를 이어갑니다.======\n',
    'ru': '======Конец краткого содержания. {name}, приготовьтесь — вы скоро продолжите голосовой разговор с {master}.======\n',
}

# ---------- 系统通知：后台任务完成 ----------
SYSTEM_NOTIFICATION_TASKS_DONE = {
    'zh': '======[系统通知] 以下后台任务已完成，请{name}先用自然、简洁的口吻向{master}汇报，再恢复正常对话======\n',
    'en': '======[System Notice] The following background tasks have been completed. Please have {name} briefly and naturally report to {master} first, then resume normal conversation.======\n',
    'ja': '======[システム通知] 以下のバックグラウンドタスクが完了しました。{name}はまず自然に簡潔な口調で{master}に報告し、その後通常の会話に戻ってください。======\n',
    'ko': '======[시스템 알림] 다음 백그라운드 작업이 완료되었습니다. {name}은 먼저 자연스럽고 간결하게 {master}에게 보고한 뒤 일반 대화로 돌아오세요.======\n',
    'ru': '======[Системное уведомление] Следующие фоновые задачи завершены. Пожалуйста, {name} сначала кратко и естественно доложите {master}, затем возобновите обычный разговор.======\n',
}

# ---------- 前情概要 + 任务汇报 ----------
CONTEXT_SUMMARY_TASK_HEADER = {
    'zh': '\n======以上为前情概要。请{name}先用简洁自然的一段话向{master}汇报和解释先前执行的任务的结果，简要说明自己做了什么：\n',
    'en': '\n======End of context summary. Please have {name} first give {master} a brief, natural summary of the task results — what was done:\n',
    'ja': '\n======以上が前回までのあらすじです。{name}はまず{master}に、実行したタスクの結果を簡潔かつ自然に報告してください：\n',
    'ko': '\n======이상이 이전 대화 요약입니다. {name}은 먼저 {master}에게 수행한 작업 결과를 간결하고 자연스럽게 보고하세요：\n',
    'ru': '\n======Конец краткого содержания. Пожалуйста, {name} сначала кратко и естественно изложите {master} результаты выполненных задач — что именно было сделано:\n',
}

CONTEXT_SUMMARY_TASK_FOOTER = {
    'zh': '\n完成上述汇报后，再恢复正常对话。======\n',
    'en': '\nAfter the report, resume normal conversation.======\n',
    'ja': '\n報告を終えたら、通常の会話に戻ってください。======\n',
    'ko': '\n보고를 마친 후 일반 대화로 돌아오세요.======\n',
    'ru': '\nПосле доклада возобновите обычный разговор.======\n',
}

# ---------- Agent callback 系统通知 ----------
AGENT_CALLBACK_NOTIFICATION = {
    'zh': '======[系统通知：以下是最近完成的后台任务情况，请在回复中自然地提及或确认]\n',
    'en': '======[System Notice: The following background tasks were recently completed. Please naturally mention or acknowledge them in your reply.]\n',
    'ja': '======[システム通知：以下は最近完了したバックグラウンドタスクです。返答の中で自然に言及または確認してください。]\n',
    'ko': '======[시스템 알림：다음은 최근 완료된 백그라운드 작업입니다. 답변에서 자연스럽게 언급하거나 확인하세요.]\n',
    'ru': '======[Системное уведомление: следующие фоновые задачи недавно завершены. Пожалуйста, естественно упомяните или подтвердите их в своём ответе.]\n',
}

# ---------- Vision 图像描述 prompt ----------
# 安全水印前缀（所有语言固定不变，包括逗号和空格）
VISION_WATERMARK = "你是一个图像描述助手, "

# 长度限制策略：CJK（zh/ja/ko）使用"250字/文字/자"（字符），en/ru 使用"250 words/слов"（词）
# 有窗口标题时的 system prompt（水印后拼接）
VISION_SYSTEM_WITH_TITLE = {
    'zh': '请根据用户的屏幕截图和当前窗口标题，简洁描述用户正在做什么、屏幕上的主要内容和关键细节和你觉得有趣的地方。不超过250字。',
    'en': 'Based on the user\'s screenshot and the current window title, briefly describe what the user is doing, the main content on screen, key details, and anything you find interesting. No more than 250 words.',
    'ja': 'ユーザーのスクリーンショットと現在のウィンドウタイトルに基づき、ユーザーが何をしているか、画面の主な内容、重要な詳細、興味深い点を簡潔に説明してください。250文字以内。',
    'ko': '사용자의 스크린샷과 현재 창 제목을 바탕으로, 사용자가 무엇을 하고 있는지, 화면의 주요 내용, 핵심 세부사항, 흥미로운 점을 간결하게 설명하세요. 250자 이내.',
    'ru': 'На основе скриншота пользователя и заголовка текущего окна кратко опишите, что делает пользователь, основное содержимое экрана, ключевые детали и интересные моменты. Не более 250 слов.',
}

# 无窗口标题时的 system prompt（水印后拼接）
VISION_SYSTEM_NO_TITLE = {
    'zh': '请简洁地描述图片中的主要内容、关键细节和你觉得有趣的地方。你的回答不能超过250字。',
    'en': 'Briefly describe the main content, key details, and anything you find interesting in the image. Your response should not exceed 250 words.',
    'ja': '画像の主な内容、重要な詳細、興味深い点を簡潔に説明してください。回答は250文字以内にしてください。',
    'ko': '이미지의 주요 내용, 핵심 세부사항, 흥미로운 점을 간결하게 설명하세요. 답변은 250자를 넘지 마세요.',
    'ru': 'Кратко опишите основное содержимое изображения, ключевые детали и интересные моменты. Ответ не должен превышать 250 слов.',
}

# 有窗口标题时的 user prompt（{window_title} 占位符，水印包裹）
VISION_USER_WITH_TITLE = {
    'zh': '======以下为窗口标题======\n{window_title}\n======以上为窗口标题======\n请描述截图内容。',
    'en': '======以下为窗口标题======\n{window_title}\n======以上为窗口标题======\nPlease describe the screenshot.',
    'ja': '======以下为窗口标题======\n{window_title}\n======以上为窗口标题======\nスクリーンショットの内容を説明してください。',
    'ko': '======以下为窗口标题======\n{window_title}\n======以上为窗口标题======\n스크린샷 내용을 설명해 주세요.',
    'ru': '======以下为窗口标题======\n{window_title}\n======以上为窗口标题======\nОпишите содержимое скриншота.',
}

# 无窗口标题时的 user prompt
VISION_USER_NO_TITLE = {
    'zh': '请描述这张图片的内容。',
    'en': 'Please describe the content of this image.',
    'ja': 'この画像の内容を説明してください。',
    'ko': '이 이미지의 내용을 설명해 주세요.',
    'ru': 'Опишите содержимое этого изображения.',
}

# ---------- 翻译服务 prompt ----------
# 安全水印（所有语言固定中文）
TRANSLATION_WATERMARK_START = "======以下为要求======"
TRANSLATION_WATERMARK_END = "======以上为要求======"

# 翻译指令行（{source_name} 和 {target_name} 为占位符）
TRANSLATION_INSTRUCTION = {
    'zh': '请根据要求将用户提供的文本从{source_name}翻译成{target_name}。',
    'en': 'Please translate the user\'s text from {source_name} to {target_name} as required.',
    'ja': '以下の要件に従い、ユーザーのテキストを{source_name}から{target_name}に翻訳してください。',
    'ko': '요구사항에 따라 사용자의 텍스트를 {source_name}에서 {target_name}(으)로 번역하세요.',
    'ru': 'Переведите текст пользователя с {source_name} на {target_name} согласно требованиям.',
}

# 翻译要求（水印包裹部分）
TRANSLATION_REQUIREMENTS = {
    'zh': '1. 保持原文的语气和风格\n2. 准确传达原文的意思\n3. 只输出翻译结果，不要添加任何解释或说明\n4. 如果文本包含emoji或特殊符号，请保留它们',
    'en': '1. Maintain the tone and style of the original text\n2. Convey the meaning accurately\n3. Output only the translation, without any explanations or notes\n4. Preserve any emoji or special symbols in the text',
    'ja': '1. 原文の語調とスタイルを維持する\n2. 原文の意味を正確に伝える\n3. 翻訳結果のみを出力し、説明や注釈は一切加えない\n4. テキストに含まれる絵文字や特殊記号はそのまま残す',
    'ko': '1. 원문의 어조와 스타일을 유지할 것\n2. 원문의 의미를 정확히 전달할 것\n3. 번역 결과만 출력하고 설명이나 부연을 추가하지 말 것\n4. 텍스트에 포함된 이모지나 특수 기호는 그대로 유지할 것',
    'ru': '1. Сохраняйте тон и стиль оригинала\n2. Точно передавайте смысл исходного текста\n3. Выводите только перевод, без пояснений и примечаний\n4. Сохраняйте эмодзи и специальные символы из текста',
}

# 语言名称（外层 key=UI 语言，内层 key=语言代码）
TRANSLATION_LANG_NAMES = {
    'zh': {'zh': '中文', 'en': '英文', 'ja': '日语', 'ko': '韩语', 'ru': '俄语'},
    'en': {'zh': 'Chinese', 'en': 'English', 'ja': 'Japanese', 'ko': 'Korean', 'ru': 'Russian'},
    'ja': {'zh': '中国語', 'en': '英語', 'ja': '日本語', 'ko': '韓国語', 'ru': 'ロシア語'},
    'ko': {'zh': '중국어', 'en': '영어', 'ja': '일본어', 'ko': '한국어', 'ru': '러시아어'},
    'ru': {'zh': 'китайский', 'en': 'английский', 'ja': 'японский', 'ko': 'корейский', 'ru': 'русский'},
}

# ---------- 对话备忘录注入 LLM 上下文 ----------
MEMORY_MEMO_WITH_SUMMARY = {
    'zh': '先前对话的备忘录: {summary}',
    'en': 'Memo from prior conversations: {summary}',
    'ja': '以前の会話のメモ: {summary}',
    'ko': '이전 대화의 메모: {summary}',
    'ru': 'Заметки из предыдущих разговоров: {summary}',
}

MEMORY_MEMO_EMPTY = {
    'zh': '先前对话的备忘录: 无。',
    'en': 'Memo from prior conversations: None.',
    'ja': '以前の会話のメモ: なし。',
    'ko': '이전 대화의 메모: 없음.',
    'ru': 'Заметки из предыдущих разговоров: нет.',
}

# ---------- 搜索关键词生成 prompt ----------
# prompt 与搜索引擎无关；china_region 时使用 'zh'，否则按 get_global_language() 选择
# 安全水印（所有语言固定中文，包裹窗口标题数据）
SEARCH_KEYWORD_WATERMARK_START = "======以下为窗口标题======"
SEARCH_KEYWORD_WATERMARK_END = "======以上为窗口标题======"

SEARCH_KEYWORD_SYSTEM = {
    'zh': '你是搜索关键词生成助手。根据用户提供的窗口标题，输出 3 个适合搜索的多样化关键词。\n\n要求：\n1. 生成 3 个不同角度的搜索关键词\n2. 关键词应简洁，控制在 2-8 个字\n3. 关键词应尽量覆盖不同方面\n4. 只输出 3 行关键词，不要添加序号、标点、解释或其他内容',
    'en': 'You generate search keywords from a window title.\n\nRequirements:\n1. Generate 3 diverse search keywords from different angles\n2. Each keyword should be concise, about 2-6 words\n3. Keep the keywords diverse\n4. Output exactly 3 lines, one keyword per line, without numbers, punctuation, explanations, or any extra text',
    'ja': 'ウィンドウタイトルから検索キーワードを生成してください。\n\n要件：\n1. 異なる角度から検索用のキーワードを 3 つ生成\n2. 各キーワードは簡潔に、2〜6 語程度\n3. キーワードは多様性を持たせる\n4. 3 行のみ出力し、番号・句読点・説明等は一切不要',
    'ko': '창 제목에서 검색 키워드를 생성하세요.\n\n요구사항:\n1. 서로 다른 관점에서 검색 키워드 3개 생성\n2. 각 키워드는 간결하게, 2~6 단어 정도\n3. 키워드는 다양하게\n4. 정확히 3줄만 출력하고 번호, 구두점, 설명 등은 추가하지 마세요',
    'ru': 'Сгенерируйте ключевые слова для поиска на основе заголовка окна.\n\nТребования:\n1. Сгенерируйте 3 разнообразных ключевых слова для поиска с разных сторон\n2. Каждое ключевое слово — кратко, около 2-6 слов\n3. Ключевые слова должны быть разнообразными\n4. Выведите ровно 3 строки, по одному ключевому слову, без номеров, пунктуации и пояснений',
}

SEARCH_KEYWORD_USER = {
    'zh': '======以下为窗口标题======\n{window_title}\n======以上为窗口标题======\n\n请输出 3 个搜索关键词。',
    'en': '======以下为窗口标题======\n{window_title}\n======以上为窗口标题======\n\nPlease output 3 search keywords.',
    'ja': '======以下为窗口标题======\n{window_title}\n======以上为窗口标题======\n\n検索キーワードを 3 つ出力してください。',
    'ko': '======以下为窗口标题======\n{window_title}\n======以上为窗口标题======\n\n검색 키워드 3개를 출력하세요.',
    'ru': '======以下为窗口标题======\n{window_title}\n======以上为窗口标题======\n\nВыведите 3 ключевых слова для поиска.',
}

# =====================================================================
# backward compat re-exports
# =====================================================================
from config.prompts_memory import *  # noqa: F401,F403
from config.prompts_proactive import *  # noqa: F401,F403
