# AI-First Digitizing Artifact — Spec (v0.2 draft)

*Starting point for handoff to Fable. Grounded in the satin/fill editing model and inkstitch harness we've been building. Treat everything here as a proposal to refine, not settled.*

*v0.2 revisions (after review against the cli-anything-inkstitch codebase): deployment recommendation flipped to (B) — the claude.ai artifact CSP blocks localhost fetches; Tier-2 latency budget grounded in measurement (§6a); Tier-1 honesty rule for fills; backend contract routed through the existing CLI command surface; open decisions 1, 3, 5 resolved against existing CLI infrastructure; fill start/end handles pulled into the v1 slice.*

---

## 1. Vision

Digitizing stays **AI-first**. The model does the first digitization pass (paths + stitch attributes) headlessly. When its precision falls short — which today is most of the time on satins and fill entry/exit — a **digitizing artifact** is kicked off: a manipulable editor that opens *in the flow of the conversation*, not as a separate app.

The artifact is not a dead export. It's a **live two-way surface**. The human corrects the digitization in two interchangeable ways — direct manipulation (drag a rail, move a start point) and continued conversation ("this column's too narrow at the top", "snap this rail to the artwork edge") — and the agent edits the same underlying design in response. Neither pure chat nor pure GUI dominates; they operate on one shared model.

This is **Lavish's** pattern (an agent-driven HTML artifact you can annotate and keep talking to, with feedback long-polled back to the agent) specialized to embroidery objects and backed by a real stitch engine instead of a browser layout audit.

> **Reference — Lavish (lavish-axi): https://github.com/kunchenguid/lavish-axi**
> Lavish is a CLI ("AXI") that opens an agent-authored HTML artifact in a local browser with an injected SDK. The human annotates rendered elements, text ranges, or Mermaid nodes directly in the artifact and sends feedback/chat; the agent long-polls, receives those annotations (by stable node identity, not CSS selectors), edits the HTML, and live-reloads — all without leaving the artifact. Sessions are keyed by file path. **Fable should read this repo first** — the loop mechanics (injected SDK, queued feedback, long-poll, live reload, file-path identity, layout-audit gate) are the pattern this spec adapts to embroidery objects. Wherever this doc says "Lavish-style" or "the loop," it means that repo's approach.

**Non-goal:** rebuilding Wilcom/Hatch. This covers the *correction* failure modes — satin geometry, fill entry/exit, angles, params — not from-scratch full-suite digitizing.

---

## 2. Design tenets

1. **AI-first, UI on demand.** The artifact is summoned only when correction is needed, and hands control back to the conversation when done.
2. **One shared model.** Human and agent edit the same SVG + inkstitch attributes. Every direct manipulation is expressible as an agent edit and vice versa.
3. **The stitch engine is the authority.** The artifact never invents stitch math. inkstitch computes the real stitch plan and exports; the canvas shows a cheap approximation live and the authoritative render on settle.
4. **Correction-scoped.** Optimize the operations the model gets wrong most (satin rails, fill start/end, angle, density), not the full digitizing toolset.
5. **Round-trippable.** State survives the artifact closing and reopening; the AI can resume a correction session.

---

## 3. Workflow lifecycle

**Phase 0 — AI digitization pass (headless).**
Model produces a candidate design: SVG paths with `inkstitch:` attributes (satins as two rails + rungs — or whatever the model emits, normalized to rails on import per §5; fills with angle/params). No UI yet.

**Phase 1 — Kick-off.**
The artifact is launched when any of: (a) the pass contains satin columns or fills (high-risk object types), (b) the model's own confidence on an object is low, or (c) the user explicitly asks to edit. Session is **keyed by the design file path** (Lavish's identity model — no opaque IDs; the agent reopens by path).

**Phase 2 — Correction loop (the core).**
Human works the canvas: direct manipulation *and/or* annotation + chat. Agent long-polls, receives feedback (object id + optional manipulation delta + freeform text), edits the model, the canvas live-reloads. Two-tier preview throughout (see §6).

**Phase 3 — Stitchability gate + handback.**
Before returning to the AI-first conversation, a quality audit runs (§9). On a clean pass, the design exports to the target stitch format and control returns to the chat with a short summary of what changed. Errors keep the artifact open (with a "stitch anyway" override).

---

## 4. The bidirectional agent loop (Lavish-inspired)

The artifact runs the design in a canvas with an injected SDK that handles selection, annotation, manipulation capture, and feedback queuing. Mechanics to mirror from Lavish:

- **Queued feedback, then send.** Manipulations and annotations queue locally; one send flushes them to the agent. Reversible edits update local state first, then commit as a single message.
- **Long-poll.** Agent waits on a poll for the next feedback batch; queued feedback is never lost across reloads.
- **Agent presence.** The canvas shows whether an agent is listening / working, and blocks sends only while the agent is mid-edit on delivered feedback.
- **Live reload.** When the agent patches the model, the canvas reloads and preserves viewport/selection.

**Annotation targets (the domain-specific part).** Instead of HTML elements / text ranges / Mermaid nodes, the selectable, addressable targets are embroidery objects:

| Target | What a click sends the agent |
|---|---|
| Satin column | object id + "satin", current params |
| Individual rail (A/B) | object id + side |
| Rail node | object id + rail + node index |
| Rung | object id + rung index/offset |
| Fill region | object id + "fill", angle, params |
| Fill start / end handle | object id + role |
| Stitch-plan segment | object id + stitch range (from the authoritative render) |
| Travel / jump between two objects | ordered pair of object ids |

Clicking sends a **stable object id + semantic label**, never a canvas selector — same principle as Lavish sending a Mermaid node id instead of a CSS path.

> **Provenance caveat (stitch-plan segment target):** mapping a clicked stitch range back to an object requires the stitch-plan SVG to carry element-id attribution. Verify what Ink/Stitch's stitch-plan output actually annotates before promising this target — it may need a post-processing step correlating plan groups back to source elements. Treat this row as v2 until verified.

**Message shape (human → agent):**
```
{
  objects: [<object_id>, ...],        // what's selected/annotated
  manipulation?: <edit_delta>,        // if the human also dragged something
  text: "make this rail hug the artwork edge"
}
```

**Message shape (agent → artifact):**
```
{
  patch: <design_model_delta>,        // add/modify/remove objects & attrs
  reply: "Snapped the right rail to the artwork boundary and re-rung at the two curvature peaks."
}
```

---

## 5. The design model (shared state)

A **Design** is an ordered list of objects on an SVG canvas plus a faint artwork reference layer. Order matters (it drives stitch sequence and travel).

**Object types:**

