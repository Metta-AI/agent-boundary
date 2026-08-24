"""Run a command inside a selected or temporary boundary."""

import os
import subprocess
import tempfile
from pathlib import Path

import typer

from agent_boundary import aws, github, kubeconfig, session
from agent_boundary.runner import wrap_argv

CONTEXT_SETTINGS = {"allow_extra_args": True, "ignore_unknown_options": True}


def run_boundary(argv: list[str], directory: Path | None) -> int:
    """Run argv with a session's pinned policy and credentials."""
    temporary_directory = None
    child_env = os.environ.copy()
    child_env.pop("AWS_PROFILE", None)
    child_env.pop("AWS_DEFAULT_PROFILE", None)
    child_env.pop("KUBECONFIG", None)
    try:
        if directory:
            config = session.read(directory)
            if config is None or not (directory / "policy.json").is_file():
                typer.echo(f"agent-boundary: no complete session at {directory}", err=True)
                return 1
        else:
            temporary_directory = tempfile.TemporaryDirectory(prefix="agent-boundary-run-")
            directory = Path(temporary_directory.name)
            config, warnings = session.write(directory, "default", "on", workdir=Path.cwd())
            for warning in warnings:
                typer.echo(f"agent-boundary: {warning}", err=True)

        kubeconfig_path = directory / kubeconfig.KUBECONFIG_FILENAME
        if kubeconfig_path.is_file():
            child_env["KUBECONFIG"] = str(kubeconfig_path)
        if config.aws_profile:
            path, note = aws.env_file(directory, config.aws_profile)
            if note:
                typer.echo(f"agent-boundary:{note}", err=True)
            if path:
                child_env.update(aws.read_env_file(path))
        if config.github and config.github.push:
            path, note = github.env_file(directory)
            if note:
                typer.echo(f"agent-boundary:{note}", err=True)
            if path:
                child_env.update(aws.read_env_file(path))

        return subprocess.run(
            [*wrap_argv(directory, config.workdir), *argv],
            env=child_env,
            cwd=config.workdir,
        ).returncode
    finally:
        if temporary_directory:
            temporary_directory.cleanup()


def command(ctx: typer.Context) -> None:
    """Run a command inside the boundary with its AWS credentials."""
    if not ctx.args:
        typer.echo("usage: agent-boundary run <command> [args...]", err=True)
        raise typer.Exit(2)
    directory = ctx.obj if isinstance(ctx.obj, Path) else None
    raise typer.Exit(run_boundary(ctx.args, directory))
