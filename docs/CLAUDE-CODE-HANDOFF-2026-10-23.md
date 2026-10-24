# Extraction Tool and dots.mocr Handoff Report

**Prepared:** 2026-10-23  
**Purpose:** Give Claude Code the project history, verified state, failure evidence, and next actions needed to get local Amharic/Ge’ez OCR working safely.

## Executive status

The book has **not** been OCRed with dots.mocr. The 218-page source PDF has been rendered and verified, the vLLM Docker image and model files are being cached locally, and Docker can see the NVIDIA GPU. The blocker is model-server initialization: vLLM runs out of GPU memory on the 8 GiB RTX 3050 before its health endpoint becomes ready. As a result, the launcher never reaches its three-page sample OCR or the full-book candidate run. No paid OCR/API inference has been used.

The first adjustment from 2 GiB to 4 GiB of CPU weight offload and from 16,384 to 8,192 context tokens did **not** solve the GPU out-of-memory error. Claude Code should diagnose and implement a deliberate low-memory runtime plan, then verify model readiness and sample-page output before attempting all content pages. Do not describe the book as extracted until candidate files exist and are reviewed.

## User goal and quality requirements

- Extract the whole scanned *Wudase Mariam* Ethiopian Orthodox book for an app.
- Focus on printed Amharic and Ge’ez/Ethiopic text, page order/references, layout, and red/black text distinctions.
- Keep inference local and avoid paid general-purpose AI/API calls. Dots.mocr is intended as the primary OCR candidate; existing Tesseract geometry/color tooling remains useful.
- Preserve the original scans and existing transcripts/reviewed edits. New OCR must be candidate output until it has been checked.
- Aim for high accuracy, but do not claim “perfect” OCR without comparison and human review. The dots adapter may only assign color where recognized words can be safely aligned to existing geometry; uncertain words need review.

## Repository and input state

- Repository: `/home/gabar/extraction-tool` (the earlier request was to use `https://github.com/afrocoder16/extraction-tool.git`).
- Book config: `books/wudase-mariam/book.json`.
- User copied `Wudase Mariam.pdf` from Windows to `books/wudase-mariam/source/` on the host using SCP.
- Verified PDF properties recorded during the work: 218 pages, approximately 39 MiB (40,818,800 bytes), unencrypted. SHA-256: `52c000f1bbd8deb8f6fcdc0540824f394e0d52f479b1145ec9e9219731344d`.
- Source manifest: `books/wudase-mariam/work/source_manifest.json`; current manifest reports 218 pages and 218 rendered pages. There are 129 content pages for OCR; remaining pages are not content pages.
- Rendering uses 300-DPI color page images; Poppler support was added alongside PyMuPDF support. Source manifest checks are performed by the launcher before model startup.
- Existing files under `work/transcripts/`, `work/review.json`, and `out/` predate dots candidate generation. They are not evidence of a successful dots run and must not be overwritten without explicit review/adoption logic.
- Intended dots candidate output: `books/wudase-mariam/work/candidates/dots/`.

## Code and workflow changes already present

The following changes are present in the working tree (they are not represented by a commit yet):

- Added `engine/dots_ocr.py`: OpenAI-compatible HTTP client, bounded retry/response validation, layout validation, raw-response retention, coordinate handling, and resumable cached candidate drafts keyed by source/model/prompt/layout information. Output is candidate-only JSON.
- Added/changed `engine/schema.py`, `engine/provenance.py`, `engine/review_store.py`, and `engine/doctor.py` for structured data, source integrity, safer review operations, and local diagnostics.
- Updated the engine CLI and book configuration so dots can be selected as the OCR provider. OCR candidates are intended to be saved without replacing existing human-edited transcript files.
- Updated the review UI / verification code to preserve review state and make missing/uncertain evidence visible. Dots layout regions are not character ground truth; existing Tesseract word geometry is used only as a cautious alignment aid for red/black color.
- Added `scripts/start-local-dots.sh` to verify source rendering, start the pinned vLLM model container, wait for its health endpoint, run sample pages 3, 21, and 129, then process all content pages if samples parse successfully.
- Added `docs/DOTS-USAGE.md`; README points to it. `.local-runtime/` is gitignored. Logs are written to `.local-runtime/setup.log`.
- Added/updated tests: `tests/test_dots_and_review.py`, `tests/test_render.py`, and `tests/test_engine.py`.

