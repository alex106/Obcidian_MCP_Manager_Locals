"""Shared helpers for Claude Code hook scripts.

Both `capture_session.py` and `session_start.py` need to resolve which vault
they're writing into / reading from, so the logic lives here once instead of
being copy-pasted a second time and drifting.

--vault is how `install` wires this, and it is the only reliable channel: the
`env` block of an MCP server config applies to the MCP server subprocess only,
so a hook never inherits OBSIDIAN_VAULT from there. Relying on the env var
alone makes a hook a silent no-op in a real session.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


def buffer_path(root: Path, session_id: str) -> Path:
    """Where codex_turn_buffer.py appends a session's turns.

    Lives here because the writer (codex_turn_buffer.py) and the reader
    (capture_session.py) must agree on it byte for byte.
    """
    sid = _SAFE_ID.sub("_", session_id or "unknown")[:120] or "unknown"
    return root / ".secondbrain" / "buffer" / f"{sid}.jsonl"


def looks_like_vault(p: Path) -> bool:
    return p.is_dir() and (
        (p / ".obsidian").is_dir() or (p / ".secondbrain" / "config.json").exists()
    )


def resolve_vault(payload: dict) -> Path | None:
    """--vault, then $OBSIDIAN_VAULT, then a vault beside the project."""
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--vault" and i + 1 < len(argv):
            candidate = argv[i + 1]
            break
        if a.startswith("--vault="):
            candidate = a.split("=", 1)[1]
            break
    else:
        candidate = os.environ.get("OBSIDIAN_VAULT")

    if candidate:
        p = Path(os.path.expandvars(os.path.expanduser(candidate)))
        return p if p.is_dir() else None

    # Last resort: a vault sitting in the project directory.
    base = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR")
    if base:
        for name in ("SecondBrain", "secondbrain"):
            p = Path(base) / name
            if looks_like_vault(p):
                return p
    return None
