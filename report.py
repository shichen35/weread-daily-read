#!/usr/bin/env python3
"""Report WeRead reading time every --interval seconds for --minutes.

--minutes takes a fixed value ("60"), a range ("45-75") or a value with a
random +/- error ("60±10", also "60+-10" or "60+/-10"), picked at random
each run. With --window W the run waits a random delay first so the reading
session lands somewhere inside the next W minutes instead of always starting
at the scheduled time.

Mirrors the KOReader plugin's reporting pipeline: open the Web reader once,
send an enter report, then periodic reports with rt=interval. On rejection it
refreshes the reader context and retries, then renews the cookie (at most once
per 10 minutes) and retries. Every successful renewal is saved immediately, to
the WEREAD_STATE secret in GitHub Actions, so a killed job never loses it.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
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
# GitHub kills jobs after 360 min; keep room for setup and the last reports.
MAX_WINDOW_MINUTES = 340
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


_NUM = r"\s*(\d+(?:\.\d+)?)\s*"


def parse_minutes(spec: str) -> tuple[float, float]:
    """Parse "60", a range "45-75" or a jitter "60±10" / "60+-10" / "60+/-10"
    into (low, high) minutes."""
    spec = spec or ""
    if match := re.fullmatch(_NUM + r"(?:±|\+/?-)" + _NUM, spec):
        base, jitter = float(match.group(1)), float(match.group(2))
        return max(base - jitter, 0.0), base + jitter
    if match := re.fullmatch(_NUM + r"(?:-" + _NUM + r")?", spec):
        low = float(match.group(1))
        high = float(match.group(2) or low)
        return min(low, high), max(low, high)
    raise ValueError(
        f"invalid minutes {spec!r}: use 60, a range like 45-75 or a jitter like 60±10"
    )


def plan(
    minutes_spec: str, window: float, rng: random.Random | None = None
) -> tuple[float, float]:
    """Pick (delay_seconds, minutes) so the session ends within `window` minutes."""
    rng = rng or random.Random()
    low, high = parse_minutes(minutes_spec)
    window = min(max(window, 0.0), MAX_WINDOW_MINUTES)
    if window:
        high = min(high, window)
        low = min(low, high)
    minutes = rng.uniform(low, high)
    slack = max(window - minutes, 0.0)
    return rng.uniform(0, slack) * 60, minutes


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
    parser.add_argument("--minutes", default=os.environ.get("READ_MINUTES") or "60",
                        help='minutes to report: 60, a random range 45-75 or 60±10 (env READ_MINUTES)')
    parser.add_argument("--window", type=float, default=float(os.environ.get("READ_WINDOW") or 0),
                        help="start at a random time so reading ends within this many minutes "
                             f"(max {MAX_WINDOW_MINUTES}, 0 = start now; env READ_WINDOW)")
    parser.add_argument("--interval", type=int, default=30, help="seconds between reports (min 10)")
    parser.add_argument("--state-file", type=Path, help="read/write state here instead of env WEREAD_STATE")
    args = parser.parse_args()
    if not args.book:
        parser.error("--book or WEREAD_BOOK is required")
    try:
        delay, minutes = plan(args.minutes, args.window)
    except ValueError as exc:
        parser.error(str(exc))
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
    start = datetime.now() + timedelta(seconds=delay)
    log(f"plan: wait {delay / 60:.1f} min, start {start:%H:%M:%S}, report {minutes:.1f} min")
    time.sleep(delay)
    return run(reporter, minutes)


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
