import pytest

from plugin.plugins.live2d_auto_layer.core.session_id import validate_session_id


@pytest.mark.parametrize(
    "session_id",
    [
        "session_123",
        "character-v1",
        "character.v1",
    ],
)
def test_validate_session_id_accepts_safe_values(session_id: str) -> None:
    assert validate_session_id(session_id) == session_id


@pytest.mark.parametrize(
    "session_id",
    [
        "../outside",
        "/tmp/outside",
        "nested/path",
        "..hidden",
        ".hidden",
        "a..b",
        "",
    ],
)
def test_validate_session_id_rejects_path_escape_values(session_id: str) -> None:
    with pytest.raises(ValueError):
        validate_session_id(session_id)
