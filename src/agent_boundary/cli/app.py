"""Root Typer application with commands imported on demand."""

import importlib
from pathlib import Path
from typing import Annotated, ClassVar

import click
import typer
from typer.core import TyperGroup
from typer.main import get_command


class LazyGroup(TyperGroup):
    """Resolve each command from its module only when Click needs it."""

    lazy_commands: ClassVar[dict[str, str]] = {}

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted({*super().list_commands(ctx), *self.lazy_commands})

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        if command := super().get_command(ctx, name):
            return command
        module_name = self.lazy_commands.get(name)
        if module_name is None:
            return None

        module = importlib.import_module(module_name)
        if factory := getattr(module, "build_command", None):
            command = factory()
        else:
            command_app = typer.Typer(add_completion=False)
            command_app.command(
                name=name,
                context_settings=getattr(module, "CONTEXT_SETTINGS", None),
            )(module.command)
            command = get_command(command_app)
        self.add_command(command, name)
        return command


class RootGroup(LazyGroup):
    lazy_commands = {
        "claude": "agent_boundary.cli.commands.claude_command",
        "list": "agent_boundary.cli.commands.list",
        "off": "agent_boundary.cli.commands.off",
        "on": "agent_boundary.cli.commands.on",
        "reload": "agent_boundary.cli.commands.reload",
        "run": "agent_boundary.cli.commands.run",
        "status": "agent_boundary.cli.commands.status",
        "session": "agent_boundary.cli.commands.session_command",
        "use": "agent_boundary.cli.commands.use",
    }


app = typer.Typer(
    cls=RootGroup,
    help="Create, inspect, and run OS-enforced agent boundaries.",
    invoke_without_command=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


@app.callback()
def main(
    ctx: typer.Context,
    session_dir: Annotated[
        Path | None,
        typer.Option(
            "--session-dir",
            envvar="AGENT_BOUNDARY_SESSION_DIR",
            help="Session directory to inspect or modify.",
            file_okay=False,
        ),
    ] = None,
) -> None:
    """Create, inspect, and run OS-enforced agent boundaries."""
    if session_dir is None:
        from agent_boundary import session  # noqa: PLC0415

        session_dir = session.current_dir()
    ctx.obj = session_dir.expanduser().resolve() if session_dir else None
    if ctx.invoked_subcommand is None:
        from agent_boundary.cli.commands.status import command  # noqa: PLC0415

        command(ctx)
