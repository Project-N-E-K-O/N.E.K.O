"""Hostile ZIP fixtures must reach structural and numerical admission checks."""

import binascii
import io
import hashlib
import json
from pathlib import Path
import random
import stat
import struct
import zipfile
import zlib

import pytest

from utils.knowledge_package import PackageError, _check_compressed_stream, canonical_json, validate_package
from tests.knowledge_archive_fixtures import make_archive


pytestmark = pytest.mark.unit_fast


def _built(root):
    """Reuse the independent v2 wire fixture without constructing a service."""
    root.mkdir(parents=True, exist_ok=True)
    return {"artifact_path": make_archive(root / "complete.zip")}


def _rewrite(path, target, *, member, transform):
    """Rehash malicious member bytes so structural checks must actually run."""
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members["manifest.json"])
    members[member] = transform(members[member])
    manifest["files"][member] = {"bytes": len(members[member]),
                                 "sha256": hashlib.sha256(members[member]).hexdigest()}
    members["manifest.json"] = canonical_json(manifest)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
    return target


def test_valid_deflate_output_is_drained_after_compressed_input_is_consumed(tmp_path):
    """A bounded zlib call can retain output even with an empty input tail."""
    path = tmp_path / "buffered-output.zip"
    payload = b"x" * 65_537
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr("pack.json", payload)
    with zipfile.ZipFile(path) as archive:
        assert archive.read("pack.json") == payload
        _check_compressed_stream(path, archive.getinfo("pack.json"))


def test_standard_zip_stream_matrix_preserves_boundaries_and_data_descriptors(tmp_path):
    """Retain 270 ordinary ZIP variants as a reproducible compatibility corpus."""
    class NonSeekable(io.BytesIO):
        def seek(self, *_args):
            raise io.UnsupportedOperation("force standard ZIP data descriptors")

    cases = 0
    for size in (0, 1, 65535, 65536, 65537, 131071, 131072, 131073, 1048576):
        payloads = (b"x" * size, (b"ABC\0\xff\x80" * (size // 6 + 1))[:size],
                    random.Random(1947 + size).randbytes(size))
        for payload in payloads:
            for method, levels in ((zipfile.ZIP_STORED, (None,)), (zipfile.ZIP_DEFLATED, (0, 1, 6, 9))):
                for level in levels:
                    for descriptor in (False, True):
                        buffer = NonSeekable() if descriptor else io.BytesIO()
                        with zipfile.ZipFile(buffer, "w", compression=method, compresslevel=level) as archive:
                            archive.writestr("vectors.f32", payload)
                        path = tmp_path / "standard.zip"
                        path.write_bytes(buffer.getvalue())
                        with zipfile.ZipFile(path) as archive:
                            info = archive.getinfo("vectors.f32")
                            assert bool(info.flag_bits & 8) == descriptor
                            assert archive.read(info) == payload
                        try:
                            _check_compressed_stream(path, info)
                        except Exception as error:
                            raise AssertionError((size, method, level, descriptor)) from error
                        cases += 1
    assert cases == 270


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
@pytest.mark.parametrize("member", ["manifest.json", "pack.json", "chunks.jsonl", "vectors.f32"])
def test_underreported_inflated_member_with_matching_prefix_crc_is_rejected(tmp_path, compression, member):
    """A self-consistent declared prefix cannot hide extra decompressed bytes."""
    built = _built(tmp_path / "author")
    with zipfile.ZipFile(built["artifact_path"]) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    prefix = members[member]
    hostile = tmp_path / "underreported.zip"
    with zipfile.ZipFile(hostile, "w", compression=compression) as archive:
        for name, raw in members.items():
            archive.writestr(name, raw + b"hidden decompressed data" * 100 if name == member else raw)
    with zipfile.ZipFile(hostile) as archive:
        entry = archive.getinfo(member)
        directory = archive.start_dir
    data = bytearray(hostile.read_bytes())
    # Forge both headers and the CRC to describe only the original, valid
    # payload. The compressed stream still inflates to a longer byte sequence.
    crc = binascii.crc32(prefix)
    struct.pack_into("<L", data, entry.header_offset + 14, crc)
    struct.pack_into("<L", data, entry.header_offset + 22, len(prefix))
    while data[directory:directory + 4] == b"PK\x01\x02":
        name_size, extra_size, comment_size = struct.unpack_from("<HHH", data, directory + 28)
        name = bytes(data[directory + 46:directory + 46 + name_size]).decode()
        if name == member:
            struct.pack_into("<L", data, directory + 16, crc)
            struct.pack_into("<L", data, directory + 24, len(prefix))
            break
        directory += 46 + name_size + extra_size + comment_size
    else:
        raise AssertionError("fixture central directory has no requested member")
    hostile.write_bytes(data)
    # Python's ordinary ZIP reader accepts the declared prefix and its forged
    # CRC. Admission must additionally validate the actual compressed stream.
    with zipfile.ZipFile(hostile) as archive:
        assert {name: archive.read(name) for name in archive.namelist()} == members
    with pytest.raises(PackageError, match="package_invalid_archive"):
        validate_package(Path(hostile))


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFDIR, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFBLK, stat.S_IFCHR])
def test_nonregular_member_modes_are_rejected_before_import(tmp_path, mode):
    built = _built(tmp_path / "author")
    hostile = tmp_path / "special-member.zip"
    with zipfile.ZipFile(built["artifact_path"]) as original, zipfile.ZipFile(hostile, "w") as archive:
        for name in original.namelist():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = ((mode if name == "pack.json" else stat.S_IFREG) | 0o600) << 16
            archive.writestr(info, original.read(name))
    with pytest.raises(PackageError, match="package_invalid_members"):
        validate_package(hostile)


