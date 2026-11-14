"""Engine-stitch gate: judge the stitches Ink/Stitch will actually sew.

The static checks in `gate.py` read the design's geometry. That cannot see what
the engine does with it: a model of the stitching said every satin column was
one pass, while the engine split columns into two passes that meet mid-column
(`end_at_nearest_point`, on by default) -- a visible seam. These checks run the
real binary and read its output instead:

  * the stitch-plan preview SVG (every needle penetration, in design units, in
    stitch order) -> per-satin-column top-layer pass count
  * the CSV command stream (the only place TRIM exists) -> untrimmed travel
    moves, longest stitch, stitch / trim totals

One engine pass costs ~25 s per call on a large design, so the analysis is cached
on disk keyed by the SVG's sha256. Every binary run takes the shared render lock
so renders from other processes never overlap (8 GB shared host).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# Bump when the analysis changes so stale cache entries are ignored.
ENGINE_CHECK_VERSION = 2

# -- thresholds (mm) -----------------------------------------------------------

# An untrimmed needle-up move leaves a thread float between the two points. Short
# ones are hidden under later stitching; ~3 mm is the engine's own collapse
# length (default collapse_len_mm), below which it does not bother to trim.
TRAVEL_WARN_MM = 3.0
# Past ~15 mm a float snags, shows through the fabric, and gets cut by hand.
TRAVEL_ERROR_MM = 15.0
# Machines handle 12.1 mm stitches (DST limit), but anything much past ~8 mm
# on a satin/fill is a loose loop that snags; the engine splits at max length,
# so a longer stitch means a jump-turned-stitch or a mis-set max_stitch_length.
LONGEST_STITCH_WARN_MM = 8.0

# -- satin pass counting -------------------------------------------------------

# A stitch belongs to a column's zigzag when it moves across most of the width:
# side = (dL - dR) / (dL + dR) runs -1 (left rail) .. +1 (right rail), and a
# zigzag stitch changes it by ~2 (or ~1 per half when the engine splits a wide
# stitch at the centre). Edge-walk and centre underlay change it by ~0. This
# beats the raw "angle to the centreline" test: in a fan or a tight curve the
# zigzag is far from perpendicular to the centreline.
_CROSS_MIN = 0.8
# How far outside the rail polygon a stitch may land (pull compensation).
_PULL_MARGIN_MM = 0.4
# Top-layer satin runs ~4 stitches/mm; zigzag underlay is ~0.3/mm. Anything
# under this density per mm of progress is underlay, not a top-layer pass.
_TOP_DENSITY_MIN = 3.0
_RUN_MIN_STITCHES = 8
_RUN_MIN_EXTENT_MM = 1.0
_FAN_EXTENT_WIDTHS = 1.0
# Consecutive zigzag stitches of one pass are adjacent in stitch order and a
# short way apart along the column. The along-column allowance grows with the
# local width because, on a curve, a stitch on the inner rail projects onto the
# centreline with a spread of up to ~half the width.
_RUN_SEQ_GAP = 3
_RUN_STEP_MM = 1.5
# A forward continuation of the same direction after a small stitch-order gap
# (a trim, a tie, another element sewn in between) is still one pass.
_MERGE_SEQ_GAP = 80
_MERGE_BACKTRACK_MM = 1.0

CACHE_ENV = "CLI_ANYTHING_INKSTITCH_CACHE"
LOCK_ENV = "INKSTITCH_RENDER_LOCK"
DEFAULT_LOCK = "/tmp/inkstitch-render.lock"


class EngineCheckUnavailable(RuntimeError):
    """The engine checks cannot run here (no binary, or shapely missing)."""


# -- render lock -----------------------------------------------------------------

@contextmanager
def render_lock():
    """Exclusive host-wide lock around any Ink/Stitch binary run."""
    path = os.environ.get(LOCK_ENV, DEFAULT_LOCK)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# -- engine output -----------------------------------------------------------------

_CSV_STITCH = re.compile(r'^"\*","\d+","(\w+)[^"]*","(-?[\d.eE+-]+)","(-?[\d.eE+-]+)"', re.M)


def parse_csv_stream(text: str) -> list[tuple[str, float, float]]:
    """(command, x_mm, y_mm) per CSV stitch row. CSV units are 1/10 mm."""
    return [(m.group(1), float(m.group(2)) / 10.0, float(m.group(3)) / 10.0)
            for m in _CSV_STITCH.finditer(text)]


def stream_from_dst(path: str) -> list[tuple[str, float, float]]:
    """Same shape as parse_csv_stream, read from a machine file (reference compare)."""
    import pyembroidery as pe
    names = {pe.STITCH: "STITCH", pe.JUMP: "JUMP", pe.TRIM: "TRIM",
             pe.COLOR_CHANGE: "COLOR_CHANGE", pe.END: "END"}
    out = []
    for x, y, c in pe.read(path).stitches:
        out.append((names.get(c & pe.COMMAND_MASK, "OTHER"), x / 10.0, y / 10.0))
    return out


def stream_stats(stream: list[tuple[str, float, float]]) -> dict[str, Any]:
    """Totals, longest stitch and untrimmed travel moves from a command stream.

    A travel move is a run of consecutive JUMPs. It is "cut" when the command
    just before it is TRIM or COLOR_CHANGE. The first move (machine origin to
    the first stitch) leaves no float -- no thread is laid before the first
    stitch -- so it is reported as `entry_jump_mm`, not as an untrimmed move.
    """
    stitches = trims = 0
    longest = 0.0
    longest_at: list[float] | None = None
    moves: list[dict[str, Any]] = []     # {"cut": bool, "mm": float, "at": [x, y]}
    prev: tuple[float, float] | None = None
    last_cmd = None
    in_jump = False
    for cmd, x, y in stream:
        if cmd == "JUMP":
            # the first move starts at the machine origin (the design centre)
            d = math.hypot(x - prev[0], y - prev[1]) if prev else math.hypot(x, y)
            if not in_jump:
                moves.append({"cut": last_cmd in ("TRIM", "COLOR_CHANGE"),
                              "mm": d, "at": [round(x, 2), round(y, 2)],
                              "first": prev is None})
            else:
                moves[-1]["mm"] += d
            in_jump = True
        else:
            in_jump = False
            if cmd == "STITCH":
                stitches += 1
                if last_cmd == "STITCH" and prev is not None:
                    d = math.hypot(x - prev[0], y - prev[1])
                    if d > longest:
                        longest, longest_at = d, [round(x, 2), round(y, 2)]
            elif cmd == "TRIM":
                trims += 1
        last_cmd = cmd
        if cmd in ("STITCH", "JUMP", "TRIM"):    # colour-change/end rows carry no position
            prev = (x, y)
    entry = next((m for m in moves if m["first"]), None)
    untrimmed = sorted(
        (m for m in moves if not m["cut"] and not m["first"]),
        key=lambda m: -m["mm"])
    return {"stitches": stitches, "trims": trims,
            "entry_jump_mm": round(entry["mm"], 1) if entry else 0.0,
            "longest_stitch_mm": round(longest, 2), "longest_stitch_at": longest_at,
            "untrimmed_moves": [{"mm": round(m["mm"], 1), "at": m["at"]}
                                for m in untrimmed]}


# -- per-column pass counting ----------------------------------------------------------

def _dist_to_poly(pt, pts) -> float:
    best = float("inf")
    px, py = pt
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        dx, dy = bx - ax, by - ay
        n = dx * dx + dy * dy
        t = 0.0 if n == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / n))
        d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        if d < best:
            best = d
    return best


class Column:
    """A satin column's rails, a centreline, and the zigzag stitches found on it."""

    def __init__(self, ident: str, left, right, uu_per_mm: float,
                 shape: str | None = None):
        from shapely.geometry import LineString, Polygon
        self.id = ident
        self.shape = shape
        self.uu = uu_per_mm
        self.left, self.right = list(left), list(right)
        ls, rs = LineString(self.left), LineString(self.right)
        n = 80
        self.cl_pts = [
            (0.5 * (ls.interpolate(i / n, normalized=True).x + rs.interpolate(i / n, normalized=True).x),
             0.5 * (ls.interpolate(i / n, normalized=True).y + rs.interpolate(i / n, normalized=True).y))
            for i in range(n + 1)]
        self.cl = LineString(self.cl_pts)
        poly = Polygon(self.left + self.right[::-1])
        poly = poly if poly.is_valid else poly.buffer(0)
        self.poly = poly.buffer(_PULL_MARGIN_MM * uu_per_mm)
        self.length_mm = self.cl.length / uu_per_mm
        self.zigzag: list[tuple[int, float, float]] = []   # (seq, t_mm, width_mm)

    def side(self, p) -> float | None:
        """-1 on the left rail .. +1 on the right rail, 0 on the centreline."""
        dl = _dist_to_poly(p, self.left)
        dr = _dist_to_poly(p, self.right)
        return None if dl + dr <= 0 else (dl - dr) / (dl + dr)

    def crossing(self, a, b) -> tuple[float, float] | None:
        """(t_mm, width_mm) when segment a-b sweeps across this column.

        Judged by how far the segment moves ACROSS the column (change in side),
        not by its angle to the centreline: a fan or tight curve skews the angle
        but a zigzag stitch still goes rail-ward. Handles wide columns where the
        engine splits one zigzag stitch into two halves through the centre.
        """
        sa, sb = self.side(a), self.side(b)
        if sa is None or sb is None or abs(sa - sb) < _CROSS_MIN:
            return None
        from shapely.geometry import Point
        mid = Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        w = (_dist_to_poly(a, self.left) + _dist_to_poly(a, self.right)
             + _dist_to_poly(b, self.left) + _dist_to_poly(b, self.right)) / 2
        return self.cl.project(mid) / self.uu, w / self.uu


