"""Run cases through the CLI's normal entry points and measure the engine's stitches."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from cli_anything_inkstitch.regress.cases import Case, shapes_svg

SUITE_VERSION = 1


def _invoke(args: list[str]) -> dict:
    """Run one CLI command in-process (the same entry the console script uses)."""
    from click.testing import CliRunner
    from cli_anything_inkstitch.cli import root
    r = CliRunner().invoke(root, args)
    out = r.stdout if hasattr(r, "stdout") else r.output
    if r.exit_code:
        raise RuntimeError(f"{' '.join(args[:4])} failed (exit {r.exit_code}): "
                           f"{(out or '')[-400:]} {r.exception!r}")
    try:
        return json.loads(out) if out.strip().startswith(("{", "[")) else {}
    except ValueError:
        return {}


def _plan(case: Case, work: Path) -> dict:
    """Build the project and run the planner. Returns the planner's own reports."""
    from cli_anything_inkstitch.artifact.engine_gate import render_lock
    proj = work / "p.json"
    reports: list[dict] = []
    if case.kind == "text":
        lines = case.spec["lines"]
        cap = float(case.spec["height_mm"])
        gap = float(case.spec.get("line_gap_mm", cap * 1.4))
        W = case.page_width_mm
        H = 40 + max(gap * len(lines), 3 * cap)
        svg = work / "page.svg"
        svg.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{H}mm" '
                       f'viewBox="0 0 {W} {H}"></svg>')
        _invoke(["document", "open", "--project", str(proj), "--svg", str(svg)])
        with render_lock():            # text-to-satin routes letters through the engine
            for i, line in enumerate(lines):
                cy = H / 2 + (i - (len(lines) - 1) / 2) * gap
                args = ["--json", "tools", "text-to-satin", "--project", str(proj),
                        "--font", case.font_path, "--text", line, "--height-mm", f"{cap:g}",
                        "--cx-mm", f"{W / 2:g}", "--cy-mm", f"{cy:.2f}"]
                if case.spec.get("letter_spacing_pct") is not None:
                    args += ["--letter-spacing-pct", str(case.spec["letter_spacing_pct"])]
                if case.spec.get("wght"):
                    args += ["--wght", str(case.spec["wght"])]
                if case.spec.get("merge_joins"):
                    args.append("--merge-joins")
                reports.append(_invoke(args + list(case.spec.get("args", []))))
    else:
        svg = work / "art.svg"
        if "svg" in case.spec:
            shutil.copyfile(case.spec["svg"], svg)
        else:
            svg.write_text(shapes_svg(case.spec))
        _invoke(["document", "open", "--project", str(proj), "--svg", str(svg)])
        args = ["--json", "tools", "shapes-to-satin", "--project", str(proj),
                "--fabric", case.fabric]
        if case.spec.get("overrides"):
            args += ["--overrides", case.spec["overrides"]]
        with render_lock():
            reports.append(_invoke(args + list(case.spec.get("args", []))))
    return {"project": str(proj), "reports": reports}


def _letters(reports: list[dict]) -> dict[str, str]:
    """group id (text0_l3) -> letter, from text-to-satin's reports."""
    out = {}
    for rep in reports:
        for i, lt in enumerate(rep.get("letters", [])):
            if lt.get("group"):
                out[lt["group"]] = f"{lt.get('letter', '?')}#{i}"
    return out


def _where(col: dict, letters: dict[str, str], groups: dict[str, str]) -> str:
    g = groups.get(col["id"])
    if g is None:
        m = re.match(r"^(text\d+_l\d+)_", col["id"])
        g = m.group(1) if m else None
    if g in letters:
        return letters[g]
    return col.get("shape") or col["id"]


def run_case(case: Case, out_dir: Path, sheets_name: str, *, sheets: bool = True,
             keep_work: bool = False, binary: str | None = None) -> dict[str, Any]:
    from cli_anything_inkstitch.artifact import engine_gate as eg
    from cli_anything_inkstitch.regress.measure import measure
    from cli_anything_inkstitch.regress.sheet import draw_sheet

    res: dict[str, Any] = {"id": case.id, "kind": case.kind, "family": case.family,
                           "private": case.private, "fabric": case.fabric,
                           "font": case.font_name, "notes": case.notes}
    if case.skip_reason:
        return {**res, "status": "skipped", "reason": case.skip_reason}
    work = out_dir / f"{sheets_name}_work" / case.id
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    t0 = time.monotonic()
    try:
        planned = _plan(case, work)
        t1 = time.monotonic()
        m = measure(planned["project"], binary=binary)
        t2 = time.monotonic()
    except Exception as e:                      # one broken case must not stop the suite
        return {**res, "status": "error", "error": f"{type(e).__name__}: {e}"[:600],
                "trace": traceback.format_exc()[-1200:],
                "runtime_s": round(time.monotonic() - t0, 1)}
    analysis = m["analysis"]
    summ = eg.summary_of(analysis)
    letters = _letters(planned["reports"])
    multi = [{"id": c["id"], "where": _where(c, letters, m["groups"]), "passes": c["passes"],
              "len_mm": c["len_mm"], "runs": c["runs"]}
             for c in analysis["columns"] if c["passes"] > 1]
    failed = [x for r in planned["reports"] for x in (r.get("failed_plans") or [])]
    res.update({
        "status": "ok",
        "satin_columns": summ["satin_columns"], "single_pass_columns": summ["single_pass_columns"],
        "multi_pass_columns": summ["multi_pass_columns"], "multi_pass": multi,
        "unmeasured_columns": sum(1 for c in analysis["columns"] if c["passes"] == 0),
        "trims": summ["trims"], "untrimmed_moves": summ["untrimmed_moves"],
        "untrimmed_mm": summ["untrimmed_mm"], "entry_jump_mm": summ["entry_jump_mm"],
        "longest_stitch_mm": summ["longest_stitch_mm"],
        "longest_stitch_at": analysis["stats"]["longest_stitch_at"],
        "stitches": summ["stitches"],
        "failed_plans": failed,
        "plan_seconds": round(t1 - t0, 1), "engine_seconds": round(t2 - t1, 1),
        "runtime_s": round(t2 - t0, 1),
    })
    if case.reference_dst:
        try:
            ref = eg.compare_reference(analysis, case.reference_dst)
            res["reference"] = {k: v for k, v in ref.items() if k != "reference"}
        except Exception as e:
            res["reference"] = {"error": str(e)[:200]}
    if sheets:
        head = [f"{case.id}   [{case.family}]   {case.fabric}",
                f"columns {res['satin_columns']}  multi-pass {res['multi_pass_columns']}"
                + (" (" + ", ".join(f"{x['where']}x{x['passes']}" for x in multi) + ")" if multi else "")
                + f"   trims {res['trims']}   untrimmed {res['untrimmed_moves']}"
                + (f" {res['untrimmed_mm']}" if res["untrimmed_mm"] else "")
                + f"   longest {res['longest_stitch_mm']} mm   stitches {res['stitches']}"]
        rails = [{"label": f"{x['where']} x{x['passes']}", "left": m["rails"][x["id"]][0],
                  "right": m["rails"][x["id"]][1]} for x in multi if x["id"] in m["rails"]]
        rel = f"{sheets_name}/{case.id}.png"
        draw_sheet(m["stream"], rails, head, out_dir / rel)
        res["sheet"] = rel
    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return res


