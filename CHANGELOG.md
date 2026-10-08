# Changelog

This file is a release ledger: every heading below is a real release, and
`scripts/check_release_ledger.py` enforces that in both directions.

The project follows Semantic Versioning.

## [Unreleased]

- **Harden `private-security-bundle-free` submodule sync.** A symlink planted
  at a declared submodule path on a reused runner would have redirected the
  no-keys cleanup (`rm -rf "$path"/*` dereferences a symlinked directory)
  and a keyed `submodule update` alike — links are now unlinked outright
  rather than followed, and the temporary deploy-key directory is removed
  by an EXIT trap instead of persisting until the job ends.
- **Fix `private-security-bundle-free` scanning stale submodule worktrees.**
  On a reused self-hosted worktree the caller checkout leaves previously
  cloned submodules untouched (`git clean -ffdx` does not remove an
  initialized submodule — its path is a tracked gitlink), so the recursive
  gates read whatever revision an earlier job left behind. A new optional
  `submodule_deploy_keys` secret carries `path base64-ssh-private-key` pairs;
  when supplied, every declared submodule is checked out at its recorded
  gitlink and verified before scanning, and when absent stale submodule
  worktrees are emptied so leftovers can never report as this commit's
  content.
- Give both private security bundles a fresh implementation checkout for each
  invocation, preserving stale runner files. Materialize the selected Git
  history before Gitleaks and fail when any required object is unavailable.
- **Bump urllib3 to 2.8.0 in `requirements-qt.txt`** (recompiled via
  `uv pip compile --upgrade-package urllib3`): the osv-scan runtime fixture
  flagged PYSEC-2026-4176 and PYSEC-2026-4177 on the pinned 2.7.0.
- **Fix the dependabot-catalog-convergence update-branch guard.** A bound
  pull request already current with the base made the API answer
  `no new commits`, which matched neither the success pattern nor a real
  error — the job exited before converging the catalog and left
  dependabot bumps red on `tool-registry`/`catalog`.

## [0.1.29] - 2026-09-28

- **Publishable re-cut of `0.1.28`.** Tag `0.1.28` was pushed as an unsigned
  lightweight ref carrying no `nddev-public-release-promotion/v2` record, so
  the promotion gate correctly refused it — and release tags are immutable,
  so it cannot be updated in place. This tag is the signed annotated
  promotion-record tag the content always needed. Tree content is identical
  to `0.1.28`; no GitHub release exists for `0.1.27` or `0.1.28`.

## [0.1.28] - 2026-09-28

- **Seven product facts re-verified after the `0.1.27` preflight stop.** The
  `product-facts-calendar` release gate correctly refused tag `0.1.27`:
  `appveyor-public`, `semaphore-cloud-free`, `semaphore-community`,
  `azure-pipelines-private`, `bitbucket-free`, `buildkite-personal` and
  `ubicloud-free` had all passed their `expires_after`. Each was re-read
  against its `source_urls` on 2026-09-28 and carries a staggered new
  `expires_after`. Three records needed correction, not just a date bump:
  Buildkite's plan is now named Free with 2,000 Linux vCPU minutes/month
  (was Personal/500), up to 10 concurrent jobs (was 3), up to 5 users
  (was 1) and 30-day retention (was 90); Semaphore's community edition moved
  from `/community` (404) to `/open-source` and is branded Semaphore Open
  Source; Ubicloud's credit statement was reconfirmed. No GitHub release
  exists for tag `0.1.27` — publication was stopped before the publish job.

## [0.1.27] - 2026-09-28

- **`rust-supply-chain` tool defaults refreshed.** `cargo_audit_version`
  now defaults to 0.22.2 and `cargo_machete_version` to 0.9.2, the current
  upstream releases. Both remain caller-overridable inputs; only the
  default moved.

## [0.1.26] - 2026-09-21

- **`dependabot-catalog-convergence` repaired.** The job now builds the
  trusted tool environment and launches `sync_action_catalog.py` through the
  execution-contract launcher. The bare `python3 -I` invocation could not
  resolve the `ci_workflows_tools` verified-file-spec package, so every
  convergence run since the sibling-import migration failed with
  `ModuleNotFoundError`. The workflow is registered in the catalog `used_by`
  for `setup-python` and `setup-uv` like every other consumer.
- **Catalog review record.** `setup-rust-toolchain` v2.0.0 review outcome is
  now persisted in `catalog/tools.yml`: the major bump stays held because the
  implicit `RUSTFLAGS=-D warnings` default becomes `build.warnings` (cargo
  >= 1.97), which would silently weaken the gate for callers on older
  toolchains. The Dependabot PR remains the tracking surface.

