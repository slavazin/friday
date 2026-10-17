"""Git adapter for `VersionControl`, plus a recording fake for dry runs and tests."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path


class GitVcs:
    def __init__(self, repo: str | Path) -> None:
        self.repo = Path(repo)

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, paths: Sequence[str], message: str) -> str:
        self._git("add", "--", *paths)
        self._git("commit", "-m", message, "--", *paths)
        return self._git("rev-parse", "HEAD")


@dataclass
class RecordingVcs:
    commits: list[tuple[tuple[str, ...], str]] = field(default_factory=list)

    def commit(self, paths: Sequence[str], message: str) -> str:
        self.commits.append((tuple(paths), message))
        return f"fake-{len(self.commits)}"
