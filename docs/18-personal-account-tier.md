# Personal-account tier — a private repo under a personal GitHub account

The other tier docs classify a repository by **visibility** (public / private)
and **plan** (free / GHAS). This one adds a third axis the generic model does
not surface: whether the repository is owned by an **organization** or by a
**personal account**. A personal-account repository is the *degenerate* case of
private-free — it inherits that posture in full, *and* it cannot reach an
organization's self-hosted runner fleet, so its runner strategy is different.

## Why this doc exists

Paid GitHub add-ons and Enterprise Cloud attach to an **organization** (or
enterprise), not to a person's namespace. [17 Paid organization
programmes](17-nddev-tier.md) is the opt-in guide for an organization that
independently holds those products. **A repository owned by a personal account
has none of that**, even if the same human owns both. So:

- a private personal-account repo has **no GHAS** → no CodeQL, no native secret
  scanning, no dependency review;
- a private personal-account repo has **no Enterprise Cloud** → no Artifact
  Attestations;
- a private personal-account repo is billed against the **2 000 min/month**
  personal Actions quota (catalog fact
  `github-actions-private-free-personal`), with no organization pool to spill
  into.

Functionally this is identical to [02 private-free](02-private-free.md). The
runner routing is the only part that differs, and it is the reason this doc
exists.

## The correction this tier exists to make

[02 private-free] now makes this choice explicit: GitHub-hosted consumes a
bounded private allowance and can incur overage; self-hosted guarantees zero
GitHub Actions compute meter. For a *personal-account* repository the allowance
is per-user, not per-organization, and there is no committer-pool to amortize it
across. **Route every job to a self-hosted runner registered on the repository
itself.**

| Concern | Generic private-free | Personal-account repo |
| --- | --- | --- |
| Posture (capabilities) | private-free | **private-free** (same) |
| CodeQL / secret scanning / dep review | excluded (paid) | **excluded** (no GHAS, same exclusions) |
| Artifact attestations | `release-supply-chain-free.yml` | **`release-supply-chain-free.yml`** (same) |
| Runner | Explicitly hosted-bounded or self-hosted | **self-hosted, repo-level registration** |

## Runner registration: org vs personal

A self-hosted runner registered on an **organization** is reachable only from
repositories *inside that organization*. A repository owned by a personal
account is in a different namespace and **cannot** consume an org-level runner
group, regardless of the group's `visibility` setting — `visibility=all` means
"all repos *in this org*", not "all repos everywhere". This is a platform gate,
not a configuration oversight; it cannot be relaxed by settings.

To serve a personal-account repository, register the runner **on the repository
itself**:

1. `gh api -X POST repos/{owner}/{repo}/actions/runners/registration-token` → a
   short-lived token (≈1 h). This endpoint is available on private repos with
   admin access; GitHub Pro is **not** required for repo-level runner
   registration.
2. On the runner host, run `./config.sh --url https://github.com/{owner}/{repo}
   --token <token> --labels "<alias>,linux,x64"` once per repository. A single
   runner process serves exactly one repository; N repositories need N
   separate runner install directories.
3. Install the systemd unit (`./svc.sh install <user> && ./svc.sh start`) so the
   listener survives reboots.
4. In the caller workflow, set `runs-on: [self-hosted, Linux, X64, <alias>]` and
   pass the same label as `runner:` to every reusable workflow call.

See [05 Runners → Routing by visibility](05-runners.md#routing-by-visibility)
for the load-bearing invariant: a self-hosted runner reachable from a public
repository is an RCE path, so this routing is for **private** personal-account
repos only. If the repository is or may become public, use the GitHub-hosted
caller in [examples/private-free/security.yml](../examples/private-free/security.yml).

## Recommended caller

[`examples/personal/security-selfhosted.yml`](../examples/personal/security-selfhosted.yml)
— the private-free stack (gitleaks, actionlint, zizmor-no-SARIF) with every job
routed to the repository's self-hosted runner. It is
`examples/private-free/security.yml` plus a `runner:` input on each call; the
capability set is identical because the tier posture is identical.

## Trigger caveat: `pull_request` only, not `push`

A reusable workflow owned by a **different account** (e.g. this library,
`NDDev-Technocracy-Sandbox/ci-workflows`, called from a personal-account repository) does
**not** resolve jobs for `push` events to the default branch. The run is
created but hangs in `pending` with zero jobs — indefinitely. Verified
empirically on a personal-account consumer: the same workflow file succeeded
instantly on `pull_request` while `push` runs to `main` hung for hours with no
jobs spawned. The mechanism is a
GitHub-side cross-owner workflow resolution gate, not a misconfiguration —
`allowed_actions: all` and full admin access do not unblock it.

**Use `pull_request` + `workflow_dispatch` as the only triggers.** Do not add
`push: branches: [main]`. Direct pushes to the default branch are gated by
branch protection requiring this CI to pass on the PR first, which is the
intended flow anyway. The example caller reflects this.

## What is deliberately not here

- **No CodeQL, no SARIF upload, no dependency review, no native secret
  scanning.** These are paid on private repos and a personal account has not
  bought them. The free substitutes (gitleaks, zizmor-no-SARIF, semgrep) are
  what this tier runs.
- **No Artifact Attestations on release.** They require GitHub Enterprise Cloud
  on private repos; use `release-supply-chain-free.yml` (SBOM + checksums, no
  provenance).
- **No cross-account runner sharing.** An org runner does not serve a
  personal-account repo; do not attempt to relax this with settings, it is a
  platform gate.