## [0.1.25] - 2026-09-21

- **Refuse `./action` refs inside `workflow_call` workflows.** `./` in a
  called workflow resolves against the caller's workspace, never this
  repository's, so `uses: ./actions/x` in a reusable fails at job setup for
  every cross-repository consumer. `check_pinned_actions.py` now rejects the
  pattern; the affected workflows were repaired in 0.1.24.
- **Scheduled tool refresh, 2026-09-21.** Bumped twelve action pins and three
  CLI pins to their current upstream releases: codeql-action v4.38.1,
  setup-android v4.0.4, setup-r v2.14.0, codecov-action v7.1.1, typos
  v1.50.2, checkov-action v12.3125.0, github-action-benchmark v1.22.2,
  setup-uv v10.1.0, setup-java v6.0.1, setup-buildx-action v4.4.1,
  build-push-action v7.4.0, install-action v2.87.17; osv-scanner 2.6.0,
  semgrep 1.177.0 and syft 1.52.0 with re-verified release checksums.
  `setup-rust-toolchain` stays on v1.17.0 (upstream v2.0.0 is a major bump
  pending input-contract review) and `zizmor` stays on 1.26.1 (upstream
  v1.30.1 reports 65 new low findings on this tree, held for dedicated
  triage). Every catalog `last_verified` restamped to the audit date.

## [0.1.24] - 2026-09-20

- **Fix a reusable workflow that could not reach its own vendored actions.**
  `./actions/...` in a called workflow resolves against the *caller's*
  workspace, never this repository. Five `uses:` were written that way while
  vendoring, and each one fails at job setup for every cross-repository
  caller:
  - `ci-feedback.yml` has used `./actions/ci-feedback` since 0.1.21. It runs
    no checkout, so the workspace is empty and the step cannot resolve. The
    job only fires on a failed conclusion, so it stayed hidden until
    2026-09-20, when `github-device-sync` run 35541640707 reported
    `Can't find 'action.yml' ... under
    /home/runner/work/github-device-sync/github-device-sync/actions/ci-feedback`.
    Every reusable caller of the CI-feedback path has been silently unable to
    publish evidence for three releases.
  - `private-security-bundle-free.yml` gained four `./actions/tool-cache`
    references in 0.1.22/0.1.23, replacing the fully-qualified
    `NDDev-Archive/github-actions-garm/actions/tool-cache@468af475` that
    worked. It *does* check out the caller, so the path resolved into the
    caller's tree: `setup-systems` run 35541466805 failed with
    `Can't find 'action.yml' ... under .../setup-systems/setup-systems/actions/tool-cache`.

  All five now name the repository explicitly and pin it:
  `NDDev-OpenNetwork/ci-workflows/actions/<name>@96215b32`. That commit is
  0.1.23, which is where both actions already live, so the pin is real and
  immutable. Vendoring the action was correct; addressing it with `./` was
  not, and the distinction is that a reusable workflow has no path to its own
  repository unless it names it.

## [0.1.23] - 2026-09-20

- `tool-cache`: vendor the composite action into `actions/tool-cache/`
  (byte-identical to `NDDev-Archive/github-actions-garm` `468af475`) and
  repoint the four `uses:` in `private-security-bundle-free.yml` to the
  same-repository path. An archived dependency cannot receive fixes; the live
  copy now lives beside the workflow that runs it, matching the `ci-feedback`
  vendoring in 0.1.21.
- Examples and docs: the retired GARM label classes (`nddev-linux-standard`,
  `nddev-linux-untrusted`, `nddev-linux-integration`) are replaced by the live
  `nddev-linux` label in `docs/03`, `examples/nddev/security-private-selfhosted.yml`,
  and the `deny_runner`/`docker-build` comments; ADR-0004 records the cutover
  as a dated amendment.

## [0.1.21] - 2026-09-20

- `ci-feedback`: vendor the reusable workflow and its composite action into
  this repository. Their home `NDDev-OpenNetwork/github-actions` moved to
  `NDDev-Archive/github-actions-garm` on 2026-09-17, and an archived
  repository cannot serve a `workflow_call`, so every caller of
  `ci-feedback-events.yml` failed at resolution. The workflow now lives at
  `.github/workflows/ci-feedback.yml` and the action at `actions/ci-feedback/`;
  callers reference the same-repository path. Other repositories' own
  `ci-feedback-events.yml` files still point at the archived path and need the
  same repoint. `tool-cache` action references are repointed at the moved
  repository (same pinned commit).

