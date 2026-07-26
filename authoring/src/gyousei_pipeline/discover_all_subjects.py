"""Discover every observed subject from the provider's year indexes.

This module is intentionally separate from ``discover.py``.  The mature
administrative-law catalog keeps its original closed contract, while this
catalog covers all question numbers that are actually linked by the provider.
No question URL or provider ID is synthesized.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .common import (
    atomic_write_json,
    data_root,
    load_json,
    normalize_text,
    sha256_bytes,
    store_gzip_blob,
    utc_now,
)
from .fetch import SerialFetcher


CATALOG_SCHEMA = "catalog@1"
ENDPOINTS = {
    "/worksheet2/w_mainnendo.php": "regular",
    "/worksheet2/w_mainarch.php": "archive",
}


class AllSubjectsDiscoveryError(RuntimeError):
    """Observed provider indexes do not satisfy the configured closed set."""


def _expand_ranges(ranges: Any, *, label: str) -> set[int]:
    if not isinstance(ranges, list):
        raise AllSubjectsDiscoveryError(f"{label} must be a list")
    values: set[int] = set()
    for item in ranges:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or type(item[0]) is not int
            or type(item[1]) is not int
            or item[0] < 1
            or item[1] < item[0]
        ):
            raise AllSubjectsDiscoveryError(f"invalid range in {label}: {item!r}")
        values.update(range(item[0], item[1] + 1))
    return values


def _expected_numbers(target: Mapping[str, Any], exam_year: int) -> set[int]:
    overrides = target.get("questionNumberRangeOverrides", {})
    if not isinstance(overrides, dict):
        raise AllSubjectsDiscoveryError("questionNumberRangeOverrides must be an object")
    ranges = overrides.get(str(exam_year), target.get("defaultQuestionNumberRanges"))
    return _expand_ranges(ranges, label=f"question numbers for {exam_year}")


def _rule_for_number(
    rules: Any,
    question_number: int,
    *,
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(rules, list):
        raise AllSubjectsDiscoveryError(f"{label} must be a list")
    matches = [
        rule
        for rule in rules
        if isinstance(rule, dict)
        and question_number
        in _expand_ranges(rule.get("ranges"), label=f"{label}.ranges")
    ]
    if len(matches) != 1:
        raise AllSubjectsDiscoveryError(
            f"question {question_number} matches {len(matches)} {label} rules"
        )
    return matches[0]


def _link_title(anchor: Any, era_year: str) -> tuple[str, int, bool]:
    text = normalize_text(anchor.get_text(" ", strip=True))
    matched = re.search(
        rf"{re.escape(era_year)}\s*[－-]\s*問\s*([0-9]+)\s*(改題)?",
        text,
    )
    if not matched:
        raise AllSubjectsDiscoveryError(f"could not parse question title: {text}")
    question_number = int(matched.group(1))
    amended = bool(matched.group(2))
    title = f"{era_year}－問{question_number}" + ("改題" if amended else "")
    return title, question_number, amended


def parse_year_index(
    html: bytes | str,
    *,
    year: Mapping[str, Any],
    config: Mapping[str, Any],
    year_index_url: str | None = None,
) -> list[dict[str, Any]]:
    source = config["source"]
    target = config["target"]
    base_url = str(source["baseUrl"])
    expected_host = urlparse(base_url).netloc
    configured_url = str(source["yearUrlTemplate"]).format(nendoId=year["nendoId"])
    year_url = year_index_url or configured_url
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    selector = (
        'a[href*="w_mainnendo.php?queID="], '
        'a[href*="w_mainarch.php?queID="]'
    )
    for anchor in soup.select(selector):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        absolute_url = urljoin(year_url, href)
        parsed = urlparse(absolute_url)
        endpoint_type = ENDPOINTS.get(parsed.path)
        if parsed.netloc != expected_host or endpoint_type is None:
            raise AllSubjectsDiscoveryError(
                f"unexpected question URL in {year_url}: {absolute_url}"
            )
        identifiers = parse_qs(parsed.query).get("queID", [])
        if len(identifiers) != 1 or not identifiers[0].isdigit():
            raise AllSubjectsDiscoveryError(f"invalid observed queID: {href}")
        external_id = identifiers[0]
        title, question_number, amended = _link_title(
            anchor,
            str(year["eraYear"]),
        )
        kind_rule = _rule_for_number(
            target["listingKindRules"],
            question_number,
            label="listingKindRules",
        )
        subject_rule = _rule_for_number(
            target["subjectRules"],
            question_number,
            label="subjectRules",
        )
        labels = [
            normalize_text(node.get_text(" ", strip=True))
            for node in anchor.select(".connect-txt")
            if normalize_text(node.get_text(" ", strip=True))
        ]
        if not labels:
            raise AllSubjectsDiscoveryError(
                f"question {question_number} has no observed labels in {year_url}"
            )
        key = (str(source["id"]), external_id)
        if key in seen:
            raise AllSubjectsDiscoveryError(f"duplicate observed question: {key}")
        seen.add(key)
        entries.append(
            {
                "catalogId": f"{source['id']}:{external_id}",
                "sourceId": str(source["id"]),
                "externalQuestionId": external_id,
                "examYear": int(year["examYear"]),
                "eraYear": str(year["eraYear"]),
                "questionNumber": question_number,
                "title": title,
                "labels": labels,
                "listingKind": str(kind_rule["kind"]),
                "subjectId": str(subject_rule["subjectId"]),
                "subjectLabel": str(subject_rule["subjectLabel"]),
                "endpointType": endpoint_type,
                "isAmended": amended,
                "explanationExpected": endpoint_type == "regular",
                "historicalUse": str(target["historicalUse"]),
                "url": absolute_url,
                "yearIndexUrl": year_url,
            }
        )
    return entries


def _validate_year(
    entries: list[dict[str, Any]],
    *,
    year: Mapping[str, Any],
    target: Mapping[str, Any],
) -> None:
    exam_year = int(year["examYear"])
    expected = _expected_numbers(target, exam_year)
    numbers = [int(entry["questionNumber"]) for entry in entries]
    problems: list[str] = []
    if len(numbers) != len(set(numbers)):
        problems.append("duplicate questionNumber")
    if set(numbers) != expected:
        problems.append(
            f"question numbers={sorted(numbers)} expected={sorted(expected)}"
        )
    if len({entry["externalQuestionId"] for entry in entries}) != len(entries):
        problems.append("duplicate externalQuestionId")
    if problems:
        raise AllSubjectsDiscoveryError(
            f"{year['eraYear']} catalog validation failed: " + "; ".join(problems)
        )


def _validate_totals(
    entries: list[dict[str, Any]],
    *,
    target: Mapping[str, Any],
) -> None:
    problems: list[str] = []
    if len(entries) != int(target["expectedTotal"]):
        problems.append(f"total={len(entries)} expected={target['expectedTotal']}")
    for field, config_key in (
        ("listingKind", "expectedByFormat"),
        ("subjectId", "expectedBySubject"),
    ):
        actual = Counter(str(entry[field]) for entry in entries)
        expected = {
            str(key): int(value)
            for key, value in target[config_key].items()
        }
        if actual != Counter(expected):
            problems.append(f"{field}={dict(actual)} expected={expected}")
    explanation_actual = Counter(
        "available" if entry["explanationExpected"] else "unavailable"
        for entry in entries
    )
    explanation_expected = {
        str(key): int(value)
        for key, value in target["expectedExplanations"].items()
    }
    if explanation_actual != Counter(explanation_expected):
        problems.append(
            f"explanations={dict(explanation_actual)} "
            f"expected={explanation_expected}"
        )
    if len({entry["catalogId"] for entry in entries}) != len(entries):
        problems.append("duplicate catalogId")
    if len({entry["url"] for entry in entries}) != len(entries):
        problems.append("duplicate question URL")
    if problems:
        raise AllSubjectsDiscoveryError(
            "all-subject catalog validation failed: " + "; ".join(problems)
        )


def build_catalog(
    pages_by_year: Mapping[int, bytes | str],
    config: Mapping[str, Any],
    *,
    year_index_urls: Mapping[int, str] | None = None,
    discovery_snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    configured_years = [int(year["examYear"]) for year in config["years"]]
    if configured_years != [int(value) for value in config["target"]["examYears"]]:
        raise AllSubjectsDiscoveryError(
            "years and target.examYears must match in order"
        )
    entries: list[dict[str, Any]] = []
    for year in config["years"]:
        exam_year = int(year["examYear"])
        if exam_year not in pages_by_year:
            raise AllSubjectsDiscoveryError(f"missing year index HTML: {exam_year}")
        year_entries = parse_year_index(
            pages_by_year[exam_year],
            year=year,
            config=config,
            year_index_url=(
                year_index_urls.get(exam_year) if year_index_urls else None
            ),
        )
        _validate_year(year_entries, year=year, target=config["target"])
        entries.extend(year_entries)
    entries.sort(key=lambda item: (item["examYear"], item["questionNumber"]))
    _validate_totals(entries, target=config["target"])

    if discovery_snapshots is None:
        discovered_at = utc_now()
        discovery_snapshots = []
        for year in config["years"]:
            exam_year = int(year["examYear"])
            raw = pages_by_year[exam_year]
            body = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
            discovery_snapshots.append(
                {
                    "kind": "year_index",
                    "examYear": exam_year,
                    "url": (
                        year_index_urls.get(exam_year)
                        if year_index_urls
                        else str(config["source"]["yearUrlTemplate"]).format(
                            nendoId=year["nendoId"]
                        )
                    ),
                    "fetchedAt": discovered_at,
                    "bodySha256": sha256_bytes(body),
                    "bodyBytes": len(body),
                }
            )
    return {
        "schemaVersion": CATALOG_SCHEMA,
        "generatedAt": utc_now(),
        "target": dict(config["target"]),
        "discoverySnapshots": discovery_snapshots,
        "entries": entries,
    }


def _store_discovery_response(
    kind: str,
    response: Any,
    **extra: Any,
) -> dict[str, Any]:
    body = bytes(response.content)
    digest, relative_path = store_gzip_blob(body, "html")
    return {
        "kind": kind,
        "url": str(response.url),
        "fetchedAt": utc_now(),
        "httpStatus": int(response.status_code),
        "contentType": str(response.headers.get("Content-Type", "")),
        "bodySha256": digest,
        "bodyBytes": len(body),
        "bodyPath": relative_path.as_posix(),
        **extra,
    }


def _observed_year_urls(
    top_html: bytes,
    config: Mapping[str, Any],
) -> dict[int, str]:
    soup = BeautifulSoup(top_html, "html.parser")
    expected_host = urlparse(str(config["source"]["baseUrl"])).netloc
    by_era = {
        normalize_text(str(year["eraYear"])): year
        for year in config["years"]
    }
    found: dict[int, str] = {}
    for anchor in soup.select('a[href*="w_subcatnendo.php?nendoID="]'):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        absolute = urljoin(str(config["source"]["topUrl"]), href)
        parsed = urlparse(absolute)
        identifiers = parse_qs(parsed.query).get("nendoID", [])
        if (
            parsed.netloc != expected_host
            or len(identifiers) != 1
            or not identifiers[0].isdigit()
        ):
            raise AllSubjectsDiscoveryError(f"invalid observed year href: {href}")
        year = by_era.get(normalize_text(anchor.get_text(" ", strip=True)))
        if year is None:
            continue
        if int(identifiers[0]) != int(year["nendoId"]):
            raise AllSubjectsDiscoveryError(
                f"observed nendoID mismatch for {year['eraYear']}"
            )
        exam_year = int(year["examYear"])
        if exam_year in found and found[exam_year] != absolute:
            raise AllSubjectsDiscoveryError(
                f"duplicate year href for {year['eraYear']}"
            )
        found[exam_year] = absolute
    missing = [
        str(year["eraYear"])
        for year in config["years"]
        if int(year["examYear"]) not in found
    ]
    if missing:
        raise AllSubjectsDiscoveryError(
            "target years missing from observed top index: " + ", ".join(missing)
        )
    return found


def _download_year_pages(
    config: Mapping[str, Any],
) -> tuple[dict[int, bytes], dict[int, str], list[dict[str, Any]]]:
    policy = config["fetch"]
    fetcher = SerialFetcher(
        session=requests.Session(),
        user_agent=str(policy["userAgent"]),
        minimum_delay_seconds=float(policy["minimumDelaySeconds"]),
        timeout_seconds=float(policy["timeoutSeconds"]),
        max_attempts=int(policy["maxAttempts"]),
    )
    top_response = fetcher.get(str(config["source"]["topUrl"]))
    top_body = bytes(top_response.content)
    year_urls = _observed_year_urls(top_body, config)
    snapshots = [_store_discovery_response("top_index", top_response)]
    pages: dict[int, bytes] = {}
    for year in config["years"]:
        exam_year = int(year["examYear"])
        response = fetcher.get(year_urls[exam_year])
        pages[exam_year] = bytes(response.content)
        snapshots.append(
            _store_discovery_response(
                "year_index",
                response,
                examYear=exam_year,
            )
        )
    return pages, year_urls, snapshots


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--year-pages-dir",
        type=Path,
        help="use local <examYear>.html files instead of network requests",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=data_root() / "catalog" / "questions.json",
    )
    args = parser.parse_args(argv)
    config = load_json(args.config)
    if config.get("schemaVersion") != "all-subjects-target@1":
        parser.error("--config must contain all-subjects-target@1")
    if args.year_pages_dir:
        pages = {
            int(year["examYear"]): (
                args.year_pages_dir / f"{year['examYear']}.html"
            ).read_bytes()
            for year in config["years"]
        }
        year_urls = None
        snapshots = None
    else:
        pages, year_urls, snapshots = _download_year_pages(config)
    catalog = build_catalog(
        pages,
        config,
        year_index_urls=year_urls,
        discovery_snapshots=snapshots,
    )
    atomic_write_json(args.output, catalog)
    counts = Counter(entry["subjectId"] for entry in catalog["entries"])
    print(
        f"wrote {len(catalog['entries'])} observed questions "
        f"for {len(config['years'])} years to {args.output}: {dict(counts)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
