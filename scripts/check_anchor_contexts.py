#!/usr/bin/env python3
"""Hold the estate anchor's required contexts to what the branch really enforces.

`.gds/repository.yaml` is read across the submodule boundary by
`github-device-sync` to reason about this module without executing it. Its
`verification.required_contexts` names the status checks a merge to the default
branch actually requires -- which is a different kind of thing from
`verification.commands`, and is not derivable from it: a required context is a
check run name, a command is a command.

Two drifts matter and this refuses both:

* the branch gains a required check nobody records, so every reader of the
  anchor believes the gate is weaker than it is. That is not hypothetical --
  a sibling module shipped for months with two required checks absent from its
  anchor, and it was found by hand;
* the branch loses one, so the anchor advertises assurance that no longer
  exists.

`check_rulesets.py` asserts the tracked `branch-main.json` does not require a
general CI status check. This script still compares that desired set (from
`.gds/repository.yaml`) to the live ruleset, so a change made through the API
cannot leave every file in this repository unchanged and green.

Advisory tier: what a live ruleset says is a property of the repository's
settings at this moment, not of the tree, and `AGENTS.md` is explicit that such
checks never sit in the blocking tier.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from ci_workflows_tools._strict_yaml import strict_load

ROOT = Path(__file__).resolve().parent.parent
ANCHOR = ROOT / ".gds/repository.yaml"
REPOSITORY = "NDDev-Technocracy-Archive/ci-workflows-20261014"
BRANCH = "main"
API = f"https://api.github.com/repos/{REPOSITORY}/rules/branches/{BRANCH}"
TIMEOUT_SECONDS = 30


def declared() -> set[str]:
    verification = strict_load(ANCHOR).get("verification") or {}
    return {str(name) for name in (verification.get("required_contexts") or [])}


def _live(token: str) -> list[dict]:
    request = urllib.request.Request(
        API, headers={"Accept": "application/vnd.github+json",
                      "Authorization": f"Bearer {token}",
                      "User-Agent": "nddev-ci-workflows-anchor-audit"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def enforced(rules: list[dict]) -> set[str]:
    contexts: set[str] = set()
    for rule in rules:
        if rule.get("type") != "required_status_checks":
            continue
        parameters = rule.get("parameters") or {}
        for check in parameters.get("required_status_checks") or []:
            context = check.get("context")
            if context:
                contexts.add(str(context))
    return contexts


def check() -> list[str]:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        return ["anchor contexts unverified: set GH_TOKEN to read the live ruleset"]
    try:
        rules = _live(token)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return [f"anchor contexts unverified: {API} unreachable: {exc}"]
    live = enforced(rules)
    anchor = declared()
    if live == anchor:
        return []
    problems = []
    for missing in sorted(live - anchor):
        problems.append(
            f"{BRANCH} requires status check {missing!r}, which "
            ".gds/repository.yaml does not declare in verification.required_contexts")
    for stale in sorted(anchor - live):
        problems.append(
            f".gds/repository.yaml declares required context {stale!r}, which "
            f"{BRANCH} does not enforce")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    problems = check()
    if problems:
        print("check_anchor_contexts: FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("check_anchor_contexts: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
