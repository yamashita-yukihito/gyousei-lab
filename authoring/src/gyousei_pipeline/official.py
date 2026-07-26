"""Fetch and preserve official examination PDFs and answer pages privately.

The module is intentionally serial and conservative.  A successful response is
stored in the content-addressed gzip blob store before its snapshot is added to
the private index.  The index is updated after each artifact, so a later run can
resume an interrupted year without downloading the completed artifact again.
"""

from __future__ import annotations

import argparse
import logging
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup, Tag

from . import common


LOGGER = logging.getLogger(__name__)
INDEX_SCHEMA = "official-snapshots@1"
ANSWER_SCHEMA = "official-answer-displays@1"
TARGET_QUESTIONS = tuple(range(8, 27)) + (42, 43, 44)
INDEX_RELATIVE_PATH = Path("raw") / "snapshots" / "official" / "index.json"
PLACEHOLDER_ANSWER_RE = re.compile(r"記述式問題の正解例[（(]?別紙[）)]?")
PROBLEM_RE = re.compile(r"^(?:問題|問)\s*([0-9]{1,2})$")


class OfficialError(RuntimeError):
    """Base class for expected acquisition failures."""


class OfficialFetchError(OfficialError):
    """A request failed or returned a status which cannot be accepted."""


class OfficialForbiddenError(OfficialFetchError):
    """The official server returned 403; the complete run must stop."""


class OfficialValidationError(OfficialError):
    """A response body did not match its declared artifact type."""


class OfficialAnswerParseError(OfficialError):
    """The official answer display could not be interpreted unambiguously."""


def _nfkc(value: str) -> str:
    return unicodedata.normalize("NFKC", value.replace("\u3000", " "))


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", _nfkc(value))


def _media_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def _html_page_problem(body: bytes, final_url: str = "") -> str | None:
    """Return a reason when an HTML body looks like login/error content."""

    soup = BeautifulSoup(body, "html.parser")
    title = common.normalize_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
    headings = " ".join(
        common.normalize_text(node.get_text(" ", strip=True))
        for node in soup.find_all(["h1", "h2"])
    )
    prominent = _nfkc((title + " " + headings).lower())
    path = urlsplit(final_url).path.lower()

    if soup.find("input", attrs={"type": re.compile(r"^password$", re.I)}):
        return "login form detected"
    if any(token in path for token in ("/login", "/signin", "/auth/")):
        return "redirected to a login page"
    login_signals = ("ログイン", "サインイン", "login", "sign in", "パスワード")
    if any(signal in prominent for signal in login_signals):
        return "login page detected"
    error_signals = (
        "404 not found",
        "403 forbidden",
        "access denied",
        "service unavailable",
        "internal server error",
        "ページが見つかりません",
        "アクセスできません",
        "エラーが発生",
    )
    if any(signal in prominent for signal in error_signals):
        return "error page detected"
    return None


def validate_download(
    body: bytes,
    content_type: str | None,
    expected_kind: str,
    *,
    final_url: str = "",
) -> str:
    """Validate content type, leading magic and obvious login/error pages.

    Returns the normalized media type.  ``expected_kind`` is ``pdf`` or
    ``html``.  Validation is deliberately strict: a server error page must not
    become a permanent content-addressed blob merely because it returned 200.
    """

    media_type = _media_type(content_type)
    if expected_kind == "pdf":
        if media_type != "application/pdf":
            raise OfficialValidationError(f"PDF content-type is {media_type or 'missing'}")
        if not body.startswith(b"%PDF-"):
            if body.lstrip().lower().startswith((b"<!doctype html", b"<html", b"<?xml")):
                reason = _html_page_problem(body, final_url) or "HTML returned for PDF"
                raise OfficialValidationError(reason)
            raise OfficialValidationError("PDF magic %PDF- is missing at byte 0")
        return media_type

    if expected_kind == "html":
        if media_type not in {"text/html", "application/xhtml+xml"}:
            raise OfficialValidationError(f"HTML content-type is {media_type or 'missing'}")
        leading = body.lstrip().lower()
        if not leading.startswith((b"<!doctype html", b"<html", b"<?xml")):
            raise OfficialValidationError("HTML leading magic is missing")
        reason = _html_page_problem(body, final_url)
        if reason:
            raise OfficialValidationError(reason)
        return media_type

    raise ValueError(f"unknown expected kind: {expected_kind}")


