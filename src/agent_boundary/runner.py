"""Low-level nono invocation shared by integrations and the CLI."""

from pathlib import Path

NONO = "nono"


class MissingPolicyError(Exception):
    pass


def wrap_argv(directory: Path, workdir: Path) -> list[str]:
    policy = directory / "policy.json"
    if not policy.is_file():
        raise MissingPolicyError(f"no policy for this session ({policy})")
    return [NONO, "wrap", "--silent", "--profile", str(policy), "--workdir", str(workdir), "--"]
