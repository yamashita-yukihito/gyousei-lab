from __future__ import annotations

import gzip
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gyousei_pipeline.provider_explanations import (
    ProviderExplanationError,
    build_reference,
    parse_explanation_html,
)


class ProviderExplanationTests(unittest.TestCase):
    def test_splits_choice_verdicts_and_reasons(self) -> None:
        parsed = parse_explanation_html(
            b"""
            <div class="que-kai"><div class="kaisetsu">
              <p>Introduction</p>
              <p class="answer"><span class="q-txt">Statement one</span>1. False</p>
              <p>Reason one</p><p>Reason two</p>
              <p class="answer"><span class="q-txt">Statement two</span>2. True</p>
              <p>Reason three</p>
            </div></div>
            """
        )
        self.assertEqual(["Introduction"], parsed["prefaceParagraphs"])
        self.assertEqual(2, len(parsed["sections"]))
        self.assertEqual("Statement one", parsed["sections"][0]["statementText"])
        self.assertEqual("1. False", parsed["sections"][0]["providerVerdict"])
        self.assertEqual(
            ["Reason one", "Reason two"],
            parsed["sections"][0]["explanationParagraphs"],
        )

    def test_rejects_page_without_explanation(self) -> None:
        with self.assertRaisesRegex(ProviderExplanationError, "container"):
            parse_explanation_html(b"<main>No explanation</main>")

    def test_keeps_sections_from_malformed_nested_paragraphs(self) -> None:
        parsed = parse_explanation_html(
            b"""
            <div class="que-kai"><div class="kaisetsu">
              <p class="answer"><span class="q-txt">One</span>1. True</p>
              <p>First reason
                <p class="answer"><span class="q-txt">Two</span>2. False</p>
                <p>Second reason</p>
              </p>
            </div></div>
            """
        )
        self.assertEqual(2, len(parsed["sections"]))
        self.assertEqual(["First reason"], parsed["sections"][0]["explanationParagraphs"])
        self.assertEqual(["Second reason"], parsed["sections"][1]["explanationParagraphs"])

    def test_records_expected_unavailable_explanation_without_hiding_unexpected_loss(
        self,
    ) -> None:
        html = b"<main>No provider explanation is published</main>"
        digest = hashlib.sha256(html).hexdigest()
        record = {
            "rawQuestionId": "goukakudojyo_archive:1",
            "externalQuestionId": "1",
            "examYear": 2006,
            "questionNumber": 1,
            "listingKind": "regular",
            "sourceUrl": "https://example.test/question/1",
            "providerUpdatedAt": None,
            "sourceBodySha256": digest,
            "subjectId": "legal_foundations",
            "subjectLabel": "基礎法学",
            "explanationExpected": False,
            "historicalUse": "frequency_only",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            blob = (
                root
                / "raw"
                / "blobs"
                / "sha256"
                / digest[:2]
                / f"{digest}.html.gz"
            )
            blob.parent.mkdir(parents=True)
            with gzip.open(blob, "wb") as output:
                output.write(html)
            with patch.dict(os.environ, {"GYOUSEI_DATA_ROOT": str(root)}):
                result = build_reference(
                    [record],
                    expected_count=1,
                    allow_expected_missing=True,
                )
                self.assertEqual(result["summary"]["availableCount"], 0)
                self.assertEqual(result["summary"]["missingCount"], 1)
                self.assertFalse(result["items"][0]["explanationAvailable"])
                self.assertEqual(
                    result["items"][0]["missingReason"],
                    "provider_explanation_not_published",
                )

                unexpected = dict(record, explanationExpected=True)
                with self.assertRaisesRegex(
                    ProviderExplanationError,
                    "provider explanation container is missing",
                ):
                    build_reference(
                        [unexpected],
                        expected_count=1,
                        allow_expected_missing=True,
                    )


if __name__ == "__main__":
    unittest.main()
