"""Codex hook path: install writes hooks.json, test-hook proves it end to end.

Runs with HOME/USERPROFILE redirected to a temp dir, so nothing touches the
developer's real ~/.codex. Uses a vault named MyBrain so the cwd/SecondBrain
fallback cannot rescue a registration that forgot --vault.
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


home = Path(tempfile.mkdtemp(prefix="codex-home-"))
proj = Path(tempfile.mkdtemp(prefix="codex-proj-"))
ENV = {k: v for k, v in os.environ.items() if k != "OBSIDIAN_VAULT"}
ENV.update(HOME=str(home), USERPROFILE=str(home))


def cli(*a):
    p = subprocess.run([str(PY), "-m", "obsidian_secondbrain.cli", *a],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=str(REPO), env=ENV)
    try:
        return json.loads(p.stdout), p.returncode
    except json.JSONDecodeError:
        return {"_stdout": p.stdout[-400:], "_stderr": p.stderr[-400:]}, p.returncode


try:
    cli("init", "--project", str(proj), "--vault", "MyBrain")

    print("\n-- before install: test-hook must fail, not pass vacuously")
    res, rc = cli("test-hook", "--client", "codex", "--project", str(proj), "--vault", "MyBrain")
    check("test-hook fails with nothing registered", res.get("ok") is False and rc == 1,
          json.dumps(res)[:300])

    print("\n-- install --client codex --scope project")
    res, rc = cli("install", "--client", "codex", "--scope", "project",
                  "--project", str(proj), "--vault", "MyBrain")
    check("install ok", res.get("ok") is True and rc == 0, json.dumps(res)[:400])

    hooks = json.loads((proj / ".codex" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    for ev in ("SessionStart", "UserPromptSubmit", "Stop", "PreCompact", "SessionEnd"):
        check(f"hooks.json has {ev}", ev in hooks and hooks[ev])
    se = hooks["SessionEnd"][0]["hooks"][0]
    check("SessionEnd timeout within Codex's 3 s ceiling", se["timeout"] <= 3, str(se))
    check("every command carries --vault",
          all("--vault" in h["command"] for grp in hooks.values() for g in grp for h in g["hooks"]))

    cfg = tomllib.loads((proj / ".codex" / "config.toml").read_text(encoding="utf-8"))
    check("server table in project config.toml", "obsidian-secondbrain" in cfg.get("mcp_servers", {}))
    check("no inline [hooks] in config.toml (one representation per layer)", "hooks" not in cfg)

    agents = (proj / "AGENTS.md").read_text(encoding="utf-8")
    check("AGENTS.md written by default, with the context-first rule",
          "Start every session by reading the vault" in agents)
    check("AGENTS.md no longer claims there are no hooks",
          "no automatic session hook" not in agents)
    check("install warns about /hooks trust",
          any("/hooks" in c for c in res.get("caveats", [])), str(res.get("caveats")))

    print("\n-- re-install is idempotent and keeps foreign hooks")
    data = json.loads((proj / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    data["hooks"]["PreToolUse"] = [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}]
    data["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "echo also-mine"}]})
    (proj / ".codex" / "hooks.json").write_text(json.dumps(data), encoding="utf-8")
    cli("install", "--client", "codex", "--scope", "project", "--project", str(proj), "--vault", "MyBrain")
    hooks = json.loads((proj / ".codex" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    check("foreign PreToolUse hook kept", "echo mine" in json.dumps(hooks.get("PreToolUse")))
    check("foreign Stop hook kept", "echo also-mine" in json.dumps(hooks["Stop"]))
    ours = [g for g in hooks["Stop"] if "codex_turn_buffer" in json.dumps(g)]
    check("our Stop hook not duplicated", len(ours) == 1, f"{len(ours)} copies")
    check("AGENTS.md block not stacked",
          (proj / "AGENTS.md").read_text(encoding="utf-8").count("secbrain:start") == 1)

    print("\n-- test-hook --client codex (registered commands, OBSIDIAN_VAULT stripped)")
    vault = proj / "MyBrain"
    snapshot = sorted(str(p.relative_to(vault)) for p in vault.rglob("*"))
    res, rc = cli("test-hook", "--client", "codex", "--project", str(proj), "--vault", "MyBrain")
    for c in res.get("checks", []):
        if not c["ok"]:
            print("     failed:", c["check"], "--", c["detail"][:160])
    check("test-hook passes", res.get("ok") is True and rc == 0,
          f"{res.get('passed')}/{res.get('total')}")
    check("test-hook admits it cannot see /hooks trust", "trust" in res.get("not_verifiable_here", ""))
    check("vault left exactly as found",
          sorted(str(p.relative_to(vault)) for p in vault.rglob("*")) == snapshot)

    print("\n-- PreCompact then SessionEnd: no turn filed twice, none lost")
    sys.path.insert(0, str(REPO / "hooks"))
    from _common import buffer_path
    cmd = {e: h["command"] for e, groups in hooks.items() for g in groups
           for h in g["hooks"] if "--vault" in h["command"]}

    def fire(ev, **kw):
        payload = {"session_id": "s-compact", "cwd": str(proj), "hook_event_name": ev, **kw}
        return subprocess.run(cmd[ev], shell=True, input=json.dumps(payload), text=True,
                              encoding="utf-8", capture_output=True, env=ENV)

    inbox = vault / "00-Inbox"
    before = set(inbox.glob("*.md")) if inbox.exists() else set()
    fire("UserPromptSubmit", prompt="same first line")
    fire("Stop", last_assistant_message="ALPHA reply")
    fire("PreCompact", trigger="auto")
    fire("UserPromptSubmit", prompt="same first line")
    fire("Stop", last_assistant_message="BETA reply")
    fire("SessionEnd", reason="other")
    new = sorted(set(inbox.glob("*.md")) - before)
    bodies = [p.read_text(encoding="utf-8") for p in new]
    check("two notes, even with the same minute and first line", len(new) == 2,
          str([p.name for p in new]))
    alpha = [b for b in bodies if "ALPHA" in b]
    beta = [b for b in bodies if "BETA" in b]
    check("each turn filed exactly once, in separate notes",
          len(alpha) == 1 and len(beta) == 1 and alpha[0] is not beta[0])
    check("PreCompact note triggered by PreCompact, the other by SessionEnd",
          len(alpha) == 1 and "trigger: PreCompact" in alpha[0]
          and len(beta) == 1 and "trigger: SessionEnd" in beta[0])
    check("buffer gone after SessionEnd", not buffer_path(vault, "s-compact").exists())

    print("\n-- doctor --client codex")
    res, rc = cli("doctor", "--client", "codex", "--project", str(proj), "--vault", "MyBrain")
    check("doctor ok", res.get("ok") is True, str(res.get("problems")))
    check("doctor does not claim trust", res["hooks"]["trusted"].startswith("unknown"))

    print("\n-- claude path untouched: detect reports codex as hook-capable")
    res, _ = cli("detect", "--project", str(proj))
    codex = next((c for c in res.get("clients", []) if c["key"] == "codex"), {})
    check("codex capabilities.session_hooks is true",
          codex.get("capabilities", {}).get("session_hooks") is True)
finally:
    shutil.rmtree(home, ignore_errors=True)
    shutil.rmtree(proj, ignore_errors=True)

print("\n" + "=" * 60)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
