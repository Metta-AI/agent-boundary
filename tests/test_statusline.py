"""Statusline rendering tests."""

import json
import subprocess
from pathlib import Path

import pytest

from agent_boundary.claude.statusline import render

SESSION_ID = "test-session"


def _input(tmp_path: Path) -> str:
    return json.dumps({"session_id": SESSION_ID, "workspace": {"current_dir": str(tmp_path)}})


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Git repo + session dir, isolated from the developer's real state and
    user-level Claude settings."""
    monkeypatch.setenv("AGENT_BOUNDARY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    subprocess.run(["git", "init", "--quiet", tmp_path], check=True)
    session = tmp_path / "state/sessions" / SESSION_ID
    session.mkdir(parents=True)
    return session


def _write(path: Path, plugins: dict[str, bool]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabledPlugins": plugins}))


def test_statusline_reflects_boundary_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _setup(tmp_path, monkeypatch)
    settings = tmp_path / ".claude/settings.json"
    _write(settings, {"agent-boundary-dev@skills-dir": True})
    config = session / "boundary.json"
    input_json = _input(tmp_path)

    config.write_text(json.dumps({"profile": "default", "state": "on", "self_edit": False}))
    assert render(input_json) == "\033[32mboundary:default\033[0m"

    config.write_text(json.dumps({"profile": "self-edit", "state": "on", "self_edit": True}))
    assert render(input_json) == "\033[33mboundary:self-edit\033[0m"

    config.write_text(json.dumps({"profile": "default", "state": "off", "self_edit": False}))
    assert render(input_json) == "\033[31mboundary:off\033[0m"

    _write(settings, {"agent-boundary-dev@skills-dir": False})
    assert render(input_json) == "\033[31mboundary:off\033[0m"


def test_statusline_matches_any_marketplace_id_across_scopes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _setup(tmp_path, monkeypatch)
    (session / "boundary.json").write_text(json.dumps({"profile": "default", "state": "on", "self_edit": False}))
    input_json = _input(tmp_path)

    # The marketplace install enabled at user scope only.
    _write(tmp_path / "claude-config/settings.json", {"agent-boundary@agent-boundary": True})
    assert render(input_json) == "\033[32mboundary:default\033[0m"

    # A checked-in project force-disable wins over the user scope.
    _write(tmp_path / ".claude/settings.json", {"agent-boundary@agent-boundary": False})
    assert render(input_json) == "\033[31mboundary:off\033[0m"


def test_statusline_is_silent_where_the_plugin_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    subprocess.run(["git", "init", "--quiet", tmp_path], check=True)
    assert render(_input(tmp_path)) == ""
