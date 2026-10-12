# ci-workflows

A **July-2026 GitHub-native CI/CD, security, governance, and supply-chain
automation knowledge base plus reusable workflow library**.

It separates repository visibility, runner billing, base plan, and the three
independent paid add-ons instead of collapsing them into one tier ladder.
Named programmes cover **public OSS**, **private without paid add-ons**, and
**private paid capabilities** — plus one opt-in product with its own licence
(**Code Quality**). The library ships SHA-pinned reusable workflows for each and
documents every capability with its status, cost model, risk, and implementation
path in [`docs/`](docs/00-overview.md) and the machine-readable
[`catalog/`](catalog/README.md).

## Ready programmes, by repository and billing contract

| Tier | What you get | Notes |
| --- | --- | --- |
| **Public OSS** | The full free security suite: CodeQL, OSSF Scorecard, dependency review, native secret scanning, gitleaks, actionlint, zizmor (SARIF), harden-runner, SBOM + attestations. | Standard hosted runners and code scanning are free on public repos. |
| **Private, no paid add-ons** | actionlint, zizmor (no SARIF), gitleaks, private static validation, cross-platform smoke, SBOM + checksummed immutable releases (no attestations), OIDC. | Self-hosted gives zero GitHub runner-meter; standard hosted uses bounded quota and may incur overage. Code Security, Secret Protection and private attestations are excluded. |
| **Private, selected paid capabilities** | Only the workflows unlocked by the independently enabled Code Security, Secret Protection, and Code Quality products. | Buying one add-on never enables another. Private attestations separately require Enterprise Cloud. |

See [`docs/01-public-oss-free.md`](docs/01-public-oss-free.md),
[`docs/02-private-free.md`](docs/02-private-free.md), and
[`docs/03-private-paid-ghas.md`](docs/03-private-paid-ghas.md).

**GitHub Code Quality sits outside these three.** It is a separate licence that
GHAS does not include, requires a Team or Enterprise plan, and bills per active
committer counted once per organization rather than per repository — so enabling
one repository already bills the whole active committer set.

Its **public** per-committer rate is disputed between GitHub's own sources: the
product page states `$0` for public repositories, while the billing
documentation grants no visibility exemption and states no rate. This library
therefore compiles no public cost from it **in either direction**; the rate stays
account-observed until an invoice settles it. Both free tiers exclude Code
Quality, and it is enabled deliberately via the org-level **Repository access**
control. The authoritative record is the `github-code-quality-transition` entry
in [`catalog/product-facts.yml`](catalog/product-facts.yml). See
[`docs/16-code-quality.md`](docs/16-code-quality.md).

This library is published from a **GitHub Organization**, not an Enterprise
account, and it does **not** assume the publisher purchased Enterprise Cloud,
Code Security, Secret Protection, or Code Quality. A live GitHub plan belongs
to one organization; do not copy one account's plan onto another. Paid
programmes stay explicitly selectable for other consumers — see
[`docs/17-nddev-tier.md`](docs/17-nddev-tier.md). Public repositories still get
the free GitHub security surfaces (CodeQL, SARIF, dependency review, Scorecard,
attestations). Private repositories without those purchases use the
private-free programme and `release-supply-chain-free.yml`; attested private
releases require Enterprise Cloud and must be opted into.

## Capability → workflow map

