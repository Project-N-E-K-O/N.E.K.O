import base64
import io
import json
import sys

import pytest
from PIL import Image

from utils import chat_avatar_store as store

UID = "a" * 32


def png(size=(320, 320), color=(12, 34, 56, 70), format="PNG"):
    output = io.BytesIO()
    Image.new("RGBA" if format == "PNG" else "RGB", size, color if format == "PNG" else color[:3]).save(output, format=format)
    return output.getvalue()


def assert_error(code, function, *args):
    with pytest.raises(store.ChatAvatarError) as error:
        function(*args)
    assert error.value.code == code
    return error.value


def test_round_trip_alpha_and_restart(tmp_path):
    normalized = store.normalize_png(png())
    record = store.write_record(tmp_path, UID, normalized, "0", "save-one")
    assert store.read_record(tmp_path, UID) == record
    payload = base64.b64decode(record["data_url"].split(",", 1)[1])
    with Image.open(io.BytesIO(payload)) as image:
        assert image.size == (320, 320)
        assert image.getpixel((0, 0)) == (12, 34, 56, 70)
        assert not image.info


def test_missing_and_clear_have_distinct_revisions(tmp_path):
    missing = store.read_record(tmp_path, UID)
    assert missing["data_url"] is None and missing["revision"] == "0"
    cleared = store.write_record(tmp_path, UID, None, "0", "restore")
    assert cleared["data_url"] is None and cleared["revision"] != "0"
    assert_error("chat_avatar_conflict", store.write_record, tmp_path, UID, store.normalize_png(png()), "0", "stale-save")


def test_idempotence_never_replays_older_operation(tmp_path):
    data = store.normalize_png(png())
    first = store.write_record(tmp_path, UID, data, "0", "first")
    assert store.write_record(tmp_path, UID, data, "0", "first") == first
    assert_error("chat_avatar_conflict", store.write_record, tmp_path, UID, None, "0", "first")
    second = store.write_record(tmp_path, UID, None, first["revision"], "second")
    assert_error("chat_avatar_conflict", store.write_record, tmp_path, UID, data, "0", "first")
    assert store.read_record(tmp_path, UID) == second


@pytest.mark.parametrize("uid", ["../escape", "A" * 32, "a" * 31, "", None])
def test_uid_filename_validation(tmp_path, uid):
    assert_error("chat_avatar_invalid_request", store.read_record, tmp_path, uid)


@pytest.mark.parametrize("payload", [b"not png", b"\x89PNG\r\n\x1a\n" + b"\0" * 16, png((1, 1)), png((320, 320), format="JPEG"), png()[:70]], ids=["text", "header", "small", "jpeg", "truncated"])
def test_real_decode_rejects_invalid_images(payload):
    assert_error("chat_avatar_invalid_image", store.normalize_png, payload)


def test_reject_animated_png():
    output = io.BytesIO()
    Image.new("RGBA", (320, 320), "red").save(output, format="PNG", save_all=True,
                                              append_images=[Image.new("RGBA", (320, 320), "blue")])
    assert_error("chat_avatar_invalid_image", store.normalize_png, output.getvalue())


def test_normalized_byte_limit():
    error = assert_error("chat_avatar_too_large", store.normalize_png, b"x" * (store.NORMALIZED_MAX_BYTES + 1))
    assert error.status == 413


@pytest.mark.parametrize("revision,operation", [("", "op"), ("0", ""), (None, "op"), ("0", "../x"), ("0", "x" * 129)])
def test_operation_validation(revision, operation):
    assert_error("chat_avatar_invalid_request", store.validate_operation, revision, operation)


@pytest.mark.parametrize("contents", [b"{", b"{}", b"null", b"x" * (store.MAX_RECORD_BYTES + 1)], ids=["bad-json", "empty-object", "null", "oversized"])
def test_corrupt_record_is_not_absent_or_overwritten(tmp_path, contents):
    path = tmp_path / f"{UID}.json"
    path.write_bytes(contents)
    assert_error("chat_avatar_record_corrupt", store.read_record, tmp_path, UID)
    assert_error("chat_avatar_record_corrupt", store.write_record, tmp_path, UID, None, "0", "save")
    assert path.read_bytes() == contents


def test_corrupt_image_inside_valid_record(tmp_path):
    record = store.write_record(tmp_path, UID, store.normalize_png(png()), "0", "save")
    record["data_url"] = store.PNG_DATA_PREFIX + base64.b64encode(b"broken").decode()
    (tmp_path / f"{UID}.json").write_text(json.dumps(record))
    assert_error("chat_avatar_record_corrupt", store.read_record, tmp_path, UID)


def test_quota_accounts_other_uid_and_replacement(tmp_path, monkeypatch):
    data = store.normalize_png(png())
    first = store.write_record(tmp_path, UID, data, "0", "first")
    size = (tmp_path / f"{UID}.json").stat().st_size
    monkeypatch.setattr(store, "STORAGE_QUOTA_BYTES", size + 10)
    assert_error("chat_avatar_quota_exceeded", store.write_record, tmp_path, "b" * 32, data, "0", "other")
    cleared = store.write_record(tmp_path, UID, None, first["revision"], "clear")
    assert cleared["data_url"] is None


@pytest.mark.parametrize("exception", [PermissionError("denied"), OSError(28, "disk full"), PermissionError(13, "file occupied")])
def test_atomic_write_failure_preserves_committed_record(tmp_path, monkeypatch, exception):
    first = store.write_record(tmp_path, UID, store.normalize_png(png()), "0", "first")
    monkeypatch.setattr(store, "atomic_write_text", lambda *args, **kwargs: (_ for _ in ()).throw(exception))
    assert_error("chat_avatar_write_failed", store.write_record, tmp_path, UID, None, first["revision"], "clear")
    assert store.read_record(tmp_path, UID) == first


def test_read_permission_failure_is_distinct(tmp_path, monkeypatch):
    monkeypatch.setattr(store.Path, "open", lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("denied")))
    assert_error("chat_avatar_read_failed", store.read_record, tmp_path, UID)


def test_delete_only_target_uid(tmp_path):
    store.write_record(tmp_path, UID, None, "0", "one")
    store.write_record(tmp_path, "b" * 32, None, "0", "two")
    store.remove_record(tmp_path, UID)
    store.remove_record(tmp_path, UID)
    assert store.read_record(tmp_path, UID)["revision"] == "0"
    assert store.read_record(tmp_path, "b" * 32)["revision"] != "0"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing mode")
def test_real_windows_file_occupation_preserves_old_record(tmp_path):
    import ctypes
    from ctypes import wintypes

    first = store.write_record(tmp_path, UID, store.normalize_png(png()), "0", "first")
    kernel = ctypes.windll.kernel32
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # Explorer/antivirus can open files for reading without FILE_SHARE_DELETE.
    handle = kernel.CreateFileW(str(tmp_path / f"{UID}.json"), 0x80000000, 0x1 | 0x2, None, 3, 0, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        assert_error("chat_avatar_write_failed", store.write_record, tmp_path, UID, None, first["revision"], "clear")
    finally:
        assert kernel.CloseHandle(handle)
    assert store.read_record(tmp_path, UID) == first
