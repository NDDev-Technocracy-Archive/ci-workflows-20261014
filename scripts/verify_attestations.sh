#!/usr/bin/env bash
# Verify the SLSA build-provenance and SBOM attestations of a released archive.
#
# Usage:
#   scripts/verify_attestations.sh <archive> <owner/repo>
#   scripts/verify_attestations.sh ci-workflows-0.2.0.tar.gz NDDev-Technocracy-OpenNetwork/ci-workflows
#
# Requires the GitHub CLI (`gh`) authenticated with read access to the repo.
# Never prints or persists any credential; `gh` uses its own credential store.
set -euo pipefail

archive="${1:-}"
repo="${2:-}"

if [ -z "$archive" ] || [ -z "$repo" ]; then
  echo "usage: $0 <archive> <owner/repo>" >&2
  exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh (GitHub CLI) is required: https://cli.github.com" >&2
  exit 3
fi

if [ ! -f "$archive" ]; then
  echo "archive not found: $archive" >&2
  exit 4
fi

echo "==> Verifying SLSA build provenance for $archive"
gh attestation verify "$archive" --repo "$repo"

echo "==> Verifying SPDX SBOM attestation for $archive"
gh attestation verify "$archive" --repo "$repo" \
  --predicate-type "https://spdx.dev/Document/v2.3"

echo "OK: build-provenance and SBOM attestations verified for $archive ($repo)"
