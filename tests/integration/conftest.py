"""Shared fixtures for integration tests.

All fixtures use pytest's tmp_path, which is a fresh temporary directory
per test that pytest removes automatically after the session. No branches
are created in the real paxpy repository, so there is no shared state and
tests are safe to run in parallel.

Git identity is set per-repo (not globally) so tests work in CI environments
that have no global git config. The CI workflow also exports GIT_AUTHOR_*
environment variables as a belt-and-suspenders fallback.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import git
import pytest


@pytest.fixture()
def git_repo(tmp_path: Path) -> git.Repo:
    """Return a fresh git repository in tmp_path with a committed initial file.

    The repo has:
      - branch: main
      - user.name / user.email set locally (safe in CI with no global config)
      - one initial commit so that branch creation and three-dot diffs work
    """
    repo = git.Repo.init(tmp_path, initial_branch="main")
    _configure_identity(repo)

    # A placeholder so the initial commit is not empty
    placeholder = tmp_path / ".gitkeep"
    placeholder.write_text("", encoding="utf-8")
    repo.git.add(".")
    repo.git.commit("-m", "init")

    return repo


def _configure_identity(repo: git.Repo) -> None:
    """Set git user.name and user.email in the repo's local config."""
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test User")
        cw.set_value("user", "email", "test@example.com")


def write_py(repo_root: Path, filename: str, source: str) -> Path:
    """Write a dedented Python source file into repo_root and return its path."""
    path = repo_root / filename
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def commit_all(repo: git.Repo, message: str) -> None:
    """Stage all changes and create a commit."""
    repo.git.add(".")
    repo.git.commit("-m", message)
