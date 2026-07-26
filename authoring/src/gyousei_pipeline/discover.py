"""Discover the target questions from the provider's observed year indexes.

Question URLs are never synthesized from a question-number/``queID`` formula.
Only links actually present in a year index become catalog entries.
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
    load_target,
    normalize_text,
    sha256_bytes,
    store_gzip_blob,
    utc_now,
)


CATALOG_SCHEMA = "catalog@1"
ENDPOINTS = {
    "/worksheet2/w_mainnendo.php": "regular",
    "/worksheet2/w_mainarch.php": "archive",
}
EXPECTED_NUMBERS = {
    "regular": set(range(8, 27)),
    "multiple_blank": {42, 43},
    "written": {44},
}
CONFIG_COUNT_KEYS = {
    "regular": "regular",
    "multiple_blank": "multipleChoice",
    "written": "written",
}


class DiscoveryError(RuntimeError):
    """The observed indexes do not satisfy the closed catalog contract."""


def _prefix_matches(labels: list[str], required: list[str]) -> bool:
    return len(labels) >= len(required) and labels[: len(required)] == required


def classify_labels(labels: list[str], rules: list[dict[str, Any]]) -> str | None:
    """Return a listing kind using the configured, ordered label prefixes."""

    cleaned = [normalize_text(label) for label in labels if normalize_text(label)]
    # Longest prefix first keeps future overlapping rules unambiguous.
    ordered = sorted(rules, key=lambda rule: len(rule["requiredLabels"]), reverse=True)
    for rule in ordered:
        required = [normalize_text(value) for value in rule["requiredLabels"]]
        if _prefix_matches(cleaned, required):
            return str(rule["kind"])
    return None


def _link_title(anchor: Any, era_year: str) -> tuple[str, int, bool] | None:
    text = normalize_text(anchor.get_text(" ", strip=True))
    match = re.search(
        rf"{re.escape(era_year)}\s*[－-]\s*問\s*([0-9]+)\s*(改題)?",
        text,
    )
    if not match:
        return None
    question_number = int(match.group(1))
    is_amended = bool(match.group(2))
    title = f"{era_year}－問{question_number}" + ("改題" if is_amended else "")
    return title, question_number, is_amended


def parse_year_index(
    html: bytes | str,
    *,
    year: Mapping[str, Any],
    config: Mapping[str, Any],
    year_index_url: str | None = None,
) -> list[dict[str, Any]]:
    """Parse one year index and return only observed administrative-law links."""

    source = config["source"]
    target = config["target"]
    base_url = str(source["baseUrl"])
    expected_host = urlparse(base_url).netloc
    year_url = year_index_url or str(source["yearUrlTemplate"]).format(
        nendoId=year["nendoId"]
    )
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict[str, Any]] = []
    seen_links: set[tuple[str, str]] = set()

    for anchor in soup.select('a[href*="w_mainnendo.php?queID="], a[href*="w_mainarch.php?queID="]'):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        absolute_url = urljoin(year_url, href)
        parsed_url = urlparse(absolute_url)
        endpoint_type = ENDPOINTS.get(parsed_url.path)
        if parsed_url.netloc != expected_host or endpoint_type is None:
            raise DiscoveryError(f"Unexpected question URL in {year_url}: {absolute_url}")
        identifiers = parse_qs(parsed_url.query).get("queID", [])
        if len(identifiers) != 1 or not identifiers[0].isdigit():
            raise DiscoveryError(f"Invalid queID in observed href: {href}")
        external_id = identifiers[0]

        title_parts = _link_title(anchor, str(year["eraYear"]))
        if title_parts is None:
            raise DiscoveryError(f"Could not parse observed question title: {anchor.get_text(' ', strip=True)}")
        title, question_number, is_amended = title_parts
        labels = [
            normalize_text(node.get_text(" ", strip=True))
            for node in anchor.select(".connect-txt")
            if normalize_text(node.get_text(" ", strip=True))
        ]
        listing_kind = classify_labels(labels, list(target["listingRules"]))
        if listing_kind is None:
            continue
        configured_numbers = target.get("questionNumbersByKind")
        if configured_numbers is not None:
            allowed = configured_numbers.get(listing_kind)
            if not isinstance(allowed, list) or not all(
                type(value) is int for value in allowed
            ):
                raise DiscoveryError(
                    f"Invalid questionNumbersByKind for {listing_kind}"
                )
            if question_number not in allowed:
                continue

        key = (str(source["id"]), external_id)
        if key in seen_links:
            raise DiscoveryError(f"Duplicate observed question link: {external_id}")
        seen_links.add(key)
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
                "listingKind": listing_kind,
                "endpointType": endpoint_type,
                "isAmended": is_amended,
                "url": absolute_url,
                "yearIndexUrl": year_url,
            }
        )

    return entries


def _validate_year(
    entries: list[dict[str, Any]], year: Mapping[str, Any], target: Mapping[str, Any]
) -> None:
    expected = target["expectedPerYear"]
    counts = Counter(entry["listingKind"] for entry in entries)
    problems: list[str] = []
    for kind, config_key in CONFIG_COUNT_KEYS.items():
        wanted = int(expected[config_key])
        if counts[kind] != wanted:
            problems.append(f"{kind}={counts[kind]} (expected {wanted})")
        numbers = {entry["questionNumber"] for entry in entries if entry["listingKind"] == kind}
        if numbers != EXPECTED_NUMBERS[kind]:
            problems.append(
                f"{kind} question numbers={sorted(numbers)} "
                f"(expected {sorted(EXPECTED_NUMBERS[kind])})"
            )
    if len(entries) != int(expected["total"]):
        problems.append(f"total={len(entries)} (expected {expected['total']})")
    if len({entry["externalQuestionId"] for entry in entries}) != len(entries):
        problems.append("duplicate externalQuestionId")
    if len({entry["questionNumber"] for entry in entries}) != len(entries):
        problems.append("duplicate questionNumber")
    if problems:
        raise DiscoveryError(f"{year['eraYear']} catalog validation failed: " + "; ".join(problems))


def build_catalog(
    pages_by_year: Mapping[int, bytes | str],
    config: Mapping[str, Any] | None = None,
    *,
    year_index_urls: Mapping[int, str] | None = None,
    discovery_snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build and fail-closed validate a ten-year ``catalog@1`` document."""

    config = config or load_target()
    all_entries: list[dict[str, Any]] = []
    for year in config["years"]:
        exam_year = int(year["examYear"])
        if exam_year not in pages_by_year:
            raise DiscoveryError(f"Missing year index HTML for {exam_year}")
        observed_year_url = year_index_urls.get(exam_year) if year_index_urls else None
        entries = parse_year_index(
            pages_by_year[exam_year],
            year=year,
            config=config,
            year_index_url=observed_year_url,
        )
        _validate_year(entries, year, config["target"])
        all_entries.extend(entries)

    expected_years = {int(value) for value in config["target"]["examYears"]}
    configured_years = {int(year["examYear"]) for year in config["years"]}
    if configured_years != expected_years:
        raise DiscoveryError("Configured years do not match target.examYears")
    if len(all_entries) != int(config["target"]["expectedTotal"]):
        raise DiscoveryError(
            f"Catalog total={len(all_entries)}; expected {config['target']['expectedTotal']}"
        )
    if len({entry["catalogId"] for entry in all_entries}) != len(all_entries):
        raise DiscoveryError("Duplicate catalogId across years")
    if len({entry["url"] for entry in all_entries}) != len(all_entries):
        raise DiscoveryError("Duplicate question URL across years")

    all_entries.sort(key=lambda item: (item["examYear"], item["questionNumber"]))
    if discovery_snapshots is None:
        discovered_at = utc_now()
        discovery_snapshots = []
        for year in config["years"]:
            exam_year = int(year["examYear"])
            raw = pages_by_year[exam_year]
            body = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
            configured_url = str(config["source"]["yearUrlTemplate"]).format(
                nendoId=year["nendoId"]
            )
            observed_url = year_index_urls.get(exam_year) if year_index_urls else None
            discovery_snapshots.append(
                {
                    "kind": "year_index",
                    "examYear": exam_year,
                    "url": observed_url or configured_url,
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
        "entries": all_entries,
    }


def _store_discovery_response(kind: str, response: Any, **extra: Any) -> dict[str, Any]:
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


def _observed_year_urls(top_html: bytes, config: Mapping[str, Any]) -> dict[int, str]:
    soup = BeautifulSoup(top_html, "html.parser")
    expected_host = urlparse(str(config["source"]["baseUrl"])).netloc
    found: dict[int, str] = {}
    for anchor in soup.select('a[href*="w_subcatnendo.php?nendoID="]'):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        absolute = urljoin(str(config["source"]["topUrl"]), href)
        parsed = urlparse(absolute)
        identifiers = parse_qs(parsed.query).get("nendoID", [])
        if parsed.netloc != expected_host or len(identifiers) != 1 or not identifiers[0].isdigit():
            raise DiscoveryError(f"Invalid observed year href: {href}")
        anchor_text = normalize_text(anchor.get_text(" ", strip=True))
        for year in config["years"]:
            if anchor_text == normalize_text(str(year["eraYear"])):
                exam_year = int(year["examYear"])
                if int(identifiers[0]) != int(year["nendoId"]):
                    raise DiscoveryError(
                        f"Observed nendoID mismatch for {anchor_text}: {identifiers[0]}"
                    )
                if exam_year in found and found[exam_year] != absolute:
                    raise DiscoveryError(f"Duplicate year href for {anchor_text}")
                found[exam_year] = absolute
    missing = [year["eraYear"] for year in config["years"] if int(year["examYear"]) not in found]
    if missing:
        raise DiscoveryError("Target years missing from observed top index: " + ", ".join(missing))
    return found


def _download_year_pages(
    config: Mapping[str, Any],
) -> tuple[dict[int, bytes], dict[int, str], list[dict[str, Any]]]:
    """GET top/year indexes serially and preserve private acquisition evidence."""

    from .fetch import SerialFetcher

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
            _store_discovery_response("year_index", response, examYear=exam_year)
        )
    return pages, year_urls, snapshots


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--year-pages-dir",
        type=Path,
        help="Use local <examYear>.html files instead of making network requests",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=data_root() / "catalog" / "questions.json",
    )
    args = parser.parse_args(argv)
    config = load_target()
    if args.year_pages_dir:
        pages = {
            int(year["examYear"]): (args.year_pages_dir / f"{year['examYear']}.html").read_bytes()
            for year in config["years"]
        }
        year_urls = None
        snapshots = None
    else:
        pages, year_urls, snapshots = _download_year_pages(config)
    atomic_write_json(
        args.output,
        build_catalog(
            pages,
            config,
            year_index_urls=year_urls,
            discovery_snapshots=snapshots,
        ),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
