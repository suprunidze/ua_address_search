"""Run isolated source and installed-copy tests in available supported QGIS versions."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true", help="Also test synced profile copies")
    args = parser.parse_args()
    rows = []
    program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
    for directory in sorted(program_files.glob("QGIS *")):
        match = re.search(r"QGIS (\d+)\.(\d+)", directory.name)
        if not match or tuple(map(int, match.groups())) < (3, 28):
            continue
        launchers = list((directory / "bin").glob("python-qgis*.bat"))
        if not launchers:
            continue
        parents = [ROOT]
        if args.installed:
            generation = "QGIS4" if int(match[1]) >= 4 else "QGIS3"
            profiles = Path(os.environ["APPDATA"]) / "QGIS" / generation / "profiles"
            parents.extend(
                p / "python" / "plugins"
                for p in sorted(profiles.glob("*"))
                if (p / "python" / "plugins" / "ua_address_search").is_dir()
            )
        for parent in parents:
            env = dict(
                os.environ,
                QT_QPA_PLATFORM="offscreen",
                PYTHONDONTWRITEBYTECODE="1",
                PYTHONIOENCODING="utf-8",
            )
            try:
                result = subprocess.run(
                    [
                        str(launchers[0]),
                        "-B",
                        str(ROOT / "scripts/qgis_smoke.py"),
                        "--plugin-parent",
                        str(parent),
                    ],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=100,
                )
                output = result.stdout + result.stderr
                passed = result.returncode == 0 and "UAAS_SMOKE_OK" in output
            except subprocess.TimeoutExpired:
                output, passed = "QGIS smoke timed out", False
            kind = "source" if parent == ROOT else str(parent.parent.parent.name)
            print(f"{directory.name} [{kind}]: {'PASS' if passed else 'FAIL'}")
            if not passed:
                print(output[-8000:])
            rows.append(
                {
                    "qgis": directory.name,
                    "plugin_parent": str(parent),
                    "passed": passed,
                    "output": output,
                }
            )
    destination = ROOT / "artifacts" / "qgis_smoke.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if rows and all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
