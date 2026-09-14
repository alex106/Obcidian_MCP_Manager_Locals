"""Work out what the user is actually running, before advising them.

Detection is evidence-based: every finding names the file or binary it came
from, so the caller can show its reasoning instead of asserting. Nothing here
writes anything.

Two different questions get answered separately, because they have different
answers and conflating them gives bad advice:

  installed   -- the client exists on this machine
  configured  -- this MCP server is already registered with it
  active      -- we appear to be running inside it right now
"""

from __future__ import annotations

import os
import platform as _platform
import shutil
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

HOME = Path.home()


# --------------------------------------------------------------- feature ---

@dataclass
class Capabilities:
    """What a client can actually do with this package."""
    mcp_tools: bool = True          # all 27 tools
    mcp_prompts: bool = False       # compact_to_vault / distill / review_brain
    server_instructions: bool = False
    session_hooks: bool = False     # automatic capture at compact/session end
    skills: bool = False            # /secbrain-init


@dataclass
class Client:
    key: str
    name: str
    installed: bool = False
    configured: bool = False
    active: bool = False
    # "agent" = we are demonstrably running as this agent; "host" = this is
    # merely the window we are displayed in. An editor hosting another agent's
    # terminal looks active but is not the thing driving the session.
    active_signal: str | None = None
    evidence: list[str] = field(default_factory=list)
    config_target: str | None = None
    capabilities: Capabilities = field(default_factory=Capabilities)
    caveats: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.configured:
            return "configured"
        return "installed" if self.installed else "absent"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status
        return d


# ------------------------------------------------------------------ util ---

def _which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def _env_any(*names: str) -> str | None:
    for n in names:
        if os.environ.get(n):
            return n
    return None


def vscode_user_dir() -> Path:
    system = _platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", HOME / "AppData/Roaming"))
        return base / "Code" / "User"
    if system == "Darwin":
        return HOME / "Library" / "Application Support" / "Code" / "User"
    return HOME / ".config" / "Code" / "User"


# --------------------------------------------------------------- clients ---

