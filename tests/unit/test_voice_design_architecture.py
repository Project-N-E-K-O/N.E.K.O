import inspect

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
    from main_routers.characters_router import voice_design as voice_design_router
    from utils import voice_design as voice_design_util

    assert voice_design_router._cosyvoice_design_voice is voice_design_util._cosyvoice_design_voice
    assert voice_design_router._minimax_design_voice is voice_design_util._minimax_design_voice
    assert voice_design_router._elevenlabs_design_previews is voice_design_util._elevenlabs_design_previews
    assert voice_design_router._elevenlabs_create_voice_from_preview is voice_design_util._elevenlabs_create_voice_from_preview
    assert voice_design_router.MimoVoiceDesignClient is voice_design_util.MimoVoiceDesignClient


@pytest.mark.unit
def test_voice_clone_router_does_not_depend_on_voice_design():
    from main_routers.characters_router import voice_cloning

    assert "utils.voice_design" not in inspect.getsource(voice_cloning)


@pytest.mark.unit
def test_voice_design_router_does_not_depend_on_voice_clone_util():
    from main_routers.characters_router import voice_design

    assert "utils.voice_clone" not in inspect.getsource(voice_design)
    assert "from .voice_preview import" not in inspect.getsource(voice_design)


@pytest.mark.unit
def test_shared_router_helpers_do_not_depend_on_voice_clone_util():
    from main_routers.characters_router import voice_providers

    assert "utils.voice_clone" not in inspect.getsource(voice_providers)


@pytest.mark.unit
def test_voice_design_routes_are_registered_once():
    from main_routers.characters_router import router

    paths = [route.path for route in router.routes]
    assert paths.count('/api/characters/voice_design') == 1
    assert paths.count('/api/characters/voice_design_preview') == 1
    assert paths.count('/api/characters/voice_design_create') == 1
