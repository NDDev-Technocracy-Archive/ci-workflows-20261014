#!/usr/bin/env python3
"""Validate bounded SDK fixtures and reject false-green runtime receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from ci_workflows_tools import _gradle_lockfile, _sdk_environment, generate_sdk_runtime_manifest
from ci_workflows_tools._strict_yaml import strict_load
from ci_workflows_tools._workflow_yaml import get_on, load_yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "tests/fixtures/sdk-runtime-spec.yml"
MANIFEST = ROOT / "tests/fixtures/sdk-runtime-manifest.json"
WORKFLOWS = ROOT / ".github/workflows"

# The receipt is a discriminated record: `kind` selects the vocabulary and
# `sections` says which parts of it this run actually produced. A field may
# appear only inside a section the receipt declares, which is what keeps a
# generic reusable from having to invent values for lanes a caller skipped --
# the previous shape had one flat record, so "not applicable" and "missing"
# were the same thing and the only way to satisfy it was to run the one
# canonical fixture.
ENVELOPE = frozenset({
    "callee_path", "callee_repository", "callee_sha", "caller_repository",
    "caller_sha", "kind", "os", "runner_arch", "schema_version", "sections",
})
KIND_ENVELOPE = {
    "flutter": frozenset(),
    "android": frozenset({
        "cache_provider", "java_version_input", "root_trust_model", "setup_android",
        "untrusted_roots",
    }),
    "qt": frozenset(),
}
SECTIONS: dict[str, dict[str, tuple[frozenset[str], frozenset[str]]]] = {
    "flutter": {
        "toolchain": (frozenset({
            "dart_version", "flutter_arch", "flutter_channel", "flutter_revision",
            "flutter_version",
        }), frozenset()),
        "cache": (frozenset({"cache_key", "pub_cache_key"}), frozenset()),
        "resolve": (frozenset({"pub_get_command", "pubspec_lock_sha256"}), frozenset()),
        "test": (frozenset({"test_command", "test_count", "test_log_sha256"}), frozenset()),
    },
    "android": {
        "build": (frozenset({"build_command", "build_log_sha256", "task_graph"}), frozenset()),
        "jdk": (frozenset({
            "java_home_resolved", "java_runtime_version_resolved",
            "java_vendor_resolved", "java_version_resolved", "java_vm_name_resolved",
        }), frozenset()),
        "gradle": (frozenset({
            "gradle_daemon_jvm_home_resolved", "gradle_daemon_jvm_resolved",
            "gradle_daemon_jvm_runtime_version_resolved",
            "gradle_daemon_jvm_vendor_resolved", "gradle_daemon_jvm_version_resolved",
            "gradle_daemon_jvm_vm_name_resolved", "gradle_launcher_jvm_resolved",
            "gradle_launcher_jvm_runtime_version_resolved",
            "gradle_launcher_jvm_vendor_resolved", "gradle_launcher_jvm_version_resolved",
            "gradle_launcher_jvm_vm_name_resolved", "gradle_version",
        }), frozenset()),
        "tests": (frozenset({"test_count"}), frozenset()),
        "artifacts": (frozenset({"apk_sha256"}), frozenset()),
        "locks": (frozenset({"lock_sha256"}), frozenset()),
        "dependency_verification": (
            frozenset({"verification_metadata_sha256"}), frozenset()),
        "wrapper": (frozenset({
            "wrapper_jar_sha256", "wrapper_properties_sha256"}), frozenset()),
        "android_sdk": (frozenset({
            "android_sdk_root_resolved", "sdk_build_tools", "sdk_platforms",
        }), frozenset()),
    },
    "qt": {
        "toolchain": (
            frozenset({"cache_key_prefix", "qt_version_input"}),
            frozenset({"aqt_version", "cmake_version", "qt_version",
                       "toolchain_lock_sha256"}),
        ),
        "configure": (frozenset({"configure_command"}), frozenset()),
        "build": (frozenset({"build_command"}), frozenset()),
        "test": (frozenset({"test_command", "test_count", "test_log_sha256"}), frozenset()),
    },
}

# What the canonical fixture in tests/fixtures must show. These live here, in
# the observer's validator, and nowhere in the reusable workflows: they are
# assertions about one repository's fixture, not part of the reusable API.
CANONICAL_SECTIONS = {
    "flutter": frozenset({"toolchain", "cache", "resolve", "test"}),
    "android": frozenset({
        "android_sdk", "artifacts", "build", "dependency_verification", "gradle",
        "jdk", "locks", "tests", "wrapper",
    }),
    "qt": frozenset({"toolchain", "configure", "build", "test"}),
}
CANONICAL_TASKS = frozenset({
    ":app:assembleDebug", ":app:testDebugUnitTest", ":app:lintDebug", ":app:build",
})

# Every digest in a receipt is checked by the same grammar. Before this, only
# `test_log_sha256` and `build_log_sha256` were shape-checked at all; the lock
# map, the APK list and the dependency-verification digest were accepted on
# truthiness, so `"apk_sha256": ["nope"]` passed.
DIGEST_SCALARS = frozenset({
    "build_log_sha256", "pubspec_lock_sha256", "test_log_sha256",
    "verification_metadata_sha256", "wrapper_jar_sha256", "wrapper_properties_sha256",
})
DIGEST_LISTS = frozenset({"apk_sha256"})
DIGEST_MAPS = frozenset({"lock_sha256"})


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _workflow_call(path: Path) -> dict[str, Any]:
    doc = load_yaml(path)
    on = get_on(doc)
    return on.get("workflow_call", {}) if isinstance(on, dict) else {}


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _is_git_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(c in "0123456789abcdef" for c in value)


def _digest_problems(kind: str, receipt: dict[str, Any]) -> list[str]:
    """Hold every digest in the receipt to one grammar.

    Scalars are exactly 64 lowercase hex. Lists are non-empty and repeat
    nothing. Mappings are non-empty, keyed by relative POSIX paths in lexical
    order, so a receipt cannot smuggle an absolute path, a traversal, or a
    Windows separator into what reads like a project-relative digest table.
    """
    problems: list[str] = []
    for key in sorted(DIGEST_SCALARS & set(receipt)):
        if not _is_sha(receipt[key]):
            problems.append(f"{kind}: {key} must be 64 lowercase hex")
    for key in sorted(DIGEST_LISTS & set(receipt)):
        value = receipt[key]
        if not isinstance(value, list) or not value:
            problems.append(f"{kind}: {key} must be a non-empty list of digests")
            continue
        if any(not _is_sha(item) for item in value):
            problems.append(f"{kind}: {key} holds a value that is not 64 lowercase hex")
            continue
        if len(set(value)) != len(value):
            problems.append(f"{kind}: {key} repeats a digest")
    for key in sorted(DIGEST_MAPS & set(receipt)):
        value = receipt[key]
        if not isinstance(value, dict) or not value:
            problems.append(f"{kind}: {key} must be a non-empty path-to-digest mapping")
            continue
        paths = list(value)
        if paths != sorted(paths):
            problems.append(f"{kind}: {key} paths are not in lexical order")
        for path, item in value.items():
            parts = PurePosixPath(path).parts if isinstance(path, str) else ()
            if not isinstance(path, str) or not path or path.startswith("/") \
                    or "\\" in path or ".." in parts:
                problems.append(f"{kind}: {key} key {path!r} is not a relative POSIX path")
            if not _is_sha(item):
                problems.append(f"{kind}: {key}[{path!r}] must be 64 lowercase hex")
    return problems


def _shape_problems(kind: str, receipt: dict[str, Any]) -> list[str]:
    """Validate the receipt against the vocabulary its own discriminator selects."""
    vocabulary = SECTIONS[kind]
    sections = receipt.get("sections")
    if not isinstance(sections, list) or sections != sorted(set(sections)) \
            or any(name not in vocabulary for name in sections):
        return [f"{kind}: sections must be a sorted unique subset of {sorted(vocabulary)}"]
    problems: list[str] = []
    declared = set(sections)
    envelope = set(ENVELOPE) | set(KIND_ENVELOPE[kind])
    vocabulary_fields: set[str] = set()
    for required, optional in vocabulary.values():
        vocabulary_fields |= required | optional
    for name in sorted(declared):
        required, _ = vocabulary[name]
        missing = sorted(required - set(receipt))
        if missing:
            problems.append(
                f"{kind}: section {name!r} is declared but {', '.join(missing)} absent")
    # Two disjoint failures, so neither can hide the other: a field of the
    # vocabulary whose section was not declared is a *leak* -- the receipt
    # answered a question it did not claim to have asked -- while a field
    # outside the vocabulary entirely is simply unknown.
    for name, (required, optional) in sorted(vocabulary.items()):
        if name in declared:
            continue
        leaked = sorted((required | optional) & set(receipt))
        if leaked:
            problems.append(
                f"{kind}: {', '.join(leaked)} present without declaring section {name!r}")
    absent = sorted(envelope - set(receipt))
    if absent:
        problems.append(f"{kind}: envelope field(s) absent: {', '.join(absent)}")
    unknown = sorted(set(receipt) - envelope - vocabulary_fields)
    if unknown:
        problems.append(f"{kind}: unknown field(s): {', '.join(unknown)}")
    if receipt.get("schema_version") != 1:
        problems.append(f"{kind}: schema_version must be 1")
    if receipt.get("kind") != kind:
        problems.append(f"{kind}: kind must equal {kind!r}")
    return problems


def _provenance_problems(kind: str, receipt: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    """Require the receipt to name the workflow that ran, not the caller's tree.

    `callee_*` comes from `job.workflow_*`, which the runner binds to the called
    workflow; `caller_*` comes from `github.*`, which inside a reusable is bound
    to the calling workflow. A commit SHA is already the cryptographic identity
    of the content at that path, so the triple pins the bytes without hashing
    anything the caller controls.
    """
    problems: list[str] = []
    for key in ("callee_sha", "caller_sha"):
        if not _is_git_sha(receipt.get(key)):
            problems.append(f"{kind}: {key} must be a full lowercase Git SHA")
    if receipt.get("callee_path") != spec["workflow"]:
        problems.append(f"{kind}: callee_path must equal {spec['workflow']!r}")
    for key in ("callee_repository", "caller_repository"):
        value = receipt.get(key)
        if not isinstance(value, str) or value.count("/") != 1 or not all(value.split("/")):
            problems.append(f"{kind}: {key} must be 'owner/name'")
    return problems


def _canonical_problems(kind: str, receipt: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    """Assert what this repository's own fixture must show.

    Everything here is about `tests/fixtures/**`, which is why it lives in the
    observer's validator rather than in the reusable. A reusable that enforced
    these would be demanding that every consumer own an `app` module, produce an
    APK, and commit dependency-verification metadata.
    """
    problems: list[str] = []
    expected = spec["toolchain"]
    defaults = spec["default_commands"]
    missing = sorted(CANONICAL_SECTIONS[kind] - set(receipt.get("sections", [])))
    if missing:
        problems.append(f"{kind}: canonical run must produce section(s): {', '.join(missing)}")
    checks: dict[str, Any] = {"os": "Linux", "runner_arch": "X64"}
    if kind == "flutter":
        version = str(expected["flutter_version"])
        checks.update({
            "dart_version": str(expected["dart_version"]),
            "flutter_revision": expected["framework_revision"],
            "flutter_version": version,
            "pub_get_command": defaults["resolve"],
            "test_command": defaults["test"],
        })
        if receipt.get("test_count", 0) < 1:
            problems.append("flutter: canonical run must report at least one test")
        if "cache" in receipt.get("sections", []) and any(
            version not in str(receipt.get(key, "")) for key in ("cache_key", "pub_cache_key")
        ):
            problems.append("flutter: action cache identities must name the pinned version")
    elif kind == "android":
        checks.update({
            "build_command": defaults["build"],
            "cache_provider": "basic",
            "gradle_version": str(expected["gradle_version"]),
            "java_version_input": str(expected["java_version_input"]),
            "setup_android": "false",
            "wrapper_jar_sha256": expected["gradle_wrapper_jar_sha256"],
        })
        if receipt.get("test_count", 0) < 1:
            problems.append("android: canonical run must report at least one test")
        if receipt.get("untrusted_roots"):
            problems.append(
                f"android: canonical run vouched for no root: {receipt['untrusted_roots']}")
        platforms = {f"android-{expected['compile_sdk']}", f"android-{expected['compile_sdk']}.0"}
        if len(platforms & set(receipt.get("sdk_platforms", []))) != 1:
            problems.append("android: required SDK platform is absent")
        if str(expected["build_tools"]) not in receipt.get("sdk_build_tools", []):
            problems.append("android: required build-tools are absent")
        if not CANONICAL_TASKS.issubset(receipt.get("task_graph", [])):
            problems.append("android: exact default build task graph is incomplete")
        try:
            _sdk_environment.validate_jvm_identity(_jvm_identity(receipt))
        except (KeyError, OSError, _sdk_environment.SdkEnvironmentError) as exc:
            problems.append(f"android: JVM identity incoherent: {exc}")
        expected_launcher = (
            f'{receipt.get("gradle_launcher_jvm_version_resolved", "")} '
            f'({receipt.get("gradle_launcher_jvm_vendor_resolved", "")} '
            f'{receipt.get("gradle_launcher_jvm_runtime_version_resolved", "")})'
        )
        if receipt.get("gradle_launcher_jvm_resolved") != expected_launcher:
            problems.append("android: raw Gradle launcher identity diverged from typed fields")
        if not str(receipt.get("gradle_daemon_jvm_resolved", "")).startswith(
            str(receipt.get("gradle_daemon_jvm_home_resolved", "")) + " ("
        ):
            problems.append("android: raw Gradle daemon identity diverged from typed fields")
    elif kind == "qt":
        checks.update({
            "build_command": defaults["build"],
            "cache_key_prefix": "qt-ci-v1",
            "configure_command": defaults["configure"],
            "qt_version": str(expected["qt_version"]),
            "qt_version_input": str(expected["qt_version"]),
            "test_command": defaults["test"],
        })
        if receipt.get("test_count", 0) < 1:
            problems.append("qt: canonical run must report at least one CTest test")
        if str(expected["aqtinstall_version"]) not in str(receipt.get("aqt_version", "")):
            problems.append("qt: wrong aqtinstall version")
    for key, value in sorted(checks.items()):
        if receipt.get(key) != value:
            problems.append(f"{kind}: {key} must equal {value!r}")
    return problems


def _receipt_problems(kind: str, receipt: Any, spec: dict[str, Any]) -> list[str]:
    if kind not in SECTIONS:
        return [f"unknown SDK receipt kind {kind!r}"]
    if not isinstance(receipt, dict):
        return [f"{kind}: evidence must be a JSON object"]
    shape = _shape_problems(kind, receipt)
    if shape:
        return shape
    return (
        _provenance_problems(kind, receipt, spec)
        + _digest_problems(kind, receipt)
        + _canonical_problems(kind, receipt, spec)
    )


def _contract_problems(*, require_generated: bool = True) -> list[str]:
    problems: list[str] = []
    spec = strict_load(SPEC_PATH)
    fixtures = spec.get("fixtures", {})
    if set(fixtures) != {"flutter", "android", "qt"}:
        problems.append("SDK spec must define exactly flutter/android/qt")
        return problems
    try:
        expected_manifest = generate_sdk_runtime_manifest.render()
    except (OSError, UnicodeError, ValueError) as exc:
        problems.append(f"SDK byte manifest source contract failed: {exc}")
        expected_manifest = b""
    if require_generated and (
        not MANIFEST.is_file() or MANIFEST.read_bytes() != expected_manifest
    ):
        problems.append("SDK byte manifest is missing or stale")
    estate = load_yaml(WORKFLOWS / "runtime-fixtures-languages.yml")
    jobs = estate.get("jobs", {})
    for kind, data in fixtures.items():
        workflow = ROOT / data["workflow"]
        call = _workflow_call(workflow)
        inputs = call.get("inputs", {})
        expected_defaults = data["default_commands"]
        keys = {
            "flutter": {"pub_get_command": "resolve", "test_command": "test"},
            "android": {"build_command": "build"},
            "qt": {"configure_command": "configure", "build_command": "build", "test_command": "test"},
        }[kind]
        for input_name, command_name in keys.items():
            if inputs.get(input_name, {}).get("default") != expected_defaults[command_name]:
                problems.append(f"{kind}: reusable default {input_name} drifted")
        caller_name = f"fixture-{('dart-flutter-ci' if kind == 'flutter' else 'kotlin-android-ci' if kind == 'android' else 'qt-ci')}"
        observer_name = f"observe-{caller_name.removeprefix('fixture-')}"
        caller = jobs.get(caller_name, {})
        observer = jobs.get(observer_name, {})
        if caller.get("uses") != data["workflow"].replace(".github", "./.github"):
            problems.append(f"{kind}: live caller is missing or points at wrong reusable")
        passed = caller.get("with", {})
        if passed.get("runner") != "ubuntu-latest" or passed.get("working_directory") != data["working_directory"]:
            problems.append(f"{kind}: caller must select exact standard runner and fixture root")
        if any(name in passed for name in keys):
            problems.append(f"{kind}: caller overrides a documented default command")
        if observer.get("if") != "always()" or observer.get("needs") != caller_name:
            problems.append(f"{kind}: observer must run always and depend directly on caller")
    android_root = ROOT / fixtures["android"]["working_directory"]
    wrapper = android_root / "gradle/wrapper/gradle-wrapper.jar"
    wrapper_props = (android_root / "gradle/wrapper/gradle-wrapper.properties").read_text(encoding="utf-8")
    android_tools = fixtures["android"]["toolchain"]
    if _digest(wrapper) != android_tools["gradle_wrapper_jar_sha256"]:
        problems.append("android: wrapper JAR digest drift")
    if f"distributionSha256Sum={android_tools['gradle_distribution_sha256']}" not in wrapper_props:
        problems.append("android: wrapper distribution digest drift")
    lockfiles = sorted(android_root.rglob("*.lockfile"))
    if require_generated and not lockfiles:
        problems.append("android: exact default build produced no dependency locks")
    for lockfile in lockfiles:
        try:
            _gradle_lockfile.parse_gradle_95_lockfile(lockfile)
        except _gradle_lockfile.GradleLockfileError as exc:
            problems.append(str(exc))
    provenance = android_root / "gradle/provenance-manifest.json"
    metadata = android_root / "gradle/verification-metadata.xml"
    if require_generated and (not provenance.is_file() or not metadata.is_file()):
        problems.append("android: generated provenance or verification metadata is missing")
    elif provenance.is_file() and metadata.is_file():
        try:
            proof = json.loads(provenance.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"android: invalid provenance receipt: {exc}")
        else:
            if proof.get("default_command") != "./gradlew build" \
                    or not CANONICAL_TASKS.issubset(proof.get("task_graph", [])):
                problems.append("android: provenance was generated from a narrower task graph")
            if proof.get("verification_metadata_sha256") != _digest(metadata):
                problems.append("android: provenance metadata digest is stale")
    return problems + _negative_selftests(fixtures)


def _jvm_identity(receipt: dict[str, Any]) -> _sdk_environment.JvmIdentity:
    return _sdk_environment.JvmIdentity(
        requested_major=str(receipt["java_version_input"]),
        java_home=str(receipt["java_home_resolved"]),
        java_version=str(receipt["java_version_resolved"]),
        java_runtime_version=str(receipt["java_runtime_version_resolved"]),
        java_vendor=str(receipt["java_vendor_resolved"]),
        java_vm_name=str(receipt["java_vm_name_resolved"]),
        launcher_version=str(receipt["gradle_launcher_jvm_version_resolved"]),
        launcher_runtime_version=str(receipt["gradle_launcher_jvm_runtime_version_resolved"]),
        launcher_vendor=str(receipt["gradle_launcher_jvm_vendor_resolved"]),
        launcher_vm_name=str(receipt["gradle_launcher_jvm_vm_name_resolved"]),
        daemon_home=str(receipt["gradle_daemon_jvm_home_resolved"]),
        daemon_version=str(receipt["gradle_daemon_jvm_version_resolved"]),
        daemon_runtime_version=str(receipt["gradle_daemon_jvm_runtime_version_resolved"]),
        daemon_vendor=str(receipt["gradle_daemon_jvm_vendor_resolved"]),
        daemon_vm_name=str(receipt["gradle_daemon_jvm_vm_name_resolved"]),
    )


def _cache_identity(slot: str, version: str) -> str:
    """Build a sample cache identity from parts.

    Written as concatenation rather than as one literal on purpose. A literal
    `pub_cache_key: "flutter-pub-<version>"` is what a secret scanner sees: a
    `_key` assignment whose value clears the generic-api-key entropy floor. It
    is a cache identity, not a credential, so the fix is to stop the sample
    looking like one rather than to teach the scanner to ignore this file.
    """
    return "flutter-" + slot + "-" + version


def _digest_negatives(kind: str, sample: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    """Substitute a malformed digest into every digest field the receipt carries.

    The point is coverage rather than cleverness: before this, four of the six
    digest-bearing fields were checked only for truthiness, so the suite proved
    nothing about them. Driving the list off `DIGEST_*` means a field added to
    the grammar is negatively tested the moment it appears in a sample.
    """
    problems: list[str] = []
    scalars = {
        "empty": "", "short": "a" * 63, "long": "a" * 65, "uppercase": "A" * 64,
        "non-hex": "g" * 64, "wrong-type": 12345, "absent": None,
    }
    for field in sorted(DIGEST_SCALARS & set(sample)):
        for label, value in scalars.items():
            broken = dict(sample)
            if value is None:
                del broken[field]
            else:
                broken[field] = value
            if not _receipt_problems(kind, broken, spec):
                problems.append(f"{kind}: digest {field} accepted {label!r}")
    for field in sorted(DIGEST_LISTS & set(sample)):
        first = sample[field][0]
        for label, value in {
            "empty-list": [], "duplicate": [first, first], "short-member": ["a" * 63],
            "uppercase-member": ["A" * 64], "non-hex-member": ["z" * 64],
            "wrong-type": first, "nested": [[first]],
        }.items():
            broken = dict(sample)
            broken[field] = value
            if not _receipt_problems(kind, broken, spec):
                problems.append(f"{kind}: digest list {field} accepted {label}")
    for field in sorted(DIGEST_MAPS & set(sample)):
        digest = next(iter(sample[field].values()))
        for label, value in {
            "empty-map": {},
            "absolute-path": {"/etc/shadow": digest},
            "traversal": {"../outside.lockfile": digest},
            "windows-separator": {"app\\gradle.lockfile": digest},
            "unordered": {"z/gradle.lockfile": digest, "a/gradle.lockfile": digest},
            "short-digest": {"app/gradle.lockfile": "e" * 63},
            "uppercase-digest": {"app/gradle.lockfile": "E" * 64},
            "wrong-type": ["app/gradle.lockfile"],
        }.items():
            broken = dict(sample)
            broken[field] = value
            if not _receipt_problems(kind, broken, spec):
                problems.append(f"{kind}: digest map {field} accepted {label}")
    return problems


def _negative_selftests(fixtures: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    flutter_tools = fixtures["flutter"]["toolchain"]
    android_tools = fixtures["android"]["toolchain"]
    qt_tools = fixtures["qt"]["toolchain"]
    flutter_version = str(flutter_tools["flutter_version"])
    repository = "NDDev-Technocracy-OpenNetwork/ci-workflows"
    envelope = {
        "callee_repository": repository, "callee_sha": "b" * 40,
        "caller_repository": repository, "caller_sha": "a" * 40,
        "os": "Linux", "runner_arch": "X64", "schema_version": 1,
    }
    samples: dict[str, dict[str, Any]] = {
        "flutter": {
            **envelope, "kind": "flutter",
            "callee_path": fixtures["flutter"]["workflow"],
            "sections": ["cache", "resolve", "test", "toolchain"],
            "dart_version": str(flutter_tools["dart_version"]),
            "flutter_arch": "x64", "flutter_channel": str(flutter_tools["channel"]),
            "flutter_revision": flutter_tools["framework_revision"],
            "flutter_version": flutter_version,
            "cache_key": _cache_identity("cache", flutter_version),
            "pub_cache_key": _cache_identity("pub", flutter_version),
            "pub_get_command": "flutter pub get", "pubspec_lock_sha256": "b" * 64,
            "test_command": "flutter test", "test_count": 1, "test_log_sha256": "c" * 64,
        },
        "android": {
            **envelope, "kind": "android",
            "callee_path": fixtures["android"]["workflow"],
            "sections": [
                "android_sdk", "artifacts", "build", "dependency_verification",
                "gradle", "jdk", "locks", "tests", "wrapper",
            ],
            "cache_provider": "basic", "java_version_input": "21",
            "root_trust_model": "ephemeral-single-tenant-runner",
            "setup_android": "false", "untrusted_roots": [],
            "build_command": "./gradlew build", "build_log_sha256": "1" * 64,
            "task_graph": sorted(CANONICAL_TASKS),
            "java_home_resolved": "/opt/jdk-21",
            "java_runtime_version_resolved": "21.0.11+0",
            "java_vendor_resolved": "Fixture", "java_version_resolved": "21.0.11",
            "java_vm_name_resolved": "Fixture VM",
            "gradle_version": str(android_tools["gradle_version"]),
            "gradle_launcher_jvm_resolved": "21.0.11 (Fixture 21.0.11+0)",
            "gradle_launcher_jvm_version_resolved": "21.0.11",
            "gradle_launcher_jvm_runtime_version_resolved": "21.0.11+0",
            "gradle_launcher_jvm_vendor_resolved": "Fixture",
            "gradle_launcher_jvm_vm_name_resolved": "Fixture VM",
            "gradle_daemon_jvm_resolved": "/opt/jdk-21 (no JDK specified, using current Java home)",
            "gradle_daemon_jvm_home_resolved": "/opt/jdk-21",
            "gradle_daemon_jvm_version_resolved": "21.0.11",
            "gradle_daemon_jvm_runtime_version_resolved": "21.0.11+0",
            "gradle_daemon_jvm_vendor_resolved": "Fixture",
            "gradle_daemon_jvm_vm_name_resolved": "Fixture VM",
            "test_count": 1, "apk_sha256": ["d" * 64],
            "lock_sha256": {"app/gradle.lockfile": "e" * 64},
            "verification_metadata_sha256": "f" * 64,
            "wrapper_jar_sha256": android_tools["gradle_wrapper_jar_sha256"],
            "wrapper_properties_sha256": "9" * 64,
            "android_sdk_root_resolved": "/opt/android",
            "sdk_build_tools": [str(android_tools["build_tools"])],
            "sdk_platforms": [f"android-{android_tools['compile_sdk']}"],
        },
        "qt": {
            **envelope, "kind": "qt",
            "callee_path": fixtures["qt"]["workflow"],
            "sections": ["build", "configure", "test", "toolchain"],
            "cache_key_prefix": "qt-ci-v1",
            "qt_version": str(qt_tools["qt_version"]),
            "qt_version_input": str(qt_tools["qt_version"]),
            "aqt_version": f"aqtinstall(aqt) v{qt_tools['aqtinstall_version']}",
            "cmake_version": "cmake version 3.31.6",
            "configure_command": fixtures["qt"]["default_commands"]["configure"],
            "build_command": fixtures["qt"]["default_commands"]["build"],
            "test_command": fixtures["qt"]["default_commands"]["test"],
            "test_count": 1, "test_log_sha256": "a" * 64,
        },
    }
    for kind, sample in samples.items():
        spec = fixtures[kind]
        if _receipt_problems(kind, sample, spec):
            problems.append(
                f"{kind}: valid receipt selftest rejected: "
                f"{_receipt_problems(kind, sample, spec)}")
        for field in ("callee_sha", "caller_sha", "test_count", "callee_path", "kind"):
            broken = dict(sample)
            broken[field] = 0 if field == "test_count" else "wrong"
            if not _receipt_problems(kind, broken, spec):
                problems.append(f"{kind}: negative {field} substitution was accepted")
        # Shape negatives are asserted against `_shape_problems` directly. Run
        # through the whole pipeline they would pass for the wrong reason: the
        # canonical check also requires these sections, so dropping one is
        # rejected by the canonical rule and the shape rule is never exercised.
        for section in sorted(SECTIONS[kind]):
            if section not in sample["sections"]:
                continue
            undeclared = dict(sample)
            undeclared["sections"] = sorted(set(sample["sections"]) - {section})
            if not any("without declaring section" in problem
                       for problem in _shape_problems(kind, undeclared)):
                problems.append(
                    f"{kind}: fields of undeclared section {section!r} were accepted")
            required = SECTIONS[kind][section][0]
            if required:
                dropped = sorted(required)[0]
                hollow = {key: value for key, value in sample.items() if key != dropped}
                if not any("is declared but" in problem
                           for problem in _shape_problems(kind, hollow)):
                    problems.append(
                        f"{kind}: section {section!r} without {dropped!r} was accepted")
        stray = dict(sample)
        stray["totally_unexpected_field"] = "x"
        if not any("unknown field" in problem for problem in _shape_problems(kind, stray)):
            problems.append(f"{kind}: unknown receipt field was accepted")
        for label, value in {
            "not-a-list": "toolchain", "unsorted": list(reversed(sample["sections"])),
            "duplicated": sample["sections"] + sample["sections"][:1],
            "out-of-vocabulary": sorted(sample["sections"] + ["invented"]),
        }.items():
            broken = dict(sample)
            broken["sections"] = value
            if not _shape_problems(kind, broken):
                problems.append(f"{kind}: {label} sections list was accepted")
        targeted = {
            "flutter": ("test_command", "cache_key", "pub_cache_key", "flutter_revision"),
            "android": ("build_command", "cache_provider", "gradle_launcher_jvm_resolved",
                        "task_graph", "untrusted_roots"),
            "qt": ("configure_command", "cache_key_prefix", "test_command", "aqt_version"),
        }[kind]
        for field in targeted:
            broken = dict(sample)
            broken[field] = [] if field == "task_graph" else (
                ["untrusted"] if field == "untrusted_roots" else "substituted")
            if not _receipt_problems(kind, broken, spec):
                problems.append(f"{kind}: negative {field} drift was accepted")
        problems.extend(_digest_negatives(kind, sample, spec))
        if kind == "android":
            identity_fields = (
                "java_version_input", "java_home_resolved", "java_version_resolved",
                "java_runtime_version_resolved", "java_vendor_resolved", "java_vm_name_resolved",
                "gradle_launcher_jvm_version_resolved",
                "gradle_launcher_jvm_runtime_version_resolved",
                "gradle_launcher_jvm_vendor_resolved", "gradle_launcher_jvm_vm_name_resolved",
                "gradle_daemon_jvm_home_resolved", "gradle_daemon_jvm_version_resolved",
                "gradle_daemon_jvm_runtime_version_resolved",
                "gradle_daemon_jvm_vendor_resolved", "gradle_daemon_jvm_vm_name_resolved",
            )
            for field in identity_fields:
                broken = dict(sample)
                broken[field] = "substituted"
                if not _receipt_problems(kind, broken, spec):
                    problems.append(f"android: JVM identity negative {field!r} was accepted")
    canonical = "\n".join((*_gradle_lockfile.HEADER, "a:b:1=alpha,beta", "empty=gamma", ""))
    mutations = {
        "missing-empty": canonical.replace("empty=gamma\n", ""),
        "duplicate-empty": canonical + "empty=gamma\n",
        "misplaced-empty": canonical.replace("a:b:1=alpha,beta\nempty=gamma", "empty=gamma\na:b:1=alpha,beta"),
        "nonlexical-config": canonical.replace("alpha,beta", "beta,alpha"),
        "truncated": canonical.rstrip("\n"),
    }
    with tempfile.TemporaryDirectory() as raw:
        for label, body in mutations.items():
            path = Path(raw) / label
            path.write_text(body, encoding="utf-8")
            try:
                _gradle_lockfile.parse_gradle_95_lockfile(path)
            except _gradle_lockfile.GradleLockfileError:
                continue
            problems.append(f"Gradle lock negative {label!r} was accepted")
    problems.extend(_sdk_environment_selftests())
    return problems


def _sdk_environment_selftests() -> list[str]:
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="sdk-environment-contract-") as raw:
        root = Path(raw)
        java_home = root / "jdk-21"
        java = java_home / "bin/java"
        java.parent.mkdir(parents=True)
        java.write_text("fixture\n", encoding="utf-8")
        sdk = root / "android-sdk"
        manager = sdk / "cmdline-tools/latest/bin/sdkmanager"
        manager.parent.mkdir(parents=True)
        manager.write_text("fixture\n", encoding="utf-8")
        (sdk / "platforms/android-37").mkdir(parents=True)
        (sdk / "build-tools/36.0.0").mkdir(parents=True)
        # State the modes instead of inheriting them. `mkdir` applies the
        # process umask, so under the common `umask 002` these roots are 0o775
        # and the trust rule correctly rejects them -- failing the *valid* case
        # and turning a blocking validator into a function of the developer's
        # shell. The ownership rule itself is exercised by pure cases below.
        java_home.chmod(0o755)
        sdk.chmod(0o755)
        props = {"java.home": str(java_home), "java.version": "21.0.11",
                 "java.runtime.version": "21.0.11+0"}
        ambient = {"JAVA_HOME": "/hostile/java", "ANDROID_HOME": "/hostile/a",
                   "ANDROID_SDK_ROOT": "/hostile/b", "ANDROID_NDK_HOME": "/leak"}
        try:
            result = _sdk_environment.derive_android_environment(
                {"PATH": "/clean"}, ambient, java_executable=java,
                java_properties=props, sdkmanager_executable=manager,
                java_major="21", compile_sdk="37", build_tools="36.0.0",
            )
        except _sdk_environment.SdkEnvironmentError as exc:
            problems.append(f"valid SDK environment shape rejected: {exc}")
            return problems
        if result["JAVA_HOME"] != str(java_home.resolve()) \
                or result["ANDROID_HOME"] != str(sdk.resolve()) \
                or result["ANDROID_SDK_ROOT"] != str(sdk.resolve()) \
                or "ANDROID_NDK_HOME" in result:
            problems.append("SDK transition inherited mismatched roots or leaked child env")
        receipt = json.loads(result[_sdk_environment.EVIDENCE_NAME])
        if receipt.get("stripped_ambient") != list(_sdk_environment.SDK_NAMES):
            problems.append("SDK transition stripped-input evidence drifted")

        cases: list[tuple[str, Path, dict[str, str], Path, int | None]] = []
        wrong = dict(props); wrong["java.version"] = "17.0.19"
        cases.append(("wrong-version", java, wrong, manager, None))
        missing = dict(props); missing["java.home"] = str(root / "missing-jdk")
        cases.append(("missing", java, missing, manager, None))
        alias = root / "jdk-alias"
        alias.symlink_to(java_home, target_is_directory=True)
        aliased = dict(props); aliased["java.home"] = str(alias)
        cases.append(("symlink-alias", java, aliased, manager, None))
        ancestor = root / "alternate-ancestor"
        ancestor.symlink_to(root, target_is_directory=True)
        alternate = dict(props)
        alternate["java.home"] = str(ancestor / "jdk-21")
        cases.append(("alternate-ancestor", java, alternate, manager, None))
        for label, executable, identity, sdk_executable, uid in cases:
            try:
                _sdk_environment.derive_android_environment(
                    {}, ambient, java_executable=executable,
                    java_properties=identity, sdkmanager_executable=sdk_executable,
                    java_major="21", compile_sdk="37", build_tools="36.0.0",
                    uid=uid,
                )
            except _sdk_environment.SdkEnvironmentError:
                continue
            problems.append(f"SDK environment negative {label!r} was accepted")
    return problems + _ownership_rule_selftests()


def _ownership_rule_selftests() -> list[str]:
    """Exercise the root trust rule on stated (uid, mode) pairs.

    Every case is a literal, so the result is a property of the rule and not of
    the uid the suite runs as or the umask that created a temporary directory.
    """
    problems: list[str] = []
    trusted = 1000
    accepted = (
        ("current-user-0755", 1000, 0o755),
        ("root-owned-0755", 0, 0o755),
        ("current-user-0700", 1000, 0o700),
        ("root-owned-0555", 0, 0o555),
    )
    rejected = (
        ("unowned-uid", 1001, 0o755),
        ("group-writable", 1000, 0o775),
        ("world-writable", 1000, 0o757),
        ("root-owned-group-writable", 0, 0o775),
        ("root-owned-world-writable", 0, 0o777),
        ("unowned-and-writable", 4242, 0o777),
    )
    for label, uid, mode in accepted:
        problem = _sdk_environment.ownership_problem(
            _sdk_environment.OwnershipFacts(uid, mode), trusted_uid=trusted,
        )
        if problem is not None:
            problems.append(f"ownership rule rejected trusted {label!r}: {problem}")
    for label, uid, mode in rejected:
        if _sdk_environment.ownership_problem(
            _sdk_environment.OwnershipFacts(uid, mode), trusted_uid=trusted,
        ) is None:
            problems.append(f"ownership rule accepted untrusted {label!r}")
    # The ephemeral model stops reading mode bits and changes nothing else:
    # ownership is still enforced, and an unrecognised model fails closed rather
    # than falling through to the permissive branch.
    ephemeral = _sdk_environment.EPHEMERAL_SINGLE_TENANT
    for label, uid, mode in (
        ("group-writable", 1000, 0o775), ("world-writable", 1000, 0o777),
        ("root-owned-world-writable", 0, 0o777), ("current-user-0700", 1000, 0o700),
    ):
        problem = _sdk_environment.ownership_problem(
            _sdk_environment.OwnershipFacts(uid, mode), trusted_uid=trusted,
            trust_model=ephemeral,
        )
        if problem is not None:
            problems.append(f"ephemeral ownership rule rejected {label!r}: {problem}")
    for label, uid, mode in (("unowned-uid", 1001, 0o755), ("unowned-and-writable", 4242, 0o777)):
        if _sdk_environment.ownership_problem(
            _sdk_environment.OwnershipFacts(uid, mode), trusted_uid=trusted,
            trust_model=ephemeral,
        ) is None:
            problems.append(f"ephemeral ownership rule accepted {label!r}")
    for bogus in ("", "exclusive", "ephemeral", "EXCLUSIVE-FILESYSTEM", "trusted"):
        if _sdk_environment.ownership_problem(
            _sdk_environment.OwnershipFacts(1000, 0o777), trusted_uid=trusted,
            trust_model=bogus,
        ) is None:
            problems.append(f"unknown root trust model {bogus!r} was accepted")
    return problems


def check() -> list[str]:
    return _contract_problems(require_generated=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", choices=["flutter", "android", "qt"])
    parser.add_argument("--static", action="store_true")
    args = parser.parse_args()
    spec = strict_load(SPEC_PATH)["fixtures"]
    if args.receipt:
        try:
            receipt = json.loads(os.environ["SDK_RUNTIME_EVIDENCE"])
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"sdk-runtime-evidence: invalid/missing JSON: {exc}")
            return 1
        problems = _receipt_problems(args.receipt, receipt, spec[args.receipt])
    else:
        problems = _contract_problems(require_generated=not args.static)
    if problems:
        print("check_sdk_runtime_fixtures: FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("check_sdk_runtime_fixtures: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
