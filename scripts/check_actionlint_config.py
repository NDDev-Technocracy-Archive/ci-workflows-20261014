#!/usr/bin/env python3
"""Hold the one actionlint suppression to the scope it claims.

`.github/actionlint.yaml` silences a single actionlint diagnostic: v1.7.12 — the
current release, and the one `actionlint.yml` pins — models the `job` context as
`{check_run_id, container, services, status}` and therefore reports
`job.workflow_repository`, `job.workflow_sha` and `job.workflow_file_path` as
undefined. Those three are real, and five reusables need them to name themselves
rather than their caller.

A suppression is a hole in a gate, so it gets a gate of its own. This validator
proves four things a reader would otherwise have to take on trust:

* the config silences exactly one pattern, scoped to exactly the files that need
  it — nothing repository-wide;
* the pattern still rejects a typo. `job.workflow_shaa` and any other unknown
  property must keep being reported, which is checked by running the regex
  against the real diagnostic text rather than by reading it;
* the files that reference `job.workflow_*` are exactly the files the glob
  covers, in both directions, so the scope cannot quietly widen or go stale;
* no workflow references a `job` property that is neither modelled by actionlint
  nor one of the three known-real ones — the case actionlint can no longer catch
  here.

Everything above is a property of the tree, so this is a blocking check. Whether
upstream has since learned these properties is a fact about somebody else's
release schedule and does not belong in this tier.
"""
from __future__ import annotations

import re
from pathlib import Path

from ci_workflows_tools._strict_yaml import strict_load
from ci_workflows_tools._workflow_yaml import WORKFLOWS_DIR, workflow_files

CONFIG = Path(__file__).resolve().parent.parent / ".github/actionlint.yaml"
EXPECTED_GLOB = ".github/workflows/{dart-flutter-ci,kotlin-android-ci,nddev-security-bundle,private-security-bundle-free,qt-ci}.yml"
SUPPRESSED = ("workflow_file_path", "workflow_repository", "workflow_sha")
# actionlint's own model of the `job` context, as of v1.7.12.
MODELLED = ("check_run_id", "container", "services", "status")
# The diagnostic actionlint emits, with the object type spelled out as it prints
# it. The ignore pattern is matched against this text, so the only honest way to
# show the pattern is narrow is to run it against the real thing.
DIAGNOSTIC = (
    'property "{name}" is not defined in object type {{check_run_id: number; '
    "container: {{id: string; network: string}}; services: {{string => "
    "{{id: string; network: string; ports: {{string => string}}}}}}; "
    "status: string}}"
)
MUST_STILL_REPORT = ("workflow_shaa", "workflow", "workflow_sha_", "container_id", "steps")
# Only real expressions count. These workflows discuss `job.workflow_*` in
# comments explaining why the contexts are used, and prose is not a reference.
EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
JOB_PROPERTY = re.compile(r"\bjob\.([A-Za-z_][A-Za-z0-9_]*)")


def _covered_by_glob() -> set[str]:
    """The workflow file names the configured glob selects."""
    prefix, _, rest = EXPECTED_GLOB.partition("{")
    names, _, suffix = rest.partition("}")
    return {f"{prefix}{name}{suffix}" for name in names.split(",")}


def check() -> list[str]:
    problems: list[str] = []
    if not CONFIG.is_file():
        return [f"{CONFIG.name} is missing; the SDK reusables cannot pass actionlint without it"]
    config = strict_load(CONFIG)
    # GitHub-hosted Ubuntu 26.04 images, which actionlint 1.7.12 predates
    # (rhysd/actionlint#682): declared as runner labels and nothing else.
    hosted = config.pop("self-hosted-runner", None)
    if hosted is not None and hosted != {"labels": ["ubuntu-26.04", "ubuntu-26.04-arm"]}:
        return [
            f"{CONFIG.name} self-hosted-runner may declare only the hosted "
            f"ubuntu-26.04 and ubuntu-26.04-arm labels; found {hosted!r}"]
    if set(config) != {"paths"}:
        problems.append(
            f"{CONFIG.name} must declare only `paths`; found {sorted(config)}")
        return problems
    paths = config["paths"]
    if list(paths) != [EXPECTED_GLOB]:
        problems.append(
            f"{CONFIG.name} must scope its suppression to {EXPECTED_GLOB!r}, "
            f"not {list(paths)}")
        return problems
    entry = paths[EXPECTED_GLOB]
    if set(entry) != {"ignore"} or not isinstance(entry["ignore"], list) \
            or len(entry["ignore"]) != 1:
        problems.append(f"{CONFIG.name} must carry exactly one ignore pattern")
        return problems

    pattern = re.compile(entry["ignore"][0])
    for name in SUPPRESSED:
        if not pattern.search(DIAGNOSTIC.format(name=name)):
            problems.append(
                f"{CONFIG.name}: the ignore pattern no longer silences {name!r}, "
                "so the SDK reusables will fail actionlint")
    for name in MUST_STILL_REPORT:
        if pattern.search(DIAGNOSTIC.format(name=name)):
            problems.append(
                f"{CONFIG.name}: the ignore pattern also silences {name!r}; a "
                "suppression must not hide an unknown property")

    known = set(SUPPRESSED) | set(MODELLED)
    users: set[str] = set()
    for path in workflow_files():
        relative = path.relative_to(WORKFLOWS_DIR.parent.parent).as_posix()
        referenced = [
            name
            for expression in EXPRESSION.findall(path.read_text(encoding="utf-8"))
            for name in JOB_PROPERTY.findall(expression)
        ]
        for name in referenced:
            if name not in known:
                problems.append(
                    f"{relative}: `job.{name}` is not a known job-context property")
            if name in SUPPRESSED:
                users.add(relative)

    covered = _covered_by_glob()
    for extra in sorted(users - covered):
        problems.append(
            f"{extra}: references a suppressed job property but is outside "
            f"{CONFIG.name}'s scope, so actionlint will fail it")
    for stale in sorted(covered - users):
        problems.append(
            f"{CONFIG.name}: {stale} no longer needs the suppression; narrow the glob")
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("check_actionlint_config: FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("check_actionlint_config: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
