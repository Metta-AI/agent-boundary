"""Policy generation checks that run without the installed nono gate."""

from pathlib import Path

from agent_boundary.boundary import generate_policy
from agent_boundary.models import Profile


def test_profile_filesystem_grants_merge_without_duplicates(tmp_path: Path) -> None:
    file = tmp_path / "config"
    file.write_text("value")
    profile = Profile.model_validate(
        {
            "name": "test",
            "description": "test",
            "git_common_dir": False,
            "allow": [str(file)],
            "nono": {"filesystem": {"allow_file": [str(file), "/another/file"]}},
        }
    )

    policy = generate_policy(profile, str(tmp_path))

    assert policy["filesystem"]["allow_file"] == [str(file), "/another/file"]
