"""Harness-neutral path resolution tests."""

from pathlib import Path

from agent_boundary.harness import current_session_dir
from agent_boundary.paths import profiles_dir, state_dir


def test_profiles_dir_prefers_explicit_environment(monkeypatch, tmp_path: Path) -> None:
    explicit = tmp_path / "profiles"
    monkeypatch.setenv("AGENT_BOUNDARY_PROFILES_DIR", str(explicit))
    assert profiles_dir() == explicit


def test_profiles_dir_uses_xdg_fallback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AGENT_BOUNDARY_PROFILES_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert profiles_dir() == tmp_path / "agent-boundary/profiles"


def test_state_dir_prefers_explicit_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_BOUNDARY_STATE_DIR", str(tmp_path / "state"))
    assert state_dir() == (tmp_path / "state").resolve()


def test_state_dir_uses_xdg_state_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AGENT_BOUNDARY_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert state_dir() == (tmp_path / "agent-boundary").resolve()


def test_state_dir_defaults_to_local_state(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_BOUNDARY_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert state_dir() == (Path.home() / ".local/state/agent-boundary").resolve()


def test_harness_proxy_defers_to_claude_provider(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_BOUNDARY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-1234")
    assert current_session_dir() == (tmp_path / "state").resolve() / "sessions/session-1234"
