"""Extract private provider snapshots into ``raw-question@1`` records.

The extractor intentionally captures question material only.  Provider
explanations remain in the immutable HTML snapshot and are never copied into
the extracted JSON.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

from bs4 import BeautifulSoup, Tag

from .common import (
    atomic_write_json,
    data_root,
    load_json,
    normalize_text,
    read_gzip_blob,
    sha256_bytes,
)


SCHEMA_VERSION = "raw-question@1"
PARSER_VERSION = "goukakudojyo-question@2"
LISTING_KINDS = {"regular", "multiple_blank", "written"}
BLANK_LABELS = ("ア", "イ", "ウ", "エ")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BLANK_PATTERN = re.compile(r"[\[［]\s*([アイウエ])\s*[\]］]")

_UPDATE_PATTERN = re.compile(
    r"更新\s*[：:]\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"
    r"(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?"
)
_CHARACTER_LIMIT_PATTERN = re.compile(r"(\d{1,3})\s*字\s*(程度|以内|以上|以下)?")
_MODEL_COUNT_PATTERN = re.compile(r"[（(]\s*(\d{1,3})\s*字\s*[）)]\s*$")
_OPTION_LABEL_PATTERN = re.compile(r"^([1-5])[.．。]?$")
_TASK_QUERY_PATTERN = re.compile(
    r"(?:もの|組合せ|組み合わせ)はどれか|いくつ(?:ある|か)|個数"
)


class ExtractionError(ValueError):
    """Raised when catalog/snapshot input violates the fixed pipeline contract."""


def _require_keys(value: dict[str, Any], keys: Iterable[str], label: str) -> None:
    missing = [key for key in keys if key not in value]
    if missing:
        raise ExtractionError(f"{label} missing required keys: {', '.join(missing)}")


def _text(node: Tag | None) -> str:
    if node is None:
        return ""
    return normalize_text(node.get_text(" ", strip=True))


def _clone(node: Tag) -> BeautifulSoup:
    return BeautifulSoup(str(node), "html.parser")


def _text_without(node: Tag, selectors: Iterable[str]) -> str:
    fragment = _clone(node)
    for selector in selectors:
        for unwanted in fragment.select(selector):
            unwanted.decompose()
    return _text(fragment)


def _block_text(nodes: Iterable[Tag]) -> str:
    blocks = [_text(node) for node in nodes]
    return "\n\n".join(block for block in blocks if block)


def _direct_tags(node: Tag) -> list[Tag]:
    return [child for child in node.children if isinstance(child, Tag)]


def _page_heading(soup: BeautifulSoup) -> tuple[str, list[str]]:
    heading = soup.select_one(".tit_status h3")
    if heading is None:
        return "", []
    page_labels = [_text(label) for label in heading.select(".connect-txt") if _text(label)]
    title = _text_without(heading, [".connect-txt"])
    return title, page_labels


def _provider_updated_at(soup: BeautifulSoup) -> str | None:
    date_node = soup.select_one(".mondai-wrap > .tit .date, .mondai-wrap .tit .date")
    matched = _UPDATE_PATTERN.search(_text(date_node))
    if not matched:
        return None
    year, month, day, hour, minute, second = matched.groups()
    if hour is None:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return (
        f"{int(year):04d}-{int(month):02d}-{int(day):02d}T"
        f"{int(hour):02d}:{int(minute):02d}:{int(second or '0'):02d}+09:00"
    )


def _answer_option(soup: BeautifulSoup) -> int | None:
    matched = re.search(r"\d+", _answer_option_text(soup))
    return int(matched.group()) if matched else None


def _answer_option_text(soup: BeautifulSoup) -> str:
    answer = soup.select_one(
        "#panel .result .kekka .kotae strong, .result .kekka .kotae strong, "
        "p.kotae:not(.kijutsu) .arch-ans"
    )
    return _text(answer)


def infer_regular_task(instruction: str) -> tuple[dict[str, str], list[str]]:
    """Infer only the broad answer task; never infer individual choice truth."""

    compact = normalize_text(instruction)
    warnings: list[str] = []
    if re.search(r"いくつ(?:ある|か)|個数", compact):
        kind = "count"
    elif "組合せ" in compact or "組み合わせ" in compact:
        kind = "combination"
    elif re.search(
        r"妥当でない|適切でない|誤っている|正しくない|"
        r"規定されていない|反している|誤りを含む|矛盾する|"
        r"挿入すべきでない|含まれていない|できないもの|"
        r"適合しない|どれにも当てはまらない|対象とならない|"
        r"明白に対立|必要ではない|他とは異な|他と異なる|"
        r"開かなくてもよい|関連しているとはいえない|"
        r"読み取れない|趣旨と異なる|必要としない|"
        r"備えているとはいえない",
        compact,
    ):
        kind = "select_false"
    elif re.search(
        r"妥当な|適切な|正しい|できるもの|あたるもの|"
        r"定めているのは|大きな値となるもの|述べるところ|"
        r"見地に基づく意見|回答しうるもの|無効原因となる|"
        r"必要がある場合|必要とする事項",
        compact,
    ):
        kind = "select_true"
    else:
        kind = "unknown"
        warnings.append("unknown_regular_task")
    return {
        "kind": kind,
        "prompt": compact,
        "confidence": "low" if kind == "unknown" else "high",
    }, warnings


def _find_regular_choice_list(question: Tag) -> Tag | None:
    direct_lists = [
        node
        for node in question.find_all("ol", recursive=False)
        if "tashi-li" not in (node.get("class") or [])
    ]
    if direct_lists:
        return direct_lists[-1]
    candidates = []
    for node in question.find_all("ol"):
        if "tashi-li" in (node.get("class") or []):
            continue
        count = len(node.find_all("li", recursive=False))
        if 2 <= count <= 10:
            candidates.append(node)
    return candidates[-1] if candidates else None


def _regular_option_rows(table: Tag) -> list[tuple[str, list[Tag]]]:
    rows: list[tuple[str, list[Tag]]] = []
    for row in table.find_all("tr"):
        cells = row.find_all(("th", "td"), recursive=False)
        if not cells:
            continue
        matched = _OPTION_LABEL_PATTERN.fullmatch(_text(cells[0]))
        if not matched:
            continue
        value_cells = [
            cell for cell in cells[1:] if "chk" not in (cell.get("class") or [])
        ]
        rows.append((matched.group(1), value_cells))
    return rows


def _find_regular_choice_table(question: Tag) -> Tag | None:
    expected = [str(number) for number in range(1, 6)]
    candidates = [
        table
        for table in question.find_all("table")
        if [label for label, _cells in _regular_option_rows(table)] == expected
    ]
    return candidates[-1] if candidates else None


def _regular_choice_columns(
    table: Tag, rows: list[tuple[str, list[Tag]]]
) -> list[str]:
    if not rows:
        return []
    width = len(rows[0][1])
    data_titles = [
        normalize_text(str(cell.get("data-title") or "")) for cell in rows[0][1]
    ]
    if len(data_titles) == width and all(data_titles):
        return data_titles

    for row in table.find_all("tr"):
        cells = row.find_all(("th", "td"), recursive=False)
        if not cells or _OPTION_LABEL_PATTERN.fullmatch(_text(cells[0])):
            continue
        headings = [_text(cell) for cell in cells[1:]]
        while headings and not headings[-1]:
            headings.pop()
        if len(headings) == width and all(headings):
            return headings
    return [str(number) for number in range(1, width + 1)]


def _regular_instruction_node(content_nodes: list[Tag]) -> Tag | None:
    paragraphs = [node for node in content_nodes if node.name == "p" and _text(node)]
    explicit = [node for node in paragraphs if _TASK_QUERY_PATTERN.search(_text(node))]
    return explicit[-1] if explicit else (paragraphs[0] if paragraphs else None)


def _parse_regular(
    soup: BeautifulSoup, question: Tag
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    choice_list = _find_regular_choice_list(question)
    choice_table = None if choice_list is not None else _find_regular_choice_table(question)
    choices: list[dict[str, Any]] = []
    choice_format = "list"
    choice_columns: list[str] = []
    if choice_list is not None:
        for index, choice in enumerate(choice_list.find_all("li", recursive=False), start=1):
            text = _text(choice)
            if not text:
                warnings.append(f"empty_regular_choice:{index}")
            choices.append({"label": str(index), "text": text})
    elif choice_table is not None:
        choice_format = "table"
        rows = _regular_option_rows(choice_table)
        choice_columns = _regular_choice_columns(choice_table, rows)
        for label, row_cells in rows:
            cells = []
            for index, cell in enumerate(row_cells):
                column = (
                    normalize_text(str(cell.get("data-title") or ""))
                    or (choice_columns[index] if index < len(choice_columns) else str(index + 1))
                )
                cells.append({"column": column, "text": _text(cell)})
            choice_text = "／".join(
                f"{cell['column']}：{cell['text']}" for cell in cells
            )
            if not choice_text or any(not cell["text"] for cell in cells):
                warnings.append(f"empty_regular_choice:{label}")
            choices.append({"label": label, "text": choice_text, "cells": cells})
    else:
        warnings.append("missing_regular_choices")

    content_nodes = [
        node
        for node in _direct_tags(question)
        if node is not choice_list and node is not choice_table
    ]
    instruction_node = _regular_instruction_node(content_nodes)
    instruction = _text(instruction_node)
    question_text = _block_text(content_nodes)
    task, task_warnings = infer_regular_task(instruction or question_text)
    warnings.extend(task_warnings)

    answer_text = _answer_option_text(soup)
    answer_value = _answer_option(soup)
    no_valid_option = bool(re.search(r"没問|正解肢なし", answer_text))
    if answer_value is None and not no_valid_option:
        warnings.append("missing_regular_answer")

    answer = {"kind": "option", "value": answer_value}
    if no_valid_option:
        answer["note"] = answer_text

    return {
        "instructionText": instruction,
        "questionText": question_text,
        "choices": choices,
        "choiceFormat": choice_format,
        "choiceColumns": choice_columns,
        "task": task,
        "answer": answer,
    }, warnings


def _parse_multiple_blank(
    soup: BeautifulSoup, question: Tag
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    direct = _direct_tags(question)
    bank_list = question.select_one("ol.tashi-li")
    instruction_node = next(
        (node for node in direct if node.name == "p" and node is not bank_list and _text(node)),
        None,
    )
    instruction = _text(instruction_node)
    source_nodes = [
        node
        for node in direct
        if "right" in (node.get("class") or []) or "read" in (node.get("class") or [])
    ]
    passage_nodes = [
        node
        for node in direct
        if node is not instruction_node and node is not bank_list and node not in source_nodes
    ]
    passage = _block_text(passage_nodes)
    source_note = _block_text(source_nodes)

    word_bank: list[dict[str, Any]] = []
    if bank_list is not None:
        for number, option in enumerate(bank_list.find_all("li", recursive=False), start=1):
            word_bank.append({"number": number, "text": _text(option)})
    if len(word_bank) != 20:
        warnings.append(f"unexpected_word_bank_count:{len(word_bank)}")

    seen = set(BLANK_PATTERN.findall(instruction + "\n" + passage))
    blanks = [label for label in BLANK_LABELS if label in seen]
    if tuple(blanks) != BLANK_LABELS:
        warnings.append("unexpected_blank_labels")

    answer_values: dict[str, int] = {}
    for answer_node in soup.select("#panel .result .kekka .kotae, .result .kekka .kotae"):
        strong = answer_node.find("strong")
        number_match = re.search(r"\d+", _text(strong))
        label_match = re.search(r"[アイウエ]", _text(answer_node))
        if label_match and number_match:
            answer_values[label_match.group()] = int(number_match.group())
    if not answer_values:
        for number_node in soup.select("p.kotae .arch-ans"):
            parent_text = _text(number_node.parent if isinstance(number_node.parent, Tag) else number_node)
            label_match = re.search(r"[アイウエ]", parent_text)
            number_match = re.search(r"\d+", _text(number_node))
            if label_match and number_match:
                answer_values[label_match.group()] = int(number_match.group())
    if tuple(answer_values) != BLANK_LABELS:
        warnings.append("missing_multiple_blank_answers")

    return {
        "instructionText": instruction,
        "passageText": passage,
        "sourceNote": source_note,
        "blanks": blanks,
        "wordBank": word_bank,
        "task": {
            "kind": "fill_four_blanks",
            "prompt": instruction,
            "confidence": "high",
        },
        "answer": {"kind": "blank_numbers", "values": answer_values},
    }, warnings


def _character_limit(question_text: str) -> tuple[int | None, str | None]:
    matched = _CHARACTER_LIMIT_PATTERN.search(question_text)
    if not matched:
        return None, None
    qualifier = {
        "程度": "approximately",
        "以内": "maximum",
        "以上": "minimum",
        "以下": "maximum",
        None: "exact",
    }[matched.group(2)]
    return int(matched.group(1)), qualifier


def _parse_written(
    soup: BeautifulSoup, question: Tag
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    reference_nodes = question.select(".read")
    direct = _direct_tags(question)
    question_nodes = [
        node
        for node in direct
        if node not in reference_nodes and "read" not in (node.get("class") or [])
    ]
    question_text = _block_text(question_nodes)
    reference_text = _block_text(reference_nodes)
    character_limit, limit_kind = _character_limit(question_text)

    answer_node = soup.select_one(
        "#panel .result.kijutu .kekka .kotae strong, "
        "#panel .result .kekka .kotae strong, .result.kijutu .kekka .kotae strong, "
        "p.kotae.kijutsu .arch-ans"
    )
    model_answer_raw = _text(answer_node)
    count_match = _MODEL_COUNT_PATTERN.search(model_answer_raw)
    model_answer_count = int(count_match.group(1)) if count_match else None
    model_answer = _MODEL_COUNT_PATTERN.sub("", model_answer_raw).strip()

    if not question_text:
        warnings.append("missing_written_question")
    if character_limit is None:
        warnings.append("missing_written_character_limit")
    if not model_answer:
        warnings.append("missing_written_model_answer")

    return {
        "questionText": question_text,
        "referenceText": reference_text,
        "characterLimit": character_limit,
        "characterLimitKind": limit_kind,
        "modelAnswer": model_answer,
        "modelAnswerRaw": model_answer_raw,
        "modelAnswerCharacterCount": model_answer_count,
        "task": {
            "kind": "written_response",
            "prompt": question_text,
            "confidence": "high" if character_limit else "low",
        },
        "answer": {"kind": "model_answer", "value": model_answer},
    }, warnings


def parse_question_html(
    html: bytes | str,
    catalog_entry: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Parse one catalog/snapshot pair into a lossless-enough question record."""

    _require_keys(
        catalog_entry,
        (
            "catalogId",
            "sourceId",
            "externalQuestionId",
            "examYear",
            "eraYear",
            "questionNumber",
            "title",
            "labels",
            "listingKind",
            "endpointType",
            "isAmended",
            "url",
        ),
        "catalog entry",
    )
    _require_keys(
        snapshot,
        (
            "snapshotId",
            "sourceId",
            "externalQuestionId",
            "url",
            "finalUrl",
            "fetchedAt",
            "fetchStatus",
            "httpStatus",
            "contentType",
            "bodySha256",
            "bodyBytes",
            "bodyPath",
        ),
        "snapshot",
    )
    if catalog_entry["listingKind"] not in LISTING_KINDS:
        raise ExtractionError(f"unsupported listingKind: {catalog_entry['listingKind']}")
    for key in ("sourceId", "externalQuestionId"):
        if str(catalog_entry[key]) != str(snapshot[key]):
            raise ExtractionError(f"catalog/snapshot {key} mismatch")
    if snapshot["fetchStatus"] != "ok":
        raise ExtractionError(f"snapshot is not fetchable: {snapshot['fetchStatus']}")

    body = html.encode("utf-8") if isinstance(html, str) else html
    warnings: list[str] = []
    integrity_error = False
    if HASH_PATTERN.fullmatch(str(snapshot["bodySha256"])) and sha256_bytes(body) != snapshot["bodySha256"]:
        warnings.append("snapshot_body_hash_mismatch")
        integrity_error = True
    if type(snapshot["bodyBytes"]) is int and len(body) != snapshot["bodyBytes"]:
        warnings.append("snapshot_body_size_mismatch")
        integrity_error = True

    soup = BeautifulSoup(body, "html.parser")
    for unwanted in soup.select("script, style, noscript, .SlctChk"):
        unwanted.decompose()

    page_title, page_labels = _page_heading(soup)
    catalog_labels = [normalize_text(str(value)) for value in catalog_entry["labels"]]
    comparable_page_title = re.sub(
        r"\s*[（(]\s*没問\s*[）)]\s*$",
        "",
        normalize_text(page_title),
    )
    if (
        page_title
        and comparable_page_title
        != normalize_text(str(catalog_entry["title"]))
    ):
        warnings.append("catalog_page_title_mismatch")
    if page_labels and page_labels != catalog_labels:
        warnings.append("catalog_page_labels_mismatch")

    provider_updated_at = _provider_updated_at(soup)
    if provider_updated_at is None and catalog_entry["endpointType"] != "archive":
        warnings.append("missing_provider_updated_at")

    question = soup.select_one(".mondai-wrap .que-mon .toi")
    parsed: dict[str, Any] = {}
    status = "parse_error" if integrity_error else "parsed"
    if question is None:
        warnings.append("missing_question_container")
        status = "parse_error"
    else:
        try:
            if catalog_entry["listingKind"] == "regular":
                parsed, parser_warnings = _parse_regular(soup, question)
            elif catalog_entry["listingKind"] == "multiple_blank":
                parsed, parser_warnings = _parse_multiple_blank(soup, question)
            else:
                parsed, parser_warnings = _parse_written(soup, question)
            warnings.extend(parser_warnings)
        except Exception as error:  # retain a diagnosable record instead of dropping coverage
            warnings.append(f"parser_exception:{type(error).__name__}")
            status = "parse_error"

    if status != "parse_error" and warnings:
        status = "needs_review"

    record: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "parserVersion": PARSER_VERSION,
        "rawQuestionId": catalog_entry["catalogId"],
        "catalogId": catalog_entry["catalogId"],
        "sourceSnapshotId": snapshot["snapshotId"],
        "sourceBodySha256": snapshot["bodySha256"],
        "sourceId": catalog_entry["sourceId"],
        "externalQuestionId": str(catalog_entry["externalQuestionId"]),
        "sourceUrl": snapshot["finalUrl"] or snapshot["url"],
        "examYear": catalog_entry["examYear"],
        "eraYear": catalog_entry["eraYear"],
        "questionNumber": catalog_entry["questionNumber"],
        "title": page_title or catalog_entry["title"],
        "labels": page_labels or catalog_labels,
        "catalogLabels": catalog_labels,
        "listingKind": catalog_entry["listingKind"],
        "endpointType": catalog_entry["endpointType"],
        "isAmended": bool(catalog_entry["isAmended"]),
        "isWithdrawn": bool(re.search(r"[（(]\s*没問\s*[）)]", page_title)),
        "providerUpdatedAt": provider_updated_at,
        "explanationCaptured": False,
        "extraction": {"status": status, "warnings": sorted(set(warnings))},
    }
    # Generic all-subject catalogs add editorial metadata that is not present
    # in the original administrative-law-only catalog.  Keep it alongside the
    # extracted question without making it mandatory for legacy records.
    for key in (
        "subjectId",
        "subjectLabel",
        "explanationExpected",
        "historicalUse",
    ):
        if key in catalog_entry:
            record[key] = catalog_entry[key]
    record.update(parsed)
    return record


