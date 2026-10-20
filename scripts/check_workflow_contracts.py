#!/usr/bin/env python3
"""Reusable-workflow contract: every workflow except the self workflows
(`ci.yml`, `release.yml`) must be reusable (`on: workflow_call`). The self
workflows must NOT be reusable, and `ci.yml` must expose the `ci-gate` job that
aggregates focused self-CI as truthful advisory evidence. Caller-provided command runners
must also fail on the first failing command instead of returning the status of
only the final command. The Go pack's history-depth input remains a typed,
backward-compatible pass-through to checkout.
The private-free workflows used on persistent self-hosted fleets must also
isolate ambient global Git configuration before checkout so runner-owned
Authorization headers cannot combine with actions/checkout's scoped token.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import io
import json
import os
import re
import subprocess
import tempfile
import zipfile
import shlex
import shutil
import signal
import ssl
import threading
import time
import sys
from pathlib import Path

from ci_workflows_tools.check_python_execution_contract import clean_environment
from ci_workflows_tools._runners import is_standard_hosted, resolve_runner_labels
from ci_workflows_tools._workflow_yaml import SELF_WORKFLOWS, get_on, is_reusable, load_yaml, workflow_files


def check() -> list[str]:
    problems: list[str] = []
    for path in workflow_files():
        doc = load_yaml(path)
        reusable = is_reusable(doc)
        if path.name in SELF_WORKFLOWS:
            if reusable:
                problems.append(f"{path.name}: self workflow must not be `on: workflow_call`")
        elif not reusable:
            problems.append(f"{path.name}: reusable workflow missing `on: workflow_call`")

    isolated_checkout_workflows = {
        "actionlint.yml",
        "cross-platform-smoke.yml",
        "private-static.yml",
        "public-codeql.yml",
        "secret-scan.yml",
        "zizmor-no-sarif.yml",
    }
    expected_isolation = """set -euo pipefail