## [0.1.20] - 2026-09-19

- Re-verify three expired product facts (`circleci-free`,
  `circleci-open-source`, `harness-free`) against their recorded
  `source_urls`; all claims still hold verbatim. Carries the
  `rust-supply-chain` toolchain fix unchanged from the 0.1.19 tag —
  that tag exists but its release was never published because release
  preflight declined on the expired facts; pin `0.1.20`.

## [0.1.19] - 2026-09-19

- `rust-supply-chain`: the `audit` and `machete` jobs now install the Rust
  toolchain named by the existing `toolchain` input before invoking `cargo`.
  Both previously ran `cargo <tool>` with no setup step, so they depended on a
  toolchain being ambient on the runner image — true for `ubuntu-latest` and
  some self-hosted slots, false for others, where the job failed with
  `cargo: command not found`. Caller interface unchanged.

## [0.1.18] - 2026-09-18

- Align the NDDev runner-routing example and `fleet_contract` with official
  `nddev-linux` slots (`github-actions-light`). The previous GARM/Incus
  container contract is no longer the public current contract.

## [0.1.17] - 2026-09-15

- Re-verify GitLab Free and Open Source allowances against current primary
  sources, with separate review deadlines and explicit eligibility conditions.

- Preserve complete redacted private security evidence in a bounded, checksummed
  run-log ZIP when artifact upload fails. Scanner enforcement and failed fallback
  remain blocking; no new token permission or external storage is required.

- Add optional `check_name` to the private-free security bundle so callers can
  retain an existing required check identity when migrating away from SARIF
  publication, with all four scanners and evidence artifacts preserved.

- Synchronize pins by catalog action family, preserving independent subpath actions
  and reusable workflows in the same repository. Apply the reviewed dependency
  updates from #92 with matching catalog and transitive-image records; historical
  evidence digests are no longer rewritten by an unrelated action update.

- Stop treating the publisher as an Enterprise Cloud buyer of Code Security,
  Secret Protection and Code Quality. Paid programmes stay explicitly
  selectable; public CodeQL, SARIF, Scorecard and attestations stay. Private
  repositories without those purchases use the private-free programme. A live
  GitHub plan belongs to one organization and is not copied between accounts.
  Consumer adoption resolves the programme from the immutable release being
  pinned, not from `main`. Private attestations stay an Enterprise Cloud plan
  gate, independent of the three add-ons.
- Dependabot catalog convergence commits only catalog and generated docs, so
  the default `GITHUB_TOKEN` can push without `workflows` permission.
  Catalog-only follows the unique workflow pin per action and fails closed
  when identities are mixed, so the catalog cannot describe a pin the tree
  does not share. Ordinary merge in this repository does not require a
  general CI status check; `ci-gate` stays truthful advisory evidence.
  Authored skill `metadata:` mappings stay mappings.

- Re-verify four vendor allowance records with staggered review dates, correct
  Ubicloud's monthly credit and Harness's conditional CI credit semantics, and
  align the disclosed Checkov image tag with the existing pinned action.
- Publish unsuccessful completed self-workflow attempts as unassigned,
  repository-local CI evidence; preserve actual conclusions and exact attempts.
- Accept exact matching development-commit comments and correct nested action
  pin validation and container whitespace rejection. Keep registrations scoped to their actual action paths.

- Declare both git-submodule and reusable-workflow consumption in the GDS
  module contract. Refresh its projection using the existing stable bundle.
- Place the Docker publisher permission explanation inline so the pinned
  pedantic audit recognizes it; workflow permissions and behavior are unchanged.

## [0.1.16] - 2026-09-02

- `security-bundle` authenticates its exact called-workflow source fetch with
  the job token, so a shared fleet egress address cannot exhaust GitHub's
  anonymous allowance and stop the gate before any scanner runs.

## [0.1.15] - 2026-08-31

- `docker-build.yml`: reusable BuildKit image build whose layer cache
  outlives the runner (registry cache on ghcr by default, `gha` and `none`
  backends), registered across the catalog with an infra example.
- `security-bundle` (free): the called-workflow source fetch retries and
  falls back to protocol v0, and evidence uploads only when the scan ran.
- Dependabot pin-registry synchronization for the bumped action set.

## [0.1.14] - 2026-08-30

