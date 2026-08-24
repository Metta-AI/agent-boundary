"""GitHub token export and per-session plaintext caching.

The token comes from `gh auth token`, run OUTSIDE the jail where gh can reach
its keychain-backed storage. Inside the jail the keychain is denied, so both
consumers run on the injected environment instead: gh itself honors GH_TOKEN,
and git's credential helper is swapped from osxkeychain — which would fail —
to `gh auth git-credential` via GIT_CONFIG_* overrides.
"""

import os
import shlex
import subprocess
import time
from pathlib import Path

from agent_boundary.aws import parse_meta

GITHUB_ENV_FILENAME = "github-credentials.env"
# gh tokens carry no expiry the way STS credentials do; re-export hourly so a
# re-login or token rotation is picked up without restarting the session.
GITHUB_REFRESH_SECONDS = 3600.0
GITHUB_FAIL_TTL = 300.0
GITHUB_TIMEOUT = 15


def export() -> dict:
    def failed(error: str) -> dict:
        return {"failed_at": time.time(), "error": error}

    try:
        process = subprocess.run(
            ["gh", "auth", "token", "--hostname", "github.com"],
            capture_output=True,
            text=True,
            timeout=GITHUB_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return failed(str(error).splitlines()[0])
    if process.returncode != 0:
        return failed((process.stderr.strip() or f"exited {process.returncode}").splitlines()[0])
    token = process.stdout.strip()
    if not token:
        return failed("gh auth token printed nothing")

    env = {
        "GH_TOKEN": token,
        "GITHUB_TOKEN": token,
        # Highest-precedence git config. The first (empty) entry clears the
        # inherited credential-helper list; the second routes credentials
        # through gh, which reads GH_TOKEN and never touches the keychain.
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": "!gh auth git-credential",
    }
    return {"exported_at": time.time(), "env": env}


def render_env(record: dict) -> str:
    lines = ["# agent-boundary GitHub credentials — do not edit"]
    if "failed_at" in record:
        lines += [f"# failed_at={record['failed_at']}", f"# error={record.get('error', '')}"]
    else:
        lines.append(f"# exported_at={record['exported_at']}")
        lines += [f"export {key}={shlex.quote(value)}" for key, value in record["env"].items()]
    return "\n".join(lines) + "\n"


def env_file(directory: Path) -> tuple[Path | None, str]:
    path = directory / GITHUB_ENV_FILENAME
    text = path.read_text() if path.is_file() else ""
    meta = parse_meta(text)

    fresh = bool(text)
    if fresh and "failed_at" in meta and time.time() - float(meta["failed_at"]) >= GITHUB_FAIL_TTL:
        fresh = False
    if fresh and "exported_at" in meta and time.time() - float(meta["exported_at"]) >= GITHUB_REFRESH_SECONDS:
        fresh = False

    if not fresh:
        record = export()
        text = render_env(record)
        temporary = path.with_name(f"{GITHUB_ENV_FILENAME}.{os.getpid()}.tmp")
        temporary.write_text(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        meta = parse_meta(text)

    if "failed_at" in meta:
        return None, f" (no GitHub token: {meta.get('error') or 'gh auth token failed'} — try `gh auth login`)"
    return path, ""
