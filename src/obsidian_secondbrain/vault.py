"""Filesystem layer: safe paths, frontmatter, notes, wikilinks.

Pure local file I/O. Nothing here calls a network, and nothing here reasons
about content -- reasoning is the agent's job.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import VaultConfig

FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]+)?(?:\|[^\]]+)?\]\]")
TAG_RE = re.compile(r"(?:(?<=\s)|\A)#([A-Za-z0-9_][A-Za-z0-9_/-]*)")


class VaultError(RuntimeError):
    pass


# ---------------------------------------------------------------- paths ----

def resolve(cfg: VaultConfig, rel: str) -> Path:
    """Resolve a vault-relative path, refusing anything outside the vault."""
    if not rel or not rel.strip():
        raise VaultError("empty note path")
    candidate = (cfg.root / rel.strip().replace("\\", "/")).resolve()
    try:
        candidate.relative_to(cfg.root)
    except ValueError:
        raise VaultError(f"path escapes the vault: {rel!r}") from None
    if candidate.suffix == "":
        candidate = candidate.with_suffix(cfg.ext)
    return candidate


def relpath(cfg: VaultConfig, path: Path) -> str:
    return path.resolve().relative_to(cfg.root).as_posix()


def is_excluded(cfg: VaultConfig, path: Path) -> bool:
    ex = cfg.exclude
    return any(part in ex for part in path.relative_to(cfg.root).parts[:-1]) or (
        path.name in ex
    )


def iter_notes(cfg: VaultConfig, folder: str | None = None):
    base = cfg.root if not folder else resolve(cfg, folder).with_suffix("")
    if not base.exists():
        return
    for p in sorted(base.rglob(f"*{cfg.ext}")):
        if is_excluded(cfg, p):
            continue
        yield p


# ---------------------------------------------------------- frontmatter ----

@dataclass
class Note:
    path: Path
    rel: str
    frontmatter: dict
    body: str

    def render(self) -> str:
        return dump_note(self.frontmatter, self.body)


def parse_note(cfg: VaultConfig, path: Path) -> Note:
    text = path.read_text(encoding="utf-8")
    fm: dict = {}
    body = text
    m = FM_RE.match(text)
    if m:
        try:
            loaded = yaml.safe_load(m.group(1))
            if isinstance(loaded, dict):
                fm = loaded
        except yaml.YAMLError:
            fm = {"_frontmatter_parse_error": True}
        body = text[m.end():]
    return Note(path=path, rel=relpath(cfg, path), frontmatter=fm, body=body)


def dump_note(frontmatter: dict | None, body: str) -> str:
    if not frontmatter:
        return body
    head = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True, default_flow_style=False
    ).strip()
    return f"---\n{head}\n---\n\n{body.lstrip()}"


# ---------------------------------------------------------------- write ----

def write_note(
    cfg: VaultConfig,
    rel: str,
    body: str,
    frontmatter: dict | None = None,
    overwrite: bool = False,
) -> Note:
    path = resolve(cfg, rel)
    if path.exists() and not overwrite:
        raise VaultError(
            f"{relpath(cfg, path)} already exists. Pass overwrite=true, or use "
            "append_note / patch_note to add to it."
        )
    fm = dict(frontmatter or {})
    now = _dt.datetime.now().isoformat(timespec="seconds")
    fm.setdefault("created", now)
    fm["updated"] = now
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_note(fm, body), encoding="utf-8")
    return parse_note(cfg, path)


def append_note(
    cfg: VaultConfig,
    rel: str,
    text: str,
    frontmatter: dict | None = None,
    heading: str | None = None,
) -> Note:
    """Append to a note, creating it if absent. The append-only primitive."""
    path = resolve(cfg, rel)
    if not path.exists():
        return write_note(cfg, rel, _block(text, heading), frontmatter)
    note = parse_note(cfg, path)
    body = note.body.rstrip("\n") + "\n\n" + _block(text, heading).strip() + "\n"
    fm = dict(note.frontmatter)
    if frontmatter:
        fm.update(frontmatter)
    fm["updated"] = _dt.datetime.now().isoformat(timespec="seconds")
    path.write_text(dump_note(fm, body), encoding="utf-8")
    return parse_note(cfg, path)


def _block(text: str, heading: str | None) -> str:
    return f"{heading.rstrip()}\n\n{text.strip()}\n" if heading else f"{text.strip()}\n"


def patch_note(cfg: VaultConfig, rel: str, heading: str, text: str) -> Note:
    """Insert text at the end of the section under `heading`.

    Creates the section at the end of the note if it is not present.
    """
    path = resolve(cfg, rel)
    if not path.exists():
        raise VaultError(f"no such note: {rel}")
    note = parse_note(cfg, path)
    lines = note.body.splitlines()
    target = heading.strip().lstrip("#").strip().casefold()

    start = None
    level = 0
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m and m.group(2).strip().casefold() == target:
            start = i
            level = len(m.group(1))
            break

    if start is None:
        body = note.body.rstrip("\n") + f"\n\n{heading.strip()}\n\n{text.strip()}\n"
    else:
        end = len(lines)
        for j in range(start + 1, len(lines)):
            m = re.match(r"^(#{1,6})\s+", lines[j])
            if m and len(m.group(1)) <= level:
                end = j
                break
        section = lines[start:end]
        while section and not section[-1].strip():
            section.pop()
        section.append("")
        section.append(text.strip())
        section.append("")
        lines[start:end] = section
        body = "\n".join(lines)

    fm = dict(note.frontmatter)
    fm["updated"] = _dt.datetime.now().isoformat(timespec="seconds")
    path.write_text(dump_note(fm, body), encoding="utf-8")
    return parse_note(cfg, path)


def set_frontmatter(cfg: VaultConfig, rel: str, updates: dict) -> Note:
    path = resolve(cfg, rel)
    if not path.exists():
        raise VaultError(f"no such note: {rel}")
    note = parse_note(cfg, path)
    fm = dict(note.frontmatter)
    fm.update(updates)
    fm["updated"] = _dt.datetime.now().isoformat(timespec="seconds")
    path.write_text(dump_note(fm, note.body), encoding="utf-8")
    return parse_note(cfg, path)


# ---------------------------------------------------------------- links ----

def outgoing_links(body: str) -> list[str]:
    return sorted({m.group(1).strip() for m in WIKILINK_RE.finditer(body)})


def tags_in(note: Note) -> list[str]:
    tags = set()
    fm_tags = note.frontmatter.get("tags")
    if isinstance(fm_tags, str):
        tags.update(t.strip() for t in fm_tags.split(",") if t.strip())
    elif isinstance(fm_tags, list):
        tags.update(str(t).strip() for t in fm_tags)
    tags.update(m.group(1) for m in TAG_RE.finditer(note.body))
    return sorted(t.lstrip("#") for t in tags if t)


def link_index(cfg: VaultConfig) -> dict[str, list[str]]:
    """basename -> list of notes linking to it."""
    index: dict[str, list[str]] = {}
    for p in iter_notes(cfg):
        note = parse_note(cfg, p)
        for target in outgoing_links(note.body):
            index.setdefault(target.casefold(), []).append(note.rel)
    return index


def note_titles(cfg: VaultConfig) -> dict[str, str]:
    """basename(casefolded) -> vault-relative path."""
    return {p.stem.casefold(): relpath(cfg, p) for p in iter_notes(cfg)}
