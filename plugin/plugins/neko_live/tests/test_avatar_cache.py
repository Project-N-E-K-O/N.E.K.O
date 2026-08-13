import io

from PIL import Image

from plugin.plugins.neko_live.adapters.neko_dispatcher import _normalize_avatar_for_neko_vision
from plugin.plugins.neko_live.stores.avatar_cache import AvatarCache


def test_avatar_cache_evicts_by_total_byte_budget():
    cache = AvatarCache(max_items=10, max_bytes=7)

    cache.put("a", b"aaaa", "image/jpeg")
    cache.put("b", b"bbbb", "image/jpeg")

    assert cache.get("a") is None
    assert cache.get("b") == (b"bbbb", "image/jpeg")
    assert cache.status() == {
        "items": 1,
        "max_items": 10,
        "bytes": 4,
        "max_bytes": 7,
    }


def test_avatar_cache_replacement_updates_byte_accounting():
    cache = AvatarCache(max_items=2, max_bytes=8)

    cache.put("a", b"aaaa", "image/jpeg")
    cache.put("b", b"bb", "image/png")
    cache.put("a", b"a", "image/webp")

    assert cache.get("a") == (b"a", "image/webp")
    assert cache.get("b") == (b"bb", "image/png")
    assert cache.status()["bytes"] == 3


def test_avatar_cache_ignores_item_larger_than_entire_budget():
    cache = AvatarCache(max_items=2, max_bytes=3)

    cache.put("too-large", b"four", "image/jpeg")

    assert cache.get("too-large") is None
    assert cache.status()["bytes"] == 0


def test_avatar_cache_default_budget_is_bounded():
    cache = AvatarCache()

    assert cache.status() == {
        "items": 0,
        "max_items": 32,
        "bytes": 0,
        "max_bytes": 4 * 1024 * 1024,
    }


def test_avatar_normalization_bounds_payload_and_dimensions():
    source = Image.effect_noise((1024, 1024), 100).convert("RGB")
    raw = io.BytesIO()
    source.save(raw, format="JPEG", quality=95)

    data, mime = _normalize_avatar_for_neko_vision(raw.getvalue(), "image/jpeg")

    assert mime == "image/jpeg"
    assert len(data) <= 64 * 1024
    with Image.open(io.BytesIO(data)) as normalized:
        assert max(normalized.size) <= 384
