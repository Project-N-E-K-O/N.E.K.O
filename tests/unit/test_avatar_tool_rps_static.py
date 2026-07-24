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
    "paper-icon.png": (240, 240),
    "rock-icon.png": (240, 240),
    "scissors-icon.png": (240, 240),
    "paper-pointer.png": (80, 80),
    "rock-pointer.png": (80, 80),
    "scissors-pointer.png": (80, 80),
}

RPS_SOUND_PATHS = {
    "rps-confirm": "confirm.mp3",
    "rps-user-win": "user-win.mp3",
    "rps-other-result": "other-result.mp3",
}
@pytest.mark.unit
def test_rps_images_use_canonical_names_and_normalized_transparent_canvases():
    actual_names = {path.name for path in RPS_IMAGE_DIR.glob("*.png")}
    assert actual_names == set(RPS_IMAGE_CONTRACT)

    for filename, expected_size in RPS_IMAGE_CONTRACT.items():
        with Image.open(RPS_IMAGE_DIR / filename) as source:
            assert source.mode == "RGBA", filename
            image = source.copy()

        alpha_bbox = image.getchannel("A").getbbox()
        assert image.size == expected_size, filename
        assert alpha_bbox is not None, filename

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
