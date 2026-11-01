#!/usr/bin/env python3
"""Every third-party `uses:` must be pinned to a full 40-char commit SHA with a
version comment. Local reusable calls (`./.github/...`) are exempt.

A called workflow additionally may not reach a local *action* through `./`.
`./` resolves against the caller's workspace, never against the repository
holding the workflow, so `uses: ./actions/x` inside a `workflow_call` workflow
fails at job setup for every cross-repository consumer. This exemption used to
skip all `./` refs as "local reusable workflow", which is how five such
references shipped across 0.1.21..0.1.23 and broke both the CI-feedback path
and the private security bundle for every real caller.
"""
from __future__ import annotations

import re
import sys

from ci_workflows_tools._workflow_yaml import is_reusable, load_yaml, workflow_files

# `uses: owner/repo[/path]@<40-hex>  # vX.Y.Z`
USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<ref>\S+)(?P<rest>.*)$")
SHA_RE = re.compile(r"@[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
# The comment must say *which release* the SHA is, because that is the only
# human-readable half of the pin: a reviewer diffing a Dependabot bump reads the
# comment, and a bare `#` or `# bumped` both satisfied the old presence-only
# test. A semantic release version or an ISO date for an untagged upstream
# identifies stable inputs. An unreleased development input must instead name
# its exact commit, equal to the immutable ref, rather than inventing a release.
# A monorepo tags each component with a prefix (`nddev-cicd-v0.6.20`); that tag
# is the release name, so the prefix is accepted in front of the version.
PIN_COMMENT_RE = re.compile(r"#\s*((?:[a-z][a-z0-9]*(?:-[a-z0-9]+)*-)?v?\d+\.\d+(?:\.\d+)?[\w.+-]*|\d{4}-\d{2}-\d{2})\b")


DEVELOPMENT_COMMENT_RE = re.compile(r"^\s*#\s*commit:([0-9a-f]{40})(?:\s|$)")

# `./.github/workflows/name.yml` is a same-repository reusable *workflow* call,
# which GitHub resolves against this repository. Anything else behind `./` is a
# local action path and is resolved in the caller's workspace instead.
LOCAL_WORKFLOW_RE = re.compile(r"^\./\.github/workflows/[A-Za-z0-9._-]+\.ya?ml$")


def supported_pin_comment(ref: str, comment: str) -> bool:
    if re.match(r"^\s*#\s*commit:", comment):
        match = DEVELOPMENT_COMMENT_RE.search(comment)
        return match is not None and match.group(1) == ref.rsplit("@", 1)[-1]
    return PIN_COMMENT_RE.search(comment) is not None


def check() -> list[str]:
    problems: list[str] = _selftest()
    for path in workflow_files():
        try:
            reusable = is_reusable(load_yaml(path))
        except Exception as exc:  # a malformed workflow is another check's finding
            problems.append(f"{path.name}: cannot read workflow triggers: {exc}")
            reusable = False
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = USES_RE.match(line)
            if not m:
                continue
            ref = m.group("ref").strip().strip("'\"")
            rest = m.group("rest")
            where = f"{path.name}:{lineno}"
            if ref.startswith("./"):
                if reusable and not LOCAL_WORKFLOW_RE.match(ref):
                    problems.append(
                        f"{where}: a called workflow cannot reach a local action through "
                        f"`./`; it resolves in the caller's workspace, not this "
                        f"repository. Name and pin the repository "
                        f"(owner/repo/path@<sha>): {ref}"
                    )
                continue  # local reusable workflow call
            if ref.startswith("docker://"):
                if not DIGEST_RE.search(ref):
                    problems.append(f"{where}: docker image not digest-pinned: {ref}")
                continue
            if "@" not in ref:
                problems.append(f"{where}: action not pinned (no @ref): {ref}")
                continue
            if not SHA_RE.search(ref):
                problems.append(f"{where}: action not pinned to a 40-char SHA: {ref}")
                continue
            if "#" not in rest:
                problems.append(f"{where}: SHA pin missing a `# vX.Y.Z` version comment: {ref}")
            elif not supported_pin_comment(ref, rest):
                problems.append(
                    f"{where}: SHA pin comment must name the release "
                    f"(`# vX.Y.Z`, or `# YYYY-MM-DD` for an upstream that tags no "
                    f"releases; or # commit:<same full SHA>), got {rest.strip()!r}: {ref}"
                )
    return problems


def _selftest() -> list[str]:
    """Accept the real forms in this tree; reject a comment that says nothing."""
    problems: list[str] = []
    for good in ("  # v7.0.1", "  # v2.20.0", "  # 2024-09-19", "  # v0.36.0",
                 "  # v12.3114", "  # v4.1.1  (attest storage record)"):
        if PIN_COMMENT_RE.search(good) is None:
            problems.append(f"check_pinned_actions self-test: rejected {good.strip()!r}")
    for bad in ("  #", "  # bumped", "  # see PR", "  # latest", "  #  "):
        if PIN_COMMENT_RE.search(bad) is not None:
            problems.append(f"check_pinned_actions self-test: accepted {bad.strip()!r}")
    development_sha = "a" * 40
    development_ref = "example/action@" + development_sha
    if not supported_pin_comment(development_ref, " # commit:" + development_sha):
        problems.append("check_pinned_actions self-test: rejected exact development commit")
    for comment in (
        " # commit:" + "b" * 40,
        " # commit:" + "a" * 7,
        " # commit:" + "a" * 41,
        " # commit:wrong # v1.0.0",
    ):
        if supported_pin_comment(development_ref, comment):
            problems.append("check_pinned_actions self-test: accepted mismatched development identity")
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("check_pinned_actions: FAIL", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("check_pinned_actions: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
