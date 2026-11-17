import hashlib
import json
import stat
import struct
import zipfile
from dataclasses import replace

import pytest

# Production imports use the built-in service; there is no legacy utils shim.
from services.model_workshop.bundle import BundleError, BundleLimits, validate_bundle
from services.model_workshop import bundle as bundle_module


def make_bundle(tmp_path, *, kind="vrm", entry="Avatar.vrm", files=None, transform=None, extras=(),
                compression=zipfile.ZIP_DEFLATED):
    # These are container fixtures, not renderable model certification samples.
    files = files or {entry: b"model resource"}
    manifest = {
        "schema": "neko.model.package.v1",
        "model_format": kind,
        "entrypoint": entry,
        "display_name": "Example model",
        "attribution": "Example author",
        "license": "Example permission statement",
        "files": [
            {"path": name, "byte_size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            for name, raw in sorted(files.items())
        ],
    }
    if transform:
        transform(manifest)
    path = tmp_path / "model.neko-model.zip"
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for name, raw in files.items():
            archive.writestr("model/" + name, raw)
        for name, raw in extras:
            archive.writestr(name, raw)
    return path


@pytest.mark.parametrize(("kind", "entry"), [
    ("live2d", "Avatar.model3.json"),
    ("vrm", "Avatar.vrm"),
    ("mmd", "Models/Avatar.pmx"),
    ("mmd", "Models/Avatar.pmd"),
])
def test_offline_bundle_identity_requires_no_market_id(tmp_path, kind, entry):
    path = make_bundle(tmp_path, kind=kind, entry=entry)
    result = validate_bundle(path)
    assert result.model_format == kind and result.entrypoint == entry
    assert result.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result.byte_size == path.stat().st_size
    assert result.files[0].path == entry


@pytest.mark.parametrize("path", [
    "../Avatar.vrm", "/Avatar.vrm", "C:/Avatar.vrm", "a//Avatar.vrm",
    "a/./Avatar.vrm", "a\\Avatar.vrm", "CON.vrm", "trailing./Avatar.vrm",
    "COM¹.vrm", "LPT³.vrm", "CONIN$.vrm", "NUL .vrm",
])
def test_rejects_nonportable_or_escaping_paths(tmp_path, path):
    with pytest.raises(BundleError):
        validate_bundle(make_bundle(tmp_path, entry=path))


def test_rejects_live2d_entry_below_the_declared_resource_root(tmp_path):
    with pytest.raises(BundleError):
        validate_bundle(make_bundle(tmp_path, kind="live2d", entry="nested/Avatar.model3.json"))


def test_rejects_hash_mismatch(tmp_path):
    path = make_bundle(tmp_path, transform=lambda m: m["files"][0].update(sha256="0" * 64))
    with pytest.raises(BundleError):
        validate_bundle(path)


def test_rejects_unlisted_extra_files(tmp_path):
    with pytest.raises(BundleError):
        validate_bundle(make_bundle(tmp_path, extras=[("model/extra.png", b"extra")]))


def test_rejects_casefold_conflicts(tmp_path):
    path = make_bundle(tmp_path, files={"Avatar.vrm": b"model", "Texture.png": b"a", "texture.png": b"b"})
    with pytest.raises(BundleError):
        validate_bundle(path)


def test_rejects_symbolic_links(tmp_path):
    path = make_bundle(tmp_path)
    link = zipfile.ZipInfo("model/link.png")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr(link, b"/outside")
    with pytest.raises(BundleError):
        validate_bundle(path)


def test_rejects_duplicate_archive_members(tmp_path):
    path = make_bundle(tmp_path)
    with zipfile.ZipFile(path, "a") as archive, pytest.warns(UserWarning):
        archive.writestr("model/Avatar.vrm", b"replacement")
    with pytest.raises(BundleError):
        validate_bundle(path)


@pytest.mark.parametrize("field", ["archive_bytes", "unpacked_bytes", "resource_bytes", "vrm_bytes", "manifest_bytes", "directory_bytes", "members"])
def test_enforces_each_resource_budget(tmp_path, field):
    path = make_bundle(tmp_path)
    with pytest.raises(BundleError):
        validate_bundle(path, limits=replace(BundleLimits(), **{field: 1}))


def test_rejects_wrong_protocol_and_boolean_sizes(tmp_path):
    for transform in [lambda m: m.update(schema="neko.model.package.v2"),
                      lambda m: m["files"][0].update(byte_size=True)]:
        with pytest.raises(BundleError):
            validate_bundle(make_bundle(tmp_path, transform=transform))


def test_rejects_duplicate_json_keys(tmp_path):
    path = make_bundle(tmp_path)
    with zipfile.ZipFile(path) as archive:
        manifest = archive.read("manifest.json").decode()
        payload = archive.read("model/Avatar.vrm")
    manifest = manifest.replace('"schema":', '"schema":"duplicate", "schema":', 1)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", manifest)
        archive.writestr("model/Avatar.vrm", payload)
    with pytest.raises(BundleError):
        validate_bundle(path)


def test_does_not_extract_files(tmp_path):
    path = make_bundle(tmp_path)
    validate_bundle(path)
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("extra_files", [
    {"Textures/a.png": b"a", "textures/b.png": b"b"},
    {"textures": b"file", "textures/a.png": b"child"},
    {"caf\u00e9/a.png": b"a", "cafe\u0301/b.png": b"b"},
])
def test_rejects_conflicting_directory_components(tmp_path, extra_files):
    path = make_bundle(tmp_path, files={"Avatar.vrm": b"model", **extra_files})
    with pytest.raises(BundleError):
        validate_bundle(path)


def test_rejects_directory_budget_before_constructing_zipfile(tmp_path, monkeypatch):
    path = make_bundle(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("central directory was allocated before checking its budget")

    monkeypatch.setattr(zipfile, "ZipFile", forbidden)
    with pytest.raises(BundleError):
        validate_bundle(path, limits=replace(BundleLimits(), directory_bytes=1))


def test_accepts_explicit_resource_directories(tmp_path):
    path = make_bundle(tmp_path, extras=[("model/", b"")])
    assert validate_bundle(path).entrypoint == "Avatar.vrm"


def test_rejects_multiple_model_entrypoints(tmp_path):
    path = make_bundle(tmp_path, files={"Avatar.vrm": b"one", "Other.vrm": b"two"})
    with pytest.raises(BundleError):
        validate_bundle(path)


def test_corrupt_deflate_is_reported_as_a_bundle_error(tmp_path):
    path = make_bundle(tmp_path)
    with zipfile.ZipFile(path) as archive:
        offset = archive.getinfo("manifest.json").header_offset
    raw = bytearray(path.read_bytes())
    name_size = int.from_bytes(raw[offset + 26:offset + 28], "little")
    extra_size = int.from_bytes(raw[offset + 28:offset + 30], "little")
    raw[offset + 30 + name_size + extra_size] = 7  # Reserved DEFLATE block type.
    path.write_bytes(raw)
    with pytest.raises(BundleError):
        validate_bundle(path)


def test_rejects_self_extracting_preamble(tmp_path):
    path = make_bundle(tmp_path)
    with zipfile.ZipFile(path) as archive:
        members = [(info.filename, archive.read(info)) for info in archive.infolist()]
    path.write_bytes(b"MZ executable preamble")
    with zipfile.ZipFile(path, "a") as archive:
        for name, payload in members:
            archive.writestr(name, payload)
    with pytest.raises(BundleError):
        validate_bundle(path)


def test_rejects_excessively_deep_resource_paths(tmp_path):
    entry = "/".join(["nested"] * 32 + ["Avatar.pmx"])
    with pytest.raises(BundleError):
        validate_bundle(make_bundle(tmp_path, kind="mmd", entry=entry))


@pytest.mark.parametrize("location", ["local", "local_and_directory"])
def test_rejects_zip64_member_without_zip64_archive_tail(tmp_path, location):
    """A small ZIP64 member is forbidden even when the EOCD remains ordinary."""
    path = make_bundle(tmp_path)
    with zipfile.ZipFile(path) as archive:
        members = [(info.filename, archive.read(info)) for info in archive.infolist()]
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members:
            if location == "local":
                with archive.open(name, "w", force_zip64=True) as handle:
                    handle.write(payload)
            else:
                info = zipfile.ZipInfo(name)
                info.extra = struct.pack("<HHQQ", 1, 16, len(payload), len(payload))
                archive.writestr(info, payload)
    # This fixture isolates member metadata, rather than a ZIP64 end record.
    assert b"PK\x06\x06" not in path.read_bytes()
    with pytest.raises(BundleError):
        validate_bundle(path)


def test_rejects_raw_nul_before_zip_library_truncates_member_name(tmp_path):
    """ZipInfo's parsed name must not hide a raw archive filename suffix."""
    path = make_bundle(tmp_path)
    with zipfile.ZipFile(path) as archive:
        members = [(info.filename, archive.read(info)) for info in archive.infolist()]
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members:
            archive.writestr(name + "X.exe" if name.startswith("model/") else name, payload)
    raw = path.read_bytes()
    assert raw.count(b"model/Avatar.vrmX.exe") == 2
    path.write_bytes(raw.replace(b"model/Avatar.vrmX.exe", b"model/Avatar.vrm\x00.exe"))
    with pytest.raises(BundleError):
        validate_bundle(path)


@pytest.mark.parametrize("mask_stat_change", [False, True])
def test_rejects_equal_length_in_place_change_after_archive_hash(tmp_path, monkeypatch, mask_stat_change):
    """Two valid inputs cannot be combined into hash(A) plus manifest(B)."""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    path = make_bundle(first, files={"Avatar.vrm": b"AAAA"}, compression=zipfile.ZIP_STORED)
    other = make_bundle(second, files={"Avatar.vrm": b"BBBB"}, compression=zipfile.ZIP_STORED)
    assert validate_bundle(path).sha256 != validate_bundle(other).sha256
    replacement = other.read_bytes()
    before = path.stat()
    original = bundle_module._directory_budget
    if mask_stat_change:
        # Exercise the independent rehash guard even if filesystem timestamps lie.
        stamp = before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns
        monkeypatch.setattr(bundle_module, "_source_stamp", lambda source: stamp)

    def change_then_parse(source, size, limits):
        # Keep the inode and size: checking only the opened object is insufficient.
        with path.open("r+b") as handle:
            handle.write(replacement)
        after = path.stat()
        assert (before.st_dev, before.st_ino, before.st_size) == (after.st_dev, after.st_ino, after.st_size)
        return original(source, size, limits)

    monkeypatch.setattr(bundle_module, "_directory_budget", change_then_parse)
    with pytest.raises(BundleError, match="model_bundle_changed"):
        validate_bundle(path)


def test_rejects_input_path_replacement_during_validation(tmp_path, monkeypatch):
    """A later stage must not reopen a different object under a validated path."""
    path = make_bundle(tmp_path)
    original = bundle_module._directory_budget
    denied = []
    old = path.with_suffix(".old")

    def replace_then_parse(source, size, limits):
        try:
            path.rename(old)
        except PermissionError as error:
            # Some filesystems refuse renaming this still-open input. That is
            # a safe refusal, not a successful substitution to detect afterward.
            denied.append(error)
            raise
        path.write_bytes(old.read_bytes())
        return original(source, size, limits)

    monkeypatch.setattr(bundle_module, "_directory_budget", replace_then_parse)
    with pytest.raises(BundleError) as failure:
        validate_bundle(path)
    if denied:
        assert str(failure.value) == "invalid_model_bundle"
        assert failure.value.__cause__ is denied[0]
        assert path.exists() and not old.exists()
    else:
        assert str(failure.value) == "model_bundle_changed", repr(failure.value.__cause__)
        assert old.exists() and path.exists()


def test_rejects_filesystem_refusal_during_input_replacement(tmp_path, monkeypatch):
    """Exercise the locked-input refusal path on every OS without skipping it."""
    path = make_bundle(tmp_path)
    denied = PermissionError("input replacement refused by filesystem")
    def replace_then_parse(_source, _size, _limits):
        raise denied
    monkeypatch.setattr(bundle_module, "_directory_budget", replace_then_parse)
    with pytest.raises(BundleError, match="invalid_model_bundle") as failure:
        validate_bundle(path)
    assert failure.value.__cause__ is denied and path.exists()


def test_rejects_symbolic_link_input_without_reading_target(tmp_path):
    """The bundle entry point accepts ordinary private inputs, not link aliases."""
    path = make_bundle(tmp_path)
    alias = tmp_path / "alias.neko-model.zip"
    alias.symlink_to(path)
    with pytest.raises(BundleError):
        validate_bundle(alias)


def test_inspection_context_checks_input_after_native_consumer(tmp_path):
    """Format checks share the open archive and cannot escape the final rehash."""
    path = make_bundle(tmp_path)
    with pytest.raises(BundleError, match="model_bundle_changed"):
        with bundle_module.inspect_bundle(path) as (_result, archive):
            assert archive.read("model/Avatar.vrm") == b"model resource"
            with path.open("r+b") as handle:
                handle.seek(-1, 2)
                handle.write(b"X")


def test_checked_store_handle_can_be_borrowed_without_closing_it(tmp_path):
    """Readmission does not reconstruct an object path after the store checked it."""
    path = make_bundle(tmp_path)
    with path.open("rb") as handle:
        result = validate_bundle(handle)
        assert result.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert not handle.closed


def test_borrowed_buffer_cannot_hide_mutation_from_final_hash(tmp_path, monkeypatch):
    """The borrowed descriptor is read unbuffered even if the caller cached old bytes."""
    path = make_bundle(tmp_path)
    info = path.stat()
    stamp = info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    monkeypatch.setattr(bundle_module, "_source_stamp", lambda source: stamp)
    with path.open("rb") as handle:
        handle.peek(8192)
        with pytest.raises(BundleError, match="model_bundle_changed"):
            with bundle_module.inspect_bundle(handle):
                with path.open("r+b") as writer:
                    writer.seek(-1, 2)
                    writer.write(b"X")
        assert not handle.closed
