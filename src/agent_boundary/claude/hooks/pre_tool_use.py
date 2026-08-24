#!/usr/bin/env python3
"""
Claude PreToolUse hook that sandboxes sessions with nono.

Design:
  - Native Claude Code sandbox: OFF. This hook is the single policy engine,
    and the nono profile is the single source of truth.
  - Bash commands are rewritten to run under `nono wrap` with that profile.
    [OS-enforced tier]
  - Read/Edit/Write/NotebookEdit are gated by *probing* the declared path from
    inside `nono wrap`: the probe opens it the way the tool would and reports
    the kernel's answer. `nono why` is deliberately not used — it models the
    policy instead of enforcing it, and gets two cases wrong that matter here:
    it ignores `filesystem.deny` paths, and it calls a symlink allowed when the
    target is outside the sandbox. [declared-input tier]
  - A fixed allowlist of tools that neither read out-of-sandbox file contents
    nor mutate the host (planning, task orchestration, owner-accepted network)
    is allowed as-is. [safe tier]
  - Every other tool ASKS: it runs in-process, outside the nono jail, so it
    could read secrets the filesystem policy would otherwise block. Asking is
    also our only way to notice unknown tools — Claude Code exposes no API for
    a hook to enumerate the session's tools up front, so discovery is reactive.
  - Any internal error DENIES (fail closed).
  - `agent-boundary off` turns all of the above into a no-op for that session.

This hook only ever READS its boundary. Generating a policy means parsing YAML and
shelling out to git; doing that here would put it on the path of every single tool
call, and would turn one malformed profile into an unexplained deny on everything.
the SessionStart hook and CLI commands are the only writers, so a policy
that is missing or unparseable is reported as such, naming `agent-boundary reload`.
Policies are PINNED: editing a profile does not affect running sessions.

The one thing it writes is its own scratch state — the per-session AWS and
GitHub credential caches — which is not policy: a corrupt or missing cache only
costs a re-export, never a wrong verdict.
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

# Per-session state, written by the two writers above:
#   <state>/sessions/<session-id>/boundary.json   profile name, state, self_edit, aws_profile, github
#   <state>/sessions/<session-id>/policy.json     the generated nono profile
# where <state> is ${XDG_STATE_HOME:-~/.local/state}/agent-boundary. Scoped by
# session so one can be escalated while another stays enforced, the way terminal
# tabs work; the SessionStart hook prunes session dirs idle past 30 days. Unless
# the profile sets self_edit, its own `filesystem.deny` covers the state root:
# sandboxed code can neither read a session's config nor write one.
# Anything other than state "off" enforces, so a truncated or missing file cannot
# silently disable the boundary.
# The toggle, run by absolute path when a Bash call asks for it. Never resolved
# through PATH: PATH[0] is the workdir's .venv/bin, which the agent can write, so
# a lookup would run *its* agent-boundary unwrapped instead of ours.
TOGGLE = claude_session.executable()
# Deliberately strict: the bare command, a subcommand, and at most one profile
# name — lowercase words, digits and hyphens only, no dots or slashes. No
# separator, redirect, substitution, or assignment can ride along, so this is
# `AgentBoundary(args)` spelled as a Bash call, not a general escape hatch.
# Anything else falls through to the normal wrapped path, where the kernel decides.
TOGGLE_RE = re.compile(r"\Aagent-boundary(?:\s+[a-z][a-z0-9-]*){0,2}\Z")


# The sandbox must not be able to rewrite its own boundary, unless the profile
# says self_edit: neither the checked-in plugin glue nor the state root holding
# the installed runtimes, sessions, and uv cache. The generated policy's
# `filesystem.deny` already blocks these subtrees and the probe reports that
# faithfully, so this check is redundant *while those entries exist* — it is kept
# because it does not depend on the policy's contents. Generate a policy without
# the denies and probing alone goes back to answering "allowed" here, silently.
# Resolved per call, not at import: plugin_root() needs the hook environment,
# and raising without it lands in the fail-closed handler.
def self_dirs() -> tuple[Path, ...]:
    return (claude_session.plugin_root(), state_dir())


# tool -> operation the tool performs ("read" or "readwrite")
PATH_TOOLS = {"Read": "read", "Edit": "readwrite", "Write": "readwrite", "NotebookEdit": "readwrite"}

# Piped to the venv's immutable base Python rather than run by path: the profile
# denies both probe.py and the protected runtime containing sys.executable.
PROBE = Path(probe.__file__)
PROBE_PYTHON = Path(sys.base_prefix) / "bin/python3"

# Tools allowed as-is. A tool belongs here only if it can neither read file
# contents from outside the sandbox nor mutate the host — the secrets boundary
# is "no out-of-sandbox contents", not "no network egress" (owner's call).
# Deliberately EXCLUDED so they ask, because they run in-process outside the
# nono jail and CAN read arbitrary file contents: Grep, LSP. (Native macOS/Linux
# builds ship no Grep/Glob tool at all — the shell snapshot shadows `grep`/`find`
# with embedded ugrep/bfs, so they arrive as Bash and get wrapped. Keep the
# exclusion anyway: Node builds and other harnesses still expose the tools.)
# Also excluded:
# PowerShell (executes commands but, unlike Bash, is NOT wrapped by this hook —
# approving it bypasses the sandbox entirely), Cron*/*Worktree (host mutation),
# and all MCP tools (mcp__*, deferred pending the owner's MCP decision).
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


def session_config(directory: Path | None) -> dict:
    """This session's boundary.json, or {} if there is none to read."""
    if directory is None:
        return {}
    try:
        cfg = json.loads((directory / "boundary.json").read_text())
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def handle_toggle(tin: dict, directory: Path):
    """Run the mode toggle for a Bash call that is *exactly* the toggle command.

    Unwrapped, because flipping the mode means writing into the skill directory
    that the sandbox denies. Safe because TOGGLE_RE admits no shell syntax and the
    program is our own absolute path, so nothing else can execute here, and
    because "ask" puts the decision in front of the user rather than letting the
    agent escalate itself.
    """
    argv = [str(TOGGLE), "--session-dir", str(directory), *(tin.get("command") or "").split()[1:]]
    action = (tin.get("command") or "").removeprefix("agent-boundary").strip() or "(show current)"
    respond(
        "ask",
        f"[gate] sandbox mode change for this session: {action}",
        {**tin, "command": " ".join(shlex.quote(a) for a in argv)},
    )


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


