"""Safely install the verified ZIP into existing QGIS3 and QGIS4 profiles."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "ua_address_search"


def running_qgis():
    if os.name != "nt":
        result = subprocess.run(["pgrep", "-f", "(^|/)qgis([ -]|$)"], capture_output=True)
        return result.returncode == 0
    result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Cannot determine whether QGIS is running")
    return any(
        line.lower().startswith(('"qgis-bin.exe"', '"qgis.exe"', '"qgis-ltr-bin.exe"'))
        for line in result.stdout.splitlines()
    )


def manifest(directory):
    return {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.rglob("*")
        if p.is_file()
    }


def sync(allow_running=False):
    if running_qgis() and not allow_running:
        raise RuntimeError("QGIS is running; close it or explicitly pass --allow-running-qgis")
    archive = ROOT / "dist" / (PLUGIN + ".zip")
    if not archive.exists():
        raise RuntimeError("Build the ZIP first")
    base = Path(os.environ.get("APPDATA", str(Path.home() / ".local/share"))) / "QGIS"
    targets = []
    with zipfile.ZipFile(archive) as release:
        for info in release.infolist():
            parts = Path(info.filename).parts
            if (
                not parts
                or parts[0] != PLUGIN
                or ".." in parts
                or Path(info.filename).is_absolute()
            ):
                raise RuntimeError("Unsafe ZIP entry")
        expected = {
            n.split("/", 1)[1]: hashlib.sha256(release.read(n)).hexdigest()
            for n in release.namelist()
        }
        for generation in ("QGIS3", "QGIS4"):
            profiles = base / generation / "profiles"
            if not profiles.is_dir():
                continue
            for profile in sorted(profiles.iterdir()):
                if not profile.is_dir() or profile.is_symlink():
                    continue
                parent = profile / "python" / "plugins"
                parent.mkdir(parents=True, exist_ok=True)
                target = parent / PLUGIN
                # Resolve the exact destination before any recursive deletion or replacement.
                if parent.resolve() != parent.absolute() or target.is_symlink():
                    raise RuntimeError("Refusing a redirected plugin destination")
                if target.resolve().parent != parent.resolve() or target.name != PLUGIN:
                    raise RuntimeError("Plugin destination escaped its profile")
                if target.exists() and any(p.is_symlink() for p in target.rglob("*")):
                    raise RuntimeError("Refusing a plugin tree containing symbolic links")
                with tempfile.TemporaryDirectory(prefix="uaas-stage-", dir=str(parent)) as staging:
                    release.extractall(staging)
                    staged = Path(staging) / PLUGIN
                    if manifest(staged) != expected:
                        raise RuntimeError("Staged plugin failed SHA-256 verification")
                    backup = Path(staging) / "previous"
                    if target.exists():
                        target.rename(backup)
                    try:
                        staged.rename(target)
                        if manifest(target) != expected:
                            raise RuntimeError("Installed plugin failed SHA-256 verification")
                    except BaseException:
                        if target.exists():
                            if target.resolve().parent != parent.resolve():
                                raise RuntimeError("Unsafe rollback destination")
                            shutil.rmtree(target)
                        if backup.exists():
                            backup.rename(target)
                        raise
                targets.append(str(target))
                print(f"Synchronized {generation}/{profile.name}; SHA-256 manifest verified")
    report = ROOT / "artifacts" / "profile_sync.json"
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps({"targets": targets}, indent=2), encoding="utf-8")
    if not targets:
        print("No existing QGIS3/QGIS4 profiles were found.")
    return targets


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-running-qgis", action="store_true")
    sync(parser.parse_args().allow_running_qgis)
