"""MCP server: local Obsidian vault as a Karpathy-style second brain.

Design rule: this server does no thinking. Every tool either moves bytes on
local disk or hands raw material back to the agent with an explicit
`next_step`. There is no model call, no network call and no API key anywhere
in this package.
"""

from __future__ import annotations

import functools
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import capture as _capture
from . import distill as _distill
from . import ops as _ops
from . import search as _search
from .config import ConfigError, VaultConfig, load_config
from .vault import (
    VaultError,
    append_note,
    iter_notes,
    link_index,
    outgoing_links,
    parse_note,
    patch_note,
    relpath,
    resolve,
    set_frontmatter,
    tags_in,
    write_note,
)

_INSTRUCTIONS = """This vault is a second brain, and you are the part that thinks.

The server only moves bytes on local disk. It never summarises, never embeds,
never calls a model or a network. Every tool hands you raw material; the
judgement is yours.

The loop:
  CAPTURE   log_entry / append_to_note -- raw, append-only, never rewritten.
  COMPACT   capture_session -- at the end of a session, YOU write the durable
            summary and this files it.
  DISTILL   distill_queue -> create_concept_note -> mark_distilled. One idea
            per note, title phrased as a claim, linked into the existing graph
            with [[wikilinks]].
  REVIEW    vault_health, resurface_notes, related_notes, stale_notes.

Two rules that matter: never edit a raw capture (distil it instead), and never
write an unlinked note (find_notes/search_notes first, then link).

Start with vault_info to learn the folder taxonomy -- it is configurable data,
not fixed."""


mcp = MCPServer("obsidian-secondbrain", instructions=_INSTRUCTIONS)

_cfg: VaultConfig | None = None


def cfg() -> VaultConfig:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg


def guarded(fn):
    """Return errors as data so the agent can recover instead of the call
    blowing up the tool channel.

    functools.wraps sets __wrapped__, so the MCP SDK still derives the tool
    schema from the real signature rather than (*args, **kwargs).
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (VaultError, ConfigError, OSError, ValueError) as exc:
            return {"error": type(exc).__name__, "message": str(exc)}
    return wrapper


# =========================================================== vault basics ===

@mcp.tool()
@guarded
def vault_info() -> dict:
    """Describe the vault: root path, folder taxonomy, note counts, method.

    Call this first in a session so you know the structure you are writing
    into. The taxonomy is data (.secondbrain/config.json), not code.
    """
    c = cfg()
    return {
        "root": str(c.root),
        "folders": c.folders,
        "counts": {k: sum(1 for _ in iter_notes(c, v)) for k, v in c.folders.items()},
        "total_notes": sum(1 for _ in iter_notes(c)),
        "method": c.get("method"),
        "config_file": str(c.config_path),
        "config_exists": c.config_path.exists(),
    }


@mcp.tool()
@guarded
def init_vault(seed_readme: bool = True) -> dict:
    """Create the folder taxonomy and config file in an empty or new vault.

    Idempotent: existing folders and notes are left untouched.
    """
    c = cfg()
    created = c.ensure_folders()
    if not c.config_path.exists():
        c.save()
    readme = c.root / "README.md"
    if seed_readme and not readme.exists():
        folders = "\n".join(
            f"- `{v}/` - {_FOLDER_DOC.get(k, k)}" for k, v in c.folders.items()
        )
        readme.write_text(_README.format(root=c.root, folders=folders), encoding="utf-8")
    return {
        "root": str(c.root),
        "created_folders": created,
        "config": str(c.config_path),
        "readme": readme.exists(),
    }


@mcp.tool()
@guarded
def list_notes(folder: str = "", limit: int = 200, with_frontmatter: bool = False) -> dict:
    """List note paths, optionally restricted to one folder."""
    c = cfg()
    out: list[dict[str, Any]] = []
    for p in iter_notes(c, folder or None):
        entry: dict[str, Any] = {"path": relpath(c, p)}
        if with_frontmatter:
            entry["frontmatter"] = parse_note(c, p).frontmatter
        out.append(entry)
        if len(out) >= limit:
            break
    return {"count": len(out), "notes": out}


@mcp.tool()
@guarded
def read_note(path: str) -> dict:
    """Read one note: frontmatter, body, tags and outgoing links."""
    c = cfg()
    p = resolve(c, path)
    if not p.exists():
        return {"error": "NotFound", "message": f"no such note: {path}"}
    note = parse_note(c, p)
    return {
        "path": note.rel,
        "frontmatter": note.frontmatter,
        "body": note.body,
        "tags": tags_in(note),
        "links_out": outgoing_links(note.body),
    }


@mcp.tool()
@guarded
def create_note(path: str, body: str, frontmatter: dict | None = None,
                overwrite: bool = False) -> dict:
    """Create a note. Refuses to clobber an existing note unless overwrite=true."""
    note = write_note(cfg(), path, body, frontmatter, overwrite)
    return {"path": note.rel, "frontmatter": note.frontmatter}


@mcp.tool()
@guarded
def append_to_note(path: str, text: str, heading: str = "") -> dict:
    """Append to a note, creating it if absent.

    The append-only primitive: prefer this over rewriting raw capture notes.
    """
    note = append_note(cfg(), path, text, heading=heading or None)
    return {"path": note.rel, "bytes": len(note.body)}


@mcp.tool()
@guarded
def patch_section(path: str, heading: str, text: str) -> dict:
    """Insert text at the end of the section under `heading`, adding the
    section at the end of the note if it is missing."""
    note = patch_note(cfg(), path, heading, text)
    return {"path": note.rel}


@mcp.tool()
@guarded
def update_frontmatter(path: str, updates: dict) -> dict:
    """Merge keys into a note's YAML frontmatter."""
    note = set_frontmatter(cfg(), path, updates)
    return {"path": note.rel, "frontmatter": note.frontmatter}


