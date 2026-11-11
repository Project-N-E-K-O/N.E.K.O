from pathlib import Path

import pytest
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APPEARANCE_ROOT = (
    PROJECT_ROOT / "static/assets/cat-resources/appearance/dev_neko"
)
IDLE_ASSET = APPEARANCE_ROOT / "idle/cat-idle-cat1.webp"
VISUAL_ACTIONS = (
    ("cat-idle-cat1-happy.webp", "normal", (512, 512), 4040),
    ("cat-idle-cat1-touch.webp", "normal", (512, 512), 6040),
    ("cat-idle-cat1-cos-1.webp", "normal", (512, 512), 4040),
    ("cat-idle-cat1-change.webp", "normal", (512, 512), 6040),
    ("cat-idle-cat1-cos-2.webp", "normal", (512, 512), 6040),
    ("cat-idle-cat1-eat-2.webp", "normal", (512, 512), 5040),
    ("cat-idle-cat1-eat-3.webp", "normal", (512, 512), 4040),
    ("cat-idle-cat1-laugh.webp", "normal", (512, 512), 4040),
    ("cat-idle-cat1-lion_dance.webp", "tall-bottom", (512, 704), 4040),
    ("cat-idle-cat1-love-1.webp", "normal", (512, 512), 4040),
    ("cat-idle-cat1-mouse.webp", "wide-centered", (896, 512), 5040),
    ("cat-idle-cat1-wave.webp", "normal", (512, 512), 4040),
    ("cat-idle-cat1-sad.webp", "normal", (512, 512), 4040),
    ("cat-idle-cat1-scared.webp", "normal", (512, 512), 4040),
    ("cat-idle-cat1-stroke.webp", "normal", (512, 512), 7980),
    ("cat-idle-cat1-wagging.webp", "normal", (512, 512), 4040),
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


def _map_to_cat1_box(
    box: tuple[int, int, int, int], layout: str
) -> tuple[float, float, float, float]:
    # Both compensated layouts render at one source pixel per 1/512 of the
    # normal CAT1 box. The overflow is centered horizontally or bottom-aligned.
    offset_x = -192 if layout == "wide-centered" else 0
    offset_y = -192 if layout == "tall-bottom" else 0
    left, top, right, bottom = box
    return (
        left + offset_x,
        top + offset_y,
        right + offset_x,
        bottom + offset_y,
    )


def _animation_duration_ms(asset: Path) -> int:
    if asset.suffix == ".gif":
        with Image.open(asset) as image:
            total = 0
            for frame_index in range(image.n_frames):
                image.seek(frame_index)
                total += int(image.info.get("duration", 0))
            return total

    data = asset.read_bytes()
    durations = []
    offset = 12
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        chunk_size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        payload = offset + 8
        if chunk_id == b"ANMF" and chunk_size >= 16:
            durations.append(int.from_bytes(data[payload + 12 : payload + 15], "little"))
        offset = payload + chunk_size + (chunk_size & 1)
    assert durations, f"missing animated WebP frames: {asset.name}"
    return sum(durations)


@pytest.mark.parametrize("filename,layout,canvas,duration_ms", VISUAL_ACTIONS)
def test_visual_action_edge_frames_align_with_default_cat1(
    filename: str, layout: str, canvas: tuple[int, int], duration_ms: int
):
    asset = APPEARANCE_ROOT / "action" / filename
    with Image.open(asset) as image:
        assert image.size == canvas
    assert _animation_duration_ms(asset) == duration_ms
    idle_boxes = _edge_frame_alpha_boxes(IDLE_ASSET)
    action_boxes = _edge_frame_alpha_boxes(asset)

    for frame_name, idle_box, action_box in zip(
        ("first", "last"), idle_boxes, action_boxes, strict=True
    ):
        compensated_box = _map_to_cat1_box(action_box, layout)
        coordinate_deltas = tuple(
            abs(actual - expected)
            for actual, expected in zip(compensated_box, idle_box, strict=True)
        )
        assert max(coordinate_deltas) <= 2, (
            f"{filename} {frame_name} frame no longer joins CAT1 seamlessly: "
            f"idle={idle_box}, compensated={compensated_box}"
        )
