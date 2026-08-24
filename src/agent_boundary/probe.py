#!/usr/bin/env python3
"""
probe.py — asks the kernel whether one path operation is permitted.

Run by the PreToolUse hook *inside* `nono wrap`, so the answer is the sandbox's
real verdict rather than a model of the policy. The hook pipes this source to
`python3 -` instead of passing the path: the profile denies the protected runtime
directory, so a sandboxed process cannot open this file to execute it.

Usage: probe.py <path> <read|readwrite>
Prints one line to stdout: "allowed", "allowed|detail", or "denied|ERRNO".

The write check opens with O_RDWR and no O_CREAT/O_TRUNC, so it never mutates
the target. A missing path cannot be answered by opening it — macOS Seatbelt
reports ENOENT rather than EPERM for a path inside a denied directory, so
trusting ENOENT would permit writing new files into ~/.ssh or ~/.aws.

An absent target is therefore answered by performing the operation the real
tool would perform, on the real path, and undoing it. For a write that means
creating any missing parent directories and the file itself, then removing
them again — asking about the parent instead would answer the wrong question:
a policy that denies a *leaf* file (`.claude/settings.local.json`) inside an
allowed directory would look writable, and the Write tool runs outside nono,
so the gate's answer is the only thing stopping it. For a read it means readdir
on the nearest existing ancestor; an absent file has no contents to leak, and
`Read` of one fails on its own.
"""

import errno
import os
import sys
from pathlib import Path
from typing import NoReturn


def emit(verdict: str, detail: str = "", cleanup: list[str] | None = None) -> NoReturn:
    # Undo what the write probe created, leaf first. try/finally, not error
    # handling: the verdict is already decided and must be printed either way.
    for p in cleanup or []:
        try:
            os.rmdir(p) if os.path.isdir(p) else os.unlink(p)
        except OSError:
            pass
    print(verdict + ("|" + detail if detail else ""))
    sys.exit(0)


def errname(e: OSError) -> str:
    if e.errno is None:
        return "UNKNOWN"
    return errno.errorcode.get(e.errno, str(e.errno))


def nearest_existing_dir(path: str) -> str:
    d = os.path.dirname(os.path.abspath(path))
    while d and not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return d


def probe_absent(path: str, op: str) -> NoReturn:
    if op == "read":
        try:
            os.listdir(nearest_existing_dir(path))
        except OSError as e:
            emit("denied", errname(e))
        emit("allowed", "target absent")

    # Create the real path, exactly as the tool would, then undo it. O_EXCL so a
    # race that materializes the file cannot make us truncate it. Missing parents
    # get created too, since the tool creates them — and a denied directory
    # refuses the mkdir, which is the verdict we want.
    made: list[str] = []
    d = os.path.dirname(os.path.abspath(path))
    try:
        for parent in reversed([d, *(str(a) for a in Path(d).parents)]):
            if not os.path.isdir(parent):
                os.mkdir(parent)
                made.append(parent)
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except OSError as e:
        emit("denied", errname(e), cleanup=[*reversed(made)])
    emit("allowed", "target absent", cleanup=[path, *reversed(made)])


def main() -> NoReturn:
    path, op = sys.argv[1], sys.argv[2]
    try:
        os.close(os.open(path, os.O_RDONLY if op == "read" else os.O_RDWR))
    except OSError as e:
        if e.errno == errno.ENOENT:
            probe_absent(path, op)
        emit("denied", errname(e))
    emit("allowed")


if __name__ == "__main__":
    main()
