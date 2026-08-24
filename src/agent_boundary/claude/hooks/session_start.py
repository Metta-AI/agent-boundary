"""Claude SessionStart hook: create a pinned boundary policy.

One of only two writers (the other is the CLI). PreToolUse never generates —
see the module docstring in boundary.py for why. The protected SessionStart
bootstrap installs and invokes this package outside the workspace virtualenv.
"""

import json
import os
import shlex
import shutil
import sys
import time
from pathlib import Path

from agent_boundary import boundary, session
from agent_boundary.claude import session as claude_session
from agent_boundary.paths import state_dir

DEFAULT_PROFILE = "default"
KUBECONFIG_FILENAME = "kubeconfig"
SESSION_MAX_IDLE_SECONDS = 30 * 24 * 3600


def export_kubeconfig(directory: Path) -> None:
    path = directory / KUBECONFIG_FILENAME
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if path.is_file() and env_file:
        with Path(env_file).open("a") as file:
            file.write(f"export KUBECONFIG={shlex.quote(str(path))}\n")


def prune_stale_sessions(current: Path) -> None:
    """Drop session dirs idle past 30 days; they no longer die with a worktree.

    Idle means the newest mtime among the dir and its direct children: live
    sessions keep rewriting credential env files, so they stay fresh. Pruning a
    genuinely idle-but-resumed session costs one `agent-boundary reload`.
    """
    sessions = state_dir() / "sessions"
    if not sessions.is_dir():
        return
    cutoff = time.time() - SESSION_MAX_IDLE_SECONDS
    for directory in sessions.iterdir():
        if directory == current or not directory.is_dir():
            continue
        try:
            newest = max((p.stat().st_mtime for p in (directory, *directory.iterdir())), default=0.0)
        except OSError:  # a concurrent SessionStart pruned it first
            continue
        if newest < cutoff:
            shutil.rmtree(directory, ignore_errors=True)


def main() -> None:
    data = json.load(sys.stdin)
    session_id = data.get("session_id") or ""
    directory = claude_session.session_dir(session_id)
    if directory is None:
        # Nothing to key a session dir on; PreToolUse fails closed on a missing
        # policy, so say why rather than leaving a silent mystery.
        sys.stderr.write("[agent-boundary] no session_id in SessionStart payload; not generating a policy\n")
        return

    prune_stale_sessions(directory)

    if (directory / "boundary.json").is_file() and (directory / "policy.json").is_file():
        # A resumed session keeps the boundary it was running under: silently
        # regenerating could widen it mid-session, and narrowing it would break
        # commands that worked a minute ago. `agent-boundary reload` is the way.
        export_kubeconfig(directory)
        return

    workdir = Path(data.get("cwd") or Path.cwd())
    config, warnings = session.write(
        directory,
        DEFAULT_PROFILE,
        "on",
        workdir=workdir,
        protected_paths=(claude_session.plugin_root(), state_dir()),
    )
    export_kubeconfig(directory)
    note = " (SELF-EDIT: soft boundary)" if config.self_edit else ""
    sys.stderr.write(f"[agent-boundary] profile {config.profile}, state {config.state}{note}\n")
    for warning in warnings:
        sys.stderr.write(f"[agent-boundary] {warning}\n")


def entrypoint() -> None:
    try:
        main()
    except boundary.PROFILE_ERRORS as e:
        # A broken profile must not abort session startup: the boundary simply has
        # no policy, and PreToolUse denies with the reason until it is fixed.
        sys.stderr.write(f"[agent-boundary] could not generate a policy: {e}\n")


def command() -> None:
    """Initialize a Claude Code session's pinned boundary policy."""
    entrypoint()


if __name__ == "__main__":
    entrypoint()
