"""Keep damaged packages distinct from temporary filesystem read failures."""

import errno

import pytest

from tests.unit.test_knowledge_archive_v2 import make_archive
from utils import knowledge_package as package


pytestmark = pytest.mark.unit_fast


@pytest.mark.parametrize("step", ["_preflight_archive", "_check_members"])
@pytest.mark.parametrize("code", [errno.EIO, errno.EACCES])
def test_package_validation_preserves_filesystem_failures(tmp_path, monkeypatch, step, code):
    path = make_archive(tmp_path / "valid.zip")
    failure = OSError(code, "synthetic storage failure")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(package, step, fail)
    with pytest.raises(OSError) as caught:
        package.validate_package(path)
    assert caught.value is failure


def test_malformed_archive_still_has_a_package_error(tmp_path):
    path = tmp_path / "invalid.zip"
    path.write_bytes(b"PK\x03\x04" + b"invalid ZIP member data" * 3)
    with pytest.raises(package.PackageError) as caught:
        package.validate_package(path)
    assert caught.value.reason == "package_invalid_archive"
