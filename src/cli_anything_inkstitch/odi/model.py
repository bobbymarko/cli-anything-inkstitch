"""ODI document model: plain dataclasses plus lossless JSON load/save.

ODI (Open Digitizing Interchange) is an object-level JSON description of an
embroidery design. Every source format is read into it and every target is
written from it. Rules that shape this module:

* Geometry is the source of truth; baked stitches are optional evidence.
* Every value carries provenance (``decoded`` / ``measured`` / ``defaulted`` /
  ``user``, optionally ``tag:confidence``), kept in each object's
  ``provenance`` map keyed by dotted path ("geometry", "params.angle", ...).
  A converter must never present a measured value as a decoded one.
* Anything not understood is kept verbatim under an ``x-<vendor>`` key, so a
  document round-trips without loss.
* Units: millimetres, y down. Angles in degrees.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ODI_VERSION = "0.1"
PROVENANCE_TAGS = ("decoded", "measured", "defaulted", "user")
OBJECT_TYPES = ("run", "satin", "fill", "lettering", "applique", "group", "manual")
STITCH_COMMANDS = ("STITCH", "JUMP", "TRIM", "COLOR_CHANGE", "STOP", "END")


class OdiError(ValueError):
    """A document that cannot be loaded or fails validation."""


def prov(tag: str, confidence: Optional[float] = None) -> str:
    """Build a provenance string, e.g. ``prov('measured', 0.92)`` -> 'measured:0.92'."""
    if tag not in PROVENANCE_TAGS:
        raise OdiError(f"unknown provenance tag {tag!r}")
    return tag if confidence is None else f"{tag}:{confidence:g}"


def parse_prov(value: str) -> Tuple[str, Optional[float]]:
    tag, _, conf = str(value).partition(":")
    if tag not in PROVENANCE_TAGS:
        raise OdiError(f"unknown provenance tag {tag!r}")
    if not conf:
        return tag, None
    try:
        c = float(conf)
    except ValueError:
        raise OdiError(f"bad provenance confidence in {value!r}") from None
    if not 0.0 <= c <= 1.0:
        raise OdiError(f"provenance confidence out of range in {value!r}")
    return tag, c


@dataclass
class Thread:
    id: str
    rgb: str
    name: Optional[str] = None
    code: Optional[str] = None
    brand: Optional[str] = None
    chart: Optional[str] = None
    # Position in the source's own thread table, when it has one.
    index: Optional[int] = None
    ext: Dict[str, Any] = field(default_factory=dict)

    _KEYS = ("id", "rgb", "name", "brand", "code", "chart", "index")

    def to_dict(self) -> Dict[str, Any]:
        d = {k: getattr(self, k) for k in self._KEYS if getattr(self, k) is not None}
        d.update(self.ext)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Thread":
        known = {k: d[k] for k in cls._KEYS if k in d}
        if "id" not in known or "rgb" not in known:
            raise OdiError("thread needs 'id' and 'rgb'")
        return cls(**known, ext={k: v for k, v in d.items() if k not in cls._KEYS})


@dataclass
class Obj:
    id: str
    type: str
    thread: Optional[str] = None
    name: Optional[str] = None
    geometry: Dict[str, Any] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    underlay: List[Dict[str, Any]] = field(default_factory=list)
    connect: Dict[str, Any] = field(default_factory=dict)
    stitches: Optional[List[list]] = None
    provenance: Dict[str, str] = field(default_factory=dict)
    # group / applique passes / lettering glyphs
    children: List["Obj"] = field(default_factory=list)
    ext: Dict[str, Any] = field(default_factory=dict)

    def set_geometry(self, geometry: Dict[str, Any], tag: str, confidence: Optional[float] = None) -> None:
        self.geometry = geometry
        self.provenance["geometry"] = prov(tag, confidence)

    def set_param(self, name: str, value: Any, tag: str, confidence: Optional[float] = None) -> None:
        """Set a parameter *and* its provenance in one step, so the two
        cannot drift apart."""
        self.params[name] = value
        self.provenance[f"params.{name}"] = prov(tag, confidence)

    def set_connect(self, name: str, value: Any, tag: str, confidence: Optional[float] = None) -> None:
        self.connect[name] = value
        self.provenance[f"connect.{name}"] = prov(tag, confidence)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"id": self.id}
        if self.name is not None:
            d["name"] = self.name
        d["type"] = self.type
        if self.thread is not None:
            d["thread"] = self.thread
        d["geometry"] = self.geometry
        if self.params:
            d["params"] = self.params
        if self.underlay:
            d["underlay"] = self.underlay
        if self.connect:
            d["connect"] = self.connect
        if self.stitches is not None:
            d["stitches"] = self.stitches
        if self.provenance:
            d["provenance"] = self.provenance
        if self.children:
            d["glyphs" if self.type == "lettering" else "children"] = [c.to_dict() for c in self.children]
        d.update(self.ext)
        return d

    _KEYS = ("id", "name", "type", "thread", "geometry", "params", "underlay",
             "connect", "stitches", "provenance", "children", "glyphs")

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Obj":
        if not isinstance(d, dict) or "id" not in d or "type" not in d:
            raise OdiError("object needs 'id' and 'type'")
        kids = d.get("children", d.get("glyphs", [])) or []
        return cls(
            id=d["id"], type=d["type"], thread=d.get("thread"), name=d.get("name"),
            geometry=d.get("geometry") or {}, params=d.get("params") or {},
            underlay=d.get("underlay") or [], connect=d.get("connect") or {},
            stitches=d.get("stitches"), provenance=d.get("provenance") or {},
            children=[cls.from_dict(c) for c in kids],
            ext={k: v for k, v in d.items() if k not in cls._KEYS},
        )


@dataclass
class Document:
    odi: str = ODI_VERSION
    units: str = "mm"
    source: Dict[str, Any] = field(default_factory=dict)
    extents: Optional[Dict[str, Any]] = None
    machine: Dict[str, Any] = field(default_factory=dict)
    fabric: Dict[str, Any] = field(default_factory=dict)
    engine: Dict[str, Any] = field(default_factory=dict)
    threads: List[Thread] = field(default_factory=list)
    colorways: List[Dict[str, Any]] = field(default_factory=list)
    objects: List[Obj] = field(default_factory=list)
    stitches: Optional[List[list]] = None
    previews: List[Dict[str, Any]] = field(default_factory=list)
    ext: Dict[str, Any] = field(default_factory=dict)

    _KEYS = ("odi", "units", "source", "extents", "machine", "fabric", "engine",
             "threads", "colorways", "objects", "stitches", "previews")

    def thread(self, thread_id: Optional[str]) -> Optional[Thread]:
        for t in self.threads:
            if t.id == thread_id:
                return t
        return None

    def walk(self):
        """Every object, depth first, in sewing order (groups before children)."""
        for o in self.objects:
            yield from o.walk()

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"odi": self.odi, "units": self.units}
        for key in ("source", "extents", "machine", "fabric", "engine"):
            v = getattr(self, key)
            if v:
                d[key] = v
        d["threads"] = [t.to_dict() for t in self.threads]
        if self.colorways:
            d["colorways"] = self.colorways
        d["objects"] = [o.to_dict() for o in self.objects]
        if self.stitches is not None:
            d["stitches"] = self.stitches
        if self.previews:
            d["previews"] = self.previews
        d.update(self.ext)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Document":
        if not isinstance(d, dict):
            raise OdiError("ODI document must be a JSON object")
        return cls(
            odi=d.get("odi", ""), units=d.get("units", ""),
            source=d.get("source") or {}, extents=d.get("extents"),
            machine=d.get("machine") or {}, fabric=d.get("fabric") or {},
            engine=d.get("engine") or {},
            threads=[Thread.from_dict(t) for t in d.get("threads") or []],
            colorways=d.get("colorways") or [],
            objects=[Obj.from_dict(o) for o in d.get("objects") or []],
            stitches=d.get("stitches"), previews=d.get("previews") or [],
            ext={k: v for k, v in d.items() if k not in cls._KEYS},
        )


def dumps(doc: Document, indent: Optional[int] = 1) -> str:
    return json.dumps(doc.to_dict(), indent=indent, ensure_ascii=False)


def loads(text: str) -> Document:
    try:
        return Document.from_dict(json.loads(text))
    except json.JSONDecodeError as e:
        raise OdiError(f"not valid JSON: {e}") from None


def save(doc: Document, path) -> None:
    Path(path).write_text(dumps(doc) + "\n", encoding="utf-8")


def load(path) -> Document:
    return loads(Path(path).read_text(encoding="utf-8"))
