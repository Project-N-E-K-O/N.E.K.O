"""
Card-Assist prompt templates.

Used by ``main_routers.card_assist_router`` to drive a 3-stage AI assistant
that helps users author a catgirl character card:

  1) clarify  — given the user's one-line description, ask 2-4 clarifying
                questions (each with chip options + optional free-text)
  2) generate — given the description + answers, output a full card field set
  3) refine   — regenerate a single field given an instruction

All three prompts require the LLM to output STRICT JSON only (no markdown
fences). The router strips ```json fences defensively before json.loads.
"""

from __future__ import annotations

from config.prompts.prompts_sys import _loc


# Canonical catgirl card field keys (Chinese keys are what's stored in
# characters.json and what the frontend form's textarea name attribute is).
# The English labels in the screenshot ("Gender", "Age", ...) are i18n
# displays of these keys.
CANONICAL_FIELDS = [
    "性别",         # Gender
    "年龄",         # Age
    "性格原型",     # Personality Archetype
    "种族",         # Race
    "自称",         # Self-Reference
    "核心特质",     # Core Traits
    "行为特征",     # Behavioral Traits
    "不喜欢",       # Dislikes
    "招牌台词",     # Signature Line
]


CARD_ASSIST_CLARIFY_PROMPT = {
    "zh": """你是猫娘角色卡设计助手。用户给出一句话角色描述，你需要抛出 2 到 4 个最有价值的澄清问题，帮助后续生成完整设定。

用户描述：
%s

已有卡片字段（可能为空，仅供参考）：
%s

要求：
- 只挑最关键的 2-4 个维度发问（如年龄段、性格基调、种族细节、说话风格、特殊背景等）。已经有用户提示出来的维度不要再问。
- 每题给 3-4 个互斥的 chip 选项，覆盖常见取向。
- 每题允许自由输入（allowCustom: true）。
- 问题语气活泼自然，符合二次元/猫娘语境。
- 严格按 JSON 返回，禁止 markdown 代码块、禁止任何前后缀文字：

{
  "questions": [
    {
      "id": "q1",
      "header": "短标签(≤6字)",
      "label": "完整问题文本",
      "options": ["选项A", "选项B", "选项C", "选项D"],
      "allowCustom": true
    }
  ]
}""",
    "en": """You are a catgirl character card design assistant. Given the user's one-line character description, raise 2 to 4 of the most valuable clarifying questions to help generate the full setting later.

User description:
%s

Existing card fields (may be empty, for reference only):
%s

Requirements:
- Pick only the 2-4 most critical dimensions (age range, personality tone, species detail, speech style, special background, etc.). Don't ask about dimensions the user already specified.
- For each question, give 3-4 mutually exclusive chip options covering common choices.
- Each question allows free-text input (allowCustom: true).
- Tone should be playful and natural, fitting the anime/catgirl context.
- Return STRICT JSON only — no markdown fences, no preface or suffix text:

{
  "questions": [
    {
      "id": "q1",
      "header": "short tag (<=8 chars)",
      "label": "full question text",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "allowCustom": true
    }
  ]
}""",
}


CARD_ASSIST_GENERATE_PROMPT = {
    "zh": """你是猫娘角色卡设计助手。根据用户的一句话描述 + 多轮澄清答案，生成完整的角色卡字段。

用户描述：
%s

澄清答案（id -> 回答）：
%s

现有卡片字段（如有冲突优先采用本次生成结果）：
%s

目标字段名（必须**原样**使用这些 key，**不要翻译、不要改写大小写、不要替换近义词**）：
%s

要求：
- 必须输出"目标字段名"里列出的**全部**字段，键名 1:1 复制
- 可以追加最多 5 个自定义字段，key 风格保持与目标字段一致（同一种语言/同一种写法）
- 每个字段的值必须是字符串（不要数组、不要对象、不要 null）
- 字段值具体、生动、可游戏化呈现；避免空泛的形容词堆砌
- 招牌台词类字段（"招牌台词"/"一句话台词"/"Signature Line" 等）要带猫娘标志（如"喵~"、"呐"、"nya"），不超过 30 字
- "行为特征"/"行为特点"/"核心特质"/"Core Traits"/"Behavioral Traits" 这类字段可以用逗号分隔列出 3-5 个特点
- 严格按 JSON 返回，禁止 markdown 代码块、禁止任何前后缀文字：

{
  "fields": {
    "<target_key_1>": "...",
    "<target_key_2>": "...",
    "...": "..."
  }
}""",
    "en": """You are a catgirl character card design assistant. Generate a full character card based on the user's one-line description plus their answers to clarifying questions.

User description:
%s

Clarification answers (id -> answer):
%s

Existing card fields (this generation takes priority on conflicts):
%s

Target field keys (use these keys **verbatim** — do NOT translate, re-case, or substitute synonyms):
%s

Requirements:
- You MUST output **every** key listed in "Target field keys" exactly as written, 1:1.
- You MAY append up to 5 additional custom keys in the same language/style as the target keys.
- Every field value must be a STRING (no arrays, no objects, no null).
- Field values should be concrete, vivid, gameable; avoid stacks of generic adjectives.
- A "signature line"-style field (e.g. "Signature Line" / "一句话台词" / "招牌台词") should include a catgirl tic (e.g. "meow~", "nya", "喵~"), <=30 chars.
- Traits-style fields (e.g. "Core Traits" / "Behavioral Traits" / "核心特质" / "行为特点") may be a comma-separated list of 3-5 traits.
- Return STRICT JSON only — no markdown fences, no preface or suffix text:

{
  "fields": {
    "<target_key_1>": "...",
    "<target_key_2>": "...",
    "...": "..."
  }
}""",
}


CARD_ASSIST_REFINE_FIELD_PROMPT = {
    "zh": """你是猫娘角色卡设计助手。请对某一个字段进行局部重生。

完整卡片（仅供上下文，不要改其他字段）：
%s

目标字段名：%s
目标字段当前值：%s
调整指令：%s

要求：
- 只输出该字段的新值（纯字符串，无引号无 JSON 包装）
- 保持与其他字段的整体调性一致
- 不要输出任何解释、思考过程、markdown 代码块或多余文本
- 长度参考原值，不要无限扩写""",
    "en": """You are a catgirl character card design assistant. Regenerate a single field locally.

Full card (context only — do NOT modify other fields):
%s

Target field key: %s
Current value: %s
Adjustment instruction: %s

Requirements:
- Output ONLY the new value as a plain string (no quotes, no JSON wrapper)
- Stay consistent with the overall tone of the other fields
- Do not output any explanation, chain-of-thought, markdown fences, or extra text
- Match the original length roughly — don't balloon out""",
}


def get_card_assist_clarify_prompt(lang: str = "zh") -> str:
    return _loc(CARD_ASSIST_CLARIFY_PROMPT, lang)


def get_card_assist_generate_prompt(lang: str = "zh") -> str:
    return _loc(CARD_ASSIST_GENERATE_PROMPT, lang)


def get_card_assist_refine_field_prompt(lang: str = "zh") -> str:
    return _loc(CARD_ASSIST_REFINE_FIELD_PROMPT, lang)
