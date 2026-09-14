"""Prove test-hook catches the env-var bug that the old test masked.

Uses a vault named MyBrain so the cwd/SecondBrain fallback cannot rescue a
registration that has no --vault.
"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(r"C:\Users\posti\Documents\obsidian-mcp")
PY = REPO / ".venv/Scripts/python.exe"
HOOK = REPO / "hooks" / "capture_session.py"


def cli(*a):
    # encoding='utf-8' matters: text=True alone decodes with the locale
    # codec (cp1252 on Windows), which mangles non-ASCII paths into mojibake
    # that still parses as JSON -- a silently wrong read, not an error.
    p = subprocess.run([str(PY), "-m", "obsidian_secondbrain.cli", *a],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(REPO))
    try:
        return json.loads(p.stdout), p.returncode
    except json.JSONDecodeError:
        return {"_stdout": p.stdout, "_stderr": p.stderr}, p.returncode


proj = Path(tempfile.mkdtemp(prefix="regress-"))
try:
    cli("init", "--project", str(proj), "--vault", "MyBrain")

    # --- the OLD registration: no --vault, relies on the env var ---
    settings = proj / ".claude" / "settings.local.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    old_cmd = f'"{PY}" "{HOOK}"'
    settings.write_text(json.dumps({"hooks": {
        e: [{"hooks": [{"type": "command", "command": old_cmd, "timeout": 15}]}]
        for e in ("PreCompact", "SessionEnd")}}, indent=2), encoding="utf-8")

    res, rc = cli("test-hook", "--project", str(proj), "--vault", "MyBrain")
    print(f"OLD registration -> ok={res.get('ok')} {res.get('passed')}/{res.get('total')} rc={rc}")
    for c in res.get("checks", []):
        if not c["ok"]:
            print("   FAILED:", c["check"], "--", c["detail"][:70])
    assert res.get("ok") is False, "BUG: test-hook passed a broken registration"
    assert rc == 1, "BUG: test-hook exited 0 on failure"
    print("   => correctly detected as broken\n")

    # --- the FIXED registration written by install ---
    cli("install", "--project", str(proj), "--vault", "MyBrain", "--scope", "project")
    cmd = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["SessionEnd"]
    cmd = [h["command"] for e in cmd for h in e["hooks"] if "capture_session" in h["command"]][0]
    print("registered command now:", cmd.replace(str(REPO), "<repo>"))
    res, rc = cli("test-hook", "--project", str(proj), "--vault", "MyBrain")
    print(f"NEW registration -> ok={res.get('ok')} {res.get('passed')}/{res.get('total')} rc={rc}")
    for c in res.get("checks", []):
        if not c["ok"]:
            print("   FAILED:", c["check"], "--", c["detail"][:70])
    assert res.get("ok") is True, "fixed registration should pass"
    print("   => works without OBSIDIAN_VAULT in the environment\n")

    print("REGRESSION TEST PASSED")
finally:
    shutil.rmtree(proj, ignore_errors=True)
