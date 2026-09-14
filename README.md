# obsidian-secondbrain-mcp

A local-only MCP server that turns an Obsidian vault into a working second
brain. **No network calls, no API keys, no embeddings, no model calls anywhere
in this package.** Every tool either moves bytes on local disk or hands raw
material back to the agent with an explicit `next_step` — the thinking is the
agent's job, and the results come back through the same tools.

## Install

```bash
cd obsidian-mcp
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .          # Windows
# .venv/bin/python -m pip install -e .                # macOS/Linux

# register with Claude Code (backs up settings.json first)
.venv/Scripts/python.exe scripts/install.py --vault "C:/path/to/YourVault"
```

Then restart Claude Code and run `/mcp` to confirm `obsidian-secondbrain` is
connected. Add `--dry-run` to see the config without writing it, `--no-hooks`
to skip automatic session capture.

First thing in a fresh vault: ask the agent to call `init_vault`.

## Configuration

Exactly one input: the `OBSIDIAN_VAULT` environment variable, set in the MCP
server config. Everything else — folder names, date formats, the frontmatter
key that marks a note distilled — lives in `<vault>/.secondbrain/config.json`,
created by `init_vault`. **Reshape the method by editing that file; no code
changes needed.**

```json
{
  "folders": {
    "inbox": "00-Inbox", "log": "10-Log", "sessions": "20-Sessions",
    "notes": "30-Notes", "maps": "40-Maps", "archive": "90-Archive"
  },
  "daily_note_format": "%Y-%m-%d",
  "distilled_key": "distilled",
  "exclude": [".obsidian", ".trash", ".git", ".secondbrain"]
}
```

## The method

Raw capture is append-only and never rewritten. Distillation is a separate,
deliberate pass that turns raw material into atomic notes — one idea each,
titled as a claim, linked into the existing graph. Review fights the
write-only vault.

```
CAPTURE  →  log_entry, append_to_note, the SessionEnd hook
COMPACT  →  capture_session   (agent writes the summary, server files it)
DISTILL  →  distill_queue → create_concept_note → mark_distilled
REVIEW   →  vault_health, resurface_notes, related_notes, stale_notes
```

## Tools (27)

**Vault** — `vault_info`, `init_vault`, `list_notes`, `read_note`,
`create_note`, `append_to_note`, `patch_section`, `update_frontmatter`,
`move_note`, `archive_note`

**Find & search** — `find_notes` (by *name*/glob), `search_notes` (inside
*content*, literal or regex), `search_by_tag` (nested tags match),
`search_frontmatter`, `backlinks`, `related_notes` (scored graph neighbours
with reasons), `recent_notes`

**Session capture** — `capture_session`, `log_entry`, `read_daily_log`

**Distillation & review** — `distill_queue`, `create_concept_note`,
`mark_distilled`, `vault_health`, `build_map`, `resurface_notes`,
`stale_notes`

## Prompts

`compact_to_vault` · `distill` · `review_brain` — surfaced as slash commands
in Claude Code once the server is connected.

## Session capture, and why the hook files raw text

`capture_session` is the `/compact` step: **the agent** writes the durable
summary (what, why, decisions, open questions, artifacts) and the server files
it as a session note marked undistilled, plus a pointer in the day's log.

The `PreCompact`/`SessionEnd` hook runs *outside* the model, so it cannot
summarise anything. It files the raw transcript — user prompts and assistant
text only, no tool spam, most recent ~20k chars — into the inbox as an
undistilled note. The next `distill_queue` call hands that to the agent, which
does the thinking. Automatic capture without ever needing a model behind the
server's back.

The hook always exits 0. A failed capture never blocks your session.

## Safety

Every path is resolved and checked against the vault root, so `../` escapes
are refused. `archive_note` moves rather than deletes; nothing in this package
deletes a note. Tool errors come back as `{"error": ..., "message": ...}` data
so the agent can recover instead of the call blowing up.

## Layout

```
src/obsidian_secondbrain/
  config.py    vault path + data-driven taxonomy
  vault.py     safe paths, frontmatter, read/append/patch, wikilinks
  search.py    content, tag and frontmatter search
  ops.py       find by name, move/archive, related, recent/stale/resurface
  capture.py   daily log, session notes
  distill.py   distill queue, concept notes, health, maps
  server.py    MCP tool + prompt surface
hooks/capture_session.py    PreCompact / SessionEnd raw capture
scripts/install.py          settings.json registration
```
