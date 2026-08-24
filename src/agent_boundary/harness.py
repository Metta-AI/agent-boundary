"""Lazy current-session discovery across supported agent harnesses."""

import importlib
from pathlib import Path

SESSION_PROVIDERS = ("agent_boundary.claude.session",)


def current_session_dir() -> Path | None:
    for module_name in SESSION_PROVIDERS:
        provider = importlib.import_module(module_name)
        if directory := provider.current_session_dir():
            return directory
    return None
