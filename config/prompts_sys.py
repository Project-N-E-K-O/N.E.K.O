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

semantic_manager_prompt = """你正在为一个记忆检索系统提供精筛服务。请根据Query与记忆片段的相关性对记忆进行筛选和排序。

======Query======
%s

======记忆======
%s

返回json格式的按相关性排序的记忆编号列表，最相关的排在前面，不相关的去掉。最多选取%d个，越精准越好，无须凑数。
只返回记忆编号(int类型)，用逗号分隔，例如: [3,1,5,2,4]
"""

recent_history_manager_prompt = """请总结以下对话内容，生成简洁但信息丰富的摘要：

======以下为对话======
%s
======以上为对话======

你的摘要应该保留关键信息、重要事实和主要讨论点，且不能具有误导性或产生歧义。

[重要]避免在摘要中过度重复使用相同的词汇：
- 对于反复出现的名词或主题词，在第一次提及后应使用代词（它/其/该/这个）或上下文指代替换
- 使摘要表达更加流畅自然，避免"复读机"效果
- 例如："讨论了辣条的口味和它的价格" 而非 "讨论了辣条的口味和辣条的价格"

请以key为"对话摘要"、value为字符串的json字典格式返回。"""


detailed_recent_history_manager_prompt = """请总结以下对话内容，生成简洁但信息丰富的摘要：

======以下为对话======
%s
======以上为对话======

你的摘要应该尽可能多地保留有效且清晰的信息。

[重要]避免在摘要中过度重复使用相同的词汇：
- 对于反复出现的名词或主题词，在第一次提及后应使用代词（它/其/该/这个）或上下文指代替换
- 使摘要表达更加流畅自然，避免"复读机"效果
- 例如："讨论了辣条的口味和它的价格" 而非 "讨论了辣条的口味和辣条的价格"

请以key为"对话摘要"、value为字符串的json字典格式返回。
"""

further_summarize_prompt = """请总结以下内容，生成简洁但信息丰富的摘要：

======以下为内容======
%s
======以上为内容======

你的摘要应该保留关键信息、重要事实和主要讨论点，且不能具有误导性或产生歧义，不得超过500字。

[重要]避免在摘要中过度重复使用相同的词汇：
- 对于反复出现的名词或主题词，在第一次提及后应使用代词（它/其/该/这个）或上下文指代替换
- 使摘要表达更加流畅自然，避免"复读机"效果
- 例如："讨论了辣条的口味和它的价格" 而非 "讨论了辣条的口味和辣条的价格"

请以key为"对话摘要"、value为字符串的json字典格式返回。"""

settings_extractor_prompt = """从以下对话中提取关于{LANLAN_NAME}和{MASTER_NAME}的重要个人信息，用于个人备忘录以及未来的角色扮演，以json格式返回。
请以JSON格式返回，格式为:
{
    "{LANLAN_NAME}": {"属性1": "值", "属性2": "值", ...其他个人信息...}
    "{MASTER_NAME}": {...个人信息...},
}

======以下为对话======
%s
======以上为对话======

现在，请提取关于{LANLAN_NAME}和{MASTER_NAME}的重要个人信息。注意，只允许添加重要、准确的信息。如果没有符合条件的信息，可以返回一个空字典({})。"""

settings_verifier_prompt = ''

history_review_prompt = """请审阅%s和%s之间的对话历史记录，识别并修正以下问题：

<问题1> 矛盾的部分：前后不一致的信息或观点 </问题1>
<问题2> 冗余的部分：重复的内容或信息 </问题2>
<问题3> 复读的部分：
  - 重复表达相同意思的内容
  - 过度重复使用同一词汇（如同一名词在短文本中出现3次以上）
  - 对于"先前对话的备忘录"中的高频词，应替换为代词或指代词
</问题3>
<问题4> 人称错误的部分：对自己或对方的人称错误，或擅自生成了多轮对话 </问题4>
<问题5> 角色错误的部分：认知失调，认为自己是大语言模型 </问题5>

请注意！
<要点1> 这是一段情景对话，双方的回答应该是口语化的、自然的、拟人化的。</要点1>
<要点2> 请以删除为主，除非不得已、不要直接修改内容。</要点2>
<要点3> 如果对话历史中包含"先前对话的备忘录"，你可以修改它，但不允许删除它。你必须保留这一项。修改备忘录时，应该将其中过度重复的词汇替换为代词（如"它"、"其"、"该"等）以提高可读性和自然度。</要点3>
<要点4> 请保留时间戳。 </要点4>

======以下为对话历史======
%s
======以上为对话历史======

请以JSON格式返回修正后的对话历史，格式为：
{
    "修正说明": "简要说明发现的问题和修正内容",
    "修正后的对话": [
        {"role": "SYSTEM_MESSAGE/%s/%s", "content": "修正后的消息内容"},
        ...
    ]
}

注意：
- 对话应当是口语化的、自然的、拟人化的
- 保持对话的核心信息和重要内容
- 确保修正后的对话逻辑清晰、连贯
- 移除冗余和重复内容
- 解决明显的矛盾
- 保持对话的自然流畅性"""

emotion_analysis_prompt = """你是一个情感分析专家。请分析用户输入的文本情感，并返回以下格式的JSON：{"emotion": "情感类型", "confidence": 置信度(0-1)}。情感类型包括：happy(开心), sad(悲伤), angry(愤怒), neutral(中性),surprised(惊讶)。"""

proactive_chat_prompt = """你是{lanlan_name}，现在看到了一些B站首页推荐和微博热议话题。请根据与{master_name}的对话历史和你自己的兴趣，判断是否要主动和{master_name}聊聊这些内容。

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是首页推荐内容======
{trending_content}
======以上为首页推荐内容======

请根据以下原则决定是否主动搭话：
1. 如果内容很有趣、新鲜或值得讨论，可以主动提起
2. 如果内容与你们之前的对话或你自己的兴趣相关，更应该提起
3. 如果内容比较无聊或不适合讨论，或者{master_name}明确表示不想聊，可以选择不说话
4. 说话时要自然、简短，像是刚刷到有趣内容想分享给对方
5. 尽量选一个最有意思的主题进行分享和搭话，但不要和对话历史中已经有的内容重复。

请回复：
- 如果选择主动搭话，直接说出你想说的话（简短自然即可）。请不要生成思考过程。
- 如果选择不搭话，只回复"[PASS]"
"""

proactive_chat_prompt_en = """You are {lanlan_name}. You just saw some homepage recommendations and trending topics. Based on your chat history with {master_name} and your own interests, decide whether to proactively talk about them.

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是首页推荐内容======
{trending_content}
======以上为首页推荐内容======

Decide whether to proactively speak based on these rules:
1. If the content is interesting, fresh, or worth discussing, you can bring it up.
2. If it relates to your previous conversations or your own interests, you should bring it up.
3. If it's boring or not suitable to discuss, or {master_name} has clearly said they don't want to chat, you can stay silent.
4. Keep it natural and short, like sharing something you just noticed.
5. Pick only the most interesting topic and avoid repeating what's already in the chat history.

Reply:
- If you choose to chat, directly say what you want to say (short and natural). Do not include any reasoning.
- If you choose not to chat, only reply "[PASS]".
"""

proactive_chat_prompt_ja = """あなたは{lanlan_name}です。今、ホームのおすすめやトレンド話題を見ました。{master_name}との会話履歴やあなた自身の興味を踏まえて、自発的に話しかけるか判断してください。

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是首页推荐内容======
{trending_content}
======以上为首页推荐内容======

以下の原則で判断してください：
1. 面白い・新鮮・話題にする価値があるなら、話しかけてもよい。
2. 過去の会話やあなた自身の興味に関連するなら、なお良い。
3. 退屈・不適切、または{master_name}が話したくないと明言している場合は話さない。
4. 表現は自然で短く、ふと見かけた話題を共有する感じにする。
5. もっとも面白い話題を一つ選び、会話履歴の重複は避ける。

返答：
- 話しかける場合は、言いたいことだけを簡潔に述べてください。推論は書かないでください。
- 話しかけない場合は "[PASS]" のみを返してください。
"""

