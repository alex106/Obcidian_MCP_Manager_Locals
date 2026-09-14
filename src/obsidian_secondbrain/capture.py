"""Session capture -- the `/compact` half of the system.

The agent writes the summary; this module only decides *where* it lands and
how it is stamped, so a captured session becomes a first-class raw note that
the distillation pass can later pick up.
"""

from __future__ import annotations

import datetime as _dt
import re

from .config import VaultConfig
from .vault import Note, append_note, write_note

SLUG_RE = re.compile(r"[^\w֐-׿ -]+", re.UNICODE)
# Characters Windows and/or Obsidian refuse in a filename.
ILLEGAL_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def slugify(text: str, max_len: int = 60) -> str:
    """Hyphenated slug -- for names that are not wikilink targets."""
    text = SLUG_RE.sub("", (text or "").strip())
    text = re.sub(r"\s+", "-", text).strip("-")
    return (text[:max_len].rstrip("-") or "untitled")


def title_filename(title: str, max_len: int = 120) -> str:
    """Filename that preserves the title verbatim, minus illegal characters.

    Obsidian resolves [[Some title]] to a file literally named "Some title.md",
    so a note that is a link target must keep its spaces -- slugifying it here
    would silently break every backlink pointing at it.
    """
    name = ILLEGAL_RE.sub("", (title or "").strip())
    name = re.sub(r"\s+", " ", name).strip()
    # Windows rejects a trailing dot or space.
    name = name[:max_len].rstrip(". ")
    return name or "untitled"


def daily_note_rel(cfg: VaultConfig, date: str | None = None) -> str:
    fmt = cfg.get("daily_note_format", "%Y-%m-%d")
    when = _dt.date.fromisoformat(date) if date else _dt.date.today()
    return f"{cfg.folders['log']}/{when.strftime(fmt)}{cfg.ext}"


def append_to_log(cfg: VaultConfig, text: str, source: str = "manual",
                  date: str | None = None) -> Note:
    """Append a timestamped entry to the day's append-only log."""
    rel = daily_note_rel(cfg, date)
    stamp = _dt.datetime.now().strftime("%H:%M")
    return append_note(
        cfg,
        rel,
        text,
        frontmatter={"type": "log", "date": (date or _dt.date.today().isoformat())},
        heading=f"## {stamp} · {source}",
    )


def capture_session(
    cfg: VaultConfig,
    title: str,
    summary: str,
    decisions: list[str] | None = None,
    open_questions: list[str] | None = None,
    artifacts: list[str] | None = None,
    tags: list[str] | None = None,
    project: str | None = None,
    session_id: str | None = None,
    source: str = "agent",
) -> dict:
    """Write one session note and cross-post a pointer into the daily log."""
    now = _dt.datetime.now()
    fmt = cfg.get("session_note_format", "%Y-%m-%d-%H%M")
    # Session notes are linked by this exact stem from the daily log, so the
    # name only has to be self-consistent, not equal to the title.
    name = f"{now.strftime(fmt)} {title_filename(title, 80)}"
    rel = f"{cfg.folders['sessions']}/{name}{cfg.ext}"

    fm = {
        "type": "session",
        "title": title,
        "date": now.date().isoformat(),
        "source": source,
        "tags": tags or [],
        cfg.distilled_key: False,
    }
    if project:
        fm["project"] = project
    if session_id:
        fm["session_id"] = session_id

    parts = [f"# {title}", "", "## Summary", "", summary.strip(), ""]
    if decisions:
        parts += ["## Decisions", ""] + [f"- {d}" for d in decisions] + [""]
    if open_questions:
        parts += ["## Open questions", ""] + [f"- [ ] {q}" for q in open_questions] + [""]
    if artifacts:
        parts += ["## Artifacts touched", ""] + [f"- `{a}`" for a in artifacts] + [""]
    parts += ["## Distillation", "", "> Not yet distilled. Run the `distill` prompt.", ""]

    note = write_note(cfg, rel, "\n".join(parts), fm, overwrite=True)
    log = append_to_log(
        cfg, f"Session captured: [[{name}]] — {summary.strip().splitlines()[0][:200]}",
        source="session",
    )
    return {"session_note": note.rel, "daily_log": log.rel, "title": title}
