#!/usr/bin/env python3
"""Pinning an action by SHA does not pin what that action calls.

`check_pinned_actions.py` proves every `uses:` written here is a full commit SHA.
That is one level deep. A composite action's own `action.yml` may name further
actions by tag, and GitHub resolves the **entire** nested graph at job setup --
so under the repository control "require actions to be pinned to a full-length
commit SHA", which `docs/00` and the consumer-adoption skill both recommend, the
job is refused before a single step runs:

    The action actions/cache@v5 is not allowed in NDDev-Technocracy-Sandbox/ci-workflows
    because all actions must be pinned to a full-length commit SHA.

That is how `dart-flutter-ci.yml` and `qt-ci.yml` turned out to be unusable in
any organisation following this library's own advice. See issue #150.

The first version of this check claimed that property and did not establish it.
Three gaps, each found by testing it rather than reading it:

* **It read YAML with a regular expression** anchored on `uses:` as the first
  token of a line, so the ordinary composite form `- uses: owner/action@ref`
  matched nothing. The sibling expression for this repository's own workflows
  handled `(?:-\\s*)?`; this one did not, and the difference was one group.
* **It did not recurse.** A nested reference that was itself a SHA ended the
  walk, so anything at depth two or beyond was never looked at.
* **It could not tell throttling from a refusal.** ~43 sequential API calls with
  no pacing hit a secondary rate limit, and the 403 was reported as
  "unreachable", which reads as a broken third party rather than as this check
  asking too fast. `maintenance.yml` filed exactly that on its first run.

Advisory tier. It reads other repositories over the network, and what a third
party writes in its own `action.yml` is not a property of this tree.
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ci_workflows_tools._strict_yaml import strict_load, strict_loads
from ci_workflows_tools._workflow_yaml import WORKFLOWS_DIR, workflow_files

USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<ref>[^\s#]+)", re.MULTILINE)
SHA = re.compile(r"^[0-9a-f]{40}$")
TIMEOUT_SECONDS = 30
# Definitions come from the raw host, not the contents API, and the reason is a
# finding rather than a preference. The Actions `GITHUB_TOKEN` is an installation
# token scoped to this repository, and two of the pinned actions
# (`aquasecurity/trivy-action`, `bridgecrewio/checkov-action`) answer it with 403
# while 41 others answer normally -- so the check could never complete in the
# very job that owns it. Both serve fine here with no credential at all. This
# also removes the 60-requests-an-hour ceiling that made a token necessary in the
# first place, so the check needs no secret and cannot be throttled into
# reporting a third party as broken.
#
# A private action would 404 here and be reported as having no definition. That
# is correct for this tree, which pins only public actions, and it is a visible
# failure rather than a silent pass.
RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{path}"
IMAGES = Path(__file__).resolve().parent.parent / "catalog/action-images.yml"

# Bounds, so a hostile or merely circular graph cannot run forever. They are
# generous: the tree's own graph is two layers and a few dozen nodes.
MAX_DEPTH = 6
MAX_NODES = 400
RETRY_DELAYS = (2, 8, 20)


class Unavailable(Exception):
    """A definition could not be read, for a reason other than "not there".

    Separate from a missing definition because the two mean opposite things: a
    404 is a finding about the pin, and this is a finding about the run.
    """


def _candidate_paths(subdirectory: str) -> tuple[str, ...]:
    """Where the definition of a pinned reference lives, by kind.

    A pinned reference is either an action, whose definition is `action.yml` or
    `action.yaml` in its directory, or a reusable workflow, which *is* the file.
    Treating the second as the first asks for `.github/workflows/x.yml/action.yml`,
    gets a 404, and reports a missing definition for a perfectly good pin.
    """
    if subdirectory.endswith((".yml", ".yaml")):
        return (subdirectory,)
    if subdirectory:
        return (f"{subdirectory}/action.yml", f"{subdirectory}/action.yaml")
    return ("action.yml", "action.yaml")


def _uses_in(node: Any) -> set[str]:
    """Every `uses:` value reachable in a parsed definition, at any nesting.

    Read from the parsed document rather than matched in the text. An action
    definition puts them under `runs.steps`; a reusable workflow puts them under
    `jobs.<id>` directly and under `jobs.<id>.steps`. Walking the structure
    covers both without encoding either, and cannot be defeated by key order or
    by which of the two step spellings the author used.
    """
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "uses" and isinstance(value, str):
                found.add(value.strip())
            else:
                found |= _uses_in(value)
    elif isinstance(node, list):
        for item in node:
            found |= _uses_in(item)
    return found


def _image_in(node: Any) -> set[str]:
    """A Docker action names what it executes under `runs.image`, not `uses`.

    Collecting only `uses:` missed the entire shape. `ossf/scorecard-action` is
    pinned here by commit SHA and that commit's `action.yaml` ends
    `image: "docker://ghcr.io/ossf/scorecard-action:v2.4.4"` -- a tag. Retagging
    that image changes what every consumer of the reusable executes, while every
    pin in this tree still looks immutable. Reported from a consuming repository
    (issue #173) rather than caught here, because the walk could not see it.

    An `image:` naming a Dockerfile is built from the action's own source at the
    pinned commit, so it introduces nothing new and is left alone.
    """
    if not isinstance(node, dict):
        return set()
    runs = node.get("runs")
    if not isinstance(runs, dict):
        return set()
    if str(runs.get("using", "")).strip().lower() != "docker":
        return set()
    image = runs.get("image")
    if isinstance(image, str) and image.strip().startswith("docker://"):
        return {image.strip()}
    return set()


def _references(text: str, origin: str) -> set[str]:
    """The nested references a definition declares."""
    try:
        document = strict_loads(text, origin)
    except Exception as exc:  # noqa: BLE001 - a third party's YAML, not ours
        raise Unavailable(f"{origin}: definition does not parse as YAML: {exc}") from exc
    return _uses_in(document) | _image_in(document)


def _third_party_pins() -> dict[str, set[str]]:
    """Every distinct `owner/repo[/path]@sha` this repository calls, and where."""
    pins: dict[str, set[str]] = {}
    for path in workflow_files():
        relative = path.relative_to(WORKFLOWS_DIR.parent.parent).as_posix()
        for match in USES.finditer(path.read_text(encoding="utf-8")):
            ref = match.group("ref")
            if ref.startswith("./") or ref.startswith("docker://"):
                continue
            _, _, revision = ref.partition("@")
            if SHA.fullmatch(revision):
                pins.setdefault(ref, set()).add(relative)
    return pins


def _is_rate_limited(exc: urllib.error.HTTPError) -> bool:
    """Distinguish "you are asking too fast" from "you may not read this".

    GitHub answers both with 403. The primary limit sets `X-RateLimit-Remaining:
    0`; the secondary limit sets `Retry-After` or says so in the body. Reporting
    a throttle as a permission failure sent a maintainer looking at the wrong
    repository -- which is what the first filed sweep finding did.
    """
    if exc.code not in (403, 429):
        return False
    headers = exc.headers or {}
    if str(headers.get("X-RateLimit-Remaining", "")).strip() == "0":
        return True
    if headers.get("Retry-After"):
        return True
    return "rate limit" in str(exc.reason).lower()


def _fetch(repo: str, path: str, ref: str) -> str | None:
    """The definition's bytes, `None` if there is none, `Unavailable` otherwise."""
    request = urllib.request.Request(
        RAW.format(repo=repo, path=path, ref=ref),
        headers={"User-Agent": "nddev-ci-workflows-pin-audit"},
    )
    for attempt, delay in enumerate((*RETRY_DELAYS, None)):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if _is_rate_limited(exc) and delay is not None:
                time.sleep(delay)
                continue
            reason = "rate limited" if _is_rate_limited(exc) else f"HTTP {exc.code}"
            raise Unavailable(f"{repo}: {reason} after {attempt + 1} attempt(s)") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if delay is None:
                raise Unavailable(f"{repo}: unreachable: {exc}") from exc
            time.sleep(delay)
    raise Unavailable(f"{repo}: exhausted retries")


Fetcher = Callable[[str, str, str], str | None]


def _declared_images() -> dict[tuple[str, str], dict[str, Any]]:
    """Tag-addressed images this repository has recorded rather than fixed."""
    if not IMAGES.is_file():
        return {}
    return {
        (str(entry["action"]), str(entry["image"])): entry
        for entry in (strict_load(IMAGES).get("images") or [])
    }


def walk(roots: dict[str, set[str]], fetch: Fetcher,
         declared: dict[tuple[str, str], dict[str, Any]] | None = None) -> list[str]:
    """Every reference reachable from the tree's pins must itself be immutable.

    Breadth-first with a visited set, so a cycle terminates instead of recursing
    for ever, and bounded in depth and node count so a hostile graph cannot make
    this run without end.
    """
    declared = {} if declared is None else declared
    seen_declared: set[tuple[str, str]] = set()
    problems: list[str] = []
    seen: set[str] = set()
    # (reference, depth, the tree paths that reach it)
    queue: list[tuple[str, int, set[str]]] = [
        (pin, 0, callers) for pin, callers in sorted(roots.items())
    ]
    while queue:
        reference, depth, callers = queue.pop(0)
        if reference in seen:
            continue
        seen.add(reference)
        if len(seen) > MAX_NODES:
            problems.append(
                f"action graph exceeded {MAX_NODES} nodes; refusing to keep walking")
            break
        if depth > MAX_DEPTH:
            problems.append(
                f"{reference}: action graph deeper than {MAX_DEPTH}; refusing to "
                "keep walking")
            continue

        location, _, revision = reference.partition("@")
        parts = location.split("/")
        repo = "/".join(parts[:2])
        candidates = _candidate_paths("/".join(parts[2:]))
        definition = None
        try:
            for candidate in candidates:
                definition = fetch(repo, candidate, revision)
                if definition is not None:
                    break
        except Unavailable as exc:
            problems.append(f"{reference}: nested pins unverified, {exc}")
            continue
        if definition is None:
            problems.append(
                f"{reference}: no definition at that ref (looked for "
                f"{', '.join(candidates)})")
            continue
        try:
            nested_refs = _references(definition, reference)
        except Unavailable as exc:
            problems.append(f"{reference}: nested pins unverified, {exc}")
            continue

        for nested in sorted(nested_refs):
            if nested.startswith("./"):
                # A local reference resolves inside the repository already being
                # walked, at the same revision; it introduces no new mutability.
                continue
            if nested.startswith("docker://"):
                if "@sha256:" in nested:
                    continue
                if (location, nested) in declared:
                    seen_declared.add((location, nested))
                    continue
                problems.append(
                    f"{reference} calls {nested} without a digest "
                    f"(used by {', '.join(sorted(callers))}); pin it, or record it "
                    "in catalog/action-images.yml with what a consumer is exposed to")
                continue
            _, _, nested_revision = nested.partition("@")
            if not SHA.fullmatch(nested_revision):
                problems.append(
                    f"{reference} calls {nested}, which is not pinned to a commit SHA "
                    f"-- a caller enforcing SHA pinning cannot start "
                    f"{', '.join(sorted(callers))}")
                continue
            queue.append((nested, depth + 1, callers))

    # Reverse control: a recorded exposure that the tree no longer reaches, or
    # that upstream has since digest-pinned, must be removed rather than left
    # describing a graph that has moved on.
    for key in sorted(set(declared) - seen_declared):
        problems.append(
            f"catalog/action-images.yml records {key[0]} calling {key[1]}, which "
            "this tree no longer reaches unpinned; drop the entry")
    return problems


def _selftest() -> list[str]:
    """The walk, on graphs built in memory rather than fetched.

    Each case here was first observed failing against the previous
    implementation; none of them needs the network, which is why they can be
    asserted every run instead of whenever GitHub is reachable.
    """
    problems: list[str] = []

    for subdirectory, expected in (
        ("", ("action.yml", "action.yaml")),
        ("setup", ("setup/action.yml", "setup/action.yaml")),
        (".github/workflows/release.yml", (".github/workflows/release.yml",)),
        (".github/workflows/release.yaml", (".github/workflows/release.yaml",)),
    ):
        actual = _candidate_paths(subdirectory)
        if actual != expected:
            problems.append(
                f"reference-kind selftest: {subdirectory!r} resolved to {actual}, "
                f"expected {expected}")

    sha = "a" * 40
    other = "b" * 40
    third = "c" * 40

    def graph(definitions: dict[str, str]) -> Fetcher:
        """Serve definitions keyed by the reference string that reaches them.

        The path matters: a reusable workflow is addressed by its own file while
        an action is addressed by `action.yml` inside its directory. Keying on
        the repository alone conflated the two, which this self-test caught.
        """
        def fetch(repo: str, path: str, ref: str) -> str | None:
            reference = f"{repo}@{ref}" if path in ("action.yml", "action.yaml") \
                else f"{repo}/{path}@{ref}"
            return definitions.get(reference)
        return fetch

    # The step spelling the regular expression could not see.
    dash_form = "runs:\n  using: composite\n  steps:\n    - uses: o/b@v1\n"
    named_form = "runs:\n  using: composite\n  steps:\n    - name: n\n      uses: o/b@v1\n"
    for label, body in (("dash", dash_form), ("named", named_form)):
        found = walk({f"o/a@{sha}": {"w.yml"}}, graph({f"o/a@{sha}": body}))
        if not any("o/b@v1" in problem for problem in found):
            problems.append(
                f"walk selftest: the {label} step form did not surface a mutable "
                f"nested reference; got {found}")

    # Depth two and three: a nested SHA is not the end of the walk.
    deep = {
        f"o/a@{sha}": f"runs:\n  steps:\n    - uses: o/b@{other}\n",
        f"o/b@{other}": f"runs:\n  steps:\n    - uses: o/c@{third}\n",
        f"o/c@{third}": "runs:\n  steps:\n    - uses: o/d@v9\n",
    }
    found = walk({f"o/a@{sha}": {"w.yml"}}, graph(deep))
    if not any("o/d@v9" in problem for problem in found):
        problems.append(
            f"walk selftest: a mutable reference at depth three was not found; got {found}")

    # A cycle terminates rather than recursing for ever.
    cycle = {
        f"o/a@{sha}": f"runs:\n  steps:\n    - uses: o/b@{other}\n",
        f"o/b@{other}": f"runs:\n  steps:\n    - uses: o/a@{sha}\n",
    }
    if walk({f"o/a@{sha}": {"w.yml"}}, graph(cycle)):
        problems.append("walk selftest: a clean cycle reported a problem")

    # A reusable workflow names actions under `jobs`, not under `runs.steps`.
    reusable = {
        f"o/a/.github/workflows/r.yml@{sha}":
            "on:\n  workflow_call:\njobs:\n  j:\n    steps:\n      - uses: o/b@v2\n",
    }
    found = walk({f"o/a/.github/workflows/r.yml@{sha}": {"w.yml"}}, graph(reusable))
    if not any("o/b@v2" in problem for problem in found):
        problems.append(
            f"walk selftest: a reusable workflow's nested reference was missed; got {found}")

    # A job that *is* a reusable-workflow call, with no steps at all.
    called = {f"o/a@{sha}": "jobs:\n  j:\n    uses: o/b/.github/workflows/x.yml@v3\n"}
    found = walk({f"o/a@{sha}": {"w.yml"}}, graph(called))
    if not any("x.yml@v3" in problem for problem in found):
        problems.append(
            f"walk selftest: a `jobs.<id>.uses` reference was missed; got {found}")

    # A missing definition is a finding about the pin.
    found = walk({f"o/a@{sha}": {"w.yml"}}, graph({}))
    if not any("no definition at that ref" in problem for problem in found):
        problems.append(f"walk selftest: a missing definition was not reported; got {found}")

    # An unreadable definition is a finding about the run, and must not be
    # silently treated as "nothing nested here".
    def refuses(repo: str, path: str, ref: str) -> str | None:
        raise Unavailable(f"{repo}: rate limited after 4 attempt(s)")

    found = walk({f"o/a@{sha}": {"w.yml"}}, refuses)
    if not any("rate limited" in problem for problem in found):
        problems.append(f"walk selftest: an unreadable definition was swallowed; got {found}")

    # Docker references need a digest, and a digest-pinned one is fine.
    docker = {f"o/a@{sha}": "runs:\n  steps:\n    - uses: docker://alpine:3\n"}
    found = walk({f"o/a@{sha}": {"w.yml"}}, graph(docker))
    if not any("without a digest" in problem for problem in found):
        problems.append(f"walk selftest: an undigested image was accepted; got {found}")

    # A Docker action's image is what it executes, and it is not under `uses`.
    docker_action = {
        f"o/a@{sha}": 'runs:\n  using: "docker"\n  image: "docker://ghcr.io/o/a:v1"\n'}
    found = walk({f"o/a@{sha}": {"w.yml"}}, graph(docker_action))
    if not any("without a digest" in problem for problem in found):
        problems.append(
            f"walk selftest: a tag-addressed `runs.image` was accepted; got {found}")

    digested = {
        f"o/a@{sha}":
            'runs:\n  using: "docker"\n  image: "docker://ghcr.io/o/a@sha256:'
            + "d" * 64 + '"\n'}
    if walk({f"o/a@{sha}": {"w.yml"}}, graph(digested)):
        problems.append("walk selftest: a digest-pinned `runs.image` was reported")

    # A Dockerfile is built from the action's own source at the pinned commit.
    built = {f"o/a@{sha}": 'runs:\n  using: "docker"\n  image: "Dockerfile"\n'}
    if walk({f"o/a@{sha}": {"w.yml"}}, graph(built)):
        problems.append("walk selftest: a Dockerfile-built image was reported")

    # A local reference introduces no new mutability and must not be chased.
    local = {f"o/a@{sha}": "runs:\n  steps:\n    - uses: ./nested\n"}
    if walk({f"o/a@{sha}": {"w.yml"}}, graph(local)):
        problems.append("walk selftest: a local reference was treated as a finding")

    return problems


def check() -> list[str]:
    return _selftest() + walk(_third_party_pins(), _fetch, _declared_images())


def main() -> int:
    problems = check()
    if problems:
        print("check_transitive_action_pins: FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("check_transitive_action_pins: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
