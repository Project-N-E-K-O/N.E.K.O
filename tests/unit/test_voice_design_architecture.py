import pytest


@pytest.mark.unit
def test_voice_clone_util_remains_clone_only():
    from utils.voice_clone import MimoVoiceCloneClient

    assert not hasattr(MimoVoiceCloneClient, "_build_design_payload")
    assert not hasattr(MimoVoiceCloneClient, "validate_design_prompt")
    assert not hasattr(MimoVoiceCloneClient, "synthesize_design_preview")


@pytest.mark.unit
def test_voice_design_util_contains_all_hosted_provider_adapters():
    from utils import voice_design

    assert callable(voice_design._cosyvoice_design_voice)
    assert callable(voice_design._minimax_design_voice)
    assert callable(voice_design._elevenlabs_design_previews)
    assert callable(voice_design._elevenlabs_create_voice_from_preview)
    assert hasattr(voice_design.MimoVoiceDesignClient, "validate_design_prompt")
    assert hasattr(voice_design.MimoVoiceDesignClient, "synthesize_design_preview")


@pytest.mark.unit
def test_voice_design_router_uses_the_design_util_contract():
    from main_routers.characters_router import voice_cloning
    from utils import voice_design

    assert voice_cloning._cosyvoice_design_voice is voice_design._cosyvoice_design_voice
    assert voice_cloning._minimax_design_voice is voice_design._minimax_design_voice
    assert voice_cloning._elevenlabs_design_previews is voice_design._elevenlabs_design_previews
    assert voice_cloning._elevenlabs_create_voice_from_preview is voice_design._elevenlabs_create_voice_from_preview
    assert voice_cloning.MimoVoiceDesignClient is voice_design.MimoVoiceDesignClient
