# Contributing

Thanks for helping harden the NDDev CI/CD supply chain. This repository ships
**reusable GitHub Actions workflows** consumed across the estate by full commit
SHA. A workflow here is a security-critical dependency for every caller, so the
bar for changes is deliberately high.

By contributing you agree to the [Code of Conduct](CODE_OF_CONDUCT.md) and to
license your contribution under [AGPL-3.0-or-later](LICENSE).

- **Security vulnerabilities are never contributions.** Do not open a public
  issue or PR. Report privately via GitHub Security Advisories — see
  [SECURITY.md](SECURITY.md).

## Ways to contribute

- **Propose a new reusable workflow or capability** — open a
  [Workflow request](https://github.com/NDDev-Technocracy-Archive/ci-workflows-20261014/issues/new?template=workflow_request.yml)
  first. Describe the capability, which tier(s) it targets
  (public / private-free / private-paid), the underlying tools, the motivation,
  and the security considerations. Agreeing on scope and tier before code avoids
  wasted work.
- **Report a bug** in an existing workflow — use the
  [Bug report](https://github.com/NDDev-Technocracy-Archive/ci-workflows-20261014/issues/new?template=bug_report.yml)
  form.
- **Flag a pinned tool/action update** — use the
  [Tool update](https://github.com/NDDev-Technocracy-Archive/ci-workflows-20261014/issues/new?template=tool_update.yml)
  form (Dependabot already batches most of these).
- **Propose a hardening improvement** — use the
  [Security hardening](https://github.com/NDDev-Technocracy-Archive/ci-workflows-20261014/issues/new?template=security_hardening.yml)
  form. This is for defense-in-depth ideas, not vulnerabilities.
- **Fix a docs gap** — use the
  [Docs gap](https://github.com/NDDev-Technocracy-Archive/ci-workflows-20261014/issues/new?template=docs_gap.yml)
  form.

## Non-negotiables for every workflow PR

These are enforced by review plus the focused local checks below (`validate_all`,
`actionlint`, and `zizmor`). `ci-gate` still runs them remotely as truthful
advisory evidence; ordinary merge does not wait on that remote context.

1. **Full-SHA action pins with version comments.** Every `uses:` of a
   third-party action pins a 40-character commit SHA followed by a version
   comment, e.g.:

   ```yaml
   uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0  # v7.0.0
   ```

   No tags, no branches, no floating major versions. Dependabot bumps the SHA.
2. **Least-privilege `permissions`.** Start from `permissions: {}` at the top
   level and grant only the exact scopes a job needs (e.g.
   `contents: read`, `security-events: write`). Never rely on the default token
   scope.
3. **Lossless `concurrency`** on every workflow. Use a run-unique group such as
   `group: <workflow>-${{ github.run_id }}` with `cancel-in-progress: false` so
   a newer commit cannot replace either a queued or a running accepted run.
4. **`timeout-minutes`** on every job. No unbounded jobs.
5. **`persist-credentials: false`** on every read-only `actions/checkout`.
   Only opt in to credential persistence when a later step provably needs to
   push.
6. **No template injection.** Never interpolate `${{ inputs.* }}`,
   `${{ github.event.* }}`, or any other expression directly inside a `run:`
   block. Pass untrusted values through `env:` and reference them as shell
   variables:

   ```yaml
   - env:
       VERSION: ${{ inputs.version }}
     run: |
       set -euo pipefail
       echo "building $VERSION"
       bash -c 'do-something "$VERSION"'
   ```

   `zizmor` will flag `template-injection`; treat that as a hard failure.
7. **Separate paid and private-free contracts.** A workflow available to the
   private-free tier must not reference a paid action or GitHub feature. Split
   SARIF/no-SARIF workflows and public/GHAS runtime hardening at the file
   boundary; never try to disable an action with lifecycle hooks through a
   step-level boolean condition.
8. **Reusable contract.** Every workflow intended for callers must declare
   `on: workflow_call:` and document its inputs in a header comment. The self-CI
   contract job verifies this.
9. **Harden public/GHAS jobs explicitly.** Use
   `step-security/harden-runner` as the unconditional first step with an egress
   policy (`audit` by default, `block` where the endpoint set is known). Do not
   reference it from cross-tier or private-free workflows.

## Local checks

Run these before opening a PR (they are the focused checks `ci-gate` also runs):

```bash
# Lint all workflow YAML
actionlint

# Workflow security analysis. Pedantic is what ci-gate runs, and the token is
# not optional: without it zizmor skips its online audits and reports a false
# "No findings".
GH_TOKEN=$(gh auth token) uvx zizmor@1.26.1 --persona pedantic --min-severity low .github/workflows

# Complete repository contract, catalog, example, and generated-doc checks
.venv/bin/python -I -B scripts/check_python_syntax.py
.venv/bin/python -I -B scripts/check_python_execution_contract.py --launch validate_all.py --
```

Install validator dependencies with the hash-locked file:

```bash
python3.13 -I -B -m venv --copies .venv
uv pip install --python .venv/bin/python --require-hashes -r requirements-ci.txt
```

Install the tools locally with:

```bash
# actionlint — pinned version, checksum-verified, matching actionlint.yml's
# `actionlint_version` default. Read the pin from the workflow if they disagree.
curl -fsSL -o /tmp/actionlint.tar.gz \
  https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz
echo "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8  /tmp/actionlint.tar.gz" | sha256sum -c -
tar -xzf /tmp/actionlint.tar.gz -C ~/.local/bin actionlint

# zizmor — run the version and persona CI runs, not whatever is on PATH. The pin is
# zizmor-sarif.yml's `zizmor_version` default.
GH_TOKEN=$(gh auth token) uvx zizmor@1.26.1 --persona pedantic --min-severity low .github/workflows
```

Every command here uses `uv` or a checksum-verified download on purpose. The
estate policy forbids `pip`, `pipx`, `npm`/`npx`, and mutable version resolution
such as `go install ...@latest`; CI itself installs with `uv pip install
--system --require-hashes`. A setup guide that told contributors to use a
forbidden installer, while CI used a different one, is how three descriptions of
one command ended up disagreeing.

## Commits and pull requests

- **Conventional Commits.** Subject lines follow
  `type(scope): summary` (e.g. `feat(workflows): add sbom diff reusable`,
  `fix(zizmor): pin analysis to full sha`). Keep the subject under 100
  characters.
- **Sign off every commit (DCO).** Add a `Signed-off-by:` trailer with
  `git commit -s`, certifying the
  [Developer Certificate of Origin](https://developercertificate.org/).
- **Sign your commits.** `main` requires cryptographically signed commits
  (`git commit -S`, or `git config commit.gpgsign true`). Unsigned commits are
  rejected.
- **Sole authorship.** Do not add `Co-Authored-By:` trailers.
- Fill in the [pull request template](.github/PULL_REQUEST_TEMPLATE.md)
  completely, including the permissions diff and threat-model note when a change
  touches workflow behavior or token scopes.
- Update the README capability table (and `docs/` / `catalog/` where present)
  and add a `CHANGELOG.md` entry under `[Unreleased]`.

## Branch protection and CI

`main` is protected: signed commits, no force-push or deletion, merge commits
only in the source ruleset, and no required general CI status check. `ci-gate`
(core + touched validators, actionlint, zizmor, negative gates) stays truthful
advisory evidence. Background workflows must stay truthful; they are not merge
blockers. All workflow files are owned
by [@rldyourmnd](https://github.com/rldyourmnd) via
[CODEOWNERS](.github/CODEOWNERS). Open PRs against `main` from a topic branch.

## Releases

Releases are tag-driven (`MAJOR.MINOR.PATCH`) and publish an SPDX SBOM,
`SHA256SUMS`, and SLSA build-provenance attestations. Contributors do not tag
releases; the maintainer cuts them.
