# Amharic and Ge’ez Orthodox book extraction roadmap

Prepared 23 October 2026 from revision `6ae1e6b093aef1ae96e357a06d61f07b146efe89`.

Build a trustworthy book-production pipeline: preserve the scan, transcribe literally, preserve red text and structure, expose uncertainty, obtain human review, and export a versioned package the app can read and search offline. Treat “perfect” as a measurable editorial release standard; no OCR model guarantees error-free religious text.

This is the plan for the next implementation stage. The current delivery downloads/reviews the project and adds planning documents; it does not enable a new OCR provider yet. Pending further app direction, the first app target is reading and search, with optional cited question answering later. Flutter is a reasonable compatibility target because the existing output explicitly supports it, but the extraction format should remain platform-independent.

## Model decision

Test **dots.mocr** first, retain **dots.ocr** as a comparison candidate, and keep current providers behind the same interface until the results justify a default. Do not spend the first milestone on the SVG-specific model: faithful text, colour, order and page references are the main needs here. Preserve devotional illustrations as original image crops.

The upstream project describes dots.mocr as a 3B model with layout parsing, reading order and structured output. It documents a vLLM service and CPU inference; it also acknowledges parsing failures. These features make it an integration candidate, not an accuracy guarantee. [Official repository](https://github.com/studio-dots-ai/dots.mocr)

The old rednote-hilab GitHub addresses redirect to studio-dots-ai. The official dots.ocr repository records the dots.ocr-1.5 → dots.mocr rename. [dots.ocr history](https://github.com/studio-dots-ai/dots.ocr)

The published paper and materials reviewed do not establish results for these Amharic/Ge’ez editions. General multilingual benchmark scores cannot settle that question. Test printed Amharic and printed Ge’ez separately; handwriting and damaged manuscripts require their own later evaluation. [Paper](https://arxiv.org/html/2603.13032v1), [model card](https://huggingface.co/dots-studio/dots.mocr)

## Phase 1 — Make the current pipeline reproducible

1. Finish retrieving the three Tesseract data files, obtain a normal Git checkout when terminal networking is available, and retain the snapshot revision for comparison. Do not overwrite existing human edits during checkout migration.
2. Restore the original PDF at `books/wudase-mariam/source/Wudase Mariam.pdf`; record SHA-256, edition, source page count, dimensions and any permission/provenance metadata supplied by the owner. Record original scan page index separately from the printed page label.
3. Create isolated extraction dependencies and a dependency lock; detect Tesseract with a configurable executable or `shutil.which` instead of defaulting to a Windows path on Linux. Add a doctor/preflight command that identifies missing PDFs, data files, packages and model endpoints before processing.
4. Fix the readiness failures documented in the review: complete page inventory, revision-bound approval and verification, required evidence, and the implicit-section fallback. Add focused regression tests for those failures.
5. Keep original PDFs, renders, raw OCR candidates and human edits in distinct storage. Never describe reviewed transcripts as disposable derived data.

Completion: the current book can be rebuilt consistently; incomplete analysis or stale review cannot produce a releasable export; full existing tests run in the configured environment.

## Phase 2 — Create a reference benchmark before selecting a provider

Start with roughly 30–50 pages across at least 3 representative printed editions, including both languages. A first smoke test can use 12–20 Wudase Mariam pages; it cannot establish general performance on all books.

- Include clean pages, dense small type, faded text, skew/gutter distortion, bleed-through, inline red names and responses, verse numerals, headings, repeated headers, two-column pages, and genuine clipped text. Include blank and illustration controls.
- Candidate Wudase pages to inspect: 3, 7, 21, 23, 81, 107, 129, 147, 165, 191 and 217. These span clean stored checks, legacy metadata, clipping and explicit drafts. Final selection and language labels require inspection of the original scans. Add blank and illustration pages according to the inventory.
- Have a fluent Amharic/Ge’ez proofreader transcribe the visible text from the scan and mark red characters, text regions, reading order, numerals and clipped areas. Resolve disagreements with a second reviewer where feasible. Existing drafts and unapproved edits are candidates, not ground truth.
- Freeze reference text and hashes. Split tuning and evaluation pages by book/edition where possible; never tune prompts on the held-out results. No training or fine-tuning in this phase.
- Compare current Tesseract, available existing vision-provider results, dots.ocr and dots.mocr on the same source crops. Run additional paid inference only within an agreed benchmark budget. Compare the current 1500-pixel resize with an appropriately higher resolution; small Ethiopic glyphs may be lost by downscaling.

| Measurement | What it must reveal |
| --- | --- |
| Character error rate, `(substitutions + deletions + insertions) / reference characters` | Literal text accuracy, by language and scan condition |
| Word error rate with a documented separator policy | Reading usefulness without hiding Ethiopic word-separator errors |
| Punctuation/numeral accuracy | Lost `፡`, sentence marks, verse labels and Ethiopic numerals |
| Red-character/span precision and recall | Wrongly red text and missed red text, aligned to reference characters |
| Region coverage and order | Missing, repeated, hallucinated or reordered lines/columns |
| Failure rate, latency, peak memory, cost | Operational suitability for whole books |
| Reviewer minutes and edits per page | Whether the model reduces the actual proofreading workload |

Report strict literal metrics as primary. An NFC/whitespace-normalized comparison may be supplementary, with its policy recorded. Do not conflate ሀ/ሐ/ኀ, ሰ/ሠ, አ/ዐ or other distinct source characters to make scores look better. Report unweighted per-page and character-weighted aggregate results, worst pages, and sample sizes.

Suggested trial targets, to be ratified after baseline measurement: median CER at most 1% on clean print; red-span F1 at least 0.98; zero observed dropped/duplicated lines on the held-out sample; and materially lower review time. Difficult-page strata must be reported separately. These are proposed targets, not achieved results or proof of zero errors.

Completion: a reproducible comparison and a documented provider decision. If dots does not improve accuracy or correction effort, retain the better provider and use dots only where it demonstrably helps.

## Phase 3 — Add a provider interface and dots adapter

Proposed modules: `engine/ocr/base.py`, `engine/ocr/dots.py`, provider wrappers for the current engines, `engine/schema.py`, and evaluation utilities under `benchmarks/`. Names are proposed and do not exist yet.

Keep GPU inference in a separate local or private remote service. The extraction client sends a page image and receives a candidate. Start with a single request at a time, explicit timeout, bounded retries and recorded failures; expand concurrency only after memory measurements. Use the official model/server recipe pinned to a tested revision. The current machine's GPU is unverified; CPU inference is an experiment, not a promised book-scale deployment.

The adapter should:

1. Use upstream document-layout prompting and generation settings as the initial baseline. Its native result is layout elements with boxes/categories/text, not our prayer schema. Validate actual fixtures from the pinned model instead of assuming an API-enforced schema.
2. Preserve the raw response and completion/stop status. Reject truncated, malformed, repeated or impossible output; keep a recoverable failure record. Do not silently “repair” the wording with another model.
3. Validate categories, finite boxes within bounds, reading order and Unicode. Preserve image dimensions and every resize/rotation/crop transform. Convert model coordinates back to the original scan coordinate space before using them with colour masks. Upstream itself adjusts boxes for image resizing. [Layout postprocessing](https://github.com/studio-dots-ai/dots.mocr/blob/main/dots_mocr/utils/layout_utils.py)
4. Map generic headings, paragraphs, footnotes, tables and pictures into a richer neutral document schema. Determine prayer/rubric/verse roles in a separate book-specific step with review; a generic `Text` category does not identify a liturgical role.
5. Record provider, model ID/revision, prompt hash, settings, input hash, elapsed time, raw-output location and retry history. Leave confidence unknown unless an engine actually supplies a defined score; token likelihood is not calibrated textual accuracy.
6. Key caches by source image, crop, provider revision, prompt, settings and preprocessing version. Write candidates separately from accepted transcripts. Make reruns resumable and writes atomic.

Configuration should include provider, base URL, served model name, prompt/profile version, timeout, resolution and concurrency. Keep endpoint credentials outside book JSON. Do not assume the served model name matches a model-hub name; upstream examples differ.

Completion: switching providers produces validated draft candidates through the same CLI, without overwriting reviewed text. Test malformed output, retries, cut-off completions, resize coordinates and restart/resume behavior using recorded fixtures; then run a small real inference smoke test.

## Phase 4 — Preserve Ethiopic text and red ink precisely

- Keep colour scans as the authority for visible ink. Tune colour thresholds per scan profile; retain rejected components and previews so faded red letters are not silently discarded as ornaments.
- dots layout boxes are generally regions, not guaranteed character boxes. For mixed-colour regions, use line/word geometry, colour-separated crops, and explicit alignment of recognized text to those regions. Tesseract geometry can assist but its recognized wording is not ground truth.
- Preserve pixel evidence and alignment quality for every colour span. When text cannot be mapped reliably to a red/black boundary, flag it for review; do not colour the whole paragraph by majority or infer colour from religious meaning.
- Retain exact source spelling, Ethiopic punctuation, numerals and visible spacing. Keep original transcription and any editorial correction as separate versions with reviewer, reason, time and affected span. Never automatically modernize Ge’ez or Amharic forms.
- Keep language tags at book/region level when known; permit `unknown` or `mixed`. Configure legitimate Latin annotations and Arabic digits per edition. Treat unusual scripts as review signals rather than automatically deleting them.
- Record cropped top, bottom, sides and gutter text. Missing physical text requires a better scan or an explicit unresolved lacuna; model completion from memory cannot fill it.
- Classify blank/illustration regions and retain captions or text around illustrations. Human-review ambiguous exclusions so an entire text-bearing page cannot disappear.

Completion: colour alignment and literal transcription meet benchmark targets, and uncertain colour/text remains visible to reviewers and release checks.

## Phase 5 — Make proofreading the route to a release

Extend the current reviewer with zoomed scan-to-text highlighting, candidate comparison, explicit issue resolution, stable region IDs, and a queue ordered by omissions, clipping, disagreement and colour uncertainty. Saving an edit must invalidate the previous approval; approving must operate on the saved revision. Keep issue history when a human resolves a flag.

For multiple reviewers, add revision conflict detection and an audit trail. Import hosted changes through schema validation and revision checks with a preview of conflicts. Preserve per-page history rather than overwriting the only saved copy.

Release rules:

- Every source page has an explicit classification/disposition; all intended text regions are accounted for.
- All content has a valid transcript, current verification, and human approval tied to its exact revision.
- No unresolved omissions, clipping, unknown colour assignments, draft provenance, or blocking review issues remain. A heuristic warning may be resolved by a documented human decision rather than erased.
- Every configured section and verse/reference boundary is checked against the scan; do not force a missing section by guessing its text.
- Source, transcript and package checksums validate. The package builder refuses release on any failed requirement and keeps the previous release intact.

This provides a reviewable edition; it does not certify theological authority. Editorial decisions should be made by the book's designated knowledgeable reviewers.

## Phase 6 — Export an app-ready book package

Preserve compatibility with existing `spans: [{t, c}]` while adding a versioned schema and a migration path. Keep machine candidates out of reader releases.

| Entity | Required information |
| --- | --- |
| Book/edition | Stable IDs, titles, known languages, edition/source metadata, schema and content version |
| Page | Stable ID, PDF index, printed label, dimensions, scan hash/path and classification |
| Region/block | Stable ID independent of position, source page/box, reading order, role, language and section membership |
| Text/span | Literal text, colour, source/alignment evidence, uncertainty and revision |
| Review | Reviewer identity, decision, transcript/source hashes, issue resolutions and timestamp |
| Release | Content checksum, provenance manifest, approved sections, migration mappings and assets |

Produce UTF-8 JSON as the portable source package and optionally SQLite plus a tested search index for offline reading. Store a distinct normalized search representation; keep the displayed text literal. Preserve mappings from search matches to original spans and page coordinates. Evaluate Ethiopic separators and tokenization with actual reader queries before choosing full-text indexing defaults.

Initial reader capabilities: book library, section/weekday navigation where applicable, adjustable Ethiopic font size, red/black text, offline search, bookmarks/highlights and “view source page.” Stable IDs and migration mappings keep bookmarks valid when an edition is corrected.

Only after the text is reviewed, consider cited question answering. Retrieval chunks must retain edition, section, page and block IDs; answers should link to supporting passages and scans. Keep generated explanations separate from quoted source text, and decline unsupported answers. Never use generated answers to silently modify the corpus.

Completion: a small sample package imports in the intended app, displays Ethiopic characters/colours correctly, searches expected phrases, opens the right source page, and preserves bookmarks across a correction release.

## First implementation slice

Start with the reproducibility and readiness fixes, then a candidate-only dots adapter and a frozen small benchmark. Use the current Wudase Mariam data to exercise the workflow while obtaining original scans and reference transcripts. Expand to more books only after the comparison is credible. Fine-tuning, handwritten manuscripts, diagram-to-SVG conversion and app question answering are later work driven by evidence and product priorities.

Inputs needed for the benchmark: the original PDF (or its local path), representative additional editions, a fluent reviewer for reference transcription, and a usable inference host. The current session has not established those resources or a cloud spending budget.
