from __future__ import annotations

import copy
import gzip
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gyousei_pipeline.common import load_target
from gyousei_pipeline.discover import DiscoveryError, build_catalog, parse_year_index
from gyousei_pipeline.fetch import (
    FetchError,
    ForbiddenError,
    SerialFetcher,
    UnexpectedPageError,
    fetch_catalog,
    validate_question_response,
)


FIXTURES = Path(__file__).parent / "fixtures"


def shifted_year_html(template: str, era_year: str, offset: int) -> str:
    value = template.replace("平成28年", era_year)
    return re.sub(
        r"queID=(\d+)",
        lambda match: "queID=" + str(int(match.group(1)) + offset),
        value,
    )


def valid_question_html(question_number: int, secret: str = "provider secret") -> bytes:
    return (
        "<!doctype html><html><head><title>令和7年－問"
        + str(question_number)
        + "</title></head><body><div class='tit_status'><h3>令和7年－問"
        + str(question_number)
        + "</h3></div><div class='mondai-wrap'>問題</div>"
        + "<div class='kaisetsu'>"
        + secret
        + "</div></body></html>"
    ).encode("utf-8")


class FakeResponse:
    def __init__(
        self,
        status: int,
        body: bytes = b"",
        *,
        url: str = "https://www.pro.goukakudojyo.com/worksheet2/w_mainnendo.php?queID=1",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status
        self.content = body
        self.url = url
        self.headers = headers or {"Content-Type": "text/html; charset=UTF-8"}


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []
        self.headers: dict[str, str] = {}

    def get(self, url: str, **_: object) -> FakeResponse:
        self.calls.append(url)
        if not self.responses:
            raise AssertionError("Unexpected network request")
        return self.responses.pop(0)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class DiscoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_target()
        cls.template = (FIXTURES / "discover_h28_complete.html").read_text(encoding="utf-8")

    def test_parse_scans_normal_and_archive_and_uses_observed_hrefs(self) -> None:
        year = self.config["years"][0]
        entries = parse_year_index(self.template, year=year, config=self.config)
        self.assertEqual(22, len(entries))
        counts = {kind: sum(item["listingKind"] == kind for item in entries) for kind in (
            "regular", "multiple_blank", "written"
        )}
        self.assertEqual({"regular": 19, "multiple_blank": 2, "written": 1}, counts)
        question_26 = next(item for item in entries if item["questionNumber"] == 26)
        self.assertEqual("archive", question_26["endpointType"])
        self.assertTrue(question_26["url"].endswith("w_mainarch.php?queID=1236"))
        question_22 = next(item for item in entries if item["questionNumber"] == 22)
        self.assertTrue(question_22["isAmended"])
        question_17 = next(item for item in entries if item["questionNumber"] == 17)
        self.assertEqual("行政事件訴訟法", question_17["labels"][1])

    def test_catalog_requires_each_year_22_and_total_220(self) -> None:
        pages = {
            int(year["examYear"]): shifted_year_html(
                self.template, str(year["eraYear"]), index * 60
            )
            for index, year in enumerate(self.config["years"])
        }
        catalog = build_catalog(pages, self.config)
        self.assertEqual("catalog@1", catalog["schemaVersion"])
        self.assertEqual(220, len(catalog["entries"]))
        self.assertEqual(10, len(catalog["discoverySnapshots"]))
        self.assertEqual(220, len({item["url"] for item in catalog["entries"]}))

    def test_catalog_fails_closed_on_one_missing_question(self) -> None:
        pages = {
            int(year["examYear"]): shifted_year_html(
                self.template, str(year["eraYear"]), index * 60
            )
            for index, year in enumerate(self.config["years"])
        }
        pages[2016] = pages[2016].replace(
            '<tr><th><a href="w_mainnendo.php?queID=1218">平成28年－問8 '
            '<span class="connect-txt">行政法</span><span class="connect-txt">行政総論</span>'
            "</a></th></tr>\n",
            "",
        )
        with self.assertRaisesRegex(DiscoveryError, "regular=18"):
            build_catalog(pages, self.config)

    def test_archive_question_number_filter_excludes_other_common_format_questions(self) -> None:
        config = copy.deepcopy(self.config)
        config["target"]["listingRules"] = [
            {"kind": "regular", "requiredLabels": ["行政法"]},
            {"kind": "multiple_blank", "requiredLabels": ["多肢選択式"]},
            {"kind": "written", "requiredLabels": ["記述式"]},
        ]
        config["target"]["questionNumbersByKind"] = {
            "regular": list(range(8, 27)),
            "multiple_blank": [42, 43],
            "written": [44],
        }
        html = self.template.replace(
            "</table>",
            '<tr><th><a href="w_mainarch.php?queID=9001">平成28年－問41 '
            '<span class="connect-txt">多肢選択式</span></a></th></tr>'
            '<tr><th><a href="w_mainarch.php?queID=9002">平成28年－問45 '
            '<span class="connect-txt">記述式</span></a></th></tr></table>',
            1,
        )
        entries = parse_year_index(html, year=config["years"][0], config=config)
        self.assertEqual(22, len(entries))
        self.assertNotIn(41, {item["questionNumber"] for item in entries})
        self.assertNotIn(45, {item["questionNumber"] for item in entries})


class FetchTests(unittest.TestCase):
    def make_fetcher(self, responses: list[FakeResponse], *, attempts: int = 3):
        session = FakeSession(responses)
        clock = FakeClock()
        fetcher = SerialFetcher(
            session=session,
            user_agent="test-agent",
            minimum_delay_seconds=0,
            timeout_seconds=2,
            max_attempts=attempts,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
        return fetcher, session, clock

    @staticmethod
    def entry(external_id: str = "1758", question_number: int = 8) -> dict[str, object]:
        url = (
            "https://www.pro.goukakudojyo.com/worksheet2/"
            f"w_mainnendo.php?queID={external_id}"
        )
        return {
            "catalogId": f"goukakudojyo:{external_id}",
            "sourceId": "goukakudojyo",
            "externalQuestionId": external_id,
            "examYear": 2025,
            "eraYear": "令和7年",
            "questionNumber": question_number,
            "title": f"令和7年－問{question_number}",
            "labels": ["行政法"],
            "listingKind": "regular",
            "endpointType": "regular",
            "isAmended": False,
            "url": url,
            "yearIndexUrl": "https://example.invalid/year",
        }

    def test_retries_only_429_and_5xx_with_at_least_one_second(self) -> None:
        responses = [
            FakeResponse(429, headers={"Content-Type": "text/html", "Retry-After": "1"}),
            FakeResponse(503),
            FakeResponse(200, valid_question_html(8)),
        ]
        fetcher, session, clock = self.make_fetcher(responses)
        response = fetcher.get(str(self.entry()["url"]))
        self.assertEqual(200, response.status_code)
        self.assertEqual(3, len(session.calls))
        self.assertEqual([1.0, 1.0], clock.sleeps)

    def test_403_is_immediate_hard_stop(self) -> None:
        fetcher, session, _ = self.make_fetcher([FakeResponse(403), FakeResponse(200)])
        with self.assertRaises(ForbiddenError):
            fetcher.get(str(self.entry()["url"]))
        self.assertEqual(1, len(session.calls))

    def test_non_retryable_status_is_not_retried(self) -> None:
        fetcher, session, _ = self.make_fetcher([FakeResponse(404), FakeResponse(200)])
        with self.assertRaises(FetchError):
            fetcher.get(str(self.entry()["url"]))
        self.assertEqual(1, len(session.calls))

    def test_login_page_is_rejected(self) -> None:
        entry = self.entry()
        response = FakeResponse(
            200,
            (
                "<html><head><title>会員ログイン</title></head><body>"
                "<input type='password'></body></html>"
            ).encode("utf-8"),
            url="https://www.pro.goukakudojyo.com/member/w_login.php",
        )
        with self.assertRaises(UnexpectedPageError):
            validate_question_response(response, entry)

    def test_content_addressed_snapshot_is_private_metadata_only_and_resumable(self) -> None:
        entry = self.entry()
        catalog = {"schemaVersion": "catalog@1", "entries": [entry]}
        body = valid_question_html(8, "DO-NOT-PUBLISH-EXPLANATION")
        response = FakeResponse(200, body, url=str(entry["url"]))
        fetcher, session, _ = self.make_fetcher([response])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(os.environ, {"GYOUSEI_DATA_ROOT": str(root)}):
                index_path = root / "raw" / "snapshots" / "index.json"
                index = fetch_catalog(catalog, fetcher=fetcher, index_path=index_path)
                self.assertEqual("snapshots@1", index["schemaVersion"])
                self.assertEqual("ok", index["items"][0]["fetchStatus"])
                blob_path = root / index["items"][0]["bodyPath"]
                with gzip.open(blob_path, "rb") as source:
                    self.assertEqual(body, source.read())
                self.assertNotIn("DO-NOT-PUBLISH", index_path.read_text(encoding="utf-8"))
                events = list((root / "raw" / "snapshots" / "provider" / "1758").glob("*.json"))
                self.assertEqual(1, len(events))
                self.assertNotIn("DO-NOT-PUBLISH", events[0].read_text(encoding="utf-8"))

                no_network_fetcher, no_network_session, _ = self.make_fetcher([])
                second = fetch_catalog(
                    catalog, fetcher=no_network_fetcher, index_path=index_path
                )
                self.assertEqual(index["items"], second["items"])
                self.assertEqual([], no_network_session.calls)
                self.assertEqual(1, len(list(events[0].parent.glob("*.json"))))
        self.assertEqual(1, len(session.calls))


if __name__ == "__main__":
    unittest.main()
