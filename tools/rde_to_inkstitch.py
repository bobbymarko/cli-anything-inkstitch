"""Convert a Chroma .rde design into an Ink/Stitch-editable SVG.

    python3 tools/rde_to_inkstitch.py design.rde design.svg

The conversion is two steps, each usable on its own:

    .rde --(tools/rde_to_odi.py)--> ODI --(odi.write_inkstitch)--> Ink/Stitch SVG

What the converter decides, and why, is documented in rde_to_odi.py; how ODI
becomes Ink/Stitch markup (with the engine-side reader of every attribute) is
documented in src/cli_anything_inkstitch/odi/inkstitch_writer.py.

``read_design`` and ``PX_PER_UNIT`` are re-exported because rde_start_end.py
needs the same decode and the same px scale.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from cli_anything_inkstitch.odi import write_inkstitch  # noqa: E402
from rde_to_odi import PX_PER_UNIT, read_design, to_odi  # noqa: E402,F401


def convert(path):
    """Return ``(svg_text, counts)`` for a .rde file."""
    svg, report = write_inkstitch(to_odi(path))
    return svg, report.counts


if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    svg, counts = convert(src)
    open(dst, 'w').write(svg)
    print(f'{src} -> {dst}')
    print(f'  satin columns: {counts["satin"]}  fills: {counts["fill"]}  '
          f'runs: {counts["run"]}  trims: {counts["trim"]}')
