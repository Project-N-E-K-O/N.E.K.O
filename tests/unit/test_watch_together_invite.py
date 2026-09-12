import pytest
from types import SimpleNamespace

from main_logic.proactive_chat import mini_game_invite as invites
from main_logic.watch_together import engine


def manager(vision=True, disabled=False, supported=True, key='tts-key', provider='provider', base_url='http://127.0.0.1:9881'):
    return SimpleNamespace(
        _config_manager=SimpleNamespace(get_model_api_config=lambda kind: {'api_key': 'vision-key' if vision else '', 'model': 'vision-model'}
                                        if kind == 'vision' else {'base_url': base_url}),
        _resolve_tts_worker_spec=lambda: (None, key, '', provider, disabled, {}),
        _tts_worker_supports_completion=lambda *args: supported,
    )


def test_unavailable_worker_cannot_invite_even_when_completion_supported(monkeypatch):
    from main_logic.tts_client._infra import configured_tts_unavailable_worker
    monkeypatch.setattr(engine, 'media_binary', lambda name: name)
    current = manager(key='', provider='vllm_omni')
    current._resolve_tts_worker_spec = lambda: (configured_tts_unavailable_worker, '', '', 'vllm_omni', False, {})
    assert not invites._watch_together_available(current)


def test_keyless_custom_vision_can_invite(monkeypatch):
    monkeypatch.setattr(engine, 'media_binary', lambda name: name)
    current = manager()
    current._config_manager.get_model_api_config = lambda _: {'api_key': '', 'is_custom': True, 'model': 'local'}
    assert invites._watch_together_available(current)


@pytest.mark.parametrize('options', [{'vision':False}, {'disabled':True}, {'supported':False}, {'key':''}, {}])
def test_invitation_checks_vision_and_speech_without_synthesis(monkeypatch, options):
    monkeypatch.setattr(engine, 'media_binary', lambda name: name)
    assert invites._watch_together_available(manager(**options)) is (not options)


@pytest.mark.parametrize('provider', ['custom', 'vllm_omni', 'local_cosyvoice', 'gptsovits'])
@pytest.mark.parametrize('supported', [True, False])
def test_keyless_local_speech_still_requires_completion(monkeypatch, provider, supported):
    monkeypatch.setattr(engine, 'media_binary', lambda name: name)
    assert invites._watch_together_available(manager(key='', provider=provider, supported=supported)) is supported


@pytest.mark.parametrize('url,available', [(None, True), ('', True), ('invalid', False),
                                          ('ftp://localhost', False), ('http://127.0.0.1:9881', True),
                                          ('https://voice.example.test', True)])
def test_gptsovits_invitation_uses_worker_url_validation(monkeypatch, url, available):
    monkeypatch.setattr(engine, 'media_binary', lambda name: name)
    assert invites._watch_together_available(manager(key='', provider='gptsovits', base_url=url)) is available


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
