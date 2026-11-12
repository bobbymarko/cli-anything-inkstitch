"""ODI model, validation and the Ink/Stitch writer, on synthetic objects only."""

from __future__ import annotations

import json
import re

import pytest

from cli_anything_inkstitch.odi import (Document, Obj, OdiError, Thread, check,
                                        dumps, loads, parse_prov, prov, validate,
                                        write_inkstitch)
from cli_anything_inkstitch.odi.paths import (PathError, format_path,
                                              parse_path)

SQUARE = "M 0.0,0.0 L 10.0,0.0 L 10.0,10.0 L 0.0,10.0 Z"
HOLE = "M 3.0,3.0 L 7.0,3.0 L 7.0,7.0 L 3.0,7.0 Z"


def fill(oid="f1", thread="t0", holes=(HOLE,)):
    o = Obj(id=oid, type="fill", thread=thread)
    o.set_geometry({"shapes": [{"outer": SQUARE, "holes": list(holes)}]}, "decoded")
    o.set_param("angle", 45.0, "measured", 0.9)
    o.set_param("expand_mm", 0.2, "defaulted")
    o.set_param("row_spacing_mm", 0.4, "user")
    return o


def satin(oid="s1", thread="t0"):
    o = Obj(id=oid, type="satin", thread=thread)
    o.set_geometry({"rails": ["M 0,0 L 5,0 L 10,0", "M 0,2 L 5,2 L 10,2"]}, "measured")
    o.set_param("zigzag_spacing_mm", 0.35, "measured")
    return o


def run(oid="r1", thread="t0"):
    o = Obj(id=oid, type="run", thread=thread)
    o.set_geometry({"path": "M 0,0 L 4,4 L 8,0"}, "decoded")
    return o


def doc(*objects, threads=None):
    return Document(
        source={"format": "synthetic"},
        extents={"min": [0.0, 0.0], "max": [10.0, 10.0]},
        threads=threads or [Thread("t0", "#112233", name="Navy", code="1001", index=0),
                            Thread("t1", "#ff0000", name="Red", code="1002", index=1)],
        objects=list(objects))


class TestProvenance:
    def test_build_and_parse(self):
        assert prov("measured", 0.92) == "measured:0.92"
        assert prov("decoded") == "decoded"
        assert parse_prov("measured:0.92") == ("measured", 0.92)
        assert parse_prov("user") == ("user", None)

    @pytest.mark.parametrize("bad", ["guessed", "measured:abc", "measured:1.5", ""])
    def test_rejects_unknown_or_malformed(self, bad):
        with pytest.raises(OdiError):
            parse_prov(bad)

    def test_setters_record_provenance_with_the_value(self):
        o = fill()
        assert o.provenance["params.angle"] == "measured:0.9"
        assert o.provenance["params.expand_mm"] == "defaulted"
        assert o.provenance["geometry"] == "decoded"


class TestRoundTrip:
    def test_every_object_type_round_trips_losslessly(self):
        letter = Obj(id="L1", type="lettering", thread="t0",
                     params={"text": "A", "font": {"name": "Sans"}},
                     provenance={"params.text": "user", "params.font": "user"},
                     children=[satin("g1")])
        group = Obj(id="grp", type="group", children=[run("r2"), fill("f2")])
        applique = Obj(id="ap", type="applique", thread="t0",
                       geometry={"outline": SQUARE}, provenance={"geometry": "decoded"},
                       children=[run("place")])
        manual = Obj(id="m1", type="manual", thread="t1",
                     stitches=[[0, 0, "STITCH"], [1, 1, "STITCH"], [2, 0, "TRIM"]])
        first = fill()
        first.underlay = [{"kind": "edge_walk", "inset_mm": 0.4}]
        first.provenance["underlay"] = "measured"
        first.connect = {"trim_after": True, "start": [0.0, 0.0]}
        first.provenance["connect.trim_after"] = "measured"
        first.provenance["connect.start"] = "decoded"
        first.ext = {"x-vendor": {"anything": [1, 2, 3]}}
        d = doc(first, satin(), run(), letter, group, applique, manual)
        d.ext = {"x-vendor": {"design_info": "kept"}}
        d.machine = {"format": "tajima-dst"}

        text = dumps(d)
        again = loads(text)
        assert again.to_dict() == d.to_dict()
        assert dumps(again) == text
        # Vendor data and nesting survived verbatim.
        assert again.objects[0].ext == {"x-vendor": {"anything": [1, 2, 3]}}
        assert again.ext == {"x-vendor": {"design_info": "kept"}}
        assert [o.id for o in again.walk()] == [o.id for o in d.walk()]
        assert check(again) is not None

    def test_float_precision_is_exact(self):
        o = run()
        o.geometry["path"] = format_path(parse_path("M 0.1,0.2 L 12.345678901234,7.1"))
        assert loads(dumps(doc(o))).objects[0].geometry["path"] == o.geometry["path"]
        assert parse_path(o.geometry["path"])[1][1] == [(12.345678901234, 7.1)]

    def test_lettering_children_serialise_as_glyphs(self):
        letter = Obj(id="L", type="lettering", thread="t0", params={"text": "A"},
                     children=[satin("g")])
        raw = json.loads(dumps(doc(letter)))["objects"][0]
        assert "glyphs" in raw and "children" not in raw

    def test_not_json(self):
        with pytest.raises(OdiError):
            loads("{nope")


