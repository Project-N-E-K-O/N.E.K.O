from types import SimpleNamespace
from pathlib import Path

import pytest

from main_routers.pages_router import cloudsave_manager_page
from main_routers.shared_state import init_shared_state


CHARACTER_CARD_MANAGER_PART_NAMES = (
    "core-and-upload.js",
    "subscriptions-and-scan.js",
    "character-data-and-transfer.js",
    "card-list-and-panel.js",
    "card-form-and-actions.js",
    "workshop-card-and-upload.js",
    "model-previews.js",
    "master-profile.js",
    "card-companion.js",
    "sync-and-legacy-memory.js",
)


class _DummyTemplates:
    def TemplateResponse(self, template_name, context):
        return {
            "template_name": template_name,
            "context": context,
        }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloudsave_manager_page_renders_with_or_without_character_query():
    templates = _DummyTemplates()
    init_shared_state(
        role_state={},
        steamworks=None,
        templates=templates,
        config_manager=SimpleNamespace(),
        logger=None,
        initialize_character_data=None,
    )

    request = SimpleNamespace()

    with_character = await cloudsave_manager_page(request, lanlan_name="小满")
    assert with_character["template_name"] == "templates/cloudsave_manager.html"
    assert with_character["context"]["request"] is request
    assert with_character["context"]["lanlan_name"] == "小满"

    without_character = await cloudsave_manager_page(request, lanlan_name="")
    assert without_character["template_name"] == "templates/cloudsave_manager.html"
    assert without_character["context"]["lanlan_name"] == ""


@pytest.mark.unit
def test_character_card_manager_disables_cloudsave_entry_when_provider_is_unavailable():
    parts_dir = Path("static/js/character_card_manager")
    source = "\n".join(
        (parts_dir / part_name).read_text(encoding="utf-8")
        for part_name in CHARACTER_CARD_MANAGER_PART_NAMES
    )
    css_source = Path("static/css/character_card_manager.css").read_text(encoding="utf-8")

    assert "refreshCloudsaveManagerEntryAvailability" in source
    assert "setCloudsaveManagerEntryDisabled" in source
    assert "setCloudsaveManagerEntryDisabled(openCloudsaveManagerBtn, true);" in source
    assert "summary.provider_available === false" in source
    assert "steamAutoCloud.disabled === true" in source
    assert "setCloudsaveManagerEntryDisabled(openCloudsaveManagerBtn, false);" in source
    assert "openCloudsaveManagerBtn.disabled" in source
    assert "if (openCloudsaveManagerBtn.disabled)" in source
    assert ".sidebar-cloudsave-btn:hover:not(:disabled)" in css_source
    assert ".sidebar-cloudsave-btn:active:not(:disabled)" in css_source
