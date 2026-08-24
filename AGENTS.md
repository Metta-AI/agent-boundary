# AGENTS.md — agent-boundary

This package implements harness-neutral agent boundaries plus explicit harness adapters. The Softmax monorepo's Claude
plugin installs it non-editably into a protected runtime; source edits do not affect already-installed sessions.

This directory is mirrored to the public
[Metta-AI/agent-boundary](https://github.com/Metta-AI/agent-boundary) repo; development happens in the Softmax
monorepo.


## Architecture

- `agent_boundary.boundary`: validates authored profiles and generates concrete nono policies from explicit paths.
- `agent_boundary.session`: creates, reads, and updates explicit session directories.
- `agent_boundary.runner`, `agent_boundary.aws`, and `agent_boundary.github`: harness-neutral execution and credential
  support.
- `agent_boundary.harness`: lazy current-session provider proxy; Claude is the only provider today.
- `agent_boundary.claude`: Claude payloads, tool policy, plugin paths, hooks, and statusline rendering.
- `agent_boundary.entrypoint`: fast-routes Claude hooks/statusline without loading Typer or writer dependencies.
- `agent_boundary.cli.commands`: one lazy-loaded module per command.

Profiles come from `AGENT_BOUNDARY_PROFILES_DIR`, falling back to the XDG config directory. Generated state belongs to
an explicit session directory; harness sessions, installed runtimes, and the private uv cache all live under
`${XDG_STATE_HOME:-~/.local/state}/agent-boundary/`, outside the checkout. Keep hook integrations on the installed
`agent-boundary <harness> ...` interface and never import workspace `.venv` code from a protected hook.

## Checks

```bash
# From this directory. The gate integration tests self-skip where they cannot
# run (they need nono and authored profiles).
uv run --extra test pytest tests -v
```

