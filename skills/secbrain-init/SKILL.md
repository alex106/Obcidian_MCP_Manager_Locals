---
name: secbrain-init
description: Set up the Obsidian second-brain MCP server for the current project - detect the OS and which agent client is in use (Claude Code, Codex, Copilot/VS Code, Cursor), create or reuse a local vault, register the server in that client's own config format, wire the capture hooks where the client supports them, then fire the hook and verify it actually wrote a note. Use when the user runs /secbrain-init, asks to set up / initialise / repair the second brain or its vault, asks whether it works with Codex or Copilot, or asks why session capture is not writing notes.
disable-model-invocation: true
---

# secbrain-init

Bring the local Obsidian second brain online for **this project**, in a way
that fits **this environment**: the right vault, registered in the right config
format for whichever agent client the user actually runs, with capture wired as
far as that client allows — and proof it works.

Everything runs through the package's own CLI, which is tested code.
**Do not hand-edit `.mcp.json`, `config.toml`, `settings.json`, or the vault
folders yourself** — drive the subcommands and read their JSON. Each prints one
JSON object and exits non-zero on failure.

## Step 0 — Locate the installation

Find the `obsidian-mcp` checkout, in order:

1. `C:/Users/posti/Documents/obsidian-mcp`
2. A sibling of the current project named `obsidian-mcp`
3. `git clone https://github.com/alex106/Obcidian_MCP_Manager_Locals` if the
   user wants a fresh install — then create `.venv` and `pip install -e .`

Set `PY` to `<checkout>/.venv/Scripts/python.exe` (Windows) or
`<checkout>/.venv/bin/python`. Every command below is:

```
"$PY" -m obsidian_secondbrain.cli <subcommand> --project "<project root>"
```

Always pass `--project` explicitly. Add `--vault NAME` to change the vault
directory name (default `SecondBrain`), or an absolute path for a vault outside
the project.

## Step 1 — Detect the environment before advising anything

```
"$PY" -m obsidian_secondbrain.cli detect --project "<root>"
```

This reports the OS, the venv and whether the package is importable in it, and
every agent client it can find — each with the **evidence** that identified it
(a binary on PATH, a config file, an env var). Read it rather than assuming.

Key fields:

- `runtime.venv_present` / `package_installed_in_venv` — fix these first; a
  missing venv makes everything downstream fail confusingly.
- `primary` and `primary_reason` — the client to configure, and why.
- `active_signal` on each client: `agent` means we are demonstrably running as
  that agent; `host` means it is only the editor hosting the terminal. Running
  Claude Code inside a VS Code terminal makes **both** look active — the
  `agent` signal is the one that identifies the driver.
- `hooks_available_for` vs `manual_capture_only` — this determines Step 5.
- `recommended_steps` — a plain list of what to do next.

If `primary` is `null` with several clients present, **ask the user which one
they want** rather than guessing. Offer `--client all` if they use more than
one.

## Step 2 — Diagnose the existing setup

```
"$PY" -m obsidian_secondbrain.cli doctor --project "<root>"
```

Read `problems[]`, and pause on two findings before changing anything:

- **`vault.has_obsidian_dir` is true** — a real Obsidian vault is already
  there. Reuse it; never reshape someone's vault without asking.
- **`mcp_server.vault_it_points_at` differs from `vault.path`** — an earlier
  (often global) install points elsewhere. Say so and confirm before
  repointing; the user may be running one shared vault deliberately.

## Step 3 — Create or reuse the vault

```
"$PY" -m obsidian_secondbrain.cli init --project "<root>" [--vault NAME]
```

Idempotent. `action` says `created vault` or `reused existing vault`; existing
folders and notes are never touched.

## Step 4 — Register, in the client's own format

```
"$PY" -m obsidian_secondbrain.cli install --project "<root>" --client <key>
```

`--client` defaults to `auto` (uses the detected `primary`). Use `all` to cover
every client found. The formats genuinely differ, and a config in the wrong
shape is **silently ignored** — no error anywhere — which is why this is not
something to write by hand:

| `--client` | File | Shape |
|---|---|---|
| `claude` | `.mcp.json` + `.claude/settings.local.json` | `mcpServers`, plus hooks |
| `codex` | `~/.codex/config.toml` (`--scope project` → `.codex/config.toml`) | TOML `[mcp_servers.NAME]` |
| `vscode` | `.vscode/mcp.json` | `servers` (**not** `mcpServers`), needs `"type": "stdio"` |
| `copilot` | workspace `.mcp.json` | `mcpServers` — the Agent Host does not read `.vscode/mcp.json` |
| `cursor` | `.cursor/mcp.json` | `mcpServers` |

For any client in `manual_capture_only`, add `--with-instructions`. That writes
`AGENTS.md` (Codex), `.github/copilot-instructions.md` (Copilot/VS Code) or
`.cursor/rules/secbrain.md` — telling that agent to call `capture_session`
itself, since nothing will do it automatically. The block is marked and
replaced on re-run, so it never stacks.

Existing entries, comments and unrelated servers are preserved everywhere, and
every file is backed up before rewriting.

## Step 5 — Verify, do not assume

**If the client supports hooks (`claude`):**

```
"$PY" -m obsidian_secondbrain.cli test-hook --project "<root>"
```

It writes a synthetic transcript, then runs **the command exactly as registered
in settings**, in an environment with `OBSIDIAN_VAULT` deliberately stripped —
what a real session gives a hook. Seven checks: a hook is registered at all, it
exits 0, it wrote exactly one inbox note, the note carries the transcript text,
it is marked `distilled: false`, tool-call noise was excluded, and the daily log
got a pointer. It then restores the vault byte-for-byte.

Running the *registered* command under a *stripped* environment is the whole
point. A hook does **not** inherit the `env` block of an MCP server config, so a
hook registered without `--vault` finds no vault, exits 0 and silently writes
nothing. A test that synthesises its own command, or injects `OBSIDIAN_VAULT`,
will happily pass a hook that does nothing in practice.

| Failing check | Likely cause |
|---|---|
| hook is registered in settings | `install` never ran, or wrote to another scope |
| hook exits 0 | wrong interpreter, or package not installed in that venv |
| wrote exactly one inbox note | registered command missing `--vault` (old install) — re-run `install` |
| note carries transcript text | transcript format changed — check `read_transcript` |
| daily log got a pointer | log folder renamed in `config.json` |

**If the client does not support hooks** (`codex`, `vscode`, `copilot`,
`cursor`): do **not** run `test-hook` — there is nothing to test, and saying
"capture is working" would be false. Instead state plainly that automatic
capture is unavailable on that client, confirm `--with-instructions` wrote the
guidance file, and tell the user that sessions are recorded only when the agent
calls `capture_session` (or the `compact_to_vault` prompt where supported).

## Step 6 — Confirm and hand off

Re-run `doctor` and confirm `ok: true`. Then report concretely:

- the OS, and which client was detected — **with the evidence**, not just the name
- which vault is in use, created or reused
- which files were written, in which format
- the hook result as `N/7`, or an explicit "no automatic capture on this client"
- **that the client must be restarted** before the MCP tools appear

The tools are named `mcp__obsidian-secondbrain__*` and will not exist in the
current session, because MCP servers connect at startup. Do not claim they are
available, and do not call them to "verify" — Step 5 is the verification.

## Afterwards

The workflow: `log_entry` to capture, `capture_session` (or `/compact_to_vault`)
to compact, `distill_queue` → `create_concept_note` → `mark_distilled` to
distil, `vault_health` to see what needs attention. The folder taxonomy is data
in `<vault>/.secondbrain/config.json` — reshape it there, not in code.
