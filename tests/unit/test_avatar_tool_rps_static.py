import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[2]
RPS_IMAGE_DIR = REPO_ROOT / "static" / "assets" / "avatar-tools" / "rps"
RPS_SOUND_DIR = REPO_ROOT / "static" / "sounds" / "avatar-tools" / "rps"

RPS_IMAGE_CONTRACT = {
    "paper-icon.png": {
        "size": (240, 240),
        "alpha_bbox": (10, 15, 230, 225),
        "content_sha256": "965103635d631df588eb6df79a0cc20482f8adc89d45aeb8cdf084c0a2cf81ad",
    },
    "rock-icon.png": {
        "size": (240, 240),
        "alpha_bbox": (10, 10, 229, 230),
        "content_sha256": "482c3c76ca95079e1dc9ba41c20382855a18afdab0dd2ca5dfad8840f27689f3",
    },
    "scissors-icon.png": {
        "size": (240, 240),
        "alpha_bbox": (41, 10, 198, 230),
        "content_sha256": "00fc014c44c8da97b742ad149ef746d238612df89f2d9bdaef14d3bca7125322",
    },
    "paper-pointer.png": {
        "size": (80, 80),
        "alpha_bbox": (1, 2, 77, 77),
        "content_sha256": "c1c68ec4f5cc1ae45389d6ea3c77da52506114e52648f3db9a45751a1653eb40",
    },
    "rock-pointer.png": {
        "size": (80, 80),
        "alpha_bbox": (1, 1, 79, 79),
        "content_sha256": "0c6c44897e6f5e1046f3f675dba9a1a49d7788ae5eef10f0f96ef55f4e919cfc",
    },
    "scissors-pointer.png": {
        "size": (80, 80),
        "alpha_bbox": (11, 1, 68, 79),
        "content_sha256": "167df7c47cb1821cf047ac08c5a91ffede87ac40c03abef8db276c1f32c9facc",
    },
}

RPS_SOUND_PATHS = {
    "rps-confirm": "confirm.mp3",
    "rps-user-win": "user-win.mp3",
    "rps-other-result": "other-result.mp3",
}
RPS_SOUND_SHA256 = {
    "confirm.mp3": "b3401b4f7fae10b57646e5dc71426715ebfc405c8e46caa4b02aae6e1c61cee2",
    "user-win.mp3": "0b77e997a978446a1f674f89eafe7a5718b5d747bd8bc447a81bff2f6c366398",
    "other-result.mp3": "82b8dae31c24c7b295165c747b79df7f3088921f4be599e963b3cb52e8b10f47",
}


def _content_digest(image: Image.Image, bbox: tuple[int, int, int, int]) -> str:
    return hashlib.sha256(image.crop(bbox).tobytes()).hexdigest()


@pytest.mark.unit
def test_rps_images_use_canonical_names_and_normalized_transparent_canvases():
    actual_names = {path.name for path in RPS_IMAGE_DIR.glob("*.png")}
    assert actual_names == set(RPS_IMAGE_CONTRACT)

    for filename, expected in RPS_IMAGE_CONTRACT.items():
        with Image.open(RPS_IMAGE_DIR / filename) as source:
            assert source.mode == "RGBA", filename
            image = source.copy()

        alpha_bbox = image.getchannel("A").getbbox()
        assert image.size == expected["size"], filename
        assert alpha_bbox == expected["alpha_bbox"], filename
        assert _content_digest(image, alpha_bbox) == expected["content_sha256"], filename

        left, top, right, bottom = alpha_bbox
        width, height = image.size
        assert 0 < left < right < width, filename
        assert 0 < top < bottom < height, filename


@pytest.mark.unit
def test_rps_sound_assets_use_canonical_ids_and_files():
    actual_names = {path.name for path in RPS_SOUND_DIR.glob("*.mp3")}
    assert actual_names == set(RPS_SOUND_PATHS.values())
    assert len(RPS_SOUND_PATHS) == len(set(RPS_SOUND_PATHS.values())) == 3

    for filename in RPS_SOUND_PATHS.values():
        payload = (RPS_SOUND_DIR / filename).read_bytes()
        assert payload.startswith((b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")), filename
        assert hashlib.sha256(payload).hexdigest() == RPS_SOUND_SHA256[filename], filename


@pytest.mark.unit
def test_rps_sounds_are_decodable_mono_mp3_files():
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("ffprobe is unavailable")

    for filename in RPS_SOUND_PATHS.values():
        completed = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_name,sample_rate,channels:format=duration",
                "-of", "json",
                str(RPS_SOUND_DIR / filename),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        metadata = json.loads(completed.stdout)
        stream = metadata["streams"][0]
        assert stream["codec_name"] == "mp3", filename
        assert stream["sample_rate"] == "44100", filename
        assert stream["channels"] == 1, filename
        assert 0 < float(metadata["format"]["duration"]) < 1, filename


@pytest.mark.unit
def test_rps_resources_are_in_the_react_chat_version_closure_once():
    from main_routers import pages_router

    expected = {
        *(RPS_IMAGE_DIR / filename for filename in RPS_IMAGE_CONTRACT),
        *(RPS_SOUND_DIR / filename for filename in RPS_SOUND_PATHS.values()),
    }
    closure = tuple(path.resolve() for path in pages_router._REACT_CHAT_ASSET_VERSION_PATHS)
    actual = {path for path in closure if path.parent in {RPS_IMAGE_DIR, RPS_SOUND_DIR}}

    assert actual == {path.resolve() for path in expected}
    assert len(closure) == len(set(closure))
    assert all(closure.count(path.resolve()) == 1 for path in expected)
