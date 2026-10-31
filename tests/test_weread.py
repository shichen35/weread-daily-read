import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import books  # noqa: E402
import report  # noqa: E402
import weread  # noqa: E402
from weread import AuthError, sorted_query  # noqa: E402

# Expected values come from a reference implementation of the Web protocol.
CTX = {
    "book_id": "43208843",
    "chapter_uid": 12,
    "chapter_idx": 3,
    "chapter_offset": 150,
    "psvts": "PSV",
}


class ProtocolTest(unittest.TestCase):
    def test_e_matches_reference(self):
        self.assertEqual(weread.weread_e("43208843"), "c9c321c07293508bc9c79df")
        self.assertEqual(weread.weread_e(0), "cfc32da010cfcd208495488")
        self.assertEqual(weread.weread_e("MP_WXS_123"), "40642b4144d505f5758535f313233d40")
        self.assertEqual(weread.weread_e(1793000000), "769327707aafe6a0g010479")

    def test_app_id_matches_doc(self):
        self.assertEqual(weread.web_app_id(), "wb115321887466h529830856")

    def test_enter_payload_matches_reference(self):
        ctx = dict(CTX, progress=42.7, summary="一二三四五六七八九十" * 2 + "多余", pclts="")
        p = weread.enter_payload(ctx, now=1793000000)
        self.assertEqual(p["s"], "7415af64")
        self.assertEqual(p["pr"], 42)
        self.assertEqual(p["pc"], "769327707aafe6a0g010479")
        self.assertNotIn("rt", p)

    def test_read_payload_matches_reference(self):
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

    def test_is_counted_needs_synckey(self):
        self.assertTrue(weread.is_counted({"succ": 1, "synckey": 0}))
        self.assertFalse(weread.is_counted({"succ": 1}))
        self.assertFalse(weread.is_counted(None))

    def test_parse_books(self):
        self.assertEqual(weread.parse_books("123"), ["123"])
        self.assertEqual(weread.parse_books("1,2, 3\n4，5"), ["1", "2", "3", "4", "5"])
        self.assertEqual(weread.parse_books("1,1,2"), ["1", "2"])
        url = "https://weread.qq.com/web/reader/abc"
        self.assertEqual(weread.parse_books(url), [url])
        self.assertEqual(weread.parse_books(" , \n"), [])

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

    def test_shelf_keeps_reader_books_in_order(self):
        body = {"books": [
            {"bookId": "2", "title": "B", "author": "Y"},
            {"bookId": "MP_WXS_1", "title": "article"},
            {"bookId": "1", "title": "A"},
        ]}
        with mock.patch("weread.requests.request", return_value=FakeResponse(body=body)):
            books = self.account().shelf()
        self.assertEqual(books, [
            {"book_id": "2", "title": "B", "author": "Y"},
            {"book_id": "1", "title": "A", "author": ""},
        ])

    def test_shelf_errors(self):
        with mock.patch("weread.requests.request", return_value=FakeResponse(body={"errCode": -2012})):
            with self.assertRaises(AuthError):
                self.account().shelf()
        with mock.patch("weread.requests.request", return_value=FakeResponse(body={"errCode": -1})):
            with self.assertRaises(RuntimeError) as cm:
                self.account().shelf()
            self.assertNotIsInstance(cm.exception, AuthError)
        with mock.patch("weread.requests.request", return_value=FakeResponse(status=502)):
            with self.assertRaises(RuntimeError):
                self.account().shelf()

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
        self.syncs = 0
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
        return self.replies.pop(0) if self.replies else {"succ": 1, "synckey": 1}

    def sync_chapters(self, book_id, referer):
        self.syncs += 1


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
        account = FakeAccount([{"errCode": 1}, {"succ": 1, "synckey": 1}])
        code, reporter, _ = self.run_reporter(account, 1)
        self.assertEqual(code, 0)
        self.assertEqual(account.renewals, 1)  # only the startup renewal: cooldown not reached
        self.assertEqual(reporter.reported_seconds, 60)

    def test_missing_synckey_syncs_chapters_then_retries(self):
        account = FakeAccount([{"succ": 1}, {"succ": 1, "synckey": 2}])
        code, reporter, _ = self.run_reporter(account, 1)
        self.assertEqual(code, 0)
        self.assertEqual(account.syncs, 1)
        self.assertEqual(reporter.reported_seconds, 60)  # the uncounted report is not added

    def test_stops_when_synckey_never_returns(self):
        account = FakeAccount([{"succ": 1}] * 100)
        code, reporter, _ = self.run_reporter(account, 30)
        self.assertEqual(code, 1)
        self.assertEqual(reporter.reported_seconds, 0)
        self.assertGreater(account.syncs, 0)

    def test_stops_after_consecutive_failures(self):
        account = FakeAccount([{"errCode": 1}] * 100)
        code, reporter, _ = self.run_reporter(account, 30)
        self.assertEqual(code, 1)
        self.assertEqual(reporter.reported_seconds, 0)
        self.assertLess(len(account.reads), 10)


