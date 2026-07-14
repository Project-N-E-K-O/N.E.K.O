"""验证单次演绎模型的结构、上下文、世界边界和安全回退。"""  # noqa: DOCSTRING_CJK

import json
from pathlib import Path

import pytest

from config.prompts.prompts_theater import THEATER_TURN_SYSTEM_PROMPT, build_theater_turn_prompts
from services.theater import llm


def _prompt_sections(user_prompt: str) -> tuple[dict, dict]:
    """解析提示词分区，确保测试不会把内部规则误当成公开演绎上下文。"""  # noqa: DOCSTRING_CJK
    envelope = json.loads(user_prompt.split("\n", 1)[1])
    return envelope["公开演绎上下文"], envelope["内部规则（只执行，不复述）"]


class _CharacterConfig:
    """为人格文件边界测试提供最小角色配置。"""  # noqa: DOCSTRING_CJK

    def __init__(self, root: Path):
        self.app_docs_dir = root

    def load_characters(self):
        """只声明当前猫娘，其他目录都不属于可读取角色。"""  # noqa: DOCSTRING_CJK
        return {"当前猫娘": "安全猫娘", "猫娘": {"安全猫娘": {}}}


class _ModelConfig:
    """为结构化模型返回测试提供最小可用配置。"""  # noqa: DOCSTRING_CJK

    def get_model_api_config(self, _kind):
        """返回不会访问真实供应商的占位模型配置。"""  # noqa: DOCSTRING_CJK
        return {"model": "fake-model", "base_url": "https://example.invalid"}


def test_fallback_roleplay_responds_to_user_message():
    """离线角色互动必须自然留在当前事件且不复述越界原话。"""  # noqa: DOCSTRING_CJK
    result = llm.fallback_turn(
        lanlan_name="兰兰",
        scene={"text": "雨夜窗边"},
        node={},
        user_message="我有点担心你",
        progress_kind="roleplay_response",
        callback="",
    )
    assert result["narration"] == ""
    assert "我有点担心你" not in result["dialogue"]
    assert "眼前这件事" in result["dialogue"]
    assert "放在心上" not in result["dialogue"]


def test_contextual_roleplay_fallback_answers_grounded_destination():
    """模型连续失败时，去向问题仍应从公开推荐项回答，不能退成泛化拖延。"""  # noqa: DOCSTRING_CJK
    fallback = llm.fallback_turn(
        lanlan_name="糖糖",
        scene={"text": "门口"},
        node={},
        user_message="我们先去哪里？",
        progress_kind="roleplay_response",
        callback="",
    )
    result = llm._contextual_roleplay_fallback(
        fallback,
        user_message="我们先去哪里？",
        progress_kind="roleplay_response",
        choice_options=[{"choice_id": "choice_route", "label": "抵达入口后，把路线递给她看"}],
    )
    assert result["dialogue"] == "那就先去入口吧。到了那里，我们再一起看接下来怎么走喵。"


def test_model_output_requires_narration_for_story_progress():
    """剧情推进缺少旁白时必须拒绝模型结果并回退作者文本。"""  # noqa: DOCSTRING_CJK
    assert llm._parse_output('{"narration":"","dialogue":"继续吧喵"}', progress_kind="graph_progress") is None
    assert llm._parse_output('{"narration":"灯亮了。","dialogue":"继续吧喵"}', progress_kind="graph_progress") == {
        "narration": "灯亮了。",
        "dialogue": "继续吧喵",
        "choice_rewrites": [],
        "matched_choice_id": "",
        "observed_intent_id": "",
    }


def test_model_output_rejects_internal_terms():
    """模型不得把内部节点或提示词字段显示给玩家。"""  # noqa: DOCSTRING_CJK
    assert llm._parse_output(
        '{"narration":"进入 node_id 下一幕","dialogue":"走吧喵"}', progress_kind="graph_progress"
    ) is None


def test_system_prompt_keeps_off_topic_input_inside_current_scene():
    """越界请求必须自然留在当前场景，不能照做或输出系统拉回话术。"""  # noqa: DOCSTRING_CJK
    assert "不得照做" in THEATER_TURN_SYSTEM_PROMPT
    assert "回到剧本选项" in THEATER_TURN_SYSTEM_PROMPT