umask 077
isolated_config="$RUNNER_TEMP/nddev-ci-global.gitconfig"
: > "$isolated_config"
printf 'GIT_CONFIG_GLOBAL=%s\\n' "$isolated_config" >> "$GITHUB_ENV"
"""
    workflow_root = workflow_files()[0].parent

    rust_supply_chain = load_yaml(workflow_root / "rust-supply-chain.yml")
    rust_call = get_on(rust_supply_chain).get("workflow_call", {})
    rust_inputs = rust_call.get("inputs", {}) if isinstance(rust_call, dict) else {}
    deny_runner = rust_inputs.get("deny_runner", {}) if isinstance(rust_inputs, dict) else {}
    rust_jobs = rust_supply_chain.get("jobs", {}) or {}
    if (
        not isinstance(deny_runner, dict)
        or deny_runner.get("type") != "string"
        or deny_runner.get("default") != ""
        or (rust_jobs.get("deny", {}) or {}).get("runs-on")
        != "${{ inputs.deny_runner || inputs.runner }}"
        or (rust_jobs.get("audit", {}) or {}).get("runs-on") != "${{ inputs.runner }}"
        or (rust_jobs.get("machete", {}) or {}).get("runs-on") != "${{ inputs.runner }}"
    ):
        problems.append(
            "rust-supply-chain.yml: cargo-deny must retain its optional "
            "deny_runner while audit and machete retain the ordinary runner"
        )

    convergence = load_yaml(workflow_root / "dependabot-catalog-convergence.yml")
    convergence_job = (convergence.get("jobs", {}) or {}).get("synchronize", {})
    convergence_permissions = convergence_job.get("permissions", {})
    if not isinstance(convergence_permissions, dict) or (
        convergence_permissions.get("actions") != "write"
        or convergence_permissions.get("contents") != "write"
        or convergence_permissions.get("pull-requests") != "write"
    ):
        problems.append(
            "dependabot-catalog-convergence.yml: synchronize must retain only "
            "actions, contents, and pull-requests write permissions"
        )
    convergence_steps = convergence_job.get("steps", []) or []
    approval_step = next(
        (
            step
            for step in convergence_steps
            if isinstance(step, dict)
            and step.get("name") == "Approve exact candidate workflow runs"
        ),
        {},
    )
    approval_env = approval_step.get("env", {})
    approval_run = approval_step.get("run", "")
    if not isinstance(approval_env, dict) or approval_env.get("CANDIDATE_SHA") != (
        "${{ steps.commit.outputs.candidate_sha }}"
    ):
        problems.append(
            "dependabot-catalog-convergence.yml: run approval must bind to the "
            "candidate SHA emitted by the commit step"
        )
    required_approval_guards = (
        "event=pull_request&head_sha=${CANDIDATE_SHA}",
        'select(.status == "action_required")',
        "/actions/runs/${run_id}/approve",
    )
    if not isinstance(approval_run, str) or any(
        guard not in approval_run for guard in required_approval_guards
    ):
        problems.append(
            "dependabot-catalog-convergence.yml: approval must select only "
            "action-required pull-request runs for the exact candidate SHA"
        )

    codeql = load_yaml(workflow_root / "public-codeql.yml")
    codeql_on = get_on(codeql)
    codeql_call = codeql_on.get("workflow_call", {}) if isinstance(codeql_on, dict) else {}
    codeql_inputs = codeql_call.get("inputs", {}) if isinstance(codeql_call, dict) else {}
    dependabot_runner = codeql_inputs.get("dependabot_runner", {}) if isinstance(codeql_inputs, dict) else {}
    if not isinstance(dependabot_runner, dict) or dependabot_runner.get("type") != "string" or dependabot_runner.get("default") != "ubuntu-latest":
        problems.append(
            "public-codeql.yml: dependabot_runner must remain a string with the "
            "public-safe ubuntu-latest default"
        )
    codeql_job = (codeql.get("jobs", {}) or {}).get("codeql", {})
    expected_codeql_runner = "${{ github.event_name == 'pull_request' && github.event.pull_request.user.login == 'dependabot[bot]' && inputs.dependabot_runner || inputs.runner }}"
    if codeql_job.get("runs-on") != expected_codeql_runner:
        problems.append(
            "public-codeql.yml: runner selection must explicitly isolate "
            "Dependabot-authored pull requests"
        )
    boundary_steps = [
        step for step in (codeql_job.get("steps", []) or [])
        if isinstance(step, dict) and step.get("name") == "Record CodeQL trust boundary"
    ]
    if len(boundary_steps) != 1 or (boundary_steps[0].get("env") or {}).get("SELECTED_RUNNER") != expected_codeql_runner:
        problems.append(
            "public-codeql.yml: selected Dependabot/ordinary runner boundary must "
            "be emitted as job evidence"
        )
    if "pull_request_target" in (workflow_root / "public-codeql.yml").read_text(encoding="utf-8"):
        problems.append("public-codeql.yml: Dependabot analysis must never use pull_request_target")

    # The consolidated scanner requires four output paths before it can create
    # empty fail-safe evidence. Changing the shared script without wiring every
    # caller broke the first public-product consumer ring at shell expansion,
    # before any scanner ran. Hold the paid/private bundle to the same complete
    # evidence contract as the free bundle.
    nddev_bundle = load_yaml(workflow_root / "nddev-security-bundle.yml")
    bundle_steps = (nddev_bundle.get("jobs", {}).get("security-bundle", {}).get("steps", []) or [])
    scan_steps = [step for step in bundle_steps if isinstance(step, dict) and step.get("name") == "Run consolidated security gates"]
    required_evidence_env = {
        "ZIZMOR_SARIF_PATH",
        "OSV_SARIF_PATH",
        "GITLEAKS_SARIF_PATH",
        "ACTIONLINT_LOG_PATH",
    }
    if len(scan_steps) != 1 or not required_evidence_env.issubset(set((scan_steps[0].get("env") or {}).keys())):
        problems.append(
            "nddev-security-bundle.yml: consolidated scan must supply all four "
            "actionlint/Zizmor/OSV/Gitleaks evidence paths"
        )
    evidence_uploads = [
        step for step in bundle_steps
        if isinstance(step, dict)
        and step.get("name") == "Upload redacted security evidence"
        and str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    if len(evidence_uploads) != 1 or (evidence_uploads[0].get("with") or {}).get("retention-days") != 1:
        problems.append(
            "nddev-security-bundle.yml: redacted evidence must upload exactly once with one-day retention"
        )

    # Transport failure is recoverable only when complete evidence survives.
    for filename, variable in (
        ("nddev-security-bundle.yml", "NDDEV_SECURITY_BUNDLE_SCRIPT"),
        ("private-security-bundle-free.yml", "PRIVATE_SECURITY_BUNDLE_SCRIPT"),
    ):
        workflow = load_yaml(workflow_root / filename)
        job = workflow["jobs"]["security-bundle"]
        steps = job["steps"]
        upload = next((step for step in steps if step.get("id") == "evidence_artifact"), {})
        fallback = next((step for step in steps if step.get("name") == "Preserve security evidence in the run log"), {})
        if (upload.get("continue-on-error") is not True
                or fallback.get("if") != "${{ !cancelled() && steps.evidence_artifact.outcome == 'failure' }}"
                or fallback.get("continue-on-error")
                or f'$(dirname "${variable}")/security_evidence_to_log.sh' not in fallback.get("run", "")):
            problems.append(f"{filename}: upload failure must require the exact checked log fallback")
        scan = next(step for step in steps if step.get("id") == "scan")
        if filename == "private-security-bundle-free.yml" and scan.get("continue-on-error"):
            problems.append(f"{filename}: scanner failure must remain blocking")
        if filename == "nddev-security-bundle.yml" and not any(
                step.get("if") == "${{ steps.scan.outcome == 'failure' }}"
                and step.get("run") == "exit 1" for step in steps):
            problems.append(f"{filename}: deferred scanner failure must still be enforced")
    problems.extend(_probe_native_deny(rust_jobs.get("deny", {})))
    problems.extend(_probe_security_evidence(workflow_root.parents[1]))
    problems.extend(_probe_security_bundle_source(workflow_root.parents[1]))
    problems.extend(_probe_gitleaks_history(workflow_root.parents[1]))

    for filename in sorted(isolated_checkout_workflows):
        workflow = load_yaml(workflow_root / filename)
        jobs = workflow.get("jobs", {}) or {}
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            steps = job.get("steps", []) or []
            checkout_indexes = [
                index
                for index, step in enumerate(steps)
                if isinstance(step, dict)
                and str(step.get("uses", "")).startswith("actions/checkout@")
            ]
            if not checkout_indexes:
                continue
            isolation_indexes = [
                index
                for index, step in enumerate(steps)
                if isinstance(step, dict)
                and step.get("name") == "Isolate global Git config"
                and step.get("shell") == "bash"
                and step.get("run") == expected_isolation
            ]
            if len(isolation_indexes) != 1 or isolation_indexes[0] >= min(
                checkout_indexes
            ):
                problems.append(
                    f"{filename}: job {job_name!r} must run the canonical global "
                    "Git-config isolation exactly once before checkout"
                )

    # This repository is public. Every local reusable call that exposes a
    # runner selector must choose a standard hosted runner explicitly; relying
    # on the reusable's private-consumer default can route public PR code to the
    # private self-hosted fleet when that default changes or remains stale.
    #
    # "Standard hosted", not the literal string `ubuntu-latest`. The rule used
    # to demand that one label, which enforced the property by accident and
    # forbade `macos-latest` — a standard hosted runner, unmetered on public
    # repositories exactly like ubuntu. That made `swift-ci.yml`, whose whole
    # purpose is macOS, impossible to call from this repository's own fixture
    # estate. What matters is that the choice is explicit and lands on a runner
    # every account resolves and nobody is billed for; _runners.py holds that
    # definition, shared with check_examples.py.
    for filename in sorted(SELF_WORKFLOWS):
        caller = load_yaml(workflow_root / filename)
        for job_name, job in (caller.get("jobs", {}) or {}).items():
            if not isinstance(job, dict):
                continue
            use = str(job.get("uses", ""))
            prefix = "./.github/workflows/"
            if not use.startswith(prefix):
                continue
            reusable_path = workflow_root / use.removeprefix(prefix)
            reusable = load_yaml(reusable_path)
            reusable_on = get_on(reusable)
            call = (
                reusable_on.get("workflow_call", {})
                if isinstance(reusable_on, dict)
                else {}
            )
            inputs = call.get("inputs", {}) if isinstance(call, dict) else {}
            if not isinstance(inputs, dict) or "runner" not in inputs:
                continue
            with_values = job.get("with", {}) or {}
            chosen = with_values.get("runner") if isinstance(with_values, dict) else None
            if chosen is None:
                problems.append(
                    f"{filename}: public self-call job {job_name!r} must select a "
                    "standard hosted runner explicitly — inheriting the reusable's "
                    "default can route public pull-request code to a private fleet"
                )
                continue
            labels = resolve_runner_labels(chosen, job)
            if labels is None:
                problems.append(
                    f"{filename}: public self-call job {job_name!r} selects runner "
                    f"{chosen!r}, which this check cannot resolve to concrete "
                    "labels. Use a literal or `${{ matrix.KEY }}` with the values "
                    "listed in the job's own strategy.matrix — an unresolvable "
                    "expression is not evidence that the runner is free"
                )
                continue
            for label in labels:
                if not is_standard_hosted(label):
                    problems.append(
                        f"{filename}: public self-call job {job_name!r} can run on "
                        f"{label!r}, which is not a standard hosted runner. Standard "
                        "runners are unmetered on public repositories in all three "
                        "operating systems; larger runners are billed there from the "
                        "first minute, and a self-hosted label makes a forked pull "
                        "request remote code execution on that hardware"
                    )

    ci = load_yaml((workflow_files()[0].parent / "ci.yml"))
    jobs = ci.get("jobs", {}) or {}
    if "ci-gate" not in jobs:
        problems.append("ci.yml: missing `ci-gate` aggregate job (advisory evidence, not a required merge context)")

    # Run 34091935173 failed because this job committed workflow files and
    # GITHUB_TOKEN cannot push them without `workflows` permission. Catalog
    # follow-up must not expand App permissions; it commits catalog only.
    convergence = (workflow_files()[0].parent / "dependabot-catalog-convergence.yml").read_text(
        encoding="utf-8"
    )
    if "git add .github/workflows" in convergence:
        problems.append(
            "dependabot-catalog-convergence.yml: must not git-add workflow files; "
            "GITHUB_TOKEN cannot push them without workflows permission"
        )
    if "--catalog-only" not in convergence:
        problems.append(
            "dependabot-catalog-convergence.yml: must run sync_action_catalog.py "
            "--catalog-only so it does not rewrite workflow files"
        )
    if "git add catalog docs/generated" not in convergence:
        problems.append(
            "dependabot-catalog-convergence.yml: catalog convergence commit must "
            "add only catalog and docs/generated"
        )

    go_ci = load_yaml((workflow_files()[0].parent / "go-ci.yml"))
    go_on = get_on(go_ci)
    go_call = go_on.get("workflow_call", {}) if isinstance(go_on, dict) else {}
    go_inputs = go_call.get("inputs", {}) if isinstance(go_call, dict) else {}
    fetch_depth = go_inputs.get("fetch_depth", {}) if isinstance(go_inputs, dict) else {}
    if not isinstance(fetch_depth, dict) or (
        fetch_depth.get("type") != "number" or fetch_depth.get("default") != 1
    ):
        problems.append(
            "go-ci.yml: fetch_depth must remain a number with the "
            "backward-compatible default 1"
        )
    cache = go_inputs.get("cache", {}) if isinstance(go_inputs, dict) else {}
    if not isinstance(cache, dict) or (
        cache.get("type") != "boolean" or cache.get("default") is not True
    ):
        problems.append(
            "go-ci.yml: cache must remain a boolean with the "
            "backward-compatible default true"
        )
    go_steps = go_ci.get("jobs", {}).get("go", {}).get("steps", [])
    checkout = next(
        (
            step
            for step in go_steps
            if isinstance(step, dict) and step.get("name") == "Checkout"
        ),
        {},
    )
    checkout_with = checkout.get("with", {})
    actual_depth = (
        checkout_with.get("fetch-depth")
        if isinstance(checkout_with, dict)
        else None
    )
    if actual_depth != "${{ inputs.fetch_depth }}":
        problems.append(
            "go-ci.yml: Checkout must pass fetch_depth through to actions/checkout"
        )
    setup_go = next(
        (
            step
            for step in go_steps
            if isinstance(step, dict) and step.get("name") == "Set up Go"
        ),
        {},
    )
    setup_go_with = setup_go.get("with", {})
    actual_cache = (
        setup_go_with.get("cache")
        if isinstance(setup_go_with, dict)
        else None
    )
    if actual_cache != "${{ inputs.cache }}":
        problems.append(
            "go-ci.yml: Set up Go must pass cache through to actions/setup-go"
        )

    private_static = load_yaml((workflow_files()[0].parent / "private-static.yml"))
    static_steps = private_static.get("jobs", {}).get("static", {}).get("steps", [])
    steps_by_name = {step.get("name"): step for step in static_steps if isinstance(step, dict)}
    fail_fast_commands = {
        "Run install command": 'bash -euo pipefail -c "$INSTALL_COMMAND"',
        "Run validation": 'bash -euo pipefail -c "$VALIDATION_COMMAND"',
    }
    for name, expected in fail_fast_commands.items():
        actual = steps_by_name.get(name, {}).get("run")
        if actual != expected:
            problems.append(
                f"private-static.yml: {name!r} must use the fail-fast runner {expected!r}"
            )

    # The fail-fast contract above was written for private-static.yml alone
    # while 47 other caller-command sites across 23 workflows ran plain
    # `bash -c "$CMD"`. That inner shell inherits nothing from the step's
    # shell, so a caller passing `lint; test` or `build | tee log` got exit 0
    # from a failing first command — the reusable reported success for a build
    # that did not succeed. The rule is the same everywhere a caller's command
    # is executed, so it is enforced everywhere.
    problems += _fail_fast_caller_commands()
    problems += _balanced_caller_commands()
    problems += _runner_selftest()
    problems += _job_defaults_pin_the_shell()
    problems += _started_runs_are_preserved()
    problems += _download_attempts_are_bounded()
    problems += _probe_tool_cache_download(workflow_root.parents[1])
    problems += _probe_tool_cache_runner_copy(workflow_root.parents[1])
    return problems


BARE_BASH_C = re.compile(r'\bbash -c "(\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)"')
# Inputs whose value is handed to a shell by the reusable that receives it.
COMMAND_INPUT = re.compile(r"(^|_)commands?$")
DOWNLOAD_RETRY = re.compile(r"--retry[ =]([0-9]+)")


def _download_attempts_are_bounded() -> list[str]:
    """curl's retry count excludes the initial request; two means three attempts."""
    problems: list[str] = []
    paths = [*workflow_files(), *Path("scripts").glob("*.sh"),
             *Path("actions").glob("*/tool-cache.sh")]
    for path in paths:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            for match in DOWNLOAD_RETRY.finditer(line):
                if int(match.group(1)) > 2:
                    problems.append(
                        f"{path}:{lineno}: download retry count {match.group(1)} "
                        "exceeds two retries / three total attempts"
                    )
    return problems



