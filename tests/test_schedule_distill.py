"""Regression test for `schedule-distill`: register, update, and remove a
Windows Task Scheduler entry, without ever touching a real vault.

Windows-only, like the feature itself -- skips cleanly elsewhere.
"""
import json, platform, shutil, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_VENV = REPO / ".venv"
PY = _VENV / "Scripts" / "python.exe" if (_VENV / "Scripts").is_dir() else _VENV / "bin" / "python"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))


def cli(*a):
    p = subprocess.run([str(PY), "-m", "obsidian_secondbrain.cli", *a],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(REPO))
    try:
        return json.loads(p.stdout), p.returncode
    except json.JSONDecodeError:
        return {"_stdout": p.stdout, "_stderr": p.stderr}, p.returncode


def schtasks_has(task_name: str) -> bool:
    p = subprocess.run(["schtasks", "/query", "/tn", task_name],
                       capture_output=True, text=True)
    return p.returncode == 0


if platform.system() != "Windows":
    print("SKIPPED: schedule-distill is Windows-only")
    sys.exit(0)

proj = Path(tempfile.mkdtemp(prefix="schedtest-"))
task_name = None
try:
    cli("init", "--project", str(proj), "--vault", "MyBrain")
    subprocess.run(["git", "init", "-b", "main", "-q"], cwd=str(proj), check=True)
    subprocess.run(["git", "-c", "user.email=test@test.invalid", "-c", "user.name=test",
                    "commit", "--allow-empty", "-q", "-m", "Initial commit"],
                   cwd=str(proj), check=True)

    print("[1] rejects a bad --time")
    res, rc = cli("schedule-distill", "--project", str(proj), "--vault", "MyBrain", "--time", "6pm")
    check("bad time format is rejected", res.get("ok") is False)
    check("exits non-zero on bad input", rc != 0)

    print("\n[2] registers a real scheduled task")
    res, rc = cli("schedule-distill", "--project", str(proj), "--vault", "MyBrain", "--time", "23:59")
    task_name = res.get("task_name")
    check("registration reports ok", res.get("ok") is True, res)
    check("exits 0", rc == 0)
    check("task actually exists in Task Scheduler", task_name and schtasks_has(task_name))
    check("prompt file written", Path(res.get("prompt_file", "")).exists())
    check("wrapper script written", Path(res.get("wrapper_script", "")).exists())

    wrapper_text = Path(res["wrapper_script"]).read_text(encoding="utf-8")
    check("wrapper cd's into the project", str(proj) in wrapper_text)
    check("wrapper uses --permission-mode dontAsk", "--permission-mode dontAsk" in wrapper_text)
    for tool in ("vault_info", "distill_queue", "create_concept_note",
                 "mark_distilled", "log_entry", "search_notes", "find_notes"):
        check(f"wrapper allow-lists {tool}",
              f"mcp__obsidian-secondbrain__{tool}" in wrapper_text)
    check("no server-wide wildcard in the allow-list",
          "mcp__obsidian-secondbrain__*" not in wrapper_text)

    print("\n[3] git backup step is idempotent and never deletes tracked files")
    # Regression for a real bug found in manual testing: an earlier version
    # did `git checkout main` before creating each day's branch, to start
    # "fresh". But a file only ever committed on a previous day's branch is
    # untracked on main, and `checkout` syncs the working tree to match the
    # branch it switches to -- that silently DELETED real tracked files
    # (.gitignore, .mcp.json) the moment a second run swapped back to main.
    # This runs just the generated git block (no live claude -p call, so no
    # API cost) twice in a row and proves neither failure mode can recur:
    # files must survive, and a no-op second run must not create a spurious
    # commit or branch.
    marker = "# --- git backup:"
    git_block = wrapper_text[wrapper_text.index(marker):]
    git_only = proj / "_git_backup_only.ps1"
    git_only.write_text(f'Set-Location -LiteralPath "{proj}"\n{git_block}', encoding="utf-8")

    (proj / "tracked.txt").write_text("only ever committed on a dated branch\n", encoding="utf-8")
    r1 = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(git_only)], capture_output=True, text=True)
    check("git block run 1 exits 0", r1.returncode == 0, r1.stderr[:300])

    log1 = subprocess.run(["git", "-C", str(proj), "log", "--oneline", "--all"],
                          capture_output=True, text=True).stdout
    check("run 1 created exactly one automated commit", log1.count("Automated distill run") == 1, log1)
    check("tracked.txt survives run 1", (proj / "tracked.txt").exists())

    r2 = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(git_only)], capture_output=True, text=True)
    check("git block run 2 exits 0", r2.returncode == 0, r2.stderr[:300])

    check("tracked.txt still exists after a second, no-op run",
          (proj / "tracked.txt").exists())
    log2 = subprocess.run(["git", "-C", str(proj), "log", "--oneline", "--all"],
                          capture_output=True, text=True).stdout
    check("no-op second run created no new commit",
          log2.count("Automated distill run") == 1, log2)
    branches = subprocess.run(["git", "-C", str(proj), "branch"],
                              capture_output=True, text=True).stdout
    check("still exactly one dated branch (no duplicate)",
          branches.count("secbrain-distill-") == 1, branches)

    (proj / "tracked2.txt").write_text("a second real change\n", encoding="utf-8")
    r3 = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(git_only)], capture_output=True, text=True)
    check("git block run 3 exits 0", r3.returncode == 0, r3.stderr[:300])
    log3 = subprocess.run(["git", "-C", str(proj), "log", "--oneline", "--all"],
                          capture_output=True, text=True).stdout
    check("a genuinely new change still gets committed on a later run",
          log3.count("Automated distill run") == 2, log3)

    print("\n[4] re-running with a new time updates the same task, no duplicate")
    res2, rc2 = cli("schedule-distill", "--project", str(proj), "--vault", "MyBrain", "--time", "06:30")
    check("same task name reused", res2.get("task_name") == task_name, res2.get("task_name"))
    q = subprocess.run(["schtasks", "/query", "/tn", task_name, "/fo", "list", "/v"],
                       capture_output=True, text=True)
    # schtasks prints the time without a leading zero ("6:30:00"), and rolls
    # the *next* run to tomorrow once today's 06:30 has already passed --
    # match on "Next Run Time" containing "6:30:00" rather than the literal
    # HH:MM we passed in, and don't assert on which calendar day.
    next_run_line = next((ln for ln in q.stdout.splitlines() if "Next Run Time" in ln), "")
    check("Task Scheduler reflects the new time", "6:30:00" in next_run_line, next_run_line)

    print("\n[5] --remove unregisters it")
    res3, rc3 = cli("schedule-distill", "--project", str(proj), "--remove")
    check("remove reports ok", res3.get("ok") is True)
    check("task is gone from Task Scheduler", not schtasks_has(task_name))
    task_name = None  # nothing left to clean up

    total = len(PASS) + len(FAIL)
    print(f"\n{'='*60}\n{len(PASS)} passed, {len(FAIL)} failed" + (f" out of {total}" if FAIL else ""))
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)
finally:
    if task_name:
        subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"], capture_output=True)
    shutil.rmtree(proj, ignore_errors=True)