def test_roleplay_prompt_hides_scripted_dialogue_to_avoid_repetition():
    """自由互动不能再次注入刚完成的作者节点或固定台词。"""  # noqa: DOCSTRING_CJK
    _, user_prompt = build_theater_turn_prompts(
        lanlan_name="兰兰",
        story={"background": "旧教室", "theme": "告别"},
        scene={"title": "教室", "text": "窗外有蝉鸣。"},
        node={"title": "说出担心", "summary": "猫娘承认害怕。", "scripted_dialogue": "这一句不能重复。"},
        user_message="你愿意说说真实感受吗？",
        progress_kind="roleplay_response",
        callback="",
        public_state={},
        recent_turns=[],
        character_profile="",
        choice_options=[],
    )
    payload, internal_rules = _prompt_sections(user_prompt)
    assert "scripted_dialogue" not in payload["目标节点"]
    assert "title" not in payload["目标节点"]
    assert "summary" not in payload["目标节点"]
    assert payload["目标节点"] == {}
    assert "不得复述上一句台词" in internal_rules["本轮演绎指令"]
    assert "不得把其中的名称、数量或秘密当成默认话题" in internal_rules["本轮演绎指令"]
    assert "再次讨论已经完成的上一个 Choice" in internal_rules["本轮演绎指令"]
    assert "不是必须反复讨论的话题" in THEATER_TURN_SYSTEM_PROMPT
    assert "与当前显示文案和作者原始文案有实质差异" in THEATER_TURN_SYSTEM_PROMPT
    assert "作者原始文案" in THEATER_TURN_SYSTEM_PROMPT
    assert "不能增加新的完成条件" in THEATER_TURN_SYSTEM_PROMPT
    assert list(payload)[-1] == "本轮唯一回应目标"
    assert payload["本轮唯一回应目标"] == "你愿意说说真实感受吗？"
    assert "不得把同一个问题反问玩家" in internal_rules["本轮回应要求"]
    assert "不得只换一种说法把同一个问题反问玩家" in THEATER_TURN_SYSTEM_PROMPT
    assert "不可偏移的世界边界" not in payload
    assert internal_rules["使用方式"].startswith("只用于约束生成结果")
    assert "不得在 narration 或 dialogue 中引用" in THEATER_TURN_SYSTEM_PROMPT


def test_roleplay_prompt_includes_story_output_guardrails():
    """剧本输出硬边界必须进入模型上下文，且同时由代码在展示前校验。"""  # noqa: DOCSTRING_CJK
    _, user_prompt = build_theater_turn_prompts(
        lanlan_name="糖糖",
        story={
            "background": "纪念祭入口",
            "runtime_guardrails": {
                "conditional_output_guards": [
                    {"until_fact": {"subject": "pair", "predicate": "is", "object": "confirmed"}, "forbidden_phrases": ["挽住手臂"]}
                ]
            },
        },
        scene={"title": "入口", "text": "两人准备出发。"},
        node={"node_id": "node_depart"},
        user_message="我们先去哪里？",
        progress_kind="roleplay_response",
        callback="",
        public_state={},
        recent_turns=[],
        character_profile="",
        choice_options=[],
    )
    payload, internal_rules = _prompt_sections(user_prompt)
    assert "输出硬边界" not in payload
    assert internal_rules["输出硬边界"]["conditional_output_guards"][0][
        "forbidden_phrases"
    ] == ["挽住手臂"]


def test_graph_progress_prompt_personalizes_author_meaning_and_preserves_handoff():
    """剧情推进必须按当前人格转述作者含义，并知道对白后会出现哪些推荐选项。"""  # noqa: DOCSTRING_CJK
    _, user_prompt = build_theater_turn_prompts(
        lanlan_name="霜瞳",
        story={"background": "约会前的家中", "theme": "共同决定"},
        scene={"title": "门口", "text": "歪星星挂在纸袋边。"},
        node={
            "title": "保留歪星星",
            "summary": "玩家接住挂坠。",
            "scripted_dialogue": "谢谢你留下它。今天如果想改路线，我们就一起商量。",
            "runtime_generation_guide": {"catgirl_raw_intent": "猫娘嘴硬地开心，并提出共同商量路线。"},
        },
        user_message="接住挂坠",
        progress_kind="graph_progress",
        callback="你把挂坠扣到包上。",
        public_state={},
        recent_turns=[],
        character_profile="自称本小姐；傲娇嘴硬",
        choice_options=[
            {
                "choice_id": "choice_agree",
                "label": "“好。有想改的地方就告诉我，我们一起决定。”",
                "choice_mode": "dialogue",
            }
        ],
    )
    payload, internal_rules = _prompt_sections(user_prompt)

    assert payload["猫娘人格摘要"] == "自称本小姐；傲娇嘴硬"
    assert payload["目标节点"]["author_dialogue_meaning"].startswith("谢谢你留下它")
    assert "narrator_intent" not in payload["目标节点"]
    assert "catgirl_intent" not in payload["目标节点"]
    assert internal_rules["作者演绎意图"]["猫娘意图"].startswith("猫娘嘴硬地开心")
    assert "performance_instruction" not in payload["目标节点"]
    assert "不能照读原句" in internal_rules["本轮演绎指令"]
    assert "必须使用该自称" in internal_rules["本轮演绎指令"]
    assert "内部规则只通过不越界来执行" in internal_rules["本轮演绎指令"]
    assert "不得转述成对白" in internal_rules["本轮演绎指令"]
    assert payload["下一轮推荐选项"] == [
        {
            "显示文案": "“好。有想改的地方就告诉我，我们一起决定。”",
            "类型": "dialogue",
        }
    ]
    assert payload["当前可推进选项"] == []
    assert "自然建立下一轮推荐选项所需前提" in internal_rules["本轮回应要求"]
    assert "不能逐字照抄" in THEATER_TURN_SYSTEM_PROMPT
    assert "不能只替换同义词" in THEATER_TURN_SYSTEM_PROMPT
    assert "不要让猫娘向玩家宣读" in THEATER_TURN_SYSTEM_PROMPT


