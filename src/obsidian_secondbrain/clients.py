"""Write MCP registration in the shape each client actually expects.

Four different shapes, and the differences are not cosmetic:

  Claude Code   .mcp.json                  root key "mcpServers"  + hooks
  Codex CLI     ~/.codex/config.toml       TOML [mcp_servers.NAME]
  VS Code       .vscode/mcp.json           root key "servers", needs "type"
  Copilot host  .mcp.json / ~/.copilot/    root key "mcpServers"
  Cursor        .cursor/mcp.json           root key "mcpServers"

Every writer backs up before rewriting and preserves unrelated entries.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
from pathlib import Path

SERVER_KEY = "obsidian-secondbrain"


def _backup(path: Path) -> str | None:
    if not path.exists():
        return None
    b = path.with_suffix(f"{path.suffix}.bak-{_dt.datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(path, b)
    return str(b)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_json(path: Path, data: dict) -> list[str]:
    written = []
    b = _backup(path)
    if b:
        written.append(b)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    written.insert(0, str(path))
    return written


def _json_server(py: str, vault: str, with_type: bool = False) -> dict:
    s: dict = {"command": py, "args": ["-m", "obsidian_secondbrain"],
               "env": {"OBSIDIAN_VAULT": vault}}
    if with_type:
        return {"type": "stdio", **s}
    return s


# ---------------------------------------------------------------- Codex ----

TOML_BLOCK_RE = re.compile(
    rf"^\[mcp_servers\.{re.escape(SERVER_KEY)}\]\s*$.*?(?=^\[|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def write_codex(path: Path, py: str, vault: str) -> dict:
    """Patch only our table, so comments and other servers survive.

    A full TOML round-trip would drop the user's comments, and the stdlib can
    read TOML but not write it -- so this edits the text in place.
    """
    block = (
        f"[mcp_servers.{SERVER_KEY}]\n"
        f'command = "{_toml_escape(py)}"\n'
        f'args = ["-m", "obsidian_secondbrain"]\n'
        f'env = {{ OBSIDIAN_VAULT = "{_toml_escape(vault)}" }}\n'
    )
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    written = []
    b = _backup(path)
    if b:
        written.append(b)

    if TOML_BLOCK_RE.search(existing):
        # A lambda replacement, because re.sub() would interpret the backslashes
        # in a Windows path as escapes and collapse "C:\\Users" to "C:\Users",
        # silently producing invalid TOML.
        updated = TOML_BLOCK_RE.sub(lambda _m: block, existing, count=1)
        action = "replaced existing entry"
    else:
        sep = "" if not existing or existing.endswith("\n\n") else (
            "\n" if existing.endswith("\n") else "\n\n")
        updated = existing + sep + block
        action = "appended entry"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    return {"files": [str(path), *written], "action": action}


# --------------------------------------------------------------- writers ---

def write_claude(project: Path, py: str, vault: str) -> dict:
    path = project / ".mcp.json"
    data = _read_json(path)
    data.setdefault("mcpServers", {})[SERVER_KEY] = _json_server(py, vault)
    return {"files": _write_json(path, data), "action": "wrote .mcp.json"}


def write_vscode(project: Path, py: str, vault: str) -> dict:
    path = project / ".vscode" / "mcp.json"
    data = _read_json(path)
    # VS Code uses "servers", not "mcpServers", and wants an explicit type.
    data.setdefault("servers", {})[SERVER_KEY] = _json_server(py, vault, with_type=True)
    return {"files": _write_json(path, data),
            "action": "wrote .vscode/mcp.json (root key 'servers')"}


def write_copilot(project: Path, py: str, vault: str) -> dict:
    # The Agent Host reads a workspace .mcp.json natively; write that so the
    # config is portable rather than VS Code-only.
    path = project / ".mcp.json"
    data = _read_json(path)
    data.setdefault("mcpServers", {})[SERVER_KEY] = _json_server(py, vault)
    return {"files": _write_json(path, data),
            "action": "wrote workspace .mcp.json (read natively by the Agent Host)"}


def write_cursor(project: Path, py: str, vault: str) -> dict:
    path = project / ".cursor" / "mcp.json"
    data = _read_json(path)
    data.setdefault("mcpServers", {})[SERVER_KEY] = _json_server(py, vault)
    return {"files": _write_json(path, data), "action": "wrote .cursor/mcp.json"}


# ------------------------------------------------- agent instruction files --

AGENTS_SECTION_START = "<!-- secbrain:start -->"
AGENTS_SECTION_END = "<!-- secbrain:end -->"

SESSION_START_RULE = """\
## Start every session by reading the vault

