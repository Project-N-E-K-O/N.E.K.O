import pytest

import main_logic.voice_turn.contracts as voice_contracts
from main_logic.asr_client.endpointing import detector_runtime
from main_logic.asr_client.endpointing.config import SmartTurnConfig


def test_config_rejects_missing_vad_hysteresis():
    with pytest.raises(ValueError):
        SmartTurnConfig(onset_probability=0.4, offset_probability=0.4)


def test_config_has_one_endpointing_owned_type_identity():
    assert not hasattr(voice_contracts, "SmartTurnConfig")
    assert detector_runtime.SmartTurnConfig is SmartTurnConfig
