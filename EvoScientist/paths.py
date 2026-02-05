"""Path resolution utilities for EvoScientist runtime directories."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


def _expand(path: str) -> Path:
    return Path(path).expanduser()


def _env_path(key: str) -> Path | None:
    value = os.getenv(key)
    if not value:
        return None
    return _expand(value)


# Workspace root: directly under cwd (no hidden .evoscientist layer)
WORKSPACE_ROOT = _env_path("EVOSCIENTIST_WORKSPACE_DIR") or (Path.cwd() / "workspace")

RUNS_DIR = _env_path("EVOSCIENTIST_RUNS_DIR") or (WORKSPACE_ROOT / "runs")
MEMORY_DIR = _env_path("EVOSCIENTIST_MEMORY_DIR") or (WORKSPACE_ROOT / "memory")
USER_SKILLS_DIR = _env_path("EVOSCIENTIST_SKILLS_DIR") or (WORKSPACE_ROOT / "skills")


def ensure_dirs() -> None:
    """Create runtime directories if they do not exist."""
    for path in (WORKSPACE_ROOT, RUNS_DIR, MEMORY_DIR, USER_SKILLS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def default_workspace_dir() -> Path:
    """Default workspace for non-CLI usage."""
    return WORKSPACE_ROOT


def new_run_dir(session_id: str | None = None) -> Path:
    """Create a new run directory name under RUNS_DIR (path only)."""
    if session_id is None:
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    return RUNS_DIR / session_id
