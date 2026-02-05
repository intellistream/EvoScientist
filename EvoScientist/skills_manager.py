"""Skill installation and management for EvoScientist.

This module provides functions for installing, listing, and uninstalling user skills.
Skills are installed to USER_SKILLS_DIR (./workspace/skills/).

Supported installation sources:
- Local directory paths
- GitHub URLs (https://github.com/owner/repo or .../tree/branch/path)
- GitHub shorthand (owner/repo@skill-name)

Usage:
    from EvoScientist.skills_manager import install_skill, list_skills, uninstall_skill

    # Install from local path
    install_skill("./my-skill")

    # Install from GitHub
    install_skill("https://github.com/user/repo/tree/main/my-skill")

    # List installed skills
    for skill in list_skills():
        print(skill["name"], skill["description"])

    # Uninstall a skill
    uninstall_skill("my-skill")
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from .paths import USER_SKILLS_DIR


@dataclass
class SkillInfo:
    """Information about an installed skill."""

    name: str
    description: str
    path: Path
    source: str  # "user" or "system"


def _parse_skill_md(skill_md_path: Path) -> dict[str, str]:
    """Parse SKILL.md frontmatter to extract name and description.

    SKILL.md format:
        ---
        name: skill-name
        description: A brief description...
        ---
        # Skill Title
        ...

    Returns:
        Dictionary with 'name' and 'description' keys.
    """
    content = skill_md_path.read_text(encoding="utf-8")

    # Extract YAML frontmatter
    frontmatter_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not frontmatter_match:
        # No frontmatter, use directory name
        return {
            "name": skill_md_path.parent.name,
            "description": "(no description)",
        }

    try:
        frontmatter = yaml.safe_load(frontmatter_match.group(1))
        return {
            "name": frontmatter.get("name", skill_md_path.parent.name),
            "description": frontmatter.get("description", "(no description)"),
        }
    except yaml.YAMLError:
        return {
            "name": skill_md_path.parent.name,
            "description": "(invalid frontmatter)",
        }


def _parse_github_url(url: str) -> tuple[str, str | None, str | None]:
    """Parse a GitHub URL into (repo, ref, path).

    Supports formats:
        https://github.com/owner/repo
        https://github.com/owner/repo/tree/main/path/to/skill
        github.com/owner/repo/tree/branch/path
        owner/repo@skill-name  (shorthand from skills.sh)

    Returns:
        (repo, ref_or_none, path_or_none)
    """
    # Shorthand: owner/repo@path
    if "@" in url and "://" not in url:
        repo, path = url.split("@", 1)
        return repo.strip(), None, path.strip()

    # Strip protocol and github.com prefix
    cleaned = re.sub(r"^https?://", "", url)
    cleaned = re.sub(r"^github\.com/", "", cleaned)
    cleaned = cleaned.rstrip("/")

    # Match: owner/repo/tree/ref/path...
    m = re.match(r"^([^/]+/[^/]+)/tree/([^/]+)(?:/(.+))?$", cleaned)
    if m:
        return m.group(1), m.group(2), m.group(3)

    # Match: owner/repo (no tree)
    m = re.match(r"^([^/]+/[^/]+)$", cleaned)
    if m:
        return m.group(1), None, None

    raise ValueError(f"Cannot parse GitHub URL: {url}")


def _clone_repo(repo: str, ref: str | None, dest: str) -> None:
    """Shallow-clone a GitHub repo."""
    clone_url = f"https://github.com/{repo}.git"
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [clone_url, dest]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed: {result.stderr.strip()}")


def _is_github_url(source: str) -> bool:
    """Check if the source looks like a GitHub URL or shorthand."""
    if "github.com" in source.lower():
        return True
    if "://" in source:
        return False  # Non-GitHub URL
    # Check for owner/repo@skill shorthand
    if "@" in source and "/" in source.split("@")[0]:
        return True
    # Check for owner/repo format (but not local paths like ./foo or /foo)
    if "/" in source and not source.startswith((".", "/")):
        parts = source.split("/")
        # GitHub shorthand: exactly 2 parts, both non-empty, no extensions
        if len(parts) == 2 and all(parts) and "." not in parts[0]:
            return True
    return False


def _validate_skill_dir(path: Path) -> bool:
    """Check if a directory contains a valid skill (has SKILL.md)."""
    return (path / "SKILL.md").is_file()


def install_skill(source: str, dest_dir: str | None = None) -> dict:
    """Install a skill from a local path or GitHub URL.

    Args:
        source: Local directory path or GitHub URL/shorthand.
        dest_dir: Destination directory (defaults to USER_SKILLS_DIR).

    Returns:
        Dictionary with installation result:
        - success: bool
        - name: skill name (if successful)
        - path: installed path (if successful)
        - error: error message (if failed)
    """
    dest_dir = dest_dir or str(USER_SKILLS_DIR)
    os.makedirs(dest_dir, exist_ok=True)

    if _is_github_url(source):
        return _install_from_github(source, dest_dir)
    else:
        return _install_from_local(source, dest_dir)


def _install_from_local(source: str, dest_dir: str) -> dict:
    """Install a skill from a local directory path."""
    source_path = Path(source).expanduser().resolve()

    if not source_path.exists():
        return {"success": False, "error": f"Path does not exist: {source}"}

    if not source_path.is_dir():
        return {"success": False, "error": f"Not a directory: {source}"}

    if not _validate_skill_dir(source_path):
        return {"success": False, "error": f"No SKILL.md found in: {source}"}

    # Parse SKILL.md to get the skill name
    skill_info = _parse_skill_md(source_path / "SKILL.md")
    skill_name = skill_info["name"]

    # Destination path
    target_path = Path(dest_dir) / skill_name

    # Remove existing if present
    if target_path.exists():
        shutil.rmtree(target_path)

    # Copy skill directory
    shutil.copytree(source_path, target_path)

    return {
        "success": True,
        "name": skill_name,
        "path": str(target_path),
        "description": skill_info["description"],
    }


def _install_from_github(source: str, dest_dir: str) -> dict:
    """Install a skill from a GitHub URL or shorthand."""
    try:
        repo, ref, path = _parse_github_url(source)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    with tempfile.TemporaryDirectory(prefix="evoscientist-skill-") as tmp:
        clone_dir = os.path.join(tmp, "repo")

        try:
            _clone_repo(repo, ref, clone_dir)
        except RuntimeError as e:
            return {"success": False, "error": str(e)}

        # Determine the skill source directory
        if path:
            skill_source = Path(clone_dir) / path
        else:
            skill_source = Path(clone_dir)

        if not skill_source.exists():
            return {"success": False, "error": f"Path not found in repo: {path or '/'}"}

        if not _validate_skill_dir(skill_source):
            # Maybe the repo root contains multiple skills?
            if not path:
                # Try to find skills in repo root
                found_skills = []
                for entry in os.listdir(clone_dir):
                    entry_path = Path(clone_dir) / entry
                    if entry_path.is_dir() and _validate_skill_dir(entry_path):
                        found_skills.append(entry)

                if found_skills:
                    return {
                        "success": False,
                        "error": (
                            f"Multiple skills found in repo. "
                            f"Please specify one: {', '.join(found_skills)}"
                        ),
                    }

            return {"success": False, "error": f"No SKILL.md found in: {source}"}

        # Parse skill info and copy
        skill_info = _parse_skill_md(skill_source / "SKILL.md")
        skill_name = skill_info["name"]
        target_path = Path(dest_dir) / skill_name

        if target_path.exists():
            shutil.rmtree(target_path)

        # Copy, excluding .git directory
        def ignore_git(dir_name: str, files: list[str]) -> list[str]:
            return [f for f in files if f == ".git"]

        shutil.copytree(skill_source, target_path, ignore=ignore_git)

        return {
            "success": True,
            "name": skill_name,
            "path": str(target_path),
            "description": skill_info["description"],
            "source": source,
        }


def list_skills(include_system: bool = False) -> list[SkillInfo]:
    """List all installed user skills.

    Args:
        include_system: If True, also include system (built-in) skills.

    Returns:
        List of SkillInfo objects for each installed skill.
    """
    skills: list[SkillInfo] = []

    # User skills
    user_dir = Path(USER_SKILLS_DIR)
    if user_dir.exists():
        for entry in sorted(user_dir.iterdir()):
            if entry.is_dir() and _validate_skill_dir(entry):
                skill_md = entry / "SKILL.md"
                info = _parse_skill_md(skill_md)
                skills.append(
                    SkillInfo(
                        name=info["name"],
                        description=info["description"],
                        path=entry,
                        source="user",
                    )
                )

    # System skills (optional)
    if include_system:
        from .EvoScientist import SKILLS_DIR

        system_dir = Path(SKILLS_DIR)
        if system_dir.exists():
            for entry in sorted(system_dir.iterdir()):
                if entry.is_dir() and _validate_skill_dir(entry):
                    # Skip if user has overridden this skill
                    if any(s.name == entry.name for s in skills):
                        continue
                    skill_md = entry / "SKILL.md"
                    info = _parse_skill_md(skill_md)
                    skills.append(
                        SkillInfo(
                            name=info["name"],
                            description=info["description"],
                            path=entry,
                            source="system",
                        )
                    )

    return skills


def uninstall_skill(name: str) -> dict:
    """Uninstall a user-installed skill.

    Args:
        name: Name of the skill to uninstall.

    Returns:
        Dictionary with result:
        - success: bool
        - error: error message (if failed)
    """
    user_dir = Path(USER_SKILLS_DIR)
    target_path = user_dir / name

    if not target_path.exists():
        # Try to find by directory name (in case name differs from dir name)
        found = None
        if user_dir.exists():
            for entry in user_dir.iterdir():
                if entry.is_dir() and _validate_skill_dir(entry):
                    info = _parse_skill_md(entry / "SKILL.md")
                    if info["name"] == name:
                        found = entry
                        break

        if not found:
            return {"success": False, "error": f"Skill not found: {name}"}
        target_path = found

    # Check if it's a user skill (not system)
    if not str(target_path).startswith(str(user_dir)):
        return {"success": False, "error": f"Cannot uninstall system skill: {name}"}

    # Remove the skill directory
    shutil.rmtree(target_path)

    return {"success": True, "name": name}


def get_skill_info(name: str) -> SkillInfo | None:
    """Get information about a specific skill.

    Args:
        name: Name of the skill.

    Returns:
        SkillInfo if found, None otherwise.
    """
    for skill in list_skills(include_system=True):
        if skill.name == name:
            return skill
    return None
