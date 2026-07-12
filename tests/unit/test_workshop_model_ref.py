import pytest

from main_routers.workshop_router.meta import _build_subscriber_workshop_model_ref


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_model_ref", "expected"),
    [
        # 正常路径保持原有语义
        ("example/example.model3.json", "/workshop/999/example/example.model3.json"),
        ("/workshop/123/model/foo.model3.json", "/workshop/999/model/foo.model3.json"),
        ("/workshop/123", "/workshop/999"),
        ("", ""),
        # 工坊卡是第三方内容，'..' 段在摄入点被剥掉，
        # 拼出的 model_ref 无法解析出 /workshop/{item_id}/ 之外
        ("../../secrets/config.json", "/workshop/999/secrets/config.json"),
        ("/workshop/123/../../secrets/config.json", "/workshop/999/secrets/config.json"),
        ("foo/../../../bar.model3.json", "/workshop/999/foo/bar.model3.json"),
        ("..\\..\\evil.model3.json", "/workshop/999/evil.model3.json"),
        ("..", "/workshop/999"),
        # '.' 段与盘符段同样被剥掉
        ("./model/foo.model3.json", "/workshop/999/model/foo.model3.json"),
        ("C:/evil/foo.model3.json", "/workshop/999/evil/foo.model3.json"),
    ],
)
def test_build_subscriber_workshop_model_ref_strips_traversal_segments(raw_model_ref: str, expected: str):
    assert _build_subscriber_workshop_model_ref("999", raw_model_ref) == expected


@pytest.mark.unit
def test_build_subscriber_workshop_model_ref_without_item_id_returns_ref_unchanged():
    # 没有 item_id 时不加前缀；调用方（sync_cards）在 item_id 为空时不会入库 avatar 绑定
    assert _build_subscriber_workshop_model_ref("", "model/foo.model3.json") == "model/foo.model3.json"
