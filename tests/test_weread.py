import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import report  # noqa: E402
import weread  # noqa: E402
from weread import AuthError, sorted_query  # noqa: E402

# Expected values were produced by weread.koplugin's weread/lib/protocol.lua.
CTX = {
    "book_id": "43208843",
    "chapter_uid": 12,
    "chapter_idx": 3,
    "chapter_offset": 150,
    "psvts": "PSV",
}


class ProtocolTest(unittest.TestCase):
    def test_e_matches_lua(self):
        self.assertEqual(weread.weread_e("43208843"), "c9c321c07293508bc9c79df")
        self.assertEqual(weread.weread_e(0), "cfc32da010cfcd208495488")
        self.assertEqual(weread.weread_e("MP_WXS_123"), "40642b4144d505f5758535f313233d40")
        self.assertEqual(weread.weread_e(1793000000), "769327707aafe6a0g010479")

    def test_app_id_matches_doc(self):
        self.assertEqual(weread.web_app_id(), "wb115321887466h529830856")

    def test_enter_payload_matches_lua(self):
        ctx = dict(CTX, progress=42.7, summary="一二三四五六七八九十" * 2 + "多余", pclts="")
        p = weread.enter_payload(ctx, now=1793000000)
        self.assertEqual(p["s"], "7415af64")
        self.assertEqual(p["pr"], 42)
        self.assertEqual(p["pc"], "769327707aafe6a0g010479")
        self.assertNotIn("rt", p)

    def test_read_payload_matches_lua(self):
        ctx = dict(CTX, progress=42, summary="abc", pclts="PCL", token="TOK")
        p = weread.read_payload(ctx, 30, now=1793000000, ts=1793000000123, rn=7)
        self.assertEqual(
            sorted_query(p),
            "appId=wb115321887466h529830856&b=c9c321c07293508bc9c79df&c=c20321001cc20ad4d76f5ae"
            "&ci=3&co=150&ct=1793000000&pc=PCL&pr=42&ps=PSV&rn=7&rt=30"
            "&sg=1df7d73a0e01a53b670cd30c860b29c4b04b98a7eea2306e8638565bd1c1f7a8&sm=abc&ts=1793000000123",
        )
        self.assertEqual(p["s"], "7cb17475")

    def test_is_accepted(self):
        self.assertTrue(weread.is_accepted({"succ": 1}))
        self.assertTrue(weread.is_accepted({"synckey": 0}))
        self.assertFalse(weread.is_accepted({"errCode": -2012}))
        self.assertFalse(weread.is_accepted(None))

    def test_merge_set_cookie_keeps_one_value_per_name_and_drops_empty(self):
        cookies = {"wr_skey": "old", "wr_ql": "0", "other": "x"}
        weread.merge_set_cookie(cookies, [
            "wr_skey=new; Domain=.weread.qq.com; Path=/",
            "wr_ql=; Expires=Thu, 01 Jan 1970 00:00:00 GMT",
            "tracking=1; Path=/",
        ])
        self.assertEqual(cookies, {"wr_skey": "new", "other": "x"})


class FakeResponse:
    def __init__(self, status=200, body=None, text="", set_cookie=(), headers=None):
        self.status_code = status
        self.ok = status < 400
        self._body = body
        self.text = text
        self.headers = headers or {}
        self.history = []
        self.raw = mock.Mock()
        self.raw.headers.getlist.return_value = list(set_cookie)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise weread.requests.HTTPError(str(self.status_code))


READER_HTML = "<script>window.__INITIAL_STATE__=" + json.dumps({
    "reader": {
        "psvts": "PSV", "pclts": "", "token": "TOK",
        "bookInfo": {"bookId": "43208843", "title": "T"},
        "currentChapter": {"chapterUid": 12, "chapterIdx": 3},
        "progress": {"book": {"progress": 55, "chapterOffset": 99, "summary": "s"}},
    }
}) + ";(function(){})</script>"


