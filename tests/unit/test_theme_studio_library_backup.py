import base64
import io
import zipfile

import pytest

from theme_studio import service
from theme_studio._sdk_compat import SdkError


def save_themes(studio, count, separate_dark_images=False):
    # 在同一张 GIF 中嵌入不同的合法注释，使背景图具有不同的内容哈希。
    gif = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")

    def background(number):
        comment = number.to_bytes(2, "big")
        data = gif[:-1] + b"\x21\xfe\x02" + comment + b"\x00;"
        filename = studio._save_background_bytes(data)
        return {"--nts-chat-bg-image": f'url("{studio._background_url(filename)}")'}

    for index in range(count):
        studio._save_library_item(
            name=f"theme-{index}",
            vars_map=background(2 * index),
            dark_map=background(2 * index + 1) if separate_dark_images else {},
        )


@pytest.mark.parametrize(("count", "separate_dark_images"), [(150, False), (200, True)])
def test_large_library_roundtrip(tmp_path, count, separate_dark_images):
    studio = service.ThemeStudioService(data_dir=tmp_path / "original")
    save_themes(studio, count, separate_dark_images)
    _, data, exported = studio._build_library_archive()
    restored = service.ThemeStudioService(data_dir=tmp_path / "restored")
    items, images = restored._parse_library_archive(data)

    assert exported == len(items) == count
    assert len(images) == count * (2 if separate_dark_images else 1)
    for item in items:
        theme = item["theme"]
        local = restored._rewrite_archive_refs(theme, images, label=theme["name"])
        for mode in ("vars", "dark_vars"):
            assert set(theme[mode]) == set(local[mode])
            for key, value in local[mode].items():
                name = restored._image_filename_in_value(value)
                assert theme[mode][key] == f'url("images/{name}")'
                assert (restored._backgrounds_dir() / name).read_bytes() == images[name]


@pytest.mark.parametrize(
    "limit", ["ARCHIVE_MAX_BYTES", "ARCHIVE_MAX_UNCOMPRESSED", "LIBRARY_ARCHIVE_MAX_ENTRIES"]
)
def test_backup_and_restore_enforce_same_limit(tmp_path, monkeypatch, limit):
    studio = service.ThemeStudioService(data_dir=tmp_path)
    save_themes(studio, 1)
    _, data, _ = studio._build_library_archive()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        sizes = {
            "ARCHIVE_MAX_BYTES": len(data),
            "ARCHIVE_MAX_UNCOMPRESSED": sum(info.file_size for info in archive.infolist()),
            "LIBRARY_ARCHIVE_MAX_ENTRIES": len(archive.infolist()),
        }
    monkeypatch.setattr(service, limit, sizes[limit] // 2)

    with pytest.raises(SdkError):
        studio._build_library_archive()
    with pytest.raises(SdkError):
        studio._parse_library_archive(data)
