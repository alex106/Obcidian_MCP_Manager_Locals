#!/usr/bin/env python3
"""Claude Code hook: file a raw session capture into the vault.

Wired to PreCompact and SessionEnd, in Claude Code and in Codex. A hook runs *outside* the model, so it
cannot write a summary -- it files the raw material (user prompts and the
assistant's final messages, not tool spam) as an undistilled inbox note. The
next `distill_queue` pass hands that note to the agent, which does the
thinking. That keeps the "no API key, agent does the work" rule intact even
for automatic capture.

Input: hook JSON on stdin (session_id, transcript_path, cwd, hook_event_name).

Source of the turns, in order:
  1. a Codex turn buffer (<vault>/.secondbrain/buffer/<session_id>.jsonl,
     written by codex_turn_buffer.py from stable hook fields). Consumed on
     use, so a PreCompact capture and the later SessionEnd capture never
     file the same turns twice.
  2. the transcript at transcript_path -- Claude Code's format, plus a
     best-effort reading of Codex rollout records. Codex documents its
     transcript as unstable, which is why (1) exists and wins.
Vault: --vault PATH, else $OBSIDIAN_VAULT, else a vault next to the project.

--vault is how `install` wires this, and it is the only reliable channel: the
`env` block of an MCP server config applies to the MCP server subprocess only,
so a hook never inherits OBSIDIAN_VAULT from there. Relying on the env var
alone makes the hook a silent no-op in a real session.

Exit: always 0 -- a failed capture must never block the session.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import buffer_path, resolve_vault  # noqa: E402

MAX_CHARS = 20000
SLUG_RE = re.compile(r"[^\w -]+", re.UNICODE)


def slug(text: str, n: int = 50) -> str:
    text = SLUG_RE.sub("", (text or "").strip())
    return (re.sub(r"\s+", "-", text)[:n].strip("-") or "session")


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # "text" is Claude Code; "input_text"/"output_text" are Codex.
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict)
            and b.get("type") in ("text", "input_text", "output_text")
        )
    return ""


def read_buffer(path: Path) -> list[tuple[str, str]]:
    """Return [(role, text)] from a Codex turn buffer."""
    turns: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        role, body = rec.get("role"), (rec.get("text") or "").strip()
        if role in ("user", "assistant") and body:
            turns.append((role, body))
    return turns


def _message_of(rec: dict) -> dict | None:
    """The message dict of one transcript record, or None.

    Claude Code: {"message": {"role", "content"}}.
    Codex (best effort, format documented as unstable):
      {"type": "response_item", "payload": {"type": "message", "role", "content"}}.
    """
    msg = rec.get("message")
    if isinstance(msg, dict):
        return msg
    payload = rec.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "message":
        return payload
    return None


def read_transcript(path: str | None) -> list[tuple[str, str]]:
    """Return [(role, text)] from a Claude Code (or Codex) .jsonl transcript."""
    turns: list[tuple[str, str]] = []
    if not path:
        return turns
    p = Path(path)
    if not p.exists():
        return turns
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = _message_of(rec)
        if msg is None:
            continue
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        body = text_of(msg.get("content")).strip()
        # Skip tool-result envelopes and harness noise.
        if not body or body.startswith("<") or body.startswith("Caveat:"):
            continue
        turns.append((role, body))
    return turns


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}

    root = resolve_vault(payload)
    if root is None:
        return 0

    cfg = {}
    cfg_file = root / ".secondbrain" / "config.json"
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cfg = {}
    folders = {"inbox": "00-Inbox", **cfg.get("folders", {})}

    buf = buffer_path(root, payload.get("session_id", ""))
    turns, source = [], "transcript"
    if buf.exists():
        turns, source = read_buffer(buf), "codex-buffer"
    if not turns:
        turns, source = read_transcript(payload.get("transcript_path")), "transcript"
    if not turns:
        return 0

    first_user = next((t for r, t in turns if r == "user"), "session")
    now = _dt.datetime.now()
    name = f"{now:%Y-%m-%d-%H%M}-{slug(first_user.splitlines()[0])}"
    dest = root / folders["inbox"] / f"{name}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    # A PreCompact and a SessionEnd capture can land in the same minute with
    # the same first line. With the buffer already consumed, overwriting the
    # earlier note would lose those turns for good -- so never overwrite.
    n = 2
    while dest.exists():
        dest = dest.with_name(f"{name}-{n}.md")
        n += 1
    name = dest.stem

    lines = [
        "---",
        "type: session-raw",
        f"title: {json.dumps(first_user.splitlines()[0][:120])}",
        f"date: {now.date().isoformat()}",
        f"captured_at: {now.isoformat(timespec='seconds')}",
        f"trigger: {payload.get('hook_event_name', 'unknown')}",
        f"session_id: {payload.get('session_id', '')}",
        f"source: {source}",
        f"cwd: {json.dumps(payload.get('cwd', ''))}",
        "distilled: false",
        "---",
        "",
        f"# Raw session capture — {now:%Y-%m-%d %H:%M}",
        "",
        "> Automatic capture, not a summary. Run the `distill` prompt to turn "
        "this into atomic notes, then `mark_distilled` this path.",
        "",
    ]

    budget = MAX_CHARS
    chunks = []
    for role, body in reversed(turns):  # keep the most recent, which matters most
        block = f"### {role}\n\n{body}\n"
        if len(block) > budget:
            break
        chunks.append(block)
        budget -= len(block)
    lines.extend(reversed(chunks))

    dest.write_text("\n".join(lines), encoding="utf-8")

    # Pointer into the day's append-only log.
    log_dir = root / folders.get("log", "10-Log")
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"{now:%Y-%m-%d}.md"
    entry = f"\n## {now:%H:%M} · session-hook\n\nRaw capture: [[{name}]]\n"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(entry)

    # Only now that the note is on disk: consume the buffer, so the next
    # capture (SessionEnd after a PreCompact) files only newer turns.
    if source == "codex-buffer":
        buf.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # never break the session over a note
        sys.exit(0)
