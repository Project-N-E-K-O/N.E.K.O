"""显式开关控制的小剧场真实模型质量 smoke。"""  # noqa: DOCSTRING_CJK

import os

import pytest

from services.theater import llm


class _EnvConfigManager:
    """从隔离环境变量构造 summary 档配置。"""  # noqa: DOCSTRING_CJK

    def get_model_api_config(self, tier: str) -> dict[str, str]:
        """真实 smoke 同样只能读取 summary 档。"""  # noqa: DOCSTRING_CJK
        assert tier == "summary"
        return {
            "model": os.environ.get("NEKO_THEATER_LLM_SMOKE_MODEL", ""),
            "base_url": os.environ.get("NEKO_THEATER_LLM_SMOKE_BASE_URL", ""),
            "api_key": os.environ.get("NEKO_THEATER_LLM_SMOKE_API_KEY", ""),
            "provider_type": os.environ.get("NEKO_THEATER_LLM_SMOKE_PROVIDER_TYPE", "openai_compatible"),
        }


def _require_environment() -> None:
    """没有显式开关时跳过，避免测试误用用户模型额度。"""  # noqa: DOCSTRING_CJK
    if os.environ.get("NEKO_RUN_THEATER_LLM_SMOKE") != "1":
        pytest.skip("set NEKO_RUN_THEATER_LLM_SMOKE=1 to run the theater real-model smoke")
    if not os.environ.get("NEKO_THEATER_LLM_SMOKE_MODEL") or not os.environ.get("NEKO_THEATER_LLM_SMOKE_BASE_URL"):
        pytest.skip("missing theater real-model smoke configuration")


@pytest.mark.asyncio
async def test_real_model_returns_safe_narration_and_dialogue():
    """真实模型必须返回可直接展示的一段旁白和猫娘对白。"""  # noqa: DOCSTRING_CJK
    _require_environment()
    result = await llm.generate_turn_async(
        config_manager=_EnvConfigManager(),
        lanlan_name="兰兰",
        story={"background": "停电的雨夜房间", "theme": "低压陪伴"},
        scene={"title": "雨夜窗边", "text": "备用灯还没有亮。"},
        node={"title": "一起找灯", "summary": "玩家提出一起寻找备用灯。"},
        user_message="我陪你一起找备用灯",
        progress_kind="graph_progress",
        callback="你们把注意力放到桌边。",
        state={},
        recent_turns=[],
    )
    assert result["narration"].strip()
    assert result["dialogue"].strip()
    assert not any(term in (result["narration"] + result["dialogue"]) for term in ("node_id", "scene_id", "prompt"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_message", "expected_match"),
    [
        ("你为什么还留着这张照片？", ""),
        ("我把照片放回文件袋。", "choice_return_photo"),
    ],
)
async def test_real_model_routes_only_explicit_current_choice(user_message, expected_match):
    """真实模型必须区分围绕 Choice 的追问与已经实施的当前行动。"""  # noqa: DOCSTRING_CJK
    _require_environment()
    result = await llm.generate_turn_async(
        config_manager=_EnvConfigManager(),
        lanlan_name="霜瞳",
        story={"background": "活动散场后的酒店走廊", "theme": "久别重逢"},
        scene={"title": "灯影里的重逢", "text": "一张七年前的合照落在你们之间。"},
        node={"title": "认出彼此", "summary": "玩家和猫娘已经认出对方。"},
        user_message=user_message,
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
                "callback": "你将照片平整地放回文件袋，给她留出决定是否解释的空间。",
                "target_summary": "玩家归还照片，没有把保存照片当作复合承诺。",
                "target_catgirl_intent": "猫娘嘴硬地接过照片。",
                "target_scripted_dialogue": "照片只是夹在旧文件里忘了扔喵。",
            }
        ],
    )
    assert result["matched_choice_id"] == expected_match
    if expected_match:
        assert result["choice_rewrites"] == []
    else:
        assert result["choice_rewrites"]
        assert "不追问她为何留着" not in result["choice_rewrites"][0]["label"]
