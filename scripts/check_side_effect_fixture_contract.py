#!/usr/bin/env python3
"""Fail-closed structure and mutation probes for event/write runtime fixtures."""
from __future__ import annotations

import copy
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from ci_workflows_tools._strict_yaml import strict_load
from ci_workflows_tools._workflow_yaml import get_on
from ci_workflows_tools.check_python_execution_contract import clean_environment

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/runtime-fixtures-event-write.yml"
LABELER = ROOT / ".github/labeler-runtime-evidence.yml"


def _run_with_fake_gh(run: str, mode: str, extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        fake_gh = fake_bin / "gh"
        fake_gh.write_text("""#!/usr/bin/env bash
set -u
if [[ " $* " == *" --include "* ]]; then
  count=0
  if [ -f "$FAKE_COUNTER" ]; then count="$(cat "$FAKE_COUNTER")"; fi
  count=$((count + 1))
  printf '%s\n' "$count" >"$FAKE_COUNTER"
  case "$FAKE_GH_MODE" in
    missing) printf 'HTTP/2.0 404 Not Found\n'; exit 1 ;;
    forbidden) printf 'HTTP/2.0 403 Forbidden\n'; exit 1 ;;
    network) printf 'connection refused\n' >&2; exit 1 ;;
    present_then_missing)
      if [ "$count" -eq 1 ]; then printf 'HTTP/2.0 200 OK\n'; exit 0; fi
      printf 'HTTP/2.0 404 Not Found\n'; exit 1 ;;
  esac
fi
if [[ " $* " == *" --method DELETE "* ]]; then
  if [ "$FAKE_GH_MODE" = label-lifecycle ]; then printf 'absent\n' >"$FAKE_LABEL_STATE"; fi
  exit 0
fi
if [ "$FAKE_GH_MODE" = label-present ]; then printf 'ci\n'; fi
if [ "$FAKE_GH_MODE" = label-lifecycle ] && [ "$(cat "$FAKE_LABEL_STATE")" = present ]; then printf 'ci\n'; fi
if [ "$FAKE_GH_MODE" = label-api-error ]; then printf 'permission denied\n' >&2; exit 1; fi
""", encoding="utf-8")
        fake_gh.chmod(0o755)
        output = root / "output"
        base_env = clean_environment()
        env = clean_environment({
            "FAKE_COUNTER": str(root / "counter"),
            "FAKE_GH_MODE": mode,
            "FAKE_LABEL_STATE": str(root / "label-state"),
            "GITHUB_OUTPUT": str(output),
            "GH_TOKEN": "fixture-token",
            "PATH": f"{fake_bin}{os.pathsep}{base_env.get('PATH', '')}",
            "RUNNER_TEMP": str(root),
            **extra_env,
        })
        if mode == "label-lifecycle":
            Path(env["FAKE_LABEL_STATE"]).write_text("present\n", encoding="utf-8")
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", run], env=env,
            text=True, capture_output=True, check=False,
        )
        if output.exists():
            result.stdout += output.read_text(encoding="utf-8")
        return result


def _job(doc: dict[str, Any], name: str) -> dict[str, Any]:
    jobs = doc.get("jobs") or {}
    value = jobs.get(name) if isinstance(jobs, dict) else None
    return value if isinstance(value, dict) else {}


