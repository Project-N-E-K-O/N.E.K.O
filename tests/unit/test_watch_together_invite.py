import pytest

from main_logic.proactive_chat import mini_game_invite as invites
from main_logic.watch_together import engine


@pytest.mark.parametrize('missing', ['ffmpeg', 'ffprobe', None])
def test_invitation_requires_both_media_tools(monkeypatch, missing):
    monkeypatch.setattr(invites, 'MINI_GAME_INVITE_AVAILABLE_GAMES', ['watch-together'])
    monkeypatch.setattr(invites, 'MINI_GAME_INVITE_LINES_BY_GAME', {'watch-together': ['invite']})
    def binary(name):
        if name == missing:
            raise FileNotFoundError(name)
        return name
    monkeypatch.setattr(engine, 'media_binary', binary)
    assert invites._pick_mini_game_type() == (None if missing else 'watch-together')