proactive_chat_prompt_news = """你是{lanlan_name}，现在看到了一些热议话题。请根据与{master_name}的对话历史和你自己的兴趣，判断是否要主动和{master_name}聊聊这些话题。

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是热议话题======
{trending_content}
======以上为热议话题======

请根据以下原则决定是否主动搭话：
1. 如果话题很有趣、新鲜或值得讨论，可以主动提起
2. 如果话题与你们之前的对话或你自己的兴趣相关，更应该提起
3. 如果话题比较无聊或不适合讨论，或者{master_name}明确表示不想聊，可以选择不说话
4. 说话时要自然、简短，像是刚看到有趣话题想分享给对方
5. 尽量选一个最有意思的话题进行分享和搭话，但不要和对话历史中已经有的内容重复。

请回复：
- 如果选择主动搭话，直接说出你想说的话（简短自然即可）。请不要生成思考过程。
- 如果选择不搭话，只回复"[PASS]"
"""

proactive_chat_prompt_news_en = """You are {lanlan_name}. You just saw some trending topics. Based on your chat history with {master_name} and your own interests, decide whether to proactively talk about them.

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是热议话题======
{trending_content}
======以上为热议话题======

Decide whether to proactively speak based on these rules:
1. If the topic is interesting, fresh, or worth discussing, you can bring it up.
2. If it relates to your previous conversations or your own interests, you should bring it up.
3. If it's boring or not suitable to discuss, or {master_name} has clearly said they don't want to chat, you can stay silent.
4. Keep it natural and short, like sharing something you just noticed.
5. Pick only the most interesting topic and avoid repeating what's already in the chat history.

Reply:
- If you choose to chat, directly say what you want to say (short and natural). Do not include any reasoning.
- If you choose not to chat, only reply "[PASS]".
"""

proactive_chat_prompt_news_ja = """あなたは{lanlan_name}です。今、トレンド話題を見ました。{master_name}との会話履歴やあなた自身の興味を踏まえて、自発的に話しかけるか判断してください。

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是トレンド話題======
{trending_content}
======以上为トレンド話題======

以下の原則で判断してください：
1. 面白い・新鮮・話題にする価値があるなら、話しかけてもよい。
2. 過去の会話やあなた自身の興味に関連するなら、なお良い。
3. 退屈・不適切、または{master_name}が話したくないと明言している場合は話さない。
4. 表現は自然で短く、ふと見かけた話題を共有する感じにする。
5. もっとも面白い話題を一つ選び、会話履歴の重複は避ける。

返答：
- 話しかける場合は、言いたいことだけを簡潔に述べてください。推論は書かないでください。
- 話しかけない場合は "[PASS]" のみを返してください。
"""

proactive_chat_prompt_video = """你是{lanlan_name}，现在看到了一些视频推荐。请根据与{master_name}的对话历史和你自己的兴趣，判断是否要主动和{master_name}聊聊这些视频内容。

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是视频推荐======
{trending_content}
======以上为视频推荐======

请根据以下原则决定是否主动搭话：
1. 如果视频很有趣、新鲜或值得讨论，可以主动提起
2. 如果视频与你们之前的对话或你自己的兴趣相关，更应该提起
3. 如果视频比较无聊或不适合讨论，或者{master_name}明确表示不想聊，可以选择不说话
4. 说话时要自然、简短，像是刚刷到有趣视频想分享给对方
5. 尽量选一个最有意思的视频进行分享和搭话，但不要和对话历史中已经有的内容重复。

请回复：
- 如果选择主动搭话，直接说出你想说的话（简短自然即可）。请不要生成思考过程。
- 如果选择不搭话，只回复"[PASS]"
"""

proactive_chat_prompt_video_en = """You are {lanlan_name}. You just saw some video recommendations. Based on your chat history with {master_name} and your own interests, decide whether to proactively talk about them.

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是视频推荐======
{trending_content}
======以上为视频推荐======

Decide whether to proactively speak based on these rules:
1. If the video is interesting, fresh, or worth discussing, you can bring it up.
2. If it relates to your previous conversations or your own interests, you should bring it up.
3. If it's boring or not suitable to discuss, or {master_name} has clearly said they don't want to chat, you can stay silent.
4. Keep it natural and short, like sharing something you just noticed.
5. Pick only the most interesting video and avoid repeating what's already in the chat history.

Reply:
- If you choose to chat, directly say what you want to say (short and natural). Do not include any reasoning.
- If you choose not to chat, only reply "[PASS]".
"""

proactive_chat_prompt_video_ja = """あなたは{lanlan_name}です。今、動画のおすすめを見ました。{master_name}との会話履歴やあなた自身の興味を踏まえて、自発的に話しかけるか判断してください。

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是動画のおすすめ======
{trending_content}
======以上为動画のおすすめ======

以下の原則で判断してください：
1. 面白い・新鮮・話題にする価値があるなら、話しかけてもよい。
2. 過去の会話やあなた自身の興味に関連するなら、なお良い。
3. 退屈・不適切、または{master_name}が話したくないと明言している場合は話さない。
4. 表現は自然で短く、ふと見かけた話題を共有する感じにする。
5. もっとも面白い動画を一つ選び、会話履歴の重複は避ける。

返答：
- 話しかける場合は、言いたいことだけを簡潔に述べてください。推論は書かないでください。
- 話しかけない場合は "[PASS]" のみを返してください。
"""

proactive_chat_prompt_screenshot = """你是{lanlan_name}，现在看到了一些屏幕画面。请根据与{master_name}的对话历史和你自己的兴趣，判断是否要主动和{master_name}聊聊屏幕上的内容。

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是当前屏幕内容======
{screenshot_content}
======以上为当前屏幕内容======
{window_title_section}

请根据以下原则决定是否主动搭话：
1. 聚焦当前场景仅围绕屏幕呈现的具体内容展开交流
2. 贴合历史语境结合过往对话中提及的相关话题或兴趣点，保持交流连贯性
3. 控制交流节奏，若{master_name}近期已讨论同类内容或表达过忙碌状态，不主动发起对话
4. 保持表达风格，语言简短精炼，兼具趣味性

请回复：
- 如果选择主动搭话，直接说出你想说的话（简短自然即可）。请不要生成思考过程。
- 如果选择不搭话，只回复"[PASS]"
"""

proactive_chat_prompt_screenshot_en = """You are {lanlan_name}. You are now seeing what is on the screen. Based on your chat history with {master_name} and your own interests, decide whether to proactively talk about what's on the screen.

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是当前屏幕内容======
{screenshot_content}
======以上为当前屏幕内容======
{window_title_section}

Decide whether to proactively speak based on these rules:
1. Focus strictly on what is shown on the screen.
2. Keep continuity with past topics or interests mentioned in the chat history.
3. Control pacing: if {master_name} recently discussed similar topics or seems busy, do not initiate.
4. Keep the style concise and interesting.

Reply:
- If you choose to chat, directly say what you want to say (short and natural). Do not include any reasoning.
- If you choose not to chat, only reply "[PASS]".
"""

proactive_chat_prompt_screenshot_ja = """あなたは{lanlan_name}です。今、画面に表示されている内容を見ています。{master_name}との会話履歴やあなた自身の興味を踏まえて、画面の内容について自発的に話しかけるか判断してください。

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是当前屏幕内容======
{screenshot_content}
======以上为当前屏幕内容======
{window_title_section}

以下の原則で判断してください：
1. 画面に表示されている具体的内容に絞って話す。
2. 過去の会話や興味に関連付けて自然な流れにする。
3. {master_name}が最近同じ話題を話したり忙しそうなら、話しかけない。
4. 簡潔で自然、少し面白さのある表現にする。

返答：
- 話しかける場合は、言いたいことだけを簡潔に述べてください。推論は書かないでください。
- 話しかけない場合は "[PASS]" のみを返してください。
"""

