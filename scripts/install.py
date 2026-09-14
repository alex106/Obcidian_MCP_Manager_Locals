#!/usr/bin/env python3
"""Register the server and its capture hook with Claude Code.

Two things are written, into two *different* files, because Claude Code reads
them from two different places:

  * ~/.claude.json          -> mcpServers.obsidian-secondbrain, the stdio
                               server, with OBSIDIAN_VAULT
  * ~/.claude/settings.json -> hooks.PreCompact / hooks.SessionEnd, the raw
                               session capture hook

Do not collapse these into one file. `settings.json` has no `mcpServers` key:
a server written there is an unknown key, dropped with no error anywhere, while
the hooks beside it keep working -- so the install looks fine and the tools
never appear. Earlier versions of this script did exactly that; it also strips
such a block if it finds one.

Everything else in both files is preserved, and a timestamped backup is written
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
    # --vault, not the env var: a hook does not inherit the MCP server's `env`,
    # so an env-only hook silently does nothing in a real session.
    hook_cmd = f'"{py}" "{PROJECT / "hooks" / "capture_session.py"}" --vault "{vault}"'
    entry = [{"hooks": [{"type": "command", "command": hook_cmd, "timeout": 15}]}]
    return server, {"PreCompact": entry, "SessionEnd": entry}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True, help="absolute path to the Obsidian vault")
    ap.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"),
                    help="where the capture hooks go")
    ap.add_argument("--config", default=str(Path.home() / ".claude.json"),
                    help="where the MCP server goes -- NOT the settings file")
    ap.add_argument("--no-hooks", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    vault = Path(args.vault).expanduser()
    if not vault.is_dir():
        print(f"vault does not exist: {vault}", file=sys.stderr)
        return 1

    server, hooks = build(str(vault).replace("\\", "/"), not args.no_hooks)

    def load(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def save(path: Path, data: dict) -> None:
        if path.exists():
            backup = path.with_name(f"{path.name}.bak-{_dt.datetime.now():%Y%m%d-%H%M%S}")
            shutil.copy2(path, backup)
            print(f"backup: {backup}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        print(f"updated: {path}")

    # --- the server, in the file Claude Code reads servers from ---
    config_path = Path(args.config)
    config = load(config_path)
    # A fresh ~/.claude.json carries `"mcpServers": []` -- a list, so indexing
    # into whatever is already there would raise on a real machine.
    if not isinstance(config.get("mcpServers"), dict):
        config["mcpServers"] = {}
    config["mcpServers"][SERVER_KEY] = server

    # --- the hooks, in the settings file ---
    settings_path = Path(args.settings)
    settings = load(settings_path)
    stale = settings.pop("mcpServers", None) is not None
    for event, entry in hooks.items():
        bucket = settings.setdefault("hooks", {}).setdefault(event, [])
        # replace any previous install of this hook, keep everything else
        marker = "capture_session.py"
        kept = [h for h in bucket
                if marker not in json.dumps(h)]
        settings["hooks"][event] = kept + entry

    if args.dry_run:
        print(f"--- {config_path} ---")
        print(json.dumps(config, indent=2, ensure_ascii=False))
        print(f"--- {settings_path} ---")
        print(json.dumps(settings, indent=2, ensure_ascii=False))
        return 0

    save(config_path, config)
    if hooks or stale:
        save(settings_path, settings)
    print(f"  {config_path.name}: mcpServers.{SERVER_KEY} -> "
          f"{server['command']} -m obsidian_secondbrain")
    if stale:
        print(f"  removed an inert mcpServers block from {settings_path} "
              "(Claude Code does not read servers from there)")
    if hooks:
        print(f"  {settings_path.name}: hooks -> {', '.join(hooks)}")
    print("Restart Claude Code, then run /mcp to confirm the server is connected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
