#!/usr/bin/env bash
# Start Ollama on a GitHub runner and pull a model: used by action.yml and by
# the benchmark workflow, so the benchmark runs the Action's own setup.
#
#   start-ollama.sh <model>
#
# STRATUM_OLLAMA_ROOT is where Ollama is installed (default ~/.stratum-ollama,
# the path action.yml caches). Two things depend on the model's size, read from
# Ollama's registry before the pull:
# - The runner's disk holds 14 GB, so a model that won't fit with room to spare
#   gets the space of preinstalled SDKs the review doesn't use. The runner is
#   thrown away after the job.
# - The Actions cache holds 10 GB a repository, so a model over CACHE_LIMIT is
#   kept out of the cached folder: Ollama itself is still cached, and the model
#   is downloaded each run.
set -euo pipefail

model="${1:?usage: start-ollama.sh <model>}"
root="${STRATUM_OLLAMA_ROOT:-$HOME/.stratum-ollama}"
version="0.40.13"
sha256="c9490e0f520be21271d969e6703f012268b1d39f8b70dd58854e5bfef7a75bb6"
cache_limit=$((9 * 1024 * 1024 * 1024))
headroom=$((3 * 1024 * 1024 * 1024))

if [ ! -x "$root/bin/ollama" ]; then
  mkdir -p "$root"
  archive="${RUNNER_TEMP:-/tmp}/ollama.tar.zst"
  curl -fsSL -o "$archive" "https://github.com/ollama/ollama/releases/download/v${version}/ollama-linux-amd64.tar.zst"
  echo "${sha256}  $archive" | sha256sum -c -  # pinned, so a changed download fails
  tar --zstd -x -C "$root" -f "$archive"
  rm -f "$archive"
  # GitHub's runners have no GPU; drop the GPU libraries so the cache stays small.
  find "$root/lib/ollama" -mindepth 1 -maxdepth 1 -type d \( -name 'cuda*' -o -name 'rocm*' -o -name 'mlx*' \) -exec rm -rf {} + 2>/dev/null || true
fi

# The model's download size in bytes, from Ollama's registry; 0 when it can't
# be told (a hf.co/ model, or no answer), and then nothing is cleared or moved.
size=0
case "$model" in
  hf.co/*) ;;
  *)
    name="${model%%:*}"; tag="${model#*:}"; [ "$tag" = "$model" ] && tag="latest"
    case "$name" in */*) path="$name" ;; *) path="library/$name" ;; esac
    size=$(curl -fsS -m 30 -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
      "https://registry.ollama.ai/v2/${path}/manifests/${tag}" 2>/dev/null \
      | python3 -c "import json, sys; print(sum(l.get('size', 0) for l in json.load(sys.stdin).get('layers', [])))" 2>/dev/null || echo 0)
    ;;
esac

free=$(df --output=avail -B1 "$HOME" | tail -1 | tr -d ' ')
if [ "$size" -gt 0 ] && [ $((size + headroom)) -gt "$free" ]; then
  echo "$model is $((size / 1024 / 1024)) MB and the disk has $((free / 1024 / 1024)) MB free: removing preinstalled SDKs"
  sudo rm -rf /usr/local/lib/android /usr/share/dotnet /opt/ghc /opt/hostedtoolcache/CodeQL /usr/local/share/boost
  df -h "$HOME" | tail -1
fi

if [ "$size" -gt "$cache_limit" ]; then
  echo "$model is too big for the Actions cache, so it's downloaded each run (Ollama itself stays cached)"
  export OLLAMA_MODELS="${RUNNER_TEMP:-/tmp}/stratum-ollama-models"
else
  export OLLAMA_MODELS="$root/models"
fi

nohup "$root/bin/ollama" serve > "${RUNNER_TEMP:-/tmp}/ollama.log" 2>&1 &
for _ in $(seq 1 60); do
  if curl -sf http://localhost:11434/api/version > /dev/null; then break; fi
  sleep 1
done
# The registry now and then answers 503, sometimes at the very end of a pull.
# Ollama keeps the layers it has, so trying again only fetches what's missing.
for attempt in 1 2 3; do
  if "$root/bin/ollama" pull "$model"; then
    exit 0
  fi
  if [ "$attempt" -lt 3 ]; then
    echo "Pulling $model failed (attempt $attempt of 3); trying again in $((attempt * 15)) s"
    sleep $((attempt * 15))
  fi
done
echo "Pulling $model failed three times" >&2
exit 1