@mcp.tool()
@guarded
def move_note(src: str, dest: str, overwrite: bool = False) -> dict:
    """Move or rename a note inside the vault."""
    return _ops.move(cfg(), src, dest, overwrite)


@mcp.tool()
@guarded
def archive_note(path: str) -> dict:
    """Move a note into the archive folder. Nothing is ever deleted."""
    return _ops.archive(cfg(), path)


# ======================================================= find and search ===

@mcp.tool()
@guarded
def find_notes(pattern: str, folder: str = "", limit: int = 100) -> dict:
    """Find notes by NAME or path. `*`/`?` globs work; a bare word matches any
    name containing it.

    Use this when you know roughly what a note is called. Use `search_notes`
    when you need to look inside the content.
    """
    results = _ops.find(cfg(), pattern, folder or None, limit)
    return {"pattern": pattern, "count": len(results), "results": results}


@mcp.tool()
@guarded
def search_notes(query: str, folder: str = "", regex: bool = False,
                 case_sensitive: bool = False, limit: int = 50) -> dict:
    """Literal or regex search across note CONTENT, with surrounding lines.

    Plain text matching, no embeddings. Read the excerpts, judge relevance
    yourself, then `read_note` the ones that matter.
    """
    hits = _search.search(cfg(), query, folder or None, regex, case_sensitive,
                          limit=limit)
    return {"query": query, "hits": len(hits), "results": hits}


@mcp.tool()
@guarded
def search_by_tag(tag: str, limit: int = 100) -> dict:
    """Find notes carrying a tag (frontmatter `tags` or inline #tag).
    Nested tags match: `ml` also matches `ml/rl`."""
    return {"tag": tag, "results": _search.by_tag(cfg(), tag, limit)}


@mcp.tool()
@guarded
def search_frontmatter(key: str, value: str | None = None, limit: int = 100) -> dict:
    """Find notes by a frontmatter key, optionally matching an exact value."""
    return {"key": key, "results": _search.by_frontmatter(cfg(), key, value, limit)}


@mcp.tool()
@guarded
def backlinks(path: str) -> dict:
    """Notes that link to this one via [[wikilink]]."""
    c = cfg()
    p = resolve(c, path)
    return {"path": relpath(c, p), "backlinks": link_index(c).get(p.stem.casefold(), [])}


