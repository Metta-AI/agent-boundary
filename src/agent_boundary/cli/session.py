"""Typer presentation for explicit boundary session operations."""

from pathlib import Path

import typer

from agent_boundary import boundary, session
from agent_boundary.models import State


def require_dir(ctx: typer.Context) -> Path:
    if isinstance(ctx.obj, Path):
        return ctx.obj
    typer.echo("agent-boundary: no session selected; pass --session-dir /absolute/path", err=True)
    raise typer.Exit(1)


def write(ctx: typer.Context, profile: str, state: State) -> None:
    directory = require_dir(ctx)
    try:
        config, warnings = session.write(directory, profile, state)
    except boundary.PROFILE_ERRORS as error:
        typer.echo(f"agent-boundary: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(session.describe(config))
    for warning in warnings:
        typer.echo(f"agent-boundary: {warning}", err=True)
    if state == "off":
        typer.echo("boundary OFF — normal harness permissions apply (not 'allow all')")