def _split_at_reversals(run, widths):
    """Cut a run where progress along the column turns round.

    Underlay often runs out and back with no break in stitch order, so a single
    run can hold an underlay pass and the top-layer pass the other way. A turn
    counts only when progress retreats by more than a column width (and 1.5 mm)
    from its furthest point: end-of-column fans jitter by about a width without
    having turned.
    """
    pieces = []
    start = 0
    direction = 0
    ext = 0
    i = 0
    while i < len(run):
        t = run[i][1]
        tol = max(_RUN_STEP_MM, widths[run[i][0]])
        if direction == 0:
            if abs(t - run[start][1]) > tol:
                direction = 1 if t > run[start][1] else -1
                ext = i
        else:
            if direction * (t - run[ext][1]) > 0:
                ext = i
            elif direction * (run[ext][1] - t) > tol:
                pieces.append(run[start:ext + 1])
                start = ext + 1
                direction = -direction
                ext = min(range(start, i + 1),
                          key=lambda k: -direction * run[k][1])
        i += 1
    pieces.append(run[start:])
    return [p for p in pieces if p]


def count_passes(zigzag: list[tuple[int, float, float]]) -> list[list[tuple[int, float]]]:
    """Group a column's zigzag stitches (seq, t_mm, width_mm) into top-layer passes."""
    runs: list[list[tuple[int, float]]] = []
    cur: list[tuple[int, float]] = []
    widths: dict[int, float] = {}
    for seq, t, w in zigzag:
        widths[seq] = w
        if cur:
            ps, pt = cur[-1]
            if seq - ps <= _RUN_SEQ_GAP and abs(t - pt) < _RUN_STEP_MM + 0.5 * w:
                cur.append((seq, t))
                continue
            runs.append(cur)
            cur = []
        cur.append((seq, t))
    if cur:
        runs.append(cur)
    runs = [piece for r in runs for piece in _split_at_reversals(r, widths)]

    def dense(r):
        ext = max(t for _, t in r) - min(t for _, t in r)
        mean_w = sum(widths[q] for q, _ in r) / len(r)
        # A run shorter than the column is wide is the end-fan, where the
        # projection onto the centreline jitters by about a width -- not a pass.
        return (len(r) >= _RUN_MIN_STITCHES
                and ext >= max(_RUN_MIN_EXTENT_MM, _FAN_EXTENT_WIDTHS * mean_w)
                and len(r) / ext >= _TOP_DENSITY_MIN)

    runs = [r for r in runs if dense(r)]
    merged: list[list[tuple[int, float]]] = []
    for r in runs:
        if merged:
            m = merged[-1]
            d1, d2 = m[-1][1] - m[0][1], r[-1][1] - r[0][1]
            same = (d1 >= 0) == (d2 >= 0)
            sign = 1 if d1 >= 0 else -1
            onward = (r[0][1] - m[-1][1]) * sign > -_MERGE_BACKTRACK_MM
            if same and onward and r[0][0] - m[-1][0] <= _MERGE_SEQ_GAP:
                merged[-1] = m + r
                continue
        merged.append(r)
    return merged


