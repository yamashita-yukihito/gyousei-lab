from __future__ import annotations

import unittest

from gyousei_pipeline.subjects import canonical_subject_id, subject_label


class SubjectNormalizationTests(unittest.TestCase):
    def test_raw_provider_ids_map_to_public_ids(self) -> None:
        expected = {
            "legal_foundations": "legal-foundations",
            "constitutional_law": "constitutional-law",
            "administrative_law": "administrative-law",
            "civil_law": "civil-law",
            "commercial_law": "commercial-law",
            "basic_knowledge": "general-knowledge",
        }
        for raw_id, public_id in expected.items():
            with self.subTest(raw_id=raw_id):
                self.assertEqual(public_id, canonical_subject_id(raw_id))

    def test_company_law_is_unified_with_commercial_law(self) -> None:
        self.assertEqual("commercial-law", canonical_subject_id("会社法"))
        self.assertEqual("commercial-law", canonical_subject_id("company_law"))
        self.assertEqual("商法・会社法", subject_label("commercial_law"))


if __name__ == "__main__":
    unittest.main()
