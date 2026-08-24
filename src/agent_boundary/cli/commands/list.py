"""List available boundary profiles."""

import typer

from agent_boundary import boundary
from agent_boundary.paths import profiles_dir


def command() -> None:
    """List available boundary profiles."""
    typer.echo("\n".join(boundary.profile_names(profiles_dir())) or "(none)")
