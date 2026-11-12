"""Explicit bounded live Visicom probe; never prints credentials or provider URLs."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_env(path):
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


async def check(limit):
    import httpx
    from server.app.visicom import ProviderError, Visicom

    key = os.environ.get("VISICOM_DATA_API_KEY", "")
    if not key:
        raise SystemExit("VISICOM_DATA_API_KEY is not configured")
    async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
        provider = Visicom(key, client)
        operations = [
            ("forward", lambda: provider.search("Київ, Хрещатик, 26")),
            ("reverse", lambda: provider.reverse(30.51043, 50.45358)),
        ]
        first = None
        for name, operation in operations[:limit]:
            try:
                records = await operation()
                if name == "forward" and records:
                    first = records[0]["id"]
                print(json.dumps({"probe": name, "results": len(records), "valid": True}))
            except ProviderError as exc:
                print(json.dumps({"probe": name, "error": exc.code}))
                return 1
        if limit == 3 and first:
            try:
                records = await provider.feature(first)
                valid = bool(records and records[0].get("geometry"))
                print(json.dumps({"probe": "feature", "valid": valid}))
                if not valid:
                    return 1
            except ProviderError as exc:
                print(json.dumps({"probe": "feature", "error": exc.code}))
                return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-requests", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.env_file:
        load_env(args.env_file)
    sys.exit(asyncio.run(check(args.max_requests)))
