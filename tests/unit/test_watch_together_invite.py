import pytest
from types import SimpleNamespace

from main_logic.proactive_chat import mini_game_invite as invites
from main_logic.watch_together import engine


def manager(vision=True, disabled=False, supported=True, key='tts-key', provider='provider'):
    return SimpleNamespace(
        _config_manager=SimpleNamespace(get_model_api_config=lambda _: {'api_key': 'vision-key' if vision else ''}),
        _resolve_tts_worker_spec=lambda: (None, key, '', provider, disabled, {}),
        _tts_worker_supports_completion=lambda *args: supported,
    )


@pytest.mark.parametrize('options', [{'vision':False}, {'disabled':True}, {'supported':False}, {'key':''}, {}])
def test_invitation_checks_vision_and_speech_without_synthesis(monkeypatch, options):
    monkeypatch.setattr(engine, 'media_binary', lambda name: name)
    assert invites._watch_together_available(manager(**options)) is (not options)


@pytest.mark.parametrize('provider', ['vllm_omni', 'local_cosyvoice'])
@pytest.mark.parametrize('supported', [True, False])
def test_keyless_local_speech_still_requires_completion(monkeypatch, provider, supported):
    monkeypatch.setattr(engine, 'media_binary', lambda name: name)
    assert invites._watch_together_available(manager(key='', provider=provider, supported=supported)) is supported


@pytest.mark.parametrize('missing', ['ffmpeg', 'ffprobe', None])
def test_invitation_requires_both_media_tools(monkeypatch, missing):
    monkeypatch.setattr(invites, 'MINI_GAME_INVITE_AVAILABLE_GAMES', ['watch-together'])
    monkeypatch.setattr(invites, 'MINI_GAME_INVITE_LINES_BY_GAME', {'watch-together': ['invite']})
    def binary(name):
        if name == missing:
            raise FileNotFoundError(name)
        return name
    monkeypatch.setattr(engine, 'media_binary', binary)
    assert invites._pick_mini_game_type(manager=manager()) == (None if missing else 'watch-together')
