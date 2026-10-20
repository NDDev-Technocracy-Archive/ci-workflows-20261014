#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

url=${NDDEV_TOOL_CACHE_URL:?}
expected_sha256=${NDDEV_TOOL_CACHE_SHA256:?}
output=${NDDEV_TOOL_CACHE_OUTPUT:?}
max_bytes=${NDDEV_TOOL_CACHE_MAX_BYTES:?}
: "${RUNNER_TEMP:?}"
: "${GITHUB_OUTPUT:?}"

[[ "$url" == https://* && "$url" != *[' @?#']* ]]
[[ "$expected_sha256" =~ ^[0-9a-f]{64}$ ]]
[[ "$max_bytes" =~ ^[1-9][0-9]{0,12}$ ]]
(( max_bytes <= 1024 * 1024 * 1024 ))
[[ "$RUNNER_TEMP" == /* && -d "$RUNNER_TEMP" ]]
[[ "$output" == "$RUNNER_TEMP"/* && "$output" != */../* && "$output" != */.. ]]

case "${url#https://}" in
  github.com/*|objects.githubusercontent.com/*|releases.astral.sh/*|static.rust-lang.org/*|go.dev/*|nodejs.org/*|registry.npmjs.org/*) ;;
  *) echo "unsupported immutable tool origin" >&2; exit 2 ;;
esac

install -d -m 0700 "$(dirname -- "$output")"
scratch=$(mktemp -d "$RUNNER_TEMP/nddev-tool-cache.XXXXXXXXXX")
candidate=$scratch/artifact
cleanup() {
  find "$scratch" -mindepth 1 -delete 2>/dev/null || true
  rmdir "$scratch" 2>/dev/null || true
}
trap cleanup EXIT

started=$(date +%s%3N)
cache_result=disabled
source=upstream
assignment_names=(
  AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_REGION AWS_CA_BUNDLE
  SCCACHE_BUCKET SCCACHE_ENDPOINT NDDEV_CACHE_DELIVERY_ID
  NDDEV_CACHE_ROLE NDDEV_CACHE_MODE NDDEV_CACHE_PREFIX_ROOT
)
present=0
for name in "${assignment_names[@]}"; do
  [[ -n "${!name:-}" ]] && ((present += 1))
done

cache_enabled=false
if (( present == ${#assignment_names[@]} )); then
  if [[ "$AWS_ACCESS_KEY_ID" =~ ^AKIA[0-9A-F]{16}$ &&
        "$AWS_SECRET_ACCESS_KEY" =~ ^[A-Za-z0-9_-]{64}$ &&
        "$AWS_REGION" =~ ^[a-z]{2}-[a-z]+-[1-9][0-9]*$ &&
        "$SCCACHE_BUCKET" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]-cache$ &&
        "$SCCACHE_ENDPOINT" == https://* &&
        "$NDDEV_CACHE_DELIVERY_ID" =~ ^[0-9a-f]{64}$ &&
        "$NDDEV_CACHE_PREFIX_ROOT" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/trust/(trusted|untrusted|promoted)$ &&
        -f "$AWS_CA_BUNDLE" && ! -L "$AWS_CA_BUNDLE" ]]; then
    case "$NDDEV_CACHE_ROLE:$NDDEV_CACHE_MODE" in
      trusted-writer:read-write|untrusted-writer:read-write|release-reader:read-only) cache_enabled=true ;;
    esac
  fi
  if [[ "$cache_enabled" != true ]]; then
    cache_result=incomplete-assignment
    printf '::warning::NDDev tool cache assignment is invalid; using verified upstream\n'
  fi
elif (( present != 0 )); then
  cache_result=incomplete-assignment
  printf '::warning::NDDev tool cache assignment is incomplete; using verified upstream\n'
fi

cache_key=$(printf 'nddev-tool-cache-v1\0%s\0%s' "$url" "$expected_sha256" | sha256sum | cut -d' ' -f1)

verify_candidate() {
  local bytes actual
  [[ -f "$candidate" && ! -L "$candidate" ]] || return 1
  bytes=$(stat --format='%s' -- "$candidate")
  [[ "$bytes" =~ ^[0-9]+$ ]] && (( bytes > 0 && bytes <= max_bytes )) || return 1
  actual=$(sha256sum "$candidate" | cut -d' ' -f1)
  [[ "$actual" == "$expected_sha256" ]]
}

# A persistent self-hosted runner keeps each verified artifact in its own tool cache, so a slow
# upstream link costs one download per runner instead of one per job. The entry is copied into
# this job's scratch and verified there; an altered, truncated or unreadable entry is replaced.
runner_result=disabled
runner_entry=
if [[ "${RUNNER_ENVIRONMENT:-}" == self-hosted && "${RUNNER_TOOL_CACHE:-}" == /* &&
      -d "$RUNNER_TOOL_CACHE" && ! -L "$RUNNER_TOOL_CACHE" ]]; then
  runner_entry="$RUNNER_TOOL_CACHE/nddev-tool-cache/v1/$cache_key"
  runner_result=miss
  if [[ -f "$runner_entry" && ! -L "$runner_entry" ]]; then
    if cp -- "$runner_entry" "$candidate" 2>/dev/null && verify_candidate; then
      runner_result=hit
      source=runner
      touch -- "$runner_entry" 2>/dev/null || true
    else
      runner_result=invalid-entry
      rm -f -- "$candidate"
      printf '::warning::NDDev runner tool cache entry failed size or checksum verification; replacing it\n'
    fi
  fi
fi

if [[ "$cache_enabled" == true && "$source" == upstream ]]; then
  object_key="$NDDEV_CACHE_PREFIX_ROOT/tools/v1/sha256/$expected_sha256/$cache_key"
  object_url="${SCCACHE_ENDPOINT%/}/$SCCACHE_BUCKET/$object_key"
  sigv4="aws:amz:$AWS_REGION:s3"
  cache_code=000
  if cache_code=$(curl --silent --show-error --connect-timeout 3 --max-time 60 --retry 2 --retry-all-errors \
      --max-filesize "$max_bytes" \
      --cacert "$AWS_CA_BUNDLE" --aws-sigv4 "$sigv4" --user "$AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY" \
      --output "$candidate" --write-out '%{http_code}' "$object_url"); then
    case "$cache_code" in
      200)
        cache_result=hit
        source=cache
        ;;
      404) cache_result=miss ;;
      *) cache_result="http-$cache_code" ;;
    esac
  else
    cache_result=transport-error
  fi
fi

if [[ "$source" == cache ]] && ! verify_candidate; then
  cache_result=invalid-object
  source=upstream
  printf '::warning::NDDev tool cache object failed size or checksum verification; using upstream\n'
fi

if [[ "$source" == upstream ]]; then
  rm -f -- "$candidate"
  # Keep a retry available after a full 300s transfer timeout. The 600s
  # retry window bounds when a new attempt starts; each attempt is still 300s.
  curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
    --retry 2 --retry-all-errors --retry-max-time 600 --connect-timeout 15 --max-time 300 \
    --max-filesize "$max_bytes" \
    --output "$candidate" "$url"
  if ! verify_candidate; then
    echo "upstream tool artifact failed size or checksum verification" >&2
    exit 1
  fi
  if [[ "$cache_enabled" == true && "$NDDEV_CACHE_MODE" == read-write ]]; then
    put_code=000
    if put_code=$(curl --silent --show-error --connect-timeout 3 --max-time 120 --retry 2 --retry-all-errors \
        --cacert "$AWS_CA_BUNDLE" --aws-sigv4 "$sigv4" --user "$AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY" \
        --request PUT --upload-file "$candidate" --output /dev/null --write-out '%{http_code}' "$object_url"); then
      case "$put_code" in 200|201|204) cache_result="${cache_result}+stored" ;; *) cache_result="${cache_result}+store-http-$put_code" ;; esac
    else
      cache_result="${cache_result}+store-transport-error"
    fi
  fi
fi

if [[ -n "$runner_entry" && "$source" != runner ]]; then
  stored=false
  if install -d -- "${runner_entry%/*}" 2>/dev/null && staged=$(mktemp "$runner_entry.XXXXXXXXXX" 2>/dev/null); then
    if cp -- "$candidate" "$staged" 2>/dev/null && mv -f -- "$staged" "$runner_entry" 2>/dev/null; then
      stored=true
    else
      rm -f -- "$staged"
    fi
  fi
  if [[ "$stored" == true ]]; then runner_result="${runner_result}+stored"; else runner_result="${runner_result}+store-failed"; fi
  # A month without a hit means the pin moved on; staged leftovers of killed jobs go too.
  find "${runner_entry%/*}" -maxdepth 1 -type f -mtime +30 -delete 2>/dev/null || true
fi

bytes=$(stat --format='%s' -- "$candidate")
test ! -L "$output"
install -m 0600 "$candidate" "$output"
duration_ms=$(( $(date +%s%3N) - started ))
printf 'source=%s\nbytes=%s\nduration_ms=%s\n' "$source" "$bytes" "$duration_ms" >>"$GITHUB_OUTPUT"
printf 'nddev_tool_cache_event={"source":"%s","cache_result":"%s","runner_cache":"%s","sha256":"%s","bytes":%s,"duration_ms":%s}\n' \
  "$source" "$cache_result" "$runner_result" "$expected_sha256" "$bytes" "$duration_ms"