def analyze_passes(columns: list[Column], paths: list[list[list[float]]]) -> list[dict]:
    """Attribute every plan segment to a column and count passes per column.

    `paths` are the plan's stitch polylines (design units, stitch order). The
    geometry is batched through shapely/numpy (a real design has ~24k segments
    and ~75 columns); only the final choice between overlapping columns is a
    sequential Python loop.
    """
    import numpy as np
    import shapely
    from shapely import STRtree

    seg_a, seg_b = [], []
    for path in paths:
        for a, b in zip(path, path[1:]):
            seg_a.append(a)
            seg_b.append(b)
    if not seg_a or not columns:
        return [_column_row(c, []) for c in columns]
    pa = np.asarray(seg_a, dtype=float)
    pb = np.asarray(seg_b, dtype=float)
    mid = (pa + pb) / 2
    mid_pts = shapely.points(mid)
    a_pts, b_pts = shapely.points(pa), shapely.points(pb)

    tree = STRtree([c.poly for c in columns])
    seg_idx, col_idx = tree.query(mid_pts, predicate="intersects")
    hits: dict[int, dict[int, tuple[float, float]]] = {}
    for ci, col in enumerate(columns):
        sel = seg_idx[col_idx == ci]
        if sel.size == 0:
            continue
        L = shapely.linestrings(np.asarray(col.left, dtype=float))
        R = shapely.linestrings(np.asarray(col.right, dtype=float))
        dla, dra = shapely.distance(a_pts[sel], L), shapely.distance(a_pts[sel], R)
        dlb, drb = shapely.distance(b_pts[sel], L), shapely.distance(b_pts[sel], R)
        with np.errstate(divide="ignore", invalid="ignore"):
            sa = (dla - dra) / (dla + dra)
            sb = (dlb - drb) / (dlb + drb)
        cross = np.abs(sa - sb) >= _CROSS_MIN
        t = shapely.line_locate_point(col.cl, mid_pts[sel]) / col.uu
        w = (dla + dra + dlb + drb) / 2 / col.uu
        for k in np.nonzero(cross)[0]:
            hits.setdefault(int(sel[k]), {})[ci] = (float(t[k]), float(w[k]))

    zig: list[list[tuple[int, float, float]]] = [[] for _ in columns]
    last = -1
    for seg in sorted(hits):
        cands = hits[seg]
        # prefer the column we were just sewing: adjacent columns overlap
        ci = last if last in cands else min(cands)
        zig[ci].append((seg + 1, *cands[ci]))
        last = ci
    return [_column_row(c, z) for c, z in zip(columns, zig)]


