"""Shared helpers for the ci-workflows validators.

GitHub Actions YAML uses the key ``on:``, which PyYAML's safe loader parses as
the boolean ``True``. These helpers normalize that quirk and centralize the
workflow-file discovery so every validator sees the same view.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ci_workflows_tools._strict_yaml import strict_load

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# Self workflows are not reusable (they are not `on: workflow_call`). They are
# this repository applying its own catalog to itself, so they carry no catalog,
# runtime-coverage, or generated-inventory entry.
SELF_WORKFLOWS = {
    "ci.yml",
    "maintenance.yml",
    "dependabot-catalog-convergence.yml",
    "release.yml",
    "runtime-fixtures.yml",
    "runtime-fixtures-languages.yml",
    "runtime-fixtures-event-write.yml",
    "runtime-fixtures-fuzzing.yml",
    "scorecard.yml",
    "security.yml",
}


def workflow_files() -> list[Path]:
    return sorted(p for p in WORKFLOWS_DIR.glob("*.yml"))


def load_yaml(path: Path) -> dict[str, Any]:
    data = strict_load(path)
    return data if isinstance(data, dict) else {}


def get_on(doc: dict[str, Any]) -> Any:
    """Return the workflow's trigger block, tolerating the ``on`` -> True quirk."""
    for key in ("on", True):
        if key in doc:
            return doc[key]
    return None


def is_reusable(doc: dict[str, Any]) -> bool:
    on = get_on(doc)
    if isinstance(on, dict):
        return "workflow_call" in on
    if isinstance(on, list):
        return "workflow_call" in on
    return on == "workflow_call"
