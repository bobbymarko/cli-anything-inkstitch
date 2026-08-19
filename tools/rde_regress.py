"""Corpus regression harness for the .rde converter.

Every change to rde_to_inkstitch.py is a change to how ~100 already-approved
designs convert, and the interesting number is not "does it still run" but
"how many designs did this move, and which". Record a baseline before a change,
compare after it, and look at anything that moved.

    python3 tools/rde_regress.py record baseline.json  tests/fixtures/rde
    ...edit the converter...
    python3 tools/rde_regress.py check  baseline.json  tests/fixtures/rde

`check` exits non-zero if any design fails to convert, and prints every design
whose element counts or geometry changed. A moved design is not automatically
a bug -- today's counter fix moved 29 of 127 -- but every one of them should be
a change you can name, and the ones you cannot name are the bugs.
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rde_to_inkstitch import convert  # noqa: E402

KEYS = ('satin', 'fill', 'run', 'trim')


def designs(paths):
    for p in paths:
        p = Path(p)
        yield from sorted(p.rglob('*.rde')) if p.is_dir() else [p]


def measure(paths):
    out, failed = {}, []
    for f in designs(paths):
        try:
            svg, counts = convert(str(f))
        except Exception as e:                                  # noqa: BLE001
            failed.append((f.name, repr(e)))
            continue
        out[f.name] = {k: counts.get(k, 0) for k in KEYS}
        # Geometry digest: counts alone hide a change that moves paths without
        # changing how many there are.
        out[f.name]['sha256'] = hashlib.sha256(svg.encode()).hexdigest()[:16]
    return out, failed


def main(argv):
    if len(argv) < 3 or argv[0] not in ('record', 'check'):
        print(__doc__)
        return 2
    mode, baseline, paths = argv[0], Path(argv[1]), argv[2:]
    now, failed = measure(paths)
    for name, err in failed:
        print(f'FAILED TO CONVERT  {name}: {err}')
    if mode == 'record':
        baseline.write_text(json.dumps(now, indent=1, sort_keys=True))
        print(f'recorded {len(now)} designs -> {baseline}')
        return 1 if failed else 0

    was = json.loads(baseline.read_text())
    moved = 0
    for name in sorted(set(was) | set(now)):
        a, b = was.get(name), now.get(name)
        if a == b:
            continue
        moved += 1
        if a is None or b is None:
            print(f'{name:45} {"ADDED" if a is None else "GONE"}')
            continue
        counts = ' '.join(f'{k} {a[k]}->{b[k]}' for k in KEYS if a[k] != b[k])
        print(f'{name:45} {counts or "geometry changed, counts identical"}')
    print(f'\n{len(now)} designs, {moved} moved, {len(failed)} failed to convert')
    return 1 if (failed or moved) else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