def test_duplicate_names_cannot_replace_a_required_member(tmp_path):
    built = _built(tmp_path / "author")
    hostile = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(built["artifact_path"]) as original, zipfile.ZipFile(hostile, "w") as archive:
        for name in ("manifest.json", "pack.json", "chunks.jsonl"):
            archive.writestr(name, original.read(name))
        # Preserve exactly four central entries, so this reaches the member
        # identity check rather than merely failing the archive's entry count.
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("manifest.json", original.read("manifest.json"))
    with pytest.raises(PackageError, match="package_invalid_members"):
        validate_package(hostile)


@pytest.mark.parametrize("compression", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_unsupported_compression_is_rejected(tmp_path, compression):
    built = _built(tmp_path / "author")
    hostile = tmp_path / "unsupported.zip"
    with zipfile.ZipFile(built["artifact_path"]) as original, zipfile.ZipFile(hostile, "w", compression=compression) as archive:
        for name in original.namelist():
            archive.writestr(name, original.read(name))
    with pytest.raises(PackageError, match="package_invalid_members"):
        validate_package(hostile)


@pytest.mark.parametrize("value", [0.0, float("nan"), float("inf"), -float("inf"), 1e30, 1e-40, 2.0])
def test_rehashed_invalid_vectors_reach_numerical_validation(tmp_path, value):
    built = _built(tmp_path / "author")

    def invalid_vectors(raw):
        # Recompute the manifest member hash, preventing checksum mismatch
        # from masking a missing finite/nonzero/unit-norm vector defense.
        return struct.pack("<64f", value, *([0.0] * 63)) + raw[64 * 4:]

    hostile = _rewrite(Path(built["artifact_path"]), tmp_path / "numeric.zip",
                       member="vectors.f32", transform=invalid_vectors)
    with pytest.raises(PackageError, match="package_invalid_vector"):
        validate_package(hostile)


def test_duplicate_json_key_is_rejected_even_when_member_digest_matches(tmp_path):
    built = _built(tmp_path / "author")
    hostile = _rewrite(Path(built["artifact_path"]), tmp_path / "duplicate-key.zip", member="chunks.jsonl",
                       transform=lambda raw: raw.replace(b'"embedding_text":', b'"embedding_text":"substitute","embedding_text":', 1))
    with pytest.raises(PackageError, match="package_duplicate_key"):
        validate_package(hostile)


def test_plain_stored_package_remains_accepted(tmp_path):
    """The strict raw-stream checks must retain ordinary stored ZIP support."""
    built = _built(tmp_path / "author")
    target = tmp_path / "stored.zip"
    with zipfile.ZipFile(built["artifact_path"]) as original, zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in original.namelist():
            archive.writestr(name, original.read(name))
    checked = validate_package(target)
    assert checked.manifest["counts"] == {"documents": 1, "chunks": 1}
    assert len(checked.chunks) == 1


def test_nul_aliased_member_name_is_rejected_before_filename_normalization(tmp_path):
    built = _built(tmp_path / "author")
    hostile = tmp_path / "nul-name.zip"
    name = b"pack.jsonXhidden"
    with zipfile.ZipFile(built["artifact_path"]) as original, zipfile.ZipFile(hostile, "w") as archive:
        for member in original.namelist():
            archive.writestr(name.decode() if member == "pack.json" else member, original.read(member))
    raw = hostile.read_bytes()
    assert raw.count(name) == 2  # Local and central header names only.
    hostile.write_bytes(raw.replace(name, b"pack.json\0hidden"))
    # ZipInfo.filename strips the NUL suffix; orig_filename retains the
    # untrusted wire name. Admission must enforce the exact wire namespace.
    with zipfile.ZipFile(hostile) as archive:
        assert archive.getinfo("pack.json").orig_filename != "pack.json"
        assert archive.read("pack.json")
    with pytest.raises(PackageError, match="package_invalid_members"):
        validate_package(hostile)


@pytest.mark.parametrize("name", ["../pack.json", "/pack.json", "C:\\pack.json",
                                 "nested/../pack.json", "pack.json/", "DOCUMENTS.JSONL"])
def test_noncanonical_member_paths_are_rejected_with_exactly_four_entries(tmp_path, name):
    built = _built(tmp_path / "author")
    hostile = tmp_path / "path.zip"
    with zipfile.ZipFile(built["artifact_path"]) as original, zipfile.ZipFile(hostile, "w") as archive:
        for member in original.namelist():
            archive.writestr(name if member == "pack.json" else member, original.read(member))
    with pytest.raises(PackageError, match="package_invalid_members"):
        validate_package(hostile)


def test_encrypted_member_flag_is_rejected_without_decrypting(tmp_path):
    built = _built(tmp_path / "author")
    path = Path(built["artifact_path"])
    raw = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as archive:
        directory = archive.start_dir
        offset = archive.infolist()[0].header_offset
    struct.pack_into("<H", raw, offset + 6, struct.unpack_from("<H", raw, offset + 6)[0] | 1)
    struct.pack_into("<H", raw, directory + 8, struct.unpack_from("<H", raw, directory + 8)[0] | 1)
    hostile = tmp_path / "encrypted.zip"
    hostile.write_bytes(raw)
    with pytest.raises(PackageError, match="package_invalid_members"):
        validate_package(hostile)


@pytest.mark.parametrize("damage", ["unfinished", "trailing", "concatenated"])
def test_deflate_stream_must_end_once_at_its_declared_compressed_boundary(tmp_path, damage):
    """A correct decoded prefix/CRC cannot excuse missing EOF or extra streams."""
    built = _built(tmp_path / "author")
    hostile = tmp_path / "stream-boundary.zip"
    with zipfile.ZipFile(built["artifact_path"]) as original, zipfile.ZipFile(hostile, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("manifest.json", "pack.json", "chunks.jsonl", "vectors.f32"):
            archive.writestr(name, original.read(name))
    with zipfile.ZipFile(hostile) as archive:
        info = archive.getinfo("vectors.f32")
        vectors = archive.read(info)
        directory = archive.start_dir
    raw = hostile.read_bytes()
    name_size, extra_size = struct.unpack_from("<HH", raw, info.header_offset + 26)
    start = info.header_offset + 30 + name_size + extra_size
    assert start + info.compress_size == directory  # Mutate the last stream only.
    compressed = raw[start:directory]
    encoder = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    if damage == "unfinished":
        compressed = encoder.compress(vectors) + encoder.flush(zlib.Z_SYNC_FLUSH)
    elif damage == "trailing":
        compressed += b"hidden compressed-member bytes"
    else:
        compressed += encoder.compress(b"second stream") + encoder.flush()
    delta = len(compressed) - info.compress_size
    changed = bytearray(raw[:start] + compressed + raw[directory:])
    struct.pack_into("<L", changed, info.header_offset + 18, len(compressed))
    cursor = directory + delta
    while changed[cursor:cursor + 4] == b"PK\x01\x02":
        name_size, extra_size, comment_size = struct.unpack_from("<HHH", changed, cursor + 28)
        name = bytes(changed[cursor + 46:cursor + 46 + name_size]).decode()
        if name == "vectors.f32":
            struct.pack_into("<L", changed, cursor + 20, len(compressed))
        cursor += 46 + name_size + extra_size + comment_size
    struct.pack_into("<L", changed, len(changed) - 22 + 16, directory + delta)
    hostile.write_bytes(changed)
    with zipfile.ZipFile(hostile) as archive:
        assert archive.read("vectors.f32") == vectors
    with pytest.raises(PackageError, match="package_invalid_archive"):
        validate_package(hostile)
