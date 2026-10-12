# Runners

GitHub-hosted and self-hosted runners execute jobs. This doc covers the runner
types, the cost model across the three tiers, egress hardening, and self-hosted
considerations.

## GitHub-hosted runner types

| Type | Labels | Notes |
| --- | --- | --- |
| Standard Linux | `ubuntu-latest`, `ubuntu-24.04` | cheapest; the library default |
| Standard Windows | `windows-latest` | higher minute multiplier |
| Standard macOS | `macos-latest` | highest standard multiplier |
| ARM64 (Linux/Windows) | `ubuntu-24.04-arm`, `windows-11-arm` | native ARM builds |
| Larger runners | custom labels | more vCPU/RAM; billable |
| GPU runners | custom labels | ML/graphics; billable |
| macOS-XL | custom labels | Apple-silicon XL; **billable even for public** |

The library defaults reusables to `ubuntu-latest` and exposes a `runner` (or
`os_list`) input to select alternatives.

<a id="cost-model"></a>
## Cost model by tier

| Scenario | Cost |
| --- | --- |
| Public repo + standard hosted runner | **Free, unlimited minutes** |
| Public repo + larger / GPU / macOS-XL runner | **Billable** (even on public) |
| Private repo + standard runner | Free monthly minutes, then billed per-minute |
| Private repo + Windows / macOS | Same, at higher minute **multipliers** |
| Any repo + self-hosted runner | No GitHub minute billing; your infra cost |

Key points:

- **Standard hosted runners are free with unlimited minutes on public repos.**
- **Larger, GPU, and macOS-XL runners are always billable**, including on public
  repositories — do not assume "public = free" for them.
- On private repos, non-Linux runners consume the included-minutes budget faster
  because of platform multipliers. The private-free tier keeps matrices to Linux
  where possible (see [02 Private free](02-private-free.md)).

## July 2026 runner governance updates

GitHub added more hosted-runner governance controls on 2026-06-25:

- admins can disable standard hosted-runner labels such as `ubuntu-latest`;
- macOS runners can be placed in runner groups with repository/workflow access
  controls;
- runner groups can enforce concurrency and routing policy.

RHEL 9 and RHEL 10 images for Linux x64 larger runners are also in public
preview. They are useful for enterprise compatibility testing, but they remain
larger-runner capacity and therefore metered. The catalog entries are
`hosted-runner-governance-controls` and `rhel-larger-runner-images`.

## harden-runner egress control

`step-security/harden-runner` monitors and optionally restricts network egress
from a job, catching exfiltration and unexpected outbound calls.

- **`egress-policy: audit`** — observe and report outbound connections; nothing
  is blocked. Start here.
- **`egress-policy: block`** — deny everything except an explicit
  `allowed-endpoints` list. Use once the audit run has revealed the real egress
  set.

```yaml
- name: Harden runner
  uses: step-security/harden-runner@<full-sha>  # pinned in the library
  with:
    egress-policy: block
    allowed-endpoints: >
      agent.api.stepsecurity.io:443
      github.com:443
      api.github.com:443
```

Harden-Runner is **free on public repos and paid on private repos**. The library
therefore references it only from explicit public/GHAS workflows. Cross-tier
and private-free workflows contain no action reference at all. A step-level
boolean is not a valid off switch because JavaScript actions can have `pre` and
`post` hooks whose lifecycle is independent of the main-step condition.

Recommended progression: `audit` → review the observed endpoints → `block`
with an explicit allow-list.

<a id="self-hosted"></a>
## Self-hosted runners and ARC

Self-hosted runners run on your own machines and skip GitHub minute billing, but
you own patching, isolation, and security.

Reusable private-free checkout jobs set `GIT_CONFIG_GLOBAL` to a job-unique
file under `runner.temp`. This prevents persistent runner Git configuration —
especially an ambient HTTP `Authorization` header — from being combined with
the scoped token configured by `actions/checkout`. The runner's global config
is neither read nor mutated by those jobs.