class PlanTest(unittest.TestCase):
    def test_parse_minutes(self):
        self.assertEqual(report.parse_minutes("60"), (60, 60))
        self.assertEqual(report.parse_minutes(" 45 - 75 "), (45, 75))
        self.assertEqual(report.parse_minutes("75-45"), (45, 75))
        for spec in ("60±10", "60 +- 10", "60+/-10"):
            self.assertEqual(report.parse_minutes(spec), (50, 70))
        self.assertEqual(report.parse_minutes("5±10"), (0, 15))
        for bad in ("", "abc", "10-", "-5", "60±", "60+10"):
            with self.assertRaises(ValueError):
                report.parse_minutes(bad)

    def test_pick_book(self):
        self.assertEqual(report.pick_book("42"), ("42", 1))
        rng = report.random.Random(3)
        picks = {report.pick_book("1,2,3", rng)[0] for _ in range(50)}
        self.assertEqual(picks, {"1", "2", "3"})
        self.assertEqual(report.pick_book("1,2", rng)[1], 2)
        with self.assertRaises(ValueError):
            report.pick_book(" ")

    def test_no_window_starts_now(self):
        delay, minutes = report.plan("60", 0)
        self.assertEqual((delay, minutes), (0, 60))

    def test_session_fits_in_window(self):
        rng = report.random.Random(1)
        for _ in range(500):
            delay, minutes = report.plan("45-75", 180, rng)
            self.assertTrue(45 <= minutes <= 75)
            self.assertGreaterEqual(delay, 0)
            self.assertLessEqual(delay / 60 + minutes, 180 + 1e-9)

    def test_minutes_capped_by_window_and_window_capped(self):
        delay, minutes = report.plan("90", 30)
        self.assertEqual((delay, minutes), (0, 30))
        delay, minutes = report.plan("60", 10_000, report.random.Random(2))
        self.assertLessEqual(delay / 60 + minutes, report.MAX_WINDOW_MINUTES)


class BooksTest(unittest.TestCase):
    def test_find_run_picks_newest_after_trigger(self):
        since = books.datetime(2026, 1, 1, 12, 0, tzinfo=books.timezone.utc)
        runs = [
            {"databaseId": 1, "createdAt": "2026-01-01T11:59:00Z"},
            {"databaseId": 3, "createdAt": "2026-01-01T12:00:09Z"},
            {"databaseId": 2, "createdAt": "2026-01-01T12:00:05Z"},
        ]
        self.assertEqual(books.find_run(runs, since), 3)
        self.assertIsNone(books.find_run(runs[:1], since))

    def test_build_choices_prechecks_current_and_keeps_missing(self):
        shelf = [{"book_id": "1", "title": "A", "author": "X"}, {"book_id": "2", "title": "B", "author": ""}]
        self.assertEqual(books.build_choices(shelf, ["2", "9"]), [
            ("A — X", "1", False),
            ("B", "2", True),
            ("9 (not on shelf)", "9", True),
        ])

    def test_fetch_shelf_runs_workflow_and_reads_artifact(self):
        calls = []

        def fake_gh(args, repo="", capture=True):
            calls.append(args[:2])
            if args[:2] == ["run", "list"]:
                now = books.datetime.now(books.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                return json.dumps([{"databaseId": 7, "createdAt": now}])
            if args[:2] == ["run", "download"]:
                Path(args[args.index("--dir") + 1], "shelf.json").write_text('[{"book_id": "1"}]')
            return ""

        with mock.patch("books.gh", side_effect=fake_gh), mock.patch("books.time.sleep"), \
                mock.patch("builtins.print"):
            self.assertEqual(books.fetch_shelf("o/r"), [{"book_id": "1"}])
        self.assertEqual(calls, [["workflow", "run"], ["run", "list"], ["run", "watch"], ["run", "download"]])

    def test_gh_passes_repo(self):
        with mock.patch("books.subprocess.run") as run:
            run.return_value.stdout = "x"
            self.assertEqual(books.gh(["variable", "get", "WEREAD_BOOK"], "o/r"), "x")
        self.assertEqual(run.call_args.args[0], ["gh", "variable", "get", "WEREAD_BOOK", "--repo", "o/r"])


if __name__ == "__main__":
    unittest.main()
