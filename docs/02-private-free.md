# Private without paid security add-ons

Private repositories can run a meaningful security and supply-chain stack
without paid GitHub security add-ons. That does **not** automatically make the
runner bill zero: standard GitHub-hosted minutes have a finite allowance on
private repositories and overage may be billed. This document separates the
feature posture from the compute route so neither promise is implied by the
other.

## Paid on private → excluded from this tier

These are free on public repositories but **require paid GitHub Advanced
Security** (or are otherwise billable) on private repositories. Do **not** enable
them in the private-free tier:

| Feature | Why excluded here | Where it lives |
| --- | --- | --- |
| CodeQL code scanning | Paid (GHAS) on private | [03 GHAS](03-private-paid-ghas.md) |
| Native secret scanning + push protection | Paid (Secret Protection) on private | [03 GHAS](03-private-paid-ghas.md) |
| Dependency review action | Paid (GHAS) on private | [03 GHAS](03-private-paid-ghas.md) |
| `step-security/harden-runner` | Paid on private repos | use a private-free workflow with no action reference |
| SARIF upload to code scanning | Requires code scanning (paid) | use the no-SARIF workflow variant |
| GitHub Artifact Attestations | Require GitHub Enterprise Cloud on private/internal (a plan gate — GHAS does not unlock it) | use `release-supply-chain-free.yml` (below) |
| GitHub Code Quality | Billed per active committer on a licence GHAS does not include; the public rate is disputed | [16 Code Quality tier](16-code-quality.md) |