class TestPaths:
    def test_parse_format_round_trip(self):
        d = "M 1.5,2.5 C 3,4 5,6 7,8 L 9,10 Z"
        assert parse_path(format_path(parse_path(d))) == parse_path(d)

    def test_implicit_lineto_after_move(self):
        assert [c for c, _ in parse_path("M 0 0 5 5 6 6")] == ["M", "L", "L"]

    @pytest.mark.parametrize("bad", ["", "L 1,2", "M 1,2 Q 3,4 5,6", "M 1", "M 1,2 l 3,4",
                                     "M 1,2 A 1 1 0 0 0 5 5", "M a,b"])
    def test_rejects_what_it_cannot_mean(self, bad):
        with pytest.raises(PathError):
            parse_path(bad)


def errors(d):
    return [str(i) for i in validate(d) if i.level == "error"]


class TestValidation:
    def test_good_document_has_no_issues(self):
        assert validate(doc(fill(), satin(), run())) == []

    def test_provenance_missing_is_a_warning_not_an_error(self):
        o = run()
        o.params["repeats"] = 2
        issues = validate(doc(o))
        assert [i.level for i in issues] == ["warning"]
        assert "repeats" in issues[0].message
        check(doc(o))       # does not raise

    def test_check_raises_on_errors(self):
        d = doc(run())
        d.units = "inch"
        with pytest.raises(OdiError, match="units"):
            check(d)

    def test_version(self):
        d = doc(run())
        d.odi = "9.9"
        assert any("version" in e for e in errors(d))

    def test_thread_must_exist_and_be_rgb(self):
        assert any("not in threads" in e for e in errors(doc(run(thread="nope"))))
        bad = doc(run(), threads=[Thread("t0", "red")])
        assert any("rgb" in e for e in errors(bad))
        dup = doc(run(), threads=[Thread("t0", "#000000"), Thread("t0", "#111111")])
        assert any("duplicate thread" in e for e in errors(dup))

    def test_duplicate_object_ids(self):
        assert any("duplicate object" in e for e in errors(doc(run("x"), run("x"))))

    def test_bad_path_is_reported_with_its_location(self):
        o = run()
        o.geometry["path"] = "M 1,2 Q 3,4 5,6"
        (e,) = errors(doc(o))
        assert "objects[r1].geometry.path" in e

    def test_shape_requirements(self):
        f = Obj(id="f", type="fill", thread="t0", geometry={"shapes": []})
        assert any("shapes" in e for e in errors(doc(f)))
        s = Obj(id="s", type="satin", thread="t0", geometry={"rails": ["M 0,0 L 1,1"]})
        assert any("two rail" in e for e in errors(doc(s)))
        s2 = Obj(id="s2", type="satin", thread="t0", geometry={"centerline": "M 0,0 L 9,0"})
        assert any("width_mm" in e for e in errors(doc(s2)))
        m = Obj(id="m", type="manual", thread="t0")
        assert any("stitches" in e for e in errors(doc(m)))

    def test_unknown_type_and_unknown_keys(self):
        assert any("unknown type" in e for e in errors(doc(Obj(id="z", type="blob", thread="t0"))))
        o = run()
        o.ext = {"wilcom": 1}
        assert any("x-" in e for e in errors(doc(o)))
        d = doc(run())
        d.ext = {"mystery": 1}
        assert any("top-level" in e for e in errors(d))

    def test_bad_stitch_command(self):
        m = Obj(id="m", type="manual", thread="t0", stitches=[[0, 0, "SEW"]])
        assert any("command" in e for e in errors(doc(m)))

    def test_bad_provenance_value(self):
        o = run()
        o.provenance["geometry"] = "guessed"
        assert any("provenance" in e for e in errors(doc(o)))


