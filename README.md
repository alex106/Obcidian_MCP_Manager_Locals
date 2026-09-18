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

# register with Claude Code (server -> ~/.claude.json, hooks -> ~/.claude/settings.json;
# both backed up first)
.venv/Scripts/python.exe scripts/install.py --vault "C:/path/to/YourVault"
```

Then restart Claude Code and run `/mcp` to confirm `obsidian-secondbrain` is
connected. Add `--dry-run` to see the config without writing it, `--no-hooks`
to skip automatic session capture.

First thing in a fresh vault: ask the agent to call `init_vault`.

## Quick setup: the `secbrain-init` skill

Copy `skills/secbrain-init/` into `~/.claude/skills/`, restart Claude Code, and
run `/secbrain-init` in any project. It creates or reuses a vault there,
registers the server and hooks for that project, fires the capture hook and
verifies it wrote a note, then reports.

The skill drives the CLI below rather than editing config by hand, so setup is
tested code and behaves the same every time.

## CLI

```bash
PY=.venv/Scripts/python.exe    # or .venv/bin/python

$PY -m obsidian_secondbrain.cli detect    --project .   # OS, runtime, which client
$PY -m obsidian_secondbrain.cli doctor    --project .   # what is / isn't set up
$PY -m obsidian_secondbrain.cli init      --project .   # create or reuse the vault
$PY -m obsidian_secondbrain.cli install   --project .   # register server + hooks
$PY -m obsidian_secondbrain.cli test-hook --project .   # fire the hook, verify, clean up
```

Each prints one JSON object and exits non-zero on failure. `init` and
`install` are idempotent: existing notes are never touched, config files are
backed up before rewriting, unrelated entries are preserved, and re-running
replaces this hook rather than stacking duplicates.

## Which clients work

The server is plain stdio MCP, so **any** MCP client can use all 27 tools; the
vault is plain markdown that Obsidian itself reads. What differs is the
automation around it.

| | Claude Code | Codex | Copilot / VS Code | Cursor |
|---|---|---|---|---|
| 27 MCP tools | yes | yes | yes | yes |
| `capture_session` (manual compact) | yes | yes | yes | yes |
| MCP prompts | yes | varies | yes | yes |
| **Automatic capture hooks** | **yes** | **yes** | no | no |
| `/secbrain-init` skill | yes | no | no | no |

Automatic capture now works on both Claude Code and Codex, but not the same
way. Claude Code's hook parses the `PreCompact`/`SessionEnd` transcript
directly. Codex documents its rollout transcript format as unstable, so its
hooks avoid it: `UserPromptSubmit`/`Stop` buffer each turn's stable fields
(the prompt, the final assistant message) to
`<vault>/.secondbrain/buffer/<session_id>.jsonl`, and `PreCompact`/`SessionEnd`
turn that buffer into the same kind of inbox note. Codex also skips every hook
until it is reviewed and trusted in `/hooks` — `install` and `doctor` both
say so, and re-trust is needed after any re-install that changes a command.
Copilot and Cursor have neither event, so capture stays manual there —
`capture_session` is an ordinary tool, so an agent writing its own summary and
filing it works anywhere. `install --with-instructions` writes
`.github/copilot-instructions.md` / `.cursor/rules/secbrain.md` for those two;
Codex gets `AGENTS.md` by default now, the same way Claude Code gets
`CLAUDE.md`, no flag needed.

### Reading the vault at the start of a session

Two mechanisms cover this, and they are deliberately not the same thing.

A `SessionStart` hook (`hooks/session_start.py`, Claude Code and Codex) fires
unconditionally on every session start/resume/clear/compact and injects a
small, passive digest as `additionalContext` — the undistilled backlog count
and the tail of the most recent log. It never searches anything; it can't,
because the user's actual request doesn't exist yet at that point.

`install` also writes a marked block into the project's rule file — `CLAUDE.md`
for Claude Code, `AGENTS.md` for Codex, with no flag needed for either — whose
first instruction is: **before acting on the first request of a session,
search the vault**. This is the *active* half: a targeted
`search_notes`/`find_notes` once the request is known, which a hook firing
before any user text exists structurally cannot do.

Every client gets the rule, Claude Code and Codex included, because a capture
hook only *writes* the vault when a session **ends** — without the rule (or,
on top of the hook's passive digest), the vault only ever fills up, and a
session starts by re-deriving what is already written down. `doctor` reports
`project_rules.has_session_start_rule` and `hooks.missing` (which lists any of
`PreCompact`/`SessionEnd`/`SessionStart` not yet registered, plus
`UserPromptSubmit`/`Stop` on the Codex path), and `--no-instructions` opts out
of the rule file specifically.

`detect` identifies the client from evidence and distinguishes an `agent`
signal (we are running as it) from a `host` signal (it is merely the editor
hosting the terminal) — Claude Code inside a VS Code terminal makes both look
active.

```bash
$PY -m obsidian_secondbrain.cli install   --project . --client codex  --scope user
$PY -m obsidian_secondbrain.cli install   --project . --client vscode --with-instructions
$PY -m obsidian_secondbrain.cli install   --project . --client all
$PY -m obsidian_secondbrain.cli test-hook --project . --client codex
$PY -m obsidian_secondbrain.cli doctor    --project . --client codex
```

`test-hook` and `doctor` default to `--client claude`; pass `--client codex`
to check the Codex-side wiring (hooks.json, the turn buffer, AGENTS.md)
instead. Neither command can see whether Codex has actually **trusted** the
hooks in `/hooks` — that state lives only in Codex, keyed by each hook's
hash — so both report the caveat explicitly rather than implying a passing
check means capture is live.

Config shapes are not interchangeable, and a wrong shape is silently ignored
with no error: VS Code uses root key `servers` and requires `"type": "stdio"`;
Claude Code, Copilot and Cursor use `mcpServers`; Codex uses TOML
`[mcp_servers.NAME]`. The Codex writer patches only its own table so comments
and other servers survive.

`install --scope project` (default) writes `.mcp.json` plus
`.claude/settings.local.json` — hooks go in the local file because they embed
absolute machine paths. `--scope user` registers one vault globally instead.

`test-hook` is the one that matters: it writes a synthetic transcript, runs
**the command exactly as registered** with `OBSIDIAN_VAULT` stripped from the
environment — what a real session gives a hook — asserts seven properties of
the note it produced, then restores the vault byte-for-byte.

That last detail is not pedantry. A hook does not inherit the `env` block of an
MCP server config, so a hook registered without `--vault` finds no vault, exits
0 and silently writes nothing. `install` therefore passes the vault on the
command line, and `test-hook` runs the registered command under a stripped
environment so a no-op hook cannot pass.

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
INJECT   →  the SessionStart hook (Claude Code, Codex) + the context-first rule
CAPTURE  →  log_entry, append_to_note, the PreCompact/SessionEnd hooks
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
summarise anything. It files raw material — user prompts and assistant text
only, no tool spam, most recent ~20k chars — into the inbox as an undistilled
note. On Claude Code that material is the transcript at `transcript_path`. On
Codex, whose transcript format is documented as unstable, it is instead
whatever `hooks/codex_turn_buffer.py` already appended from
`UserPromptSubmit.prompt` and `Stop.last_assistant_message` — two fields Codex
does treat as stable — to `<vault>/.secondbrain/buffer/<session_id>.jsonl`;
the buffer is consumed (deleted) once its note is written, so a `PreCompact`
capture followed by a `SessionEnd` capture never files the same turns twice.
The next `distill_queue` call hands the note to the agent, which does the
thinking. Automatic capture without ever needing a model behind the server's
back.

The `SessionStart` hook (`hooks/session_start.py`) is the same idea run in
reverse: it can't summarise either, so it just counts and quotes — the
undistilled backlog size and the tail of the latest log — and hands that back
as `additionalContext`. Injection without a model in the hook, same as capture.

Every hook always exits 0. A failed capture never blocks your session. On
Codex this matters doubly: `SessionEnd` gets 1 s by default and 3 s at the
hard ceiling, so its capture only ever has to rename a buffer file into a
note — the actual buffering already happened earlier, in `UserPromptSubmit`
and `Stop`, where there's no shared time budget to blow.

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
  cli.py       detect / doctor / init / install / test-hook bootstrap
  environment.py  evidence-based OS + client detection
  clients.py      per-client config writers (json / toml shapes)
hooks/capture_session.py    PreCompact / SessionEnd raw capture (Claude Code + Codex)
hooks/session_start.py      SessionStart digest injection (Claude Code + Codex)
hooks/codex_turn_buffer.py  Codex UserPromptSubmit / Stop: buffers each turn's stable fields
hooks/_common.py            shared vault resolution + the Codex buffer path, for all hook scripts
scripts/install.py          user-scope registration (~/.claude.json + settings.json)
skills/secbrain-init/       Claude Code skill: /secbrain-init
tests/test_lifecycle.py     34 assertions over a real stdio MCP client
tests/test_hook_wiring.py   proves test-hook rejects an env-only hook
tests/test_clients.py       pins each client's config shape
tests/test_codex_hooks.py   install/test-hook/doctor --client codex, end to end
```
