import pytest

from main_routers.workshop_router import _build_subscriber_workshop_model_ref


@pytest.mark.unit
@pytest.mark.parametrize(
    ("item_id", "raw_model_ref", "expected"),
    [
        ("987654", "folder/model.pmx", "/workshop/987654/folder/model.pmx"),
        ("987654", "folder\\nested\\model.vrm", "/workshop/987654/folder/nested/model.vrm"),
        ("987654", "/workshop/123456/folder/model.pmx", "/workshop/987654/folder/model.pmx"),
        ("987654", "/workshop/123456", "/workshop/987654"),
    ],
)
def test_build_subscriber_workshop_model_ref_rewrites_item_id_and_preserves_relative_path(
    item_id: str,
    raw_model_ref: str,
    expected: str,
):
    assert _build_subscriber_workshop_model_ref(item_id, raw_model_ref) == expected
