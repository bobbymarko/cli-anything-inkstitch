#!/usr/bin/env python3
"""Run the planner regression suite against another checkout's planner.

    regress_with_src.py PLANNER_SRC [--label branch@rev] -- regress run --results out.json ...

PLANNER_SRC is the `src` directory of a checkout (or a `git archive` of a branch) whose planner
you want to measure. It goes first on the path, so its `trace/`, `commands/tools.py` and the rest
sew the cases; this checkout's `regress` suite, `commands/regress.py` and `artifact/engine_gate.py`
are added behind it for the packages that checkout lacks. Nothing in PLANNER_SRC is modified.
The results JSON records the planner path, and the label if given.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "src"


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2
    planner = Path(argv[1]).resolve()
    rest = argv[2:]
    if rest[:1] == ["--label"]:
        os.environ["REGRESS_PLANNER_LABEL"] = rest[1]
        rest = rest[2:]
    if rest[:1] == ["--"]:
        rest = rest[1:]
    sys.path.insert(0, str(planner))
    import cli_anything_inkstitch as pkg
    import cli_anything_inkstitch.artifact as art
    import cli_anything_inkstitch.commands as cmds
    mine = HERE / "cli_anything_inkstitch"
    for mod, sub in ((pkg, ""), (art, "artifact"), (cmds, "commands")):
        extra = str(mine / sub) if sub else str(mine)
        if extra not in mod.__path__:
            mod.__path__.append(extra)
    from cli_anything_inkstitch.cli import root
    from cli_anything_inkstitch.commands import regress as _regress
    if "regress" not in root.commands:
        root.add_command(_regress.regress, "regress")
    import cli_anything_inkstitch.commands.tools as tools
    print(f"planner: {Path(tools.__file__).resolve()}", file=sys.stderr)
    assert str(planner) in str(Path(tools.__file__).resolve()), "planner path did not win"
    root.main(args=rest, prog_name="regress_with_src")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
