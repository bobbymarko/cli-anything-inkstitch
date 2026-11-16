"""Engine-stitch gate: pass counting, travel stats, caching, wiring.

The pass counter is tested on synthetic stitch plans (no binary): the shapes a
real engine plan produces -- one pass, a split column, underlay out-and-back,
fans, tight curves, wide columns split at the centre. One end-to-end test runs
the real binary when it is installed.
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("shapely")

from cli_anything_inkstitch.artifact import engine_gate as eg  # noqa: E402
from cli_anything_inkstitch.artifact.gate import require_gate, run_gate  # noqa: E402
from cli_anything_inkstitch.binary import discover  # noqa: E402
from cli_anything_inkstitch.errors import UserError  # noqa: E402

from tests.test_artifact_gate import GOOD_SATIN, make_project  # noqa: E402


# -- synthetic columns ---------------------------------------------------------

def straight(length=40.0, width=4.0, n=40):
    left = [(i * length / n, 0.0) for i in range(n + 1)]
    right = [(i * length / n, width) for i in range(n + 1)]
    return left, right


def fan(length=40.0, w0=2.0, w1=12.0, n=40):
    left = [(i * length / n, 0.0) for i in range(n + 1)]
    right = [(i * length / n, w0 + (w1 - w0) * i / n) for i in range(n + 1)]
    return left, right


def arc(radius=5.0, width=3.0, sweep=math.pi * 1.5, n=60):
    """A tight curve: centre radius 5 mm, 3 mm wide, 270 degrees."""
    def p(r, k):
        a = sweep * k / n
        return (r * math.cos(a), r * math.sin(a))
    return ([p(radius - width / 2, k) for k in range(n + 1)],
            [p(radius + width / 2, k) for k in range(n + 1)])


def column(rails):
    return eg.Column("c", rails[0], rails[1], 1.0, "shape1")


def at(rails, u):
    """Rail points at fraction u of the column."""
    def pick(r):
        f = u * (len(r) - 1)
        i = min(int(f), len(r) - 2)
        k = f - i
        return (r[i][0] + k * (r[i + 1][0] - r[i][0]), r[i][1] + k * (r[i + 1][1] - r[i][1]))
    return pick(rails[0]), pick(rails[1])


def zigzag(rails, u0, u1, pitch_mm, length_mm, half_stitches=False):
    """Satin zigzag from fraction u0 to u1 -> one polyline."""
    steps = max(2, int(abs(u1 - u0) * length_mm / pitch_mm))
    pts = []
    for k in range(steps + 1):
        u = u0 + (u1 - u0) * k / steps
        l, r = at(rails, u)
        a, b = (l, r) if k % 2 == 0 else (r, l)
        if half_stitches:
            pts.append(a)
            pts.append(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
        else:
            pts.append(a)
    return pts


def passes_for(rails, paths):
    return eg.analyze_passes([column(rails)], paths)[0]


class TestPassCounting:
    def test_single_dense_pass(self):
        r = straight()
        assert passes_for(r, [zigzag(r, 0, 1, 0.3, 40)])["passes"] == 1

    def test_split_column_is_two_passes(self):
        # what end_at_nearest_point does: the start half, then the far half run
        # back towards it, meeting mid-column
        r = straight()
        row = passes_for(r, [zigzag(r, 0, 0.5, 0.3, 40), zigzag(r, 1.0, 0.5, 0.3, 40)])
        assert row["passes"] == 2

    def test_split_seam_off_centre(self):
        r = straight()
        row = passes_for(r, [zigzag(r, 0, 0.3, 0.3, 40), zigzag(r, 1.0, 0.3, 0.3, 40)])
        assert row["passes"] == 2

    def test_trim_in_the_middle_is_still_one_pass(self):
        # same direction, picks up where it left off
        r = straight()
        row = passes_for(r, [zigzag(r, 0, 0.5, 0.3, 40), zigzag(r, 0.5, 1.0, 0.3, 40)])
        assert row["passes"] == 1

    def test_sparse_underlay_out_and_back_does_not_count(self):
        r = straight()
        under = zigzag(r, 0, 1, 3.0, 40) + zigzag(r, 1, 0, 3.0, 40)
        row = passes_for(r, [under, zigzag(r, 0, 1, 0.3, 40)])
        assert row["passes"] == 1

    def test_underlay_back_joined_to_top_layer_without_a_break(self):
        # underlay out, back, then the top layer, all one continuous path
        r = straight()
        run = zigzag(r, 0, 1, 3.0, 40) + zigzag(r, 1, 0, 3.0, 40) + zigzag(r, 0, 1, 0.3, 40)
        assert passes_for(r, [run])["passes"] == 1

    def test_edge_walk_and_centre_underlay_are_ignored(self):
        r = straight()
        edge = [(float(x), 0.0) for x in range(0, 41)]
        centre = [(float(x), 2.0) for x in range(0, 41)]
        row = passes_for(r, [edge, centre, zigzag(r, 0, 1, 0.3, 40)])
        assert row["passes"] == 1

    def test_fan_is_one_pass(self):
        r = fan()
        assert passes_for(r, [zigzag(r, 0, 1, 0.3, 40)])["passes"] == 1

    def test_fan_split_is_two_passes(self):
        r = fan()
        row = passes_for(r, [zigzag(r, 0, 0.5, 0.3, 40), zigzag(r, 1.0, 0.5, 0.3, 40)])
        assert row["passes"] == 2

    def test_tight_curve_is_one_pass(self):
        r = arc()
        length = column(r).length_mm
        assert passes_for(r, [zigzag(r, 0, 1, 0.3, length)])["passes"] == 1

    def test_tight_curve_split_is_two_passes(self):
        r = arc()
        length = column(r).length_mm
        row = passes_for(r, [zigzag(r, 0, 0.5, 0.3, length), zigzag(r, 1.0, 0.5, 0.3, length)])
        assert row["passes"] == 2

    def test_wide_column_halves_through_the_centre(self):
        # wide stitches the engine splits in two at the centre
        r = straight(width=9.0)
        paths = [zigzag(r, 0, 1, 0.4, 40, half_stitches=True)]
        assert passes_for(r, paths)["passes"] == 1

    def test_end_fan_jitter_is_not_a_pass(self):
        # a short back-and-forth at the very start, shorter than the width
        r = straight(width=8.0)
        jitter = zigzag(r, 0.0, 0.1, 0.3, 40)
        row = passes_for(r, [jitter, zigzag(r, 0.02, 1, 0.3, 40)])
        assert row["passes"] == 1

    def test_overlapping_columns_attribute_to_the_one_being_sewn(self):
        a = straight()
        shifted = [(x, y + 1.0) for x, y in a[0]], [(x, y + 1.0) for x, y in a[1]]
        cols = [eg.Column("a", *a, 1.0), eg.Column("b", *shifted, 1.0)]
        rows = eg.analyze_passes(cols, [zigzag(a, 0, 1, 0.3, 40)])
        assert rows[0]["passes"] == 1


class TestStreamStats:
    def test_trim_and_untrimmed_moves(self):
        s = [("JUMP", 0, 0), ("STITCH", 0, 0), ("STITCH", 1, 0), ("TRIM", 1, 0),
             ("JUMP", 11, 0), ("STITCH", 11, 0), ("STITCH", 12, 0),
             ("JUMP", 12, 30), ("STITCH", 12, 30), ("END", 12, 30)]
        st = eg.stream_stats(s)
        assert st["stitches"] == 5 and st["trims"] == 1
        assert [m["mm"] for m in st["untrimmed_moves"]] == [30.0]   # entry jump skipped
        assert st["longest_stitch_mm"] == 1.0

    def test_entry_jump_is_reported_not_flagged(self):
        s = [("JUMP", 30, 40), ("STITCH", 30, 40), ("STITCH", 31, 40)]
        st = eg.stream_stats(s)
        assert st["entry_jump_mm"] == 50.0 and st["untrimmed_moves"] == []

    def test_csv_colour_change_row_has_a_label_and_no_position(self):
        text = ('"*","0","STITCH","0","0"\n"*","1","COLOR_CHANGE t1 n2","999","999"\n'
                '"*","2","JUMP","50","0"\n')
        s = eg.parse_csv_stream(text)
        assert [c for c, _, _ in s] == ["STITCH", "COLOR_CHANGE", "JUMP"]
        assert eg.stream_stats(s)["untrimmed_moves"] == []

    def test_color_change_counts_as_cut(self):
        s = [("STITCH", 0, 0), ("COLOR_CHANGE", 0, 0), ("JUMP", 20, 0), ("STITCH", 20, 0)]
        assert eg.stream_stats(s)["untrimmed_moves"] == []

    def test_consecutive_jumps_are_one_move(self):
        s = [("STITCH", 0, 0), ("JUMP", 5, 0), ("JUMP", 10, 0), ("STITCH", 10, 0)]
        assert eg.stream_stats(s)["untrimmed_moves"][0]["mm"] == 10.0

    def test_longest_stitch_ignores_jumps(self):
        s = [("STITCH", 0, 0), ("STITCH", 3, 4), ("JUMP", 100, 0), ("STITCH", 100, 0)]
        assert eg.stream_stats(s)["longest_stitch_mm"] == 5.0

    def test_csv_parse_is_tenths_of_mm(self):
        text = '"#","x"\n"*","0","JUMP","10.0","-20.0"\n"*","1","STITCH","30","40"\n'
        assert eg.parse_csv_stream(text) == [("JUMP", 1.0, -2.0), ("STITCH", 3.0, 4.0)]


def analysis(passes=1, untrimmed=(), longest=3.0):
    return {"columns": [{"id": "c0", "shape": "s", "len_mm": 40.0, "passes": passes,
                         "runs": [[0, 20, 50], [40, 20, 50]][:max(passes, 0)]}],
            "stats": {"stitches": 100, "trims": 2, "longest_stitch_mm": longest,
                      "longest_stitch_at": [0, 0],
                      "untrimmed_moves": [{"mm": m, "at": [0, 0]} for m in untrimmed]}}


class TestFindings:
    def test_clean(self):
        assert eg.findings_from(analysis()) == []

    def test_multi_pass_is_error(self):
        f = eg.findings_from(analysis(passes=2))
        assert [(x["severity"], x["check"], x["object"]) for x in f] == [
            ("error", "satin_multi_pass", "c0")]

    def test_travel_thresholds(self):
        f = eg.findings_from(analysis(untrimmed=[eg.TRAVEL_ERROR_MM + 1, eg.TRAVEL_WARN_MM + 1, 1.0]))
        assert sorted(x["severity"] for x in f) == ["error", "warning"]

    def test_long_stitch_warns(self):
        f = eg.findings_from(analysis(longest=eg.LONGEST_STITCH_WARN_MM + 1))
        assert [x["check"] for x in f] == ["long_stitch"]

    def test_unmeasured_column_warns(self):
        f = eg.findings_from(analysis(passes=0))
        assert [x["check"] for x in f] == ["satin_pass_unmeasured"]


class TestGateWiring:
    def test_default_gate_never_runs_engine(self, tmp_path, monkeypatch):
        monkeypatch.setattr(eg, "run_engine_checks",
                            lambda *a, **k: pytest.fail("engine must be opt-in"))
        result = run_gate(make_project(tmp_path, GOOD_SATIN))
        assert result["ok"] and result["engine_checked"] is False

    def test_engine_errors_block(self, tmp_path, monkeypatch):
        monkeypatch.setattr(eg, "engine_analysis",
                            lambda *a, **k: (analysis(passes=2), {"cached": False}))
        proj = make_project(tmp_path, GOOD_SATIN)
        result = run_gate(proj, engine=True)
        assert result["ok"] is False and result["engine_checked"] is True
        assert result["errors"][0]["check"] == "satin_multi_pass"
        assert result["engine"]["multi_pass_columns"] == 1
        with pytest.raises(UserError, match="satin_multi_pass"):
            require_gate(proj)

    def test_require_gate_passes_clean(self, tmp_path, monkeypatch):
        monkeypatch.setattr(eg, "engine_analysis",
                            lambda *a, **k: (analysis(), {"cached": True}))
        assert require_gate(make_project(tmp_path, GOOD_SATIN))["ok"] is True

    def test_engine_that_cannot_run_is_an_error_not_a_skip(self, tmp_path, monkeypatch):
        def boom(*a, **k):
            raise eg.EngineCheckUnavailable("no binary")
        monkeypatch.setattr(eg, "engine_analysis", boom)
        result = run_gate(make_project(tmp_path, GOOD_SATIN), engine=True)
        assert result["ok"] is False
        assert result["errors"][0]["check"] == "engine_unavailable"


class TestCache:
    def test_second_run_is_served_from_cache(self, tmp_path, monkeypatch):
        monkeypatch.setenv(eg.CACHE_ENV, str(tmp_path / "cache"))
        calls = []
        monkeypatch.setattr(eg, "run_engine",
                            lambda *a, **k: calls.append(1) or analysis())
        proj = make_project(tmp_path, GOOD_SATIN)
        _, m1 = eg.engine_analysis(proj)
        _, m2 = eg.engine_analysis(proj)
        assert (m1["cached"], m2["cached"]) == (False, True) and len(calls) == 1

    def test_changed_svg_misses_cache(self, tmp_path, monkeypatch):
        monkeypatch.setenv(eg.CACHE_ENV, str(tmp_path / "cache"))
        calls = []
        monkeypatch.setattr(eg, "run_engine",
                            lambda *a, **k: calls.append(1) or analysis())
        proj = make_project(tmp_path, GOOD_SATIN)
        eg.engine_analysis(proj)
        svg = tmp_path / "design.svg"
        svg.write_text(svg.read_text().replace("M10,12", "M10,13"))
        from cli_anything_inkstitch.project import ProjectFile
        from cli_anything_inkstitch.svg.document import sha256_of
        p = ProjectFile.load(proj)
        p.svg_sha256 = sha256_of(svg)
        p.save()
        eg.engine_analysis(proj)
        assert len(calls) == 2

    def test_no_cache_flag_reruns(self, tmp_path, monkeypatch):
        monkeypatch.setenv(eg.CACHE_ENV, str(tmp_path / "cache"))
        calls = []
        monkeypatch.setattr(eg, "run_engine",
                            lambda *a, **k: calls.append(1) or analysis())
        proj = make_project(tmp_path, GOOD_SATIN)
        eg.engine_analysis(proj)
        eg.engine_analysis(proj, use_cache=False)
        assert len(calls) == 2


@pytest.mark.skipif(not discover(), reason="Ink/Stitch binary not installed")
def test_real_engine_one_column(tmp_path, monkeypatch):
    """End to end: the real binary sews a simple column; the gate reads it."""
    monkeypatch.setenv(eg.CACHE_ENV, str(tmp_path / "cache"))
    monkeypatch.setenv(eg.LOCK_ENV, "/tmp/inkstitch-render.lock")
    result = run_gate(make_project(tmp_path, GOOD_SATIN), engine=True)
    assert result["engine"]["stitches"] > 50
    assert result["engine"]["satin_columns"] == 1
    assert all(f["check"] != "satin_multi_pass" for f in result["errors"])
