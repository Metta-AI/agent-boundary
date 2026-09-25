#!/usr/bin/env python3
"""Load authored profiles and resolve paths into concrete nono policies.

Only writers import this module; per-tool hooks read the pinned policy instead.
Unresolvable grants are dropped because nono silently ignores missing paths.
"""

import os
import subprocess
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agent_boundary.models import Profile, SymlinkSpec
from agent_boundary.paths import profiles_dir

# Nono needs distinct keys for file and directory grants.
DIR_KEY = {"allow": "allow", "read": "read", "write": "write"}
FILE_KEY = {"allow": "allow_file", "read": "read_file", "write": "write_file"}


class ProfileError(Exception):
    """A profile is unusable. Raised to the CLI, which reports and exits."""


PROFILE_ERRORS = (ProfileError, ValidationError)


def profile_names(directory: Path | None = None) -> list[str]:
    return sorted(p.stem for p in (directory or profiles_dir()).glob("*.yaml"))


def load_profile(name: str, directory: Path | None = None) -> Profile:
    directory = directory or profiles_dir()
    path = directory / f"{name}.yaml"
    if not path.is_file():
        raise ProfileError(f"unknown profile {name!r}; have: {', '.join(profile_names(directory)) or '(none)'}")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        # A typo in YAML is the same class of problem as an unknown key: report it
        # as one, so the writers print a message instead of a traceback.
        raise ProfileError(f"{path.name}: not valid YAML: {e}") from e
    return Profile.model_validate(data)


def expand(raw: str, workdir: str) -> str:
    """$WORKDIR / $HOME expansion. Left in the value when nono expands it itself."""
    return raw.replace("$WORKDIR", workdir).replace("$HOME", str(Path.home()))


def symlink_chain(path: Path) -> list[Path]:
    """Include every symlink hop and linked parent needed to open a path."""
    out: list[Path] = []
    seen: set[Path] = set()
    # realpath() skips intermediate links that also need grants.
    todo = [path]
    while todo:
        p = todo.pop(0)
        if p in seen:
            continue
        seen.add(p)
        if not p.exists() and not p.is_symlink():
            continue
        out.append(p)
        if p.is_symlink():
            todo.append(Path(os.path.normpath(os.path.join(p.parent, os.readlink(p)))))
        # A parent that is itself a link must be granted too, or traversal dies
        # there even though every named path is allowed.
        todo.extend(parent for parent in p.parents if parent.is_symlink())
    return out


def add(fs: dict[str, list[str]], key: str, value: str) -> None:
    values = fs.setdefault(key, [])
    if value not in values:
        values.append(value)


def generate_policy(
    profile: Profile,
    workdir: str,
    protected_paths: tuple[Path, ...] = (),
    readable_protected_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    """Resolve a profile against this machine into a concrete nono profile."""
    nono_block = dict(profile.nono)
    policy: dict[str, Any] = {
        "meta": {"name": f"agent-boundary-{profile.name}"},
        **nono_block,
    }
    fs: dict[str, list[str]] = {}

    for kind in ("allow", "read", "write"):
        for raw in getattr(profile, kind):
            pattern = expand(raw, workdir)
            # Resolve globs now; leave literal paths for nono to expand.
            if any(c in pattern for c in "*?["):
                for hit in sorted(Path("/").glob(pattern.lstrip("/"))):
                    add(fs, DIR_KEY[kind] if hit.is_dir() else FILE_KEY[kind], str(hit))
            else:
                p = Path(pattern)
                add(fs, FILE_KEY[kind] if p.is_file() else DIR_KEY[kind], pattern)

    for raw in profile.deny:
        add(fs, "deny", expand(raw, workdir))

    # bypass_protection lifts a required-group deny but does not grant access.
    for entry in profile.resolve_symlinks:
        if isinstance(entry, str):
            entry = SymlinkSpec(path=entry)
        target = Path(expand(entry.path, workdir)).expanduser()
        access = entry.access
        for hop in symlink_chain(target):
            add(fs, DIR_KEY[access] if hop.is_dir() else FILE_KEY[access], str(hop))
            if entry.bypass_protection:
                add(fs, "bypass_protection", str(hop))

    # Linked worktrees need their common gitdir, including writable lock files.
    if profile.git_common_dir:
        proc = subprocess.run(
            ["git", "-C", workdir, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            common = Path(proc.stdout.strip())
            add(fs, "allow", str(common))
            # Grant linked hooks outside the common dir read-only.
            if (common / "hooks").is_dir():
                for hook in (common / "hooks").iterdir():
                    for hop in symlink_chain(hook):
                        if common not in hop.parents:
                            add(fs, DIR_KEY["read"] if hop.is_dir() else FILE_KEY["read"], str(hop))

    # self_edit grants the protected state root, normally outside allow lists.
    for path in protected_paths:
        add(fs, "allow" if profile.self_edit else "deny", str(path))

    for path in readable_protected_paths:
        add(fs, "read_file", str(path))
        add(fs, "bypass_protection", str(path))

    if fs:
        # Preserve authored nono.filesystem entries.
        for key, values in (nono_block.get("filesystem") or {}).items():
            for v in values:
                add(fs, key, v)
        policy["filesystem"] = fs

    env = {k: expand(v, workdir) for k, v in profile.env.items()}
    if env:
        environment = dict(policy.get("environment") or {})
        environment["set_vars"] = {**(environment.get("set_vars") or {}), **env}
        policy["environment"] = environment

    return policy
