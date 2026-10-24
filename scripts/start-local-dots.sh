#!/usr/bin/env bash
# Run from the host's SSH terminal. No paid inference API is used.
# Starts the official vLLM image (or reuses it if already running), checks three
# sample pages, then processes the book.
#   bash scripts/start-local-dots.sh [books/<book-folder>]   (default: books/wudase-mariam)
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
book_dir="${1:-books/wudase-mariam}"
book_dir="${book_dir%/}"
[[ -f "$book_dir/book.json" ]] || { echo "No book.json in $book_dir"; exit 2; }
mkdir -p .local-runtime
exec > >(tee -a .local-runtime/setup.log) 2>&1

command -v docker >/dev/null || { echo 'Docker is required on the host.'; exit 2; }
command -v nvidia-smi >/dev/null || { echo 'NVIDIA utilities are unavailable on the host.'; exit 2; }
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
docker_cmd=(docker)
if ! "${docker_cmd[@]}" info >/dev/null 2>&1; then
  command -v sudo >/dev/null || {
    echo 'Docker access was denied and sudo is unavailable. Ask the host administrator for Docker access.'
    exit 2
  }
  echo 'Your account cannot access Docker directly. Docker commands will use sudo for this run.'
  echo 'If prompted, enter your Linux account password; typing will not be displayed.'
  sudo -v || { echo 'Administrator authentication failed; Docker has not been started.'; exit 2; }
  docker_cmd=(sudo docker)
  "${docker_cmd[@]}" info >/dev/null || {
    echo 'Docker is still unavailable with sudo. Check the Docker service on the host.'
    exit 2
  }
fi
# Checks the rendered source and picks first/middle/last content pages as samples.
sample_pages="$(BOOK_DIR="$book_dir" python3 - <<'PY'
import json,os,pathlib,sys
from engine.book import load
from engine.provenance import source_problems
root=pathlib.Path(os.environ['BOOK_DIR'])
p=root/'work'/'source_manifest.json'
if not p.exists():
 raise SystemExit('Render the source PDF before starting OCR (python -m engine %s --stage render).' % root)
m=json.loads(p.read_text())
if len(m.get('rendered',{})) != m.get('pages'):
 raise SystemExit('Page rendering is still incomplete. Wait for it to finish.')
issues=source_problems(load(root),m['pages'])
if issues:
 raise SystemExit('Source verification failed: '+'; '.join(issues[:5]))
stats_path=root/'work'/'page_stats.json'
stats=json.loads(stats_path.read_text()) if stats_path.exists() else {}
content=sorted(int(k) for k,v in stats.items() if not v.get('is_blank') and not v.get('is_illustration'))
if not content:
 raise SystemExit('Run the colour split first (python -m engine %s --stage split).' % root)