def _probe_tool_cache_download(root: Path) -> list[str]:
    """Real curl/TLS, with only the destination and time scale changed in the fixture."""
    problems: list[str] = []
    script = root / "actions/tool-cache/tool-cache.sh"
    curl = shutil.which("curl")
    if not curl:
        return ["tool-cache HTTPS probe requires curl"]
    payload = b"immutable tool fixture\n"
    try:
        with tempfile.TemporaryDirectory(prefix="tool-cache-probe-") as directory:
            target = Path(directory)
            cert, key = target / "cert.pem", target / "key.pem"
            generated = subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                 "-subj", "/CN=github.com", "-addext", "subjectAltName=DNS:github.com",
                 "-keyout", str(key), "-out", str(cert)],
                env=clean_environment(), capture_output=True, text=True, timeout=10)
            if generated.returncode:
                return ["tool-cache HTTPS fixture certificate generation failed"]
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.minimum_version = ssl.TLSVersion.TLSv1_2
            tls.load_cert_chain(cert, key)
            bindir = target / "bin"
            bindir.mkdir()
            wrapper = bindir / "curl"
            wrapper.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + r'''
args=()
while (( $# )); do
  case "$1" in
    --max-time|--retry-max-time|--connect-timeout)
      option=$1; value=$2; shift 2
      [[ "$value" =~ ^[1-9][0-9]*$ ]]
      value=$(( value / 100 )); (( value > 0 )) || value=1
      args+=("$option" "$value") ;;
    https://*)
      [[ "$1" == 'https://github.com/fixture/tool.tar.gz' ]]
      args+=("$1"); shift ;;
    *) args+=("$1"); shift ;;
  esac
done
printf '%s\0' "${args[@]}" > "$PROBE_ARGV"
trust=()
if [[ "$PROBE_MODE" != untrusted ]]; then trust=(--cacert "$PROBE_CERT"); fi
exec "$PROBE_CURL" --disable "${args[@]}" --noproxy '*' \
  --connect-to "github.com:443:127.0.0.1:$PROBE_PORT" "${trust[@]}"
''')
            wrapper.chmod(0o700)
            for mode in ("stall-once", "stall-always", "checksum", "oversize", "untrusted"):
                case = target / mode
                case.mkdir()
                output = case / "artifact"
                stopped = threading.Event()
                requests: list[str] = []

                class Handler(http.server.BaseHTTPRequestHandler):
                    def log_message(self, *_args):
                        pass

                    def do_GET(self):
                        requests.append(self.path)
                        body = b"wrong checksum\n" if mode == "checksum" else payload
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Connection", "close")
                        self.end_headers()
                        try:
                            if mode == "stall-always" or (mode == "stall-once" and len(requests) == 1):
                                self.wfile.write(body[:1])
                                self.wfile.flush()
                                stopped.wait(12)
                            else:
                                self.wfile.write(body)
                        except OSError:
                            pass  # A bounded client timeout closes its own socket.

                server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                server.daemon_threads = True
                server.socket = tls.wrap_socket(server.socket, server_side=True)
                thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
                thread.start()
                env = clean_environment({
                    "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
                    "HOME": str(case), "RUNNER_TEMP": str(case), "GITHUB_OUTPUT": str(case / "outputs"),
                    "NDDEV_TOOL_CACHE_URL": "https://github.com/fixture/tool.tar.gz",
                    "NDDEV_TOOL_CACHE_SHA256": hashlib.sha256(payload).hexdigest(),
                    "NDDEV_TOOL_CACHE_OUTPUT": str(output),
                    "NDDEV_TOOL_CACHE_MAX_BYTES": "8" if mode == "oversize" else "64",
                    "PROBE_CURL": curl, "PROBE_PORT": str(server.server_port),
                    "PROBE_CERT": str(cert), "PROBE_MODE": mode, "PROBE_ARGV": str(case / "argv"),
                })
                # Exercise upstream only; never consume ambient cache credentials.
                for name in tuple(env):
                    if name.startswith(("AWS_", "SCCACHE_", "NDDEV_CACHE_")):
                        del env[name]
                started = time.monotonic()
                try:
                    process = subprocess.Popen(["bash", str(script)], cwd=root, env=env,
                                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                               text=True, start_new_session=True)
                    try:
                        stdout, stderr = process.communicate(timeout=15)
                    except subprocess.TimeoutExpired:
                        # Only this fixture's process group, including its curl child.
                        os.killpg(process.pid, signal.SIGKILL)
                        process.communicate()
                        raise
                    result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
                    elapsed = time.monotonic() - started
                finally:
                    stopped.set()
                    server.shutdown()
                    thread.join(timeout=2)
                    server.server_close()
                print(f"tool-cache HTTPS {mode}: exit={result.returncode}, "
                      f"requests={len(requests)}, elapsed={elapsed:.2f}s")
                if mode == "stall-once":
                    if result.returncode or len(requests) != 2 or not output.exists() or output.read_bytes() != payload:
                        problems.append("tool-cache: a timed-out first download must retry and install only verified bytes")
                    outputs = (case / "outputs").read_text() if (case / "outputs").exists() else ""
                    if "source=upstream\n" not in outputs or f"bytes={len(payload)}\n" not in outputs:
                        problems.append("tool-cache: recovered download lacks verified output evidence")
                elif result.returncode == 0 or output.exists() or (case / "outputs").exists():
                    problems.append(f"tool-cache: {mode} must fail without installing or reporting success")
                if mode == "stall-always" and (result.returncode != 28 or not 2 <= len(requests) <= 3 or elapsed > 12):
                    problems.append("tool-cache: permanent stall must exhaust bounded attempts and retain timeout failure")
                rejection = {"checksum": 1, "oversize": 63, "untrusted": 60}.get(mode)
                if rejection is not None and result.returncode != rejection:
                    problems.append(f"tool-cache: {mode} failed for the wrong reason ({result.returncode})")
                if mode == "untrusted" and requests:
                    problems.append("tool-cache: untrusted TLS must fail before any HTTP request")
                args = (case / "argv").read_bytes().split(b"\0")[:-1]
                for flag, value in ((b"--proto", b"=https"), (b"--max-filesize", env["NDDEV_TOOL_CACHE_MAX_BYTES"].encode())):
                    if flag not in args or args[args.index(flag) + 1] != value:
                        problems.append(f"tool-cache: fixture lost {flag.decode()} protection")
                for flag, maximum in ((b"--max-time", 3), (b"--retry-max-time", 6), (b"--retry", 2)):
                    if flag not in args or not 0 < int(args[args.index(flag) + 1]) <= maximum:
                        problems.append(f"tool-cache: {flag.decode()} must retain its finite upper bound")
                if b"--tlsv1.2" not in args or b"--insecure" in args or b"-k" in args:
                    problems.append("tool-cache: upstream TLS verification must stay enabled")
                if list(case.glob("nddev-tool-cache.*")):
                    problems.append("tool-cache: failed or successful attempt left its partial scratch artifact")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        problems.append(f"tool-cache HTTPS probe failed: {error}")
    return problems


