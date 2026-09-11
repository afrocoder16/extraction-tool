"""Command line for the extraction engine.

    python -m engine books/wudase-mariam --stage all
    python -m engine books/wudase-mariam --stage split --pages 21,147
    python -m engine --list

Stages run in dependency order, so `--stage all` is the normal invocation:

    render   PDF -> page images                        (free)
    split    separate red ink from black, find blanks  (free)
    layout   word geometry + deterministic red flags   (free)
    audit    red-coverage report for eyeballing        (free)
    ocr      transcribe with Claude vision             (costs money)
    verify   cross-check text against the pixel mask   (free)
    build    assemble the finished book JSON           (free)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import book as book_mod
from . import build_book, color_split, layout, render, verify
from .console import configure_utf8_stdio

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOKS_DIR = REPO_ROOT / "books"

STAGES = ["render", "split", "layout", "audit", "ocr", "verify", "build"]


def parse_pages(spec: str, total: int) -> list[int]:
    """'all', '21', '21,147', '21-26,99'."""
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


def list_books() -> int:
    roots = book_mod.discover(BOOKS_DIR)
    if not roots:
        print(f"No books found in {BOOKS_DIR}")
        return 1
    print(f"{len(roots)} book(s) in {BOOKS_DIR}:\n")
    for root in roots:
        bk = book_mod.load(root)
        pages = len(list(bk.page_images.glob("page_*.png")))
        done = len(bk.transcript_files())
        print(f"  {root.name}")
        print(f"    {bk.title_am}  ({bk.title_en})")
        print(f"    {len(bk.sections)} sections · {pages} pages rendered · "
              f"{done} transcribed")
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="python -m engine",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("book", nargs="?", type=Path,
                        help="path to a book folder, e.g. books/wudase-mariam")
    parser.add_argument("--list", action="store_true", help="list available books")
    parser.add_argument("--stage", default="all", choices=["all", *STAGES])
    parser.add_argument("--pages", default="all", help="'all', '21', '21,147', '21-26'")
    parser.add_argument("--force", action="store_true",
                        help="redo work that would otherwise be skipped")
    parser.add_argument(
        "--require-ready", action="store_true",
        help="with build, refuse to write unless every content page is verified and approved",
    )
    parser.add_argument("--tesseract", type=Path,
                        default=Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"))
    parser.add_argument("--tessdata", type=Path, default=REPO_ROOT / "engine" / "tessdata")
    args = parser.parse_args(argv)

    if args.list or args.book is None:
        return list_books()

    bk = book_mod.load(args.book)
    bk.ensure_dirs()
    run_all = args.stage == "all"
    print(f"book: {bk.title_am} ({bk.book_id})  ->  {bk.root}")

    total = render.page_count(bk.source_pdf) if bk.source_pdf.exists() else 0

    # --- render -----------------------------------------------------------
    if run_all or args.stage == "render":
        written = render.render_pdf(
            bk.source_pdf, bk.page_images, bk.dpi, args.force,
            None if args.pages == "all" else parse_pages(args.pages, total or 10_000),
        )
        print(f"[render] {total} pages in PDF; wrote {len(written)} "
              f"({total - len(written)} already present)")

    if not total:
        total = len(list(bk.page_images.glob("page_*.png")))
    pages = parse_pages(args.pages, total)

    # --- split ------------------------------------------------------------
    if run_all or args.stage == "split":
        print(f"[split] {len(pages)} pages")
        for stat in color_split.split_pages(bk.page_images, bk.work, pages):
            if stat.is_blank or stat.is_illustration:
                kind = "blank scan" if stat.is_blank else "colour illustration"
                print(f"  page {stat.page}: {kind} (skipped downstream)")
            elif len(pages) <= 30:
                print(f"  page {stat.page}: red {stat.red_ink_ratio:.1%} of ink, "
                      f"{stat.red_components_kept} glyph groups, "
                      f"{stat.red_components_dropped_ornament} ornaments dropped")

    # Pages with no prayer text are excluded from every later stage.
    skip = color_split.blank_pages(bk.work, pages)
    if skip:
        pages = [p for p in pages if p not in skip]
        print(f"[skip] {len(skip)} blank/illustration pages excluded; {len(pages)} remain")

    # --- layout -----------------------------------------------------------
    if run_all or args.stage == "layout":
        print(f"[layout] {len(pages)} pages")
        results = layout.analyse_pages(
            pages, bk.work, args.tesseract, args.tessdata, bk.tesseract_lang,
            page_images=bk.page_images,
        )
        words = sum(r["word_count"] for r in results)
        red = sum(r["red_word_count"] for r in results)
        print(f"  {words} words, {red} red ({100 * red / max(1, words):.1f}%)")

    # --- audit ------------------------------------------------------------
    if run_all or args.stage == "audit":
        path = color_split.write_audit(bk.work)
        stats = color_split.load_stats(bk.work)
        content = [p for p, s in stats.items()
                   if not s["is_blank"] and not s.get("is_illustration")]
        no_red = [p for p in content
                  if stats[p]["red_ink_ratio"] < color_split.SUSPICIOUS_NO_RED]
        print(f"[audit] {len(content)} text pages, {len(stats) - len(content)} skipped")
        print(f"  no red detected: {len(no_red)} -> "
              f"{', '.join('p.' + str(p) for p in no_red[:12]) or 'none'}")
        print(f"  wrote {path}")

    # --- ocr --------------------------------------------------------------
    if run_all or args.stage == "ocr":
        print(f"[ocr] {len(pages)} pages")
        from . import transcribe

        transcribe.transcribe_pages(
            pages, bk.work, bk.effort, args.force,
            model=bk.model, page_images=bk.page_images,
        )

    # --- verify -----------------------------------------------------------
    if run_all or args.stage == "verify":
        print(f"[verify] {len(pages)} pages")
        reports = verify.verify_pages(pages, bk.work)
        path = verify.write_report(reports, bk.work)
        flagged = [r for r in reports if r["review_flags"]]
        for report in flagged[:20]:
            print(f"  page {report['page']}: " + "; ".join(report["review_flags"]))
        if len(flagged) > 20:
            print(f"  ... and {len(flagged) - 20} more")
        print(f"  {len(flagged)}/{len(reports)} pages flagged -> {path}")

    # --- build ------------------------------------------------------------
    if run_all or args.stage == "build":
        payload = build_book.build(bk, pages, total)
        quality = payload["quality"]
        if args.require_ready and not quality["production_ready"]:
            print(
                "[build] refused: output is not production-ready "
                f"(status={quality['status']}, "
                f"missing={len(quality['missing_transcript_pages'])}, "
                f"flagged={len(quality['flagged_transcript_pages'])}, "
                f"unapproved={len(quality['unapproved_content_pages'])}, "
                f"sections_missing={len(quality['missing_sections'])})",
                file=sys.stderr,
            )
            return 2
        full, minified = build_book.write(bk, payload)
        print(f"[build] {payload['extraction']['pages_transcribed']} pages -> "
              f"{payload['sections_count']} sections")
        print(
            f"  quality: {quality['status']} "
            f"(production_ready={str(quality['production_ready']).lower()})"
        )
        print(f"  {full}")
        print(f"  {minified}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
