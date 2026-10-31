#!/usr/bin/env python3
"""One-time WeRead QR login for this repo.

Generates a fresh wr_fp so this runner is a separate device from your phone or
browser (sharing one wr_fp makes the sessions replace each other), logs in by
QR, verifies the session with one renewal, then saves the state locally and/or
into the WEREAD_STATE repository secret. Credentials are never printed.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from weread import BASE_URL, Account, AuthError, describe, save_state

SKILLS_PAGE_URL = f"{BASE_URL}/r/weread-skills"
LOGIN_UID_URL = f"{BASE_URL}/api/auth/getLoginUid"
LOGIN_INFO_URL = f"{BASE_URL}/api/auth/getLoginInfo"
USER_INFO_URL = f"{BASE_URL}/api/userInfo"


def get_json(account: Account, url: str, *, timeout: float = 20, headers: dict[str, str] | None = None) -> dict[str, Any]:
    response = account.request("GET", url, referer=SKILLS_PAGE_URL, timeout=timeout, headers=headers)
    try:
        data = response.json()
    except ValueError:
        data = None
    if not response.ok or not isinstance(data, dict):
        raise AuthError(f"{url.split('?')[0]} failed: HTTP {response.status_code} {describe(data)}")
    return data


def show_qr(url: str) -> None:
    try:
        import qrcode
    except ImportError:
        print("Install `qrcode` to render the QR code here; or open this URL as a QR code:")
        print(url)
        return
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.print_ascii(invert=True)
    print("Scan with WeChat/WeRead and confirm on your phone.")


def wait_for_login(account: Account, uid: str) -> dict[str, Any]:
    otp = ""
    while True:
        url = f"{LOGIN_INFO_URL}?uid={quote(uid, safe='')}&otp={quote(otp, safe='')}"
        try:
            result = get_json(account, url, timeout=70)
        except requests.Timeout:
            continue
        if result.get("succeed") is True:
            return result
        code = str(result.get("logicCode") or "")
        if code in ("NEED_OTP", "OTP_NOT_MATCH"):
            if code == "OTP_NOT_MATCH":
                print("Verification code did not match.")
            otp = input("Enter the four-digit code shown on your phone: ").strip()
            continue
        raise AuthError(f"login stopped: {code or describe(result)}")


def login() -> Account:
    account = Account({"cookies": {"wr_fp": str(secrets.randbits(32))}})
    page = account.request(
        "GET", SKILLS_PAGE_URL, accept="text/html,application/xhtml+xml,*/*;q=0.8"
    )
    page.raise_for_status()
    uid = get_json(account, LOGIN_UID_URL).get("uid")
    if not isinstance(uid, str) or not uid:
        raise AuthError("WeRead did not return a login uid")
    show_qr(f"{BASE_URL}/web/confirm?uid={quote(uid, safe='')}")

    result = wait_for_login(account, uid)
    vid = str(result.get("webLoginVid") or "")
    access_token = str(result.get("accessToken") or "")
    refresh_token = str(result.get("refreshToken") or "")
    if not vid or not access_token:
        raise AuthError("login response is missing credentials")
    account.cookies.update({"wr_vid": vid, "wr_skey": access_token, "wr_ql": "0"})
    if refresh_token:
        account.cookies["wr_rt"] = quote(refresh_token, safe="")

    user = get_json(
        account,
        f"{USER_INFO_URL}?userVid={quote(vid, safe='')}",
        headers={"X-Vid": vid, "X-Skey": access_token},
    )
    print(f"Logged in. Account name present: {bool(user.get('name'))}")
    account.renew()
    print("Renewal verified.")
    return account


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="write state JSON here (mode 600)")
    parser.add_argument("--push", action="store_true", help="store state in the WEREAD_STATE secret via gh")
    parser.add_argument("--repo", default="", help="owner/name for --push (default: current repo)")
    args = parser.parse_args()
    if not args.out and not args.push:
        parser.error("pass --out and/or --push")

    account = login()
    save_state(account.state, args.out, args.push, args.repo)
    print("State saved" + (f" to {args.out}" if args.out else "") + (" and pushed to secret" if args.push else "") + ".")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (AuthError, requests.RequestException) as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