print('Prepared source pages:',m['pages'],file=sys.stderr)
print(','.join(str(content[i]) for i in sorted({0,len(content)//2,len(content)-1})))
PY
)"
sample_pages="${DOTS_SAMPLE_PAGES:-$sample_pages}"

container_name=extraction-dots-mocr
runtime_image="${DOTS_VLLM_IMAGE:-vllm/vllm-openai:v0.17.1}"
model_id=dots-studio/dots.mocr
# Sized for an 8 GiB RTX 3050. vLLM profiles the vision encoder at the largest
# image the processor allows (11.3 MP by default, which alone exhausts 8 GiB), so
# the server cap must match the client's DOTS_MAX_PIXELS.
# Measured budget: bf16 weights 5.69 GiB + encoder profiling peak ~1.0 GiB + KV
# cache for 16384 tokens 0.44 GiB. 0.90 of 7.66 GiB plus 0.5 GiB of decoder
# weights in host RAM leaves ~0.68 GiB for KV. vLLM offloads decoder layers only
# (never the vision tower), and offloaded weights cross PCIe on every token, so
# keep the offload as small as fits.
model_revision="${DOTS_MODEL_REVISION:-e539fbb52280393adc081b289ec597430a0f9031}"
max_pixels="${DOTS_MAX_PIXELS:-3211264}"
max_completion_tokens="${DOTS_MAX_COMPLETION_TOKENS:-8192}"
max_model_len="${DOTS_MAX_MODEL_LEN:-16384}"
cpu_offload_gb="${DOTS_CPU_OFFLOAD_GB:-0.5}"
cpu_offload_params="${DOTS_CPU_OFFLOAD_PARAMS:-}"
gpu_memory_utilization="${DOTS_GPU_MEMORY_UTILIZATION:-0.90}"
dtype="${DOTS_DTYPE:-bfloat16}"
[[ "$model_revision" =~ ^[0-9a-f]{40}$ ]] || { echo 'DOTS_MODEL_REVISION must be a 40-character commit SHA.'; exit 2; }
# One image (max_pixels / 28^2 tokens) + prompt + completion must fit the context.
needed_tokens=$(( max_pixels / 784 + 512 + max_completion_tokens ))
if (( needed_tokens > max_model_len )); then
  echo "DOTS_MAX_MODEL_LEN=$max_model_len is too small: one page needs up to $needed_tokens tokens."
  exit 2
fi

log_dir="$repo_root/.local-runtime"
save_server_log() {
  local file="$log_dir/dots-server-$(date +%Y%m%d-%H%M%S).log"
  "${docker_cmd[@]}" logs "$container_name" >"$file" 2>&1 || true
  echo "Full server log saved to $file"
  grep -E 'OutOfMemoryError|Error:|ValueError|CUDA error' "$file" | grep -v '^\s*File ' | tail -5 || true
}
server_running=0
if "${docker_cmd[@]}" container inspect "$container_name" >/dev/null 2>&1; then
  if [[ "$("${docker_cmd[@]}" inspect --format '{{.State.Running}}' "$container_name")" == true ]]; then
    echo "Reusing the running $container_name server (to change its settings: ${docker_cmd[*]} stop $container_name)."
    server_running=1
  else
    echo "Removing the stopped container $container_name (the model cache volume is kept). Its last error:"
    save_server_log
    "${docker_cmd[@]}" rm "$container_name" >/dev/null
  fi
fi

if (( server_running == 0 )); then
# vLLM refuses to start unless this fraction of the GPU is free (desktop apps count).
read -r gpu_total_mib gpu_free_mib < <(nvidia-smi --query-gpu=memory.total,memory.free --format=csv,noheader,nounits | head -1 | tr -d ',')
if ! awk -v t="$gpu_total_mib" -v f="$gpu_free_mib" -v u="$gpu_memory_utilization" 'BEGIN { exit !(f >= t * u * 0.96) }'; then
  echo "Only ${gpu_free_mib} MiB of ${gpu_total_mib} MiB GPU memory is free; DOTS_GPU_MEMORY_UTILIZATION=$gpu_memory_utilization needs more."
  echo 'Close GPU-using apps (see nvidia-smi) and rerun.'
  exit 2
fi

offload_args=(--cpu-offload-gb "$cpu_offload_gb")
[[ -n "$cpu_offload_params" ]] && offload_args+=(--cpu-offload-params "$cpu_offload_params")
echo "Runtime: image=$runtime_image model=$model_id@$model_revision dtype=$dtype"
echo "Memory: max_pixels=$max_pixels max_model_len=$max_model_len completion=$max_completion_tokens" \
  "gpu_util=$gpu_memory_utilization offload=${cpu_offload_gb}GiB(${cpu_offload_params:-any})"
echo 'Downloading the local inference runtime/model if needed. This uses bandwidth and disk, not API credits.'
"${docker_cmd[@]}" run --detach --name "$container_name" --gpus all --shm-size 4g \
  -p 127.0.0.1:8001:8000 \
  -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  -v extraction-dots-model-cache:/root/.cache/huggingface \
  -v "$repo_root:/workspace/extraction-tool" \
  "$runtime_image" "$model_id" \
  --served-model-name "$model_id" --trust-remote-code \
  --revision "$model_revision" --code-revision "$model_revision" \
  --host 0.0.0.0 --port 8000 --tensor-parallel-size 1 \
  --dtype "$dtype" --max-model-len "$max_model_len" --max-num-seqs 1 \
  --limit-mm-per-prompt '{"image": 1}' --mm-processor-kwargs "{\"max_pixels\": $max_pixels}" \
  --gpu-memory-utilization "$gpu_memory_utilization" "${offload_args[@]}" --enforce-eager \
  --chat-template-content-format string

ready=0
for attempt in $(seq 1 360); do
  if [[ "$("${docker_cmd[@]}" inspect --format '{{.State.Running}}' "$container_name")" != true ]]; then
    echo 'Model startup failed. Cause:'
    save_server_log
    echo "Fix the settings above, then rerun this script (it removes the stopped container itself)."
    exit 2
  fi
  if "${docker_cmd[@]}" exec "$container_name" python3 -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" 2>/dev/null; then
    ready=1
    break
  fi
  if (( attempt % 6 == 0 )); then
    echo 'Waiting for the local model...'
    "${docker_cmd[@]}" logs --tail 3 "$container_name"
  fi
  sleep 5
done
if (( ready == 0 )); then
  echo "Model did not become ready. Inspect: ${docker_cmd[*]} logs $container_name"
  exit 2
fi
fi  # server_running == 0

run_ocr() {
  "${docker_cmd[@]}" exec --user "$(id -u):$(id -g)" \
    -e "DOTS_MAX_PIXELS=$max_pixels" -e "DOTS_MAX_COMPLETION_TOKENS=$max_completion_tokens" \
    -e "DOTS_MODEL_REVISION=$model_revision" \
    --workdir /workspace/extraction-tool "$container_name" \
    python3 -m engine "$book_dir" --stage ocr --provider dots \
      --base-url http://127.0.0.1:8000/v1 --model "$model_id" "$@"
}
echo "Running sample pages $sample_pages. Invalid or truncated responses stop this script."
run_ocr --pages "$sample_pages"
echo 'Producing candidates for all content pages. Every page still requires accuracy/colour review.'
run_ocr
echo "Local OCR candidate run finished. Results: $book_dir/work/candidates/dots/"
echo 'This is candidate generation, not a verified final edition.'
echo "The model is still running. To free GPU memory: ${docker_cmd[*]} stop $container_name"
