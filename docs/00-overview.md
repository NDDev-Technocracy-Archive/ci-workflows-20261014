# ci-workflows — Overview

`ci-workflows` is a **reusable GitHub Actions library plus a GitHub-native
CI/CD, security, governance, and supply-chain knowledge base**, current for
July 2026. Consumers reference the reusable workflows by full commit SHA; the
docs in this folder explain the platform behavior those workflows depend on so
you can compose them correctly for your repository's billing reality.

The library is intentionally opinionated:

- Every third-party action is pinned to a **full commit SHA** with a version
  comment. Dependabot bumps the SHA. That is one level deep: a composite action
  may name further actions by tag inside its own `action.yml`, and GitHub
  resolves the whole nested graph at job setup, so a pin one level deep is not
  the whole story. `check_transitive_action_pins.py` resolves every pinned action
  and reports any that reaches a tag; the two that did — the Flutter and Qt setup
  actions — are gone, replaced by toolchain provisioning this repository pins
  itself.
- Every workflow declares least-privilege `permissions`, `concurrency`, and
  `timeout-minutes`.
- Container images are digest-pinned; downloaded binaries are checksum-verified.

## The three-tier model

Cost and capability on GitHub depend on whether a repository is public,
private-on-a-free-plan, or private with paid GitHub Advanced Security (GHAS).
Every doc here maps features to one of three tiers.

These three are a **reading aid, not the model.** Visibility, base plan and the
three paid add-ons vary independently, and most real repositories sit in a
combination the three tiers cannot name. The machine-readable model —
four operating profiles, all eight Code Security / Secret Protection / Code
Quality combinations, and the controls that go with each — lives in
`catalog/profiles.yml` and is rendered to
[the generated profile matrix](generated/profile-matrix.md). Where this prose
and that matrix disagree, the matrix is right: it is validated, this is not.

| Tier | What you get | Doc |
| --- | --- | --- |
| **Public OSS (free full suite)** | Standard hosted runners, CodeQL, native secret scanning + push protection, dependency review, OSSF Scorecard, artifact attestations/SBOM, GHCR public, Pages, rulesets — all free | [01-public-oss-free.md](01-public-oss-free.md) |
| **Private without paid add-ons** | actionlint, zizmor (no SARIF upload), gitleaks, static validation, SBOM + checksummed immutable releases (attestations need GHEC on private), OIDC, cross-platform smoke. CodeQL / native secret scanning / dependency review / harden-runner are **paid** and excluded. Self-hosted gives zero GitHub runner-meter; hosted uses bounded quota with possible overage. | [02-private-free.md](02-private-free.md) |
| **Private-paid / GHAS** | Code scanning, secret scanning + push protection, dependency review, Copilot Autofix, delegated governance — requires paid GitHub Code Security / Secret Protection | [03-private-paid-ghas.md](03-private-paid-ghas.md) |

### The one capability that ignores the model

**GitHub Code Quality** (GA and billable since 2026-07-20) is **not** sorted by
visibility the way the tiers assume: its licence is independent of GHAS, and
committers are counted once per organization rather than per repository. Its
public per-committer rate is **disputed between GitHub's own sources**, so no
public cost is compiled for it in either direction. It therefore sits outside
the three tiers as an opt-in fourth one — see
[16 Code Quality tier](16-code-quality.md). Both free tiers exclude it, and the
free maintainability substitutes are listed there.

### Publisher posture versus opt-in paid programmes

The three tiers describe what GitHub *offers*. This library is published from a
GitHub Organization, not an Enterprise account, and it does **not** assume the
publisher purchased Enterprise Cloud, Code Security, Secret Protection, or Code
Quality. A live GitHub plan belongs to one organization; do not copy one
account's plan onto another. [17 Paid organization programmes](17-nddev-tier.md)
is the opt-in guide for consumers who independently hold those products. Public
repositories still get free CodeQL, SARIF, dependency review, Scorecard and
attestations. Private repositories without those purchases use the private-free
programme and `release-supply-chain-free.yml`; attested private releases require
Enterprise Cloud.

### Personal-account repositories

A repository owned by a **personal** account (not an organization) takes the
[private-free](02-private-free.md) posture regardless of visibility — it has no
GHAS, no Enterprise Cloud, and a 2 000 min/month Actions quota with no org pool.
Its runner strategy differs from a generic private-free repo: every job is
routed to a **repo-level** self-hosted runner, because an org-level runner is
not reachable from a personal-account namespace. See
[18 Personal-account tier](18-personal-account-tier.md).

## How to consume a reusable workflow

Reference by `owner/repo/.github/workflows/<name>.yml@<full-sha>` from a caller
workflow's `jobs.<id>.uses`. Pin the SHA; never a tag or branch.

```yaml
jobs:
  actionlint:
    permissions:
      contents: read
    uses: NDDev-Technocracy-Archive/ci-workflows-20261014/.github/workflows/actionlint.yml@<full-sha>
```