def test_punctuation_only_input_requests_new_explanation():
    """玩家只发问号时必须要求新解释，不能把上一回答原样播放。"""  # noqa: DOCSTRING_CJK
    _, user_prompt = build_theater_turn_prompts(
        lanlan_name="霜瞳",
        story={"background": "活动厅", "theme": "久别重逢"},
        scene={"title": "灯影里的重逢", "text": "旧合照落在两人之间。"},
        node={"title": "先叫出她现在的名字", "summary": "已经完成的旧动作"},
        user_message="？",
        progress_kind="roleplay_response",
        callback="",
        public_state={},
        recent_turns=[],
        character_profile="",
        choice_options=[],
    )
    payload, internal_rules = _prompt_sections(user_prompt)
    assert "补充新的解释" in internal_rules["本轮回应要求"]
    assert payload["本轮唯一回应目标"] == "玩家没有理解你上一句话，正在等你换一种说法解释清楚。"


def test_prompt_tolerates_invalid_optional_story_sections():
    """可选剧本段落类型异常时应使用安全默认值，不能阻断模型回退链路。"""  # noqa: DOCSTRING_CJK
    _, user_prompt = build_theater_turn_prompts(
        lanlan_name="兰兰",
        story={"background": "旧教室", "seed": None, "scenario_card": []},
        scene={"title": "教室", "text": "窗外有蝉鸣。"},
        node={"title": "重逢", "summary": "两人再次见面。"},
        user_message="你好。",
        progress_kind="roleplay_response",
        callback="",
        public_state={},
        recent_turns=[],
        character_profile="",
        choice_options=[],
    )
    payload, internal_rules = _prompt_sections(user_prompt)
    assert payload["玩家身份"] == "故事参与者"
    assert internal_rules["禁止假设"] == []
    assert internal_rules["主线目标"] == "按作者静态剧情推进并正常结束"


def test_recent_context_includes_assistant_narration_and_dialogue():
    """最近上下文必须独立保留对白，不能被较长旁白截断。"""  # noqa: DOCSTRING_CJK
    turns = llm._recent_public_turns(
        [{"role": "assistant", "narration": "她把合同推回桌面。", "text": "这一条需要修改喵。"}]
    )
    assert turns == [
        {
            "role": "assistant",
            "dialogue": "这一条需要修改喵。",
            "narration": "她把合同推回桌面。",
        }
    ]


def test_roleplay_repetition_removes_old_narration_and_dialogue_clause():
    """真实坏记录中的重复旁白和重复句段必须删除，并保留新的猫娘反应。"""  # noqa: DOCSTRING_CJK
    previous_narration = "霜瞳的耳尖微不可察地抖动了一下，视线从旧照移开，落在你平静的脸上。"
    previous_dialogue = "别误会，这只是夹在文件里忘了扔，才不是特意留着怀念什么的喵。"
    parsed = {
        "narration": previous_narration,
        "dialogue": "你那是什么表情……才不是特意留着怀念什么的喵！照片先还我。",
        "choice_rewrites": [],
    }
    cleaned = llm._sanitize_roleplay_repetition(
        parsed,
        [{"role": "assistant", "narration": previous_narration, "text": previous_dialogue}],
    )
    assert cleaned["narration"] == ""
    assert "才不是特意留着怀念" not in cleaned["dialogue"]
    assert cleaned["dialogue"] == "你那是什么表情……照片先还我。"


def test_roleplay_repetition_checks_two_recent_assistant_turns():
    """玩家用问号追问后，猫娘也不能重播问号之前已经说过的长句。"""  # noqa: DOCSTRING_CJK
    repeated = "既然你看得这么清楚，那就别想再像七年前那样逃走了喵。"
    cleaned = llm._sanitize_roleplay_repetition(
        {"narration": "", "dialogue": "切……" + repeated, "choice_rewrites": []},
        [
            {"role": "assistant", "text": repeated, "narration": "她移开视线。"},
            {"role": "user", "text": "？"},
            {"role": "assistant", "text": "你那个问号是什么意思喵？", "narration": ""},
        ],
    )
    assert repeated not in cleaned["dialogue"]
    assert cleaned["dialogue"] == "切……"


def test_assistant_echo_detection_rejects_player_choice_line():
    """猫娘近似照读玩家 Choice 时必须识别为角色反转。"""  # noqa: DOCSTRING_CJK
    assert llm._assistant_echoes_user(
        "哼，既然数据无误……那就一起打开看看最后的真相吧，别手抖喵！",
        "既然数据无误，那就一起打开保险柜看看最后的真相吧",
    ) is True
    assert llm._assistant_echoes_user("我还需要一点时间想清楚喵。", "我会坐下来听你慢慢说。") is False


