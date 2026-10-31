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
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from weread import parse_books, reader_url

WORKFLOW = "shelf.yml"
VARIABLE = "WEREAD_BOOK"


def gh(args: list[str], repo: str = "", capture: bool = True) -> str:
    cmd = ["gh", *args] + (["--repo", repo] if repo else [])
    result = subprocess.run(cmd, capture_output=capture, text=True, check=True)
    return result.stdout if capture else ""


def gh_api(path: str, repo: str = "", jq: str = "") -> str:
    """`gh api` takes no --repo; GH_REPO fills {owner}/{repo} instead."""
    env = dict(os.environ, GH_REPO=repo) if repo else None
    cmd = ["gh", "api", path] + (["--jq", jq] if jq else [])
    return subprocess.run(cmd, capture_output=True, text=True, check=True, env=env).stdout


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
    """The saved WEREAD_BOOK, read over REST so it works with any gh version."""
    try:
        value = gh_api(f"repos/{{owner}}/{{repo}}/actions/variables/{VARIABLE}", repo, jq=".value")
    except subprocess.CalledProcessError as exc:
        if "404" in (exc.stderr or ""):
            print(f"{VARIABLE} is not set yet; nothing pre-ticked.")
        else:
            print(f"Warning: could not read {VARIABLE}, nothing pre-ticked: {(exc.stderr or '').strip()}")
        return []
    books = parse_books(value)
    print(f"Currently selected: {len(books)} books (from {VARIABLE}).")
    return books


def build_choices(
    books: list[dict[str, str]], current: list[str]
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Split the shelf into (picked, others) as (label, value) pairs.

    Picked books come in WEREAD_BOOK order; a saved reader URL counts as the
    book it opens. Saved entries that are not on the shelf stay picked.
    """
    by_key: dict[str, dict[str, str]] = {}
    for book in books:
        by_key[book["book_id"]] = book
        by_key[reader_url(book["book_id"])] = book

    def label(book: dict[str, str]) -> str:
        return book["title"] + (f" — {book['author']}" if book.get("author") else "")

    picked: list[tuple[str, str]] = []
    for item in current:
        book = by_key.get(item.split("?")[0])
        choice = (label(book), book["book_id"]) if book else (f"{item} (not on shelf)", item)
        if choice not in picked:
            picked.append(choice)
    picked_ids = {value for _, value in picked}
    others = [(label(book), book["book_id"]) for book in books if book["book_id"] not in picked_ids]
    return picked, others


def step_index(valid: list[bool], current: int, delta: int) -> int:
    """Move by delta without wrapping, landing on a selectable row (not a separator)."""
    if not any(valid):
        return current
    last = len(valid) - 1
    target = min(max(current + delta, 0), last)
    forward = 1 if delta >= 0 else -1
    for step in (forward, -forward):
        i = target
        while 0 <= i <= last:
            if valid[i]:
                return i
            i += step
    return current


def add_paging(question: Any) -> Any:
    """Add PgUp/PgDn/Home/End to a questionary checkbox."""
    from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
    from questionary import Separator
    from questionary.prompts.common import InquirerControl

    app = question.application
    control = next(c for c in app.layout.find_all_controls() if isinstance(c, InquirerControl))
    window = next((w for w in app.layout.find_all_windows() if w.content is control), None)

    def page() -> int:
        info = window.render_info if window else None
        height = info.window_height if info else shutil.get_terminal_size().lines - 4
        return max(1, height - 1)

    def move(delta: int) -> None:
        rows = control.filtered_choices
        valid = [not isinstance(c, Separator) and not c.disabled for c in rows]
        control.pointed_at = step_index(valid, control.pointed_at, delta)

    keys = KeyBindings()
    keys.add("pagedown", eager=True)(lambda _: move(page()))
    keys.add("pageup", eager=True)(lambda _: move(-page()))
    keys.add("end", eager=True)(lambda _: move(len(control.filtered_choices)))
    keys.add("home", eager=True)(lambda _: move(-len(control.filtered_choices)))
    app.key_bindings = merge_key_bindings([app.key_bindings, keys])
    return question


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
    current, others = build_choices(books, current_books(args.repo))
    choices = [questionary.Choice(label, value=value, checked=True) for label, value in current]
    if current and others:
        choices.append(questionary.Separator("── shelf ──"))
    choices += [questionary.Choice(label, value=value) for label, value in others]
    picked = add_paging(questionary.checkbox(
        "Books to report (one is picked at random each day):",
        choices=choices,
        instruction="(↑↓ move, PgUp/PgDn page, Home/End, space toggle, enter confirm)",
    )).ask()
    if not picked:
        print("Nothing selected; WEREAD_BOOK unchanged.")
        return 0

    labels = {value: label for label, value in current + others}
    print("\n".join(f"  - {labels[value]}" for value in picked))
    value = ",".join(picked)
    if args.dry_run:
        print(f"{VARIABLE}={value}")
        print("Dry run: nothing saved, so this selection won't be pre-ticked next time.")
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