At an earlier point, 16 Python tests, existing Node validation tests, shell syntax, and `git diff --check` passed. The subsequent launcher memory-setting and documentation edits have not been re-tested. Do not infer that live inference works from unit tests; it has not succeeded.

## Host and runtime facts verified by the user over SSH

- Host OS reported by local inspection: Ubuntu 26.04.1 LTS.
- GPU: NVIDIA GeForce RTX 3050, 8,192 MiB VRAM; NVIDIA driver 595.99.02.
- The account `gabar` cannot access `/var/run/docker.sock` directly. The launcher uses `sudo docker` after authentication. Do not request, print, or store the user's password.
- Initially Docker reported `could not select device driver "" with capabilities: [[gpu]]`. The NVIDIA Container Toolkit was then installed/configured, and Docker was restarted.
- This command subsequently succeeded and showed the RTX 3050 inside the container:

  ```bash
  sudo docker run --rm --gpus all ubuntu nvidia-smi
  ```

- The vLLM runtime image was successfully pulled: `vllm/vllm-openai:v0.17.1`, digest `sha256:0dc46f74eb0e630675d83101dc66c6441c4475cceedcf9235ee42b87c3affd23`.
- Model ID: `dots-studio/dots.mocr`. Launcher resolved and pinned revision `e539fbb52280393adc081b289ec597430a0f9031`.
- Model cache is in Docker named volume `extraction-dots-model-cache`. Keep this volume between attempts. Removing the failed named container should not remove this cache.
- Docker's NVIDIA runtime setup was applied using `sudo nvidia-ctk runtime configure --runtime=docker` and `sudo systemctl restart docker`.

## Attempts and exact failure

1. Initial launcher run stopped because Docker had no GPU runtime support.
2. NVIDIA Container Toolkit was installed/configured; GPU passthrough test passed.
3. A stale failed container named `extraction-dots-mocr` was removed and the launcher retried.
4. First model-server attempt reached vLLM/CUDA initialization. At a dummy/model forward pass in `vllm/model_executor/models/dots_ocr.py` (rotary embedding path), it failed with `torch.OutOfMemoryError`. It attempted another 338 MiB allocation; GPU reported 7.66 GiB total, only 111.81 MiB free, and 7.17 GiB used by the process. That run used `--cpu-offload-gb 2` and `--max-model-len 16384`.
5. The launcher was adjusted to default to `--cpu-offload-gb 4` and `--max-model-len 8192`, retaining `--max-num-seqs 1`, `--gpu-memory-utilization 0.85`, eager execution, and `DOTS_MAX_PIXELS=3211264` to avoid an unvalidated resolution reduction.
6. Second attempt still failed in the same rotary embedding forward path on a 338 MiB allocation. GPU reported 7.66 GiB total, 199.81 MiB free; process total 7.08 GiB, PyTorch allocated 6.36 GiB and reserved 606.73 MiB. Thus doubling offload and halving context length did not make initialization succeed.

The warning about Inductor being disabled and the warning about the fast image processor are not the fatal error shown in these logs. The fatal error is CUDA OOM. The NCCL destroy-process-group warning follows the failed initialization and is not the root cause.

The current failed `extraction-dots-mocr` container likely remains as an exited container after the latest attempt. Before another launch, inspect/remove that **container only**. Do not remove the model cache volume. There are no successful dots candidate files from these attempts.

## Current launcher settings and relevant files

Inspect these before changing anything:

- `scripts/start-local-dots.sh` — vLLM container args, health polling, sample/full OCR flow, and current 4 GiB CPU offload / 8,192 context defaults. It accepts `DOTS_CPU_OFFLOAD_GB`, `DOTS_MAX_MODEL_LEN`, `DOTS_GPU_MEMORY_UTILIZATION`, and `DOTS_MAX_PIXELS` overrides.
- `engine/dots_ocr.py` — API payload and output parsing; check the exact expected dots.mocr response format against the pinned upstream revision.
- `engine/cli.py` and `engine/transcribe.py` — candidate-generation orchestration and input/output paths.
- `engine/render.py`, `engine/color_split.py`, and `engine/provenance.py` — source render, color masks, integrity checks.
- `docs/DOTS-USAGE.md` — user workflow and runtime limits (update it when the tested settings change).
- `.local-runtime/setup.log` — persistent setup output; user has also pasted the full failure in the conversation.

