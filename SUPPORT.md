# Support

`ci-workflows` is a library of reusable GitHub Actions workflows consumed
by full commit SHA. Here is where to get help.

## Read the docs first

- **[README](README.md)** — the capability tiers table (public / private-free),
  per-workflow usage snippets, and the common inputs
  (`upload_sarif`, `egress_policy`, `runner`).
- **Workflow header comments** — each reusable workflow documents its own inputs
  and behavior at the top of its file in `.github/workflows/`.
- **`docs/`** — deeper reference material and the workflow catalog, when present.

## Report a vulnerability — privately

**Do not open a public issue for security problems.** Report suspected
vulnerabilities through a private
[GitHub Security Advisory](https://github.com/NDDev-Technocracy-OpenNetwork/ci-workflows/security/advisories/new).
See [SECURITY.md](SECURITY.md) for the policy.

## Open an issue

Public issues are disabled in favor of structured forms. Pick the one that fits:

- **[Bug report](https://github.com/NDDev-Technocracy-OpenNetwork/ci-workflows/issues/new?template=bug_report.yml)**
  — a reusable workflow misbehaves (wrong result, unexpected failure, bad
  permission scope).
- **[Workflow request](https://github.com/NDDev-Technocracy-OpenNetwork/ci-workflows/issues/new?template=workflow_request.yml)**
  — propose a new reusable workflow or capability, and the tier(s) it targets.
- **[Tool update](https://github.com/NDDev-Technocracy-OpenNetwork/ci-workflows/issues/new?template=tool_update.yml)**
  — a pinned action or tool has a new version worth adopting.
- **[Docs gap](https://github.com/NDDev-Technocracy-OpenNetwork/ci-workflows/issues/new?template=docs_gap.yml)**
  — documentation is missing, wrong, or outdated.
- **[Security hardening](https://github.com/NDDev-Technocracy-OpenNetwork/ci-workflows/issues/new?template=security_hardening.yml)**
  — a defense-in-depth improvement (not a vulnerability report).

## Contributing

If you want to send a change, read [CONTRIBUTING.md](CONTRIBUTING.md) for the
non-negotiable requirements every workflow PR must satisfy (full-SHA pins,
least-privilege permissions, concurrency, timeouts, no template injection,
and explicit public/GHAS versus private-free boundaries) and the local checks
to run.

## Scope of support

This project is maintained by Danil Silantyev
([@rldyourmnd](https://github.com/rldyourmnd)), CEO NDDev, on a best-effort
basis. There is no SLA. Well-scoped, reproducible reports and PRs that follow
the contribution rules get attention fastest.
