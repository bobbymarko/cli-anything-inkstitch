"""The editor's `knife` edit op (artifact/design_model.py _knife).

Op-level tests run without the engine. The differential tests at the bottom
drive the real binary: the cut pieces must stitch where the original did —
the same coverage, nothing outside it — with controls proving the coverage
measurement does fail on a wrong result.
"""

from __future__ import annotations

import math

import pytest
from lxml import etree

from cli_anything_inkstitch.artifact.design_model import (
    apply_edits,
    apply_history_step,
    extract_stitch_blocks,
    read_design,
    stitch_plan_svg,
)
from cli_anything_inkstitch.binary import discover
from cli_anything_inkstitch.errors import UserError
from cli_anything_inkstitch.project import ProjectFile
from cli_anything_inkstitch.svg.document import sha256_of
from cli_anything_inkstitch.svg.knife import fill_area

# 1 user unit = 1 mm
SVG = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     xmlns:xlink="http://www.w3.org/1999/xlink"
     width="60mm" height="40mm" viewBox="0 0 60 40">
  <metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
  <path id="bowl" d="M2,30 L2,12 C8,0 22,0 28,12 L28,30 Z" fill="#f17095"
        inkstitch:fill_method="auto_fill" inkstitch:angle="30"
        inkscape:label="Bowl"/>
  <path id="line" d="M2,36 C20,32 40,40 58,36" fill="none" stroke="#000000"
        inkstitch:running_stitch_length_mm="2"/>
  <path id="col" fill="none" stroke="#208020" inkstitch:satin_column="True"
        d="M34,2 C40,10 40,22 34,30 M40,2 C46,10 46,22 40,30 M34,6 L44,6 M34,26 L44,26"/>
