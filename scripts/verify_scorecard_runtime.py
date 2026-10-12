#!/usr/bin/env python3
"""Verify a live default-branch Scorecard SARIF run through GitHub APIs.

This is deliberately not part of the offline gate. It turns provider state into
an immutable receipt only after checking the caller run, reusable provenance,
required step conclusions, and accepted Code Scanning analysis independently.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from typing import Any

import yaml

from ci_workflows_tools._strict_yaml import strict_load  # noqa: E402
from ci_workflows_tools._workflow_yaml import REPO_ROOT  # noqa: E402
from ci_workflows_tools.check_scorecard_evidence_contract import verify_proof, verify_sarif_contract  # noqa: E402
from ci_workflows_tools.check_python_execution_contract import clean_environment  # noqa: E402


def fail(message: str) -> None:
    print(f"verify_scorecard_runtime: {message}", file=sys.stderr)
    raise SystemExit(1)


def api(endpoint: str) -> Any:
    result = subprocess.run(
        ["gh", "api", endpoint], cwd=REPO_ROOT,
        env=clean_environment(inherit=("GH_HOST", "GH_TOKEN")), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        fail(f"GitHub API {endpoint!r} failed: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        fail(f"GitHub API {endpoint!r} returned invalid JSON: {exc}")


def api_bytes(endpoint: str) -> bytes:
    result = subprocess.run(
        ["gh", "api", endpoint], cwd=REPO_ROOT,
        env=clean_environment(inherit=("GH_HOST", "GH_TOKEN")),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        fail(f"GitHub API {endpoint!r} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def artifact_sarif(repo: str, run_id: int, consumer: dict[str, Any]) -> tuple[int, str]:
    artifacts = api(f"repos/{repo}/actions/runs/{run_id}/artifacts").get("artifacts") or []
    matches = [item for item in artifacts if item.get("name") == "scorecard-results"]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        fail(f"expected one live scorecard-results artifact, found {len(matches)}")
    artifact = matches[0]
    archive = api_bytes(f"repos/{repo}/actions/artifacts/{artifact['id']}/zip")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = bundle.infolist()
            if len(members) != 1 or members[0].filename != "results.sarif":
                fail("Scorecard artifact must contain only results.sarif")
            mode = members[0].external_attr >> 16
            if mode and (mode & 0o170000) not in {0, 0o100000}:
                fail("Scorecard artifact results.sarif is not a regular file")
            raw = bundle.read(members[0])
    except (zipfile.BadZipFile, KeyError) as exc:
        fail(f"Scorecard artifact is not a valid exact archive: {exc}")
    try:
        sarif_log = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"Scorecard artifact results.sarif is invalid JSON: {exc}")
    sarif_problems = verify_sarif_contract(sarif_log, consumer)
    if sarif_problems:
        for problem in sarif_problems:
            print(f"  - {problem}", file=sys.stderr)
        fail("uploaded Scorecard artifact violates the upstream multi-run contract")
    return artifact["id"], hashlib.sha256(raw).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--repo", default="NDDev-OpenNetwork/ci-workflows")
    args = parser.parse_args()

    contract = strict_load(REPO_ROOT / "catalog" / "scorecard-evidence.yml")
    consumer = contract["designated_consumer"]
    if args.repo != consumer["repository"]:
        fail(f"{args.repo!r} is not the designated consumer {consumer['repository']!r}")
    repository = api(f"repos/{args.repo}")
    if repository.get("private") is not False or repository.get("fork") is not False:
        fail("designated consumer is not a public non-fork repository")
    if repository.get("default_branch") != consumer["default_branch"]:
        fail("designated consumer default branch drifted from the contract")

    run = api(f"repos/{args.repo}/actions/runs/{args.run_id}")
    expected_ref = f"refs/heads/{consumer['default_branch']}"
    expected_reusable = (
        f"{args.repo}/{consumer['reusable_workflow']}@{run.get('head_sha')}"
    )
    expected_analysis_reusable = (
        f"{args.repo}/{consumer['analysis_reusable_workflow']}@{run.get('head_sha')}"
    )
    run_expected = {
        "event": "push",
        "head_branch": consumer["default_branch"],
        "path": consumer["caller_workflow"],
        "conclusion": "success",
    }
    for key, value in run_expected.items():
        if run.get(key) != value:
            fail(f"run {key} is {run.get(key)!r}, expected {value!r}")
    if run.get("head_repository", {}).get("full_name") != args.repo:
        fail("run head repository is not the designated consumer")
    referenced = run.get("referenced_workflows") or []
    exact_refs = [item for item in referenced if item.get("path") == expected_reusable]
    if len(exact_refs) != 1 or exact_refs[0].get("sha") != run.get("head_sha"):
        fail("run did not load the local SARIF reusable from its exact caller SHA")
    exact_analysis_refs = [
        item for item in referenced if item.get("path") == expected_analysis_reusable
    ]
    if len(exact_analysis_refs) != 1 or exact_analysis_refs[0].get("sha") != run.get("head_sha"):
        fail("run did not load the physical analysis reusable from its exact caller SHA")

    jobs = api(f"repos/{args.repo}/actions/runs/{args.run_id}/jobs").get("jobs") or []
    matching_jobs = [job for job in jobs if job.get("name") == "scorecard / OSSF Scorecard (publish)"]
    if len(matching_jobs) != 1:
        fail(f"expected one Scorecard SARIF job, found {len(matching_jobs)}")
    job = matching_jobs[0]
    if job.get("conclusion") != "success":
        fail(f"Scorecard SARIF job concluded {job.get('conclusion')!r}")
    steps = {step.get("name"): step.get("conclusion") for step in job.get("steps") or []}
    required_steps = {
        "Validate public default-branch contract": "success",
        "Run analysis": "success",
        "Upload artifact": "success",
        "Upload SARIF to code scanning": "success",
    }
    for name, conclusion in required_steps.items():
        if steps.get(name) != conclusion:
            fail(f"required step {name!r} concluded {steps.get(name)!r}, expected success")
    analysis_only_jobs = [
        candidate for candidate in jobs
        if candidate.get("name") == "analysis-only / OSSF Scorecard (analysis only)"
    ]
    if len(analysis_only_jobs) != 1:
        fail(f"expected one analysis-only job, found {len(analysis_only_jobs)}")
    analysis_only_job = analysis_only_jobs[0]
    if analysis_only_job.get("conclusion") != "success":
        fail(f"analysis-only job concluded {analysis_only_job.get('conclusion')!r}")
    analysis_only_steps = {
        step.get("name"): step.get("conclusion")
        for step in analysis_only_job.get("steps") or []
    }
    if analysis_only_steps.get("Run analysis") != "success":
        fail("analysis-only Scorecard step did not execute successfully")
    forbidden_runtime_steps = {"Upload artifact", "Upload SARIF to code scanning"}
    if forbidden_runtime_steps & set(analysis_only_steps):
        fail("analysis-only job unexpectedly contains a publication step")

    artifact_id, artifact_digest = artifact_sarif(args.repo, run["id"], consumer)
    analyses = api(f"repos/{args.repo}/code-scanning/analyses?per_page=100")
    candidates = [item for item in analyses if (
        item.get("tool", {}).get("name") == consumer["sarif_tool"]["name"]
        and str(item.get("created_at", "")) >= str(run.get("run_started_at", ""))
        and str(item.get("created_at", "")) <= str(run.get("updated_at", ""))
    )]
    analysis_receipts = []
    for candidate in candidates:
        analysis = api(f"repos/{args.repo}/code-scanning/analyses/{candidate['id']}")
        analysis_receipts.append({
            "id": analysis["id"], "url": analysis["url"],
            "analysis_key": analysis["analysis_key"], "ref": analysis["ref"],
            "commit_sha": analysis["commit_sha"], "category": analysis["category"],
            "tool": {
                "name": analysis.get("tool", {}).get("name"),
                "version": analysis.get("tool", {}).get("version"),
                "guid": analysis.get("tool", {}).get("guid"),
            },
            "created_at": analysis["created_at"], "sarif_id": analysis["sarif_id"],
            "error": analysis.get("error"),
        })
    analysis_receipts.sort(key=lambda item: item["category"])
    reusable_path = REPO_ROOT / consumer["reusable_workflow"]
    analysis_reusable_path = REPO_ROOT / consumer["analysis_reusable_workflow"]
    digest = hashlib.sha256(reusable_path.read_bytes()).hexdigest()
    analysis_digest = hashlib.sha256(analysis_reusable_path.read_bytes()).hexdigest()
    proof = {
        "repository": args.repo,
        "caller_sha": run["head_sha"],
        "caller_ref": expected_ref,
        "caller_workflow": consumer["caller_workflow"],
        "event": run["event"],
        "run_id": run["id"],
        "run_url": run["html_url"],
        "job_id": job["id"],
        "job_url": f"{run['html_url']}/job/{job['id']}",
        "analysis_only_job_id": analysis_only_job["id"],
        "analysis_only_job_url": f"{run['html_url']}/job/{analysis_only_job['id']}",
        "analysis_only_step": analysis_only_steps["Run analysis"],
        "analysis_step": steps["Run analysis"],
        "upload_step": steps["Upload SARIF to code scanning"],
        "reusable_sha": exact_refs[0]["sha"],
        "reusable_digest": digest,
        "reusable_workflow": consumer["reusable_workflow"],
        "analysis_reusable_sha": exact_analysis_refs[0]["sha"],
        "analysis_reusable_digest": analysis_digest,
        "analysis_reusable_workflow": consumer["analysis_reusable_workflow"],
        "artifact_id": artifact_id,
        "artifact_sha256": artifact_digest,
        "run_started_at": run["run_started_at"],
        "run_completed_at": run["updated_at"],
        "analyses": analysis_receipts,
    }
    problems = verify_proof(proof, consumer)
    if problems:
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        fail("constructed proof failed the canonical contract")
    print(yaml.safe_dump({"proof": proof}, sort_keys=False).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
