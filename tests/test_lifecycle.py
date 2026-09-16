"""Full-lifecycle MCP test over stdio: same transport Claude Code uses.

Runs against a throwaway vault so the real one stays clean.
"""
import asyncio, json, os, sys, tempfile, shutil
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT = Path(__file__).resolve().parents[1]  # repo root, wherever it is cloned
_VENV = PROJECT / ".venv"
PY = _VENV / "Scripts" / "python.exe" if (_VENV / "Scripts").is_dir() else _VENV / "bin" / "python"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))


async def call(s, tool, args=None):
    r = await s.call_tool(tool, args or {})
    txt = r.content[0].text if r.content else "{}"
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        return {"_raw": txt}


async def main():
    vault = Path(tempfile.mkdtemp(prefix="sbtest-"))
    params = StdioServerParameters(
        command=str(PY), args=["-m", "obsidian_secondbrain"],
        env={**os.environ, "OBSIDIAN_VAULT": str(vault).replace("\\", "/")},
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            print("\n[1] init + structure")
            init = await call(s, "init_vault")
            check("init_vault creates 6 folders", len(init["created_folders"]) == 6, init)
            again = await call(s, "init_vault")
            check("init_vault is idempotent", again["created_folders"] == [], again)
            info = await call(s, "vault_info")
            check("vault_info reports taxonomy", set(info["folders"]) == {
                "inbox", "log", "sessions", "notes", "maps", "archive"}, info)

            print("\n[2] CAPTURE - append-only log")
            await call(s, "log_entry", {"text": "Attention is all you need - reread.", "source": "reading"})
            await call(s, "log_entry", {"text": "Idea: distillation should be a separate pass.", "source": "thought"})
            log = await call(s, "read_daily_log")
            check("both entries appended to one daily note",
                  log["body"].count("##") == 2 and "separate pass" in log["body"], log.get("body"))

            print("\n[3] COMPACT - session capture")
            cap = await call(s, "capture_session", {
                "title": "Design the distillation loop",
                "summary": "Agreed the server never summarises; the agent does.",
                "decisions": ["Raw capture is append-only"],
                "open_questions": ["What marks a note 'done'?"],
                "artifacts": ["server.py"], "tags": ["design"], "project": "mcp",
            })
            note = await call(s, "read_note", {"path": cap["session_note"]})
            check("session note marked undistilled", note["frontmatter"]["distilled"] is False)
            check("open questions rendered as checkboxes", "- [ ] What marks" in note["body"])
            check("pointer written into daily log",
                  "Session captured" in (await call(s, "read_daily_log"))["body"])

            print("\n[4] DISTILL")
            q = await call(s, "distill_queue", {"limit": 5})
            check("queue picks up log + session", q["pending_total"] == 2, q["pending_total"])
            check("queue returns content for the agent", "content" in q["items"][0])
            check("queue tells the agent what to do", "atomic note" in q["next_step"])

            c1 = await call(s, "create_concept_note", {
                "title": "Distillation must be a separate pass from capture",
                "claim": "Mixing them makes you edit raw material while capturing it.",
                "links": ["Raw capture is append-only"], "tags": ["second-brain"],
                "sources": [cap["session_note"]]})
            c2 = await call(s, "create_concept_note", {
                "title": "Raw capture is append-only",
                "claim": "An immutable log is what makes distillation auditable.",
                "links": ["Distillation must be a separate pass from capture"],
                "tags": ["second-brain"]})
            check("concept notes land in notes folder", c1["path"].startswith("30-Notes/"), c1)

            md = await call(s, "mark_distilled", {"path": cap["session_note"],
                                                  "produced": [c1["path"], c2["path"]]})
            check("mark_distilled flips the flag", md["frontmatter"]["distilled"] is True)
            check("mark_distilled records provenance", len(md["frontmatter"]["produced"]) == 2)
            q2 = await call(s, "distill_queue", {"limit": 5, "include_body": False})
            check("distilled item leaves the queue", q2["pending_total"] == 1, q2["pending_total"])

            print("\n[5] FIND vs SEARCH")
            f = await call(s, "find_notes", {"pattern": "Raw*"})
            check("find_notes matches by name glob", f["count"] == 1 and "Raw capture" in f["results"][0]["path"], f)
            f2 = await call(s, "find_notes", {"pattern": "append"})
            check("find_notes bare word is substring", f2["count"] == 1, f2)
            f3 = await call(s, "find_notes", {"pattern": "auditable"})
            check("find_notes does NOT match content", f3["count"] == 0, f3)
            sr = await call(s, "search_notes", {"query": "auditable"})
            check("search_notes DOES match content", sr["hits"] == 1, sr)
            rx = await call(s, "search_notes", {"query": r"append-only|immutable", "regex": True})
            check("regex search works", rx["hits"] >= 2, rx["hits"])
            tg = await call(s, "search_by_tag", {"tag": "second-brain"})
            check("tag search finds both notes", len(tg["results"]) == 2, tg)

            print("\n[6] GRAPH")
            bl = await call(s, "backlinks", {"path": c2["path"]})
            check("backlinks resolves wikilink", c1["path"] in bl["backlinks"], bl)
            rel = await call(s, "related_notes", {"path": c1["path"]})
            check("related_notes scores neighbours", len(rel["related"]) >= 1, rel)
            check("related_notes explains why", "why" in rel["related"][0])
            h = await call(s, "vault_health")
            check("health counts backlog", h["undistilled_raw_notes"] == 1, h["undistilled_raw_notes"])
            check("health finds no orphans (both linked)", h["orphan_concept_notes"] == [], h["orphan_concept_notes"])

            print("\n[7] EDIT + MOVE")
            await call(s, "patch_section", {"path": c1["path"], "heading": "## Related",
                                            "text": "- [[A third note]]"})
            n = await call(s, "read_note", {"path": c1["path"]})
            check("patch_section inserts into existing section", "A third note" in n["body"])
            await call(s, "patch_section", {"path": c1["path"], "heading": "## Evidence",
                                            "text": "Observed in practice."})
            n = await call(s, "read_note", {"path": c1["path"]})
            check("patch_section creates missing section", "## Evidence" in n["body"])
            h2 = await call(s, "vault_health")
            check("dangling link surfaces as unresolved", "A third note" in h2["unresolved_links"], h2["unresolved_links"])

            arch = await call(s, "archive_note", {"path": c2["path"]})
            check("archive_note moves, not deletes", arch["to"].startswith("90-Archive/"), arch)

            print("\n[8] SAFETY")
            esc = await call(s, "read_note", {"path": "../../../Windows/win.ini"})
            check("path escape refused", esc.get("error") == "VaultError", esc)
            dup = await call(s, "create_note", {"path": c1["path"], "body": "clobber"})
            check("no silent clobber", dup.get("error") == "VaultError", dup)
            missing = await call(s, "read_note", {"path": "30-Notes/nope.md"})
            check("missing note returns error data, not a crash", missing.get("error") == "NotFound", missing)

            print("\n[9] PROMPTS")
            ps = await s.list_prompts()
            check("3 prompts exposed", len(ps.prompts) == 3, [p.name for p in ps.prompts])
            got = await s.get_prompt("distill", {"limit": "3"})
            check("distill prompt renders with arg", "distill_queue(limit=3)" in got.messages[0].content.text)

    shutil.rmtree(vault, ignore_errors=True)
    print(f"\n{'='*60}\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", *FAIL, sep="\n  - ")
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