Before you act on the user's first request in a session, search the vault --
`search_notes` on the key terms of the request, `find_notes` on the obvious
titles, `vault_info` if you do not yet know the folder taxonomy. Read what
comes back *before* you open a file, run a command, or answer. What was decided
and why, what is still open and what was already tried live in there;
re-deriving them throws away the work that put them there.

Do this once per session, on the first request, whatever the request is. If
nothing relevant comes back, say so in one line and carry on -- an empty vault
is an answer, not a blocker.
"""

AGENTS_BODY = """\
## Second brain (obsidian-secondbrain MCP)

This project has a local Obsidian vault wired up over MCP. The server only
moves bytes on disk -- it never summarises. The thinking is yours.

- **Capture** as you go: `log_entry` for a fact or half-formed idea.
- **Compact** before you finish: write a durable summary yourself -- what we
  did and why, decisions and their reasons, what is still open, which files
  changed -- then call `capture_session` with it. This client has no automatic
  session hook, so if you skip this the session is not recorded.
- **Distil** when asked: `distill_queue` -> `create_concept_note` (one idea per
  note, title phrased as a claim, linked with [[wikilinks]]) -> `mark_distilled`.
- **Review**: `vault_health`, `related_notes`, `resurface_notes`.

Never edit a raw capture -- distil it instead. Never write an unlinked note:
`find_notes` or `search_notes` first, then link. Run `vault_info` once to learn
the folder taxonomy; it is configurable data, not fixed.
"""

CLAUDE_BODY = """\
## Second brain (obsidian-secondbrain MCP)

This project has a local Obsidian vault wired up over MCP. The server only
moves bytes on disk -- it never summarises. The thinking is yours.

- **Capture** as you go: `log_entry` for a fact or half-formed idea.
- **Compact** happens on its own: the `PreCompact` and `SessionEnd` hooks file
  the raw session into the inbox, undistilled. Still call `capture_session`
  with a written summary when a session decided something worth keeping -- a
  hook runs outside the model and cannot summarise.
- **Inject** happens on its own too: a `SessionStart` hook drops a short vault
  digest (undistilled backlog count, recent log tail) into context before you
  see the user's first message. It is a passive index read, not a search --
  the rule above still applies once you know what the request actually is.
- **Distil** when asked: `distill_queue` -> `create_concept_note` (one idea per
  note, title phrased as a claim, linked with [[wikilinks]]) -> `mark_distilled`.
- **Review**: `vault_health`, `related_notes`, `resurface_notes`.

Never edit a raw capture -- distil it instead. Never write an unlinked note:
`find_notes` or `search_notes` first, then link. Run `vault_info` once to learn
the folder taxonomy; it is configurable data, not fixed.
"""


def instruction_body(client: str) -> str:
    """The guidance block for `client`, context-first rule on top.

    The rule leads because it is the only part that has to fire before the
    agent does anything else; capture and distillation keep until later.
    """
    body = CLAUDE_BODY if client == "claude" else AGENTS_BODY
    return SESSION_START_RULE + "\n" + body


def write_instructions(path: Path, client: str = "codex") -> dict:
    """Write the per-session guidance into `client`'s instruction file.

    Every client gets it, Claude Code included: hooks only *write* to the
    vault at the end of a session, so without this nothing makes an agent
    *read* it at the start of one.

    Idempotent: replaces the marked block, leaves the rest of the file alone.
    """
    section = (f"{AGENTS_SECTION_START}\n{instruction_body(client)}"
               f"{AGENTS_SECTION_END}\n")
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    written = []
    b = _backup(path)
    if b:
        written.append(b)

    if AGENTS_SECTION_START in existing and AGENTS_SECTION_END in existing:
        pre = existing.split(AGENTS_SECTION_START)[0]
        post = existing.split(AGENTS_SECTION_END, 1)[1]
        updated = pre + section + post.lstrip("\n")
        action = "updated existing secbrain section"
    else:
        sep = "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
        updated = existing + sep + section
        action = "appended secbrain section"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    return {"files": [str(path), *written], "action": action}


INSTRUCTION_FILE = {
    "claude": lambda project: project / "CLAUDE.md",
    "codex": lambda project: project / "AGENTS.md",
    "vscode": lambda project: project / ".github" / "copilot-instructions.md",
    "copilot": lambda project: project / ".github" / "copilot-instructions.md",
    "cursor": lambda project: project / ".cursor" / "rules" / "secbrain.md",
}

WRITERS = {
    "claude": write_claude,
    "codex": None,        # handled separately: TOML, user-scope by default
    "vscode": write_vscode,
    "copilot": write_copilot,
    "cursor": write_cursor,
}