</svg>
"""


def _make(tmp_path, svg=SVG):
    p = tmp_path / "design.svg"
    p.write_text(svg)
    proj_path = tmp_path / "design.inkstitch-cli.json"
    proj, _ = ProjectFile.load_or_create(str(proj_path))
    proj.svg_path = str(p)
    proj.svg_sha256 = sha256_of(p)
    proj.save()
    return str(proj_path)


@pytest.fixture
def project(tmp_path):
    return _make(tmp_path)


def _objs(project):
    return {o["id"]: o for o in read_design(project)["objects"]}


def _history_len(project):
    return ProjectFile.load(project).history["cursor"] + 1


class TestKnifeOp:
    def test_fill_cut_copies_params_and_labels(self, project):
        r = apply_edits(project, [{"op": "knife", "ids": ["bowl"],
                                   "line": [[0, 15], [30, 15]]}])
        res = r["results"][0]
        assert res["cut"] == ["bowl"]
        objs = _objs(project)
        assert "bowl" not in objs
        pieces = [objs[i] for i in res["pieces"]["bowl"]]
        assert len(pieces) == 2
        for p in pieces:
            assert p["kind"] == "fill"
            assert p["params"]["fill_method"] == "auto_fill"
            assert p["params"]["angle"] == "30"
            assert p["fill"] == "#f17095"
        assert [p["label"] for p in pieces] == ["Bowl 1", "Bowl 2"]
        assert sum(fill_area(p["d"]) for p in pieces) == pytest.approx(
            fill_area("M2,30 L2,12 C8,0 22,0 28,12 L28,30 Z"), rel=1e-3)
        # the bowl's curve survives as a curve
        assert any("C" in p["d"] for p in pieces)

    def test_pieces_take_the_originals_place(self, project):
        r = apply_edits(project, [{"op": "knife", "ids": ["bowl"],
                                   "line": [[0, 15], [30, 15]]}])
        order = [o["id"] for o in read_design(project)["objects"]]
        assert order[:2] == r["results"][0]["pieces"]["bowl"]
        assert order[2:] == ["line", "col"]

    def test_run_cut(self, project):
        r = apply_edits(project, [{"op": "knife", "ids": ["line"],
                                   "line": [[30, 0], [30, 40]]}])
        ids = r["results"][0]["pieces"]["line"]
        objs = _objs(project)
        assert len(ids) == 2
        for i in ids:
            assert objs[i]["kind"] == "run"
            assert objs[i]["params"]["running_stitch_length_mm"] == "2"
        # the two runs meet at the cut
        end0 = objs[ids[0]]["d"].split()[-1]
        assert objs[ids[1]]["d"].startswith("M" + end0)

    def test_one_undo_restores_everything(self, project):
        n0 = _history_len(project)
        apply_edits(project, [{"op": "knife", "ids": ["bowl", "line"],
                               "line": [[0, 15], [30, 15], [30, 40]]}])
        assert _history_len(project) == n0 + 1
        assert "bowl" not in _objs(project) and "line" not in _objs(project)
        apply_history_step(project)
        objs = _objs(project)
        assert set(objs) == {"bowl", "line", "col"}
        after = etree.tostring(etree.parse(ProjectFile.load(project).svg_path))
        assert b'id="bowl"' in after
        # redo re-applies the same cut
        apply_history_step(project, redo=True)
        assert "bowl" not in _objs(project)

    def test_miss_is_noop_without_history(self, project):
        n0 = _history_len(project)
        r = apply_edits(project, [{"op": "knife", "line": [[0, 39], [1, 39]]}])
        assert r["results"][0]["cut"] == []
        assert _history_len(project) == n0
        assert set(_objs(project)) == {"bowl", "line", "col"}

    def test_without_ids_cuts_everything_crossed(self, project):
        r = apply_edits(project, [{"op": "knife", "line": [[0, 20], [30, 20]]}])
        assert r["results"][0]["cut"] == ["bowl"]       # run and satin not crossed

    def test_bad_line_rejected(self, project):
        with pytest.raises(UserError):
            apply_edits(project, [{"op": "knife", "line": [[0, 0]]}])

    def test_transformed_element_cut_in_canvas_space(self, tmp_path):
        svg = SVG.replace('<path id="bowl"', '<path id="bowl" transform="translate(10,0)"')
        project = _make(tmp_path, svg)
        # on the canvas the bowl spans x 12..38, so a cut at x=13 shaves a
        # ~1 mm strip; read without the transform it would take 11 mm
        r = apply_edits(project, [{"op": "knife", "ids": ["bowl"],
                                   "line": [[13, -1], [13, 41]]}])
        objs = _objs(project)
        pieces = [objs[i] for i in r["results"][0]["pieces"]["bowl"]]
        assert all(p["transform"] == "translate(10,0)" for p in pieces)
        areas = sorted(fill_area(p["d"]) for p in pieces)
        assert 18 <= areas[0] <= 21          # 1 mm × 18 mm strip + its curved top

    def test_attached_commands_are_detached(self, project):
        apply_edits(project, [{"op": "attach_command", "id": "bowl",
                               "command": "fill_start", "x": 3, "y": 29}])
        r = apply_edits(project, [{"op": "knife", "ids": ["bowl"],
                                   "line": [[0, 15], [30, 15]]}])
        text = open(ProjectFile.load(project).svg_path).read()
        assert "#bowl" not in text                    # no dangling connector
        assert all(not o["commands"] for o in _objs(project).values())
        apply_history_step(project)                   # one undo brings it back
        assert _objs(project)["bowl"]["commands"]
        assert r["results"][0]["cut"] == ["bowl"]


# -- engine-backed --------------------------------------------------------------

def _stitch_segments(project):
    blocks = extract_stitch_blocks(stitch_plan_svg(project))
    segs = []
    for b in blocks["blocks"]:
        for path in b["paths"]:
            segs.extend(zip(path, path[1:]))
    return segs


def _seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    t = 0.0 if ll == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / ll))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def _coverage(segs, probes, reach=0.6):
    """Fraction of probe points with a stitch within ``reach`` mm."""
    hit = 0
    for p in probes:
        if any(_seg_dist(p, a, b) <= reach for a, b in segs):
            hit += 1
    return hit / len(probes)


def _outside(segs, inside_fn):
    """Fraction of needle points that fall outside the region."""
    pts = [a for a, _b in segs] + [segs[-1][1]]
    return sum(1 for p in pts if not inside_fn(p)) / len(pts)


def _only(svg: str, keep: str) -> str:
    """The design with every top-level shape but ``keep`` removed."""
    root = etree.fromstring(svg.encode())
    for e in list(root):
        if etree.QName(e.tag).localname == "path" and e.get("id") != keep:
            root.remove(e)
    return etree.tostring(root).decode()


# probe grid inside the bowl, 1.5 mm in from the edge (underlay inset and
# pull compensation make the outermost rim legitimately uneven)
def _bowl_inside(p, inset=0.0):
    x, y = p
    if not (2 + inset <= x <= 28 - inset and y <= 30 - inset):
        return False
    if y >= 12 + inset:
        return True
    # under the cubic top: compare against the curve's y at this x
    from cli_anything_inkstitch.svg.knife import _seg_point
    c = ("C", (2, 12), (8, 0), (22, 0), (28, 12))
    t = min(range(201), key=lambda k: abs(_seg_point(c, k / 200)[0] - x)) / 200
    return y >= _seg_point(c, t)[1] + inset


BOWL_PROBES = [(x / 2, y / 2) for x in range(4, 57) for y in range(4, 60)
               if _bowl_inside((x / 2, y / 2), inset=1.5)]

# the satin's band (between its rails), probed 1 mm inside each rail
COL_PROBES = []
for _k in range(3, 28):
    _y = float(_k)
    _s = (_y - 2) / 28
    # rails: x = 34 + bulge, 40 + bulge (same cubic shape, 6 mm apart)
    from cli_anything_inkstitch.svg.knife import _seg_point as _sp
    _r0 = ("C", (34, 2), (40, 10), (40, 22), (34, 30))
    _t = min(range(401), key=lambda k: abs(_sp(_r0, k / 400)[1] - _y)) / 400
    _x0 = _sp(_r0, _t)[0]
    for _dx in (1.0, 2.0, 3.0, 4.0, 5.0):
        COL_PROBES.append((_x0 + _dx, _y))


@pytest.mark.skipif(discover() is None, reason="Ink/Stitch binary not installed")
class TestKnifeStitchesLikeTheOriginal:
    """Differential proof: the pieces cover the original's stitch area and
    stay inside it; dropping a piece (control) must fail the same check."""

    def test_fill_pieces_cover_the_original(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        whole = _make(tmp_path / "a", _only(SVG, "bowl"))
        cut = _make(tmp_path / "b", _only(SVG, "bowl"))
        r = apply_edits(cut, [{"op": "knife", "line": [[0, 18], [15, 8], [30, 16]]}])
        pieces = r["results"][0]["pieces"]["bowl"]
        assert len(pieces) == 2

        seg_whole = _stitch_segments(whole)
        seg_cut = _stitch_segments(cut)
        cov_whole = _coverage(seg_whole, BOWL_PROBES)
        cov_cut = _coverage(seg_cut, BOWL_PROBES)
        assert cov_whole > 0.97, cov_whole           # sanity: measurement sees a fill
        assert cov_cut > 0.97, cov_cut
        assert _outside(seg_cut, lambda p: _bowl_inside(p, inset=-1.0)) < 0.01

        # control: drop one piece — the same measurement must now fail
        apply_edits(cut, [{"op": "delete_element", "id": pieces[0]}])
        cov_dropped = _coverage(_stitch_segments(cut), BOWL_PROBES)
        assert cov_dropped < 0.8, cov_dropped

    def test_each_fill_piece_stitches_on_its_own(self, tmp_path):
        project = _make(tmp_path, _only(SVG, "bowl"))
        r = apply_edits(project, [{"op": "knife", "line": [[0, 18], [30, 16]]}])
        for pid in r["results"][0]["pieces"]["bowl"]:
            other = [i for i in r["results"][0]["pieces"]["bowl"] if i != pid]
            apply_edits(project, [{"op": "delete_element", "id": o} for o in other])
            segs = _stitch_segments(project)
            assert len(segs) > 50, pid
            apply_history_step(project)              # restore the other piece

    def test_satin_pieces_cover_the_original(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        whole = _make(tmp_path / "a", _only(SVG, "col"))
        cut = _make(tmp_path / "b", _only(SVG, "col"))
        r = apply_edits(cut, [{"op": "knife", "line": [[30, 15], [50, 17]]}])
        pieces = r["results"][0]["pieces"]["col"]
        assert len(pieces) == 2
        objs = _objs(cut)
        for pid in pieces:
            o = objs[pid]
            assert o["kind"] == "satin"
            assert len(o["rails"]) == 2 and len(o["rungs"]) >= 1

        cov_whole = _coverage(_stitch_segments(whole), COL_PROBES)
        cov_cut = _coverage(_stitch_segments(cut), COL_PROBES)
        assert cov_whole > 0.97, cov_whole
        assert cov_cut > 0.97, cov_cut

        apply_edits(cut, [{"op": "delete_element", "id": pieces[1]}])
        cov_dropped = _coverage(_stitch_segments(cut), COL_PROBES)
        assert cov_dropped < 0.8, cov_dropped
