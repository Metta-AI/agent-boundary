"""Lazy command group for explicit session management."""

import click

from agent_boundary.cli.app import LazyGroup


class SessionGroup(LazyGroup):
    lazy_commands = {"create": "agent_boundary.cli.commands.session.create"}


def build_command() -> click.Command:
    return SessionGroup.build_command("session", "Create explicit boundary sessions.")
