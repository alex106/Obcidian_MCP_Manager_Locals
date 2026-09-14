"""Finding, moving and resurfacing notes.

`find` here means *locate a note by name or path* -- the complement to
`search`, which looks inside content. Resurfacing (random / stale / review)
is the part of the second-brain loop that fights write-only vaults.
"""

from __future__ import annotations

import datetime as _dt
import fnmatch
import random as _random
import shutil

from .config import VaultConfig
from .vault import (
    VaultError,
    iter_notes,
    outgoing_links,
    parse_note,
    relpath,
    resolve,
    tags_in,
)


def find(cfg: VaultConfig, pattern: str, folder: str | None = None,
         limit: int = 100) -> list[dict]:
    """Locate notes by filename or path glob. `*` and `?` work; a bare word
    matches any name containing it."""
    pat = pattern.strip()
    glob = pat if any(ch in pat for ch in "*?[") else f"*{pat}*"
    glob = glob.casefold()
    out = []
    for p in iter_notes(cfg, folder):
        rel = relpath(cfg, p)
        if fnmatch.fnmatch(p.stem.casefold(), glob) or fnmatch.fnmatch(rel.casefold(), glob):
            out.append({"path": rel, "title": p.stem,
                        "modified": _dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")})
        if len(out) >= limit:
            break
    return out


def recent(cfg: VaultConfig, days: int = 7, folder: str | None = None,
           limit: int = 50) -> list[dict]:
    cutoff = _dt.datetime.now().timestamp() - days * 86400
    rows = []
    for p in iter_notes(cfg, folder):
        st = p.stat()
        if st.st_mtime >= cutoff:
            rows.append((st.st_mtime, relpath(cfg, p)))
    rows.sort(reverse=True)
    return [
        {"path": rel,
         "modified": _dt.datetime.fromtimestamp(mt).isoformat(timespec="seconds")}
        for mt, rel in rows[:limit]
    ]


def stale(cfg: VaultConfig, days: int = 90, folder: str | None = None,
          limit: int = 20) -> list[dict]:
    """Notes untouched for a long time -- resurfacing candidates."""
    cutoff = _dt.datetime.now().timestamp() - days * 86400
    rows = [(p.stat().st_mtime, relpath(cfg, p)) for p in iter_notes(cfg, folder)
            if p.stat().st_mtime < cutoff]
    rows.sort()
    return [
        {"path": rel,
         "modified": _dt.datetime.fromtimestamp(mt).isoformat(timespec="seconds")}
        for mt, rel in rows[:limit]
    ]


def resurface(cfg: VaultConfig, count: int = 3, folder: str | None = None,
              with_body: bool = True) -> list[dict]:
    """Random notes, to be re-read and re-connected."""
    paths = list(iter_notes(cfg, folder if folder else cfg.folders["notes"]))
    if not paths:
        return []
    picked = _random.sample(paths, min(count, len(paths)))
    out = []
    for p in picked:
        note = parse_note(cfg, p)
        row = {"path": note.rel, "title": p.stem, "tags": tags_in(note),
               "links_out": outgoing_links(note.body)}
        if with_body:
            row["body"] = note.body
        out.append(row)
    return out


def move(cfg: VaultConfig, src: str, dest: str, overwrite: bool = False) -> dict:
    s = resolve(cfg, src)
    d = resolve(cfg, dest)
    if not s.exists():
        raise VaultError(f"no such note: {src}")
    if d.exists() and not overwrite:
        raise VaultError(f"destination exists: {relpath(cfg, d)}")
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return {"from": relpath(cfg, s), "to": relpath(cfg, d),
            "note": "wikilinks are by basename; if the basename changed, "
                    "run search_notes on the old name and fix the links."}


def archive(cfg: VaultConfig, path: str) -> dict:
    src = resolve(cfg, path)
    if not src.exists():
        raise VaultError(f"no such note: {path}")
    dest = f"{cfg.folders['archive']}/{src.name}"
    return move(cfg, path, dest, overwrite=True)


def related(cfg: VaultConfig, path: str, limit: int = 15) -> dict:
    """Neighbours in the graph: shared links, shared tags, direct links both ways."""
    target = resolve(cfg, path)
    if not target.exists():
        raise VaultError(f"no such note: {path}")
    note = parse_note(cfg, target)
    my_links = {t.casefold() for t in outgoing_links(note.body)}
    my_tags = {t.casefold() for t in tags_in(note)}
    stem = target.stem.casefold()

    scored = []
    for p in iter_notes(cfg):
        if p == target:
            continue
        other = parse_note(cfg, p)
        o_links = {t.casefold() for t in outgoing_links(other.body)}
        o_tags = {t.casefold() for t in tags_in(other)}
        score = 0
        reasons = []
        if stem in o_links:
            score += 3
            reasons.append("links here")
        if p.stem.casefold() in my_links:
            score += 3
            reasons.append("linked from here")
        shared_l = my_links & o_links
        if shared_l:
            score += len(shared_l)
            reasons.append(f"shares links: {', '.join(sorted(shared_l)[:3])}")
        shared_t = my_tags & o_tags
        if shared_t:
            score += len(shared_t)
            reasons.append(f"shares tags: {', '.join(sorted(shared_t)[:3])}")
        if score:
            scored.append((score, other.rel, reasons))

    scored.sort(key=lambda t: -t[0])
    return {
        "path": note.rel,
        "related": [{"path": r, "score": s, "why": w} for s, r, w in scored[:limit]],
    }
