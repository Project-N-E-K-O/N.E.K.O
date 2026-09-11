"""A tool's own dict is not the pixel envelope.

``/api/tools/register`` used to pass a plain business dictionary through as
``output``. Moving it onto ``tool_result_from_envelope`` made every dict an
envelope, so a search tool returning ``{"images": ["https://..."], ...}`` lost
that key from the model-visible output and got a base64 warning in its place.

The plugin callback route already made this distinction. The predicate is now
one definition used by both.
"""

from __future__ import annotations

import pytest

from main_logic.tool_calling import (
    ToolCall,
    looks_like_tool_envelope,
    tool_result_from_envelope,
)


def _call() -> ToolCall:
    return ToolCall(call_id="c1", name="image_search", arguments={})


@pytest.mark.parametrize(
    "body",
    [
        {"images": ["https://example.com/a.jpg"], "count": 1},
        {"results": [], "images": []},
        {"images": "not-even-a-list"},
    ],
)
def test_business_dicts_with_images_are_not_envelopes(body: dict) -> None:
    """Mutation: drop the ``output`` requirement from the predicate."""
    assert not looks_like_tool_envelope(body)


@pytest.mark.parametrize(
    "body",
    [
        {"output": "ok", "images": [{"data_b64": "x", "mime": "image/png"}]},
        {"is_error": True, "error": "boom"},
        {"is_error": False, "output": "fine"},
    ],
)
def test_real_envelopes_are_recognised(body: dict) -> None:
    """The predicate must not become "never an envelope"."""
    assert looks_like_tool_envelope(body)


def test_a_plain_dict_reaches_the_model_intact() -> None:
    """End to end through the same helper the remote route calls.

    Asserting on the *output* rather than on the predicate: the predicate could
    be right and the route still wrap it wrongly.
    """
    body = {"images": ["https://example.com/a.jpg"], "query": "cats"}

    wrapped = tool_result_from_envelope(_call(), {"output": body})

    assert wrapped.output == body, "工具自己的 images 字段被当像素摘走了"
    assert not wrapped.images
    assert not wrapped.is_error


def test_an_envelope_still_yields_its_images() -> None:
    """The narrowing must not cost the feature this PR is adding."""
    import base64
    import io as _io

    from PIL import Image

    buf = _io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format="PNG")
    png = base64.b64encode(buf.getvalue()).decode()
    body = {"output": "here", "images": [{"data_b64": png, "mime": "image/png"}]}

    assert looks_like_tool_envelope(body)
    result = tool_result_from_envelope(_call(), body)
    assert result.output == "here"
    assert len(result.images) == 1, "真信封的图被判别式一起挡掉了"


# ── the route itself, not just the predicate ───────────────────────────


@pytest.mark.asyncio
async def test_remote_dispatch_passes_a_business_dict_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression was at the call site, so the guard has to be there too.

    Mutation: delete the ``looks_like_tool_envelope`` check in
    ``_remote_dispatch``. Asserting only on the predicate would leave that
    mutant alive.
    """
    from main_routers import tool_router

    body = {"images": ["https://example.com/a.jpg"], "query": "cats"}

    class _Resp:
        status_code = 200

        def json(self):
            return body

    class _Client:
        async def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(tool_router, "_get_http_client", lambda: _Client())
    monkeypatch.setattr(tool_router, "_note_dispatch_outcome", lambda *a, **k: None)

    result = await tool_router._remote_dispatch(
        _call(), {"callback_url": "http://127.0.0.1:1/cb", "source": "tool:x"}
    )

    assert result.output == body, "工具返回的 images 字段在路由里被当像素摘走了"
    assert not result.images