def validate(doc: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    triggers = get_on(doc)
    if not isinstance(triggers, dict) or set(triggers) != {
        "push", "pull_request", "workflow_dispatch"
    }:
        problems.append("side-effect fixture must have push/PR/manual event-correct triggers")
    if doc.get("permissions") != {}:
        problems.append("side-effect fixture must deny permissions at top level")
    concurrency = doc.get("concurrency") or {}
    if concurrency.get("cancel-in-progress") is not False:
        problems.append("side-effect fixture must preserve the first run; cancellation hides evidence")

    for job_name in (
        "prepare-pr-hygiene",
        "fixture-pr-hygiene",
        "fixture-pr-hygiene-explicit",
        "cleanup-pr-hygiene",
        "evidence-pr-hygiene",
    ):
        condition = str(_job(doc, job_name).get("if", ""))
        if "github.event.pull_request.user.login != 'dependabot[bot]'" not in condition:
            problems.append(
                f"{job_name} must exclude Dependabot from owner-only side-effect evidence"
            )

    benchmark = _job(doc, "fixture-benchmark")
    if benchmark.get("permissions") != {"contents": "write"}:
        problems.append("benchmark caller must grant exactly contents:write")
    benchmark_with = benchmark.get("with") or {}
    if not benchmark_with.get("external_data_json_path"):
        problems.append("benchmark fixture must use file-backed history")
    branch = str(benchmark_with.get("history_branch", ""))
    if not branch.startswith("${{ needs.prepare-benchmark.outputs.branch"):
        problems.append("benchmark fixture must use the prepared disposable branch")

    benchmark_prepare = _job(doc, "prepare-benchmark")
    prepare_benchmark_text = str(
        (benchmark_prepare.get("steps") or [{}])[0].get("run", "")
    )
    for marker in (
        "gh api --include", "[ \"$http_status\" != 404 ]",
        "status=${http_status:-missing}", "absent before caller (HTTP 404)",
    ):
        if marker not in prepare_benchmark_text:
            problems.append(f"benchmark preparation missing probe marker {marker!r}")

    benchmark_cleanup = _job(doc, "cleanup-benchmark")
    if benchmark_cleanup.get("permissions") != {"contents": "write"}:
        problems.append("benchmark cleanup must grant exactly contents:write")
    if set(benchmark_cleanup.get("needs") or []) != {
        "prepare-benchmark", "fixture-benchmark"
    }:
        problems.append("benchmark cleanup must observe preparation and caller")
    cleanup_text = str((benchmark_cleanup.get("steps") or [{}])[0].get("run", ""))
    for marker in (
        "^runtime-evidence/benchmark-[0-9]+-[0-9]+$",
        "--method DELETE",
        "status=${status:-missing}",
        "elif [ \"$status\" = 404 ]",
        "initial ref observation failed",
        "final ref observation failed",
        "CALLER_RESULT",
        "disposable branch remains",
    ):
        if marker not in cleanup_text:
            problems.append(f"benchmark cleanup missing fail-closed marker {marker!r}")

    hygiene = _job(doc, "fixture-pr-hygiene")
    if hygiene.get("permissions") != {
        "contents": "read", "issues": "write", "pull-requests": "write"
    }:
        problems.append("PR-hygiene caller has broader or insufficient permissions")
    hygiene_with = hygiene.get("with") or {}
    expected = {"commitlint": True, "pr_title": True, "labeler": True, "stale": False}
    if any(hygiene_with.get(key) != value for key, value in expected.items()):
        problems.append("PR-hygiene fixture must run read lanes plus labeler and exclude stale")

    explicit_hygiene = _job(doc, "fixture-pr-hygiene-explicit")
    if explicit_hygiene.get("permissions") != {
        "contents": "read", "issues": "write", "pull-requests": "write"
    }:
        problems.append("explicit PR-hygiene caller has broader or insufficient permissions")
    explicit_with = explicit_hygiene.get("with") or {}
    if explicit_with.get("commitlint_config") != \
            ".github/commitlint-runtime-evidence.mjs" or any(
                explicit_with.get(key) is not value for key, value in {
                    "commitlint": True, "pr_title": False,
                    "labeler": False, "stale": False,
                }.items()
            ):
        problems.append("explicit PR-hygiene fixture must isolate config-path evidence")

    pr_cleanup = _job(doc, "cleanup-pr-hygiene")
    if pr_cleanup.get("permissions") != {"pull-requests": "write"}:
        problems.append("PR cleanup must grant exactly pull-requests:write")
    if set(pr_cleanup.get("needs") or []) != {
        "prepare-pr-hygiene", "fixture-pr-hygiene"
    }:
        problems.append("PR cleanup must observe preparation and caller")
    pr_text = str((pr_cleanup.get("steps") or [{}])[0].get("run", ""))
    for marker in (
        "labeler did not apply the expected ci label",
        "labeler new-labels output does not prove a ci write",
        "labeler all-labels output does not contain ci",
        "--method DELETE",
        "prior label state was not restored",
        "CALLER_RESULT",
        "[ \"$HAD_LABEL\" != false ]",
    ):
        if marker not in pr_text:
            problems.append(f"PR cleanup missing fail-closed marker {marker!r}")
    pr_env = ((pr_cleanup.get("steps") or [{}])[0].get("env") or {})
    if pr_env.get("LABELER_NEW_LABELS") != \
            "${{ needs.fixture-pr-hygiene.outputs.labeler_new_labels }}" or \
            pr_env.get("LABELER_ALL_LABELS") != \
            "${{ needs.fixture-pr-hygiene.outputs.labeler_all_labels }}":
        problems.append("PR cleanup must consume both documented labeler outputs")

    prepare_pr = _job(doc, "prepare-pr-hygiene")
    prepare_pr_text = str((prepare_pr.get("steps") or [{}])[0].get("run", ""))
    for marker in (
        "refusing proof because ci label already exists",
        "ci label absent before caller",
        "--paginate",
        "exit 1",
    ):
        if marker not in prepare_pr_text:
            problems.append(f"PR preparation missing observable-write marker {marker!r}")

    for evidence_job, caller, cleanup in (
        ("evidence-benchmark", "fixture-benchmark", "cleanup-benchmark"),
        ("evidence-pr-hygiene", "fixture-pr-hygiene", "cleanup-pr-hygiene"),
    ):
        evidence = _job(doc, evidence_job)
        expected_needs = {caller, cleanup}
        if evidence_job == "evidence-pr-hygiene":
            expected_needs.add("fixture-pr-hygiene-explicit")
        if set(evidence.get("needs") or []) != expected_needs:
            problems.append(f"{evidence_job} must depend on caller and cleanup")
        env = ((evidence.get("steps") or [{}, {}])[-1].get("env") or {})
        if caller not in str(env.get("PROVES")) or cleanup not in str(env.get("GUARDS")):
            problems.append(f"{evidence_job} must bind proof to its cleanup guard")
        if evidence_job == "evidence-pr-hygiene" and \
                "fixture-pr-hygiene-explicit" not in str(env.get("PROVES")):
            problems.append("PR evidence must include the explicit-config caller")
    return problems


def check() -> list[str]:
    doc = strict_load(WORKFLOW)
    problems = validate(doc)
    labeler = strict_load(LABELER)
    label_paths = set(
        (((labeler.get("ci") or [{}])[0].get("changed-files") or [{}])[0]
         .get("any-glob-to-any-file") or [])
    ) if isinstance(labeler, dict) else set()
    expected_label_paths = {"**/*"}
    if label_paths != expected_label_paths:
        problems.append(
            "runtime labeler must match every PR; no-match is a negative probe, not a path skip"
        )
    probes: list[tuple[str, Callable[[dict[str, Any]], object]]] = [
        ("broad benchmark permission", lambda d: _job(d, "fixture-benchmark")["permissions"].update({"issues": "write"})),
        ("stale false-green", lambda d: _job(d, "fixture-pr-hygiene")["with"].update({"stale": True})),
        ("missing cleanup guard", lambda d: (_job(d, "evidence-benchmark")["steps"][-1]["env"].pop("GUARDS"))),
        ("cleanup without caller", lambda d: _job(d, "cleanup-pr-hygiene").update({"needs": ["prepare-pr-hygiene"]})),
        ("missing explicit config proof", lambda d: _job(d, "evidence-pr-hygiene")["steps"][-1]["env"].update({"PROVES": '{"fixture-pr-hygiene":"pr-hygiene.yml"}'})),
        ("pre-existing label accepted", lambda d: _job(d, "prepare-pr-hygiene")["steps"][0].update({"run": "echo had_label=true >>\"$GITHUB_OUTPUT\""})),
        ("API errors treated as missing ref", lambda d: _job(d, "cleanup-benchmark")["steps"][0].update({"run": "gh api missing >/dev/null 2>&1 || echo missing"})),
        ("prepare API errors treated as missing ref", lambda d: _job(d, "prepare-benchmark")["steps"][0].update({"run": "echo branch=unsafe >>\"$GITHUB_OUTPUT\""})),
        ("label outputs ignored", lambda d: _job(d, "cleanup-pr-hygiene")["steps"][0]["env"].pop("LABELER_NEW_LABELS")),
    ]
    for label, mutate in probes:
        candidate = copy.deepcopy(doc)
        mutate(candidate)
        if not validate(candidate):
            problems.append(f"negative probe accepted {label}")

    cleanup_run = str(
        (_job(doc, "cleanup-benchmark").get("steps") or [{}])[0].get("run", "")
    )
    cleanup_env = {
        "BRANCH": "runtime-evidence/benchmark-123-1",
        "CALLER_RESULT": "success",
        "REPOSITORY": "NDDev-Technocracy-OpenNetwork/ci-workflows",
    }
    for mode in ("missing", "present_then_missing"):
        if _run_with_fake_gh(cleanup_run, mode, cleanup_env).returncode != 0:
            problems.append(f"benchmark cleanup rejected valid {mode} ref lifecycle")
    for mode in ("forbidden", "network"):
        if _run_with_fake_gh(cleanup_run, mode, cleanup_env).returncode == 0:
            problems.append(f"benchmark cleanup accepted {mode} probe failure as absence")
    if _run_with_fake_gh(
        cleanup_run, "missing", {**cleanup_env, "CALLER_RESULT": "failure"}
    ).returncode == 0:
        problems.append("benchmark cleanup hid a failed caller")

    prepare_benchmark_run = str(
        (_job(doc, "prepare-benchmark").get("steps") or [{}])[0].get("run", "")
    )
    prepare_benchmark_env = {
        "REPOSITORY": "NDDev-Technocracy-OpenNetwork/ci-workflows",
        "RUN_ID": "123",
        "RUN_ATTEMPT": "1",
    }
    missing = _run_with_fake_gh(
        prepare_benchmark_run, "missing", prepare_benchmark_env
    )
    if missing.returncode != 0 or \
            "branch=runtime-evidence/benchmark-123-1" not in missing.stdout:
        problems.append("benchmark preparation rejected an explicit 404")
    for mode in ("forbidden", "network", "present_then_missing"):
        if _run_with_fake_gh(
            prepare_benchmark_run, mode, prepare_benchmark_env
        ).returncode == 0:
            problems.append(f"benchmark preparation accepted {mode} as absent")

    prepare_pr_run = str(
        (_job(doc, "prepare-pr-hygiene").get("steps") or [{}])[0].get("run", "")
    )
    prepare_env = {"PR_NUMBER": "132", "REPOSITORY": "NDDev-Technocracy-OpenNetwork/ci-workflows"}
    absent = _run_with_fake_gh(prepare_pr_run, "label-absent", prepare_env)
    if absent.returncode != 0 or "had_label=false" not in absent.stdout:
        problems.append("PR preparation rejected an absent label")
    if _run_with_fake_gh(
        prepare_pr_run, "label-present", prepare_env
    ).returncode == 0:
        problems.append("PR preparation accepted a pre-existing label as observable write")

    cleanup_pr_run = str(
        (_job(doc, "cleanup-pr-hygiene").get("steps") or [{}])[0].get("run", "")
    )
    cleanup_pr_env = {
        "CALLER_RESULT": "success",
        "HAD_LABEL": "false",
        "LABELER_ALL_LABELS": "docs, ci",
        "LABELER_NEW_LABELS": "ci",
        "PR_NUMBER": "132",
        "REPOSITORY": "NDDev-Technocracy-OpenNetwork/ci-workflows",
    }
    if _run_with_fake_gh(
        cleanup_pr_run, "label-lifecycle", cleanup_pr_env
    ).returncode != 0:
        problems.append("PR cleanup rejected documented outputs and present-to-absent lifecycle")
    for label, overrides, mode in (
        ("no-match", {"LABELER_NEW_LABELS": ""}, "label-lifecycle"),
        ("all-labels missing", {"LABELER_ALL_LABELS": "docs"}, "label-lifecycle"),
        ("missing action output", {"LABELER_NEW_LABELS": "missing"}, "label-lifecycle"),
        ("API permission error", {}, "label-api-error"),
        ("skipped caller", {"CALLER_RESULT": "skipped"}, "label-lifecycle"),
    ):
        if _run_with_fake_gh(
            cleanup_pr_run, mode, {**cleanup_pr_env, **overrides}
        ).returncode == 0:
            problems.append(f"PR cleanup accepted false-green {label}")
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("check_side_effect_fixture_contract: FAIL", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("check_side_effect_fixture_contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
