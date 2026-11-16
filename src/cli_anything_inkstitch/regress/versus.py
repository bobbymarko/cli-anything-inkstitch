"""`regress versus`: our planner next to a contender, case by case, from one `run --versus` results file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cli_anything_inkstitch.regress.compare import MAX_UNTRIMMED_MOVE_MM


def pairs(doc: dict) -> list[tuple[dict, dict]]:
    out = []
    for cid, r in doc["cases"].items():
        if r.get("contender"):
            twin = doc["cases"].get(cid.rsplit("@", 1)[0])
            if twin is not None:
                out.append((twin, r))
    return out


def _long(r: dict) -> int:
    return sum(1 for mm in r.get("untrimmed_mm", []) if mm > MAX_UNTRIMMED_MOVE_MM)


def versus(doc: dict, results_dir: str | Path, out_dir: str | Path | None = None, *,
           images: bool = True) -> dict[str, Any]:
    from cli_anything_inkstitch.regress.sheet import side_by_side
    rows = []
    for ours, theirs in pairs(doc):
        row = {"id": ours["id"], "family": ours["family"], "contender": theirs["contender"]}
        if ours["status"] != "ok" or theirs["status"] != "ok":
            row["note"] = f"planner {ours['status']}, {theirs['contender']} {theirs['status']}"
            rows.append(row)
            continue
        for tag, r in (("planner", ours), ("contender", theirs)):
            row[tag] = {"stitches": r["stitches"], "trims": r["trims"], "untrimmed_over_3mm": _long(r),
                        "longest_stitch_mm": r["longest_stitch_mm"], "seconds": r["runtime_s"],
                        "size_mm": r.get("size_mm"), "multi_pass": r.get("multi_pass_columns")}
        if images and out_dir and ours.get("sheet") and theirs.get("sheet"):
            a, b = Path(results_dir) / ours["sheet"], Path(results_dir) / theirs["sheet"]
            if a.exists() and b.exists():
                dest = Path(out_dir) / f"{ours['id']}.png"
                side_by_side(a, b, f"planner  {ours['id']}", f"{theirs['contender']}  {theirs['id']}", dest)
                row["image"] = str(dest)
        rows.append(row)
    ok = [r for r in rows if "planner" in r]
    tot = lambda k, who: sum(r[who][k] for r in ok)
    return {"rows": rows,
            "totals": {who: {k: tot(k, who) for k in ("stitches", "trims", "untrimmed_over_3mm")}
                       | {"longest_stitch_mm": max((r[who]["longest_stitch_mm"] for r in ok), default=0)}
                       for who in ("planner", "contender")},
            "meta": doc.get("meta", {})}


def format_table(res: dict) -> str:
    head = ("case", "stitches", "trims", "float>3", "longest", "time s", "size mm (wxh)")
    cell = lambda a, b: f"{a}|{b}"
    body = []
    for r in res["rows"]:
        if "planner" not in r:
            body.append((r["id"], r.get("note", ""), "", "", "", "", ""))
            continue
        p, c = r["planner"], r["contender"]
        sz = lambda x: "x".join(f"{v:.0f}" for v in x) if x else "?"
        body.append((r["id"], cell(p["stitches"], c["stitches"]), cell(p["trims"], c["trims"]),
                     cell(p["untrimmed_over_3mm"], c["untrimmed_over_3mm"]),
                     cell(f"{p['longest_stitch_mm']:.1f}", f"{c['longest_stitch_mm']:.1f}"),
                     cell(f"{p['seconds']:.0f}", f"{c['seconds']:.1f}"),
                     cell(sz(p["size_mm"]), sz(c["size_mm"]))))
    w = [max(len(str(x[i])) for x in [head, *body]) for i in range(len(head))]
    fmt = lambda row: "  ".join(str(c).ljust(w[i]) if i == 0 else str(c).rjust(w[i]) for i, c in enumerate(row))
    t = res["totals"]
    foot = ["", "each cell: planner|contender",
            "totals  planner: " + json.dumps(t["planner"]) + "   contender: " + json.dumps(t["contender"])]
    return "\n".join([fmt(head), fmt(["-" * x for x in w])] + [fmt(r) for r in body] + foot)
