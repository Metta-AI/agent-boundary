"""Lazy command group for Claude hook events."""

import click
import typer
from typer.main import get_command

from agent_boundary.cli.app import LazyGroup


class HookGroup(LazyGroup):
    lazy_commands = {
        "PreToolUse": "agent_boundary.claude.hooks.pre_tool_use",
        "SessionStart": "agent_boundary.claude.hooks.session_start",
    }


app = typer.Typer(
    name="hook",
    cls=HookGroup,
    help="Run a Claude Code hook event.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """Run a Claude Code hook event."""


def build_command() -> click.Command:
    return get_command(app)