def element(svg, oid):
    return re.search(rf'<path id="{oid}"[^>]*/>', svg).group(0)


class TestWriter:
    def test_fill_with_hole_and_params(self):
        svg, report = write_inkstitch(doc(fill()))
        el = element(svg, "f1")
        px = 96 / 25.4
        # outer then hole, as subpaths of one evenodd path, in px
        assert f'd="M 0.000,0.000 L {10 * px:.3f},0.000' in el
        assert el.count("M ") == 2
        assert "fill:#112233;fill-rule:evenodd;stroke:none" in el
        assert 'inkstitch:angle="45.0"' in el
        assert 'inkstitch:expand_mm="0.2"' in el
        assert 'inkstitch:row_spacing_mm="0.40"' in el
        assert report.counts == {"satin": 0, "fill": 1, "run": 0, "trim": 0}
        assert report.dropped == []

    def test_satin_is_two_rails_with_the_satin_flag(self):
        svg, report = write_inkstitch(doc(satin()))
        el = element(svg, "s1")
        assert 'inkstitch:satin_column="true"' in el
        assert 'inkstitch:zigzag_spacing_mm="0.35"' in el
        assert el.count("M ") == 2 and "fill:none;stroke:#112233" in el
        assert report.counts["satin"] == 1

    def test_run_params(self):
        r = run()
        r.set_param("running_stitch_length_mm", 2.0, "user")
        r.set_param("repeats", 3, "user")
        el = element(write_inkstitch(doc(r))[0], "r1")
        assert 'inkstitch:running_stitch_length_mm="2"' in el
        assert 'inkstitch:repeats="3"' in el

    def test_trim_and_stop_after(self):
        f = fill()
        f.set_connect("trim_after", True, "measured")
        f.set_connect("stop_after", True, "user")
        svg, report = write_inkstitch(doc(f))
        assert 'inkstitch:trim_after="true" inkstitch:stop_after="true"/>' in element(svg, "f1")
        assert report.counts["trim"] == 1

    def test_layers_follow_first_appearance_of_each_thread(self):
        svg, _ = write_inkstitch(doc(run("a", "t1"), run("b", "t0"), run("c", "t1")))
        ids = re.findall(r'<g id="(color\d)"', svg)
        assert ids == ["color1", "color0"]
        assert 'inkscape:label="1002 Red"' in svg
        # elements stay in sewing order within their layer
        assert svg.index('id="a"') < svg.index('id="c"')

    def test_origin_and_size_come_from_extents(self):
        d = doc(run())
        d.extents = {"min": [-5.0, -2.0], "max": [5.0, 2.0]}
        d.objects[0].geometry["path"] = "M -5,-2 L 5,2"
        svg, _ = write_inkstitch(d)
        px = 96 / 25.4
        assert f'width="{10 * px:.3f}" height="{4 * px:.3f}"' in svg
        assert 'd="M 0.000,0.000 L ' in svg

    def test_extents_are_computed_when_absent(self):
        d = doc(run())
        d.extents = None
        svg, _ = write_inkstitch(d)
        assert f'width="{8 * 96 / 25.4:.3f}"' in svg

    def test_engine_settings_go_to_metadata(self):
        d = doc(run())
        d.engine = {"collapse_len_mm": 3.0, "min_stitch_len_mm": 0.1}
        svg, _ = write_inkstitch(d)
        assert "<inkstitch:collapse_len_mm>3.0</inkstitch:collapse_len_mm>" in svg
        assert "<inkstitch:min_stitch_len_mm>0.1</inkstitch:min_stitch_len_mm>" in svg
        assert "<inkstitch:inkstitch_svg_version>4<" in svg

    def test_group_and_applique_flatten_into_sewing_order(self):
        grp = Obj(id="g", type="group", children=[run("a"), run("b")])
        ap = Obj(id="ap", type="applique", thread="t0", geometry={"outline": SQUARE},
                 children=[run("place"), satin("tack")])
        svg, report = write_inkstitch(doc(grp, ap, run("z")))
        order = re.findall(r'<path id="(\w+)"', svg)
        assert order == ["a", "b", "place", "tack", "z"]
        assert report.counts["run"] == 4 and report.counts["satin"] == 1

    def test_centerline_satin_becomes_rails_of_the_requested_width(self):
        s = Obj(id="c", type="satin", thread="t0",
                geometry={"centerline": "M 0,5 L 10,5", "width_mm": 2.0})
        svg, _ = write_inkstitch(doc(s))
        d = re.search(r'id="c" d="([^"]*)"', svg).group(1)
        a, b = [parse_path(p) for p in re.split(r"(?=M )", d.strip())[1:]]
        px = 96 / 25.4
        ys = sorted({round(p[0][1], 3) for _, p in (a[0], b[0])})
        assert ys == [round(4 * px, 3), round(6 * px, 3)]       # 5 +- 1 mm
        assert a[-1][1][0][0] == pytest.approx(10 * px, abs=1e-3)

    def test_variable_width_centerline(self):
        s = Obj(id="c", type="satin", thread="t0",
                geometry={"centerline": "M 0,5 L 10,5", "width_mm": [[0, 2], [1, 4]]})
        svg, _ = write_inkstitch(doc(s))
        d = re.search(r'id="c" d="([^"]*)"', svg).group(1)
        a, b = [parse_path(p) for p in re.split(r"(?=M )", d.strip())[1:]]
        px = 96 / 25.4
        assert abs(a[-1][1][0][1] - b[-1][1][0][1]) == pytest.approx(4 * px, abs=1e-3)

    def test_manual_stitches_become_a_manual_stitch_polyline(self):
        m = Obj(id="m", type="manual", thread="t0",
                stitches=[[0, 0, "STITCH"], [5, 5, "STITCH"], [9, 0, "STITCH"]])
        svg, report = write_inkstitch(doc(m))
        el = element(svg, "m")
        assert 'inkstitch:stroke_method="manual_stitch"' in el
        assert el.count("L ") == 2
        assert report.dropped == []

    def test_nothing_is_dropped_silently(self):
        f = fill()
        f.set_param("fill_method", "contour", "decoded")
        f.underlay = [{"kind": "edge_walk"}, {"kind": "tatami", "angle": 45}]
        f.provenance["underlay"] = "decoded"
        f.set_connect("start", [0.0, 0.0], "decoded")
        letter = Obj(id="L", type="lettering", thread="t0", params={"text": "A"})
        m = Obj(id="m", type="manual", thread="t0",
                stitches=[[0, 0, "STITCH"], [1, 1, "STITCH"], [2, 2, "JUMP"]])
        _, report = write_inkstitch(doc(f, letter, m))
        text = "\n".join(report.dropped)
        for needle in ("params.fill_method", "underlay (edge_walk, tatami)",
                       "connect.start", "L: lettering has no glyph", "JUMP"):
            assert needle in text

    def test_provenance_histogram(self):
        _, report = write_inkstitch(doc(fill(), satin()))
        assert report.provenance["decoded"] == 1
        assert report.provenance["measured"] == 3       # angle, satin geometry, zigzag
        assert report.provenance["defaulted"] == 1
        assert report.provenance["user"] == 1

    def test_ids_and_labels_are_escaped(self):
        d = doc(run('a"b<c'), threads=[Thread("t0", "#000000", name='R&D "x"', code="1")])
        svg, _ = write_inkstitch(d)
        assert 'id="a&quot;b&lt;c"' in svg and "R&amp;D &quot;x&quot;" in svg

    def test_output_is_well_formed_xml(self):
        from lxml import etree
        svg, _ = write_inkstitch(doc(fill(), satin(), run()))
        root = etree.fromstring(svg.encode())
        assert len(root.findall(".//{http://www.w3.org/2000/svg}path")) == 3

    def test_missing_thread_raises(self):
        with pytest.raises(ValueError, match="thread"):
            write_inkstitch(doc(run(thread="nope")))
