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
    "neko_maintenance", _ROOT / "scripts/maintain_local.py"
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
        maintain.verify(self.repo, self.policy())
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

    def policy(self, code="pass"):
        path = self.root / "checks.json"
        path.write_text(
            json.dumps(
                {
                    "suites": [
                        {
                            "name": "check",
                            "command": [sys.executable, "-c", code],
                        }
                    ]
                }
            )
        )
        return path

    def assert_failed_verification(self, policy):
        with self.assertRaisesRegex(ValueError, "Verification failed"):
            maintain.verify(self.repo, policy)
        state = maintain.state_dir(self.repo)
        self.assertFalse((state / "verified.json").exists())
        report = max(state.glob("verify-*/receipt.json"))
        return json.loads(report.read_text())

    def pending_merge(self, conflict=False):
        self.git("switch", "-c", "official")
        path = "app/main.py" if conflict else "upstream.txt"
        (self.repo / path).write_text("upstream\n")
        upstream = self.commit("upstream")
        self.git("update-ref", "refs/remotes/origin/main", upstream)
        self.git("switch", "local/test")
        (self.repo / "app/main.py").write_text("local\n")
        self.commit("local")
        result = subprocess.run(
            ["git", "merge", "--no-ff", "--no-commit", "official"],
            cwd=self.repo,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 1 if conflict else 0)

    def test_conflict_backup_rejects_before_writing_and_preserves_merge(self):
        self.pending_merge(conflict=True)
        index = (self.repo / ".git/index").read_bytes()
        working = (self.repo / "app/main.py").read_bytes()
        merge_head = (self.repo / ".git/MERGE_HEAD").read_bytes()
        for destination in (None, self.root / "backups"):
            with self.subTest(destination=destination):
                with self.assertRaisesRegex(ValueError, "Resolve or abort"):
                    maintain.backup(self.repo, destination)
        self.assertFalse((self.repo / ".git/local-maintenance").exists())
        self.assertFalse((self.root / "backups").exists())
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assertEqual((self.repo / "app/main.py").read_bytes(), working)
        self.assertEqual((self.repo / ".git/MERGE_HEAD").read_bytes(), merge_head)
        self.git("merge", "--abort")

    def test_backup_round_trip_restores_index_working_and_binary_untracked(self):
        path = self.repo / "app/main.py"
        path.write_text("staged\n")
        self.git("add", ".")
        path.write_text("working\n")
        (self.repo / "new file.bin").write_bytes(b"\x00\xff\x01")
        saved = maintain.backup(self.repo)
        restored = self.root / "restored"
        subprocess.run(
            ["git", "clone", str(saved / "history.bundle"), str(restored)],
            check=True,
            capture_output=True,
        )
        for filename, options in (("staged.patch", ["--index"]), ("working.patch", [])):
            if (saved / filename).stat().st_size:
                subprocess.run(
                    ["git", "apply", *options, str(saved / filename)],
                    cwd=restored,
                    check=True,
                )
        with tarfile.open(saved / "untracked.tar.gz") as archive:
            archive.extractall(restored, filter="data")
        self.assertEqual(
            subprocess.check_output(["git", "show", ":app/main.py"], cwd=restored),
            b"staged\n",
        )
        self.assertEqual((restored / "app/main.py").read_text(), "working\n")
        self.assertEqual((restored / "new file.bin").read_bytes(), b"\x00\xff\x01")

    def test_clean_backup_restores_with_empty_patches_skipped(self):
        saved = maintain.backup(self.repo)
        restored = self.root / "restored"
        subprocess.run(
            ["git", "clone", str(saved / "history.bundle"), str(restored)],
            check=True,
            capture_output=True,
        )
        for filename in ("staged.patch", "working.patch"):
            self.assertEqual((saved / filename).stat().st_size, 0)
        self.assertEqual((restored / "app/main.py").read_text(), "version = 1\n")

    def test_status_preserves_unstaged_first_column(self):
        import io
        from contextlib import redirect_stdout

        (self.repo / "app/main.py").write_text("dirty\n")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        expected = " M app/main.py"
        saved = maintain.backup(self.repo)
        self.assertEqual(
            json.loads((saved / "receipt.json").read_text())["status"], expected
        )
        self.assertEqual(
            maintain.upstream_review(self.repo)["working_changes"], expected
        )
        stream = io.StringIO()
        with redirect_stdout(stream):
            maintain.status(self.repo)
        self.assertEqual(json.loads(stream.getvalue())["working_changes"], expected)

    def test_official_identity_allows_transport_rewrite_but_rejects_fork(self):
        with patch.dict(
            os.environ,
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "url.git@github.com:.insteadOf",
                "GIT_CONFIG_VALUE_0": "https://github.com/",
            },
        ):
            self.assertTrue(self.git("remote", "get-url", "origin").startswith("git@"))
            maintain.official_required(self.repo)
            self.git(
                "remote", "set-url", "origin", "https://github.com/other/N.E.K.O.git"
            )
            with self.assertRaisesRegex(ValueError, "not the configured"):
                maintain.official_required(self.repo)

    @unittest.skipUnless(
        sys.platform.startswith("linux"), "Raw non-UTF-8 filenames require Linux"
    )
    def test_non_utf8_paths_round_trip_archive_receipt_and_review(self):
        name = os.fsdecode(b"raw-\xff.txt")
        path = self.repo / name
        path.write_bytes(b"preserved")
        saved = maintain.backup(self.repo)
        receipt = json.loads((saved / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(os.fsencode(receipt["untracked"][0]), b"raw-\xff.txt")
        restored = self.root / "restored"
        with tarfile.open(saved / "untracked.tar.gz") as archive:
            archive.extractall(restored, filter="data")
        self.assertEqual((restored / name).read_bytes(), b"preserved")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.commit("raw filename")
        report = maintain.upstream_review(self.repo)
        self.assertEqual(report["local_changed_paths"], [name])
        maintain.write_json(self.root / "review.json", report)
        self.assertEqual(json.loads((self.root / "review.json").read_text()), report)

    def test_verify_detects_untracked_creation_modification_deletion_and_rename(self):
        path = self.repo / "new.py"
        operations = (
            "p.write_text('new')",
            "p.write_text('changed')",
            "p.unlink()",
            "p.rename('renamed.py')",
        )
        for operation in operations:
            with self.subTest(operation=operation):
                path.unlink(missing_ok=True)
                (self.repo / "renamed.py").unlink(missing_ok=True)
                if operation != operations[0]:
                    path.write_text("original")
                maintain.verify(self.repo, self.policy())
                policy = self.policy(
                    "from pathlib import Path; p=Path('new.py'); " + operation
                )
                self.assertFalse(
                    self.assert_failed_verification(policy)["source_unchanged"]
                )

    def test_verify_allows_unchanged_untracked_and_ignored_generated_files(self):
        (self.repo / ".gitignore").write_text("generated/\n")
        self.commit("ignore outputs")
        (self.repo / "new.py").write_text("unchanged")
        maintain.verify(
            self.repo,
            self.policy(
                "from pathlib import Path; Path('generated').mkdir(); "
                "Path('generated/output').write_text('build')"
            ),
        )
        receipt = json.loads(
            (maintain.state_dir(self.repo) / "verified.json").read_text()
        )
        self.assertTrue(receipt["source_unchanged"])
        self.assertEqual(list(receipt["source_snapshot"]["untracked"]), ["new.py"])

    @unittest.skipIf(os.name == "nt", "Symlink creation requires Windows privileges")
    def test_verify_detects_untracked_symlink_retargeting(self):
        (self.repo / "link").symlink_to("missing-one")
        receipt = self.assert_failed_verification(
            self.policy(
                "from pathlib import Path; p=Path('link'); p.unlink(); p.symlink_to('missing-two')"
            )
        )
        self.assertFalse(receipt["source_unchanged"])

    def test_invalid_manifests_run_no_commands_and_remove_stale_success(self):
        valid = {
            "name": "first",
            "command": [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('ran-suite').touch()",
            ],
        }
        invalid = [None, [], {}, {"suites": []}, {"suites": {}}, {"suites": [None]}]
        bad_suites = [
            {},
            {"name": "missing-command"},
            {"command": ["git"]},
            {"name": "../escape", "command": ["git"]},
            {"name": "bad", "command": "git status"},
            {"name": "bad", "command": []},
            {"name": "bad", "command": [1]},
            {"name": "bad", "command": [""]},
            {"name": "bad", "command": ["git"], "cwd": 1},
            {"name": "bad", "command": ["git"], "cwd": "missing"},
            valid,
        ]
        invalid.extend({"suites": [valid, bad]} for bad in bad_suites)
        state = maintain.state_dir(self.repo)
        for value in invalid:
            with self.subTest(value=value):
                (state / "verified.json").write_text("old success")
                policy = self.root / "invalid.json"
                policy.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    maintain.verify(self.repo, policy)
                self.assertFalse((self.repo / "ran-suite").exists())
                self.assertFalse((state / "verified.json").exists())
                self.assertEqual(list(state.glob("verify-*")), [])

    def test_invalid_manifest_cli_reports_error_without_traceback(self):
        import shutil

        script = self.repo / "scripts/maintain_local.py"
        script.parent.mkdir()
        shutil.copyfile(_ROOT / "scripts/maintain_local.py", script)
        policy = self.root / "invalid.json"
        policy.write_text('{"suites": []}')
        result = subprocess.run(
            [sys.executable, str(script), "verify", "--regression", str(policy)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("non-empty suites", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_verify_pending_merge_requires_staged_tested_content(self):
        self.pending_merge()
        (self.repo / "app/main.py").write_text("untested index\n")
        with self.assertRaisesRegex(ValueError, "Stage all tracked changes"):
            maintain.verify(self.repo, self.policy())
        self.git("add", "app/main.py")
        maintain.verify(self.repo, self.policy())
        self.assertTrue((maintain.state_dir(self.repo) / "verified.json").exists())

    def test_verify_detects_index_only_mutation_during_pending_merge(self):
        self.pending_merge()
        receipt = self.assert_failed_verification(
            self.policy(
                "import subprocess; subprocess.run(['git', 'restore', '--source=official', "
                "'--staged', '--', 'app/main.py'], check=True)"
            )
        )
        self.assertFalse(receipt["source_unchanged"])

    def test_verify_detects_tracked_and_head_mutations(self):
        for code in (
            "from pathlib import Path; Path('app/main.py').write_text('changed')",
            "import subprocess; subprocess.run(['git', 'commit', '--allow-empty', '-m', 'changed'], check=True)",
        ):
            with self.subTest(code=code):
                self.assertFalse(
                    self.assert_failed_verification(self.policy(code))[
                        "source_unchanged"
                    ]
                )

    def test_example_checks_history_index_and_worktree_whitespace(self):
        policy = json.loads(
            (_ROOT / "local-desktop-ui/regression.example.json").read_text()
        )
        suites = [
            suite
            for suite in policy["suites"]
            if suite["name"].startswith("whitespace")
        ]
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        for state in ("staged", "working", "history"):
            with self.subTest(state=state):
                (self.repo / "app/main.py").write_text("bad whitespace \n")
                if state in ("staged", "history"):
                    self.git("add", "app/main.py")
                if state == "history":
                    self.commit("bad whitespace")
                results = [
                    subprocess.run(suite["command"], cwd=self.repo, capture_output=True)
                    for suite in suites
                ]
                self.assertTrue(any(result.returncode for result in results))
                (self.repo / "app/main.py").write_text(f"clean {state}\n")
                self.commit("clean whitespace")
                self.git("update-ref", "refs/remotes/origin/main", "HEAD")