> Code Quality is the one exclusion here that publishing the repository may
> **not** solve — and the only one whose cost this library cannot state.
> GitHub's product page says public repositories are $0 per committer; its
> billing documentation grants no visibility exemption. Until that is settled
> for the account, do not assume publishing makes it free
> ([the dispute](16-code-quality.md#public-rate-dispute)). Either way, keeping
> this tier free means leaving the org-level **Repository access** control off
> this repository — the free maintainability substitutes are listed in
> [16 Code Quality tier](16-code-quality.md#what-the-free-tiers-do-instead).

## The no-paid-add-on private stack

| Capability | Workflow | Notes |
| --- | --- | --- |
| Workflow YAML lint | `actionlint.yml` | contains no paid action |
| Actions static analysis | `zizmor-no-sarif.yml` | fails on findings, no upload |
| Secret scanning (history-aware) | `secret-scan.yml` | gitleaks; contains no paid action |
| Static validation | `private-static.yml` | single Ubuntu job, no matrix/cache |
| Release supply chain (SBOM, checksums, no attestations) | `release-supply-chain-free.yml` | attestations require GHEC on private |
| Cross-platform smoke | `cross-platform-smoke.yml` | OS matrix |
| Cloud auth | OIDC | short-lived credentials, no stored secrets |

### Choose the compute contract first

- `private-self-hosted` compute — pass a private self-hosted label to every
  reusable workflow and separately route managed CodeQL/Code Quality scans.
  GitHub does not meter runner minutes, but the fleet still has infrastructure
  and operations cost.
- `private-hosted-bounded` compute — pass a standard GitHub-hosted label. This uses the
  plan allowance and can incur overage; Windows and macOS consume it faster.
  Set an Actions budget with **Stop paid usage** enabled. Included-usage alerts
  are notifications, not a spending boundary.

The resolver defaults private/internal repositories to the first, fail-closed
mode. The hosted choice requires an explicit flag:

```bash
.venv/bin/python -I -B scripts/check_python_execution_contract.py --launch resolve_profile.py -- --visibility private --plan free
.venv/bin/python -I -B scripts/check_python_execution_contract.py --launch resolve_profile.py -- --visibility private --plan free \
  --private-compute github-hosted
```

### Bounded-quota GitHub-hosted caller

```yaml
# .github/workflows/security.yml  (private, no paid add-ons; hosted quota)
name: security
on:
  push: { branches: [main] }
  pull_request: { branches: [main] }
permissions: {}
jobs:
  secret-scan:
    permissions: { contents: read }
    uses: NDDev-Technocracy-Archive/ci-workflows-20261014/.github/workflows/secret-scan.yml@<full-sha>
  actionlint:
    permissions: { contents: read }
    uses: NDDev-Technocracy-Archive/ci-workflows-20261014/.github/workflows/actionlint.yml@<full-sha>
  zizmor:
    permissions: { contents: read }
    uses: NDDev-Technocracy-Archive/ci-workflows-20261014/.github/workflows/zizmor-no-sarif.yml@<full-sha>
  validate:
    permissions: { contents: read }
    uses: NDDev-Technocracy-Archive/ci-workflows-20261014/.github/workflows/private-static.yml@<full-sha>
    with:
      command: "python3 scripts/validate_all.py"
```

> `zizmor-no-sarif.yml` still **fails the job** when zizmor reports findings at
> or above `min_severity`; it just skips the (paid) SARIF upload. The
> public/GHAS tier uses `zizmor-sarif.yml` instead. See
> [06 Security scanning](06-security-scanning.md#zizmor).

For a zero-GitHub-meter caller, use
[`examples/private-free/security-selfhosted.yml`](../examples/private-free/security-selfhosted.yml)
and replace its placeholder runner label with a private, isolated fleet label.

## Consolidated private-fleet security

`private-security-bundle-free.yml` runs actionlint, zizmor, OSV-Scanner and
Gitleaks in one private Linux placement. All four gates execute before the job
aggregates failure. After scanning runs, the job uploads one-day evidence containing the
actionlint log plus Zizmor, OSV and fully redacted Gitleaks SARIF reports; it
does not require or attempt a paid code-scanning upload.

When migrating an existing caller from `nddev-security-bundle.yml`, preserve
its caller job key/name and inspect the exact required check context. The free
workflow defaults its inner job name to
`actionlint + zizmor + OSV + gitleaks (no SARIF)`. Set the optional `check_name`
to `actionlint + zizmor + OSV + gitleaks` when that is the existing protected
inner name. This input affects only the displayed check name; it does not alter
scanner execution, failure handling, artifact evidence or token permissions.
The caller needs only `contents: read`; no Code Security feature or
`security-events: write` permission is required by this variant.

Callers whose repository declares git submodules get one extra behaviour:
the caller checkout never touches submodule worktrees, and a reused
self-hosted worktree keeps whatever revision an earlier job left behind —
a recursive scan would otherwise read yesterday's lockfile as this commit's.
Pass `submodule_deploy_keys` (one `path base64-ssh-private-key` line per
declared path) and each submodule is checked out at its recorded gitlink
and verified before the gates run; omit it and stale submodule worktrees
are emptied instead, so the scan still describes the commit it ran on. A
declared path without a matching key fails the job — a partial view is a
wrong scan.

Use this workflow when placement latency and fleet contention cost more than
the limited failure-domain isolation of four separate jobs. Public fork code
must remain on GitHub-hosted runners and must never call this private lane.

## actionlint (`actionlint.yml`)

Lints workflow YAML for syntax errors, expression typos, and deprecated usage.
The binary is downloaded pinned and **checksum-verified** (`actionlint_sha256`).
Free everywhere. Its reusable workflow contains no Harden-Runner reference.
Linux X64 runners only: the workflow installs the linux_amd64 binary, and a
first-step guard rejects any other platform before the download.

## gitleaks (`secret-scan.yml`)

History-aware secret detection using a **digest-pinned** gitleaks container by
default (`detect --redact --exit-code 1` over full git history via
`fetch-depth: 0`). Linux X64 workers without Docker may opt into
`execution_mode: binary`; the only accepted release tuple is pinned by exact
asset size and SHA-256 and is extracted and version-checked fail-closed.
Gitleaks is free on public and private. It is the private-tier substitute for
native secret scanning, which is paid.

## private-static (`private-static.yml`)

A deliberately minimal single Ubuntu job with **no matrix, no cache, no
artifacts** — the low-consumption validation lane. It runs a caller-provided `command`
(required input) with optional Python setup.

```yaml
jobs:
  validate:
    permissions: { contents: read }
    uses: NDDev-Technocracy-Archive/ci-workflows-20261014/.github/workflows/private-static.yml@<full-sha>
    with:
      command: "ruff check . && pyright ."
      timeout_minutes: 10
```

### `checkout_ref` and privileged events

`private-static.yml` and `cross-platform-smoke.yml` are the only two reusables
that let the caller choose the checked-out commit via `checkout_ref`. Both open
with a **fail-closed guard**: because a reusable workflow inherits the caller's
`github` context, the workflow can see the event that triggered the *calling*
run, and it refuses to start when a privileged event —
`pull_request_target`, `workflow_run`, `issue_comment`, `issues`, `discussion`,
`discussion_comment` — supplies a non-empty `checkout_ref`.

That combination would run caller-chosen code with the base repository's write
token and secrets. Pass `checkout_ref` from a `pull_request` wrapper instead,
where the token is read-only. A privileged caller that supplies no
`checkout_ref` is unaffected — checking out the base repository stays
legitimate. There is no opt-out input; the guard is enforced by
`scripts/check_privileged_ref_guard.py`, which executes it against the full
event matrix on every gate run.

## Release supply chain without attestations (`release-supply-chain-free.yml`)

GitHub Artifact Attestations are **not available to private or internal
repositories on the Free, Pro, or Team plan** — they require GitHub Enterprise
Cloud. That is a plan gate, not a GHAS gate, so buying Code Security does not
unlock it either. `release-supply-chain.yml` would fail at its unconditional
attestation steps on such repositories, so the private-free tier calls
`release-supply-chain-free.yml`: the same deterministic tracked-source archive,
exact-payload SPDX SBOM, canonical release notes, manifest, and `SHA256SUMS`,
published as one immutable release — with no attestation actions and only
`contents: write`. The manifest records `slsa_build_level: null` because no
provenance is generated. Copy-paste caller:
[`examples/release/private-free-release.yml`](../examples/release/private-free-release.yml).
Details: [07 Supply chain](07-supply-chain-slsa-sbom-attestations.md).

## OIDC instead of stored secrets

Use OIDC to obtain short-lived cloud credentials (AWS/GCP/Azure) at run time
instead of storing long-lived keys as Actions secrets. This is free and is the
recommended posture for private repos — see
[10 Deployments & environments](10-deployments-environments.md#cloud-oidc).

## Cross-platform smoke (`cross-platform-smoke.yml`)

Runs a caller command across an OS matrix. On private repos, **Windows and macOS
minutes are billed at higher multipliers than Linux** (see
[05 Runners](05-runners.md#cost-model)). Trim `os_list` to `["ubuntu-latest"]`
to conserve quota, or accept the multiplier cost intentionally. Use
`linux_command`, `macos_command`, or `windows_command` when one platform needs a
lighter smoke than the default `command`.

`install_command` runs on every OS before the smoke command, so a caller whose
smoke needs dependencies does not have to prefix the same install onto each
per-OS override — and a broken install is reported as an install failure rather
than as a smoke failure. It is deliberately not a per-OS input: a caller that
needs genuinely different install steps per platform should branch inside the
command it passes.

It carries the same `checkout_ref` privileged-event guard as `private-static.yml`
(see [`checkout_ref` and privileged events](#checkout_ref-and-privileged-events)).

## Self-hosted caveats

Self-hosted runners avoid GitHub minute billing but move trust and cost onto
your own infrastructure. **Never** attach self-hosted runners to a public repo
(forked PRs can execute arbitrary code on them). For private repos, isolate,
auto-scale (e.g. Actions Runner Controller), and treat egress carefully — see
[05 Runners](05-runners.md#self-hosted).

---
Last verified: 2026-08-03
