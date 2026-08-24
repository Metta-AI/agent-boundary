# agent-boundary Claude Code plugin

An opt-in, OS-enforced boundary around Claude Code sessions: the agent can work freely in your checkout while your
credentials stay out of reach.

The point is to make "let it run without approving every command" a reasonable default, instead of choosing between
approving everything by hand and YOLO mode.

For profile generation, AWS credential injection, the standalone CLI, implementation details, and design rationale, see
the [agent-boundary package README](https://github.com/Metta-AI/agent-boundary#readme).

## Security note

This raises the floor against accidental misuse; it is not containment. Your profile is the boundary: read it, and keep
its tradeoffs documented in comments beside each grant. The
[starter profile](https://github.com/Metta-AI/agent-boundary/blob/main/examples/default.yaml) is the commented
reference.

## Installing

Requirements: [uv](https://docs.astral.sh/uv/), [nono](https://nono.sh/), and `jq` (statusline only).

```text
/plugin marketplace add Metta-AI/agent-boundary
/plugin install agent-boundary@agent-boundary
```

Enable it at the user scope and it applies to every project you open. Then give it a profile: copy the
[starter profile](https://github.com/Metta-AI/agent-boundary/blob/main/examples/default.yaml) to
`${XDG_CONFIG_HOME:-~/.config}/agent-boundary/profiles/default.yaml` and adjust it, or point
`AGENT_BOUNDARY_PROFILES_DIR` at a directory of profiles. Start a new session.

You only need to do it once. If you ever need to temporarily disable the boundary, use `!agent-boundary off` - then the
plugin will switch to passthrough mode.

If you're working on the plugin itself, and accidentally lock yourself out of tool use, then you will have to switch it
off in `/plugins` or in Claude Code settings. Don't forget to reenable it after you fix the problem.

The first session in each project installs the Python package non-editably into a protected per-project runtime — from
the uv workspace named by `AGENT_BOUNDARY_SOURCE_DIR` when that is set (the Softmax monorepo's dev shell exports it),
otherwise from the public repo at the revision pinned in `bin/bootstrap`. Later sessions reuse the runtime without
consulting package source. If that first install fails, the gate fails closed.

All generated state lives outside the checkout, under `${XDG_STATE_HOME:-~/.local/state}/agent-boundary/`:

```text
sessions/<session-id>/   boundary.json, policy.json, kubeconfig, credential env files
runtimes/<project-key>/  one installed runtime per project/worktree
uv-cache/                private uv cache, so installs never trust the agent-writable ~/.cache/uv
bin/agent-boundary       stable symlink to the last-installed runtime's executable
```

The whole tree is denied inside the sandbox, and deleting it is always safe: sessions regenerate at the next
SessionStart and runtimes reinstall. Session dirs idle for 30 days are pruned automatically at session start.

The plugin glue knows only the installed executable's stable hook interface: `agent-boundary claude hook SessionStart`
and `agent-boundary claude hook PreToolUse`. All of it lives in one script: both hooks run through `bin/bootstrap`,
which locates the runtime, installs it when missing, and execs the hook; `bin/bootstrap install` always removes and
recreates the runtime.

### In your statusline

`agent-boundary claude statusline` prints one colored token (`boundary:default`, `boundary:self-edit`, or
`boundary:off`) and nothing else. Snippet for your statusline script:

```bash
# input=$(cat) # if you're starting from scratch, otherwise you already have it
AGENT_BOUNDARY_BIN="${XDG_STATE_HOME:-$HOME/.local/state}/agent-boundary/bin/agent-boundary"
if [ -e "$AGENT_BOUNDARY_BIN" ]; then
    boundary=$("$AGENT_BOUNDARY_BIN" claude statusline "$input")
    [ -n "$boundary" ] && boundary="$boundary "
fi
```

It stays silent where it doesn't apply, so it is safe in a statusline shared across repos. Statuslines are per-user, so
this is opt-in by nature.

## How the Claude integration works

A `PreToolUse` hook is the single policy engine.

- `Bash` commands are rewritten to run under [nono](https://nono.sh/) (macOS Seatbelt / Linux Landlock), so the kernel
  enforces the boundary on the command and everything it spawns
- Tools that declare a path — `Read`, `Edit`, `Write` — run in-process, outside the jail, so the hook instead _probes_:
  it opens the path from inside the sandbox and reports the kernel's answer.
- Most other known tools are safe and invoked as is
- Unknown tools ask, internal errors deny, and a crashed hook blocks the call.

The generated policy is pinned for the life of a session. Editing a profile does not affect running sessions; run
`agent-boundary reload` to update the current one.

## Using it

```text
agent-boundary                 # show this session's profile and state
agent-boundary off             # stop enforcing, for this session
agent-boundary on              # resume
agent-boundary use <profile>   # switch profile
agent-boundary reload          # regenerate after editing a profile
agent-boundary list            # available profiles
agent-boundary run <cmd>...    # run one command inside the boundary
```

Type it with a `!` prefix to run it directly, or just ask Claude to run it — the hook recognizes the exact command, runs
it outside the sandbox, and asks you first. Nothing with shell syntax in it qualifies.

`run` answers "what would the agent see?": it wraps the command exactly the way the hook wraps agent Bash — pinned
session policy, AWS credentials injected — so `!agent-boundary run aws sts get-caller-identity` shows the agent's
readonly role while your own shell keeps its own identity.

**State is per session**, the way terminal tabs are: one session can be escalated while another stays enforced. It lives
in `${XDG_STATE_HOME:-~/.local/state}/agent-boundary/sessions/<session-id>/`.

**`off` is not "allow everything".** The hook goes silent, Claude Code reads that as "no opinion", and your normal
`settings.json` permissions and permission mode take over.

## Editing profiles or agent-boundary codebase

### Self-editing profiles

The default profile protects the plugin, generated policy, and installed runtime. A profile with `self_edit: true`
deliberately lifts that protection, making it a soft boundary that the agent can switch or disable itself.

Whenever you or the agent edits the profile, you need to run `!agent-boundary reload` to regenerate the policy used in
the current session.

### Updating the plugin runtime

Source edits deliberately do not affect the protected runtime. Reinstall it and start a new Claude session — deleting
`${XDG_STATE_HOME:-~/.local/state}/agent-boundary/runtimes/` always works (the next session reinstalls), or run the
installed plugin's `bin/bootstrap install` directly.

Editing the boundary while it is enforcing itself can lock up your session: a crashed hook blocks every tool call,
including the one that would fix it. Use a `self_edit` profile, or disable the plugin first.
