from __future__ import annotations

import copy
import json
import stat
import tempfile
import unittest
from pathlib import Path

from gyousei_pipeline.mapping_ox import MappingOxError, build_inventory, main


DIGEST = "a" * 64


def record(
    raw_id: str,
    *,
    choices: list[dict],
    answer: int,
    question_text: str,
) -> dict:
    return {
        "rawQuestionId": raw_id,
        "sourceSnapshotId": f"snapshot-{raw_id}",
        "sourceId": "goukakudojyo",
        "externalQuestionId": raw_id,
        "sourceBodySha256": DIGEST,
        "examYear": 2025,
        "questionNumber": 1,
        "subjectId": "administrative_law",
        "subjectLabel": "行政法",
        "listingKind": "regular",
        "task": {"kind": "combination", "confidence": "high"},
        "instructionText": "組合せを選べ。",
        "questionText": question_text,
        "choices": choices,
        "answer": {"kind": "option", "value": answer},
    }


def mapping(
    source: dict,
    index: int,
    label: str,
    statement: str,
    verdict: str,
) -> dict:
    return {
        "mappingId": f"{source['rawQuestionId']}:provider-mapping:{index}",
        "candidateKind": "provider_editorial_mapping",
        "rawQuestionId": source["rawQuestionId"],
        "sourceSnapshotId": source["sourceSnapshotId"],
        "sourceBodySha256": source["sourceBodySha256"],
        "examYear": source["examYear"],
        "questionNumber": source["questionNumber"],
        "subjectId": source["subjectId"],
        "subjectLabel": source["subjectLabel"],
        "format": source["listingKind"],
        "task": "combination",
        "sectionIndex": index,
        "statementText": statement,
        "providerVerdict": verdict,
        "providerExplanationParagraphs": ["根拠。"],
        "questionContext": {
            "instructionText": source["instructionText"],
            "questionText": source["questionText"],
        },
        "sourceCitation": {
            "sourceId": "goukakudojyo",
            "sourceUrl": "https://example.invalid/question",
            "sourceBodySha256": DIGEST,
        },
        "reviewed": False,
        "publishable": False,
    }


def base(mappings: list[dict], candidates: list[dict] | None = None) -> dict:
    return {
        "schemaVersion": "explanation-derived-ox@1",
        "summary": {"additionalCandidatesBySubject": {}},
        "candidates": candidates or [],
        "editorialMappings": mappings,
    }


def output_rule(source: dict, item: dict, label: str, statement: str) -> dict:
    return {
        "mappingId": item["mappingId"],
        "statementLabel": label,
        "expectedProviderVerdict": item["providerVerdict"],
        "expectedMappingStatementText": item["statementText"],
        "sourceStatementText": item["statementText"],
        "statementText": statement,
        "compositionKind": "test_completion",
    }


