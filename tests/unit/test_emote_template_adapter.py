"""Tests for content-based, self-contained Spine ZIP imports."""
import io
import json
import stat
import zipfile
import pytest
from main_logic.emote_template_adapter import EmoteTemplatePackageError, inspect_spine_zip, spine_resource_archive
from tests.emote_fixtures import spine_zip


def rewrite(data, transform):
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(result, "w") as target:
        for item in source.infolist():
            value = transform(item.filename, source.read(item))
            if value is not None:
                name, content = value
                target.writestr(name, content)
    return result.getvalue()


def test_reads_plain_spine_without_vendor_configuration():
    package = inspect_spine_zip(spine_zip(), filename="my-character.zip")
    assert package["source"]["kind"] == "spine"
    assert package["schemaVersion"] == 2
    assert [item["id"] for item in package["catalogs"]["animations"]] == ["idle0", "idle1"]
    assert package["model"]["atlas"]["pages"][0]["path"] == "assets/page.png"
    assert package["model"]["displayName"] == "my-character"


def test_unselected_invalid_atlas_does_not_block_the_matching_model():
    stream = io.BytesIO(spine_zip())
    with zipfile.ZipFile(stream, 'a') as archive:
        archive.writestr('unused/invalid.atlas', b'\xffnot UTF-8')
    package = inspect_spine_zip(stream.getvalue())
    assert package['model']['atlas']['path'] == 'assets/character.atlas'
    explicit = inspect_spine_zip(stream.getvalue(), atlas_path='assets/character.atlas')
    assert explicit['id'] == package['id']


def test_atlas_page_without_optional_page_properties_is_supported():
    archive = rewrite(spine_zip(), lambda path, content: (
        path, b'page.png\nbody\nbounds: 0, 0, 1, 1\n' if path.endswith('.atlas') else content))
    assert inspect_spine_zip(archive)['model']['atlas']['pages'][0]['name'] == 'page.png'


def test_repacked_stored_compressible_resource_remains_importable():
    archive = rewrite(spine_zip(), lambda path, content: (
        path, content + b' ' * 2_000_000 if path.endswith('.json') else content))
    original = inspect_spine_zip(archive)
    repacked = spine_resource_archive(archive, original)
    assert inspect_spine_zip(repacked)['id'] == original['id']


def test_identity_is_independent_of_archive_filename_and_unrelated_files():
    data = spine_zip()
    first = inspect_spine_zip(data, filename="one.zip")
    second = inspect_spine_zip(data, filename="renamed.zip")
    assert first["id"] == second["id"]
    stream = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as old, zipfile.ZipFile(stream, "w") as new:
        for path in old.namelist():
            new.writestr(path, old.read(path))
        new.writestr("metadata/ignored.json", '{"scenes":[{"animationName":"fake"}]}')
        new.writestr("metadata/ignored.csv", "anything")
    package = inspect_spine_zip(stream.getvalue())
    assert package["id"] == first["id"]
    assert len(package["scenes"]) == 2
    with zipfile.ZipFile(io.BytesIO(spine_resource_archive(stream.getvalue(), package))) as stored:
        assert set(stored.namelist()) == set(package["resources"])


def test_same_named_packages_with_changed_model_data_have_distinct_ids():
    assert inspect_spine_zip(spine_zip(animations=1))["id"] != inspect_spine_zip(spine_zip(animations=2))["id"]


def test_uploaded_manifest_keeps_its_legacy_identity_but_only_builtin_profile_drives_parts():
    archive = spine_zip()
    profile = json.dumps({
        "format": "neko.emote-template", "formatVersion": 1,
        "parts": [{"slot": "body", "kind": "toggle", "attachments": ["body"]}],
    }).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive)) as source, zipfile.ZipFile(stream, "w") as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        target.writestr("assets/neko-template.json", profile)
    external = inspect_spine_zip(stream.getvalue())
    assert external["character"]["slots"] == []
    assert "assets/neko-template.json" in external["resources"]
    saved = spine_resource_archive(stream.getvalue(), external)
    assert inspect_spine_zip(saved)["id"] == external["id"]
    builtin = inspect_spine_zip(archive, part_profile=profile)
    assert builtin["character"]["slots"] == [{"name": "body", "attachmentMode": "auto"}]


