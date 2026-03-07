from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.plugin_e2e
def test_plugin_ui_smoke(page: Page) -> None:
    base_url = os.getenv("PLUGIN_E2E_BASE_URL", "").strip()
    if not base_url:
        pytest.skip("PLUGIN_E2E_BASE_URL is not set")

    page.goto(base_url, wait_until="domcontentloaded")
    expect(page).to_have_title(lambda title: len(title) > 0)

