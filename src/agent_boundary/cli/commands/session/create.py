"""Create an explicit boundary session directory."""

from pathlib import Path
from typing import Annotated

import typer

from agent_boundary import boundary, session


def command(
    directory: Annotated[Path, typer.Argument(help="Directory to create for this session.")],
    profile: Annotated[str, typer.Option(help="Profile to pin.")] = "default",
    workdir: Annotated[Path | None, typer.Option(help="Working directory protected by the session.")] = None,
) -> None:
    """Create a session with a pinned profile and policy."""
    try:
        config, warnings = session.write(directory, profile, "on", workdir=workdir or Path.cwd())
    except boundary.PROFILE_ERRORS as error:
        typer.echo(f"agent-boundary: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(str(directory.expanduser().resolve()))
    typer.echo(session.describe(config))
    for warning in warnings:
        typer.echo(f"agent-boundary: {warning}", err=True)
