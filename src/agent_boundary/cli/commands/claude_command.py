"""Lazy command group for Claude-specific integrations."""

import click

from agent_boundary.cli.app import LazyGroup


class ClaudeGroup(LazyGroup):
    lazy_commands = {
        "hook": "agent_boundary.cli.commands.claude_hook_command",
        "statusline": "agent_boundary.claude.statusline",
    }


def build_command() -> click.Command:
    return ClaudeGroup.build_command("claude", "Run Claude Code integrations.")
