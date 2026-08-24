"""Statusline rendering tests."""

import json
import subprocess
from pathlib import Path

from agent_boundary.claude.statusline import render

PLUGIN = "agent-boundary@skills-dir"
SESSION_ID = "test-session"


def test_statusline_reflects_boundary_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BOUNDARY_STATE_DIR", str(tmp_path / "state"))
    subprocess.run(["git", "init", "--quiet", tmp_path], check=True)
    skill = tmp_path / ".claude/skills/agent-boundary"
    skill.mkdir(parents=True)
    session = tmp_path / "state/sessions" / SESSION_ID
    session.mkdir(parents=True)
    settings = tmp_path / ".claude/settings.json"
    settings.write_text(json.dumps({"enabledPlugins": {PLUGIN: True}}))
    config = session / "boundary.json"
    input_json = json.dumps({"session_id": SESSION_ID, "workspace": {"current_dir": str(tmp_path)}})

    config.write_text(json.dumps({"profile": "default", "state": "on", "self_edit": False}))
    assert render(input_json) == "\033[32mboundary:default\033[0m"

    config.write_text(json.dumps({"profile": "self-edit", "state": "on", "self_edit": True}))
    assert render(input_json) == "\033[33mboundary:self-edit\033[0m"

    config.write_text(json.dumps({"profile": "default", "state": "off", "self_edit": False}))
    assert render(input_json) == "\033[31mboundary:off\033[0m"

    settings.write_text(json.dumps({"enabledPlugins": {PLUGIN: False}}))
    assert render(input_json) == "\033[31mboundary:off\033[0m"


def test_statusline_is_silent_outside_agent_boundary_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet", tmp_path], check=True)
    input_json = json.dumps({"session_id": SESSION_ID, "workspace": {"current_dir": str(tmp_path)}})
    assert render(input_json) == ""
