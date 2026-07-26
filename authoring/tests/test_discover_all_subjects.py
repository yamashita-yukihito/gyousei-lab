from __future__ import annotations

import unittest

from gyousei_pipeline.discover_all_subjects import (
    AllSubjectsDiscoveryError,
    build_catalog,
)


def config() -> dict:
    return {
        "source": {
            "id": "goukakudojyo",
            "baseUrl": "https://www.pro.goukakudojyo.com",
            "yearUrlTemplate": (
                "https://www.pro.goukakudojyo.com/worksheet2/"
                "w_subcatnendo.php?nendoID={nendoId}"
            ),
        },
        "target": {
            "examYears": [2025],
            "expectedTotal": 3,
            "defaultQuestionNumberRanges": [[1, 3]],
            "questionNumberRangeOverrides": {},
            "listingKindRules": [
                {"kind": "regular", "ranges": [[1, 2]]},
                {"kind": "multiple_blank", "ranges": [[3, 3]]},
            ],
            "subjectRules": [
                {
                    "subjectId": "legal_foundations",
                    "subjectLabel": "基礎法学",
                    "ranges": [[1, 1]],
                },
                {
                    "subjectId": "constitutional_law",
                    "subjectLabel": "憲法",
                    "ranges": [[2, 3]],
                },
            ],
            "expectedByFormat": {"regular": 2, "multiple_blank": 1},
            "expectedBySubject": {
                "legal_foundations": 1,
                "constitutional_law": 2,
            },
            "expectedExplanations": {"available": 2, "unavailable": 1},
            "historicalUse": "current_editorial_reference",
        },
        "years": [{"examYear": 2025, "eraYear": "令和7年", "nendoId": 42}],
    }


def page(*, omit: int | None = None, duplicate: int | None = None) -> bytes:
    links = [
        (
            1,
            "w_mainnendo.php?queID=101",
            '<span class="connect-txt">基礎法学</span>',
        ),
        (
            2,
            "w_mainarch.php?queID=102",
            '<span class="connect-txt">憲法</span>',
        ),
        (
            3,
            "w_mainnendo.php?queID=103",
            (
                '<span class="connect-txt">多肢選択式</span>'
                '<span class="connect-txt">憲法</span>'
            ),
        ),
    ]
    rendered = []
    for number, href, labels in links:
        if number == omit:
            continue
        rendered.append(
            f'<a href="{href}">令和7年－問{number}{labels}</a>'
        )
        if number == duplicate:
            rendered.append(
                f'<a href="{href}">令和7年－問{number}{labels}</a>'
            )
    return ("<main>" + "".join(rendered) + "</main>").encode()


class AllSubjectsDiscoveryTests(unittest.TestCase):
    def test_observed_links_receive_format_subject_and_explanation_metadata(
        self,
    ) -> None:
        catalog = build_catalog({2025: page()}, config())
        self.assertEqual(3, len(catalog["entries"]))
        first, second, third = catalog["entries"]
        self.assertEqual(first["subjectId"], "legal_foundations")
        self.assertEqual(first["listingKind"], "regular")
        self.assertTrue(first["explanationExpected"])
        self.assertEqual(second["endpointType"], "archive")
        self.assertFalse(second["explanationExpected"])
        self.assertEqual(third["listingKind"], "multiple_blank")
        self.assertEqual(third["subjectId"], "constitutional_law")

    def test_missing_or_duplicate_question_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            AllSubjectsDiscoveryError,
            "question numbers",
        ):
            build_catalog({2025: page(omit=2)}, config())
        with self.assertRaisesRegex(
            AllSubjectsDiscoveryError,
            "duplicate observed question",
        ):
            build_catalog({2025: page(duplicate=2)}, config())


if __name__ == "__main__":
    unittest.main()
