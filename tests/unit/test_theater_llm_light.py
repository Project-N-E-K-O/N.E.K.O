"""验证单次演绎模型的结构、上下文、世界边界和安全回退。"""  # noqa: DOCSTRING_CJK

import json

from config.prompts.prompts_theater import THEATER_TURN_SYSTEM_PROMPT, build_theater_turn_prompts
from services.theater import llm


def test_fallback_roleplay_responds_to_user_message():
    """离线角色互动必须先回应玩家原话且不生成旁白。"""  # noqa: DOCSTRING_CJK
    result = llm.fallback_turn(
        lanlan_name="兰兰",
        scene={"text": "雨夜窗边"},
        node={},
        user_message="我有点担心你",
        progress_kind="roleplay_response",
        callback="",
    )
    assert result["narration"] == ""
    assert "我有点担心你" in result["dialogue"]
    assert "留在眼前这件事" in result["dialogue"]
    assert "放在心上" not in result["dialogue"]


def test_model_output_requires_narration_for_story_progress():
    """剧情推进缺少旁白时必须拒绝模型结果并回退作者文本。"""  # noqa: DOCSTRING_CJK
    assert llm._parse_output('{"narration":"","dialogue":"继续吧喵"}', progress_kind="graph_progress") is None
    assert llm._parse_output('{"narration":"灯亮了。","dialogue":"继续吧喵"}', progress_kind="graph_progress") == {
        "narration": "灯亮了。",
        "dialogue": "继续吧喵",
        "choice_rewrites": [],
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
    """自由互动不能再次注入刚播过的作者固定台词。"""  # noqa: DOCSTRING_CJK
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
    payload = json.loads(user_prompt.split("\n", 1)[1])
    assert "scripted_dialogue" not in payload["目标节点"]
    assert "不要复述上一句台词" in payload["目标节点"]["roleplay_instruction"]


def test_recent_context_includes_assistant_narration_and_dialogue():
    """最近上下文必须同时包含已发生动作和对白，避免下一轮重演动作。"""  # noqa: DOCSTRING_CJK
    turns = llm._recent_public_turns(
        [{"role": "assistant", "narration": "她把合同推回桌面。", "text": "这一条需要修改喵。"}]
    )
    assert turns == [{"role": "assistant", "text": "她把合同推回桌面。 这一条需要修改喵。"}]


def test_near_duplicate_dialogue_ignores_punctuation_and_final_neko_particle():
    """只删除句尾“喵”的上一句复述仍应被识别，避免机械连续对白。"""  # noqa: DOCSTRING_CJK
    recent = [{"role": "assistant", "text": "你还记得……算了，记得也不代表什么。今晚我只是来交设备的喵。"}]
    assert llm._repeats_recent_dialogue("你还记得，算了，记得也不代表什么。今晚我只是来交设备的。", recent) is True
    assert llm._repeats_recent_dialogue("我其实还没想好该怎么面对你。", recent) is False


def test_model_choice_rewrites_only_accept_current_stable_ids():
    """模型只能改当前按钮文案，未知 ID、重复 ID 和内部术语必须被丢弃。"""  # noqa: DOCSTRING_CJK
    result = llm._parse_output(
        '{"narration":"","dialogue":"我一直留着它喵。","choice_rewrites":['
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
    }