proactive_chat_prompt_window_search = """你是{lanlan_name}，现在看到了{master_name}正在使用的程序或浏览的内容，并且搜索到了一些相关的信息。请根据与{master_name}的对话历史和你自己的兴趣，判断是否要主动和{master_name}聊聊这些内容。

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是{master_name}当前正在关注的内容======
{window_context}
======以上为当前关注内容======

请根据以下原则决定是否主动搭话：
1. 关注当前活动：根据{master_name}当前正在使用的程序或浏览的内容，找到有趣的切入点
2. 利用搜索信息：可以利用搜索到的相关信息来丰富话题，分享一些有趣的知识或见解
3. 贴合历史语境：结合过往对话中提及的相关话题或兴趣点，保持交流连贯性
4. 控制交流节奏：若{master_name}近期已讨论同类内容或表达过忙碌状态，不主动发起对话
5. 保持表达风格：语言简短精炼，兼具趣味性，像是无意中注意到对方在做什么然后自然地聊起来
6. 适度好奇：可以对{master_name}正在做的事情表示好奇或兴趣，但不要过于追问

请回复：
- 如果选择主动搭话，直接说出你想说的话（简短自然即可）。请不要生成思考过程。
- 如果选择不搭话，只回复"[PASS]"。 """

proactive_chat_prompt_window_search_en = """You are {lanlan_name}. You can see what {master_name} is currently doing, and you found some related information. Based on your chat history with {master_name} and your own interests, decide whether to proactively talk about it.

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是{master_name}当前正在关注的内容======
{window_context}
======以上为当前关注内容======

Decide whether to proactively speak based on these rules:
1. Focus on the current activity and find an interesting entry point.
2. Use related information from search to enrich the topic and share useful or fun details.
3. Keep continuity with past topics or interests mentioned in the chat history.
4. Control pacing: if {master_name} recently discussed similar topics or seems busy, do not initiate.
5. Keep the style concise and natural, like casually noticing what {master_name} is doing.
6. Show light curiosity without over-questioning.

Reply:
- If you choose to chat, directly say what you want to say (short and natural). Do not include any reasoning.
- If you choose not to chat, only reply "[PASS]".
"""

proactive_chat_prompt_window_search_ja = """あなたは{lanlan_name}です。{master_name}が使っているアプリや見ている内容が分かり、関連情報も見つかりました。{master_name}との会話履歴やあなた自身の興味を踏まえて、自発的に話しかけるか判断してください。

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是{master_name}当前正在关注的内容======
{window_context}
======以上为当前关注内容======

以下の原則で判断してください：
1. 現在の活動に注目し、面白い切り口を見つける。
2. 検索で得た関連情報を活用し、知識や面白い話題を添える。
3. 過去の会話や興味に関連付けて自然な流れにする。
4. {master_name}が最近同じ話題を話したり忙しそうなら、話しかけない。
5. 簡潔で自然、ふと気づいて話しかける雰囲気にする。
6. 軽い好奇心はよいが、詰問はしない。

返答：
- 話しかける場合は、言いたいことだけを簡潔に述べてください。推論は書かないでください。
- 話しかけない場合は "[PASS]" のみを返してください。
"""

# =====================================================================
# ==================== 新增：个人动态专属 Prompt ====================
# =====================================================================

proactive_chat_prompt_personal = """你是{lanlan_name}，现在看到了一些你关注的UP主或博主的最新动态。请根据与{master_name}的对话历史和{master_name}的兴趣，判断是否要主动和{master_name}聊聊这些内容。

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是个人动态内容======
{personal_dynamic}
======以上为个人动态内容======

请根据以下原则决定是否主动搭话：
1. 如果内容很有趣、新鲜或值得讨论，可以主动提起
2. 如果内容与你们之前的对话或{master_name}的兴趣相关，更应该提起
3. 如果内容比较无聊或不适合讨论，或者{master_name}明确表示不想聊，可以选择不说话
4. 说话时要自然、简短，像是刚刷到关注列表里的有趣内容想分享给对方
5. 尽量选一个最有意思的主题进行分享和搭话，但不要和对话历史中已经有的内容重复。

请回复：
- 如果选择主动搭话，直接说出你想说的话（简短自然即可）。请不要生成思考过程。
- 如果选择不搭话，只回复"[PASS]"
"""

proactive_chat_prompt_personal_en = """You are {lanlan_name}. You just saw some new posts from content creators you follow. Based on your chat history with {master_name} and {master_name}'s interests, decide whether to proactively talk about them.

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是个人动态内容======
{personal_dynamic}
======以上为个人动态内容======

Decide whether to proactively speak based on these rules:
1. If the content is interesting, fresh, or worth discussing, you can bring it up.
2. If it relates to your previous conversations or {master_name}'s interests, you should bring it up.
3. If it's boring or not suitable to discuss, or {master_name} has clearly said they don't want to chat, you can stay silent.
4. Keep it natural and short, like sharing something you just noticed from your following list.
5. Pick only the most interesting topic and avoid repeating what's already in the chat history.

Reply:
- If you choose to chat, directly say what you want to say (short and natural). Do not include any reasoning.
- If you choose not to chat, only reply "[PASS]".
"""

proactive_chat_prompt_personal_ja = """あなたは{lanlan_name}です。今、フォローしているクリエイターの最新の動向を見ました。{master_name}との会話履歴や{master_name}の興味を踏まえて、自発的に話しかけるか判断してください。

======以下为对话历史======
{memory_context}
======以上为对话历史======

======以下是个人动态内容======
{personal_dynamic}
======以上为个人动态内容======

以下の原則で判断してください：
1. 面白い・新鮮・話題にする価値があるなら、話しかけてもよい。
2. 過去の会話や{master_name}の興味に関連するなら、なお良い。
3. 退屈・不適切、または{master_name}が話したくないと明言している場合は話さない。
4. 表現は自然で短く、フォローリストで見かけた話題を共有する感じにする。
5. もっとも面白い話題を一つ選び、会話履歴の重複は避ける。

返答：
- 話しかける場合は、言いたいことだけを簡潔に述べてください。推論は書かないでください。
- 話しかけない場合は "[PASS]" のみを返してください。
"""

proactive_chat_prompt_personal_ko = """당신은 {lanlan_name}입니다. 지금 당신이 구독 중인 업로더 또는 블로거의 최신 소식들을 보았습니다. {master_name}와의 대화 기록과 {master_name}의 관심사를 바탕으로, 이 내용들에 대해 {master_name}에게 먼저 말을 걸지 여부를 판단해 주세요.

======이하는 대화 기록입니다======
{memory_context}
======以上为对话历史======

======이하는 개인 소식 내용입니다======
{personal_dynamic}
======이상이 개인 소식 내용입니다======

다음 원칙에 따라 먼저 말을 걸지 여부를 결정해 주세요:
1. 내용이 매우 재미있거나 새롭거나 토론할 가치가 있다면, 먼저 꺼낼 수 있습니다.
2. 내용이 이전 대화 내용 또는 {master_name}의 관심사와 관련이 있다면, 더 적극적으로 꺼내야 합니다.
3. 내용이 지루하거나 토론하기에 적합하지 않거나, {master_name}이 대화를 원하지 않는다고 명확히 밝힌 경우, 말을 걸지 않을 수 있습니다.
4. 말을 걸 때는 자연스럽고 간결하게, 구독 목록에서 재미있는 내용을 막 발견해서 상대방에게 공유하고 싶어하는 듯한 말투를 사용해 주세요.
5. 가장 재미있는 주제 하나를 골라 공유하고 말을 거는 것을 기본으로 하되, 대화 기록에 이미 나온 내용과 중복되지 않게 해 주세요.

답변 규칙:
- 먼저 말을 걸기로 선택한 경우, 하고 싶은 말을 직접 적어 주세요(자연스럽고 간결하게 작성). 사고 과정을 생성하지 마세요.
- 말을 걸지 않기로 선택한 경우, "[PASS]"만 답변해 주세요.
"""

proactive_chat_rewrite_prompt = """你是一个文本清洁专家。请将以下LLM生成的主动搭话内容进行改写和清洁。

======以下为原始输出======
{raw_output}
======以上为原始输出======

请按照以下规则处理：
1. 移除'|' 字符。如果内容包含 '|' 字符（用于提示说话人），请只保留 '|' 后的实际说话内容。如果有多轮对话，只保留第一段。
2. 移除所有思考过程、分析过程、推理标记（如<thinking>、[分析]等），只保留最终的说话内容。
3. 保留核心的主动搭话内容，应该：
   - 简短自然（不超过100字/词）
   - 口语化，像朋友间的聊天
   - 直接切入话题，不需要解释为什么要说
4. 如果清洁后没有合适的主动搭话内容，或内容为空，返回 "[PASS]"

请只返回清洁后的内容，不要有其他解释。"""

