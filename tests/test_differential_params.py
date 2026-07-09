"""Differential stitch-plan tests: prove params actually reach the engine.

The engine fails silent — a wrong-format value produces a valid plan that
just ignores it (the join_style="miter" bug shipped this way). Success
signals prove nothing; these tests drive a param two ways and assert the
plans DIFFER, and that a known-invalid value matches the default's plan
(proving it was ignored). Binary-backed; skipped when Ink/Stitch isn't
installed.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from lxml import etree

from cli_anything_inkstitch.artifact.design_model import (
    apply_edits,
    extract_stitch_blocks,
    stitch_plan_svg,
)
from cli_anything_inkstitch.binary import discover
from cli_anything_inkstitch.project import ProjectFile
from cli_anything_inkstitch.svg.document import sha256_of

pytestmark = pytest.mark.skipif(discover() is None,
                                reason="Ink/Stitch binary not installed")


@pytest.fixture(autouse=True)
def real_schema():
    """conftest isolates the cache dir per test, which falls back to the
    bootstrap schema — but these tests exercise the mined read contracts
    (value_kind, dropdown indexes), so extract the real schema from the
    in-repo engine checkout into the isolated cache."""
    import pathlib
    src = pathlib.Path(__file__).parent.parent / "inkstitch"
    if not (src / "lib" / "elements").exists():
        pytest.skip("inkstitch source checkout not present")
    from cli_anything_inkstitch.schema.extract import extract_schema, write_cache
    write_cache(extract_schema(src))

SVG_TMPL = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     xmlns:xlink="http://www.w3.org/1999/xlink"
     width="40mm" height="40mm" viewBox="0 0 40 40">
  <metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
  {body}
</svg>
"""

FILL_SQUARE = ('<path id="e" d="M5,5 L35,5 L35,35 L5,35 Z" fill="#ff0000" '
               'inkstitch:fill_method="auto_fill" inkstitch:angle="0"/>')

# ring: contour fill exercises join_style / contour_strategy
CONTOUR_RING = ('<path id="e" d="M5,5 L35,5 L35,35 L5,35 Z M12,12 L28,12 '
                'L28,28 L12,28 Z" fill="#ff0000" '
                'inkstitch:fill_method="contour_fill"/>')

SATIN = ('<path id="e" fill="none" stroke="#000" inkstitch:satin_column="True" '
         'd="M8,15 C16,11 24,11 32,15 M8,25 C16,29 24,29 32,25 '
         'M8,14 L8,26 M20,10 L20,30 M32,14 L32,26"/>')


def make_project(tmp_path, body):
    svg = tmp_path / "design.svg"
    svg.write_text(SVG_TMPL.format(body=body))
    proj_path = tmp_path / "design.inkstitch-cli.json"
    proj, _ = ProjectFile.load_or_create(str(proj_path))
    proj.svg_path = str(svg)
    proj.svg_sha256 = sha256_of(svg)
    proj.save()
    return str(proj_path)


def plan_hash(project) -> str:
    blocks = extract_stitch_blocks(stitch_plan_svg(project))
    return hashlib.sha256(json.dumps(blocks).encode()).hexdigest()


def set_param(project, name, value):
    apply_edits(project, [{"op": "set_attr", "id": "e",
                           "name": name, "value": value}])


def write_raw_attr(project, name, value):
    """Bypass validation — for planting known-INVALID values the way old
    sessions did, to prove the engine ignores them."""
    proj = ProjectFile.load(project)
    tree = etree.parse(proj.svg_path)
    elem = tree.getroot().find(".//*[@id='e']")
    elem.set(f"{{http://inkstitch.org/namespace}}{name}", value)
    tree.write(proj.svg_path)
    proj.svg_sha256 = sha256_of(proj.svg_path)
    proj.save()


class TestFillAngleDifferential:
    def test_angle_changes_plan(self, tmp_path):
        p = make_project(tmp_path, FILL_SQUARE)
        a = plan_hash(p)
        set_param(p, "angle", "90")
        assert plan_hash(p) != a


class TestDropdownIndexDifferential:
    def test_join_style_indexes_differ(self, tmp_path):
        # dropdowns store option INDEXES (get_int_param) — 0=Round, 2=Beveled
        p = make_project(tmp_path, CONTOUR_RING)
        set_param(p, "join_style", "0")
        round_plan = plan_hash(p)
        set_param(p, "join_style", "2")
        assert plan_hash(p) != round_plan

    def test_invalid_legacy_value_is_ignored(self, tmp_path):
        # the historical bug: "miter" parses as no int → engine uses the
        # default, byte-identical to writing nothing at all
        p = make_project(tmp_path, CONTOUR_RING)
        default_plan = plan_hash(p)
        write_raw_attr(p, "join_style", "miter")
        assert plan_hash(p) == default_plan


class TestMultiValueDifferential:
    def test_per_side_pull_compensation_differs(self, tmp_path):
        # engine reads a space-separated per-side list (get_split_float_param)
        p = make_project(tmp_path, SATIN)
        base = plan_hash(p)
        set_param(p, "pull_compensation_mm", "1 3")
        asym = plan_hash(p)
        assert asym != base
        set_param(p, "pull_compensation_mm", "3 1")
        assert plan_hash(p) not in (base, asym)   # sides are independent
