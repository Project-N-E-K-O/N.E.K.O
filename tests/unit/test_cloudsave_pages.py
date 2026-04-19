from types import SimpleNamespace

import pytest

from main_routers.pages_router import cloudsave_manager_page
from main_routers.shared_state import init_shared_state


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
