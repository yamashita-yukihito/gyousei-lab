"""Serial, resumable acquisition of private provider question snapshots."""

from __future__ import annotations

import argparse
import email.utils
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .common import (
    atomic_write_json,
    data_root,
    load_json,
    load_target,
    read_gzip_blob,
    sha256_bytes,
    store_gzip_blob,
    utc_now,
)


SNAPSHOTS_SCHEMA = "snapshots@1"


class FetchError(RuntimeError):
    """A snapshot could not be safely acquired."""


class ForbiddenError(FetchError):
    """HTTP 403 is a hard stop for the whole run."""


class UnexpectedPageError(FetchError):
    """The response is a login/error/non-question page."""


class SerialFetcher:
    """A one-request-at-a-time client with a hard minimum one-second gap."""

    def __init__(
        self,
        *,
        session: Any | None = None,
        user_agent: str,
        minimum_delay_seconds: float = 1.0,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self.session = session or requests.Session()
        if hasattr(self.session, "headers"):
            self.session.headers.update({"User-Agent": user_agent})
        self.user_agent = user_agent
        self.minimum_delay_seconds = max(1.0, float(minimum_delay_seconds))
        self.timeout_seconds = float(timeout_seconds)
        self.max_attempts = int(max_attempts)
        self.sleep = sleep
        self.monotonic = monotonic
        self._last_request_at: float | None = None
        self._retry_not_before = 0.0

    def _wait_for_slot(self) -> None:
        now = self.monotonic()
        wait = max(0.0, self._retry_not_before - now)
        if self._last_request_at is not None:
            wait = max(wait, self.minimum_delay_seconds - (now - self._last_request_at))
        if wait:
            self.sleep(wait)

    @staticmethod
    def _retry_after_seconds(response: Any) -> float:
        raw = str(response.headers.get("Retry-After", "")).strip()
        if not raw:
            return 0.0
        try:
            return max(0.0, float(raw))
        except ValueError:
            try:
                parsed = email.utils.parsedate_to_datetime(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return 0.0

    def get(self, url: str) -> Any:
        """GET once, retrying only 429/5xx up to the configured total attempts."""

        for attempt in range(1, self.max_attempts + 1):
            self._wait_for_slot()
            self._last_request_at = self.monotonic()
            self._retry_not_before = 0.0
            try:
                response = self.session.get(
                    url,
                    timeout=self.timeout_seconds,
                    allow_redirects=True,
                    headers={"User-Agent": self.user_agent},
                )
            except requests.RequestException as exc:
                # The contract intentionally retries HTTP 429/5xx only.
                raise FetchError(f"GET failed without retry: {url}: {exc}") from exc

            status = int(response.status_code)
            if status == 403:
                raise ForbiddenError(f"HTTP 403; stopping acquisition: {url}")
            if status == 429 or 500 <= status <= 599:
                if attempt >= self.max_attempts:
                    raise FetchError(
                        f"Retry limit reached after {attempt} attempts: HTTP {status} {url}"
                    )
                retry_after = self._retry_after_seconds(response)
                self._retry_not_before = self.monotonic() + max(
                    self.minimum_delay_seconds, retry_after
                )
                continue
            if status != 200:
                raise FetchError(f"Non-retryable HTTP {status}: {url}")
            return response
        raise AssertionError("unreachable")


def validate_question_response(response: Any, entry: Mapping[str, Any]) -> None:
    """Reject successful HTTP responses that are not the expected question page."""

    final_url = str(getattr(response, "url", entry["url"]))
    content_type = str(response.headers.get("Content-Type", ""))
    if "html" not in content_type.lower():
        raise UnexpectedPageError(f"Expected HTML but received {content_type or 'unknown'}")
    if "/member/w_login.php" in urlparse(final_url).path:
        raise UnexpectedPageError(f"Redirected to login page: {final_url}")

    body = bytes(response.content)
    if not body:
        raise UnexpectedPageError("Empty response body")
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    heading = soup.select_one(".tit_status h3")
    heading_text = heading.get_text(" ", strip=True) if heading else ""
    error_text = " ".join(
        node.get_text(" ", strip=True) for node in soup.select("h1, h2, h3")[:4]
    )
    if soup.select_one('input[type="password"]') or "ログイン" in title:
        raise UnexpectedPageError(f"Login page detected: {final_url}")
    if any(
        marker in (title + " " + error_text)
        for marker in ("エラー", "ページが見つかりません", "Not Found", "アクセスできません")
    ):
        raise UnexpectedPageError(f"Error page detected: {final_url}")
    if soup.select_one(".mondai-wrap") is None or heading is None:
        raise UnexpectedPageError(f"Question page markers missing: {final_url}")
    expected_number = int(entry["questionNumber"])
    if not re.search(rf"問\s*{expected_number}(?:\D|$)", heading_text):
        raise UnexpectedPageError(
            f"Question number mismatch; expected {expected_number}: {heading_text}"
        )


def _empty_index() -> dict[str, Any]:
    return {"schemaVersion": SNAPSHOTS_SCHEMA, "updatedAt": utc_now(), "items": []}


def load_snapshot_index(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_index()
    value = load_json(path)
    if value.get("schemaVersion") != SNAPSHOTS_SCHEMA or not isinstance(value.get("items"), list):
        raise FetchError(f"Invalid snapshot index: {path}")
    return value


def _snapshot_key(item: Mapping[str, Any]) -> tuple[str, str]:
    return str(item["sourceId"]), str(item["externalQuestionId"])


def _reusable(snapshot: Mapping[str, Any]) -> bool:
    try:
        event_path = _snapshot_path(snapshot)
        if not event_path.is_file():
            return False
        event = load_json(event_path)
        if (
            event.get("snapshotId") != snapshot.get("snapshotId")
            or event.get("bodySha256") != snapshot.get("bodySha256")
        ):
            return False
        body = read_gzip_blob(str(snapshot["bodyPath"]))
    except (OSError, KeyError, ValueError, TypeError):
        return False
    return (
        len(body) == int(snapshot.get("bodyBytes", -1))
        and sha256_bytes(body) == snapshot.get("bodySha256")
        and int(snapshot.get("httpStatus", 0)) == 200
    )


def _snapshot_path(item: Mapping[str, Any]) -> Path:
    safe_external = re.sub(r"[^A-Za-z0-9._-]", "_", str(item["externalQuestionId"]))
    safe_snapshot = re.sub(r"[^A-Za-z0-9._-]", "_", str(item["snapshotId"]))
    return (
        data_root()
        / "raw"
        / "snapshots"
        / "provider"
        / safe_external
        / f"{safe_snapshot}.json"
    )


def _write_index(index_path: Path, items: list[dict[str, Any]]) -> dict[str, Any]:
    document = {
        "schemaVersion": SNAPSHOTS_SCHEMA,
        "updatedAt": utc_now(),
        "items": items,
    }
    atomic_write_json(index_path, document)
    return document


def fetch_catalog(
    catalog: Mapping[str, Any],
    *,
    fetcher: SerialFetcher,
    index_path: Path | None = None,
) -> dict[str, Any]:
    """Fetch catalog entries serially and persist after every successful page."""

    if catalog.get("schemaVersion") != "catalog@1" or not isinstance(catalog.get("entries"), list):
        raise FetchError("Expected catalog@1 with entries")
    entries = list(catalog["entries"])
    keys = [_snapshot_key(entry) for entry in entries]
    if len(set(keys)) != len(keys):
        raise FetchError("Duplicate sourceId/externalQuestionId in catalog")

    index_path = index_path or data_root() / "raw" / "snapshots" / "index.json"
    current = load_snapshot_index(index_path)
    current_keys = [_snapshot_key(item) for item in current["items"]]
    if len(set(current_keys)) != len(current_keys):
        raise FetchError("Duplicate sourceId/externalQuestionId in snapshot index")
    by_key = {_snapshot_key(item): dict(item) for item in current["items"]}

    for entry in entries:  # Deliberately sequential: concurrency is exactly one.
        key = _snapshot_key(entry)
        existing = by_key.get(key)
        if existing and existing.get("url") == entry["url"] and _reusable(existing):
            continue

        response = fetcher.get(str(entry["url"]))
        validate_question_response(response, entry)
        body = bytes(response.content)
        digest, relative_path = store_gzip_blob(body, "html")
        try:
            stored_body = read_gzip_blob(relative_path)
        except OSError as exc:
            raise FetchError(f"Stored blob is unreadable: {relative_path}") from exc
        if stored_body != body:
            raise FetchError(f"Content-addressed blob integrity failure: {relative_path}")
        fetched_at = utc_now()
        snapshot = {
            "snapshotId": (
                f"{entry['sourceId']}:{entry['externalQuestionId']}:"
                f"{fetched_at}:{digest[:12]}:{uuid.uuid4().hex[:12]}"
            ),
            "sourceId": str(entry["sourceId"]),
            "externalQuestionId": str(entry["externalQuestionId"]),
            "url": str(entry["url"]),
            "finalUrl": str(getattr(response, "url", entry["url"])),
            "fetchedAt": fetched_at,
            "fetchStatus": "ok",
            "httpStatus": int(response.status_code),
            "contentType": str(response.headers.get("Content-Type", "")),
            "bodySha256": digest,
            "bodyBytes": len(body),
            "bodyPath": relative_path.as_posix(),
        }
        # Snapshot JSON contains metadata only. Provider HTML, including its
        # explanation, remains solely in the private content-addressed blob.
        event_path = _snapshot_path(snapshot)
        if event_path.exists():  # UUID collision or external tampering: never overwrite history.
            raise FetchError(f"Snapshot event already exists: {event_path}")
        atomic_write_json(event_path, snapshot)
        by_key[key] = snapshot
        ordered = [by_key[_snapshot_key(item)] for item in entries if _snapshot_key(item) in by_key]
        _write_index(index_path, ordered)

    ordered = [by_key[_snapshot_key(item)] for item in entries if _snapshot_key(item) in by_key]
    return _write_index(index_path, ordered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=data_root() / "catalog" / "questions.json",
    )
    parser.add_argument(
        "--index",
        type=Path,
        default=data_root() / "raw" / "snapshots" / "index.json",
    )
    args = parser.parse_args(argv)
    config = load_target()
    policy = config["fetch"]
    fetcher = SerialFetcher(
        user_agent=str(policy["userAgent"]),
        minimum_delay_seconds=float(policy["minimumDelaySeconds"]),
        timeout_seconds=float(policy["timeoutSeconds"]),
        max_attempts=int(policy["maxAttempts"]),
    )
    fetch_catalog(load_json(args.catalog), fetcher=fetcher, index_path=args.index)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
