"""Credential cache permissions are shared by AWS and GitHub exports."""

import stat
from pathlib import Path

from agent_boundary.aws import write_env_file


def test_replacing_credential_cache_preserves_private_mode(tmp_path: Path) -> None:
    path = tmp_path / "credentials.env"
    path.write_text("old")

    write_env_file(path, "new")

    assert path.read_text() == "new"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [path]
