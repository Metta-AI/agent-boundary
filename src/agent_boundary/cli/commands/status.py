"""Show the current session's boundary."""

import typer

from agent_boundary import session
from agent_boundary.cli.session import require_dir


def command(ctx: typer.Context) -> None:
    """Show the current session's profile and state."""
    config = session.read(require_dir(ctx))
    typer.echo(session.describe(config) if config else "no boundary for this session; run: agent-boundary reload")
