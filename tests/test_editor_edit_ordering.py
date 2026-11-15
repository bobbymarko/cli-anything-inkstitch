"""Editor edit ordering: moved points must never snap back.

Over a slow link the editor lost node moves: concurrent /edit requests
landed out of order (each set_path carries the whole path, so the late
one overwrote the newer), and a `reload` fetched the design from before an
in-flight edit and painted the old geometry back — or arrived mid-drag and
was dropped, leaving the stale state on screen. Measured in a browser with
injected latency: the old editor snapped back in 5 of 5 rapid-drag trials
and lost an edit for good in 1; the queued editor in none.

These tests run the editor's own code (cut out of editor.html by its
markers) under Node with fake network timing, so they guard the exact
functions the page ships.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

EDITOR = (Path(__file__).resolve().parent.parent / "src" / "cli_anything_inkstitch"
          / "artifact" / "editor" / "editor.html")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not installed")


def _between(src: str, start: str, end: str) -> str:
    i = src.index(start)
    j = src.index(end, i)
    return src[i:j]


def _run(scenario: str) -> dict:
    html = EDITOR.read_text(encoding="utf-8")
    queue = _between(html, "let editChain = Promise.resolve();", "async function getDesign()")
    post = _between(html, "function postEdit(ops) {", "async function postFeedback(")
    refresh = _between(html, "let refreshGen = 0;", "refresh().then(connectSSE)")
    prelude = r"""
const log = [];
let dragging = null, design = null, routeData = null, routeOn = false;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const api = (p) => p;
function showToast() {}
function scheduleTier2() {}
const noop = () => {};
const renderAll = noop, renderOverview = noop, renderLayers = noop,
      renderColors = noop, renderReferencePanel = noop, renderHistory = noop,
      preserveScaleOnResize = noop;
async function fetchRoute() {}
const $ = () => ({ textContent: "", title: "" });
const document = {};
// fake server: an edit applies when its request "arrives", after a
// per-call delay; /design answers with the state at arrival time
let serverState = "v0";
const delays = [];
async function fetch(url, opts) {
  // a delay is ms each way, or [request ms, response ms]
  const dd = delays.length ? delays.shift() : 5;
  const [d, back] = Array.isArray(dd) ? dd : [dd, dd];
  await sleep(d);
  if (url === "/edit") {
    const ops = JSON.parse(opts.body).ops;
    serverState = ops[0].d;
    log.push("server:" + serverState);
    return { ok: true, json: async () => ({ applied: 1 }) };
  }
  const snapshot = serverState;
  await sleep(back);                            // slow response
  return { ok: true, json: async () => ({ objects: [{ d: snapshot }] }) };
}
async function getDesign() { const r = await fetch("/design"); return r.json(); }
"""
    js = prelude + queue + post + refresh + "\n(async () => {\n" + scenario + "\n})();"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


class TestEditOrdering:
    def test_edits_reach_the_server_in_the_order_they_were_made(self):
        # first request is slow on the wire, second fast: unqueued, the
        # server applied v2 then v1 — the older path won
        r = _run("""
          delays.push(200, 5);
          const a = postEdit([{op: "set_path", id: "x", d: "v1"}]);
          const b = postEdit([{op: "set_path", id: "x", d: "v2"}]);
          await Promise.all([a, b]);
          console.log(JSON.stringify({log, final: serverState}));
        """)
        assert r["log"] == ["server:v1", "server:v2"]
        assert r["final"] == "v2"

    def test_reload_during_an_edit_never_paints_the_old_design(self):
        # the reload's /design answers BEFORE the edit lands (fast response
        # vs slow edit) — it must not replace the locally moved geometry
        r = _run("""
          design = {objects: [{d: "v1-local"}]};
          delays.push(300);                       // the edit is slow
          const e = postEdit([{op: "set_path", id: "x", d: "v1-local"}]);
          requestReload();                        // SSE reload mid-edit
          await refresh();                        // a direct refresh too
          const during = design.objects[0].d;
          await e;
          await sleep(100);                       // deferred reload runs
          console.log(JSON.stringify({during, after: design.objects[0].d}));
        """)
        assert r["during"] == "v1-local"
        assert r["after"] == "v1-local"

    def test_reload_during_a_drag_is_deferred_not_dropped(self):
        # old behavior: `if (!dragging) refresh()` threw the event away, so
        # an agent's change never showed until something else reloaded
        r = _run("""
          design = {objects: [{d: "v0"}]};
          serverState = "agent-change";
          dragging = {type: "fill-node"};
          requestReload();
          await sleep(50);
          const during = design.objects[0].d;
          dragging = null;
          settleReload();                         // what pointerup calls
          await sleep(50);
          console.log(JSON.stringify({during, after: design.objects[0].d}));
        """)
        assert r["during"] == "v0"
        assert r["after"] == "agent-change"

    def test_stale_design_response_is_dropped_when_a_newer_fetch_exists(self):
        r = _run("""
          design = {objects: [{d: "v0"}]};
          serverState = "old";
          delays.push([5, 200]);                  // reads "old" now, answers late
          const slow = refresh();
          await sleep(10);
          serverState = "new";
          await refresh();                        // second fetch fast
          await slow;
          console.log(JSON.stringify({final: design.objects[0].d}));
        """)
        assert r["final"] == "new"
