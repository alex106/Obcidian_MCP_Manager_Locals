"""Bootstrap CLI -- everything the `secbrain-init` skill needs, as tested code.

The skill drives these subcommands rather than re-deriving the steps in prose,
so setup behaves the same every time and can be verified.

    python -m obsidian_secondbrain.cli doctor    --project DIR
    python -m obsidian_secondbrain.cli init      --project DIR [--vault NAME]
    python -m obsidian_secondbrain.cli install   --project DIR [--scope project|user]
    python -m obsidian_secondbrain.cli test-hook --project DIR

Every subcommand prints a single JSON object on stdout and exits 0 on success,
1 on failure, so the caller parses rather than scrapes.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import clients as _clients
from .config import DEFAULT_CONFIG, load_config
from .environment import detect_all

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent          # the obsidian-mcp checkout
HOOK_SCRIPT = PROJECT_ROOT / "hooks" / "capture_session.py"
SESSION_START_SCRIPT = PROJECT_ROOT / "hooks" / "session_start.py"
SERVER_KEY = "obsidian-secondbrain"
# Each event's own script -- SessionStart injects a digest, the other two
# capture. Every place that used to assume "one script for every event" is
# keyed off this map instead, so a broken or missing SessionStart entry is
# just as visible to doctor/test-hook as a broken capture hook.
HOOK_SCRIPTS: dict[str, Path] = {
    "PreCompact": HOOK_SCRIPT,
    "SessionEnd": HOOK_SCRIPT,
    "SessionStart": SESSION_START_SCRIPT,
}
HOOK_EVENTS = tuple(HOOK_SCRIPTS)

# Claude Code reads user-scoped MCP servers from ~/.claude.json, and hooks from
# ~/.claude/settings.json. They are different files with different schemas:
# `settings.json` has no `mcpServers` key, so a server written there is dropped
# without an error anywhere -- the client simply starts with no such server.
CLAUDE_USER_CONFIG = Path.home() / ".claude.json"
CLAUDE_USER_SETTINGS = Path.home() / ".claude" / "settings.json"
DEFAULT_VAULT_DIRNAME = "SecondBrain"


# ------------------------------------------------------------------ utils --

def out(payload: dict, ok: bool = True) -> int:
    """Emit JSON as UTF-8 bytes, not through the console's default codec.

    On Windows a piped stdout defaults to cp1252, so a project or vault path
    containing Hebrew, Cyrillic, CJK or accented characters would raise
    UnicodeEncodeError and take down every subcommand.
    """
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        buf.write(text.encode("utf-8") + b"\n")
        buf.flush()
    else:  # a wrapped stream in tests
        print(text)
    return 0 if ok else 1


def python_exe() -> str:
    for c in (PROJECT_ROOT / ".venv/Scripts/python.exe", PROJECT_ROOT / ".venv/bin/python"):
        if c.exists():
            return str(c)
    return sys.executable


def project_dir(raw: str | None) -> Path:
    return Path(raw or os.getcwd()).expanduser().resolve()


def vault_dir(project: Path, name: str | None) -> Path:
    """Resolve the vault path: absolute stays put, bare name sits in project."""
    n = name or DEFAULT_VAULT_DIRNAME
    p = Path(n).expanduser()
    return p.resolve() if p.is_absolute() else (project / n).resolve()


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(f"{path.suffix}.bak-{_dt.datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(path, backup)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def is_vault(path: Path) -> bool:
    """A directory counts as an existing vault if Obsidian or we marked it."""
    return path.is_dir() and (
        (path / ".obsidian").is_dir() or (path / ".secondbrain" / "config.json").exists()
    )


# ------------------------------------------------------------------- init --

def cmd_init(args) -> int:
    project = project_dir(args.project)
    vault = vault_dir(project, args.vault)

    pre_existing = is_vault(vault)
    had_obsidian = (vault / ".obsidian").is_dir()
    vault.mkdir(parents=True, exist_ok=True)

    os.environ["OBSIDIAN_VAULT"] = str(vault)
    cfg = load_config(str(vault))
    created = cfg.ensure_folders()
    wrote_config = not cfg.config_path.exists()
    if wrote_config:
        cfg.save()

    readme = vault / "README.md"
    wrote_readme = not readme.exists()
    if wrote_readme:
        from .server import _FOLDER_DOC, _README
        readme.write_text(
            _README.format(
                root=vault,
                folders="\n".join(f"- `{v}/` - {_FOLDER_DOC.get(k, k)}"
                                  for k, v in cfg.folders.items()),
            ),
            encoding="utf-8",
        )

    return out({
        "ok": True,
        "action": "reused existing vault" if pre_existing else "created vault",
        "pre_existing": pre_existing,
        "opened_by_obsidian_before": had_obsidian,
        "vault": str(vault),
        "folders_created": created,
        "folders": cfg.folders,
        "config_written": wrote_config,
        "readme_written": wrote_readme,
        "note_count": sum(1 for _ in vault.rglob("*.md")),
    })


# ----------------------------------------------------------------- detect --

def cmd_detect(args) -> int:
    """Identify the environment and say what to do about it."""
    project = project_dir(args.project)
    report = detect_all(project, PROJECT_ROOT)

    steps: list[str] = []
    rt = report["runtime"]
    if not rt["venv_present"]:
        steps.append(f"create the venv: python -m venv {PROJECT_ROOT / '.venv'}")
    elif rt["package_installed_in_venv"] is False:
        steps.append(f"install the package: \"{python_exe()}\" -m pip install -e {PROJECT_ROOT}")

    primary = report["primary"]
    if primary is None:
        steps.append("no client identified -- pass --client explicitly "
                     f"(one of: {', '.join(_clients.WRITERS)})")
    else:
        found = next(c for c in report["clients"] if c["key"] == primary)
        if not found["configured"]:
            steps.append(f"register the server: install --client {primary}")
        if not found["capabilities"]["session_hooks"]:
            steps.append("this client has no session hooks -- automatic capture is "
                         "unavailable; use --with-instructions so the agent knows "
                         "to call capture_session itself")

    others = [c for c in report["clients"]
              if c["key"] != primary and (c["installed"] or c["configured"])]
    if others:
        steps.append("other clients are present too: "
                     + ", ".join(f"{c['name']} ({c['status']})" for c in others)
                     + " -- install --client all covers them")

    report["recommended_steps"] = steps
    report["ok"] = True
    return out(report)


# ---------------------------------------------------------------- install --

def resolve_clients(args, project: Path) -> tuple[list[str], str]:
    """Which clients to configure, and why."""
    if args.client == "all":
        report = detect_all(project, PROJECT_ROOT)
        present = report["present"]
        return (present or ["claude"],
                "all clients found on this machine" if present
                else "no client detected; defaulted to Claude Code")
    if args.client != "auto":
        return [args.client], "named explicitly with --client"

    report = detect_all(project, PROJECT_ROOT)
    if report["primary"]:
        return [report["primary"]], report["primary_reason"]
    return ["claude"], "no client detected; defaulted to Claude Code"


def cmd_install(args) -> int:
    project = project_dir(args.project)
    vault = vault_dir(project, args.vault)
    if not vault.is_dir():
        return out({"ok": False, "error": f"vault does not exist: {vault}. Run init first."}, False)
    missing_scripts = [str(s) for s in set(HOOK_SCRIPTS.values()) if not s.exists()]
    if missing_scripts:
        return out({"ok": False, "error": f"hook script(s) missing: {missing_scripts}"}, False)

    py = python_exe()
    vault_s = str(vault).replace("\\", "/")
    targets, why = resolve_clients(args, project)

    results: dict[str, dict] = {}
    notes: list[str] = []

    for key in targets:
        if key == "claude":
            continue  # handled below, it is the only one with hooks
        if key == "codex":
            path = (project / ".codex" / "config.toml" if args.scope == "project"
                    else Path.home() / ".codex" / "config.toml")
            results[key] = _clients.write_codex(path, py, vault_s)
        else:
            writer = _clients.WRITERS.get(key)
            if writer is None:
                results[key] = {"error": f"no writer for client {key!r}"}
                continue
            results[key] = writer(project, py, vault_s)

        if args.with_instructions and key in _clients.INSTRUCTION_FILE:
            target = _clients.INSTRUCTION_FILE[key](project)
            results[key].setdefault("files", []).extend(
                _clients.write_instructions(target, key)["files"])
        notes.append(f"{key}: no session hooks -- capture must be called by the agent")

    if "claude" not in targets:
        return out({
            "ok": True,
            "clients": targets,
            "why": why,
            "vault": str(vault),
            "python": py,
            "results": results,
            "caveats": notes,
            "next_step": "Restart the client, then confirm the server is connected.",
        })

    changed = []

    server = {"command": py, "args": ["-m", "obsidian_secondbrain"],
              "env": {"OBSIDIAN_VAULT": vault_s}}
    # The vault must be passed on the command line: a hook does NOT inherit the
    # MCP server's `env` block, so relying on OBSIDIAN_VAULT here would make the
    # hook a silent no-op.
    def hook_entry_for(script: Path) -> list[dict]:
        cmd = f'"{py}" "{script}" --vault "{vault_s}"'
        return [{"hooks": [{"type": "command", "command": cmd, "timeout": 15}]}]

    if args.scope == "project":
        # .mcp.json is the project-scoped MCP config Claude Code reads.
        mcp_path = project / ".mcp.json"
        # Hooks carry absolute machine-specific paths, so they belong in the
        # personal, gitignored settings file rather than the shared one.
        settings_path = project / ".claude" / "settings.local.json"
    else:
        # NOT settings.json -- see CLAUDE_USER_CONFIG above.
        mcp_path = CLAUDE_USER_CONFIG
        settings_path = CLAUDE_USER_SETTINGS

    mcp = read_json(mcp_path)
    # Claude Code writes `"mcpServers": []` into a fresh ~/.claude.json, so the
    # existing value is not necessarily the mapping setdefault would assume.
    if not isinstance(mcp.get("mcpServers"), dict):
        mcp["mcpServers"] = {}
    before = json.dumps(mcp["mcpServers"].get(SERVER_KEY))
    mcp["mcpServers"][SERVER_KEY] = server
    if before != json.dumps(server):
        write_json(mcp_path, mcp)
        changed.append(str(mcp_path))

    settings = read_json(settings_path)
    # Installs before this fix put the server in settings.json, where it was
    # silently ignored. Leaving it there would keep doctor reporting a server
    # that never connects.
    if settings.pop("mcpServers", None) is not None:
        notes.append(f"removed an ignored mcpServers block from {settings_path} "
                     f"(the server belongs in {mcp_path})")

    if not args.no_hooks:
        for event, script in HOOK_SCRIPTS.items():
            bucket = settings.setdefault("hooks", {}).setdefault(event, [])
            kept = [h for h in bucket if script.name not in json.dumps(h)]
            settings["hooks"][event] = kept + hook_entry_for(script)

    write_json(settings_path, settings)
    changed.append(str(settings_path))

    # Claude Code has hooks, but a hook only writes the vault at the end of a
    # session. The context-first rule has to reach the model itself, and
    # CLAUDE.md is the only channel that is read on every new session.
    if not args.no_instructions:
        rules = _clients.write_instructions(
            _clients.INSTRUCTION_FILE["claude"](project), "claude")
        changed.extend(rules["files"])

    return out({
        "ok": True,
        "clients": targets,
        "why": why,
        "scope": args.scope,
        "vault": str(vault),
        "python": py,
        "files_written": changed,
        "results": results,
        "mcp_server": SERVER_KEY,
        "hooks": [] if args.no_hooks else list(HOOK_EVENTS),
        "project_rules": None if args.no_instructions else str(
            _clients.INSTRUCTION_FILE["claude"](project)),
        "caveats": notes,
        "next_step": "Restart Claude Code, then /mcp to confirm the server connects.",
    })


# -------------------------------------------------------------- test-hook --

SYNTH_TRANSCRIPT = [
    {"message": {"role": "user", "content": "secbrain-init hook self-test"}},
    {"message": {"role": "assistant", "content": [
        {"type": "text", "text": "Self-test marker: SECBRAIN_HOOK_SELFTEST_OK"},
        {"type": "tool_use", "name": "Write"}]}},
    {"message": {"role": "user", "content": [{"type": "tool_result", "content": "ignored"}]}},
]


def registered_hook_command(project: Path, event: str) -> str | None:
    """The command actually registered for one hook event, project scope first."""
    script_name = HOOK_SCRIPTS[event].name
    for path in (project / ".claude" / "settings.local.json",
                 project / ".claude" / "settings.json",
                 Path.home() / ".claude" / "settings.json"):
        settings = read_json(path)
        for entry in settings.get("hooks", {}).get(event, []):
            for h in entry.get("hooks", []):
                if script_name in h.get("command", ""):
                    return h["command"]
    return None


def cmd_test_hook(args) -> int:
    """Actually fire the hook and verify it wrote a note, then clean up."""
    project = project_dir(args.project)
    vault = vault_dir(project, args.vault)
    if not vault.is_dir():
        return out({"ok": False, "error": f"vault does not exist: {vault}. Run init first."}, False)
    missing_scripts = [str(s) for s in set(HOOK_SCRIPTS.values()) if not s.exists()]
    if missing_scripts:
        return out({"ok": False, "error": f"hook script(s) missing: {missing_scripts}"}, False)

    inbox = vault / DEFAULT_CONFIG["folders"]["inbox"]
    cfg_file = vault / ".secondbrain" / "config.json"
    if cfg_file.exists():
        folders = read_json(cfg_file).get("folders", {})
        inbox = vault / folders.get("inbox", DEFAULT_CONFIG["folders"]["inbox"])

    before = set(inbox.glob("*.md")) if inbox.exists() else set()

    # The hook also appends a pointer to today's log. Snapshot it so the
    # self-test leaves the vault byte-identical to how it found it.
    log_dir = vault / (read_json(cfg_file).get("folders", {}).get("log", "10-Log")
                       if cfg_file.exists() else "10-Log")
    today_log = log_dir / f"{_dt.date.today():%Y-%m-%d}.md"
    log_before = today_log.read_bytes() if today_log.exists() else None

    tmp = Path(tempfile.mkdtemp(prefix="secbrain-hooktest-"))
    transcript = tmp / "transcript.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(r) for r in SYNTH_TRANSCRIPT), encoding="utf-8")

    payload = {
        "session_id": "secbrain-selftest",
        "transcript_path": str(transcript),
        "cwd": str(project),
        "hook_event_name": "SessionEnd",
    }

    checks: list[dict] = []

    def record(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # Run the command exactly as it is REGISTERED, in an environment with
    # OBSIDIAN_VAULT stripped -- which is what a real Claude Code session gives
    # a hook. Synthesising a command here, or injecting the env var, would let a
    # hook that is a no-op in practice pass this test.
    registered = registered_hook_command(project, "SessionEnd")
    record("capture hook is registered in settings", registered is not None,
           "no capture_session.py hook found in project or user settings")
    env = {k: v for k, v in os.environ.items() if k != "OBSIDIAN_VAULT"}
    env["CLAUDE_PROJECT_DIR"] = str(project)

    try:
        cmd = registered or f'"{python_exe()}" "{HOOK_SCRIPT}" --vault "{vault}"'
        proc = subprocess.run(
            cmd, shell=True,
            input=json.dumps(payload), text=True, capture_output=True, timeout=30,
            env=env,
        )
        record("hook exits 0", proc.returncode == 0,
               f"rc={proc.returncode} stderr={proc.stderr.strip()[:300]}")

        after = set(inbox.glob("*.md")) if inbox.exists() else set()
        new = sorted(after - before)
        record("hook wrote exactly one inbox note", len(new) == 1,
               f"new={[p.name for p in new]}")

        body = new[0].read_text(encoding="utf-8") if new else ""
        record("note carries the transcript text",
               "SECBRAIN_HOOK_SELFTEST_OK" in body)
        record("note marked undistilled", "distilled: false" in body)
        record("tool_use noise excluded", '"type": "tool_use"' not in body
               and "ignored" not in body)
        record("daily log got a pointer",
               today_log.exists() and "Raw capture" in today_log.read_text(encoding="utf-8"))
    finally:
        # Restore the vault exactly: drop the notes this test created and put
        # the daily log back the way it was (or remove it if we created it).
        after = set(inbox.glob("*.md")) if inbox.exists() else set()
        for p in after - before:
            p.unlink(missing_ok=True)
        if log_before is None:
            today_log.unlink(missing_ok=True)
        else:
            today_log.write_bytes(log_before)
        shutil.rmtree(tmp, ignore_errors=True)

    # --- SessionStart: injection, not capture. No transcript, no vault
    # side-effect expected -- it must only print a hookSpecificOutput digest.
    start_registered = registered_hook_command(project, "SessionStart")
    record("SessionStart hook is registered in settings", start_registered is not None,
           "no session_start.py hook found in project or user settings")

    start_payload = {
        "session_id": "secbrain-selftest",
        "cwd": str(project),
        "hook_event_name": "SessionStart",
        "source": "startup",
    }
    start_cmd = start_registered or f'"{python_exe()}" "{SESSION_START_SCRIPT}" --vault "{vault}"'
    start_proc = subprocess.run(
        start_cmd, shell=True,
        input=json.dumps(start_payload), text=True, capture_output=True, timeout=30,
        env=env,
    )
    record("SessionStart hook exits 0", start_proc.returncode == 0,
           f"rc={start_proc.returncode} stderr={start_proc.stderr.strip()[:300]}")

    start_out = {}
    try:
        start_out = json.loads(start_proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        pass
    digest = start_out.get("hookSpecificOutput", {}).get("additionalContext", "")
    record("SessionStart hook emits hookSpecificOutput.additionalContext",
           bool(digest), f"stdout={start_proc.stdout.strip()[:200]!r}")
    record("SessionStart digest mentions the vault's undistilled count",
           "undistilled" in digest.lower())

    passed = [c for c in checks if c["ok"]]
    ok = len(passed) == len(checks)
    return out({
        "ok": ok,
        "passed": len(passed),
        "total": len(checks),
        "checks": checks,
        "cleaned_up": True,
        "summary": "hook is wired and working" if ok else "hook is NOT working correctly",
    }, ok)


# ----------------------------------------------------------------- doctor --

def cmd_doctor(args) -> int:
    project = project_dir(args.project)
    vault = vault_dir(project, args.vault)

    mcp_path = project / ".mcp.json"
    proj_settings = project / ".claude" / "settings.local.json"
    user_settings = CLAUDE_USER_SETTINGS

    def hooks_in(path: Path) -> list[str]:
        s = read_json(path)
        return [e for e, script in HOOK_SCRIPTS.items()
                if script.name in json.dumps(s.get("hooks", {}).get(e, []))]

    def server_in(path: Path) -> dict | None:
        servers = read_json(path).get("mcpServers")
        return servers.get(SERVER_KEY) if isinstance(servers, dict) else None

    # Only the two files Claude Code actually reads servers from count as
    # registered. settings.json is checked separately, as a fault: a server
    # there looks installed to a human reading the file, and is inert.
    registered = server_in(mcp_path) or server_in(CLAUDE_USER_CONFIG)
    stranded = server_in(user_settings)

    rules_file = _clients.INSTRUCTION_FILE["claude"](project)
    rules_text = (rules_file.read_text(encoding="utf-8", errors="replace")
                  if rules_file.exists() else "")
    has_rules = _clients.AGENTS_SECTION_START in rules_text

    report = {
        "project": str(project),
        "vault": {
            "path": str(vault),
            "exists": vault.is_dir(),
            "recognised_as_vault": is_vault(vault),
            "has_obsidian_dir": (vault / ".obsidian").is_dir(),
            "note_count": sum(1 for _ in vault.rglob("*.md")) if vault.is_dir() else 0,
        },
        "mcp_server": {
            "registered_project": bool(server_in(mcp_path)),
            "registered_user": bool(server_in(CLAUDE_USER_CONFIG)),
            "stranded_in_settings_json": bool(stranded),
            "vault_it_points_at": (registered or {}).get("env", {}).get("OBSIDIAN_VAULT"),
        },
        "hooks": {
            "script_exists": all(s.exists() for s in set(HOOK_SCRIPTS.values())),
            "project_scope": hooks_in(proj_settings),
            "user_scope": hooks_in(user_settings),
            "missing": sorted(set(HOOK_EVENTS)
                               - set(hooks_in(proj_settings)) - set(hooks_in(user_settings))),
        },
        "project_rules": {
            "path": str(rules_file),
            "exists": rules_file.exists(),
            "has_secbrain_section": has_rules,
            "has_session_start_rule": has_rules and (
                "Start every session by reading the vault" in rules_text),
        },
        "python": python_exe(),
    }

    problems = []
    if not report["vault"]["exists"]:
        problems.append("vault missing -- run: init")
    if not (report["mcp_server"]["registered_project"] or report["mcp_server"]["registered_user"]):
        problems.append("MCP server not registered -- run: install")
    if report["mcp_server"]["stranded_in_settings_json"]:
        problems.append(
            f"an mcpServers block sits in {user_settings}, which Claude Code "
            "does not read -- it is inert; re-run: install")
    if report["hooks"]["missing"]:
        problems.append(
            f"hook event(s) not registered: {', '.join(report['hooks']['missing'])} "
            "-- run: install")
    if not report["project_rules"]["has_session_start_rule"]:
        problems.append(
            "project rule file missing the context-first rule, so a new "
            f"session will not search the vault -- run: install ({rules_file})")
    pointed = report["mcp_server"]["vault_it_points_at"]
    if pointed and Path(pointed).resolve() != vault:
        problems.append(f"registered server points at a different vault: {pointed}")

    report["ok"] = not problems
    report["problems"] = problems
    return out(report, not problems)


# --------------------------------------------------------- schedule-distill --

# Distillation needs a model's judgment (title/claim/links for each raw
# capture) -- the server deliberately never summarises, so this can't be a
# plain cron script. It has to be a real headless agent run, scoped to
# exactly the tools it needs so nothing else this server exposes gets a free
# pass. Windows Task Scheduler is the trigger; `claude -p` is the agent.
DISTILL_TASK_PREFIX = "SecondBrainDistill"
DISTILL_PROMPT_FILE = "secbrain-distill-prompt.md"
DISTILL_WRAPPER_FILE = "run-secbrain-distill.ps1"
DISTILL_LOG_FILE = "secbrain-distill.log"
DISTILL_BRANCH_PREFIX = "secbrain-distill-"
DISTILL_ALLOWED_TOOLS = (
    "mcp__obsidian-secondbrain__vault_info",
    "mcp__obsidian-secondbrain__distill_queue",
    "mcp__obsidian-secondbrain__create_concept_note",
    "mcp__obsidian-secondbrain__mark_distilled",
    "mcp__obsidian-secondbrain__log_entry",
    # The prompt below explicitly invites a quick search_notes/find_notes to
    # link a new note to existing ones -- these have to be in the allow-list
    # too, or that linking silently no-ops via a denied tool call instead of
    # failing loudly. Caught by a live-fire test, not by inspection.
    "mcp__obsidian-secondbrain__search_notes",
    "mcp__obsidian-secondbrain__find_notes",
)

DISTILL_PROMPT = """\
You are running unattended, once a day, with no human present. Do exactly \
this, then stop -- do not ask questions, there is nobody to answer them.