The caller job must grant every permission the reusable job declares, or the run
fails at startup — see the [permissions cap gotcha](04-actions-core.md#reusable-caller-permissions-cap).
For end-to-end caller examples per tier, see the tier docs and the repository
[README](../README.md).

## Workflow inventory

| Workflow | Purpose | Primary tier |
| --- | --- | --- |
| `public-codeql.yml` | CodeQL code scanning | Public |
| `public-scorecard.yml` | OSSF Scorecard SARIF upload | Public |
| `public-scorecard-analysis.yml` | OSSF Scorecard read-only SARIF analysis | Public |
| `public-scorecard-json.yml` | OSSF Scorecard JSON artifact | Public |
| `public-dependency-review.yml` | PR dependency review | Public |
| `secret-scan.yml` | Gitleaks history-aware secret scan | Both |
| `actionlint.yml` | Workflow YAML linting | Both |
| `zizmor-sarif.yml` | zizmor Actions static analysis, SARIF upload | Public / GHAS |
| `zizmor-no-sarif.yml` | zizmor Actions static analysis, no upload | Private-free |
| `cross-platform-smoke.yml` | OS-matrix smoke test | Both |
| `private-static.yml` | Zero-cost single-job validation | Private-free |
| `release-supply-chain.yml` | Archive, SBOM, SHA256SUMS, attestations, Release | Public / private GHEC |
| `release-supply-chain-free.yml` | Archive, SBOM, SHA256SUMS, Release (no attestations) | Both |
| `ci.yml` | This repo's own self-CI | Internal |
| `release.yml` | Tag-driven release entrypoint | Internal |
| `python-ci.yml` / `node-ci.yml` / `go-ci.yml` / `rust-ci.yml` / `java-ci.yml` / `dotnet-ci.yml` | Language build/test/lint packs | Both |
| `container-ci.yml` | Container build + Trivy scan | Both |
| `terraform-ci.yml` | Terraform fmt/validate/plan | Both |
| `docs-ci.yml` | Docs lint/link-check/build | Both |
| `monorepo-changed-paths.yml` | Changed-path filtering for monorepos | Both |
| `dart-flutter-ci.yml` · `cpp-ci.yml` · `qt-ci.yml` · `kotlin-android-ci.yml` · `swift-ci.yml` · `r-ci.yml` · `web-ci.yml` · `sql-ci.yml` | Language packs (Dart/Flutter, C/C++, Qt, Kotlin/Android, Swift, R, web, SQL) | Both |
| `coverage-gate.yml` · `docs-quality.yml` · `pr-hygiene.yml` | Coverage, docs quality, PR hygiene | Both |
| `semgrep-ci.yml` · `osv-scan.yml` · `grype-scan.yml` · `hadolint-ci.yml` · `iac-scan.yml` | Free SAST/SCA/IaC (incl. private-free) | Both |
| `mutation-testing.yml` · `fuzzing.yml` · `benchmark.yml` · `benchmark-compare.yml` | Mutation testing, fuzzing, benchmark publish/compare lanes | Both |

> The July 2026 language/quality/security/testing packs are documented in
> [15 Language & quality packs](15-language-and-quality-packs.md).

> zizmor is split into two callers: `zizmor-sarif.yml` (uploads to code
> scanning, for public and GHAS) and `zizmor-no-sarif.yml` (fails the job on
> findings without upload, for the private-free tier).

## Document index

- Tiers: [01 Public OSS free](01-public-oss-free.md) ·
  [02 Private free](02-private-free.md) ·
  [03 Private paid / GHAS](03-private-paid-ghas.md) ·
  [16 Code Quality](16-code-quality.md) ·
  [17 Paid organization programmes](17-nddev-tier.md) ·
  [18 Personal account](18-personal-account-tier.md)
- Platform: [04 Actions core](04-actions-core.md) · [05 Runners](05-runners.md)
- Security: [06 Security scanning](06-security-scanning.md) ·
  [07 Supply chain / SLSA / SBOM / attestations](07-supply-chain-slsa-sbom-attestations.md)
- Governance: [08 Governance & rulesets](08-governance-rulesets.md)
- Delivery: [09 Releases & packages](09-releases-packages.md) ·
  [10 Deployments & environments](10-deployments-environments.md)
- Operations: [11 Observability & analytics](11-observability-analytics.md) ·
  [12 Community & DX](12-community-dx.md)
- Tooling: [13 External tools](13-external-tools.md) ·
  [14 AI / agentic workflows](14-ai-agentic-workflows.md)
- Packs: [15 Language & quality packs](15-language-and-quality-packs.md)
- Horizon: [Watchlist 2026](watchlist-2026.md)
- Security deep-dive: [pull_request_target / pwn requests](security/pull-request-target.md)
- Generated from catalog: [capability matrix](generated/capability-matrix.md) ·
  [workflow inventory](generated/workflow-inventory.md) ·
  [Scorecard SARIF evidence](generated/scorecard-evidence.md)

---
Last verified: 2026-07-08