class AccountTest(unittest.TestCase):
    def account(self):
        return weread.Account({"cookies": {"wr_skey": "old", "wr_vid": "1", "wr_fp": "9"}})

    def test_failed_renewal_keeps_credentials(self):
        account = self.account()
        resp = FakeResponse(body={"errCode": -2012}, set_cookie=["wr_skey=bad"])
        with mock.patch("weread.requests.request", return_value=resp):
            with self.assertRaises(AuthError):
                account.renew()
        self.assertEqual(account.cookies["wr_skey"], "old")

    def test_renewal_merges_cookies_and_headers(self):
        account = self.account()
        resp = FakeResponse(body={"succ": 1}, set_cookie=["wr_skey=new; Path=/"],
                            headers={"x-wr-ticket": "T1"})
        with mock.patch("weread.requests.request", return_value=resp) as req:
            account.renew()
        self.assertEqual(account.cookies["wr_skey"], "new")
        self.assertEqual(account.state["wr_ticket"], "T1")
        self.assertIn("wr_fp=9", req.call_args.kwargs["headers"]["Cookie"])

    def test_load_context_reads_server_position(self):
        account = self.account()
        with mock.patch("weread.requests.request", return_value=FakeResponse(text=READER_HTML)) as req:
            ctx = account.load_context("https://weread.qq.com/web/reader/abc?x=1")
        self.assertEqual(req.call_args.args[1], "https://weread.qq.com/web/reader/abc")
        self.assertEqual((ctx["book_id"], ctx["chapter_uid"], ctx["chapter_idx"]), ("43208843", 12, 3))
        self.assertEqual((ctx["progress"], ctx["chapter_offset"], ctx["psvts"]), (55, 99, "PSV"))

    def test_load_context_rejects_foreign_urls(self):
        with self.assertRaises(ValueError):
            self.account().load_context("https://evil.example/web/reader/abc")

    def test_logged_out_reader_page_is_auth_error(self):
        with mock.patch("weread.requests.request", return_value=FakeResponse(text="<html>login</html>")):
            with self.assertRaises(AuthError):
                self.account().load_context("43208843")


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeAccount:
    def __init__(self, replies):
        self.replies = list(replies)
        self.renewals = 0
        self.opens = 0
        self.reads = []

    def renew(self):
        self.renewals += 1

    def load_context(self, book):
        self.opens += 1
        return {"title": "T", "progress": 1, "reader_url": "u", "book_id": "1", "psvts": "P"}

    def report(self, payload, referer):
        if "rt" not in payload:
            return {"succ": 1}
        self.reads.append(payload["rt"])
        return self.replies.pop(0) if self.replies else {"succ": 1}


class RunTest(unittest.TestCase):
    def run_reporter(self, account, minutes):
        clock = FakeClock()
        saves = []
        reporter = report.Reporter(account, "1", 30, lambda: saves.append(clock()), clock=clock)
        with mock.patch("report.log"):
            code = report.run(reporter, minutes, sleep=clock.sleep, clock=clock)
        return code, reporter, saves

    def test_reports_full_intervals_and_renews_periodically(self):
        account = FakeAccount([])
        code, reporter, saves = self.run_reporter(account, 61)
        self.assertEqual(code, 0)
        self.assertEqual(len(account.reads), 122)
        self.assertEqual(reporter.reported_seconds, 3660)
        self.assertEqual(saves, [0, 1200, 2400, 3600])  # start + every 20 min, each persisted
        self.assertEqual(account.opens, 5)  # start + every 15 min

    def test_rejection_refreshes_context_then_retries(self):
        account = FakeAccount([{"errCode": 1}, {"succ": 1}])
        code, reporter, _ = self.run_reporter(account, 1)
        self.assertEqual(code, 0)
        self.assertEqual(account.renewals, 1)  # only the startup renewal: cooldown not reached
        self.assertEqual(reporter.reported_seconds, 60)

    def test_stops_after_consecutive_failures(self):
        account = FakeAccount([{"errCode": 1}] * 100)
        code, reporter, _ = self.run_reporter(account, 30)
        self.assertEqual(code, 1)
        self.assertEqual(reporter.reported_seconds, 0)
        self.assertLess(len(account.reads), 10)


if __name__ == "__main__":
    unittest.main()
