"""Switch boundary profiles."""

from typing import Annotated

import typer

from agent_boundary import session
from agent_boundary.cli.session import require_dir, write


def command(ctx: typer.Context, profile: Annotated[str, typer.Argument(help="Profile to activate.")]) -> None:
    """Switch profile and regenerate this session's pinned policy."""
    current = session.read(require_dir(ctx))
    write(ctx, profile, current.state if current else "on")
