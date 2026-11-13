"""Planner regression suite: case files, the compare verdicts, sheets, and one real engine run."""

import copy
import json

import pytest
from click.testing import CliRunner

from cli_anything_inkstitch.binary import discover
from cli_anything_inkstitch.cli import root
from cli_anything_inkstitch.regress import cases as C
from cli_anything_inkstitch.regress import compare as K

try:
    import shapely  # noqa: F401
    import scipy.spatial  # noqa: F401
    import fontTools  # noqa: F401
    HAVE_SHAPELY = True
except ImportError:
    HAVE_SHAPELY = False

needs_shapely = pytest.mark.skipif(not HAVE_SHAPELY, reason="needs the satin extra")
needs_binary = pytest.mark.skipif(discover() is None, reason="Ink/Stitch binary not installed")


def _ok(**kw):
    r = {"id": "c", "kind": "text", "family": "sans", "status": "ok", "satin_columns": 10,
         "single_pass_columns": 10, "multi_pass_columns": 0, "multi_pass": [], "trims": 10,
         "untrimmed_moves": 0, "untrimmed_mm": [], "longest_stitch_mm": 3.0, "stitches": 1000,
         "failed_plans": []}
    r.update(kw)
    return r


class TestCaseFiles:
    def test_public_cases_load(self):
        cases = C.load_cases()
        ids = [c.id for c in cases]
        assert len(ids) == len(set(ids)) >= 25
        fams = {c.family for c in cases}
        assert {"sans", "serif", "script", "script-connected", "shape"} <= fams
        assert 5 <= sum(c.quick for c in cases) <= 8
        assert not any(c.private for c in cases)

    def test_vendored_sans_font_resolves(self):
        sans = next(c for c in C.load_cases() if c.id == "sans-ham-10")
        assert sans.skip_reason is None, sans.skip_reason

    def test_missing_font_is_skipped_not_fatal(self, tmp_path):
        f = tmp_path / "c.json"
        f.write_text(json.dumps({"fonts": {"x": {"path": "nowhere/x.ttf"}}, "cases": [
            {"id": "a", "kind": "text", "font": "x", "text": "Hi", "height_mm": 10}]}))
        (c,) = C.load_file(f, private=True)
        assert c.private and "missing input" in c.skip_reason

    def test_relative_paths_resolve_against_the_case_file(self, tmp_path):
        (tmp_path / "art.svg").write_text("<svg/>")
        f = tmp_path / "c.json"
        f.write_text(json.dumps({"cases": [{"id": "a", "kind": "shapes", "svg": "art.svg"}]}))
        (c,) = C.load_file(f, private=True)
        assert c.skip_reason is None and c.spec["svg"] == str(tmp_path / "art.svg")

    def test_bad_cases_are_refused(self, tmp_path):
        f = tmp_path / "c.json"
        f.write_text(json.dumps({"cases": [{"id": "a", "kind": "text", "font": "/x.ttf"}]}))
        with pytest.raises(C.CaseError):
            C.load_file(f, private=True)

    def test_duplicate_ids_across_files_are_refused(self, tmp_path):
        f = tmp_path / "c.json"
        f.write_text(json.dumps({"cases": [{"id": "sans-ham-10", "kind": "shapes", "svg": "a.svg"}]}))
        with pytest.raises(C.CaseError):
            C.load_cases([str(f)])

    def test_select_quick_and_only(self):
        cases = C.load_cases()
        assert all(c.quick for c in C.select(cases, quick=True))
        assert [c.id for c in C.select(cases, only=["shape-T"])] == ["shape-T"]
        with pytest.raises(C.CaseError):
            C.select(cases, only=["nope"])

    @needs_shapely
    def test_every_public_shape_builds_an_svg(self):
        from lxml import etree
        for c in C.load_cases():
            if c.kind == "shapes":
                svg = etree.fromstring(C.shapes_svg(c.spec).encode())
                assert svg.findall(".//{http://www.w3.org/2000/svg}path"), c.id