- **Never** attach self-hosted runners to a **public** repository or any repo
  that accepts forked pull requests — an attacker's PR could execute code on
  your infrastructure. Use ephemeral, isolated runners if you must.
- **Actions Runner Controller (ARC)** runs runners as ephemeral Kubernetes pods
  that auto-scale and are destroyed after each job, which is the recommended
  pattern for private-fleet CI.
- Apply egress controls at the network layer; harden-runner's hosted-runner
  features do not all apply to self-hosted.

<a id="visibility-routing"></a>
## Routing by visibility

Minutes on **standard** GitHub-hosted runners are free and unlimited on public
repositories and metered on private ones, so the cost-optimal routing is the
opposite of what "use our own hardware everywhere" would suggest:

**All three operating systems are included.** `macos-latest`, `windows-latest`
and `ubuntu-latest` are all standard runners, and all three are free and
unlimited on a public repository — macOS is not an exception. On a **private**
repository the multiplier is steep: Linux $0.006/min, Windows 1.67x that, macOS
**10.33x**. A macOS matrix lane that costs a public repository nothing eats ten
times the quota on a private one, so `cross-platform-smoke.yml`'s default matrix
is cheap on OSS and expensive behind the paywall.

**Standard** is load-bearing in that sentence. Larger runners — anything whose
label ends in `-N-cores`, `-large` or `-xlarge` — are billed from the first
minute *including on public repositories*, and the standard-runner allowance does
not offset them. A public repository that reaches for `ubuntu-latest-8-cores`
because "Actions is free on OSS" starts paying immediately. The amounts live in
`catalog/product-facts.yml` (`github-actions-public-standard`,
`github-actions-larger-runners`); do not copy them into prose.
`scripts/check_examples.py` rejects a larger runner in any example, so this
repository cannot ship one by accident.

| Repository visibility | Route to | Why |
| --- | --- | --- |
| **public** | GitHub-hosted (`ubuntu-latest`) | free and unlimited; self-hosted here is a **security defect**, not a saving |
| **private / internal** | self-hosted label | private minutes are metered; self-hosted removes them from the bill entirely |

> **This repository is public.** Nothing in `ci-workflows` may route its own jobs
> to a self-hosted runner, and no example in `examples/` may ship a self-hosted
> label as a default. A forked pull request against a public repository executes
> attacker-controlled code, so a self-hosted runner reachable from a public repo
> is a remote-code-execution path into your own infrastructure. Every `runner`
> input in this library therefore defaults to a GitHub-hosted label, and the
> self-hosted value is supplied **by the calling private repository**, never
> baked in here.

The repository's six self-workflows pass `runner: ubuntu-latest` explicitly on
every local reusable call that exposes a runner selector. A validator resolves
those callees and rejects an omitted, expression-based, or self-hosted value,
so a private-consumer default cannot silently reroute this public repository.

Defence in depth: a private runner group should set
`allows_public_repositories: false`, so even a mistaken `runs-on` in a public
repository cannot reach that fleet — the job stays queued instead of executing.
Treat that as the backstop, not the control. This library does not publish a
live fleet inventory.

### Two independent runner settings

Switching a private repository over is **not** one change. The `runner` input
only covers workflows this library defines; GitHub's own managed scanners have a
separate setting that no workflow file can reach:

| What runs | Where the runner is chosen | How to set it |
| --- | --- | --- |
| Reusable workflows from this library | `runner` input on the caller | `with: { runner: <label> }` |
| CodeQL **default setup** (code scanning) | repository code-scanning settings | `PATCH /repos/{owner}/{repo}/code-scanning/default-setup` with `runner_type: labeled`, `runner_label: <label>` |
| **Code Quality** scans | repository Code quality settings | `PATCH /repos/{owner}/{repo}/code-quality/setup` with `runner_type: labeled`, `runner_label: <label>` — or the UI, *Runner type → Labeled runner* |

