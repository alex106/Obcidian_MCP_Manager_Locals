---
name: secbrain-init
description: Set up the Obsidian second-brain MCP server for the current project - detect the OS and which agent client is in use (Claude Code, Codex, Copilot/VS Code, Cursor), create or reuse a local vault, register the server in that client's own config format, wire the capture hooks where the client supports them, write the project rule that makes every new session search the vault before acting, then fire the hook and verify it actually wrote a note. Use when the user runs /secbrain-init, asks to set up / initialise / repair the second brain or its vault, asks whether it works with Codex or Copilot, or asks why session capture is not writing notes.
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

1. `~/Documents/obsidian-mcp` — the current user's home, never a hard-coded
   username (`$env:USERPROFILE\Documents\obsidian-mcp` on Windows,
   `$HOME/Documents/obsidian-mcp` elsewhere)
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
- `hooks_available_for` vs `manual_capture_only` — this determines Step 6.
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

`doctor` also reports `project_rules.has_session_start_rule`. False means the
project has a wired-up vault that no session will ever read on its own — treat
it as a real finding, not cosmetics, and fix it by re-running `install`.

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
| `claude` (`--scope project`) | `.mcp.json` + `.claude/settings.local.json` | `mcpServers`, plus hooks |
| `claude` (`--scope user`) | `~/.claude.json` + `~/.claude/settings.json` | `mcpServers` in the **first** file, hooks in the second |
| `codex` | `~/.codex/config.toml` + `~/.codex/hooks.json` (`--scope project` → `.codex/…`) | TOML `[mcp_servers.NAME]`, plus hooks in `hooks.json` |
| `vscode` | `.vscode/mcp.json` | `servers` (**not** `mcpServers`), needs `"type": "stdio"` |
| `copilot` | workspace `.mcp.json` | `mcpServers` — the Agent Host does not read `.vscode/mcp.json` |
| `cursor` | `.cursor/mcp.json` | `mcpServers` |

Codex gets five hooks — `SessionStart` (digest), `UserPromptSubmit` and `Stop`
(buffer each turn from the event payload), `PreCompact` and `SessionEnd` (file
the buffer into the inbox) — and `AGENTS.md` by default, the way Claude Code
gets `CLAUDE.md`. Capture deliberately does **not** parse the Codex transcript:
Codex documents that format as unstable.

For any client in `manual_capture_only`, add `--with-instructions`. That writes
`.github/copilot-instructions.md` (Copilot/VS Code) or
`.cursor/rules/secbrain.md` — telling that agent to call `capture_session`
itself, since nothing will do it automatically. The block is marked and
replaced on re-run, so it never stacks.

Existing entries, comments and unrelated servers are preserved everywhere, and
every file is backed up before rewriting.

**The two Claude scopes use two different files, and `settings.json` is not one
of them for servers.** `~/.claude/settings.json` holds hooks, permissions, model
and plugins; it has no `mcpServers` key, so a server written there is an unknown
key that Claude Code drops without an error anywhere — the hooks in the same
file keep working, which makes the install look successful. User-scoped servers
live in `~/.claude.json`. `install` now writes each to its own file and strips
any inert `mcpServers` block an older install left in `settings.json`, reporting
that in `caveats`. If you are choosing the scope for the user: `project` keeps
the vault with the project and is the default; `user` makes it available in
every project.

## Step 5 — The context-first rule lands in the project

`install` also writes the marked `<!-- secbrain:start -->` block into the
project's rule file: `CLAUDE.md` for Claude Code — written every time, no flag
needed — and `AGENTS.md`, `.github/copilot-instructions.md` or
`.cursor/rules/secbrain.md` for the clients that took `--with-instructions` in
Step 4. The block leads with the rule that has to fire first:

> **Before acting on the user's first request in a session, search the vault**
> — `search_notes` on the terms of the request, `find_notes` on the obvious
> titles, `vault_info` for the taxonomy — and read the results before opening a
> file, running a command, or answering.

This is not redundant with the hooks. For Claude Code, `install` also wires a
`SessionStart` hook that injects a passive digest (undistilled backlog count,
recent log tail) into every new session unconditionally — but that hook fires
before the user's request exists, so it cannot search anything. The rule block
is the *active* half: a targeted `search_notes`/`find_notes` once the request
is known. Every other client still needs the rule for the reason it always
did: a capture hook only *writes* the vault when a session **ends**; without
the rule nothing makes the agent *read* it when a session **begins**, and the
second brain only ever accumulates.

The block is marked, so a re-run replaces it instead of stacking, and the rest
of the file is untouched — a `.bak-<timestamp>` is written first either way.
Check `project_rules` in the `install` output for the path it landed in.

Pass `--no-instructions` only when the user keeps project rules somewhere the
tool should not touch. Then say so plainly: the rule is not installed, and no
session will read the vault on its own until they paste it in themselves.

## Step 6 — Verify, do not assume

