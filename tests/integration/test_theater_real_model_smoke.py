"""显式开关控制的小剧场真实模型质量 smoke。"""

import os

import pytest

from services.theater import llm


class _EnvConfigManager:
    """从隔离环境变量构造 summary 档配置。"""

    def get_model_api_config(self, tier: str) -> dict[str, str]:
        """真实 smoke 同样只能读取 summary 档。"""
        assert tier == "summary"
        return {
            "model": os.environ.get("NEKO_THEATER_LLM_SMOKE_MODEL", ""),
            "base_url": os.environ.get("NEKO_THEATER_LLM_SMOKE_BASE_URL", ""),
            "api_key": os.environ.get("NEKO_THEATER_LLM_SMOKE_API_KEY", ""),
            "provider_type": os.environ.get("NEKO_THEATER_LLM_SMOKE_PROVIDER_TYPE", "openai_compatible"),
        }


def _require_environment() -> None:
    """没有显式开关时跳过，避免测试误用用户模型额度。"""
    if os.environ.get("NEKO_RUN_THEATER_LLM_SMOKE") != "1":
        pytest.skip("set NEKO_RUN_THEATER_LLM_SMOKE=1 to run the theater real-model smoke")
    if not os.environ.get("NEKO_THEATER_LLM_SMOKE_MODEL") or not os.environ.get("NEKO_THEATER_LLM_SMOKE_BASE_URL"):
        pytest.skip("missing theater real-model smoke configuration")


@pytest.mark.asyncio
async def test_real_model_returns_safe_narration_and_dialogue():
    """真实模型必须返回可直接展示的一段旁白和猫娘对白。"""
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
