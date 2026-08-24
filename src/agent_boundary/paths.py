"""User-configurable paths used by the harness-neutral core."""

import os
from pathlib import Path

PROFILES_ENV = "AGENT_BOUNDARY_PROFILES_DIR"
STATE_ENV = "AGENT_BOUNDARY_STATE_DIR"


def profiles_dir() -> Path:
    """The authored profile directory for this invocation."""
    raw = os.environ.get(PROFILES_ENV)
    if raw:
        return Path(raw).expanduser().resolve()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return (config_home / "agent-boundary/profiles").resolve()


def state_dir() -> Path:
    """Generated state: sessions, installed runtimes, and the private uv cache.

    The explicit override exists for tests: repointing XDG_STATE_HOME would also
    relocate nono's own protected state root and break its system grants.
    """
    raw = os.environ.get(STATE_ENV)
    if raw:
        return Path(raw).expanduser().resolve()
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return (state_home / "agent-boundary").resolve()
