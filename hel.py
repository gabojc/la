#!/usr/bin/env python3
"""
patch_webapp.py — surgical patcher for /opt/lastfm/webapp.py

Fixes applied:
  A   Guard the renderTable <script> lookup (AttributeError → None)
  A2  Guard both renderTable write-backs
  B   Wrap apply_bootstrap in try/except; never leave a job stuck "running"
  C   Move apply_bootstrap OUT of JOBS_LOCK (was blocking /status)
  D   Regex-based SECTIONS injection (whitespace-tolerant, no silent no-op)
  D2  Regex-based 'firstlast' branch injection (same)
  E   sweep() no longer yanks running jobs (KeyError race)
  F   /u/<user>/playhistory.json gets X-Robots-Tag
  G   Guard JOBS[user] in the stdout reader loop (KeyError after sweep)

Usage:
    python3 patch_webapp.py --dry-run
    python3 patch_webapp.py
    python3 patch_webapp.py --path /some/other/webapp.py

After patching, restart the Flask process so the new code loads
(e.g. `sudo systemctl restart lastfm-web`, or however you run it).
"""
import argparse
import re
import shutil
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def apply_patch(src, name, old, new, *, required=True, count=1):
    """Apply one replacement. Returns (new_src, message). Idempotent."""
    if new in src and old not in src:
        return src, f"[skip] {name}: already applied"
    n = src.count(old)
    if n == 0:
        if required:
            raise RuntimeError(f"{name}: pattern not found")
        return src, f"[warn] {name}: pattern not found"
    if count == 0:
        src = src.replace(old, new)
        return src, f"[ok  ] {name}: replaced all {n}"
    src = src.replace(old, new, count)
    return src, f"[ok  ] {name}: replaced {min(n, count)}"


