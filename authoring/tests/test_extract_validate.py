from __future__ import annotations

import copy
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from gyousei_pipeline.extract import (
    _documents as extract_documents,
    infer_regular_task,
    parse_question_html,
)
from gyousei_pipeline.validate import _documents as validate_documents, validate_dataset


FIXTURES = Path(__file__).parent / "fixtures"
TARGET = {
    "target": {
        "examYears": list(range(2016, 2026)),
        "expectedPerYear": {
            "regular": 19,
            "multipleChoice": 2,
            "written": 1,
            "total": 22,
        },
        "expectedTotal": 220,
    }
}


class ExtractValidateTests(unittest.TestCase):
    def make_entry(
        self,
        kind: str,
        number: int,
        external_id: str,
        labels: list[str],
        *,
        amended: bool = False,
    ) -> dict:
        return {
            "catalogId": f"gkd-2025-q{number}",
            "sourceId": "goukakudojyo",
            "externalQuestionId": external_id,
            "examYear": 2025,
            "eraYear": "令和7年",
            "questionNumber": number,
            "title": f"令和7年－問{number}",
            "labels": labels,
            "listingKind": kind,
            "endpointType": "regular",
            "isAmended": amended,
            "url": f"https://example.test/question?id={external_id}",
            "yearIndexUrl": "https://example.test/year/2025",
        }

    def make_snapshot(self, entry: dict, body: bytes) -> dict:
        digest = hashlib.sha256(body).hexdigest()
        return {
            "snapshotId": f"snapshot-{entry['externalQuestionId']}",
            "sourceId": entry["sourceId"],
            "externalQuestionId": entry["externalQuestionId"],
            "url": entry["url"],
            "finalUrl": entry["url"],
            "fetchedAt": "2026-07-18T10:00:00Z",
            "fetchStatus": "ok",
            "httpStatus": 200,
            "contentType": "text/html; charset=UTF-8",
            "bodySha256": digest,
            "bodyBytes": len(body),
            "bodyPath": f"raw/blobs/{digest}.html.gz",
        }

    def parse_fixture(
        self, fixture: str, kind: str, number: int, external_id: str, labels: list[str]
    ) -> tuple[dict, dict]:
        body = (FIXTURES / fixture).read_bytes()
        entry = self.make_entry(kind, number, external_id, labels)
        snapshot = self.make_snapshot(entry, body)
        return parse_question_html(body, entry, snapshot), snapshot

    def assert_valid_record(self, record: dict, snapshot: dict) -> None:
        report = validate_dataset(
            [record], [snapshot], TARGET, check_coverage=False
        )
        self.assertTrue(report["ok"], report["issues"])

    def test_regular_extracts_question_choices_answer_without_ui_or_explanation(self) -> None:
        record, snapshot = self.parse_fixture(
            "extract_regular.html",
            "regular",
            11,
            "1761",
            ["行政法", "行政手続法"],
        )
        self.assertEqual(record["task"]["kind"], "select_true")
        self.assertEqual(record["answer"], {"kind": "option", "value": 1})
        self.assertEqual(len(record["choices"]), 5)
        self.assertEqual(record["choiceFormat"], "list")
        self.assertEqual(record["choiceColumns"], [])
        self.assertEqual(record["providerUpdatedAt"], "2026-01-12T00:44:17+09:00")
        self.assertFalse(record["explanationCaptured"])
        serialized = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("SlctChk", serialized)
        self.assertNotIn("UI-ONLY", serialized)
        self.assertNotIn("EXPLANATION-MUST-NOT-BE-CAPTURED", serialized)
        self.assert_valid_record(record, snapshot)

    def test_optional_all_subject_catalog_metadata_is_preserved(self) -> None:
        body = (FIXTURES / "extract_regular.html").read_bytes()
        entry = self.make_entry(
            "regular",
            11,
            "1761",
            ["行政法", "行政手続法"],
        )
        entry.update(
            {
                "subjectId": "administrative_law",
                "subjectLabel": "行政法",
                "explanationExpected": True,
                "historicalUse": "current_editorial_reference",
            }
        )
        snapshot = self.make_snapshot(entry, body)
        record = parse_question_html(body, entry, snapshot)
        self.assertEqual(record["subjectId"], "administrative_law")
        self.assertEqual(record["subjectLabel"], "行政法")
        self.assertIs(record["explanationExpected"], True)
        self.assertEqual(record["historicalUse"], "current_editorial_reference")

    def test_regular_task_inference_is_conservative(self) -> None:
        cases = {
            "妥当でないものはどれか。": "select_false",
            "行政手続法に規定されていないものはどれか。": "select_false",
            "判決の内容に明らかに反しているものはどれか。": "select_false",
            "判決の論旨に含まれていないものはどれか。": "select_false",
            "時効取得できないものはどれか。": "select_false",
            "趣旨に最も適合しないものはどれか。": "select_false",
            "空欄のどれにも当てはまらないものはどれか。": "select_false",
            "規律の対象とならないものはどれか。": "select_false",
            "発言の趣旨と明白に対立する見解はどれか。": "select_false",
            "憲法改正が必要ではないものはどれか。": "select_false",
            "他とは異なっているものはどれか。": "select_false",
            "この文章から読み取れない内容はどれか。": "select_false",
            "定款の定めを必要としないものはどれか。": "select_false",
            "正しいものの組合せはどれか。": "combination",
            "妥当なものはいくつあるか。": "count",
            "妥当なものはどれか。": "select_true",
            "会社法が定めているのはどれか。": "select_true",
            "Aの方がBより大きな値となるものはどれか。": "select_true",
            "最高裁判所の判例の述べるところはどれか。": "select_true",
            "この見地に基づく意見はどれか。": "select_true",
            "可能ですと回答しうるものはどれか。": "select_true",
            "無効原因となるものはどれか。": "select_true",
            "本人の同意を得る必要がある場合はどれか。": "select_true",
            "登記を必要とする事項はどれか。": "select_true",
        }
        for prompt, expected in cases.items():
            with self.subTest(prompt=prompt):
                task, warnings = infer_regular_task(prompt)
                self.assertEqual(task["kind"], expected)
                self.assertEqual(warnings, [])
        task, warnings = infer_regular_task("次の記述を検討しなさい。")
        self.assertEqual(task["kind"], "unknown")
        self.assertEqual(warnings, ["unknown_regular_task"])

    def test_regular_uses_the_explicit_question_after_a_background_paragraph(self) -> None:
        body = (FIXTURES / "extract_regular.html").read_bytes().replace(
            "<p>次の記述のうち、妥当なものはどれか。</p>".encode(),
            (
                "<p>会社に対して処分が行われた。</p>"
                "<p>この事例に関する次の記述のうち、妥当なものはどれか。</p>"
            ).encode(),
        )
        entry = self.make_entry("regular", 11, "1761", ["行政法", "行政手続法"])
        snapshot = self.make_snapshot(entry, body)
        record = parse_question_html(body, entry, snapshot)
        self.assertEqual(
            record["instructionText"],
            "この事例に関する次の記述のうち、妥当なものはどれか。",
        )
        self.assertEqual(record["task"]["kind"], "select_true")
        self.assertIn("会社に対して処分", record["questionText"])
        self.assert_valid_record(record, snapshot)

    def test_regular_combination_table_keeps_rows_and_columns(self) -> None:
        record, snapshot = self.parse_fixture(
            "extract_regular_table.html",
            "regular",
            11,
            "1761",
            ["行政法", "行政手続法"],
        )
        self.assertEqual(record["task"]["kind"], "combination")
        self.assertEqual(record["choiceFormat"], "table")
        self.assertEqual(record["choiceColumns"], ["Ⅰ", "Ⅱ"])
        self.assertEqual(
            record["choices"][0],
            {
                "label": "1",
                "text": "Ⅰ：ア／Ⅱ：ア",
                "cells": [
                    {"column": "Ⅰ", "text": "ア"},
                    {"column": "Ⅱ", "text": "ア"},
                ],
            },
        )
        self.assertIn("候補A", record["questionText"])
        self.assertNotIn("Ⅰ：ア／Ⅱ：ア", record["questionText"])
        self.assertNotIn("UI-ONLY", json.dumps(record, ensure_ascii=False))
        self.assertEqual(record["answer"], {"kind": "option", "value": 4})
        self.assertEqual(record["extraction"]["status"], "parsed")
        self.assert_valid_record(record, snapshot)

        malformed = copy.deepcopy(record)
        malformed["choices"][0]["cells"][0]["column"] = "不一致"
        report = validate_dataset([malformed], [snapshot], TARGET, check_coverage=False)
        self.assertIn("regular_choice_cells", {item["code"] for item in report["issues"]})

    def test_unknown_regular_task_is_queued_for_review(self) -> None:
        body = (FIXTURES / "extract_regular.html").read_bytes().replace(
            "次の記述のうち、妥当なものはどれか。".encode(),
            "次の記述を検討しなさい。".encode(),
        )
        entry = self.make_entry("regular", 11, "1761", ["行政法", "行政手続法"])
        snapshot = self.make_snapshot(entry, body)
        record = parse_question_html(body, entry, snapshot)
        self.assertEqual(record["task"]["kind"], "unknown")
        self.assertEqual(record["extraction"]["status"], "needs_review")

    def test_multiple_blank_extracts_passage_twenty_terms_and_four_answers(self) -> None:
        record, snapshot = self.parse_fixture(
            "extract_multiple_blank.html",
            "multiple_blank",
            42,
            "1792",
            ["多肢選択式", "行政法"],
        )
        self.assertEqual(record["blanks"], ["ア", "イ", "ウ", "エ"])
        self.assertEqual(len(record["wordBank"]), 20)
        self.assertEqual(
            record["answer"]["values"], {"ア": 3, "イ": 6, "ウ": 19, "エ": 16}
        )
        self.assertIn("本文", record["passageText"])
        self.assert_valid_record(record, snapshot)

    def test_multiple_blank_extracts_passage_from_provider_div_variants(self) -> None:
        fixture = (FIXTURES / "extract_multiple_blank_div.html").read_bytes()
        for class_name in (b"no_read", b"read2"):
            with self.subTest(class_name=class_name.decode()):
                body = fixture.replace(b"no_read", class_name)
                entry = self.make_entry(
                    "multiple_blank", 42, "1792", ["多肢選択式", "行政法"]
                )
                snapshot = self.make_snapshot(entry, body)
                record = parse_question_html(body, entry, snapshot)
                self.assertEqual(record["blanks"], ["ア", "イ", "ウ", "エ"])
                self.assertIn("会話の中に", record["passageText"])
                self.assertEqual(record["sourceNote"], "")
                self.assertEqual(record["extraction"]["status"], "parsed")
                self.assert_valid_record(record, snapshot)

    def test_written_extracts_reference_limit_and_model_answer(self) -> None:
        record, snapshot = self.parse_fixture(
            "extract_written.html",
            "written",
            44,
            "1794",
            ["記述式", "行政法"],
        )
        self.assertEqual(record["characterLimit"], 40)
        self.assertEqual(record["characterLimitKind"], "approximately")
        self.assertIn("行政事件訴訟法", record["referenceText"])
        self.assertNotIn("（30字）", record["modelAnswer"])
        self.assertEqual(record["modelAnswerCharacterCount"], 30)
        self.assert_valid_record(record, snapshot)

    def test_written_reference_is_optional_for_years_without_reference_block(self) -> None:
        body = (FIXTURES / "extract_written.html").read_bytes()
        body = re.sub(rb'<p class="read">.*?</p>', b"", body, flags=re.DOTALL)
        entry = self.make_entry("written", 44, "1794", ["記述式", "行政法"])
        snapshot = self.make_snapshot(entry, body)
        record = parse_question_html(body, entry, snapshot)
        self.assertEqual(record["referenceText"], "")
        self.assertEqual(record["extraction"]["status"], "parsed")
        self.assert_valid_record(record, snapshot)

    def test_archive_regular_reads_historical_answer_without_update_date(self) -> None:
        body = """
        <div class="tit_status"><h3>平成27年－問8<span class="connect-txt">行政法</span></h3></div>
        <div class="mondai-wrap"><div class="que-mon"><div class="toi">
          <p>妥当なものはどれか。</p><ol>
            <li>肢1<span class="SlctChk">UI</span></li><li>肢2</li><li>肢3</li><li>肢4</li><li>肢5</li>
          </ol>
        </div></div></div><p class="kotae">当時の答え<span class="arch-ans">2</span></p>
        """.encode()
        entry = self.make_entry("regular", 8, "618", ["行政法"])
        entry.update({"catalogId": "goukakudojyo_archive:618", "sourceId": "goukakudojyo_archive", "examYear": 2015, "eraYear": "平成27年", "title": "平成27年－問8", "endpointType": "archive"})
        snapshot = self.make_snapshot(entry, body)
        record = parse_question_html(body, entry, snapshot)
        self.assertEqual({"kind": "option", "value": 2}, record["answer"])
        self.assertIsNone(record["providerUpdatedAt"])
        self.assertEqual({"status": "parsed", "warnings": []}, record["extraction"])

    def test_archive_withdrawn_regular_question_keeps_the_provider_note(self) -> None:
        body = """
        <div class="tit_status"><h3>平成27年－問16<span class="connect-txt">行政法</span></h3></div>
        <div class="mondai-wrap"><div class="que-mon"><div class="toi">
          <p>妥当なものはどれか。</p><ol><li>肢1</li><li>肢2</li>
          <li>肢3</li><li>肢4</li><li>肢5</li></ol>
        </div></div></div>
        <p class="kotae">当時の答え<span class="arch-ans">没問（正解肢なし）</span></p>
        """.encode()
        entry = self.make_entry("regular", 16, "626", ["行政法"])
        entry.update({"catalogId": "goukakudojyo_archive:626", "sourceId": "goukakudojyo_archive", "examYear": 2015, "eraYear": "平成27年", "title": "平成27年－問16", "endpointType": "archive"})
        snapshot = self.make_snapshot(entry, body)
        record = parse_question_html(body, entry, snapshot)
        self.assertIsNone(record["answer"]["value"])
        self.assertIn("没問", record["answer"]["note"])
        self.assertFalse(record["isWithdrawn"])
        self.assertNotIn("missing_regular_answer", record["extraction"]["warnings"])

    def test_withdrawn_page_title_is_preserved_without_false_mismatch_warning(
        self,
    ) -> None:
        body = (FIXTURES / "extract_regular.html").read_bytes().replace(
            "令和7年－問11".encode(),
            "令和7年－問11（没問）".encode(),
        )
        entry = self.make_entry("regular", 11, "1761", ["行政法", "行政手続法"])
        snapshot = self.make_snapshot(entry, body)
        record = parse_question_html(body, entry, snapshot)
        self.assertEqual(record["title"], "令和7年－問11（没問）")
        self.assertTrue(record["isWithdrawn"])
        self.assertNotIn(
            "catalog_page_title_mismatch",
            record["extraction"]["warnings"],
        )

    def test_archive_multiple_blank_and_written_answers_are_kept(self) -> None:
        multiple = ("""
        <div class="tit_status"><h3>平成27年－問42<span class="connect-txt">多肢選択式</span></h3></div>
        <div class="mondai-wrap"><div class="que-mon"><div class="toi">
          <p>空欄［ ア ］～［ エ ］を選びなさい。</p><p>［ ア ］［ イ ］［ ウ ］［ エ ］</p>
          <ol class="tashi-li">%s</ol>
        </div></div></div>
        <p class="kotae"><span><span>ア<span class="arch-ans">9</span></span><span>イ<span class="arch-ans">13</span></span><span>ウ<span class="arch-ans">17</span></span><span>エ<span class="arch-ans">7</span></span></span></p>
        """ % "".join(f"<li>語{number}</li>" for number in range(1, 21))).encode()
        entry = self.make_entry("multiple_blank", 42, "652", ["多肢選択式"])
        entry.update({"catalogId": "goukakudojyo_archive:652", "sourceId": "goukakudojyo_archive", "examYear": 2015, "eraYear": "平成27年", "title": "平成27年－問42", "endpointType": "archive"})
        snapshot = self.make_snapshot(entry, multiple)
        record = parse_question_html(multiple, entry, snapshot)
        self.assertEqual({"ア": 9, "イ": 13, "ウ": 17, "エ": 7}, record["answer"]["values"])
        self.assertEqual("parsed", record["extraction"]["status"])

        written = """
        <div class="tit_status"><h3>平成27年－問44<span class="connect-txt">記述式</span></h3></div>
        <div class="mondai-wrap"><div class="que-mon"><div class="toi"><p>40字程度で記述しなさい。</p></div></div></div>
        <p class="kotae kijutsu">当時の正解例<span class="arch-ans">Y県が被告となる。（9字）</span></p>
        """.encode()
        entry = self.make_entry("written", 44, "654", ["記述式"])
        entry.update({"catalogId": "goukakudojyo_archive:654", "sourceId": "goukakudojyo_archive", "examYear": 2015, "eraYear": "平成27年", "title": "平成27年－問44", "endpointType": "archive"})
        snapshot = self.make_snapshot(entry, written)
        record = parse_question_html(written, entry, snapshot)
        self.assertEqual("Y県が被告となる。", record["modelAnswer"])
        self.assertEqual("parsed", record["extraction"]["status"])

    def test_validator_detects_snapshot_hash_and_parse_errors(self) -> None:
        record, snapshot = self.parse_fixture(
            "extract_regular.html",
            "regular",
            11,
            "1761",
            ["行政法", "行政手続法"],
        )
        bad_hash = copy.deepcopy(record)
        bad_hash["sourceBodySha256"] = "0" * 64
        report = validate_dataset([bad_hash], [snapshot], TARGET, check_coverage=False)
        self.assertIn("snapshot_hash_mismatch", {item["code"] for item in report["issues"]})

        parse_error = copy.deepcopy(record)
        parse_error["extraction"] = {"status": "parse_error", "warnings": ["broken"]}
        report = validate_dataset([parse_error], [snapshot], TARGET, check_coverage=False)
        self.assertIn("parse_error", {item["code"] for item in report["issues"]})

    def test_validator_verifies_the_stored_snapshot_blob(self) -> None:
        record, snapshot = self.parse_fixture(
            "extract_regular.html",
            "regular",
            11,
            "1761",
            ["行政法", "行政手続法"],
        )
        report = validate_dataset(
            [record],
            [snapshot],
            TARGET,
            check_coverage=False,
            blob_loader=lambda _path: b"different snapshot body",
        )
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("snapshot_blob_size", codes)
        self.assertIn("snapshot_blob_hash", codes)

    def test_snapshot_indexes_accept_items_and_snapshots_collections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            items_path = root / "items.json"
            snapshots_path = root / "snapshots.json"
            items_path.write_text(json.dumps({"items": [{"snapshotId": "one"}]}))
            snapshots_path.write_text(json.dumps({"snapshots": [{"snapshotId": "two"}]}))
            self.assertEqual(extract_documents(items_path), [{"snapshotId": "one"}])
            self.assertEqual(extract_documents(snapshots_path), [{"snapshotId": "two"}])
            self.assertEqual(validate_documents(items_path, "snapshots"), [{"snapshotId": "one"}])
            self.assertEqual(
                validate_documents(snapshots_path, "snapshots"), [{"snapshotId": "two"}]
            )

    def test_validator_accepts_exact_22_per_year_and_220_total(self) -> None:
        regular, _ = self.parse_fixture(
            "extract_regular.html", "regular", 11, "1761", ["行政法", "行政手続法"]
        )
        multiple, _ = self.parse_fixture(
            "extract_multiple_blank.html", "multiple_blank", 42, "1792", ["多肢選択式", "行政法"]
        )
        written, _ = self.parse_fixture(
            "extract_written.html", "written", 44, "1794", ["記述式", "行政法"]
        )
        records = []
        for year in range(2016, 2026):
            for number in range(8, 27):
                records.append(self.reidentify(regular, year, number, "regular"))
            records.append(self.reidentify(multiple, year, 42, "multiple_blank"))
            records.append(self.reidentify(multiple, year, 43, "multiple_blank"))
            records.append(self.reidentify(written, year, 44, "written"))
        report = validate_dataset(records, target=TARGET)
        self.assertTrue(report["ok"], report["issues"])
        self.assertEqual(report["recordCount"], 220)
        self.assertTrue(all(value["total"] == 22 for value in report["countsByYear"].values()))

    @staticmethod
    def reidentify(template: dict, year: int, number: int, kind: str) -> dict:
        record = copy.deepcopy(template)
        identifier = f"gkd-{year}-q{number}-{kind}"
        record.update(
            {
                "rawQuestionId": identifier,
                "catalogId": identifier,
                "sourceSnapshotId": f"snapshot-{identifier}",
                "externalQuestionId": f"{year}-{number}-{kind}",
                "examYear": year,
                "eraYear": str(year),
                "questionNumber": number,
                "title": f"{year}年－問{number}",
                "listingKind": kind,
            }
        )
        return record


if __name__ == "__main__":
    unittest.main()
