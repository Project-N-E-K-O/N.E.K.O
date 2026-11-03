"""First-write consistency of the persisted telemetry A/B branch.

With more than one active branch, a reader that sees the branch file between another
writer's O_EXCL create and its write must not heal it with its own draw; otherwise the
cache and the disk (and so the frontend default and later reports) can disagree.
"""
import threading
import time
from unittest.mock import patch

import utils.token_tracker as tt
import utils.token_tracker.telemetry as telemetry


def _branch_file(config_dir):
    return config_dir / tt._TELEMETRY_BRANCH_FILE


def test_empty_branch_file_waits_for_the_peer_writer_instead_of_healing(tmp_path):
    tt._telemetry_branch_cache.clear()
    _branch_file(tmp_path).write_text("", encoding="utf-8")  # peer created the file, not yet written

    def peer_write():
        time.sleep(0.05)
        _branch_file(tmp_path).write_text("proactive_interval_40s", encoding="utf-8")

    writer = threading.Thread(target=peer_write)
    writer.start()
    try:
        with patch.object(telemetry.secrets, "choice", return_value="main"):
            branch = tt._get_telemetry_branch(tmp_path)
    finally:
        writer.join()

    assert branch == "proactive_interval_40s"
    assert _branch_file(tmp_path).read_text(encoding="utf-8").strip() == "proactive_interval_40s"


def test_concurrent_first_resolves_in_one_process_agree_with_disk(tmp_path):
    tt._telemetry_branch_cache.clear()
    results = []
    start = threading.Barrier(8)

    def resolve():
        start.wait()
        results.append(tt._get_telemetry_branch(tmp_path))

    threads = [threading.Thread(target=resolve) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    on_disk = _branch_file(tmp_path).read_text(encoding="utf-8").strip()
    assert set(results) == {on_disk}
    assert on_disk in tt._TELEMETRY_BRANCHES
