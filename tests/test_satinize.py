"""Medial-axis satinization (trace/satinize.py).

Synthetic glyphs encode the v1 celtic-patch failures: a T's crossbar must
survive (dropped by centerline conversion before), terminals must taper
(constant width erased them), and a ring 'O' must produce closed rails
without overshoot (offset rails self-crossed before).
"""

from __future__ import annotations

import math

from cli_anything_inkstitch.trace.satinize import (
    branch_to_rails,
    distance_transform,
    extract_branches,
    rails_to_satin_d,
    satinize_mask,
    skeletonize,
)


def rect_mask(x0, y0, x1, y1):
    return {(x, y) for x in range(x0, x1) for y in range(y0, y1)}


def ring_mask(cx, cy, r_out, r_in):
    m = set()
    for x in range(cx - r_out - 1, cx + r_out + 2):
        for y in range(cy - r_out - 1, cy + r_out + 2):
            d = math.hypot(x - cx, y - cy)
            if r_in <= d <= r_out:
                m.add((x, y))
    return m


class TestSkeleton:
    def test_bar_skeleton_is_a_line(self):
        skel = skeletonize(rect_mask(0, 0, 40, 8))
        ys = {y for _x, y in skel}
        assert len(ys) <= 3          # thin midline, not the full bar
        xs = {x for x, _y in skel}
        assert max(xs) - min(xs) > 30  # spans the bar's length

    def test_distance_transform_measures_half_width(self):
        mask = rect_mask(0, 0, 40, 10)
        dist = distance_transform(mask)
        assert abs(dist[(20, 5)] - 5.0) < 1.5   # middle ≈ half the height


class TestBranches:
    def _t_mask(self):
        # T: horizontal crossbar 30x6 + vertical stem 6x30
        return rect_mask(0, 0, 30, 6) | rect_mask(12, 0, 18, 30)

    def test_crossbar_survives(self):
        mask = self._t_mask()
        skel = skeletonize(mask)
        radii = {p: d for p, d in distance_transform(mask).items() if p in skel}
        branches = extract_branches(skel, radii)
        # stem + both crossbar arms reachable: T must yield >= 2 real
        # branches spanning both the horizontal and vertical extents
        all_pts = [p for b in branches for p in b]
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        assert max(xs) - min(xs) > 20, "crossbar was pruned"
        assert max(ys) - min(ys) > 20, "stem was pruned"

    def test_ring_yields_closed_branch(self):
        mask = ring_mask(30, 30, 20, 12)
        skel = skeletonize(mask)
        radii = {p: d for p, d in distance_transform(mask).items() if p in skel}
        branches = extract_branches(skel, radii)
        closed = [b for b in branches if b[0] == b[-1] and len(b) > 8]
        assert closed, "ring skeleton did not come back as a closed loop"


class TestRails:
    def test_taper_preserved(self):
        # wedge: height shrinks from 12px to 2px over 60px length
        mask = {(x, y) for x in range(60)
                for y in range(-int(6 - x * 5 / 60), int(6 - x * 5 / 60) + 1)}
        pairs = satinize_mask(mask, min_half_width=0.5)
        assert pairs
        rail_a, rail_b, _closed = max(
            pairs, key=lambda p: len(p[0]))
        # rail separation at the wide end must exceed the narrow end
        def sep(i):
            return math.hypot(rail_a[i][0] - rail_b[i][0],
                              rail_a[i][1] - rail_b[i][1])
        wide = max(sep(0), sep(len(rail_a) - 1))
        narrow = min(sep(0), sep(len(rail_a) - 1))
        assert wide > narrow * 1.8, f"taper lost: {wide:.1f} vs {narrow:.1f}"

    def test_ring_rails_do_not_self_cross(self):
        from cli_anything_inkstitch.artifact.gate import poly_self_intersects
        mask = ring_mask(30, 30, 18, 11)
        pairs = satinize_mask(mask)
        assert pairs
        for rail_a, rail_b, closed in pairs:
            assert closed
            assert not poly_self_intersects(rail_a)
            assert not poly_self_intersects(rail_b)