Miss either of the last two and the repository still burns metered minutes even
though every caller says otherwise — the scans are scheduled by GitHub, not by a
workflow file in the repository.

<a id="always-name-the-runner"></a>
### Always name the runner — the default belongs to the pin

Every reusable here defaults its runner to `ubuntu-latest`, and that default is
a property of the ref you pinned, not a promise. Move the pin and the default
moves with it; a caller that never wrote the label down inherits whatever the
new revision decided, on a line that did not appear in its own diff.

Write the label in the caller:

```yaml
uses: NDDev-Technocracy-OpenNetwork/ci-workflows/.github/workflows/cross-platform-smoke.yml@<sha>
with:
  os_list: '["ubuntu-latest"]'
```

The cost of naming it is one line. The cost of omitting it is a runner change
that arrives with a version bump and is attributed to the bump.

<a id="move-execution-without-renaming-the-check"></a>
### Moving execution without renaming a required check

`cross-platform-smoke.yml` takes two separate inputs because a matrix entry is
two different facts wearing one name:

| Input | Answers | Appears in |
| --- | --- | --- |
| `os_list` | which platforms are covered | the check name, `smoke (<entry>)` |
| `runner_overrides` | where each entry executes | nothing a ruleset can see |

While every caller is GitHub-hosted the two coincide, and the distinction is
invisible. They come apart the moment a runner class becomes unavailable: if
`smoke (ubuntu-latest)` is a required status context on a protected branch,
moving the job to self-hosted hardware by editing `os_list` renames the context
to `smoke (<new label>)`, the required one never reports, and the branch stops
merging — including the pull request that would fix it.

`runner_overrides` says where an entry executes, leaving the entry — and
therefore the check name — untouched:

```yaml
with:
  os_list: '["ubuntu-latest","macos-latest"]'
  runner_overrides: '[{"os":"ubuntu-latest","runner":["self-hosted","linux","x64"]}]'
```

`smoke (ubuntu-latest)` still reports under that exact name; only the machine
changed.

It is a list of matrix `include` entries rather than a map because that is
GitHub's own mechanism: an include entry whose keys match a combination
contributes its remaining keys to that combination and adds no combinations of
its own. So `runner` is simply absent for anything nobody overrode, `runs-on`
falls back to the entry, and the default `[]` leaves every existing caller
byte-identical. Reading the runner from a computed index instead would work and
zizmor rejects it as obfuscation, correctly: a reader cannot see which runner a
job gets without evaluating an expression in their head.

**Point this at private hardware only from a repository whose forked pull
requests cannot reach it.** A public repository routed to a private fleet turns
any fork's pull request into code execution on that hardware — the property
`scripts/_runners.py` exists to protect. Self-hosted routing belongs to private
callers; public ones stay on standard hosted runners, which are unmetered
anyway.

## What a workflow needs from the machine

Choosing a runner is choosing a machine, and permissions do not tell you what
that machine must provide. `catalog/capabilities.yml` now states it:
`runtime_requirements` names host capabilities a workflow actually uses.

The surface is small, and knowing that is the useful part. **Almost every
reusable needs nothing but a shell by default.** Exactly two default modes need
a container runtime:

| Workflow | Why |
| --- | --- |
| `secret-scan.yml` | compatibility default is a digest-pinned image; explicit Linux/X64 binary mode is shell-only |
| `container-ci.yml` | `trivy-action` shells out to the Docker daemon |

So a private caller routing work onto self-hosted classes needs the Docker-capable
class for two lanes and can put the other forty-four on the cheapest thing that
runs a shell.

The field is derived from the workflow and compared against the catalog by
`check_runtime_requirements.py`, so the two cannot drift: add `docker run` to a
workflow and the gate fails until the catalog says so — and remove it and the
gate fails too, because an overstated requirement pushes callers onto a scarcer
class than they need.

This is deliberately **not** a `runner_class` input. An input would have to name
a private label taxonomy inside a public library, which is what ADR 0004 forbids,
and it would sit beside `runner` as a second way to choose one machine. A
requirement is durable; a class name belongs to whoever operates the fleet and
changes when they do.

