from __future__ import annotations

import copy
import unittest

from gyousei_pipeline.archive_frequency import ArchiveFrequencyError, build_corpus


def record(year: int, number: int, kind: str) -> dict:
    base = {
        "schemaVersion": "raw-question@1", "rawQuestionId": f"archive:{year}:{number}",
        "endpointType": "archive", "examYear": year, "eraYear": str(year),
        "questionNumber": number, "title": f"{year}-{number}", "labels": ["行政法"],
        "listingKind": kind, "isAmended": False, "sourceUrl": "https://example.test/q",
        "sourceBodySha256": "a" * 64, "extraction": {"status": "parsed", "warnings": []},
    }
    if kind == "regular":
        base.update({"instructionText": "正しいものはどれか。", "questionText": "問題", "choices": [{"label": "1", "text": "肢"}], "choiceFormat": "list", "choiceColumns": [], "task": {"kind": "select_true"}, "answer": {"kind": "option", "value": 1}})
    elif kind == "multiple_blank":
        base.update({"instructionText": "空欄", "passageText": "本文", "sourceNote": "", "blanks": ["ア", "イ", "ウ", "エ"], "wordBank": [{"number": 1, "text": "語"}], "task": {"kind": "fill_four_blanks"}, "answer": {"kind": "blank_numbers", "values": {"ア": 1, "イ": 2, "ウ": 3, "エ": 4}}})
    else:
        base.update({"questionText": "40字程度", "referenceText": "", "characterLimit": 40, "characterLimitKind": "approximately", "modelAnswer": "答え", "task": {"kind": "written_response"}, "answer": {"kind": "model_answer", "value": "答え"}})
    return base


def complete_records() -> list[dict]:
    values = []
    for year in range(2006, 2016):
        values.extend(record(year, number, "regular") for number in range(8, 27))
        values.extend(record(year, number, "multiple_blank") for number in (42, 43))
        values.append(record(year, 44, "written"))
    return values


class ArchiveFrequencyTests(unittest.TestCase):
    def test_builds_complete_private_frequency_corpus(self) -> None:
        corpus = build_corpus(complete_records())
        self.assertEqual(220, corpus["summary"]["questionCount"])
        self.assertTrue(all(value == 22 for value in corpus["summary"]["yearCounts"].values()))
        self.assertFalse(corpus["policy"]["showInRelatedPastQuestions"])
        self.assertTrue(corpus["policy"]["answerStored"])

    def test_missing_question_or_answer_fails_closed(self) -> None:
        with self.assertRaisesRegex(ArchiveFrequencyError, "expected 220"):
            build_corpus(complete_records()[:-1])
        values = complete_records()
        values[0] = copy.deepcopy(values[0])
        values[0]["answer"]["value"] = None
        with self.assertRaisesRegex(ArchiveFrequencyError, "answer is missing"):
            build_corpus(values)


if __name__ == "__main__":
    unittest.main()
