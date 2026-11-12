"""Minimal SVG path handling for ODI geometry.

ODI paths are SVG path strings in millimetres. Only the commands that every
source and target here needs are accepted: absolute ``M``, ``L``, ``C`` and
``Z``. Anything else (arcs, relative commands, quadratics) is rejected rather
than guessed at, so a path either means exactly what it says or fails loudly.

A parsed path is a list of ``(command, points)`` tuples, where ``points`` is a
list of ``(x, y)`` pairs: one for M/L, three for C (control, control, end), and
none for Z.
"""

from __future__ import annotations

import math
import re
from typing import Callable, Iterable, List, Sequence, Tuple

Point = Tuple[float, float]
Segment = Tuple[str, List[Point]]

_TOKEN = re.compile(r"[A-Za-z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARITY = {"M": 1, "L": 1, "C": 3, "Z": 0}


class PathError(ValueError):
    """The string is not a path ODI accepts."""


def parse_path(d: str) -> List[Segment]:
    tokens = _TOKEN.findall(d)
    # findall silently skips junk characters; make sure nothing but separators
    # was skipped, otherwise "M 1,2 Q 3" would parse as something it is not.
    if re.sub(_TOKEN, "", d).strip(" \t\r\n,"):
        raise PathError(f"unexpected characters in path: {d[:60]!r}")
    segs: List[Segment] = []
    i = 0
    cmd = None
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            if t not in _ARITY:
                raise PathError(f"unsupported path command {t!r} (ODI paths use M, L, C, Z)")
            cmd = t
            i += 1
            if cmd == "Z":
                segs.append(("Z", []))
                cmd = None
                continue
        elif cmd is None:
            raise PathError("number without a command")
        n = _ARITY[cmd] * 2
        nums = tokens[i:i + n]
        if len(nums) < n or any(x.isalpha() for x in nums):
            raise PathError(f"{cmd} needs {n} numbers")
        vals = [float(x) for x in nums]
        if not all(math.isfinite(v) for v in vals):
            raise PathError("non-finite coordinate")
        segs.append((cmd, list(zip(vals[0::2], vals[1::2]))))
        i += n
        if cmd == "M":
            cmd = "L"          # extra pairs after M are implicit lineto
    if not segs or segs[0][0] != "M":
        raise PathError("path must start with M")
    return segs


def _num(v: float) -> str:
    return repr(float(v))


def format_path(segs: Iterable[Segment], num: Callable[[float], str] = _num) -> str:
    """Serialise. The default number format is the shortest exact repr, so
    parse -> format round-trips without loss."""
    out = []
    for cmd, pts in segs:
        if cmd == "Z":
            out.append("Z")
        else:
            out.append(cmd + " " + " ".join(f"{num(x)},{num(y)}" for x, y in pts))
    return " ".join(out)


def map_points(segs: Iterable[Segment], fn: Callable[[float, float], Point]) -> List[Segment]:
    return [(c, [fn(x, y) for x, y in pts]) for c, pts in segs]


def subpaths(segs: Sequence[Segment]) -> List[List[Segment]]:
    """Split on M."""
    out: List[List[Segment]] = []
    for s in segs:
        if s[0] == "M":
            out.append([])
        out[-1].append(s)
    return out


def flatten(segs: Sequence[Segment], steps: int = 8) -> List[Point]:
    """Polyline approximation of one subpath (cubics sampled at `steps`)."""
    pts: List[Point] = []
    cur: Point = (0.0, 0.0)
    for cmd, p in segs:
        if cmd in ("M", "L"):
            cur = p[0]
            pts.append(cur)
        elif cmd == "C":
            p0, (p1, p2, p3) = cur, p
            for s in range(1, steps + 1):
                t = s / steps
                u = 1 - t
                pts.append((u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                            u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]))
            cur = p3
    return pts


def polyline(points: Sequence[Point]) -> str:
    return format_path([("M", [points[0]])] + [("L", [p]) for p in points[1:]])