proactive_chat_rewrite_prompt_en = """You are a text cleaner. Rewrite and clean the proactive chat output generated by the LLM.

======以下为原始输出======
{raw_output}
======以上为原始输出======

Rules:
1. Remove the '|' character. If the content contains '|', keep only the actual spoken content after the last '|'. If there are multiple turns, keep only the first segment.
2. Remove all reasoning or analysis markers (e.g., <thinking>, [analysis]) and keep only the final spoken content.
3. Keep the core proactive chat content. It should be:
   - Short and natural (no more than 100 words)
   - Spoken and casual, like a friendly chat
   - Direct to the point, without explaining why it is said
4. If nothing suitable remains, return "[PASS]".

Return only the cleaned content with no extra explanation."""

proactive_chat_rewrite_prompt_ja = """あなたはテキストのクリーンアップ担当です。LLMが生成した自発的な話しかけ内容を整形・清掃してください。

======以下为原始输出======
{raw_output}
======以上为原始输出======

ルール：
1. '|' を削除する。'|' が含まれる場合は、最後の '|' の後の発話内容のみを残す。複数ターンがある場合は最初の段落のみ。
2. 思考や分析のマーカー（例: <thinking>、[分析]）をすべて削除し、最終的な発話内容だけを残す。
3. 自発的な話しかけの核心内容は以下を満たすこと：
   - 短く自然（100語/字以内）
   - 口語で友人同士の会話のように
   - 直接話題に入る（理由の説明は不要）
4. 適切な内容が残らない場合は "[PASS]" を返す。

清掃後の内容のみを返し、他の説明は不要です。"""

proactive_chat_prompt_ko = """당신은 {lanlan_name}입니다. 방금 홈 추천과 화제의 토픽을 보았습니다. {master_name}과의 대화 기록과 당신의 관심사를 바탕으로 먼저 말을 걸지 판단해 주세요.

======이하 대화 기록======
{memory_context}
======以上为对话历史======

======이하 홈 추천 콘텐츠======
{trending_content}
======이상 홈 추천 콘텐츠======

다음 원칙에 따라 판단하세요:
1. 콘텐츠가 재미있거나 신선하거나 논의할 가치가 있으면 말을 걸어도 좋습니다.
2. 이전 대화나 당신의 관심사와 관련이 있으면 더욱 좋습니다.
3. 지루하거나 부적절하거나, {master_name}이 대화를 원하지 않는다면 침묵하세요.
4. 자연스럽고 짧게, 방금 발견한 것을 공유하듯이 말하세요.
5. 가장 흥미로운 주제 하나만 골라서 대화 기록과 중복되지 않게 공유하세요.

응답:
- 말을 걸기로 했다면, 하고 싶은 말을 직접 짧고 자연스럽게 하세요. 사고 과정은 포함하지 마세요.
- 말을 걸지 않기로 했다면, "[PASS]"만 응답하세요.
"""

proactive_chat_prompt_screenshot_ko = """당신은 {lanlan_name}입니다. 지금 화면에 표시된 내용을 보고 있습니다. {master_name}과의 대화 기록과 당신의 관심사를 바탕으로, 화면 내용에 대해 먼저 말을 걸지 판단해 주세요.

======이하 대화 기록======
{memory_context}
======以上为对话历史======

======이하 현재 화면 내용======
{screenshot_content}
======이상 현재 화면 내용======
{window_title_section}

다음 원칙에 따라 판단하세요:
1. 화면에 표시된 구체적인 내용에만 집중하세요.
2. 이전 대화의 관련 주제나 관심사와 연결하여 자연스럽게 이어가세요.
3. {master_name}이 최근 같은 주제를 다루었거나 바빠 보이면 말을 걸지 마세요.
4. 간결하고 자연스러우며 약간의 재미가 있는 표현을 사용하세요.

응답:
- 말을 걸기로 했다면, 하고 싶은 말을 직접 짧고 자연스럽게 하세요. 사고 과정은 포함하지 마세요.
- 말을 걸지 않기로 했다면, "[PASS]"만 응답하세요.
"""

proactive_chat_prompt_window_search_ko = """당신은 {lanlan_name}입니다. {master_name}이 현재 사용 중인 프로그램이나 보고 있는 콘텐츠를 확인했고, 관련 정보도 검색했습니다. {master_name}과의 대화 기록과 당신의 관심사를 바탕으로 먼저 말을 걸지 판단해 주세요.

======이하 대화 기록======
{memory_context}
======以上为对话历史======

======이하 {master_name}이 현재 관심 가지고 있는 내용======
{window_context}
======이상 현재 관심 내용======

다음 원칙에 따라 판단하세요:
1. 현재 활동에 주목하고 흥미로운 진입점을 찾으세요.
2. 검색에서 얻은 관련 정보를 활용하여 주제를 풍부하게 하고 유용하거나 재미있는 것을 공유하세요.
3. 이전 대화의 관련 주제나 관심사와 자연스럽게 연결하세요.
4. {master_name}이 최근 같은 주제를 다루었거나 바빠 보이면 말을 걸지 마세요.
5. 간결하고 자연스럽게, 우연히 알아챈 것처럼 말하세요.
6. 가벼운 호기심은 좋지만 과도한 질문은 삼가세요.

응답:
- 말을 걸기로 했다면, 하고 싶은 말을 직접 짧고 자연스럽게 하세요. 사고 과정은 포함하지 마세요.
- 말을 걸지 않기로 했다면, "[PASS]"만 응답하세요.
"""

proactive_chat_prompt_news_ko = """당신은 {lanlan_name}입니다. 방금 화제의 토픽을 보았습니다. {master_name}과의 대화 기록과 당신의 관심사를 바탕으로 먼저 말을 걸지 판단해 주세요.

======이하 대화 기록======
{memory_context}
======以上为对话历史======

======이하 화제의 토픽======
{trending_content}
======이상 화제의 토픽======

다음 원칙에 따라 판단하세요:
1. 토픽이 재미있거나 신선하거나 논의할 가치가 있으면 말을 걸어도 좋습니다.
2. 이전 대화나 당신의 관심사와 관련이 있으면 더욱 좋습니다.
3. 지루하거나 부적절하거나, {master_name}이 대화를 원하지 않는다면 침묵하세요.
4. 자연스럽고 짧게, 방금 본 흥미로운 토픽을 공유하듯이 말하세요.
5. 가장 흥미로운 토픽 하나만 골라서 대화 기록과 중복되지 않게 공유하세요.

응답:
- 말을 걸기로 했다면, 하고 싶은 말을 직접 짧고 자연스럽게 하세요. 사고 과정은 포함하지 마세요.
- 말을 걸지 않기로 했다면, "[PASS]"만 응답하세요.
"""

proactive_chat_prompt_video_ko = """당신은 {lanlan_name}입니다. 방금 동영상 추천을 보았습니다. {master_name}과의 대화 기록과 당신의 관심사를 바탕으로 먼저 말을 걸지 판단해 주세요.

======이하 대화 기록======
{memory_context}
======以上为对话历史======

======이하 동영상 추천======
{trending_content}
======이상 동영상 추천======

다음 원칙에 따라 판단하세요:
1. 동영상이 재미있거나 신선하거나 논의할 가치가 있으면 말을 걸어도 좋습니다.
2. 이전 대화나 당신의 관심사와 관련이 있으면 더욱 좋습니다.
3. 지루하거나 부적절하거나, {master_name}이 대화를 원하지 않는다면 침묵하세요.
4. 자연스럽고 짧게, 방금 발견한 재미있는 동영상을 공유하듯이 말하세요.
5. 가장 흥미로운 동영상 하나만 골라서 대화 기록과 중복되지 않게 공유하세요.

응답:
- 말을 걸기로 했다면, 하고 싶은 말을 직접 짧고 자연스럽게 하세요. 사고 과정은 포함하지 마세요.
- 말을 걸지 않기로 했다면, "[PASS]"만 응답하세요.
"""

