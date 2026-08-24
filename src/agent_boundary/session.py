"""Creation and discovery of explicit boundary session directories."""

import json
import os
from pathlib import Path

from agent_boundary import boundary, kubeconfig
from agent_boundary.models import SessionConfig, State
from agent_boundary.paths import profiles_dir

SESSION_DIR_ENV = "AGENT_BOUNDARY_SESSION_DIR"


def current_dir() -> Path | None:
    if value := os.environ.get(SESSION_DIR_ENV):
        return Path(value).expanduser().resolve()
    from agent_boundary.harness import current_session_dir  # noqa: PLC0415

    return current_session_dir()


def read(directory: Path) -> SessionConfig | None:
    path = directory / "boundary.json"
    if not path.is_file():
        return None
    return SessionConfig.model_validate_json(path.read_text())


def write(
    directory: Path,
    profile_name: str,
    state: State,
    *,
    workdir: Path | None = None,
    profile_directory: Path | None = None,
    protected_paths: tuple[Path, ...] = (),
) -> tuple[SessionConfig, tuple[str, ...]]:
    """Generate a session's config and pinned policy together."""
    directory = directory.expanduser().resolve()
    existing = read(directory)
    workdir = (workdir or (existing.workdir if existing else Path.cwd())).resolve()
    profile_directory = (profile_directory or (existing.profiles_dir if existing else profiles_dir())).resolve()
    if existing:
        protection = tuple(existing.protected_paths)
    else:
        protection = tuple(dict.fromkeys((directory, profile_directory, *protected_paths)))

    profile = boundary.load_profile(profile_name, profile_directory)
    warnings = kubeconfig.write(directory, profile)
    kubeconfig_path = directory / kubeconfig.KUBECONFIG_FILENAME
    readable_protected_paths = (kubeconfig_path,) if kubeconfig_path.is_file() else ()
    policy = boundary.generate_policy(
        profile,
        str(workdir),
        protection,
        readable_protected_paths=readable_protected_paths,
    )
    config = SessionConfig(
        profile=profile_name,
        state=state,
        self_edit=profile.self_edit,
        workdir=workdir,
        profiles_dir=profile_directory,
        protected_paths=list(protection),
        aws_profile=profile.aws.profile if profile.aws else None,
        github=profile.github,
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "policy.json").write_text(json.dumps(policy, indent=2) + "\n")
    (directory / "boundary.json").write_text(config.model_dump_json(indent=2, exclude_none=True) + "\n")
    return config, warnings


def describe(config: SessionConfig) -> str:
    note = " SELF-EDIT (soft boundary: the agent can switch or disable it)" if config.self_edit else ""
    return f"profile {config.profile} state {config.state}{note}"
