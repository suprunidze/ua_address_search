import configparser
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]


class ReleaseTests(unittest.TestCase):
    def test_offline_help_is_bundled_utf8_and_has_no_automatic_external_resources(self):
        class Resources(HTMLParser):
            def handle_starttag(self, tag, attributes):
                if tag in ("script", "link", "img", "iframe", "object"):
                    raise AssertionError("Help must not load external resources")

        with zipfile.ZipFile(ROOT / "dist/visicom_data_api.zip") as archive:
            content = archive.read("visicom_data_api/resources/help.html")
            self.assertEqual(content, (ROOT / "visicom_data_api/resources/help.html").read_bytes())
            html = content.decode("utf-8")
            self.assertIn('lang="uk"', html)
            self.assertIn("Найближча адреса", html)
            self.assertIn("register_form&amp;lang=uk", html)
            self.assertIn("не блокує запити", html)
            for internal in ("SQLite", "/v1/", "SHA-256", "Bearer", "резервування"):
                self.assertNotIn(internal, html)
            self.assertNotIn("url(", html.lower())
            Resources().feed(html)

    def test_archive_is_exact_runtime_and_mit(self):
        from scripts.package_plugin import runtime_files

        with zipfile.ZipFile(ROOT / "dist/visicom_data_api.zip") as archive:
            expected = {
                p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in runtime_files()
            }
            actual = {
                name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
            }
            self.assertEqual(actual, expected)
            self.assertEqual(
                archive.read("visicom_data_api/LICENSE"), (ROOT / "LICENSE").read_bytes()
            )
            self.assertIn(b"MIT License", archive.read("visicom_data_api/LICENSE"))
            for name in archive.namelist():
                self.assertFalse(
                    any(
                        part in name
                        for part in ("__pycache__", ".env", "tests/", ".secret", ".git/")
                    )
                )

    def test_manifest_versions_match(self):
        metadata = configparser.ConfigParser()
        metadata.read(ROOT / "visicom_data_api/metadata.txt", encoding="utf-8")
        version = metadata["general"]["version"]
        entry = ET.parse(ROOT / "dist/plugins.xml").getroot().find("pyqgis_plugin")
        self.assertEqual(entry.attrib["version"], version)
        self.assertEqual(entry.findtext("version"), version)
        self.assertEqual(entry.findtext("file_name"), "visicom_data_api.zip")
