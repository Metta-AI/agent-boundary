"""Lazy command group for Claude-specific integrations."""

import click
import typer
from typer.main import get_command

from agent_boundary.cli.app import LazyGroup


class ClaudeGroup(LazyGroup):
    lazy_commands = {
        "hook": "agent_boundary.cli.commands.claude_hook_command",
        "statusline": "agent_boundary.claude.statusline",
    }


app = typer.Typer(
    name="claude",
    cls=ClaudeGroup,
    help="Run Claude Code integrations.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """Run Claude Code integrations."""


def build_command() -> click.Command:
    return get_command(app)
