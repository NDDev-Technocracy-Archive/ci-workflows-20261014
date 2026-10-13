#!/usr/bin/env python3
"""Validate copy-paste caller workflow examples.

Examples are part of the public contract: they must parse as GitHub Actions
YAML, keep least-privilege permissions, and use either the documented `@<sha>`
placeholder or a full-SHA reusable workflow reference.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ci_workflows_tools._workflow_yaml import get_on, load_yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"
USES_RE = re.compile(
    r"^NDDev-Technocracy-Archive/ci-workflows-20261014/\.github/workflows/[^@]+\.ya?ml@(<sha>|[0-9a-f]{40})$"
)
REUSABLE_RE = re.compile(
    r"^NDDev-Technocracy-Archive/ci-workflows-20261014/\.github/workflows/([^@]+\.ya?ml)@"
)
# Runner semantics live in one place so this check and
# check_workflow_contracts.py cannot drift apart about what "hosted" and
# "standard" mean. See _runners.py for why the distinction matters.
from ci_workflows_tools._runners import HOSTED_RUNNER_PREFIXES, LARGER_RUNNER_SUFFIXES
# Only an example that says in its own filename that it targets a private
# repository may name a non-hosted runner. Public repositories get free unmetered
# hosted minutes, and pointing one at self-hosted hardware turns a forked pull
# request into remote code execution on that hardware — so a public example
# naming a fleet label is a defect, not a preference.
SELF_HOSTED_EXAMPLES = (
    "examples/nddev/security-private-selfhosted.yml",
    "examples/nddev/os-capability-routing.yml",
    "examples/personal/security-selfhosted.yml",
    "examples/private-free/security-selfhosted.yml",
    "examples/private-paid-ghas/security-selfhosted.yml",
)


def _reusable_runner_default(uses: str) -> str | None:
    """The `runner` default of a locally-defined reusable, or None.

    Returns None when the reference is not a local reusable or exposes no
    `runner` input — in both cases the caller has nothing to choose.
    """
    match = REUSABLE_RE.match(uses)
    if not match:
        return None
    path = REPO_ROOT / ".github" / "workflows" / match.group(1)
    if not path.is_file():
        return None
    on_block = get_on(load_yaml(path))
    call = on_block.get("workflow_call") if isinstance(on_block, dict) else None
    inputs = (call or {}).get("inputs") if isinstance(call, dict) else None
    runner = (inputs or {}).get("runner") if isinstance(inputs, dict) else None
    if not isinstance(runner, dict):
        return None
    return str(runner.get("default", ""))


def _events(doc: dict[str, Any]) -> set[str]:
    raw = get_on(doc)
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {str(item) for item in raw}
    if isinstance(raw, dict):
        return {str(key) for key in raw}
    return set()


def _coverage() -> list[str]:
    """Every reusable workflow must have at least one caller example.

    AGENTS.md has always required "an example under `examples/`" for a new
    workflow, and nothing checked it. Three reusables shipped without one —
    `gate.yml`, `rust-supply-chain.yml`, `clusterfuzzlite.yml` — so the rule
    held only for as long as whoever wrote the workflow remembered it. An
    example is the only executable statement of a workflow's caller contract:
    the permissions it needs, the inputs it requires, and the runner the caller
    must name for itself.
    """
    from ci_workflows_tools._workflow_yaml import (
        SELF_WORKFLOWS, WORKFLOWS_DIR, get_on, load_yaml,
    )

    reusables = set()
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        if path.name in SELF_WORKFLOWS:
            continue
        on = get_on(load_yaml(path))
        if isinstance(on, dict) and "workflow_call" in on:
            reusables.add(path.name)

    called: set[str] = set()
    for example in sorted(EXAMPLES_DIR.rglob("*.yml")):
        text = example.read_text(encoding="utf-8")
        for name in reusables:
            if f"/.github/workflows/{name}@" in text:
                called.add(name)

    return [
        f"examples/: no caller example references {name} — a reusable without "
        "one has no executable statement of its caller contract"
        for name in sorted(reusables - called)
    ]


def check() -> list[str]:
    problems: list[str] = []
    if not EXAMPLES_DIR.is_dir():
        return [f"missing examples directory: {EXAMPLES_DIR}"]

    for path in sorted(EXAMPLES_DIR.rglob("*.yml")):
        rel = str(path.relative_to(REPO_ROOT))
        doc = load_yaml(path)
        if not isinstance(doc, dict):
            problems.append(f"{rel}: example is not a mapping")
            continue
        events = _events(doc)
        if "pull_request_target" in events:
            problems.append(f"{rel}: canonical examples must not use pull_request_target")
        if "permissions" not in doc:
            problems.append(f"{rel}: missing top-level permissions")
        jobs = doc.get("jobs", {}) or {}
        if not jobs:
            problems.append(f"{rel}: missing jobs")
        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                problems.append(f"{rel}: job `{job_id}` is not a mapping")
                continue
            if "permissions" not in job:
                problems.append(f"{rel}: job `{job_id}` missing permissions")
            uses = str(job.get("uses", ""))
            if uses and not USES_RE.match(uses):
                problems.append(f"{rel}: job `{job_id}` reusable ref is not @<sha> or full SHA: {uses}")
            # An example that omits `runner` inherits whatever the pinned
            # commit defaults to. Most reusables here default to the NDDev
            # self-hosted label, so a copied example either fails on a label
            # the copier does not own, or — worse, when the copier is inside
            # this estate and the repository is public — routes untrusted fork
            # code onto trusted infrastructure. The default is a property of
            # the pin, not of the example, so the example must state it.
            #
            # This is checked whatever the current default is. The rule used to
            # fire only when the default was non-hosted, which made it vacuous
            # the moment the default became `ubuntu-latest`: an example could
            # then inherit silently again, and the next default change would move
            # it with no diff in the example. The point was never the value of
            # today's default — it is that a caller must not have one chosen for
            # it by a commit it pinned months ago.
            if _reusable_runner_default(uses) is not None:
                with_values = job.get("with")
                chosen = with_values.get("runner") if isinstance(with_values, dict) else None
                if chosen is None:
                    problems.append(
                        f"{rel}: job `{job_id}` must set `runner` explicitly — a "
                        "default belongs to the pinned commit, not to the caller"
                    )
                elif str(chosen).endswith(LARGER_RUNNER_SUFFIXES):
                    problems.append(
                        f"{rel}: job `{job_id}` names the larger runner {chosen!r}. "
                        "Hosted is not the same as free: standard runners are "
                        "unmetered on public repositories, larger ones are billed "
                        "from the first minute there too. An example must not put "
                        "a cost on a repository that copies it"
                    )
                elif not str(chosen).startswith(HOSTED_RUNNER_PREFIXES) \
                        and rel not in SELF_HOSTED_EXAMPLES:
                    problems.append(
                        f"{rel}: job `{job_id}` names the non-hosted runner "
                        f"{chosen!r}. Public repositories get free unmetered hosted "
                        "minutes, and a forked pull request on self-hosted hardware "
                        "is remote code execution on it — only an example whose "
                        "filename declares a private target may name a fleet label"
                    )
        if rel.endswith("scorecard.yml") and events != {"push", "schedule"}:
            problems.append(f"{rel}: Scorecard example must use only push + schedule")
        if rel == "examples/private-free/security.yml":
            for job_id, job in jobs.items():
                perms = job.get("permissions", {}) if isinstance(job, dict) else {}
                if isinstance(perms, dict) and "security-events" in perms:
                    problems.append(f"{rel}: private-free job `{job_id}` must not request security-events")
            text = path.read_text(encoding="utf-8")
            if "zizmor-no-sarif.yml" not in text:
                problems.append(f"{rel}: private-free security must use zizmor-no-sarif.yml")
            if "enable_harden_runner" in text:
                problems.append(
                    f"{rel}: private-free callers must use workflows that contain "
                    "no Harden-Runner action, not the unsafe legacy step toggle"
                )
        if rel.endswith("dependency-review.yml") and events != {"pull_request"}:
            problems.append(f"{rel}: dependency-review example must use pull_request only")
    return problems + _coverage()


def main() -> int:
    problems = check()
    if problems:
        print("check_examples: FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("check_examples: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
