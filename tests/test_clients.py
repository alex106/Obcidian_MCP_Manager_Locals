"""Each client gets a genuinely different config shape -- pin all of them.

The failure this guards against is silent: a config written in the wrong shape
(or valid JSON in the wrong root key) is simply ignored by the client, with no
error anywhere.
"""
import json, shutil, subprocess, sys, tempfile, tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = REPO / ".venv/Scripts/python.exe"
if not PY.exists():
    PY = REPO / ".venv/bin/python"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))


def cli(*a):
    p = subprocess.run([str(PY), "-m", "obsidian_secondbrain.cli", *a],
                       capture_output=True, text=True, cwd=str(REPO))
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"_stdout": p.stdout[-400:], "_stderr": p.stderr[-400:]}


proj = Path(tempfile.mkdtemp(prefix="clients-"))
try:
    cli("init", "--project", str(proj))

    # ---------------------------------------------------------- Codex TOML --
    print("\n[Codex] TOML, hand-edited file must survive")
    codex = proj / ".codex" / "config.toml"
    codex.parent.mkdir(parents=True, exist_ok=True)
    codex.write_text(
        '# my settings\nmodel = "gpt-5"\n\n'
        '[mcp_servers.context7]\ncommand = "npx"\nargs = ["-y", "@upstash/context7-mcp"]\n',
        encoding="utf-8")

    cli("install", "--project", str(proj), "--client", "codex", "--scope", "project")
    text = codex.read_text(encoding="utf-8")
    check("comment preserved", "# my settings" in text)
    data = tomllib.loads(text)
    check("file still parses as TOML", True)
    check("unrelated key preserved", data.get("model") == "gpt-5", data.get("model"))
    check("other server preserved", "context7" in data["mcp_servers"])
    ours = data["mcp_servers"].get("obsidian-secondbrain", {})
    check("our entry under [mcp_servers]", bool(ours))
    check("args correct", ours.get("args") == ["-m", "obsidian_secondbrain"], ours.get("args"))
    check("vault in env", "OBSIDIAN_VAULT" in ours.get("env", {}), ours.get("env"))

    # rerun: must replace, not append a second table
    cli("install", "--project", str(proj), "--client", "codex", "--scope", "project")
    text2 = codex.read_text(encoding="utf-8")
    check("rerun does not duplicate the table",
          text2.count("[mcp_servers.obsidian-secondbrain]") == 1,
          text2.count("[mcp_servers.obsidian-secondbrain]"))
    check("rerun keeps it parseable", bool(tomllib.loads(text2)["mcp_servers"]["context7"]))

    # ------------------------------------------------------------- VS Code --
    print("\n[VS Code] root key is 'servers', needs explicit type")
    cli("install", "--project", str(proj), "--client", "vscode")
    v = json.loads((proj / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
    check("root key is 'servers' not 'mcpServers'",
          "servers" in v and "mcpServers" not in v, sorted(v))
    check("declares type stdio", v["servers"]["obsidian-secondbrain"]["type"] == "stdio")

    # -------------------------------------------------- Copilot host/Cursor --
    print("\n[Copilot / Cursor] root key is 'mcpServers'")
    cli("install", "--project", str(proj), "--client", "copilot")
    c = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))
    check("copilot writes workspace .mcp.json with mcpServers",
          "obsidian-secondbrain" in c.get("mcpServers", {}), sorted(c))

    cli("install", "--project", str(proj), "--client", "cursor")
    cu = json.loads((proj / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    check("cursor writes .cursor/mcp.json with mcpServers",
          "obsidian-secondbrain" in cu.get("mcpServers", {}), sorted(cu))

    # ---------------------------------------------------------- instructions --
    print("\n[Instructions] for clients with no session hooks")
    agents = proj / "AGENTS.md"
    agents.write_text("# My project\n\nExisting guidance.\n", encoding="utf-8")
    cli("install", "--project", str(proj), "--client", "codex",
        "--scope", "project", "--with-instructions")
    a = agents.read_text(encoding="utf-8")
    check("existing AGENTS.md content kept", "Existing guidance." in a)
    check("capture_session instruction added", "capture_session" in a)
    # Normalise: the source wraps this sentence across lines.
    flat = " ".join(a.split())
    check("says automatic capture is unavailable", "no automatic session hook" in flat)

    cli("install", "--project", str(proj), "--client", "codex",
        "--scope", "project", "--with-instructions")
    a2 = agents.read_text(encoding="utf-8")
    check("rerun replaces the block, does not stack",
          a2.count("<!-- secbrain:start -->") == 1, a2.count("<!-- secbrain:start -->"))

    # ----------------------------------------------------------- no hooks ---
    print("\n[Honesty] non-Claude installs must flag the missing hook")
    r = cli("install", "--project", str(proj), "--client", "vscode")
    check("caveat returned to the caller",
          any("no session hooks" in c for c in r.get("caveats", [])), r.get("caveats"))
    check("no hooks were registered for a hookless client",
          "hooks" not in r or not r.get("hooks"), r.get("hooks"))
finally:
    shutil.rmtree(proj, ignore_errors=True)

print(f"\n{'='*60}\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", *FAIL, sep="\n  - ")
sys.exit(1 if FAIL else 0)
