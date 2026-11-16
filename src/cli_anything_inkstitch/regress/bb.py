"""Bold Bobbin as a contender: the same words in the same font, sewn by its CLI, measured from its DST.

The CLI (Node + wasm, boldbobbin.com) is located by $BOLDBOBBIN_CLI or ~/code/ext/boldbobbin-cli/1.4.4/boldbobbin.
Its exports are licensed to the account signed in to it: this module only ships the harness. Keep what it writes
(DSTs, contact sheets, results) out of the repository.

How a case is sewn: a `bb.text` object with the case's words, the app's own defaults for everything else (no
`optimizePorts`: the docs describe it as a Layers-panel command the person runs once the order is right, and
nothing says an export runs it), then `boldbobbin export` to DST. `bb.text`'s sizeMm is an em size, not a cap
height, so the size is set from the planner's own result: the "H" the planner sews at the case's height_mm has some
ink height, and BB's size is chosen so ITS "H" has the same ink height. Weight: BB's fonts are the regular cuts
(Lato-Regular, Lora-Regular, GreatVibes-Regular); the planner side uses the same families from Google Fonts.

Metrics come from the DST (engine_gate's stream reader): stitches, trims, untrimmed moves, longest stitch. Satin
column passes are not judged for BB: its text is one object and the DST has no column structure to attribute
stitches to, so multi-pass is reported as n/a.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

DEFAULT_CLI = Path.home() / "code/ext/boldbobbin-cli/1.4.4/boldbobbin"
_PROGRAM = """
const hp = {hp};
const probe = await bb.text("H", {{font: {font}, sizeMm: 10, at: {{x: 0, y: 0}}}});
const sizeMm = 10 * hp / probe.bboxMm.h;
bb.remove([probe.id]);
const o = await bb.text({text}, {{font: {font}, sizeMm, at: {{x: 0, y: 0}}, align: "center"}});
const st = await bb.stats();
const ck = await bb.check();
return {{sizeMm, bbox: o.bboxMm, jumps: st.jumps, trims: st.trims, stitches: st.stitches, runMinutes: st.runMinutes,
         verdict: ck.verdict, critical: ck.critical.length, warnings: ck.warnCount}};
"""


def cli_path() -> Path:
    return Path(os.environ.get("BOLDBOBBIN_CLI", DEFAULT_CLI))


def available() -> bool:
    return cli_path().exists()


def _run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(cli_path()), *args], capture_output=True, text=True, timeout=120, **kw)


def planner_h_ink(font_path: str, height_mm: float, wght: float | None) -> float:
    """Ink height of the "H" our planner sews at this cap height (mm)."""
    from cli_anything_inkstitch.trace import glyph_columns as gc
    font = gc.static_font(font_path, wght)
    (h,) = gc.plan_text(font, "H", height_mm)
    ys = [p[1] for c in h.contours for p in c]
    return max(ys) - min(ys)


def sew(case, work: Path, dst_dir: Path) -> dict[str, Any]:
    """Run one case through BB; returns {dst, seconds, report}. Raises RuntimeError on a refusal."""
    work.mkdir(parents=True, exist_ok=True)
    dst_dir.mkdir(parents=True, exist_ok=True)
    text = case.spec["lines"][0]
    hp = planner_h_ink(case.font_path, float(case.spec["height_mm"]), case.spec.get("wght"))
    prog = work / "prog.js"
    prog.write_text(_PROGRAM.format(hp=repr(hp), font=json.dumps(case.spec["bb_font"]), text=json.dumps(text)))
    base, out, dst = work / "base.vemb", work / "out.vemb", dst_dir / f"{case.id}.dst"
    t0 = time.monotonic()
    for args in (["new", "-o", str(base), "--force"],
                 ["run", str(base), str(prog), "-o", str(out), "--force"]):
        r = _run(args)
        if r.returncode:
            raise RuntimeError(f"boldbobbin {args[0]} refused: {(r.stdout + r.stderr)[-300:]}")
        if args[0] == "run":
            raw = r.stdout
    try:
        report = json.JSONDecoder().raw_decode(raw[raw.index("{"):])[0]
    except ValueError as e:
        raise RuntimeError(f"unreadable boldbobbin run output: {raw[-200:]}") from e
    r = _run(["export", str(out), "-o", str(dst), "--force"])
    if r.returncode:
        raise RuntimeError(f"boldbobbin export refused: {(r.stdout + r.stderr)[-300:]}")
    return {"dst": dst, "seconds": round(time.monotonic() - t0, 2), "report": report, "planner_h_ink_mm": hp}