1. Call vault_info to confirm the vault.
2. Call distill_queue with limit 15.
3. For each item returned: read its content, extract the durable ideas, and
   write each one as a single atomic note via create_concept_note -- title
   phrased as a claim, linked to existing related notes with [[wikilinks]]
   where you can find them (a quick search_notes/find_notes is fine, but
   don't spend more than a couple of calls per item on it). Then call
   mark_distilled on that item's path, listing the note(s) you produced.
4. Stop after distilling at most 15 items, even if more remain -- the next
   run will pick up where this one left off.
5. Finish with one log_entry summarising the run: how many items were
   distilled, how many concept notes were created, and how many remain
   pending.

If distill_queue returns zero pending items, just call log_entry saying so
and stop. Do not touch anything outside these five calls.
"""


def _validate_time(value: str) -> str:
    import re as _re
    if not _re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError(f"--time must be HH:MM 24h, got {value!r}")
    return value


def _distill_task_name(project: Path) -> str:
    # One task per project. A short hash keeps the Task Scheduler name ASCII
    # and stable regardless of the project path's script (Hebrew, spaces,
    # length limits), while still being reproducible from the project path
    # alone so re-running schedule-distill finds and replaces the same task.
    import hashlib
    digest = hashlib.sha1(str(project).encode("utf-8")).hexdigest()[:8]
    return f"{DISTILL_TASK_PREFIX}-{digest}"


def cmd_schedule_distill(args) -> int:
    import platform as _platform
    if _platform.system() != "Windows":
        return out({
            "ok": False,
            "error": "schedule-distill only supports Windows Task Scheduler today",
        }, False)

    project = project_dir(args.project)
    task = _distill_task_name(project)

    if args.remove:
        proc = subprocess.run(
            ["schtasks", "/delete", "/tn", task, "/f"],
            capture_output=True, text=True,
        )
        return out({
            "ok": True,
            "task_name": task,
            "removed": proc.returncode == 0,
            "detail": (proc.stdout or proc.stderr).strip(),
        })

    vault = vault_dir(project, args.vault)
    if not vault.is_dir():
        return out({"ok": False, "error": f"vault does not exist: {vault}. Run init first."}, False)

    try:
        time_s = _validate_time(args.time)
    except ValueError as exc:
        return out({"ok": False, "error": str(exc)}, False)

    claude_exe = shutil.which("claude") or shutil.which("claude.exe")
    if not claude_exe:
        return out({"ok": False, "error": "claude executable not found on PATH"}, False)

    claude_dir = project / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    # If the project is (or becomes) a git repo, its own bookkeeping -- an
    # ever-growing log, an absolute machine-specific claude.exe path -- must
    # never be what a daily backup branch commits. Guarantee the exclusion
    # here rather than assume a human wrote it, so a run on a fresh git init
    # can't silently start committing this instead of real project changes.
    gitignore_path = project / ".gitignore"
    gitignore_text = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    if not any(line.strip() in (".claude/", ".claude") for line in gitignore_text.splitlines()):
        with gitignore_path.open("a", encoding="utf-8") as fh:
            if gitignore_text and not gitignore_text.endswith("\n"):
                fh.write("\n")
            fh.write("# Added by schedule-distill: machine-specific automation artifacts.\n.claude/\n")
    prompt_path = claude_dir / DISTILL_PROMPT_FILE
    prompt_path.write_text(DISTILL_PROMPT, encoding="utf-8")

    log_path = claude_dir / DISTILL_LOG_FILE
    wrapper_path = claude_dir / DISTILL_WRAPPER_FILE
    allowed = " ".join(DISTILL_ALLOWED_TOOLS)
    # --allowedTools scopes exactly these five tools to this one invocation --
    # nothing is written into the project's shared settings.local.json, so
    # there's no persistent permission grant to later forget about or drift.
    #
    # The git step after it is best-effort and never fails the run: a backup
    # step that can break the thing it's backing up is worse than no backup.
    # It only touches the project's own repo (never the vault), commits to a
    # dated branch, and pushes only once a remote actually exists -- until
    # then it commits locally and says so.
    #
    # It deliberately never does `git checkout main` first. An earlier
    # version did, to start each day "fresh" -- but any file only ever
    # committed on a previous day's branch (never merged to main) is, by
    # definition, untracked on main, and `checkout` syncs the working tree to
    # match the branch it switches to. That silently DELETED real files
    # (.gitignore, .mcp.json) from disk the moment a second day's run swapped
    # back to main. Caught by testing two consecutive runs, not by review.
    # Instead each day's branch is created from wherever HEAD already is --
    # a plain `checkout -b` (never `-B`, which resets and would reintroduce
    # the same data loss), or a plain `checkout` if today's branch already
    # exists from an earlier run today. Nothing is ever force-reset.
    wrapper = f"""\
# Auto-generated by `obsidian_secondbrain.cli schedule-distill`.
# Safe to regenerate; hand edits are overwritten on the next run of it.
Set-Location -LiteralPath "{project}"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -LiteralPath "{log_path}" -Value "--- run $stamp ---"
Get-Content -LiteralPath "{prompt_path}" -Raw | & "{claude_exe}" -p `
    --permission-mode dontAsk `
    --allowedTools {allowed} `
    --output-format json *>> "{log_path}"

# --- git backup: dated branch, commit, push if a remote is configured -----
git -C "{project}" rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -eq 0) {{
    $branchDate = Get-Date -Format "yyyy-MM-dd"
    $branch = "{DISTILL_BRANCH_PREFIX}$branchDate"
    git -C "{project}" rev-parse --verify --quiet $branch *> $null
    if ($LASTEXITCODE -eq 0) {{
        git -C "{project}" checkout $branch *>> "{log_path}"
    }} else {{
        git -C "{project}" checkout -b $branch *>> "{log_path}"
    }}
    git -C "{project}" add -A *>> "{log_path}"
    git -C "{project}" diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {{
        git -C "{project}" commit -m "Automated distill run $stamp" *>> "{log_path}"
        $remotes = git -C "{project}" remote
        if ($remotes) {{
            git -C "{project}" push -u origin $branch *>> "{log_path}"
            Add-Content -LiteralPath "{log_path}" -Value "git: pushed $branch to origin"
        }} else {{
            Add-Content -LiteralPath "{log_path}" -Value "git: committed to $branch locally (no remote configured yet)"
        }}
    }} else {{
        Add-Content -LiteralPath "{log_path}" -Value "git: nothing to commit"
    }}
}} else {{
    Add-Content -LiteralPath "{log_path}" -Value "git: {project} is not a git repository -- skipped"
}}
"""
    wrapper_path.write_text(wrapper, encoding="utf-8")

    tr = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{wrapper_path}"'
    proc = subprocess.run(
        ["schtasks", "/create", "/tn", task, "/tr", tr,
         "/sc", "daily", "/st", time_s, "/f"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return out({"ok": False, "error": (proc.stderr or proc.stdout).strip()}, False)

    return out({
        "ok": True,
        "task_name": task,
        "time": time_s,
        "project": str(project),
        "vault": str(vault),
        "prompt_file": str(prompt_path),
        "wrapper_script": str(wrapper_path),
        "log_file": str(log_path),
        "allowed_tools": list(DISTILL_ALLOWED_TOOLS),
        "git_backup": {
            "branch_prefix": DISTILL_BRANCH_PREFIX,
            "note": "commits the project repo to today's dated branch (created "
                    "from wherever HEAD already is, never reset) after each "
                    "run; pushes to origin only once a remote is configured, "
                    "otherwise commits locally and says so in the log",
        },
        "next_step": f'Verify with: schtasks /query /tn "{task}" /v /fo list',
    })


# ------------------------------------------------------------------- main --

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="obsidian_secondbrain.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--project", default=None, help="project root (default: cwd)")
        p.add_argument("--vault", default=None,
                       help=f"vault dir name inside the project, or an absolute "
                            f"path (default: {DEFAULT_VAULT_DIRNAME})")

    common(sub.add_parser("init", help="create or reuse the project vault"))
    common(sub.add_parser("detect", help="identify OS, runtime and agent client"))
    p = sub.add_parser("install", help="register the MCP server and capture hooks")
    common(p)
    p.add_argument("--scope", choices=["project", "user"], default="project")
    p.add_argument("--no-hooks", action="store_true")
    p.add_argument("--client", default="auto",
                   choices=["auto", "all", *sorted(_clients.WRITERS)],
                   help="which agent client to configure (default: auto-detect)")
    p.add_argument("--no-instructions", action="store_true",
                   help="do not write the project rule file (CLAUDE.md) that "
                        "tells the agent to search the vault before acting on "
                        "the first request of a session")
    p.add_argument("--with-instructions", action="store_true",
                   help="also write AGENTS.md / copilot-instructions.md for "
                        "clients that have no session hooks")
    common(sub.add_parser("test-hook", help="fire the capture hook and verify it"))
    common(sub.add_parser("doctor", help="report setup status"))

    p = sub.add_parser("schedule-distill",
                        help="register/remove a daily headless distillation run (Windows only)")
    common(p)
    p.add_argument("--time", default="18:00", help="24h HH:MM local time to run daily (default: 18:00)")
    p.add_argument("--remove", action="store_true", help="unregister the scheduled task")

    args = ap.parse_args(argv)
    return {
        "init": cmd_init, "install": cmd_install, "detect": cmd_detect,
        "test-hook": cmd_test_hook, "doctor": cmd_doctor,
        "schedule-distill": cmd_schedule_distill,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
