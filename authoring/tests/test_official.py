from __future__ import annotations

import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from gyousei_pipeline import common
from gyousei_pipeline.official import (
    INDEX_RELATIVE_PATH,
    OfficialAnswerParseError,
    OfficialForbiddenError,
    OfficialValidationError,
    SerialOfficialClient,
    acquire_official,
    parse_official_answers,
    validate_download,
)


FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, status, body=b"", content_type="text/html", url="https://official.test/final"):
        self.status_code = status
        self.content = body
        self.headers = {"Content-Type": content_type}
        self.url = url


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected request: {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if response.url == "https://official.test/final":
            response.url = url
        return response


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.value

    def wall_time(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


def sample_config():
    return {
        "officialSource": {
            "id": "gyosei-shiken",
            "questionUrlTemplate": "https://official.test/pdf/{officialCode}_mondai.pdf",
            "answerUrlTemplate": "https://official.test/doc/{officialCode}ans.html",
        },
        "target": {"examYears": [2016]},
        "years": [
            {"examYear": 2016, "eraYear": "平成28年", "officialCode": "h28"},
        ],
        "fetch": {
            "userAgent": "unit-test",
            "minimumDelaySeconds": 0.01,
            "timeoutSeconds": 2,
            "maxAttempts": 3,
        },
    }


class OfficialAnswerParserTests(unittest.TestCase):
    def test_parses_modern_split_cells_and_written_grid(self):
        result = parse_official_answers((FIXTURES / "official_modern.html").read_bytes())
        self.assertEqual(22, len(result["answers"]))
        by_number = {row["questionNumber"]: row for row in result["answers"]}
        self.assertEqual(1, by_number[8]["answer"])
        self.assertEqual({"ア": 3, "イ": 6, "ウ": 19, "エ": 16}, by_number[42]["blanks"])
        self.assertEqual("行政庁は理由を示す。", by_number[44]["answerText"])
        self.assertEqual(10, by_number[44]["characterCount"])
        self.assertEqual([], result["warnings"])

    def test_parses_legacy_combined_cells_and_sibling_written_answer(self):
        result = parse_official_answers((FIXTURES / "official_legacy.html").read_bytes())
        by_number = {row["questionNumber"]: row for row in result["answers"]}
        self.assertEqual(5, by_number[8]["answer"])
        self.assertEqual({"ア": 1, "イ": 2, "ウ": 3, "エ": 4}, by_number[42]["blanks"])
        self.assertEqual("処分庁は理由を示す。", by_number[44]["answerText"])

    def test_conflicting_answer_is_an_error(self):
        html = (FIXTURES / "official_modern.html").read_text(encoding="utf-8")
        html = html.replace("</table>", "<tr><td>問題８</td><td>５</td></tr></table>", 1)
        with self.assertRaisesRegex(OfficialAnswerParseError, "conflicting"):
            parse_official_answers(html)

    def test_missing_target_answer_is_an_error(self):
        html = (FIXTURES / "official_modern.html").read_text(encoding="utf-8")
        html = html.replace("問題２６", "問題２７")
        with self.assertRaisesRegex(OfficialAnswerParseError, "question 26"):
            parse_official_answers(html)


class DownloadValidationTests(unittest.TestCase):
    def test_accepts_expected_magic_and_content_types(self):
        self.assertEqual("application/pdf", validate_download(b"%PDF-1.7\nbody", "application/pdf", "pdf"))
        html = "<!doctype html><title>正解</title>".encode()
        self.assertEqual("text/html", validate_download(html, "text/html; charset=utf-8", "html"))

    def test_rejects_wrong_content_type_and_magic(self):
        with self.assertRaises(OfficialValidationError):
            validate_download(b"%PDF-1.7", "text/html", "pdf")
        with self.assertRaises(OfficialValidationError):
            validate_download(b"not a PDF", "application/pdf", "pdf")

    def test_rejects_login_and_error_html_with_status_200(self):
        login = b"<!doctype html><html><title>Login</title><input type='password'></html>"
        with self.assertRaisesRegex(OfficialValidationError, "login"):
            validate_download(login, "text/html", "html", final_url="https://official.test/login")
        error = b"<html><title>404 Not Found</title><h1>404 Not Found</h1></html>"
        with self.assertRaisesRegex(OfficialValidationError, "error"):
            validate_download(error, "text/html", "html")


class SerialClientTests(unittest.TestCase):
    def test_retries_429_and_5xx_with_bounded_attempts_and_one_second_floor(self):
        clock = FakeClock()
        responses = [
            FakeResponse(429),
            FakeResponse(503),
            FakeResponse(200, b"%PDF-1.7\nok", "application/pdf"),
        ]
        session = FakeSession(responses)
        client = SerialOfficialClient(
            sample_config()["fetch"],
            session=session,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            wall_time=clock.wall_time,
        )
        result = client.get("https://official.test/file.pdf", "pdf")
        self.assertEqual(200, result.status_code)
        self.assertEqual(3, len(session.calls))
        self.assertGreaterEqual(clock.value, 3.0)
        self.assertTrue(all(value >= 1.0 for value in clock.sleeps))

    def test_403_stops_without_retry(self):
        session = FakeSession([FakeResponse(403)])
        client = SerialOfficialClient(sample_config()["fetch"], session=session, sleep=lambda _: None)
        with self.assertRaises(OfficialForbiddenError):
            client.get("https://official.test/blocked", "html")
        self.assertEqual(1, len(session.calls))

    def test_network_failure_is_not_retried(self):
        clock = FakeClock()
        session = FakeSession([requests.ConnectionError("down")] * 3)
        client = SerialOfficialClient(
            sample_config()["fetch"],
            session=session,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
        with self.assertRaisesRegex(Exception, "without retry"):
            client.get("https://official.test/down", "html")
        self.assertEqual(1, len(session.calls))


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"GYOUSEI_DATA_ROOT": self.temporary.name})
        self.environment.start()
        self.clock = FakeClock()
        self.answer_html = (FIXTURES / "official_modern.html").read_bytes()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def run_acquire(self, session):
        return acquire_official(
            sample_config(),
            exam_years=[2016],
            session=session,
            sleep=self.clock.sleep,
            monotonic=self.clock.monotonic,
            wall_time=self.clock.wall_time,
        )

    def test_stores_contract_and_skips_complete_year_on_second_run(self):
        session = FakeSession([
            FakeResponse(200, b"%PDF-1.7\nquestion", "application/pdf"),
            FakeResponse(200, self.answer_html, "text/html; charset=utf-8"),
        ])
        result = self.run_acquire(session)
        self.assertEqual([2016], result["completed"])
        self.assertEqual(2, len(session.calls))

        index = common.load_json(Path(self.temporary.name) / INDEX_RELATIVE_PATH)
        self.assertEqual("official-snapshots@1", index["schemaVersion"])
        self.assertEqual("complete", index["years"]["2016"]["fetchStatus"])
        self.assertEqual(2, len(index["snapshots"]))
        expected_fields = {
            "snapshotId", "sourceId", "externalQuestionId", "url", "finalUrl",
            "fetchedAt", "fetchStatus", "httpStatus", "contentType", "bodySha256",
            "bodyBytes", "bodyPath",
        }
        self.assertEqual(expected_fields, set(index["snapshots"][0]))
        self.assertEqual("official-2016-questions", index["snapshots"][0]["externalQuestionId"])
        self.assertEqual("official-2016-answers", index["snapshots"][1]["externalQuestionId"])
        for snapshot in index["snapshots"]:
            self.assertTrue((Path(self.temporary.name) / snapshot["bodyPath"]).is_file())
            events = list(
                (
                    Path(self.temporary.name)
                    / "raw" / "snapshots" / "official" / "events"
                    / snapshot["externalQuestionId"]
                ).glob("*.json")
            )
            self.assertEqual(1, len(events))
            self.assertEqual(snapshot, json.loads(events[0].read_text(encoding="utf-8")))

        no_requests = FakeSession([])
        second = self.run_acquire(no_requests)
        self.assertEqual([2016], second["skipped"])
        self.assertEqual([], no_requests.calls)

    def test_resumes_answer_after_pdf_was_already_saved(self):
        first = FakeSession([
            FakeResponse(200, b"%PDF-1.7\nquestion", "application/pdf"),
            FakeResponse(404, b"<html><title>missing</title></html>"),
        ])
        failed = self.run_acquire(first)
        self.assertEqual(1, len(failed["failed"]))

        second = FakeSession([FakeResponse(200, self.answer_html, "text/html")])
        completed = self.run_acquire(second)
        self.assertEqual([2016], completed["completed"])
        self.assertEqual(1, len(second.calls))
        self.assertTrue(second.calls[0][0].endswith("h28ans.html"))

    def test_existing_but_corrupted_blob_is_fetched_again(self):
        first = FakeSession([
            FakeResponse(200, b"%PDF-1.7\nquestion", "application/pdf"),
            FakeResponse(200, self.answer_html, "text/html"),
        ])
        self.run_acquire(first)
        index_path = Path(self.temporary.name) / INDEX_RELATIVE_PATH
        index = common.load_json(index_path)
        question = next(
            row for row in index["snapshots"]
            if row["externalQuestionId"] == "official-2016-questions"
        )
        blob = Path(self.temporary.name) / question["bodyPath"]
        blob.write_bytes(gzip.compress(b"%PDF-1.7\ntampered", mtime=0))

        second = FakeSession([
            FakeResponse(200, b"%PDF-1.7\nquestion-restored", "application/pdf"),
        ])
        result = self.run_acquire(second)
        self.assertEqual([2016], result["completed"])
        self.assertEqual(1, len(second.calls))
        self.assertTrue(second.calls[0][0].endswith("h28_mondai.pdf"))


if __name__ == "__main__":
    unittest.main()
