"""Revision fingerprints and atomic JSON writes, without OCR dependencies."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


def json_hash(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def atomic_json(path: Path, value: object, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            name = stream.name
            json.dump(value, stream, ensure_ascii=False, indent=None if compact else 2,
                      separators=(",", ":") if compact else None, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def page_revision(work: Path, page: int, transcript: dict) -> dict:
    """Bind review to content plus the exact scan, mask, layout, and classification."""
    stem = f"page_{page:03d}"
    inputs = {folder: file_hash(work / folder / f"{stem}.{ext}") for folder, ext in (
        ("page_images", "png"), ("red_mask", "png"), ("black_layer", "png"),
        ("red_layer", "png"), ("layout", "json"),
    )}
    stats = read_json(work / "page_stats.json", {})
    manifest = read_json(work / "source_manifest.json", {})
    inputs["classification"] = json_hash(stats.get(str(page)))
    inputs["source_sha256"] = manifest.get("source_sha256")
    return {"transcript_sha256": json_hash(transcript), "evidence_sha256": json_hash(inputs)}


def revision_matches(record: dict, revision: dict) -> bool:
    return all(record.get(key) == value for key, value in revision.items())


def source_problems(bk, total_pages: int) -> list[str]:
    manifest = read_json(bk.work / "source_manifest.json", {})
    issues = []
    try:
        source_hash = file_hash(bk.source_pdf)
    except FileNotFoundError:
        source_hash = None
    if source_hash is None:
        issues.append("original PDF is missing")
    if not manifest:
        issues.append("source manifest is missing; render the original PDF")
        return issues
    if source_hash is None or manifest.get("source_sha256") != source_hash:
        issues.append("source PDF does not match the render manifest")
    if manifest.get("pages") != total_pages or total_pages < 1:
        issues.append("source page count does not match the render manifest")
    if manifest.get("dpi") != bk.dpi:
        issues.append("render resolution changed")
    rendered = manifest.get("rendered", {})
    for page in range(1, total_pages + 1):
        actual = file_hash(bk.page_images / f"page_{page:03d}.png")
        if actual is None or rendered.get(str(page)) != actual:
            issues.append(f"page {page}: original render is missing or changed")
    return issues
