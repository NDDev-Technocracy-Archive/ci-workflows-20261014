---
name: ci-consumer-adoption
description: Wire a repository onto the ci-workflows reusable library correctly — pick the tier from visibility and entitlements, pin to a released tag, route the runner, and close the two scanner settings no workflow file can reach. Invoke when adopting, migrating, or auditing a consumer repository, not when editing the library itself.
license: AGPL-3.0-or-later
compatibility: Codex and Agent Skills compatible; OpenCode discovers .agents/skills. Generate .claude/skills mirrors for Claude Code.
metadata:
  version: 1.2.0
  owner: NDDev
  status: proposed
  reviewed_at: '2026-09-07'
---

# Adopting ci-workflows in a consumer repository

This is the *caller* side. For work inside this library, use `AGENTS.md` and
`scripts/` in the library checkout.

Adoption is four decisions, in order. Getting them out of order is what produces
the two failure shapes seen in practice: a repository that looks configured but
still bills, and a repository that is routed to hardware its jobs cannot use.

## Start by resolving the mode

Do not choose a tier by reading prose. Resolve it.

The resolver lives in the library, not in your repository, so check the library
out first. Everything below runs in that checkout, not in yours:

```bash
# LIBRARY_TAG is the immutable SemVer release you verified and will pin.
# Resolve the programme from that same revision. Do not check out main.
LIBRARY_TAG="${LIBRARY_TAG:?set LIBRARY_TAG to the release tag you will pin}"
git clone --depth 1 --branch "$LIBRARY_TAG" \
    https://github.com/NDDev-Technocracy-OpenNetwork/ci-workflows.git /tmp/ci-workflows
cd /tmp/ci-workflows
LIBRARY_SHA=$(git rev-parse HEAD)
# Write $LIBRARY_SHA in every consumer `uses: ...@<sha>` line.

# The library runs every Python tool through one launcher, which needs its own
# environment. A bare `python3 scripts/...` aborts with ModuleNotFoundError.
python3.13 -I -B -m venv --copies .venv
uv pip install --python .venv/bin/python --require-hashes -r requirements-ci.txt

# Pass THIS consumer organization's live plan. Do not copy another
# organization's observed plan. Public no-addon shapes keep CodeQL and
# attestations on current GitHub plans whether the org is Free or Team:
.venv/bin/python -I -B scripts/check_python_execution_contract.py --launch resolve_profile.py -- --visibility public --plan free
.venv/bin/python -I -B scripts/check_python_execution_contract.py --launch resolve_profile.py -- --visibility public --plan team
# Private attestations need --plan enterprise-cloud. Add-on flags are separate:
# --code-security, --secret-protection, --code-quality.
```

It returns the matching profile, its controls (CodeQL mode, runner class,
enforcement, release provenance), the fixed and metered cost lines, and the
capability/workflow set split into run / conditional / unavailable, with the
free substitute for everything the mode does not entitle. Adopt that set; the
sections below are how to wire it correctly.

## 1. Tier — from visibility *and* entitlements, never visibility alone

Pick the tier doc first; it decides which reusables are even legal to call:

| Situation | Doc |
| --- | --- |
| public repository | `docs/01-public-oss-free.md` |
| private, no paid security products | `docs/02-private-free.md` |
| private, Advanced Security **selected** | `docs/03-private-paid-ghas.md` |
| opt-in paid organization programmes | `docs/17-nddev-tier.md` |
| Code Quality (orthogonal to all of the above) | `docs/16-code-quality.md` |

The publisher is a GitHub Organization, not an Enterprise account, and this
library does not assume it purchased Code Security, Secret Protection, Code
Quality, or Enterprise Cloud. A live GitHub plan belongs to one organization;
do not copy one account's plan onto another.

Public repositories keep CodeQL, native secret scanning, dependency review and
artifact attestations on current GitHub plans without those add-ons. Code
Security, Secret Protection and Code Quality are independent purchases; none
of them unlocks private Artifact Attestations. That is an Enterprise Cloud
**plan** gate. Following private-free on a private repository without
Enterprise Cloud is correct even if Code Security is held. Following
private-free on Enterprise Cloud drops attested `release-supply-chain.yml`
even if no add-on is held. Check entitlements and the plan gate separately.
Prices and quotas live in `catalog/product-facts.yml`; never quote them from
memory or from a skill.

## 2. Pin — to a released tag, by full SHA

Reference reusables as `NDDev-Technocracy-OpenNetwork/ci-workflows/.github/workflows/<file>@<40-hex>`
with a trailing comment naming the release. Pin to a commit that a release tag
points at, not to whatever `main` happens to be: tags in this library are
immutable and protected, `main` is not, and an estate pinned to arbitrary commits
cannot answer "what version are we on".

