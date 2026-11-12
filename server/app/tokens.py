"""Issue tokens into a private file; never print the secret to logs."""

import argparse
import os
from pathlib import Path
from .config import Config
from .storage import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("issue")
    issue.add_argument("--label", required=True)
    issue.add_argument("--output", type=Path, required=True)
    revoke = sub.add_parser("revoke")
    revoke.add_argument("--label", required=True)
    args = parser.parse_args()
    config = Config.from_env()
    store = Store(config.db_path, config.global_daily, config.token_daily, config.token_minute)
    if args.command == "issue":
        if args.output.suffix != ".secret":
            parser.error("Token output must have the ignored .secret suffix")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(str(args.output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(store.issue(args.label) + "\n")
        print("Token saved to the requested private file; protect its filesystem permissions.")
    else:
        print(f"Revoked tokens: {store.revoke(args.label)}")


if __name__ == "__main__":
    main()
