"""Local server entry point with optional .env file, without printing secrets."""

import argparse
import os
from pathlib import Path
import uvicorn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.env_file:
        for line in args.env_file.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    uvicorn.run(
        "server.app.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        workers=1,
        access_log=False,
    )


if __name__ == "__main__":
    main()
