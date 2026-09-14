"""The distillation loop -- raw capture becomes atomic linked notes.

The server never summarises anything. It finds *what* needs attention and
hands the raw material to the agent; the agent writes the distilled note back
through `create_concept_note` and marks the source done.
"""

from __future__ import annotations

import datetime as _dt

from .config import VaultConfig
from .vault import (
    iter_notes,
    link_index,
    note_titles,
    outgoing_links,
    parse_note,
    relpath,
    set_frontmatter,
    write_note,
)

RAW_FOLDERS = ("inbox", "log", "sessions")


def queue(cfg: VaultConfig, limit: int = 10, include_body: bool = True) -> dict:
    """Raw captures not yet marked distilled, oldest first."""
    key = cfg.distilled_key
    pending = []
    for folder in RAW_FOLDERS:
        for path in iter_notes(cfg, cfg.folders[folder]):
            note = parse_note(cfg, path)
            if note.frontmatter.get(key) is True:
                continue
            pending.append((path.stat().st_mtime, folder, note))

    pending.sort(key=lambda t: t[0])
    items = []
    for _, folder, note in pending[:limit]:
        item = {
            "path": note.rel,
            "kind": folder,
            "title": note.frontmatter.get("title") or note.path.stem,
            "date": note.frontmatter.get("date"),
        }
        if include_body:
            item["content"] = note.body
        items.append(item)

    return {
        "pending_total": len(pending),
        "returned": len(items),
        "items": items,
        "next_step": (
            "For each item: extract the durable ideas, write each as one atomic "
            "note via create_concept_note (linking to existing notes with [[...]]), "
            "then call mark_distilled on the source path."
        ),
    }


def mark_distilled(cfg: VaultConfig, rel: str, produced: list[str] | None = None) -> dict:
    updates = {
        cfg.distilled_key: True,
        "distilled_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    if produced:
        updates["produced"] = produced
    note = set_frontmatter(cfg, rel, updates)
    return {"path": note.rel, "frontmatter": note.frontmatter}


def create_concept_note(
    cfg: VaultConfig,
    title: str,
    claim: str,
    body: str = "",
    links: list[str] | None = None,
    tags: list[str] | None = None,
    sources: list[str] | None = None,
    overwrite: bool = False,
) -> dict:
    """One atomic note = one idea, stated as a claim in the title/first line."""
    from .capture import title_filename

    # Filename == title, so [[title]] from any other note resolves here.
    rel = f"{cfg.folders['notes']}/{title_filename(title)}{cfg.ext}"
    fm = {
        "type": "concept",
        "title": title,
        "tags": tags or [],
        "sources": sources or [],
    }
    parts = [f"# {title}", "", claim.strip(), ""]
    if body.strip():
        parts += [body.strip(), ""]
    if links:
        parts += ["## Related", ""] + [f"- [[{t}]]" for t in links] + [""]
    if sources:
        parts += ["## Sources", ""] + [f"- [[{s.rsplit('/', 1)[-1].removesuffix(cfg.ext)}]]" for s in sources] + [""]

    note = write_note(cfg, rel, "\n".join(parts), fm, overwrite=overwrite)
    return {"path": note.rel, "title": title}


def health(cfg: VaultConfig) -> dict:
    """Where the brain is fraying: orphans, dead links, undistilled backlog."""
    titles = note_titles(cfg)
    incoming = link_index(cfg)

    orphans, unresolved, hubs = [], {}, {}
    for path in iter_notes(cfg, cfg.folders["notes"]):
        note = parse_note(cfg, path)
        stem = path.stem.casefold()
        if not incoming.get(stem):
            orphans.append(note.rel)
        outs = outgoing_links(note.body)
        hubs[note.rel] = len(outs)
        for target in outs:
            if target.casefold() not in titles:
                unresolved.setdefault(target, []).append(note.rel)

    pending = queue(cfg, limit=0, include_body=False)["pending_total"]
    counts = {k: sum(1 for _ in iter_notes(cfg, v)) for k, v in cfg.folders.items()}

    return {
        "counts": counts,
        "undistilled_raw_notes": pending,
        "orphan_concept_notes": orphans[:50],
        "unresolved_links": {k: v[:5] for k, v in list(unresolved.items())[:50]},
        "most_connected": sorted(hubs.items(), key=lambda kv: -kv[1])[:10],
    }


def build_map(cfg: VaultConfig, topic: str, note_paths: list[str],
              intro: str = "") -> dict:
    """Write a Map-of-Content index note the agent has curated."""
    from .capture import title_filename

    rel = f"{cfg.folders['maps']}/{title_filename(topic)}{cfg.ext}"
    lines = [f"# {topic}", ""]
    if intro.strip():
        lines += [intro.strip(), ""]
    lines += ["## Notes", ""]
    for p in note_paths:
        lines.append(f"- [[{p.rsplit('/', 1)[-1].removesuffix(cfg.ext)}]]")
    lines.append("")
    note = write_note(
        cfg, rel, "\n".join(lines),
        {"type": "map", "title": topic}, overwrite=True,
    )
    return {"path": note.rel, "entries": len(note_paths)}