def _probe_tool_cache_runner_copy(root: Path) -> list[str]:
    """A self-hosted runner's verified copy replaces the download; nothing else may."""
    problems: list[str] = []
    script = root / "actions/tool-cache/tool-cache.sh"
    payload = b"immutable tool fixture\n"
    digest = hashlib.sha256(payload).hexdigest()
    url = "https://github.com/fixture/tool.tar.gz"
    key = hashlib.sha256(b"nddev-tool-cache-v1\0" + url.encode() + b"\0" + digest.encode()).hexdigest()
    # mode: (runner environment, seeded entry, upstream reachable, expected source, entry after)
    cases = {
        "store": ("self-hosted", None, True, "upstream", payload),
        "hit": ("self-hosted", payload, False, "runner", payload),
        "poisoned": ("self-hosted", b"poisoned tool fixture\n", True, "upstream", payload),
        "github-hosted": ("github-hosted", payload, True, "upstream", payload),
    }
    try:
        with tempfile.TemporaryDirectory(prefix="tool-cache-runner-") as directory:
            target = Path(directory)
            bindir = target / "bin"
            bindir.mkdir()
            wrapper = bindir / "curl"
            # Upstream stands in for the TLS path the download probe already covers.
            wrapper.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + r"""
printf 'call\n' >> "$PROBE_CALLS"
[[ "$PROBE_UPSTREAM" == reachable ]] || exit 7
while (( $# )); do
  if [[ "$1" == --output ]]; then cp -- "$PROBE_PAYLOAD" "$2"; exit 0; fi
  shift
done
exit 2
""")
            wrapper.chmod(0o700)
            served = target / "payload"
            served.write_bytes(payload)
            for mode, (environment, seeded, reachable, expected, after) in cases.items():
                case = target / mode
                case.mkdir()
                tools = case / "tool-cache"
                entry = tools / "nddev-tool-cache" / "v1" / key
                if seeded is not None:
                    entry.parent.mkdir(parents=True)
                    entry.write_bytes(seeded)
                tools.mkdir(exist_ok=True)
                output = case / "artifact"
                env = clean_environment({
                    "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
                    "HOME": str(case), "RUNNER_TEMP": str(case), "GITHUB_OUTPUT": str(case / "outputs"),
                    "RUNNER_ENVIRONMENT": environment, "RUNNER_TOOL_CACHE": str(tools),
                    "NDDEV_TOOL_CACHE_URL": url, "NDDEV_TOOL_CACHE_SHA256": digest,
                    "NDDEV_TOOL_CACHE_OUTPUT": str(output), "NDDEV_TOOL_CACHE_MAX_BYTES": "64",
                    "PROBE_CALLS": str(case / "calls"), "PROBE_PAYLOAD": str(served),
                    "PROBE_UPSTREAM": "reachable" if reachable else "offline",
                })
                for name in tuple(env):
                    if name.startswith(("AWS_", "SCCACHE_", "NDDEV_CACHE_")):
                        del env[name]
                result = subprocess.run(["bash", str(script)], cwd=root, env=env,
                                        capture_output=True, text=True, timeout=15)
                calls = len((case / "calls").read_text().splitlines()) if (case / "calls").exists() else 0
                outputs = (case / "outputs").read_text() if (case / "outputs").exists() else ""
                print(f"tool-cache runner {mode}: exit={result.returncode}, upstream calls={calls}")
                if result.returncode or not output.exists() or output.read_bytes() != payload:
                    problems.append(f"tool-cache runner {mode}: must install the verified artifact")
                if f"source={expected}\n" not in outputs:
                    problems.append(f"tool-cache runner {mode}: source must be {expected}")
                if calls != (0 if expected == "runner" else 1):
                    problems.append(f"tool-cache runner {mode}: upstream calls {calls} do not match the source")
                if not entry.exists() or entry.read_bytes() != after:
                    problems.append(f"tool-cache runner {mode}: the runner entry must hold only verified bytes")
                if [p.name for p in entry.parent.iterdir() if p.name != key]:
                    problems.append(f"tool-cache runner {mode}: left a staged copy beside the entry")
                if list(case.glob("nddev-tool-cache.*")):
                    problems.append(f"tool-cache runner {mode}: left its scratch artifact")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        problems.append(f"tool-cache runner probe failed: {error}")
    return problems


