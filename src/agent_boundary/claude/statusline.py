"""Fast, dependency-free Claude statusline rendering."""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from agent_boundary.paths import state_dir

PLUGIN = "agent-boundary@skills-dir"
SESSION_ID_RE = re.compile(r"\A[A-Za-z0-9_-]{8,64}\Z")
GIT = shutil.which("git")
JQ = shutil.which("jq")


def git(cwd: Path, *args: str) -> str:
    if GIT is None:
        return ""
    process = subprocess.run([GIT, "-C", cwd, *args], capture_output=True, text=True)
    return process.stdout.strip() if process.returncode == 0 else ""


def jq(path: Path, expression: str) -> str:
    if JQ is None or not path.is_file():
        return ""
    process = subprocess.run([JQ, "-r", expression, path], capture_output=True, text=True)
    return process.stdout.strip() if process.returncode == 0 else ""


def render(input_json: str) -> str:
    if JQ is None:
        return ""
    data = json.loads(input_json)
    workspace = data.get("workspace")
    cwd_value = workspace.get("current_dir") if isinstance(workspace, dict) else data.get("cwd")
    session_id = data.get("session_id")
    if not isinstance(cwd_value, str) or not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        return ""

    cwd = Path(cwd_value)
    root_value = git(cwd, "rev-parse", "--show-toplevel")
    common_dir = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not root_value or not common_dir:
        return ""
    root = Path(root_value)
    main = Path(common_dir).parent
    skill = root / ".claude/skills/agent-boundary"
    if not skill.is_dir():
        return ""

    enabled = ""
    for settings in (
        root / ".claude/settings.local.json",
        main / ".claude/settings.local.json",
        root / ".claude/settings.json",
        main / ".claude/settings.json",
    ):
        enabled = jq(
            settings,
            f'if .enabledPlugins | has("{PLUGIN}") then .enabledPlugins["{PLUGIN}"] else empty end',
        )
        if enabled:
            break

    values = jq(
        state_dir() / "sessions" / session_id / "boundary.json",
        '[.state // "", .profile // "?", .self_edit // false] | .[]',
    ).splitlines()
    state, profile, self_edit = (values + ["", "?", "false"])[:3]

    if enabled != "true" or state == "off":
        return "\033[31mboundary:off\033[0m"
    if self_edit == "true":
        return f"\033[33mboundary:{profile}\033[0m"
    return f"\033[32mboundary:{profile}\033[0m"


def entrypoint(input_json: str) -> None:
    sys.stdout.write(render(input_json))


def command(input_json: str) -> None:
    """Render the boundary segment from Claude Code's statusline JSON."""
    entrypoint(input_json)
