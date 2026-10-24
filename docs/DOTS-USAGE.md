# dots.mocr workflow

dots is the default OCR provider. The client talks to a separately running dots.mocr or dots.ocr model over an OpenAI-compatible HTTP endpoint. It makes no automatic calls to Claude or another general-purpose AI service. Claude remains an explicit `--provider claude` option.

## Current implementation status

- Added a dots HTTP client with bounded retries, strict layout/coordinate validation, raw-response preservation, and resumable candidate files keyed by image, model settings, prompt and layout hashes.
- Preserved existing transcripts and edits: dots output is saved under `work/candidates/dots/`. Candidate adoption/comparison in the browser is not implemented yet; retain candidates for comparison before replacing transcripts.
- Colour assignment uses exact text/geometry matches when available. Unmatched regions carry an unresolved-colour marker and provisional black display text; they are not ready to publish.
- Added source-render manifests, completeness checks, revision-bound verification/approval, atomic writes and local edit history.
- The local review server can display the repository's WebP review images when original PNGs are absent. Those derivatives are not counted as original source evidence.
- Local review saves reject stale page revisions and invalidate approval when saved text changes. Hosted Netlify storage still uses its existing workflow; hosted approvals require migration before satisfying the new local release checks.
- Sixteen Python tests pass, including mocked dots responses, malformed/truncated output, candidate caching, preservation of human edits, stale approval rejection and missing-page checks, and actual Poppler colour rendering/resume. Existing Node validation tests and review-script syntax check pass. No live dots inference, model accuracy evaluation or full extraction has been completed.

## Source PDF

Place the original file at:

```text
/home/gabar/extraction-tool/books/wudase-mariam/source/Wudase Mariam.pdf
```

The original PDF was transferred by SCP and verified: 218 pages, 40,818,800 bytes,
SHA-256 `52c000f1bbd8deb8f6fcdc0540824f394f0e0d52f479b1145ec9e9219731344d`.
Rendering now supports the installed Poppler tools as well as PyMuPDF, so original
300-DPI colour PNGs can be prepared without downloading Python OCR packages.

## Model server

The host has an RTX 3050 with 8 GiB VRAM and NVIDIA driver 595.99.02. Docker
GPU passthrough was verified with `nvidia-smi` inside a container. The agent
execution environment cannot access the host Docker socket, network downloads,
or local model HTTP connections. CPU inference speed has not been measured.

After rendering finishes, run the prepared launcher in the host SSH terminal:

```bash
bash /home/gabar/extraction-tool/scripts/start-local-dots.sh
```

It checks the source checksums and host GPU, starts the upstream vLLM Docker image,
pins the model revision, then runs three sample pages followed by all content
pages if those responses parse successfully. Model files are cached in a Docker
volume. The endpoint is published only at `127.0.0.1:8001`; scans are processed
locally. Logs are written to `.local-runtime/setup.log`.

### 8 GiB GPU settings

The two earlier startups (2 and 4 GiB offload) failed with CUDA OOM inside the
vision encoder (`dots_ocr.py` rotary/attention) during vLLM's startup profiling.
Cause: vLLM profiles the encoder with the largest image the processor accepts —
11,289,600 px by default (~57,600 patches) — regardless of what the client sends.
Weight offload and context length do not change that activation peak.

The launcher now uses:

