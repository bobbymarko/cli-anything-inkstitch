"""ODI validation. Hand-written so the package stays dependency-light; this is
the normative checker for draft 0.1.

``validate(doc)`` returns a list of :class:`Issue`; ``check(doc)`` raises
:class:`OdiError` if any is an error. Warnings never fail a document, but a
parameter without provenance is reported, because a value of unknown origin is
how a guess gets mistaken for a measurement.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, List

from .model import (OBJECT_TYPES, ODI_VERSION, STITCH_COMMANDS, Document, Obj,
                    OdiError, parse_prov)
from .paths import PathError, parse_path

_RGB = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class Issue:
    level: str          # "error" | "warning"
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.where}: {self.message}"


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate(doc: Document) -> List[Issue]:
    issues: List[Issue] = []

    def err(where, msg):
        issues.append(Issue("error", where, msg))

    def warn(where, msg):
        issues.append(Issue("warning", where, msg))

    if doc.odi != ODI_VERSION:
        err("odi", f"unsupported version {doc.odi!r} (this library reads {ODI_VERSION})")
    if doc.units != "mm":
        err("units", f"units must be 'mm', got {doc.units!r}")
    for k in doc.ext:
        if not k.startswith("x-"):
            err(k, "unknown top-level key (vendor data must be named 'x-<vendor>')")

    if doc.extents is not None:
        try:
            lo, hi = doc.extents["min"], doc.extents["max"]
            if not (len(lo) == len(hi) == 2 and all(_is_num(v) for v in (*lo, *hi))):
                raise ValueError
            if hi[0] < lo[0] or hi[1] < lo[1]:
                err("extents", "max is below min")
        except (KeyError, TypeError, ValueError):
            err("extents", "needs numeric 'min' and 'max' [x, y] pairs")

    thread_ids = set()
    for i, t in enumerate(doc.threads):
        where = f"threads[{i}]"
        if t.id in thread_ids:
            err(where, f"duplicate thread id {t.id!r}")
        thread_ids.add(t.id)
        if not isinstance(t.rgb, str) or not _RGB.match(t.rgb):
            err(where, f"rgb must be '#rrggbb', got {t.rgb!r}")
        for k in t.ext:
            if not k.startswith("x-"):
                err(where, f"unknown key {k!r}")

    seen = set()
    for o in doc.objects:
        _object(o, "objects", thread_ids, seen, err, warn, top=True)

    if doc.stitches is not None:
        _stitches(doc.stitches, "stitches", err)
    return issues


def _stitches(st, where, err):
    if not isinstance(st, list):
        err(where, "stitches must be a list of [x, y, command]")
        return
    for i, s in enumerate(st):
        if not (isinstance(s, (list, tuple)) and len(s) == 3 and _is_num(s[0])
                and _is_num(s[1]) and s[2] in STITCH_COMMANDS):
            err(f"{where}[{i}]", f"expected [x, y, command] with command in {STITCH_COMMANDS}")
            return          # one report per list is enough


def _path(value, where, err):
    if not isinstance(value, str):
        err(where, "path must be a string")
        return False
    try:
        parse_path(value)
    except PathError as e:
        err(where, str(e))
        return False
    return True


def _object(o: Obj, parent: str, thread_ids, seen, err, warn, top=False):
    where = f"{parent}[{o.id}]"
    if not isinstance(o.id, str) or not o.id:
        err(where, "id must be a non-empty string")
    elif o.id in seen:
        err(where, "duplicate object id")
    seen.add(o.id)
    if o.type not in OBJECT_TYPES:
        err(where, f"unknown type {o.type!r}")
        return
    for k in o.ext:
        if not k.startswith("x-"):
            err(where, f"unknown key {k!r} (vendor data must be named 'x-<vendor>')")

    if o.type != "group":
        if o.thread is None:
            err(where, "needs a thread")
        elif o.thread not in thread_ids:
            err(where, f"thread {o.thread!r} is not in threads[]")

    g = o.geometry
    if o.type == "run":
        _path(g.get("path"), f"{where}.geometry.path", err)
    elif o.type == "satin":
        if "rails" in g:
            r = g["rails"]
            if not (isinstance(r, list) and len(r) == 2):
                err(f"{where}.geometry.rails", "needs exactly two rail paths")
            else:
                for j, p in enumerate(r):
                    _path(p, f"{where}.geometry.rails[{j}]", err)
        elif "centerline" in g:
            _path(g["centerline"], f"{where}.geometry.centerline", err)
            w = g.get("width_mm")
            if not (_is_num(w) and w > 0) and not (
                    isinstance(w, list) and w and all(
                        isinstance(p, (list, tuple)) and len(p) == 2 and all(_is_num(v) for v in p) and p[1] > 0
                        for p in w)):
                err(f"{where}.geometry.width_mm", "needs a positive number or [[t, width], ...]")
        else:
            err(f"{where}.geometry", "satin needs 'rails' or 'centerline'")
    elif o.type == "fill":
        shapes = g.get("shapes")
        if not (isinstance(shapes, list) and shapes):
            err(f"{where}.geometry.shapes", "needs at least one shape")
        else:
            for j, s in enumerate(shapes):
                if not isinstance(s, dict) or "outer" not in s:
                    err(f"{where}.geometry.shapes[{j}]", "needs 'outer'")
                    continue
                _path(s["outer"], f"{where}.geometry.shapes[{j}].outer", err)
                for k, h in enumerate(s.get("holes", [])):
                    _path(h, f"{where}.geometry.shapes[{j}].holes[{k}]", err)
    elif o.type == "manual":
        if not o.stitches:
            err(where, "manual objects need stitches")
    elif o.type == "applique":
        _path(g.get("outline"), f"{where}.geometry.outline", err)
    elif o.type == "lettering":
        if not isinstance(o.params.get("text"), str):
            err(f"{where}.params.text", "lettering needs text")
    elif o.type == "group":
        if not o.children:
            err(where, "group needs children")

    if o.stitches is not None:
        _stitches(o.stitches, f"{where}.stitches", err)

    for key, val in o.provenance.items():
        try:
            parse_prov(val)
        except OdiError as e:
            err(f"{where}.provenance[{key}]", str(e))
    if o.geometry and "geometry" not in o.provenance:
        warn(where, "geometry has no provenance")
    for name in o.params:
        if f"params.{name}" not in o.provenance:
            warn(where, f"param {name!r} has no provenance")
    for name in o.connect:
        if f"connect.{name}" not in o.provenance:
            warn(where, f"connect.{name} has no provenance")
    if o.underlay and "underlay" not in o.provenance:
        warn(where, "underlay has no provenance")

    for c in o.children:
        _object(c, f"{where}.children", thread_ids, seen, err, warn)


def check(doc: Document) -> List[Issue]:
    """Raise OdiError on any error; return the warnings."""
    issues = validate(doc)
    errors = [i for i in issues if i.level == "error"]
    if errors:
        raise OdiError("invalid ODI document:\n  " + "\n  ".join(str(i) for i in errors))
    return issues
