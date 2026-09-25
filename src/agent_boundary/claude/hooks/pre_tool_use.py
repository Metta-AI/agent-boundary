#!/usr/bin/env python3
"""Enforce a pinned nono policy for Claude PreToolUse events.

Bash runs under nono. File tools are probed inside nono because `nono why`
ignores denies and symlink targets. Safe tools run directly; unknown tools ask.
Internal errors deny. Only SessionStart and CLI commands write policy, so
profile parsing and git discovery stay off the per-tool path.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Literal, NoReturn

from agent_boundary import aws, github, probe
from agent_boundary.claude import session as claude_session
from agent_boundary.paths import state_dir
from agent_boundary.runner import MissingPolicyError
from agent_boundary.runner import wrap_argv as boundary_argv

# Run the trusted installed toggle, never a writable workdir PATH entry.
TOGGLE = claude_session.executable()
# The bare command, subcommand, and optional profile cannot contain shell syntax.
TOGGLE_RE = re.compile(r"\Aagent-boundary(?:\s+[a-z][a-z0-9-]*){0,2}\Z")


# Independently protect the plugin and state root if a generated policy omits them.
# tool -> operation the tool performs ("read" or "readwrite")
PATH_TOOLS = {"Read": "read", "Edit": "readwrite", "Write": "readwrite", "NotebookEdit": "readwrite"}

# Pipe the probe to base Python: the policy denies the probe and runtime paths.
PROBE = Path(probe.__file__)
PROBE_PYTHON = Path(sys.base_prefix) / "bin/python3"

# These tools cannot read out-of-sandbox files or mutate the host. Grep, LSP,
# PowerShell, Cron, worktree, and MCP tools ask because they run outside nono.
SAFE_TOOLS = {
    # Planning / interaction — no filesystem or host effect
    "EnterPlanMode",
    "ExitPlanMode",
    "AskUserQuestion",
    # Task & todo orchestration — internal session state; spawned agents inherit this hook
    "Agent",
    "Task",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskOutput",
    "TaskStop",
    "TaskUpdate",
    "TodoWrite",
    # Skill / tool machinery — the invoked work itself flows back through this hook
    "Skill",
    "ToolSearch",
    "ReportFindings",
    "StructuredOutput",
    # Bash lifecycle — operate only on shells this hook already sandboxed
    "BashOutput",
    "KillShell",
    "KillBash",
    # Filename globbing — returns paths, never file contents
    "Glob",
    # Network — owner-accepted (secrets boundary is contents, not egress)
    "WebFetch",
    "WebSearch",
}


def respond(decision: Literal["allow", "ask", "deny"], reason: str = "ok", updated_input=None) -> NoReturn:
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    if updated_input is not None:
        out["hookSpecificOutput"]["updatedInput"] = updated_input
    print(json.dumps(out))
    sys.exit(0)


def deny(reason: str) -> NoReturn:
    respond("deny", reason)


def wrap_argv(directory: Path | None, cwd: str) -> list[str]:
    """`nono wrap` invocation prefix for this session's generated policy.

    Everything that used to be computed here — the gitdir, symlink chains, the
    graphite addon glob, NETRC, the shell-rc and kubeconfig grants — now lives in
    the generated policy, which the writers resolve against this machine once.
    Shared by both enforcement tiers: if the Bash tier can reach a path, the probe
    has to agree, or Read/Edit would report a boundary that Bash ignores.
    """
    if directory is None:
        deny("[gate] invalid Claude session ID")
    try:
        return boundary_argv(directory, Path(cwd))
    except MissingPolicyError as error:
        deny(f"[gate] {error}. Run `agent-boundary reload`, or restart the session to generate one.")


def main():
    data = json.load(sys.stdin)
    tool = data.get("tool_name", "")
    tin = data.get("tool_input", {}) or {}
    cwd = data.get("cwd") or os.getcwd()
    session_id = data.get("session_id") or ""
    directory = claude_session.session_dir(session_id)
    cfg = {}
    if directory is not None:
        try:
            loaded = json.loads((directory / "boundary.json").read_text())
            if isinstance(loaded, dict):
                cfg = loaded
        except (OSError, ValueError):
            pass

    # Only the exact string "off" disables enforcement; missing, malformed, or
    # unrecognized state leaves the boundary on.
    if cfg.get("state") == "off":
        # Exit silently rather than answering "allow": Claude Code reads a silent
        # hook as "no opinion" and applies the normal permission flow, so the
        # user's settings.json rules and permission mode still govern the call.
        # Answering "allow" here would auto-approve everything, which is a much
        # bigger grant than "stop sandboxing".
        sys.exit(0)

    if tool == "Bash":
        cmd = (tin.get("command") or "").strip()
        if TOGGLE_RE.match(cmd):
            if directory is None:
                deny("[gate] invalid Claude session ID")
            argv = [str(TOGGLE), "--session-dir", str(directory), *cmd.split()[1:]]
            action = cmd.removeprefix("agent-boundary").strip() or "(show current)"
            respond(
                "ask",
                f"[gate] sandbox mode change for this session: {action}",
                {**tin, "command": " ".join(shlex.quote(arg) for arg in argv)},
            )
        if not cmd:
            deny("[gate] Bash: empty command")
        wrap = wrap_argv(directory, cwd)
        assert directory is not None  # wrap_argv denies invalid sessions
        prefix_parts = []
        note = ""
        if (kubeconfig := directory / "kubeconfig").is_file():
            prefix_parts.append(f"export KUBECONFIG={shlex.quote(str(kubeconfig))}")
        if profile := cfg.get("aws_profile"):
            path, aws_note = aws.env_file(directory, profile)
            note += aws_note
            if path:
                prefix_parts += [
                    f". {shlex.quote(str(path))}",
                    "unset AWS_PROFILE AWS_DEFAULT_PROFILE",
                ]
        if (cfg.get("github") or {}).get("push"):
            path, github_note = github.env_file(directory)
            note += github_note
            if path:
                prefix_parts.append(f". {shlex.quote(str(path))}")
        prefix = " && ".join(prefix_parts)
        wrapped = " ".join(shlex.quote(arg) for arg in [*wrap, "bash", "-c", cmd])
        if prefix:
            wrapped = f"{prefix} && {wrapped}"
        respond("allow", f"[gate] Bash sandboxed with nono{note}", {**tin, "command": wrapped})
    elif tool in PATH_TOOLS:
        path = tin.get("file_path") or tin.get("notebook_path")
        if not path:
            deny(f"[gate] {tool}: no path declared")
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.join(cwd, path)
        operation = PATH_TOOLS[tool]
        if operation != "read" and not cfg.get("self_edit"):
            target = Path(path).resolve()
            for protected in (claude_session.plugin_root(), state_dir()):
                if target.is_relative_to(protected):
                    deny(f"[gate] {tool} {path}: refusing to let the sandbox rewrite its own policy ({protected})")
        process = subprocess.run(
            [*wrap_argv(directory, cwd), PROBE_PYTHON, "-", path, operation],
            input=PROBE.read_text(),
            capture_output=True,
            text=True,
        )
        output = process.stdout.strip()
        if process.returncode != 0 or not output:
            detail = (process.stderr.strip() or f"probe exited {process.returncode}").splitlines()[0]
            verdict, detail = "denied", f"probe inconclusive: {detail}"
        else:
            verdict, _, detail = output.partition("|")
        if verdict != "allowed":
            deny(f"[gate] {tool} {path}: blocked by sandbox policy ({detail or 'denied'})")
        respond("allow", f"[gate] {tool} allowed by sandbox probe")
    elif tool in SAFE_TOOLS:
        respond("allow")
    else:
        # Unknown tool: runs in-process outside the nono jail, so it could read
        # secrets the filesystem policy blocks. Ask the user and flag it on
        # stderr so an un-sandboxed tool never passes silently.
        sys.stderr.write(f"[gate] unknown tool {tool!r} not in SAFE_TOOLS; asking for confirmation\n")
        respond(
            "ask",
            f"[gate] {tool!r} is not sandboxed by this hook. Approve only if it cannot read secrets outside {cwd}.",
        )


def entrypoint() -> None:
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # fail CLOSED on any internal error
        try:
            deny(f"[gate] hook internal error, failing closed: {e!r}")
        except Exception:
            sys.stderr.write(f"gate hook fatal: {e!r}\n")
            sys.exit(2)  # exit 2 = block, stderr shown as reason


def command() -> None:
    """Enforce the boundary for one Claude Code PreToolUse event."""
    entrypoint()


if __name__ == "__main__":
    entrypoint()