@pytest.mark.asyncio
async def test_graph_progress_echo_falls_back_to_author_dialogue(monkeypatch):
    """模型把玩家 Choice 当猫娘台词复述时，必须改用作者节点对白。"""  # noqa: DOCSTRING_CJK

    class _FakeClient:
        """返回可复现角色反转 JSON 的异步模型客户端。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            return type(
                "Result",
                (),
                {"content": '{"narration":"保险柜被打开。","dialogue":"既然数据无误，那就一起打开保险柜看看最后的真相吧。","choice_rewrites":[]}'},
            )()

    async def _create_fake_client(*_args, **_kwargs):
        """绕过真实网络并返回可控客户端。"""  # noqa: DOCSTRING_CJK
        return _FakeClient()

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    result = await llm.generate_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="霜瞳",
        story={"background": "旧录音室"},
        scene={"text": "保险柜在墙角。"},
        node={"scripted_dialogue": "钥匙给你一半，我们一起打开它喵。", "summary": "共同打开保险柜。"},
        user_message="既然数据无误，那就一起打开保险柜看看最后的真相吧",
        progress_kind="graph_progress",
        callback="你们共同打开保险柜。",
        state={"scene_notes": ["刚才有过自由互动"]},
        recent_turns=[],
    )
    assert result["dialogue"] == "钥匙给你一半，我们一起打开它喵。"
    assert result["narration"] == "你们共同打开保险柜。"


@pytest.mark.asyncio
async def test_graph_progress_repairs_persona_coercion_once(monkeypatch):
    """傲娇人格把共同商量演成不可拒绝命令时，必须纠错一次并保留作者边界。"""  # noqa: DOCSTRING_CJK
    outputs = [
        '{"narration":"星星被扣好。","dialogue":"那今天都听本小姐的，不许有异议喵。","choice_rewrites":[]}',
        '{"narration":"星星被扣好。","dialogue":"本小姐可没打算一个人说了算。想停就停，想换地方也得我们两个都点头喵。","choice_rewrites":[]}',
    ]
    calls = 0

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            nonlocal calls
            content = outputs[calls]
            calls += 1
            return type("Result", (), {"content": content})()

    async def _create_fake_client(*_args, **_kwargs):
        return _FakeClient()

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    result = await llm.generate_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="霜瞳",
        story={"background": "约会前的家中"},
        scene={"text": "星星挂坠被接住。"},
        node={
            "scripted_dialogue": "今天如果想停或改路线，我们就一起商量。",
            "summary": "两人约定共同商量路线。",
        },
        user_message="接住挂坠",
        progress_kind="graph_progress",
        callback="你把挂坠扣到包上。",
        state={"scene_notes": []},
        recent_turns=[],
    )

    assert calls == 2
    assert "不许有异议" not in result["dialogue"]
    assert "我们两个都点头" in result["dialogue"]
    assert result["narration"] == "你把挂坠扣到包上。"


@pytest.mark.asyncio
async def test_roleplay_repairs_unchanged_choice_label_once(monkeypatch):
    """自由对话原样返回旧按钮时必须重试，并产出承接本轮内容的新文案。"""  # noqa: DOCSTRING_CJK
    outputs = [
        '{"narration":"","dialogue":"那我们走吧喵。","matched_choice_id":"","choice_rewrites":['
        '{"choice_id":"choice_depart","label":"“好，那就一起出发吧。”"}]}',
        '{"narration":"","dialogue":"出发前先把票收好，到了入口再决定第一站喵。",'
        '"matched_choice_id":"","choice_rewrites":['
        '{"choice_id":"choice_depart","label":"“好，我收好票，到了入口再和你决定。”"}]}',
    ]
    calls = 0

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            nonlocal calls
            content = outputs[calls]
            calls += 1
            return type("Result", (), {"content": content})()

    async def _create_fake_client(*_args, **_kwargs):
        return _FakeClient()

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    result = await llm.generate_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="希尔",
        story={"background": "约会前的家中"},
        scene={"text": "两张入场券放在纸袋里。"},
        node={"summary": "猫娘邀请玩家同行。"},
        user_message="先去哪里呢？",
        progress_kind="roleplay_response",
        callback="",
        state={"scene_notes": []},
        recent_turns=[{"role": "assistant", "text": "你愿意和我一起出发吗？"}],
        choice_options=[
            {
                "choice_id": "choice_depart",
                "label": "“好，那就一起出发吧。”",
                "author_label": "“好，那就一起出发吧。”",
                "choice_mode": "dialogue",
            }
        ],
    )

    assert calls == 2
    assert result["choice_rewrites"] == [
        {"choice_id": "choice_depart", "label": "“好，我收好票，到了入口再和你决定。”"}
    ]


@pytest.mark.asyncio
async def test_graph_progress_repairs_authored_forbidden_topic_phrase(monkeypatch):
    """作者声明暂时禁用的话题词被模型擅自补入时，必须纠错后再展示。"""  # noqa: DOCSTRING_CJK
    outputs = [
        '{"narration":"你收下入场券。","dialogue":"那份七项安排就留到入口再说喵。","choice_rewrites":[]}',
        '{"narration":"你收下入场券。","dialogue":"想保密的就留作惊喜，到了入口再告诉我路线喵。","choice_rewrites":[]}',
    ]
    calls = 0

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            nonlocal calls
            content = outputs[calls]
            calls += 1
            return type("Result", (), {"content": content})()

    async def _create_fake_client(*_args, **_kwargs):
        return _FakeClient()

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    result = await llm.generate_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="希尔",
        story={"background": "约会前的家中"},
        scene={"text": "两张入场券放在纸袋里。"},
        node={
            "summary": "玩家接受邀请。",
            "scripted_dialogue": "想保密的就留作惊喜，到了入口再告诉我路线。",
            "runtime_generation_guide": {"forbidden_dialogue_phrases": ["七项安排"]},
        },
        user_message="出发",
        progress_kind="graph_progress",
        callback="你收下入场券。",
        state={"scene_notes": []},
        recent_turns=[],
    )

    assert calls == 2
    assert "七项安排" not in result["dialogue"]
    assert "留作惊喜" in result["dialogue"]


def test_authored_performance_requires_declared_self_name_when_speaking_about_self():
    """人格明确声明自称时，作者演出不得退化成无人格的第一人称近义改写。"""  # noqa: DOCSTRING_CJK
    reason = llm._performance_repair_reason(
        {"dialogue": "我本来想把它重缝。"},
        progress_kind="graph_progress",
        user_message="接住挂坠",
        node={"scripted_dialogue": "我本来想把它重缝。"},
        character_profile="自称: 本小姐\n核心特质: 傲娇嘴硬",
    )
    assert reason == "persona_self_name_missing"


def test_roleplay_rejects_mirroring_players_location_question():
    """玩家问第一站时，猫娘不得只把同一个去向问题反问回来。"""  # noqa: DOCSTRING_CJK
    bad_reason = llm._performance_repair_reason(
        {"narration": "", "dialogue": "主人先告诉糖糖，我们第一站要去哪里呀？"},
        progress_kind="roleplay_response",
        user_message="我们先去哪里？",
        node={},
        character_profile="",
    )
    good_reason = llm._performance_repair_reason(
        {"narration": "", "dialogue": "先去星灯长廊吧，那里离入口最近。"},
        progress_kind="roleplay_response",
        user_message="我们先去哪里？",
        node={},
        character_profile="",
    )
    assert bad_reason == "current_question_mirrored"
    assert good_reason == ""


def test_roleplay_rejects_unintroduced_named_destination():
    """回答去向时不得临场发明公开上下文里不存在的命名摊位。"""  # noqa: DOCSTRING_CJK
    bad_reason = llm._performance_repair_reason(
        {"narration": "", "dialogue": "先去入口旁的「星愿风铃」摊吧。"},
        progress_kind="roleplay_response",
        user_message="我们先去哪里？",
        node={},
        character_profile="",
        grounding_text="已经公开的最近目的地是纪念祭入口。",
    )
    good_reason = llm._performance_repair_reason(
        {"narration": "", "dialogue": "先到纪念祭入口吧，之后再看你愿意公开的路线。"},
        progress_kind="roleplay_response",
        user_message="我们先去哪里？",
        node={},
        character_profile="",
        grounding_text="已经公开的最近目的地是纪念祭入口。",
    )
    assert bad_reason == "ungrounded_named_destination"
    assert good_reason == ""


def test_story_output_guardrails_cover_narration_and_dialogue():
    """关系边界不能只检查对白，旁白中的越界接触也必须拦截。"""  # noqa: DOCSTRING_CJK
    reason = llm._performance_repair_reason(
        {"narration": "糖糖顺势挽住你的手臂。", "dialogue": "我们出发吧喵。"},
        progress_kind="roleplay_response",
        user_message="出发吧。",
        node={},
        character_profile="",
        story={
            "runtime_guardrails": {
                "conditional_output_guards": [
                    {
                        "until_fact": {"subject": "player", "predicate": "chooses", "object": "relationship"},
                        "forbidden_phrases": ["挽住你的手臂"],
                    }
                ]
            }
        },
        state={"narrative_facts": []},
    )
    assert reason == "forbidden_output_phrase_used"

    allowed_after_confirmation = llm._performance_repair_reason(
        {"narration": "糖糖征得同意后挽住你的手臂。", "dialogue": "这样可以吗？"},
        progress_kind="roleplay_response",
        user_message="可以。",
        node={},
        character_profile="",
        story={
            "runtime_guardrails": {
                "conditional_output_guards": [
                    {
                        "until_fact": {"subject": "player", "predicate": "chooses", "object": "relationship"},
                        "forbidden_phrases": ["挽住你的手臂"],
                    }
                ]
            }
        },
        state={"narrative_facts": [{"subject": "player", "predicate": "chooses", "object": "relationship"}]},
    )
    assert allowed_after_confirmation == ""


def test_story_silent_rules_cannot_be_explained_in_dialogue():
    """内部规则只能改变行为，猫娘不能把它们组织成免责声明说给玩家。"""  # noqa: DOCSTRING_CJK
    story = {
        "runtime_guardrails": {
            "forbidden_output_patterns": [
                "中途.{0,16}(?:停|换).{0,20}(?:商量|决定)",
                "入场券.{0,16}(?:不会|不能|不).{0,16}(?:安排|决定)",
            ]
        }
    }
    rule_dump = llm._performance_repair_reason(
        {"narration": "", "dialogue": "中途想停或者换地方，我们都可以一起商量喵。"},
        progress_kind="graph_progress",
        user_message="出发",
        node={},
        character_profile="",
        story=story,
    )
    natural_reply = llm._performance_repair_reason(
        {"narration": "", "dialogue": "那就出发喵，先去旧街入口。"},
        progress_kind="graph_progress",
        user_message="出发",
        node={},
        character_profile="",
        story=story,
    )
    assert rule_dump == "internal_rule_exposed"
    assert natural_reply == ""


@pytest.mark.asyncio
async def test_choice_rewrite_retry_cannot_replace_valid_answer_with_mirrored_question(monkeypatch):
    """推荐项纠错若让对白退化成反问，应保留首版合格回答并退回作者按钮。"""  # noqa: DOCSTRING_CJK
    outputs = [
        (
            '{"narration":"","dialogue":"先到纪念祭入口吧，到了那里再看路线。",'
            '"matched_choice_id":"","choice_rewrites":['
            '{"choice_id":"choice_route","label":"抵达入口后，把愿意公开的路线面递给她看"}]}'
        ),
        (
            '{"narration":"","dialogue":"主人先告诉糖糖，我们第一站要去哪里呀？",'
            '"matched_choice_id":"","choice_rewrites":['
            '{"choice_id":"choice_route","label":"到了入口，把想分享的路线交给她"}]}'
        ),
    ]
    calls = 0

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            nonlocal calls
            content = outputs[calls]
            calls += 1
            return type("Result", (), {"content": content})()

    async def _create_fake_client(*_args, **_kwargs):
        return _FakeClient()

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    result = await llm.generate_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="糖糖",
        story={"background": "两人正前往纪念祭入口。"},
        scene={"text": "旧街入口就在前方。"},
        node={"node_id": "node_depart"},
        user_message="我们先去哪里？",
        progress_kind="roleplay_response",
        callback="",
        state={"scene_notes": []},
        recent_turns=[],
        choice_options=[
            {
                "choice_id": "choice_route",
                "label": "抵达入口后，把愿意公开的路线面递给她看",
                "author_label": "抵达入口后，把愿意公开的路线面递给她看",
                "choice_mode": "action",
            }
        ],
    )

    assert calls == 2
    assert result["dialogue"] == "先到纪念祭入口吧，到了那里再看路线。"
    assert result["choice_rewrites"] == []


@pytest.mark.parametrize(
    "performed_dialogue",
    [
        "要是中途本小姐想停或者换地方，我们就当场商量，不许有意见喵。",
        "临时起意的事由我们共同决定，不过得先问过本小姐才行。",
    ],
)
def test_author_consent_boundary_rejects_real_single_party_approval_phrases(performed_dialogue):
    """真实演绎出现的单方否决或批准句式不能伪装成共同决定。"""  # noqa: DOCSTRING_CJK
    assert llm._violates_author_consent_boundary(
        "中途想停或改路线时一起商量，由我们两个决定。",
        performed_dialogue,
        self_name="本小姐",
    ) is True


@pytest.mark.asyncio
async def test_graph_progress_uses_author_callback_for_narration(monkeypatch):
    """模型不得把“等待同意”擅自写成已经按下播放键。"""  # noqa: DOCSTRING_CJK

    class _FakeClient:
        """返回会抢跑下一节点动作的可控模型结果。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            return type(
                "Result",
                (),
                {"content": '{"narration":"她直接按下了播放键。","dialogue":"等我准备好再说喵。","choice_rewrites":[]}'},
            )()

    async def _create_fake_client(*_args, **_kwargs):
        """绕过真实网络并返回抢跑剧情的客户端。"""  # noqa: DOCSTRING_CJK
        return _FakeClient()

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    result = await llm.generate_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="霜瞳",
        story={"background": "旧档案室"},
        scene={"text": "磁带机还没有启动。"},
        node={"scripted_dialogue": "我还没准备好喵。", "summary": "玩家等待猫娘同意。"},
        user_message="把手停在播放键旁，等她亲自决定",
        progress_kind="graph_progress",
        callback="你没有碰播放键，只把手收回桌边，等猫娘自己作出决定。",
        state={"scene_notes": []},
        recent_turns=[],
    )
    assert result["narration"] == "你没有碰播放键，只把手收回桌边，等猫娘自己作出决定。"
    assert "按下" not in result["narration"]