def _column_row(col: "Column", zigzag) -> dict:
    passes = count_passes(zigzag)
    return {"id": col.id, "shape": col.shape, "len_mm": round(col.length_mm, 1),
            "passes": len(passes),
            "runs": [[round(r[0][1], 1), round(r[-1][1], 1), len(r)] for r in passes]}


# -- findings --------------------------------------------------------------------------

def _finding(severity, obj, check, message, **data):
    return {"severity": severity, "object": obj, "check": check,
            "message": message, "source": "engine", **data}


def findings_from(analysis: dict[str, Any]) -> list[dict]:
    out: list[dict] = []
    for col in analysis["columns"]:
        if col["passes"] > 1:
            spans = ", ".join(f"{a}-{b} mm" for a, b, _ in col["runs"])
            out.append(_finding(
                "error", col["id"], "satin_multi_pass",
                f"the engine sews this {col['len_mm']} mm satin column in "
                f"{col['passes']} separate top-layer passes ({spans}) -- a visible "
                "seam where they meet. Usually end_at_nearest_point (the engine "
                "default) splitting the column; set it false, or set an explicit "
                "ending point",
                passes=col["passes"], runs=col["runs"], len_mm=col["len_mm"]))
        elif col["passes"] == 0 and col["len_mm"] >= _RUN_MIN_EXTENT_MM:
            out.append(_finding(
                "warning", col["id"], "satin_pass_unmeasured",
                "no top-layer zigzag found in the engine stitch plan for this "
                "satin column, so its pass count could not be checked",
                len_mm=col["len_mm"]))
    for mv in analysis["stats"]["untrimmed_moves"]:
        if mv["mm"] >= TRAVEL_ERROR_MM:
            sev = "error"
        elif mv["mm"] >= TRAVEL_WARN_MM:
            sev = "warning"
        else:
            continue
        out.append(_finding(
            sev, "design", "untrimmed_travel",
            f"{mv['mm']} mm needle-up move with no trim before it -- the thread "
            f"floats across the fabric (near {mv['at'][0]}, {mv['at'][1]} mm from "
            "the design centre). Add a trim or reorder so the next element is "
            "closer", mm=mv["mm"], at=mv["at"]))
    longest = analysis["stats"]["longest_stitch_mm"]
    if longest > LONGEST_STITCH_WARN_MM:
        out.append(_finding(
            "warning", "design", "long_stitch",
            f"longest stitch is {longest} mm (limit {LONGEST_STITCH_WARN_MM} mm) "
            f"near {analysis['stats']['longest_stitch_at']}",
            mm=longest, at=analysis["stats"]["longest_stitch_at"]))
    return out


