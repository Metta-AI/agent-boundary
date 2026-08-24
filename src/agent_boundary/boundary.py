#!/usr/bin/env python3
"""Profile loading and policy generation for agent-boundary.

Imported only by writer commands. The PreToolUse hook must never import this: it
runs on every tool call, and generating a policy means parsing YAML and shelling
out to git. See README.

A profile (profiles/<name>.yaml) is human-authored and backend-agnostic where it
can be. Generation resolves it against this machine — expanding globs, following
symlink chains, asking git where the real gitdir is — and writes a concrete nono
profile to sessions/<id>/policy.json. Anything unresolvable is dropped rather
than emitted, because nono silently ignores grants for paths that do not exist,
and a grant that looks present but does nothing is worse than an absent one.
"""

import os
import subprocess
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agent_boundary.models import Profile, SymlinkSpec
from agent_boundary.paths import profiles_dir

# Access kinds a profile may request, mapped to the nono filesystem key. Files and
# directories are distinct in nono, and picking the wrong one silently grants
# nothing, so `resolve_symlinks` chooses per path by looking at the target.
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
    """Every path that must be granted for `path` to be openable.

    Granting a symlink does not grant traversal *through* it, and the chain can
    include intermediate *directory* links: ~/.gitconfig -> ~/etc/dotfiles/gitconfig
    where ~/etc -> coding/my/etc. Granting only the link and its final target
    still EPERMs — verified — so every hop, and every parent that is itself a
    link, has to be named.
    """
    out: list[Path] = []
    seen: set[Path] = set()
    # One hop at a time: realpath() collapses the whole chain at once and would
    # skip right over an intermediate directory link, which is the hop that
    # actually blocks traversal.
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
    fs.setdefault(key, [])
    if value not in fs[key]:
        fs[key].append(value)


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
            # A glob is a claim about this machine, so resolve it now; a literal
            # path passes through untouched so nono still sees $WORKDIR-relative
            # entries it can expand itself.
            if any(c in pattern for c in "*?["):
                for hit in sorted(Path("/").glob(pattern.lstrip("/"))):
                    add(fs, DIR_KEY[kind] if hit.is_dir() else FILE_KEY[kind], str(hit))
            else:
                p = Path(pattern)
                is_file = p.is_file() and not p.is_dir()
                add(fs, FILE_KEY[kind] if is_file else DIR_KEY[kind], pattern)

    for raw in profile.deny:
        add(fs, "deny", expand(raw, workdir))

    # Symlinked config that lives outside the boundary: grant every hop of the
    # chain. `bypass_protection` lifts a required-group deny (shell rc files,
    # kubeconfig); it does not grant access, so the grant above is still needed.
    for entry in profile.resolve_symlinks:
        if isinstance(entry, str):
            entry = SymlinkSpec(path=entry)
        target = Path(expand(entry.path, workdir)).expanduser()
        access = entry.access
        for hop in symlink_chain(target):
            add(fs, DIR_KEY[access] if hop.is_dir() else FILE_KEY[access], str(hop))
            if entry.bypass_protection:
                add(fs, "bypass_protection", str(hop))

    # A linked worktree keeps its gitdir under the *parent* repo's .git, outside
    # $WORKDIR, so git cannot find its own repository without this. Writable
    # because `git add` locks refs/heads/* — which also leaves .git/hooks
    # writable, a known hole. Defaults on: a boundary that breaks git is a
    # boundary nobody enables.
    if profile.git_common_dir:
        proc = subprocess.run(
            ["git", "-C", workdir, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            common = Path(proc.stdout.strip())
            add(fs, "allow", str(common))
            # A repo may symlink .git/hooks/* into the parent checkout's working
            # tree (the Softmax monorepo does), which the allow above does not
            # reach — so
            # `git commit` dies on the pre-commit hook. Grant each hook's chain
            # read-only: enough to execute, while the parent's copy stays
            # unwritable (an agent-written hook would run outside the jail when
            # the *user* commits). Hops inside the common dir are already allowed.
            if (common / "hooks").is_dir():
                for hook in (common / "hooks").iterdir():
                    for hop in symlink_chain(hook):
                        if common not in hop.parents:
                            add(fs, DIR_KEY["read"] if hop.is_dir() else FILE_KEY["read"], str(hop))

    # Self-protection. Without it the agent can rewrite its policy, profiles, or
    # trusted runtime. `self_edit: true` deliberately flips these to grants: the
    # state root lives outside every profile's allow list, so lifting the denies
    # alone would leave a self-edit session unable to touch its own machinery.
    for path in protected_paths:
        add(fs, "allow" if profile.self_edit else "deny", str(path))

    for path in readable_protected_paths:
        add(fs, "read_file", str(path))
        add(fs, "bypass_protection", str(path))

    if fs:
        # Merge rather than replace: a profile's `nono.filesystem` is an escape
        # hatch and must survive generation.
        for key, values in (nono_block.get("filesystem") or {}).items():
            for v in values:
                add(fs, key, v if isinstance(v, str) else v)
        policy["filesystem"] = fs

    env = {k: expand(v, workdir) for k, v in profile.env.items()}
    if env:
        environment = dict(policy.get("environment") or {})
        environment["set_vars"] = {**(environment.get("set_vars") or {}), **env}
        policy["environment"] = environment

    return policy
