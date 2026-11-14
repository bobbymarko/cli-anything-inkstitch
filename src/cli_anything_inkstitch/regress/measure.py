"""One engine run per case, through engine_gate, keeping what the contact sheet needs.

`engine_gate.run_engine` returns only the analysis (per-column passes, stream totals). The
contact sheet also needs the stitch stream itself and the column rails, and the suite must not
read an analysis cached from an earlier planner, so this repeats run_engine's few steps with
the same engine_gate pieces (render lock, satin columns, pass analysis, CSV stats) and keeps
the stream. It takes the render lock exactly as run_engine does; callers must not hold it.
"""

from __future__ import annotations

from typing import Any

from cli_anything_inkstitch.artifact import design_model as dm
from cli_anything_inkstitch.artifact import engine_gate as eg


def measure(project_file: str, *, binary: str | None = None) -> dict[str, Any]:
    from cli_anything_inkstitch.binary import require, run_extension
    from cli_anything_inkstitch.errors import CLIError

    try:
        dm.ensure_prepped(project_file)
        with dm._open_locked(project_file) as (proj, _t):
            exe = require(binary, proj.session)
            svg_path = proj.svg_path
        columns_raw, uu = eg._satin_columns(project_file)
        with eg.render_lock():
            plan = dm.stitch_plan_svg(project_file, binary_override=binary)
            csv = run_extension(exe, "output", svg_path, args={"format": "csv"},
                                capture_stdout=True) or b""
    except CLIError as e:
        raise eg.EngineCheckUnavailable(str(e)) from e
    blocks = dm.extract_stitch_blocks(plan)["blocks"]
    paths = [p for b in blocks for p in b["paths"]]
    columns = [eg.Column(i, left, right, uu, shape) for i, shape, left, right in columns_raw]
    stream = eg.parse_csv_stream(csv.decode("utf-8", "replace"))
    analysis = {"columns": eg.analyze_passes(columns, paths) if columns else [],
                "stats": eg.stream_stats(stream)}
    # CSV coordinates are centred on the design; the columns' rails are in the page frame.
    # Both are y-down, so one translation maps rails into the stream's frame (mm).
    pts = [q for p in paths for q in p]
    stitched = [(x, y) for c, x, y in stream if c == "STITCH"]
    offset = (0.0, 0.0)
    if pts and stitched:
        px = [p[0] for p in pts], [p[1] for p in pts]
        sx = [p[0] for p in stitched], [p[1] for p in stitched]
        offset = ((min(px[0]) + max(px[0])) / 2 / uu - (min(sx[0]) + max(sx[0])) / 2,
                  (min(px[1]) + max(px[1])) / 2 / uu - (min(sx[1]) + max(sx[1])) / 2)
    rails = {i: ([(x / uu - offset[0], y / uu - offset[1]) for x, y in left],
                 [(x / uu - offset[0], y / uu - offset[1]) for x, y in right])
             for i, _shape, left, right in columns_raw}
    return {"analysis": analysis, "stream": stream, "rails": rails,
            "groups": _letter_groups(svg_path)}


def _letter_groups(svg_path: str) -> dict[str, str]:
    """element id -> id of its text-to-satin letter group (text0_l3). The engine's auto_satin
    renames routed columns (autosatin1630), so a column is placed by its ancestor, not its id."""
    import re
    from lxml import etree
    pat = re.compile(r"^text\d+_l\d+$")
    out: dict[str, str] = {}
    for e in etree.parse(svg_path).getroot().iter():
        if not isinstance(e.tag, str) or not e.get("id"):
            continue
        a = e
        while a is not None and not (a.get("id") and pat.match(a.get("id"))):
            a = a.getparent()
        if a is not None:
            out[e.get("id")] = a.get("id")
    return out
