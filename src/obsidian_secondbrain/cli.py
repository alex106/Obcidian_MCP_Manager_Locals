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
SERVER_KEY = "obsidian-secondbrain"
HOOK_EVENTS = ("PreCompact", "SessionEnd")
DEFAULT_VAULT_DIRNAME = "SecondBrain"


# ------------------------------------------------------------------ utils --

def out(payload: dict, ok: bool = True) -> int:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
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
    if not HOOK_SCRIPT.exists():
        return out({"ok": False, "error": f"hook script missing: {HOOK_SCRIPT}"}, False)

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
                _clients.write_instructions(target)["files"])
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
    hook_cmd = f'"{py}" "{HOOK_SCRIPT}" --vault "{vault_s}"'
    hook_entry = [{"hooks": [{"type": "command", "command": hook_cmd, "timeout": 15}]}]

    if args.scope == "project":
        # .mcp.json is the project-scoped MCP config Claude Code reads.
        mcp_path = project / ".mcp.json"
        mcp = read_json(mcp_path)
        before = json.dumps(mcp.get("mcpServers", {}).get(SERVER_KEY))
        mcp.setdefault("mcpServers", {})[SERVER_KEY] = server
        if before != json.dumps(server):
            write_json(mcp_path, mcp)
            changed.append(str(mcp_path))
        # Hooks carry absolute machine-specific paths, so they belong in the
        # personal, gitignored settings file rather than the shared one.
        settings_path = project / ".claude" / "settings.local.json"
    else:
        settings_path = Path.home() / ".claude" / "settings.json"

    settings = read_json(settings_path)
    if args.scope == "user":
        settings.setdefault("mcpServers", {})[SERVER_KEY] = server

    if not args.no_hooks:
        for event in HOOK_EVENTS:
            bucket = settings.setdefault("hooks", {}).setdefault(event, [])
            kept = [h for h in bucket if "capture_session.py" not in json.dumps(h)]
            settings["hooks"][event] = kept + hook_entry

    write_json(settings_path, settings)
    changed.append(str(settings_path))

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


def registered_hook_command(project: Path) -> str | None:
    """The capture hook command as actually registered, project scope first."""
    for path in (project / ".claude" / "settings.local.json",
                 project / ".claude" / "settings.json",
                 Path.home() / ".claude" / "settings.json"):
        settings = read_json(path)
        for event in HOOK_EVENTS:
            for entry in settings.get("hooks", {}).get(event, []):
                for h in entry.get("hooks", []):
                    if "capture_session.py" in h.get("command", ""):
                        return h["command"]
    return None


def cmd_test_hook(args) -> int:
    """Actually fire the hook and verify it wrote a note, then clean up."""
    project = project_dir(args.project)
    vault = vault_dir(project, args.vault)
    if not vault.is_dir():
        return out({"ok": False, "error": f"vault does not exist: {vault}. Run init first."}, False)
    if not HOOK_SCRIPT.exists():
        return out({"ok": False, "error": f"hook script missing: {HOOK_SCRIPT}"}, False)

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
    registered = registered_hook_command(project)
    record("hook is registered in settings", registered is not None,
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
    user_settings = Path.home() / ".claude" / "settings.json"

    def hooks_in(path: Path) -> list[str]:
        s = read_json(path)
        return [e for e in HOOK_EVENTS
                if "capture_session.py" in json.dumps(s.get("hooks", {}).get(e, []))]

    def server_in(path: Path) -> dict | None:
        return read_json(path).get("mcpServers", {}).get(SERVER_KEY)

    registered = server_in(mcp_path) or server_in(user_settings)
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
            "registered_user": bool(server_in(user_settings)),
            "vault_it_points_at": (registered or {}).get("env", {}).get("OBSIDIAN_VAULT"),
        },
        "hooks": {
            "script_exists": HOOK_SCRIPT.exists(),
            "project_scope": hooks_in(proj_settings),
            "user_scope": hooks_in(user_settings),
        },
        "python": python_exe(),
    }

    problems = []
    if not report["vault"]["exists"]:
        problems.append("vault missing -- run: init")
    if not (report["mcp_server"]["registered_project"] or report["mcp_server"]["registered_user"]):
        problems.append("MCP server not registered -- run: install")
    if not (report["hooks"]["project_scope"] or report["hooks"]["user_scope"]):
        problems.append("capture hooks not registered -- run: install")
    pointed = report["mcp_server"]["vault_it_points_at"]
    if pointed and Path(pointed).resolve() != vault:
        problems.append(f"registered server points at a different vault: {pointed}")

    report["ok"] = not problems
    report["problems"] = problems
    return out(report, not problems)


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
    p.add_argument("--with-instructions", action="store_true",
                   help="also write AGENTS.md / copilot-instructions.md for "
                        "clients that have no session hooks")
    common(sub.add_parser("test-hook", help="fire the capture hook and verify it"))
    common(sub.add_parser("doctor", help="report setup status"))

    args = ap.parse_args(argv)
    return {
        "init": cmd_init, "install": cmd_install, "detect": cmd_detect,
        "test-hook": cmd_test_hook, "doctor": cmd_doctor,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