class TestSatinD:
    def test_d_has_rails_and_crossing_rungs(self):
        from cli_anything_inkstitch.artifact.gate import (
            flatten_path,
            segments_intersect,
        )
        mask = rect_mask(0, 0, 60, 8)
        pairs = satinize_mask(mask)
        rail_a, rail_b, _ = max(pairs, key=lambda p: len(p[0]))
        d = rails_to_satin_d(rail_a, rail_b)
        subs = [flatten_path("M " + tok.strip())
                for tok in d.split("M") if tok.strip()]
        assert len(subs) >= 3   # 2 rails + at least one rung
        rails = subs[:2]
        for rung in subs[2:]:
            for rail in rails:
                assert any(
                    segments_intersect(rung[0], rung[-1], rail[i], rail[i+1])
                    for i in range(len(rail) - 1)), "rung misses a rail"

    def test_transform_applied(self):
        d = rails_to_satin_d([(0, 0), (10, 0)], [(0, 4), (10, 4)],
                             transform=(2, 0, 0, 2, 100, 50))
        assert d.startswith("M 100.00,50.00")


class TestFoldRemoval:
    def test_small_fold_cut_out(self):
        from cli_anything_inkstitch.artifact.gate import poly_self_intersects
        from cli_anything_inkstitch.trace.satinize import remove_folds
        # straight line with a small crossing loop in the middle
        pts = [(0, 0), (10, 0), (20, 0), (24, 3), (22, 6), (18, 3),
               (30, 0), (40, 0)]
        assert poly_self_intersects(pts)
        cleaned = remove_folds(pts)
        assert not poly_self_intersects(cleaned)
        # endpoints untouched
        assert cleaned[0] == (0, 0) and cleaned[-1] == (40, 0)

    def test_clean_polyline_untouched(self):
        from cli_anything_inkstitch.trace.satinize import remove_folds
        pts = [(0, 0), (10, 0), (20, 5), (30, 0)]
        assert remove_folds(pts) == pts


class TestSmallLoopSplitting:
    def test_small_ring_becomes_two_open_arcs(self):
        from cli_anything_inkstitch.trace.satinize import satinize_mask
        mask = ring_mask(20, 20, 12, 7)
        pairs = satinize_mask(mask, split_loops_shorter_than=200)
        opens = [p for p in pairs if not p[2]]
        assert len(opens) == 2, f"expected 2 open arcs, got {len(pairs)}"

    def test_large_ring_stays_closed(self):
        from cli_anything_inkstitch.trace.satinize import satinize_mask
        mask = ring_mask(40, 40, 30, 22)
        pairs = satinize_mask(mask, split_loops_shorter_than=50)
        assert any(p[2] for p in pairs)


class TestTerminalCaps:
    def test_open_column_covers_stroke_ends(self):
        from cli_anything_inkstitch.trace.satinize import satinize_mask
        mask = rect_mask(0, 0, 60, 10)
        pairs = satinize_mask(mask)
        rail_a, rail_b, _ = max(pairs, key=lambda p: len(p[0]))
        xs = [p[0] for p in rail_a] + [p[0] for p in rail_b]
        # rails must reach (or slightly pass) both bar ends, not stop a
        # half-width short of them
        assert min(xs) <= 1.5, f"left terminal uncovered: min x {min(xs):.1f}"
        assert max(xs) >= 58.5, f"right terminal uncovered: max x {max(xs):.1f}"


