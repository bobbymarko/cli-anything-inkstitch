"""Scale an Ink/Stitch SVG's geometry, leaving stitch density alone.

This is the whole reason a design is converted to elements rather than kept as
a stitch file: scaling baked stitches scales the spacing between them and
wrecks the density, while scaling ELEMENTS leaves the engine to re-stitch the
smaller shape at the density you asked for.

So geometry scales and mm-denominated params do not. row_spacing_mm,
zigzag_spacing_mm, expand_mm and pull compensation are absolute measurements of
thread and fabric behaviour -- a youth-size design is stitched at the same
density as the adult one, with fewer rows, not at a proportionally coarser one.

    python3 tools/svg_scale.py in.svg out.svg --scale 0.869
    python3 tools/svg_scale.py in.svg out.svg --width-mm 243.6
    python3 tools/svg_scale.py in.svg out.svg --fit 243.6x234.1
"""

import argparse
import re
import sys

PX_PER_MM = 96.0 / 25.4
NUMBER = re.compile(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?')


def _scale_path(d, k):
    return NUMBER.sub(lambda m: f'{float(m.group()) * k:.3f}', d)


def scale_svg(svg, k):
    """Scale every coordinate, the canvas, and nothing else."""
    if 'transform=' in svg:
        # A transform would scale twice, or not at all, depending where it sits.
        # The converter never emits one; anything else is out of contract.
        raise SystemExit('refusing to scale: document contains a transform')

    def head(m):
        attr, value = m.group(1), float(m.group(2))
        return f'{attr}="{value * k:.3f}"'

    svg = re.sub(r'\b(width|height)="([\d.]+)"', head, svg, count=2)
    svg = re.sub(r'viewBox="([^"]*)"',
                 lambda m: 'viewBox="' + _scale_path(m.group(1), k) + '"',
                 svg, count=1)
    return re.sub(r'\sd="([^"]*)"',
                  lambda m: ' d="' + _scale_path(m.group(1), k) + '"', svg)


def document_mm(svg):
    w, h = (float(v) for v in re.search(
        r'width="([\d.]+)"\s+height="([\d.]+)"', svg).groups())
    return w / PX_PER_MM, h / PX_PER_MM


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src')
    ap.add_argument('dst')
    size = ap.add_mutually_exclusive_group(required=True)
    size.add_argument('--scale', type=float, help='uniform factor')
    size.add_argument('--width-mm', type=float, help='scale to this width')
    size.add_argument('--height-mm', type=float, help='scale to this height')
    size.add_argument('--fit', help='WxH in mm; scales to fit inside both')
    a = ap.parse_args(argv)

    svg = open(a.src).read()
    w, h = document_mm(svg)
    if a.scale:
        k = a.scale
    elif a.width_mm:
        k = a.width_mm / w
    elif a.height_mm:
        k = a.height_mm / h
    else:
        fw, fh = (float(v) for v in a.fit.lower().split('x'))
        k = min(fw / w, fh / h)

    out = scale_svg(svg, k)
    open(a.dst, 'w').write(out)
    nw, nh = document_mm(out)
    print(f'{w:.1f} x {h:.1f} mm  ->  {nw:.1f} x {nh:.1f} mm   (scale {k:.5f})')
    print('  stitch density params unchanged: ' + ', '.join(sorted(set(
        re.findall(r'inkstitch:(\w+_mm)="', out)))) or '  (none present)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