@pytest.mark.asyncio
async def test_natural_language_match_uses_author_choice_callback(monkeypatch):
    """自然语言命中只选择稳定 ID，公开旁白仍必须来自作者 callback。"""  # noqa: DOCSTRING_CJK

    class _FakeClient:
        """返回自然语言命中和抢写旁白的可控模型结果。"""  # noqa: DOCSTRING_CJK

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, _messages):
            return type(
                "Result",
                (),
                {
                    "content": (
                        '{"narration":"她替玩家把照片收走了。","dialogue":"……算你还知道给人留点余地喵。",'
                        '"matched_choice_id":"choice_return_photo","choice_rewrites":[]}'
                    )
                },
            )()

    async def _create_fake_client(*_args, **_kwargs):
        """绕过真实网络并返回自然语言路由结果。"""  # noqa: DOCSTRING_CJK
        return _FakeClient()

    monkeypatch.setattr(llm, "create_chat_llm_async", _create_fake_client)
    result = await llm.generate_turn_async(
        config_manager=_ModelConfig(),
        lanlan_name="霜瞳",
        story={"background": "酒店走廊"},
        scene={"text": "旧合照落在两人之间。"},
        node={"summary": "两人已经认出彼此。"},
        user_message="我把照片放回文件袋。",
        progress_kind="roleplay_response",
        callback="",
        state={"scene_notes": []},
        recent_turns=[],
        choice_options=[
            {
                "choice_id": "choice_return_photo",
                "label": "把照片放回文件袋，不追问她为何留着",
                "author_label": "把照片放回文件袋，不追问她为何留着",
                "choice_mode": "action",
                "callback": "你将照片平整地放回文件袋。",
                "target_summary": "玩家归还照片。",
                "target_catgirl_intent": "猫娘嘴硬地接过照片。",
                "target_scripted_dialogue": "照片只是夹在旧文件里忘了扔喵。",
            }
        ],
    )
    assert result["matched_choice_id"] == "choice_return_photo"
    assert result["narration"] == "你将照片平整地放回文件袋。"


