#!/usr/bin/env python3
"""Register the server and its capture hook with Claude Code.

Writes two things into ~/.claude/settings.json:
  * mcpServers.obsidian-secondbrain  -> the stdio server, with OBSIDIAN_VAULT
  * hooks.PreCompact / hooks.SessionEnd -> the raw session capture hook

Everything else in the file is preserved, and a timestamped backup is written
first. Run with --dry-run to see the JSON without touching anything.

Usage:
    python scripts/install.py --vault "C:/Users/you/Documents/SecondBrain"
    python scripts/install.py --vault ... --no-hooks     # MCP server only
    python scripts/install.py --vault ... --dry-run
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SERVER_KEY = "obsidian-secondbrain"


def python_exe() -> str:
    """The venv interpreter if there is one, else the one running this."""
    for candidate in (PROJECT / ".venv/Scripts/python.exe", PROJECT / ".venv/bin/python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def build(vault: str, hooks: bool) -> tuple[dict, dict]:
    py = python_exe()
    server = {
        "command": py,
        "args": ["-m", "obsidian_secondbrain"],
        "env": {"OBSIDIAN_VAULT": vault},
    }
    if not hooks:
        return server, {}
    hook_cmd = f'"{py}" "{PROJECT / "hooks" / "capture_session.py"}"'
    entry = [{"hooks": [{"type": "command", "command": hook_cmd, "timeout": 15}]}]
    return server, {"PreCompact": entry, "SessionEnd": entry}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True, help="absolute path to the Obsidian vault")
    ap.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))
    ap.add_argument("--no-hooks", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    vault = Path(args.vault).expanduser()
    if not vault.is_dir():
        print(f"vault does not exist: {vault}", file=sys.stderr)
        return 1

    server, hooks = build(str(vault).replace("\\", "/"), not args.no_hooks)

    path = Path(args.settings)
    settings = {}
    if path.exists():
        settings = json.loads(path.read_text(encoding="utf-8"))

    settings.setdefault("mcpServers", {})[SERVER_KEY] = server
    for event, entry in hooks.items():
        bucket = settings.setdefault("hooks", {}).setdefault(event, [])
        # replace any previous install of this hook, keep everything else
        marker = "capture_session.py"
        kept = [h for h in bucket
                if marker not in json.dumps(h)]
        settings["hooks"][event] = kept + entry

    rendered = json.dumps(settings, indent=2, ensure_ascii=False)
    if args.dry_run:
        print(rendered)
        return 0

    if path.exists():
        backup = path.with_suffix(f".json.bak-{_dt.datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(path, backup)
        print(f"backup: {backup}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered + "\n", encoding="utf-8")
    print(f"updated: {path}")
    print(f"  mcpServers.{SERVER_KEY} -> {server['command']} -m obsidian_secondbrain")
    if hooks:
        print(f"  hooks: {', '.join(hooks)}")
    print("Restart Claude Code, then run /mcp to confirm the server is connected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
