import pytest

from config.prompts_game import (
    get_soccer_pregame_context_prompt,
    get_soccer_quick_lines_prompt,
    get_soccer_quick_lines_user_prompt,
    get_soccer_system_prompt,
)
from main_routers import game_router
from scripts import check_no_temperature


@pytest.mark.unit
def test_game_llm_paths_do_not_send_temperature_kwarg():
    assert check_no_temperature.main([
        "main_routers/game_router.py",
        "main_logic/omni_offline_client.py",
    ]) == 0


@pytest.mark.unit
def test_soccer_game_prompts_follow_user_language():
    zh_prompt = get_soccer_system_prompt("zh").format(name="Lan", personality="likes soccer")
    en_prompt = get_soccer_system_prompt("en").format(name="Lan", personality="likes soccer")
    ja_prompt = get_soccer_system_prompt("ja").format(name="Lan", personality="likes soccer")
    es_prompt = get_soccer_system_prompt("es").format(name="Lan", personality="likes soccer")

    assert "你正在和主人踢一场足球比赛" in zh_prompt
    assert "Output only the spoken line" in en_prompt
    assert "Japanese" in ja_prompt
    assert "Spanish" in es_prompt
    assert en_prompt != zh_prompt


@pytest.mark.unit
def test_soccer_quick_lines_and_pregame_prompts_are_localized():
    quick_prompt = get_soccer_quick_lines_prompt("ko").format(
        name="Lan",
        personality="likes soccer",
    )
    user_prompt = get_soccer_quick_lines_user_prompt("ru")
    pregame_prompt = get_soccer_pregame_context_prompt("pt")

    assert "Korean" in quick_prompt
    assert "Russian" in user_prompt
    assert "Portuguese" in pregame_prompt


@pytest.mark.unit
def test_build_game_prompt_uses_requested_language():
    prompt = game_router._build_game_prompt(
        "soccer",
        "Lan",
        "likes soccer",
        language="en",
    )

    assert "Output only the spoken line" in prompt
    assert "你正在和主人踢一场足球比赛" not in prompt