def test_near_duplicate_dialogue_ignores_punctuation_and_final_neko_particle():
    """只删除句尾“喵”的上一句复述仍应被识别，避免机械连续对白。"""  # noqa: DOCSTRING_CJK
    recent = [{"role": "assistant", "text": "你还记得……算了，记得也不代表什么。今晚我只是来交设备的喵。"}]
    assert llm._repeats_recent_dialogue("你还记得，算了，记得也不代表什么。今晚我只是来交设备的。", recent) is True
    assert llm._repeats_recent_dialogue("我其实还没想好该怎么面对你。", recent) is False


def test_model_choice_rewrites_only_accept_current_stable_ids():
    """模型只能改写或命中当前按钮，未知 ID、重复 ID 和内部术语必须被丢弃。"""  # noqa: DOCSTRING_CJK
    result = llm._parse_output(
        '{"narration":"","dialogue":"我一直留着它喵。","matched_choice_id":"choice_keep","choice_rewrites":['
        '{"choice_id":"choice_keep","label":"收好照片，回应她刚才的坦白"},'
        '{"choice_id":"choice_keep","label":"重复覆盖"},'
        '{"choice_id":"choice_unknown","label":"跳到未知结局"},'
        '{"choice_id":"choice_wait","label":"查看 node_id"}]}' ,
        progress_kind="roleplay_response",
        allowed_choice_ids={"choice_keep", "choice_wait"},
    )
    assert result == {
        "narration": "",
        "dialogue": "我一直留着它喵。",
        "choice_rewrites": [{"choice_id": "choice_keep", "label": "收好照片，回应她刚才的坦白"}],
        "matched_choice_id": "choice_keep",
        "observed_intent_id": "",
    }

    unknown_match = llm._parse_output(
        '{"narration":"","dialogue":"先等等喵。","matched_choice_id":"choice_unknown","choice_rewrites":[]}',
        progress_kind="roleplay_response",
        allowed_choice_ids={"choice_keep"},
    )
    assert unknown_match["matched_choice_id"] == ""


