#!/usr/bin/env python3
"""Choose which books the daily run reports, straight from your WeRead shelf.

Runs locally. It starts the `shelf` workflow, which reads the shelf in GitHub
Actions with the WEREAD_STATE secret (no local login needed), downloads the
result, lets you tick books with the arrow keys, and saves the choice to the
WEREAD_BOOK variable. Each daily run then picks one of them at random.

Needs the GitHub CLI (`gh auth login`) and `pip install -r requirements-local.txt`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from weread import parse_books

WORKFLOW = "shelf.yml"
VARIABLE = "WEREAD_BOOK"


def gh(args: list[str], repo: str = "", capture: bool = True) -> str:
    cmd = ["gh", *args] + (["--repo", repo] if repo else [])
    result = subprocess.run(cmd, capture_output=capture, text=True, check=True)
    return result.stdout if capture else ""


def find_run(runs: list[dict[str, Any]], since: datetime) -> int | None:
    """The newest run created at or after `since` (gh reports createdAt in UTC)."""
    for run in sorted(runs, key=lambda r: r["createdAt"], reverse=True):
        created = datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))
        if created >= since:
            return int(run["databaseId"])
    return None


def fetch_shelf(repo: str) -> list[dict[str, str]]:
    # Allow some clock skew between this machine and GitHub.
    since = datetime.now(timezone.utc) - timedelta(seconds=30)
    gh(["workflow", "run", WORKFLOW], repo)
    print("Started the shelf workflow; waiting for it to appear...")
    run_id = None
    for _ in range(40):
        time.sleep(3)
        runs = json.loads(gh(["run", "list", "--workflow", WORKFLOW, "--event", "workflow_dispatch",
                              "--limit", "5", "--json", "databaseId,createdAt"], repo))
        run_id = find_run(runs, since)
        if run_id:
            break
    if not run_id:
        raise RuntimeError("the shelf run did not show up; check the Actions page")
    print(f"Waiting for run {run_id} (it queues behind daily-read if that is running)...")
    try:
        gh(["run", "watch", str(run_id), "--exit-status", "--interval", "5"], repo, capture=False)
    except subprocess.CalledProcessError:
        raise RuntimeError(f"the shelf run failed; see `gh run view {run_id} --log-failed`")
    with tempfile.TemporaryDirectory() as tmp:
        gh(["run", "download", str(run_id), "--name", "shelf", "--dir", tmp], repo)
        return json.loads((Path(tmp) / "shelf.json").read_text("utf-8"))


def current_books(repo: str) -> list[str]:
    try:
        return parse_books(gh(["variable", "get", VARIABLE], repo))
    except subprocess.CalledProcessError:
        return []


def build_choices(books: list[dict[str, str]], current: list[str]) -> list[tuple[str, str, bool]]:
    """(label, value, checked) per book; current picks that are not on the shelf are kept at the end."""
    choices = []
    for book in books:
        label = book["title"] + (f" — {book['author']}" if book.get("author") else "")
        choices.append((label, book["book_id"], book["book_id"] in current))
    on_shelf = {book["book_id"] for book in books}
    choices += [(f"{item} (not on shelf)", item, True) for item in current if item not in on_shelf]
    return choices


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default="", help="owner/name (default: the current repo)")
    parser.add_argument("--dry-run", action="store_true", help="print the new value without saving it")
    args = parser.parse_args()
    try:
        import questionary
    except ImportError:
        parser.error("questionary is missing: pip install -r requirements-local.txt")

    books = fetch_shelf(args.repo)
    if not books:
        print("No books on the shelf that the Web reader can open.")
        return 1
    choices = build_choices(books, current_books(args.repo))
    picked = questionary.checkbox(
        "Books to report (one is picked at random each day):",
        choices=[questionary.Choice(label, value=value, checked=checked) for label, value, checked in choices],
        instruction="(↑↓ move, space toggle, enter confirm)",
    ).ask()
    if not picked:
        print("Nothing selected; WEREAD_BOOK unchanged.")
        return 0

    labels = {value: label for label, value, _ in choices}
    print("\n".join(f"  - {labels[value]}" for value in picked))
    value = ",".join(picked)
    if args.dry_run:
        print(f"{VARIABLE}={value}")
        return 0
    if not questionary.confirm(f"Save these {len(picked)} books to {VARIABLE}?", default=True).ask():
        print("Cancelled; WEREAD_BOOK unchanged.")
        return 0
    gh(["variable", "set", VARIABLE, "--body", value], args.repo)
    print(f"Saved {len(picked)} books to {VARIABLE}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except subprocess.CalledProcessError as exc:
        print(f"gh failed: {' '.join(exc.cmd[:3])} ... {exc.stderr or ''}".strip(), file=sys.stderr)
        raise SystemExit(3)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
