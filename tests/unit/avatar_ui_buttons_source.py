from pathlib import Path


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


def read_avatar_ui_buttons_source() -> str:
    part_paths = tuple(AVATAR_UI_BUTTONS_DIR / name for name in AVATAR_UI_BUTTON_PART_NAMES)
    assert all(path.is_file() for path in part_paths), (
        f"avatar UI button parts not found: {AVATAR_UI_BUTTONS_DIR}"
    )
    return "\n".join(path.read_text(encoding="utf-8") for path in part_paths)
