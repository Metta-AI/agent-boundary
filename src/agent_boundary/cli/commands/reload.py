"""Regenerate the current boundary."""

import typer

from agent_boundary import session
from agent_boundary.cli.session import require_dir, write


def command(ctx: typer.Context) -> None:
    """Regenerate this session's pinned policy from its profile."""
    current = session.read(require_dir(ctx))
    write(ctx, current.profile if current else "default", current.state if current else "on")