Audit for drift across an estate by collecting the unique pinned SHAs per
repository. More than one distinct pin inside a single repository is always a
defect; several distinct pins across an estate means upgrades have been landing
per repository rather than per release.

The library was renamed once. A reference to the old name still resolves through
GitHub's rename redirect, which is not a dependency worth keeping — if the
redirect lapses or the old name is reclaimed, every caller stops resolving.
Repoint rather than rely on it.

## 3. Runner — by visibility, and it is not one setting

Minutes are free and unlimited on public repositories and metered on private
ones, so the cost-optimal routing is the inverse of "use our own hardware
everywhere":

- **public → GitHub-hosted.** A self-hosted runner reachable from a public
  repository is a remote-code-execution path: a forked pull request runs
  attacker-controlled code on your machine. This is a security defect, not a
  saving. Never ship a self-hosted label as a default in an example.
- **private → self-hosted label**, passed by the caller through the `runner`
  input.

Private callers supply their own labels. Example class names used by some
NDDev private repositories (`nddev-linux-fast` / `-standard` / `-integration`
/ `-untrusted` / `-release`) are caller-owned; this library does not publish a
live fleet inventory. Do not put a private checkout on a checkout-free class,
Docker work on a class without a container runtime, or untrusted code on a
credentialed class.

Then close the two settings that **no workflow file can reach**, and only when
those products are actually enabled, because GitHub schedules them itself:

| Scan | Where the runner is chosen |
| --- | --- |
| CodeQL *default setup* | `PATCH /repos/{owner}/{repo}/code-scanning/default-setup` with `runner_type: labeled` |
| Code Quality | `PATCH /repos/{owner}/{repo}/code-quality/setup` with `runner_type: labeled`, `runner_label` — or repository settings → Code quality → *Labeled runner* |

Miss either **on a repository that has those products enabled** and it keeps
consuming metered minutes while every caller in the tree claims otherwise. Do
not enable or route them on a repository that has not purchased them. Full
mechanics: `docs/05-runners.md#visibility-routing`.

There is **no** automatic spillover from a self-hosted label to a hosted runner.
A job whose label is busy queues until a runner frees. Size the fleet so
queueing is rare; a "fallback" to hosted runners on a private repository would
silently reintroduce the metered minutes you just moved off.

## 4. Prove it on the runner you actually chose

A saved setting is not evidence. Two failure classes only appear on a real run:

- **Privilege.** Reusables that install a pinned tool must write somewhere the
  job user owns. A correctly isolated self-hosted runner is unprivileged, so a
  step that installs into a system path fails there while passing on hosted
  runners. If you hit this, fix the workflow's destination — granting the runner
  account write access to system paths trades isolation for convenience and
  makes every subsequent job share mutable state.
- **Toolchain.** Hosted images ship a large preinstalled toolchain; a
  self-hosted host ships whatever you put on it.

Verify by reading the job's `runner_name` and `runner_group_name` from the
completed run, not by re-reading the setting you just wrote.

Retry only an idempotent transient boundary, at most twice after the first
failure (three total attempts), and preserve one structured log record per
attempt. Do not retry deterministic tests, policy failures, missing tools,
permission failures, or failed deploy health checks. Fleet scheduling already
reconciles placement; workflow-level retries must not duplicate an active job.

## Cost controls that belong to the consumer, not the library

- **Code Quality AI findings are metered separately from the licence, with no
  included allowance.** Leave them off unless the credit burn has been sized for
  that specific repository. A product budget cannot fence them off, because the
  budget must leave headroom for the licence accruing under the same SKU — the
  per-repository switch is the only real control. Where CodeQL finds no
  supported language the switch is absent entirely, which is a stronger
  guarantee than "off" and is not a gap to fix.
- **Artifact retention drives storage spend and is not blocked by spend budgets.**
  Keep it short and purge accumulated artifacts; budgets stop compute, not
  storage.

## Checklist

1. Tier chosen from visibility **and** verified entitlements.
2. Every reusable reference pinned by full SHA to a released tag; one pin per repository.
3. No reference to the pre-rename library name.
4. `runner` input set on private callers; absent on public ones.
5. Managed CodeQL default setup and Code Quality routed **only** when those products are enabled.
6. A completed run inspected for `runner_name`, not just a saved setting.
7. AI findings off unless deliberately sized.
8. Release caller matches the plan gate: attested on public, and on private/internal only with Enterprise Cloud. Add-ons do not unlock private attestations.
9. Transient retries are idempotent, logged, and capped at three attempts.