**First, verify the registration landed in a file the client reads.** Re-run
`doctor` and check three fields before anything else:

- `mcp_server.registered_project` / `registered_user` — at least one must be
  true for the scope you installed. Both false means the server will not
  connect, whatever else passed.
- `mcp_server.stranded_in_settings_json` — true means a server is sitting in
  `~/.claude/settings.json`, where it is inert. Re-run `install`.

This check exists because the rest of Step 6 cannot catch a bad registration.
`test-hook` verifies the *hooks*, and they live in different files from the
server, so it passes 11/11 with the server misfiled. Never report the setup as
working on the strength of the hook result alone.

**Then, if the client supports hooks (`claude`):**

```
"$PY" -m obsidian_secondbrain.cli test-hook --project "<root>"
```

It writes a synthetic transcript, then runs **the commands exactly as
registered in settings**, in an environment with `OBSIDIAN_VAULT` deliberately
stripped — what a real session gives a hook. Eleven checks across the two
hooks:

- **Capture (`SessionEnd`, 7 checks)**: the hook is registered, exits 0, wrote
  exactly one inbox note, the note carries the transcript text, it is marked
  `distilled: false`, tool-call noise was excluded, and the daily log got a
  pointer. It then restores the vault byte-for-byte.
- **Injection (`SessionStart`, 4 checks)**: the hook is registered, exits 0,
  emits `hookSpecificOutput.additionalContext` on stdout, and that digest
  mentions the undistilled count. No vault side-effect is expected here — a
  digest is read-only.

Running the *registered* commands under a *stripped* environment is the whole
point. A hook does **not** inherit the `env` block of an MCP server config, so a
hook registered without `--vault` finds no vault, exits 0 and (for the capture
hook) silently writes nothing, or (for the injection hook) silently emits no
digest. A test that synthesises its own command, or injects `OBSIDIAN_VAULT`,
will happily pass a hook that does nothing in practice.

| Failing check | Likely cause |
|---|---|
| capture hook is registered in settings | `install` never ran, or wrote to another scope |
| SessionStart hook is registered in settings | same, or `install` ran before this feature existed — re-run `install` |
| hook exits 0 | wrong interpreter, or package not installed in that venv |
| wrote exactly one inbox note | registered command missing `--vault` (old install) — re-run `install` |
| note carries transcript text | transcript format changed — check `read_transcript` |
| daily log got a pointer | log folder renamed in `config.json` |
| SessionStart hook emits additionalContext | registered command missing `--vault`, or `obsidian_secondbrain` not importable from that interpreter |

**If the client is `codex`:**

```
"$PY" -m obsidian_secondbrain.cli test-hook --client codex --project "<root>"
"$PY" -m obsidian_secondbrain.cli doctor    --client codex --project "<root>"
```

Same principle — the registered commands, `OBSIDIAN_VAULT` stripped — driven
by Codex's events: `UserPromptSubmit` + `Stop` must fill the turn buffer, and
`SessionEnd` with no transcript must turn it into one inbox note **inside 3
seconds** (Codex's hard ceiling for that event). Then the digest checks.

What neither command can see: **whether Codex trusts the hooks.** Codex skips
every non-managed hook until it is reviewed and trusted in `/hooks`, and the
trust is pinned to the hook's hash — so a re-install that changes a command
needs re-trusting. Never report Codex capture as working without telling the
user to open `/hooks` and trust the five `obsidian-secondbrain` entries.

**If the client does not support hooks** (`vscode`, `copilot`, `cursor`): do **not** run `test-hook` — there is nothing to test, and saying
"capture is working" would be false. Instead state plainly that automatic
capture is unavailable on that client, confirm `--with-instructions` wrote the
guidance file, and tell the user that sessions are recorded only when the agent
calls `capture_session` (or the `compact_to_vault` prompt where supported).

## Step 7 — Confirm and hand off

Re-run `doctor` and confirm `ok: true`. Then report concretely:

- the OS, and which client was detected — **with the evidence**, not just the name
- which vault is in use, created or reused
- which files were written, in which format
- **which file the server itself was registered in**, and that `doctor` reads it
  back as registered — name the path, so a wrong one is visible at a glance
- the hook result as `N/11`, or an explicit "no automatic capture on this client"
- which rule file carries the context-first block, and that it takes effect
  on the **next** session, not this one
- **that the client must be restarted** before the MCP tools appear

The tools are named `mcp__obsidian-secondbrain__*` and will not exist in the
current session, because MCP servers connect at startup. Do not claim they are
available, and do not call them to "verify" — Step 6 is the verification.

## Afterwards

Every session now opens with a vault search — that is the installed rule, not
a habit to remember. The rest of the workflow: `log_entry` to capture, `capture_session` (or `/compact_to_vault`)
to compact, `distill_queue` → `create_concept_note` → `mark_distilled` to
distil, `vault_health` to see what needs attention. The folder taxonomy is data
in `<vault>/.secondbrain/config.json` — reshape it there, not in code.
