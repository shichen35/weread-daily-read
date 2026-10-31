#!/usr/bin/env python3
"""Report WeRead reading time every --interval seconds for --minutes.

Mirrors the KOReader plugin's reporting pipeline: open the Web reader once,
send an enter report, then periodic reports with rt=interval. On rejection it
refreshes the reader context and retries, then renews the cookie (at most once
per 10 minutes) and retries. Every successful renewal is saved immediately, to
the WEREAD_STATE secret in GitHub Actions, so a killed job never loses it.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import requests

from weread import (
    Account,
    AuthError,
    describe,
    enter_payload,
    is_accepted,
    load_state,
    read_payload,
    save_state,
)

CONTEXT_TTL_SECONDS = 15 * 60
RENEW_EVERY_SECONDS = 20 * 60
RENEW_COOLDOWN_SECONDS = 10 * 60
MAX_CONSECUTIVE_FAILURES = 3
NEVER = float("-inf")


def log(*parts: object) -> None:
    print(datetime.now().strftime("%H:%M:%S"), *parts, flush=True)


class Reporter:
    def __init__(
        self,
        account: Account,
        book: str,
        interval: int,
        persist: Callable[[], None],
        clock: Callable[[], float] = time.monotonic,
    ):
        self.account = account
        self.book = book
        self.interval = interval
        self.persist = persist
        self.clock = clock
        self.ctx: dict = {}
        self.ctx_at = NEVER
        self.renewed_at = NEVER
        self.reported_seconds = 0

    def renew(self) -> None:
        self.account.renew()
        self.renewed_at = self.clock()
        self.persist()
        log("cookie renewed and saved")

    def open(self) -> None:
        self.ctx = self.account.load_context(self.book)
        self.ctx_at = self.clock()
        body = self.account.report(enter_payload(self.ctx), self.ctx["reader_url"])
        log(f"opened reader: {self.ctx['title']} ({self.ctx['progress']}%) enter={describe(body)}")

    def _send(self) -> bool:
        body = self.account.report(read_payload(self.ctx, self.interval), self.ctx["reader_url"])
        if is_accepted(body):
            self.reported_seconds += self.interval
            return True
        log("report rejected:", describe(body))
        return False

    def tick(self) -> bool:
        if self.clock() - self.renewed_at >= RENEW_EVERY_SECONDS:
            self.renew()
        if self.clock() - self.ctx_at >= CONTEXT_TTL_SECONDS:
            self.open()
        if self._send():
            return True
        self.open()
        if self._send():
            return True
        if self.clock() - self.renewed_at >= RENEW_COOLDOWN_SECONDS:
            self.renew()
            self.open()
            return self._send()
        return False


def run(
    reporter: Reporter,
    minutes: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    reporter.renew()
    reporter.open()
    deadline = clock() + minutes * 60
    failures = 0
    # Only report full intervals: rt claims `interval` seconds each time.
    while clock() + reporter.interval <= deadline:
        sleep(reporter.interval)
        try:
            ok = reporter.tick()
        except AuthError:
            raise
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            log("report error:", type(exc).__name__, str(exc)[:200])
            ok = False
        failures = 0 if ok else failures + 1
        if failures >= MAX_CONSECUTIVE_FAILURES:
            log(f"stopping after {failures} consecutive failures")
            break
    log(f"done: reported {reporter.reported_seconds // 60} min {reporter.reported_seconds % 60} s")
    return 0 if failures < MAX_CONSECUTIVE_FAILURES else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=os.environ.get("WEREAD_BOOK", ""),
                        help="bookId or weread.qq.com/web/reader/ URL (env WEREAD_BOOK)")
    parser.add_argument("--minutes", type=float, default=float(os.environ.get("READ_MINUTES") or 60))
    parser.add_argument("--interval", type=int, default=30, help="seconds between reports (min 10)")
    parser.add_argument("--state-file", type=Path, help="read/write state here instead of env WEREAD_STATE")
    args = parser.parse_args()
    if not args.book:
        parser.error("--book or WEREAD_BOOK is required")
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"

    account = Account(load_state(args.state_file))
    reporter = Reporter(
        account,
        args.book.strip(),
        max(10, args.interval),
        lambda: save_state(
            account.state, args.state_file, in_actions, os.environ.get("GITHUB_REPOSITORY", "")
        ),
    )
    return run(reporter, args.minutes)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuthError as exc:
        print(f"Authentication failed: {exc}\nRun `python login.py --push` to log in again.", file=sys.stderr)
        raise SystemExit(2)
    except subprocess.CalledProcessError as exc:
        # Losing renewed credentials breaks tomorrow's run, so this is fatal.
        print(f"Saving state to the secret failed: {exc.stderr}", file=sys.stderr)
        raise SystemExit(3)
