"""Review server: serves the UI for one book and saves corrections back to disk.

A browser cannot write files, so proofreading needs a small server behind the UI:

    GET  /                          the review UI
    GET  /work/<path>               masks, overlays, page images, stats
    GET  /api/book                  book identity and section table
    GET  /api/review                saved approve/flag decisions
    POST /api/review                replace them
    POST /api/transcript/<page>     overwrite one page's transcript

Before the first edit of any page the untouched transcript is copied to
`page_NNN.original.json`. Corrections to a liturgical text must always be
revertible and diffable against what the extractor originally produced.

Bound to 127.0.0.1 — it writes files, so it must not be reachable off-machine.

    python -m engine.serve books/wudase-mariam
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import book as book_mod
from .console import configure_utf8_stdio

MAX_BODY = 8 * 1024 * 1024
PAGE_RE = re.compile(r"^\d{1,4}$")
UI_FILE = Path(__file__).resolve().parent / "review.html"

CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".html": "text/html; charset=utf-8",
}


class ReviewHandler(BaseHTTPRequestHandler):
    """Serves one book. Every path is resolved inside that book's folder."""

    bk: book_mod.Book  # injected in main()

    # --- helpers ---------------------------------------------------------

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        self._send(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   CONTENT_TYPES[".json"], status)

    def _read_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _serve_file(self, path: Path) -> None:
        # The path arrives from the URL, so confirm it stayed inside the book.
        try:
            resolved = path.resolve()
            resolved.relative_to(self.bk.root.resolve())
        except (ValueError, OSError):
            self._send(b"forbidden", "text/plain", 403)
            return
        if not resolved.is_file():
            self._send(b"not found", "text/plain", 404)
            return
        self._send(resolved.read_bytes(),
                   CONTENT_TYPES.get(resolved.suffix.lower(), "application/octet-stream"))

    # --- routing ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        route = self.path.split("?")[0]

        if route in ("/", "/index.html", "/review.html"):
            self._send(UI_FILE.read_bytes(), CONTENT_TYPES[".html"])
            return
        if route == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if route == "/api/book":
            self._send_json({
                "book_id": self.bk.book_id,
                "title_am": self.bk.title_am,
                "title_en": self.bk.title_en,
                "sections": [
                    {k: s.get(k) for k in ("section_id", "order", "title_am", "title_en")}
                    for s in self.bk.sections
                ],
            })
            return
        if route == "/api/review":
            if self.bk.review_file.exists():
                self._send_json(json.loads(self.bk.review_file.read_text(encoding="utf-8")))
            else:
                self._send_json({"entries": {}})
            return
        if route.startswith("/work/"):
            self._serve_file(self.bk.work / route[len("/work/"):])
            return

        self._send(b"not found", "text/plain", 404)

    def do_POST(self) -> None:  # noqa: N802
        route = self.path.split("?")[0]
        body = self._read_body()
        if body is None:
            self._send_json({"error": "invalid or oversized JSON body"}, 400)
            return
        if route == "/api/review":
            self._save_review(body)
            return
        if route.startswith("/api/transcript/"):
            self._save_transcript(route.rsplit("/", 1)[-1], body)
            return
        self._send_json({"error": "unknown endpoint"}, 404)

    # --- handlers --------------------------------------------------------

    def _save_review(self, body: dict) -> None:
        entries = body.get("entries")
        if not isinstance(entries, dict):
            self._send_json({"error": "expected an 'entries' object"}, 400)
            return
        self.bk.work.mkdir(parents=True, exist_ok=True)
        self.bk.review_file.write_text(
            json.dumps({"entries": entries}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        self._send_json({"ok": True, "saved": len(entries)})

    def _save_transcript(self, page_str: str, body: dict) -> None:
        # The page number becomes part of a filename; validate rather than trust.
        if not PAGE_RE.match(page_str):
            self._send_json({"error": "page must be a number"}, 400)
            return
        if not isinstance(body.get("blocks"), list):
            self._send_json({"error": "expected a 'blocks' array"}, 400)
            return

        # Upgrade transcripts created before the structured review fields were
        # introduced. The UI displays these fields and verify.py distinguishes a
        # missing field from a reviewed page with an empty list.
        body.setdefault("notes", "")
        body.setdefault("corrections", [])
        body.setdefault("uncertain", [])
        body.setdefault("clipped", [])
        if not isinstance(body["notes"], str) or any(
            not isinstance(body[field], list)
            for field in ("corrections", "uncertain", "clipped")
        ):
            self._send_json({"error": "invalid transcript review fields"}, 400)
            return

        page = int(page_str)
        out_dir = self.bk.dir("transcripts")
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"page_{page:03d}.json"
        backup = out_dir / f"page_{page:03d}.original.json"

        # Preserve the pre-edit version once, so corrections stay revertible.
        if target.exists() and not backup.exists():
            shutil.copy2(target, backup)

        body["page"] = page
        target.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        self._send_json({"ok": True, "path": str(target), "backup": backup.exists()})

    # --- logging ---------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:
        # Show API calls (saves); hide routine static traffic. log_error also lands
        # here and passes an HTTPStatus, so format first rather than indexing args.
        try:
            line = fmt % args if args else fmt
        except (TypeError, ValueError):
            line = fmt
        if "/api/" in line:
            super().log_message("%s", line)

    def log_error(self, fmt: str, *args) -> None:
        # A missing transcript is the normal state of a partly-run pipeline.
        self.log_message(fmt, *args)


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book", type=Path, help="path to a book folder")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    bk = book_mod.load(args.book)
    ReviewHandler.bk = bk  # type: ignore[attr-defined]

    server = ThreadingHTTPServer(("127.0.0.1", args.port), ReviewHandler)
    print(f"Book:      {bk.title_am} ({bk.book_id})")
    print(f"Review UI: http://127.0.0.1:{args.port}/")
    print(f"Edits save to {bk.dir('transcripts')} — Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
