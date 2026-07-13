from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_UI_BUTTONS_DIR = PROJECT_ROOT / "static" / "avatar" / "avatar-ui-buttons"


def read_avatar_ui_buttons_source() -> str:
    # Numeric prefixes define the runtime load order used by every template.
    part_paths = tuple(sorted(AVATAR_UI_BUTTONS_DIR.glob("*.js")))
    assert part_paths, f"avatar UI button parts not found: {AVATAR_UI_BUTTONS_DIR}"
    return "\n".join(path.read_text(encoding="utf-8") for path in part_paths)
