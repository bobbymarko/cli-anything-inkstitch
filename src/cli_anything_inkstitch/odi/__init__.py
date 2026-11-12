"""ODI: the Open Digitizing Interchange format (draft 0.1).

A neutral, object-level JSON description of an embroidery design. Sources are
read into it; targets are written from it. See ``model`` for the rules, and
``inkstitch_writer`` for the Ink/Stitch SVG target.
"""

from .inkstitch_writer import Report, write_inkstitch
from .model import (ODI_VERSION, PROVENANCE_TAGS, Document, Obj, OdiError,
                    Thread, dumps, load, loads, parse_prov, prov, save)
from .validate import Issue, check, validate

__all__ = [
    "ODI_VERSION", "PROVENANCE_TAGS", "Document", "Obj", "OdiError", "Thread",
    "Issue", "Report", "check", "dumps", "load", "loads", "parse_prov", "prov",
    "save", "validate", "write_inkstitch",
]