| Capability | Workflow | Public | Private-free | Private-paid |
| --- | --- | :---: | :---: | :---: |
| CodeQL code scanning | `public-codeql.yml` | ✅ | ❌ paid | ✅ |
| OSSF Scorecard (SARIF) | `public-scorecard.yml` | ✅ | ❌ | ✅ |
| OSSF Scorecard (read-only SARIF analysis) | `public-scorecard-analysis.yml` | ✅ | ❌ | ✅ |
| OSSF Scorecard (JSON artifact) | `public-scorecard-json.yml` | ✅ | ❌ | ✅ |
| Dependency Review | `public-dependency-review.yml` | ✅ | ❌ paid | ✅ |
| Gitleaks secret scan | `secret-scan.yml` | ✅ | ✅ | ✅ |
| actionlint | `actionlint.yml` | ✅ | ✅ | ✅ |
| zizmor (SARIF) | `zizmor-sarif.yml` | ✅ | ❌ (needs code scanning) | ✅ |
| zizmor (no SARIF) | `zizmor-no-sarif.yml` | ✅ | ✅ | ✅ |
| Cross-platform smoke | `cross-platform-smoke.yml` | ✅ | ✅ | ✅ |
| Release supply chain (SBOM + attest) | `release-supply-chain.yml` | ✅ | ❌ needs GHEC | ⚠️ GHEC only |
| Release supply chain (no attestations) | `release-supply-chain-free.yml` | ✅ | ✅ | ✅ |
| Lightweight static validation | `private-static.yml` | ✅ | ✅ | ✅ |
| Language CI packs | `python-ci.yml`, `node-ci.yml`, `go-ci.yml`, `rust-ci.yml`, `java-ci.yml`, `dotnet-ci.yml` | ✅ | ✅ | ✅ |
| Container image scan (Trivy) | `container-ci.yml` | ✅ | ✅ | ✅ |
| Terraform CI | `terraform-ci.yml` | ✅ | ✅ | ✅ |
| Docs CI | `docs-ci.yml` | ✅ | ✅ | ✅ |
| Monorepo changed-paths router | `monorepo-changed-paths.yml` | ✅ | ✅ | ✅ |

The machine-readable source of truth is [`catalog/capabilities.yml`](catalog/capabilities.yml).
Generated mirrors live in [`docs/generated/`](docs/generated/) and are checked by
`scripts/generate_docs.py --check`.
Repository-owned Python validation runs through one hermetic launcher; its
machine policy and threat boundary are documented in
[`docs/19-python-execution.md`](docs/19-python-execution.md).

### Extended packs (July 2026)

Beyond the security suite, the library ships language packs (Python, Node, Go,
Rust, Java, .NET, **Dart/Flutter, C/C++, Qt, Kotlin/Android, Swift, R, HTML/CSS,
SQL**), quality gates (coverage, docs-quality, PR-hygiene), free SAST/SCA/IaC for
every tier (Semgrep, OSV-Scanner, Grype, hadolint, Checkov — free even on
private-free, where CodeQL and dependency review are paid), advanced testing
(mutation, fuzzing, benchmark), and opt-in Level-3 patterns (AI code review,
release-please). See
[`docs/15-language-and-quality-packs.md`](docs/15-language-and-quality-packs.md)
and copy-paste callers under [`examples/`](examples/).

## Usage

Always pin by **full commit SHA**, and pin to a commit a release tag points at
rather than to whatever `main` happens to be — an estate pinned to arbitrary
commits cannot answer "what version are we on". Release tags here are protected
against deletion, updates and non-fast-forward moves, so the tag names a stable
commit; the SHA is what the workflow reference should carry, with a trailing
comment naming the release. Dependabot bumps the SHA.
A caller job **must grant every permission the reusable job declares**, or the
run fails at startup — see [`docs/04-actions-core.md`](docs/04-actions-core.md).

`go-ci.yml` keeps checkout shallow by default (`fetch_depth: 1`). Set
`fetch_depth: 0` when a validation command inspects commit ancestry or pull
request merge parents; tree-only builds can retain the faster default.
Its `cache` input defaults to `true` for backward compatibility and hosted
runners. Set `cache: false` only on a warm self-hosted runner whose Go caches
are owned independently; this prevents `actions/setup-go` from restoring an
Actions cache over the runner's existing cache tree.

### Public repository

Security suite (push + PR):

