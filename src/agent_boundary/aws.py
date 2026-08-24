"""Short-lived AWS credential export and per-session plaintext caching."""

import json
import os
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path

AWS_ENV_FILENAME = "aws-credentials.env"
AWS_REFRESH_MARGIN = 300.0
AWS_FAIL_TTL = 300.0
AWS_TIMEOUT = 15


def export(profile: str) -> dict:
    def failed(error: str) -> dict:
        return {"profile": profile, "failed_at": time.time(), "error": error}

    try:
        process = subprocess.run(
            ["aws", "configure", "export-credentials", "--profile", profile],
            capture_output=True,
            text=True,
            timeout=AWS_TIMEOUT,
        )
        if process.returncode != 0:
            return failed((process.stderr.strip() or f"exited {process.returncode}").splitlines()[0])
        region = subprocess.run(
            ["aws", "configure", "get", "region", "--profile", profile],
            capture_output=True,
            text=True,
            timeout=AWS_TIMEOUT,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return failed(str(error).splitlines()[0])

    try:
        credentials = json.loads(process.stdout)
    except ValueError:
        return failed("unparseable export-credentials output")
    if (
        not isinstance(credentials, dict)
        or not credentials.get("AccessKeyId")
        or not credentials.get("SecretAccessKey")
    ):
        return failed("export-credentials output missing key material")

    env = {
        "AWS_ACCESS_KEY_ID": credentials["AccessKeyId"],
        "AWS_SECRET_ACCESS_KEY": credentials["SecretAccessKey"],
    }
    if credentials.get("SessionToken"):
        env["AWS_SESSION_TOKEN"] = credentials["SessionToken"]
    if region:
        env["AWS_REGION"] = env["AWS_DEFAULT_REGION"] = region
    env["AWS_CONFIG_FILE"] = env["AWS_SHARED_CREDENTIALS_FILE"] = "/dev/null"

    record = {"profile": profile, "env": env}
    if expiration := credentials.get("Expiration"):
        record["expires_at"] = datetime.fromisoformat(expiration).timestamp()
    return record


def render_env(record: dict) -> str:
    lines = ["# agent-boundary AWS credentials — do not edit", f"# profile={record['profile']}"]
    if "failed_at" in record:
        lines += [f"# failed_at={record['failed_at']}", f"# error={record.get('error', '')}"]
    else:
        if "expires_at" in record:
            lines.append(f"# expires_at={record['expires_at']}")
        lines += [f"export {key}={shlex.quote(value)}" for key, value in record["env"].items()]
    return "\n".join(lines) + "\n"


def parse_meta(text: str) -> dict[str, str]:
    meta = {}
    for line in text.splitlines():
        if line.startswith("# ") and "=" in line:
            key, value = line.removeprefix("# ").split("=", 1)
            meta[key] = value
    return meta


def read_env_file(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        if line.startswith("export "):
            key, value = line.removeprefix("export ").split("=", 1)
            env[key] = shlex.split(value)[0]
    return env


def env_file(directory: Path, profile: str) -> tuple[Path | None, str]:
    path = directory / AWS_ENV_FILENAME
    text = path.read_text() if path.is_file() else ""
    meta = parse_meta(text)

    fresh = bool(text) and meta.get("profile") == profile
    if fresh and "failed_at" in meta and time.time() - float(meta["failed_at"]) >= AWS_FAIL_TTL:
        fresh = False
    if fresh and "expires_at" in meta and time.time() >= float(meta["expires_at"]) - AWS_REFRESH_MARGIN:
        fresh = False

    if not fresh:
        record = export(profile)
        text = render_env(record)
        temporary = path.with_name(f"{AWS_ENV_FILENAME}.{os.getpid()}.tmp")
        temporary.write_text(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        meta = parse_meta(text)

    if "failed_at" in meta:
        return None, f" (no AWS creds: {meta.get('error') or 'export failed'} — try `aws sso login`)"
    return path, ""
