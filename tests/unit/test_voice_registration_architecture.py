from pathlib import Path

import pytest


@pytest.mark.unit
def test_hosted_registration_providers_keep_clone_and_design_together():
    from main_logic.voice_registration.providers import cosyvoice, elevenlabs, minimax, mimo

    assert hasattr(cosyvoice.CosyVoiceRegistrationClient, "clone_voice")
    assert callable(cosyvoice.design_voice)

    assert hasattr(minimax.MinimaxVoiceCloneClient, "clone_voice")
    assert callable(minimax.design_voice)

    assert callable(elevenlabs.clone_voice)
    assert callable(elevenlabs.design_previews)
    assert callable(elevenlabs.create_voice_from_preview)

    assert hasattr(mimo.MimoVoiceCloneClient, "validate_sample")
    assert hasattr(mimo.MimoVoiceCloneClient, "validate_design_prompt")


@pytest.mark.unit
def test_router_layer_contains_no_provider_adapter_module():
    router_dir = Path(__file__).parents[2] / "main_routers" / "characters_router"
    assert not (router_dir / "voice_providers.py").exists()


@pytest.mark.unit
def test_legacy_voice_clone_util_is_removed():
    legacy_util = Path(__file__).parents[2] / "utils" / "voice_clone.py"
    assert not legacy_util.exists()


@pytest.mark.unit
def test_legacy_voice_cloning_router_module_aliases_registration_module():
    import main_routers.characters_router.voice_cloning as legacy
    import main_routers.characters_router.voice_registration as registration

    assert legacy is registration
