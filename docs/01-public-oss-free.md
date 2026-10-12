# Public OSS tier — the free full suite

Public repositories on GitHub get the **entire security and supply-chain suite
for free**, including features that are paid on private repositories. This is
the programme this public library itself runs, and it is where the library
delivers its full value without paid add-ons.

> **One documented exception: GitHub Code Quality is not free on public repos.**
> Unlike CodeQL and everything else in the table below, it is billed per active
> committer regardless of visibility. Being public only makes its Actions minutes
> free. Keep it out of this tier — see
> [16 Code Quality tier](16-code-quality.md).

## What public OSS gets free

| Capability | Free on public? | Delivered by |
| --- | --- | --- |
| Standard hosted runners (Linux/Windows/macOS) | ✅ unlimited minutes | any workflow |
| CodeQL code scanning | ✅ | [`public-codeql.yml`](#codeql) |
| Native secret scanning + push protection | ✅ | repository setting (not a workflow) |
| Dependency review (PR diff gate) | ✅ | [`public-dependency-review.yml`](#dependency-review) |
| OSSF Scorecard publish / read-only / JSON | ✅ | [`public-scorecard.yml`](#scorecard) / `public-scorecard-analysis.yml` / `public-scorecard-json.yml` |
| Artifact attestations / SBOM / provenance | ✅ | [`release-supply-chain.yml`](#attestations) |
| GHCR (public packages) | ✅ | [09 Releases & packages](09-releases-packages.md) |
| GitHub Pages | ✅ | [10 Deployments & environments](10-deployments-environments.md) |
| Repository rulesets | ✅ | [08 Governance & rulesets](08-governance-rulesets.md) |
| harden-runner egress control | ✅ | explicit public/GHAS workflows |
| GitHub Code Quality | ❌ paid per active committer even on public | [16 Code Quality tier](16-code-quality.md) |

> Standard GitHub-hosted runners are free with unlimited minutes on public
> repositories. Larger, GPU, and macOS-XL runners are billable **even for
> public repos** — see [05 Runners](05-runners.md).

## Standard hosted runners

Public repositories run on `ubuntu-latest`, `windows-latest`, and `macos-latest`
with no minute quota. The library defaults every reusable to `ubuntu-latest` and
exposes a `runner` input where an alternative label makes sense.

<a id="codeql"></a>
## CodeQL code scanning — `public-codeql.yml`

CodeQL is GitHub's semantic code-analysis engine. It is free on public repos and
uploads results to the repository's **Code scanning** tab. The reusable runs a
language matrix and needs `security-events: write`.

```yaml
jobs:
  codeql:
    permissions:
      actions: read
      contents: read
      security-events: write
    uses: NDDev-Technocracy-Sandbox/ci-workflows/.github/workflows/public-codeql.yml@<full-sha>
    with:
      languages: '["python","actions"]'
      queries: security-and-quality
      runner: ubuntu-latest
      dependabot_runner: ubuntu-latest
```

Inputs: `languages` (JSON array), `queries` (suite, default
`security-and-quality`), `runner`, `dependabot_runner`, `egress_policy`. Public
callers keep both on `ubuntu-latest`; private GHAS callers may route ordinary
analysis to a trusted private class and Dependabot pull requests to an isolated
untrusted class without changing the `CodeQL (<language>)` check identity. See
[06 Security scanning](06-security-scanning.md#codeql) for language coverage and
Copilot Autofix.

<a id="scorecard"></a>
## OSSF Scorecard — publish, read-only, and JSON entrypoints

Scorecard scores your repository against supply-chain best practices (pinned
actions, branch protection, token permissions, etc.). Use `public-scorecard.yml`
for SARIF/code-scanning upload, or
`public-scorecard-json.yml` when Scorecard
should remain a JSON artifact/check signal instead of a persistent
code-scanning alert source. `public-scorecard-json.yml` defaults
`publish_results: false` because reusable workflow calls do not satisfy the
OpenSSF Scorecard webapp workflow-shape verification required for publishing.

> **Trigger constraint:** Scorecard officially supports only `push` and
> `schedule` (default branch). `pull_request` and `workflow_dispatch` are
> experimental, and forks are unsupported. Call it from a dedicated workflow
> triggered on push to the default branch and a weekly schedule — **do not**
> place it under `pull_request`.

```yaml
# .github/workflows/scorecard.yml
on:
  push: { branches: [main] }
  schedule: [{ cron: "31 2 * * 1" }]
permissions: {}
jobs:
  scorecard:
    permissions:
      security-events: write
      id-token: write
      contents: read
      actions: read
    uses: NDDev-Technocracy-Sandbox/ci-workflows/.github/workflows/public-scorecard.yml@<full-sha>
    with:
      publish_results: true
      sarif_category: ossf-scorecard
```

Grant `id-token: write` and `security-events: write` only to
`public-scorecard.yml`, the publication entrypoint. Read-only consumers call
`public-scorecard-analysis.yml`; its physical workflow graph contains neither
OIDC nor artifact/SARIF upload authority and requires only `contents: read` plus
`actions: read`. The two entrypoints' common analysis steps are machine-checked
for semantic parity. Scorecard v5.5.0 deliberately emits separate SARIF runs for
`supply-chain/branch-protection`, `supply-chain/local`, and
`supply-chain/online-scm`. GitHub preserves those upstream
`runAutomationDetails.id` categories; the caller's `ossf-scorecard` input is a
fail-closed fallback only and does not collapse the three analyses.

<a id="dependency-review"></a>
## Dependency review — `public-dependency-review.yml`

Dependency review compares the dependency manifest between the base and head of a
pull request and fails when a new dependency introduces a vulnerability at or
above a severity threshold. Free on public repos; **paid (GHAS) on private**.

```yaml
on: { pull_request: { branches: [main] } }
permissions: {}
jobs:
  dependency-review:
    permissions:
      contents: read
      pull-requests: write
    uses: NDDev-Technocracy-Sandbox/ci-workflows/.github/workflows/public-dependency-review.yml@<full-sha>
    with:
      fail_on_severity: moderate
```

Inputs: `fail_on_severity` (`low`/`moderate`/`high`/`critical`),
`comment_summary_in_pr`, `runner`.

## Native secret scanning + push protection

Native secret scanning (and push protection, which blocks a push containing a
recognized secret) is a **repository setting**, not a workflow. Enable it under
**Settings → Code security**. It is free on public repos. For history-aware
scanning that also works on the private-free tier, the library ships
`secret-scan.yml` (gitleaks) — see [06 Security scanning](06-security-scanning.md#gitleaks).

<a id="attestations"></a>
## Artifact attestations, SBOM, and provenance — `release-supply-chain.yml`

Public repos can generate and publicly verify build provenance and SBOM
attestations for free via Sigstore's public infrastructure. The release
reusable builds a deterministic archive, an SPDX SBOM, `SHA256SUMS`, and attests
them. Because the build runs inside a **reusable workflow**, it qualifies for
**SLSA Build L3**. This is a public-repository entitlement: on private and
internal repositories Artifact Attestations require GitHub Enterprise Cloud,
and the private-free tier releases with `release-supply-chain-free.yml`
instead — see [02 Private free](02-private-free.md). Full detail:
[07 Supply chain](07-supply-chain-slsa-sbom-attestations.md).

## GHCR, Pages, and rulesets

- **GHCR public packages** are free and world-readable — see
  [09 Releases & packages](09-releases-packages.md).
- **GitHub Pages** is free for public repos — see
  [10 Deployments & environments](10-deployments-environments.md).
- **Rulesets** (branch/tag/push) are free and the canonical governance
  mechanism — see [08 Governance & rulesets](08-governance-rulesets.md).

---
Last verified: 2026-07-04
