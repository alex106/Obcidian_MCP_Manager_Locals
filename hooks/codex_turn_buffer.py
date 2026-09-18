#!/usr/bin/env python3
"""Codex hook: buffer each turn from the hook payload, not from the transcript.

Wired to UserPromptSubmit and Stop. Codex hands a hook a `transcript_path`, but
documents the transcript format as NOT a stable interface -- a parser written
against today's rollout file can start returning nothing after an upgrade, and
capture_session.py would then exit 0 having written nothing. That is exactly
the silent no-op test-hook exists to catch, so the Codex path avoids the file
entirely and uses two documented, stable event fields instead:

  UserPromptSubmit.prompt            -- what the user sent
  Stop.last_assistant_message        -- the assistant's final text for the turn

Each firing appends one JSON line to
    <vault>/.secondbrain/buffer/<session_id>.jsonl
and capture_session.py (PreCompact / SessionEnd) turns that buffer into the
inbox note. This also keeps SessionEnd cheap, which matters: Codex gives a
SessionEnd hook 1 s by default and 3 s at most.

Tool calls never reach the buffer -- neither field carries them -- so the
"no tool spam" property of the Claude capture holds by construction.

Output: nothing for UserPromptSubmit (plain stdout there would be injected as
model context), `{}` for Stop (Codex requires JSON on stdout when Stop exits 0).
Exit: always 0 -- buffering must never block or continue a turn.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import buffer_path, resolve_vault  # noqa: E402


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}

    event = payload.get("hook_event_name", "")
    try:
        if event == "UserPromptSubmit":
            role, text = "user", payload.get("prompt")
        elif event == "Stop":
            role, text = "assistant", payload.get("last_assistant_message")
        else:
            return 0

        text = (text or "").strip()
        root = resolve_vault(payload)
        if root is None or not text:
            return 0

        dest = buffer_path(root, payload.get("session_id", ""))
        dest.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "role": role,
            "text": text,
            "turn_id": payload.get("turn_id"),
            "at": _dt.datetime.now().isoformat(timespec="seconds"),
            "cwd": payload.get("cwd"),
        }
        with dest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return 0
    finally:
        if event == "Stop":
            print("{}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # never break a turn over a buffer line
        sys.exit(0)
