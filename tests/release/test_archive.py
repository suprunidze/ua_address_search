import configparser
import hashlib
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]


class ReleaseTests(unittest.TestCase):
    def test_archive_is_exact_runtime_and_mit(self):
        from scripts.package_plugin import runtime_files

        with zipfile.ZipFile(ROOT / "dist/ua_address_search.zip") as archive:
            expected = {
                p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in runtime_files()
            }
            actual = {
                name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
            }
            self.assertEqual(actual, expected)
            self.assertEqual(
                archive.read("ua_address_search/LICENSE"), (ROOT / "LICENSE").read_bytes()
            )
            self.assertIn(b"MIT License", archive.read("ua_address_search/LICENSE"))
            for name in archive.namelist():
                self.assertFalse(
                    any(
                        part in name
                        for part in ("__pycache__", ".env", "tests/", ".secret", ".git/")
                    )
                )

    def test_manifest_versions_match(self):
        metadata = configparser.ConfigParser()
        metadata.read(ROOT / "ua_address_search/metadata.txt", encoding="utf-8")
        version = metadata["general"]["version"]
        entry = ET.parse(ROOT / "dist/plugins.xml").getroot().find("pyqgis_plugin")
        self.assertEqual(entry.attrib["version"], version)
        self.assertEqual(entry.findtext("version"), version)
        self.assertEqual(entry.findtext("file_name"), "ua_address_search.zip")