def _documents(path: Path) -> list[dict[str, Any]]:
    paths = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    documents: list[dict[str, Any]] = []
    for document_path in paths:
        value = load_json(document_path)
        if isinstance(value, list):
            documents.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            collection = next(
                (
                    value[key]
                    for key in ("items", "snapshots")
                    if isinstance(value.get(key), list)
                ),
                None,
            )
            if collection is None:
                documents.append(value)
            else:
                documents.extend(item for item in collection if isinstance(item, dict))
    return documents


def extract_catalog(
    catalog: dict[str, Any],
    snapshots: Iterable[dict[str, Any]],
    blob_loader: Callable[[str | Path], bytes] = read_gzip_blob,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Join catalog and latest successful snapshots by provider IDs."""

    entries = catalog.get("entries")
    if not isinstance(entries, list):
        raise ExtractionError("catalog entries must be a list")
    snapshot_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        key = (str(snapshot.get("sourceId", "")), str(snapshot.get("externalQuestionId", "")))
        snapshot_index.setdefault(key, []).append(snapshot)

    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for entry in entries:
        key = (str(entry.get("sourceId", "")), str(entry.get("externalQuestionId", "")))
        candidates = [
            item for item in snapshot_index.get(key, []) if item.get("fetchStatus") == "ok"
        ]
        if not candidates:
            errors.append(f"{entry.get('catalogId', key)}: no successful snapshot")
            continue
        snapshot = max(candidates, key=lambda item: str(item.get("fetchedAt", "")))
        try:
            body = blob_loader(snapshot["bodyPath"])
            records.append(parse_question_html(body, entry, snapshot))
        except Exception as error:
            errors.append(f"{entry.get('catalogId', key)}: {error}")
    return records, errors


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=data_root() / "catalog" / "questions.json",
    )
    parser.add_argument(
        "--snapshots",
        type=Path,
        default=data_root() / "raw" / "snapshots" / "index.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=data_root() / "extracted",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    catalog = load_json(args.catalog)
    snapshots = _documents(args.snapshots)
    records, errors = extract_catalog(catalog, snapshots)
    for record in records:
        destination = args.output / str(record["examYear"]) / f"{record['catalogId']}.json"
        atomic_write_json(destination, record)
    summary = {
        "written": len(records),
        "parseErrors": sum(
            record["extraction"]["status"] == "parse_error" for record in records
        ),
        "errors": errors,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if errors or summary["parseErrors"] else 0


if __name__ == "__main__":
    sys.exit(main())
