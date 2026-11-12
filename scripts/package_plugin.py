"""Build and verify the real distributable ZIP and local repository manifest."""

import argparse
import configparser
import hashlib
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "ua_address_search"


def version():
    metadata = configparser.ConfigParser()
    metadata.read(ROOT / PLUGIN / "metadata.txt", encoding="utf-8")
    return metadata["general"]["version"]


def runtime_files():
    candidates = sorted(p for p in (ROOT / PLUGIN).rglob("*") if p.is_file())
    names = [p.relative_to(ROOT).as_posix() for p in candidates]
    ignored = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=ROOT,
        input="\n".join(names),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if ignored.returncode not in (0, 1):
        raise RuntimeError("Cannot evaluate .gitignore")
    excluded = set(ignored.stdout.splitlines())
    files = []
    for path, name in zip(candidates, names):
        if path.is_symlink():
            raise RuntimeError("Symlinks are not allowed in a release")
        if name in excluded or "__pycache__" in path.parts:
            continue
        if path.name == "LICENSE" or path.suffix in (".py", ".txt", ".svg", ".png", ".qm"):
            files.append(path)
    return files


def build():
    if (ROOT / "LICENSE").read_bytes() != (ROOT / PLUGIN / "LICENSE").read_bytes():
        raise RuntimeError("MIT license copies differ")
    destination = ROOT / "dist"
    destination.mkdir(exist_ok=True)
    archive = destination / (PLUGIN + ".zip")
    files = runtime_files()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            output.writestr(info, path.read_bytes())
    with zipfile.ZipFile(archive) as release:
        if release.testzip() is not None or len(release.namelist()) != len(files):
            raise RuntimeError("ZIP integrity failure")
        for path in files:
            if release.read(path.relative_to(ROOT).as_posix()) != path.read_bytes():
                raise RuntimeError("ZIP content differs from source")
        if release.read(PLUGIN + "/LICENSE") != (ROOT / "LICENSE").read_bytes():
            raise RuntimeError("ZIP is missing the MIT license")
        secret_file = ROOT / ".env"
        if secret_file.exists():
            for line in secret_file.read_text(encoding="utf-8-sig").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    secret = line.split("=", 1)[1].strip().strip("\"'")
                    if len(secret) >= 12 and any(
                        secret.encode() in release.read(n) for n in release.namelist()
                    ):
                        raise RuntimeError("A local secret was found in the ZIP")
    root = ET.Element("plugins")
    entry = ET.SubElement(root, "pyqgis_plugin", name="UA Address Search", version=version())
    for key, value in {
        "description": "Пошук адрес України через Visicom",
        "version": version(),
        "qgis_minimum_version": "3.28",
        "qgis_maximum_version": "4.99",
        "author_name": "UA Address Search contributors",
        "experimental": "True",
        "file_name": archive.name,
        "download_url": archive.name,
    }.items():
        ET.SubElement(entry, key).text = value
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(destination / "plugins.xml", encoding="utf-8", xml_declaration=True)
    print(
        f"Built {archive.name} v{version()}, files={len(files)}, "
        f"sha256={hashlib.sha256(archive.read_bytes()).hexdigest()}"
    )
    return archive


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    build()