def _started_runs_are_preserved() -> list[str]:
    """Every queued or started run must retain its own concurrency identity."""
    problems: list[str] = []
    paths = [*workflow_files(), *sorted(Path("examples").rglob("*.yml"))]
    for path in paths:
        workflow = load_yaml(path)
        concurrency = workflow.get("concurrency")
        if concurrency is None:
            continue
        group = concurrency.get("group") if isinstance(concurrency, dict) else None
        if (
            not isinstance(concurrency, dict)
            or concurrency.get("cancel-in-progress") is not False
            or not isinstance(group, str)
            or "github.run_id" not in group
        ):
            problems.append(
                f"{path}: concurrency must use github.run_id and set "
                "cancel-in-progress to literal false; GitHub retains only one "
                "pending run in a shared group, so ref grouping can erase evidence"
            )
    return problems


def _balanced_caller_commands() -> list[str]:
    """A command passed to a reusable must be shell-parseable.

    An unbalanced quote in one of these is invisible to every check this
    repository had: the YAML is valid, so actionlint passes; the value is just a
    string, so no schema complains; and the failure surfaces only when a runner
    reaches `bash: unexpected EOF while looking for matching \"`. That is a
    real minute of CI and a confusing log to reach a typo. `shlex` settles it in
    microseconds.

    This checks parseability, not safety: these strings are caller-authored by
    design, and the reusables run them through `bash -euo pipefail -c` on
    purpose.
    """
    problems: list[str] = []
    for path in workflow_files():
        for job_name, job in (load_yaml(path).get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            for key, value in (job.get("with") or {}).items():
                if not isinstance(value, str) or not COMMAND_INPUT.search(str(key)):
                    continue
                try:
                    shlex.split(value)
                except ValueError as exc:
                    problems.append(
                        f"{path.name}: job {job_name!r} input {key!r} is not "
                        f"shell-parseable ({exc}): {value[:70]!r}"
                    )
    return problems


def _fail_fast_caller_commands() -> list[str]:
    """Any `bash -c "$VAR"` running a caller command must set -euo pipefail."""
    problems: list[str] = []
    for path in workflow_files():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            match = BARE_BASH_C.search(line)
            if match:
                problems.append(
                    f"{path.name}:{lineno}: `bash -c \"{match.group(1)}\"` does not "
                    "fail fast; use `bash -euo pipefail -c` so a caller command "
                    "like `a; b` or `a | b` cannot report success after failing"
                )
    return problems


def _runner_selftest() -> list[str]:
    """The shared runner predicate must keep saying no to the expensive answers.

    Widening this rule from the literal `ubuntu-latest` to "standard hosted" is
    only safe while the three things it was protecting against still fail:
    a larger runner (hosted, but billed on public repositories from the first
    minute), a self-hosted fleet label (a forked pull request becomes remote
    code execution on that hardware), and a missing or non-string value.
    """
    expectations = {
        # standard hosted, all three operating systems, latest and pinned
        "ubuntu-latest": True, "macos-latest": True, "windows-latest": True,
        "ubuntu-24.04": True, "macos-14": True, "windows-2022": True,
        # larger runners: hosted, never free
        "ubuntu-latest-8-cores": False, "ubuntu-latest-4-cores": False,
        "macos-latest-large": False, "windows-latest-xlarge": False,
        # somebody's fleet
        "nddev-linux-fast": False, "nddev-linux-release": False,
        "self-hosted": False, "amsterdam": False,
        # nothing at all
        "": False,
    }
    problems = []
    for label, expected in expectations.items():
        if is_standard_hosted(label) is not expected:
            problems.append(
                f"_runners.is_standard_hosted({label!r}) is "
                f"{is_standard_hosted(label)}, expected {expected}"
            )
    for value in (None, 123, ["ubuntu-latest"]):
        if is_standard_hosted(value):
            problems.append(
                f"_runners.is_standard_hosted({value!r}) accepted a non-string"
            )
    # Matrix resolution: a self-call may pick its runner from its own matrix,
    # but the check must see the values behind the expression. The dangerous
    # cases are an include: entry smuggling a fleet label past a clean `os:`
    # list, and an expression pointing at a matrix that does not exist — which
    # must read as "cannot tell", never as "fine".
    matrix_cases = [
        ({"strategy": {"matrix": {"os": ["ubuntu-latest", "windows-latest",
                                         "macos-latest"]}}},
         ["ubuntu-latest", "windows-latest", "macos-latest"]),
        ({"strategy": {"matrix": {"os": ["ubuntu-latest"],
                                  "include": [{"os": "nddev-linux-fast"}]}}},
         ["ubuntu-latest", "nddev-linux-fast"]),
        ({"strategy": {"matrix": {"other": ["ubuntu-latest"]}}}, None),
        ({}, None),
        ({"strategy": {"matrix": {"os": []}}}, None),
    ]
    for job, expected in matrix_cases:
        got = resolve_runner_labels("${{ matrix.os }}", job)
        if got != expected:
            problems.append(
                f"resolve_runner_labels(matrix.os, {job!r}) is {got!r}, "
                f"expected {expected!r}"
            )
    # A literal still resolves to itself; an unknown expression must not.
    if resolve_runner_labels("ubuntu-latest", {}) != ["ubuntu-latest"]:
        problems.append("resolve_runner_labels lost a literal label")
    if resolve_runner_labels("${{ inputs.runner }}", {}) is not None:
        problems.append(
            "resolve_runner_labels treated an unresolvable expression as decidable"
        )
    return problems


def _job_defaults_pin_the_shell() -> list[str]:
    """A job-level `defaults.run` must name its shell.

    A job-level `defaults.run` REPLACES the workflow-level one rather than
    merging with it key by key. The documentation describes per-name
    precedence, so this is easy to get wrong and impossible to see on Linux:
    twenty-six jobs here declared `defaults: run: working-directory:` and
    thereby dropped the `shell: bash` their own file declared three lines
    above. Linux hid it, because bash is the default there anyway.

    Windows does not hide it. A fixture run of python-ci on windows-latest
    executed its steps under PowerShell, where a bash line continuation and a
    `>>` redirect are syntax errors and `${VAR}` expands to nothing — the job
    printed "(requested )" and then failed. cross-platform-smoke.yml, the one
    workflow written for three operating systems, already set `shell: bash` on
    every individual step, which is the previous author reaching the same
    conclusion for one file.

    So: any job that declares `defaults.run` at all must also pin `shell`.
    """
    problems: list[str] = []
    for path in workflow_files():
        workflow = load_yaml(path)
        for job_name, job in (workflow.get("jobs", {}) or {}).items():
            if not isinstance(job, dict):
                continue
            run_defaults = ((job.get("defaults") or {}).get("run") or {})
            if not run_defaults:
                continue
            has_run_step = any(
                isinstance(step, dict) and "run" in step
                for step in (job.get("steps") or [])
            )
            if has_run_step and "shell" not in run_defaults:
                problems.append(
                    f"{path.name}: job {job_name!r} declares defaults.run without "
                    "`shell`. A job-level defaults.run replaces the workflow-level "
                    "one instead of merging, so this silently drops the file's own "
                    "`shell: bash` and every run step becomes PowerShell on Windows"
                )
    return problems



def _probe_security_evidence(root: Path) -> list[str]:
    """Round-trip actual fallback bytes, and reject incomplete or unsafe inputs."""
    script = root / "scripts" / "security_evidence_to_log.sh"
    names = ("private-security-actionlint.log", "private-security-zizmor.sarif",
             "private-security-osv.sarif", "private-security-gitleaks.sarif")
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="security-evidence-probe-") as directory:
        target = Path(directory)
        summary = target / "summary.md"
        summary.touch()
        expected = {name: (b"::error::literal diagnostic\n" if name.endswith(".log")
                           else b'{"version":"2.1.0","runs":[]}\n') for name in names}
        env = clean_environment({"RUNNER_TEMP": str(target), "GITHUB_REPOSITORY": "example-org/example",
               "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
               "GITHUB_STEP_SUMMARY": str(summary)})
        def run():
            return subprocess.run(["bash", str(script)], env=env, capture_output=True,
                                  text=True, timeout=10, check=False)
        for name, raw in expected.items():
            (target / name).write_bytes(raw)
        try:
            first, second = run(), run()
            if first.returncode or second.returncode or first.stdout != second.stdout:
                return ["security evidence: valid inputs must produce one deterministic archive"]
            lines = first.stdout.splitlines()
            digest = lines[0].split()[1]
            end = lines.index("SECURITY_EVIDENCE_V1_END " + digest)
            raw = base64.b64decode("".join(lines[1:end]), validate=True)
            if hashlib.sha256(raw).hexdigest() != digest or len(raw) != int(lines[0].split()[2]):
                problems.append("security evidence: archive digest or length mismatch")
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if set(archive.namelist()) != set(names) | {"SHA256SUMS", "identity.json"}:
                    problems.append("security evidence: archive lost or added a report")
                for name, content in expected.items():
                    if archive.read(name) != content:
                        problems.append("security evidence: report bytes changed")
                for line in archive.read("SHA256SUMS").decode().splitlines():
                    sha, name = line.split("  ")
                    if hashlib.sha256(archive.read(name)).hexdigest() != sha:
                        problems.append("security evidence: member digest mismatch")
                if json.loads(archive.read("identity.json"))["run_id"] != "123":
                    problems.append("security evidence: run identity missing")
            victim = target / names[1]
            for case in ("missing", "symlink", "fifo", "oversized", "invalid-sarif"):
                victim.unlink(missing_ok=True)
                if case == "symlink": victim.symlink_to(target / names[2])
                elif case == "fifo": os.mkfifo(victim)
                elif case == "oversized":
                    with victim.open("wb") as file: file.truncate(4 * 1024 * 1024 + 1)
                elif case == "invalid-sarif": victim.write_text("{}")
                failed = run()
                if failed.returncode == 0 or "SECURITY_EVIDENCE_V1_BEGIN" in failed.stdout:
                    problems.append(f"security evidence: accepted {case} report")
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired, zipfile.BadZipFile) as error:
            problems.append(f"security evidence probe failed: {error}")
    return problems


