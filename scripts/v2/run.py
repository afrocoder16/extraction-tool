"""CLI entry point for the colour-aware Wudase Mariam pipeline.

    python scripts/v2/run.py --pages 21,147 --stage split
    python scripts/v2/run.py --pages sample --stage all

Stages run in order and each depends on the previous one's output, so `all` is the
normal invocation. `split` and `layout` are free and offline; `ocr` calls the API.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_json
import color_split
import layout
import verify

# Covers every layout variant found in the book: inline red words with verse
# numerals, a continuation page, section boundaries, a red title with an ornament
# border and heavy bleed-through, and the last pages with content. Excludes the
# 89 blank verso sheets and the 2 colour plates, which carry no prayer text.
SAMPLE_PAGES = [3, 21, 23, 39, 99, 147, 149, 207, 209, 217]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "output"


def parse_pages(spec: str, total: int) -> list[int]:
    """Accept 'sample', 'all', '21,147', or '21-26,99'."""
    if spec == "sample":
        return list(SAMPLE_PAGES)
    if spec == "all":
        return list(range(1, total + 1))

    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            pages.extend(range(int(start), int(end) + 1))
        else:
            pages.append(int(part))
    return sorted(set(pages))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", default="sample", help="'sample', 'all', or e.g. 21,39-42")
    parser.add_argument(
        "--stage",
        default="all",
        choices=["all", "split", "layout", "audit", "ocr", "verify", "build"],
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--total-pages", type=int, default=218)
    parser.add_argument("--tesseract", type=Path, default=Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"))
    parser.add_argument("--lang", default="amh")
    parser.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--force", action="store_true", help="re-transcribe cached pages")
    args = parser.parse_args()

    pages = parse_pages(args.pages, args.total_pages)
    v2_root = args.output / "v2"
    page_images = args.output / "page_images"
    tessdata = args.output / "tessdata"

    run_all = args.stage == "all"

    if run_all or args.stage == "split":
        print(f"[split] {len(pages)} pages")
        for stat in color_split.split_pages(page_images, v2_root, pages):
            if stat.is_blank or stat.is_illustration:
                kind = "blank scan" if stat.is_blank else "colour illustration"
                print(f"  page {stat.page}: {kind} (skipped downstream)")
                continue
            print(
                f"  page {stat.page}: red {stat.red_ink_ratio:.1%} of ink, "
                f"{stat.red_components_kept} glyph groups, "
                f"{stat.red_components_dropped_ornament} ornaments dropped"
            )

    # Blank verso sheets carry no prayer text. Excluding them here keeps them out
    # of the OCR bill, the verification report, and the final JSON.
    blanks = color_split.blank_pages(v2_root, pages)
    live = [p for p in pages if p not in blanks]
    if blanks:
        print(f"[skip] {len(blanks)} blank pages excluded; {len(live)} remain")
    pages = live

    if run_all or args.stage == "layout":
        print(f"[layout] {len(pages)} pages")
        for result in layout.analyse_pages(pages, v2_root, args.tesseract, tessdata, args.lang):
            print(
                f"  page {result['page']}: {result['line_count']} lines, "
                f"{result['red_word_count']}/{result['word_count']} words red"
            )

    if run_all or args.stage == "audit":
        path = color_split.write_audit(v2_root)
        stats = color_split.load_stats(v2_root)
        content = [p for p, s in stats.items()
                   if not s["is_blank"] and not s.get("is_illustration")]
        no_red = [p for p in content
                  if stats[p]["red_ink_ratio"] < color_split.SUSPICIOUS_NO_RED]
        too_much = [p for p in content
                    if stats[p]["red_ink_ratio"] > color_split.SUSPICIOUS_TOO_MUCH_RED]
        print(f"[audit] {len(content)} content pages, "
              f"{len(stats) - len(content)} blank")
        print(f"  no red detected: {len(no_red)} -> "
              f"{', '.join('p.' + str(p) for p in no_red[:12]) or 'none'}")
        print(f"  red over 70% of ink: {len(too_much)} -> "
              f"{', '.join('p.' + str(p) for p in too_much[:12]) or 'none'}")
        print(f"  wrote {path}")

    if run_all or args.stage == "ocr":
        print(f"[ocr] {len(pages)} pages")
        import transcribe

        transcribe.transcribe_pages(pages, v2_root, args.effort, args.force)

    if run_all or args.stage == "verify":
        print(f"[verify] {len(pages)} pages")
        reports = verify.verify_pages(pages, v2_root)
        path = verify.write_report(reports, v2_root)
        flagged = [r for r in reports if r["review_flags"]]
        for report in flagged:
            print(f"  page {report['page']}: " + "; ".join(report["review_flags"]))
        print(f"  {len(flagged)}/{len(reports)} pages flagged -> {path}")

    if run_all or args.stage == "build":
        print("[build]")
        transcripts = build_json.load_transcripts(pages, v2_root)
        book = build_json.build_book(transcripts, v2_root, args.total_pages)
        full, minified = build_json.write_outputs(book, v2_root)
        print(f"  {len(transcripts)} pages -> {book['sections_count']} sections")
        print(f"  wrote {full}")
        print(f"  wrote {minified}")


if __name__ == "__main__":
    main()