def test_model_latent_intent_only_accepts_current_author_whitelist():
    """模型只能观察当前作者 intent_id；可见 Choice 命中时必须压过隐藏意图。"""  # noqa: DOCSTRING_CJK
    observed = llm._parse_output(
        '{"narration":"","dialogue":"我会认真回答你喵。","matched_choice_id":"",'
        '"observed_intent_id":"intent_impression","choice_rewrites":[]}',
        progress_kind="roleplay_response",
        allowed_choice_ids={"choice_main"},
        allowed_intent_ids={"intent_impression"},
    )
    assert observed["observed_intent_id"] == "intent_impression"

    unknown = llm._parse_output(
        '{"narration":"","dialogue":"先说眼前的事喵。","matched_choice_id":"",'
        '"observed_intent_id":"intent_unknown","choice_rewrites":[]}',
        progress_kind="roleplay_response",
        allowed_intent_ids={"intent_impression"},
    )
    assert unknown["observed_intent_id"] == ""

    visible_wins = llm._parse_output(
        '{"narration":"","dialogue":"那就按你说的做喵。","matched_choice_id":"choice_main",'
        '"observed_intent_id":"intent_impression","choice_rewrites":[]}',
        progress_kind="roleplay_response",
        allowed_choice_ids={"choice_main"},
        allowed_intent_ids={"intent_impression"},
    )
    assert visible_wins["matched_choice_id"] == "choice_main"
    assert visible_wins["observed_intent_id"] == ""
    assert "必须先完整回应玩家本轮问题" in THEATER_TURN_SYSTEM_PROMPT
    assert "当前可见物件、天气" in THEATER_TURN_SYSTEM_PROMPT


