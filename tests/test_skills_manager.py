"""Tests for EvoScientist.tools.skills_manager module."""

from pathlib import Path
from unittest.mock import patch

import pytest

from EvoScientist.tools.skills_manager import (
    _parse_skill_md,
    _parse_github_url,
    _is_github_url,
    _validate_skill_dir,
    install_skill,
    list_skills,
    uninstall_skill,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_skills_dir(tmp_path):
    """Create a temporary skills directory."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    return skills_dir


@pytest.fixture
def sample_skill_dir(tmp_path):
    """Create a sample skill directory with SKILL.md."""
    skill_dir = tmp_path / "sample-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        """---
name: sample-skill
description: A sample skill for testing
---

# Sample Skill

This is a sample skill for testing purposes.
"""
    )
    return skill_dir


@pytest.fixture
def sample_skill_no_frontmatter(tmp_path):
    """Create a skill directory without YAML frontmatter."""
    skill_dir = tmp_path / "no-frontmatter-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        """# No Frontmatter Skill

This skill has no YAML frontmatter.
"""
    )
    return skill_dir


# =============================================================================
# Tests for _parse_skill_md
# =============================================================================


class TestParseSkillMd:
    """Tests for _parse_skill_md function."""

    def test_parse_with_frontmatter(self, sample_skill_dir):
        skill_md = sample_skill_dir / "SKILL.md"
        result = _parse_skill_md(skill_md)

        assert result["name"] == "sample-skill"
        assert result["description"] == "A sample skill for testing"

    def test_parse_without_frontmatter(self, sample_skill_no_frontmatter):
        skill_md = sample_skill_no_frontmatter / "SKILL.md"
        result = _parse_skill_md(skill_md)

        # Should use directory name as fallback
        assert result["name"] == "no-frontmatter-skill"
        assert result["description"] == "(no description)"

    def test_parse_with_partial_frontmatter(self, tmp_path):
        skill_dir = tmp_path / "partial-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
name: my-skill
---

# My Skill
"""
        )

        result = _parse_skill_md(skill_md)
        assert result["name"] == "my-skill"
        assert result["description"] == "(no description)"


# =============================================================================
# Tests for _parse_github_url
# =============================================================================


class TestParseGithubUrl:
    """Tests for _parse_github_url function."""

    def test_parse_full_url_with_path(self):
        url = "https://github.com/owner/repo/tree/main/my-skill"
        repo, ref, path = _parse_github_url(url)

        assert repo == "owner/repo"
        assert ref == "main"
        assert path == "my-skill"

    def test_parse_full_url_without_path(self):
        url = "https://github.com/owner/repo/tree/develop"
        repo, ref, path = _parse_github_url(url)

        assert repo == "owner/repo"
        assert ref == "develop"
        assert path is None

    def test_parse_simple_repo_url(self):
        url = "https://github.com/owner/repo"
        repo, ref, path = _parse_github_url(url)

        assert repo == "owner/repo"
        assert ref is None
        assert path is None

    def test_parse_shorthand(self):
        url = "owner/repo@my-skill"
        repo, ref, path = _parse_github_url(url)

        assert repo == "owner/repo"
        assert ref is None
        assert path == "my-skill"

    def test_parse_url_without_protocol(self):
        url = "github.com/owner/repo/tree/v1.0/path/to/skill"
        repo, ref, path = _parse_github_url(url)

        assert repo == "owner/repo"
        assert ref == "v1.0"
        assert path == "path/to/skill"

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            _parse_github_url("not-a-valid-url")


# =============================================================================
# Tests for _is_github_url
# =============================================================================


class TestIsGithubUrl:
    """Tests for _is_github_url function."""

    def test_github_com_url(self):
        assert _is_github_url("https://github.com/owner/repo") is True
        assert _is_github_url("http://github.com/owner/repo/tree/main/skill") is True

    def test_shorthand(self):
        assert _is_github_url("owner/repo@skill-name") is True

    def test_local_path(self):
        assert _is_github_url("./my-skill") is False
        assert _is_github_url("/absolute/path/skill") is False
        assert _is_github_url("../relative/path") is False

    def test_other_urls(self):
        assert _is_github_url("https://gitlab.com/owner/repo") is False
        assert _is_github_url("file:///path/to/file") is False


# =============================================================================
# Tests for _validate_skill_dir
# =============================================================================


