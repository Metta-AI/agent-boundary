"""Claude session and protected plugin path discovery."""

import os
import re
import sys
from pathlib import Path

from agent_boundary.paths import state_dir

SESSION_ID_RE = re.compile(r"\A[A-Za-z0-9_-]{8,64}\Z")


def plugin_root() -> Path:
    """The checked-in plugin glue. Only hook invocations carry the env var."""
    value = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not value:
        raise RuntimeError("CLAUDE_PLUGIN_ROOT is not set; not running as a Claude plugin hook")
    return Path(value).resolve()


def session_dir(session_id: str) -> Path | None:
    if not SESSION_ID_RE.match(session_id):
        return None
    return state_dir() / "sessions" / session_id


def current_session_dir() -> Path | None:
    return session_dir(os.environ.get("CLAUDE_CODE_SESSION_ID", ""))


def executable() -> Path:
    # Self-referential: hooks always run from the installed runtime, so the
    # trusted binary is the one running right now — never a PATH lookup.
    return Path(sys.prefix).resolve() / "bin/agent-boundary"
