"""The column planner (src/cli_anything_inkstitch/planner): it imports, plans, and keeps its API.

The planner moved here from stitchpilot (same author), which will import it from this package, so
its public names are pinned: code written against the old modules must keep working. Synthetic
shapes only. The parity test runs only where the original package is installed.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("shapely")
pytest.importorskip("scipy.spatial")

PLANNER = Path(__file__).resolve().parents[2] / "src" / "cli_anything_inkstitch" / "planner"

# Every name the original package's code and tests use from these modules (an AST scan of its src and
# tests at the move, 2026-11-11). Renaming or removing one breaks it.
PUBLIC = {
    "columns": "BUILTIN CAP_DEPTH_MM CAP_OVERHANG_MM Column DEFAULT_SPACING_MM GAP_MIN_WIDTH_MM Letter "
               "PULL_IN_SHARE RING_OVERLAP_MM SPACING_FLOOR_MM Stroke _arc _crossed_pairs _uncross "
               "builtin_strokes check_letter check_letter_order compensate_pull_in edge_spread end_name "
               "find_font_file font_fingerprint glyph_outline open_ends overlap_seams plan_letter_extras "
               "preview_png ring_overlaps sewing_order sewn_closeups spacing_for stitch_file_columns "
               "svg_letter_outlines svg_word_columns word_box word_columns",
    "art_columns": "FLAT_OVERLAP_MM2 MIN_TIP_MM flat_check plan_groups plan_shape preview screen_pairs "
                   "shape_contours_mm text_columns trim_tips",
    "autostrokes": "_smooth_pairs letter_strokes",
    "ring_seam": "object_run recover_ring ring_pairs seam_angle seam_density",
}


def _ring(r_out=10.0, r_in=7.0, n=64):
    return [np.array([(r_out * math.cos(2 * math.pi * i / n), r_out * math.sin(2 * math.pi * i / n)) for i in range(n)]),
            np.array([(r_in * math.cos(-2 * math.pi * i / n), r_in * math.sin(-2 * math.pi * i / n)) for i in range(n)])]


def _bar(w=30.0, h=3.0):
    return [np.array([(0, 0), (w, 0), (w, h), (0, h)], float)]


def test_public_names_are_kept():
    from cli_anything_inkstitch.planner import art_columns, autostrokes, columns, ring_seam
    mods = {"columns": columns, "art_columns": art_columns, "autostrokes": autostrokes, "ring_seam": ring_seam}
    missing = [f"{m}.{n}" for m, names in PUBLIC.items() for n in names.split() if not hasattr(mods[m], n)]
    assert missing == []


def test_self_contained():
    """No imports from the rest of the CLI package: the planner can be split out on its own."""
    for f in PLANNER.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("cli_anything_inkstitch"), f"{f.name}: {node.module}"
                assert not (node.level == 2), f"{f.name}: relative import out of the package"


def test_bar_is_one_column_at_its_width():
    from cli_anything_inkstitch.planner import art_columns as ac
    sc = ac.plan_shape(0, _bar())
    assert sc.ok and sc.letter is not None and len(sc.letter.columns) == 1
    w = sorted(math.dist(a, b) for a, b in sc.letter.columns[0].pairs)
    assert abs(w[len(w) // 2] - 3.0) < 0.3


def test_ring_is_one_closed_column():
    from cli_anything_inkstitch.planner import art_columns as ac
    sc = ac.plan_shape(0, _ring())
    assert sc.ok and len(sc.letter.columns) == 1 and sc.letter.columns[0].closed
    assert sc.check["crossed_pairs"] == 0 and sc.check["sewable_bare_pct"] < 2


def test_strokes_of_an_l():
    from cli_anything_inkstitch.planner.autostrokes import letter_strokes
    ell = [np.array([(0, 0), (12, 0), (12, 3), (3, 3), (3, 20), (0, 20)], float)]
    strokes = letter_strokes(ell, 20.0, overlap_feet=True)
    assert 1 <= len(strokes) <= 2 and all(len(s.path) >= 2 for s in strokes)


def test_same_plan_as_the_original_package():
    """Moving the code changed nothing: same pairs for the same shapes."""
    orig = pytest.importorskip("stitchpilot.art_columns")
    from cli_anything_inkstitch.planner import art_columns as ac
    for shape in (_bar(), _ring(), [np.array([(0, 0), (12, 0), (12, 3), (3, 3), (3, 20), (0, 20)], float)]):
        a, b = ac.plan_shape(0, shape), orig.plan_shape(0, shape)
        assert a.ok == b.ok and len(a.letter.columns) == len(b.letter.columns)
        for ca, cb in zip(a.letter.columns, b.letter.columns):
            assert np.allclose(np.asarray(ca.pairs, float), np.asarray(cb.pairs, float))
