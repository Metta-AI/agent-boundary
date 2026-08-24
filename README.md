# agent-boundary

Harness-neutral profile validation, policy generation, session management, AWS credential injection, and `nono`
execution for OS-enforced agent boundaries.

The repository's Claude Code plugin is one integration. The core operates on explicit profile, work, and session paths;
Claude-specific payloads, tool classification, environment variables, and plugin paths live under
`agent_boundary.claude`.

## How it works

The Claude integration uses a `PreToolUse` hook as its single policy engine.

- `Bash` commands are rewritten to run under [nono](https://nono.sh/) (macOS Seatbelt / Linux Landlock), so the kernel
  enforces the boundary on the command and everything it spawns
- Tools that declare a path — `Read`, `Edit`, `Write` — run in-process, outside the jail, so the hook instead _probes_:
  it opens the path from inside the sandbox and reports the kernel's answer.
- Most other known tools are safe and invoked as is
- Unknown tools ask (if this becomes a problem, add them to the PreToolUse command's tool list)

Two consequences worth knowing, because they explain most of the design:

- **The kernel is the source of truth** Predicting a verdict in Python (or with `nono why`) gets edge cases wrong — deny
  entries, symlinks pointing outside, paths that don't exist yet — and each wrong guess is a silent hole. Probing costs
  a subprocess and is worth it.
- **Anything unrecognized is refused.** An unknown tool asks, an internal error denies, a crashed hook blocks. The hook
  can't enumerate a session's tools up front, so discovery is reactive and the default has to be "no".

## Profiles

A profile is a YAML file describing one boundary — what's readable, what's writable, which config symlinks to follow.
That's the file to read and edit; it's the source of truth, and it carries comments explaining every grant that isn't
self-evident.

Profiles are supplied through `AGENT_BOUNDARY_PROFILES_DIR`. Without it, the package reads
`${XDG_CONFIG_HOME:-~/.config}/agent-boundary/profiles`.


Profiles are **authored** and committed to the repo, then **generated** into a concrete nono policy per session, under
`${XDG_STATE_HOME:-~/.local/state}/agent-boundary/sessions/<session-id>/policy.json`, resolved against your machine
(globs expanded, symlink chains followed, the real gitdir located). That state root also holds the Claude plugin's
installed runtimes (`runtimes/<worktree-key>/`) and its private uv cache; the whole tree is denied inside the sandbox
and always safe to delete. `AGENT_BOUNDARY_STATE_DIR` overrides the state root — tests need that, because repointing
`XDG_STATE_HOME` would also relocate nono's own protected state.

Session policy generation happens at session start and on demand — never inside the hook, which only reads. Putting YAML
parsing and `git` calls on the path of every tool call would be both slow and fragile: one bad profile would turn into
an unexplained deny on everything.

A generated policy is **pinned** for the life of a session. Editing a profile does not affect running sessions; to
update the current session's policy, run `agent-boundary reload`. That's deliberate: a boundary that shifts under a
running agent is worse than one that needs a command.


### AWS credentials

`~/.aws` is denied in most profiles, but a profile can name an AWS config profile to hand to the agent:

```yaml
aws:
  profile: MyOrg/ReadonlyAccess
```

For each Bash call, the integration runs `aws configure export-credentials` outside the jail and writes the resulting
short-lived credentials to `sessions/<session-id>/aws-credentials.env` (mode 0600). The rewritten command sources that
file — by path — before it launches `nono wrap`, so the outer shell picks up the credentials and nono carries them into
the jail. The values never appear in the command itself, so they stay out of the tool-use log. `AWS_PROFILE` is unset
(otherwise the SDK tries to read the denied `~/.aws` for it) and `AWS_CONFIG_FILE` points at `/dev/null`. Credentials
are re-issued shortly before they expire.

Remove the key to hand out no AWS access at all.


### Kubernetes access

A profile can generate a minimal kubeconfig from the machine's existing `~/.kube/config`:

```yaml
kube:
  eks: true
  orbstack: true
```

`eks` selects the one local EKS cluster whose ARN belongs to `aws.profile`'s account and writes an exec user that runs
`aws eks get-token` without a profile. It therefore uses the same short-lived environment credentials described above,
while `~/.aws` stays denied. `orbstack` independently copies the local orbstack cluster, context, and user, including
its client key.

The result is `sessions/<session-id>/kubeconfig` (mode 0600), exposed as `KUBECONFIG` for the session. Selection is
local and makes no EKS API calls at session start. If an entry cannot be selected uniquely, agent-boundary warns and
omits that entry; a valid requested entry of the other kind still works.

Two things to be clear-eyed about:

- The agent legitimately holds these credentials — they end up in its environment, so it can read them. What the file
  approach buys is keeping them out of the transcript; the boundary still protects the rest of `~/.aws` (your other
  profiles, SSO cache), not the exported role.
- If the export fails (SSO token expired, say), commands still run, just without credentials; the hook's decision reason
  says so and suggests `aws sso login`. Failures are cached for a few minutes to keep the hook fast.

### GitHub credentials

Keychains are denied, which is where both `gh` and git's `osxkeychain` credential helper keep their tokens — so pushes
and `gh` calls fail inside the jail. A profile can opt in to the same inject-from-outside mechanism:

```yaml
github:
  push: true
```

For each Bash call, the integration runs `gh auth token` outside the jail and writes the token to
`sessions/<session-id>/github-credentials.env` (mode 0600), sourced the same way as the AWS file. The environment
carries `GH_TOKEN`/`GITHUB_TOKEN` for gh and API tooling, plus `GIT_CONFIG_*` entries that replace git's credential
helper with `gh auth git-credential`, which reads `GH_TOKEN` and never touches the keychain. The token is re-exported
hourly (it carries no expiry, so this picks up re-logins), and failures are cached for a few minutes and suggest
`gh auth login`.

The same clear eyes apply: the agent holds a push-capable token in its environment — the file keeps it out of the
transcript, not away from the agent. Its scope is whatever `gh auth login` granted, so narrow it there. The keychain
itself, and every other secret in it, stays denied.

## CLI

Create and exercise a session without an agent harness:

```bash
agent-boundary session create /tmp/my-boundary --profile default --workdir "$PWD"
agent-boundary --session-dir /tmp/my-boundary status
agent-boundary --session-dir /tmp/my-boundary run -- aws sts get-caller-identity
```

`AGENT_BOUNDARY_SESSION_DIR` can replace the global `--session-dir` option.

`run` answers "what would the agent see?": it wraps the command exactly the way the hook wraps agent Bash — pinned
session policy, AWS credentials injected — so `agent-boundary run aws sts get-caller-identity` shows the agent's
readonly role while your own shell keeps its own identity. It also works in a plain terminal with no session at all,
generating a throwaway policy from the default profile.

To inspect a harness-created session from a normal terminal, pass its full directory explicitly. For example, for this
repository's Claude plugin:

```bash
agent-boundary --session-dir ~/.local/state/agent-boundary/sessions/<session-id> status
agent-boundary --session-dir ~/.local/state/agent-boundary/sessions/<session-id> run -- env
```

## Harness integrations

Harness integrations are explicit subcommands. The current integration is Claude Code:

```bash
agent-boundary claude hook SessionStart
agent-boundary claude hook PreToolUse
agent-boundary claude statusline "$input"
```

The Claude plugin glue (hook wiring, protected runtime install) is not published yet; today it ships with the Softmax
monorepo.


## Working on it

Read in this order: an authored profile for the boundary itself, `src/agent_boundary/claude/hooks/pre_tool_use.py` for
the tiers and how each Claude tool is handled, then `boundary.py` for how a profile becomes a policy. `probe.py` is
small and worth reading to see what "ask the kernel" means concretely.

```bash
# From this directory; works in the monorepo and in the mirror.
uv run --extra test pytest tests -v
```

The gate integration tests (`tests/test_gate.py`) run the real gate against generated policies, so they need nono plus
the Softmax monorepo's profile and plugin layout — elsewhere they self-skip. They also need the Claude plugin
**disabled** and cannot run nested inside another filesystem sandbox. Some cases assert that a known hole is _open_. If
one starts failing because the repo got more self-contained, delete the grant rather than fixing the test.

Two conventions to preserve when changing things: the PreToolUse command stays stdlib-only on its hot path, and all hook
entrypoints run from the protected non-editable runtime—never from this repo's agent-writable `.venv`. Profile and
session models use Pydantic in the less frequent writer paths.


## Why not...

**Why not Claude's native sandbox?**

Because it's less flexible and only covers `Bash` tool.

**Why not devcontainers?**

First, we don't have a working implementation of devcontianers right now, and it's harder to do without sacrificing some
performance or security. You still need your nix store in it, uv cache, and so on, and if those are all mounted, then
devcontainers don't provide much of a safety net.

The pard part is describing the boundary, and we probably want to have multiple boundaries for different use cases, so
it's better to store the boundary definition in a readable YAML format.

Second, this hook-based implementation is intentionally backend-agnostic. It uses nono right now, but it **could** be
made to support devcontainers or remote hosts instead.

**Why not wrap the entire harness in nono/sandbox-exec?**

That's the recommended usage mode for nono.sh, and it's true that this plugin fights against it and has to do more work
to achieve goals that harness-inside-nono gets for free.

The main argument for this approach is the `!agent-boundary` script, that allows you to stretch and shrink the boundary
**without restarting the session**. So the agent can ask you to reconfigure the boundary, minimizing the friction.

Also, this approach has a more general advantage: since we put a boundary around tool calls, not around the harness, we
get a pseudo-client/server architecture. With a bit more sophistication in the future, we can keep the harness on one
machine and the environment on another, or even dynamically move the environment without restarting the session.

**Why not anthropic's [srt](https://github.com/anthropic-experimental/sandbox-runtime)?**

No strong preference; I had more experience with nono, and I liked its design. We can switch to srt or some other
backend in the future.

**Why not remove all credentials from dev machines?**

We might want to narrow down team member credentials in the future, but we probably want team member boundaries to be
wider than their agent boundaries — so the agent boundary problem exists either way.