def summary_of(analysis: dict[str, Any]) -> dict[str, Any]:
    cols = analysis["columns"]
    st = analysis["stats"]
    return {
        "stitches": st["stitches"], "trims": st["trims"],
        "satin_columns": len(cols),
        "single_pass_columns": sum(1 for c in cols if c["passes"] == 1),
        "multi_pass_columns": sum(1 for c in cols if c["passes"] > 1),
        "untrimmed_moves": len(st["untrimmed_moves"]),
        "untrimmed_mm": [m["mm"] for m in st["untrimmed_moves"]],
        "entry_jump_mm": st.get("entry_jump_mm", 0.0),
        "longest_stitch_mm": st["longest_stitch_mm"],
    }


# -- running the engine ----------------------------------------------------------------------

def _cache_dir() -> Path:
    env = os.environ.get(CACHE_ENV)
    base = Path(env) if env else Path(os.environ.get(
        "XDG_CACHE_HOME", Path.home() / ".cache")) / "cli-anything-inkstitch"
    return base / "engine-gate"


def _cache_path(sha: str, binary_version: str | None) -> Path:
    key = hashlib.sha256(
        f"{sha}|{binary_version}|{ENGINE_CHECK_VERSION}".encode()).hexdigest()[:32]
    return _cache_dir() / f"{key}.json"


def _satin_columns(project_file: str):
    from lxml import etree
    from cli_anything_inkstitch.artifact import design_model as dm
    from cli_anything_inkstitch.artifact.gate import flatten_path
    from cli_anything_inkstitch.svg.elements import classify
    from cli_anything_inkstitch.svg.geometry import apply_matrix, ctm_for

    cols = []
    with dm._open_locked(project_file) as (_proj, tree):
        root = tree.getroot()
        uu = dm._uu_per_mm(root)
        for e in root.iter():
            if not isinstance(e.tag, str) or etree.QName(e.tag).localname not in dm._SHAPE_TAGS:
                continue
            if dm._is_command_use(e) or dm._is_command_plumbing(e) or not e.get("id") or not e.get("d"):
                continue
            if dm._kind_of(e, classify(e)) != "satin":
                continue
            subs = dm.split_subpaths(e.get("d"))
            if len(subs) < 2:
                continue
            m = ctm_for(e)
            rails = [[apply_matrix(m, x, y) for x, y in flatten_path(s, per_curve=24)]
                     for s in subs[:2]]
            if min(len(r) for r in rails) < 2:
                continue
            cols.append((e.get("id"), dm._shape_id_for(e), rails[0], rails[1]))
    return cols, uu


