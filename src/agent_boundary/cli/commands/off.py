"""Disable boundary enforcement."""

import typer

from agent_boundary import session
from agent_boundary.cli.session import require_dir, write


def command(ctx: typer.Context) -> None:
    """Return this session to its harness's normal permissions."""
    current = session.read(require_dir(ctx))
    write(ctx, current.profile if current else "default", "off")