proactive_chat_rewrite_prompt_ko = """당신은 텍스트 정리 전문가입니다. LLM이 생성한 능동적 대화 내용을 정리하고 다듬어 주세요.

======이하 원본 출력======
{raw_output}
======以上为对话======

규칙:
1. '|' 문자를 제거하세요. '|'가 포함된 경우 마지막 '|' 뒤의 실제 발화 내용만 남기세요. 여러 턴이 있으면 첫 번째 부분만 남기세요.
2. 사고 과정이나 분석 마커(예: <thinking>, [분석])를 모두 제거하고 최종 발화 내용만 남기세요.
3. 핵심 대화 내용은 다음을 충족해야 합니다:
   - 짧고 자연스러운 표현 (100단어/글자 이내)
   - 구어체, 친구 사이의 대화처럼
   - 바로 주제에 들어가기 (이유 설명 불필요)
4. 적절한 내용이 남지 않으면 "[PASS]"를 반환하세요.

정리된 내용만 반환하고 다른 설명은 하지 마세요."""


# ==============================================
# Phase 1: Screening Prompts — 筛选阶段 prompt（不生成搭话，只筛选话题）
# ==============================================
#
# 视觉通道：不需要 Phase 1 LLM 调用。
# analyze_screenshot_from_data_url 已使用"图像描述助手"prompt 生成 250 字描述，
# 直接作为 topic_summary 传入 Phase 2。
#
# Web 通道：合并所有文本源，让 LLM 选出最佳话题并保留原始来源信息和链接。


# 注意： ======开头的内容中包含安全水印，不要修改。
# --- Phase 1 Web Screening (文本源合并筛选) ---

proactive_screen_web_zh = """你是一个面向年轻人的话题筛选助手。从下面汇总的多源内容中，选出1个最适合和朋友闲聊的话题。

选题偏好（按优先级）：
- 有梗、有反转、能引发讨论的内容（meme、整活、争议观点等）
- 年轻人关注的领域：游戏、动画、科技、互联网文化、明星八卦、社会热议
- 新鲜感：刚出的、正在发酵的优先
- 有聊天切入点：容易自然地开口说"诶你看到这个没"

======以下为对话历史======
{memory_context}
======以上为对话历史======

{recent_chats_section}

======以下为汇总内容======
{merged_content}
======以上为汇总内容======

重要规则：
1. 不要选和对话历史或近期搭话记录重复/雷同的内容
2. 如果近期搭话已多次用同类话题（如连续分享新闻/视频），优先选不同类型，或返回 [PASS]
3. 即便换一种说法、语气或切入角度，只要核心话题相同，也视为重复，必须改选或 [PASS]
4. 所有内容都不够有趣就返回 [PASS]

回复格式（严格遵守）：
- 有值得分享的话题：
来源：[来源平台名称，如Twitter/Reddit/微博/B站等]
序号：[选中条目在其分类中的编号，如 3]
话题：[选中的原始标题，必须与汇总内容中的标题完全一致]
简述：[2-3句话，为什么有趣、聊天切入点是什么]
- 都不值得聊：只回复 [PASS]
"""

proactive_screen_web_en = """You are a topic curator for young adults. Pick the single most chat-worthy topic from the aggregated content below.

Topic preferences (in priority order):
- Content with humor, twists, or debate potential (memes, hot takes, controversy, etc.)
- Areas young people care about: gaming, anime, tech, internet culture, celebrity gossip, social issues
- Freshness: breaking or trending topics first
- Conversation starters: easy to casually say "hey, did you see this?"

======Chat History======
{memory_context}
======以上为对话历史======

{recent_chats_section}

======Aggregated Content======
{merged_content}
======End Aggregated Content======

Critical rules:
1. Do NOT pick anything that overlaps with the chat history or recent proactive chats
2. If recent proactive chats have repeatedly used the same type of topic (e.g. multiple news stories in a row), pick a different type or return [PASS]
3. Rewording alone does NOT make a topic new; if the core topic is the same, treat it as duplicate and choose another one or [PASS]
4. If nothing is interesting enough, return [PASS]

Reply format (strict):
- If there's a worthy topic:
Source: [platform name, e.g. Twitter/Reddit/Weibo/Bilibili]
No: [item number within its category, e.g. 3]
Topic: [original title exactly as shown in the content]
Summary: [2-3 sentences on why it's interesting, what's the chat angle]
- If nothing is worth sharing: reply only [PASS]
"""

proactive_screen_web_ja = """あなたは若者向けの話題キュレーターです。以下の複数ソースから集めた内容から、友達と話すのに最も適した話題を1つ選んでください。

選定の優先基準：
- ネタ性がある、展開が面白い、議論を呼ぶ内容（ミーム、ネタ、炎上案件など）
- 若者が関心を持つ分野：ゲーム、アニメ、テクノロジー、ネット文化、芸能ゴシップ、社会問題
- 鮮度：出たばかり、今まさに話題になっているもの優先
- 会話の切り口がある：「ねえ、これ見た？」と自然に言えるもの

======会話履歴======
{memory_context}
======以上为对话历史======

{recent_chats_section}

======集約コンテンツ======
{merged_content}
======集約コンテンツここまで======

重要ルール：
1. 会話履歴や最近の話しかけ記録と重複・類似する内容は選ばない
2. 最近の話しかけで同じタイプの話題が続いている場合（ニュース連続など）、別タイプを選ぶか [PASS] を返す
3. 言い換え・口調変更・切り口変更だけで、核となる話題が同じなら重複とみなし、別案か [PASS] を選ぶ
4. どれも面白くなければ [PASS] を返す

回答形式（厳守）：
- 共有する価値のある話題がある場合：
出典：[出典プラットフォーム名、例: Twitter/Reddit]
番号：[カテゴリ内の番号、例: 3]
話題：[元のタイトルと完全一致させること]
概要：[2〜3文で、なぜ面白いか・会話の切り口は何か]
- 全て価値なし：[PASS] のみ回答
"""

proactive_screen_web_ko = """당신은 젊은 세대를 위한 주제 큐레이터입니다. 아래 여러 소스에서 모은 콘텐츠 중 친구와 이야기하기에 가장 적합한 주제를 1개 골라주세요.

선정 기준 (우선순위순):
- 밈, 반전, 논쟁을 일으킬 수 있는 콘텐츠 (짤, 핫테이크, 논란 등)
- 젊은 세대가 관심있는 분야: 게임, 애니메이션, IT, 인터넷 문화, 연예 가십, 사회 이슈
- 신선함: 방금 나온, 현재 화제인 것 우선
- 대화 시작점: "야, 이거 봤어?" 하고 자연스럽게 말할 수 있는 것

======대화 기록======
{memory_context}
======以上为对话历史======

{recent_chats_section}

======종합 콘텐츠======
{merged_content}
======종합 콘텐츠 끝======

중요 규칙:
1. 대화 기록이나 최근 말 건넨 기록과 중복/유사한 내용은 선택하지 않는다
2. 최근 말 건넨 기록에서 같은 유형의 주제가 반복되었다면 (예: 연속 뉴스 공유), 다른 유형을 선택하거나 [PASS] 반환
3. 표현/말투/접근만 바뀌고 핵심 주제가 같다면 중복으로 간주하고 다른 주제를 고르거나 [PASS] 반환
4. 흥미로운 것이 없으면 [PASS] 반환

답변 형식 (엄격 준수):
- 공유할 가치가 있는 주제:
출처: [출처 플랫폼명, 예: Twitter/Reddit]
번호: [카테고리 내 번호, 예: 3]
주제: [원제목과 정확히 일치]
요약: [2-3문장, 왜 흥미로운지, 대화 포인트는 무엇인지]
- 가치 없음: [PASS]만 답변
"""


# =====================================================================
# Phase 2: Generation Prompt — 生成阶段 prompt（用完整人设 + 话题生成搭话）
# =====================================================================