def _retry_after_seconds(value: str | None, now: Callable[[], float]) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return min(60.0, float(value))
    try:
        timestamp = parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None
    return min(60.0, max(0.0, timestamp - now()))


@dataclass
class DownloadedArtifact:
    body: bytes
    url: str
    final_url: str
    status_code: int
    content_type: str
    fetched_at: str


class SerialOfficialClient:
    """A one-request-at-a-time client with a start-to-start rate limit."""

    def __init__(
        self,
        fetch_config: Mapping[str, Any],
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self.session = session or requests.Session()
        self.user_agent = str(fetch_config.get("userAgent") or "YukiPersonalStudyArchive/1.0")
        self.minimum_delay = max(1.0, float(fetch_config.get("minimumDelaySeconds", 1.0)))
        self.timeout = float(fetch_config.get("timeoutSeconds", 30))
        self.max_attempts = max(1, int(fetch_config.get("maxAttempts", 3)))
        self.sleep = sleep
        self.monotonic = monotonic
        self.wall_time = wall_time
        self._last_started: float | None = None

    def _wait_for_slot(self) -> None:
        if self._last_started is not None:
            remaining = self.minimum_delay - (self.monotonic() - self._last_started)
            if remaining > 0:
                self.sleep(remaining)
        self._last_started = self.monotonic()

    def get(self, url: str, expected_kind: str) -> DownloadedArtifact:
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._wait_for_slot()
            try:
                response = self.session.get(
                    url,
                    headers={"User-Agent": self.user_agent, "Accept": "application/pdf,text/html;q=0.9"},
                    timeout=self.timeout,
                    allow_redirects=True,
                )
            except requests.RequestException as error:
                raise OfficialFetchError(f"GET failed without retry: {url}: {error}") from error

            status = int(response.status_code)
            if status == 403:
                raise OfficialForbiddenError(f"403 Forbidden: {url}")
            if status == 429 or 500 <= status <= 599:
                last_error = OfficialFetchError(f"HTTP {status}: {url}")
                if attempt == self.max_attempts:
                    break
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"), self.wall_time)
                delay = retry_after if retry_after is not None else min(30.0, self.minimum_delay * (2 ** (attempt - 1)))
                LOGGER.warning("official HTTP %s; retrying (%s/%s)", status, attempt, self.max_attempts)
                if delay > 0:
                    self.sleep(delay)
                continue
            if status != 200:
                raise OfficialFetchError(f"HTTP {status}: {url}")

            body = bytes(response.content)
            content_type = response.headers.get("Content-Type", "")
            validate_download(
                body,
                content_type,
                expected_kind,
                final_url=str(response.url),
            )
            return DownloadedArtifact(
                body=body,
                url=url,
                final_url=str(response.url),
                status_code=status,
                content_type=str(content_type),
                fetched_at=common.utc_now(),
            )

        raise OfficialFetchError(
            f"request failed after {self.max_attempts} attempts: {url}: {last_error}"
        )


def _cell_text(cell: Tag, *, compact: bool = False) -> str:
    separator = "" if compact or cell.find("table") else " "
    return _nfkc(cell.get_text(separator, strip=True)).strip()


def _row_answer_candidates(soup: BeautifulSoup) -> dict[int, list[str]]:
    candidates: dict[int, list[str]] = {}
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        markers: list[tuple[int, int]] = []
        for index, cell in enumerate(cells):
            match = PROBLEM_RE.fullmatch(_nfkc(cell.get_text(" ", strip=True)).strip())
            if match:
                markers.append((index, int(match.group(1))))
        for marker_index, (cell_index, number) in enumerate(markers):
            if number not in TARGET_QUESTIONS:
                continue
            end_index = markers[marker_index + 1][0] if marker_index + 1 < len(markers) else len(cells)
            answer_cells = cells[cell_index + 1 : end_index]
            if not answer_cells:
                continue
            pieces = [_cell_text(cell, compact=number == 44) for cell in answer_cells]
            display = " ".join(piece for piece in pieces if piece).strip()
            if display:
                candidates.setdefault(number, []).append(display)
    return candidates


def _written_fallback(soup: BeautifulSoup) -> list[str]:
    """Handle old pages where the Q44 heading and answer are sibling blocks."""

    found: list[str] = []
    for node in soup.find_all(["h2", "h3", "h4", "p", "div", "dt", "th", "td"]):
        if _compact(node.get_text(" ", strip=True)) not in {"問題44", "問44"}:
            continue
        if node.find_parent("tr") is not None:
            continue
        sibling = node.find_next_sibling()
        while sibling is not None:
            text = _nfkc(sibling.get_text("", strip=True)).strip()
            if re.fullmatch(r"(?:問題|問)\s*(?:45|46)", _nfkc(text)):
                break
            if text and not PLACEHOLDER_ANSWER_RE.fullmatch(_compact(text)):
                found.append(text)
                break
            sibling = sibling.find_next_sibling()
    return found


def _deduplicate_candidates(number: int, values: Iterable[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        compact = _compact(value)
        if number == 44 and PLACEHOLDER_ANSWER_RE.fullmatch(compact):
            continue
        if compact and compact not in seen:
            seen.add(compact)
            unique.append(value)
    return unique


def _parse_single(number: int, display: str) -> dict[str, Any]:
    compact = _compact(display)
    if re.fullmatch(r"[1-5]", compact):
        return {"questionNumber": number, "kind": "single", "display": compact, "answer": int(compact)}
    if compact == "全員正解":
        return {
            "questionNumber": number,
            "kind": "single",
            "display": "全員正解",
            "answer": None,
            "specialStatus": "all_correct",
        }
    if compact in {"正解なし", "該当なし"}:
        return {
            "questionNumber": number,
            "kind": "single",
            "display": compact,
            "answer": None,
            "specialStatus": "no_valid_answer",
        }
    raise OfficialAnswerParseError(f"question {number}: unexpected single-choice display {display!r}")


def _parse_multiple_blank(number: int, display: str) -> dict[str, Any]:
    normalized = _nfkc(display)
    pairs = re.findall(r"([アイウエ])\s*[:：=]?\s*([0-9]{1,2})", normalized)
    blanks: dict[str, int] = {}
    for label, raw_value in pairs:
        if label in blanks:
            raise OfficialAnswerParseError(f"question {number}: duplicate blank {label}")
        value = int(raw_value)
        if not 1 <= value <= 20:
            raise OfficialAnswerParseError(f"question {number}: blank {label} is out of range")
        blanks[label] = value
    if set(blanks) != {"ア", "イ", "ウ", "エ"}:
        raise OfficialAnswerParseError(f"question {number}: four blank answers were not found")
    residue = re.sub(r"([アイウエ])\s*[:：=]?\s*([0-9]{1,2})", "", normalized)
    if re.sub(r"[\s/／,、・|｜]+", "", residue):
        raise OfficialAnswerParseError(f"question {number}: ambiguous extra text in {display!r}")
    canonical = " / ".join(f"{label} {blanks[label]}" for label in "アイウエ")
    return {
        "questionNumber": number,
        "kind": "multiple_blank",
        "display": canonical,
        "blanks": blanks,
    }


def _parse_written(display: str, warnings: list[str]) -> dict[str, Any]:
    normalized = _nfkc(display)
    count_matches = re.findall(r"[（(]\s*([0-9]{1,3})\s*字\s*[）)]", normalized)
    if len(set(count_matches)) > 1:
        raise OfficialAnswerParseError("question 44: conflicting character counts")
    character_count = int(count_matches[0]) if count_matches else None
    without_count = re.sub(r"[（(]\s*[0-9]{1,3}\s*字\s*[）)]", "", normalized)
    answer_text = re.sub(r"\s+", "", without_count)
    if not answer_text or PLACEHOLDER_ANSWER_RE.fullmatch(answer_text):
        raise OfficialAnswerParseError("question 44: written answer text was not found")
    if len(answer_text) < 10:
        raise OfficialAnswerParseError("question 44: written answer is implausibly short")
    if character_count is None:
        warnings.append("question 44: character-count display was not found")
    elif len(answer_text) != character_count:
        warnings.append(
            f"question 44: displayed character count is {character_count}, parsed text length is {len(answer_text)}"
        )
    result: dict[str, Any] = {
        "questionNumber": 44,
        "kind": "written",
        "display": answer_text,
        "answerText": answer_text,
        "characterCount": character_count,
    }
    return result


def parse_official_answers(body: bytes | str) -> dict[str, Any]:
    """Parse the official displays for Q8-26 and Q42-44.

    The parser is class-name independent and accepts both split table cells and
    older combined cells.  It rejects missing or conflicting candidates rather
    than silently selecting one.
    """

    soup = BeautifulSoup(body, "html.parser")
    candidates = _row_answer_candidates(soup)
    if not _deduplicate_candidates(44, candidates.get(44, [])):
        candidates.setdefault(44, []).extend(_written_fallback(soup))

    warnings: list[str] = []
    answers: list[dict[str, Any]] = []
    for number in TARGET_QUESTIONS:
        values = _deduplicate_candidates(number, candidates.get(number, []))
        if not values:
            raise OfficialAnswerParseError(f"question {number}: answer display is missing")
        if len(values) != 1:
            raise OfficialAnswerParseError(
                f"question {number}: conflicting answer displays: {values!r}"
            )
        if 8 <= number <= 26:
            answers.append(_parse_single(number, values[0]))
        elif number in {42, 43}:
            answers.append(_parse_multiple_blank(number, values[0]))
        else:
            answers.append(_parse_written(values[0], warnings))

    return {"schemaVersion": ANSWER_SCHEMA, "answers": answers, "warnings": warnings}


def _new_index(source_id: str) -> dict[str, Any]:
    return {
        "schemaVersion": INDEX_SCHEMA,
        "sourceId": source_id,
        "updatedAt": common.utc_now(),
        "snapshots": [],
        "years": {},
    }


def _index_path() -> Path:
    return common.data_root() / INDEX_RELATIVE_PATH


def _load_index(source_id: str) -> dict[str, Any]:
    path = _index_path()
    if not path.exists():
        return _new_index(source_id)
    index = common.load_json(path)
    if not isinstance(index, dict) or index.get("schemaVersion") != INDEX_SCHEMA:
        raise OfficialError(f"unsupported official snapshot index: {path}")
    if index.get("sourceId") != source_id:
        raise OfficialError(f"official snapshot source mismatch: {path}")
    if not isinstance(index.get("snapshots"), list) or not isinstance(index.get("years"), dict):
        raise OfficialError(f"invalid official snapshot index: {path}")
    return index


def _save_index(index: dict[str, Any]) -> None:
    index["updatedAt"] = common.utc_now()
    common.atomic_write_json(_index_path(), index)


def _snapshot_by_id(index: Mapping[str, Any], snapshot_id: str | None) -> dict[str, Any] | None:
    if not snapshot_id:
        return None
    for snapshot in index.get("snapshots", []):
        if isinstance(snapshot, dict) and snapshot.get("snapshotId") == snapshot_id:
            return snapshot
    return None


def _usable_snapshot(
    index: Mapping[str, Any],
    snapshot_id: str | None,
    *,
    external_id: str,
    url: str,
) -> dict[str, Any] | None:
    snapshot = _snapshot_by_id(index, snapshot_id)
    if not snapshot:
        return None
    if (
        snapshot.get("externalQuestionId") != external_id
        or snapshot.get("url") != url
        or snapshot.get("fetchStatus") != "ok"
        or snapshot.get("httpStatus") != 200
    ):
        return None
    raw_path = snapshot.get("bodyPath")
    if not isinstance(raw_path, str):
        return None
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    if not (common.data_root() / relative).is_file():
        return None
    try:
        body = common.read_gzip_blob(relative)
    except (OSError, EOFError):
        return None
    if len(body) != snapshot.get("bodyBytes"):
        return None
    if common.sha256_bytes(body) != snapshot.get("bodySha256"):
        return None
    return snapshot


def _store_snapshot(
    index: dict[str, Any],
    artifact: DownloadedArtifact,
    *,
    source_id: str,
    external_id: str,
    suffix: str,
) -> dict[str, Any]:
    digest, relative_path = common.store_gzip_blob(artifact.body, suffix)
    snapshot_id = (
        f"{source_id}:{external_id}:{artifact.fetched_at}:"
        f"{digest[:12]}:{uuid.uuid4().hex[:12]}"
    )
    snapshot = {
        "snapshotId": snapshot_id,
        "sourceId": source_id,
        "externalQuestionId": external_id,
        "url": artifact.url,
        "finalUrl": artifact.final_url,
        "fetchedAt": artifact.fetched_at,
        "fetchStatus": "ok",
        "httpStatus": artifact.status_code,
        "contentType": artifact.content_type,
        "bodySha256": digest,
        "bodyBytes": len(artifact.body),
        "bodyPath": relative_path.as_posix(),
    }
    event_path = _event_path(snapshot)
    if event_path.exists():
        raise OfficialError(f"official snapshot event already exists: {event_path}")
    common.atomic_write_json(event_path, snapshot)

    # The index is the current view. Immutable fetch history lives in events/.
    existing = next(
        (
            position
            for position, value in enumerate(index["snapshots"])
            if value.get("sourceId") == source_id
            and value.get("externalQuestionId") == external_id
        ),
        None,
    )
    if existing is None:
        index["snapshots"].append(snapshot)
    else:
        index["snapshots"][existing] = snapshot
    return snapshot


def _event_path(snapshot: Mapping[str, Any]) -> Path:
    safe_external = re.sub(
        r"[^A-Za-z0-9._-]", "_", str(snapshot["externalQuestionId"])
    )
    safe_snapshot = re.sub(r"[^A-Za-z0-9._-]", "_", str(snapshot["snapshotId"]))
    return (
        common.data_root()
        / "raw"
        / "snapshots"
        / "official"
        / "events"
        / safe_external
        / f"{safe_snapshot}.json"
    )


def _answer_records_valid(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("schemaVersion") != ANSWER_SCHEMA:
        return False
    answers = value.get("answers")
    return isinstance(answers, list) and [row.get("questionNumber") for row in answers if isinstance(row, dict)] == list(TARGET_QUESTIONS)


def _artifact_url(template: str, year: Mapping[str, Any]) -> str:
    return template.format(officialCode=year["officialCode"], examYear=year["examYear"])


def acquire_official(
    config: Mapping[str, Any] | None = None,
    *,
    exam_years: Sequence[int] | None = None,
    force: bool = False,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    wall_time: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Acquire configured years serially and return a run summary."""

    config = config or common.load_target()
    official = config["officialSource"]
    source_id = str(official["id"])
    configured_years = {int(row["examYear"]): row for row in config["years"]}
    selected = list(exam_years) if exam_years is not None else list(config["target"]["examYears"])
    unknown = sorted(set(selected) - set(configured_years))
    if unknown:
        raise OfficialError(f"years are not configured: {unknown}")

    index = _load_index(source_id)
    client = SerialOfficialClient(
        config.get("fetch", {}),
        session=session,
        sleep=sleep,
        monotonic=monotonic,
        wall_time=wall_time,
    )
    summary: dict[str, Any] = {"completed": [], "skipped": [], "failed": []}

    for exam_year in selected:
        year = configured_years[exam_year]
        year_key = str(exam_year)
        question_url = _artifact_url(str(official["questionUrlTemplate"]), year)
        answer_url = _artifact_url(str(official["answerUrlTemplate"]), year)
        question_external_id = f"official-{exam_year}-questions"
        answer_external_id = f"official-{exam_year}-answers"
        year_state = index["years"].setdefault(
            year_key,
            {
                "examYear": exam_year,
                "eraYear": year.get("eraYear"),
                "officialCode": year.get("officialCode"),
                "fetchStatus": "partial",
            },
        )

        question_snapshot = None if force else _usable_snapshot(
            index,
            year_state.get("questionSnapshotId"),
            external_id=question_external_id,
            url=question_url,
        )
        answer_snapshot = None if force else _usable_snapshot(
            index,
            year_state.get("answerSnapshotId"),
            external_id=answer_external_id,
            url=answer_url,
        )
        if (
            not force
            and question_snapshot
            and answer_snapshot
            and year_state.get("fetchStatus") == "complete"
            and _answer_records_valid(year_state.get("answerDisplays"))
        ):
            summary["skipped"].append(exam_year)
            continue

        try:
            if not question_snapshot:
                artifact = client.get(question_url, "pdf")
                question_snapshot = _store_snapshot(
                    index,
                    artifact,
                    source_id=source_id,
                    external_id=question_external_id,
                    suffix="pdf",
                )
                year_state["questionSnapshotId"] = question_snapshot["snapshotId"]
                year_state["fetchStatus"] = "partial"
                year_state["updatedAt"] = common.utc_now()
                _save_index(index)

            if not answer_snapshot:
                artifact = client.get(answer_url, "html")
                answer_snapshot = _store_snapshot(
                    index,
                    artifact,
                    source_id=source_id,
                    external_id=answer_external_id,
                    suffix="html",
                )
                year_state["answerSnapshotId"] = answer_snapshot["snapshotId"]
                year_state["fetchStatus"] = "partial"
                year_state["updatedAt"] = common.utc_now()
                _save_index(index)

            answer_body = common.read_gzip_blob(answer_snapshot["bodyPath"])
            parsed = parse_official_answers(answer_body)
            year_state["answerDisplays"] = parsed
            year_state["parseWarnings"] = parsed["warnings"]
            year_state["fetchStatus"] = "complete"
            year_state.pop("lastError", None)
            year_state["updatedAt"] = common.utc_now()
            _save_index(index)
            summary["completed"].append(exam_year)
        except OfficialForbiddenError as error:
            year_state["fetchStatus"] = "failed"
            year_state["lastError"] = str(error)
            year_state["updatedAt"] = common.utc_now()
            _save_index(index)
            summary["failed"].append({"examYear": exam_year, "error": str(error)})
            raise
        except OfficialError as error:
            year_state["fetchStatus"] = "failed"
            year_state["lastError"] = str(error)
            year_state["updatedAt"] = common.utc_now()
            _save_index(index)
            summary["failed"].append({"examYear": exam_year, "error": str(error)})
            LOGGER.error("official year %s failed: %s", exam_year, error)

    summary["indexPath"] = INDEX_RELATIVE_PATH.as_posix()
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch private official exam snapshots serially")
    parser.add_argument(
        "--year",
        type=int,
        action="append",
        dest="years",
        help="fetch one configured exam year; repeat to select more than one",
    )
    parser.add_argument("--force", action="store_true", help="refetch even when a complete snapshot exists")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    try:
        summary = acquire_official(exam_years=args.years, force=args.force)
    except OfficialForbiddenError as error:
        LOGGER.error("stopped after 403: %s", error)
        return 2
    except OfficialError as error:
        LOGGER.error("official acquisition failed: %s", error)
        return 2
    for key in ("completed", "skipped", "failed"):
        print(f"{key}: {summary[key]}")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