def _security_fixture_process(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=30, check=False)


def _security_fixture_repo(target: Path, env: dict[str, str]) -> str:
    target.mkdir()
    for args in (["git", "init", "--quiet"],
                 ["git", "config", "uploadpack.allowFilter", "true"],
                 ["git", "config", "uploadpack.allowAnySHA1InWant", "true"]):
        result = _security_fixture_process(args, target, env)
        if result.returncode:
            raise ValueError(result.stderr)
    (target / "fixture.txt").write_text("synthetic history fixture\n")
    (target / "scripts").mkdir()
    (target / "scripts" / "run_private_security_bundle.sh").write_text("#!/bin/bash\nexit 0\n")
    for args in (["git", "add", "."],
                 ["git", "commit", "--quiet", "--no-gpg-sign", "-m", "fixture"],
                 ["git", "rev-parse", "HEAD"]):
        result = _security_fixture_process(args, target, env)
        if result.returncode:
            raise ValueError(result.stderr)
    return result.stdout.strip()


def _security_fixture_env(target: Path) -> dict[str, str]:
    config = target / "gitconfig"
    config.touch()
    return clean_environment({
        "GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Fixture", "GIT_COMMITTER_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_TERMINAL_PROMPT": "0", "GH_TOKEN": "synthetic-fixture-only",
        "RUNNER_TEMP": str(target),
    })