```yaml
# .github/workflows/security.yml
name: security
on:
  push: { branches: [main] }
  pull_request: { branches: [main] }
permissions: {}
jobs:
  codeql:
    permissions: { actions: read, contents: read, security-events: write }
    uses: NDDev-Technocracy-Sandbox/ci-workflows/.github/workflows/public-codeql.yml@<sha>
    with:
      languages: '["python","actions"]'
  secret-scan:
    permissions: { contents: read }
    uses: NDDev-Technocracy-Sandbox/ci-workflows/.github/workflows/secret-scan.yml@<sha>
  actionlint:
    permissions: { contents: read }
    uses: NDDev-Technocracy-Sandbox/ci-workflows/.github/workflows/actionlint.yml@<sha>
  zizmor:
    permissions: { contents: read, security-events: write }
    uses: NDDev-Technocracy-Sandbox/ci-workflows/.github/workflows/zizmor-sarif.yml@<sha>
```

Dependency Review runs **on pull requests only**:

```yaml
# .github/workflows/dependency-review.yml
name: dependency-review
on: { pull_request: { branches: [main] } }
permissions: {}
jobs:
  dependency-review:
    permissions: { contents: read, pull-requests: write }
    uses: NDDev-Technocracy-Sandbox/ci-workflows/.github/workflows/public-dependency-review.yml@<sha>
```

OSSF Scorecard runs **on push-to-default + schedule only** (`pull_request` is
experimental and unsupported by the action), so keep it in its own file. The
SARIF workflow below uploads persistent Code Scanning results; use
`public-scorecard-json.yml` instead for an artifact-only signal. The publishing
entrypoint defaults to publishing and requires its write permissions at graph
construction:

```yaml
# .github/workflows/scorecard.yml
name: scorecard
on:
  push: { branches: [main] }
  schedule: [{ cron: "31 2 * * 1" }]
permissions: {}
jobs:
  scorecard:
    permissions: { security-events: write, id-token: write, contents: read, actions: read }
    uses: NDDev-Technocracy-Sandbox/ci-workflows/.github/workflows/public-scorecard.yml@<sha>
    with:
      publish_results: true
      sarif_category: ossf-scorecard
```

For a physically read-only graph, call `public-scorecard-analysis.yml` with
only `contents: read` and `actions: read`. It contains no OIDC, artifact upload,
or Code Scanning upload job; `public-scorecard.yml` remains the compatible
publication entrypoint. A former caller that explicitly passed
`publish_results: false` must move to the analysis entrypoint; permission
preflight makes that physical split necessary.

The pinned Scorecard emits three authoritative SARIF categories —
`supply-chain/branch-protection`, `supply-chain/local`, and
`supply-chain/online-scm`. The `ossf-scorecard` upload category is only the
fallback used when a SARIF run lacks `automationDetails.id`; it does not replace
or merge upstream run identities.

### Private repository (no paid add-ons)

```yaml
# .github/workflows/security.yml
name: security
on:
  push: { branches: [main] }
  pull_request: { branches: [main] }
permissions: {}
jobs:
  secret-scan:
    permissions: { contents: read }
    uses: NDDev-Technocracy-Sandbox/ci-workflows/.github/workflows/secret-scan.yml@<sha>
  actionlint:
    permissions: { contents: read }
    uses: NDDev-Technocracy-Sandbox/ci-workflows/.github/workflows/actionlint.yml@<sha>
  zizmor:
    permissions: { contents: read }   # no security-events: write — least privilege
    uses: NDDev-Technocracy-Sandbox/ci-workflows/.github/workflows/zizmor-no-sarif.yml@<sha>
  validate:
    permissions: { contents: read }
    uses: NDDev-Technocracy-Sandbox/ci-workflows/.github/workflows/private-static.yml@<sha>
    with:
      command: "python3 scripts/validate_all.py"
```

### Release

Attested variant — **public repositories on any plan, or private repositories
on GitHub Enterprise Cloud** (GitHub Artifact Attestations are a plan gate on
private/internal repos; GHAS does not unlock them):

