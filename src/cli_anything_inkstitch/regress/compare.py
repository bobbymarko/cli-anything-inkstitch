"""Compare two results files: a per-case table, a verdict, and side-by-side images of what changed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# -- thresholds (named, so a planner PR can argue with one) -------------------------------------
MAX_UNTRIMMED_MOVE_MM = 3.0       # an untrimmed needle-up move longer than this is a thread float
MAX_LONGEST_STITCH_MM = 8.0       # a longer stitch is a snagging loop
TRIM_INCREASE_ABS = 2             # trims may rise by max(this, ...
TRIM_INCREASE_FRAC = 0.10         # ... this fraction of the baseline) before it is a regression
STITCH_COUNT_WARN_FRAC = 0.05     # stitch count moving more than this is a warning only
_EPS = 0.05                       # mm: rounding noise on a longest-stitch comparison


def load(path: str | Path) -> dict:
    p = Path(path)
    doc = json.loads(p.read_text())
    doc["_dir"] = str(p.resolve().parent)
    return doc


def _long_moves(r: dict) -> int:
    return sum(1 for mm in r.get("untrimmed_mm", []) if mm > MAX_UNTRIMMED_MOVE_MM)


_KEYS = ("satin_columns", "multi_pass_columns", "trims", "untrimmed_moves",
         "longest_stitch_mm", "stitches")


def judge(base: dict | None, new: dict | None) -> dict[str, Any]:
    """Verdict for one case: {"regressions": [...], "warnings": [...], "changed": bool}."""
    reg: list[str] = []
    warn: list[str] = []
    if new is None:
        return {"regressions": [], "warnings": ["not in the new run"], "changed": False, "missing": "new"}
    if base is None:
        return {"regressions": [], "warnings": ["not in the baseline"], "changed": False, "missing": "base"}
    bs, ns = base["status"], new["status"]
    if ns != "ok":
        if bs == "ok" and ns == "error":
            reg.append(f"case now fails: {new.get('error', '')[:100]}")
        elif ns == "skipped":
            warn.append(f"skipped: {new.get('reason', '')[:80]}")
        return {"regressions": reg, "warnings": warn, "changed": bs != ns}
    if bs != "ok":
        return {"regressions": [], "warnings": [f"baseline was {bs}"], "changed": True}

    if new["multi_pass_columns"] > base["multi_pass_columns"]:
        where = sorted({m["where"] for m in new["multi_pass"]} - {m["where"] for m in base["multi_pass"]})
        reg.append(f"multi-pass columns {base['multi_pass_columns']} -> {new['multi_pass_columns']}"
                   + (f" (new: {', '.join(where)})" if where else ""))
    if _long_moves(new) > _long_moves(base):
        reg.append(f"untrimmed moves over {MAX_UNTRIMMED_MOVE_MM:g} mm "
                   f"{_long_moves(base)} -> {_long_moves(new)} ({[m for m in new['untrimmed_mm'] if m > MAX_UNTRIMMED_MOVE_MM]})")
    nl, bl = new["longest_stitch_mm"], base["longest_stitch_mm"]
    if nl > MAX_LONGEST_STITCH_MM:
        # a case that was already over the limit fails only if it got worse
        (reg if nl > bl + _EPS else warn).append(
            f"longest stitch {nl} mm > {MAX_LONGEST_STITCH_MM:g}" + ("" if nl > bl + _EPS else f" (baseline {bl}, not worse)"))
    if new["trims"] - base["trims"] > max(TRIM_INCREASE_ABS, TRIM_INCREASE_FRAC * base["trims"]):
        reg.append(f"trims {base['trims']} -> {new['trims']}")
    if base["stitches"] and abs(new["stitches"] - base["stitches"]) > STITCH_COUNT_WARN_FRAC * base["stitches"]:
        warn.append(f"stitches {base['stitches']} -> {new['stitches']} "
                    f"({(new['stitches'] - base['stitches']) / base['stitches']:+.0%})")
    if len(new.get("failed_plans", [])) > len(base.get("failed_plans", [])):
        warn.append(f"letters/shapes falling back to fill {len(base.get('failed_plans', []))} -> {len(new['failed_plans'])}")
    changed = (any(new[k] != base[k] for k in _KEYS)
               or sorted(m["where"] for m in new["multi_pass"]) != sorted(m["where"] for m in base["multi_pass"])
               or new["untrimmed_mm"] != base["untrimmed_mm"])
    return {"regressions": reg, "warnings": warn, "changed": changed}


def _arrow(a, b, fmt="{}") -> str:
    return fmt.format(a) if a == b else f"{fmt.format(a)}>{fmt.format(b)}"


def compare(base: dict, new: dict, out_dir: str | Path | None = None, *,
            images: bool = True) -> dict[str, Any]:
    from cli_anything_inkstitch.regress.sheet import side_by_side
    ids = list(new["cases"]) + [i for i in base["cases"] if i not in new["cases"]]
    rows, regressed, warned = [], 0, 0
    for cid in ids:
        b, n = base["cases"].get(cid), new["cases"].get(cid)
        v = judge(b, n)
        row = {"id": cid, **v}
        if b and n and b["status"] == "ok" and n["status"] == "ok":
            row["cells"] = [
                _arrow(b["multi_pass_columns"], n["multi_pass_columns"]),
                _arrow(_long_moves(b), _long_moves(n)),
                _arrow(b["longest_stitch_mm"], n["longest_stitch_mm"], "{:.1f}"),
                _arrow(b["trims"], n["trims"]),
                _arrow(b["stitches"], n["stitches"]),
            ]
        else:
            row["cells"] = ["-"] * 5
        if v["regressions"]:
            regressed += 1
        elif v["warnings"]:
            warned += 1
        if images and out_dir and v["changed"] and b and n and b.get("sheet") and n.get("sheet"):
            bp, np_ = Path(base["_dir"]) / b["sheet"], Path(new["_dir"]) / n["sheet"]
            if bp.exists() and np_.exists():
                dest = Path(out_dir) / f"{cid}.png"
                side_by_side(bp, np_, f"baseline  {cid}", f"new  {cid}", dest)
                row["image"] = str(dest)
        rows.append(row)
    return {"rows": rows, "regressed": regressed, "warned": warned,
            "ok": regressed == 0,
            "changed": sum(1 for r in rows if r["changed"]),
            "base_meta": base.get("meta", {}), "new_meta": new.get("meta", {})}


def format_table(result: dict) -> str:
    head = ("case", "multi", "float>3", "longest", "trims", "stitches", "verdict")
    body = []
    for r in result["rows"]:
        verdict = ("REGRESSION" if r["regressions"] else "warn" if r["warnings"]
                   else "changed" if r["changed"] else "same")
        body.append((r["id"], *r["cells"], verdict))
    w = [max(len(str(x[i])) for x in [head, *body]) for i in range(len(head))]
    fmt = lambda row: "  ".join(str(c).ljust(w[i]) if i in (0, 6) else str(c).rjust(w[i])
                                for i, c in enumerate(row))
    lines = [fmt(head), fmt(["-" * x for x in w])] + [fmt(r) for r in body]
    detail = []
    for r in result["rows"]:
        for m in r["regressions"]:
            detail.append(f"  REGRESSION  {r['id']}: {m}")
        for m in r["warnings"]:
            detail.append(f"  warning     {r['id']}: {m}")
        if r.get("image"):
            detail.append(f"  image       {r['id']}: {r['image']}")
    nb, nn = result["base_meta"], result["new_meta"]
    foot = [f"baseline: {nb.get('git_branch')}@{nb.get('git_rev')}   new: {nn.get('git_branch')}@{nn.get('git_rev')}",
            f"{len(result['rows'])} cases, {result['changed']} changed, {result['regressed']} regressed, "
            f"{result['warned']} with warnings -> {'FAIL' if result['regressed'] else 'PASS'}"]
    return "\n".join(lines + ([""] + detail if detail else []) + [""] + foot)
