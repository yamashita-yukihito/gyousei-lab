"""Normalize provider explanations into a private editorial reference.

The normalized file is deliberately kept outside the web root.  It is an
editing source for answer truth and concise original summaries; it is not a
payload that the production bundle may publish wholesale.
"""

from __future__ import annotations

import argparse
import gzip
import os
from pathlib import Path
from typing import Any, Iterable

from bs4 import BeautifulSoup, Tag

from .common import atomic_write_json, data_root, load_json, normalize_text, utc_now


SCHEMA_VERSION = "provider-explanations@1"
DEFAULT_INPUT = data_root() / "extracted"
DEFAULT_OUTPUT = data_root() / "curation" / "provider_explanations.json"


class ProviderExplanationError(ValueError):
    """The saved provider page cannot satisfy the editorial contract."""


def _node_text(node: Tag) -> str:
    return normalize_text(node.get_text(" ", strip=True))


def _paragraph_text(node: Tag) -> str:
    """Read one paragraph without duplicating malformed nested paragraphs."""

    fragment = BeautifulSoup(str(node), "html.parser").find("p")
    if fragment is None:
        return ""
    for nested in fragment.find_all("p"):
        nested.decompose()
    return _node_text(fragment)


def parse_explanation_html(html: bytes) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one(".que-kai .kaisetsu, .kaisetsu-wrap .kaisetsu")
    if container is None:
        raise ProviderExplanationError("provider explanation container is missing")

    # One historical page contains malformed nested <p> tags.  Walking all
    # paragraphs and reading only each paragraph's own text preserves all five
    # choice sections without duplicating the nested content.
    paragraphs = list(container.find_all("p"))
    if not paragraphs:
        raise ProviderExplanationError("provider explanation has no paragraphs")

    preface: list[str] = []
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for paragraph in paragraphs:
        text = _paragraph_text(paragraph)
        if not text:
            continue
        if "answer" in (paragraph.get("class") or []):
            statement_node = paragraph.select_one(".q-txt")
            statement = _node_text(statement_node) if statement_node else ""
            heading = text
            if statement and heading.startswith(statement):
                heading = heading[len(statement) :].strip()
            current = {
                "statementText": statement or None,
                "providerVerdict": heading,
                "explanationParagraphs": [],
            }
            sections.append(current)
            continue
        if current is None:
            preface.append(text)
        else:
            current["explanationParagraphs"].append(text)

    return {
        "prefaceParagraphs": preface,
        "sections": sections,
        "fullText": "\n\n".join(
            text for node in paragraphs if (text := _paragraph_text(node))
        ),
    }


def _documents(path: Path) -> Iterable[dict[str, Any]]:
    for document_path in sorted(path.rglob("*.json")):
        value = load_json(document_path)
        if isinstance(value, dict):
            yield value


def build_reference(
    records: Iterable[dict[str, Any]],
    *,
    expected_count: int = 220,
    allow_expected_missing: bool = False,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for record in records:
        digest = record.get("sourceBodySha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ProviderExplanationError("record has an invalid sourceBodySha256")
        blob = data_root() / "raw" / "blobs" / "sha256" / digest[:2] / f"{digest}.html.gz"
        if not blob.is_file():
            raise ProviderExplanationError(f"provider snapshot is missing: {record.get('rawQuestionId')}")
        with gzip.open(blob, "rb") as source:
            html = source.read()

        explanation_expected = record.get("explanationExpected", True)
        if type(explanation_expected) is not bool:
            raise ProviderExplanationError(
                f"invalid explanationExpected: {record.get('rawQuestionId')}"
            )
        try:
            parsed = parse_explanation_html(html)
        except ProviderExplanationError as error:
            if explanation_expected or not allow_expected_missing:
                raise ProviderExplanationError(
                    f"{record.get('rawQuestionId')}: {error}"
                ) from error
            parsed = {
                "prefaceParagraphs": [],
                "sections": [],
                "fullText": "",
            }
            explanation_available = False
            missing_reason = "provider_explanation_not_published"
        else:
            explanation_available = True
            missing_reason = None

        item = {
            "rawQuestionId": record["rawQuestionId"],
            "externalQuestionId": record["externalQuestionId"],
            "examYear": record["examYear"],
            "questionNumber": record["questionNumber"],
            "format": record["listingKind"],
            "sourceUrl": record["sourceUrl"],
            "providerUpdatedAt": record["providerUpdatedAt"],
            "sourceBodySha256": digest,
            "explanationExpected": explanation_expected,
            "explanationAvailable": explanation_available,
            "missingReason": missing_reason,
            **parsed,
        }
        for key in ("subjectId", "subjectLabel", "historicalUse"):
            if key in record:
                item[key] = record[key]
        items.append(item)
    items.sort(key=lambda item: (item["examYear"], item["questionNumber"]))
    if len(items) != expected_count:
        raise ProviderExplanationError(
            f"expected {expected_count} provider records, got {len(items)}"
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "visibility": "private_editorial_reference",
        "policy": {
            "answerAuthority": "provider_result_and_explanation",
            "webPublication": "never_copy_wholesale",
            "normalExplanation": "concise_original_paraphrase",
        },
        "summary": {
            "questionCount": len(items),
            "availableCount": sum(item["explanationAvailable"] for item in items),
            "missingCount": sum(not item["explanationAvailable"] for item in items),
            "sectionCount": sum(len(item["sections"]) for item in items),
            "nonEmptyCount": sum(bool(item["fullText"]) for item in items),
        },
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--expected-count", type=int, default=220)
    parser.add_argument(
        "--allow-expected-missing",
        action="store_true",
        help=(
            "store an explicit missing marker only when a record has "
            "explanationExpected=false"
        ),
    )
    args = parser.parse_args()
    if args.expected_count < 0:
        parser.error("--expected-count must be non-negative")
    result = build_reference(
        _documents(args.input),
        expected_count=args.expected_count,
        allow_expected_missing=args.allow_expected_missing,
    )
    atomic_write_json(args.output, result)
    os.chmod(args.output, 0o600)
    print(
        f"wrote {result['summary']['questionCount']} provider records "
        f"({result['summary']['availableCount']} explanations, "
        f"{result['summary']['missingCount']} unavailable, "
        f"{result['summary']['sectionCount']} sections) to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
