import pytest

from scripts import check_no_temperature


@pytest.mark.unit
def test_game_llm_paths_do_not_send_temperature_kwarg():
    assert check_no_temperature.main([
        "main_routers/game_router.py",
        "main_logic/omni_offline_client.py",
    ]) == 0
