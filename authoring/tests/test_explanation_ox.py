from __future__ import annotations

import copy
import json
import stat
import tempfile
import unittest
from pathlib import Path

from gyousei_pipeline.explanation_ox import (
    ExplanationOxError,
    build_inventory,
    main,
    parse_explicit_verdict,
)


def record(
    raw_id: str,
    question_number: int,
    task: str,
    *,
    withdrawn: bool = False,
) -> dict:
    value = {
        "rawQuestionId": raw_id,
        "sourceSnapshotId": f"snapshot-{raw_id}",
        "sourceId": "goukakudojyo",
        "externalQuestionId": raw_id,
        "examYear": 2025,
        "questionNumber": question_number,
        "listingKind": "regular",
        "task": {"kind": task, "prompt": "prompt", "confidence": "high"},
        "subjectId": "administrative_law",
        "subjectLabel": "行政法",
        "sourceBodySha256": "a" * 64,
        "sourceUrl": f"https://example.invalid/{raw_id}",
        "title": f"2025-{question_number}",
        "eraYear": "令和7年",
        "isWithdrawn": withdrawn,
        "extraction": {"status": "parsed", "warnings": []},
        "instructionText": "正しいものを選べ。",
        "questionText": "ア．命題A。 イ．命題B。 ウ．命題C。",
        "choices": [
            {"label": str(number), "text": f"命題{number}"}
            for number in range(1, 6)
        ],
        "answer": {"kind": "option", "value": 2},
    }
    if task == "combination":
        value["task"]["prompt"] = "正しいものをすべて挙げた組合せはどれか。"
        value["questionText"] = "ア．命題A。 イ．命題B。"
        value["choices"] = [
            {"label": str(number), "text": "ア・イ"}
            for number in range(1, 6)
        ]
        value["answer"] = {"kind": "option", "value": 1}
    elif task == "count":
        value["task"]["prompt"] = "正しいものはいくつあるか。"
        value["questionText"] = "ア．命題A。 イ．命題B。"
        value["choices"] = [
            {"label": "1", "text": "一つ"},
            {"label": "2", "text": "二つ"},
            {"label": "3", "text": "三つ"},
            {"label": "4", "text": "四つ"},
            {"label": "5", "text": "五つ"},
        ]
        value["answer"] = {"kind": "option", "value": 1}
    return value


def section(
    statement: str | None,
    verdict: str,
    explanation: str = "根拠の説明。",
) -> dict:
    return {
        "statementText": statement,
        "providerVerdict": verdict,
        "explanationParagraphs": [explanation] if explanation else [],
    }


def provider_item(source: dict, sections: list[dict], *, available: bool = True) -> dict:
    return {
        "rawQuestionId": source["rawQuestionId"],
        "examYear": source["examYear"],
        "questionNumber": source["questionNumber"],
        "format": source["listingKind"],
        "subjectId": source["subjectId"],
        "subjectLabel": source["subjectLabel"],
        "sourceBodySha256": source["sourceBodySha256"],
        "explanationAvailable": available,
        "sections": sections,
    }


def reference(items: list[dict]) -> dict:
    return {
        "schemaVersion": "provider-explanations@1",
        "items": items,
    }


