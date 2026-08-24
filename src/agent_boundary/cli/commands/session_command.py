"""Lazy command group for explicit session management."""

import click
import typer
from typer.main import get_command

from agent_boundary.cli.app import LazyGroup


class SessionGroup(LazyGroup):
    lazy_commands = {"create": "agent_boundary.cli.commands.session.create"}


app = typer.Typer(
    name="session",
    cls=SessionGroup,
    help="Create explicit boundary sessions.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """Create explicit boundary sessions."""


def build_command() -> click.Command:
    return get_command(app)
