import json

import pytest
from playwright.sync_api import Page, expect


def _svg(color: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800" viewBox="0 0 600 800">'
        f'<rect width="600" height="800" fill="{color}"/>'
        '</svg>'
    )


def _install_card_maker_pngtuber_routes(page: Page, *, layered: bool) -> list[str]:
    saved_urls: list[str] = []
    idle_path = "/user_pngtuber/cardmaker-test/idle.png"
    talking_path = "/user_pngtuber/cardmaker-test/talking.png"
    metadata_path = "/user_pngtuber/cardmaker-test/metadata.json"
    layer_path = "/user_pngtuber/cardmaker-test/layer.png"

    def handle(route):
        request = route.request
        url = request.url
        if "/api/config/page_config" in url:
            pngtuber = {
                "idle_image": idle_path,
                "talking_image": talking_path,
                "scale": 1,
                "offset_x": 0,
                "offset_y": 0,
            }
            if layered:
                pngtuber.update(
                    {
                        "adapter": "layered_canvas_v1",
                        "layered_metadata": metadata_path,
                        "source_format": "pngtube_remix_pngremix",
                    }
                )
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "success": True,
                        "lanlan_name": "CardPNGTuber",
                        "model_type": "pngtuber",
                        "model_path": idle_path,
                        "pngtuber": pngtuber,
                    }
                ),
            )
            return

        if request.method == "PUT" and "/api/characters/catgirl/CardPNGTuber/card-face" in url:
            saved_urls.append(url)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"success": True}),
            )
            return

        if url.endswith(metadata_path):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "runtime": "layered_canvas",
                        "canvas": {"width": 600, "height": 800},
                        "state_count": 1,
                        "layers": [
                            {
                                "name": "idle-body",
                                "image": "layer.png",
                                "order": 0,
                                "x": 0,
                                "y": 0,
                                "width": 600,
                                "height": 800,
                                "state": {"visible": True},
                            }
                        ],
                    }
                ),
            )
            return

        if url.endswith(idle_path) or url.endswith(layer_path):
            route.fulfill(status=200, content_type="image/svg+xml", body=_svg("#28c76f"))
            return

        if url.endswith(talking_path):
            route.fulfill(status=200, content_type="image/svg+xml", body=_svg("#ff3b30"))
            return

        route.continue_()

    page.route("**/*", handle)
    return saved_urls


@pytest.mark.frontend
@pytest.mark.parametrize(
    ("layered", "expected_source_tag"),
    [
        (False, "IMG"),
        (True, "CANVAS"),
    ],
)
def test_card_maker_pngtuber_idle_preview_and_save(
    mock_page: Page,
    running_server: str,
    layered: bool,
    expected_source_tag: str,
):
    saved_urls = _install_card_maker_pngtuber_routes(mock_page, layered=layered)

    mock_page.goto(f"{running_server}/card_maker?name=CardPNGTuber&mode=maker")
    mock_page.wait_for_function(
        """
        () => {
            const mgr = window.cardMakerPNGTuberManager;
            const canvas = document.getElementById('card-portrait-canvas');
            return !!mgr
                && window.currentModelType !== 'live2d'
                && canvas
                && canvas.width > 0
                && canvas.height > 0
                && document.getElementById('portrait-placeholder')?.classList.contains('hidden');
        }
        """,
        timeout=10000,
    )

    mock_page.evaluate("window.dispatchEvent(new CustomEvent('neko-assistant-speech-start'));")
    mock_page.wait_for_timeout(120)

    state = mock_page.evaluate(
        """
        () => {
            const mgr = window.cardMakerPNGTuberManager;
            const source = mgr?.isLayeredActive?.() ? mgr.canvasElement : mgr?.imageElement;
            const preview = document.getElementById('card-portrait-canvas');
            const ctx = preview.getContext('2d');
            const pixel = Array.from(ctx.getImageData(Math.floor(preview.width / 2), Math.floor(preview.height / 2), 1, 1).data);
            return {
                modelType: window.lanlan_config?.model_type,
                pngtuberConfigIdle: window.lanlan_config?.pngtuber?.idle_image || '',
                sourceTag: source?.tagName || '',
                sourceWidth: source?.naturalWidth || source?.width || 0,
                sourceHeight: source?.naturalHeight || source?.height || 0,
                state: mgr?.state,
                isSpeaking: !!mgr?.isSpeaking,
                layered: !!mgr?.isLayeredActive?.(),
                layeredStateIndex: mgr?.layeredStateIndex,
                live2dDisplay: document.getElementById('live2d-container')?.style.display || '',
                vrmDisplay: document.getElementById('vrm-container')?.style.display || '',
                mmdDisplay: document.getElementById('mmd-container')?.style.display || '',
                pngtuberDisplay: document.getElementById('pngtuber-container')?.style.display || '',
                floatingButtons: !!document.getElementById('pngtuber-floating-buttons'),
                lockIcon: !!document.getElementById('pngtuber-lock-icon'),
                previewPixel: pixel,
            };
        }
        """
    )

    assert state["modelType"] == "pngtuber"
    assert state["pngtuberConfigIdle"].endswith("/user_pngtuber/cardmaker-test/idle.png")
    assert state["sourceTag"] == expected_source_tag
    assert state["sourceWidth"] > 0
    assert state["sourceHeight"] > 0
    assert state["state"] == "idle"
    assert state["isSpeaking"] is False
    assert state["layered"] is layered
    assert state["layeredStateIndex"] == 0
    assert state["live2dDisplay"] == "none"
    assert state["vrmDisplay"] == "none"
    assert state["mmdDisplay"] == "none"
    assert state["pngtuberDisplay"] != "none"
    assert state["floatingButtons"] is False
    assert state["lockIcon"] is False
    assert state["previewPixel"][1] > 120
    assert state["previewPixel"][3] > 0

    save_button = mock_page.locator("#export-full-btn")
    expect(save_button).to_be_enabled(timeout=5000)
    with mock_page.expect_response(
        lambda response: response.request.method == "PUT"
        and response.url == f"{running_server}/api/characters/catgirl/CardPNGTuber/card-face"
        and response.status == 200,
        timeout=5000,
    ):
        save_button.click()

    assert saved_urls == [f"{running_server}/api/characters/catgirl/CardPNGTuber/card-face"]
