import pytest
from playwright.sync_api import Page


@pytest.mark.frontend
def test_cookies_login_uses_dompurify_3417_and_blocks_qr_error_xss(
    mock_page: Page,
    running_server: str,
):
    mock_page.route(
        "**/api/auth/get_QR",
        lambda route: route.fulfill(
            status=502,
            content_type="application/json",
            body='{"detail":"<img src=x onerror=\\"window.__xss_qr=1\\">"}',
        ),
    )

    mock_page.goto(f"{running_server}/api/auth/page")
    mock_page.wait_for_load_state("domcontentloaded")
    mock_page.wait_for_function("() => Boolean(window.DOMPurify && typeof requestQR === 'function')")

    purify_result = mock_page.evaluate(
        """
        () => ({
            version: window.DOMPurify.version,
            isSupported: window.DOMPurify.isSupported,
            sanitized: window.DOMPurify.sanitize('<b>ok</b><img src=x onerror="window.__xss_purify=1">')
        })
        """
    )
    assert purify_result["version"] == "3.4.17"
    assert purify_result["isSupported"] is True
    assert "onerror" not in purify_result["sanitized"]
    assert "<b>ok</b>" in purify_result["sanitized"]

    qr_result = mock_page.evaluate(
        """
        async () => {
            window.__xss_qr = 0;
            currentPlatform = 'xiaohongshu';
            await requestQR({ name: 'Xiaohongshu' }, 'xiaohongshu');
            const errEl = document.querySelector('#QRLogin .qr-session-error');
            return {
                xss: window.__xss_qr,
                imgCount: errEl ? errEl.querySelectorAll('img').length : -1,
                text: errEl ? errEl.textContent : ''
            };
        }
        """
    )
    assert qr_result["xss"] == 0
    assert qr_result["imgCount"] == 0
    assert "<img src=x" in qr_result["text"]


@pytest.mark.frontend
def test_character_card_manager_tag_xss_and_workshop_link_rel_in_browser(
    mock_page: Page,
    running_server: str,
):
    mock_page.goto(f"{running_server}/character_card_manager")
    mock_page.wait_for_load_state("domcontentloaded")
    mock_page.wait_for_function("() => typeof addTag === 'function'")

    result = mock_page.evaluate(
        """
        () => {
            window.xss = 0;
            addTag('<img src=x onerror="xss=1">');
            const container = document.getElementById('tags-container');
            const tags = container ? [...container.querySelectorAll('.tag')] : [];
            const link = document.querySelector('a[href*="workshoplegalagreement"]');
            return {
                xss: window.xss,
                tagCount: tags.length,
                imgCount: container ? container.querySelectorAll('img').length : -1,
                tagText: tags[0] ? tags[0].textContent.replace('×', '').replace('🔒', '').trim() : '',
                rel: link ? link.getAttribute('rel') : null,
                target: link ? link.getAttribute('target') : null
            };
        }
        """
    )
    assert result["xss"] == 0
    assert result["tagCount"] == 1
    assert result["imgCount"] == 0
    assert result["tagText"] == '<img src=x onerror="xss=1">'
    assert result["target"] == "_blank"
    assert set((result["rel"] or "").split()) >= {"noopener", "noreferrer"}
