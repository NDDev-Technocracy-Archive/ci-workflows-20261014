# Governance — repository rulesets

**Rulesets are GA and the canonical way to govern branches, tags, and pushes**,
superseding classic branch protection. They are layerable, support an
**evaluate** (shadow) mode, have granular bypass actors, and are configurable via
REST and JSON. This library ships ruleset definitions under `.github/rulesets/`.

## Why rulesets over classic branch protection

| Aspect | Classic branch protection | Rulesets |
| --- | --- | --- |
| Targets | Branches only | Branches, tags, **and pushes** |
| Layering | One rule per branch pattern | Multiple rulesets stack |
| Dry-run | No | **`evaluate`** (shadow) mode |
| Bypass control | Coarse | Per-actor `bypass_actors` with `always`/`pull_request` modes |
| API | Legacy protection API | `POST /repos/{owner}/{repo}/rulesets` |
| Availability | Legacy | GA, canonical |

Rulesets are free on public and private repositories.

## Ruleset structure

A ruleset targets `branch`, `tag`, or `push`, has an enforcement state, an
optional bypass list, ref-name conditions, and a list of rules.

```jsonc
{
  "name": "main-protection",
  "target": "branch",
  "enforcement": "active",           // active | evaluate | disabled
  "bypass_actors": [
    { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request" }
  ],
  "conditions": {
    "ref_name": { "include": ["refs/heads/main"], "exclude": [] }
  },
  "rules": [
    { "type": "pull_request",
      "parameters": { "required_approving_review_count": 1,
                      "require_code_owner_review": true,
                      "dismiss_stale_reviews_on_push": true } },
    { "type": "required_status_checks",
      "parameters": { "strict_required_status_checks_policy": true,
                      "required_status_checks": [ { "context": "ci-gate" } ] } },
    { "type": "required_linear_history" },
    { "type": "required_signatures" },
    { "type": "non_fast_forward" },   // block force-push
    { "type": "deletion" }            // block branch deletion
  ]
}
```

- **`enforcement`**: `active` enforces; `evaluate` logs would-be violations
  without blocking (shadow mode — roll out a rule safely first); `disabled` is
  off.
- **`bypass_actors`**: who may bypass, and whether `always` or only via
  `pull_request`. Keep this minimal.
- **`conditions.ref_name`**: which refs the ruleset applies to.

## Applying via REST

```bash
gh api --method POST /repos/NDDev-OpenNetwork/my-repo/rulesets \
  --input .github/rulesets/main-protection.json
```

Store ruleset JSON in the repo under `.github/rulesets/` so governance is
version-controlled and reviewable.

## Recommended `main` ruleset

| Rule | Purpose |
| --- | --- |
| Required status check `ci-gate` (strict) | Every required job green against latest base |
| Required review (≥1) + CODEOWNERS review | Human + owner sign-off |
| Required linear history | No merge-commit tangles |
| Required signatures | Signed commits only |
| Block force-push (`non_fast_forward`) | No history rewrite |
| Block deletion | Protect the branch |

This is the recommended multi-maintainer baseline for **consumers who want a
merge gate**. This repository's own ruleset still requires a pull request,
merge-commit-only merge, resolved review threads, signed commits, and no
force-push/deletion, but it does **not** require a general CI status check:
ordinary merge does not wait on remote `ci-gate`. `ci-gate` still runs here as
truthful advisory evidence. Approvals are zero because GitHub does not allow
an author to approve their own pull request. Projects with an independent
reviewer should use the recommendation above. `ci-gate` is the aggregate job
in `ci.yml`; requiring it remains a selectable consumer choice.

## Who owns which governance surface

Governance was previously described in four places that disagreed. The live API
settles it, and this table records who owns what so the next reader does not
have to re-derive it:

| Surface | Owner | Status |
| --- | --- | --- |
| `.github/rulesets/branch-main.json` | this repository | Canonical desired state for ruleset `21104086`: `allowed_merge_methods: ["merge"]`, signed commits, thread resolution, zero approvals, **no** extra approval for unattributed agent commits, **no** required general CI status check. Live settings are not applied from this tree. |
| `.github/rulesets/tag-semver.json`, `push-hygiene.json` | this repository | Canonical desired state for the repository's own tag and push rules. |
| `NDDev baseline: *` rulesets | the estate control plane | Applied on top, not tracked here. Deleting or editing them from this repository would fight the reconciler. |
| `.gds/compiled-policy.json` | GDS, generated | Generated from the estate policy sources; agrees with live state since the repository-tier override landed. Never edit it here. |

`.github/branch-protection/main.json` used to sit alongside these. It claimed to
record ruleset `18506136` while describing `merge_methods: ["squash"]` and
`require_linear_history: true`; the live ruleset allows the `merge` method only
and sets no linear-history rule. Nothing read the file and no validator compared
it to anything, so it contradicted the tracked ruleset silently. It has been
deleted rather than corrected: two files describing one object is the defect.

### The GDS projection used to disagree

For most of this repository's life `.gds/compiled-policy.json` declared
`allow_squash_merge: true` and `allow_merge_commit: false` under
`management: managed`, while the live repository is the opposite. That is not
stale documentation but *managed intent* disagreeing with reality: a reconcile
run would have flipped a governance model the ruleset, the instruction docs and
the contributor guide all describe correctly.

It could not be corrected from here, and the reason is worth keeping: the file
is generated, its sources live in the estate control plane, and the compiled
policy itself sets `agent.generated_projection_edit: forbidden`. Editing it here
is reverted by the next `gds` run.

