"""Case files: declared data, loaded and validated here, turned into SVG / CLI arguments by runner.py.

A case file is JSON:

    {"version": 1, "fabric": "twill", "page_width_mm": 300,
     "fonts":  {"<key>": {"name": "...", "path": "..."}},
     "cases":  [ ... ]}

Paths may start with ~ or $REPO (the source checkout), and a relative path is relative to the
case file. The public file is `cases.json` beside this module; `--cases FILE` adds more (a
private file outside the repo can name client art and fonts).

Text case:   {"id", "kind": "text", "font": <key or path>, "text" | "lines": [...], "height_mm",
              "letter_spacing_pct"?, "line_gap_mm"?, "wght"?, "merge_joins"?, "args"?: [extra CLI args]}
Shapes case: {"id", "kind": "shapes", "svg": <path> | "strokes": [...] | "polys": [...],
              "overrides"?: <path>, "fabric"?, "args"?: [extra CLI args]}
Either:      {"quick"?: bool, "family"?: str, "notes"?: str, "reference_dst"?: <path>}

Synthetic shapes: a stroke is {"pts": [[x, y], ...], "width", "cap"?: flat|round, "join"?: mitre|round}
or {"circle": {"c": [x, y], "r", "n"?}, "width"}; a poly is {"pts": [[x, y], ...]}. All strokes and
polys of a case are unioned into one filled outline (mm, y down).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PUBLIC_CASES = Path(__file__).with_name("cases.json")
REPO_ROOT = Path(__file__).resolve().parents[3]       # <checkout>/src/cli_anything_inkstitch/regress
DEFAULT_FABRIC = "twill"
DEFAULT_PAGE_WIDTH_MM = 300.0


class CaseError(ValueError):
    pass


@dataclass
class Case:
    id: str
    kind: str                       # "text" | "shapes"
    family: str                     # grouping for summaries: font key, or "shape"
    spec: dict[str, Any]
    quick: bool = False
    private: bool = False
    fabric: str = DEFAULT_FABRIC
    page_width_mm: float = DEFAULT_PAGE_WIDTH_MM
    font_path: str | None = None
    font_name: str | None = None
    reference_dst: str | None = None
    skip_reason: str | None = None
    notes: str = ""
    source: str = ""
    versus: bool = False             # half of a planner-versus-contender pair (run with --versus)
    contender: str | None = None     # "boldbobbin": not our planner, see regress/bb.py
    wght: float | None = None
    inputs: list[str] = field(default_factory=list)   # files the case reads (all must exist)


def _expand(p: str, base: Path) -> str:
    p = p.replace("$REPO", str(REPO_ROOT))
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else str((base / p).resolve())


def load_file(path: str | Path, *, private: bool) -> list[Case]:
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise CaseError(f"cannot read case file {path}: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise CaseError(f"{path}: expected an object with a 'cases' list")
    base = path.resolve().parent
    fonts = data.get("fonts") or {}
    fabric = data.get("fabric", DEFAULT_FABRIC)
    page_w = float(data.get("page_width_mm", DEFAULT_PAGE_WIDTH_MM))
    out: list[Case] = []
    for raw in data["cases"]:
        cid, kind = raw.get("id"), raw.get("kind")
        if not cid or kind not in ("text", "shapes"):
            raise CaseError(f"{path}: every case needs an id and kind text|shapes: {raw!r}")
        spec = {k: v for k, v in raw.items() if k not in
                ("id", "kind", "quick", "family", "notes", "reference_dst")}
        case = Case(id=cid, kind=kind, family=raw.get("family", "shape"), spec=spec,
                    quick=bool(raw.get("quick")), private=private,
                    fabric=raw.get("fabric", fabric), page_width_mm=page_w,
                    notes=raw.get("notes", ""), source=str(path))
        if kind == "text":
            _check_text(case, raw, fonts, base)
        else:
            _check_shapes(case, raw, base)
        if raw.get("reference_dst"):
            case.reference_dst = _expand(raw["reference_dst"], base)
            case.inputs.append(case.reference_dst)
        if not case.skip_reason:
            missing = [p for p in case.inputs if not Path(p).exists()]
            if missing:
                case.skip_reason = "missing input: " + ", ".join(missing)
        out.append(case)
    out += _versus_cases(data.get("versus"), fonts, base, fabric, page_w, private, str(path))
    return out


def _versus_cases(v: dict | None, fonts: dict, base: Path, fabric: str, page_w: float,
                  private: bool, source: str) -> list[Case]:
    """Each (font, text) of a `versus` block becomes our planner's case and the contender's."""
    if not v:
        return []
    from cli_anything_inkstitch.regress import bb
    contender = v.get("contender", "boldbobbin")
    out: list[Case] = []
    for f in v["fonts"]:
        for t in v["texts"]:
            h = t["height_mm"]
            cid = f"vs-{f['family']}-{t['slug']}-{h:g}"
            spec = {"lines": [t["text"]], "height_mm": h}
            fnt = fonts[f["font"]]
            path = _expand(fnt["path"], base)
            common = dict(kind="text", family=f["family"], quick=False, private=private,
                          fabric=fabric, page_width_mm=page_w, font_path=path,
                          font_name=fnt.get("name", f["font"]), source=source, versus=True,
                          wght=fnt.get("wght"))
            ours = Case(id=cid, spec=dict(spec, **({"wght": fnt["wght"]} if fnt.get("wght") else {})),
                        inputs=[path], **common)
            theirs = Case(id=f"{cid}@{contender}", spec=dict(spec, bb_font=f["bb_font"]),
                          contender=contender, inputs=[path],
                          notes=f"{contender} {f['bb_font']}", **common)
            if not Path(path).exists():
                ours.skip_reason = theirs.skip_reason = "missing input: " + path
            elif contender == "boldbobbin" and not bb.available():
                theirs.skip_reason = "Bold Bobbin CLI not found (set BOLDBOBBIN_CLI)"
            out += [ours, theirs]
    return out


def _check_text(case: Case, raw: dict, fonts: dict, base: Path) -> None:
    if "text" not in raw and "lines" not in raw:
        raise CaseError(f"{case.id}: a text case needs text or lines")
    if "height_mm" not in raw:
        raise CaseError(f"{case.id}: a text case needs height_mm")
    key = raw.get("font")
    if key in fonts:
        case.font_name = fonts[key].get("name", key)
        case.font_path = _expand(fonts[key]["path"], base)
        case.family = raw.get("family", key)
    elif key:
        case.font_path = _expand(key, base)
        case.font_name = Path(case.font_path).stem
        case.family = raw.get("family", "font")
    else:
        raise CaseError(f"{case.id}: a text case needs a font")
    case.inputs.append(case.font_path)
    if raw.get("lines"):
        case.spec["lines"] = [str(x) for x in raw["lines"]]
    else:
        case.spec["lines"] = [str(raw["text"])]
    case.spec.pop("text", None)


def _check_shapes(case: Case, raw: dict, base: Path) -> None:
    if not any(k in raw for k in ("svg", "strokes", "polys")):
        raise CaseError(f"{case.id}: a shapes case needs svg, strokes or polys")
    if "svg" in raw:
        case.spec["svg"] = _expand(raw["svg"], base)
        case.inputs.append(case.spec["svg"])
    if raw.get("overrides"):
        case.spec["overrides"] = _expand(raw["overrides"], base)
        case.inputs.append(case.spec["overrides"])


def load_cases(extra: list[str] | None = None, *, public: bool = True) -> list[Case]:
    cases = load_file(PUBLIC_CASES, private=False) if public else []
    for p in extra or []:
        cases += load_file(p, private=True)
    seen: set[str] = set()
    for c in cases:
        if c.id in seen:
            raise CaseError(f"duplicate case id {c.id!r}")
        seen.add(c.id)
    return cases


def select(cases: list[Case], *, quick: bool = False, only: list[str] | None = None,
           versus: bool = False) -> list[Case]:
    """The default run leaves out the planner-versus-contender cases; --versus runs only those."""
    cases = [c for c in cases if c.versus == versus]
    if quick:
        cases = [c for c in cases if c.quick]
    if only:
        wanted = set(only)
        unknown = wanted - {c.id for c in cases}
        if unknown:
            raise CaseError("no such case: " + ", ".join(sorted(unknown)))
        cases = [c for c in cases if c.id in wanted]
    return cases


# -- synthetic shape geometry ---------------------------------------------------------------

def shapes_svg(spec: dict, *, margin_mm: float = 12.0, fill: str = "#000000") -> str:
    """The case's strokes and polys, unioned, as a mm-unit SVG (one filled path per polygon)."""
    import math
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union

    parts = []
    for st in spec.get("strokes", []):
        if "circle" in st:
            cx, cy = st["circle"]["c"]
            r, n = st["circle"]["r"], st["circle"].get("n", 72)
            pts = [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
                   for i in range(n + 1)]
        else:
            pts = [tuple(p) for p in st["pts"]]
        cap = {"flat": 2, "round": 1, "square": 3}[st.get("cap", "flat")]
        join = {"round": 1, "mitre": 2, "bevel": 3}[st.get("join", "mitre")]
        parts.append(LineString(pts).buffer(st["width"] / 2, cap_style=cap, join_style=join,
                                            mitre_limit=4.0, quad_segs=16))
    for po in spec.get("polys", []):
        parts.append(Polygon([tuple(p) for p in po["pts"]]).buffer(0))
    shape = unary_union(parts)
    x0, y0, x1, y1 = shape.bounds
    w, h = x1 - x0 + 2 * margin_mm, y1 - y0 + 2 * margin_mm
    ox, oy = margin_mm - x0, margin_mm - y0
    polys = list(shape.geoms) if hasattr(shape, "geoms") else [shape]

    def ring(coords):
        return "M" + " L".join(f"{x + ox:.3f},{y + oy:.3f}" for x, y in list(coords)[:-1]) + " Z"

    ds = [ring(p.exterior.coords) + "".join(" " + ring(i.coords) for i in p.interiors) for p in polys]
    paths = "".join(f'<path id="art{i}" d="{d}" fill="{fill}" fill-rule="evenodd" stroke="none"/>'
                    for i, d in enumerate(ds))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.2f}mm" height="{h:.2f}mm" '
            f'viewBox="0 0 {w:.2f} {h:.2f}">{paths}</svg>')