```yaml
# .github/workflows/release.yml
name: release
on: { push: { tags: ["[0-9]+.[0-9]+.[0-9]+"] } }
permissions: {}
jobs:
  publish:
    permissions: { contents: write, id-token: write, attestations: write, artifact-metadata: write }
    uses: NDDev-Technocracy-Sandbox/ci-workflows/.github/workflows/release-supply-chain.yml@<sha>
    with:
      version: ${{ github.ref_name }}
      package_name: my-repo
      archive_paths: "README.md LICENSE VERSION CHANGELOG.md src"
```

Private repositories on Free/Pro/Team use the attestation-free variant — same
five checksummed assets, `contents: write` only, no attest actions:

```yaml
jobs:
  publish:
    permissions: { contents: write }
    uses: NDDev-Technocracy-Sandbox/ci-workflows/.github/workflows/release-supply-chain-free.yml@<sha>
    with:
      version: ${{ github.ref_name }}
      package_name: my-repo
      archive_paths: "README.md LICENSE VERSION CHANGELOG.md src"
```

The release is **immutable** and ships canonical checksummed release notes, an
SPDX SBOM, SHA256SUMS, and (attested variant) a build-provenance attestation
plus an SBOM attestation (SLSA v1 Build L3). The free variant records
`slsa_build_level: null` in its manifest and makes no provenance claim. Verify
attested releases with
[`scripts/verify_attestations.sh`](scripts/verify_attestations.sh). See
[`docs/07-supply-chain-slsa-sbom-attestations.md`](docs/07-supply-chain-slsa-sbom-attestations.md).

Release versions are strict numeric SemVer (`X.Y.Z`, no leading zeros), and
`VERSION` must contain that value as one LF-terminated line. `archive_paths`
accepts normalized relative selections and expands each through the literal Git
index: only tracked files enter the archive, even when a selected directory
contains untracked output. Symlinks, submodules, and non-regular entries are
rejected. The reusable validates input syntax before checkout, checks out the
exact tag, and revalidates that tag's `VERSION` and tracked changelog heading.
The release version must have exactly one matching changelog heading.
Canonical notes must come from that non-empty section or from a tracked,
regular, non-symlink UTF-8 `notes_file` with non-whitespace content.
The SBOM scans the exact extracted archive payload. Every release publishes
exactly five assets: the archive, `sbom.spdx.json`, `release-notes.md`,
`release-manifest.json`, and `SHA256SUMS`. The same canonical notes file is used
for the release body and is checksum-bound because GitHub permits immutable
release metadata to be edited. Syft 1.46.0 is downloaded directly for Linux
X64/ARM64 runners and verified against pinned archive size and SHA-256 before
execution; no remote installer script runs. The manifest records the source tag
object and peeled commit, and the remote tag is revalidated immediately before
publication.

### Migrating release callers to 0.5.0

Remove the former `sbom_source_path` input, use a Linux X64/ARM64 runner, and
make every `archive_paths` selection a normalized relative tracked path whose
expansion contains regular files only. `VERSION` is mandatory and exact, and an
explicit `notes_file` must be a tracked regular non-symlink file. These are
intentional fail-closed changes from the 0.4.x contract.

Pin `0.5.1` or its full commit SHA to include canonical release notes in the
immutable manifest/checksum boundary. No caller input changed from `0.5.0`.

### Migrating to 0.6.0

- **Release:** private repositories on Free/Pro/Team switch from
  `release-supply-chain.yml` to `release-supply-chain-free.yml` and drop
  `id-token: write` / `attestations: write` from the caller job (GitHub
  Artifact Attestations require GHEC on private repos). Public and GHEC
  callers change nothing.
- **Benchmark:** the `auto_push` input is gone. Callers that passed
  `auto_push: false` switch to `benchmark-compare.yml` with
  `permissions: { contents: read }`; default-behavior callers keep
  `benchmark.yml` unchanged.
- **Monorepo router:** `filters` must be a strict JSON object of exact file
  paths or `/`-terminated directory prefixes — wildcard patterns now fail the
  run. Unresolvable bases fail instead of reporting "unchanged"; pushes
  without a usable previous tip conservatively run every group.
- **actionlint:** non-Linux-X64 runners are rejected by an explicit
  first-step guard (previously they failed mid-install with obscure errors).

