"""Shared paths and deterministic file helpers."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = (
    Path.home()
    / ".local"
    / "share"
    / "yuki-services"
    / "gyousei-lab"
    / "authoring"
)


def data_root() -> Path:
    return Path(os.environ.get("GYOUSEI_DATA_ROOT", DEFAULT_DATA_ROOT))


def target_path() -> Path:
    return Path(os.environ.get("GYOUSEI_TARGET_CONFIG", PACKAGE_ROOT / "config" / "target.json"))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_target() -> dict[str, Any]:
    return load_json(target_path())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def atomic_write_json(path: Path, value: Any) -> None:
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write_bytes(path, encoded)


def store_gzip_blob(content: bytes, suffix: str) -> tuple[str, Path]:
    digest = sha256_bytes(content)
    relative = Path("raw") / "blobs" / "sha256" / digest[:2] / f"{digest}.{suffix}.gz"
    destination = data_root() / relative
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(destination, gzip.compress(content, compresslevel=9, mtime=0))
    return digest, relative


def read_gzip_blob(relative_path: str | Path) -> bytes:
    with gzip.open(data_root() / relative_path, "rb") as source:
        return source.read()


def normalize_text(value: str) -> str:
    return " ".join(value.replace("\u3000", " ").split())