- Enable Corepack's pnpm and Yarn shims before activating the caller-pinned
  package-manager version, so hosted Node jobs resolve the requested binary
  instead of finding no pnpm or the image's unrelated Yarn Classic install.

## [0.1.13] - 2026-08-27

- Added an optional dedicated runner for Docker-based cargo-deny so
  cargo-audit and cargo-machete can use a lighter runner without weakening
  supply-chain coverage.

## [0.1.12] - 2026-08-27

- The signed `0.1.12` tag is retained as immutable rejected evidence because
  its candidate changelog used the next local-calendar date rather than the
  tag's UTC date. No release exists.

## [0.1.11] - 2026-08-25

- Standardized reviewed network downloads on two retries after the initial
  request: exactly three total attempts, with checksum and permanent-failure
  handling unchanged.

## [0.1.10] - 2026-08-25

- Updated the immutable tool-cache action to signed `v1.0.1`, retaining each
  verified fetch event in the ephemeral runner diagnostic bundle for durable
  OpenObserve evidence after teardown.

## [0.1.9] - 2026-08-25

- The signed `0.1.9` tag is retained as immutable rejected evidence because its
  annotation was not a canonical public promotion record. No release exists.

## [0.1.8] - 2026-08-24

- Added a trusted default-branch Dependabot catalog synchronizer. Failed
  same-repository Dependabot action bumps are updated in place from a
  `workflow_run` job that never executes candidate code; hardening and
  Scorecard validators now derive action identities from the catalog instead
  of carrying additional hardcoded SHA copies.
- Made the synchronizer update the exact bound pull request through GitHub's
  native branch API, approve only `action_required` runs for its exact derived
  SHA, and keep candidate trees data-only. Transitive Docker-action image
  declarations now converge with action pin updates as well.
- Added the machine-enforced cache trust contract v2: provider ref scopes,
  exact-first key dimensions, persistent-runner residue rules, retention and
  rate limits, hosted/fleet equivalence, and real cold/warm telemetry.
- Added successful runtime harnesses for the real cargo-fuzz and
  ClusterFuzzLite reusable workflows, including a complete C++ libFuzzer
  builder integration and fail-closed evidence aggregation.
- Isolated owner-only side-effect runtime fixtures from Dependabot pull
  requests so real bot commits and repository labels are never mistaken for
  disposable commitlint or label-mutation evidence.
- Made release-ledger date reconciliation timezone-independent by deriving the
  tagged commit's UTC author date. The signed `0.1.7` tag remains immutable
  rejected evidence and has no release.

## [0.1.6] - 2026-08-24

- Added explicit Dependabot-safe advanced CodeQL routing. Ordinary private
  analysis and Dependabot pull requests can use separate reviewed runner
  classes while preserving stable language check identities and a secretless
  `pull_request` trust boundary.
- Added validated opt-in dependency cache inputs to `python-ci.yml` pip callers
  and `node-ci.yml` npm/pnpm/Yarn callers. Cache backends must match the package
  manager and dependency paths cannot be supplied without enabling a cache.
- Treat OSV Scanner's exact `No package sources found` result as a successful
  empty inventory while preserving failure for vulnerabilities and all other
  errors. The first MyAttention consolidation ring exposed this in an
  observability-schema repository with no package manager files.
- Fixed `nddev-security-bundle.yml` to supply all four evidence paths required
  by the shared scanner and upload the redacted one-day evidence bundle. The
  first public-product consumer ring exposed the missing OSV, Gitleaks and
  actionlint paths in real private PR jobs.

### Changed

- Routed the consolidated private security bundle's pinned uv, actionlint,
  OSV-Scanner and gitleaks artifacts through the public immutable tool-cache
  action. Baked uv is reused without setup; GitHub-hosted and cache-miss jobs
  retain the same checksum-verified upstream fallback.
- Strengthened the consolidated private-free security bundle without adding a
  placement: actionlint logs plus Zizmor, OSV and fully redacted Gitleaks SARIF
  are always retained as a one-day artifact, including on aggregate failure.
- Completed the no-cancel invariant for queued work: every self-workflow and
  example now uses a run-id-unique concurrency group, because GitHub retains
  only one pending run in a shared group even when cancellation is false.
- Made preservation of started jobs a library invariant. All self-workflows and
  consumer examples now use `cancel-in-progress: false`; the executable
  workflow contract rejects future cancellation expressions, and the
  performance skills optimize duplicate work before execution instead of
  erasing in-flight evidence.
