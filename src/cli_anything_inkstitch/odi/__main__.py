"""``python -m cli_anything_inkstitch.odi validate|to-svg FILE``."""

from __future__ import annotations

import sys

from . import OdiError, load, validate, write_inkstitch


def main(argv):
    if len(argv) < 2 or argv[0] not in ("validate", "to-svg") or (argv[0] == "to-svg" and len(argv) != 3):
        print("usage: python -m cli_anything_inkstitch.odi validate FILE.odi.json\n"
              "       python -m cli_anything_inkstitch.odi to-svg FILE.odi.json OUT.svg", file=sys.stderr)
        return 2
    try:
        doc = load(argv[1])
    except (OdiError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    issues = validate(doc)
    for i in issues:
        print(i)
    if any(i.level == "error" for i in issues):
        return 1
    if argv[0] == "validate":
        print("valid")
        return 0
    svg, report = write_inkstitch(doc)
    with open(argv[2], "w", encoding="utf-8") as f:
        f.write(svg)
    c = report.counts
    print(f"{argv[1]} -> {argv[2]}  satin {c['satin']}  fill {c['fill']}  run {c['run']}  trim {c['trim']}")
    for d in report.dropped:
        print(f"dropped: {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
