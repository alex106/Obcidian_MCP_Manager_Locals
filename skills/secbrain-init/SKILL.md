---
name: secbrain-init
description: Set up the Obsidian second-brain MCP server for the current project - create or reuse a local vault, register the MCP server and the PreCompact/SessionEnd capture hooks, then fire the hook and verify it actually wrote a note. Use when the user runs /secbrain-init, asks to set up / initialise / repair the second brain or its vault in a project, or asks why session capture is not writing notes.
disable-model-invocation: true
---

# secbrain-init

Bring the local Obsidian second brain online for **this project**: a vault on
disk, the MCP server registered against it, the capture hooks wired, and proof
the hooks work.

Everything here runs through the package's own CLI, which is tested code.
**Do not hand-edit `.mcp.json`, `settings.json`, or the vault folders yourself**
— drive the subcommands and read their JSON. Each prints one JSON object and
exits non-zero on failure.

## Step 0 — Locate the installation

The server lives in the `obsidian-mcp` checkout. Find it, in order:

1. `C:/Users/posti/Documents/obsidian-mcp`
2. A sibling of the current project named `obsidian-mcp`
3. `git clone https://github.com/alex106/Obcidian_MCP_Manager_Locals` if the
   user wants it installed fresh — then create `.venv` and `pip install -e .`

Set `PY` to `<checkout>/.venv/Scripts/python.exe` on Windows, or
`<checkout>/.venv/bin/python` elsewhere. If that interpreter is missing, the
venv was never created — create it and install the package before continuing.

Every command below is:

```
"$PY" -m obsidian_secondbrain.cli <subcommand> --project "<project root>"
```

`--project` defaults to the cwd. Pass it explicitly so there is no ambiguity.
Add `--vault NAME` to change the vault directory name (default `SecondBrain`),
or give an absolute path to use a vault outside the project.

## Step 1 — Diagnose first

```
"$PY" -m obsidian_secondbrain.cli doctor --project "<root>"
```

Read `problems[]`. This tells you what is already correct, so you only fix what
is broken. Two cases deserve attention before you change anything:

- **`vault.has_obsidian_dir` is true** — a real Obsidian vault is already there.
  Reuse it; never reshape someone's existing vault without asking.
- **`mcp_server.vault_it_points_at` differs from `vault.path`** — a previous
  install (often a global one) points somewhere else. Say so explicitly and
  confirm before repointing it, because the user may be running one shared
  vault on purpose.

## Step 2 — Check with the user when the choice is real

Ask only when `doctor` shows a genuine fork:

- An existing vault was found somewhere other than the default location — use
  that one or create a project-local one?
- A global registration already points at a different vault — repoint it to
  this project, or leave it global?

If the project has no vault and no conflicting registration, there is no
decision to make. Proceed without asking.

## Step 3 — Create or reuse the vault

```
"$PY" -m obsidian_secondbrain.cli init --project "<root>" [--vault NAME]
```

Idempotent. `action` says `created vault` or `reused existing vault`; existing
folders and notes are never touched. It creates the six-folder taxonomy,
`.secondbrain/config.json`, and a README.

## Step 4 — Register the server and hooks

```
"$PY" -m obsidian_secondbrain.cli install --project "<root>" --scope project
```

Writes `.mcp.json` (the MCP server, with `OBSIDIAN_VAULT` set to this vault)
and `.claude/settings.local.json` (the `PreCompact` and `SessionEnd` capture
hooks). Hooks go in the *local* settings file because they embed absolute
machine-specific paths — they should not be committed. Both files are backed up
before being rewritten, and a previous install of this hook is replaced rather
than duplicated.

Use `--scope user` only if the user wants one vault across every project.

## Step 5 — Test the hook, do not assume it

This is the part that matters. Never report success without it.

```
"$PY" -m obsidian_secondbrain.cli test-hook --project "<root>"
```

It writes a synthetic transcript, fires the real hook as a subprocess against
the real vault, and asserts six things: the hook exits 0, it wrote exactly one
inbox note, the note carries the transcript text, it is marked
`distilled: false`, tool-call noise was excluded, and the daily log got a
pointer. It then restores the vault byte-for-byte — the inbox note is deleted
and the daily log is rolled back — so running it repeatedly leaves no residue.

If any check fails, report which one and its `detail`. Common causes:

| Failing check | Likely cause |
|---|---|
| hook exits 0 | wrong interpreter path, or the package is not installed in that venv |
| wrote exactly one inbox note | `OBSIDIAN_VAULT` not reaching the hook, or vault path wrong |
| note carries transcript text | transcript format changed — check `read_transcript` |
| daily log got a pointer | log folder renamed in `config.json` |

## Step 6 — Confirm and hand off

Re-run `doctor` and confirm `ok: true` with an empty `problems[]`. Then tell
the user, concretely:

- which vault is in use, and whether it was created or reused
- which files were written
- the hook test result as `N/6`
- **that Claude Code must be restarted** before the MCP tools appear, and that
  `/mcp` confirms the connection afterwards

The MCP tools are named `mcp__obsidian-secondbrain__*` and will not exist in
the current session, because MCP servers are connected at startup. Do not
claim the tools are available, and do not try to call them to "verify" — the
hook test above is the verification.

## Afterwards

Once connected, the workflow is: `log_entry` to capture, `capture_session` (or
the `/compact_to_vault` prompt) to compact a session, `distill_queue` →
`create_concept_note` → `mark_distilled` to distil, and `vault_health` to see
what needs attention. The folder taxonomy is data in
`<vault>/.secondbrain/config.json` — reshape it there, not in code.
