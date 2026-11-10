"""Portable Git backup and integration review contracts."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "neko_maintenance", _ROOT / "local-desktop-ui/maintain.py"
)
maintain = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(maintain)


class MaintenanceTests(unittest.TestCase):
    def test_verify_uses_portable_example_without_a_personal_policy(self):
        policy = self.repo / "local-desktop-ui/regression.example.json"
        policy.parent.mkdir()
        policy.write_text(
            json.dumps(
                {
                    "suites": [
                        {
                            "name": "portable",
                            "command": [sys.executable, "-c", "print('checked')"],
                        }
                    ]
                }
            )
        )
        self.commit("add example")
        maintain.verify(self.repo)
        receipt = json.loads(
            (maintain.state_dir(self.repo) / "verified.json").read_text()
        )
        self.assertEqual(receipt["commit"], self.git("rev-parse", "HEAD"))
        self.assertTrue(receipt["source_unchanged"])
        self.assertEqual(receipt["results"][0]["returncode"], 0)

    def test_explicit_failing_policy_cannot_mark_source_verified(self):
        policy = self.root / "checks.json"
        policy.write_text(
            json.dumps(
                {
                    "suites": [
                        {
                            "name": "failure",
                            "command": [sys.executable, "-c", "raise SystemExit(3)"],
                        }
                    ]
                }
            )
        )
        with self.assertRaisesRegex(ValueError, "Verification failed"):
            maintain.verify(self.repo, policy)
        self.assertFalse((maintain.state_dir(self.repo) / "verified.json").exists())

    def setUp(self):
        self.identity = patch.dict(
            os.environ,
            {
                "GIT_AUTHOR_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@example.invalid",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "test@example.invalid",
            },
        )
        self.identity.start()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "local/test")
        self.git("remote", "add", "origin", maintain.OFFICIAL_URL)
        (self.repo / "app").mkdir()
        (self.repo / "app/main.py").write_text("version = 1\n")
        self.commit("initial")

    def tearDown(self):
        self.temp.cleanup()
        self.identity.stop()

    def git(self, *args):
        return (
            subprocess.check_output(
                ["git", *args], cwd=self.repo, stderr=subprocess.PIPE
            )
            .decode()
            .strip()
        )

    def commit(self, message):
        self.git("add", ".")
        self.git(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            message,
        )
        return self.git("rev-parse", "HEAD")

    def test_backup_preserves_index_worktree_and_untracked_separately(self):
        path = self.repo / "app/main.py"
        path.write_text("version = 2\n")
        self.git("add", "app/main.py")
        path.write_text("version = 3\n")
        (self.repo / "new file.txt").write_text("uncommitted")
        before = self.git("status", "--porcelain")
        saved = maintain.backup(self.repo, self.root / "backups")
        self.assertEqual(before, self.git("status", "--porcelain"))
        self.assertIn("+version = 2", (saved / "staged.patch").read_text())
        self.assertIn("+version = 3", (saved / "working.patch").read_text())
        with tarfile.open(saved / "untracked.tar.gz") as archive:
            self.assertEqual(archive.extractfile("new file.txt").read(), b"uncommitted")
        self.assertTrue((saved / "history.bundle").is_file())

    def test_backup_rejects_recursive_worktree_destination(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            maintain.backup(self.repo, self.repo / "backup")

    def test_dirty_merge_does_not_touch_the_worktree(self):
        (self.repo / "untracked").write_text("keep me")
        with self.assertRaisesRegex(ValueError, "Commit"):
            maintain.merge_upstream(self.repo)
        self.assertEqual((self.repo / "untracked").read_text(), "keep me")

    def test_review_reports_overlap_without_touching_dirty_index_or_files(self):
        self.git("switch", "-c", "official")
        (self.repo / "app/main.py").write_text("version = 'official'\n")
        (self.repo / "official.txt").write_text("new upstream file\n")
        upstream = self.commit("upstream")
        self.git("update-ref", "refs/remotes/origin/main", upstream)
        self.git("switch", "local/test")
        (self.repo / "app/main.py").write_text("version = 'local'\n")
        head = self.commit("local")
        (self.repo / "app/main.py").write_text("staged\n")
        self.git("add", "app/main.py")
        (self.repo / "app/main.py").write_text("unstaged\n")
        (self.repo / "private.txt").write_text("do not read or include this content")
        before = [
            self.git(*args)
            for args in [("status", "--porcelain"), ("diff",), ("diff", "--cached")]
        ]
        report = maintain.upstream_review(self.repo)
        after = [
            self.git(*args)
            for args in [("status", "--porcelain"), ("diff",), ("diff", "--cached")]
        ]
        self.assertEqual(before, after)
        self.assertEqual(report["head"], head)
        self.assertEqual(report["upstream"], upstream)
        self.assertEqual(report["overlap_paths"], ["app/main.py"])
        self.assertEqual(report["upstream_high_risk_paths"], ["app/main.py"])
        self.assertEqual(report["upstream_only_commits"], 1)
        self.assertNotIn("private.txt", report["local_changed_paths"])

    def test_review_expands_rename_and_handles_identical_branches(self):
        base = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", base)
        self.assertEqual(maintain.upstream_review(self.repo)["overlap_paths"], [])
        self.git("switch", "-c", "official")
        self.git("mv", "app/main.py", "app/renamed.py")
        upstream = self.commit("rename upstream")
        self.git("update-ref", "refs/remotes/origin/main", upstream)
        self.git("switch", "local/test")
        (self.repo / "app/main.py").write_text("local change\n")
        self.commit("local")
        report = maintain.upstream_review(self.repo)
        self.assertEqual(report["overlap_paths"], ["app/main.py"])
        self.assertEqual(
            report["upstream_changed_paths"], ["app/main.py", "app/renamed.py"]
        )

    def test_conflicting_merge_can_abort_without_losing_local_commit(self):
        base = self.git("rev-parse", "HEAD")
        self.git("switch", "-c", "official")
        (self.repo / "app/main.py").write_text("version = 'official'\n")
        official = self.commit("official update")
        self.git("update-ref", "refs/remotes/origin/main", official)
        self.git("switch", "local/test")
        (self.repo / "app/main.py").write_text("version = 'local'\n")
        local = self.commit("local customization")
        with self.assertRaisesRegex(ValueError, "resolution"):
            maintain.merge_upstream(self.repo, self.root / "backups")
        self.assertTrue(self.git("ls-files", "--unmerged"))
        self.git("merge", "--abort")
        self.assertEqual(self.git("rev-parse", "HEAD"), local)
        self.assertEqual((self.repo / "app/main.py").read_text(), "version = 'local'\n")
        self.assertNotEqual(base, official)