def handle_bash(tin: dict, cfg: dict, directory: Path | None, cwd: str):
    cmd = (tin.get("command") or "").strip()
    if not cmd:
        deny("[gate] Bash: empty command")
    wrap = wrap_argv(directory, cwd)  # deny-checks the policy before any credential work
    prefix_parts = []
    note = ""
    if directory is not None and (kubeconfig := directory / "kubeconfig").is_file():
        prefix_parts.append(f"export KUBECONFIG={shlex.quote(str(kubeconfig))}")
    profile = cfg.get("aws_profile")
    if profile:
        if directory is None:
            deny("[gate] invalid Claude session ID")
        path, aws_note = aws.env_file(directory, profile)
        note += aws_note
        if path:
            # Source outside the jail; nono inherits the exports. Drop profiles so
            # SDKs do not try to read the denied ~/.aws after entering the jail.
            prefix_parts += [f". {shlex.quote(str(path))}", "unset AWS_PROFILE AWS_DEFAULT_PROFILE"]
    if (cfg.get("github") or {}).get("push"):
        if directory is None:
            deny("[gate] invalid Claude session ID")
        path, github_note = github.env_file(directory)
        note += github_note
        if path:
            prefix_parts.append(f". {shlex.quote(str(path))}")
    prefix = " && ".join(prefix_parts)
    wrapped = (f"{prefix} && " if prefix else "") + " ".join(shlex.quote(a) for a in [*wrap, "bash", "-c", cmd])
    respond("allow", f"[gate] Bash sandboxed with nono{note}", {**tin, "command": wrapped})


def probe_path(path: str, op: str, directory: Path | None, cwd: str) -> tuple[str, str]:
    """Ask the kernel, from inside the sandbox, whether `op` on `path` is allowed."""
    proc = subprocess.run(
        [*wrap_argv(directory, cwd), PROBE_PYTHON, "-", path, op],
        input=PROBE.read_text(),
        capture_output=True,
        text=True,
    )
    out = proc.stdout.strip()
    if proc.returncode != 0 or not out:
        # Sandbox failed to start or the probe never spoke: no verdict, fail closed.
        detail = (proc.stderr.strip() or f"probe exited {proc.returncode}").splitlines()[0]
        return "denied", f"probe inconclusive: {detail}"
    verdict, _, detail = out.partition("|")
    return verdict, detail


def handle_path_tool(tool: str, tin: dict, cfg: dict, directory: Path | None, cwd: str):
    path = tin.get("file_path") or tin.get("notebook_path")
    if not path:
        deny(f"[gate] {tool}: no path declared")
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        p = os.path.join(cwd, p)

    op = PATH_TOOLS[tool]
    if op != "read" and not cfg.get("self_edit"):
        # resolve() follows symlinks, so a link planted inside the workdir
        # cannot launder a write to the policy or this hook.
        target = Path(p).resolve()
        for self_dir in self_dirs():
            if target.is_relative_to(self_dir):
                deny(f"[gate] {tool} {p}: refusing to let the sandbox rewrite its own policy ({self_dir})")

    verdict, detail = probe_path(p, op, directory, cwd)
    if verdict != "allowed":
        deny(f"[gate] {tool} {p}: blocked by sandbox policy ({detail or 'denied'})")
    respond("allow", f"[gate] {tool} allowed by sandbox probe")


def main():
    data = json.load(sys.stdin)
    tool = data.get("tool_name", "")
    tin = data.get("tool_input", {}) or {}
    cwd = data.get("cwd") or os.getcwd()
    session_id = data.get("session_id") or ""
    directory = claude_session.session_dir(session_id)
    cfg = session_config(directory)

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
        if TOGGLE_RE.match((tin.get("command") or "").strip()):
            if directory is None:
                deny("[gate] invalid Claude session ID")
            handle_toggle(tin, directory)
        handle_bash(tin, cfg, directory, cwd)
    elif tool in PATH_TOOLS:
        handle_path_tool(tool, tin, cfg, directory, cwd)
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