The fix was therefore made where it belongs. The estate base policy is
squash-only by design, so the correction is a repository-tier override —
`policies/repositories/ci-workflows.yaml` in the control plane, in the same
shape as the one `github-actions` already carries — plus this module claiming it
in `.gds/repository.yaml`. Both have landed
(NDDev-Technocracy-OpenNetwork/github-device-sync#150), and `gds compile policy` now resolves
four sources and reports `allow_merge_commit: true`, matching the live API.

Two properties of that system are worth carrying, because both cost a CI round
trip to learn:

- **A policy source and its projections move in one commit.** Adding the
  override changed the canonical source-tree digest, so `AGENTS.md`,
  `.claude/CLAUDE.md`, `.github/workflows/gds-ci.yml` and the bundle lock all
  went stale at once and `gds context` failed with
  `GDS_CONTEXT_POLICY_SOURCE_DIGEST_MISMATCH`. Regenerate with
  `gds generate repository --plan` then `--apply`; `--check` prints the expected
  digests beforehand, so the result is verifiable before it is trusted.
- **A module must not claim a profile whose source has not landed.**
  `gds compile policy` fails closed with `GDS_POLICY_PROFILE_MISSING`, so
  claiming it early breaks every `gds` invocation rather than anticipating the
  fix. Merge the source first.

## A required check must be caller-native

Point branch protection at a context produced by a job **in the caller**, whose
`needs:` is the real dependency graph of that run. Copy
[`examples/quality/caller-native-gate.yml`](../examples/quality/caller-native-gate.yml);
this repository's own `ci.yml` uses the same shape.

Do **not** require the context produced by
[`gate.yml`](../.github/workflows/gate.yml). A reusable workflow cannot read its
caller's `needs` context, so that reusable receives the results as the
caller-authored `needs_json` string. It fails closed on inputs that assert
nothing — an empty object, an empty `required_jobs`, a required job missing from
`needs`, or a `required_jobs` list that omits a job present in `needs` — but a
fabricated all-success object still passes, and no validation inside a reusable
can change that. It is a reporting helper for dashboards and summaries.

Two properties matter for any required context, caller-native or not:

- **`if: always()`**, or the context never appears when an upstream job fails
  and the pull request waits forever.
- **a `merge_group` trigger** on the workflow, or an enabled merge queue waits
  for a status that can never arrive.

And never require a context from a **push- or schedule-only** workflow. OSSF
Scorecard is the standing trap: it supports `push` and `schedule` on the default
branch only, so as a required context it can never report on a pull-request head
— it protects nothing while blocking every merge. That requirement can also live
in *classic* branch protection rather than a ruleset, so a ruleset-shaped
investigation finds nothing.

## Tag rulesets

Protect release tags with a **tag ruleset** targeting SemVer tags:

```jsonc
{
  "name": "semver-tags",
  "target": "tag",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/tags/[0-9]*.[0-9]*.[0-9]*"], "exclude": [] } },
  "rules": [ { "type": "deletion" }, { "type": "non_fast_forward" } ]
}
```

This prevents deleting or moving a published release tag — complementing
immutable releases (see
[07 Supply chain](07-supply-chain-slsa-sbom-attestations.md#immutable-releases-ga-2025-10-28)).

## Push rulesets

Push rulesets evaluate **before** the ref updates and can block:

- **Oversized files** (a max file size).
- **Forbidden paths / file extensions** (e.g. block `*.pem`, `secrets/**`).

These run on the push itself, catching mistakes earlier than a PR check.

## Long-lived refs outside the release flow

Two kinds of branch here are kept rather than cleaned up, and neither is covered
by the tag rules above, so the convention is written down instead:

- `checkpoint/**` — a work-in-progress snapshot attached to an open issue. It is
  not merge-ready by construction and its pull request says so.
- `archive/**` — a frozen pre-rewrite state, kept so a rewritten branch's history
  stays reachable.

Two archive refs currently resolve to the same object, `0215cf26`:
`archive/2026-08-13-ci-workflows-sdk-pre-python-split` is **canonical** and
`archive/2026-08-13-ci-workflows-129-pre-python-split` is a documented alias of
it. Neither is deleted. Cite the canonical name; expect to meet the alias in
older issue comments.

## Migrating from classic branch protection

1. Read the existing protection (`GET /repos/{owner}/{repo}/branches/{branch}/protection`).
2. Translate each setting into ruleset rules (table below).
3. Create the ruleset in **`evaluate`** mode and watch the ruleset insights for
   violations for a few days.
4. Flip to **`active`**, then remove the classic protection.

| Classic setting | Ruleset rule |
| --- | --- |
| Require PR reviews | `pull_request` (`required_approving_review_count`) |
| Require code owner review | `pull_request` (`require_code_owner_review`) |
| Require status checks (strict) | `required_status_checks` (`strict_...: true`) |
| Require linear history | `required_linear_history` |
| Require signed commits | `required_signatures` |
| Disallow force push | `non_fast_forward` |
| Disallow deletion | `deletion` |

## Required workflows and merge queue

- **Organization required workflows** can force a reusable CI workflow to run on
  every repo in scope — an org-level complement to per-repo rulesets.
- **Merge queue** integrates with the required-status-checks rule: enable it on
  the protected branch and required checks run against the queue's candidate
  branch via the `merge_group` event (see
  [04 Actions core](04-actions-core.md#event-triggers-for-orchestration)).
- **License compliance** entered public preview on 2026-06-30 for Enterprise
  Cloud customers with GitHub Advanced Security Code Security. Treat it as a
  ruleset-based supply-chain gate: pilot in evaluate mode, then require license
  compliance check results before merge once policy false-positives are known.

---
Last verified: 2026-07-10