def _probe_security_bundle_source(root: Path) -> list[str]:
    """Execute both YAML steps twice against one runner temp with stale files."""
    problems: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="security-source-probe-") as directory:
            target = Path(directory)
            env = _security_fixture_env(target)
            fixture = target / "origin"
            oid = _security_fixture_repo(fixture, env)
            rewritten = _security_fixture_process(
                ["git", "config", "--global", f"url.{fixture.as_uri()}.insteadOf",
                 "https://github.com/example-org/fixture.git"], target, env)
            if rewritten.returncode:
                return ["security source fixture could not configure its local origin"]
            for filename, prefix, variable in (
                ("private-security-bundle-free.yml", "private-security-bundle-source", "PRIVATE_SECURITY_BUNDLE_SCRIPT"),
                ("nddev-security-bundle.yml", "nddev-security-bundle-source", "NDDEV_SECURITY_BUNDLE_SCRIPT"),
            ):
                workflow = load_yaml(root / ".github" / "workflows" / filename)
                step = next(s for s in workflow["jobs"]["security-bundle"]["steps"]
                            if s.get("name") == "Fetch exact called-workflow implementation")
                stale = target / prefix
                stale.mkdir()
                sentinel = stale / "fixture.txt"
                sentinel.write_text("preserve stale runner file\n")
                output = target / (prefix + ".env")
                run_env = dict(env, GITHUB_ENV=str(output), WORKFLOW_REPOSITORY="example-org/fixture",
                               WORKFLOW_SHA=oid, FETCH_TOKEN="synthetic-fixture-only",
                               GITHUB_TOKEN="synthetic-fixture-only")
                for _ in range(2):
                    result = _security_fixture_process(["bash", "-c", step["run"]], target, run_env)
                    if result.returncode:
                        problems.append(f"{filename}: exact checkout failed with stale runner files")
                        break
                else:
                    paths = [line.split("=", 1)[1] for line in output.read_text().splitlines()
                             if line.startswith(variable + "=")]
                    if len(paths) != 2 or len(set(paths)) != 2 or not all(Path(p).is_file() for p in paths):
                        problems.append(f"{filename}: repeated calls did not get distinct complete checkouts")
                    original = re.sub(r'^source_root=.*$', f'source_root="$RUNNER_TEMP/{prefix}"',
                                      step["run"], count=1, flags=re.MULTILINE)
                    failed = _security_fixture_process(["bash", "-c", original], target, run_env)
                    if failed.returncode == 0:
                        problems.append(f"{filename}: shared-directory negative control did not fail")
                if sentinel.read_text() != "preserve stale runner file\n":
                    problems.append(f"{filename}: stale runner file was modified")
    except (OSError, ValueError, KeyError, StopIteration, subprocess.TimeoutExpired) as error:
        problems.append(f"security source probe failed: {error}")
    return problems


