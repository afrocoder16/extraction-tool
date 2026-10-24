"""Merge a hosted-reviewer export (``Export all saved changes``) into a book.

    python -m engine.import_review books/wudase-mariam ~/Downloads/wudase_mariam_review_export.json

Every hosted transcript replaces the page's working transcript; the version it
replaces is kept as ``page_NNN.original.json`` (the editor's Revert target) and in
``work/history/``. Review decisions are merged. An approval is carried over only
for pages whose text arrived in the same export, because an approval of a hosted
page that was never edited refers to an older baseline. The export file itself
is archived under ``work/review-exports/``.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import shutil
import sys
from pathlib import Path

from . import book as book_mod
from .provenance import atomic_json, json_hash, page_revision, read_json
from .schema import validate_transcript


def import_export(bk, export: dict, source: Path | None = None) -> dict:
    if export.get("book_id") != bk.book_id:
        raise ValueError(f"export is for {export.get('book_id')!r}, not {bk.book_id!r}")
    work = bk.work
    imported = []
    for key, transcript in sorted(export.get("transcripts", {}).items()):
        page = int(key.removeprefix("page_"))
        transcript = {**transcript, "page": page}
        validate_transcript(transcript, page, legacy=True)
        transcript["_review_edit"] = export.get("edit_history", {}).get(key, {})
        target = work / "transcripts" / f"page_{page:03d}.json"
        current = read_json(target)
        if current is not None and current != transcript:
            backup = target.with_name(f"page_{page:03d}.original.json")
            if not backup.exists():
                atomic_json(backup, current)
            atomic_json(work / "history" / str(page) / f"{json_hash(current)}.json", current)
        atomic_json(target, transcript)
        imported.append(page)

    review = read_json(work / "review.json", {"entries": {}})
    entries = review.setdefault("entries", {})
    approved, withdrawn = [], []
    for key, raw in export.get("review", {}).get("entries", {}).items():
        entry = {"status": raw.get("status"), "note": (raw.get("note") or "").strip()}
        if entry["status"] == "approved":
            page = int(key)
            if page in imported:
                transcript = read_json(work / "transcripts" / f"page_{page:03d}.json")
                entry.update(page_revision(work, page, transcript),
                             reviewed_at=export.get("exported_at"), reviewed_by="hosted reviewer")
                approved.append(page)
            else:
                entry.update(status=None, note=(entry["note"] + " [approval withdrawn on import: it referred "
                             "to an older baseline text]").strip())
                withdrawn.append(page)
        entries[str(int(key))] = entry
    atomic_json(work / "review.json", review)

    if source is not None:
        archive = work / "review-exports"
        archive.mkdir(parents=True, exist_ok=True)
        stamp = (export.get("exported_at") or datetime.now(timezone.utc).isoformat())[:19].replace(":", "")
        shutil.copy2(source, archive / f"{bk.book_id}_review_export_{stamp}.json")
    return {"transcripts_imported": imported, "approvals_kept": approved,
            "approvals_withdrawn": withdrawn, "review_entries": len(export.get("review", {}).get("entries", {}))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("book", type=Path)
    parser.add_argument("export", type=Path)
    args = parser.parse_args(argv)
    bk = book_mod.load(args.book)
    summary = import_export(bk, json.loads(args.export.read_text(encoding="utf-8")), args.export)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
