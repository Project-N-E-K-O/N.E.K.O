"""Exercise installations against the tracked, real built-in model archives."""

import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2] / "local-desktop-ui"
sys.path.insert(0, str(TOOLS))
import install_yui_profile as profiles


class OutfitInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def model(self, name):
        with tarfile.open(TOOLS.parent / "assets" / f"{name}.tar.gz") as archive:
            # Only the installer inputs; textures and private user models are unnecessary.
            for member in archive.getmembers():
                if member.name.endswith((".model3.json", ".moc3")):
                    archive.extract(member, self.root, filter="data")
        return self.root / name / f"{name}.model3.json"

    def test_both_builtin_outfits_install_all_profiles_and_repeat_without_writes(self):
        for name in ("yui-origin", "yui-lolita"):
            with self.subTest(outfit=name):
                model = self.model(name)
                original = json.loads(model.read_text())
                preview = profiles.install_model(model, emotions=True)
                self.assertTrue(preview["changed"])
                self.assertFalse((model.parent / "expressions").exists())
                result = profiles.install_model(
                    model, backups=self.root / "backups", apply=True, emotions=True
                )
                installed = json.loads(model.read_text())
                self.assertEqual(
                    installed["FileReferences"]["Motions"],
                    original["FileReferences"]["Motions"],
                )
                self.assertEqual(
                    installed["FileReferences"]["Moc"],
                    original["FileReferences"]["Moc"],
                )
                for asset in profiles.PROFILE_ROOT.glob("*.exp3.json"):
                    self.assertEqual(
                        (model.parent / "expressions" / asset.name).read_bytes(),
                        asset.read_bytes(),
                    )
                for emotion in (
                    "happy",
                    "sad",
                    "angry",
                    "surprised",
                    "neutral",
                    "listening-sad",
                ):
                    self.assertEqual(
                        len(installed["EmotionMapping"]["expressions"][emotion]), 1
                    )
                before = {
                    p: (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in model.parent.rglob("*")
                    if p.is_file()
                }
                again = profiles.install_model(
                    model, backups=self.root / "backups", apply=True, emotions=True
                )
                self.assertFalse(again["changed"])
                self.assertEqual(
                    before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}
                )
                self.assertEqual(
                    json.loads((Path(result["backup"]) / model.name).read_text()),
                    original,
                )

    def test_changed_moc_and_custom_mapping_fail_before_any_write(self):
        model = self.model("yui-origin")
        moc = model.parent / "yui-origin.moc3"
        original_moc = moc.read_bytes()
        moc.write_bytes(b"different rig")
        with self.assertRaisesRegex(ValueError, "model changed"):
            profiles.install_model(
                model, backups=self.root / "backups", apply=True, emotions=True
            )
        moc.write_bytes(original_moc)
        data = json.loads(model.read_text())
        data.setdefault("EmotionMapping", {}).setdefault("expressions", {})[
            "listening"
        ] = ["mine.json"]
        model.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "Custom mapping"):
            profiles.install_model(
                model, backups=self.root / "backups", apply=True, emotions=True
            )
        self.assertFalse((self.root / "backups").exists())
        self.assertFalse((model.parent / "expressions").exists())
        self.assertEqual(json.loads(model.read_text()), data)

    def test_write_failure_has_complete_receipt_and_preserves_model_references(self):
        model = self.model("yui-lolita")
        original = model.read_bytes()
        target = model.parent / "expressions/chat-angry.exp3.json"
        target.parent.mkdir()
        target.write_text("old profile")
        with patch.object(Path, "replace", side_effect=OSError("controlled failure")):
            with self.assertRaisesRegex(OSError, "controlled failure"):
                profiles.install_model(
                    model, backups=self.root / "backups", apply=True, emotions=True
                )
        receipt = next((self.root / "backups").glob("*/receipt.json"))
        files = json.loads(receipt.read_text())["files"]
        self.assertEqual(
            len(files), len(list(profiles.PROFILE_ROOT.glob("*.exp3.json")))
        )
        self.assertTrue(
            next(entry for entry in files if entry["file"] == str(target.resolve()))[
                "existed"
            ]
        )
        self.assertEqual((receipt.parent / target.name).read_text(), "old profile")
        self.assertEqual((receipt.parent / model.name).read_bytes(), original)
        self.assertEqual(model.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