## Required next work for Claude Code

1. **Inspect the actual failure/runtime context.** Check current GPU occupancy (`nvidia-smi` on host and inside container), container exit state/logs, available host RAM/swap, cgroup memory limits, and vLLM arguments actually used. Confirm whether other GPU processes consume memory. Read complete logs around first OOM, not just the final traceback.
2. **Check supported deployment options at the pinned dots.mocr/vLLM revision.** Confirm correct model dtype, vLLM server version, vision/image-token limits, and whether this model is realistically supported on an 8 GiB RTX 3050. Avoid assuming a 3B parameter label means it fits in 8 GiB; the vision encoder, activations, CUDA workspace, and cache also use memory.
3. **Make one evidence-based low-VRAM plan, then run a single sample-page smoke test.** Evaluate, in a controlled order, additional CPU offload, smaller context/batched-token limits, supported multimodal image-size/crop controls, allocator fragmentation settings, and a compatible quantized checkpoint or non-vLLM Transformers path. CPU offload can add transfer cost; quantization or reducing image pixels may affect text accuracy and must be documented/evaluated. Preserve 300-DPI source images and original color masks. Do not destroy the Docker model volume.
4. **Improve launcher diagnostics.** Log the full effective model/runtime options, stop promptly when the container exits, surface the concise OOM cause without flooding the terminal, and print precise inspect/log commands. Avoid an unconditional retry loop on a configuration that is known to OOM.
5. **Validate the pipeline in stages.** First prove server health. Then run pages 3, 21, and 129 and inspect candidate JSON, page/coordinate references, Unicode text, reading order, color/uncertainty mapping, and resume behavior. Only after those pass, process all 129 content pages. OCR is candidate generation; do not overwrite existing approved transcripts or publish automatically.
6. **Measure quality on Amharic and Ge’ez separately.** Compare a human-corrected sample against dots and the existing OCR baseline. Include character/word errors, omissions, page order, red/black accuracy, and time to correct. General benchmarks do not prove quality on this book.
7. **Update the docs with observed results and exact host commands.** State GPU/RAM requirements, any slower CPU-offload tradeoff, model cache size, candidate locations, recovery steps, and what remains unverified. Keep “OCR complete” separate from “candidates generated” and “human reviewed.”

Useful primary references:

- NVIDIA Container Toolkit install/configuration: <https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html>
- vLLM serve options and CPU offload: <https://docs.vllm.ai/en/stable/cli/serve/> and <https://docs.vllm.ai/en/stable/api/vllm/index.html>
- dots.mocr model card: <https://huggingface.co/dots-studio/dots.mocr>
- Official dots OCR/mOCR repository and deployment notes: <https://github.com/studio-dots-ai/dots.ocr>

## User recovery commands (do not run until settings are reviewed)

From the user's SSH shell, check the failed container and available GPU memory:

```bash
sudo docker ps -a --filter name=extraction-dots-mocr
nvidia-smi
```

After Claude Code makes a reviewed launcher/config change, remove only the exited failed container and rerun:

```bash
sudo docker rm -f extraction-dots-mocr
bash /home/gabar/extraction-tool/scripts/start-local-dots.sh
```

Do **not** run `docker volume prune` or remove `extraction-dots-model-cache`; that would discard downloaded model files and force another download. Keep the SSH terminal open and monitor `.local-runtime/setup.log`.

## Completion checklist

- [ ] Dots model server remains healthy on the local host.
- [ ] Sample candidate pages 3, 21, and 129 are created and inspected.
- [ ] All 129 content pages have resumable dots candidate files.
- [ ] Candidate files are validated for page IDs, Unicode, reading order, coordinates, and uncertainty.
- [ ] Red/black text assignments have page-level and word-level evidence and are reviewed.
- [ ] Amharic and Ge’ez accuracy is evaluated against human-corrected samples.
- [ ] Existing human-approved transcripts remain preserved until candidates are explicitly adopted.
- [ ] User is told accurately whether model startup, sample OCR, full candidate extraction, and human review are each complete.
