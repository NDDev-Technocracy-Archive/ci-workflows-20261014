#!/usr/bin/env python3
"""Fail-closed contract for event-correct Scorecard SARIF runtime evidence."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from copy import deepcopy
from typing import Any

from ci_workflows_tools._strict_yaml import strict_load
from ci_workflows_tools._workflow_yaml import REPO_ROOT, WORKFLOWS_DIR, get_on, load_yaml
from ci_workflows_tools.check_harden_runner_contract import HARDENED_WORKFLOWS, HARDEN_RUNNER
from ci_workflows_tools.check_tool_registry import catalog_action

CODEQL_ACTION, CODEQL_ACTION_VERSION = catalog_action("codeql-action")
CODEQL_REPOSITORY, CODEQL_SHA = CODEQL_ACTION.rsplit("@", 1)
from ci_workflows_tools.check_python_execution_contract import clean_environment

CONTRACT_PATH = REPO_ROOT / "catalog" / "scorecard-evidence.yml"
CALLER_PATH = WORKFLOWS_DIR / "scorecard.yml"
REUSABLE_PATH = WORKFLOWS_DIR / "public-scorecard.yml"
ANALYSIS_REUSABLE_PATH = WORKFLOWS_DIR / "public-scorecard-analysis.yml"
SARIF_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "scorecard" / "three-runs.sarif"
EXPECTED_CATEGORIES = [
    "supply-chain/branch-protection",
    "supply-chain/local",
    "supply-chain/online-scm",
]
EXPECTED_TOOL = {"name": "Scorecard", "version": "v5.5.0", "guid": None}
EXPECTED_CATEGORY_CONTRACT = {
    "authority": "upstream-run-automation-details",
    "category_separator": "last-forward-slash",
    "upload_fallback_category": "ossf-scorecard",
    "expected_categories": EXPECTED_CATEGORIES,
    "expected_tool": EXPECTED_TOOL,
    "source_urls": [
        "https://github.com/ossf/scorecard/blob/v5.5.0/pkg/scorecard/sarif.go",
        "https://github.com/github/codeql-action/blob/"
        f"{CODEQL_SHA}/src/upload-lib.ts",
        "https://docs.github.com/en/code-security/reference/code-scanning/"
        "sarif-files/sarif-support-for-code-scanning",
    ],
}
EXPECTED_CONSUMER = {
    "repository": "NDDev-OpenNetwork/ci-workflows",
    "visibility": "public",
    "default_branch": "main",
    "caller_workflow": ".github/workflows/scorecard.yml",
    "reusable_workflow": ".github/workflows/public-scorecard.yml",
    "analysis_reusable_workflow": ".github/workflows/public-scorecard-analysis.yml",
    "supported_events": ["push", "schedule"],
    "runner": "ubuntu-latest",
    "publish_results": True,
    "sarif_fallback_category": "ossf-scorecard",
    "sarif_categories": EXPECTED_CATEGORIES,
    "sarif_tool": EXPECTED_TOOL,
    "required_permissions": {
        "actions": "read",
        "contents": "read",
        "id-token": "write",
        "security-events": "write",
    },
    "analysis_only_permissions": {
        "actions": "read",
        "contents": "read",
    },
}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_MATRIX = [{
    "identity": "analysis-only",
    "workflow": ".github/workflows/public-scorecard-analysis.yml",
    "public_execution_tier": "conditional-public-oss",
    "private_execution_tier": "unsupported",
    "runner_class": "github-hosted-standard",
    "capability": "sarif-analysis-no-publication",
    "permissions": {"actions": "read", "contents": "read"},
    "harden_runner": {
        "required": True, "pin": HARDEN_RUNNER,
        "egress_policy": "audit", "position": "first",
    },
    "allowed_callers": {
        "visibility": "public", "repository_kind": "non-fork",
        "events": ["push", "schedule"], "ref": "actual-default-branch",
        "runner": "ubuntu-latest",
    },
    "evidence_obligations": [
        "job-and-analysis-step-success", "no-oidc-or-write-permission",
        "no-artifact-or-sarif-upload-step", "exact-caller-and-reusable-sha",
    ],
}, {
    "identity": "publish",
    "workflow": ".github/workflows/public-scorecard.yml",
    "public_execution_tier": "public-oss",
    "private_execution_tier": "unsupported",
    "runner_class": "github-hosted-standard",
    "capability": "scorecard-publication-and-sarif-upload",
    "permissions": {
        "actions": "read", "contents": "read", "id-token": "write",
        "security-events": "write",
    },
    "harden_runner": {
        "required": True, "pin": HARDEN_RUNNER,
        "egress_policy": "audit", "position": "first",
    },
    "allowed_callers": {
        "visibility": "public", "repository_kind": "non-fork",
        "events": ["push", "schedule"], "ref": "actual-default-branch",
        "runner": "ubuntu-latest",
    },
    "evidence_obligations": [
        "analysis-and-upload-steps-success", "exact-caller-and-reusable-sha",
        "accepted-code-scanning-analysis-set",
        "exact-ref-sha-workflow-tool-and-category-set",
    ],
}]
EXPECTED_ATTEMPTS = [{
    "attempt": 1,
    "classification": "product-failure",
    "caller_sha": "1c5545457b36d6875a43695902fca66a02136a18",
    "run_id": 31662081168,
    "run_url": "https://github.com/NDDev-OpenNetwork/ci-workflows/actions/runs/31662081168",
    "conclusion": "startup_failure",
    "reason": (
        "Nested scorecard job requested id-token: write while its "
        "publish_results:false caller allowed id-token: none."
    ),
}, {
    "attempt": 2,
    "classification": "product-failure",
    "caller_sha": "d1d5376da29bb2c494498630e04c5454287575a4",
    "run_id": 31662731064,
    "run_url": "https://github.com/NDDev-OpenNetwork/ci-workflows/actions/runs/31662731064",
    "conclusion": "startup_failure",
    "reason": (
        "GitHub preflight validated the skipped publish job before if and "
        "rejected its write permissions for the read-only caller."
    ),
}, {
    "attempt": 3,
    "classification": "contract-test-failure",
    "base_sha": "d1d5376da29bb2c494498630e04c5454287575a4",
    "issue_receipt": (
        "https://github.com/NDDev-OpenNetwork/ci-workflows/issues/128"
        "#issuecomment-5275483568"
    ),
    "command": "python3 scripts/validate_all.py --tier core",
    "failed_contract": "harden-runner-contract",
    "reason": (
        "The physical public analysis entrypoint was absent from the exact "
        "Harden-Runner public/GHAS allowlist."
    ),
}]
EXPECTED_RECOVERY_ATTEMPTS = [{
    "attempt": 1,
    "classification": "product-contract-failure",
    "caller_sha": "292bd5b4cb4b0df6848a204ffcb85367475b9f4e",
    "run_id": 31663872580,
    "run_url": "https://github.com/NDDev-OpenNetwork/ci-workflows/actions/runs/31663872580",
    "job_ids": [94334223400, 94334223449],
    "analysis_ids": [1611368002, 1611367941, 1611367893],
    "artifact_id": 9167197636,
    "artifact_sha256": "d0343578d2def1f7c6b4a0a139a381876f76b376fb0662e39ce9c80b06e3ea33",
    "issue_receipt": (
        "https://github.com/NDDev-OpenNetwork/ci-workflows/issues/128"
        "#issuecomment-5275586641"
    ),
    "reason": (
        "The verifier required one caller category, but upstream Scorecard emitted "
        "three authoritative runAutomationDetails categories that upload-sarif preserved."
    ),
}]


def _steps(workflow: dict[str, Any], job_id: str) -> dict[str, dict[str, Any]]:
    raw = workflow.get("jobs", {}).get(job_id, {}).get("steps", [])
    return {str(step.get("name")): step for step in raw if isinstance(step, dict)}


def _embedded_guard(step: dict[str, Any]) -> str:
    lines = str(step.get("run", "")).splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == "python3 -I <<'PY'"]
    if len(starts) != 1:
        raise ValueError(f"expected one isolated Python heredoc, found {len(starts)}")
    start = starts[0] + 1
    end = next(i for i in range(start, len(lines)) if lines[i] == "PY")
    return "\n".join(lines[start:end]) + "\n"


def _run_guard(program: str, **overrides: str) -> int:
    env = {
        "SCORECARD_EVENT_NAME": "push",
        "SCORECARD_REF": "refs/heads/main",
        "SCORECARD_DEFAULT_BRANCH": "main",
        "SCORECARD_REPOSITORY_PRIVATE": "false",
        "SCORECARD_REPOSITORY_FORK": "false",
        "SCORECARD_SERVER_URL": "https://github.com",
        "SCORECARD_SARIF_CATEGORY": "ossf-scorecard",
        **overrides,
    }
    return subprocess.run(
        [sys.executable, "-I", "-"], input=program,
        env=clean_environment(env), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    ).returncode


def _caller_contract_problems(caller: dict[str, Any], consumer: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    events = get_on(caller)
    if not isinstance(events, dict) or set(events) != {"push", "schedule"}:
        problems.append("canonical Scorecard consumer must trigger only on push and schedule")
    elif events.get("push", {}).get("branches") != ["main"]:
        problems.append("canonical Scorecard consumer push must target only its default branch main")
    if caller.get("permissions") != {}:
        problems.append("canonical Scorecard consumer must deny permissions at top level")
    concurrency = caller.get("concurrency", {})
    if concurrency.get("cancel-in-progress") is not False:
        problems.append("canonical Scorecard evidence must preserve the first run rather than cancel it")
    jobs = caller.get("jobs", {})
    expected = {
        "analysis-only": (
            consumer["analysis_only_permissions"],
            "./.github/workflows/public-scorecard-analysis.yml",
            {"runner": "ubuntu-latest", "sarif_category": "ossf-scorecard"},
        ),
        "scorecard": (
            consumer["required_permissions"],
            "./.github/workflows/public-scorecard.yml",
            {"runner": "ubuntu-latest", "publish_results": True,
             "sarif_category": "ossf-scorecard"},
        ),
    }
    if set(jobs) != set(expected):
        problems.append("canonical consumer must contain exactly analysis-only and publish jobs")
    for job_id, (permissions, entrypoint, inputs) in expected.items():
        job = jobs.get(job_id, {})
        if job.get("uses") != entrypoint:
            problems.append(f"canonical {job_id} consumer must call its physical entrypoint")
        if "if" in job:
            problems.append(f"canonical {job_id} must not hide an entrypoint behind job if")
        if job.get("permissions") != permissions:
            problems.append(f"canonical {job_id} permissions drifted from its exact mode")
        if job.get("with") != inputs:
            problems.append(f"canonical {job_id} inputs must select its exact boolean mode")
    analysis_permissions = jobs.get("analysis-only", {}).get("permissions", {})
    if {"id-token", "security-events"} & set(analysis_permissions):
        problems.append("analysis-only caller must not grant OIDC or Code Scanning writes")
    return problems


def _category_from_automation_id(value: Any) -> str | None:
    if not isinstance(value, str) or "/" not in value:
        return None
    category, run_id = value.rsplit("/", 1)
    return category if category and run_id else None


def verify_sarif_contract(sarif_log: Any, consumer: dict[str, Any]) -> list[str]:
    """Validate the upstream-owned multi-run identities before SARIF upload."""
    if not isinstance(sarif_log, dict):
        return ["Scorecard SARIF must be a mapping"]
    problems: list[str] = []
    if sarif_log.get("version") != "2.1.0":
        problems.append("Scorecard SARIF version must be 2.1.0")
    runs = sarif_log.get("runs")
    if not isinstance(runs, list):
        return problems + ["Scorecard SARIF runs must be a list"]
    observed: list[str | None] = []
    for index, run in enumerate(runs):
        if not isinstance(run, dict):
            problems.append(f"Scorecard SARIF run {index} must be a mapping")
            continue
        automation_id = (run.get("automationDetails") or {}).get("id")
        category = _category_from_automation_id(automation_id)
        observed.append(category)
        if category is None:
            problems.append(f"Scorecard SARIF run {index} lacks a category/run-id automationDetails.id")
        driver = (run.get("tool") or {}).get("driver") or {}
        actual_tool = {
            "name": driver.get("name"),
            "version": driver.get("semanticVersion"),
            "guid": driver.get("guid"),
        }
        if actual_tool != consumer["sarif_tool"]:
            problems.append(f"Scorecard SARIF run {index} tool identity drifted")
    if sorted(observed, key=str) != sorted(consumer["sarif_categories"]):
        problems.append(
            f"Scorecard SARIF category multiset is {observed!r}, "
            f"expected {consumer['sarif_categories']!r}"
        )
    return problems


def _analysis_problems(analyses: Any, proof: dict[str, Any], consumer: dict[str, Any]) -> list[str]:
    if not isinstance(analyses, list):
        return ["scorecard proof analyses must be a list"]
    problems: list[str] = []
    categories: list[Any] = []
    analysis_ids: list[Any] = []
    sarif_ids: list[Any] = []
    required = {
        "id", "url", "analysis_key", "ref", "commit_sha", "category",
        "tool", "created_at", "sarif_id", "error",
    }
    for index, analysis in enumerate(analyses):
        if not isinstance(analysis, dict):
            problems.append(f"scorecard proof analysis {index} must be a mapping")
            continue
        if set(analysis) != required:
            problems.append(f"scorecard proof analysis {index} fields drifted")
            continue
        categories.append(analysis["category"])
        analysis_ids.append(analysis["id"])
        sarif_ids.append(analysis["sarif_id"])
        expected = {
            "analysis_key": f"{consumer['caller_workflow']}:scorecard",
            "ref": f"refs/heads/{consumer['default_branch']}",
            "commit_sha": proof.get("caller_sha"),
            "tool": consumer["sarif_tool"],
        }
        for key, value in expected.items():
            if analysis.get(key) != value:
                problems.append(f"scorecard proof analysis {index} {key} drifted")
        if not isinstance(analysis["id"], int) or analysis["id"] <= 0:
            problems.append(f"scorecard proof analysis {index} id must be positive")
        expected_url = (
            f"https://api.github.com/repos/{consumer['repository']}/"
            f"code-scanning/analyses/{analysis['id']}"
        )
        if analysis["url"] != expected_url:
            problems.append(f"scorecard proof analysis {index} URL is not its exact GitHub API receipt")
        if str(analysis["created_at"]) < str(proof.get("run_started_at", "")):
            problems.append(f"scorecard proof analysis {index} predates the caller run")
        if str(analysis["created_at"]) > str(proof.get("run_completed_at", "")):
            problems.append(f"scorecard proof analysis {index} postdates the completed caller run")
        if not isinstance(analysis["sarif_id"], str) or not analysis["sarif_id"]:
            problems.append(f"scorecard proof analysis {index} lacks its upload SARIF ID")
        if analysis["error"] != "":
            problems.append(f"scorecard proof analysis {index} was not accepted without error")
    if sorted(str(category) for category in categories) != sorted(consumer["sarif_categories"]):
        problems.append("scorecard proof analysis categories are not the exact expected set")
    if len({repr(value) for value in analysis_ids}) != len(analysis_ids):
        problems.append("scorecard proof contains duplicate analysis IDs")
    if len({repr(value) for value in sarif_ids}) != 1:
        problems.append("scorecard proof analyses do not share one upload SARIF ID")
    return problems


def verify_proof(proof: Any, consumer: dict[str, Any]) -> list[str]:
    """Validate an immutable post-merge API receipt; synthetic self-tests use this too."""
    if proof is None:
        return []
    if not isinstance(proof, dict):
        return ["scorecard proof must be null or a mapping"]
    required = {
        "repository", "caller_sha", "caller_ref", "caller_workflow", "event",
        "run_id", "run_url", "job_id", "job_url", "analysis_only_job_id",
        "analysis_only_job_url", "analysis_only_step", "analysis_step",
        "upload_step", "reusable_sha", "reusable_digest", "reusable_workflow",
        "analysis_reusable_sha", "analysis_reusable_digest",
        "analysis_reusable_workflow", "artifact_id", "artifact_sha256",
        "run_started_at", "run_completed_at", "analyses",
    }
    missing = required - set(proof)
    extra = set(proof) - required
    problems: list[str] = []
    if missing:
        problems.append(f"scorecard proof missing fields {sorted(missing)}")
    if extra:
        problems.append(f"scorecard proof has unexpected fields {sorted(extra)}")
    if missing:
        return problems
    expected = {
        "repository": consumer["repository"],
        "caller_ref": f"refs/heads/{consumer['default_branch']}",
        "caller_workflow": consumer["caller_workflow"],
        "event": "push",
        "analysis_only_step": "success",
        "analysis_step": "success",
        "upload_step": "success",
        "reusable_workflow": consumer["reusable_workflow"],
        "analysis_reusable_workflow": consumer["analysis_reusable_workflow"],
    }
    for key, value in expected.items():
        if proof.get(key) != value:
            problems.append(f"scorecard proof {key} is {proof.get(key)!r}, expected {value!r}")
    if not SHA_RE.fullmatch(str(proof.get("caller_sha", ""))):
        problems.append("scorecard proof caller_sha must be a full commit SHA")
    for key in ("reusable_sha", "analysis_reusable_sha"):
        if proof.get(key) != proof.get("caller_sha"):
            problems.append(f"scorecard proof {key} does not equal the exact caller commit")
    for key in ("reusable_digest", "analysis_reusable_digest", "artifact_sha256"):
        if not DIGEST_RE.fullmatch(str(proof.get(key, ""))):
            problems.append(f"scorecard proof {key} must be a lowercase SHA-256")
    for key in ("run_id", "job_id", "analysis_only_job_id", "artifact_id"):
        if not isinstance(proof.get(key), int) or proof[key] <= 0:
            problems.append(f"scorecard proof {key} must be a positive integer")
    for key in ("run_url", "job_url", "analysis_only_job_url"):
        if not str(proof.get(key, "")).startswith("https://github.com/"):
            problems.append(f"scorecard proof {key} must be an immutable GitHub URL")
    problems += _analysis_problems(proof.get("analyses"), proof, consumer)
    return problems


def _proof_selftests(consumer: dict[str, Any]) -> list[str]:
    base_analysis = {
        "id": 3,
        "url": "https://api.github.com/repos/NDDev-OpenNetwork/ci-workflows/code-scanning/analyses/3",
        "analysis_key": ".github/workflows/scorecard.yml:scorecard",
        "ref": "refs/heads/main",
        "commit_sha": "a" * 40,
        "category": "",
        "tool": deepcopy(consumer["sarif_tool"]),
        "created_at": "2026-08-13T00:01:00Z",
        "sarif_id": "upload-1",
        "error": "",
    }
    analyses = []
    for index, category in enumerate(consumer["sarif_categories"]):
        analysis = deepcopy(base_analysis)
        analysis_id = index + 3
        analysis.update({
            "id": analysis_id,
            "url": (
                "https://api.github.com/repos/NDDev-OpenNetwork/ci-workflows/"
                f"code-scanning/analyses/{analysis_id}"
            ),
            "category": category,
        })
        analyses.append(analysis)
    base = {
        "repository": consumer["repository"], "caller_sha": "a" * 40,
        "caller_ref": "refs/heads/main", "caller_workflow": consumer["caller_workflow"],
        "event": "push", "run_id": 1,
        "run_url": "https://github.com/NDDev-OpenNetwork/ci-workflows/actions/runs/1",
        "job_id": 2, "job_url": "https://github.com/NDDev-OpenNetwork/ci-workflows/actions/runs/1/job/2",
        "analysis_only_job_id": 4,
        "analysis_only_job_url": "https://github.com/NDDev-OpenNetwork/ci-workflows/actions/runs/1/job/4",
        "analysis_only_step": "success", "analysis_step": "success", "upload_step": "success",
        "reusable_sha": "a" * 40, "reusable_digest": "b" * 64,
        "reusable_workflow": consumer["reusable_workflow"],
        "analysis_reusable_sha": "a" * 40, "analysis_reusable_digest": "d" * 64,
        "analysis_reusable_workflow": consumer["analysis_reusable_workflow"],
        "artifact_id": 5, "artifact_sha256": "e" * 64,
        "run_started_at": "2026-08-13T00:00:00Z",
        "run_completed_at": "2026-08-13T00:02:00Z", "analyses": analyses,
    }
    if verify_proof(base, consumer):
        return ["scorecard proof self-test rejected the valid exact-set receipt"]
    problems: list[str] = []
    top_mutations = {
        "non-default-ref": {"caller_ref": "refs/heads/fixtures/test"},
        "private-or-nondesignated": {"repository": "NDDev-OpenNetwork/other"},
        "skipped-analysis": {"analysis_step": "skipped"},
        "skipped-analysis-only": {"analysis_only_step": "skipped"},
        "skipped-upload": {"upload_step": "skipped"},
        "unpinned-reusable": {"reusable_sha": "main"},
        "json-only": {"reusable_workflow": ".github/workflows/public-scorecard-json.yml"},
        "wrong-analysis-entrypoint": {"analysis_reusable_workflow": ".github/workflows/public-scorecard.yml"},
    }
    for label, values in top_mutations.items():
        candidate = deepcopy(base)
        candidate.update(values)
        if not verify_proof(candidate, consumer):
            problems.append(f"scorecard proof self-test accepted {label}")
    analysis_mutations = {
        "wrong-ref": {"ref": "refs/heads/other"},
        "wrong-sha": {"commit_sha": "c" * 40},
        "wrong-tool": {"tool": {"name": "CodeQL", "version": "v5.5.0", "guid": None}},
        "wrong-version": {"tool": {"name": "Scorecard", "version": "v0", "guid": None}},
        "wrong-analysis-key": {"analysis_key": ".github/workflows/other.yml:scorecard"},
        "stale-analysis": {"created_at": "2026-08-12T23:59:59Z"},
        "later-run-analysis": {"created_at": "2026-08-13T00:03:00Z"},
        "api-error": {"error": "processing failed"},
    }
    for label, values in analysis_mutations.items():
        candidate = deepcopy(base)
        candidate["analyses"][0].update(values)
        if not verify_proof(candidate, consumer):
            problems.append(f"scorecard proof self-test accepted {label}")
    list_mutations = {
        "missing-category": lambda values: values.pop(),
        "extra-category": lambda values: values.append({**deepcopy(values[0]), "id": 99, "category": "supply-chain/extra"}),
        "duplicate-category": lambda values: values.__setitem__(1, deepcopy(values[0])),
        "duplicate-analysis-id": lambda values: values[1].update({"id": values[0]["id"]}),
        "different-upload": lambda values: values[1].update({"sarif_id": "upload-2"}),
    }
    for label, mutate in list_mutations.items():
        candidate = deepcopy(base)
        mutate(candidate["analyses"])
        if not verify_proof(candidate, consumer):
            problems.append(f"scorecard proof self-test accepted {label}")
    return problems


def _sarif_selftests(consumer: dict[str, Any]) -> list[str]:
    fixture = json.loads(SARIF_FIXTURE_PATH.read_text(encoding="utf-8"))
    if verify_sarif_contract(fixture, consumer):
        return ["Scorecard SARIF fixture rejected the valid upstream exact set"]
    mutations = {
        "missing-run": lambda value: value["runs"].pop(),
        "extra-run": lambda value: value["runs"].append(deepcopy(value["runs"][0])),
        "duplicate-run": lambda value: value["runs"].__setitem__(1, deepcopy(value["runs"][0])),
        "missing-id": lambda value: value["runs"][0].pop("automationDetails"),
        "wrong-category": lambda value: value["runs"][0]["automationDetails"].update({"id": "supply-chain/other/run"}),
        "wrong-tool": lambda value: value["runs"][0]["tool"]["driver"].update({"name": "Other"}),
        "wrong-version": lambda value: value["runs"][0]["tool"]["driver"].update({"semanticVersion": "v0"}),
    }
    problems: list[str] = []
    for label, mutate in mutations.items():
        candidate = deepcopy(fixture)
        mutate(candidate)
        if not verify_sarif_contract(candidate, consumer):
            problems.append(f"Scorecard SARIF self-test accepted {label}")
    return problems


def _matrix_contract_problems(matrix: Any) -> list[str]:
    return [] if matrix == EXPECTED_MATRIX else ["Scorecard entrypoint tier/permission/hardening matrix drifted"]


def _matrix_selftests() -> list[str]:
    mutations = {
        "analysis-overgrant": (0, "permissions", {"actions": "read", "contents": "read", "id-token": "write"}),
        "analysis-private": (0, "private_execution_tier", "private-paid"),
        "analysis-fleet": (0, "runner_class", "self-hosted-fleet"),
        "analysis-no-hardening": (0, "harden_runner", {"required": False}),
        "publish-undergrant": (1, "permissions", {"actions": "read", "contents": "read"}),
        "publish-wrong-tier": (1, "public_execution_tier", "private-free"),
        "wildcard-entrypoint": (1, "workflow", ".github/workflows/public-scorecard*.yml"),
    }
    problems: list[str] = []
    for label, (index, key, value) in mutations.items():
        candidate = deepcopy(EXPECTED_MATRIX)
        candidate[index][key] = value
        if not _matrix_contract_problems(candidate):
            problems.append(f"Scorecard matrix self-test accepted {label}")
    return problems


def check() -> list[str]:
    problems: list[str] = []
    contract = strict_load(CONTRACT_PATH)
    if contract.get("schema") != "nddev-ci-scorecard-evidence/v1":
        problems.append("scorecard evidence catalog has an unknown schema")
    if contract.get("category_contract") != EXPECTED_CATEGORY_CONTRACT:
        problems.append("Scorecard upstream category authority contract drifted")
    problems += _matrix_contract_problems(contract.get("entrypoint_matrix"))
    problems += _matrix_selftests()
    matrix_workflows = {row["workflow"].rsplit("/", 1)[-1] for row in EXPECTED_MATRIX}
    if not matrix_workflows <= HARDENED_WORKFLOWS:
        problems.append("Scorecard public entrypoints are not exact Harden-Runner allowlist members")
    if any("*" in name for name in HARDENED_WORKFLOWS):
        problems.append("Harden-Runner allowlist must enumerate exact workflow filenames")
    if contract.get("attempts") != EXPECTED_ATTEMPTS:
        problems.append("Scorecard preserved product-failure receipts drifted")
    if contract.get("recovery_attempts") != EXPECTED_RECOVERY_ATTEMPTS:
        problems.append("Scorecard recovery-cycle failure receipts drifted")
    consumer = contract.get("designated_consumer")
    if consumer != EXPECTED_CONSUMER:
        problems.append("scorecard evidence consumer must be the exact owned public default-branch contract")
        consumer = EXPECTED_CONSUMER

    capabilities = strict_load(REPO_ROOT / "catalog" / "capabilities.yml").get("capabilities", [])
    scorecard_tiers = {
        row.get("id"): (row.get("public_oss"), row.get("private_free"), row.get("private_paid"))
        for row in capabilities if str(row.get("id", "")).startswith("ossf-scorecard")
    }
    if scorecard_tiers.get("ossf-scorecard") != ("free", "unavailable", "unavailable"):
        problems.append("Scorecard publish entrypoint tier classification drifted")
    if scorecard_tiers.get("ossf-scorecard-analysis") != ("conditional", "unavailable", "unavailable"):
        problems.append("Scorecard analysis alternative tier classification drifted")

    caller = load_yaml(CALLER_PATH)
    problems += _caller_contract_problems(caller, consumer)
    missing_security_events = deepcopy(caller)
    del missing_security_events["jobs"]["scorecard"]["permissions"]["security-events"]
    if not _caller_contract_problems(missing_security_events, consumer):
        problems.append("Scorecard caller self-test accepted missing security-events permission")
    missing_oidc = deepcopy(caller)
    del missing_oidc["jobs"]["scorecard"]["permissions"]["id-token"]
    if not _caller_contract_problems(missing_oidc, consumer):
        problems.append("Scorecard caller self-test accepted missing publish OIDC permission")
    json_only_caller = deepcopy(caller)
    json_only_caller["jobs"]["scorecard"]["uses"] = "./.github/workflows/public-scorecard-json.yml"
    if not _caller_contract_problems(json_only_caller, consumer):
        problems.append("Scorecard caller self-test accepted the JSON-only reusable")
    overgranted_caller = deepcopy(caller)
    overgranted_caller["jobs"]["analysis-only"]["permissions"]["id-token"] = "write"
    if not _caller_contract_problems(overgranted_caller, consumer):
        problems.append("Scorecard caller self-test accepted analysis-only OIDC overgrant")
    analysis_write_caller = deepcopy(caller)
    analysis_write_caller["jobs"]["analysis-only"]["permissions"]["security-events"] = "write"
    if not _caller_contract_problems(analysis_write_caller, consumer):
        problems.append("Scorecard caller self-test accepted analysis-only SARIF write")
    hidden_publish_caller = deepcopy(caller)
    hidden_publish_caller["jobs"]["scorecard"]["if"] = "${{ false }}"
    if not _caller_contract_problems(hidden_publish_caller, consumer):
        problems.append("Scorecard caller self-test accepted hidden conditional publish")
    dual_publish_caller = deepcopy(caller)
    dual_publish_caller["jobs"]["analysis-only"]["uses"] = "./.github/workflows/public-scorecard.yml"
    if not _caller_contract_problems(dual_publish_caller, consumer):
        problems.append("Scorecard caller self-test accepted dual publish entrypoints")
    reusable = load_yaml(REUSABLE_PATH)
    analysis_reusable = load_yaml(ANALYSIS_REUSABLE_PATH)
    reusable_call = get_on(reusable).get("workflow_call", {})
    analysis_call = get_on(analysis_reusable).get("workflow_call", {})
    publish_input = reusable_call.get("inputs", {}).get("publish_results", {})
    if publish_input != {
        "description": "Publish results to the OpenSSF Scorecard API (public repos).",
        "type": "boolean", "default": True,
    }:
        problems.append("publish_results must remain an exact typed boolean with its compatible default")
    category_input = reusable_call.get("inputs", {}).get("sarif_category", {})
    if category_input != {
        "description": (
            "Fail-closed upload fallback; upstream Scorecard runAutomationDetails "
            "define the three analysis categories."
        ),
        "type": "string", "default": "ossf-scorecard",
    }:
        problems.append("sarif_category must remain the compatible upload fallback contract")
    expected_analysis_inputs = {
        key: deepcopy(value) for key, value in reusable_call.get("inputs", {}).items()
        if key not in {"publish_results", "upload_sarif_on_forks"}
    }
    if analysis_call.get("inputs") != expected_analysis_inputs:
        problems.append("analysis entrypoint inputs drifted from the common compatible API")
    if {"publish_results", "upload_sarif_on_forks"} & set(analysis_call.get("inputs", {})):
        problems.append("analysis entrypoint must expose no publication-mode inputs")
    if reusable_call.get("outputs", {}).get("sarif_id", {}).get("value") != "${{ jobs.scorecard.outputs.sarif_id }}":
        problems.append("public-scorecard must expose the SARIF upload identifier")
    jobs = reusable.get("jobs", {})
    analysis_jobs = analysis_reusable.get("jobs", {})
    if set(jobs) != {"scorecard"} or set(analysis_jobs) != {"scorecard"}:
        problems.append("each Scorecard entrypoint must physically contain exactly one job")
    publish_job = jobs.get("scorecard", {})
    analysis_job = analysis_jobs.get("scorecard", {})
    if "if" in publish_job or "if" in analysis_job:
        problems.append("physical Scorecard entrypoint jobs must never use conditional mode selection")
    if analysis_job.get("permissions") != consumer["analysis_only_permissions"]:
        problems.append("analysis-only reusable must request only contents/actions read")
    if {"id-token", "security-events"} & set(analysis_job.get("permissions", {})):
        problems.append("analysis-only reusable must never request OIDC or Code Scanning writes")
    if publish_job.get("permissions") != consumer["required_permissions"]:
        problems.append("publish reusable must request the exact proven publication permissions")
    if publish_job.get("outputs", {}).get("sarif_id") != "${{ steps.upload-sarif.outputs.sarif-id }}":
        problems.append("publish job must bind sarif_id to the upload-sarif output")
    steps = _steps(reusable, "scorecard")
    analysis_steps = _steps(analysis_reusable, "scorecard")
    required_steps = {
        "Harden runner", "Validate public default-branch contract", "Run analysis",
        "Upload artifact", "Upload SARIF to code scanning",
    }
    if not required_steps <= steps.keys():
        problems.append(f"public-scorecard is missing required evidence steps {sorted(required_steps - steps.keys())}")
        return problems
    if "Upload SARIF to code scanning" in analysis_steps:
        problems.append("analysis-only mode must not contain a SARIF upload step")
    forbidden_analysis_steps = {"Upload artifact", "Upload SARIF to code scanning"}
    if forbidden_analysis_steps & set(analysis_steps):
        problems.append("analysis-only mode must not publish SARIF as an artifact or Code Scanning analysis")
    for mode, mode_steps in (("analysis", analysis_steps), ("publish", steps)):
        harden = mode_steps.get("Harden runner", {})
        if harden.get("uses") != HARDEN_RUNNER or harden.get("with") != {"egress-policy": "audit"}:
            problems.append(f"Scorecard {mode} hardening pin/config drifted")
        if list(mode_steps)[0] != "Harden runner" or "if" in harden:
            problems.append(f"Scorecard {mode} hardening must be unconditional and first")
    common_names = list(analysis_steps)
    publish_common_names = [name for name in steps if name not in {
        "Validate publish entrypoint", "Upload artifact", "Upload SARIF to code scanning",
    }]
    if common_names != publish_common_names:
        problems.append("Scorecard mode common-step order drifted")
    for name in common_names:
        analysis_step = deepcopy(analysis_steps[name])
        publish_step = deepcopy(steps[name])
        if name == "Run analysis":
            if analysis_step.get("with", {}).get("publish_results") is not False:
                problems.append("analysis entrypoint must hardcode publish_results: false")
            analysis_step["with"]["publish_results"] = "${{ inputs.publish_results }}"
        if analysis_step != publish_step:
            problems.append(f"Scorecard physical entrypoint common step {name!r} drifted")
    for field in ("runs-on", "timeout-minutes"):
        if analysis_job.get(field) != publish_job.get(field):
            problems.append(f"Scorecard mode {field} drifted")
    step_order = list(steps)
    if step_order[0] != "Harden runner" or not (
        step_order.index("Validate public default-branch contract")
        < step_order.index("Checkout") < step_order.index("Run analysis")
    ):
        problems.append("Scorecard hardening and event guard must run before checkout or analysis")
    try:
        guard = _embedded_guard(steps["Validate public default-branch contract"])
    except (ValueError, StopIteration) as exc:
        problems.append(f"Scorecard guard extraction failed: {exc}")
    else:
        for event in ("push", "schedule"):
            if _run_guard(guard, SCORECARD_EVENT_NAME=event) != 0:
                problems.append(f"Scorecard guard rejected supported {event} default-branch event")
        invalid = {
            "non-default-ref": {"SCORECARD_REF": "refs/heads/fixtures/test"},
            "private": {"SCORECARD_REPOSITORY_PRIVATE": "true"},
            "fork": {"SCORECARD_REPOSITORY_FORK": "true"},
            "pull-request": {"SCORECARD_EVENT_NAME": "pull_request"},
            "workflow-dispatch": {"SCORECARD_EVENT_NAME": "workflow_dispatch"},
            "ghes": {"SCORECARD_SERVER_URL": "https://github.example.com"},
            "wrong-category-shape": {"SCORECARD_SARIF_CATEGORY": "Scorecard SARIF"},
        }
        for label, values in invalid.items():
            if _run_guard(guard, **values) == 0:
                problems.append(f"Scorecard guard accepted unsupported {label}")

    analysis = steps["Run analysis"]
    if analysis.get("uses") != "ossf/scorecard-action@2d1146689b8cda280b9bc96326124645441f03bc":
        problems.append("Scorecard analysis action must retain the immutable v2.4.4 pin")
    if analysis.get("with", {}).get("results_format") != "sarif":
        problems.append("public-scorecard analysis must produce SARIF, not JSON-only evidence")
    upload = steps["Upload SARIF to code scanning"]
    if upload.get("id") != "upload-sarif":
        problems.append("Scorecard SARIF upload must expose its documented output")
    if upload.get("uses") != f"{CODEQL_REPOSITORY}/upload-sarif@{CODEQL_SHA}":
        problems.append(f"Scorecard upload-sarif action must use catalog pin {CODEQL_ACTION_VERSION}")
    if upload.get("with", {}).get("category") != "${{ inputs.sarif_category }}":
        problems.append("Scorecard SARIF upload must use the caller-visible deterministic category")
    if upload.get("continue-on-error") or analysis.get("continue-on-error"):
        problems.append("Scorecard analysis/upload failures must never be hidden")
    problems += verify_proof(contract.get("proof"), consumer)
    problems += _proof_selftests(consumer)
    problems += _sarif_selftests(consumer)
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("check_scorecard_evidence_contract: FAIL", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("check_scorecard_evidence_contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