def run_engine(project_file: str, *, binary: str | None = None) -> dict[str, Any]:
    """Run the real engine (under the render lock) and analyse its output.

    Uncached; see `engine_analysis` for the cached entry point.
    """
    try:
        import shapely  # noqa: F401
    except ImportError as e:
        raise EngineCheckUnavailable(
            "engine checks need shapely (pip install 'cli-anything-inkstitch[satin]')") from e
    from cli_anything_inkstitch.artifact import design_model as dm
    from cli_anything_inkstitch.binary import discover, require, run_extension
    from cli_anything_inkstitch.errors import CLIError

    try:
        dm.ensure_prepped(project_file)
        with dm._open_locked(project_file) as (proj, _t):
            exe = require(binary, proj.session)
            svg_path = proj.svg_path
        columns_raw, uu = _satin_columns(project_file)
        with render_lock():
            plan = dm.stitch_plan_svg(project_file, binary_override=binary)
            csv = run_extension(exe, "output", svg_path, args={"format": "csv"},
                                capture_stdout=True) or b""
    except CLIError as e:
        raise EngineCheckUnavailable(str(e)) from e
    blocks = dm.extract_stitch_blocks(plan)["blocks"]
    paths = [p for b in blocks for p in b["paths"]]
    columns = [Column(i, left, right, uu, shape) for i, shape, left, right in columns_raw]
    return {
        "columns": analyze_passes(columns, paths) if columns else [],
        "stats": stream_stats(parse_csv_stream(csv.decode("utf-8", "replace"))),
    }


def engine_analysis(project_file: str, *, binary: str | None = None,
                    use_cache: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    """Cached engine analysis -> (analysis, meta{cached, sha256, engine_seconds})."""
    import time
    from cli_anything_inkstitch.artifact import design_model as dm
    from cli_anything_inkstitch.binary import detect_binary_version, discover
    from cli_anything_inkstitch.svg.document import sha256_of

    with dm._open_locked(project_file) as (proj, _t):
        sha = sha256_of(proj.svg_path)
        exe = discover(binary, proj.session)
    version = detect_binary_version(exe)
    path = _cache_path(sha, version)
    if use_cache and path.exists():
        try:
            return json.loads(path.read_text()), {"cached": True, "svg_sha256": sha,
                                                  "engine_seconds": 0.0}
        except (OSError, ValueError):
            pass
    t0 = time.monotonic()
    analysis = run_engine(project_file, binary=binary)
    took = round(time.monotonic() - t0, 1)
    # The preview step may stamp the SVG (ensure_prepped), changing its hash; key
    # the entry on the pre-run hash AND the post-run one so the next call hits.
    try:
        _cache_dir().mkdir(parents=True, exist_ok=True)
        with dm._open_locked(project_file) as (proj, _t):
            sha_after = sha256_of(proj.svg_path)
        for s in {sha, sha_after}:
            p = _cache_path(s, version)
            tmp = p.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(analysis))
            os.replace(tmp, p)
    except OSError:
        pass
    return analysis, {"cached": False, "svg_sha256": sha, "engine_seconds": took}


def compare_reference(analysis: dict[str, Any], reference_dst: str) -> dict[str, Any]:
    ref = stream_stats(stream_from_dst(reference_dst))
    cur = analysis["stats"]
    keys = ("stitches", "trims")
    out = {"reference": reference_dst.rsplit("/", 1)[-1],
           "reference_untrimmed_moves": len(ref["untrimmed_moves"]),
           "current_untrimmed_moves": len(cur["untrimmed_moves"]),
           "reference_entry_jump_mm": ref["entry_jump_mm"],
           "current_entry_jump_mm": cur.get("entry_jump_mm", 0.0),
           "reference_longest_stitch_mm": ref["longest_stitch_mm"],
           "current_longest_stitch_mm": cur["longest_stitch_mm"]}
    for k in keys:
        out[f"reference_{k}"] = ref[k]
        out[f"current_{k}"] = cur[k]
        out[f"delta_{k}"] = cur[k] - ref[k]
    return out


def run_engine_checks(project_file: str, *, binary: str | None = None,
                      use_cache: bool = True,
                      reference_dst: str | None = None) -> dict[str, Any]:
    """Engine findings + totals for the gate. Never raises: an engine that cannot
    run is reported as an error finding so a delivery path that requires the
    checks refuses to proceed."""
    try:
        analysis, meta = engine_analysis(project_file, binary=binary, use_cache=use_cache)
    except EngineCheckUnavailable as e:
        return {"findings": [_finding("error", "design", "engine_unavailable",
                                      f"engine checks could not run: {e}")],
                "engine": None}
    result = {"findings": findings_from(analysis),
              "engine": {**summary_of(analysis), **meta}}
    if reference_dst:
        try:
            result["engine"]["reference"] = compare_reference(analysis, reference_dst)
        except Exception as e:  # unreadable reference must not hide the gate result
            result["findings"].append(_finding(
                "warning", "design", "reference_unreadable",
                f"could not read reference file: {e}"))
    return result
