from __future__ import annotations

from ...models import (
    OCR_CAPTURE_PROFILE_STAGE_CONFIG,
    OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
    OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    OCR_CAPTURE_PROFILE_STAGE_MENU,
    OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
    OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
    OCR_CAPTURE_PROFILE_STAGE_TITLE,
    OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
)


GALGAME_VISION_LABELS: tuple[str, ...] = (
    "dialogue",
    "choice_menu",
    "backlog",
    "save_load",
    "gallery",
    "title_screen",
    "config",
    "gameplay",
    "menu_main",
    "loading",
    "unknown",
)

GALGAME_VISION_LABEL_TO_SCREEN_TYPE: dict[str, str] = {
    "dialogue": OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    "choice_menu": OCR_CAPTURE_PROFILE_STAGE_MENU,
    "backlog": OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    "save_load": OCR_CAPTURE_PROFILE_STAGE_SAVE_LOAD,
    "gallery": OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    "title_screen": OCR_CAPTURE_PROFILE_STAGE_TITLE,
    "config": OCR_CAPTURE_PROFILE_STAGE_CONFIG,
    "gameplay": OCR_CAPTURE_PROFILE_STAGE_MINIGAME,
    "menu_main": OCR_CAPTURE_PROFILE_STAGE_MENU,
    "loading": OCR_CAPTURE_PROFILE_STAGE_TRANSITION,
    "unknown": OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
}


def vision_label_to_screen_type(label: str) -> str:
    return GALGAME_VISION_LABEL_TO_SCREEN_TYPE.get(
        str(label or "").strip(),
        OCR_CAPTURE_PROFILE_STAGE_DEFAULT,
    )