def verify(src):
    """Post-patch sanity checks. Returns list of problem strings."""
    checks = [
        ("script_tag = soup.find", 1),
        ("if script_tag is not None:", 2),
        ("_add_playhistory_section", 2),
        ("_branch_pat", 2),
        ("j = JOBS.get(user)", 2),
        ('err = f"apply_bootstrap:', 1),
        ("X-Robots-Tag", 3),
    ]
    problems = []
    for needle, min_count in checks:
        c = src.count(needle)
        if c < min_count:
            problems.append(f"missing {needle!r} (need ≥{min_count}, found {c})")
    return problems


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="/opt/lastfm/webapp.py")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = Path(args.path)
    if not path.is_file():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 1

    src = path.read_text(encoding="utf-8")
    orig = src
    msgs = []

    # ------------------------------------------------------------------ A
    src, m = apply_patch(
        src, "A. guard renderTable lookup",
        old=(
            '    # 7. Update dynamically generated tables in JS\n'
            '    js_raw = soup.find("script", string=re.compile("renderTable")).string\n'
            '    if js_raw:\n'
        ),
        new=(
            '    # 7. Update dynamically generated tables in JS\n'
            '    script_tag = soup.find("script", string=re.compile("renderTable"))\n'
            '    js_raw = script_tag.string if script_tag is not None else None\n'
            '    if js_raw:\n'
        ),
    )
    msgs.append(m)

    # ------------------------------------------------------------------ A2
    src, m = apply_patch(
        src, "A2. guard renderTable write-back (all)",
        old='        soup.find("script", string=re.compile("renderTable")).string = js_raw\n',
        new=(
            '        if script_tag is not None:\n'
            '            script_tag.string = js_raw\n'
        ),
        count=0,  # replace all occurrences
    )
    msgs.append(m)

    # ------------------------------------------------------------------ D
    src, m = apply_patch(
        src, "D. regex SECTIONS injection",
        old=(
            '        # Add to SECTIONS list\n'
            '        js_raw = js_raw.replace(\n'
            '            "const SECTIONS = [\'unplayed\', \'span\', \'streaks\', \'patterns\', \'loved\', \'firstlast\'",\n'
            '            "const SECTIONS = [\'unplayed\', \'span\', \'streaks\', \'patterns\', \'loved\', \'playhistory\', \'firstlast\'"\n'
            '        )\n'
        ),
        new=(
            '        # Add to SECTIONS list (regex — whitespace-tolerant)\n'
            '        def _add_playhistory_section(_m):\n'
            '            body = _m.group(0)\n'
            '            if "\'playhistory\'" in body or \'"playhistory"\' in body:\n'
            '                return body\n'
            '            return body.replace("\'firstlast\'", "\'playhistory\', \'firstlast\'", 1)\n'
            '        js_raw = re.sub(\n'
            '            r"const\\s+SECTIONS\\s*=\\s*\\[[^\\]]*\\]",\n'
            '            _add_playhistory_section, js_raw, count=1,\n'
            '        )\n'
        ),
    )
    msgs.append(m)

    # ------------------------------------------------------------------ D2
    src, m = apply_patch(
        src, "D2. regex firstlast-branch injection",
        old=(
            '        js_raw = js_raw.replace("    } else if (current === \'firstlast\') {", js_logic)\n'
        ),
        new=(
            '        # Inject the playhistory branch before the firstlast branch (regex).\n'
            '        _branch_pat = re.compile(\n'
            '            r"[ \\t]*\\}[ \\t]*else[ \\t]+if[ \\t]*\\([ \\t]*current[ \\t]*===[ \\t]*[^a-zA-Z0-9_]firstlast[^a-zA-Z0-9_][ \\t]*\\)[ \\t]*\\{"\n'
            '        )\n'
            '        if "current === \'playhistory\'" in js_raw:\n'
            '            pass  # already injected\n'
            '        else:\n'
            '            js_raw, _n = _branch_pat.subn(lambda _m: js_logic, js_raw, count=1)\n'
            '            if _n != 1:\n'
            '                print(f"[apply_bootstrap] WARN: could not inject playhistory "\n'
            '                      f"branch (pattern not found); tab will not work",\n'
            '                      file=sys.stderr)\n'
        ),
    )
    msgs.append(m)

    # ------------------------------------------------------------------ B/C
    src, m = apply_patch(
        src, "B/C. try/except + apply_bootstrap outside lock",
        old=(
            '    with JOBS_LOCK:\n'
            '        j = JOBS[user]\n'
            '        j["finished"] = time.time()\n'
            '        if done and (target / "index.html").exists():\n'
            '            apply_bootstrap(target / "index.html")\n'
            '            j["state"] = "done"\n'
            '            j["progress"] = 100.0\n'
            '            j["last_line"] = "Done."\n'
            '            try:\n'
            '                target.touch()\n'
            '            except Exception:\n'
            '                pass\n'
            '        else:\n'
            '            j["state"] = "failed"\n'
            '            j["error"] = "The sync script exited with an error. See server logs."\n'
            '            j["last_line"] = (j["log"][-1] if j["log"] else "")\n'
        ),
        new=(
            '    # B/C: apply_bootstrap runs OUTSIDE the lock, and never raises\n'
            '    ok = False\n'
            '    err = None\n'
            '    if done and (target / "index.html").exists():\n'
            '        try:\n'
            '            apply_bootstrap(target / "index.html")\n'
            '            ok = True\n'
            '        except Exception as e:\n'
            '            err = f"apply_bootstrap: {e!r}"\n'
            '            print(f"[_run_sync] apply_bootstrap failed for {user}: {e!r}",\n'
            '                  file=sys.stderr)\n'
            '\n'
            '    with JOBS_LOCK:\n'
            '        j = JOBS.get(user)\n'
            '        if j is None:\n'
            '            return  # swept while running; nothing to update\n'
            '        j["finished"] = time.time()\n'
            '        if ok:\n'
            '            j["state"] = "done"\n'
            '            j["progress"] = 100.0\n'
            '            j["last_line"] = "Done."\n'
            '            try:\n'
            '                target.touch()\n'
            '            except Exception:\n'
            '                pass\n'
            '        else:\n'
            '            j["state"] = "failed"\n'
            '            j["error"] = err or "The sync script exited with an error. See server logs."\n'
            '            j["last_line"] = (j["log"][-1] if j["log"] else "")\n'
        ),
    )
    msgs.append(m)

    # ------------------------------------------------------------------ G
    src, m = apply_patch(
        src, "G. guard JOBS[user] in reader loop",
        old=(
            '            with JOBS_LOCK:\n'
            '                j = JOBS[user]\n'
            '                j["log"].append(line)\n'
        ),
        new=(
            '            with JOBS_LOCK:\n'
            '                j = JOBS.get(user)\n'
            '                if j is None:\n'
            '                    break\n'
            '                j["log"].append(line)\n'
        ),
    )
    msgs.append(m)

    # ------------------------------------------------------------------ E
    src, m = apply_patch(
        src, "E. sweep: skip running jobs",
        old=(
            '            if now - entry.stat().st_mtime > USER_TTL_SECONDS:\n'
            '                shutil.rmtree(entry, ignore_errors=True)\n'
            '                with JOBS_LOCK:\n'
            '                    JOBS.pop(entry.name, None)\n'
        ),
        new=(
            '            if now - entry.stat().st_mtime > USER_TTL_SECONDS:\n'
            '                with JOBS_LOCK:\n'
            '                    job = JOBS.get(entry.name)\n'
            '                    if job and job.get("state") == "running":\n'
            '                        continue\n'
            '                shutil.rmtree(entry, ignore_errors=True)\n'
            '                with JOBS_LOCK:\n'
            '                    JOBS.pop(entry.name, None)\n'
        ),
    )
    msgs.append(m)

    # ------------------------------------------------------------------ F
    src, m = apply_patch(
        src, "F. X-Robots-Tag on playhistory.json",
        old=(
            '    out_file = d / "playhistory.json"\n'
            '    out_file.write_text(json.dumps(history[:1000]))\n'
            '    \n'
            '    return jsonify(history[:1000])\n'
        ),
        new=(
            '    out_file = d / "playhistory.json"\n'
            '    out_file.write_text(json.dumps(history[:1000]))\n'
            '    \n'
            '    resp = jsonify(history[:1000])\n'
            '    resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"\n'
            '    return resp\n'
        ),
    )
    msgs.append(m)

    # ------------------------------------------------------------------ report
    print(f"Patcher — {path}")
    for line in msgs:
        print("  " + line)

    if src == orig:
        print("\nNothing changed (already fully patched?).")
        return 0

    try:
        compile(src, str(path), "exec")
    except SyntaxError as e:
        print(f"\nERROR: patched source does not compile: {e}", file=sys.stderr)
        return 3

    problems = verify(src)
    if problems:
        print("\nVERIFY WARNINGS:")
        for p in problems:
            print("  - " + p)

    if args.dry_run:
        print("\n[dry-run] not writing")
        return 0

    bak = path.with_name(path.name + f".bak-{int(time.time())}")
    shutil.copy2(path, bak)
    print(f"\nBackup: {bak}")

    path.write_text(src, encoding="utf-8")
    print(f"Wrote:  {path}")
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
    print("Compile: OK")
    print("\nRestart the Flask process so the new code loads.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
