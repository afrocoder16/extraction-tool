"""Revision-aware proofreading storage for the local review endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from .provenance import atomic_json, json_hash, page_revision, read_json, revision_matches
from .schema import validate_transcript

LOCK = RLock()


class RevisionConflict(ValueError):
    pass


def load_transcript(work: Path, page: int) -> dict:
    transcript = read_json(work / "transcripts" / f"page_{page:03d}.json")
    if transcript is None:
        raise FileNotFoundError(f"page {page} has no saved transcript")
    revision = page_revision(work, page, transcript)
    return {**transcript, "_revision": revision["transcript_sha256"],
            "_evidence_revision": revision["evidence_sha256"]}


def save_transcript(work: Path, page: int, payload: dict) -> dict:
    with LOCK:
        body = dict(payload)
        expected = body.pop("_revision", None)
        body.pop("_evidence_revision", None)
        body["page"] = page
        body.setdefault("notes", "")
        for key in ("corrections", "uncertain", "clipped"):
            body.setdefault(key, [])
        validate_transcript(body, page)
        target = work / "transcripts" / f"page_{page:03d}.json"
        current = read_json(target)
        if current is not None and expected != json_hash(current):
            raise RevisionConflict("This page changed since it was opened. Reload before saving.")
        if current is None and expected is not None:
            raise RevisionConflict("The saved transcript no longer exists.")
        if current is not None and current != body:
            backup = target.with_name(f"page_{page:03d}.original.json")
            if not backup.exists():
                atomic_json(backup, current)
            atomic_json(work / "history" / str(page) / f"{json_hash(current)}.json", current)
        atomic_json(target, body)
        if current != body:
            review_file = work / "review.json"
            review = read_json(review_file, {"entries": {}})
            entry = review.get("entries", {}).get(str(page))
            if entry and entry.get("status") == "approved":
                entry.update(status=None, invalidated_at=datetime.now(timezone.utc).isoformat())
                atomic_json(review_file, review)
        return load_transcript(work, page)


def save_review(work: Path, entries: dict) -> dict:
    """Merge decisions; only revisions actually displayed can be approved."""
    if not isinstance(entries, dict):
        raise ValueError("expected review entries")
    with LOCK:
        result = read_json(work / "review.json", {"entries": {}}).get("entries", {})
        for key, raw in entries.items():
            if not str(key).isdigit() or int(key) < 1 or not isinstance(raw, dict):
                raise ValueError("invalid review page")
            status = raw.get("status")
            if status not in {None, "approved", "needs_fix"} or not isinstance(raw.get("note", ""), str):
                raise ValueError("invalid review decision")
            entry = {**raw, "note": raw.get("note", "").strip(), "status": status}
            if status == "approved":
                transcript = read_json(work / "transcripts" / f"page_{int(key):03d}.json")
                if transcript is None:
                    raise ValueError("cannot approve a page without a transcript")
                revision = page_revision(work, int(key), transcript)
                if not revision_matches(raw, revision):
                    raise RevisionConflict("Approval refers to an older transcript or scan. Reload the page.")
                entry["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            result[str(int(key))] = entry
        payload = {"entries": result}
        atomic_json(work / "review.json", payload)
        return payload
