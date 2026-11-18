"""Local browser acceptance with real library routes and an injected encoder.

Run explicitly: this browser test does not start NEKO's chat/memory pipelines,
publish real user data, or touch the user's configured application directory.
"""

from contextlib import nullcontext
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from playwright.sync_api import expect, sync_playwright
import uvicorn

from config import AUTOSTART_CSRF_TOKEN
from main_routers import knowledge_library_router as routes, pages_router
from services.knowledge_library.host import LibraryHost


class Encoder:
    """Avoid downloading models while exercising the production vector contract."""

    async def specification(self):
        return {"model_id": "local-text-retrieval-v1-64d-fp32-mlen1024", "profile": "local-text-retrieval-v1",
                "dimension": 64, "quantization": "fp32", "max_length": 1024,
                "weights_sha256": {key: "a" * 64 for key in ("model", "external_data", "tokenizer")},
                "processing_version": "neko-onnx-last-token-truncate-l2-v1"}

    async def embed_batch(self, texts, **_kwargs):
        return [[1.0] + [0.0] * 63 for _ in texts]

    async def plan_inputs(self, inputs, **_kwargs):
        """Keep these short browser fixtures whole without loading a tokenizer."""
        return [[{"start": 0, "end": len(row["body"])}] for row in inputs]


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Serve the worktree's real template, assets and domain against a temp root."""
    project = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(routes, "get_config_manager", lambda: SimpleNamespace(app_docs_dir=tmp_path))
    monkeypatch.setattr(LibraryHost, "write_guard", lambda self: nullcontext())
    monkeypatch.setattr("services.knowledge_library.host.HttpEmbeddingAdapter", Encoder)
    monkeypatch.setattr(pages_router, "get_templates", lambda: Jinja2Templates(directory=str(project)))
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(pages_router.router)
    app.mount("/static", StaticFiles(directory=project / "static"))

    @app.get("/api/config/page_config")
    async def config():
        return {"autostart_csrf_token": AUTOSTART_CSRF_TOKEN}

    async def shutdown():
        host = getattr(app.state, "knowledge_library_host", None)
        if host is not None:
            await host.close()

    app.router.add_event_handler("shutdown", shutdown)
    # Reserve the actual socket rather than racing another local process for
    # a previously discovered free port. All runtime state stays in tmp_path.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    address = f"http://127.0.0.1:{listener.getsockname()[1]}"
    service = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    worker = threading.Thread(target=service.run, kwargs={"sockets": [listener]}, daemon=True)
    worker.start()
    for _ in range(100):
        if service.started:
            break
        time.sleep(0.02)
    assert service.started
    try:
        yield address
    finally:
        service.should_exit = True
        worker.join(timeout=10)
        listener.close()
        assert not worker.is_alive()


def test_browser_import_search_build_and_install_real_domain(server):
    """Verify the page against real task states, not mocked business responses."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        # Wait for the controller's real first request, not just parser-loaded
        # HTML; locale initialization intentionally precedes event binding.
        with page.expect_response(lambda response: response.url.endswith("/api/knowledge-library/libraries"), timeout=15_000):
            page.goto(server + "/knowledge-library?ui_lang=zh-CN")
        # The real Chinese labels previously competed for one narrow row,
        # leaving a single trailing character on a second line in both items.
        for selector in ("#libraries-heading", '.library-sidebar [data-action="create"]'):
            lines = page.locator(selector).evaluate("""element => {
                const style = getComputedStyle(element);
                return (element.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)) / parseFloat(style.lineHeight);
            }""")
            assert lines < 1.1, f"sidebar label wraps awkwardly: {selector}"
        page.locator('[data-action="create"]').click()
        page.locator("#library-dialog-input").fill("Browser references")
        page.locator("#library-dialog-submit").click()
        expect(page.locator("#library-name")).to_have_text("Browser references")
        page.locator('[data-action="upload"]').click()
        text = "知识库保留真实原文。Browser library source. <script>throw 'injected'</script>"
        page.locator("#library-upload").set_input_files({"name": "source.txt", "mimeType": "text/plain", "buffer": text.encode()})
        page.locator('[data-action="preview"]').last.click(timeout=15_000)
        expect(page.locator("#library-dialog-content pre")).to_contain_text(text)
        page.locator("#library-dialog-submit").click()
        expect(page.locator('#library-documents [data-action="view"]')).to_have_count(1, timeout=15_000)
        page.locator("#library-query").fill("知识库")
        page.locator('#library-search button[type="submit"]').click()
        expect(page.locator("#library-results .library-excerpt")).to_contain_text("知识库")
        # Readers see localized coordinates while the original protocol keeps
        # its internal coverage and chunk subdivision data unchanged.
        expect(page.locator("#library-results .library-row-meta")).to_contain_text("段")
        assert "{" not in page.locator("#library-results .library-row-meta").inner_text()
        page.locator('[data-action="build"]').click()
        page.locator("#library-dialog-input").fill("1.0.0")
        fields = page.locator("#library-dialog-content input")
        fields.nth(0).fill("Browser author")
        fields.nth(1).fill("CC0-1.0")
        page.locator("#library-dialog-submit").click()
        export = page.locator('#library-packages [data-action="package-export"]').first
        expect(export).to_be_visible(timeout=15_000)
        expect(page.locator("#library-feedback")).not_to_have_text("排队中")
        export.click()
        expect(page.locator("#library-dialog-content .library-package-scope")).to_contain_text("source.txt")
        expect(page.locator("#library-dialog-content .library-package-scope")).to_contain_text("CC0-1.0")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.locator("#library-dialog").evaluate("element => element.scrollWidth <= element.clientWidth")
        page.set_viewport_size({"width": 1280, "height": 900})
        # Cancel really closes the review. The actual download starts only from
        # a second explicit confirmation bound to the immutable artifact.
        page.locator('[data-action="close-dialog"]').click()
        expect(page.locator("#library-dialog")).not_to_be_visible()
        export.click()
        with page.expect_download() as download_event:
            page.locator("#library-dialog-submit").click()
        artifact = Path(download_event.value.path()).read_bytes()
        page.locator('[data-action="install"]').click()
        page.locator("#library-install").set_input_files({"name": "shared.neko-knowledge-pack.zip", "mimeType": "application/zip", "buffer": artifact})
        # Offline installation is an explicit confirmation, just like import.
        page.locator("#library-dialog-submit").click()
        expect(page.locator('#library-list [data-action="select"]')).to_have_count(2, timeout=15_000)
        page.reload()
        expect(page.locator('#library-list [data-action="select"]')).to_have_count(2)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        # Real localized labels must fit the narrow page too; key-set checks
        # alone cannot catch a long translation pushing actions off screen.
        for language in ("en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW"):
            with page.expect_response(lambda response: response.url.endswith("/api/knowledge-library/libraries"), timeout=15_000):
                page.goto(server + "/knowledge-library?ui_lang=" + language)
            expect(page.locator("html")).to_have_attribute("lang", language)
            expect(page.locator('[data-action="create"]')).to_be_enabled()
            page.locator("#library-query").fill("知识库")
            page.locator('#library-search button[type="submit"]').click()
            expect(page.locator("#library-results .library-excerpt")).to_contain_text("知识库")
            assert "{" not in page.locator("#library-results .library-row-meta").inner_text()
            assert "knowledgeLibrary." not in page.locator("body").inner_text()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), language
        assert not errors
        browser.close()
