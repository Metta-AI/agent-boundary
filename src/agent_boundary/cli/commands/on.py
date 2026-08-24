"""Enable boundary enforcement."""

import typer

from agent_boundary import session
from agent_boundary.cli.session import require_dir, write


def command(ctx: typer.Context) -> None:
    """Enable boundary enforcement for this session."""
    current = session.read(require_dir(ctx))
    write(ctx, current.profile if current else "default", "on")
