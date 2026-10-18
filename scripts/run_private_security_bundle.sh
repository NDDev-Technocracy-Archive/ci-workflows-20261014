#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

readonly actionlint_version=1.7.12
readonly actionlint_sha256=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8
readonly zizmor_version=1.31.1
readonly osv_version=2.7.0
readonly osv_sha256=160c6253e2922e9e0a6a9771c5c4c40a4b3c6090b34a3cdd5e80c96cc2e4218b
readonly gitleaks_version=8.30.1
readonly gitleaks_sha256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
readonly gitleaks_size=8230402

test "${RUNNER_OS:?}" = Linux
test "${RUNNER_ARCH:?}" = X64
test -n "${GITHUB_WORKSPACE:?}"
test -n "${RUNNER_TEMP:?}"
test -n "${GH_TOKEN:?}"
readonly zizmor_sarif="${ZIZMOR_SARIF_PATH:?}"
readonly osv_sarif="${OSV_SARIF_PATH:?}"
readonly gitleaks_sarif="${GITLEAKS_SARIF_PATH:?}"
readonly actionlint_log="${ACTIONLINT_LOG_PATH:?}"
readonly zizmor_target="${ZIZMOR_TARGET:-.}"
readonly osv_lockfile="${OSV_LOCKFILE:-}"
readonly gitleaks_scan_scope="${GITLEAKS_SCAN_SCOPE:-ref-history}"
printf '{"version":"2.1.0","runs":[]}\n' > "$zizmor_sarif"
printf '{"version":"2.1.0","runs":[]}\n' > "$osv_sarif"
printf '{"version":"2.1.0","runs":[]}\n' > "$gitleaks_sarif"
: > "$actionlint_log"
[[ "$zizmor_target" != /* && "$zizmor_target" != *..* ]]
test -e "$GITHUB_WORKSPACE/$zizmor_target"
if [[ -n "$osv_lockfile" ]]; then
  [[ "$osv_lockfile" != /* && "$osv_lockfile" != *..* ]]
  test -f "$GITHUB_WORKSPACE/$osv_lockfile"
fi
case "$gitleaks_scan_scope" in
  ref-history) readonly gitleaks_log_opts=HEAD ;;
  all-refs) readonly gitleaks_log_opts=--all ;;
  *) echo "GITLEAKS_SCAN_SCOPE must be ref-history or all-refs" >&2; exit 2 ;;
esac
tool_root="$(mktemp -d "${RUNNER_TEMP}/nddev-security-bundle.XXXXXXXX")"
cleanup() { find "$tool_root" -depth -delete; }
trap cleanup EXIT
install -d -m 0700 "$tool_root/bin"
export PATH="$tool_root/bin:$PATH"

use_or_download() {
  local supplied=$1 output=$2 url=$3
  if [[ -n "$supplied" ]]; then
    [[ "$supplied" == "$RUNNER_TEMP"/* && -f "$supplied" && ! -L "$supplied" ]]
    install -m 0600 "$supplied" "$output"
    return
  fi
  curl -fsSL --retry 2 --retry-max-time 120 -o "$output" "$url"
}

use_or_download "${ACTIONLINT_ARCHIVE_PATH:-}" "$tool_root/actionlint.tar.gz" \
  "https://github.com/rhysd/actionlint/releases/download/v${actionlint_version}/actionlint_${actionlint_version}_linux_amd64.tar.gz"
printf '%s  %s\n' "$actionlint_sha256" "$tool_root/actionlint.tar.gz" | sha256sum -c -
tar -xzf "$tool_root/actionlint.tar.gz" -C "$tool_root/bin" actionlint
chmod 0700 "$tool_root/bin/actionlint"

use_or_download "${OSV_SCANNER_PATH:-}" "$tool_root/osv-scanner" \
  "https://github.com/google/osv-scanner/releases/download/v${osv_version}/osv-scanner_linux_amd64"
printf '%s  %s\n' "$osv_sha256" "$tool_root/osv-scanner" | sha256sum -c -
install -m 0700 "$tool_root/osv-scanner" "$tool_root/bin/osv-scanner"

use_or_download "${GITLEAKS_ARCHIVE_PATH:-}" "$tool_root/gitleaks.tar.gz" \
  "https://github.com/gitleaks/gitleaks/releases/download/v${gitleaks_version}/gitleaks_${gitleaks_version}_linux_x64.tar.gz"
test "$(wc -c < "$tool_root/gitleaks.tar.gz" | tr -d '[:space:]')" = "$gitleaks_size"
printf '%s  %s\n' "$gitleaks_sha256" "$tool_root/gitleaks.tar.gz" | sha256sum -c -
tar -xzf "$tool_root/gitleaks.tar.gz" -C "$tool_root/bin" gitleaks
chmod 0700 "$tool_root/bin/gitleaks"
test "$(gitleaks version)" = "$gitleaks_version"

gitleaks_args=(detect --source "$GITHUB_WORKSPACE" --redact=100 --no-banner --exit-code 1 --log-opts "$gitleaks_log_opts" --report-format sarif --report-path "$gitleaks_sarif")
if [[ -n "${GITLEAKS_CONFIG_PATH:-}" ]]; then
  [[ "$GITLEAKS_CONFIG_PATH" != /* && "$GITLEAKS_CONFIG_PATH" != *..* ]]
  test -f "$GITHUB_WORKSPACE/$GITLEAKS_CONFIG_PATH"
  gitleaks_args+=(--config "$GITHUB_WORKSPACE/$GITLEAKS_CONFIG_PATH")
fi

declare -a failed=()
run_gate() {
  local name=$1
  shift
  printf '::group::%s\n' "$name"
  if "$@"; then
    printf '%s passed\n' "$name"
  else
    failed+=("$name")
  fi
  printf '::endgroup::\n'
}

run_zizmor() {
  uvx "zizmor@${zizmor_version}" --persona pedantic --min-severity low \
    --format sarif "$zizmor_target" > "$zizmor_sarif" || return
  # With --format sarif zizmor exits 0 whatever it finds; the reported results decide
  # this gate. Ignored findings are not reported; a suppressed one would not count.
  local findings
  findings=$(jq -r '.runs[].results[]? | select((.suppressions // []) == [])
    | "\(.ruleId) \(.locations[0].physicalLocation.artifactLocation.uri):\(.locations[0].physicalLocation.region.startLine)"' \
    "$zizmor_sarif") || return
  if [[ -n "$findings" ]]; then
    printf 'zizmor findings:\n%s\n' "$findings"
    return 1
  fi
}

run_actionlint() {
  actionlint -color 2>&1 | tee "$actionlint_log"
}

run_osv() {
  local output status
  set +e
  if [[ -n "$osv_lockfile" ]]; then
    output=$(osv-scanner scan source --lockfile="$osv_lockfile" --format sarif \
      --output-file "$osv_sarif" 2>&1)
    status=$?
  else
    output=$(osv-scanner scan source --recursive . --format sarif \
      --output-file "$osv_sarif" 2>&1)
    status=$?
  fi
  set -e
  printf '%s\n' "$output"
  if (( status == 0 )); then
    return 0
  fi
  # An empty infrastructure/schema repository has nothing OSV can resolve.
  # The fail-safe empty SARIF was created before the scan, so this exact
  # scanner result is a successful zero-package inventory. Vulnerabilities and
  # every operational failure retain their original non-zero status.
  if grep -Fq 'No package sources found' <<<"$output"; then
    return 0
  fi
  return "$status"
}

cd "$GITHUB_WORKSPACE"
run_gitleaks() {
  bash "$(dirname "${BASH_SOURCE[0]}")/prepare_gitleaks_history.sh" "$gitleaks_scan_scope" || return
  gitleaks "${gitleaks_args[@]}"
}
run_gate actionlint run_actionlint
run_gate zizmor run_zizmor
run_gate osv-scanner run_osv
run_gate gitleaks run_gitleaks

for report in "$zizmor_sarif" "$osv_sarif" "$gitleaks_sarif"; do
  if ! jq -e '(.version == "2.1.0") and (.runs | type == "array")' "$report" >/dev/null; then
    failed+=("evidence-integrity")
    printf 'invalid SARIF evidence: %s\n' "$report" >&2
  fi
done

{
  echo '## Consolidated private security bundle'
  echo
  echo '| Gate | Result |'
  echo '| --- | --- |'
  for gate in actionlint zizmor osv-scanner gitleaks; do
    result=passed
    for failed_gate in "${failed[@]:-}"; do
      if [[ "$gate" = "$failed_gate" ]]; then result=failed; fi
    done
    printf '| `%s` | %s |\n' "$gate" "$result"
  done
} >> "$GITHUB_STEP_SUMMARY"

if (( ${#failed[@]} != 0 )); then
  printf 'security gates failed: %s\n' "${failed[*]}" >&2
  exit 1
fi
