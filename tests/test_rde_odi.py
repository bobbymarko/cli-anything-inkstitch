"""RDE -> ODI -> Ink/Stitch on synthetic .rde files (see rde_synth.py)."""

from __future__ import annotations

import hashlib

import tools_path  # noqa: F401  (puts tools/ on sys.path)
from rde_synth import build, fill_rows, object_payload, rect, ring_fill
from rde_to_inkstitch import convert
from rde_to_odi import to_odi

from cli_anything_inkstitch.odi import check, dumps, loads, parse_prov, write_inkstitch

CYAN = [((4, 141, 173), "1295", "Cyan")]


def counter_letter(tmp_path):
    return build(tmp_path / "l.rde", CYAN,
                 [object_payload(0, ring_fill(0, 0, 100, 100, (35, 35, 65, 65)),
                                 [rect(0, 0, 100, 100), rect(35, 35, 30, 30)])])


def two_squares(tmp_path, gap):
    """The first fill ends at (100, 100); the second starts `gap` units right of it."""
    a = fill_rows(0, 0, 100, 100)
    b = fill_rows(100 + gap, 100, 100, 100)
    return build(tmp_path / f"t{gap}.rde", CYAN,
                 [object_payload(0, a, [rect(0, 0, 100, 100)]),
                  object_payload(0, b, [rect(100 + gap, 100, 100, 100)])])


def test_decoder_emits_valid_odi_with_provenance_on_every_value(tmp_path):
    doc = to_odi(str(counter_letter(tmp_path)))
    assert check(doc) == []              # no errors AND no provenance warnings
    assert doc.source["format"] == "rde" and doc.units == "mm"
    (obj,) = doc.objects
    assert obj.type == "fill" and obj.thread == "t0"
    assert len(obj.geometry["shapes"][0]["holes"]) == 1
    # The fill flag is read from the file; the angle and spacing are measured;
    # expand is a constant. A measured value must never be tagged decoded.
    assert parse_prov(obj.provenance["type"])[0] == "decoded"
    assert parse_prov(obj.provenance["params.angle"])[0] == "measured"
    assert parse_prov(obj.provenance["params.row_spacing_mm"])[0] == "measured"
    assert parse_prov(obj.provenance["params.expand_mm"])[0] == "defaulted"
    assert parse_prov(obj.provenance["geometry"])[0] == "decoded"


def test_units_are_mm_and_extents_cover_the_stitches(tmp_path):
    doc = to_odi(str(counter_letter(tmp_path)))
    lo, hi = doc.extents["min"], doc.extents["max"]
    assert (hi[0] - lo[0], hi[1] - lo[1]) == (10.0, 10.0)     # 100 units of 0.1 mm


def test_source_hash_and_thread_table(tmp_path):
    path = counter_letter(tmp_path)
    doc = to_odi(str(path))
    assert doc.source["file_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    (t,) = doc.threads
    assert (t.rgb, t.code, t.name, t.index) == ("#048dad", "1295", "Cyan", 0)


def test_trim_is_a_measured_connect_value(tmp_path):
    doc = to_odi(str(two_squares(tmp_path, gap=100)))       # 10 mm apart
    first, second = doc.objects
    assert first.connect == {"trim_after": True}
    assert parse_prov(first.provenance["connect.trim_after"])[0] == "measured"
    assert second.connect == {}
    near = to_odi(str(two_squares(tmp_path, gap=10)))        # 1 mm apart
    assert all(not o.connect for o in near.objects)


def test_going_through_a_saved_odi_file_changes_nothing(tmp_path):
    design = two_squares(tmp_path, gap=100)
    direct, counts = convert(str(design))
    via_json = write_inkstitch(loads(dumps(to_odi(str(design)))))[0]
    assert via_json == direct
    assert counts == {"satin": 0, "fill": 2, "run": 0, "trim": 1}


def test_baked_stitches_are_opt_in(tmp_path):
    path = str(counter_letter(tmp_path))
    assert to_odi(path).objects[0].stitches is None
    with_st = to_odi(path, include_stitches=True).objects[0].stitches
    assert with_st and with_st[0][2] == "STITCH"
    assert check(to_odi(path, include_stitches=True)) == []


def test_unthreaded_design_gets_a_defaulted_black_thread(tmp_path):
    design = build(tmp_path / "n.rde", [],
                   [object_payload(0, fill_rows(0, 0, 100, 100), [rect(0, 0, 100, 100)])])
    doc = to_odi(str(design))
    assert [t.rgb for t in doc.threads] == ["#000000"]
    assert doc.threads[0].ext == {"x-defaulted": True}
    svg, _ = convert(str(design))
    assert 'inkscape:label="color 0"' in svg and "fill:#000000" in svg