class MappingOxTests(unittest.TestCase):
    def test_selected_labels_derive_truth_and_reconstruct_answer(self) -> None:
        source = record(
            "question",
            choices=[
                {"label": "1", "text": "ア・ウ"},
                {"label": "2", "text": "ア・イ"},
                {"label": "3", "text": "イ・ウ"},
                {"label": "4", "text": "ア・エ"},
                {"label": "5", "text": "ウ・エ"},
            ],
            answer=1,
            question_text="ア．行為A。 イ．行為B。 ウ．行為C。 エ．行為D。",
        )
        items = [
            mapping(source, 0, "ア", "行為A。", "ア．努力義務"),
            mapping(source, 1, "イ", "行為B。", "イ．法的義務"),
            mapping(source, 2, "ウ", "行為C。", "ウ．努力義務"),
            mapping(source, 3, "エ", "行為D。", "エ．法的義務"),
        ]
        rules = {
            "schemaVersion": "explanation-mapping-ox-rules@1",
            "groups": [
                {
                    "rawQuestionId": "question",
                    "expectedSourceBodySha256": DIGEST,
                    "expectedAnswerOption": 1,
                    "answerCrossCheck": {
                        "kind": "selected_labels",
                        "selectedTruth": True,
                        "truthByLabel": {
                            "ア": True,
                            "イ": False,
                            "ウ": True,
                            "エ": False,
                        },
                    },
                    "outputs": [
                        output_rule(
                            source,
                            item,
                            label,
                            f"{item['statementText']}は努力義務である。",
                        )
                        for item, label in zip(items, "アイウエ")
                    ],
                }
            ],
            "excludedMappings": [],
        }

        inventory = build_inventory([source], base(items), rules)

        self.assertEqual(inventory["summary"]["additionalCandidateCount"], 4)
        self.assertEqual(
            {
                candidate["statementLabel"]: candidate["inferredTruth"]
                for candidate in inventory["candidates"]
            },
            {"ア": True, "イ": False, "ウ": True, "エ": False},
        )
        self.assertEqual(
            inventory["summary"]["answerCrossChecksByMethod"],
            {"selected_labels": 1},
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

    def test_selected_cells_require_exact_saved_classification(self) -> None:
        source = record(
            "definitions",
            choices=[
                {
                    "label": "1",
                    "text": "ア：自然法",
                    "cells": [{"column": "ア", "text": "自然法"}],
                }
            ],
            answer=1,
            question_text="ア．普遍的に妥当する法があるとする考え方",
        )
        item = mapping(
            source,
            0,
            "ア",
            "普遍的に妥当する法があるとする考え方",
            "ア．自然法",
        )
        rule = output_rule(
            source,
            item,
            "ア",
            "普遍的に妥当する法があるとする考え方は、自然法である。",
        )
        rule["classificationValue"] = "自然法"
        rules = {
            "schemaVersion": "explanation-mapping-ox-rules@1",
            "groups": [
                {
                    "rawQuestionId": "definitions",
                    "expectedSourceBodySha256": DIGEST,
                    "expectedAnswerOption": 1,
                    "answerCrossCheck": {
                        "kind": "selected_cells",
                        "valueByLabel": {"ア": "自然法"},
                    },
                    "outputs": [rule],
                }
            ],
            "excludedMappings": [],
        }

        inventory = build_inventory([source], base([item]), rules)

        self.assertTrue(inventory["candidates"][0]["inferredTruth"])
        self.assertEqual(
            inventory["candidates"][0]["classificationValue"], "自然法"
        )

    def test_source_or_answer_drift_fails_closed(self) -> None:
        source = record(
            "question",
            choices=[
                {"label": "1", "text": "ア・イ"},
                {"label": "2", "text": "ア・ウ"},
            ],
            answer=1,
            question_text="ア．A。 イ．B。 ウ．C。",
        )
        items = [
            mapping(source, 0, "ア", "A。", "ア．対象"),
            mapping(source, 1, "イ", "B。", "イ．対象"),
            mapping(source, 2, "ウ", "C。", "ウ．対象外"),
        ]
        rules = {
            "schemaVersion": "explanation-mapping-ox-rules@1",
            "groups": [
                {
                    "rawQuestionId": "question",
                    "expectedSourceBodySha256": DIGEST,
                    "expectedAnswerOption": 1,
                    "answerCrossCheck": {
                        "kind": "selected_labels",
                        "selectedTruth": True,
                        "truthByLabel": {
                            "ア": True,
                            "イ": False,
                            "ウ": True,
                        },
                    },
                    "outputs": [
                        output_rule(source, item, label, f"{item['statementText']}対象。")
                        for item, label in zip(items, "アイウ")
                    ],
                }
            ],
            "excludedMappings": [],
        }

        with self.assertRaisesRegex(MappingOxError, "conflicts with saved answer"):
            build_inventory([source], base(items), rules)

        corrected = copy.deepcopy(rules)
        corrected["groups"][0]["answerCrossCheck"]["truthByLabel"] = {
            "ア": True,
            "イ": True,
            "ウ": False,
        }
        corrected["groups"][0]["outputs"][0]["expectedProviderVerdict"] = "drift"
        with self.assertRaisesRegex(MappingOxError, "provider verdict drift"):
            build_inventory([source], base(items), corrected)

    def test_cli_writes_private_atomic_artifacts(self) -> None:
        source = record(
            "definitions",
            choices=[
                {
                    "label": "1",
                    "text": "ア：自然法",
                    "cells": [{"column": "ア", "text": "自然法"}],
                }
            ],
            answer=1,
            question_text="ア．定義",
        )
        item = mapping(source, 0, "ア", "定義", "ア．自然法")
        rule = output_rule(source, item, "ア", "この定義は自然法である。")
        rule["classificationValue"] = "自然法"
        rules = {
            "schemaVersion": "explanation-mapping-ox-rules@1",
            "groups": [
                {
                    "rawQuestionId": "definitions",
                    "expectedSourceBodySha256": DIGEST,
                    "expectedAnswerOption": 1,
                    "answerCrossCheck": {
                        "kind": "selected_cells",
                        "valueByLabel": {"ア": "自然法"},
                    },
                    "outputs": [rule],
                }
            ],
            "excludedMappings": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            base_path = root / "base.json"
            rules_path = root / "rules.json"
            output_path = root / "out" / "candidates.json"
            validation_path = root / "out" / "validation.json"
            manifest_path = root / "out" / "manifest.json"
            input_path.write_text(
                json.dumps(source, ensure_ascii=False), encoding="utf-8"
            )
            base_path.write_text(
                json.dumps(base([item]), ensure_ascii=False), encoding="utf-8"
            )
            rules_path.write_text(
                json.dumps(rules, ensure_ascii=False), encoding="utf-8"
            )

            status = main(
                [
                    "--input",
                    str(input_path),
                    "--base-candidates",
                    str(base_path),
                    "--rules",
                    str(rules_path),
                    "--output",
                    str(output_path),
                    "--validation-output",
                    str(validation_path),
                    "--manifest-output",
                    str(manifest_path),
                    "--expected-count",
                    "1",
                    "--expected-base-candidate-count",
                    "0",
                    "--expected-base-mapping-count",
                    "1",
                    "--expected-candidate-count",
                    "1",
                    "--expected-remaining-mapping-count",
                    "0",
                ]
            )

            self.assertEqual(status, 0)
            for path in (output_path, validation_path, manifest_path):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertTrue(
                json.loads(validation_path.read_text(encoding="utf-8"))["passed"]
            )
            self.assertFalse(
                json.loads(validation_path.read_text(encoding="utf-8"))["checks"][
                    "autoFrequency"
                ]
            )
            self.assertEqual(list(output_path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
