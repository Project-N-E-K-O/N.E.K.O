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


@pytest.mark.frontend
def test_character_card_manager_subscriptions_non_string_json_xss_blocked(
    mock_page: Page,
    running_server: str,
):
    import json

    mock_page.route(
        "**/api/steam/workshop/subscribed-items",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "success": True,
                "items": [
                    {
                        "publishedFileId": "2",
                        "title": ['<img src=x onerror="window.__xss_title_arr=1">'],
                        "authorName": ['<img src=x onerror="window.__xss_author_arr=1">'],
                        "description": ['<img src=x onerror="window.__xss_desc_arr=1">'],
                        "voiceReferenceAvailable": True,
                        "voiceReference": {
                            "displayName": ['"><img src=x onerror="window.__xss=97">'],
                        },
                        "state": {"installed": True},
                    }
                ],
            }),
        ),
    )
    mock_page.route(
        "**/api/steam/workshop/item/9",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "success": True,
                "item": {
                    "publishedFileId": 9,
                    "title": "Normal",
                    "tags": [['<img src=x onerror="window.__xss_tag_arr=1">']],
                    "state": {"installed": True},
                },
            }),
        ),
    )

    mock_page.goto(f"{running_server}/character_card_manager")
    mock_page.wait_for_load_state("domcontentloaded")
    mock_page.wait_for_function(
        "() => typeof escapeHtml === 'function' && typeof renderSubscriptionsPage === 'function' && typeof viewItemDetails === 'function'"
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.__xss = 0;
            window.__xss_title_arr = 0;
            window.__xss_author_arr = 0;
            window.__xss_desc_arr = 0;
            window.__xss_tag_arr = 0;

            const escapedInt = escapeHtml(123);
            const escapedArr = escapeHtml(['<b>x</b>']);
            const escapedNull = escapeHtml(null);
            const escapedUndef = escapeHtml(undefined);

            allSubscriptions = [{
                publishedFileId: '2',
                title: ['<img src=x onerror="window.__xss_title_arr=1">'],
                authorName: ['<img src=x onerror="window.__xss_author_arr=1">'],
                description: ['<img src=x onerror="window.__xss_desc_arr=1">'],
                voiceReferenceAvailable: true,
                voiceReference: { displayName: ['"><img src=x onerror="window.__xss=97">'] },
                state: { installed: true }
            }];
            currentPage = 1;
            itemsPerPage = 10;
            totalPages = 1;
            renderSubscriptionsPage();

            viewItemDetails(9);
            const deadline = Date.now() + 3000;
            while (Date.now() < deadline) {
                if (document.querySelector('#itemDetailContent .tags-container .tag')) {
                    break;
                }
                await new Promise((r) => setTimeout(r, 20));
            }
            await new Promise((r) => setTimeout(r, 50));

            const tagEl = document.querySelector('#itemDetailContent .tags-container .tag');
            const tagsContainer = document.querySelector('#itemDetailContent .tags-container');
            const listImgs = [...document.querySelectorAll('#subscriptions-list img')].map((img) => img.className);
            return {
                xss: window.__xss,
                xssTitleArr: window.__xss_title_arr,
                xssAuthorArr: window.__xss_author_arr,
                xssDescArr: window.__xss_desc_arr,
                xssTagArr: window.__xss_tag_arr,
                escapedInt,
                escapedArr,
                escapedNull,
                escapedUndef,
                listImgs,
                tagImgCount: tagsContainer ? tagsContainer.querySelectorAll('img').length : -1,
                tagText: tagEl ? tagEl.textContent : '',
                tagHtml: tagEl ? tagEl.innerHTML : ''
            };
        }
        """
    )

    assert result["xss"] == 0
    assert result["xssTitleArr"] == 0
    assert result["xssAuthorArr"] == 0
    assert result["xssDescArr"] == 0
    assert result["xssTagArr"] == 0
    assert result["escapedInt"] == "123"
    assert result["escapedArr"] == "&lt;b&gt;x&lt;/b&gt;"
    assert result["escapedNull"] == ""
    assert result["escapedUndef"] == ""
    assert result["listImgs"] == ["card-image", "card-title-paw"]
    assert result["tagImgCount"] == 0
    assert result["tagText"] == '<img src=x onerror="window.__xss_tag_arr=1">'
    assert "&lt;img" in result["tagHtml"]
