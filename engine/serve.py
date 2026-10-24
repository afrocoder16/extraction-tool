"""Review server: serves the UI for local books and saves corrections back to disk.

A browser cannot write files, so proofreading needs a small server behind the UI.
URLs match the hosted (Netlify) site so one UI serves both:

    GET  /                                    the review UI (library, or ?book=<id>)
    GET  /books.json                          every local book
    GET  /books/<id>/book.json                book identity and section table
    GET  /books/<id>/work/<path>              masks, overlays, page images, stats
    GET  /api/books/<id>/review               saved approve/flag decisions
    POST /api/books/<id>/review               merge decisions
    GET  /api/books/<id>/transcript/<page>    one page's transcript
    POST /api/books/<id>/transcript/<page>    overwrite it
    GET  /api/books/<id>/history/<page>       earlier saved versions

Before the first edit of any page the untouched transcript is copied to
`page_NNN.original.json`. Corrections to a liturgical text must always be
revertible and diffable against what the extractor originally produced.

Bound to 127.0.0.1 — it writes files, so it must not be reachable off-machine.

    python -m engine.serve                       # every folder under books/
    python -m engine.serve books/wudase-mariam   # just these books
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import book as book_mod
from .console import configure_utf8_stdio
from . import review_store

MAX_BODY = 8 * 1024 * 1024
PAGE_RE = re.compile(r"^\d{1,4}$")
ROOT = Path(__file__).resolve().parent.parent
UI_FILE = Path(__file__).resolve().parent / "review.html"

CONTENT_TYPES = {
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".html": "text/html; charset=utf-8",
}


class ReviewHandler(BaseHTTPRequestHandler):
    """Serves several books. Every path is resolved inside its own book's folder."""

    books: dict[str, book_mod.Book] = {}  # book_id -> Book, injected in main()
    bk: book_mod.Book  # the book addressed by the current request

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
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            return value if isinstance(value, dict) else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _serve_file(self, path: Path, allowed_root: Path | None = None) -> None:
        # The path arrives from the URL, so confirm it stayed inside the book.
        try:
            resolved = path.resolve()
            resolved.relative_to((allowed_root or self.bk.root).resolve())
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
        if route == "/books.json":
            self._send_json([self._summary(b) for b in self.books.values()])  # type: ignore[arg-type]
            return
        match = re.fullmatch(r"/(api/)?books/([a-z0-9_]+)(/.*)", route)
        if not match or match[2] not in self.books:
            self._send(b"not found", "text/plain", 404)
            return
        self.bk = self.books[match[2]]
        rest = match[3]
        if match[1]:
            self._api_get(rest)
            return
        if rest == "/book.json":
            self._send_json({**self._summary(self.bk), "hosting": "local", "image_extension": "png",
                             "sections": [{k: s.get(k) for k in ("section_id", "order", "title_am", "title_en")}
                                          for s in self.bk.sections]})
            return
        if rest.startswith("/work/"):
            # Browser-size copies are review aids, never original-image evidence.
            image = re.fullmatch(r"/work/(page_images|debug)/(page_\d{3,})\.png", rest)
            if image and not (self.bk.work / image[1] / (image[2] + ".png")).exists():
                assets = ROOT / "netlify" / "review-assets" / self.bk.book_id / "work"
                self._serve_file(assets / image[1] / (image[2] + ".webp"), assets)
                return
            self._serve_file(self.bk.work / rest[len("/work/"):])
            return

        self._send(b"not found", "text/plain", 404)

    def _api_get(self, rest: str) -> None:
        if rest == "/review":
            if self.bk.review_file.exists():
                self._send_json(json.loads(self.bk.review_file.read_text(encoding="utf-8")))
            else:
                self._send_json({"entries": {}})
            return
        match = re.fullmatch(r"/(transcript|history)/(\d{1,4})", rest)
        if not match or int(match[2]) < 1:
            self._send_json({"error": "unknown endpoint"}, 404)
            return
        page = int(match[2])
        if match[1] == "history":
            folder = self.bk.work / "history" / str(page)
            versions = sorted((f.stat().st_mtime for f in folder.glob("*.json")), reverse=True) if folder.is_dir() else []
            self._send_json({"page": page, "versions": [
                {"saved_at": datetime.fromtimestamp(t, timezone.utc).isoformat(), "saved_by": "local"}
                for t in versions[:50]]})
            return
        try:
            self._send_json(review_store.load_transcript(self.bk.work, page))
        except FileNotFoundError:
            self._send_json({"error": "no transcript"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        route = self.path.split("?")[0]
        body = self._read_body()
        if body is None:
            self._send_json({"error": "invalid or oversized JSON body"}, 400)
            return
        match = re.fullmatch(r"/api/books/([a-z0-9_]+)/(review|transcript/(\d{1,4}))", route)
        if not match or match[1] not in self.books:
            self._send_json({"error": "unknown endpoint"}, 404)
            return
        self.bk = self.books[match[1]]
        if match[2] == "review":
            self._save_review(body)
        else:
            self._save_transcript(match[3], body)

    @staticmethod
    def _summary(bk: book_mod.Book) -> dict:
        stats = json.loads(bk.stats_file.read_text(encoding="utf-8")) if bk.stats_file.exists() else {}
        return {"book_id": bk.book_id, "title_am": bk.title_am, "title_en": bk.title_en,
                "total_pages": len(stats),
                "text_pages": sum(1 for v in stats.values() if not v.get("is_blank") and not v.get("is_illustration"))}

    # --- handlers --------------------------------------------------------

    def _save_review(self, body: dict) -> None:
        try:
            saved = review_store.save_review(self.bk.work, body.get("entries"))
            self._send_json({"ok": True, "saved": len(saved["entries"])})
        except review_store.RevisionConflict as error:
            self._send_json({"error": str(error)}, 409)
        except ValueError as error:
            self._send_json({"error": str(error)}, 400)

    def _save_transcript(self, page_str: str, body: dict) -> None:
        # The page number becomes part of a filename; validate rather than trust.
        if not PAGE_RE.fullmatch(page_str) or int(page_str) < 1:
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

        for field in ("_saved_at", "_saved_by", "_base_saved_at"):
            body.pop(field, None)
        try:
            saved = review_store.save_transcript(self.bk.work, int(page_str), body)
            self._send_json({"ok": True, "transcript": saved})
        except review_store.RevisionConflict as error:
            self._send_json({"error": str(error)}, 409)
        except ValueError as error:
            self._send_json({"error": str(error)}, 400)

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
    parser.add_argument("books", type=Path, nargs="*", help="book folders (default: every folder under books/)")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    folders = args.books or sorted(p.parent for p in (ROOT / "books").glob("*/book.json"))
    ReviewHandler.books = {bk.book_id: bk for bk in map(book_mod.load, folders)}
    if not ReviewHandler.books:
        parser.error("no books found")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), ReviewHandler)
    for bk in ReviewHandler.books.values():
        print(f"Book:      {bk.title_am} ({bk.book_id}) — edits save to {bk.dir('transcripts')}")
    print(f"Review UI: http://127.0.0.1:{args.port}/   (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
