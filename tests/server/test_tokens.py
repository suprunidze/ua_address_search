import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from server.app.storage import AccessError, Store

ROOT = Path(__file__).resolve().parents[2]


class TokenCLITests(unittest.TestCase):
    def test_issue_and_revoke_without_printing_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "test.sqlite")
            output = Path(directory) / "pilot.secret"
            env = dict(os.environ, UAAS_DB_PATH=database)
            command = [sys.executable, "-B", "-m", "server.app.tokens"]
            issued = subprocess.run(
                command + ["issue", "--label", "pilot", "--output", str(output)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            token = output.read_text(encoding="utf-8").strip()
            self.assertNotIn(token, issued.stdout + issued.stderr)
            store = Store(database)
            store.authenticate(token)
            duplicate = subprocess.run(
                command + ["issue", "--label", "pilot", "--output", str(output)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(duplicate.returncode, 0)
            subprocess.run(
                command + ["revoke", "--label", "pilot"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                check=True,
            )
            with self.assertRaises(AccessError):
                store.authenticate(token)
