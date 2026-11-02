import pytest

from main_routers.workshop_router.meta import _build_subscriber_workshop_model_ref


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_model_ref", "expected"),
    [
        # Preserve existing semantics on the normal path.
        ("example/example.model3.json", "/workshop/999/example/example.model3.json"),
        ("/workshop/123/model/foo.model3.json", "/workshop/999/model/foo.model3.json"),
        ("/workshop/123", "/workshop/999"),
        ("", ""),
        # Workshop cards are third-party content; strip '..' segments at ingestion,
        # so the resulting model_ref cannot resolve outside /workshop/{item_id}/.
        ("../../secrets/config.json", "/workshop/999/secrets/config.json"),
        ("/workshop/123/../../secrets/config.json", "/workshop/999/secrets/config.json"),
        ("foo/../../../bar.model3.json", "/workshop/999/foo/bar.model3.json"),
        ("..\\..\\evil.model3.json", "/workshop/999/evil.model3.json"),
        ("..", "/workshop/999"),
        # Strip '.' and drive-letter segments too.
        ("./model/foo.model3.json", "/workshop/999/model/foo.model3.json"),
        ("C:/evil/foo.model3.json", "/workshop/999/evil/foo.model3.json"),
    ],
)
def test_build_subscriber_workshop_model_ref_strips_traversal_segments(raw_model_ref: str, expected: str):
    assert _build_subscriber_workshop_model_ref("999", raw_model_ref) == expected


@pytest.mark.unit
def test_build_subscriber_workshop_model_ref_without_item_id_returns_ref_unchanged():
    # Without item_id, add no prefix; sync_cards does not store avatar bindings when item_id is empty.
    assert _build_subscriber_workshop_model_ref("", "model/foo.model3.json") == "model/foo.model3.json"
