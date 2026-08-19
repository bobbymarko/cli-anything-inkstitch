"""Carry Chroma's fill start/end positions into a converted SVG.

A digitiser chooses where each shape starts and ends so the fill runs across it
in one pass instead of being routed into sections and hopped between. Chroma
does not store that choice as a setting -- it IS the object's stitch stream, so
the first and last stitch are the two points that were chosen.

Ink/Stitch reads them as commands, not params: lib/elements/fill_stitch.py
get_starting_point (1017-1026) and get_ending_point (1032-1034) each look for a
Command and fall back to their own choice when there is none. The command
structure is written by the package's svg/commands.py attach_command, which is
cited to lib/commands.py find_commands -- writing that XML in a second place is
how a plausible-but-unread structure gets shipped, so this imports it.

    python3 tools/rde_start_end.py design.rde design.svg

Works on a scaled copy too: the factor is recovered from the document width, so
a youth-size SVG gets its commands in the right places.

Honest limit: this fixes where each fill ENTERS and LEAVES, verified honoured
to two decimal places. It does not reproduce Chroma's section ORDER in between
-- Ink/Stitch's auto_fill routes that itself, and there is no knob for it.
"""

import re
import sys
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from cli_anything_inkstitch.svg.commands import attach_command  # noqa: E402
from rde_to_inkstitch import PX_PER_UNIT, read_design  # noqa: E402

SVG_NS = 'http://www.w3.org/2000/svg'
ID = re.compile(r'^rde\d+$')


def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _path_points(d):
    n = [float(v) for v in re.findall(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?', d)]
    return list(zip(n[0::2], n[1::2]))


def object_endpoints(rde_path):
    """Every object's first and last stitch, in the converter's px space."""
    _colors, objs = read_design(rde_path)
    allpts = [s for o in objs for s in o['stitches']]
    minx = min(p[0] for p in allpts)
    miny = min(p[1] for p in allpts)

    def px(p):
        return ((p[0] - minx) * PX_PER_UNIT, (p[1] - miny) * PX_PER_UNIT)

    out = []
    for o in objs:
        st = o['stitches']
        if len(st) < 4:
            continue
        pts = [px(s) for s in st]
        out.append({'bbox': _bbox(pts), 'start': pts[0], 'end': pts[-1]})
    return out


def apply(rde_path, svg_path, dry_run=False):
    tree = etree.parse(str(svg_path))
    root = tree.getroot()
    elems = [e for e in root.iter(f'{{{SVG_NS}}}path')
             if e.get('id') and ID.match(e.get('id'))]
    objs = object_endpoints(rde_path)

    # A scaled copy (a youth size) needs its commands scaled the same way. The
    # factor comes from the document, so it is exact rather than remembered.
    _colors, all_objs = read_design(rde_path)
    allpts = [s for o in all_objs for s in o['stitches']]
    design_w = (max(p[0] for p in allpts) - min(p[0] for p in allpts)) * PX_PER_UNIT
    k = float(root.get('width')) / design_w

    done, skipped = 0, []
    for elem in elems:
        pts = _path_points(elem.get('d'))
        if not pts:
            continue
        pb = _bbox(pts)
        # Match by outline box: the emitted contour and the stitches that fill
        # it describe the same shape, so the nearest box is the right object.
        best = min(objs, key=lambda o: sum(
            abs(a * k - b) for a, b in zip(o['bbox'], pb)))
        err = sum(abs(a * k - b) for a, b in zip(best['bbox'], pb))
        if err > 120 * k:          # ~30 mm of disagreement is not a match
            skipped.append((elem.get('id'), round(err, 1)))
            continue
        objs.remove(best)
        for command, key in (('starting_point', 'start'), ('ending_point', 'end')):
            x, y = best[key]
            if not dry_run:
                attach_command(tree, elem, command, x * k, y * k)
        done += 1

    if not dry_run:
        tree.write(str(svg_path), xml_declaration=True, encoding='UTF-8')
    print(f'{done} of {len(elems)} elements given Chroma start/end'
          + (f' (scale {k:.5f})' if abs(k - 1) > 1e-6 else ''))
    for eid, err in skipped:
        print(f'  no matching object for {eid} (box error {err} px)')
    return done


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    apply(args[0], args[1], dry_run='--dry-run' in sys.argv)
