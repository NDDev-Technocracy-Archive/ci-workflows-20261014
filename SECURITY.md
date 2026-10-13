# Security Policy

## Reporting a vulnerability

Report suspected vulnerabilities privately via GitHub Security Advisories:
https://github.com/NDDev-Technocracy-Archive/ci-workflows-20261014/security/advisories/new

Do not open public issues for security reports. You will receive an initial
response as soon as reasonably possible.

## Consuming these workflows securely

- **Always pin by full commit SHA**, never by tag or branch:
  `uses: NDDev-Technocracy-Archive/ci-workflows-20261014/.github/workflows/<name>.yml@<40-char-sha>`.
  Tags are mutable; a full SHA is immutable. Dependabot can bump the pinned SHA.
- Grant the calling job only the permissions the reusable declares it needs.
- Use the dedicated private-free workflows for private repositories without
  paid services. They contain no Harden-Runner action and use no SARIF upload;
  code scanning, native secret scanning, and Harden-Runner require paid plans
  on private repositories.

## Posture of this repository

- Every third-party action is pinned to a full commit SHA with a version comment.
- Every workflow declares least-privilege `permissions`, `concurrency`, and a
  `timeout-minutes`.
- `ci.yml` runs static validators (`scripts/validate_all.py`), `actionlint`, and
  `zizmor` (regular persona, SARIF) against this repository's own workflows on
  every push and pull request; `ci-gate` aggregates them as truthful advisory
  evidence, not a required merge context.
- `check_harden_runner_contract.py` rejects conditional Harden-Runner steps and
  any paid runtime-hardening reference in cross-tier/private-free workflows.
- `main` is protected by a repository ruleset (`.github/rulesets/branch-main.json`):
  pull-request-only merge commits, resolved review threads, signed commits, and
  no force-push or deletion. Ordinary merge does not wait on a required general
  CI status check. The solo-maintainer repository does not require
  self-approval. Release tags are
  protected by `.github/rulesets/tag-semver.json`.
- Releases are tag-driven and immutable, and ship an SPDX SBOM, SHA256SUMS, a
  build-provenance attestation, and an SBOM attestation (SLSA v1 Build L3,
  built inside the reusable `release-supply-chain.yml`). Verify with
  `scripts/verify_attestations.sh`.
