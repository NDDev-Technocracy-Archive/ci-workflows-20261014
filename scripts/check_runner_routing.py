#!/usr/bin/env python3
"""Compile and validate a workflow/platform/class route before GitHub queues it."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ci_workflows_tools._strict_yaml import strict_load  # noqa: E402
from ci_workflows_tools._workflow_yaml import REPO_ROOT, get_on, workflow_files  # noqa: E402
from ci_workflows_tools import check_runtime_requirements  # noqa: E402

CONTRACT = REPO_ROOT / "catalog" / "workflow-routing.yml"
CONTRACT_SCHEMA = REPO_ROOT / "catalog" / "schema" / "workflow-routing.schema.yaml"
DEFAULT_MAPPING = REPO_ROOT / "examples" / "nddev" / "runner-routing.yaml"
PLATFORMS = {"linux", "macos", "windows"}
BACKENDS = {"self-hosted", "github-hosted-standard"}
VISIBILITIES = {"public", "private", "internal"}
KNOWN_CAPABILITIES = {"container-runtime"}
FLEET_CONTRACT = {
    "repository": "NDDev-Technocracy-OpenNetwork/github-actions-light",
    "minimum_contract_version": 1,
    "worker_kind": "official-actions-runner",
    "ephemeral": False,
    "jobs_per_worker": 1,
    "executed_worker_disposition": "persist",
}


def reusable_workflows() -> set[str]:
    result: set[str] = set()
    for path in workflow_files():
        on_block = get_on(strict_load(path))
        if isinstance(on_block, dict) and "workflow_call" in on_block:
            result.add(f".github/workflows/{path.name}")
    return result


def load_contract(path: Path = CONTRACT) -> tuple[dict[str, dict[str, Any]], list[str]]:
    problems: list[str] = []
    doc = strict_load(path)
    if set(doc) != {"schema", "platforms", "runtime_requirements", "groups"}:
        problems.append(f"{path}: unexpected or missing top-level keys {sorted(set(doc))}")
    if doc.get("schema") != "nddev-ci-workflow-routing/v1":
        problems.append(f"{path}: unsupported schema {doc.get('schema')!r}")
    declared_platforms = doc.get("platforms") or []
    if set(declared_platforms) != PLATFORMS or len(declared_platforms) != len(PLATFORMS):
        problems.append(f"{path}: platforms must be exactly {sorted(PLATFORMS)}")
    declared_requirements = doc.get("runtime_requirements") or []
    known_requirements = set(declared_requirements)
    if known_requirements != KNOWN_CAPABILITIES \
            or len(declared_requirements) != len(known_requirements):
        problems.append(
            f"{path}: runtime_requirements must be exactly {sorted(KNOWN_CAPABILITIES)}"
        )
    routes: dict[str, dict[str, Any]] = {}
    group_ids: set[str] = set()
    for group in doc.get("groups") or []:
        if not isinstance(group, dict):
            problems.append(f"{path}: every group must be a mapping")
            continue
        if set(group) != {"id", "supported_os", "runtime_requirements", "workflows"}:
            problems.append(f"{path}: group {group.get('id')!r} has invalid keys")
        group_id = group.get("id")
        if not isinstance(group_id, str) or not group_id or group_id in group_ids:
            problems.append(f"{path}: invalid or duplicate group id {group_id!r}")
        group_ids.add(str(group_id))
        supported = group.get("supported_os")
        requirements = group.get("runtime_requirements")
        if not isinstance(supported, list) or not supported \
                or len(supported) != len(set(supported)) or set(supported) - PLATFORMS:
            problems.append(f"{path}: group {group.get('id')!r} has invalid supported_os")
            continue
        if not isinstance(requirements, list) \
                or len(requirements) != len(set(requirements)) \
                or set(requirements) - known_requirements:
            problems.append(
                f"{path}: group {group.get('id')!r} has invalid runtime_requirements"
            )
            continue
        for workflow in group.get("workflows") or []:
            if workflow in routes:
                problems.append(f"{path}: {workflow} appears in more than one group")
            routes[workflow] = {
                "group": group.get("id"),
                "supported_os": list(supported),
                "runtime_requirements": list(requirements),
            }
    expected = reusable_workflows()
    missing = expected - set(routes)
    extra = set(routes) - expected
    if missing:
        problems.append(f"{path}: missing reusable workflows {sorted(missing)}")
    if extra:
        problems.append(f"{path}: unknown/non-reusable workflows {sorted(extra)}")

    for workflow in expected & set(routes):
        actual = check_runtime_requirements.derive(REPO_ROOT / workflow)
        stated = set(routes[workflow]["runtime_requirements"])
        if stated != actual:
            problems.append(
                f"{path}: {workflow} states requirements {sorted(stated)}, "
                f"implementation derives {sorted(actual)}"
            )
    return routes, problems


def load_mapping(path: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    problems: list[str] = []
    doc = strict_load(path)
    if set(doc) != {"schema", "fleet_contract", "routes"}:
        problems.append(f"{path}: unexpected or missing top-level keys")
    if doc.get("schema") != "nddev-ci-runner-map/v1":
        problems.append(f"{path}: unsupported schema {doc.get('schema')!r}")
    if doc.get("fleet_contract") != FLEET_CONTRACT:
        problems.append(
            f"{path}: fleet_contract must be the current public container contract "
            f"{FLEET_CONTRACT!r}"
        )
    result: dict[str, dict[str, Any]] = {}
    routes = doc.get("routes") or {}
    for platform, classes in routes.items():
        if platform not in PLATFORMS or not isinstance(classes, dict):
            problems.append(f"{path}: invalid platform {platform!r}")
            continue
        for class_name, route in classes.items():
            key = f"{platform}/{class_name}"
            if not isinstance(route, dict):
                problems.append(f"{path}: {key} must be a mapping")
                continue
            if set(route) != {"runner", "backend", "capabilities"}:
                problems.append(f"{path}: {key} has unexpected or missing keys")
            backend = route.get("backend")
            runner = route.get("runner")
            capabilities = route.get("capabilities")
            if backend not in BACKENDS:
                problems.append(f"{path}: {key} has invalid backend {backend!r}")
            if not isinstance(runner, str) or not runner:
                problems.append(f"{path}: {key} needs a non-empty runner")
            if not isinstance(capabilities, list):
                problems.append(f"{path}: {key} capabilities must be a list")
                capabilities = []
            elif set(capabilities) - KNOWN_CAPABILITIES:
                problems.append(
                    f"{path}: {key} has unknown capabilities "
                    f"{sorted(set(capabilities) - KNOWN_CAPABILITIES)}"
                )
            if platform in {"macos", "windows"} and backend != "github-hosted-standard":
                problems.append(
                    f"{path}: {platform} must remain github-hosted-standard until "
                    "a reviewed native backend exists"
                )
            if backend == "github-hosted-standard":
                expected_prefix = {"linux": "ubuntu-", "macos": "macos-",
                                   "windows": "windows-"}[platform]
                if not isinstance(runner, str) or not runner.startswith(expected_prefix):
                    problems.append(
                        f"{path}: {key} claims a standard hosted backend but runner "
                        f"{runner!r} is not a {expected_prefix} label"
                    )
            result[key] = {
                "platform": platform,
                "class": str(class_name),
                "runner": runner,
                "backend": backend,
                "capabilities": list(capabilities),
            }
    return result, problems


def resolve(workflow: str, platform: str, class_name: str, visibility: str,
            contract: dict[str, dict[str, Any]],
            mapping: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if workflow not in contract:
        raise ValueError(f"unknown reusable workflow {workflow!r}")
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}")
    if visibility not in VISIBILITIES:
        raise ValueError(f"unknown repository visibility {visibility!r}")
    declaration = contract[workflow]
    if platform not in declaration["supported_os"]:
        raise ValueError(
            f"{workflow} does not support {platform}; supported: "
            f"{', '.join(declaration['supported_os'])}"
        )
    key = f"{platform}/{class_name}"
    if key not in mapping:
        raise ValueError(f"unsupported platform/class combination {key!r}")
    route = mapping[key]
    missing = set(declaration["runtime_requirements"]) - set(route["capabilities"])
    if missing:
        raise ValueError(
            f"{key} lacks required machine capabilities {sorted(missing)} for {workflow}"
        )
    if visibility == "public" and route["backend"] == "self-hosted":
        raise ValueError(
            "public repositories may not route fork-capable jobs to a self-hosted backend"
        )
    return {
        "workflow": workflow,
        "platform": platform,
        "class": class_name,
        "runner": route["runner"],
        "backend": route["backend"],
        "runtime_requirements": declaration["runtime_requirements"],
    }


def check() -> list[str]:
    contract, problems = load_contract()
    if not CONTRACT_SCHEMA.is_file():
        problems.append(f"missing routing schema {CONTRACT_SCHEMA}")
    mapping, mapping_problems = load_mapping(DEFAULT_MAPPING)
    problems += mapping_problems
    expected_nddev_routes = {
        "linux/hosted", "linux/fast", "linux/standard", "linux/integration",
        "macos/hosted", "windows/hosted",
    }
    if set(mapping) != expected_nddev_routes:
        problems.append(
            f"{DEFAULT_MAPPING}: NDDev example routes must be exactly "
            f"{sorted(expected_nddev_routes)}, got {sorted(mapping)}"
        )
    if problems:
        return problems

    positive = (
        (".github/workflows/actionlint.yml", "linux", "fast", "private", "nddev-linux"),
        (".github/workflows/go-ci.yml", "linux", "standard", "private", "nddev-linux"),
        (".github/workflows/secret-scan.yml", "linux", "integration", "private", "nddev-linux"),
        (".github/workflows/go-ci.yml", "macos", "hosted", "private", "macos-latest"),
        (".github/workflows/go-ci.yml", "windows", "hosted", "private", "windows-latest"),
        (".github/workflows/swift-ci.yml", "macos", "hosted", "public", "macos-latest"),
        (".github/workflows/actionlint.yml", "linux", "hosted", "public", "ubuntu-latest"),
    )
    for workflow, platform, class_name, visibility, expected in positive:
        try:
            got = resolve(workflow, platform, class_name, visibility, contract, mapping)
        except ValueError as exc:
            problems.append(f"runner-routing positive probe rejected {workflow}: {exc}")
            continue
        if got["runner"] != expected:
            problems.append(
                f"runner-routing positive probe selected {got['runner']!r}, expected {expected!r}"
            )

    negative = (
        (".github/workflows/secret-scan.yml", "linux", "fast", "private"),
        (".github/workflows/secret-scan.yml", "linux", "standard", "private"),
        (".github/workflows/swift-ci.yml", "linux", "standard", "private"),
        (".github/workflows/actionlint.yml", "windows", "hosted", "private"),
        (".github/workflows/go-ci.yml", "macos", "standard", "private"),
        (".github/workflows/go-ci.yml", "windows", "integration", "private"),
        (".github/workflows/go-ci.yml", "linux", "fast", "public"),
        (".github/workflows/secret-scan.yml", "linux", "integration", "public"),
        (".github/workflows/missing.yml", "linux", "hosted", "private"),
        (".github/workflows/go-ci.yml", "solaris", "hosted", "private"),
        (".github/workflows/go-ci.yml", "linux", "missing", "private"),
        (".github/workflows/go-ci.yml", "linux", "hosted", "unknown"),
    )
    for args in negative:
        try:
            resolve(*args, contract, mapping)
        except ValueError:
            continue
        problems.append(f"runner-routing negative probe unexpectedly accepted {args}")

    caller_path = REPO_ROOT / "examples" / "nddev" / "os-capability-routing.yml"
    caller = strict_load(caller_path)
    caller_expectations = {
        "linux-fast": (".github/workflows/actionlint.yml", "nddev-linux"),
        "linux-standard": (".github/workflows/go-ci.yml", "nddev-linux"),
        "linux-integration": (".github/workflows/secret-scan.yml", "nddev-linux"),
        "macos-hosted": (".github/workflows/go-ci.yml", "macos-latest"),
        "windows-hosted": (".github/workflows/go-ci.yml", "windows-latest"),
    }
    jobs = caller.get("jobs") or {}
    for job_id, (workflow, runner) in caller_expectations.items():
        job = jobs.get(job_id) or {}
        if not str(job.get("uses") or "").startswith(
            f"NDDev-Technocracy-Archive/ci-workflows-20261014/{workflow}@"
        ) or (job.get("with") or {}).get("runner") != runner:
            problems.append(
                f"{caller_path}: job {job_id!r} drifted from compiled route "
                f"{workflow} -> {runner}"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow")
    parser.add_argument("--platform", choices=sorted(PLATFORMS))
    parser.add_argument("--class", dest="class_name")
    parser.add_argument("--visibility", choices=["public", "private", "internal"])
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not all((args.workflow, args.platform, args.class_name, args.visibility)):
        problems = check()
        if problems:
            for problem in problems:
                print(f"runner-routing: {problem}", file=sys.stderr)
            return 1
        print("check_runner_routing: OK")
        return 0
    contract, problems = load_contract()
    mapping, mapping_problems = load_mapping(args.mapping)
    problems += mapping_problems
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 2
    try:
        result = resolve(args.workflow, args.platform, args.class_name,
                         args.visibility, contract, mapping)
    except ValueError as exc:
        print(f"route rejected: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else result["runner"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
