"""Export dots OCR candidates as plain-text files for reading and editing.

    python3 scripts/export_dots_text.py

Writes one ``page_NNN.txt`` per candidate plus ``all-pages.txt`` to
``books/wudase-mariam/work/candidates/dots-text/``. Re-running refreshes the
export; text edited in these files is not read back by the engine.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "books" / "wudase-mariam" / "work" / "candidates" / "dots"
OUT = CANDIDATES.parent / "dots-text"


def main() -> int:
    latest: dict[int, Path] = {}
    for path in CANDIDATES.glob("page_*.json"):
        match = re.fullmatch(r"page_(\d+)\.[0-9a-f]{16}\.json", path.name)
        if match:
            page = int(match.group(1))
            if page not in latest or path.stat().st_mtime > latest[page].stat().st_mtime:
                latest[page] = path

    OUT.mkdir(parents=True, exist_ok=True)
    combined = []
    for page in sorted(latest):
        cells = json.loads(latest[page].read_text(encoding="utf-8"))["layout"]
        text = "\n\n".join(c["text"] for c in cells if c.get("text"))
        (OUT / f"page_{page:03d}.txt").write_text(text + "\n", encoding="utf-8")
        combined.append(f"===== Page {page} =====\n\n{text}\n")
    (OUT / "all-pages.txt").write_text("\n".join(combined), encoding="utf-8")
    print(f"Exported {len(latest)} pages to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
