"""Lazy command group for Claude hook events."""

import click

from agent_boundary.cli.app import LazyGroup


class HookGroup(LazyGroup):
    lazy_commands = {
        "PreToolUse": "agent_boundary.claude.hooks.pre_tool_use",
        "SessionStart": "agent_boundary.claude.hooks.session_start",
    }


def build_command() -> click.Command:
    return HookGroup.build_command("hook", "Run a Claude Code hook event.")