def test_roleplay_prompt_routes_only_explicit_current_choice_completion():
    """自然语言只可命中唯一当前 Choice，询问、否定、假设和歧义必须停留。"""  # noqa: DOCSTRING_CJK
    assert "询问原因" in THEATER_TURN_SYSTEM_PROMPT
    assert "否定" in THEATER_TURN_SYSTEM_PROMPT
    assert "唯一" in THEATER_TURN_SYSTEM_PROMPT
    assert "不能仅凭句末的“？”或“?”" in THEATER_TURN_SYSTEM_PROMPT
    assert "“那就……出发？”" in THEATER_TURN_SYSTEM_PROMPT
    assert "“为什么出发？”“现在出发吗？”“你想出发吗？”" in THEATER_TURN_SYSTEM_PROMPT
    assert "必须为“当前可推进选项”中的每个 choice_id 返回一项" in THEATER_TURN_SYSTEM_PROMPT
    assert "不得再写“不追问她为何留着”" in THEATER_TURN_SYSTEM_PROMPT

    _, user_prompt = build_theater_turn_prompts(
        lanlan_name="霜瞳",
        story={"background": "活动厅", "theme": "久别重逢"},
        scene={"title": "灯影里的重逢", "text": "旧合照落在两人之间。"},
        node={"title": "认出彼此", "summary": "两人已经认出对方。"},
        user_message="我把照片放回文件袋。",
        progress_kind="roleplay_response",
        callback="",
        public_state={},
        recent_turns=[],
        character_profile="",
        choice_options=[
            {
                "choice_id": "choice_return_photo",
                "label": "把照片放回文件袋，不追问她为何留着",
                "choice_mode": "action",
                "callback": "你将照片平整地放回文件袋。",
                "target_summary": "玩家归还照片并尊重猫娘是否解释。",
                "target_catgirl_intent": "猫娘嘴硬地接过照片。",
                "completion_phrases": ["放回去", "还给她"],
            }
        ],
    )
    payload, internal_rules = _prompt_sections(user_prompt)
    candidate = payload["当前可推进选项"][0]
    assert candidate["choice_id"] == "choice_return_photo"
    assert candidate["作者回调"] == "你将照片平整地放回文件袋。"
    assert "目标结果" not in candidate
    assert "作者完成表达" not in candidate
    assert internal_rules["当前选项路由语义"][0]["目标结果"] == "玩家归还照片并尊重猫娘是否解释。"
    assert internal_rules["当前选项路由语义"][0]["作者完成表达"] == ["放回去", "还给她"]
    assert "句末单独的问号不能覆盖玩家已经明确说出的行动或接受" in internal_rules["本轮回应要求"]


def test_character_profile_only_reads_current_configured_catgirl(tmp_path):
    """人格摘要只能读取当前已配置猫娘，路径片段和其他猫娘都必须被拒绝。"""  # noqa: DOCSTRING_CJK
    safe_path = tmp_path / "memory" / "安全猫娘" / "persona.json"
    safe_path.parent.mkdir(parents=True)
    safe_path.write_text(
        json.dumps({"neko": {"facts": [{"text": "喜欢雨天散步"}]}}),
        encoding="utf-8",
    )
    escaped_path = tmp_path / "private" / "persona.json"
    escaped_path.parent.mkdir(parents=True)
    escaped_path.write_text(
        json.dumps({"neko": {"facts": [{"text": "不应泄露的秘密"}]}}),
        encoding="utf-8",
    )
    config = _CharacterConfig(tmp_path)

    assert llm._load_character_profile(config, "安全猫娘") == "喜欢雨天散步"
    assert llm._load_character_profile(config, "../private") == ""
    assert llm._load_character_profile(config, "其他猫娘") == ""