- **Satin** — two independent rails (`railA`, `railB`, both bezier paths) + `rungs` (cross-connectors that pair the rails) + params. *Two rails, not a centerline.* The best satins are variable-width: each edge hugs its own artwork feature — a calligraphic stroke has one edge doing a subtle S while the other stays near-straight — and those edges are independent, not a spine plus a width function. Variable and asymmetric width are therefore inherent, not bolted on. Uniform columns degrade gracefully (parallel rails). **Rungs are first-class**, because they're what makes independent rails safe: they pair the rails explicitly, so the two rails don't need matching node counts and the zigzag interpolation can't twist. Editor obligations that follow: rungs are always present and visualized, pairing is shown, and desync/crossing is caught by the gate (§8) rather than prevented by dumbing down the model.

  *Emission is deliberately not privileged here.* Whatever the model produces — two rough rails, or a centerline+width — **normalizes to two rails on import** (a centerline bakes to rails exactly like Ink/Stitch's stroke-to-satin, giving a clean symmetric *starting point*). From that moment the canonical editable object is two rails, and the human sculpts each edge to the artwork. We don't constrain the representation to what's easy to generate, because the whole point of the artifact is that the human corrects it.
- **Fill** — closed `boundary` path + `angle` + `start`/`end` handles (free points; engine resolves to nearest boundary point) + params. Start/end drive **entry/exit and travel/chaining**, not row traversal.
- **Run / stroke** — single path + params.

Every object carries a **stable id** (annotation identity) and serializes to `inkstitch:` attributes written server-side via lxml. The complete attribute surface lives at `inkstitch.org/namespace/` — the model writes against that table.

> **Reuse, don't mint:** cli-anything-inkstitch's `document prep` already assigns stable `elem_N` ids, and the entire CLI command surface (plus `history.py` patches) addresses elements by those ids. The artifact adopts them as its object ids. Agent regeneration of an object rewrites `d`/attrs in place under the same id — which is how the CLI's patch history already behaves.

**Two editing axes, kept distinct in the UI:**
- **Geometry axis** — rail nodes (each rail edited independently), rungs, fill boundaries, start/end handles (drag on canvas).
- **Parameter axis** — density, underlay, pull compensation, angle (tuning controls; also settable by chat, e.g. "softer edges" → underlay/compensation change).

---

## 6. Stitch engine integration

- **Two-tier preview.** Tier 1: instant JS approximation during manipulation (near-zero latency). Tier 2: authoritative render on settle — invoke the Ink/Stitch binary for the stitch plan, overlay the returned stitch-plan SVG. Debounced.
- **Latency: better than feared, but not "live."** Measured (§6a): ~1.5 s for the realistic case (one element edited, rest cache-hit), ~0.6 s on full cache hit, ~0.5 s warm invocation floor. Ink/Stitch's own persistent per-element stitch-plan cache does the heavy lifting — the backend must preserve its cache keys (stable ids, minimal-diff writes). Design for a debounced ~1.5 s settle-render with request coalescing, and absorb the cold start with a throwaway invocation at artifact launch.
- **Tier-1 honesty rule.** Zigzag interpolation between satin rails is safe to approximate in JS. Fill *routing* is not — section breaks and travel paths are exactly what only the engine knows (and exactly what the human is there to correct). For fills, Tier-1 renders only the boundary, an angle indicator, and start/end handles — **never fake rows**. Fills lean entirely on Tier-2 for truth.
- **Export.** Same binary path produces the final stitch file (DST/PES/etc.).
- **Backend contract.** Thin HTTP wrapper **around the existing cli-anything-inkstitch command surface** (`element set-attr`, `params set`, `validate`, `preview`, `export`) — not raw SVG writes. The project layer tracks the SVG's SHA-256 and a patch history; writing through the CLI keeps file locking, undo, and hash coherence for free, whereas direct SVG writes force `document open --force` resyncs and orphan the history. The artifact never touches stitch math.
- **The existing Paper.js scaffold** implements Tier-1 preview, rungs, and a mocked Tier-2 hook — reusable — but its satin model is centerline+width and **must be reworked to two independent rails + rungs** before it's the canonical editing surface. The rung logic, preview round-trip, and canvas plumbing carry over; the rail representation and the width-handle interaction get replaced by independent per-rail node editing.

### 6a. Measured Tier-2 latency (Ink/Stitch 3.2.2, macOS arm64, 2026-07-04)

Measured with `--extension=stitch_plan_preview` / `--extension=output` on sparkle-squad-front.svg (~4.5k stitches, 15 elements), 4 runs per case:

| Operation | Latency | Notes |
|---|---|---|
| Warm invocation floor (trivial design, fresh compute) | **~0.5 s** | PyInstaller boot + minimal stitch math |
| Preview, full stitch-plan cache hit (design unchanged) | **~0.6 s** | Effectively pure invocation + render overhead |
| Preview, **one element edited** (the realistic loop case) | **~1.5 s** | Recompute of the edited ~3.6k-stitch fill; rest served from cache |
| DST export, full design | **~1.0 s** | |
| True cold start (first run after boot) | *not cleanly measured* | The one true-cold reading was contaminated by a blocking dialog (finding 3); expect a few seconds of bundle page-in. A throwaway warmup invocation at artifact launch is cheap insurance either way. |

**Three load-bearing findings:**

1. **Ink/Stitch has a persistent per-element stitch-plan cache.** Unchanged elements are served from cache across invocations — Tier-2 cost scales with the *edited element*, not the whole design. This is the keep-warm strategy, already built. The backend should do nothing to defeat it (stable element ids and minimal-diff attr writes preserve cache keys — another reason to write through the CLI layer).
2. **The realistic settle-render is ~1.5 s, not the feared 2–5 s.** That's comfortably workable for a debounced Tier-2: drag freely under Tier-1, see engine truth ~1.5 s after release. Debounce floor: ~500 ms (below that, invocation overhead dominates and stale renders pile up); coalesce by dropping any in-flight render when a newer manipulation supersedes it.
3. **Unversioned SVGs block headless invocation on a GUI dialog.** SVGs lacking `inkstitch_svg_version` metadata trigger a confirmation dialog *even with `INKSTITCH_OFFLINE_SCRIPT=true`* — a raw Illustrator export handed straight to the binary hangs the "headless" Phase-0 pass until a human clicks. cli-anything-inkstitch's `document prep` already stamps the version metadata (`svg/document.py`), so any design that entered through the CLI is safe. Hard requirement: **the binary is never invoked on an SVG that hasn't passed through `document prep`** — one more reason the backend contract routes through the CLI layer.

---

## 7. Agent edit operations (what conversation can do)

The agent's edit vocabulary against the model, invokable by chat or as auto-fixes:

- Adjust either rail independently (taper, reshape an edge); convert **stroke ↔ satin** (stroke bakes to a symmetric two-rail column); re-angle a fill.
- **Snap a rail to the artwork edge** (project that rail's nodes onto the reference boundary — the highest-value satin correction, since it's exactly the edge-matching that variable-width columns need).
- **Auto re-rung** at curvature peaks; add/remove rungs; re-pair rails after edits.
- Set **start/end** handles to minimize travel between two objects (chaining).
- Fix **stitch order** across objects.
- Tune params by description ("less dense", "softer edges", "more underlay") → concrete param deltas.
- Regenerate a single object the model got wrong, in place, without disturbing the rest.

---

## 8. Stitchability gate (Lavish layout-audit analog)

Before handback, audit the real design for un-stitchable geometry — the embroidery equivalent of Lavish's browser layout audit:

- **Error severity (blocks handback):** desynced satin rails / interpolation twist (**the primary check** — this is the risk we accept by choosing independent rails, and the gate is where it's caught: rails that can't be cleanly paired by the current rungs), satins below min or above max stitchable width, self-crossing rails on concave turns, fills with no resolvable entry, density outside machine range.
- **Warning severity (surface, don't block):** excessive travel/jumps, very long unbroken satins, sharp angle transitions.

Errors keep the artifact open and notify the agent through the same poll path (so it can auto-fix and re-check before asking the human). A **"stitch anyway"** override with a persistent banner ensures review is never blocked indefinitely.

---

## 9. Deployment model — decision needed

Two ways to implement the two-way loop; pick based on where this lives:

- **(A) Self-contained Claude artifact.** The artifact calls the model directly from inside itself (Claude-in-artifact API) for the conversation, and calls the inkstitch wrapper for previews. Fully in-app, no CLI. **Blocker: claude.ai artifacts run under a strict CSP that blocks *all* external requests — including `fetch` to localhost.** A hosted artifact cannot reach a local inkstitch wrapper, which kills Tier-2 preview and export. (A) is only viable on a self-hosted artifact surface.
- **(B) Lavish-style AXI.** A CLI + long-poll loop the agent runs, opening the editor locally; feedback polls back to whatever agent is driving (Claude Code, clisbot, etc.). Best if this plugs into your existing VPS/agent pipeline and you want any agent to drive it.

They're not exclusive — the canvas + model + engine are identical; only the agent transport differs. **Recommendation (revised): prototype on (B).** The CSP wall makes (A) unbuildable against a local engine today, and (B) matches how the project is actually driven (Claude Code in a terminal next to a browser). Keep the agent transport behind an interface so (A) becomes a swap if the platform constraint ever lifts or the engine moves to a VPS.

---

## 10. Open decisions (for you + Fable)

1. ~~Deployment model (§9) for v1.~~ **Resolved: (B).** The artifact CSP wall rules out (A) against a local engine (§9).
2. ~~Where inkstitch runs — local vs VPS.~~ **Resolved: local.** Measured latency (§6a) is workable — ~1.5 s realistic settle-render; a VPS round-trip would only add network latency on top.
3. ~~Object-id strategy.~~ **Resolved: reuse `document prep`'s stable `elem_N` ids.** The CLI command surface and `history.py` patches already key on them; regeneration rewrites in place under the same id (§5).
4. How much the agent **auto-fixes** vs **proposes and waits**. **Proposed split:** auto-fix gate *errors* (objective failures — desynced rails, unstitchable width, unresolvable entry), propose-and-wait for aesthetic changes. Keeps the gate loop fast without the agent redesigning things unasked.
5. ~~Undo/history model.~~ **Resolved: per-batch.** `history.py`'s apply/reverse patch ring buffer already exists; one feedback send = one patch = one undo step.
6. v1 object scope — see §11 (one satin column **plus** fill start/end handles).
7. **Rung-pairing behavior when rail node counts drift during editing** — auto-redistribute rungs (smooth, but can silently shift where the zigzag crosses) vs. keep manual rungs fixed (predictable, fiddlier). Drives both the manipulation feel and the desync-detection logic in the gate (§8). **Lean: hybrid** — manual rungs stay fixed, auto-rungs redistribute. *Still prototype both ways before committing.*

---

## 11. Suggested v1 slice

Smallest thing that proves the loop end-to-end:

> One satin column with **two independently editable rails + rungs**, **plus one fill's start/end handles**. Direct manipulation (drag either rail's nodes, add/move rungs, drag a fill's start/end points) **plus** chat ("narrower at the top", "snap this rail to the artwork edge") **plus** authoritative inkstitch preview **plus** the two-way agent loop, with rail-desync caught by the gate. Local inkstitch. Deployment model (B).

*Why fill handles made the v1 cut:* empirically the #1 correction in real digitization testing (the sparkle-squad star's fill_start moved three times across stitch-out iterations), it's the simplest possible manipulation (drag one point), and it exercises identical loop mechanics. Satin rails prove the *hard* editing; fill start/end proves the *valuable* editing.

Everything else — full fill boundary editing, travel/chaining, multi-object order, the full gate, transport (A) — layers on after the loop feels right on one object.
