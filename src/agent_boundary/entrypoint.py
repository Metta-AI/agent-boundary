"""Single executable surface with fast paths for harness integrations."""

import sys
from typing import NoReturn


def main() -> NoReturn:
    if sys.argv[1:] == ["claude", "hook", "SessionStart"]:
        from agent_boundary.claude.hooks.session_start import entrypoint  # noqa: PLC0415

        entrypoint()
        raise SystemExit(0)

    if sys.argv[1:] == ["claude", "hook", "PreToolUse"]:
        from agent_boundary.claude.hooks.pre_tool_use import entrypoint  # noqa: PLC0415

        entrypoint()
        raise SystemExit(0)

    if len(sys.argv) == 4 and sys.argv[1:3] == ["claude", "statusline"] and not sys.argv[3].startswith("-"):
        from agent_boundary.claude.statusline import entrypoint  # noqa: PLC0415

        entrypoint(sys.argv[3])
        raise SystemExit(0)

    from agent_boundary.cli.app import app  # noqa: PLC0415

    app()
    raise SystemExit(0)


if __name__ == "__main__":
    main()