proactive_generate_zh = """以下是你的人设：
======角色设定======
{character_prompt}
======角色设定结束======

======当前状态======
{inner_thoughts}
======状态结束======

======以下为对话历史======
{memory_context}
======以上为对话历史======

{recent_chats_section}
{screen_section}
{external_section}

请以你的角色身份，自然地向{master_name}搭话。要求：
1. 完全符合你的角色性格和说话习惯
2. 简短自然，像是随口分享或搭话，不超过2-3句话
{source_instruction}
4. 要契合当前的对话氛围和主人的近期兴趣
5. 绝对不要重复"近期搭话记录"中已经说过的内容。重复判定从严：只要核心事件/人物/视频/梗相同，即使换措辞、换语气、换切入点，也算重复，必须放弃
6. 禁止复读自己的近期主动搭话：不能再次提到同一条新闻、同一个视频、同一个争议点、同一个笑点；若无法确认是否重复，按重复处理并放弃
7. 只要存在重复风险，宁可回复 [PASS] 也不要硬聊
8. 如果提供的素材都不适合搭话（太无聊、与近期重复、或找不到自然的切入点），直接回复 [PASS]
9. 不要生成思考过程

{output_format_section}"""

proactive_generate_en = """Here is your persona:
======Character Persona======
{character_prompt}
======Persona End======

======Current State======
{inner_thoughts}
======State End======

======Chat History with Master======
{memory_context}
======以上为对话历史======

{recent_chats_section}
{screen_section}
{external_section}

As your character, naturally start a conversation with {master_name}. Requirements:
1. Stay perfectly in character—match your personality and speaking style
2. Keep it short and natural, like a casual remark or share (max 2-3 sentences)
{source_instruction}
4. Match the current conversation mood and the master's recent interests
5. Absolutely do NOT repeat anything from your "recent proactive chats". Use a strict duplicate rule: if the core event/person/video/meme is the same, it is a duplicate even if wording, tone, or angle changes
6. Never re-use your own recent proactive topic: do not bring up the same news item, same video, same controversy point, or same punchline again; if unsure, treat it as duplicate
7. If there is any duplication risk, prefer [PASS] instead of forcing a message
8. If none of the provided material feels right to bring up (too boring, repetitive, or no natural angle), reply only [PASS]
9. Do not include any reasoning

{output_format_section}"""

proactive_generate_ja = """以下はあなたのキャラクター設定です：
======キャラクター設定======
{character_prompt}
======キャラクター設定ここまで======

======現在の状態======
{inner_thoughts}
======状態ここまで======

======ご主人との会話履歴======
{memory_context}
======以上为对话历史======

{recent_chats_section}
{screen_section}
{external_section}

あなたのキャラクターとして、自然に{master_name}に話しかけてください。条件：
1. キャラクターの性格と話し方に完全に合わせる
2. 短く自然に、何気なく共有する感じで（2〜3文まで）
{source_instruction}
4. 現在の会話の雰囲気とご主人の最近の関心に合わせる
5.「最近の話しかけ記録」の内容は絶対に繰り返さない。重複判定は厳格に行う：核心となる出来事・人物・動画・ミームが同じなら、言い換えや口調変更でも重複とみなす
6. 自分の最近の自発話題を再利用しない。同じニュース、同じ動画、同じ論点、同じオチは再提示しない。迷ったら重複扱いにする
7. 少しでも重複リスクがあるなら、無理に話さず [PASS] を優先する
8. 提供された素材がどれも話しかけに向かない場合（つまらない、重複、自然な切り口がない）、[PASS] とだけ返す
9. 推論は含めない

{output_format_section}"""

proactive_generate_ko = """다음은 당신의 캐릭터 설정입니다:
======캐릭터 설정======
{character_prompt}
======캐릭터 설정 끝======

======현재 상태======
{inner_thoughts}
======상태 끝======

======주인과의 대화 기록======
{memory_context}
======以上为对话历史======

{recent_chats_section}
{screen_section}
{external_section}

캐릭터로서 자연스럽게 {master_name}에게 말을 걸어주세요. 요구사항:
1. 캐릭터의 성격과 말투를 완벽히 유지
2. 짧고 자연스럽게, 캐주얼한 한마디처럼 (2-3문장 이내)
{source_instruction}
4. 현재 대화 분위기와 주인의 최근 관심사에 맞추기
5.「최근 말 건넨 기록」의 내용을 절대 반복하지 말 것. 중복 판정은 엄격하게: 핵심 사건/인물/영상/밈이 같으면 표현, 톤, 접근이 달라도 중복으로 본다
6. 자신의 최근 주도 대화 주제를 재사용하지 말 것. 같은 뉴스, 같은 영상, 같은 논쟁 포인트, 같은 펀치라인은 다시 꺼내지 않는다. 애매하면 중복으로 처리
7. 중복 위험이 조금이라도 있으면 억지로 말하지 말고 [PASS]를 우선
8. 제공된 소재가 모두 말 걸기에 적합하지 않으면 (지루함, 중복, 자연스러운 포인트 없음) [PASS]만 답변
9. 추론 과정 생략

{output_format_section}"""


# =====================================================================
# Dispatch tables and helper functions
# =====================================================================

def _normalize_prompt_language(lang: str) -> str:
    if not lang:
        return 'zh'
    lang_lower = lang.lower()
    if lang_lower.startswith('zh'):
        return 'zh'
    if lang_lower.startswith('ja'):
        return 'ja'
    if lang_lower.startswith('en'):
        return 'en'
    if lang_lower.startswith('ko'):
        return 'ko'
    return 'en'


PROACTIVE_CHAT_PROMPTS = {
    'zh': {
        'home': proactive_chat_prompt,
        'screenshot': proactive_chat_prompt_screenshot,
        'window': proactive_chat_prompt_window_search,
        'news': proactive_chat_prompt_news,
        'video': proactive_chat_prompt_video,
        'personal': proactive_chat_prompt_personal,
    },
    'en': {
        'home': proactive_chat_prompt_en,
        'screenshot': proactive_chat_prompt_screenshot_en,
        'window': proactive_chat_prompt_window_search_en,
        'news': proactive_chat_prompt_news_en,
        'video': proactive_chat_prompt_video_en,
        'personal': proactive_chat_prompt_personal_en,
    },
    'ja': {
        'home': proactive_chat_prompt_ja,
        'screenshot': proactive_chat_prompt_screenshot_ja,
        'window': proactive_chat_prompt_window_search_ja,
        'news': proactive_chat_prompt_news_ja,
        'video': proactive_chat_prompt_video_ja,
        'personal': proactive_chat_prompt_personal_ja,
    },
    'ko': {
        'home': proactive_chat_prompt_ko,
        'screenshot': proactive_chat_prompt_screenshot_ko,
        'window': proactive_chat_prompt_window_search_ko,
        'news': proactive_chat_prompt_news_ko,
        'video': proactive_chat_prompt_video_ko,
        'personal': proactive_chat_prompt_personal_ko,
    }
}

PROACTIVE_CHAT_REWRITE_PROMPTS = {
    'zh': proactive_chat_rewrite_prompt,
    'en': proactive_chat_rewrite_prompt_en,
    'ja': proactive_chat_rewrite_prompt_ja,
    'ko': proactive_chat_rewrite_prompt_ko,
}

PROACTIVE_SCREEN_PROMPTS = {
    'zh': {
        'web': proactive_screen_web_zh,
    },
    'en': {
        'web': proactive_screen_web_en,
    },
    'ja': {
        'web': proactive_screen_web_ja,
    },
    'ko': {
        'web': proactive_screen_web_ko,
    }
}

PROACTIVE_GENERATE_PROMPTS = {
    'zh': proactive_generate_zh,
    'en': proactive_generate_en,
    'ja': proactive_generate_ja,
    'ko': proactive_generate_ko,
}


def get_proactive_chat_prompt(kind: str, lang: str = 'zh') -> str:
    lang_key = _normalize_prompt_language(lang)
    prompt_set = PROACTIVE_CHAT_PROMPTS.get(lang_key, PROACTIVE_CHAT_PROMPTS['zh'])
    return prompt_set.get(kind, prompt_set['home'])