def detect_claude_code(project: Path) -> Client:
    c = Client("claude", "Claude Code",
               capabilities=Capabilities(mcp_prompts=True, server_instructions=True,
                                         session_hooks=True, skills=True))
    binary = _which("claude")
    if binary:
        c.installed = True
        c.evidence.append(f"binary on PATH: {binary}")
    for p in (HOME / ".claude", HOME / ".claude" / "settings.json"):
        if p.exists():
            c.installed = True
            c.evidence.append(f"exists: {p}")
    for p in (project / ".mcp.json", HOME / ".claude" / "settings.json"):
        if p.exists() and "obsidian-secondbrain" in p.read_text(encoding="utf-8", errors="ignore"):
            c.configured = True
            c.evidence.append(f"server registered in: {p}")
    if _env_any("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        c.active = True
        c.active_signal = "agent"
        c.evidence.append("running inside a Claude Code session")
    c.config_target = str(project / ".mcp.json")
    return c


def detect_codex(project: Path) -> Client:
    c = Client("codex", "OpenAI Codex CLI",
               capabilities=Capabilities(mcp_prompts=False))
    binary = _which("codex")
    if binary:
        c.installed = True
        c.evidence.append(f"binary on PATH: {binary}")
    user_cfg = HOME / ".codex" / "config.toml"
    for p in (HOME / ".codex", user_cfg):
        if p.exists():
            c.installed = True
            c.evidence.append(f"exists: {p}")
    for p in (project / ".codex" / "config.toml", user_cfg):
        if p.exists() and "obsidian-secondbrain" in p.read_text(encoding="utf-8", errors="ignore"):
            c.configured = True
            c.evidence.append(f"server registered in: {p}")
    if _env_any("CODEX_SANDBOX", "CODEX_HOME"):
        c.active = True
        c.active_signal = "agent"
        c.evidence.append("running inside a Codex session")
    c.config_target = str(user_cfg)
    c.caveats = [
        "No session hooks: Codex has no PreCompact/SessionEnd equivalent, so "
        "automatic capture is unavailable. Call capture_session explicitly.",
        "MCP prompt support varies by version; drive the workflow from AGENTS.md "
        "instead of relying on /distill.",
    ]
    return c


def detect_vscode(project: Path) -> Client:
    c = Client("vscode", "VS Code + GitHub Copilot",
               capabilities=Capabilities(mcp_prompts=True))
    binary = _which("code", "code-insiders")
    if binary:
        c.installed = True
        c.evidence.append(f"binary on PATH: {binary}")
    user_dir = vscode_user_dir()
    if user_dir.exists():
        c.installed = True
        c.evidence.append(f"exists: {user_dir}")
    ext = HOME / ".vscode" / "extensions"
    if ext.is_dir():
        copilot = sorted(ext.glob("github.copilot-chat-*"))
        if copilot:
            c.evidence.append(f"Copilot Chat extension: {copilot[-1].name}")
    for p in (project / ".vscode" / "mcp.json", user_dir / "mcp.json"):
        if p.exists() and "obsidian-secondbrain" in p.read_text(encoding="utf-8", errors="ignore"):
            c.configured = True
            c.evidence.append(f"server registered in: {p}")
    if os.environ.get("TERM_PROGRAM") == "vscode" or _env_any("VSCODE_PID", "VSCODE_CWD"):
        c.active = True
        c.active_signal = "host"
        c.evidence.append("running inside a VS Code terminal -- this identifies "
                          "the editor hosting the terminal, not necessarily the "
                          "agent driving the session")
    c.config_target = str(project / ".vscode" / "mcp.json")
    c.caveats = [
        "No session hooks: automatic capture is unavailable. Call "
        "capture_session explicitly, or use the /mcp.obsidian-secondbrain."
        "compact_to_vault prompt.",
        "MCP tools only appear in Agent mode, not Ask or Edit mode.",
    ]
    return c


def detect_copilot_host(project: Path) -> Client:
    c = Client("copilot", "Copilot Agent Host / portable MCP",
               capabilities=Capabilities(mcp_prompts=True))
    user_cfg = HOME / ".copilot" / "mcp-config.json"
    for p in (HOME / ".copilot", user_cfg):
        if p.exists():
            c.installed = True
            c.evidence.append(f"exists: {p}")
    for p in (project / ".mcp.json", user_cfg):
        if p.exists() and "obsidian-secondbrain" in p.read_text(encoding="utf-8", errors="ignore"):
            c.configured = True
            c.evidence.append(f"server registered in: {p}")
    c.config_target = str(user_cfg)
    c.caveats = [
        "The Agent Host does not read .vscode/mcp.json; it reads a workspace "
        ".mcp.json or ~/.copilot/mcp-config.json.",
        "No session hooks: automatic capture is unavailable.",
    ]
    return c


def detect_cursor(project: Path) -> Client:
    c = Client("cursor", "Cursor", capabilities=Capabilities(mcp_prompts=True))
    binary = _which("cursor")
    if binary:
        c.installed = True
        c.evidence.append(f"binary on PATH: {binary}")
    if (HOME / ".cursor").exists():
        c.installed = True
        c.evidence.append(f"exists: {HOME / '.cursor'}")
    for p in (project / ".cursor" / "mcp.json", HOME / ".cursor" / "mcp.json"):
        if p.exists() and "obsidian-secondbrain" in p.read_text(encoding="utf-8", errors="ignore"):
            c.configured = True
            c.evidence.append(f"server registered in: {p}")
    c.config_target = str(project / ".cursor" / "mcp.json")
    c.caveats = ["No session hooks: automatic capture is unavailable."]
    return c


DETECTORS = (detect_claude_code, detect_codex, detect_vscode,
             detect_copilot_host, detect_cursor)


# ---------------------------------------------------------------- runtime ---

def runtime_report(repo_root: Path) -> dict:
    venv_win = repo_root / ".venv/Scripts/python.exe"
    venv_nix = repo_root / ".venv/bin/python"
    venv = venv_win if venv_win.exists() else (venv_nix if venv_nix.exists() else None)
    return {
        "os": _platform.system(),
        "os_release": _platform.release(),
        "arch": _platform.machine(),
        "python_running": sys.version.split()[0],
        "venv_present": venv is not None,
        "venv_python": str(venv) if venv else None,
        "package_installed_in_venv": _package_ok(venv),
        "repo_root": str(repo_root),
    }


def _package_ok(venv: Path | None) -> bool | None:
    if venv is None:
        return None
    import subprocess
    try:
        p = subprocess.run([str(venv), "-c", "import obsidian_secondbrain"],
                           capture_output=True, timeout=30)
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def detect_all(project: Path, repo_root: Path) -> dict:
    clients = [d(project) for d in DETECTORS]
    active = [c for c in clients if c.active]
    present = [c for c in clients if c.installed or c.configured]
    # An agent-level signal beats a host-level one: running Claude Code in a
    # VS Code terminal makes both look active, but only one is the agent.
    agents = [c for c in active if c.active_signal == "agent"]

    if len(agents) == 1:
        primary, why = agents[0].key, "we are running as this agent"
    elif agents:
        primary = agents[0].key
        why = ("several agent-level signals present: "
               + ", ".join(c.key for c in agents))
    elif len(active) == 1:
        primary, why = active[0].key, "the only client that looks active"
    elif len(present) == 1:
        primary, why = present[0].key, "the only client found on this machine"
    elif len(present) > 1:
        primary = None
        why = ("several clients are present and none identified itself as the "
               "running agent -- ask the user which one they want, or use "
               "--client all")
    else:
        primary, why = None, "no client could be identified with confidence"

    return {
        "project": str(project),
        "runtime": runtime_report(repo_root),
        "clients": [c.to_dict() for c in clients],
        "present": [c.key for c in present],
        "active": [c.key for c in active],
        "primary": primary,
        "primary_reason": why,
        "hooks_available_for": [c.key for c in present if c.capabilities.session_hooks],
        "manual_capture_only": [c.key for c in present if not c.capabilities.session_hooks],
    }