def test_missing_or_ambiguous_texture_is_rejected():
    missing = rewrite(spine_zip(), lambda path, content: None if path.endswith(".png") else (path, content))
    with pytest.raises(EmoteTemplatePackageError, match="贴图"):
        inspect_spine_zip(missing)


def test_multiple_models_require_explicit_selection():
    data = spine_zip()
    stream = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as old, zipfile.ZipFile(stream, "w") as new:
        for path in old.namelist():
            new.writestr(path, old.read(path))
        new.writestr("assets/second.json", old.read("assets/character.json"))
    with pytest.raises(EmoteTemplatePackageError) as caught:
        inspect_spine_zip(stream.getvalue())
    assert caught.value.code == "selection_required"
    assert caught.value.role == "model"
    assert inspect_spine_zip(stream.getvalue(), model_path="assets/second.json")["model"]["skeleton"]["path"] == "assets/second.json"


def test_json_version_and_attachment_references_are_validated():
    def change(path, content):
        if path.endswith(".json"):
            obj = json.loads(content)
            obj["skeleton"]["spine"] = "4.2.0"
            content = json.dumps(obj).encode()
        return path, content
    with pytest.raises(EmoteTemplatePackageError, match="4.3"):
        inspect_spine_zip(rewrite(spine_zip(), change))
    invalid = rewrite(spine_zip(), lambda path, content: (path, content.replace(b"body\nbounds", b"missing\nbounds")) if path.endswith(".atlas") else (path, content))
    with pytest.raises(EmoteTemplatePackageError, match="附件"):
        inspect_spine_zip(invalid)


@pytest.mark.parametrize("path", ["../escape.png", "/abs.png", "C:/drive.png"])
def test_rejects_unsafe_paths(path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(path, b"x")
    with pytest.raises(EmoteTemplatePackageError):
        inspect_spine_zip(stream.getvalue())


def test_rejects_symlinks_duplicate_paths_and_non_zip():
    with pytest.raises(EmoteTemplatePackageError):
        inspect_spine_zip(b"not a zip")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        item = zipfile.ZipInfo("link")
        item.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(item, "outside")
    with pytest.raises(EmoteTemplatePackageError):
        inspect_spine_zip(stream.getvalue())


def test_static_skeleton_does_not_invent_animation():
    result = inspect_spine_zip(spine_zip(animations=0))
    assert result["scenes"] == []
    assert result["counts"]["animations"] == 0


def test_rejects_duplicate_archive_paths_after_case_normalization():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("assets/page.png", b"one")
        archive.writestr("ASSETS/Page.png", b"two")
    with pytest.raises(EmoteTemplatePackageError, match="冲突"):
        inspect_spine_zip(stream.getvalue())


@pytest.mark.parametrize("budget,value", [
    ("MAX_ARCHIVE_BYTES", 1), ("MAX_ARCHIVE_ENTRIES", 2),
    ("MAX_UNCOMPRESSED_BYTES", 1), ("MAX_SCAN_BYTES", 1), ("MAX_PATH_DEPTH", 1),
])
def test_resource_budgets_reject_oversized_packages(monkeypatch, budget, value):
    import main_logic.emote_template_adapter as adapter
    monkeypatch.setattr(adapter, budget, value)
    with pytest.raises(EmoteTemplatePackageError, match="上限|越界"):
        inspect_spine_zip(spine_zip())


def test_malformed_skins_report_a_package_error():
    def malformed(path, content):
        if path.endswith(".json"):
            data = json.loads(content)
            data["skins"] = 1
            content = json.dumps(data).encode()
        return path, content
    with pytest.raises(EmoteTemplatePackageError, match="skins"):
        inspect_spine_zip(rewrite(spine_zip(), malformed))
