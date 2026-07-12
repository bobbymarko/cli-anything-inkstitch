"""Measured engine capabilities: which fill methods the INSTALLED binary acts on.

The schema is mined from engine SOURCE, which can be newer than the installed
binary. An unknown fill_method falls through the engine's elif chain into
plain auto fill with no error (fill_stitch.py to_stitch_groups) — found live
with cross_stitch on an Ink/Stitch v3.2.2 binary. There is no version→feature
table to mine, so we MEASURE: render a canned shape once per method and
compare against auto fill on identical paint; a byte-identical plan means the
method never reached the stitches. Verdicts are cached per binary version.

Fixture notes (each mined from the engine's readers):
- guided_fill needs a guide line: a stroked SIBLING in the same <g> whose
  style contains marker-start:url(#inkstitch-guide-line-marker
  (lib/marker.py get_marker_elements xpath) — included in every probe doc.
- linear_gradient_fill needs gradient paint: that method (and its auto_fill
  baseline) probe with fill="url(#...)" so the pair differs only in the
  fill_method attribute.
A method that probes "no effect" on the NEWEST engine means our fixture is
wrong, not the engine — tests assert known-good methods probe supported.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path

PROBE_FORMAT = 1

_SVG = """<svg xmlns="http://www.w3.org/2000/svg"
 xmlns:inkstitch="http://inkstitch.org/namespace"
 width="40mm" height="40mm" viewBox="0 0 40 40">
<metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
<defs>
  <linearGradient id="probegrad" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#cc2244"/>
    <stop offset="1" stop-color="#2244cc"/>
  </linearGradient>
</defs>
<g id="layer">
  <path id="probe_fill" d="M5,5 L35,5 L35,35 L5,35 Z" fill="{paint}"
        inkstitch:fill_method="{method}"/>
  <path id="probe_guide" d="M5,8 C15,20 25,20 35,32" fill="none"
        stroke="#004400"
        style="marker-start:url(#inkstitch-guide-line-marker);fill:none;stroke:#004400"/>
</g>
</svg>
"""


def _paint_for(method: str) -> str:
    return "url(#probegrad)" if method == "linear_gradient_fill" else "#cc2244"


def _plan_hash(binary: str, method: str, paint: str) -> str | None:
    from cli_anything_inkstitch.artifact.design_model import (
        _PREVIEW_ARGS,
        extract_stitch_blocks,
    )
    from cli_anything_inkstitch.binary import run_extension

    svg = _SVG.format(method=method, paint=paint)
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False) as f:
        f.write(svg)
        path = f.name
    try:
        out = run_extension(binary, "stitch_plan_preview", path,
                            args=_PREVIEW_ARGS, ids=["probe_fill"],
                            capture_stdout=True)
    except Exception:  # noqa: BLE001 — an erroring method isn't "no effect"
        return None
    finally:
        Path(path).unlink(missing_ok=True)
    if not out:
        return None
    blocks = extract_stitch_blocks(out)
    return hashlib.sha256(json.dumps(blocks).encode()).hexdigest()


def _cache_path(binary_version: str) -> Path:
    from cli_anything_inkstitch.schema.cache import cache_dir
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", binary_version)
    return cache_dir() / f"probe-fill-methods-{safe}-f{PROBE_FORMAT}.json"


def _binary_and_version() -> tuple[str | None, str | None]:
    from cli_anything_inkstitch.binary import detect_binary_version, discover
    binary = discover()
    return binary, (detect_binary_version(binary) if binary else None)


def get_cached() -> dict | None:
    """The cached verdict for the installed binary; None until computed."""
    _, version = _binary_and_version()
    if not version:
        return None
    path = _cache_path(version)
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def compute_and_cache(methods: list[str] | None = None) -> dict:
    """Probe every mined fill method against the installed binary (slow —
    two renders per paint variant plus one per method; run in the background
    and read via get_cached afterwards)."""
    binary, version = _binary_and_version()
    if not binary or not version:
        return {"no_effect": [], "supported": [], "errors": [],
                "binary_version": None, "skipped": "no Ink/Stitch binary found"}
    if methods is None:
        methods = _mined_methods()
    baselines: dict[str, str | None] = {}
    no_effect: list[str] = []
    supported: list[str] = []
    errors: list[str] = []
    for m in methods:
        if m == "auto_fill":
            continue
        paint = _paint_for(m)
        if paint not in baselines:
            baselines[paint] = _plan_hash(binary, "auto_fill", paint)
        h = _plan_hash(binary, m, paint)
        if h is None or baselines[paint] is None:
            errors.append(m)
        elif h == baselines[paint]:
            no_effect.append(m)
        else:
            supported.append(m)
    result = {"binary_version": version, "no_effect": no_effect,
              "supported": supported, "errors": errors}
    try:
        path = _cache_path(version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2))
    except OSError:
        pass
    return result


def _mined_methods() -> list[str]:
    try:
        from cli_anything_inkstitch.schema.cache import load_schema
        params = load_schema()["stitch_types"]["auto_fill"]["params"]
        return list(params["fill_method"].get("options") or [])
    except Exception:  # noqa: BLE001 — no mined options, nothing to probe
        return []
