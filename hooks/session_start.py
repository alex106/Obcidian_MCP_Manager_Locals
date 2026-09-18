#!/usr/bin/env python3
"""Claude Code hook: inject a vault digest at session start.

Wired to SessionStart. This is the other half of the loop capture_session.py
covers: a hook writes the vault when a session ends, but nothing otherwise
makes the agent read it when a session begins. This hook hands back a short,
deterministic digest -- the undistilled-backlog count and the tail of the
most recent log -- as additionalContext, so a new session sees it
unconditionally, before the model has read anything else.

It never summarises -- same "no API key" rule as capture_session.py: it only
counts and quotes existing text, it never calls a model. Distinct from the
CLAUDE.md "search the vault first" rule: that rule does a *targeted* search
once the user's actual request is known, which doesn't exist yet at
SessionStart time. This hook is the passive half; that rule is the active
half.

Input: hook JSON on stdin (session_id, cwd, hook_event_name, source -- one of
startup/resume/clear/compact/fork). Treated uniformly: this is cheap enough
to run on every firing.
Vault: --vault PATH, else $OBSIDIAN_VAULT, else a vault next to the project.

Output: on success, {"hookSpecificOutput": {"hookEventName": "SessionStart",
"additionalContext": "..."}} on stdout. additionalContext is capped well
under Claude Code's 4000-char per-hook limit.

Exit: always 0 -- a broken digest must never block the session. Silence
(no output) whenever the vault or the installed package isn't available.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import resolve_vault  # noqa: E402

MAX_CONTEXT_CHARS = 1500
RECENT_LOG_LINES = 12


def recent_log_tail(root: Path, log_folder: str) -> str:
    log_dir = root / log_folder
    if not log_dir.is_dir():
        return ""
    logs = sorted(log_dir.glob("*.md"))
    if not logs:
        return ""
    text = logs[-1].read_text(encoding="utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-RECENT_LOG_LINES:])


def build_digest(root: Path) -> str | None:
    try:
        from obsidian_secondbrain.config import load_config
        from obsidian_secondbrain.distill import queue
    except ImportError:
        return None

    try:
        cfg = load_config(str(root))
        pending = queue(cfg, limit=0, include_body=False)["pending_total"]
    except Exception:
        return None

    tail = recent_log_tail(root, cfg.folders.get("log", "10-Log"))

    parts = [
        "Second-brain vault check-in (automatic, from a SessionStart hook -- "
        "not a search):",
        f"- vault: {root}",
        f"- undistilled raw captures waiting in the inbox: {pending}",
    ]
    if tail:
        parts += ["- tail of the most recent daily log:", tail]
    parts.append(
        "This is a passive index read, nothing was searched. If the user's "
        "first request needs prior context, still run search_notes/"
        "find_notes on it per the project rule."
    )
    digest = "\n".join(parts)
    if len(digest) > MAX_CONTEXT_CHARS:
        digest = digest[:MAX_CONTEXT_CHARS].rstrip() + "\n... (truncated)"
    return digest


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}

    root = resolve_vault(payload)
    if root is None:
        return 0

    digest = build_digest(root)
    if not digest:
        return 0

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": digest,
        }
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # never break the session over a digest
        sys.exit(0)