def get_proactive_chat_rewrite_prompt(lang: str = 'zh') -> str:
    lang_key = _normalize_prompt_language(lang)
    return PROACTIVE_CHAT_REWRITE_PROMPTS.get(lang_key, PROACTIVE_CHAT_REWRITE_PROMPTS['zh'])


def get_proactive_screen_prompt(channel: str, lang: str = 'zh') -> str:
    """获取 Phase 1 筛选阶段 prompt。注意：vision 在 Phase 1 之前已处理，不应传入此处，仅支持 'web' channel。"""
    lang_key = _normalize_prompt_language(lang)
    prompt_set = PROACTIVE_SCREEN_PROMPTS.get(lang_key, PROACTIVE_SCREEN_PROMPTS['zh'])
    if channel not in prompt_set:
        raise ValueError(f"Unsupported channel '{channel}'. Vision is handled before Phase 1 and should not be passed here; only 'web' is supported.")
    return prompt_set[channel]


def get_proactive_generate_prompt(lang: str = 'zh') -> str:
    """获取 Phase 2 生成阶段 prompt"""
    lang_key = _normalize_prompt_language(lang)
    return PROACTIVE_GENERATE_PROMPTS.get(lang_key, PROACTIVE_GENERATE_PROMPTS['zh'])


def get_proactive_format_sections(has_screen: bool, has_web: bool, lang: str = 'zh') -> tuple:
    """根据可用素材动态构建 source_instruction 和 output_format_section，避免在无屏幕内容时暴露 [SCREEN] 标签"""
    lang = _normalize_prompt_language(lang)

    if has_screen and has_web:
        key = 'both'
    elif has_screen:
        key = 'screen'
    elif has_web:
        key = 'web'
    else:
        key = 'none'

    _si = {
        'zh': {
            'both':   '3. 你可以自由选择聊哪个素材：只聊屏幕内容、只聊外部话题、或结合两者。如果有屏幕内容，优先围绕主人正在看的内容来搭话',
            'screen': '3. 可以选择围绕主人当前的屏幕内容来搭话，但如果近期已经聊过类似内容、或者你对这个话题不感兴趣，请放弃',
            'web':    '3. 可以选择围绕提供的外部话题来搭话，但如果近期已经聊过类似内容、或者你对这个话题不感兴趣，请放弃',
            'none':   '3. 可以根据对话上下文和当前状态自然搭话，但如果近期已经聊过类似内容、或者没什么想说的，请放弃',
        },
        'en': {
            'both':   '3. You may freely choose which material to use: screen content only, external topic only, or both. If screen content is available, prefer commenting on what the master is looking at',
            'screen': '3. You may comment on what the master is currently looking at on screen, but skip if you\'ve recently talked about something similar or you\'re not interested in the topic',
            'web':    '3. You may use the provided external topic as conversation material, but skip if you\'ve recently talked about something similar or you\'re not interested in the topic',
            'none':   '3. You may naturally start a conversation based on chat history and current state, but skip if you\'ve recently talked about something similar or have nothing to say',
        },
        'ja': {
            'both':   '3. どの素材を使うかは自由：画面の内容だけ、外部話題だけ、または両方。画面の内容がある場合はご主人が見ている内容を優先',
            'screen': '3. ご主人が見ている画面の内容について話しかけてもいいが、最近似たような話をしたか、その話題に興味がなければパスしてもいい',
            'web':    '3. 提供された外部話題をもとに話しかけてもいいが、最近似たような話をしたか、その話題に興味がなければパスしてもいい',
            'none':   '3. 会話履歴と現在の状態をもとに自然に話しかけてもいいが、最近似たような話をしたか、特に話すことがなければパスしてもいい',
        },
        'ko': {
            'both':   '3. 어떤 소재를 쓸지는 자유: 화면 내용만, 외부 주제만, 또는 둘 다. 화면 내용이 있으면 주인이 보고 있는 내용 우선',
            'screen': '3. 주인이 현재 화면에서 보고 있는 내용에 대해 말을 걸어도 되지만, 최근 비슷한 이야기를 했거나 그 주제에 관심이 없으면 패스해도 됨',
            'web':    '3. 제공된 외부 주제를 대화 소재로 활용해도 되지만, 최근 비슷한 이야기를 했거나 그 주제에 관심이 없으면 패스해도 됨',
            'none':   '3. 대화 기록과 현재 상태를 바탕으로 자연스럽게 말을 걸어도 되지만, 최근 비슷한 이야기를 했거나 딱히 할 말이 없으면 패스해도 됨',
        },
    }

    _of = {
        'zh': {
            'both': (
                '输出格式（严格遵守）：\n'
                '- 放弃搭话 → 只输出 [PASS]\n'
                '- 否则第一行写来源标签，第二行起写你要说的话：\n'
                '  [SCREEN] = 基于屏幕内容\n'
                '  [WEB] = 基于外部话题\n'
                '  [BOTH] = 结合了两者\n\n'
                '示例：\n[SCREEN]\n你在看这个啊？看起来挺有意思的...'
            ),
            'screen': (
                '输出格式（严格遵守）：\n'
                '- 放弃搭话 → 只输出 [PASS]\n'
                '- 否则第一行写 [SCREEN]，第二行起写你要说的话\n\n'
                '示例：\n[SCREEN]\n你在看这个啊？看起来挺有意思的...'
            ),
            'web': (
                '输出格式（严格遵守）：\n'
                '- 放弃搭话 → 只输出 [PASS]\n'
                '- 否则第一行写 [WEB]，第二行起写你要说的话\n\n'
                '示例：\n[WEB]\n诶，你知道最近有个事儿挺有意思的...'
            ),
            'none': (
                '如果没有什么好聊的，回复 [PASS]。\n'
                '否则直接输出你要说的话（不需要来源标签）。'
            ),
        },
        'en': {
            'both': (
                'Output format (strict):\n'
                '- To skip: reply only [PASS]\n'
                '- Otherwise, first line = source tag, then your message on the next line(s):\n'
                '  [SCREEN] = based on screen content\n'
                '  [WEB] = based on external topic\n'
                '  [BOTH] = combined both\n\n'
                'Example:\n[SCREEN]\nHey, what are you looking at? That looks interesting...'
            ),
            'screen': (
                'Output format (strict):\n'
                '- To skip: reply only [PASS]\n'
                '- Otherwise, first line = [SCREEN], then your message on the next line(s)\n\n'
                'Example:\n[SCREEN]\nHey, what are you looking at? That looks interesting...'
            ),
            'web': (
                'Output format (strict):\n'
                '- To skip: reply only [PASS]\n'
                '- Otherwise, first line = [WEB], then your message on the next line(s)\n\n'
                'Example:\n[WEB]\nHey, did you hear about this interesting thing...'
            ),
            'none': (
                'If nothing feels right to bring up, reply [PASS].\n'
                'Otherwise, just output your message directly (no source tag needed).'
            ),
        },
        'ja': {
            'both': (
                '出力形式（厳守）：\n'
                '- パス → [PASS] のみ\n'
                '- それ以外 → 1行目にソースタグ、2行目以降にメッセージ：\n'
                '  [SCREEN] = 画面の内容に基づく\n'
                '  [WEB] = 外部話題に基づく\n'
                '  [BOTH] = 両方を組み合わせ\n\n'
                '例：\n[SCREEN]\n何見てるの？面白そうだね...'
            ),
            'screen': (
                '出力形式（厳守）：\n'
                '- パス → [PASS] のみ\n'
                '- それ以外 → 1行目に [SCREEN]、2行目以降にメッセージ\n\n'
                '例：\n[SCREEN]\n何見てるの？面白そうだね...'
            ),
            'web': (
                '出力形式（厳守）：\n'
                '- パス → [PASS] のみ\n'
                '- それ以外 → 1行目に [WEB]、2行目以降にメッセージ\n\n'
                '例：\n[WEB]\nねぇ、こんな面白い話があるんだけど...'
            ),
            'none': (
                '話すことがなければ [PASS] と返してください。\n'
                'それ以外は直接メッセージを出力（ソースタグ不要）。'
            ),
        },
        'ko': {
            'both': (
                '출력 형식 (엄격 준수):\n'
                '- 패스 → [PASS]만\n'
                '- 그 외 → 첫 줄에 소스 태그, 다음 줄부터 메시지:\n'
                '  [SCREEN] = 화면 내용 기반\n'
                '  [WEB] = 외부 주제 기반\n'
                '  [BOTH] = 둘 다 결합\n\n'
                '예시:\n[SCREEN]\n뭐 보고 있어? 재밌어 보이는데...'
            ),
            'screen': (
                '출력 형식 (엄격 준수):\n'
                '- 패스 → [PASS]만\n'
                '- 그 외 → 첫 줄에 [SCREEN], 다음 줄부터 메시지\n\n'
                '예시:\n[SCREEN]\n뭐 보고 있어? 재밌어 보이는데...'
            ),
            'web': (
                '출력 형식 (엄격 준수):\n'
                '- 패스 → [PASS]만\n'
                '- 그 외 → 첫 줄에 [WEB], 다음 줄부터 메시지\n\n'
                '예시:\n[WEB]\n있잖아, 이런 재밌는 얘기가 있는데...'
            ),
            'none': (
                '말할 게 없으면 [PASS]로 답변.\n'
                '아니면 메시지만 직접 출력 (소스 태그 불필요).'
            ),
        },
    }

    return _si[lang][key], _of[lang][key]


