#!/usr/bin/env python3
"""Export the WeRead shelf to JSON for books.py.

Runs in GitHub Actions with the WEREAD_STATE secret: renews the cookie,
saves it back (like report.py), then writes the books the Web reader can
open to --out.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from weread import Account, AuthError, load_state, save_state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("shelf.json"))
    parser.add_argument("--state-file", type=Path, help="read/write state here instead of env WEREAD_STATE")
    args = parser.parse_args()
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"

    account = Account(load_state(args.state_file))
    account.renew()
    save_state(account.state, args.state_file, in_actions, os.environ.get("GITHUB_REPOSITORY", ""))
    print("cookie renewed and saved", flush=True)
    books = account.shelf()
    args.out.write_text(json.dumps(books, ensure_ascii=False, indent=1), "utf-8")
    print(f"shelf: {len(books)} books written to {args.out}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuthError as exc:
        print(f"Authentication failed: {exc}\nRun `python login.py --push` to log in again.", file=sys.stderr)
        raise SystemExit(2)
    except subprocess.CalledProcessError as exc:
        print(f"Saving state to the secret failed: {exc.stderr}", file=sys.stderr)
        raise SystemExit(3)
