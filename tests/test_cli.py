"""CLI layout and lazy-loading tests."""

import os
import subprocess
import sys

PYTHON_ENV = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}


def test_root_app_does_not_import_commands() -> None:
    code = """
import sys
import agent_boundary.cli.app

loaded = set(sys.modules)
commands = {
    "agent_boundary.cli.commands.list",
    "agent_boundary.cli.commands.claude_command",
    "agent_boundary.cli.commands.off",
    "agent_boundary.cli.commands.on",
    "agent_boundary.cli.commands.reload",
    "agent_boundary.cli.commands.run",
    "agent_boundary.cli.commands.status",
    "agent_boundary.cli.commands.session_command",
    "agent_boundary.cli.commands.use",
}
assert loaded.isdisjoint(commands), loaded & commands
"""
    subprocess.run([sys.executable, "-c", code], check=True, env=PYTHON_ENV)


def test_generic_core_does_not_import_harness_integrations() -> None:
    code = """
import sys
import agent_boundary.aws
import agent_boundary.boundary
import agent_boundary.github
import agent_boundary.runner
import agent_boundary.session

assert not any(name.startswith("agent_boundary.claude") for name in sys.modules)
"""
    subprocess.run([sys.executable, "-c", code], check=True, env=PYTHON_ENV)


def test_invocation_imports_only_selected_command() -> None:
    code = """
import sys
from typer.testing import CliRunner
from agent_boundary.cli.app import app

result = CliRunner().invoke(app, ["list"])
assert result.exit_code == 0, result.output
loaded = set(sys.modules)
assert "agent_boundary.cli.commands.list" in loaded
commands = {
    "agent_boundary.cli.commands.off",
    "agent_boundary.cli.commands.on",
    "agent_boundary.cli.commands.reload",
    "agent_boundary.cli.commands.run",
    "agent_boundary.cli.commands.status",
    "agent_boundary.cli.commands.session_command",
    "agent_boundary.cli.commands.use",
}
assert loaded.isdisjoint(commands), loaded & commands
"""
    subprocess.run([sys.executable, "-c", code], check=True, env=PYTHON_ENV)


def test_pre_tool_use_import_stays_dependency_free() -> None:
    code = """
import sys
import agent_boundary.claude.hooks.pre_tool_use

assert "typer" not in sys.modules
assert "pydantic" not in sys.modules
assert "yaml" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True, env=PYTHON_ENV)


def test_statusline_import_stays_dependency_free() -> None:
    code = """
import sys
sys.argv = ["agent-boundary", "claude", "statusline", '{"session_id":"test-session","cwd":"/"}']

from agent_boundary.entrypoint import main

try:
    main()
except SystemExit as error:
    assert error.code == 0

assert "typer" not in sys.modules
assert "pydantic" not in sys.modules
assert "yaml" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True, env=PYTHON_ENV)
