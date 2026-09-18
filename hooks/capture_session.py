#!/usr/bin/env python3
"""Claude Code hook: file a raw session capture into the vault.

Wired to PreCompact and SessionEnd. A hook runs *outside* the model, so it
cannot write a summary -- it files the raw material (user prompts and the
assistant's final messages, not tool spam) as an undistilled inbox note. The
next `distill_queue` pass hands that note to the agent, which does the
thinking. That keeps the "no API key, agent does the work" rule intact even
for automatic capture.

Input: hook JSON on stdin (session_id, transcript_path, cwd, hook_event_name).
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
from _common import resolve_vault  # noqa: E402

MAX_CHARS = 20000
SLUG_RE = re.compile(r"[^\w -]+", re.UNICODE)


def slug(text: str, n: int = 50) -> str:
    text = SLUG_RE.sub("", (text or "").strip())
    return (re.sub(r"\s+", "-", text)[:n].strip("-") or "session")


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def read_transcript(path: str) -> list[tuple[str, str]]:
    """Return [(role, text)] from a Claude Code .jsonl transcript."""
    turns: list[tuple[str, str]] = []
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
        msg = rec.get("message")
        if not isinstance(msg, dict):
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

    turns = read_transcript(payload.get("transcript_path", ""))
    if not turns:
        return 0

    first_user = next((t for r, t in turns if r == "user"), "session")
    now = _dt.datetime.now()
    name = f"{now:%Y-%m-%d-%H%M}-{slug(first_user.splitlines()[0])}"
    dest = root / folders["inbox"] / f"{name}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "---",
        "type: session-raw",
        f"title: {json.dumps(first_user.splitlines()[0][:120])}",
        f"date: {now.date().isoformat()}",
        f"captured_at: {now.isoformat(timespec='seconds')}",
        f"trigger: {payload.get('hook_event_name', 'unknown')}",
        f"session_id: {payload.get('session_id', '')}",
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

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # never break the session over a note
        sys.exit(0)