class TestValidateSkillDir:
    """Tests for _validate_skill_dir function."""

    def test_valid_skill_dir(self, sample_skill_dir):
        assert _validate_skill_dir(sample_skill_dir) is True

    def test_invalid_skill_dir_no_skillmd(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        assert _validate_skill_dir(empty_dir) is False

    def test_invalid_skill_dir_file_not_dir(self, tmp_path):
        file_path = tmp_path / "file.txt"
        file_path.write_text("not a directory")
        assert _validate_skill_dir(file_path) is False


# =============================================================================
# Tests for install_skill
# =============================================================================


class TestInstallSkill:
    """Tests for install_skill function."""

    def test_install_from_local_path(self, sample_skill_dir, temp_skills_dir):
        result = install_skill(str(sample_skill_dir), str(temp_skills_dir))

        assert result["success"] is True
        assert result["name"] == "sample-skill"
        assert "sample-skill" in result["path"]

        # Verify the skill was copied
        installed_path = Path(result["path"])
        assert installed_path.exists()
        assert (installed_path / "SKILL.md").exists()

    def test_install_nonexistent_path(self, temp_skills_dir):
        result = install_skill("/nonexistent/path", str(temp_skills_dir))

        assert result["success"] is False
        assert "does not exist" in result["error"]

    def test_install_invalid_skill_no_skillmd(self, tmp_path, temp_skills_dir):
        empty_dir = tmp_path / "empty-skill"
        empty_dir.mkdir()

        result = install_skill(str(empty_dir), str(temp_skills_dir))

        assert result["success"] is False
        assert "No SKILL.md" in result["error"]

    def test_install_replaces_existing(self, sample_skill_dir, temp_skills_dir):
        # Install first time
        result1 = install_skill(str(sample_skill_dir), str(temp_skills_dir))
        assert result1["success"] is True

        # Modify the original skill
        skill_md = sample_skill_dir / "SKILL.md"
        skill_md.write_text(
            """---
name: sample-skill
description: Modified description
---

# Modified
"""
        )

        # Install again
        result2 = install_skill(str(sample_skill_dir), str(temp_skills_dir))
        assert result2["success"] is True
        assert result2["description"] == "Modified description"


# =============================================================================
# Tests for list_skills
# =============================================================================


class TestListSkills:
    """Tests for list_skills function."""

    def test_list_empty_dir(self, temp_skills_dir):
        with patch("EvoScientist.paths.USER_SKILLS_DIR", temp_skills_dir):
            skills = list_skills(include_system=False)
            assert skills == []

    def test_list_with_skills(self, sample_skill_dir, temp_skills_dir):
        # Install a skill
        install_skill(str(sample_skill_dir), str(temp_skills_dir))

        with patch("EvoScientist.paths.USER_SKILLS_DIR", temp_skills_dir):
            skills = list_skills(include_system=False)

            assert len(skills) == 1
            assert skills[0].name == "sample-skill"
            assert skills[0].description == "A sample skill for testing"
            assert skills[0].source == "user"

    def test_list_multiple_skills(self, tmp_path, temp_skills_dir):
        # Create and install multiple skills
        for i in range(3):
            skill_dir = tmp_path / f"skill-{i}"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                f"""---
name: skill-{i}
description: Skill number {i}
---
"""
            )
            install_skill(str(skill_dir), str(temp_skills_dir))

        with patch("EvoScientist.paths.USER_SKILLS_DIR", temp_skills_dir):
            skills = list_skills(include_system=False)

            assert len(skills) == 3
            names = [s.name for s in skills]
            assert "skill-0" in names
            assert "skill-1" in names
            assert "skill-2" in names


# =============================================================================
# Tests for uninstall_skill
# =============================================================================


class TestUninstallSkill:
    """Tests for uninstall_skill function."""

    def test_uninstall_existing_skill(self, sample_skill_dir, temp_skills_dir):
        # Install first
        install_skill(str(sample_skill_dir), str(temp_skills_dir))

        with patch("EvoScientist.paths.USER_SKILLS_DIR", temp_skills_dir):
            result = uninstall_skill("sample-skill")

            assert result["success"] is True

            # Verify the skill was removed
            skill_path = temp_skills_dir / "sample-skill"
            assert not skill_path.exists()

    def test_uninstall_nonexistent_skill(self, temp_skills_dir):
        with patch("EvoScientist.paths.USER_SKILLS_DIR", temp_skills_dir):
            result = uninstall_skill("nonexistent-skill")

            assert result["success"] is False
            assert "not found" in result["error"]
