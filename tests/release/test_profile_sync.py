"""Exercise installation and one-time retirement against disposable profiles."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from scripts import sync_to_profiles as installer


class ProfileSyncTests(unittest.TestCase):
    def test_verified_install_then_retire_old_copy_with_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dist").mkdir()
            with zipfile.ZipFile(root / "dist/visicom_data_api.zip", "w") as archive:
                archive.writestr("visicom_data_api/__init__.py", "# synthetic\n")
            profile = root / "QGIS/QGIS3/profiles/test"
            parent = profile / "python/plugins"
            old = parent / "ua_address_search"
            old.mkdir(parents=True)
            (old / "__init__.py").write_text("# old\n", encoding="utf-8")
            old_manifest = installer.manifest(old)
            with (
                patch.object(installer, "ROOT", root),
                patch.object(installer, "running_qgis", return_value=False),
                patch.dict(os.environ, {"APPDATA": str(root)}),
            ):
                targets = installer.sync(remove_old=True)
            self.assertEqual(targets, [str(parent / "visicom_data_api")])
            self.assertFalse(old.exists())
            backups = list((profile / "python/visicom_plugin_backups").iterdir())
            self.assertEqual(len(backups), 1)
            self.assertEqual(installer.manifest(backups[0]), old_manifest)
            self.assertEqual((parent / "visicom_data_api/__init__.py").read_text(), "# synthetic\n")

    def test_running_guard_and_unsafe_archive_do_not_retire(self):
        with patch.object(installer, "running_qgis", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "Close all QGIS"):
                installer.sync(allow_running=True, remove_old=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dist").mkdir()
            with zipfile.ZipFile(root / "dist/visicom_data_api.zip", "w") as archive:
                archive.writestr("visicom_data_api/../escape.py", "bad")
            with (
                patch.object(installer, "ROOT", root),
                patch.object(installer, "running_qgis", return_value=False),
                patch.dict(os.environ, {"APPDATA": str(root)}),
            ):
                with self.assertRaisesRegex(RuntimeError, "Unsafe ZIP"):
                    installer.sync(remove_old=True)
