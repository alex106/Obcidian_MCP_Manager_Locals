"""Each client gets a genuinely different config shape -- pin all of them.

The failure this guards against is silent: a config written in the wrong shape
(or valid JSON in the wrong root key) is simply ignored by the client, with no
error anywhere.
"""
import json, os, shutil, subprocess, sys, tempfile, tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = REPO / ".venv/Scripts/python.exe"
if not PY.exists():
    PY = REPO / ".venv/bin/python"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))


def cli(*a, home=None):
    # `home` redirects Path.home() at the user scope, so a --scope user install
    # is testable without writing into the developer's real config. Windows
    # expanduser reads USERPROFILE, POSIX reads HOME -- set both.
    env = None if home is None else {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    # encoding='utf-8' matters: text=True alone decodes with the locale
    # codec (cp1252 on Windows), which mangles non-ASCII paths into mojibake
    # that still parses as JSON -- a silently wrong read, not an error.
    p = subprocess.run([str(PY), "-m", "obsidian_secondbrain.cli", *a],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=str(REPO), env=env)
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

    # ------------------------------------------------- context-first rule ---
    # A hook writes the vault when a session ends; only this rule makes an
    # agent read it when one starts. Claude Code gets it without any flag,
    # because it is the client that needs no other instruction file.
    print("\n[Rule] every new session must be told to search the vault first")
    claude_md = proj / "CLAUDE.md"
    claude_md.write_text("# My project\n\nExisting guidance.\n", encoding="utf-8")
    r = cli("install", "--project", str(proj), "--client", "claude",
            "--scope", "project")
    c = claude_md.read_text(encoding="utf-8")
    flat_c = " ".join(c.split())
    check("CLAUDE.md written without --with-instructions",
          r.get("project_rules", "").endswith("CLAUDE.md"), r.get("project_rules"))
    check("existing CLAUDE.md content kept", "Existing guidance." in c)
    check("carries the session-start rule",
          "Start every session by reading the vault" in c)
    check("tells the agent to search before acting",
          "search the vault" in flat_c and "before* you open a file" in flat_c)
    check("rule comes before the capture guidance",
          c.index("Start every session") < c.index("Second brain"))
    check("does not claim capture is manual on Claude",
          "no automatic session hook" not in flat_c)

    cli("install", "--project", str(proj), "--client", "claude", "--scope", "project")
    c2 = claude_md.read_text(encoding="utf-8")
    check("rerun replaces the rule block, does not stack",
          c2.count("<!-- secbrain:start -->") == 1, c2.count("<!-- secbrain:start -->"))

    d = cli("doctor", "--project", str(proj))
    check("doctor sees the rule", d.get("project_rules", {}).get("has_session_start_rule"))
    claude_md.write_text("# My project\n", encoding="utf-8")
    d = cli("doctor", "--project", str(proj))
    check("doctor flags a project whose sessions would start blind",
          any("context-first rule" in p for p in d.get("problems", [])), d.get("problems"))

    check("hookless clients get the rule too",
          "Start every session by reading the vault" in
          (proj / ".cursor" / "rules" / "secbrain.md").read_text(encoding="utf-8")
          if cli("install", "--project", str(proj), "--client", "cursor",
                 "--with-instructions") else False)

    r = cli("install", "--project", str(proj), "--client", "claude",
            "--scope", "project", "--no-instructions")
    check("--no-instructions leaves the rule file alone",
          r.get("project_rules") is None and
          "secbrain:start" not in claude_md.read_text(encoding="utf-8"))

    # -------------------------------------------------- Claude user scope ---
    # The two user-scope files are not interchangeable: Claude Code reads MCP
    # servers from ~/.claude.json and hooks from ~/.claude/settings.json.
    # settings.json has no mcpServers key, so a server written there is dropped
    # in silence -- which is exactly how this branch shipped broken: every
    # other test ran --scope project, so nothing ever exercised it.
    print("\n[Claude user scope] server -> ~/.claude.json, hooks -> settings.json")
    home = Path(tempfile.mkdtemp(prefix="home-"))
    (home / ".claude").mkdir(parents=True)
    # A fresh ~/.claude.json carries `"mcpServers": []` -- a list, not a
    # mapping, so setdefault-then-index would raise TypeError on a real machine.
    (home / ".claude.json").write_text(
        '{"mcpServers": [], "numStartups": 3}\n', encoding="utf-8")
    (home / ".claude" / "settings.json").write_text(json.dumps({
        "model": "Opus",
        "mcpServers": {"obsidian-secondbrain": {"command": "stale-and-inert"}},
    }), encoding="utf-8")

    r = cli("install", "--project", str(proj), "--client", "claude",
            "--scope", "user", home=home)
    uc = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    us = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    check("server lands in ~/.claude.json",
          "obsidian-secondbrain" in (uc.get("mcpServers") or {}), sorted(uc))
    check("an empty-list mcpServers is replaced, not crashed on",
          isinstance(uc.get("mcpServers"), dict), type(uc.get("mcpServers")).__name__)
    check("unrelated ~/.claude.json keys preserved", uc.get("numStartups") == 3)
    check("no mcpServers left in settings.json, where it is ignored",
          "mcpServers" not in us, sorted(us))
    check("the stale block's removal is reported to the caller",
          any("ignored mcpServers" in c for c in r.get("caveats", [])), r.get("caveats"))
    check("hooks still go to settings.json",
          sorted(us.get("hooks", {})) == ["PreCompact", "SessionEnd"],
          sorted(us.get("hooks", {})))
    check("unrelated settings.json keys preserved", us.get("model") == "Opus")

    d = cli("doctor", "--project", str(proj), home=home)
    check("doctor sees the user-scope server",
          d.get("mcp_server", {}).get("registered_user") is True, d.get("mcp_server"))

    # The fault doctor used to be blind to, because it read back the same wrong
    # file install had written.
    us["mcpServers"] = {"obsidian-secondbrain": {"command": "stale-and-inert"}}
    (home / ".claude" / "settings.json").write_text(json.dumps(us), encoding="utf-8")
    d = cli("doctor", "--project", str(proj), home=home)
    check("doctor flags a server stranded in settings.json",
          any("does not read" in p for p in d.get("problems", [])), d.get("problems"))
    shutil.rmtree(home, ignore_errors=True)

    # ----------------------------------------------------------- no hooks ---
    print("\n[Honesty] non-Claude installs must flag the missing hook")
    r = cli("install", "--project", str(proj), "--client", "vscode")
    check("caveat returned to the caller",
          any("no session hooks" in c for c in r.get("caveats", [])), r.get("caveats"))
    check("no hooks were registered for a hookless client",
          "hooks" not in r or not r.get("hooks"), r.get("hooks"))
    # ------------------------------------------------------ non-ASCII paths --
    print("\n[Unicode] a project path outside cp1252 must not break the CLI")
    uni = Path(tempfile.mkdtemp(prefix="clients-")) / "פינוי בינוי — café"
    uni.mkdir(parents=True)
    try:
        r = cli("init", "--project", str(uni))
        check("init works under a Hebrew/accented path", r.get("ok") is True,
              r.get("_stderr", r)),
        r = cli("detect", "--project", str(uni))
        check("detect works under a Hebrew/accented path", r.get("ok") is True,
              r.get("_stderr", r))
        check("the path survives the round trip",
              "פינוי" in r.get("project", ""), r.get("project"))
    finally:
        shutil.rmtree(uni.parent, ignore_errors=True)
finally:
    shutil.rmtree(proj, ignore_errors=True)

print(f"\n{'='*60}\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", *FAIL, sep="\n  - ")
sys.exit(1 if FAIL else 0)