class TestJudge:
    def test_same_is_clean(self):
        v = K.judge(_ok(), _ok())
        assert not v["regressions"] and not v["warnings"] and not v["changed"]

    def test_multi_pass_increase(self):
        new = _ok(multi_pass_columns=1, multi_pass=[{"where": "B#3", "passes": 2}])
        v = K.judge(_ok(), new)
        assert v["regressions"] and "B#3" in v["regressions"][0]

    def test_multi_pass_decrease_is_an_improvement(self):
        base = _ok(multi_pass_columns=3, multi_pass=[{"where": "a"}, {"where": "b"}, {"where": "c"}])
        v = K.judge(base, _ok())
        assert not v["regressions"] and v["changed"]

    def test_new_untrimmed_move_over_3mm(self):
        assert K.judge(_ok(), _ok(untrimmed_moves=1, untrimmed_mm=[3.4]))["regressions"]
        assert not K.judge(_ok(), _ok(untrimmed_moves=1, untrimmed_mm=[2.9]))["regressions"]
        base = _ok(untrimmed_moves=1, untrimmed_mm=[5.0])
        assert not K.judge(base, base)["regressions"]

    def test_longest_stitch_limit(self):
        assert K.judge(_ok(), _ok(longest_stitch_mm=8.4))["regressions"]
        assert not K.judge(_ok(), _ok(longest_stitch_mm=8.0))["regressions"]

    def test_longest_stitch_already_over_fails_only_if_worse(self):
        base = _ok(longest_stitch_mm=8.3)
        assert not K.judge(base, _ok(longest_stitch_mm=8.3))["regressions"]
        assert K.judge(base, _ok(longest_stitch_mm=9.1))["regressions"]

    def test_trim_threshold_is_max_of_abs_and_fraction(self):
        assert not K.judge(_ok(trims=10), _ok(trims=12))["regressions"]       # +2 allowed
        assert K.judge(_ok(trims=10), _ok(trims=13))["regressions"]
        assert not K.judge(_ok(trims=100), _ok(trims=110))["regressions"]     # +10% allowed
        assert K.judge(_ok(trims=100), _ok(trims=111))["regressions"]
        assert not K.judge(_ok(trims=10), _ok(trims=2))["regressions"]        # fewer is fine

    def test_trim_rise_toward_the_reference_is_a_warning(self):
        ref = {"reference_trims": 62}
        v = K.judge(_ok(trims=42), _ok(trims=62, reference=ref))
        assert not v["regressions"] and "closer to the reference" in v["warnings"][0]
        v = K.judge(_ok(trims=42), _ok(trims=90, reference=ref))      # past it, and farther away
        assert v["regressions"]
        v = K.judge(_ok(trims=70), _ok(trims=90, reference=ref))      # up, but away from it
        assert v["regressions"]

    def test_trim_rise_without_a_reference_says_check_intent(self):
        v = K.judge(_ok(trims=42), _ok(trims=62))
        assert "trims up; check whether intended" in v["regressions"][0]

    def test_stitch_count_is_a_warning(self):
        v = K.judge(_ok(), _ok(stitches=1060))
        assert v["warnings"] and not v["regressions"]
        assert not K.judge(_ok(), _ok(stitches=1040))["warnings"]

    def test_case_that_now_fails(self):
        v = K.judge(_ok(), {"id": "c", "status": "error", "error": "boom"})
        assert v["regressions"]
        assert not K.judge({"id": "c", "status": "error"}, {"id": "c", "status": "error"})["regressions"]

    def test_missing_cases_warn(self):
        assert K.judge(_ok(), None)["warnings"] and K.judge(None, _ok())["warnings"]


def _write_results(tmp_path, name, cases):
    from cli_anything_inkstitch.regress.sheet import draw_sheet
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    out = {"meta": {"git_rev": name}, "summary": {}, "cases": {}}
    for cid, r in cases.items():
        r = dict(r, id=cid)
        stream = [("JUMP", 0, 0), ("STITCH", 0, 0), ("STITCH", 2, 0), ("STITCH", 2, 2), ("TRIM", 2, 2),
                  ("JUMP", 9, 9), ("STITCH", 9, 9), ("STITCH", 12, 9)]
        draw_sheet(stream, [{"label": "x", "left": [(0, 0), (5, 0)], "right": [(0, 3), (5, 3)]}],
                   [cid], d / f"{cid}.png")
        r["sheet"] = f"{cid}.png"
        out["cases"][cid] = r
    p = d / "results.json"
    p.write_text(json.dumps(out))
    return p


class TestCompare:
    def test_table_images_and_exit_code(self, tmp_path):
        base = _write_results(tmp_path, "base", {"same": _ok(), "worse": _ok(), "gone": _ok()})
        new = _write_results(tmp_path, "new", {"same": _ok(), "worse": _ok(multi_pass_columns=2,
                             multi_pass=[{"where": "A#0"}, {"where": "B#1"}])})
        res = CliRunner().invoke(root, ["regress", "compare", str(base), str(new),
                                        "--out", str(tmp_path / "img")])
        assert res.exit_code == 1, res.output
        assert "REGRESSION" in res.output and "worse" in res.output and "FAIL" in res.output
        assert (tmp_path / "img" / "worse.png").exists()
        assert not (tmp_path / "img" / "same.png").exists()      # unchanged: no image

    def test_clean_compare_exits_zero(self, tmp_path):
        base = _write_results(tmp_path, "base", {"a": _ok()})
        new = _write_results(tmp_path, "new", {"a": _ok(trims=9)})
        res = CliRunner().invoke(root, ["regress", "compare", str(base), str(new),
                                        "--out", str(tmp_path / "img")])
        assert res.exit_code == 0, res.output
        assert "PASS" in res.output

    def test_quick_run_against_a_full_baseline_ignores_the_rest(self, tmp_path):
        base = _write_results(tmp_path, "base", {"a": _ok(), "b": _ok()})
        new = _write_results(tmp_path, "new", {"a": _ok()})
        doc = json.loads(new.read_text())
        doc["meta"]["quick"] = True
        new.write_text(json.dumps(doc))
        res = K.compare(K.load(base), K.load(new), None, images=False)
        assert [r["id"] for r in res["rows"]] == ["a"] and res["not_run"] == 1 and res["warned"] == 0

    def test_json_output(self, tmp_path):
        base = _write_results(tmp_path, "base", {"a": _ok()})
        res = CliRunner().invoke(root, ["regress", "compare", str(base), str(base), "--no-images", "--json"])
        assert res.exit_code == 0
        assert json.loads(res.output)["ok"] is True


@needs_shapely
@needs_binary
class TestRealRun:
    def test_one_shape_case_end_to_end(self, tmp_path):
        from cli_anything_inkstitch.regress.runner import run_suite
        cases = C.select(C.load_cases(), only=["shape-T"])
        doc = run_suite(cases, tmp_path / "r.json", case_files=["cases.json"], log=lambda s: None)
        r = doc["cases"]["shape-T"]
        assert r["status"] == "ok", r
        assert r["satin_columns"] >= 2 and r["stitches"] > 100
        assert (tmp_path / r["sheet"]).exists()
        assert doc["meta"]["package_path"].endswith("cli_anything_inkstitch")