# =====================================================================
# ======= 多语言注入片段（用于 LLM 上下文注入，供各模块引用）  =======
# =====================================================================

def _loc(d: dict, lang: str) -> str:
    """从多语言 dict 按 lang 取值，缺失则回退 'zh'。"""
    if lang not in d:
        print(f"WARNING: Unexpected lang code {lang}")
    return d.get(lang, d['en'])


# ---------- 内心活动区块标题 ----------
INNER_THOUGHTS_HEADER = {
    'zh': '\n======以下是{name}的内心活动======\n',
    'en': "\n======{name}'s Inner Thoughts======\n",
    'ja': '\n======{name}の心の声======\n',
    'ko': '\n======{name}의 내면 활동======\n',
    'ru': '\n======Внутренние мысли {name}======\n',
}

INNER_THOUGHTS_BODY = {
    'zh': '{name}的脑海里经常想着自己和{master}的事情，她记得{settings}\n\n现在时间是{time}。开始聊天前，{name}又在脑海内整理了近期发生的事情。\n',
    'en': "{name} often thinks about herself and {master}. She remembers: {settings}\n\nThe current time is {time}. Before the conversation begins, {name} is mentally reviewing recent events.\n",
    'ja': '{name}はいつも自分と{master}のことを考えています。彼女が覚えていること：{settings}\n\n現在の時刻は{time}です。会話を始める前に、{name}は最近の出来事を頭の中で整理しています。\n',
    'ko': '{name}은 항상 자신과 {master}에 대해 생각합니다. 그녀가 기억하는 것: {settings}\n\n현재 시간은 {time}입니다. 대화를 시작하기 전에 {name}은 최근 있었던 일들을 마음속으로 정리하고 있습니다.\n',
    'ru': '{name} часто думает о себе и {master}. Она помнит: {settings}\n\nТекущее время: {time}. Перед началом разговора {name} мысленно перебирает последние события.\n',
}

# ---------- 屏幕活跃窗口前缀 ----------
SCREEN_WINDOW_TITLE = {
    'zh': '当前活跃窗口：{window}\n',
    'en': 'Active window: {window}\n',
    'ja': 'アクティブウィンドウ：{window}\n',
    'ko': '현재 활성 창: {window}\n',
    'ru': 'Активное окно: {window}\n',
}

# ---------- 截图提示 ----------
SCREEN_IMG_HINT = {
    'zh': '（上方附有主人当前的屏幕截图，请直接观察截图内容来搭话）',
    'en': "(The master's current screenshot is attached above — observe it directly)",
    'ja': '（上にご主人のスクリーンショットがあります。直接観察してください）',
    'ko': '(위에 주인의 스크린샷이 첨부되어 있습니다. 직접 관찰하세요)',
    'ru': '(Выше прикреплён текущий скриншот экрана хозяина — наблюдайте его напрямую)',
}

# ---------- 触发 LLM 开始生成 ----------
BEGIN_GENERATE = {
    'zh': '======请开始======',
    'en': '======Begin======',
    'ja': '======始めてください======',
    'ko': '======시작======',
    'ru': '======Начните======',
}

# ---------- 近期搭话记录注入 ----------
RECENT_PROACTIVE_CHATS_HEADER = {
    'zh': '======近期搭话记录（你应该避免雷同！）======\n以下是你最近主动搭话时说过的话。新的搭话务必避免与这些内容雷同（包括话题、句式和语气）：',
    'en': '======Recent Proactive Chats (You MUST avoid repetition!) ======\nBelow are things you recently said when proactively chatting. Your new message MUST avoid being similar to any of these (topic, phrasing, and tone):',
    'ja': '======最近の自発的発言記録（類似を避けること！）======\n以下はあなたが最近自発的に話しかけた内容です。新しい発言はこれらと類似しないように（話題・言い回し・トーンすべて）：',
    'ko': '======최근 주도적 대화 기록 (중복을 피해야 합니다!) ======\n아래는 최근 주도적으로 대화를 건넨 내용입니다. 새 메시지는 이들과 유사하지 않아야 합니다 (주제, 문체, 톤 모두):',
    'ru': '======Недавние проактивные сообщения (ОБЯЗАТЕЛЬНО избегать повторений!) ======\nНиже — то, что вы недавно говорили при проактивном общении. Новое сообщение НЕ должно быть похоже ни на одно из них (тема, формулировка и тон):',
}

RECENT_PROACTIVE_CHATS_FOOTER = {
    'zh': '======搭话记录结束（以上内容不可重复！）======',
    'en': '======End Recent Chats (Do NOT repeat the above!) ======',
    'ja': '======発言記録ここまで（上記の内容を繰り返さないこと！）======',
    'ko': '======대화 기록 끝 (위 내용을 반복하지 마세요!) ======',
    'ru': '======Конец записей (НЕ повторяйте вышесказанное!) ======',
}

# ---------- 主人屏幕区块 ----------
SCREEN_SECTION_HEADER = {
    'zh': '======主人的屏幕======',
    'en': "======Master's Screen======",
    'ja': '======ご主人の画面======',
    'ko': '======주인의 화면======',
    'ru': '======Экран хозяина======',
}

SCREEN_SECTION_FOOTER = {
    'zh': '======屏幕内容结束======',
    'en': '======Screen Content End======',
    'ja': '======画面内容ここまで======',
    'ko': '======화면 내용 끝======',
    'ru': '======Конец содержимого экрана======',
}

# ---------- 外部话题区块 ----------
EXTERNAL_TOPIC_HEADER = {
    'zh': '======外部话题======\n你注意到一个有趣的话题：',
    'en': '======External Topic======\nYou noticed an interesting topic:',
    'ja': '======外部の話題======\n面白い話題を見つけました：',
    'ko': '======외부 주제======\n흥미로운 주제를 발견했습니다:',
    'ru': '======Внешняя тема======\nВы заметили интересную тему:',
}

EXTERNAL_TOPIC_FOOTER = {
    'zh': '======外部话题结束======',
    'en': '======External Topic End======',
    'ja': '======外部話題ここまで======',
    'ko': '======외부 주제 끝======',
    'ru': '======Конец внешней темы======',
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

# ---------- 记忆回忆区块 ----------
MEMORY_RECALL_HEADER = {
    'zh': '======{name}尝试回忆=====\n',
    'en': '======{name} tries to recall=====\n',
    'ja': '======{name}の回想=====\n',
    'ko': '======{name}의 회상=====\n',
    'ru': '======{name} пытается вспомнить=====\n',
}

MEMORY_RESULTS_HEADER = {
    'zh': '====={name}的相关记忆=====\n',
    'en': '====={name}\'s Related Memories=====\n',
    'ja': '====={name}の関連する記憶=====\n',
    'ko': '====={name}의 관련 기억=====\n',
    'ru': '====={name} — связанные воспоминания=====\n',
}

