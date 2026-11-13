"""Guide natural forge-drop replies without prescribing character dialogue."""

from config.prompts.prompts_sys import normalize_sys_prompt_locale


_FORGE_DROP_CHAT_CONTEXT = {
    "zh": (
        "锻造券已真实到账，可去「喵宇宙」社区使用它锻造卡片，留下你们的回忆。"
        "请沿用当前人设，顺着正在聊的内容，用一两句自然的话回应掉落，轻轻邀请对方去社区试试锻造。"
        "这是一起做件小事的邀请，避免客服播报或催促；本次回复在邀请处自然收住，不必马上转回旧任务。"
        "不要照读事件字段，也不要编造掉落原因或声称已经替用户打开社区、锻造了卡片。"
        "邀请时顺带提醒券的有效期。有 expires_at 就简短说出到期时间及其时区，缺少该字段时请用户去社区确认。"
    ),
    "zh-TW": (
        "鍛造券已真實到帳，可去「喵宇宙」社群使用它鍛造卡片，留下你們的回憶。"
        "請沿用目前人設，順著正在聊的內容，用一兩句自然的話回應掉落，輕輕邀請對方去社群試試鍛造。"
        "這是一起做件小事的邀請，避免客服播報或催促；這次回覆在邀請處自然收住，不必立刻轉回舊任務。"
        "不要照讀事件欄位，也不要編造掉落原因或聲稱已經替使用者開啟社群、鍛造了卡片。"
        "邀請時順帶提醒券的有效期限。有 expires_at 就簡短說出到期時間及其時區，缺少該欄位時請使用者到社群確認。"
    ),
    "en": (
        "A forge ticket has really arrived. It can be used in the Meowverse community to forge a card of your shared memories. "
        "Stay in character and connect to the current conversation: react naturally in one or two sentences and gently invite the user to try forging a card together in the community. "
        "Make it a personal invitation, not a service announcement or pressure. Let this reply end with the invitation rather than immediately returning to the previous task. "
        "Do not read out event fields, invent a cause for the drop, or claim you already opened the community or forged a card. "
        "Briefly mention the ticket's expiry using expires_at and include its time zone. If the field is missing, ask the user to check in the community."
    ),
    "ja": (
        "鍛造チケットが実際に届きました。「喵宇宙」のコミュニティで使い、二人の思い出をカードにできます。"
        "今のキャラクターと会話の流れを保ち、一、二文で自然に反応し、一緒にコミュニティでカードを作ってみようとさりげなく誘ってください。"
        "事務的な通知や催促にせず、誘いで自然に締め、すぐ元の作業に戻さないでください。"
        "イベント項目の読み上げ、未確認の獲得理由、すでにコミュニティを開いたりカードを作ったという説明は避けてください。"
        "誘うときに expires_at に基づく有効期限とタイムゾーンを簡潔に伝え、その値がなければコミュニティで確認するよう案内してください。"
    ),
    "ko": (
        "제작 티켓이 실제로 도착했습니다. 냥 유니버스 커뮤니티에서 사용해 함께한 추억을 카드로 만들 수 있습니다. "
        "현재 캐릭터와 대화 흐름을 유지하며 한두 문장으로 자연스럽게 반응하고, 커뮤니티에서 함께 카드를 만들어 보자고 부드럽게 권하세요. "
        "고객 안내나 재촉처럼 말하지 말고, 곧바로 이전 작업으로 돌아가기보다 초대하는 말로 마무리하세요. "
        "이벤트 필드를 읽거나 획득 이유를 지어내거나, 이미 커뮤니티를 열었거나 카드를 만들었다고 주장하지 마세요. "
        "초대할 때 expires_at을 기준으로 티켓 만료 시각과 시간대를 짧게 알려 주세요. 값이 없으면 커뮤니티에서 확인하도록 안내하세요."
    ),
    "ru": (
        "Билет для ковки действительно получен. В сообществе Meowverse его можно использовать, чтобы создать карточку ваших общих воспоминаний. "
        "Сохраняя характер персонажа и связь с текущим разговором, естественно отреагируй в одном-двух предложениях и мягко пригласи вместе попробовать создать карточку в сообществе. "
        "Это личное приглашение, без официального тона и давления. Заверши им реплику, не возвращаясь сразу к прежней задаче. "
        "Не зачитывай поля события, не придумывай причину выпадения и не утверждай, что уже открыла сообщество или создала карточку. "
        "Коротко назови срок действия билета по expires_at и укажи часовой пояс. Если значения нет, предложи уточнить срок в сообществе."
    ),
    "pt": (
        "Um bilhete de forja realmente chegou. Ele pode ser usado na comunidade Meowverse para criar um cartão das memórias de vocês. "
        "Mantenha a personalidade e o contexto da conversa: reaja naturalmente em uma ou duas frases e convide com leveza a experimentar a forja de um cartão juntos na comunidade. "
        "Faça um convite pessoal, sem tom de atendimento nem pressão, e termine nele em vez de voltar imediatamente à tarefa anterior. "
        "Não leia os campos do evento, invente uma causa para o bilhete ou afirme que já abriu a comunidade ou criou o cartão. "
        "Ao convidar, mencione brevemente o vencimento com base em expires_at e indique o fuso horário. Se o campo estiver ausente, sugira consultar a comunidade."
    ),
    "es": (
        "Ha llegado un boleto de forja real. Puede usarse en la comunidad Meowverse para crear una tarjeta de sus recuerdos compartidos. "
        "Mantén la personalidad y el contexto de la conversación: reacciona de forma natural en una o dos frases e invita con suavidad a probar juntos la forja de una tarjeta en la comunidad. "
        "Haz una invitación personal, sin tono de atención al cliente ni presión, y termina con ella en lugar de volver inmediatamente a la tarea anterior. "
        "No leas los campos del evento, inventes la causa de la recompensa ni afirmes que ya abriste la comunidad o creaste la tarjeta. "
        "Al invitar, menciona brevemente cuándo caduca el boleto según expires_at e indica la zona horaria. Si no hay un valor, sugiere consultar en la comunidad."
    ),
}


def build_forge_drop_chat_context(language: str) -> str:
    return _FORGE_DROP_CHAT_CONTEXT[normalize_sys_prompt_locale(language)]
