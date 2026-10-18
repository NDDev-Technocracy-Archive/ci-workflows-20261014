#!/usr/bin/env python3
"""Keep GDS module verification hermetic and executable from a clean clone."""
from __future__ import annotations

from pathlib import Path

from ci_workflows_tools._strict_yaml import strict_load

REPO_ROOT = Path(__file__).resolve().parent.parent


def check() -> list[str]:
    problems: list[str] = []
    anchor = strict_load(REPO_ROOT / ".gds" / "repository.yaml")
    commands = ((anchor.get("verification") or {}).get("commands") or {})
    if commands.get("test") != ["scripts/validate_module.sh"]:
        problems.append("module test lane must call only scripts/validate_module.sh")
    wrapper = REPO_ROOT / "scripts" / "validate_module.sh"
    if not wrapper.is_file() or wrapper.is_symlink():
        return problems + ["hermetic module verification wrapper is missing or unsafe"]
    text = wrapper.read_text(encoding="utf-8")
    required = (
        "UV_VERSION=0.13.1",
        'PYTHON_ENV="$ROOT/.venv"',
        "PYTHON_ENV_OWNED=0",
        "python find 3.13.14",
        "-m venv --copies",
        "--require-hashes -r requirements-ci.txt",
        "check_python_execution_contract.py",
        "--launch validate_all.py -- --tier core",
    )
    for marker in required:
        if marker not in text:
            problems.append(f"module verification wrapper omits {marker!r}")
    if "python3 -I -B scripts/validate_all.py" in text:
        problems.append("module verification bypasses the repository package launcher")
    return problems
