from pathlib import Path

import pytest
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APPEARANCE_ROOT = (
    PROJECT_ROOT / "static/assets/cat-resources/appearance/dev_neko"
)
IDLE_ASSETS = {
    "cat1": APPEARANCE_ROOT / "idle/cat-idle-cat1.webp",
    "cat2": APPEARANCE_ROOT / "idle/cat-idle-cat2.webp",
    "cat3": APPEARANCE_ROOT / "idle/cat-idle-cat3.webp",
}
SLEEP_VISUAL_ACTIONS = (
    ("cat-idle-cat2-back1-1.webp", "wide-125-centered", "cat2", "cat1", (640, 512), 4040),
    ("cat-idle-cat2-turn-1.webp", "wide-centered", "cat2", "cat2", (896, 512), 5040),
    ("cat-idle-cat3-turn-2.webp", "normal", "cat3", "cat3", (512, 512), 5930),
    ("cat-idle-cat3-back1-1.webp", "normal", "cat3", "cat1", (512, 512), 5040),
    ("cat-idle-cat3-back1-2.webp", "normal", "cat3", "cat1", (512, 512), 5390),
)


def _edge_frame_alpha_boxes(asset: Path) -> tuple[tuple[int, int, int, int], ...]:
    with Image.open(asset) as image:
        boxes = []
        for frame_index in (0, image.n_frames - 1):
            image.seek(frame_index)
            box = image.convert("RGBA").getchannel("A").getbbox()
            assert box is not None, f"transparent edge frame: {asset.name}#{frame_index}"
            boxes.append(box)
        return tuple(boxes)


def _map_to_idle_box(
    box: tuple[int, int, int, int], layout: str
) -> tuple[float, float, float, float]:
    offset_x = {
        "normal": 0,
        "wide-125-centered": -64,
        "wide-centered": -192,
    }[layout]
    left, top, right, bottom = box
    return left + offset_x, top, right + offset_x, bottom


def _animation_duration_ms(asset: Path) -> int:
    if asset.suffix == ".webp":
        data = asset.read_bytes()
        total = 0
        offset = 12
        while offset + 8 <= len(data):
            chunk_type = data[offset : offset + 4]
            chunk_size = int.from_bytes(data[offset + 4 : offset + 8], "little")
            chunk = data[offset + 8 : offset + 8 + chunk_size]
            if chunk_type == b"ANMF" and len(chunk) >= 16:
                total += int.from_bytes(chunk[12:15], "little")
            offset += 8 + chunk_size + (chunk_size & 1)
        return total
    with Image.open(asset) as image:
        total = 0
        for frame_index in range(image.n_frames):
            image.seek(frame_index)
            total += int(image.info.get("duration", 0))
        return total


@pytest.mark.parametrize(
    "filename,layout,source_tier,terminal_tier,canvas,duration_ms",
    SLEEP_VISUAL_ACTIONS,
)
def test_sleep_visual_action_edge_frames_join_their_actual_tiers(
    filename: str,
    layout: str,
    source_tier: str,
    terminal_tier: str,
    canvas: tuple[int, int],
    duration_ms: int,
):
    asset = APPEARANCE_ROOT / "action" / filename
    with Image.open(asset) as image:
        assert image.size == canvas
    assert _animation_duration_ms(asset) == duration_ms

    action_first, action_last = _edge_frame_alpha_boxes(asset)
    source_idle = _edge_frame_alpha_boxes(IDLE_ASSETS[source_tier])[0]
    terminal_idle = _edge_frame_alpha_boxes(IDLE_ASSETS[terminal_tier])[-1]
    for frame_name, actual, expected in (
        ("first", action_first, source_idle),
        ("last", action_last, terminal_idle),
    ):
        compensated = _map_to_idle_box(actual, layout)
        coordinate_deltas = tuple(
            abs(value - target)
            for value, target in zip(compensated, expected, strict=True)
        )
        assert max(coordinate_deltas) <= 2, (
            f"{filename} {frame_name} frame no longer joins {source_tier if frame_name == 'first' else terminal_tier} "
            f"seamlessly: idle={expected}, compensated={compensated}"
        )