@mcp.tool()
@guarded
def related_notes(path: str, limit: int = 15) -> dict:
    """Graph neighbours of a note, scored: direct links either way, shared
    outbound links, shared tags. Each result says why it matched.

    Structural only — no semantics. Judge the candidates yourself.
    """
    return _ops.related(cfg(), path, limit)


@mcp.tool()
@guarded
def recent_notes(days: int = 7, folder: str = "", limit: int = 50) -> dict:
    """Notes modified in the last N days, newest first."""
    return {"days": days, "results": _ops.recent(cfg(), days, folder or None, limit)}


# ======================================================= session capture ===

@mcp.tool()
@guarded
def capture_session(
    title: str,
    summary: str,
    decisions: list[str] | None = None,
    open_questions: list[str] | None = None,
    artifacts: list[str] | None = None,
    tags: list[str] | None = None,
    project: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Persist the current session into the vault -- the `/compact` step.

    YOU write the summary; this tool only files it. Before calling, compact the
    session yourself into: what was being done and why, decisions taken with
    their reasons, what is still open, which files/artifacts were touched.
    Write durable facts, not a transcript -- a reader six months from now
    should not need the conversation.

    Creates a note in the sessions folder marked undistilled, and drops a
    pointer into today's log.
    """
    return _capture.capture_session(
        cfg(), title, summary, decisions, open_questions, artifacts,
        tags, project, session_id,
    )


@mcp.tool()
@guarded
def log_entry(text: str, source: str = "manual", date: str | None = None) -> dict:
    """Append a timestamped entry to the day's append-only log.

    For in-flight capture: a fact, a link, a half-formed idea. Never rewrite
    the log -- it is the raw stream distillation feeds on.
    """
    note = _capture.append_to_log(cfg(), text, source, date)
    return {"path": note.rel}


@mcp.tool()
@guarded
def read_daily_log(date: str | None = None) -> dict:
    """Read one day's log (ISO date, default today)."""
    c = cfg()
    rel = _capture.daily_note_rel(c, date)
    p = resolve(c, rel)
    if not p.exists():
        return {"path": rel, "exists": False, "body": ""}
    note = parse_note(c, p)
    return {"path": note.rel, "exists": True, "frontmatter": note.frontmatter,
            "body": note.body}


# =========================================================== distillation ===

@mcp.tool()
@guarded
def distill_queue(limit: int = 5, include_body: bool = True) -> dict:
    """Raw captures not yet distilled, oldest first, with their content.

    This is the core loop and it is YOUR job, not the server's: read each item,
    pull out the ideas that will still matter later, write each one as a single
    atomic note via `create_concept_note` (linking to existing notes with
    [[Title]]), then call `mark_distilled` on the source listing what you
    produced. Skip anything ephemeral -- mark it distilled with an empty
    `produced` list.
    """
    return _distill.queue(cfg(), limit, include_body)


@mcp.tool()
@guarded
def create_concept_note(title: str, claim: str, body: str = "",
                        links: list[str] | None = None, tags: list[str] | None = None,
                        sources: list[str] | None = None, overwrite: bool = False) -> dict:
    """Write one atomic note: one idea, stated as a claim.

    `title` should read as an assertion you could disagree with, not a topic
    label. `claim` is the idea in one or two sentences. `links` are titles of
    existing notes -- call `find_notes` or `search_notes` first so you link
    into the graph instead of creating an island.
    """
    return _distill.create_concept_note(cfg(), title, claim, body, links, tags,
                                        sources, overwrite)


@mcp.tool()
@guarded
def mark_distilled(path: str, produced: list[str] | None = None) -> dict:
    """Mark a raw capture as distilled, recording which notes came out of it."""
    return _distill.mark_distilled(cfg(), path, produced)


@mcp.tool()
@guarded
def vault_health() -> dict:
    """Where the graph is fraying: undistilled backlog, orphan notes,
    unresolved [[links]], most-connected hubs, per-folder counts.

    Use it to pick the next maintenance job -- an unresolved link is usually a
    note worth writing; an orphan is usually a note worth linking.
    """
    return _distill.health(cfg())


@mcp.tool()
@guarded
def build_map(topic: str, note_paths: list[str], intro: str = "") -> dict:
    """Write a Map-of-Content index note over notes you have curated."""
    return _distill.build_map(cfg(), topic, note_paths, intro)


@mcp.tool()
@guarded
def resurface_notes(count: int = 3, folder: str = "", with_body: bool = True) -> dict:
    """Random notes pulled up for re-reading -- the anti-write-only-vault move.

    Re-read them, then either link them somewhere new, sharpen the claim, or
    archive them if they no longer hold.
    """
    results = _ops.resurface(cfg(), count, folder or None, with_body)
    return {"count": len(results), "notes": results,
            "next_step": "Re-read each one. Link it, sharpen it, or archive it."}


@mcp.tool()
@guarded
def stale_notes(days: int = 90, folder: str = "", limit: int = 20) -> dict:
    """Notes untouched for N+ days, oldest first."""
    return {"days": days, "results": _ops.stale(cfg(), days, folder or None, limit)}


# =============================================================== prompts ===

@mcp.prompt()
def compact_to_vault(project: str = "") -> str:
    """Compact this session and file it in the vault."""
    return (
        "Compact this session, then persist it.\n\n"
        "1. Write a durable summary: what we were doing and why, decisions and "
        "their reasons, what is still open, which files changed. Facts a reader "
        "six months from now can use without the transcript. No pleasantries, "
        "no blow-by-blow.\n"
        "2. Call `capture_session` with that summary, the decisions, the open "
        "questions and the artifacts touched"
        + (", project=" + repr(project) if project else "")
        + ".\n"
        "3. If a durable idea emerged that is bigger than this session, also "
        "call `create_concept_note` for it and pass the new path to "
        "`mark_distilled(produced=...)`.\n"
    )


@mcp.prompt()
def distill(limit: int = 5) -> str:
    """Run one distillation pass over the raw capture backlog."""
    return (
        "Run a distillation pass.\n\n"
        "1. `distill_queue(limit=" + str(limit) + ")` to get raw captures.\n"
        "2. `vault_health()` and `list_notes(folder=<notes folder>)` so you know "
        "what already exists and can link into it rather than duplicate it.\n"
        "3. For each raw item, extract only ideas that will still matter in a "
        "year. One idea per note, via `create_concept_note`, title phrased as a "
        "claim, `links` pointing at existing note titles, `sources` at the raw "
        "path.\n"
        "4. `mark_distilled(path, produced=[...])` on each source -- including "
        "items you deliberately skipped, with an empty list.\n"
        "5. Report what you wrote and what you skipped."
    )


@mcp.prompt()
def review_brain() -> str:
    """Audit the vault and propose the next maintenance moves."""
    return (
        "Call `vault_health()`. Then: for each unresolved [[link]], decide "
        "whether it should become a note or be rewritten; for each orphan, use "
        "`related_notes` to find where it belongs and add the link; if a cluster "
        "of 5+ related notes has no map, propose `build_map`. Propose the "
        "changes with reasons before making them."
    )


_FOLDER_DOC = {
    "inbox": "raw capture, append-only. Never edited, only distilled.",
    "log": "daily append-only log, one note per day.",
    "sessions": "compacted agent sessions.",
    "notes": "atomic concept notes - one idea each, heavily linked.",
    "maps": "maps of content, curated indexes over the notes.",
    "archive": "things that are done and no longer distilled.",
}

_README = """# Second Brain

Local Obsidian vault at `{root}`, driven by the `obsidian-secondbrain` MCP
server. Nothing here talks to a network or holds an API key - the agent reads
raw material out of the vault, does the thinking, and writes distilled notes
back in.

## Structure

{folders}

## The loop

1. **Capture** - append raw material to the log or inbox. Never rewrite it.
2. **Compact** - at the end of a session the agent writes a session note.
3. **Distill** - the agent turns raw captures into atomic notes, one idea each,
   linked into the existing graph, then marks the source distilled.
4. **Review** - `vault_health` and `resurface_notes` surface orphans, dead
   links, backlog and things worth re-reading.
"""


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