class TestRungDensity:
    def test_rungs_spaced_by_arc_length(self):
        from cli_anything_inkstitch.artifact.gate import flatten_path
        from cli_anything_inkstitch.trace.satinize import rails_to_satin_d
        rail_a = [(float(x), 0.0) for x in range(0, 101, 2)]
        rail_b = [(float(x), 6.0) for x in range(0, 101, 2)]
        d = rails_to_satin_d(rail_a, rail_b, rung_spacing=25.0)
        subs = [flatten_path("M " + t) for t in d.split("M") if t.strip()]
        rungs = subs[2:]
        assert 3 <= len(rungs) <= 5, f"expected ~4 rungs on 100u rail, got {len(rungs)}"

    def test_auto_spacing_is_sparse(self):
        from cli_anything_inkstitch.artifact.gate import flatten_path
        from cli_anything_inkstitch.trace.satinize import rails_to_satin_d
        rail_a = [(float(x), 0.0) for x in range(0, 101, 2)]
        rail_b = [(float(x), 6.0) for x in range(0, 101, 2)]
        d = rails_to_satin_d(rail_a, rail_b)
        rungs = [t for t in d.split("M") if t.strip()][2:]
        # auto = ~8x separation (6u) -> ~2 rungs, never one per sample
        assert len(rungs) < 10


class TestTaperTruncation:
    def test_tail_below_stitchable_width_is_cut(self):
        from cli_anything_inkstitch.trace.satinize import satinize_mask
        # bar whose last third is a single-pixel hairline (radius 1.0,
        # far below the 3.0 stitchable half-width)
        mask = set()
        for x in range(60):
            for y in range(-5, 6):
                mask.add((x, y))
        for x in range(60, 90):
            mask.add((x, 0))
        pairs = satinize_mask(mask, min_half_width=3.0)
        rail_a, rail_b, _ = max(pairs, key=lambda p: len(p[0]))
        max_x = max(p[0] for p in rail_a + rail_b)
        # the hairline must be cut, not floored into a manufactured-width
        # pile (the celtic C's tail failure)
        assert max_x < 80, f"hairline tail not truncated (max x {max_x:.0f})"


class TestBranchMerging:
    def test_straight_through_spur_junction_merges(self):
        from cli_anything_inkstitch.trace.satinize import merge_collinear_branches
        # a long horizontal path broken at a junction + a perpendicular spur
        left = [(x, 0) for x in range(0, 31)]
        right = [(x, 0) for x in range(30, 61)]
        spur = [(30, y) for y in range(0, 8)]
        merged = merge_collinear_branches([left, right, spur])
        lengths = sorted(len(b) for b in merged)
        assert len(merged) == 2, f"expected through-merge, got {len(merged)}"
        assert lengths[-1] >= 60, "through-path was not spliced"

    def test_right_angle_stays_separate(self):
        from cli_anything_inkstitch.trace.satinize import merge_collinear_branches
        horiz = [(x, 0) for x in range(0, 31)]
        vert = [(30, y) for y in range(0, 31)]
        merged = merge_collinear_branches([horiz, vert])
        assert len(merged) == 2, "L-corner must not merge into one column"


class TestRadialRungs:
    def test_rungs_radial_on_concentric_arcs(self):
        import math
        from cli_anything_inkstitch.artifact.gate import flatten_path
        from cli_anything_inkstitch.trace.satinize import rails_to_satin_d
        outer = [(50 * math.cos(a), 50 * math.sin(a))
                 for a in [i * math.pi / 60 for i in range(61)]]
        inner = [(38 * math.cos(a), 38 * math.sin(a))
                 for a in [i * math.pi / 60 for i in range(61)]]
        d = rails_to_satin_d(outer, inner, rung_spacing=30.0)
        rungs = [flatten_path("M " + t) for t in d.split("M") if t.strip()][2:]
        assert len(rungs) >= 3   # curvature adds rungs beyond arc spacing
        for r in rungs:
            (x1, y1), (x2, y2) = r[0], r[-1]
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            radial = math.atan2(my, mx)
            rung_ang = math.atan2(y2 - y1, x2 - x1)
            diff = abs((rung_ang - radial + math.pi/2) % math.pi - math.pi/2)
            assert diff < math.radians(12), \
                f"rung leans {math.degrees(diff):.0f}° off radial"