def _git(path: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _meta(cases: list[Case], case_files: list[str], quick: bool) -> dict:
    import cli_anything_inkstitch as pkg
    from cli_anything_inkstitch.binary import detect_binary_version, discover
    pkg_dir = Path(pkg.__file__).resolve().parent
    exe = discover(None, None)
    label = os.environ.get("REGRESS_PLANNER_LABEL")      # set by tools/regress_with_src.py
    git_branch, git_rev = (label.split("@", 1) + [None])[:2] if label else (None, None)
    return {"suite_version": SUITE_VERSION, "quick": quick,
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "package_path": str(pkg_dir), "package_version": pkg.__version__,
            "git_rev": git_rev or _git(pkg_dir, "rev-parse", "--short", "HEAD"),
            "git_branch": git_branch or _git(pkg_dir, "rev-parse", "--abbrev-ref", "HEAD"),
            "git_dirty": False if label else bool(_git(pkg_dir, "status", "--porcelain", "--", str(pkg_dir))),
            "engine_binary": exe, "engine_version": detect_binary_version(exe) if exe else None,
            "python": sys.version.split()[0], "host": platform.node(),
            "case_files": case_files}


def summarize(results: dict[str, dict]) -> dict:
    fam: dict[str, dict] = {}
    for r in results.values():
        f = fam.setdefault(r["family"], {"cases": 0, "ok": 0, "satin_columns": 0,
                                         "multi_pass_columns": 0, "trims": 0,
                                         "untrimmed_moves": 0, "stitches": 0})
        f["cases"] += 1
        if r["status"] == "ok":
            f["ok"] += 1
            for k in ("satin_columns", "multi_pass_columns", "trims", "untrimmed_moves", "stitches"):
                f[k] += r[k]
    ok = [r for r in results.values() if r["status"] == "ok"]
    return {"cases": len(results), "ok": len(ok),
            "errors": sum(r["status"] == "error" for r in results.values()),
            "skipped": sum(r["status"] == "skipped" for r in results.values()),
            "satin_columns": sum(r["satin_columns"] for r in ok),
            "multi_pass_columns": sum(r["multi_pass_columns"] for r in ok),
            "by_family": fam}


def run_suite(cases: list[Case], results_path: Path, *, case_files: list[str],
              quick: bool = False, sheets: bool = True, keep_work: bool = False,
              binary: str | None = None, log: Callable[[str], None] = print) -> dict:
    """Run the cases one at a time; write `results_path` and `<stem>_sheets/<id>.png` beside it."""
    results_path = results_path.resolve()
    out_dir = results_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    sheets_name = f"{results_path.stem}_sheets"
    t0 = time.monotonic()
    results: dict[str, dict] = {}
    for i, case in enumerate(cases, 1):          # sequential: one heavy engine run at a time
        r = run_case(case, out_dir, sheets_name, sheets=sheets, keep_work=keep_work, binary=binary)
        results[case.id] = r
        if r["status"] == "ok":
            log(f"[{i}/{len(cases)}] {case.id}: {r['satin_columns']} cols, {r['multi_pass_columns']} multi, "
                f"{r['trims']} trims, longest {r['longest_stitch_mm']}, {r['stitches']} st, {r['runtime_s']}s")
        else:
            log(f"[{i}/{len(cases)}] {case.id}: {r['status'].upper()} {r.get('error') or r.get('reason')}")
    doc = {"meta": {**_meta(cases, case_files, quick),
                    "total_runtime_s": round(time.monotonic() - t0, 1)},
           "summary": summarize(results), "cases": results}
    tmp = results_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1))
    os.replace(tmp, results_path)
    if not keep_work:
        shutil.rmtree(out_dir / f"{sheets_name}_work", ignore_errors=True)
    return doc
