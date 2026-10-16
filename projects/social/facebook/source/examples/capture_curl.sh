#!/usr/bin/env bash
# Usage: bash examples/capture_curl.sh URL [OUTPUT]
# Resume: bash examples/capture_curl.sh --job-id JOB_ID [OUTPUT]
# Requires curl + Python 3 for JSON only (no jq, no Python HTTP library).
set -euo pipefail
base="${FACEBOOK_API_BASE:-http://127.0.0.1:8765}"
base="${base%/}"
python="${PYTHON:-python3}"
timeout="${WAIT_SECONDS:-1800}"
[[ "$timeout" =~ ^[1-9][0-9]*$ ]] || { echo 'WAIT_SECONDS must be a positive integer' >&2; exit 2; }
get() { curl --noproxy '*' --fail --silent --show-error --max-time 30 "$base$1"; }
field() { "$python" -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }
get /health >/dev/null
if [[ "${1:-}" == --job-id ]]; then
  id="${2:?JOB_ID required}"; output="${3:-reactors.csv}"
else
  url="${1:?URL required}"; output="${2:-reactors.csv}"
  body="$("$python" -c 'import json,sys; print(json.dumps({"post_url":sys.argv[1],"collect":["reactions"],"save_artifacts":["progress","safe_capture"]}))' "$url")"
  created="$(curl --noproxy '*' --fail --silent --show-error --max-time 30 -H 'Content-Type: application/json' --data-binary "$body" "$base/v1/jobs")"
  id="$(printf '%s' "$created" | field job_id)"
fi
echo "job_id=$id"
path="/v1/jobs/$id"
deadline=$((SECONDS + timeout))
while true; do
  job="$(get "$path")"
  status="$(printf '%s' "$job" | field status)"
  case "$status" in
    completed) break ;;
    failed|cancelled) printf '%s\n' "$job" >&2; exit 1 ;;
    queued|running|cancelling) get "$path/progress"; echo ;;
    *) echo "Unknown status: $status" >&2; exit 1 ;;
  esac
  (( SECONDS < deadline )) || { echo "Resume with --job-id $id; job was not cancelled" >&2; exit 1; }
  sleep 2
done
result="$(get "$path/result")"
temp_output="$(mktemp "${output}.XXXXXX")"
trap 'rm -f -- "$temp_output"' EXIT
get "$path/artifacts/facebook-reactor-users.csv?download=1" >"$temp_output"
mv -- "$temp_output" "$output"
printf '%s\n' "$result"