### Migrating to 0.8.1

- **Attested releases:** callers of `release-supply-chain.yml` must add
  `artifact-metadata: write` to the publish job's `permissions` (alongside
  `id-token: write` / `attestations: write`). The attest actions
  (`actions/attest@v4.1.1`) document it as required to write the artifact
  storage record; because a caller's token is the ceiling for the reusable
  workflow, omitting it caps the attest step below the scope it needs. The
  free variant is unaffected (`contents: write` only).

## Common inputs

- `runner` — runner label (default `ubuntu-latest`). Workflows with a
  platform-specific payload enforce it: `actionlint.yml` accepts Linux X64
  only (guarded first step), `release-supply-chain*.yml` require Linux
  X64/ARM64.
- `upload_sarif` (zizmor) — split into `zizmor-sarif.yml` (uploads) and
  `zizmor-no-sarif.yml` (least privilege; no `security-events: write`).
- `egress_policy` — `audit` (default) or `block` for harden-runner.

Each workflow documents its full input set in its header comment.

Harden-Runner is present only in explicitly public/GHAS workflows and is
unconditional there. Cross-tier and private-free workflows contain no
Harden-Runner reference. This file-level separation is intentional: the action
has `pre` and `post` entry points that GitHub can execute even when a step-level
`if` evaluates to false, so a boolean toggle is not a safe disable mechanism.

## Governance

`main` and release tags are protected by **rulesets** in
[`.github/rulesets/`](.github/rulesets/) (pull-request-only merge commits,
resolved review threads, signed commits, no force-push or deletion, and tag
protection). Ordinary merge in this repository does not wait on a required
general CI status check. `ci-gate` still runs as truthful advisory evidence;
Scorecard, CodeQL and runtime fixtures stay background evidence. Consumers
may require `ci-gate` if they want a merge gate. The repository's
solo-maintainer rule does not require an impossible self-approval; reusable
projects with independent reviewers should require approvals and CODEOWNERS
review. See
[`docs/08-governance-rulesets.md`](docs/08-governance-rulesets.md) for the
rulesets-first model and a migration guide from classic branch protection.

## Repository map

```
docs/       CI/CD encyclopedia (public/private tiers, security, supply chain, governance, AI)
catalog/    machine-readable capability + tools + deprecations + operating-profile catalog
docs/generated/ catalog-derived matrices (do not edit by hand)
.github/
  workflows/   reusable workflows (the product)
  rulesets/    branch/tag/push ruleset specs
  ISSUE_TEMPLATE/  issue forms
scripts/    static validators (validate_all.py) + attestation verifier
examples/   copy-paste callers: per-tier + languages/ quality/ security/ testing/ infra/ level3/
```

## Conventions

- Third-party actions pinned to full commit SHAs with release or matching development-commit comments, and
  `check_transitive_action_pins.py` resolves each one to check what *it* calls —
  a pin is only one level deep, and the advisory sweep reports any action
  reaching a tag.
- Least-privilege `permissions`, `concurrency`, and `timeout-minutes` everywhere.
- No `${{ inputs.* }}` inline in `run:` (passed via `env:` — zizmor
  template-injection hardening).
- Digest-pinned container images (gitleaks) and checksum-verified downloads
  (actionlint).

## License

[AGPL-3.0-or-later](LICENSE). Author: Danil Silantyev (github:rldyourmnd), CEO NDDev.

- Security policy: [SECURITY.md](SECURITY.md)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Releases: https://github.com/NDDev-Technocracy-Sandbox/ci-workflows/releases


## CI feedback

The CI feedback workflow reports unsuccessful completed self-workflow attempts
as unassigned issues in this repository. It uses a pinned publisher and records
actual job conclusions and attempt identity without executing source-run code.
Issue publication does not launch a repair agent or authorize deployment.

Unreleased dependencies use a `commit:<full SHA>` comment matching the immutable
ref and catalog version. Stable releases retain their release-version comments;
the validator rejects a shortened or mismatched development identity.