| Setting | Value | Why |
| --- | --- | --- |
| `--mm-processor-kwargs {"max_pixels": 3211264}` | `DOTS_MAX_PIXELS` | Server profiling cap equals the client's resize cap (pages are 2550×3300 = 8.4 MP, sent at ~1568×2016, ≈185 DPI). |
| `--max-model-len 16384` | `DOTS_MAX_MODEL_LEN` | 4,096 image tokens + prompt + 8,192 completion tokens must fit; the script refuses settings that cannot. |
| `--gpu-memory-utilization 0.90` | `DOTS_GPU_MEMORY_UTILIZATION` | The script checks enough GPU memory is free first (desktop apps use ~0.4 GiB). |
| `--cpu-offload-gb 0.5` | `DOTS_CPU_OFFLOAD_GB` | Measured on this host: bf16 weights 5.69 GiB, encoder profiling peak ~1.0 GiB, KV for 16384 tokens 0.44 GiB. 0.5 GiB offload leaves ~0.68 GiB KV. vLLM v0.17.1 offloads **decoder layers only** — the vision tower cannot be offloaded (a `vision_tower` filter matched nothing on attempt 3, which then failed with "No available memory for the cache blocks"). Offloaded weights cross PCIe every token, so keep this small. |
| `--dtype bfloat16` | `DOTS_DTYPE` | The checkpoint's native dtype; Ampere supports it. fp16 risks overflow. |
| `--limit-mm-per-prompt {"image": 1}` | — | One page per request. |
| `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` | — | Reduces fragmentation. |
| model revision `e539fbb5…9031` | `DOTS_MODEL_REVISION` | Fixed in the script; no longer re-resolved from the Hub on every run. |

Client request timeout is 900 s (`book.json`). If a stopped `extraction-dots-mocr`
container exists, the launcher saves its full log to `.local-runtime/dots-server-*.log`
and removes the container (never the `extraction-dots-model-cache` volume). On a
startup failure the same log is saved and the key error lines are printed.

These settings are derived from the vLLM v0.17.1 source but **have not yet produced a
successful startup on this host**. If accuracy on the samples is poor at 3.2 MP, try a
larger `DOTS_MAX_PIXELS` (e.g. 4.5 MP) with a matching `DOTS_MAX_MODEL_LEN`; record the
change because it changes cache keys. It does not fall back to a paid service. A
successful candidate run still needs text and colour review.

Sources: [official dots.mocr deployment](https://github.com/studio-dots-ai/dots.mocr),
[vLLM server options](https://docs.vllm.ai/en/stable/cli/serve/).

Keep the extraction client and GPU model environment separate. The client itself only needs the lightweight requirements file:

```bash
python3 -m pip install -r requirements-dots-client.txt
```

Full rendering, colour splitting and Tesseract geometry also require `requirements.txt`, a Tesseract executable and the Amharic traineddata. Those runtime dependencies are not installed here. The missing data files can be restored using `../extraction-tool-download/complete_download.py --download` when networking is available.

## Commands

Run these from `/home/gabar/extraction-tool`.

```bash
# Report local source/runtime availability without importing GPU libraries.
python3 -m engine books/wudase-mariam --doctor

# Render source and prepare colour/layout evidence once dependencies are ready.
python3 -m engine books/wudase-mariam --stage render
python3 -m engine books/wudase-mariam --stage split
python3 -m engine books/wudase-mariam --stage layout

# Set this to your actual model service and served model name.
export DOTS_BASE_URL=http://127.0.0.1:8001/v1
python3 -m engine books/wudase-mariam --stage ocr --provider dots \
  --model dots-studio/dots.mocr --pages 3,21,129

# Once sample quality is assessed, produce candidates for every content page.
python3 -m engine books/wudase-mariam --stage ocr --provider dots

# Inspect existing transcripts and scan images locally.
python3 -m engine.serve books/wudase-mariam

# Explicit release command: refuses incomplete or stale approval evidence.
python3 -m engine books/wudase-mariam --stage build --require-ready
```

Use `DOTS_API_KEY` only if your model service requires authentication; keep its value outside book configuration and source control. No key is required by this client for a local service that permits unauthenticated access.

`--stage all --provider dots` prepares inputs and writes OCR candidates, then stops for review. It does not auto-publish candidates. A changed source PDF or render DPI requires `--stage render --force`; source provenance will prevent partially rerendered books from being released.

For a provisional experiment with the downloaded review images, supply `--image-dir netlify/review-assets/work/page_images` to the OCR command. Such candidates are marked as derivative input. Evaluate final accuracy against the original PDF.

## Remaining work toward the complete book

Start and assess the local model service; run a small real comparison; complete candidate review/adoption and issue-resolution support; process all content pages; resolve clipped/missing text using the scan or a better copy; verify red spans, numerals and sections; export only after current human review. No claim of finished or error-free extraction is made by the current implementation.