class ExplanationOxTests(unittest.TestCase):
    def test_explicit_verdict_parser_is_anchored_and_fail_closed(self) -> None:
        self.assertEqual(
            parse_explicit_verdict("ア．正しい。"), ("ア", True, "正しい")
        )
        self.assertEqual(
            parse_explicit_verdict("２：妥当でない"), ("2", False, "妥当でない")
        )
        self.assertEqual(
            parse_explicit_verdict("ウ.誤っている。"), ("ウ", False, "誤っている")
        )
        self.assertIsNone(parse_explicit_verdict("イ、ウ．正しい。"))
        self.assertIsNone(parse_explicit_verdict("ア．対象となる"))
        self.assertIsNone(parse_explicit_verdict("ア：正しい、イ：誤り"))
        self.assertIsNone(parse_explicit_verdict("解説上は正しい"))

    def test_only_combination_and_count_create_new_ox_candidates(self) -> None:
        select = record("select", 1, "select_true")
        combo = record("combo", 2, "combination")
        combo["questionText"] = (
            "ア．命題A。 イ．命題B。 ウ．命題C。 エ．命題D。"
        )
        combo["choices"] = [
            {"label": "1", "text": "ア・イ"},
            {"label": "2", "text": "ア・ウ・エ"},
            {"label": "3", "text": "イ・ウ"},
            {"label": "4", "text": "イ・エ"},
            {"label": "5", "text": "ウ・エ"},
        ]
        combo["answer"]["value"] = 2
        count = record("count", 3, "count")
        withdrawn = record("withdrawn", 4, "combination", withdrawn=True)
        items = [
            provider_item(
                select,
                [
                    section(f"命題{number}", f"{number}．{'正しい' if number == 2 else '誤り'}。")
                    for number in range(1, 6)
                ],
            ),
            provider_item(
                combo,
                [
                    section("命題A。", "ア．正しい。"),
                    section("命題B。", "イ．誤り。"),
                    section("命題C。", "ウ、エ．正しい。"),
                ],
            ),
            provider_item(
                count,
                [
                    section("命題A。", "ア．妥当である。"),
                    section("命題B。", "イ．妥当でない。"),
                ],
            ),
            provider_item(
                withdrawn,
                [section("命題A。", "ア．正しい。")],
            ),
        ]

        inventory = build_inventory(
            [select, combo, count, withdrawn],
            reference(items),
            generated_at="2026-07-26T00:00:00Z",
        )

        self.assertEqual(inventory["summary"]["additionalCandidateCount"], 4)
        self.assertEqual(inventory["summary"]["additionalSourceQuestionCount"], 2)
        self.assertEqual(inventory["summary"]["corroborationCount"], 5)
        self.assertEqual(inventory["summary"]["editorialMappingCount"], 1)
        self.assertEqual(
            inventory["summary"]["targetSourceAnswerCrossCheckCount"], 2
        )
        self.assertTrue(
            all(
                item["sourceAnswerCrossCheck"]["result"] == "matched"
                for item in inventory["candidates"]
            )
        )
        self.assertEqual(
            {
                candidate["statementLabel"]: candidate["inferredTruth"]
                for candidate in inventory["candidates"]
                if candidate["rawQuestionId"] == "combo"
            },
            {"ア": True, "イ": False},
        )
        self.assertTrue(
            all(candidate["oxEligible"] for candidate in inventory["candidates"])
        )
        self.assertTrue(
            all(not candidate["publishable"] for candidate in inventory["candidates"])
        )
        self.assertTrue(
            all(
                candidate["frequencyEligible"] is False
                for candidate in inventory["candidates"]
            )
        )
        self.assertFalse(inventory["policy"]["autoFrequency"])
        self.assertEqual(
            inventory["editorialMappings"][0]["decisionReason"],
            "provider_heading_not_single_explicit_verdict",
        )
        withdrawn_queue = next(
            item
            for item in inventory["questionQueue"]
            if item["rawQuestionId"] == "withdrawn"
        )
        self.assertEqual(withdrawn_queue["reasons"], ["withdrawn_question_excluded"])

    def test_select_truth_conflict_aborts_the_whole_build(self) -> None:
        select = record("select", 1, "select_true")
        item = provider_item(
            select,
            [section("命題1", "1．正しい。")],
        )
        with self.assertRaisesRegex(
            ExplanationOxError, "provider verdict conflicts"
        ):
            build_inventory([select], reference([item]))

    def test_select_heading_can_corroborate_when_paragraph_is_empty(self) -> None:
        select = record("select", 1, "select_true")
        item = provider_item(
            select,
            [section("命題2", "2．正しい。", explanation="")],
        )

        inventory = build_inventory([select], reference([item]))

        self.assertEqual(inventory["summary"]["corroborationCount"], 1)
        self.assertEqual(inventory["summary"]["editorialMappingCount"], 0)

    def test_source_join_mismatch_aborts_the_whole_build(self) -> None:
        combo = record("combo", 1, "combination")
        item = provider_item(combo, [section("命題A", "ア．正しい。")])
        item["sourceBodySha256"] = "b" * 64
        with self.assertRaisesRegex(ExplanationOxError, "source mismatch"):
            build_inventory([combo], reference([item]))

    def test_raw_id_set_must_match_exactly(self) -> None:
        combo = record("combo", 1, "combination")
        with self.assertRaisesRegex(ExplanationOxError, "rawQuestionId sets differ"):
            build_inventory([combo], reference([]))

    def test_cli_writes_private_atomic_artifacts(self) -> None:
        combo = record("combo", 1, "combination")
        item = provider_item(
            combo,
            [
                section("命題A。", "ア．正しい。"),
                section("命題B。", "イ．正しい。"),
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extracted = root / "extracted"
            extracted.mkdir()
            (extracted / "combo.json").write_text(
                json.dumps(combo, ensure_ascii=False),
                encoding="utf-8",
            )
            explanations = root / "provider.json"
            explanations.write_text(
                json.dumps(reference([item]), ensure_ascii=False),
                encoding="utf-8",
            )
            output = root / "out" / "candidates.json"
            validation = root / "out" / "validation.json"
            manifest = root / "out" / "manifest.json"

            status_code = main(
                [
                    "--input",
                    str(extracted),
                    "--explanations",
                    str(explanations),
                    "--output",
                    str(output),
                    "--validation-output",
                    str(validation),
                    "--manifest-output",
                    str(manifest),
                    "--expected-count",
                    "1",
                ]
            )

            self.assertEqual(status_code, 0)
            for path in (output, validation, manifest):
                self.assertTrue(path.is_file())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8"))["schemaVersion"],
                "explanation-derived-ox@1",
            )
            self.assertTrue(
                json.loads(validation.read_text(encoding="utf-8"))["passed"]
            )
            self.assertFalse(
                json.loads(validation.read_text(encoding="utf-8"))["checks"][
                    "autoFrequency"
                ]
            )
            self.assertEqual(
                len(json.loads(manifest.read_text(encoding="utf-8"))["artifacts"]),
                2,
            )
            self.assertEqual(list(output.parent.glob("*.tmp")), [])

    def test_combination_answer_conflict_aborts_the_whole_build(self) -> None:
        combo = record("combo", 1, "combination")
        combo["choices"][0]["text"] = "ア・イ"
        item = provider_item(
            combo,
            [
                section("命題A。", "ア．正しい。"),
                section("命題B。", "イ．誤り。"),
            ],
        )

        with self.assertRaisesRegex(
            ExplanationOxError, "provider verdict labels conflict"
        ):
            build_inventory([combo], reference([item]))

    def test_statementless_summary_does_not_create_false_review_queue(self) -> None:
        combo = record("combo", 1, "combination")
        item = provider_item(
            combo,
            [
                section("命題A。", "ア．正しい。"),
                section("命題B。", "イ．正しい。"),
                section(None, "ア、イが正しい"),
            ],
        )

        inventory = build_inventory([combo], reference([item]))

        self.assertEqual(inventory["questionQueue"], [])
        self.assertEqual(
            inventory["summary"]["ignoredStatementlessSectionCount"], 1
        )

    def test_extraction_warning_keeps_question_out_of_candidates(self) -> None:
        combo = record("combo", 1, "combination")
        combo["extraction"]["warnings"] = ["unexpected"]
        item = provider_item(combo, [section("命題A。", "ア．正しい。")])

        inventory = build_inventory([combo], reference([item]))

        self.assertEqual(inventory["candidates"], [])
        self.assertEqual(
            inventory["questionQueue"][0]["reasons"],
            ["extraction_not_clean_for_ox_derivation"],
        )

    def test_duplicate_provider_id_is_rejected(self) -> None:
        combo = record("combo", 1, "combination")
        item = provider_item(combo, [section("命題A。", "ア．正しい。")])
        with self.assertRaisesRegex(ExplanationOxError, "duplicate rawQuestionId"):
            build_inventory(
                [combo],
                reference([item, copy.deepcopy(item)]),
            )


if __name__ == "__main__":
    unittest.main()
