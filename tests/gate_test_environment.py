import shutil
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
WORKTREE = PACKAGE.parents[1]
SKILL = WORKTREE / ".claude/skills/agent-boundary"
PROFILES = WORKTREE / "devops/agent-boundary/profiles"


def require_gate_integration() -> None:
    if not (PROFILES.is_dir() and SKILL.is_dir() and shutil.which("nono")):
        pytest.skip(
            "gate integration tests need nono and the Softmax monorepo layout (profiles, skill)",
            allow_module_level=True,
        )
