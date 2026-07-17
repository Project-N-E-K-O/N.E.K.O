from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_UI_BUTTONS_DIR = PROJECT_ROOT / "static" / "avatar" / "avatar-ui-buttons"
AVATAR_UI_BUTTON_PART_NAMES = (
    "core.js",
    "idle-assets-and-question.js",
    "idle-playground.js",
    "idle-actions-and-audio.js",
    "idle-drag-and-subactions.js",
    "idle-journey-and-presentation.js",
    "idle-cat-mind-observations.js",
    "methods-setup.js",
    "methods-buttons.js",
    "methods-return.js",
    "methods-state-and-cleanup.js",
)
BACKENDS = (
    ("live2d", "Live2DManager", PROJECT_ROOT / "static/live2d/live2d-ui-buttons.js"),
    ("vrm", "VRMManager", PROJECT_ROOT / "static/vrm/vrm-ui-buttons.js"),
    ("mmd", "MMDManager", PROJECT_ROOT / "static/mmd/mmd-ui-buttons.js"),
)
VIEWPORTS = (
    pytest.param({"width": 1440, "height": 900}, id="wide"),
    pytest.param({"width": 390, "height": 844}, id="narrow"),
)
AVATAR_UI_BUTTON_TEMPLATE_PATHS = (
    PROJECT_ROOT / "templates/index.html",
    PROJECT_ROOT / "templates/card_maker.html",
    PROJECT_ROOT / "templates/character_card_manager.html",
    PROJECT_ROOT / "templates/live2d_parameter_editor.html",
    PROJECT_ROOT / "templates/model_manager.html",
    PROJECT_ROOT / "templates/soccer_demo.html",
    PROJECT_ROOT / "templates/viewer.html",
)


@pytest.mark.parametrize("template_path", AVATAR_UI_BUTTON_TEMPLATE_PATHS)
def test_avatar_button_parts_load_in_dependency_order(template_path: Path):
    template = template_path.read_text(encoding="utf-8")
    script_positions = [
        template.index(f"/static/avatar/avatar-ui-buttons/{part_name}")
        for part_name in AVATAR_UI_BUTTON_PART_NAMES
    ]
    assert script_positions == sorted(script_positions)


@pytest.mark.parametrize("viewport", VIEWPORTS)
@pytest.mark.parametrize("prefix,manager_name,backend_script", BACKENDS)
def test_avatar_button_parts_smoke_each_backend_and_layout(
    page: Page,
    viewport: dict[str, int],
    prefix: str,
    manager_name: str,
    backend_script: Path,
):
    page.set_viewport_size(viewport)
    page.set_content("<main id='smoke-root'></main>")
    page.evaluate(
        """
        () => {
            window.APP_VERSION = 'split-smoke';
            window.t = (key) => key;
            window.__NEKO_MULTI_WINDOW__ = true;
        }
        """
    )
    page.add_script_tag(content=f"class {manager_name} {{}}; window.{manager_name} = {manager_name};")

    part_paths = tuple(AVATAR_UI_BUTTONS_DIR / name for name in AVATAR_UI_BUTTON_PART_NAMES)
    assert all(path.is_file() for path in part_paths)
    for part_path in part_paths:
        page.add_script_tag(path=str(part_path))
    page.add_script_tag(path=str(backend_script))

    page.evaluate(
        """
        ({ prefix, managerName }) => {
            const Manager = window[managerName];
            const manager = new Manager();
            const toolbar = manager.setupFloatingButtonsBase(null);
            const config = manager.getDefaultButtonConfigs()[0];
            const buttonData = manager.createButtonElement(config, toolbar, 0);
            toolbar.appendChild(buttonData.btnWrapper);
            buttonData.btnWrapper.appendChild(buttonData.btn);
            toolbar.style.left = '16px';
            toolbar.style.top = '16px';
            toolbar.style.display = 'flex';

            const returnContainer = manager.createReturnButton();
            returnContainer.style.left = '80px';
            returnContainer.style.top = '16px';
            returnContainer.style.width = '48px';
            returnContainer.style.height = '48px';
            returnContainer.style.display = 'block';

            window.__avatarReturnClicks = 0;
            window.addEventListener(`${prefix}-return-click`, () => {
                window.__avatarReturnClicks += 1;
            });
        }
        """,
        {"prefix": prefix, "managerName": manager_name},
    )

    toolbar = page.locator(f"#{prefix}-floating-buttons")
    return_button = page.locator(f"#{prefix}-btn-return")
    expect(toolbar).to_be_visible()
    expect(return_button).to_be_visible()

    return_button.click()
    assert page.evaluate("window.__avatarReturnClicks") == 1

    page.evaluate(
        "prefix => { document.getElementById(`${prefix}-floating-buttons`).style.display = 'none'; }",
        prefix,
    )
    expect(toolbar).to_be_hidden()
