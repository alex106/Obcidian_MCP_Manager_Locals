"""Local search over the vault. Plain string/regex matching -- no embeddings,
no model calls. The agent does the semantic work on what this returns.
"""

from __future__ import annotations

import re

from .config import VaultConfig
from .vault import iter_notes, parse_note, relpath, tags_in


def search(
    cfg: VaultConfig,
    query: str,
    folder: str | None = None,
    regex: bool = False,
    case_sensitive: bool = False,
    context: int = 1,
    limit: int = 50,
) -> list[dict]:
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(query if regex else re.escape(query), flags)

    hits: list[dict] = []
    for path in iter_notes(cfg, folder):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not pattern.search(text):
            continue
        lines = text.splitlines()
        matches = []
        for i, line in enumerate(lines):
            if pattern.search(line):
                lo = max(0, i - context)
                hi = min(len(lines), i + context + 1)
                matches.append(
                    {"line": i + 1, "text": "\n".join(lines[lo:hi]).strip()}
                )
            if len(matches) >= 5:
                break
        hits.append({"path": relpath(cfg, path), "matches": matches})
        if len(hits) >= limit:
            break
    return hits


def by_tag(cfg: VaultConfig, tag: str, limit: int = 100) -> list[dict]:
    want = tag.lstrip("#").casefold()
    out = []
    for path in iter_notes(cfg):
        note = parse_note(cfg, path)
        tags = [t.casefold() for t in tags_in(note)]
        if any(t == want or t.startswith(want + "/") for t in tags):
            out.append({"path": note.rel, "tags": tags_in(note)})
        if len(out) >= limit:
            break
    return out


def by_frontmatter(cfg: VaultConfig, key: str, value=None, limit: int = 100) -> list[dict]:
    out = []
    for path in iter_notes(cfg):
        note = parse_note(cfg, path)
        if key not in note.frontmatter:
            continue
        if value is not None and note.frontmatter[key] != value:
            continue
        out.append({"path": note.rel, key: note.frontmatter[key]})
        if len(out) >= limit:
            break
    return out