- Added opt-in preinstalled-toolchain paths to Go and Java CI. Immutable
  ephemeral runners verify exact baked Go/gofmt and Java/Maven commands and
  skip redundant setup-action downloads; hosted callers keep existing setup.
- Expanded `python-ci.yml` to an explicit uv-or-pip contract. Hosted callers
  receive the appropriate pinned setup action; immutable ephemeral callers may
  verify baked commands. pip fails closed without a project-owned install
  command instead of guessing dependency or lockfile policy.
- Expanded `node-ci.yml` from a Bun-only lane to a fail-closed npm, pnpm, Yarn
  and Bun contract. Hosted callers receive exact setup; immutable ephemeral
  callers can verify and reuse baked toolchains, avoiding repeated downloads.
  Empty install commands select each manager's frozen-lockfile default.

## [0.1.3] - 2026-08-21

### Fixed

- Granted the release caller the same read-only Actions scope required by its
  reusable promotion gate, and added a static transitive-permission check so a
  tag cannot fail during workflow startup before evidence verification.

## [0.1.2] - 2026-08-21

### Changed

- Added a no-SARIF consolidated private-free security bundle so actionlint,
  zizmor, OSV-Scanner, and gitleaks share one ephemeral placement without paid
  code-scanning permissions.
- Added a private-only consolidated security reusable that runs actionlint,
  zizmor, OSV-Scanner, and gitleaks in one ephemeral job, eliminating three
  independent cold placements per security wave.
- Replaced the private-era release promotion payload with
  `nddev-public-release-promotion/v2`: a signed annotated tag now binds the
  exact public commit to public CI, contract and security evidence without any
  private repository read, and the repository ships the canonical record
  renderer.
- Bound the NDDev private runner mapping to public fleet contract v2: one-job
  ephemeral Incus containers, destroy-after-use lifecycle, and explicit class
  labels. Amsterdam is documented as a bastion/application host rather than an
  Actions execution target.
- Made the GDS module verification lane hermetic with checksum-pinned `uv`,
  Python 3.13.14, hash-locked dependencies and the repository package launcher,
  so a clean consumer checkout can verify the exact pin without ambient Python
  packages.
- Reconciled the public release ledger with the immutable `0.1.1` tag. That
  first publication attempt remains intentionally unreleased after its
  tag-date preflight failed; this version carries the corrected public-native
  promotion graph forward without moving or deleting the signed tag.
- Made promotion evidence self-verifying: the renderer derives canonical
  digests from exact public run/job API payloads, and the release gate fetches
  and recomputes them before authorization.

## [0.1.1] - 2026-08-21

First release of `ci-workflows` as an open-source library under
`NDDev-OpenNetwork`. The version line starts here: consumers pin these workflows
by commit SHA, and numbering carried over from the repository this grew out of
would name releases no tag in this repository can resolve.

### Added

- **Fifty reusable workflows** covering language CI (Go, Python, Node, Rust,
  Java, Kotlin/Android, Swift, C/C++, Qt, Dart/Flutter, .NET, R, SQL, web),
  security scanning (CodeQL, Semgrep, OSV, Grype, gitleaks, zizmor, Trivy,
  Scorecard, IaC), release supply chain, container and documentation lanes, and
  pull-request hygiene.
- **The caller chooses the runner** (ADR 0004). Every workflow takes a `runner`
  input and defaults to a standard GitHub-hosted label, because a public library
  that defaulted to a private self-hosted label would send an outside consumer's
  job somewhere it cannot reach — and would turn a fork's pull request into
  remote code execution on someone's hardware.
- **A fixture estate** that calls each reusable the way a consumer would, so a
  workflow is exercised rather than merely linted. Side-effecting fixtures are
  bound to their cleanup: the evidence gate fails unless every claimed caller
  *and* its cleanup guard succeeded.
- **Forty-five blocking checks** in the core tier, including transitive action
  pinning — a third-party action pinned by SHA can still call one by tag, and
  that resolves at job setup where no input can reach it.
- **Nine consumer skills** under `.agents/skills` and their `.claude` mirrors:
  adoption, failure triage, cost and performance, free-tier planning, inventory
  audit, release provenance, runtime contract testing, workflow authoring and
  Actions security.

### Notes

The runtime-coverage ledger that recorded a `proven_digest` per workflow is not
carried over. It was bound to the predecessor repository — its baseline was a
commit there and every recorded run belonged to it — so importing it would have
asserted evidence that no longer bound anything, which is precisely the false
green such a ledger exists to prevent. Coverage is re-established here by
running the fixtures.