def _probe_gitleaks_history(root: Path) -> list[str]:
    """Hydrate a real partial clone; reject missing history without fake green."""
    problems: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="security-history-probe-") as directory:
            target = Path(directory)
            env = _security_fixture_env(target)
            fixture = target / "origin"
            _security_fixture_repo(fixture, env)
            helper = root / "scripts" / "prepare_gitleaks_history.sh"
            for case in ("available", "unavailable"):
                checkout = target / case
                cloned = _security_fixture_process(
                    ["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout",
                     fixture.as_uri(), str(checkout)], target, env)
                if cloned.returncode:
                    return ["gitleaks history fixture could not create a partial clone"]
                missing = _security_fixture_process(
                    ["git", "rev-list", "--objects", "--missing=print", "HEAD"], checkout, env)
                if not any(line.startswith("?") for line in missing.stdout.splitlines()):
                    return ["gitleaks history fixture did not contain missing blobs"]
                if case == "unavailable":
                    _security_fixture_process(["git", "remote", "set-url", "origin",
                                               (target / "absent.git").as_uri()], checkout, env)
                result = _security_fixture_process(["bash", str(helper), "ref-history"], checkout, env)
                if (result.returncode == 0) != (case == "available"):
                    problems.append(f"gitleaks history: wrong outcome for {case} promisor origin")
                if case == "available":
                    after = _security_fixture_process(
                        ["git", "rev-list", "--objects", "--missing=print", "HEAD"], checkout, env)
                    if any(line.startswith("?") for line in after.stdout.splitlines()):
                        problems.append("gitleaks history: successful hydration left missing blobs")
                stored = _security_fixture_process(
                    ["git", "config", "--local", "--get-regexp", "extraheader"], checkout, env)
                if stored.stdout or "synthetic-fixture-only" in result.stdout + result.stderr:
                    problems.append("gitleaks history: authorization escaped its process environment")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        problems.append(f"gitleaks history probe failed: {error}")
    return problems


def _probe_native_deny(job: dict) -> list[str]:
    """Run the shipped argv adapter; preserve failures and literal input data."""
    steps = job.get("steps", [])
    step = next((s for s in steps if s.get("name") == "Run cargo-deny"), {})
    install = next((s for s in steps if s.get("name") == "Install cargo-deny"), {})
    if not step.get("run") or step.get("shell") != "bash":
        return ["rust-supply-chain: cargo-deny needs a native bash adapter"]
    if (install.get("with", {}) or {}).get("tool") != "cargo-deny@0.20.2":
        return ["rust-supply-chain: retain the reviewed cargo-deny version"]
    if any("cargo-deny-action" in s.get("uses", "") for s in steps):
        return ["rust-supply-chain: cargo-deny must not require Docker pulls"]
    problems = []
    try:
        with tempfile.TemporaryDirectory(prefix="native-deny-probe-") as directory:
            root = Path(directory)
            executable = root / "cargo"
            executable.write_text('#!/bin/sh\nprintf "%s\\0" "$@" > "$DENY_PROBE_ARGV"\nexit "$DENY_PROBE_EXIT"\n')
            executable.chmod(0o700)
            recorded = root / "argv"
            env = clean_environment({
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "DENY_DIRECTORY": "workspace with spaces",
                "DENY_ARGUMENTS": '--all-features --config "policy with spaces.toml"',
                "DENY_COMMAND": 'check bans licenses advisories sources "$(touch injected)" "`touch injected2`"',
                "DENY_PROBE_ARGV": str(recorded),
            })
            expected = ["deny", "--manifest-path", "workspace with spaces/Cargo.toml",
                        "--all-features", "--config", "policy with spaces.toml", "check",
                        "bans", "licenses", "advisories", "sources", "$(touch injected)",
                        "`touch injected2`"]
            for code in (0, 37):
                result = subprocess.run(["bash", "-c", step["run"]], cwd=root,
                                        env={**env, "DENY_PROBE_EXIT": str(code)},
                                        capture_output=True, text=True, timeout=10)
                if result.returncode != code:
                    problems.append(f"rust-supply-chain: native deny did not preserve exit {code}")
                actual = recorded.read_bytes().split(b"\0")[:-1] if recorded.exists() else []
                if actual != [arg.encode() for arg in expected]:
                    problems.append("rust-supply-chain: native deny changed argv boundaries")
                if (root / "injected").exists() or (root / "injected2").exists():
                    problems.append("rust-supply-chain: native deny evaluated input as shell")
            recorded.unlink(missing_ok=True)
            result = subprocess.run(["bash", "-c", step["run"]], cwd=root,
                                    env={**env, "DENY_ARGUMENTS": '"unterminated', "DENY_PROBE_EXIT": "0"},
                                    capture_output=True, text=True, timeout=10)
            if result.returncode == 0 or recorded.exists():
                problems.append("rust-supply-chain: malformed arguments reached cargo")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        problems.append(f"rust-supply-chain native deny probe failed: {error}")
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("check_workflow_contracts: FAIL", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("check_workflow_contracts: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
