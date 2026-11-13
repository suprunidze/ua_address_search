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
    parser.add_argument(
        "--plugin-parent", type=Path, help="Test an extracted release instead of the source tree"
    )
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
        parents = [(args.plugin_parent or ROOT).resolve()]
        if args.installed:
            generation = "QGIS4" if int(match[1]) >= 4 else "QGIS3"
            profiles = Path(os.environ["APPDATA"]) / "QGIS" / generation / "profiles"
            parents.extend(
                p / "python" / "plugins"
                for p in sorted(profiles.glob("*"))
                if (p / "python" / "plugins" / "visicom_data_api").is_dir()
            )
        for parent in parents:
            env = dict(
                os.environ,
                QT_QPA_PLATFORM="offscreen",
                PYTHONDONTWRITEBYTECODE="1",
                PYTHONIOENCODING="utf-8",
            )
            destination = ROOT / "artifacts"
            destination.mkdir(exist_ok=True)
            log_path = destination / ("qgis-run-" + str(len(rows)) + ".log")
            try:
                command = [
                    str(launchers[0]),
                    "-B",
                    str(ROOT / "scripts/qgis_smoke.py"),
                    "--plugin-parent",
                    str(parent),
                ]
                with log_path.open("w", encoding="utf-8") as log:
                    process = subprocess.Popen(
                        command,
                        cwd=ROOT,
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                    try:
                        returncode = process.wait(timeout=100)
                    except subprocess.TimeoutExpired:
                        # A .bat launcher has a Python child; killing the launcher alone
                        # leaves its child alive and can hang communicate() on Windows.
                        if os.name == "nt":
                            subprocess.run(
                                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                capture_output=True,
                                check=False,
                            )
                        if process.poll() is None:
                            process.kill()
                        process.wait(timeout=5)
                        raise
                output = log_path.read_text(encoding="utf-8", errors="replace")
                passed = returncode == 0 and "UAAS_SMOKE_OK" in output
            except subprocess.TimeoutExpired:
                output = "QGIS smoke timed out\n" + log_path.read_text(
                    encoding="utf-8", errors="replace"
                )
                passed = False
            kind = (
                "source"
                if parent == ROOT
                else "package"
                if args.plugin_parent and parent == args.plugin_parent.resolve()
                else str(parent.parent.parent.name)
            )
            print(f"{directory.name} [{kind}]: {'PASS' if passed else 'FAIL'}", flush=True)
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
