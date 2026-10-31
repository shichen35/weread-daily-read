"""WeRead Web protocol helpers shared by login.py and report.py."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import requests

BASE_URL = "https://weread.qq.com"
RENEWAL_URL = f"{BASE_URL}/web/login/renewal"
READ_URL = f"{BASE_URL}/web/book/read"
CHAPTER_INFOS_URL = f"{BASE_URL}/web/book/chapterInfos"
SHELF_URL = f"{BASE_URL}/web/shelf/sync?synckey=0&teenmode=0&album=1&onlyBookid=0"
SHELF_PAGE_URL = f"{BASE_URL}/web/shelf"
AUTH_ERROR_CODES = {-2010, -2012, -2013}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0"
)
DEFAULT_READER_TOKEN = "3c5c8717f3daf09iop3423zafeqoi"
SECRET_NAME = "WEREAD_STATE"
_EXTRA_COOKIES = {"ptcz", "RK", "pgv_pvid"}


class AuthError(RuntimeError):
    """Credentials were rejected; a new QR login is required."""


def md5_hex(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def weread_e(value: Any) -> str:
    s = str(value)
    h = md5_hex(s)
    if re.fullmatch(r"\d+", s):
        chunks = [format(int(s[i : i + 9]), "x") for i in range(0, len(s), 9)]
        type_flag = "3"
    else:
        chunks = ["".join(format(ord(ch), "x") for ch in s)]
        type_flag = "4"
    result = h[:3] + type_flag + "2" + h[-2:]
    result += "g".join(format(len(c), "x").zfill(2) + c for c in chunks)
    if len(result) < 20:
        result += h[: 20 - len(result)]
    return result + md5_hex(result)[:3]


def weread_sign(query: str) -> str:
    a = b = 0x15051505
    length = len(query)
    i = length - 1
    while i > 0:
        a = (a ^ (ord(query[i]) << ((length - i) % 30))) & 0x7FFFFFFF
        b = (b ^ (ord(query[i - 1]) << (i % 30))) & 0x7FFFFFFF
        i -= 2
    return format(a + b, "x")


def sorted_query(params: dict[str, Any]) -> str:
    def js(value: Any) -> str:
        if value is True:
            return "true"
        if value is False:
            return "false"
        return str(value)

    return "&".join(
        f"{k}={quote(js(params[k]), safe='')}" for k in sorted(params) if k != "s"
    )


def web_app_id(user_agent: str = USER_AGENT) -> str:
    prefix = "".join(str(len(part) % 10) for part in user_agent.split(" ")[:12])
    value = 0
    for char in user_agent:
        value = (131 * value + ord(char)) & 0x7FFFFFFF
    return f"wb{prefix}h{value}"


def parse_books(value: str) -> list[str]:
    """Split WEREAD_BOOK ("id1,id2", one per line, or a single id/URL); dedupe in order."""
    seen: list[str] = []
    for item in re.split(r"[,，\s]+", value or ""):
        if item and item not in seen:
            seen.append(item)
    return seen


def reader_url(book_id: str) -> str:
    return f"{BASE_URL}/web/reader/{weread_e(book_id)}"


def _position_payload(ctx: dict[str, Any], now: int) -> dict[str, Any]:
    pc = ctx.get("pclts")
    if pc in (None, "", "0", 0):
        pc = weread_e(now)
    return {
        "appId": web_app_id(),
        "b": weread_e(ctx["book_id"]),
        "c": weread_e(ctx.get("chapter_uid") or 0),
        "ci": int(ctx.get("chapter_idx") or 0),
        "co": max(0, int(ctx.get("chapter_offset") or 0)),
        "sm": (ctx.get("summary") or "")[:20],
        "pr": max(0, min(100, int(float(ctx.get("progress") or 0)))),
        "ct": now,
        "ps": ctx.get("psvts") or "",
        "pc": pc,
    }


def enter_payload(ctx: dict[str, Any], now: Optional[int] = None) -> dict[str, Any]:
    params = _position_payload(ctx, int(now if now is not None else time.time()))
    params["s"] = weread_sign(sorted_query(params))
    return params


def read_payload(
    ctx: dict[str, Any],
    elapsed_seconds: int,
    now: Optional[int] = None,
    ts: Optional[int] = None,
    rn: Optional[int] = None,
) -> dict[str, Any]:
    now = int(now if now is not None else time.time())
    params = _position_payload(ctx, now)
    ts = ts if ts is not None else now * 1000 + random.randint(0, 999)
    rn = rn if rn is not None else random.randint(0, 999)
    token = ctx.get("token") or DEFAULT_READER_TOKEN
    params["rt"] = max(0, int(elapsed_seconds))
    params["ts"] = ts
    params["rn"] = rn
    params["sg"] = hashlib.sha256(f"{ts}{rn}{token}".encode("utf-8")).hexdigest()
    params["s"] = weread_sign(sorted_query(params))
    return params


def is_accepted(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    succ = body.get("succ")
    return succ is True or str(succ) == "1" or body.get("synckey") is not None


def is_counted(body: Any) -> bool:
    """A read report only adds reading time when the reply carries a synckey."""
    return isinstance(body, dict) and body.get("synckey") is not None


def merge_set_cookie(cookies: dict[str, str], set_cookie_lines: list[str]) -> dict[str, str]:
    for line in set_cookie_lines:
        name, _sep, value = line.split(";", 1)[0].strip().partition("=")
        if not (name.startswith("wr_") or name in _EXTRA_COOKIES):
            continue
        if value:
            cookies[name] = value
        else:
            cookies.pop(name, None)
    return cookies


def _set_cookie_lines(response: requests.Response) -> list[str]:
    lines: list[str] = []
    for r in [*response.history, response]:
        lines.extend(r.raw.headers.getlist("Set-Cookie") if r.raw is not None else [])
    return lines


def _mask(value: str) -> None:
    # Renewed credentials are not GitHub secrets yet, so hide them from logs explicitly.
    if value and os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{value}", flush=True)


class Account:
    """One WeRead Web session: cookie jar plus renewal headers."""

    def __init__(self, state: dict[str, Any]):
        self.state = {
            "cookies": dict(state.get("cookies") or {}),
            "wr_ticket": state.get("wr_ticket") or "",
            "wr_wrpa": state.get("wr_wrpa") or "",
            "updated_at": state.get("updated_at") or 0,
        }
        for value in self.cookies.values():
            _mask(value)

    @property
    def cookies(self) -> dict[str, str]:
        return self.state["cookies"]

    def cookie_header(self, cookies: Optional[dict[str, str]] = None) -> str:
        return "; ".join(f"{k}={v}" for k, v in sorted((cookies or self.cookies).items()))

    def request(
        self,
        method: str,
        url: str,
        *,
        referer: str = f"{BASE_URL}/",
        json_body: Any = None,
        headers: Optional[dict[str, str]] = None,
        accept: str = "application/json, text/plain, */*",
        timeout: float = 30,
        merge_cookies: bool = True,
    ) -> requests.Response:
        all_headers = {
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Referer": referer,
            "Cookie": self.cookie_header(),
        }
        if json_body is not None:
            all_headers["Origin"] = BASE_URL
            all_headers["Content-Type"] = "application/json;charset=UTF-8"
        all_headers.update(headers or {})
        data = None
        if json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        response = requests.request(
            method, url, headers=all_headers, data=data, timeout=timeout
        )
        if merge_cookies:
            self._merge(_set_cookie_lines(response))
        return response

    def _merge(self, lines: list[str]) -> None:
        before = dict(self.cookies)
        merge_set_cookie(self.cookies, lines)
        for name, value in self.cookies.items():
            if before.get(name) != value:
                _mask(value)

    def renew(self) -> None:
        # Merge cookies only after succ=1 so a failed renewal keeps the old credentials.
        response = self.request(
            "POST",
            RENEWAL_URL,
            json_body={"rq": "%2Fweb%2Fbook%2Fread", "ql": False},
            merge_cookies=False,
        )
        try:
            body = response.json()
        except ValueError:
            body = None
        if not response.ok or not isinstance(body, dict) or not (
            body.get("succ") is True or str(body.get("succ")) == "1"
        ):
            raise AuthError(f"renewal rejected: HTTP {response.status_code} {describe(body)}")
        self._merge(_set_cookie_lines(response))
        for key, header in (("wr_ticket", "x-wr-ticket"), ("wr_wrpa", "x-wrpa-0")):
            value = response.headers.get(header)
            if value:
                _mask(value)
                self.state[key] = value
        self.state["updated_at"] = int(time.time())

    def load_context(self, book: str) -> dict[str, Any]:
        """Open the Web reader page and capture the session values a report needs.

        `book` is a numeric bookId or a https://weread.qq.com/web/reader/... URL.
        """
        if book.startswith(f"{BASE_URL}/web/reader/"):
            url = book.split("?")[0]
        elif re.fullmatch(r"\w+", book):
            url = reader_url(book)
        else:
            raise ValueError("book must be a bookId or a weread.qq.com/web/reader/ URL")
        response = self.request(
            "GET",
            url,
            referer=url,
            accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        )
        response.raise_for_status()
        match = re.search(
            r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;\s*\(function",
            response.text,
            flags=re.S,
        )
        if not match:
            raise AuthError("reader page has no __INITIAL_STATE__ (logged out?)")
        reader = json.loads(match.group(1)).get("reader") or {}
        if not reader.get("psvts"):
            raise AuthError("reader page has no psvts (logged out?)")
        book_info = reader.get("bookInfo") or {}
        book_id = str(book_info.get("bookId") or "")
        if not book_id:
            raise RuntimeError("reader page has no bookId")
        chapter = reader.get("currentChapter") or {}
        progress = reader.get("progress") or {}
        progress = progress.get("book") if isinstance(progress.get("book"), dict) else progress
        chapter_uid = chapter.get("chapterUid") or progress.get("chapterUid")
        if chapter_uid is None:
            raise RuntimeError("reader page has no current chapter")
        # Report the server's own current position so the reading progress is never moved.
        return {
            "book_id": book_id,
            "title": book_info.get("title") or book_id,
            "chapter_uid": chapter_uid,
            "chapter_idx": chapter.get("chapterIdx") or progress.get("chapterIdx") or 0,
            "chapter_offset": chapter.get("chapterOffset") or progress.get("chapterOffset") or 0,
            "progress": progress.get("progress") or 0,
            "summary": progress.get("summary") or "",
            "psvts": reader.get("psvts"),
            "pclts": reader.get("pclts") or "",
            "token": reader.get("token") or "",
            "reader_url": url,
        }

    def sync_chapters(self, book_id: str, referer: str) -> None:
        """Fetch chapter infos, as the Web reader does; restores the synckey."""
        response = self.request(
            "POST", CHAPTER_INFOS_URL, referer=referer, json_body={"bookIds": [book_id]}
        )
        response.raise_for_status()

    def shelf(self) -> list[dict[str, str]]:
        """Books on the shelf that the Web reader can open, in shelf order."""
        response = self.request("GET", SHELF_URL, referer=SHELF_PAGE_URL)
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise RuntimeError(f"shelf failed: HTTP {response.status_code}")
        code = body.get("errCode", body.get("errcode"))
        if code is not None:
            error = AuthError if code in AUTH_ERROR_CODES else RuntimeError
            raise error(f"shelf failed: HTTP {response.status_code} {describe(body)}")
        books = []
        for book in body.get("books") or []:
            book_id = str(book.get("bookId") or "")
            # Articles and uploads have non-numeric ids and no Web reader page.
            if not book_id.isdigit():
                continue
            books.append({
                "book_id": book_id,
                "title": str(book.get("title") or book_id),
                "author": str(book.get("author") or ""),
            })
        return books

    def report(self, payload: dict[str, Any], referer: str) -> Any:
        response = self.request("POST", READ_URL, referer=referer, json_body=payload)
        try:
            return response.json()
        except ValueError:
            return {"http_status": response.status_code}


def describe(body: Any) -> str:
    """Describe a response without leaking values."""
    if isinstance(body, dict):
        keep = {k: body[k] for k in ("succ", "errCode", "errcode", "errMsg", "errmsg") if k in body}
        return json.dumps(keep, ensure_ascii=False) if keep else f"keys={sorted(body)}"
    return type(body).__name__



def load_state(path: Optional[Path]) -> dict[str, Any]:
    raw = path.read_text("utf-8") if path else os.environ.get(SECRET_NAME, "")
    if not raw.strip():
        raise AuthError(f"no state: set {SECRET_NAME} or pass --state-file")
    state = json.loads(raw)
    if not (state.get("cookies") or {}).get("wr_skey"):
        raise AuthError("state has no wr_skey; run login.py again")
    return state


def save_state(state: dict[str, Any], path: Optional[Path], push_secret: bool, repo: str = "") -> None:
    text = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    if path:
        path.write_text(text, "utf-8")
        path.chmod(0o600)
    if push_secret:
        cmd = ["gh", "secret", "set", SECRET_NAME]
        if repo:
            cmd += ["--repo", repo]
        subprocess.run(cmd, input=text, text=True, check=True, capture_output=True)
