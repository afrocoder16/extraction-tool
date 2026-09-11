"""Local server for the review UI: serves the files and saves edits back to disk.

`python -m http.server` can only read. Proofreading corrections have to be written
somewhere, and browsers cannot write to the filesystem, so this adds three small
endpoints on top of ordinary static serving:

    GET  /api/review                    -> the saved approve/flag decisions
    POST /api/review                    -> replace them
    POST /api/transcript/<page>         -> overwrite one page's transcript

Before the first edit of any page, the untouched transcript is copied to
`transcripts/page_NNN.original.json`. Corrections to a liturgical text must always
be revertible and diffable against what the extractor originally produced.

Bound to 127.0.0.1 — this writes files, so it must not be reachable off-machine.

    python scripts/v2/serve.py
    -> http://127.0.0.1:8000/output/v2/review.html
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 8 * 1024 * 1024  # a page of spans is a few KB; this is generous
PAGE_RE = re.compile(r"^\d{1,4}$")


class ReviewHandler(SimpleHTTPRequestHandler):
    """Static file serving plus the save endpoints."""

    v2_root: Path  # set via functools.partial in main()

    # --- helpers ---------------------------------------------------------

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

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

    def _review_path(self) -> Path:
        return self.v2_root / "review.json"

    # --- routing ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        route = self.path.split("?")[0]
        if route == "/api/review":
            path = self._review_path()
            if path.exists():
                self._send_json(json.loads(path.read_text(encoding="utf-8")))
            else:
                self._send_json({"entries": {}})
            return
        if route == "/favicon.ico":
            # Browsers always ask; there isn't one, and a 404 per page load is noise.
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()

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
        path = self._review_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"entries": entries}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._send_json({"ok": True, "saved": len(entries), "path": str(path)})

    def _save_transcript(self, page_str: str, body: dict) -> None:
        # The page number becomes part of a filename, so validate it strictly
        # rather than trusting anything that arrived over the wire.
        if not PAGE_RE.match(page_str):
            self._send_json({"error": "page must be a number"}, 400)
            return
        page = int(page_str)

        if not isinstance(body.get("blocks"), list):
            self._send_json({"error": "expected a 'blocks' array"}, 400)
            return

        out_dir = self.v2_root / "transcripts"
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"page_{page:03d}.json"
        backup = out_dir / f"page_{page:03d}.original.json"

        # Preserve the pre-edit version once, so corrections stay revertible.
        if target.exists() and not backup.exists():
            shutil.copy2(target, backup)

        body["page"] = page
        target.write_text(
            json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._send_json({"ok": True, "path": str(target), "backup": backup.exists()})

    # --- misc ------------------------------------------------------------

    def end_headers(self) -> None:
        # The UI reads JSON that changes as the pipeline runs; stale caches here
        # look like lost edits.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # Show API calls (saves) and hide routine static traffic. Both log_request
        # and log_error land here, and log_error passes an HTTPStatus rather than a
        # string, so format first instead of indexing into args.
        try:
            line = fmt % args if args else fmt
        except (TypeError, ValueError):
            line = fmt
        if "/api/" in line:
            super().log_message("%s", line)

    def log_error(self, fmt: str, *args) -> None:
        # A missing transcript or page image is the normal state of a partly-run
        # pipeline, not a server fault. Report the status line, never a traceback.
        self.log_message(fmt, *args)


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--root", type=Path, default=repo_root,
                        help="directory to serve (default: the extract-tool folder)")
    args = parser.parse_args()

    v2_root = args.root / "output" / "v2"
    handler = partial(ReviewHandler, directory=str(args.root))
    handler.v2_root = v2_root          # type: ignore[attr-defined]
    ReviewHandler.v2_root = v2_root    # type: ignore[attr-defined]

    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{args.port}/output/v2/review.html"
    print(f"Serving {args.root}")
    print(f"Review UI:  {url}")
    print("Edits save to output/v2/transcripts/ — Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