### Compile the route before dispatch

`catalog/workflow-routing.yml` completes the contract with a supported OS set
for every reusable. `scripts/check_runner_routing.py` combines that public,
estate-neutral declaration with an operator mapping. The NDDev example is
[`examples/nddev/runner-routing.yaml`](../examples/nddev/runner-routing.yaml):

| Requested route | Concrete backend |
| --- | --- |
| Linux `hosted` | `ubuntu-latest`, standard GitHub-hosted/public-safe |
| Linux `fast` | `nddev-linux`, official persistent `actions/runner` |
| Linux `standard` | `nddev-linux`, official persistent `actions/runner` |
| Linux `integration` | `nddev-linux` with Docker, official persistent `actions/runner` |
| macOS `hosted` | `macos-latest`, standard GitHub-hosted |
| Windows `hosted` | `windows-latest`, standard GitHub-hosted |

Resolve during adoption or generation, then commit the final label in the
caller. Do not create an Actions "routing job": that job has already entered a
runner queue and therefore cannot enforce pre-queue routing.

```bash
.venv/bin/python -I -B scripts/check_python_execution_contract.py --launch check_runner_routing.py -- \
  --workflow .github/workflows/secret-scan.yml \
  --platform linux --class integration --visibility private
```

Invalid combinations fail closed: container work on fast/standard, Swift on
Linux, Linux-only workflows on Windows, a Linux class requested for macOS, and
any public repository mapped to a self-hosted backend. The compiled caller in
[`examples/nddev/os-capability-routing.yml`](../examples/nddev/os-capability-routing.yml)
is checked against the mapping on every gate run.

The generated [workflow routing matrix](generated/workflow-routing.md) keeps
supported OS separate from runtime-proven OS. A static routing check is not a
live run: only successful scoped fixture results with an exact workflow digest
from `scripts/render_runtime_evidence.py` are runtime evidence.
macOS and Windows stay hosted until a future native backend is independently
reviewed and proves the same lifecycle contract.

Reusables here default `runner` to `ubuntu-latest`. A default is a property of
**the commit you pinned**, not of your caller, so a caller that omits `runner`
silently adopts whatever the next pin says. Name it anyway — and if you run your
own fleet you must, because a hosted default will quietly meter you.

That the default is safe today is recent. It was a private self-hosted label
until August 2026. Callers that still need self-hosted Linux pass their own
labels; this library does not publish live hosts. Both ways a stale private
default still bite:

- **On a caller that does not own that label** the job queues forever against a
  runner that will never appear.
- **On a public repository**, `pull_request` executes untrusted fork code —
  and a private default puts it on trusted private infrastructure. GitHub's
  own guidance is blunt about this: "forks of your public repository can
  potentially run dangerous code on your self-hosted runner machine."

So every caller states its runner, even when the pinned default already looks
right. `scripts/check_examples.py` enforces this for every example outside
`examples/nddev/`, which is estate-specific by name. When you move a pin,
diff `inputs.runner.default` between the old and new commit before merging.

### Caller shape

```yaml
# private repository — self-hosted label supplied by the caller
jobs:
  validate:
    permissions: { contents: read }
    uses: NDDev-Technocracy-OpenNetwork/ci-workflows/.github/workflows/private-static.yml@<full-sha>
    with:
      runner: <your-self-hosted-label>
      command: "python3 scripts/validate_all.py"
```

Public callers simply omit `runner` and take the GitHub-hosted default.

> **Capacity, not fallback.** GitHub Actions has **no** automatic spillover from
> a self-hosted label to a hosted runner: a job whose label is busy queues until
> a runner frees up. Size the fleet so queueing is rare rather than trying to
> engineer a fallback — and note that a fallback to hosted runners on a *private*
> repo would silently reintroduce the metered minutes you moved off.

---
Last verified: 2026-08-01
